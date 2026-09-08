"""Инлайн-клавиатуры и фабрики callback-данных."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.game.classes import (
    ALL_STATS,
    ALL_ZONES,
    BLOCK_WIDTH,
    FIGHTER_CLASSES,
    FighterClass,
    Stat,
    Zone,
    block_button,
    block_combos,
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
    value: str  # эмодзи бойца


class StatCB(CallbackData, prefix="stat"):
    action: str  # add | reset | done
    stat: str = ""


class BuyCB(CallbackData, prefix="buy"):
    """Покупка в чате. confirm=0 — спросить, 1 — платить."""

    code: str
    confirm: int = 0


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


class RaidLobbyCB(CallbackData, prefix="rlob"):
    action: str  # join | leave | go
    lobby_id: int


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
    """Открытые образы своего пола — из них и выбирают лицо бойца."""
    builder = InlineKeyboardBuilder()
    for look in free_looks():
        if look.gender != gender:
            continue
        builder.button(
            text=f"{look.emoji} {look.title}",
            callback_data=LookCB(code=look.code),
        )
    builder.adjust(1)
    return builder.as_markup()


def avatars_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for emoji in AVATARS:
        builder.button(text=emoji, callback_data=AvatarCB(value=emoji))
    builder.adjust(6)
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


def confirm_buy_keyboard(code: str, price: int) -> InlineKeyboardMarkup:
    """Два ответа на «Купить»: заплатить или передумать.

    Всплывающего окна с двумя кнопками в чате нет, поэтому вопрос задаём
    самой клавиатурой — на месте прежнего списка товара. Код товара несут
    обе кнопки: по нему отмена знает, какой список вернуть на место.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Подтвердить · {price} 💰",
        callback_data=BuyCB(code=code, confirm=1),
    )
    builder.button(text="✖️ Отмена", callback_data=BuyCB(code=code, confirm=2))
    builder.adjust(1)
    return builder.as_markup()


def potions_keyboard(
    level: int, credits: int, bag: dict[str, int]
) -> InlineKeyboardMarkup:
    """Склянки: сначала выпить то, что есть, потом докупить открытое."""
    builder = InlineKeyboardBuilder()
    for potion in POTIONS:
        count = bag.get(potion.code, 0)
        # Пропуск в этой же стопке, но пить его нельзя — кнопки ему не даём
        if count and not potion.is_pass:
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


def raid_lobby_keyboard(lobby) -> InlineKeyboardMarkup:
    """Кнопки записи в рейд: место в отряде одно на всех, сторон тут нет."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🩸 В отряд ({lobby.total}/{lobby.size})",
        callback_data=RaidLobbyCB(action="join", lobby_id=lobby.id),
    )
    # Кнопка ранней отправки висит у всех: клавиатура в ветке одна на чат.
    # Нажмёт не созвавший — сервис ответит отказом всплывающим окном.
    if lobby.can_start and not lobby.is_full:
        builder.button(
            text="⚔️ Выходим сейчас",
            callback_data=RaidLobbyCB(action="go", lobby_id=lobby.id),
        )
    builder.button(
        text="🚪 Выйти", callback_data=RaidLobbyCB(action="leave", lobby_id=lobby.id)
    )
    builder.adjust(1)
    return builder.as_markup()


def _fight_panel(icon: str, attack_data, block_data) -> InlineKeyboardMarkup:
    """Панель хода: слева удар по зоне, справа блок на две смежные.

    Столбца ровно два, и зоны помещаются названиями целиком. Такой панель
    и останется: в ветке дерутся только на кулаках, а там ни второго оружия,
    ни щита — значит, ни второго удара, ни трёх закрытых зон. Снаряжение
    живёт в карточке, и панель хода со всеми его вариантами — тоже там.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for zone, combo in zip(ALL_ZONES, block_combos(BLOCK_WIDTH)):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon}{zone.title.capitalize()}",
                    callback_data=attack_data(zone),
                ),
                InlineKeyboardButton(
                    text=block_button(combo), callback_data=block_data(combo[0])
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fight_keyboard(duel_id: int, icon: str = "👊") -> InlineKeyboardMarkup:
    """Панель хода одна на обоих бойцов — так проще читать ветку.

    Кто нажал, тому и засчитали: бот отвечает всплывающей подсказкой лично
    нажавшему.
    """
    return _fight_panel(
        icon,
        lambda zone: FightCB(
            action="attack", duel_id=duel_id, zone=zone.value
        ).pack(),
        lambda zone: FightCB(action="block", duel_id=duel_id, zone=zone.value).pack(),
    )


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
