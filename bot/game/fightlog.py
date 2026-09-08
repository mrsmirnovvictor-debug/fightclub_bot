"""Разбор боя по ходам — тот же и на экране, и в истории.

Движок отдаёт `RoundResult` с объектами; здесь он превращается в простые
словари. Одна форма на два применения: мини-апп рисует по ней живой бой, а
база кладёт её в лог, чтобы потом было что показать в истории.

Ни правил, ни телеграма: чистый перевод. Поэтому модуль лежит в `game` — на
него смотрят и сервис боёв, и веб, и ни один не тянет за собой другой.
"""

from __future__ import annotations

from typing import Any

from bot.game.classes import ZONE_PREPOSITIONAL
from bot.game.narrator import plain
from bot.game.combat import Outcome, RoundResult, Strike, boxing_round, turn_in_round

# Как назвать исход удара. Значки те же, что в логе боя в ветке: лог один,
# и читаться он должен одинаково, где бы ни показывался.
OUTCOME_TITLES: dict[Outcome, tuple[str, str]] = {
    Outcome.HIT: ("👊", "попал"),
    Outcome.CRIT: ("🩸", "крит"),
    Outcome.BREAK: ("🛡🩸", "пробил блок"),
    Outcome.BLOCK: ("🛡", "в блок"),
    Outcome.DODGE: ("🌀", "мимо"),
    Outcome.COUNTER: ("🔄", "контрудар"),
    Outcome.SKIP: ("🤲", "не бил"),
}


def strike_payload(strike: Strike) -> dict[str, Any]:
    """Один удар: кто, куда, чем кончилось и сколько снял."""
    emoji, title = OUTCOME_TITLES[strike.outcome]
    return {
        "attacker_id": strike.attacker_id,
        "defender_id": strike.defender_id,
        "zone": strike.zone.value if strike.zone else None,
        "zone_title": strike.zone.title.capitalize() if strike.zone else "",
        # «в голову», «по ногам» — падеж берём из той же таблицы, по которой
        # говорит судья в ветке: иначе выходит «в голова»
        "zone_where": ZONE_PREPOSITIONAL[strike.zone] if strike.zone else "",
        "outcome": strike.outcome.value,
        "emoji": emoji,
        "title": title,
        "weapon": strike.weapon,
        "damage": strike.damage,
        "counter": strike.counter_damage,
        "armor": strike.armor,
        "hp_after": strike.defender_hp_after,
        "missed_turn": strike.missed_turn,
    }


def turn_payload(result: RoundResult, lines: list[str] | None = None) -> dict[str, Any]:
    """Ход целиком: номера, оба размена и слова судьи о них.

    `lines` — то, что судья уже сказал в ветку, слово в слово. Их передают
    сюда, а не собирают заново: формулировку судья выбирает броском, и
    второй заход дал бы про тот же удар другие слова.
    """
    return {
        "number": result.number,
        "round": boxing_round(result.number),
        "turn": turn_in_round(result.number),
        "strikes": [strike_payload(strike) for strike in result.strikes],
        "hp_after": {str(uid): hp for uid, hp in result.hp_after.items()},
        "finished": result.finished,
        "winner_id": result.winner_id,
        # Комментарий судьи без разметки: мини-апп рисует его как текст
        "lines": [plain(line) for line in lines or []],
    }


__all__ = ["OUTCOME_TITLES", "strike_payload", "turn_payload"]
