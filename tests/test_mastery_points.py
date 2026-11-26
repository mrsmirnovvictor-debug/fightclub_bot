"""Очки владения в карточке бойца: откуда берутся и как ложатся.

Запас обоих пулов считается от уровня, а не копится в базе, и этим
закрыт весь контракт про начисление: перескок уровней ничего не теряет,
повторный запрос ничего не создаёт, а начислить дважды попросту нечего.
Проверяется здесь именно это — плюс то, что пулы не смешиваются и что
вложенное переживает сохранение.
"""

import pytest

from bot.game.mastery import HoldType, WeaponFamily, points_at
from bot.mastery_service import MasteryError, invest, pool_of
from bot.models import Player

SWORDS = WeaponFamily.SWORDS.value
CLUBS = WeaponFamily.CLUBS.value
SHIELD = HoldType.SHIELD.value
TWO_HANDED = HoldType.TWO_HANDED.value


def a_fighter(level: int = 5, **kwargs) -> Player:
    return Player(
        user_id=42,
        nickname="Тайлер",
        class_code="warrior",
        level=level,
        strength=12,
        agility=8,
        intuition=3,
        endurance=12,
        **kwargs,
    )


async def test_the_pools_come_from_the_level_and_not_from_the_base(db):
    """У нового бойца очки есть сразу, и ровно по уровню.

    Начислять их нечем и незачем: запас — это уровень минус вложенное.
    Поэтому и у заведённого до этой системы бойца они появляются сами.
    """
    one = a_fighter(level=7)
    await db.save_player(one)

    saved = await db.get_player(42)
    assert saved.mastery_free == points_at(7) == 7
    assert saved.holds_free == 7
    assert saved.mastery == {} and saved.holds == {}


async def test_a_level_jump_grants_both_pools_exactly_once(db):
    """Перескок через несколько уровней не теряет и не удваивает очки."""
    one = a_fighter(level=1)
    await db.save_player(one)
    await invest(db, one, SWORDS)
    assert one.mastery_free == 0

    # Крупная победа может поднять сразу несколько уровней
    one.level = 5
    await db.save_player(one)

    saved = await db.get_player(42)
    assert saved.mastery_in(SWORDS) == 1
    assert saved.mastery_free == 4, "четыре новых уровня — четыре очка"
    assert saved.holds_free == 5, "в хватах не вложено ничего"


async def test_a_point_goes_where_it_was_asked_and_stays_there(db):
    """Вложенное очко сохраняется и читается обратно тем же."""
    one = a_fighter(level=5)
    await db.save_player(one)

    assert await invest(db, one, SWORDS, 3) == 3
    assert await invest(db, one, SHIELD, 2) == 2

    saved = await db.get_player(42)
    assert saved.mastery == {SWORDS: 3}
    assert saved.holds == {SHIELD: 2}
    assert saved.mastery_free == 2 and saved.holds_free == 3


async def test_nobody_spends_more_than_the_level_gave(db):
    """Больше свободного остатка не вложить, и отказ говорит сколько осталось."""
    one = a_fighter(level=3)
    await db.save_player(one)
    await invest(db, one, SWORDS, 3)

    with pytest.raises(MasteryError) as oops:
        await invest(db, one, CLUBS)
    assert "0" in str(oops.value)

    # И повторный запрос ничего не создаёт: остаток считается заново
    saved = await db.get_player(42)
    assert saved.mastery_free == 0
    assert saved.mastery == {SWORDS: 3}


async def test_the_two_pools_never_lend_each_other_a_point(db):
    """Очко вида не уходит в хват, даже если в хватах пусто.

    Пул выбирает сервер по коду направления: приезжай он с нажатием, его
    можно было бы подменить и перелить очко из пула в пул.
    """
    one = a_fighter(level=2)
    await db.save_player(one)
    await invest(db, one, TWO_HANDED, 2)

    assert one.holds_free == 0
    assert one.mastery_free == 2, "вид не тронут"
    assert pool_of(TWO_HANDED) == "hold"
    assert pool_of(SWORDS) == "family"
    assert pool_of("нет такого") == ""


async def test_a_nonsense_request_changes_nothing(db):
    """Чужой код и нулевое вложение — отказ, а не тихая запись."""
    one = a_fighter(level=5)
    await db.save_player(one)

    for code, amount in ((SWORDS, 0), (SWORDS, -2), ("лазерный меч", 1)):
        with pytest.raises(MasteryError):
            await invest(db, one, code, amount)

    saved = await db.get_player(42)
    assert saved.mastery == {} and saved.mastery_free == 5


async def test_a_broken_line_in_the_base_does_not_break_the_card(db):
    """Строка владения из чужой версии не роняет карточку.

    Разбор отбрасывает коды, которых нет, и мусор: иначе одна кривая
    запись закрывала бы бойцу вход в игру.
    """
    one = a_fighter(level=5)
    await db.save_player(one)
    await db.conn.execute(
        "UPDATE players SET mastery = ?, holds = ? WHERE user_id = 42",
        ("swords:3,лом:9,swords,clubs_maces:-1", "shield:x,two_handed:2"),
    )
    await db.conn.commit()

    saved = await db.get_player(42)
    assert saved.mastery == {SWORDS: 3}
    assert saved.holds == {TWO_HANDED: 2}
