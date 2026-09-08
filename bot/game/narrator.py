"""Судья на ринге: превращает сухие цифры боя в текст для ветки группы."""

from __future__ import annotations

import html
import random
import unicodedata
import re

from typing import TYPE_CHECKING

from bot.game.classes import ZONE_PREPOSITIONAL, Zone
from bot.game.economy import MAX_LEVEL
from bot.game.health import HURT_THRESHOLD, READY_THRESHOLD, format_duration
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
    boxing_round,
    turn_in_round,
)

if TYPE_CHECKING:  # только для подсказок типов: models импортирует не нас
    from bot.models import Player, ProgressReport

# Эмодзи исхода: 👊 попадание, 🛡 блок, 🩸 крит, 🌀 уворот, 🔄 контрудар,
# 🛡‍🩸 пробитый блок. Те же значки стоят у этих показателей на карточке —
# чтобы лог боя читался теми же символами, что и характеристики.
OUTCOME_EMOJI = {
    Outcome.HIT: "👊",
    Outcome.CRIT: "🩸",
    Outcome.BLOCK: "🛡",
    Outcome.BREAK: "🛡🩸",
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

BREAK_LINES = [
    "{a} вкладывает всё {w} {zone} — блок {d} не выдерживает",
    "{a} проламывает блок {d} {w} {zone}",
    "{d} закрывается, но удар {w} {zone} проходит сквозь блок",
    "{a} бьёт {w} {zone} так, что защита {d} складывается внутрь",
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
    """Без разметки — для всплывающих ответов, где HTML не разбирается.

    Экранированное возвращаем как было: иначе боец с ником «Кот&Пёс» вместо
    имени получает «Кот&amp;Пёс».
    """
    return html.unescape(TAGS.sub("", text))


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


# Полоска в цвете: зелёная, пока боец свеж, жёлтая на середине, красная
# под нокаутом. Цветного текста Telegram не умеет, а цветные квадраты — да,
# и на панели раунда это единственный способ увидеть беду одним взглядом.
BAR_FULL = "🟩"
BAR_HURT = "🟨"
BAR_LOW = "🟥"
BAR_EMPTY = "⬛"
# Столько клеток в полоске на панели: два бойца в строке, и десять клеток
# на каждого телефон переносит.
BOARD_BAR = 5
# С какого знакоместа начинается правая колонка. Табло рисуется
# моноширинным блоком, поэтому ширину можно считать честно.
BOARD_COLUMN = 22
# В рейде колонка шире: рядом с полоской стоит остаток здоровья
RAID_COLUMN = 30


def bar_color(percent: float) -> str:
    if percent < HURT_THRESHOLD:
        return BAR_LOW
    if percent < READY_THRESHOLD:
        return BAR_HURT
    return BAR_FULL


def color_bar(current: int, maximum: int, width: int = BOARD_BAR) -> str:
    """Полоска здоровья цветными клетками."""
    if maximum <= 0:
        return BAR_EMPTY * width
    filled = max(0, min(width, round(width * current / maximum)))
    if current > 0 and filled == 0:
        filled = 1
    return bar_color(current / maximum) * filled + BAR_EMPTY * (width - filled)


def cells(text: str) -> int:
    """Ширина строки в знакоместах моноширинного шрифта.

    Значок занимает два места, модификаторы вроде VS16 — ни одного.
    Без этого счёта колонки разъезжаются ровно там, где стоят эмодзи.
    """
    width = 0
    for char in text:
        if unicodedata.combining(char) or char in "\ufe0f\ufe0e\u200d":
            continue
        if unicodedata.east_asian_width(char) in "WF" or unicodedata.category(
            char
        ) == "So":
            width += 2
        else:
            width += 1
    return width


def columns(left: str, right: str, width: int = BOARD_COLUMN) -> str:
    """Две колонки: левая от края, правая — от постоянного места."""
    gap = max(1, width - cells(left))
    return f"{left}{' ' * gap}{right}"


def fighter_head(fighter: Fighter) -> str:
    """«⚔️ Victor [3]» — класс, имя и уровень."""
    return f"{fighter.fclass.emoji} {esc(fighter.name)} [{fighter.level}]"


def fight_board(pairs, ready) -> list[str]:
    """Табло раунда: пара бойцов — четыре строки, по колонке на каждого.

    Табло идёт моноширинным блоком: только так колонки встают друг под
    другом. Плата за это — имена здесь не ссылки: Telegram не разрешает
    ссылке жить внутри такого блока. Имя со ссылкой на карточку осталось
    везде, где боец упоминается по ходу боя.
    """
    lines: list[str] = []
    for first, second in pairs:
        if lines:
            lines.append("")
        lines.append(columns(fighter_head(first), f"VS.  {fighter_head(second)}"))
        lines.append(
            columns(
                f"[{first.hp}/{first.max_hp}]", f"[{second.hp}/{second.max_hp}]"
            )
        )
        lines.append(
            columns(
                color_bar(first.hp, first.max_hp),
                color_bar(second.hp, second.max_hp),
            )
        )
        lines.append(columns(ready(first), ready(second)))
    return lines


def ready_mark(is_ready: bool) -> str:
    return "✅ Готов" if is_ready else "⏳ Думает"


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
        return f"{emoji} {rng.choice(BLOCK_LINES).format(**names)}"

    if strike.outcome is Outcome.BREAK:
        line = rng.choice(BREAK_LINES).format(**names)
        tail = damage_tail(
            strike.damage, strike.defender_hp_after, defender.max_hp, crit=True
        )
        return f"{emoji} {line}{tail}"

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


def corner_break(
    first: Fighter,
    second: Fighter,
    round_number: int,
    total: int,
    seconds: int,
) -> str:
    """Гонг в конце раунда: счёт по углам и сколько ещё отдыхать."""
    lines = [f"<b>🔔 Гонг! Раунд {round_number} из {total} окончен.</b>", ""]
    for fighter in (first, second):
        lines.append(
            f"{fighter.fclass.emoji} {mention(fighter)} "
            f"{hp_bar(fighter.hp, fighter.max_hp)} {fighter.hp}/{fighter.max_hp} "
            f"— нанёс {fighter.damage_dealt}"
        )
    lines.append("")
    if seconds > 0:
        lines.append(
            f"Судья разводит по углам. Отдых {rest_phrase(seconds)} — "
            f"и выходим на раунд {round_number + 1}."
        )
    else:
        lines.append(f"По углам — и сразу на раунд {round_number + 1}.")
    return "\n".join(lines)


def rest_phrase(seconds: int) -> str:
    """«минута», «30 секунд» — то, что судья говорит про отдых."""
    if seconds == 60:
        return "минута"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} минуты" if 2 <= minutes <= 4 else f"{minutes} минут"
    return f"{seconds} секунд"


def strike_lines(
    result: RoundResult,
    fighters: dict[int, Fighter],
    rng: random.Random | None = None,
) -> list[str]:
    """Слова судьи об этом ходе — по строке на удар.

    Формулировку судья выбирает броском, поэтому строки собираются один раз
    и дальше передаются как есть: и в ветку, и в мини-апп, и в лог боя.
    Позовёшь второй раз — получишь другие слова про тот же удар.
    """
    rng = rng or random
    return [
        describe_strike(
            strike, fighters[strike.attacker_id], fighters[strike.defender_id], rng
        )
        for strike in result.strikes
    ]


def round_report(
    result: RoundResult,
    fighters: dict[int, Fighter],
    rng: random.Random | None = None,
    lines: list[str] | None = None,
) -> str:
    """Разбор хода для ветки: заголовок и уже сказанные слова судьи."""
    said = strike_lines(result, fighters, rng) if lines is None else lines
    head = [
        f"<b>⚔️ Раунд {boxing_round(result.number)}, "
        f"удар {turn_in_round(result.number)}</b>",
        "",
    ]
    return "\n".join(head + said)


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
        lines.append(
            f"🔔 Финальный гонг! Все {boxing_round(result.number)} раундов позади, "
            "никто не упал — решение за судьёй."
        )
        if winner:
            other = next(f for f in fighters.values() if f.user_id != winner.user_id)
            lines.append(
                f"🏆 По нанесённому урону побеждает {mention(winner)} — "
                f"{winner.damage_dealt} против {other.damage_dealt}."
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
    damage: dict[int, int] | None = None,
) -> str:
    """Что каждый унёс с ринга: урон, опыт, кредиты, рейтинг, апы и уровни.

    `damage` — сколько каждый боец успел нанести. Урон стоит первым: по нему
    судья и решает бой, дошедший до последнего гонга.
    """
    lines = ["📊 <b>Итоги</b>"]
    events: list[str] = []

    for player, report in rows:
        parts: list[str] = []
        dealt = (damage or {}).get(player.user_id)
        if dealt is not None:
            parts.append(f"Нанесено урона {dealt}")
        parts.append(
            f"получено +{report.exp} опыта" if report.exp else "получено 0 опыта"
        )
        if report.credits:
            # Кошелёк целиком тут не к месту: это итог боя, а не карточка
            parts.append(f"+{report.credits} 💰")
        if report.rating_delta:
            sign = "+" if report.rating_delta > 0 else "−"
            parts.append(f"рейтинг {player.rating} ({sign}{abs(report.rating_delta)})")
        else:
            # Ничья: рейтинг на месте, и «(+0)» рядом с ним только сбивает
            parts.append(f"рейтинг {player.rating} — без изменений")
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


# ---------- рейды ----------


def raid_lobby_card(lobby, timeout: int) -> str:
    """Объявление о сборе в рейд: кто уже идёт и сколько ещё ждать.

    Часы стоят на месте до следующего нажатия — Telegram не даёт править
    сообщение каждую секунду, да и лимит чата этого не переживёт. Живой
    отсчёт идёт в карточке, а здесь честная отметка на момент правки.
    """
    names = ", ".join(esc(name) for name in lobby.members.values()) or "—"
    left = lobby.seconds_left(timeout)
    clock = f"выходим через {format_duration(left)}" if left else "время вышло"
    return "\n".join(
        [
            f"{lobby.boss.emoji} <b>Рейд: {esc(lobby.boss.title)}</b>",
            "",
            esc(lobby.boss.tagline) if lobby.boss.tagline else "",
            f"Отряд: <b>{lobby.total}/{lobby.size}</b> · {clock}",
            f"Идут: {names}",
            "",
            "Уровень не важен — берут любого. Босс подстроится под отряд и "
            "будет выше него на четыре уровня.",
            "Наберётся полный отряд — выходим сразу, ждать не будем. "
            "Не хочется ждать — созвавший жмёт «Выходим сейчас».",
        ]
    )


def raid_intro(session) -> str:
    """Кто спустился в подвал и что их там встретило."""
    enemy = session.enemy
    party = ", ".join(
        f"<b>{esc(fighter.name)}</b> [{fighter.level}]"
        for fighter in session.fighters.values()
    )
    return "\n".join(
        [
            f"{session.boss.emoji} <b>{esc(enemy.name)}</b>, "
            f"{enemy.level} уровень, {enemy.max_hp} здоровья",
            f"{enemy.fclass.emoji} {enemy.fclass.title} · бьёт {enemy.weapon}",
            "",
            f"Отряд ({len(session.fighters)}): {party}",
            "",
            "Бьём по очереди или разом — как выйдет. Кто промолчит, тот "
            "пропустит удар, но получит своё.",
        ]
    )


def raid_board(session) -> list[str]:
    """Табло рейда: сверху босс, под ним отряд по двое в ряд."""
    enemy = session.enemy
    lines = [
        f"{session.boss.emoji} {esc(enemy.name)} [{enemy.level}]",
        f"[{enemy.hp}/{enemy.max_hp}]  {color_bar(enemy.hp, enemy.max_hp, 10)}",
        "",
    ]
    party = list(session.fighters.items())
    for index in range(0, len(party), 2):
        row = party[index : index + 2]
        heads, bars = [], []
        for user_id, fighter in row:
            heads.append(fighter_head(fighter))
            mark = raid_mark(session, user_id, fighter)
            bars.append(
                f"[{fighter.hp}/{fighter.max_hp}] "
                f"{color_bar(fighter.hp, fighter.max_hp)} {mark}"
            )
        if len(row) == 1:
            lines.append(heads[0])
            lines.append(bars[0])
        else:
            lines.append(columns(heads[0], heads[1], RAID_COLUMN))
            lines.append(columns(bars[0], bars[1], RAID_COLUMN))
    return lines


def raid_mark(session, user_id: int, fighter) -> str:
    """Что с бойцом прямо сейчас: отработал, думает или уже не встанет."""
    if not fighter.alive:
        return "💀"
    if user_id in session.acted:
        return "✅"
    return "⏳"


def raid_panel(session, timeout: int) -> str:
    """Панель волны: табло и сколько осталось думать."""
    waiting = len(session.waiting_for())
    lines = [f"<b>🔔 Волна {session.wave}</b>", ""]
    lines.append("<pre>" + "\n".join(raid_board(session)) + "</pre>")
    lines.append("")
    lines.append(
        f"⏱️ {timeout} сек. Удар и блок — в карточке, вкладка «Клуб»: ждём ещё "
        f"{waiting} {plural(waiting, 'бойца', 'бойцов', 'бойцов')}."
    )
    return "\n".join(lines)


def raid_break(session, seconds: int) -> str:
    """Передышка после шести ударов."""
    enemy = session.enemy
    alive = len(session.alive_ids)
    lines = [
        "<b>😮‍💨 Передышка.</b>",
        "",
        f"{session.boss.emoji} {esc(enemy.name)}: {enemy.hp}/{enemy.max_hp}",
        f"На ногах в отряде: {alive} "
        f"{plural(alive, 'боец', 'бойца', 'бойцов')}",
    ]
    if seconds:
        lines += ["", f"Следующая волна через {format_duration(seconds)}."]
    return "\n".join(lines)


def raid_result(
    session, outcome, prizes: dict[int, str] | None = None, reward: int = 0
) -> str:
    """Итог рейда: чем кончилось, кто сколько набил и кому что досталось."""
    from bot.game.equipment import get_item

    enemy = session.enemy
    lines = [f"{outcome.end.emoji} <b>{outcome.end.title}</b>", ""]
    if outcome.won:
        lines.append(
            f"{esc(enemy.name)} падает на {session.wave}-й волне. "
            f"Вышли из подвала: {len(outcome.survivors)} из {len(session.fighters)}."
        )
    elif outcome.draw:
        lines.append(
            f"{esc(enemy.name)} рухнул вместе с последним из отряда. "
            "Подвал забрал всех."
        )
    else:
        lines.append(
            f"{esc(enemy.name)} остаётся на ногах: {enemy.hp}/{enemy.max_hp}. "
            "Отряд кончился."
        )

    lines += ["", "<b>📊 Кто сколько набил</b>"]
    prizes = prizes or {}
    for place, (user_id, damage) in enumerate(outcome.damage, start=1):
        fighter = session.fighters[user_id]
        mark = "💀" if not fighter.alive else fighter.fclass.emoji
        row = f"{place}. {mark} <b>{esc(fighter.name)}</b> — урона {damage}"
        code = prizes.get(user_id)
        if code:
            item = get_item(code)
            row += f", приз: {item.emoji} {esc(item.title)}" if item else ""
        lines.append(row)

    if outcome.won:
        if reward:
            lines += ["", f"💰 Каждому по {reward} 💰."]
        if prizes:
            lines.append("🎁 Троим лучшим по урону — по вещи с прилавка.")
    else:
        lines += ["", "Награды за такое не дают. В другой раз."]
    return "\n".join(lines)


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
    session, results, fallen: list[int], said: list[list[str]] | None = None
) -> str:
    """Разбор всех пар за один ход. Про выбывших говорим один раз — когда упали.

    `said` — слова судьи по каждой паре, собранные один раз. Их передают
    сюда, а не сочиняют заново: те же строки уходят и в лог боя, который
    читает карточка.
    """
    lines = [f"<b>⚔️ Раунд {session.round_number}</b>"]
    for spoken in said or []:
        lines.append("")
        lines.extend(spoken)
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
        if delta:
            sign = "+" if report.rating_delta > 0 else "-"
            parts.append(
                f"{sign}{delta} {plural(delta, 'очко', 'очка', 'очков')} рейтинга"
            )
        else:
            parts.append("рейтинг без изменений")
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
