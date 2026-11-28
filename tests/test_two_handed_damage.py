"""Двуручный хват: вещь сильнее написанным, а не скрытым множителем.

Хват — это выбор, и у выбора должна быть отдача. Двуручное занимает обе
руки: рядом с ним нельзя держать ни щит, ни второе оружие, — и без
прибавки к урону он был бы просто запретом. Прибавка стоит на самой
вещи: урон и прибавки к характеристикам у двуручного на половину выше,
чем у обычного оружия его ступени, а цена та же.

Прежде было наоборот: в каталоге у двуручного стояли те же числа, что у
одноручного, а на бойце они шли в 2,4 раза. Выход за ход выравнивался с
парным хватом, но прочитать это игрок не мог никак — вещь обещала 17–25,
а била на 41–60. Теперь скрытой поправки нет вовсе: что написано, то и
бьёт, а лестница хватов снова читается числами на прилавке.
"""

import random

from bot.content.arming import HEAVY
from bot.game.classes import Stats, get_class
from bot.game.combat import Action, Fighter, Zone, resolve_round
from bot.game.equipment import CATALOGUE, Equipment, OwnedItem, Slot

TWO_HANDED = CATALOGUE["greatsword"]
ONE_HANDED = CATALOGUE["katana"]


def middle(item) -> float:
    return (item.damage_min + item.damage_max) / 2


def step_rival(item):
    """Лучшее обычное оружие той же ступени и той же полки."""
    rivals = [
        other
        for other in CATALOGUE.values()
        if other.is_weapon
        and not other.two_handed
        and other.level_required == item.level_required
        and bool(other.stars) == bool(item.stars)
        and bool(other.reward) == bool(item.reward)
        and other.shelf == item.shelf
    ]
    return max(rivals, key=middle) if rivals else None


def test_two_handed_weapons_are_half_again_stronger():
    """Урон и прибавки двуручного — на половину выше ступени, цена та же.

    Это всё правило хвата одной проверкой. Мерка — лучшее обычное
    оружие той же ступени и той же полки: прилавочное сравнивается с
    прилавочным, наградное с наградным, иначе двуручный посох мерился
    бы кастетом третьего уровня. Трофеи кибернетики исключены: у них
    своя лестница призов, и половина сверху выбила бы их из неё.

    Разброс при этом свой у каждой вещи: у танка ровный, у ассасина
    рваный. Поэтому сходится середина, а не концы.
    """
    checked = 0
    for code in HEAVY:
        item = CATALOGUE[code]
        # Трофеи кибернетики живут не по ступеням лавки, а по своей
        # лестнице призов: клуб 21 средним уроном, фанатский приз 25,
        # кибернетика 30, и ни одна ступень не обесценивает прошлую
        # (`tests/test_military_raid.py`). Половина сверху выбила бы их
        # из этой лестницы вовсе, поэтому правило хвата их не трогает
        if item.shelf == "cyber":
            continue
        rival = step_rival(item)
        if rival is None:
            continue
        checked += 1
        want = 1.5 * middle(rival)
        # Половина единицы — цена округления: числа на вещах целые
        assert abs(middle(item) - want) <= 0.5, (
            f"{item.title}: {middle(item)} против {want} у {rival.title}"
        )
        # За хват не доплачивают: двуручное стоит не дороже самого
        # дорогого одноручного своей ступени. Ровного равенства тут не
        # потребуешь — у сетовых вещей своя цена, у прилавочных своя
        dearest = max(
            one.price
            for one in CATALOGUE.values()
            if one.is_weapon
            and not one.two_handed
            and one.level_required == item.level_required
            and one.shelf == item.shelf
        )
        assert item.price <= dearest, (
            f"{item.title}: {item.price} дороже всего одноручного ступени ({dearest})"
        )

    assert checked >= 15, f"двуручного с эталоном ступени нашлось {checked}"


def test_a_set_heavy_weapon_is_half_again_stronger_than_its_own_one_hander():
    """В наборе двуручное против одноручного той же линии: на половину выше.

    У набора две вещи в руки, и это самая прямая мерка правила: та же
    линия, та же ступень, те же требования, та же цена, и половина
    сверху у двуручного — в уроне и в прибавках. У вещей вне наборов
    прибавок может не быть вовсе (у танковского прилавочного оружия их
    нет по складу класса), поэтому прибавки сверяются здесь, а не на
    всём тяжёлом.
    """
    pairs = 0
    for code in HEAVY:
        if not code.endswith("_heavy"):
            continue
        one_handed = CATALOGUE.get(code.replace("_heavy", "_weapon"))
        heavy = CATALOGUE[code]
        if one_handed is None:
            continue
        pairs += 1
        assert abs(middle(heavy) - 1.5 * middle(one_handed)) <= 0.5, heavy.title
        assert heavy.level_required == one_handed.level_required, heavy.title
        assert heavy.price == one_handed.price, heavy.title
        assert heavy.requires == one_handed.requires, heavy.title
        for stat in ("strength", "agility", "intuition", "hp"):
            mine, theirs = getattr(heavy, stat), getattr(one_handed, stat)
            if not theirs:
                assert not mine, f"{heavy.title}: {stat} взялся из ниоткуда"
                continue
            assert mine == round(theirs * 1.5), (
                f"{heavy.title}: {stat} не на половину выше"
            )

    assert pairs == 10, f"пар «одноручное — двуручное» в наборах нашлось {pairs}"


def test_the_counter_and_the_fighter_say_the_same_number():
    """На прилавке и на бойце одно и то же число: поправки больше нет.

    Это и есть смысл отмены множителя. Пока он был, кукла показывала
    одно, прилавок другое, и объяснить разницу было нечем.
    """
    equipment = Equipment(
        items={Slot.WEAPON: OwnedItem(item=TWO_HANDED, slot=Slot.WEAPON)}
    )
    shown = equipment.grown(equipment.weapon)

    assert TWO_HANDED.describe_damage() == "22–28"
    assert (shown.damage_min, shown.damage_max) == (22, 28)
    assert equipment.weapon_damage == (22, 28)
    assert equipment.weapon_damage_max() == 28


def test_the_set_still_amplifies_a_two_handed_weapon():
    """Комплект двуручное усиливает — это правило набора, а не хвата.

    Отмена множителя хвата набора не касается: собранный комплект
    поднимает оружие, каким бы хватом его ни держали.
    """
    from bot.game.sets import set_power

    hero = CATALOGUE["set_hero_weapon"]
    kit = {Slot.WEAPON: OwnedItem(item=hero, slot=Slot.WEAPON)}
    alone = Equipment(items=dict(kit))
    assert alone.grown(alone.weapon).damage_max == hero.damage_max

    for slot, code in (
        (Slot.HEAD, "set_hero_head"),
        (Slot.BELT, "set_hero_belt"),
        (Slot.BOOTS, "set_hero_boots"),
    ):
        kit[slot] = OwnedItem(item=CATALOGUE[code], slot=slot)
    dressed = Equipment(items=kit)
    power = set_power(4)
    assert power > 1.0
    assert dressed.grown(dressed.weapon).damage_max == int(hero.damage_max * power + 0.5)


def test_the_heavier_weapon_reaches_the_health_of_the_other_fighter():
    """Прибавка доходит до здоровья противника, а не остаётся в карточке.

    Сравниваются два меча одной ступени — одноручная катана и двуручный
    меч, — и всё, что между числами на вещах и уроном в бою, остаётся
    тем же.
    """
    fclass = get_class("warrior")
    stats = Stats(strength=30, agility=10, intuition=10, endurance=20)

    hits: dict[str, int] = {}
    for item in (TWO_HANDED, ONE_HANDED):
        total = 0
        for seed in range(60):
            attacker = Fighter(
                user_id=1,
                name=item.title,
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
        hits[item.title] = total

    assert hits[TWO_HANDED.title] > hits[ONE_HANDED.title], hits


def test_the_three_holds_make_a_ladder_and_not_one_right_answer():
    """Лестница хватов: щит — двуручное — парно, и в этом порядке.

    Это весь баланс хватов одним правилом. Щит урона не прибавляет
    вовсе, зато прикрывает; двуручное прибавляет написанной половиной;
    парно бьёт больше всех, но стоит двух вещей в руках, очков обоих
    пулов и щита.

    Проверяется порядок, а не числа: числа — решение владельца, а
    порядок — обещание игроку. Пока у хвата был скрытый множитель,
    порядок стоял иначе, двуручное и парно сходились вплотную, и выбор
    решался не уроном.
    """
    from bot.game.classes import FIGHTER_CLASSES
    from bot.game.combat import OFFHAND_STRENGTH_SHARE
    from bot.game.reference import best_kit, developed_stats, equipment_of
    from bot.game.stats import derive

    def output(fclass, level: int, kit: dict) -> float:
        """Средний урон за ход этой сборкой: по удару на руку с оружием."""
        equipment = equipment_of(kit)
        derived = derive(fclass, developed_stats(fclass, level), 0)
        own = (derived.damage_min + derived.damage_max) / 2
        total = 0.0
        for index, _ in enumerate(equipment.weapons):
            low, high = equipment.weapon_damages[index]
            share = OFFHAND_STRENGTH_SHARE if index else 1.0
            total += own * share + (low + high) / 2 * fclass.damage_mult
        return total

    seen = 0
    for fclass in FIGHTER_CLASSES.values():
        for level in (5, 6, 7, 8, 9, 10, 12):
            kit = dict(best_kit(fclass, level))
            weapon = kit.get(Slot.WEAPON)
            if weapon is None:
                continue
            where = f"{fclass.code} ур.{level}"
            assert not weapon.two_handed, f"{where}: эталон берёт одноручное"
            shield = output(fclass, level, kit)
            bare = dict(kit)
            bare.pop(Slot.OFFHAND, None)

            light = [
                one
                for one in CATALOGUE.values()
                if one.is_weapon
                and one.on_sale
                and not one.two_handed
                and one.family == weapon.family
                and one.level_required <= level
                and one.code != weapon.code
            ]
            heavy = [
                one
                for one in CATALOGUE.values()
                if one.is_weapon
                and one.on_sale
                and one.two_handed
                and one.family == weapon.family
                and one.level_required <= level
            ]
            if not light or not heavy:
                continue
            seen += 1
            pair = dict(bare)
            pair[Slot.OFFHAND] = max(light, key=middle)
            hands = dict(bare)
            hands[Slot.WEAPON] = max(heavy, key=middle)

            with_shield = shield
            with_both = output(fclass, level, hands)
            with_pair = output(fclass, level, pair)
            assert with_shield < with_both < with_pair, (
                f"{where}: щит {with_shield:.0f}, "
                f"двуручное {with_both:.0f}, парно {with_pair:.0f}"
            )

    assert seen >= 10, f"сборок с обоими хватами нашлось {seen}"
