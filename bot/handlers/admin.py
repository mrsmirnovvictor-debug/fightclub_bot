"""Команды владельца клуба: то, что делается руками и в обход правил.

Их три, и две из них про одно — выдать подписку и начислить кредиты.
Больше таких заводить не стоит: уровень, характеристики, вещи боец
добывает сам, и чем короче этот список, тем меньше в клубе того, что
нельзя объяснить правилами.

Третья стои́т особняком. `/wipe` обнуляет город целиком — это не
подарок кому-то одному, а начало нового сезона, и к игре она вообще не
относится. Как она устроена и почему так осторожно, написано у неё же,
перед командой.

Кредиты в этом списке — не подарок, а инструмент: ими возвращают
потерянное на чужой ошибке и платят за то, чего в игре ещё нет
(турнир, конкурс, отыгрыш). Поэтому у начисления нет потолка, зато
есть минус: `/givecredits ник -500` снимает. Без минуса лишний ноль в
сумме исправить было бы нечем, а потолок, за которым владелец идёт
правкой базы, хуже честного «любое число».

Кредиты ложатся наличными, как всякий другой доход: на счёт в банке их
кладёт уже сам боец. Своего кошелька у владельца нет — деньги берутся
из ниоткуда, и ровно поэтому дверь заперта так же крепко, как у
подписки.

Кто владелец, решает `OWNER_ID` в окружении. Ноль или мусор — владельца
нет, и команда не отвечает никому, включая того, кто её писал:
незаданная переменная не должна открывать дверь всем подряд. Чужим
команда не отвечает вовсе, а не ругается: её для них попросту не
существует.

Выдача подписки идёт той же дверью, что и оплата звёздами (`grant_pro`),
— иначе подарочная подписка однажды разошлась бы с купленной: клинок бы
не лёг, образ не открылся. Разница только в цене: ноль звёзд и запись в
журнале как подарок, чтобы такое продление не путалось с покупками в
истории.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import Config
from bot.database import Database
from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.narrator import esc
from bot.game.pro import (
    PREMIUM,
    PREMIUM_TIER,
    PRO_DAYS,
    PRO_TIER,
    ProOffer,
    Tier,
)
from bot.models import Player
from bot.pro_service import grant_pro

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

# Дольше года подписку руками не выдают: такой срок почти наверняка
# опечатка в числе, а не намерение
MAX_GIFT_DAYS = 365

PRO_HOW_TO = (
    "Кому и насколько: <code>/givepro ник</code> — на месяц, "
    "<code>/givepro ник 7</code> — на неделю.\n"
    "Старшая ступень — та же команда с другим именем: "
    "<code>/givepremium ник</code>.\n"
    "Ник пишется как в карточке, пробелы внутри можно."
)

MONEY_HOW_TO = (
    "Кому и сколько: <code>/givecredits ник 500</code> — начислить, "
    "<code>/givecredits ник -500</code> — снять.\n"
    "Ник пишется как в карточке, пробелы внутри можно; сумма — последней."
)


def is_owner(user_id: int, config: Config) -> bool:
    """Владелец ли это. Без OWNER_ID владельца нет ни у кого."""
    return bool(config.owner_id) and user_id == config.owner_id


async def read_target(db: Database, args: str) -> tuple[Player | None, int]:
    """Разобрать «ник» или «ник дней». Игрок None — такого бойца нет.

    Сначала пробуем прочесть всю строку как прозвище и только потом
    отрываем от неё число: прозвище может кончаться цифрой, и «Боец 7»
    — это чей-то ник, а не семь дней для «Бойца».
    """
    text = " ".join(args.split())
    if not text:
        return None, PRO_DAYS
    whole = await db.find_by_nickname(text)
    if whole is not None:
        return whole, PRO_DAYS
    head, _, tail = text.rpartition(" ")
    if head and tail.isdigit():
        return await db.find_by_nickname(head), int(tail)
    return None, PRO_DAYS


@router.message(Command("givepro"))
async def cmd_give_pro(
    message: Message, command: CommandObject, db: Database, config: Config
) -> None:
    """Выдать или продлить бойцу PRO. Только владельцу клуба."""
    await _give_tier(message, command, db, config, PRO_TIER)


@router.message(Command("givepremium"))
async def cmd_give_premium(
    message: Message, command: CommandObject, db: Database, config: Config
) -> None:
    """Выдать или продлить бойцу PREMIUM. Только владельцу клуба."""
    await _give_tier(message, command, db, config, PREMIUM_TIER)


async def _give_tier(
    message: Message,
    command: CommandObject,
    db: Database,
    config: Config,
    tier: Tier,
) -> None:
    """Выдать подарочную подписку названной ступени.

    Команды две, а тело одно: выдача не зависит от ступени ничем, кроме
    того, какой срок продлевать, — и это решает сама оферта.
    """
    if not is_owner(message.from_user.id, config):
        # Молчим: для посторонних этой команды нет. Но если OWNER_ID не
        # задан вовсе, сказать об этом стоит — иначе владелец жмёт
        # команду и не понимает, почему бот молчит
        if not config.owner_id:
            logger.warning(
                "Команда владельца без OWNER_ID: %s просил /givepro",
                message.from_user.id,
            )
        return

    args = command.args or ""
    if not args.strip():
        await message.answer("Кому выдавать?\n\n" + PRO_HOW_TO)
        return

    player, days = await read_target(db, args)
    if player is None:
        await message.answer(
            f"Бойца «{esc(' '.join(args.split()))}» в клубе нет.\n\n" + PRO_HOW_TO
        )
        return
    if not 1 <= days <= MAX_GIFT_DAYS:
        await message.answer(
            f"Срок — от 1 до {MAX_GIFT_DAYS} дней, а не {days}."
        )
        return

    now = now_ts()
    was = (
        player.is_premium(now)
        if tier.code == PREMIUM
        else player.is_pro_exactly(now)
    )
    grant = await grant_pro(
        db, player, ProOffer(stars=0, days=days, tier=tier), now
    )
    # Подарок, а не покупка: в историю оплат не идёт и возврату не
    # подлежит. Ключ с часом — чтобы вторая выдача тому же бойцу не
    # затёрлась молча об уникальный столбец
    await db.add_purchase(
        user_id=player.user_id,
        code=tier.code,
        stars=0,
        credits=0,
        charge_id=f"gift:{tier.code}:{player.user_id}:{now}",
        kind="gift",
    )
    logger.info(
        "Владелец выдал %s: %s (%s) на %s дней, до %s",
        tier.title,
        player.nickname,
        player.user_id,
        days,
        grant.until,
    )

    await message.answer(
        f"{tier.badge} <b>{esc(player.nickname)}</b>: {tier.title} "
        f"{'продлена' if was else 'оформлена'} на {days} дней — "
        f"до {club_moment(grant.until)} мск."
    )


def _as_amount(said: str) -> int | None:
    """Прочесть сумму со знаком. None — это не число."""
    body = said[1:] if said[:1] in "+-" else said
    if not body.isdigit():
        return None
    return -int(body) if said[:1] == "-" else int(body)


async def read_purse(db: Database, args: str) -> tuple[Player | None, int | None]:
    """Разобрать «ник сумма». Сумма None — её не назвали или назвали не числом.

    Здесь, в отличие от подписки, сумма обязательна, поэтому последнее
    слово читается как число всегда. Прозвищу это не мешает: «Боец 7»
    остаётся прозвищем, потому что сумма стоит после него — «Боец 7 500»
    разбирается как пятьсот кредитов тому самому «Бойцу 7».
    """
    text = " ".join(args.split())
    if not text:
        return None, None
    head, _, tail = text.rpartition(" ")
    amount = _as_amount(tail) if head else None
    if amount is None:
        return None, None
    return await db.find_by_nickname(head), amount


@router.message(Command("givecredits"))
async def cmd_give_credits(
    message: Message, command: CommandObject, db: Database, config: Config
) -> None:
    """Начислить бойцу кредиты (или снять). Только владельцу клуба."""
    if not is_owner(message.from_user.id, config):
        if not config.owner_id:
            logger.warning(
                "Команда владельца без OWNER_ID: %s просил /givecredits",
                message.from_user.id,
            )
        return

    args = command.args or ""
    said = " ".join(args.split())
    if not said:
        await message.answer("Кому начислять?\n\n" + MONEY_HOW_TO)
        return

    player, amount = await read_purse(db, args)
    if amount is None:
        # Ник и сумма нужны оба, и по одному слову не видно, что назвали:
        # «500» — это сумма без ника, «Рагнар» — ник без суммы
        await message.answer(
            f"Не разобрал «{esc(said)}»: нужны и ник, и сумма.\n\n"
            + MONEY_HOW_TO
        )
        return
    if player is None:
        head = said.rpartition(" ")[0]
        await message.answer(f"Бойца «{esc(head)}» в клубе нет.\n\n" + MONEY_HOW_TO)
        return
    if amount == 0:
        await message.answer("Ноль кредитов — это ничего. Назовите сумму.")
        return

    was = player.credits
    # `grant_credits` сам не даёт уйти ниже нуля, поэтому снять больше,
    # чем у бойца есть, — не ошибка: кошелёк просто опустеет
    became = player.grant_credits(amount)
    await db.save_player(player)
    logger.info(
        "Владелец начислил кредиты: %s (%s) %+d — было %s, стало %s",
        player.nickname,
        player.user_id,
        amount,
        was,
        became,
    )

    verb = "начислено" if amount > 0 else "снято"
    await message.answer(
        f"💰 <b>{esc(player.nickname)}</b>: {verb} {abs(amount)} "
        f"кредитов. Наличными было {was}, стало {became}."
    )


# ---------- обнуление города ----------
#
# Сезон начинают с чистого листа: бойцов нет, кланов нет, мэра нет,
# машин на улицах нет. Это единственное необратимое действие во всём
# клубе, поэтому оно обставлено тремя заслонами разом.
#
# 1. Фраза, а не кнопка. Кнопку жмут не глядя, фразу набирают, и
#    набрать её случайно нельзя. Она же не даёт команде сработать от
#    пересланного сообщения или от истории команд.
# 2. Копия до, а не после. Копия снимается первой, и если снять её не
#    вышло — не стирается ничего. Обнуление без копии — это не
#    обнуление, а потеря.
# 3. Перезапуск после. В памяти бота живут идущие бои, столы казино и
#    собранные отряды, и все они знают бойцов, которых больше нет.
#    Доигранный после стирания бой записал бы их обратно — и город
#    начался бы не с чистого листа, а с призраков.

WIPE_WORD = "СТЕРЕТЬ ВСЁ"

WIPE_HOW_TO = (
    "Обнуление города стирает всех бойцов, кланы, деньги, машины, "
    "подписки и дела — всё, что игроки нажили. Контент (локации, вещи, "
    "приёмы) живёт в коде и не пострадает.\n\n"
    "Перед стиранием снимается копия базы рядом с ней — на том же диске.\n\n"
    f"Если решение принято: <code>/wipe {WIPE_WORD}</code>"
)

# Сколько ждать перед остановкой: ответ владельцу должен успеть уйти
RESTART_DELAY = 3.0


def backup_name(path: str, now: int | None = None) -> str:
    """Имя файла копии: рядом с базой, с часом снятия в названии.

    Рядом, а не в отдельном каталоге: на хостинге подключённый диск
    один, и копия, положенная мимо него, исчезнет вместе с контейнером.
    """
    moment = now_ts() if now is None else now
    stamp = club_moment(moment, "%Y%m%d-%H%M%S")
    base = Path(path)
    return str(base.with_name(f"{base.stem}-before-wipe-{stamp}{base.suffix}"))


async def restart_after(dispatcher, delay: float = RESTART_DELAY) -> None:
    """Остановить опрос — хостинг поднимет бота заново.

    Ждём, потому что `stop_polling` ждёт окончания всех обработчиков, а
    мы сами и есть такой обработчик: позвать её отсюда напрямую значит
    дождаться самого себя. Поэтому отдельной задачей и с паузой — чтобы
    ответ владельцу успел уйти.
    """
    await asyncio.sleep(delay)
    try:
        await dispatcher.stop_polling()
    except RuntimeError:  # pragma: no cover - опрос уже не идёт
        logger.warning("Опрос уже остановлен — перезапускать нечего")


def schedule_restart(dispatcher) -> asyncio.Task:
    """Поставить перезапуск в очередь. Отдельной дверью — чтобы проверять.

    Проверке нужно знать, что перезапуск назначен, и не нужно, чтобы он
    случился: на стенде опроса нет, а висящая задача переживёт тест.
    """
    return asyncio.create_task(restart_after(dispatcher))


@router.message(Command("wipe"))
async def cmd_wipe(
    message: Message,
    command: CommandObject,
    db: Database,
    config: Config,
    dispatcher: Dispatcher,
) -> None:
    """Обнулить город целиком. Только владельцу и только по фразе."""
    if not is_owner(message.from_user.id, config):
        if not config.owner_id:
            logger.warning("OWNER_ID не задан: /wipe не работает ни у кого")
        return

    said = " ".join((command.args or "").split()).upper()
    if said != WIPE_WORD:
        players = len(await db.all_players())
        tables = await db.game_tables()
        await message.answer(
            f"🔥 <b>Обнуление города</b>\n\n"
            f"Сейчас в клубе бойцов: <b>{players}</b>, "
            f"таблиц с игрой: <b>{len(tables)}</b>.\n\n"
            + WIPE_HOW_TO
        )
        return

    where = backup_name(db.path)
    try:
        size = await db.backup_to(where)
    except Exception as error:
        logger.exception("Копия перед обнулением не снялась")
        await message.answer(
            "❌ Копию базы снять не вышло, поэтому ничего не стёрто.\n"
            f"<code>{esc(str(error))}</code>"
        )
        return

    was = await db.wipe_game()
    logger.warning(
        "Владелец %s обнулил город: копия в %s", message.from_user.id, where
    )
    await message.answer(
        "🔥 <b>Город обнулён.</b>\n"
        f"Стёрто строк: <b>{sum(was.values())}</b> "
        f"в {len(was)} таблицах.\n"
        f"Копия базы: <code>{esc(where)}</code> "
        f"({size // 1024} КБ).\n\n"
        "Бот сейчас перезапустится — в памяти остались бои и столы с "
        "бойцами, которых больше нет. Через полминуты пиши /start."
    )
    schedule_restart(dispatcher)


__all__ = [
    "MAX_GIFT_DAYS",
    "MONEY_HOW_TO",
    "PRO_HOW_TO",
    "RESTART_DELAY",
    "WIPE_HOW_TO",
    "WIPE_WORD",
    "backup_name",
    "cmd_give_credits",
    "cmd_give_pro",
    "cmd_wipe",
    "is_owner",
    "read_purse",
    "read_target",
    "restart_after",
    "schedule_restart",
    "router",
]
