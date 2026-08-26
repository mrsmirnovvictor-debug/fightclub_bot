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
    action: str  # attack | block | giveup
    duel_id: int
    zone: str = ""


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


def fight_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    """Одна клавиатура на обоих бойцов: кто нажал — тому и засчитали."""
    builder = InlineKeyboardBuilder()
    for zone in ALL_ZONES:
        builder.button(
            text=f"👊 {zone.title}",
            callback_data=FightCB(action="attack", duel_id=duel_id, zone=zone.value),
        )
    builder.adjust(5)
    block_row = [
        InlineKeyboardButton(
            text=f"🛡 {zone.title}",
            callback_data=FightCB(
                action="block", duel_id=duel_id, zone=zone.value
            ).pack(),
        )
        for zone in ALL_ZONES
    ]
    builder.row(*block_row)
    builder.row(
        InlineKeyboardButton(
            text="🏳️ Сдаться",
            callback_data=FightCB(action="giveup", duel_id=duel_id).pack(),
        )
    )
    return builder.as_markup()


def class_hint(fclass: FighterClass) -> str:
    return f"{fclass.label} — {fclass.tagline}"


def stat_from_value(value: str) -> Stat:
    return Stat(value)


def zone_from_value(value: str) -> Zone:
    return Zone(value)
