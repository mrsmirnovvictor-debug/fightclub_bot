"""Профиль, справка, таблица чемпионов."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot.config import Config
from bot.database import Database
from bot.game.classes import FIGHTER_CLASSES, ALL_ZONES
from bot.game.combat import MAX_MISSED_TURNS, MAX_ROUNDS
from bot.game.equipment import (
    MAX_WEAR,
    REPAIR_DEGRADE_CHANCE,
    REPAIR_PRICE_PER_POINT,
    WEAR_CHANCE_LOSS,
    WEAR_CHANCE_WIN,
)
from bot.game.health import FULL_REGEN_SECONDS, HURT_THRESHOLD, READY_THRESHOLD
from bot.game.economy import (
    LEVEL_CREDITS,
    MAX_LEVEL,
    POINTS_PER_UP,
    RATING_BASE,
    REPEAT_SHARES,
    UP_CREDITS,
    WIN_CREDITS_MAX,
    WIN_CREDITS_MIN,
)
from bot.game.narrator import esc, name_link
from bot.game.links import links
from bot.handlers.common import profile_text, send_profile

router = Router(name="profile")


def help_text(turn_timeout: int = 30) -> str:
    return (
        "🥊 <b>Бойцовский клуб — как это работает</b>\n\n"
        "<b>В личке бота</b>\n"
        "/start — создать бойца (класс → прозвище → аватар → характеристики)\n"
        "/card — карточка бойца в мини-аппе\n"
        "/profile — то же самое текстом\n"
        "/upgrade — раскидать свободные очки после апа или уровня\n"
        "/shop — кредиты и на что их тратить\n"
        "/buy — витрина клуба: оружие, броня, обувь\n"
        "/respec — снести характеристики и раздать заново\n"
        "/class — сменить класс бойца\n"
        "/rename — новое прозвище, /avatar — новая аватарка\n"
        "/reset — начать заново с новым персонажем\n"
        "/classes — чем отличаются классы\n\n"
        "<b>В группе</b>\n"
        "/arena — отметить текущую ветку как ринг клуба (только для админов)\n"
        "/duel — бросить вызов всем желающим\n"
        "/duel в ответ на сообщение — вызвать конкретного бойца\n"
        "/card — открыть свою карточку\n"
        "/top — чемпионы клуба\n\n"
        "<b>Как идёт бой</b>\n"
        "Вызов принимают кнопкой, после чего вызвавший смотрит на соперника "
        "и решает, выходить на ринг или разойтись.\n"
        "В бою одна панель на двоих: слева удары по зонам, справа блоки. "
        "Блок закрывает две смежные зоны, со щитом — три. Класс на удары и "
        "блоки не влияет — разница между классами в характеристиках. "
        "Соперник твоих нажатий не видит: бот отвечает лично тому, кто нажал.\n"
        "Без оружия бьют кулаком, с оружием — кастетом, мечом и чем найдёшь. "
        "Второе оружие вместо щита даёт второй удар за раунд.\n"
        f"На ход даётся {turn_timeout} секунд. "
        "Успели оба раньше — раунд считается сразу.\n"
        "Что успел нажать, то и работает: не выбрал зону удара — не бьёшь, "
        "не выбрал блок — стоишь открытым.\n"
        f"Не нажал ничего — пропуск хода. {MAX_MISSED_TURNS} пропущенных удара "
        "подряд — техническое поражение. Сдаться нельзя: бой идёт до конца.\n"
        "Блок гасит удар полностью. Дальше судья считает по порядку: попал или "
        "нет (уворот защитника минус точность бьющего), крит или нет (крит "
        "бьющего минус антикрит защитника), урон от силы плюс урон оружия, "
        "потом сопротивление от выносливости и броня той зоны, куда прилетело.\n"
        "У каждой характеристики две стороны: 🤸 ловкость даёт уворот и точность (уворота чуть больше, так что уйти можно всегда), "
        "🔮 интуиция — крит и антикрит (крита чуть больше), 💪 сила — урон, 🫀 выносливость — здоровье и сопротивление.\n"
        f"Зоны: {', '.join(z.label for z in ALL_ZONES)}.\n"
        f"С 6-го раунда бойцы устают и бьют всё сильнее, "
        f"через {MAX_ROUNDS} раундов победу присуждает судья по остатку здоровья.\n"
        "Взаимный нокаут и равное здоровье по решению судьи — ничья.\n\n"
        "<b>Здоровье между боями</b>\n"
        "Здоровье не восстанавливается мгновенно: после боя остаётся ровно то, "
        "с чем ты его закончил, и затягивается полностью за "
        f"{FULL_REGEN_SECONDS // 60} минут.\n"
        f"🔴 меньше {HURT_THRESHOLD:.0%} — избит, "
        f"🟡 от {HURT_THRESHOLD:.0%} до {READY_THRESHOLD:.0%} — отдыхает, "
        f"🟢 от {READY_THRESHOLD:.0%} — можно на ринг.\n"
        "В красной и жёлтой зоне драться нельзя. В зелёной — можно, "
        "даже если здоровье неполное: это уже твой риск.\n"
        "Сколько осталось ждать, видно в /profile.\n\n"
        "<b>Лавка, экипировка и инвентарь</b>\n"
        "Лавка живёт в мини-аппе, на вкладке «Магазин» (/buy откроет её "
        "кнопкой). Товар разложен по типам вещей и открывается уровнем: "
        "с 4-го — оружие, с 5-го куртки, штаны, наручи и пояса, с 6-го "
        "новое оружие, головные уборы и обувь, с 7-го оружие, перчатки и "
        "щиты, с 8-го последнее оружие. В каждой партии есть вещь под "
        "каждый класс, но кредитов на всё не хватит — выбирай.\n"
        "Купленное падает в инвентарь; он там же, в карточке, на вкладке "
        "«Боец»: оттуда вещь надевают, снимают кликом по слоту и чинят.\n"
        "Надеть можно, только дотянув до требований вещи по уровню и "
        "характеристикам. Считаются свои характеристики, без экипировки.\n"
        f"У каждой вещи запас прочности — {MAX_WEAR} пунктов износа. После поражения "
        f"надетая вещь с шансом {WEAR_CHANCE_LOSS:.0%} снашивается на пункт, после победы — {WEAR_CHANCE_WIN:.0%}.\n"
        f"Починка стоит {REPAIR_PRICE_PER_POINT} 💰 за пункт, но каждая починка "
        f"с шансом {REPAIR_DEGRADE_CHANCE:.0%} отнимает у вещи пункт запаса — чинить "
        "выгоднее сразу целиком, а не по одному.\n"
        "Износ дошёл до запаса — вещь рассыпается в труху безвозвратно.\n"
        "Что дают вещи: оружие — прибавку к урону, одежда — броню на свою зону "
        "(кепка на голову, куртка на грудь и живот, пояс на пояс, штаны на пояс "
        "и ноги, обувь на ноги, щит — на всё сразу), плюс проценты точности "
        "и антикрита и запас здоровья.\n\n"
        "<b>Что даёт бой</b>\n"
        "Опыт получает только победитель: база плюс доля от нанесённого урона, "
        "умноженная на разницу уровней. Побить старшего выгодно, "
        "младшего — почти бесполезно.\n"
        f"Каждая четверть уровня — <b>ап</b>: +{POINTS_PER_UP} очко характеристик "
        f"и {UP_CREDITS} 💰. Четвёртый ап совпадает с новым уровнем, за него "
        f"дают ещё {LEVEL_CREDITS} 💰 и прибавку к здоровью.\n"
        f"За победу капает {WIN_CREDITS_MIN}–{WIN_CREDITS_MAX} 💰. "
        "За поражение и ничью — ни опыта, ни кредитов.\n"
        f"Рейтинг: ±{RATING_BASE} с поправкой на разницу уровней, "
        "ничья считается поражением обоим.\n"
        f"Повторные бои с одним соперником за сутки приносят "
        f"{' → '.join(f'{share:.0%}' for share in REPEAT_SHARES)} награды.\n"
        f"Потолок — {MAX_LEVEL} уровень, дальше опыт копится про запас."
    )


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config) -> None:
    await message.answer(help_text(config.turn_timeout))


@router.message(Command("classes"))
async def cmd_classes(message: Message) -> None:
    blocks = []
    for fclass in FIGHTER_CLASSES.values():
        stats = fclass.base_stats
        blocks.append(
            f"{fclass.label} — <i>{fclass.tagline}</i>\n"
            f"{fclass.description}\n"
            f"Старт: 💪{stats.strength} 🤸{stats.agility} "
            f"🔮{stats.intuition} 🫀{stats.endurance}"
        )
    await message.answer("\n\n".join(blocks))


def card_keyboard(config: Config, user_id: int, private: bool) -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая карточку.

    В личке Telegram разрешает web_app-кнопки, в группах — только ссылки,
    поэтому там ведём на прямую ссылку мини-аппа.
    """
    if private and config.webapp_enabled:
        url = f"{config.webapp_url}/?user_id={user_id}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🪪 Карточка бойца", web_app=WebAppInfo(url=url)
                    )
                ]
            ]
        )
    card_url = links.card_url(user_id)
    if card_url:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪪 Карточка бойца", url=card_url)]
            ]
        )
    return None


@router.message(Command("card"))
async def cmd_card(message: Message, db: Database, config: Config) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Бойца ещё нет. Напиши мне в личку /start.")
        return

    keyboard = card_keyboard(
        config, player.user_id, private=message.chat.type == "private"
    )
    if keyboard is None:
        await message.answer(
            "Карточка пока не поднята: администратор клуба не настроил мини-апп.\n"
            "Вот что есть в текстовом виде:"
        )
        await message.answer(profile_text(player))
        return
    await message.answer(
        f"🪪 Карточка бойца <b>{esc(player.nickname)}</b> "
        f"[{player.level}] — открывается прямо в Telegram.",
        reply_markup=keyboard,
    )


@router.message(Command("profile", "me"))
async def cmd_profile(message: Message, db: Database, config: Config) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Бойца ещё нет. Напиши мне в личку /start.")
        return
    keyboard = card_keyboard(
        config, player.user_id, private=message.chat.type == "private"
    )
    if message.chat.type == "private":
        await send_profile(message, player, keyboard)
    else:
        await message.answer(profile_text(player), reply_markup=keyboard)


@router.message(Command("top"))
async def cmd_top(message: Message, db: Database) -> None:
    players = await db.top_players(10)
    if not players:
        await message.answer("В клубе пока ни одного бойца. /start — исправь это.")
        return
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = ["🏆 <b>Чемпионы клуба</b>", "<i>по рейтингу</i>", ""]
    for index, player in enumerate(players):
        lines.append(
            f"{medals.get(index, f'{index + 1}.')} {player.avatar} "
            f"<b>{name_link(player.user_id, player.nickname)}</b> — "
            f"<b>{player.rating}</b> · "
            f"{player.fclass.title}, {player.level} ур. · "
            f"{player.wins}—{player.losses}"
        )
    await message.answer("\n".join(lines))
