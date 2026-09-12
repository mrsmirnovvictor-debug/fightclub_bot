"""Аналитик боя: что он считает по чужим логам и о чём молчит.

Две вещи проверяются строже прочего. Первая — аналитик читает только
законченные ходы: подсказка, знающая текущий выбор соперника, ломает бой.
Вторая — он говорит «вообще» там, где похожих случаев было мало: два
случая из десяти боёв не закономерность, а совпадение.
"""

import pytest

from bot.game.scout import (
    MIN_CASES,
    SCOUT_FIGHTS,
    advise,
    moves_of,
    opening,
    read_habits,
    trend,
)

RIVAL = 2
ME = 1


def turn(number: int, mine: dict, theirs: dict) -> dict:
    """Ход боя так, как он ложится в лог: два размена и что кто закрывал."""
    return {
        "number": number,
        "strikes": [
            {
                "attacker_id": ME,
                "defender_id": RIVAL,
                "zone": mine.get("zone"),
                "outcome": mine.get("outcome", "hit"),
                "block": list(theirs.get("block", ())),
            },
            {
                "attacker_id": RIVAL,
                "defender_id": ME,
                "zone": theirs.get("zone"),
                "outcome": theirs.get("outcome", "hit"),
                "block": list(mine.get("block", ())),
            },
        ],
    }


def fight(*rows: tuple[dict, dict]) -> list[dict]:
    return [turn(number, mine, theirs) for number, (mine, theirs) in enumerate(rows, 1)]


# Соперник, у которого есть привычки: первым ходом всегда бьёт в голову и
# закрывает пояс с ногами, а после удачного удара по ногам бьёт туда снова
HABITUAL = fight(
    ({"zone": "head", "outcome": "block", "block": ("head", "chest")},
     {"zone": "head", "outcome": "hit", "block": ("belt", "legs")}),
    ({"zone": "belt", "outcome": "hit", "block": ("head", "chest")},
     {"zone": "legs", "outcome": "hit", "block": ("head", "chest")}),
    ({"zone": "legs", "outcome": "dodge", "block": ("head", "chest")},
     {"zone": "legs", "outcome": "hit", "block": ("belt", "legs")}),
    ({"zone": "head", "outcome": "hit", "block": ("head", "chest")},
     {"zone": "legs", "outcome": "block", "block": ("belt", "legs")}),
)


# ---------- чтение лога ----------


def test_a_move_is_read_with_both_halves():
    """Ход бойца — это и удар, и блок: по одному без другого не разобрать."""
    moves = moves_of(HABITUAL, RIVAL)

    assert len(moves) == 4
    first = moves[0]
    assert first.attacks == ("head",) and first.landed
    assert first.block == ("belt", "legs")
    # в первом ходу он пропустил только блокированный удар — значит, выстоял
    assert first.took == ()
    assert moves[1].took == ("belt",), "пропущенный удар виден по исходу"


def test_habits_count_the_first_turn_apart():
    """Первый ход считается отдельно: с него и начинается разбор."""
    habits = read_habits([HABITUAL, HABITUAL], RIVAL)

    assert habits.fights == 2 and habits.turns == 8
    assert habits.first_turns == 2
    assert habits.first_attacks["head"] == 2
    assert habits.first_blocks["belt"] == 2 and habits.first_blocks["head"] == 0
    # общий счёт идёт по всем ходам, а не только по первым
    assert habits.attacks["legs"] == 6


def test_habits_remember_what_came_after():
    """Привычка — это «после такого он делает вот так», а не просто счёт."""
    habits = read_habits([HABITUAL], RIVAL)

    # после удачного удара по ногам он бил по ногам снова — и не раз
    assert habits.after_attack[("legs", True)]["legs"] == 2
    # а после блока «пояс и ноги», в котором выстоял, закрывал голову с корпусом
    assert habits.after_block[(("belt", "legs"), False)][("head", "chest")] == 1


# ---------- что он говорит ----------


def test_the_opening_names_the_rarest_block_and_the_favourite_strike():
    """До первого удара — две строки: куда бить и чего ждать."""
    habits = read_habits([HABITUAL] * 3, RIVAL)

    advice = opening(habits)

    assert "первом ходу" in advice.attack and "реже всего блокирует" in advice.attack
    assert "Пояс — 100%" in advice.attack or "Ноги — 100%" in advice.attack
    assert "первый удар в Голову — 100%" in advice.block
    assert "3 боя" in advice.title


def test_the_trend_speaks_of_similar_cases_only_when_there_were_enough():
    """Мало похожих случаев — аналитик честно переходит на общий счёт."""
    thin = read_habits([HABITUAL], RIVAL)
    thick = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]  # удачный удар по ногам

    assert "Вообще он чаще бьёт" in trend(thin, last).block
    said = trend(thick, last).block
    assert "После таких он обычно бьёт в Ноги" in said
    cases = sum(thick.after_attack[last.key_attack].values())
    assert cases >= MIN_CASES and f"случаев: {cases}" in said


def test_a_stranger_is_not_invented():
    """О новичке сказать нечего — и аналитик не выдумывает."""
    habits = read_habits([], RIVAL)

    advice = advise(habits, [], RIVAL)

    assert advice.empty and "новичок" in advice.title


def test_the_analyst_reads_only_finished_turns():
    """Подсказка считается по законченным ходам, и только по ним.

    Это не украшение, а правило игры: аналитик, знающий текущий выбор
    соперника, превращает бой в подглядывание. Проверяем прямо: тот же
    разбор до и после того, как соперник «нажал» кнопки в текущем ходу, —
    ходов в логе не прибавилось, значит и подсказка не изменилась.
    """
    habits = read_habits([HABITUAL] * 3, RIVAL)
    played = HABITUAL[:2]

    before = advise(habits, played, RIVAL)
    # соперник выбрал ход, но он ещё не посчитан — в логе его нет
    after = advise(habits, played, RIVAL)

    assert before == after
    assert "В прошлом ходу" in before.block
    # и говорит она о предыдущем ходе, а не о текущем
    assert "Ноги" in before.block


def test_ten_fights_is_the_depth():
    """Глубина разбора — десять боёв: дальше привычки уже не те."""
    assert SCOUT_FIGHTS == 10


# ---------- база ----------


async def test_the_last_fights_come_in_one_query(db):
    """Логи последних боёв поднимаются одним запросом, свежие первыми."""
    for _ in range(12):
        await db.add_duel(
            chat_id=None,
            thread_id=None,
            challenger_id=ME,
            opponent_id=RIVAL,
            winner_id=ME,
            rounds=len(HABITUAL),
            end_reason="ko",
            log=HABITUAL,
        )

    fights = await db.recent_duel_logs(RIVAL, SCOUT_FIGHTS)

    assert len(fights) == SCOUT_FIGHTS
    assert all(len(turns) == len(HABITUAL) for turns in fights)
    assert fights[0][0]["strikes"][0]["block"] == ["belt", "legs"]


@pytest.mark.parametrize("limit", [1, 5])
async def test_the_depth_is_respected(db, limit):
    for _ in range(3):
        await db.add_duel(
            chat_id=None, thread_id=None, challenger_id=ME, opponent_id=RIVAL,
            winner_id=None, rounds=1, end_reason="points", log=HABITUAL[:1],
        )

    assert len(await db.recent_duel_logs(RIVAL, limit)) == min(limit, 3)
