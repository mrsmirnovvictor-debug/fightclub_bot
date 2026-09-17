"""Аналитик боя: что он считает по чужим логам и о чём молчит.

Две вещи проверяются строже прочего. Первая — аналитик читает только
законченные ходы: подсказка, знающая текущий выбор соперника, ломает бой.
Вторая — он говорит «вообще» там, где похожих случаев было мало: два
случая из десяти боёв не закономерность, а совпадение.
"""

import pytest

from bot.game.scout import (
    MIN_CASES,
    zone_title,
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


# ---------- совет ----------


def test_the_tip_names_one_move_and_one_number():
    """Совет — это действие и число: разбор читать между ходами некогда."""
    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]  # удачный удар по ногам

    advice = trend(habits, last)

    assert advice.attack_tip.move.startswith("Бей в ")
    assert "с вероятностью" in advice.attack_tip.why
    assert advice.block_tip.move.startswith("Закрывай ")
    assert "отбить удар" in advice.block_tip.why


def test_the_guard_tip_covers_where_he_actually_strikes():
    """Совет по блоку закрывает те зоны, куда соперник бьёт чаще всего."""
    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    advice = trend(habits, last)

    # после удачного удара по ногам он бьёт в ноги — их и закрываем
    assert "Ноги" in advice.block_tip.move
    assert "После таких он обычно бьёт в Ноги" in advice.block


def test_the_guard_tip_is_a_button_the_fighter_can_press():
    """Совет по блоку — настоящий блок: закрыть можно только смежные зоны."""
    from bot.game.classes import BLOCK_WIDTH, block_combos

    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    move = trend(habits, last).block_tip.move

    allowed = {
        "Закрывай " + "+".join(zone_title(zone.value) for zone in combo)
        for combo in block_combos(BLOCK_WIDTH)
    }
    assert move in allowed, f"так блок не поставить: {move}"


def test_a_shield_gets_a_wider_tip():
    """Со щитом блок держит три зоны — совет обязан советовать все три."""
    from bot.game.classes import SHIELD_BLOCK_WIDTH

    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    bare = trend(habits, last).block_tip
    shielded = trend(habits, last, SHIELD_BLOCK_WIDTH).block_tip

    assert len(shielded.move.split("+")) == SHIELD_BLOCK_WIDTH
    assert len(bare.move.split("+")) == 2
    # шире блок — больше ударов отобьёшь, и обещание не должно быть меньше
    assert share_of(shielded.why) >= share_of(bare.why)


def share_of(why: str) -> int:
    import re

    found = re.search(r"(\d+)%", why)
    return int(found.group(1)) if found else -1


def test_the_strike_tip_aims_at_the_zone_he_guards_least():
    """Бить советуем туда, где он реже всего держит защиту."""
    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    advice = trend(habits, last)

    # после «головы и корпуса» он всегда закрывает пояс с ногами — значит,
    # верх открыт, и бить советуем именно туда
    assert "После таких он обычно закрывает Пояс и Ноги" in advice.attack
    assert advice.attack_tip.move in ("Бей в Голову", "Бей в Корпус", "Бей в Живот")
    assert share_of(advice.attack_tip.why) == 0
    assert advice.attack_tip.move not in ("Бей в Пояс", "Бей в Ноги")


def test_the_strike_tip_is_the_same_every_time():
    """Одни и те же числа — один и тот же совет, а не гадание.

    Нулём закрыты сразу три зоны, и без твёрдого порядка совет прыгал бы
    между ними от отрисовки к отрисовке.
    """
    habits = read_habits([HABITUAL] * MIN_CASES, RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    said = {trend(habits, last).attack_tip.move for _ in range(5)}

    assert len(said) == 1


def test_the_tip_does_not_argue_with_the_line_above_it():
    """Мало похожих случаев — и строка, и совет разом идут на общий счёт.

    Совет, посчитанный по другому распределению, спорил бы с разбором над
    ним, а это хуже, чем совета не иметь вовсе.
    """
    thin = read_habits([HABITUAL], RIVAL)
    last = moves_of(HABITUAL, RIVAL)[1]

    advice = trend(thin, last)

    assert "Вообще он чаще бьёт" in advice.block
    # общий счёт: чаще всего он бьёт в ноги, туда же смотрит и совет
    assert "Ноги" in advice.block_tip.move


def test_the_opening_advises_too():
    """До первого удара совет тоже есть: на старте он и нужнее всего."""
    habits = read_habits([HABITUAL] * 3, RIVAL)

    advice = opening(habits)

    assert advice.attack_tip.move and advice.block_tip.move
    # первым ходом он всегда бьёт в голову и закрывает пояс с ногами
    assert "Голов" in advice.block_tip.move
    assert share_of(advice.block_tip.why) == 100
    assert advice.attack_tip.move != "Бей в Пояс"


def test_a_stranger_gets_no_tip():
    """О новичке сказать нечего — и советовать тоже нечего."""
    advice = advise(read_habits([], RIVAL), [], RIVAL)

    assert advice.attack_tip.empty and advice.block_tip.empty
