"""Наборы: во сколько работает оружие собранного комплекта.

Правила лежат в `bot/game/sets.py`, а складывает их в бой
`Equipment.grown` — через него же они попадают и в карточку. Здесь
проверяется лестница («сколько вещей — столько процентов»), счёт вещей
набора и то, что растёт только оружие.
"""

import pytest

from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot
from bot.game.sets import (
    SET_POWER_LADDER,
    amplified,
    next_step,
    power_percent,
    set_power,
)


def worn(**slots) -> Equipment:
    """Экипировка из кодов: `worn(weapon="set_bouncer_weapon", head=...)`."""
    return Equipment(
        items={
            Slot(slot): OwnedItem(item=CATALOGUE[code], slot=Slot(slot))
            for slot, code in slots.items()
        }
    )


BOUNCER = {
    "weapon": "set_bouncer_weapon",
    "offhand": "set_bouncer_shield",
    "head": "set_bouncer_head",
    "jacket": "set_bouncer_jacket",
    "belt": "set_bouncer_belt",
    "gloves": "set_bouncer_gloves",
    "pants": "set_bouncer_pants",
    "boots": "set_bouncer_boots",
    "necklace": "set_bouncer_necklace",
    "ring_right": "set_bouncer_ring",
    "ring_middle": "set_bouncer_ring",
    "ring_left": "set_bouncer_ring",
}


def kit_of(count: int) -> Equipment:
    """Комплект вышибалы из `count` вещей, и оружие всегда среди них."""
    codes = list(BOUNCER.items())
    return worn(**dict(codes[:count]))


# ---------- лестница ----------


@pytest.mark.parametrize(
    "items, percent",
    [
        (0, 100), (1, 100), (3, 100),  # комплекта нет — оружие как написано
        (4, 120), (5, 120), (6, 120),
        (7, 135), (8, 135),
        (9, 150), (11, 150), (12, 150),
    ],
)
def test_the_ladder_turns_items_into_percents(items, percent):
    """Четыре вещи — 120%, семь — 135%, девять и дальше — 150%.

    Числа владельца, и они же стоят в карточке: ступеней три, между ними
    оружие не растёт вовсе — до четвёртой вещи оно работает как
    написано, и это нарочно. Набор начинается там, где его видно.
    """
    assert power_percent(set_power(items)) == percent


def test_the_ladder_tells_what_is_left_to_the_next_step():
    """Следующая ступень: сколько вещей нужно и что они дадут."""
    assert next_step(0) == (4, 1.20)
    assert next_step(3) == (4, 1.20)
    assert next_step(4) == (7, 1.35)
    assert next_step(6) == (7, 1.35)
    assert next_step(7) == (9, 1.50)
    assert next_step(9) is None  # выше некуда
    assert next_step(12) is None


def test_the_steps_only_grow():
    """Лестница идёт вверх и начинается с единицы: вещь не ослабляет оружие."""
    steps = sorted(SET_POWER_LADDER)
    assert [need for need, _ in steps] == [4, 7, 9]
    powers = [power for _, power in steps]
    assert powers == sorted(powers) and min(powers) > 1.0


# ---------- счёт вещей ----------


def test_the_weapon_counts_itself():
    """Оружие — вещь набора: девятой ступени на восьми тряпках не выйдет."""
    gear = kit_of(9)
    assert gear.kit_worn("bouncer") == 9
    assert gear.kit_power("bouncer") == 1.50

    # Снимаем само оружие — и восемь оставшихся вещей считаются восемью
    without = dict(BOUNCER)
    without.pop("weapon")
    assert worn(**without).kit_worn("bouncer") == 11


def test_every_ring_of_the_set_counts_on_its_own():
    """Кольцо набора одно, а клеток три — и это три вещи, а не одна.

    Иначе до девятой ступени не добрался бы никто: у половины наборов
    всего вещей меньше девяти, и носят их все три кольца разом.
    """
    rings = worn(
        weapon="set_bouncer_weapon",
        ring_right="set_bouncer_ring",
        ring_middle="set_bouncer_ring",
        ring_left="set_bouncer_ring",
    )
    assert rings.kit_worn("bouncer") == 4
    assert rings.kit_power("bouncer") == 1.20


def test_two_weapons_of_one_set_count_twice():
    """Оружие берут и во вторую руку: два одинаковых — две вещи."""
    hands = worn(
        weapon="set_cardsharp_weapon",
        offhand="set_cardsharp_weapon",
        head="set_cardsharp_head",
        jacket="set_cardsharp_jacket",
    )
    assert hands.kit_worn("cardsharp") == 4
    assert hands.kit_power("cardsharp") == 1.20
    # И обе руки работают на одной силе: набор у них один
    assert hands.weapon_damages[0] == hands.weapon_damages[1]


def test_a_stranger_set_does_not_count():
    """Чужие вещи в счёт не идут, даже если они из того же пака."""
    mixed = worn(
        weapon="set_bouncer_weapon",
        head="set_boxer_head",
        jacket="set_boxer_jacket",
        boots="set_boxer_boots",
        belt="set_boxer_belt",
        gloves="set_boxer_gloves",
        pants="set_boxer_pants",
    )
    assert mixed.kit_worn("bouncer") == 1
    assert mixed.kit_worn("boxer") == 6
    assert mixed.kit_power("bouncer") == 1.0
    # Боксёрский комплект собран, но его оружия на бойце нет — и расти
    # дубинке вышибалы от этого нечему
    assert mixed.weapon_damage == (
        CATALOGUE["set_bouncer_weapon"].damage_min,
        CATALOGUE["set_bouncer_weapon"].damage_max,
    )


def test_things_outside_the_sets_have_no_kit():
    """Вещь без набора ничего не считает и ни во что не растёт."""
    plain = worn(weapon="viking_axe", head="moto_helmet", offhand="riot_shield")
    assert plain.kits == {}
    assert plain.kit_worn("") == 0
    axe = CATALOGUE["viking_axe"]
    assert plain.weapon_damage == (axe.damage_min, axe.damage_max)
    assert plain.accuracy == pytest.approx(axe.accuracy)


# ---------- что именно растёт ----------


def test_the_whole_weapon_grows_not_only_its_damage():
    """Растёт всё, что оружие даёт: урон, доли и прибавка к характеристике.

    Доля на оружии — половина его силы, и оставить её в стороне значило
    бы, что собранный комплект ассасина бьёт чаще, но мимо.
    """
    weapon = CATALOGUE["set_bouncer_weapon"]
    strong = amplified(weapon, 1.5)

    # Половинка идёт вверх, а не к чётному, как у `round`: 19 и 23
    # превращаются в 29 и 35, а не в 28 и 34
    assert (weapon.damage_min, weapon.damage_max) == (19, 23)
    assert (strong.damage_min, strong.damage_max) == (29, 35)
    assert strong.accuracy == pytest.approx(weapon.accuracy * 1.5)
    assert strong.anticrit == pytest.approx(weapon.anticrit * 1.5)
    assert strong.strength == 6  # четвёрка и половина сверху
    # Всё остальное — как было: цена, уровень, требования, слот
    assert strong.price == weapon.price
    assert strong.level_required == weapon.level_required
    assert strong.requires == weapon.requires
    assert strong.code == weapon.code


def test_the_power_rounds_to_the_nearest_so_the_step_is_not_lost():
    """Плоские числа округляются к ближайшему, а не вниз.

    На второй ступени 120% от двойки — это 2.4, и вниз оно превратилось
    бы в ту же двойку: комплект не дал бы ничего.
    """
    small = CATALOGUE["set_plut_weapon"]  # 2–5 на третьем уровне
    assert (small.damage_min, small.damage_max) == (2, 5)
    grown = amplified(small, 1.2)
    assert (grown.damage_min, grown.damage_max) == (2, 6)


def test_the_set_grows_only_its_weapon():
    """Лестницу тянет оружие: остальные вещи набора работают как написано."""
    gear = kit_of(9)
    shield = CATALOGUE["set_bouncer_shield"]
    head = CATALOGUE["set_bouncer_head"]

    assert gear.kit_power("bouncer") == 1.50
    assert gear.grown(gear.get(Slot.OFFHAND)) == shield
    assert gear.grown(gear.get(Slot.HEAD)) == head
    # А оружие в тех же руках выросло вполтора раза: 19–23 → 29–35
    assert gear.weapon_damage == (29, 35)


def test_the_growing_weapon_reaches_everything_the_fight_asks():
    """Сила комплекта доезжает до каждого числа, которое спрашивает бой.

    Урон, доли, прибавки к характеристикам и запас здоровья считает
    экипировка — и каждое из этих чисел обязано знать про комплект.
    Иначе половина счёта живёт с набором, а половина без него, и поймать
    такое глазом нельзя: в карточке стоит одно, на ринге другое.

    Оружие тут собрано нарочно щедрым — со всеми пятью долями, запасом
    здоровья и тремя прибавками сразу. В лавке такого нет и не будет, но
    в счёте участвует каждое из этих полей, и проверять их надо все.
    """
    from dataclasses import replace

    rich = replace(
        CATALOGUE["set_bouncer_weapon"],
        hp=10,
        strength=4,
        agility=2,
        intuition=2,
        accuracy=0.3,
        dodge=0.2,
        crit=0.1,
        anticrit=0.3,
        counter=0.1,
    )
    pieces = {
        Slot(slot): CATALOGUE[code]
        for slot, code in list(BOUNCER.items())[1:9]  # восемь вещей без оружия
    }
    gear = Equipment(items={**pieces, Slot.WEAPON: rich})
    assert gear.kit_worn("bouncer") == 9 and gear.kit_power("bouncer") == 1.5

    strong = amplified(rich, 1.5)
    assert strong != rich, "щедрое оружие не выросло вовсе"

    def from_pieces(name: str):
        return sum(getattr(item, name) for item in pieces.values())

    for share in ("accuracy", "dodge", "crit", "anticrit", "counter"):
        assert getattr(gear, share) == pytest.approx(
            from_pieces(share) + getattr(strong, share)
        ), share
    assert gear.hp_bonus == from_pieces("hp") + strong.hp
    for stat in ("strength", "agility", "intuition"):
        assert getattr(gear.bonus, stat) == from_pieces(stat) + getattr(
            strong, stat
        ), stat

    # Урон спрашивают четырьмя способами, и все четыре про комплект
    paw = (strong.damage_min, strong.damage_max)
    assert gear.weapon_damage == paw
    assert gear.weapon_damages == (paw,)
    assert gear.weapon_damage_max() == strong.damage_max
    rolls = {gear.roll_weapon_damage(0) for _ in range(200)}
    assert min(rolls) >= strong.damage_min and max(rolls) == strong.damage_max

    # А броня на тряпках осталась своей: щит и шлем в наборе не растут
    from bot.game.classes import Zone

    assert gear.armor_range(Zone.HEAD) == (
        sum(item.armor_min for item in pieces.values() if Zone.HEAD in item.zones),
        sum(item.armor_max for item in pieces.values() if Zone.HEAD in item.zones),
    )


def test_the_sharpened_weapon_grows_from_what_the_smith_left():
    """Заточка и комплект складываются, а не отменяют друг друга.

    Мастерская точит вещь, набор её усиливает — и усиливает уже
    наточенное: в бой идёт то, что боец видит в карточке.
    """
    from bot.content.mods import get_mod
    from bot.game.gear import modified

    weapon = CATALOGUE["set_bouncer_weapon"]
    sharp = OwnedItem(item=weapon, id=1, slot=Slot.WEAPON)
    sharp.modify("sharpen_weapon_2", 5)
    gear = kit_of(9)
    gear.items[Slot.WEAPON] = sharp

    whetted = modified(weapon, get_mod("sharpen_weapon_2"), 5)
    assert (whetted.damage_min, whetted.damage_max) == (24, 28)
    assert gear.weapon_damage == (36, 42)


def test_the_power_lands_in_the_card_as_a_line():
    """Строка комплекта в карточке: сколько надето и что это даёт.

    Лестницу считает сервер, а не страница: иначе два места считали бы
    одно и то же по-разному — и расходились бы на первой же правке.
    """
    from bot.webapp.card import kit_payload

    weapon = CATALOGUE["set_bouncer_weapon"]
    empty = kit_payload(weapon)
    assert empty["title"] == "Вышибала" and empty["worn"] == 0
    assert empty["power"] == 100 and empty["next"] == {"need": 4, "power": 120}

    line = kit_payload(weapon, kit_of(9))
    assert line["worn"] == 9 and line["power"] == 150 and line["next"] == {}
    assert "150%" in line["text"] and "Вышибала" in line["text"]
    assert line["weapon"] is True

    # На тряпке та же строка, но про оружие набора, а не про неё саму
    shirt = kit_payload(CATALOGUE["set_bouncer_head"], kit_of(9))
    assert shirt["power"] == 150 and shirt["weapon"] is False
    assert "оружие набора" in shirt["text"]

    # Вещь вне наборов строки не получает вовсе
    assert kit_payload(CATALOGUE["viking_axe"], kit_of(9)) == {}
