"""Доход точек и городская охрана: что осталось от войны за территории.

Прежде этот набор проверял наёмников — покупную охрану, которая стояла
на точке и гибла насовсем. Их заменил отряд (`tests/test_squad.py`,
`tests/test_turf.py`), а здесь остались правила, которые эту замену
пережили:

1. **Городская охрана.** Трое четвёртого уровня у дверей всякой точки,
   которой не правит семья. Она ничья и не покупается.
2. **Доход.** Город берёт с точки вдвое меньше семьи, платят только за
   то время, что точку держали, и только пока на ней кто-то стоит.
"""

from __future__ import annotations

from bot.game.guards import GUARD_LEVEL, GUARD_SIZE, guards
from bot.game.turf import (
    DAY_SECONDS,
    SERVICE_SHARE,
    final_payout,
    get_spot,
    owner_of_clan,
    owner_of_service,
    payout,
    share_of,
)
from bot.turf_service import hand_over, income_of, survey, vault_of
from tests.test_police import cop
from tests.test_robbery import NOW, family, stand
from tests.test_turf import a_guard

BAR = "bar"


# ---------- городская охрана ----------


def test_the_city_guards_are_three_tanks_of_the_fourth_level():
    """Охрана города: трое, четвёртый уровень, и с оружием из лавки."""
    wall = guards()
    assert len(wall) == GUARD_SIZE == 3
    assert all(one.level == GUARD_LEVEL == 4 for one in wall)
    assert all(one.fclass.code == "tank" for one in wall)
    assert all(one.equipment.items for one in wall), "голыми руками не выпускают"
    # Номера отрицательные и разные: судья ищет бойца по номеру, и два
    # одинаковых в одном гарнизоне обрушили бы поиск
    assert all(one.user_id < 0 for one in wall)
    assert len({one.user_id for one in wall}) == GUARD_SIZE


def test_the_city_guard_is_not_for_sale():
    """Охрану города не нанимают: её не продают и никому не считают.

    Это и отличает её от прежних наёмников, которых купили и убрали
    вместе с наймом: тот пост у дверей не покупался и тогда.
    """
    import bot.game.guards as module

    assert not hasattr(module, "hire")
    assert not hasattr(module, "GRADES")


# ---------- доход ----------


def test_the_city_takes_half_of_what_a_family_takes():
    """Город держит точки не ради денег: доля у него половинная."""
    spot = get_spot("casino")
    assert share_of("clan") == 1.0
    assert share_of("service") == SERVICE_SHARE == 0.5
    assert payout(spot, 1, 1 + DAY_SECONDS)[0] == spot.income
    assert payout(spot, 1, 1 + DAY_SECONDS, 0.5)[0] == spot.income // 2


def test_the_leftover_hours_are_paid_at_the_handover():
    """Держал четырнадцать часов — получи за четырнадцать часов."""
    spot = get_spot("casino")
    assert final_payout(spot, 0, 0) == 0
    assert final_payout(spot, 1_000, 1_000) == 0
    assert final_payout(spot, 1_000, 1_000 + DAY_SECONDS) == spot.income
    half = final_payout(spot, 1_000, 1_000 + DAY_SECONDS // 2)
    assert half == spot.income // 2
    assert final_payout(spot, 1_000, 1_000 + DAY_SECONDS, 0.5) == spot.income // 2


async def test_the_handover_pays_the_hours_actually_held(db):
    """Расчёт при смене владельца идёт за неполные сутки."""
    don, _, clan = await family(db)
    spot = get_spot(BAR)
    mine = owner_of_clan(clan.id)
    taken = NOW
    await db.take_turf(BAR, "clan", str(clan.id), taken)
    await survey(db, taken)

    row = await db.turf_row(BAR)
    half = taken + DAY_SECONDS // 2
    paid = await hand_over(db, spot, mine, row.paid_until, half)
    assert paid == spot.income // 2
    assert await vault_of(db, mine) == paid

    # Ничьей точке платить некому
    from bot.game.turf import Owner

    assert await hand_over(db, spot, Owner(), taken, half) == 0


async def test_an_abandoned_spot_pays_nothing(db):
    """Точка платит, только пока на ней кто-то стоит.

    Взятая и брошенная — это дом с вывеской, а не доход. И несчитанные
    сутки не копятся: поставишь бойца завтра — за вчера не дадут.
    """
    don, squad, clan = await family(db)
    spot = get_spot(BAR)
    mine = owner_of_clan(clan.id)
    await db.take_turf(BAR, "clan", str(clan.id), NOW)

    # Двое суток без охраны — в сейфе пусто
    await survey(db, NOW + 2 * DAY_SECONDS)
    assert await vault_of(db, mine) == 0
    assert await income_of(db, mine, NOW + 2 * DAY_SECONDS) == 0

    # Поставили бойца — и вот с этого часа точка платит
    await stand(db, [don.user_id], BAR)
    await a_guard(db, don.user_id, BAR, level=5, name="Охрана")

    await survey(db, NOW + 3 * DAY_SECONDS)
    assert await vault_of(db, mine) == spot.income, "за сутки, а не за трое"
    assert await income_of(db, mine, NOW + 3 * DAY_SECONDS) == spot.income


async def test_the_city_keeps_half_of_the_income(db):
    """Точка под городом платит вдвое меньше, чем под семьёй."""
    from bot.police_service import POLICE

    spot = get_spot(BAR)
    officer = await cop(db, 90, "Сержант")
    officer.location = BAR
    await db.save_player(officer)
    await db.take_turf(BAR, "service", POLICE, NOW)
    await a_guard(db, 90, BAR, level=5, name="Патрульный")

    await survey(db, NOW + DAY_SECONDS)
    assert await vault_of(db, owner_of_service(POLICE)) == spot.income // 2
