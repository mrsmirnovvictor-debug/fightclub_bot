"""Владение оружием: лестница очков, пороги вещей и рекомендации классов.

Проверяется то, на чём держится вся система: что очков у бойца ровно
столько, сколько уровней, что порог вещи равен её уровню — и, значит,
специалист носит оружие своего уровня, а распылившийся платит за широту
верхними ступенями, — и что рекомендация класса ничего не запрещает.
"""

from bot.game.classes import FIGHTER_CLASSES
from bot.game.mastery import (
    ALL_FAMILIES,
    ALL_HOLDS,
    HoldType,
    START_POINTS,
    WeaponFamily,
    advice_for,
    enough,
    free_points,
    may_spend,
    points_at,
    required_for,
    spent,
)


def test_the_pool_grows_by_one_point_a_level():
    """Очков в пуле ровно столько, сколько уровней у бойца.

    На этом держится порог вещей: специалист носит оружие своего уровня
    потому, что вложить ему есть ровно столько, сколько оно требует.
    """
    assert START_POINTS == 1
    assert [points_at(level) for level in (1, 2, 7, 10)] == [1, 2, 7, 10]
    # Ниже первого уровня бойцов не бывает, и пустого пула тоже
    assert points_at(0) == 1


def test_the_first_level_already_has_a_point_in_each_pool():
    """Новичок начинает с очком вида и очком хвата.

    Без него стартовый персонаж не мог бы взять даже дубинку первого
    уровня — она требует владения в единицу, — и развивать владение
    было бы нечем до второго уровня.
    """
    assert points_at(1) == 1
    assert free_points(1, {}) == 1
    assert free_points(1, {WeaponFamily.CLUBS.value: 1}) == 0


def test_a_weapon_asks_for_mastery_equal_to_its_level():
    """Порог равен уровню вещи — и ничему другому.

    Меч седьмого уровня требует семи очков в мечах. Игрок видит уровень
    на прилавке и сразу знает, сколько в него вложить.
    """
    assert [required_for(level) for level in (1, 3, 7, 10)] == [1, 3, 7, 10]
    assert enough(7, 7) and not enough(6, 7)
    # Чужое владение не помогает: считают вложенное в нужное направление
    assert not enough(0, 1)
    assert enough(10, 7), "сверх порога — тоже можно"


def test_a_specialist_wears_his_level_and_a_scatterer_does_not():
    """Вся цена распыления: два направления — оружие пониже.

    Седьмой уровень даёт семь очков. Вложил все в мечи — носит меч
    седьмого. Разложил поровну — носит по четвёртому, зато обе ветки.
    """
    whole = {WeaponFamily.SWORDS.value: 7}
    split = {WeaponFamily.SWORDS.value: 4, WeaponFamily.CLUBS.value: 3}

    assert spent(whole) == spent(split) == points_at(7)
    assert enough(whole[WeaponFamily.SWORDS.value], 7)
    assert not enough(split[WeaponFamily.SWORDS.value], 7)
    assert enough(split[WeaponFamily.SWORDS.value], 4)
    assert enough(split[WeaponFamily.CLUBS.value], 3)


def test_nobody_spends_more_than_he_has():
    """Больше свободного остатка не вложить, и очки не берутся из воздуха."""
    points = {WeaponFamily.HAMMERS.value: 3}

    assert free_points(5, points) == 2
    assert may_spend(5, points, 1) and may_spend(5, points, 2)
    assert not may_spend(5, points, 3)
    assert not may_spend(5, points, 0), "нулевое вложение — не вложение"
    assert not may_spend(5, points, -1)
    # Перебор в базе остатка не рождает: минус не бывает
    assert free_points(5, {WeaponFamily.HAMMERS.value: 9}) == 0


def test_the_two_pools_never_mix():
    """Вид и хват считаются порознь: очко из одного в другой не переносится.

    Пул общий сделал бы выбор ложным: взял щит — остался без оружия
    своего уровня, и все ходили бы одинаково.
    """
    families = {WeaponFamily.SWORDS.value: 7}
    holds = {HoldType.SHIELD.value: 2}

    # Семь в мечах не мешают держать щит второго уровня и не помогают
    # держать щит седьмого
    assert free_points(7, families) == 0
    assert free_points(7, holds) == 5
    assert enough(holds[HoldType.SHIELD.value], 2)
    assert not enough(holds[HoldType.SHIELD.value], 7)


def test_every_class_is_advised_something_and_forbidden_nothing():
    """У каждого класса есть свой вид и свой хват, но запретов нет.

    Рекомендация — подсказка экрана. Любой класс вкладывает очки в
    любое направление: танк с мечами законен, просто непривычен.
    """
    for code in FIGHTER_CLASSES:
        families, holds = advice_for(code)
        assert families, code
        assert holds, code

    # Каждый вид кому-то рекомендован, и каждый — ровно одному классу:
    # четыре вида на четыре класса, без любимчиков
    advised = [one.advised for one in ALL_FAMILIES]
    assert sorted(sum(advised, ())) == sorted(FIGHTER_CLASSES)
    # А хваты пересекаются: второе оружие и щит сподручны не одному
    assert all(one.advised for one in ALL_HOLDS)


def test_the_names_are_the_ones_the_task_asked_for():
    """Коды видов и хватов — те же, что в постановке и в базе.

    По ним считается всё остальное: разметка каталога, колонки базы и
    поля экрана. Переименовать их молча нельзя — разойдётся с тем, что
    уже вложено у игроков.
    """
    assert [one.value for one in ALL_FAMILIES] == [
        "knives_knuckles", "clubs_maces", "hammers_axes", "swords"
    ]
    assert [one.value for one in ALL_HOLDS] == ["offhand", "two_handed", "shield"]
    assert WeaponFamily.KNIVES.title == "Ножи и кастеты"
    assert HoldType.TWO_HANDED.title == "Двуручное оружие"
