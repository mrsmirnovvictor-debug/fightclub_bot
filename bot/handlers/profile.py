"""Профиль, справка, таблица чемпионов."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import Config
from bot.database import Database
from bot.game.classes import FIGHTER_CLASSES, ALL_ZONES
from bot.game.combat import MAX_MISSED_TURNS, MAX_ROUNDS
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
from bot.game.narrator import esc
from bot.handlers.common import profile_text, send_profile

router = Router(name="profile")


def help_text(turn_timeout: int = 30) -> str:
    return (
        "🥊 <b>Бойцовский клуб — как это работает</b>\n\n"
        "<b>В личке бота</b>\n"
        "/start — создать бойца (класс → прозвище → аватар → характеристики)\n"
        "/profile — карточка бойца\n"
        "/upgrade — раскидать свободные очки после апа или уровня\n"
        "/shop — кредиты и на что их тратить\n"
        "/respec — снести характеристики и раздать заново\n"
        "/class — сменить класс бойца\n"
        "/rename — новое прозвище, /avatar — новая аватарка\n"
        "/reset — начать заново с новым персонажем\n"
        "/classes — чем отличаются классы\n\n"
        "<b>В группе</b>\n"
        "/arena — отметить текущую ветку как ринг клуба (только для админов)\n"
        "/duel — бросить вызов всем желающим\n"
        "/duel в ответ на сообщение — вызвать конкретного бойца\n"
        "/top — чемпионы клуба\n\n"
        "<b>Как идёт бой</b>\n"
        "Каждый раунд оба бойца жмут одну зону удара (👊) и закрывают блоком (🛡) "
        "столько зон, сколько положено классу. Соперник твоих нажатий не видит — "
        "бот отвечает лично тому, кто нажал.\n"
        f"На ход даётся {turn_timeout} секунд. "
        "Успели оба раньше — раунд считается сразу.\n"
        "Что успел нажать, то и работает: не выбрал зону удара — не бьёшь, "
        "закрыл одну зону из двух — вторая открыта.\n"
        f"Не нажал ничего — пропуск хода. {MAX_MISSED_TURNS} пропуска подряд — "
        "техническое поражение. Сдаться нельзя: бой идёт до конца.\n"
        "Блок гасит удар полностью, ловкач может увернуться и добавить контрудар, "
        "ассасин — влепить крит.\n"
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
            f"🔮{stats.intuition} 🫀{stats.endurance}, блоков: {fclass.block_zones}"
        )
    await message.answer("\n\n".join(blocks))


@router.message(Command("profile", "me"))
async def cmd_profile(message: Message, db: Database) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Бойца ещё нет. Напиши мне в личку /start.")
        return
    if message.chat.type == "private":
        await send_profile(message, player)
    else:
        await message.answer(profile_text(player))


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
            f"<b>{esc(player.nickname)}</b> — <b>{player.rating}</b> · "
            f"{player.fclass.title}, {player.level} ур. · "
            f"{player.wins}—{player.losses}"
        )
    await message.answer("\n".join(lines))
