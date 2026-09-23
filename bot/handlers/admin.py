"""Команды владельца клуба: то, что делается руками и в обход правил.

Их ровно одна — выдать подписку. Всё остальное боец добывает сам: за
звёзды, за кредиты или за победы, и заводить сюда «дать кредитов» или
«поднять уровень» не стоит. Чем короче этот список, тем меньше в клубе
того, что нельзя объяснить правилами.

Кто владелец, решает `OWNER_ID` в окружении. Ноль или мусор — владельца
нет, и команда не отвечает никому, включая того, кто её писал:
незаданная переменная не должна открывать дверь всем подряд. Чужим
команда не отвечает вовсе, а не ругается: её для них попросту не
существует.

Выдача идёт той же дверью, что и оплата звёздами (`grant_pro`), — иначе
подарочная подписка однажды разошлась бы с купленной: клинок бы не лёг,
образ не открылся. Разница только в цене: ноль звёзд и запись в журнале
как подарок, чтобы такое продление не путалось с покупками в истории.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import Config
from bot.database import Database
from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.narrator import esc
from bot.game.pro import PRO_DAYS, ProOffer
from bot.models import Player
from bot.pro_service import grant_pro

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

# Дольше года подписку руками не выдают: такой срок почти наверняка
# опечатка в числе, а не намерение
MAX_GIFT_DAYS = 365

HOW_TO = (
    "Кому и насколько: <code>/givepro ник</code> — на месяц, "
    "<code>/givepro ник 7</code> — на неделю.\n"
    "Ник пишется как в карточке, пробелы внутри можно."
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
    """Выдать или продлить бойцу подписку. Только владельцу клуба."""
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
        await message.answer("Кому выдавать?\n\n" + HOW_TO)
        return

    player, days = await read_target(db, args)
    if player is None:
        await message.answer(
            f"Бойца «{esc(' '.join(args.split()))}» в клубе нет.\n\n" + HOW_TO
        )
        return
    if not 1 <= days <= MAX_GIFT_DAYS:
        await message.answer(
            f"Срок — от 1 до {MAX_GIFT_DAYS} дней, а не {days}."
        )
        return

    now = now_ts()
    was = player.is_pro(now)
    grant = await grant_pro(db, player, ProOffer(stars=0, days=days), now)
    # Подарок, а не покупка: в историю оплат не идёт и возврату не
    # подлежит. Ключ с часом — чтобы вторая выдача тому же бойцу не
    # затёрлась молча об уникальный столбец
    await db.add_purchase(
        user_id=player.user_id,
        code="pro",
        stars=0,
        credits=0,
        charge_id=f"gift:pro:{player.user_id}:{now}",
        kind="gift",
    )
    logger.info(
        "Владелец выдал PRO: %s (%s) на %s дней, до %s",
        player.nickname,
        player.user_id,
        days,
        grant.until,
    )

    extras = []
    if grant.blade:
        extras.append("🗡 Клинок ассасина — в инвентаре")
    if grant.look:
        extras.append("🥷 Образ ассасина — в гардеробе")
    await message.answer(
        f"💎 <b>{esc(player.nickname)}</b>: подписка "
        f"{'продлена' if was else 'оформлена'} на {days} дней — "
        f"до {club_moment(grant.until)} мск."
        + ("\n" + "\n".join(extras) if extras else "")
    )


__all__ = ["MAX_GIFT_DAYS", "cmd_give_pro", "is_owner", "read_target", "router"]
