"""Судья на ринге: превращает сухие цифры боя в текст для ветки группы."""

from __future__ import annotations

import html
import random

from bot.game.classes import ZONE_PREPOSITIONAL, Zone
from bot.game.combat import (
    MAX_MISSED_TURNS,
    DuelEnd,
    Fighter,
    Outcome,
    RoundResult,
    Strike,
)

BLOCK_LINES = [
    "{a} метит {zone}, но {d} встречает удар глухим блоком.",
    "{a} бьёт {zone} — {d} закрывается вовремя. Глухо.",
    "Удар {zone} от {a} вязнет в блоке {d}.",
    "{d} читает {a} как открытую книгу: удар {zone} принят на блок.",
]

DODGE_LINES = [
    "{a} бьёт {zone}, но {d} уходит с линии удара.",
    "{a} проваливается: удар {zone} рассекает воздух, {d} уже в стороне.",
    "{d} убирает корпус — кулак {a} проходит мимо.",
]

COUNTER_LINES = [
    "И тут же отвечает контрударом: <b>−{dmg}</b> по {a}!",
    "Ответка прилетает мгновенно: <b>−{dmg}</b>!",
    "{d} наказывает за промах контрударом на <b>{dmg}</b>.",
]

HIT_LINES = [
    "{a} пробивает {zone}: <b>−{dmg}</b>.",
    "{a} достаёт {zone} — {d} принимает <b>{dmg}</b>.",
    "Чистое попадание {zone} от {a}: <b>−{dmg}</b>.",
    "{a} вкладывается в удар {zone}. <b>{dmg}</b> из {d}.",
]

CRIT_LINES = [
    "💥 КРИТ! {a} ловит момент и бьёт {zone} — <b>{dmg}</b>! {d} ведёт.",
    "💥 {a} находит брешь {zone}: <b>{dmg}</b> одним ударом!",
    "💥 Хруст на весь зал: {a} проламывает защиту {zone}. <b>−{dmg}</b>!",
]

MISSED_TURN_LINES = [
    "⏳ {a} не сделал(а) ни одного движения — судья фиксирует пропуск хода.",
    "⏳ Тридцать секунд тишины от {a}. Пропуск хода.",
    "⏳ {a} стоит столбом: ни удара, ни блока.",
]

NO_ATTACK_LINES = [
    "🤲 {a} закрывается, но бить не стал(а) — зона удара не выбрана.",
    "🤲 {a} уходит в глухую оборону: удара в этом раунде нет.",
    "🤲 {a} только защищается — судья не засчитывает удар.",
]

TECHNICAL_LINES = [
    "Судья разводит бойцов: {loser} не отвечает уже три хода подряд.",
    "Бой остановлен — {loser} перестал(а) отзываться на гонг.",
]

DRAW_LINES = [
    "Оба бойца рухнули на настил одновременно. Судья разводит руками: ничья.",
    "Взаимный нокаут! Поднять руку некому — ничья.",
]

KO_LINES = [
    "{loser} валится на настил. Судья не считает — тут и так всё ясно.",
    "{loser} больше не встанет. Бой окончен!",
    "Ноги {loser} подкашиваются, и он оседает на пол.",
]


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def mention(fighter: Fighter) -> str:
    return f'<a href="tg://user?id={fighter.user_id}">{esc(fighter.name)}</a>'


def hp_bar(current: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0:
        return "▱" * width
    filled = max(0, min(width, round(width * current / maximum)))
    if current > 0 and filled == 0:
        filled = 1
    return "▰" * filled + "▱" * (width - filled)


def hp_line(fighter: Fighter) -> str:
    return (
        f"{fighter.fclass.emoji} {esc(fighter.name)} "
        f"{hp_bar(fighter.hp, fighter.max_hp)} {fighter.hp}/{fighter.max_hp}"
    )


def zone_phrase(zone: Zone) -> str:
    return ZONE_PREPOSITIONAL[zone]


def describe_strike(
    strike: Strike,
    attacker: Fighter,
    defender: Fighter,
    rng: random.Random | None = None,
) -> str:
    rng = rng or random
    names = {
        "a": f"<b>{esc(attacker.name)}</b>",
        "d": f"<b>{esc(defender.name)}</b>",
        "zone": zone_phrase(strike.zone) if strike.zone else "",
    }
    if strike.outcome is Outcome.SKIP:
        lines = MISSED_TURN_LINES if strike.missed_turn else NO_ATTACK_LINES
        return rng.choice(lines).format(**names)
    if strike.outcome is Outcome.BLOCK:
        line = rng.choice(BLOCK_LINES).format(**names)
    elif strike.outcome is Outcome.DODGE:
        line = rng.choice(DODGE_LINES).format(**names)
        if strike.counter_damage:
            line += " " + rng.choice(COUNTER_LINES).format(
                **names, dmg=strike.counter_damage
            )
    elif strike.outcome is Outcome.CRIT:
        line = rng.choice(CRIT_LINES).format(**names, dmg=strike.damage)
    else:
        line = rng.choice(HIT_LINES).format(**names, dmg=strike.damage)

    return f"{strike.zone.emoji} {line}"


def round_report(
    result: RoundResult,
    fighters: dict[int, Fighter],
    rng: random.Random | None = None,
) -> str:
    rng = rng or random
    lines = [f"<b>⚔️ Раунд {result.number}</b>", ""]
    for strike in result.strikes:
        lines.append(
            describe_strike(
                strike, fighters[strike.attacker_id], fighters[strike.defender_id], rng
            )
        )
    lines.append("")
    for fighter in fighters.values():
        lines.append(hp_line(fighter))
    return "\n".join(lines)


def finish_report(
    result: RoundResult,
    fighters: dict[int, Fighter],
    reward_exp: int = 0,
    rng: random.Random | None = None,
) -> str:
    rng = rng or random
    lines: list[str] = []
    winner = fighters.get(result.winner_id) if result.winner_id else None
    loser = (
        next((f for f in fighters.values() if f.user_id != winner.user_id), None)
        if winner
        else None
    )

    if result.end_reason is DuelEnd.KO and winner and loser:
        lines.append(rng.choice(KO_LINES).format(loser=f"<b>{esc(loser.name)}</b>"))
        lines.append("")
        lines.append(f"🏆 Победа: {mention(winner)} ({winner.fclass.label})")
    elif result.end_reason is DuelEnd.DOUBLE_KO:
        lines.append("🤝 " + rng.choice(DRAW_LINES))
    elif result.end_reason is DuelEnd.TECHNICAL:
        if winner and loser:
            lines.append(
                rng.choice(TECHNICAL_LINES).format(loser=f"<b>{esc(loser.name)}</b>")
            )
            lines.append("")
            lines.append(f"🏆 Техническая победа: {mention(winner)}")
        else:
            lines.append("🤝 Оба бойца перестали отвечать. Судья закрывает бой ничьёй.")
    elif result.end_reason is DuelEnd.JUDGE:
        lines.append("🔔 Гонг! Раунды кончились, решение за судьёй.")
        if winner:
            lines.append(
                f"🏆 По остатку здоровья побеждает {mention(winner)} "
                f"({winner.hp}/{winner.max_hp})."
            )
        else:
            lines.append("🤝 Судья фиксирует ничью — бойцы неотличимы.")
    else:  # pragma: no cover - неизвестный исход
        lines.append("🤝 Ничья.")

    lines.append("")
    for fighter in fighters.values():
        lines.append(hp_line(fighter))
    if reward_exp:
        lines.append("")
        lines.append(
            f"Опыт: победителю +{reward_exp}, проигравшему +{max(1, reward_exp // 3)}."
        )
    return "\n".join(lines)


def duel_intro(first: Fighter, second: Fighter) -> str:
    return (
        "🥊 <b>Бойцовский клуб. Дуэль на кулаках</b>\n\n"
        f"{first.fclass.emoji} {mention(first)} — {first.fclass.title}, "
        f"{first.level} ур., {first.max_hp} HP\n"
        f"{second.fclass.emoji} {mention(second)} — {second.fclass.title}, "
        f"{second.level} ур., {second.max_hp} HP\n\n"
        "Правила простые: бьёшь в одну зону, закрываешь остальные. "
        f"Первое правило клуба — не заставлять судью ждать: {MAX_MISSED_TURNS} "
        "пропущенных хода подряд, и бой засчитают техническим поражением."
    )
