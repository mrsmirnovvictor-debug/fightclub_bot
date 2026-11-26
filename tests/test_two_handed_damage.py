"""Двуручный хват: обеими руками бьют сильнее, чем написано на вещи.

Хват — это выбор, и у выбора должна быть отдача. Двуручное занимает обе
руки: рядом с ним нельзя держать ни щит, ни второе оружие, — и без
прибавки к урону он был бы просто запретом. Множитель живёт там же, где
усиление комплекта (`Equipment.grown`): всё, что считает экипировку,
спрашивает вещь в одном месте, и ни урон в бою, ни число в кукле не
расходятся с правилом.

Числа каталога при этом не трогаются: на прилавке у вещи написано своё,
как и у оружия комплекта, которое на собранном бойце бьёт сильнее
лавочного.
"""

import random

from bot.game.classes import Stats, get_class
from bot.game.combat import Action, Fighter, Zone, resolve_round
from bot.game.equipment import (
    CATALOGUE,
    Equipment,
    OwnedItem,
    Slot,
    two_handed_damage,
)
from bot.game.gear import TWO_HANDED_DAMAGE

HEAVY = CATALOGUE["greatsword"]
LIGHT = CATALOGUE["katana"]


def worn(code: str, slot: Slot = Slot.WEAPON) -> Equipment:
    item = CATALOGUE[code]
    return Equipment(items={slot: OwnedItem(item=item, slot=slot)})


def test_the_hold_raises_the_damage_and_nothing_else():
    """Растёт урон. Прибавки, запас и доли хват не трогает.

    Множитель, который задевал бы точность или прибавку к силе, сделал
    бы двуручное лучше во всём сразу — а оно о другом: обеими руками
    бьют сильнее, а не видят дальше.
    """
    grown = two_handed_damage(HEAVY)

    assert grown.damage_min == int(HEAVY.damage_min * TWO_HANDED_DAMAGE + 0.5)
    assert grown.damage_max == int(HEAVY.damage_max * TWO_HANDED_DAMAGE + 0.5)
    assert grown.strength == HEAVY.strength
    assert grown.agility == HEAVY.agility
    assert grown.intuition == HEAVY.intuition
    assert grown.hp == HEAVY.hp
    assert (grown.accuracy, grown.crit, grown.dodge) == (
        HEAVY.accuracy, HEAVY.crit, HEAVY.dodge,
    )


def test_a_one_handed_weapon_keeps_its_own_numbers():
    """Одноручному хват ничего не прибавляет: он не двуручный."""
    assert two_handed_damage(LIGHT) is LIGHT
    assert two_handed_damage(CATALOGUE["club_tee"]) is CATALOGUE["club_tee"]


def test_the_counter_still_shows_what_is_written_on_the_thing():
    """На прилавке у вещи своё число: множитель — про бойца, а не про лавку."""
    assert CATALOGUE["greatsword"].describe_damage() == "15–19"
    assert HEAVY.damage_max == 19, "каталог не тронут"


def test_the_doll_shows_the_damage_the_fighter_really_deals():
    """В кукле стоит то число, с которым боец выйдет на ринг."""
    equipment = worn("greatsword")
    shown = equipment.grown(equipment.weapon)

    assert shown.damage_min == 23 and shown.damage_max == 29
    assert equipment.weapon_damage == (23, 29)
    assert equipment.weapon_damage_max() == 29


def test_the_hold_and_the_set_multiply_one_after_another():
    """Комплект и хват считаются подряд, а не вместо друг друга.

    Меч героя двуручный и притом комплектный: собранный набор поднимает
    его урон, хват поднимает ещё раз. Оба правила про одно и то же
    число, и считать их порознь значило бы потерять одно из них.
    """
    from bot.game.sets import set_power

    hero = CATALOGUE["set_hero_weapon"]
    kit = {Slot.WEAPON: OwnedItem(item=hero, slot=Slot.WEAPON)}
    # Один меч — комплекта нет, хват есть
    alone = Equipment(items=dict(kit))
    assert alone.grown(alone.weapon).damage_max == int(
        hero.damage_max * TWO_HANDED_DAMAGE + 0.5
    )

    # Четыре вещи набора — первая ступень лестницы и тот же хват
    for slot, code in (
        (Slot.HEAD, "set_hero_head"),
        (Slot.BELT, "set_hero_belt"),
        (Slot.BOOTS, "set_hero_boots"),
    ):
        kit[slot] = OwnedItem(item=CATALOGUE[code], slot=slot)
    dressed = Equipment(items=kit)
    power = set_power(4)
    assert power > 1.0
    expected = int(int(hero.damage_max * power + 0.5) * TWO_HANDED_DAMAGE + 0.5)
    assert dressed.grown(dressed.weapon).damage_max == expected


def test_the_hold_reaches_the_health_of_the_other_fighter():
    """Прибавка доходит до здоровья противника, а не остаётся в карточке.

    Сравниваются две одинаковые вещи — те же числа, тот же уровень, —
    и отличаются они только хватом: всё, что между правилом и уроном,
    остаётся тем же, и разницу делает именно хват.
    """
    from dataclasses import replace

    from bot.game.gear import WeaponHand

    fclass = get_class("warrior")
    stats = Stats(strength=30, agility=10, intuition=10, endurance=20)
    probe = replace(LIGHT, code="probe_hold")

    hits: dict[str, int] = {}
    for name, hand in (
        ("двуручное", WeaponHand.TWO_HANDED),
        ("одноручное", WeaponHand.MAIN),
    ):
        item = replace(probe, hand=hand)
        total = 0
        for seed in range(60):
            attacker = Fighter(
                user_id=1,
                name=name,
                fclass=fclass,
                stats=stats,
                level=10,
                equipment=Equipment(
                    items={Slot.WEAPON: OwnedItem(item=item, slot=Slot.WEAPON)}
                ),
            )
            target = Fighter(
                user_id=2,
                name="мешок",
                fclass=get_class("tank"),
                stats=Stats(strength=10, agility=2, intuition=2, endurance=30),
                level=14,
                hp=10**6,
            )
            before = target.hp
            resolve_round(
                attacker,
                Action(attacks=(Zone.HEAD,), block=Zone.LEGS),
                target,
                Action(attacks=(Zone.HEAD,), block=Zone.LEGS),
                1,
                rng=random.Random(seed),
            )
            total += before - target.hp
        hits[name] = total

    assert hits["двуручное"] > hits["одноручное"], hits
