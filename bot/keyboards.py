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
    block_title,
)

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


def fight_keyboard(duel_id: int, fighter) -> InlineKeyboardMarkup:
    """Панель бойца: слева удары (по колонке на оружие), справа блоки.

    Блок закрывает смежные зоны, поэтому вариантов ровно пять — по одному
    на каждую зону, с которой блок начинается.
    """
    icons = fighter.weapon_icons
    blocks = fighter.block_options()
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
