"""Судья на ринге: превращает сухие цифры боя в текст для ветки группы."""

from __future__ import annotations

import html
import random
import re

from typing import TYPE_CHECKING

from bot.game.classes import ZONE_PREPOSITIONAL, Zone
from bot.game.economy import MAX_LEVEL
from bot.game.health import READY_THRESHOLD, format_duration
from bot.game.links import links
from bot.game.modes import FightMode
from bot.game.pro import PRO_BADGE
from bot.game.stats import derive
from bot.game.combat import (
    MAX_MISSED_TURNS,
    DuelEnd,
    Fighter,
    Outcome,
    RoundResult,
    Strike,
)

if TYPE_CHECKING:  # только для подсказок типов: models импортирует не нас
    from bot.models import Player, ProgressReport

# Эмодзи исхода: 👊 попадание, 🛡 блок, 🩸 крит, 🌀 уворот, 🔄 контрудар.
# Те же значки стоят у этих показателей на карточке — чтобы лог боя читался
# теми же символами, что и характеристики.
OUTCOME_EMOJI = {
    Outcome.HIT: "👊",
    Outcome.CRIT: "🩸",
    Outcome.BLOCK: "🛡",
    Outcome.DODGE: "🌀",
    Outcome.COUNTER: "🔄",
}

# Дальше — «{a} глагол {w} {zone}, {d} реакция». Формы подобраны так, чтобы
# годились и бойцу, и бойчихе: прошедшего времени в мужском роде здесь нет.
HIT_LINES = [
    "{a} вкладывается {w} {zone}, {d} не отбивает",
    "{a} прописывает {w} {zone} — {d} пропускает",
    "{a} вламывает {w} {zone}, {d} теряет равновесие",
    "{a} достаёт {w} {zone} соперника — {d} принимает",
    "{a} коротко бьёт {w} {zone}, {d} не успевает закрыться",
]

CRIT_LINES = [
    "{a} страшно вламывает {w} {zone} — {d} плывёт",
    "{a} ловит момент и лупит {w} {zone}, {d} едва держится",
    "{a} проламывает защиту {w} {zone} — {d} складывается",
]

BLOCK_LINES = [
    "{a} метит {w} {zone}, {d} отбивает",
    "{a} бьёт {w} {zone} — {d} закрывается вовремя",
    "Удар {w} {zone} от {a} вязнет в блоке {d}",
]

SHIELD_BLOCK_LINES = [
    "{a} метит {w} {zone}, {d} отбивает щитом",
    "{a} бьёт {w} {zone} — {d} подставляет щит",
    "Удар {w} {zone} от {a} гаснет о щит {d}",
]

DODGE_LINES = [
    "{a} бьёт {w} {zone}, {d} уходит с линии удара",
    "{a} проваливается: удар {w} {zone} рассекает воздух",
    "{d} убирает корпус — {a} машет {w} впустую",
]

COUNTER_LINES = [
    "{a} бьёт {w} {zone} — {d} уходит и отвечает",
    "{a} промахивается {w} {zone}, и тут же прилетает ответка",
    "{d} уворачивается от {a} и наказывает контрударом",
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


TAGS = re.compile(r"</?[a-z][^>]*>")


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def plain(text: str) -> str:
    """Без разметки — для всплывающих ответов, где HTML не разбирается."""
    return TAGS.sub("", text)


def mention(fighter: Fighter) -> str:
    return name_link(fighter.user_id, fighter.name, fighter.pro)


def name_link(user_id: int, name: str, pro: bool = False) -> str:
    """Имя-ссылка: открывает карточку бойца, если мини-апп настроен.

    У подписчика к имени приклеен значок — он должен быть виден везде, где
    бойца вообще называют по имени, поэтому живёт здесь, а не в каждом тексте.
    """
    badge = f" {PRO_BADGE}" if pro else ""
    return f'<a href="{links.href(user_id)}">{esc(name)}</a>{badge}'


def player_link(player: "Player") -> str:
    """Имя бойца со всем, что к нему прилагается."""
    return name_link(player.user_id, player.nickname, player.is_pro())


def upgrade_hint(player: "Player") -> str:
    """Куда идти раскладывать очки.

    В ветке боя команды бота не работают — их слушает личка. Раньше здесь
    стояло «— /upgrade», и люди честно пробовали набрать это прямо на ринге.
    Поэтому зовём в карточку: она открывается поверх чата одним касанием.
    """
    free = player.free_points
    where = links.card_url(player.user_id)
    if where:
        return (
            f"Свободных очков: {free} — "
            f'разложить в <a href="{where}">карточке бойца</a>'
        )
    return f"Свободных очков: {free} — разложить в личке бота: /upgrade"


def hp_bar(current: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0:
        return "▱" * width
    filled = max(0, min(width, round(width * current / maximum)))
    if current > 0 and filled == 0:
        filled = 1
    return "▰" * filled + "▱" * (width - filled)


def hp_line(fighter: Fighter) -> str:
    return (
        f"{fighter.fclass.emoji} {mention(fighter)} "
        f"{hp_bar(fighter.hp, fighter.max_hp)} {fighter.hp}/{fighter.max_hp}"
    )


def zone_phrase(zone: Zone) -> str:
    return ZONE_PREPOSITIONAL[zone]


def damage_tail(
    damage: int, hp: int, maximum: int, crit: bool = False, armor: int = 0
) -> str:
    """«−11 [11/66]» — сколько снял и сколько у защищающегося осталось.

    Если броня успела погасить часть удара, судья это отмечает: «−11 🛡3».
    """
    amount = f"−{damage}"
    body = f"<b>{amount}</b>" if crit else amount
    shield = f" 🛡{armor}" if armor > 0 else ""
    return f", {body}{shield} [{hp}/{maximum}]"


def describe_strike(
    strike: Strike,
    attacker: Fighter,
    defender: Fighter,
    rng: random.Random | None = None,
) -> str:
    rng = rng or random
    names = {
        "a": f"<b>{mention(attacker)}</b>",
        "d": f"<b>{mention(defender)}</b>",
        "w": strike.weapon,
        "zone": zone_phrase(strike.zone) if strike.zone else "",
    }

    if strike.outcome is Outcome.SKIP:
        lines = MISSED_TURN_LINES if strike.missed_turn else NO_ATTACK_LINES
        return rng.choice(lines).format(**names)

    emoji = OUTCOME_EMOJI[strike.outcome]
    if strike.outcome is Outcome.BLOCK:
        lines = SHIELD_BLOCK_LINES if strike.by_shield else BLOCK_LINES
        return f"{emoji} {rng.choice(lines).format(**names)}"

    if strike.outcome is Outcome.DODGE:
        return f"{emoji} {rng.choice(DODGE_LINES).format(**names)}"

    if strike.outcome is Outcome.COUNTER:
        line = rng.choice(COUNTER_LINES).format(**names)
        tail = damage_tail(
            strike.counter_damage, strike.attacker_hp_after, attacker.max_hp
        )
        return f"{emoji} {line}{tail}"

    line = rng.choice(
        CRIT_LINES if strike.outcome is Outcome.CRIT else HIT_LINES
    ).format(**names)
    tail = damage_tail(
        strike.damage,
        strike.defender_hp_after,
        defender.max_hp,
        crit=strike.outcome is Outcome.CRIT,
        armor=strike.armor,
    )
    return f"{emoji} {line}{tail}"


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
    return "\n".join(lines)


def finish_report(
    result: RoundResult,
    fighters: dict[int, Fighter],
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
        lines.append(rng.choice(KO_LINES).format(loser=f"<b>{mention(loser)}</b>"))
        lines.append("")
        lines.append(f"🏆 Победа: {mention(winner)} ({winner.fclass.label})")
    elif result.end_reason is DuelEnd.DOUBLE_KO:
        lines.append("🤝 " + rng.choice(DRAW_LINES))
    elif result.end_reason is DuelEnd.TECHNICAL:
        if winner and loser:
            lines.append(
                rng.choice(TECHNICAL_LINES).format(loser=f"<b>{mention(loser)}</b>")
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
    return "\n".join(lines)


def health_line(player: "Player", now: int | None = None) -> str:
    """Строка здоровья с цветом и временем восстановления."""
    state = player.health_state(now)
    hp, maximum = player.current_hp(now), player.max_hp
    line = (
        f"{state.emoji} Здоровье: <b>{hp}/{maximum}</b> "
        f"({player.hp_percent(now):.0%}) — {state.title}"
    )
    if not state.can_fight:
        line += (
            f"\n⏳ Драться можно с {READY_THRESHOLD:.0%}: через "
            f"{format_duration(player.seconds_until_ready(now))}"
        )
    elif hp < maximum:
        line += f"\n⏳ До полного: {format_duration(player.seconds_until_full(now))}"
    return line


def health_warning(player: "Player", is_self: bool = True) -> str:
    """Отказ пустить на ринг: кто, сколько здоровья и сколько ждать."""
    state = player.health_state()
    who = (
        "Ты ещё не в форме"
        if is_self
        else f"<b>{player_link(player)}</b> не в форме"
    )
    return (
        f"{state.emoji} {who}: {player.current_hp()}/{player.max_hp} "
        f"({player.hp_percent():.0%}).\n"
        f"Выходить на ринг можно с {READY_THRESHOLD:.0%} — это через "
        f"<b>{format_duration(player.seconds_until_ready())}</b>."
    )


def fighter_hp_note(fighter: Fighter) -> str:
    """Пометка в интро, если боец вышел на ринг недолеченным."""
    if fighter.hp >= fighter.max_hp:
        return f"{fighter.max_hp} HP"
    return f"{fighter.hp}/{fighter.max_hp} HP (не долечился)"


def _fighter_brief(player: "Player", mode: FightMode = FightMode.FIST) -> str:
    """Строка «на кого иду»: класс, уровень, здоровье, урон, рейтинг, счёт.

    В кулачном бою считаем бойца без вещей — таким он и выйдет на ринг.
    """
    if mode.armed:
        stats = derive(player.fclass, player.stats, player.level, player.extra_hp)
    else:
        # Вещи в раздевалке, а выпитое при бойце — как и на ринге
        stats = derive(
            player.fclass,
            player.base_stats.merge(player.effect_stats),
            player.level,
            player.effect_hp,
        )
    return (
        f"{player.fclass.emoji} "
        f"<b>{player_link(player)}</b> — "
        f"{player.fclass.title}, {player.level} ур.\n"
        f"❤️ {player.current_hp()}/{stats.max_hp} · "
        f"👊 {stats.damage_min}–{stats.damage_max} · "
        f"💥 {stats.crit_chance:.0%} · 🌀 {stats.dodge_chance:.0%}\n"
        f"🏆 рейтинг {player.rating} · {player.wins}—{player.losses}"
    )


def standoff_card(
    first: "Player",
    second: "Player",
    decision: str = "",
    mode: FightMode = FightMode.FIST,
) -> str:
    """Бойцы сошлись лицом к лицу — вызвавшему решать, драться или разойтись."""
    lines = [
        f"🥊 <b>Вызов принят. {mode.title.capitalize()}.</b>",
        "",
        _fighter_brief(first, mode),
        "",
        _fighter_brief(second, mode),
        "",
    ]
    gap = second.level - first.level
    if gap:
        stronger = second if gap > 0 else first
        lines.append(
            f"⚖️ Разница в уровнях — {abs(gap)} в пользу "
            f"<b>{player_link(stronger)}</b>."
        )
    else:
        lines.append("⚖️ Уровни равны.")

    if decision:
        lines += ["", decision]
    else:
        lines += [
            "",
            f"Слово за <b>{player_link(first)}</b>: "
            "выходить на ринг или разойтись.",
        ]
    return "\n".join(lines)


def duel_intro(
    first: Fighter, second: Fighter, mode: FightMode = FightMode.FIST
) -> str:
    gear = (
        "Дерутся тем, что надето."
        if mode.armed
        else "Вещи остались в раздевалке: спорят чистые характеристики."
    )
    return (
        f"🥊 <b>Бойцовский клуб. {mode.title.capitalize()}</b>\n\n"
        f"{first.fclass.emoji} {mention(first)} — {first.fclass.title}, "
        f"{first.level} ур., {fighter_hp_note(first)}\n"
        f"{second.fclass.emoji} {mention(second)} — {second.fclass.title}, "
        f"{second.level} ур., {fighter_hp_note(second)}\n\n"
        f"{gear}\n"
        "Правила простые: удар слева, бьёшь в одну зону. "
        "Блок справа — закрываешь две смежные, а со щитом три.\n"
        f"Не заставлять судью ждать: {MAX_MISSED_TURNS} пропущенных удара "
        "подряд, и бой засчитают техническим поражением."
    )


def recovery_line(players: list["Player"]) -> str:
    """Когда бойцы снова смогут выйти на ринг."""
    waiting = [
        f"{player_link(player)} — "
        f"через {format_duration(player.seconds_until_ready())}"
        for player in players
        if not player.can_fight()
    ]
    if not waiting:
        return "🩹 Оба отделались лёгким испугом и готовы к новому бою."
    return "🩹 Отлежаться: " + ", ".join(waiting)


def broken_gear_report(broken: list[tuple["Player", list]]) -> list[str]:
    """Что развалилось на бойцах за этот бой."""
    lines = []
    for player, items in broken:
        for owned in items:
            lines.append(
                f"💔 <b>{player_link(player)}</b>: "
                f"«{owned.title}» доносили "
                "до дыр — вещь рассыпалась в труху."
            )
    return lines


def rewards_report(
    rows: list[tuple["Player", "ProgressReport"]],
    share: float = 1.0,
    previous_fights: int = 0,
    broken: list[tuple["Player", list]] | None = None,
) -> str:
    """Что каждый унёс с ринга: опыт, кредиты, рейтинг, апы и уровни."""
    lines = ["📊 <b>Итоги</b>"]
    events: list[str] = []

    for player, report in rows:
        parts: list[str] = []
        parts.append(f"+{report.exp} опыта" if report.exp else "без опыта")
        if report.credits:
            parts.append(f"+{report.credits} 💰 (всего {player.credits})")
        sign = "+" if report.rating_delta >= 0 else "−"
        parts.append(
            f"рейтинг {player.rating} ({sign}{abs(report.rating_delta)})"
        )
        lines.append(
            f"{player.avatar} <b>{player_link(player)}</b>: "
            + ", ".join(parts)
        )

        name = f"<b>{player_link(player)}</b>"
        if report.levels:
            grown = f"+{report.endurance} к выносливости" if report.endurance else ""
            events.append(
                f"🎉 {name} берёт <b>{player.level}</b> уровень! "
                f"Здоровье выросло, {grown + ', ' if grown else ''}"
                f"очков характеристик: +{report.points}.\n"
                f"{upgrade_hint(player)}"
            )
        elif report.ups:
            word = "ап" if report.ups == 1 else "апа"
            events.append(
                f"⚡ {name} получает {report.ups} {word}: +{report.points} "
                f"к характеристикам.\n{upgrade_hint(player)}"
            )
        if report.capped and report.exp:
            events.append(
                f"🔒 {name} на потолке уровня: опыт копится "
                f"(всего {player.total_exp}), но новых уровней пока нет."
            )

    lines.append("")
    lines.append(recovery_line([player for player, _ in rows]))

    if share < 1.0:
        lines.append("")
        lines.append(
            f"♻️ Бой номер {previous_fights + 1} с этим соперником за сутки — "
            f"награда урезана до {share:.0%}."
        )
    ruined = broken_gear_report(broken or [])
    if ruined:
        lines.append("")
        lines.extend(ruined)
    if events:
        lines.append("")
        lines.extend(events)
    return "\n".join(lines)


# ---------- бои на много бойцов ----------


def lobby_card(lobby, timeout: int) -> str:
    """Объявление о сборе: кто уже записался, кого ждём и до каких пор."""
    from bot.game.battle import BLUE, RED, BattleKind, team_name

    lines = [
        f"{lobby.kind.emoji} <b>{lobby.kind.title.capitalize()}</b> "
        f"{lobby.mode.emoji} {lobby.mode.title}",
        "",
        f"Уровни: <b>{lobby.min_level}–{lobby.max_level}</b> · "
        f"на сбор {format_duration(timeout)}",
        "",
    ]
    if lobby.kind is BattleKind.TEAM:
        for team in (RED, BLUE):
            side = lobby.side(team)
            names = ", ".join(esc(lobby.names[user_id]) for user_id in side) or "—"
            lines.append(f"{team_name(team)} ({len(side)}/{lobby.size}): {names}")
    else:
        names = ", ".join(esc(name) for name in lobby.names.values()) or "—"
        lines.append(f"Записались ({lobby.total}/{lobby.size}): {names}")
    lines += ["", "Наберётся состав — гонг сразу, ждать не будем."]
    return "\n".join(lines)


def battle_intro(session) -> str:
    """Кто вышел на ринг и по каким правилам."""
    from bot.game.battle import BLUE, RED, BattleKind, team_name

    lines = [
        f"{session.kind.emoji} <b>{session.kind.title.capitalize()}</b> — "
        f"{session.mode.title}",
        "",
    ]
    if session.kind is BattleKind.TEAM:
        for team in (RED, BLUE):
            side = [
                user_id
                for user_id in session.fighters
                if session.teams.get(user_id) == team
            ]
            lines.append(
                f"{team_name(team)}: "
                + ", ".join(mention(session.fighters[user_id]) for user_id in side)
            )
    else:
        lines.append(
            "На ринге: "
            + ", ".join(mention(fighter) for fighter in session.fighters.values())
        )
    lines += [
        "",
        "Каждый ход бойцы разбиты на пары: соперник меняется от раунда к раунду. "
        "Кому пары не хватило — стоит и ждёт своего.",
        "Упал — выбыл: кнопки больше не твои, но бой идёт дальше.",
    ]
    return "\n".join(lines)


def battle_round_report(
    session, results, fallen: list[int], rng: random.Random | None = None
) -> str:
    """Разбор всех пар за один ход. Про выбывших говорим один раз — когда упали."""
    rng = rng or random
    lines = [f"<b>⚔️ Раунд {session.round_number}</b>"]
    for result in results:
        lines.append("")
        for strike in result.strikes:
            lines.append(
                describe_strike(
                    strike,
                    session.fighters[strike.attacker_id],
                    session.fighters[strike.defender_id],
                    rng,
                )
            )
    if fallen:
        names = ", ".join(
            f"<b>{esc(session.fighters[user_id].name)}</b>" for user_id in fallen
        )
        lines.append("")
        lines.append(f"❌ Выбывает из боя: {names}")
    return "\n".join(lines)


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 очко», «2 очка», «5 очков» — русский счёт без ляпов."""
    tail_two = abs(count) % 100
    tail = abs(count) % 10
    if 11 <= tail_two <= 14:
        return many
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def battle_rewards_report(rows, broken=None) -> str:
    """Итог по каждому бойцу: урон, опыт, кредиты, рейтинг.

    rows — тройки (боец, награда, победил ли он).
    """
    lines = ["📊 <b>Итоги</b>"]
    events: list[str] = []

    for player, report, fighter, won in rows:
        points = plural(report.exp, "очко", "очка", "очков")
        parts = [f"нанесено урона {fighter.damage_dealt}"]
        parts.append(f"получено {report.exp} {points} опыта")
        if report.credits:
            parts.append(f"+{report.credits} кр.")
        delta = abs(report.rating_delta)
        sign = "+" if report.rating_delta >= 0 else "-"
        parts.append(
            f"{sign}{delta} {plural(delta, 'очко', 'очка', 'очков')} рейтинга"
        )
        mark = "🎉" if won else "❌"
        name = player_link(player)
        lines.append(f"{mark} <b>{name}</b>: " + ", ".join(parts))

        title = f"<b>{player_link(player)}</b>"
        if report.levels:
            grown = f"+{report.endurance} к выносливости" if report.endurance else ""
            events.append(
                f"🎉 {title} берёт <b>{player.level}</b> уровень! "
                f"Здоровье выросло, {grown + ', ' if grown else ''}"
                f"очков характеристик: +{report.points}.\n"
                f"{upgrade_hint(player)}"
            )
        elif report.ups:
            word = "ап" if report.ups == 1 else "апа"
            events.append(
                f"⚡ {title} получает {report.ups} {word}: +{report.points} "
                f"к характеристикам.\n{upgrade_hint(player)}"
            )

    lines.append("")
    lines.append(recovery_line([player for player, _, _, _ in rows]))

    ruined = broken_gear_report(broken or [])
    if ruined:
        lines.append("")
        lines.extend(ruined)
    if events:
        lines.append("")
        lines.extend(events)
    return "\n".join(lines)


def battle_result(session, outcome) -> str:
    """Итог боя: кто устоял."""
    from bot.game.battle import BattleKind, team_name

    if outcome.draw:
        return "🤝 <b>Ничья.</b> Судья развёл всех по углам."

    winners = ", ".join(
        mention(session.fighters[user_id]) for user_id in outcome.winners
    )
    how = " по остатку здоровья" if outcome.by_rounds else ""
    if session.kind is BattleKind.TEAM:
        side = team_name(outcome.winning_team or 0)
        return f"🏆 <b>Победа{how}: {side}</b>\n{winners}"
    return f"👑 <b>Последний на ногах{how}: {winners}</b>"


# ---------- турнир ----------


def tournament_card(tournament, names: list[tuple[int, str, int]]) -> str:
    """Объявление о наборе: кто уже в списке и сколько осталось времени."""
    lines = [
        f"🏆 <b>Турнир{' «' + esc(tournament.title) + '»' if tournament.title else ''}</b> "
        f"{tournament.mode.emoji} {tournament.mode.title}",
        "",
        f"Мест: <b>{len(names)}/{tournament.size}</b> · " + (
            "уровни <b>любые</b>"
            if tournament.min_level <= 1 and tournament.max_level >= MAX_LEVEL
            else f"уровни <b>{tournament.min_level}–{tournament.max_level}</b>"
        ),
        f"Запись закрывается через <b>{format_duration(tournament.seconds_left)}</b>",
        "",
    ]
    if names:
        lines.append("<b>Записались</b>")
        for index, (user_id, nickname, rating) in enumerate(
            sorted(names, key=lambda row: -row[2]), start=1
        ):
            lines.append(f"{index}. {name_link(user_id, nickname)} — рейтинг {rating}")
    else:
        lines.append("Пока никого. Кто первый?")
    lines += [
        "",
        "Сетка плей-офф: проигравший выбывает, ничья переигрывается. "
        "Перед каждым боем бот лечит обоих до полного здоровья.",
    ]
    return "\n".join(lines)


def bracket_text(tournament, matches: list[dict], names: dict[int, str]) -> str:
    """Сетка: круги сверху вниз, в каждом — пары и их исход."""
    rounds: dict[int, list[dict]] = {}
    for match in matches:
        rounds.setdefault(match["round"], []).append(match)

    lines = ["🗂 <b>Сетка</b>"]
    for number in sorted(rounds):
        pairs = sorted(rounds[number], key=lambda row: row["slot"])
        lines += ["", f"<b>{round_title_for(len(pairs))}</b>"]
        for match in pairs:
            lines.append(_bracket_line(match, names))
    return "\n".join(lines)


def round_title_for(matches: int) -> str:
    from bot.game.bracket import round_title

    return round_title(matches)


def _bracket_line(match: dict, names: dict[int, str]) -> str:
    def who(user_id: int | None) -> str:
        if user_id is None:
            return "—"
        return esc(names.get(user_id, "боец"))

    first, second = who(match["first_id"]), who(match["second_id"])
    if match["first_id"] is None and match["second_id"] is None:
        return "▫️ —"
    if match["winner_id"]:
        winner = who(match["winner_id"])
        if match["first_id"] is None or match["second_id"] is None:
            return f"🎟 {winner} — без боя"
        return f"✅ {first} — {second} → <b>{winner}</b>"
    if match["state"] == "running":
        return f"▶️ {first} — {second}"
    return f"⏳ {first} — {second}"


def tournament_winner(tournament, winner) -> str:
    if winner is None:
        return "🚫 Турнир закончился без победителя."
    return (
        "🏆 <b>Турнир взят!</b>\n"
        f"Победитель: <b>{player_link(winner)}</b> "
        f"({winner.fclass.label}, {winner.level} ур., рейтинг {winner.rating})"
    )
