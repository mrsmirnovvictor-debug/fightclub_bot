"""Общие помощники для хендлеров."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot.config import Config
from bot.game.classes import ALL_STATS, ALL_ZONES, FighterClass, Stats
from bot.game.equipment import Equipment
from bot.game.economy import MICRO_UPS_PER_LEVEL
from bot.game.stats import derive
from bot.game.links import links
from bot.game.narrator import esc, health_line
from bot.game.potions import spell_duration
from bot.models import Player

PRIVATE_HINT = (
    "Сначала заведи бойца: напиши мне в личку /start и пройди регистрацию."
)


def thread_id_of(message: Message | None) -> int | None:
    """message_thread_id имеет смысл только для веток форума."""
    if message is None:
        return None
    if getattr(message, "is_topic_message", False):
        return message.message_thread_id
    return None


def stats_block(stats: Stats, indent: str = "") -> str:
    return "\n".join(
        f"{indent}{stat.emoji} {stat.title.capitalize()}: <b>{stats.get(stat)}</b>"
        for stat in ALL_STATS
    )


def combat_block(
    fclass: FighterClass,
    stats: Stats,
    level: int = 1,
    equipment: Equipment | None = None,
    extra_hp: int = 0,
) -> str:
    """extra_hp — запас сверх вещей: то, что дал выпитый эликсир."""
    equipment = equipment or Equipment()
    d = derive(fclass, stats, level, equipment.hp_bonus + extra_hp)
    weapon = equipment.weapon_damages[0] if equipment.weapon_damages else (0, 0)
    hit = f"{d.damage_min}–{d.damage_max}"
    if weapon[1]:
        # оружие тоже проходит через множитель класса
        low, high = (round(value * fclass.damage_mult) for value in weapon)
        hit += f" + оружие {low}–{high}"
    lines = [
        f"❤️ Запас здоровья: <b>{d.max_hp}</b>",
        f"👊 Урон: <b>{hit}</b>",
        f"💥 Крит: <b>{d.crit_chance:.0%}</b> (×{d.crit_power})",
        f"🚫 Антикрит: <b>{d.anticrit + equipment.anticrit:.0%}</b>",
        f"🌀 Уворот: <b>{d.dodge_chance:.0%}</b>",
        f"🎯 Точность: <b>{d.accuracy + equipment.accuracy:.0%}</b>",
        f"🔄 Контрудар: <b>{d.counter_chance:.0%}</b>",
        f"🪨 Сопротивление: <b>{d.resist:.0%}</b>",
        f"🪚 Пробивание: <b>{d.penetration:.0%}</b>",
    ]
    armor = [
        f"{zone.emoji}{low}–{high}"
        for zone, (low, high) in (
            (zone, equipment.armor_range(zone)) for zone in ALL_ZONES
        )
        if high
    ]
    if armor:
        lines.append("🛡 Броня: <b>" + " ".join(armor) + "</b>")
    return "\n".join(lines)


def progress_line(player: Player) -> str:
    """Строка прогресса: опыт до уровня и до ближайшего апа."""
    if player.at_max_level:
        return (
            f"🔒 Потолок уровня. Всего опыта: <b>{player.total_exp}</b> — "
            "он копится под будущие уровни."
        )
    ups_left = MICRO_UPS_PER_LEVEL - player.micro_ups
    return (
        f"📈 Опыт: <b>{player.exp}/{player.exp_needed}</b> · "
        f"до апа: <b>{player.exp_to_next_up}</b> "
        f"(апов до уровня: {ups_left})"
    )


def effects_line(player: Player) -> str:
    """Что сейчас действует и сколько ему осталось. Пусто — ничего не пил."""
    working = player.active_effects()
    if not working:
        return ""
    parts = [
        f"{effect.potion.emoji} {effect.potion.title} "
        f"({spell_duration(effect.seconds_left())})"
        for effect in working
        if effect.potion is not None
    ]
    return "🧪 Действует: " + ", ".join(parts) if parts else ""


def profile_text(player: Player, own: bool = True) -> str:
    """Профиль текстом. Чужому кошелёк и подсказки про очки не показываем."""
    fclass = player.fclass
    lines = [
        f"{player.avatar} <b>{esc(player.nickname)}</b>",
        f"{fclass.label} · {player.level} уровень · рейтинг <b>{player.rating}</b>",
        health_line(player),
        progress_line(player),
    ]
    if own:
        lines.append(f"💰 Кредиты: <b>{player.credits}</b>")
    effects = effects_line(player)
    if effects:
        lines.append(effects)
    lines += [
        "",
        stats_block(player.stats),
        "",
        combat_block(
            fclass, player.stats, player.level, player.equipment, player.effect_hp
        ),
        "",
        f"🥊 Боёв: <b>{player.fights}</b> · "
        f"побед: <b>{player.wins}</b> · "
        f"поражений: <b>{player.losses}</b> · "
        f"ничьих: <b>{player.draws}</b>",
    ]
    if own and player.free_points:
        lines.append(
            f"\n✨ Свободных очков: <b>{player.free_points}</b> — раскидай их: /upgrade"
        )
    return "\n".join(lines)


def card_keyboard(
    config: Config, user_id: int, private: bool
) -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая карточку.

    В личке Telegram разрешает web_app-кнопки, и это самый надёжный путь:
    он не зависит от того, заведено ли приложение в BotFather. В группах
    web_app-кнопок нет, поэтому там ведём на прямую ссылку мини-аппа.
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


async def send_profile(message: Message, player: Player, keyboard=None) -> None:
    text = profile_text(player)
    if player.avatar_file_id:
        await message.answer_photo(
            player.avatar_file_id, caption=text, reply_markup=keyboard
        )
    else:
        await message.answer(text, reply_markup=keyboard)
