"""Инлайн-клавиатуры и фабрики callback-данных."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.game.classes import (
    ALL_STATS,
    ALL_ZONES,
    FIGHTER_CLASSES,
    FighterClass,
    Stat,
    Zone,
    block_combos,
    block_title,
)
from bot.game.equipment import SHOWCASE, items_unlocked_at
from bot.game.looks import FEMALE, MALE, free_looks
from bot.game.potions import POTIONS

AVATARS: tuple[str, ...] = (
    "🥊", "🥷", "🐺", "🦍", "👹", "🤖",
    "🦂", "🐍", "🔥", "💀", "🃏", "🐻",
)


class ClassCB(CallbackData, prefix="cls"):
    code: str


class AvatarCB(CallbackData, prefix="ava"):
    value: str  # эмодзи или "custom"


class StatCB(CallbackData, prefix="stat"):
    action: str  # add | reset | done
    stat: str = ""


class BuyCB(CallbackData, prefix="buy"):
    code: str


class GenderCB(CallbackData, prefix="gender"):
    code: str  # male | female


class LookCB(CallbackData, prefix="look"):
    code: str  # код образа из bot.game.looks


class DrinkCB(CallbackData, prefix="drink"):
    """Выпить эликсир из рюкзака прямо из чата.

    confirm=1 — человек уже видел предупреждение о том, что нынешний
    временный эффект погаснет, и всё равно согласен.
    """

    code: str
    confirm: int = 0


class LobbyCB(CallbackData, prefix="lob"):
    action: str  # join | leave
    lobby_id: int
    team: int = 0


class BattleCB(CallbackData, prefix="btl"):
    action: str  # attack | block
    battle_id: int
    zone: str
    slot: int = 0


class TourCB(CallbackData, prefix="tour"):
    action: str  # join | leave
    tournament_id: int


class TopUpCB(CallbackData, prefix="topup"):
    code: str  # какая пачка кредитов


class ProCB(CallbackData, prefix="pro"):
    """Взять подписку. free=1 — по акции, даром."""

    free: int = 0


class ChallengeCB(CallbackData, prefix="chl"):
    action: str  # accept | cancel
    challenge_id: int


class FightCB(CallbackData, prefix="fight"):
    action: str  # attack | block
    duel_id: int
    zone: str = ""
    slot: int = 0  # каким оружием бьём: 0 — основное, 1 — второе


class StandoffCB(CallbackData, prefix="stand"):
    action: str  # start | decline
    duel_id: int


def classes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for fclass in FIGHTER_CLASSES.values():
        builder.button(text=fclass.label, callback_data=ClassCB(code=fclass.code))
    builder.adjust(2)
    return builder.as_markup()


def genders_keyboard() -> InlineKeyboardMarkup:
    """Пол бойца: от него зависит, какие образы предложить."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🙎‍♂️ Мужской", callback_data=GenderCB(code=MALE))
    builder.button(text="🙎‍♀️ Женский", callback_data=GenderCB(code=FEMALE))
    builder.adjust(2)
    return builder.as_markup()


def looks_keyboard(gender: str) -> InlineKeyboardMarkup:
    """Открытые образы своего пола. Плюс своё фото, если готового мало."""
    builder = InlineKeyboardBuilder()
    for look in free_looks():
        if look.gender != gender:
            continue
        builder.button(
            text=f"{look.emoji} {look.title}",
            callback_data=LookCB(code=look.code),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="📷 Загрузить своё фото",
            callback_data=AvatarCB(value="custom").pack(),
        )
    )
    return builder.as_markup()


def avatars_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for emoji in AVATARS:
        builder.button(text=emoji, callback_data=AvatarCB(value=emoji))
    builder.adjust(6)
    builder.row(
        InlineKeyboardButton(
            text="📷 Загрузить своё фото",
            callback_data=AvatarCB(value="custom").pack(),
        )
    )
    return builder.as_markup()


def stats_keyboard(free_points: int, allow_reset: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for stat in ALL_STATS:
        builder.button(
            text=f"+1 {stat.label}",
            callback_data=StatCB(action="add", stat=stat.value),
        )
    builder.adjust(2)
    bottom: list[InlineKeyboardButton] = []
    if allow_reset:
        bottom.append(
            InlineKeyboardButton(
                text="↩️ Сбросить", callback_data=StatCB(action="reset").pack()
            )
        )
    if free_points == 0:
        bottom.append(
            InlineKeyboardButton(
                text="✅ Готово", callback_data=StatCB(action="done").pack()
            )
        )
    if bottom:
        builder.row(*bottom)
    return builder.as_markup()


def showcase_keyboard(level: int, credits: int) -> InlineKeyboardMarkup:
    """Покупка прямо из чата — то, что открылось последним.

    Весь прилавок в кнопки не влезает: за полным списком идут в лавку
    мини-аппа, а здесь под рукой свежая партия товара.
    """
    newest = max(
        (item.level_required for item in SHOWCASE if item.level_required <= level),
        default=1,
    )
    builder = InlineKeyboardBuilder()
    for item in items_unlocked_at(newest):
        builder.button(
            text=f"{item.emoji} {item.title} — {item.price} 💰"
            + ("" if credits >= item.price else " 🔒"),
            callback_data=BuyCB(code=item.code),
        )
    builder.adjust(1)
    return builder.as_markup()


def potions_keyboard(
    level: int, credits: int, bag: dict[str, int]
) -> InlineKeyboardMarkup:
    """Склянки: сначала выпить то, что есть, потом докупить открытое."""
    builder = InlineKeyboardBuilder()
    for potion in POTIONS:
        count = bag.get(potion.code, 0)
        if count:
            builder.button(
                text=f"🥤 Выпить {potion.title} ({count})",
                callback_data=DrinkCB(code=potion.code),
            )
    for potion in POTIONS:
        if potion.level_required > level:
            continue
        builder.button(
            text=f"{potion.emoji} {potion.title} — {potion.price} 💰"
            + ("" if credits >= potion.price else " 🔒"),
            callback_data=BuyCB(code=potion.code),
        )
    builder.adjust(1)
    return builder.as_markup()


def challenge_keyboard(challenge_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🥊 Принять вызов",
        callback_data=ChallengeCB(action="accept", challenge_id=challenge_id),
    )
    builder.button(
        text="❌ Отозвать",
        callback_data=ChallengeCB(action="cancel", challenge_id=challenge_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def lobby_keyboard(lobby) -> InlineKeyboardMarkup:
    """Кнопки записи в бой: за какую сторону или просто влезть в мясорубку."""
    from bot.game.battle import BLUE, RED, BattleKind, team_name

    builder = InlineKeyboardBuilder()
    if lobby.kind is BattleKind.TEAM:
        for team in (RED, BLUE):
            taken = len(lobby.side(team))
            builder.button(
                text=f"{team_name(team)} ({taken}/{lobby.size})",
                callback_data=LobbyCB(action="join", lobby_id=lobby.id, team=team),
            )
    else:
        builder.button(
            text=f"👑 Влезть ({lobby.total}/{lobby.size})",
            callback_data=LobbyCB(action="join", lobby_id=lobby.id),
        )
    builder.button(
        text="🚪 Выйти", callback_data=LobbyCB(action="leave", lobby_id=lobby.id)
    )
    builder.adjust(2 if lobby.kind is BattleKind.TEAM else 1)
    return builder.as_markup()


def tournament_keyboard(tournament_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🏆 Записаться",
        callback_data=TourCB(action="join", tournament_id=tournament_id),
    )
    builder.button(
        text="🚪 Передумал",
        callback_data=TourCB(action="leave", tournament_id=tournament_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def battle_keyboard(
    battle_id: int, icons: tuple[str, ...] = ("👊",), block_width: int = 2
) -> InlineKeyboardMarkup:
    """Та же панель, что в дуэли, но нажатия уходят в групповой бой."""
    blocks = block_combos(block_width)
    rows: list[list[InlineKeyboardButton]] = []
    for index, zone in enumerate(ALL_ZONES):
        row = [
            InlineKeyboardButton(
                text=f"{icon} {zone.title.capitalize()}",
                callback_data=BattleCB(
                    action="attack", battle_id=battle_id, zone=zone.value, slot=slot
                ).pack(),
            )
            for slot, icon in enumerate(icons)
        ]
        combo = blocks[index]
        row.append(
            InlineKeyboardButton(
                text=f"🛡 {block_title(combo)}",
                callback_data=BattleCB(
                    action="block", battle_id=battle_id, zone=combo[0].value
                ).pack(),
            )
        )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fight_keyboard(
    duel_id: int, icons: tuple[str, ...] = ("👊",), block_width: int = 2
) -> InlineKeyboardMarkup:
    """Панель раунда: слева удары по зонам, справа блоки.

    Панель одна на обоих бойцов — так проще читать ветку. Кто нажал, тому
    и засчитали: бот отвечает всплывающей подсказкой лично нажавшему.
    """
    blocks = block_combos(block_width)
    rows: list[list[InlineKeyboardButton]] = []

    for index, zone in enumerate(ALL_ZONES):
        row = [
            InlineKeyboardButton(
                text=f"{icon} {zone.title.capitalize()}",
                callback_data=FightCB(
                    action="attack", duel_id=duel_id, zone=zone.value, slot=slot
                ).pack(),
            )
            for slot, icon in enumerate(icons)
        ]
        combo = blocks[index]
        row.append(
            InlineKeyboardButton(
                text=f"🛡 {block_title(combo)}",
                callback_data=FightCB(
                    action="block", duel_id=duel_id, zone=combo[0].value
                ).pack(),
            )
        )
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def standoff_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    """Последнее слово перед гонгом: выходить на ринг или разойтись."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Бьёмся",
                    callback_data=StandoffCB(action="start", duel_id=duel_id).pack(),
                ),
                InlineKeyboardButton(
                    text="🚪 Отказаться",
                    callback_data=StandoffCB(action="decline", duel_id=duel_id).pack(),
                ),
            ]
        ]
    )


def class_hint(fclass: FighterClass) -> str:
    return f"{fclass.label} — {fclass.tagline}"


def stat_from_value(value: str) -> Stat:
    return Stat(value)


def zone_from_value(value: str) -> Zone:
    return Zone(value)
