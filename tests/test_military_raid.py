"""Захват военной базы: десять солдат на полигоне и трофей с них.

Третий рейд города, и устроен он не как первые два. Проверяем пять
вещей и в таком порядке важности:

1. **Противник не подстраивается ни числом, ни уровнем.** Десять солдат
   двенадцатого уровня — и столько же их будет для одного бойца и для
   полного отряда. Трудность тут в числе, а не в подгонке.
2. **Форма охраны своя целиком.** Прилавок к ней ничего не добирает: ни
   одной клубной вещи на солдате быть не должно.
3. **Трофей добывают только здесь.** Кибернетическое оружие не лежит ни
   в лавке, ни у мага, и падает оно пятнадцатью процентами.
4. **Трофей носят все четыре класса.** Он кросс-классовый, и спрашивает
   выносливость, а не ремесло: иначе воин с кинжалом ассасина остался бы
   с ним в рюкзаке.
5. **Пропуск свой и дороже фанатского на полсотни.** Сто кредитов за
   вход: за базой стоит оружие, которого нет ни в одной лавке.
"""

from __future__ import annotations

import random
from datetime import date, datetime

import pytest

from bot.config import Config
from bot.game.classes import FIGHTER_CLASSES, Zone, block_combo
from bot.game.combat import Action, Fighter, resolve_round
from bot.game.economy import MAX_LEVEL
from bot.game.equipment import (
    CATALOGUE,
    CYBER_SHELF,
    CYBER_WEAPONS,
    MILITARY_KIT,
    MILITARY_LEVEL,
    SHOWCASE,
    Slot,
    shelf_of,
)
from bot.game.gear import ALL_SLOTS, weapon_share_cap, weapon_share_floor
from bot.game.locations import Service, get_location
from bot.game.potions import BASE_PASS, STADIUM_PASS, get_potion
from bot.game.raid import (
    CYBER_CHANCE,
    GANG_LEADER,
    GANG_LEVEL,
    MILITARY_GUARDS,
    MILITARY_PURSE,
    MILITARY_RAID,
    MILITARY_SQUAD,
    MOSCOW,
    boss_fighter,
    kind_at,
    kind_of_boss,
    raid_foes,
)
from bot.game.reference import developed_stats
from bot.raid_service import RaidError, RaidService
from scripts.foe_power import armor_mean
from tests.test_battle_flow import make_player
from tests.test_duel_flow import FakeBot

BASE = "indoor_training_ground"
CHAT_ID = -100500
THREAD_ID = 7

MONDAY = date(2026, 10, 26)  # понедельник
TUESDAY = date(2026, 10, 27)


def moscow(hour: int, day: date = MONDAY, minute: int = 0) -> int:
    return int(
        datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW
        ).timestamp()
    )


@pytest.fixture
def bot():
    return FakeBot()


def make_service(db, bot, **over) -> RaidService:
    settings = dict(
        bot_token="test",
        db_path=":memory:",
        raid_lobby_timeout=600,
        raid_turn_timeout=600,
        raid_break=0,
        raid_any_time=True,
    )
    settings.update(over)
    return RaidService(
        bot=bot, db=db, config=Config(**settings), rng=random.Random(22)
    )


async def soldier_hunters(
    db, count: int = 2, level: int = 12, passes: bool = True
) -> list:
    """Отряд на базу с пропусками: без них на полигон не пускают."""
    out = []
    for index in range(count):
        player = make_player(index + 1, f"Боец{index + 1}")
        player.level = level
        player.location = BASE
        player.credits = 500
        await db.save_player(player)
        if passes:
            player.potions[BASE_PASS] = await db.add_potion(
                player.user_id, BASE_PASS
            )
        out.append(player)
    return out


# ---------- дом и расписание ----------


def test_the_base_is_stormed_at_the_training_ground():
    """Рейд собирают на полигоне, и дверь его туда ведёт."""
    place = get_location(BASE)

    assert kind_at(BASE) is MILITARY_RAID
    assert MILITARY_RAID.house == BASE
    assert place.allows(Service.RAID)
    # Учёба стоит первой: занятия идут каждый день, рейд — раз в неделю
    assert place.services[0] is Service.STUDY
    assert place.allows(Service.STUDY), "кадеты ходят сюда же"


def test_the_base_opens_three_days_a_week_from_six_to_midnight():
    """Понедельник, четверг и суббота — с шести вечера до полуночи.

    Раз в неделю окно легко проспать целиком, и полигон от этого
    простаивал. Дни всё равно твёрдые: расписание выучивают наизусть.
    """
    windows = MILITARY_RAID.schedule.windows_on(MONDAY)

    assert len(windows) == 1
    start, end = windows[0].start, windows[0].end
    assert start == moscow(18)
    assert end - start == 6 * 3600
    # Понедельник, четверг и суббота по питоновскому счёту дней
    assert MILITARY_RAID.schedule.weekdays == (0, 3, 5)
    assert MILITARY_RAID.schedule.windows_on(TUESDAY) == ()
    assert MILITARY_RAID.schedule_text(moscow(18)) == (
        "по понедельникам, четвергам и субботам с 18:00 до 00:00 мск"
    )
    # В окно пускают, в полночь — уже нет
    assert MILITARY_RAID.window_now(moscow(18)) is not None
    assert MILITARY_RAID.window_now(moscow(23, minute=59)) is not None
    assert MILITARY_RAID.window_now(moscow(17, minute=59)) is None
    assert MILITARY_RAID.window_now(moscow(12, day=TUESDAY)) is None


# ---------- охрана ----------


def test_ten_guards_come_out_whoever_knocks():
    """Десять солдат — и для одного бойца, и для полного отряда."""
    assert len(MILITARY_GUARDS) == MILITARY_SQUAD == 10
    assert MILITARY_RAID.min_party == 1
    assert len(MILITARY_RAID.roster(1)) == 10
    assert len(MILITARY_RAID.roster(MILITARY_RAID.max_party)) == 10
    # Пополнения у них нет: состав и так полный
    assert MILITARY_RAID.reserve == ()
    assert not MILITARY_RAID.one_on_one
    # Все десять — воины двенадцатого уровня, и различаются фамилией
    assert {one.class_code for one in MILITARY_GUARDS} == {"warrior"}
    assert len({one.title for one in MILITARY_GUARDS}) == 10
    assert len({one.code for one in MILITARY_GUARDS}) == 10
    assert MILITARY_RAID.foe_level == MILITARY_LEVEL == 12
    assert all(kind_of_boss(one.code) is MILITARY_RAID for one in MILITARY_GUARDS)
    # Лицо одно на всех: на базе стоит караул в одной форме, а не десять
    # характеров
    faces = {one.image for one in MILITARY_GUARDS}
    assert len(faces) == 1
    # Фон прозрачный, как у образов бойцов: в рамке караул ложится на тот
    # же студийный серый
    assert faces == {
        "https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/avatars/"
        "npc_military_uniform_transparent.png"
    }


def test_the_guards_do_not_grow_with_the_squad():
    """Ни числом, ни запасом здоровья: отряд им ничего не прибавляет."""
    alone = raid_foes(MILITARY_RAID, [12])
    crowd = raid_foes(MILITARY_RAID, [12] * 10)

    assert len(alone) == len(crowd) == 10
    assert MILITARY_RAID.hp_share == 0.0
    mine = list(alone.values())[0]
    theirs = list(crowd.values())[0]
    assert mine.max_hp == theirs.max_hp, "запас здоровья от отряда не растёт"
    assert mine.level == theirs.level == 12
    # Номера у противников отрицательные: с бойцами они не путаются
    assert all(number < 0 for number in alone)


def test_the_guard_wears_his_own_uniform_head_to_boots():
    """Форма своя целиком: клубной вещи на солдате нет ни одной."""
    guard = boss_fighter(MILITARY_GUARDS[0], [12])

    worn = guard.equipment.items
    # Тринадцать клеток: одиннадцать картинок, а колец три
    assert len(worn) == len(ALL_SLOTS) == 13
    # Всё своё: форма караула и его табельное оружие, ничего клубного
    own = {one.code for one in MILITARY_KIT}
    assert {item.item.code for item in worn.values()} <= own
    club = {item.code for item in SHOWCASE}
    assert not {item.item.code for item in worn.values()} & club
    # В руке пистолет, а клинок он возьмёт сам, когда кончатся патроны
    assert worn[Slot.WEAPON].item.code == "soldier_pistol"
    assert worn[Slot.OFFHAND].item.code == "military_shield"
    assert guard.has_shield, "щит у охраны настоящий: блок на три зоны"


def test_the_uniform_is_not_for_sale_anywhere():
    """Форму охраны не купить и не выиграть: она есть только на солдате."""
    # Четырнадцать: тринадцать на солдате плюс клинок, который он
    # берёт после последнего патрона
    assert len(MILITARY_KIT) == 14
    for item in MILITARY_KIT:
        assert item.reward, item.code
        assert not item.price and not item.stars, item.code
        assert item.level_required == MILITARY_LEVEL
        assert not item.shelf, f"{item.code}: форма лежит на прилавке"
        assert item.code not in {one.code for one in SHOWCASE}
        # Картинка — из папки набора, а не из общей оружейной. Табельное
        # оружие рисовали отдельным заходом, и у него папка своя
        assert "items_new/military_energy/" in item.picture or (
            "items_new/soldier/" in item.picture
        ), item.code


# ---------- табельный пистолет ----------


def a_guard(level: int = MILITARY_LEVEL):
    """Один солдат охраны, как его собирает рейд."""
    return boss_fighter(MILITARY_GUARDS[0], [10], 0.0, level=level)


def a_club_fighter(cls: str = "warrior", level: int = 10):
    """Боец города в лучших вещах своей ступени — тот, кто придёт на базу."""
    from bot.game.equipment import Equipment, OwnedItem
    from bot.game.reference import best_kit

    fclass = FIGHTER_CLASSES[cls]
    worn = {
        slot: OwnedItem(item=item, slot=slot)
        for slot, item in best_kit(fclass, level).items()
    }
    gear = Equipment(items=worn)
    stats = developed_stats(fclass, level).merge(gear.bonus)
    return Fighter(
        user_id=1, name="Боец", fclass=fclass, stats=stats,
        level=level, equipment=gear,
    )


def test_the_pistol_is_the_guards_own_and_nowhere_on_sale():
    """Табельный пистолет есть только у караула, и картинка у него своя."""
    pistol = CATALOGUE["soldier_pistol"]

    assert pistol.title == "Табельный пистолет"
    assert pistol.slot is Slot.WEAPON and pistol.is_weapon
    assert pistol.reward and not pistol.price and not pistol.stars
    assert pistol.code not in {one.code for one in SHOWCASE}
    assert pistol.level_required == MILITARY_LEVEL
    assert pistol.picture == (
        "https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev"
        "/items_new/soldier/pistol.jpg"
    )
    # Точность на потолке своей ступени, и ни пунктом выше
    assert pistol.accuracy <= weapon_share_cap(MILITARY_LEVEL) + 1e-9


def test_the_pistol_holds_eight_shots_and_hits_for_a_hundred_and_fifty():
    """Восемь патронов, 150–200 за выстрел, дальше — армейский клинок."""
    pistol = CATALOGUE["soldier_pistol"]

    assert pistol.is_firearm and pistol.shots == 8
    assert (pistol.damage_min, pistol.damage_max) == (150, 200)
    assert pistol.spent_into == "military_sword"
    # Сила та же, что у клинка: запас здоровья солдата не должен
    # меняться в тот момент, когда он перекладывает оружие
    assert pistol.strength == CATALOGUE["military_sword"].strength


def test_the_shot_is_not_driven_by_strength_or_class():
    """Выстрел весит столько, сколько написан: ни сила, ни класс его не разгоняют.

    У клинка иначе: его прибавка идёт через множитель класса и
    складывается с ударом от силы. Поэтому 150–200 у пистолета — это не
    «оружие на пять ступеней выше», а другая арифметика.
    """
    from bot.game.combat import _roll_damage

    guard = a_guard()
    rng = random.Random(7)
    shots = [_roll_damage(guard, 1, rng, 0, limit=60) for _ in range(200)]
    # Первый ход, усталости ещё нет — выстрел приходит как написан
    assert min(shots) >= 150 and max(shots) <= 200

    blade = a_guard()
    while blade.firearm() is not None:
        blade.spend_shot()
    rng = random.Random(7)
    hits = [_roll_damage(blade, 1, rng, 0, limit=60) for _ in range(200)]
    # Клинком он бьёт заметно слабее — и это тело плюс оружие
    assert max(hits) < min(shots)
    assert min(hits) >= blade.derived.damage_min


def test_every_shot_spends_a_bullet_even_a_missed_one():
    """Патрон уходит на любую попытку: дошло, ушло в блок, ушло в уворот."""
    from bot.game.combat import strike_of

    for guard_zone, block in (
        (Zone.HEAD, (Zone.HEAD,)),      # ушло в блок
        (Zone.HEAD, (Zone.LEGS,)),      # дошло
    ):
        guard = a_guard()
        target = a_club_fighter()
        left = guard.shots_left()
        strike_of(
            guard, target, guard_zone, guard.weapons[0], 0,
            Action(attacks=(Zone.CHEST,), block=block), 1,
            random.Random(3), limit=60,
        )
        assert guard.shots_left() == left - 1

    # Промах по увернувшемуся — тоже минус патрон: пуля вылетела
    guard = a_guard()
    nimble = a_club_fighter("rogue")
    spent = 0
    for turn in range(1, 9):
        before = guard.shots_left()
        strike_of(
            guard, nimble, Zone.HEAD, guard.weapons[0], 0,
            Action(attacks=(Zone.CHEST,), block=(Zone.CHEST,)), turn,
            random.Random(turn), limit=60,
        )
        spent += before - guard.shots_left()
    assert spent == 8, "восемь попыток — восемь патронов"


def test_the_guard_draws_the_blade_when_the_magazine_is_empty():
    """Девятый ход он уже с клинком, и запас здоровья от этого не дрогнул."""
    guard = a_guard()
    hp_before, max_before = guard.hp, guard.max_hp

    assert guard.weapons == ("табельным пистолетом",)
    for _ in range(7):
        assert guard.spend_shot() == ""
    # Восьмой патрон — последний, и тут он берётся за клинок
    assert guard.spend_shot() == "Армейский клинок"
    assert guard.shots_left() == 0 and guard.firearm() is None
    assert guard.weapons == ("армейским клинком",)
    assert guard.max_hp == max_before and guard.hp == hp_before
    # Щит остался в левой руке: блок по-прежнему на три зоны
    assert guard.has_shield and guard.block_width == 3
    # И пустой магазин больше ничего не меняет
    assert guard.spend_shot() == ""


def test_the_magazine_costs_a_top_set_fighter_most_of_his_health():
    """Восемь выстрелов снимают с бойца города большую часть запаса.

    Это и есть смысл правки: прежде рейд проходили в соло без потерь —
    клинок охраны против вещей десятой ступени почти не доходил до тела.
    Выстрел доходит: броня режет не больше трёх пятых остатка, а
    резать ей теперь приходится вдвое больше.
    """
    from bot.game.raid import boss_action

    runs, mag = 200, 8
    losses = []
    for seed in range(runs):
        rng = random.Random(1_000 + seed)
        guard, target = a_guard(), a_club_fighter()
        lost = 0
        for turn in range(1, mag + 1):
            before = target.hp
            mine = Action(
                attacks=(Zone.HEAD,),
                block=block_combo(Zone.CHEST, target.block_width),
            )
            resolve_round(
                target, mine, guard,
                boss_action(target, rng, MILITARY_GUARDS[0].temper),
                turn, rng, 60,
            )
            lost += before - target.hp
        losses.append(lost)
        assert guard.shots_left() == 0, "за восемь ходов магазин пуст"
    losses.sort()
    median = losses[runs // 2]
    full = a_club_fighter().max_hp
    assert median > full * 0.5, f"магазин снимает только {median} из {full}"
    # Каждый шестой магазин кладёт бойца насмерть
    deadly = sum(1 for one in losses if one >= full) / runs
    assert deadly >= 0.1, f"смертельных магазинов всего {deadly:.0%}"

    # А клинком — ровно вдвое меньше: это и есть разница, из-за которой
    # рейд проходили в соло без потерь
    blade = []
    for seed in range(runs):
        rng = random.Random(1_000 + seed)
        guard, target = a_guard(), a_club_fighter()
        while guard.firearm() is not None:
            guard.spend_shot()
        lost = 0
        for turn in range(1, mag + 1):
            before = target.hp
            mine = Action(
                attacks=(Zone.HEAD,),
                block=block_combo(Zone.CHEST, target.block_width),
            )
            resolve_round(
                target, mine, guard,
                boss_action(target, rng, MILITARY_GUARDS[0].temper),
                turn, rng, 60,
            )
            lost += before - target.hp
        blade.append(lost)
    blade.sort()
    assert median > blade[runs // 2] * 1.8, (
        f"магазин {median}, клинок {blade[runs // 2]} — разница мелкая"
    )


def test_the_blade_is_the_half_of_the_fight_the_balance_is_built_on():
    """После магазина солдат бьёт как раньше: договор с бандой — об этом.

    Залп короче любого боя рейда: восемь ходов против девяноста. Поэтому
    ровная мощь караула от пистолета не изменилась ни на пункт — и
    именно её сравнивает `scripts/foe_power.py`.
    """
    from scripts.foe_power import damage_per_turn, spent

    firing = a_guard()
    assert damage_per_turn(firing) > 150
    after = spent(a_guard())
    # Клинком — то же, что и до появления пистолета в руке
    blade = boss_fighter(MILITARY_GUARDS[0], [10], 0.0, level=MILITARY_LEVEL)
    while blade.firearm() is not None:
        blade.spend_shot()
    assert damage_per_turn(after) == damage_per_turn(blade)
    assert damage_per_turn(after) < damage_per_turn(firing) / 1.5


def test_a_crit_from_the_pistol_goes_through_the_armour():
    """Крит выстрелом броня не держит — и потому он почти смертелен.

    Обычный удар защита режет на четыре пятых: половину снимает
    выносливость, три пятых остатка — броня. Пуля в упор этого не знает,
    и крит доходит до тела целиком.
    """
    pistol = CATALOGUE["soldier_pistol"]
    assert pistol.crit_pierce == 1.0
    assert CATALOGUE["military_sword"].crit_pierce == 0.0

    guard = a_guard()
    soft = a_club_fighter("rogue")
    # Крит у караула 26% — столько показывает и карточка
    assert round(guard.crit, 2) == 0.26
    hit = guard.derived.crit_power * 150
    assert hit > soft.max_hp * 0.5, (
        f"крит выстрелом — {hit:.0f} при запасе {soft.max_hp}"
    )


# ---------- трофей ----------


def test_the_cyber_weapon_drops_from_the_base_and_nowhere_else():
    """Шестнадцать клинков, и взять их можно только с рейда."""
    assert len(CYBER_WEAPONS) == 16
    assert shelf_of(CYBER_SHELF) == CYBER_WEAPONS
    assert MILITARY_RAID.spoils.item_shelf == CYBER_SHELF
    assert MILITARY_RAID.spoils.item_chance == CYBER_CHANCE == 0.15

    for item in CYBER_WEAPONS:
        assert item.is_weapon and item.slot is Slot.WEAPON
        assert item.reward, item.code
        assert not item.price and not item.stars, item.code
        assert item.code not in {one.code for one in SHOWCASE}
        assert item.level_required == 12
        assert "weapons/futuristic/" in item.picture
        assert item.instrumental, f"{item.code}: нечем ударить в рассказе судьи"
    # По четыре на класс, и у каждого своя картинка
    assert len({item.picture for item in CYBER_WEAPONS}) == 16
    for line in ("warrior", "tank", "trickster", "assassin"):
        mine = [one for one in CYBER_WEAPONS if one.code.startswith(line)]
        assert len(mine) == 4, line


def test_the_drop_is_one_roll_in_seven():
    """Пятнадцать процентов — и падает оно лучшему по урону, одно на рейд."""
    spoils = MILITARY_RAID.spoils
    codes = {one.code for one in CYBER_WEAPONS}

    # Бросок всегда отдаёт клинок с полки — или ничего
    got = [spoils.item_for(random.Random(seed)) for seed in range(200)]
    assert all(one is None or one in codes for one in got)
    lucky = [one for one in got if one is not None]
    assert 10 <= len(lucky) <= 50, f"выпало {len(lucky)} из 200"
    # Склянок база не даёт: её приз один
    assert not spoils.elixirs and not spoils.potions


def test_the_trophy_is_worn_by_every_class():
    """Кросс-классовое: трофей спрашивает тело, а не ремесло.

    Обычное оружие двенадцатого уровня требовало бы главной
    характеристики своего класса, и воин с кинжалом ассасина остался бы
    с ним в рюкзаке. Кибернетика цепляется к выносливости, а её к
    двенадцатому уровню хватает у всех четырёх.
    """
    for item in CYBER_WEAPONS:
        assert item.for_classes == (), item.code
        need = item.requires
        assert need.endurance and not need.strength, item.code
        assert not need.agility and not need.intuition, item.code
        for code, fclass in FIGHTER_CLASSES.items():
            mine = developed_stats(fclass, item.level_required)
            assert mine.endurance >= need.endurance, f"{item.code}: {code}"


def test_the_trophy_keeps_the_ladder_of_its_tier():
    """Доли трофея идут по полосе двенадцатой ступени, и пары сходятся.

    Полоса узкая, и обе стороны обеих пар обязаны быть на ступени: иначе
    уворот трикстера некому сбить точностью, а крит ассасина — антикритом,
    и круг классов переворачивается на одном трофее.
    """
    floor = weapon_share_floor(12)
    cap = weapon_share_cap(12)
    best: dict[str, float] = {}
    for item in CYBER_WEAPONS:
        shares = {
            "accuracy": item.accuracy,
            "dodge": item.dodge,
            "crit": item.crit,
            "anticrit": item.anticrit,
            "counter": item.counter,
        }
        top = max(shares.values())
        assert floor <= top <= cap, f"{item.code}: {top:.0%}"
        for name, value in shares.items():
            best[name] = max(best.get(name, 0.0), value)
        # Плоская прибавка не обгоняет свой уровень
        assert max(item.strength, item.agility, item.intuition) <= 7, item.code
        assert item.bonus.endurance == 0, item.code
    for one, other in (("dodge", "accuracy"), ("crit", "anticrit")):
        assert best[one] >= floor and best[other] >= floor, f"{one}/{other}"


def test_the_trophy_tops_the_ladder_without_breaking_it():
    """Трофей сильнее и прилавка, и приза прошлого рейда — но не вдвое.

    Лестница у оружия выходит такая: лучшее в лавке клуба (девятая
    ступень) — двадцать один средним уроном, фанатский приз десятой —
    двадцать пять, кибернетика двенадцатой — тридцать. По пятой части
    за ступень, и ни одна не обесценивает предыдущую.
    """
    shop = [one for one in SHOWCASE if one.is_weapon]
    top = max(one.level_required for one in shop)
    club = max(
        (one.damage_min + one.damage_max) / 2
        for one in shop
        if one.level_required == top
    )
    fan = max(
        (one.damage_min + one.damage_max) / 2
        for one in CATALOGUE.values()
        if one.is_weapon and one.shelf == "fan"
    )

    assert top == 9 and club == 21 and fan > club
    for item in CYBER_WEAPONS:
        mine = (item.damage_min + item.damage_max) / 2
        assert mine > fan, f"{item.code}: не обходит приз стычки"
        assert mine < club * 1.6, f"{item.code}: обходит лавку больше чем вдвое"


# ---------- экран: один солдат за всех ----------


def test_the_clones_show_one_card_for_all_ten():
    """Под «i» стоит один солдат: остальные девять — его копии.

    Десять одинаковых кукол подряд не рассказывают ничего, чего не
    рассказала первая, — а вот про банду фанатов так сказать нельзя: там
    у каждого свой класс, своё оружие и свои числа.
    """
    from bot.game.raid import CELLAR_RAID, HOOLIGAN_RAID
    from bot.webapp.raid import shown_roster

    assert MILITARY_RAID.clones is True
    assert HOOLIGAN_RAID.clones is False and CELLAR_RAID.clones is False

    ten = MILITARY_RAID.roster(10)
    assert len(ten) == 10
    assert len(shown_roster(MILITARY_RAID, ten)) == 1
    # Банда показывается целиком, босс казино и так один
    gang = HOOLIGAN_RAID.roster(3)
    assert len(shown_roster(HOOLIGAN_RAID, gang)) == len(gang) > 1
    assert len(shown_roster(CELLAR_RAID, CELLAR_RAID.roster(1))) == 1


def test_the_story_of_the_base_names_the_clones_and_the_hours():
    """Рассказ о рейде: секретное оружие, десять клонов и понедельник."""
    story = MILITARY_RAID.story

    assert "секретного оружия" in story
    assert "клонированных солдат из 10 человек" in story
    assert "каждый понедельник с 18:00 до 00:00" in story


async def test_the_screen_of_the_base_carries_one_soldier(db, bot):
    """Экран базы: рассказ, один солдат под «i» и десять на табло волны."""
    from bot.webapp.raid import build_raid

    service = make_service(db, bot)
    player = (await soldier_hunters(db, 1))[0]

    body = build_raid(player, service, 0, MILITARY_RAID)

    assert body["kind"]["story"] == MILITARY_RAID.story
    assert body["kind"]["clones"] is True
    assert len(body["roster"]) == 1, "десять одинаковых кукол незачем"
    assert body["boss"] is body["roster"][0]
    assert body["boss"]["title"] == MILITARY_GUARDS[0].title
    # А вот противников на минимальный отряд по-прежнему десять
    assert body["kind"]["foes"] == 10


# ---------- вход ----------


def test_the_pass_to_the_base_costs_fifty_more_than_the_stadium():
    """Пропуск на полигон дороже билета на матч ровно на полсотни.

    Разница не из воздуха: за базой стоит кибероружие, которого нет ни в
    одной лавке, и вход на неё обязан стоить дороже стадиона, откуда
    приносят фанатскую вещь с прилавка.
    """
    ticket = get_potion(BASE_PASS)
    stadium = get_potion(STADIUM_PASS)

    assert MILITARY_RAID.pass_code == BASE_PASS
    assert ticket.price == stadium.price + 50 == 100
    assert ticket.is_pass is True  # его не пьют, его предъявляют


async def test_the_base_takes_the_pass_at_the_gate(db, bot):
    """Пропуск отдают на входе: один заход — один пропуск."""
    service = make_service(db, bot)
    squad = await soldier_hunters(db, 2)

    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, squad[0], MILITARY_RAID, chat_title="Клуб"
    )

    assert lobby is not None
    fresh = await db.get_player(squad[0].user_id)
    assert fresh.credits == 500, "пропуск был в рюкзаке, покупать нечего"
    assert fresh.potion_count(BASE_PASS) == 0, "пропуск отдан на входе"


async def test_without_a_pass_the_base_sends_you_to_the_shop(db, bot):
    """Без пропуска и без согласия на покупку на полигон не пускают."""
    service = make_service(db, bot)
    player = (await soldier_hunters(db, 1, passes=False))[0]

    with pytest.raises(RaidError, match="Пропуск на полигон"):
        await service.open_raid(
            CHAT_ID, THREAD_ID, player, MILITARY_RAID, chat_title="Клуб"
        )


async def test_the_pass_is_taken_once_per_window(db, bot):
    """Пропуск отдают раз за окно: второй заход в то же окно бесплатный.

    Окно у базы одно в неделю, и правило «своё за окно берут один раз»
    держится именно отданным пропуском: пока он числится за этим окном,
    ходить можно сколько угодно.
    """
    service = make_service(db, bot, raid_any_time=False)
    player = (await soldier_hunters(db, 1))[0]
    moment = moscow(19)

    import bot.game.raid as rules

    was = rules.now_ts
    rules.now_ts = lambda: moment
    try:
        await service._admit(player, kind=MILITARY_RAID)
        window = MILITARY_RAID.window_now(moment)
        seen = await db.raid_window(
            player.user_id, window.start, MILITARY_RAID.code
        )
        assert seen is not None, "окно не занято"
        assert not seen["won"], "победы ещё не было"
        # Второй заход в то же окно проходит так же молча: пропуск за
        # него уже отдан, а ходить можно сколько угодно
        await service._admit(player, kind=MILITARY_RAID)
    finally:
        rules.now_ts = was

    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 500, "второй пропуск не покупали"
    assert fresh.potion_count(BASE_PASS) == 0, "и не брали из рюкзака дважды"


async def test_the_purse_is_split_between_the_squad(db, bot):
    """Кошель делится: большой отряд бой облегчает, а не утяжеляет."""
    assert MILITARY_RAID.split is True
    assert MILITARY_RAID.purse == MILITARY_PURSE == 1_200

    alone = MILITARY_RAID.shares(1)
    crowd = MILITARY_RAID.shares(10)

    assert alone == [MILITARY_PURSE]
    assert sum(crowd) == MILITARY_PURSE and len(crowd) == 10
    assert max(crowd) <= MILITARY_PURSE // 10 + 1


# ---------- экран ----------


async def test_the_raid_screen_shows_the_base_with_its_pass(db, bot):
    """Экран рейда: охрана, расписание и пропуск на полигон в окне входа."""
    from bot.webapp.raid import gate_payload, kind_row, plates_payload

    service = make_service(db, bot)
    player = (await soldier_hunters(db, 1))[0]

    row = kind_row(MILITARY_RAID)
    gate = await gate_payload(player, service, MILITARY_RAID)

    assert row["code"] == "military" and row["house"] == BASE
    assert row["foes"] == 10 and row["min_party"] == 1
    assert row["purse"] == MILITARY_PURSE
    assert gate["pass_code"] == BASE_PASS
    assert gate["pass_title"] == "Пропуск на полигон"
    assert gate["pass_price"] == 100 and gate["passes"] == 1
    assert gate["can_afford"] is True, "пятисот хватает на сотню"
    assert gate["schedule"] == (
        "по понедельникам, четвергам и субботам с 18:00 до 00:00 мск"
    )
    # Плашка на карте есть у каждого рейдового дома, и у базы тоже
    plates = await plates_payload(player, service)
    assert BASE in plates


def test_the_guard_stands_above_the_gang_leader():
    """Солдат сильнее лидера банды фанатов на десять-пятнадцать процентов.

    Мерка — `scripts/foe_power.py`: живучесть на урон в ход. Живучесть
    считается по правилам боя: выносливость режет удар всегда, броня —
    не больше трёх пятых остатка (`MAX_ARMOR_SHARE`), и брони у обоих
    заведомо больше этого потолка.

    Полоса узкая нарочно. Шире — и договор перестаёт что-либо держать:
    форму охраны правят числом за раз, и без полосы солдат однажды
    окажется либо слабее гопника, либо крепче босса казино.

    Меряется ровная мощь — та, которой солдат бьёт после последнего
    патрона. Залп из табельного пистолета в неё не входит нарочно:
    восемь выстрелов короче любого боя рейда, и считать их ровной
    мощью значило бы объявить караул втрое сильнее босса казино.
    """
    from scripts.foe_power import power

    guard = power(MILITARY_GUARDS[0], MILITARY_LEVEL)
    leader = power(GANG_LEADER, GANG_LEVEL)

    assert 1.10 <= guard / leader <= 1.15, round(guard / leader, 3)
    # А залпом — заметно выше, и это и есть опасность первых восьми
    # ходов. Полоса широкая: число подбиралось не под неё, а под то,
    # сколько здоровья магазин снимает с бойца (см. тесты пистолета)
    salvo = power(MILITARY_GUARDS[0], MILITARY_LEVEL, firing=True)
    assert 1.8 <= salvo / leader <= 3.0, round(salvo / leader, 3)
    # Сильнее он по живучести, а не только по клинку: прежде форма
    # двенадцатой ступени держала хуже фанатской десятой
    one = boss_fighter(MILITARY_GUARDS[0], [10], 0.0, level=MILITARY_LEVEL)
    major = boss_fighter(GANG_LEADER, [10], 0.0, level=GANG_LEVEL)
    assert one.max_hp > major.max_hp
    assert armor_mean(one) > armor_mean(major)


def test_the_sword_of_the_guard_holds_the_chiefs_level():
    """Клинок охраны не слабее дубинки начальника полиции.

    Караул на секретном складе вооружён не хуже, чем кабинет в участке,
    и заметно лучше фанатской биты — иначе рейд двенадцатого уровня
    встречал бы оружием десятого.
    """
    sword = CATALOGUE["military_sword"]
    chief = CATALOGUE["chief_baton"]
    gang = CATALOGUE["fan_boss_bat"]

    def middle(item):
        return (item.damage_min + item.damage_max) / 2

    assert middle(sword) >= middle(chief) > middle(gang)
    assert sword.accuracy >= chief.accuracy
    assert sword.anticrit >= chief.anticrit
    assert sword.strength >= chief.strength
    # И по полосе своей ступени он всё ещё проходит
    assert sword.accuracy <= weapon_share_cap(MILITARY_LEVEL)


def test_the_level_of_the_guards_matches_their_uniform():
    """Уровень охраны и уровень её формы — одно число, а не два."""
    assert MILITARY_LEVEL == 12 <= MAX_LEVEL
    assert MILITARY_RAID.foe_level == MILITARY_LEVEL
    assert {one.level_required for one in MILITARY_KIT} == {MILITARY_LEVEL}
    assert {one.level_required for one in CYBER_WEAPONS} == {MILITARY_LEVEL}
    assert CATALOGUE["military_sword"].level_required == MILITARY_LEVEL
