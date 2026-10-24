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
5. **Вход свободный.** Пропуска на базу не продают — окно при этом
   считается как у всех.
"""

from __future__ import annotations

import random
from datetime import date, datetime

import pytest

from bot.config import Config
from bot.game.classes import FIGHTER_CLASSES
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
from bot.game.raid import (
    CYBER_CHANCE,
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


async def soldier_hunters(db, count: int = 2, level: int = 12) -> list:
    """Отряд на базу. Пропусков им не надо: на базе их не спрашивают."""
    out = []
    for index in range(count):
        player = make_player(index + 1, f"Боец{index + 1}")
        player.level = level
        player.location = BASE
        player.credits = 500
        await db.save_player(player)
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


def test_the_base_opens_on_mondays_from_six_to_midnight():
    """Раз в неделю, по понедельникам с шести вечера до полуночи."""
    windows = MILITARY_RAID.schedule.windows_on(MONDAY)

    assert len(windows) == 1
    start, end = windows[0].start, windows[0].end
    assert start == moscow(18)
    assert end - start == 6 * 3600
    assert MILITARY_RAID.schedule.windows_on(TUESDAY) == ()
    assert MILITARY_RAID.schedule_text(moscow(18)) == (
        "по понедельникам с 18:00 до 00:00 мск"
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
    assert faces == {
        "https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/avatars/"
        "npc_military_uniform.jpg"
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
    assert all(item.item.code.startswith("military_") for item in worn.values())
    club = {item.code for item in SHOWCASE}
    assert not {item.item.code for item in worn.values()} & club
    assert worn[Slot.WEAPON].item.code == "military_sword"
    assert worn[Slot.OFFHAND].item.code == "military_shield"
    assert guard.has_shield, "щит у охраны настоящий: блок на три зоны"


def test_the_uniform_is_not_for_sale_anywhere():
    """Форму охраны не купить и не выиграть: она есть только на солдате."""
    assert len(MILITARY_KIT) == 13
    for item in MILITARY_KIT:
        assert item.reward, item.code
        assert not item.price and not item.stars, item.code
        assert item.level_required == MILITARY_LEVEL
        assert not item.shelf, f"{item.code}: форма лежит на прилавке"
        assert item.code not in {one.code for one in SHOWCASE}
        # Картинка — из папки набора, а не из общей оружейной
        assert "items_new/military_energy/" in item.picture


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


# ---------- вход ----------


async def test_the_base_asks_for_no_pass(db, bot):
    """Пропуска на базу не продают: вход свободный."""
    service = make_service(db, bot)
    squad = await soldier_hunters(db, 2)

    assert MILITARY_RAID.needs_pass is False
    assert MILITARY_RAID.pass_code == ""

    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, squad[0], MILITARY_RAID, chat_title="Клуб"
    )

    assert lobby is not None
    fresh = await db.get_player(squad[0].user_id)
    assert fresh.credits == 500, "за вход не платят"
    assert not fresh.potions, "и пропуска не тратят"


async def test_the_window_is_taken_even_without_a_pass(db, bot):
    """Окно занимается и без билета — по нему и считается «своё взято».

    В подвале и на стадионе окно занимает отданный пропуск. На базе
    пропуска нет, и если бы окно не занималось вовсе, правило «одна
    победа за окно» на базе не работало бы — заходить можно было бы
    сколько угодно.
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
        # него уже «отдан», а ходить можно сколько угодно
        await service._admit(player, kind=MILITARY_RAID)
    finally:
        rules.now_ts = was

    fresh = await db.get_player(player.user_id)
    assert fresh.credits == 500 and not fresh.potions


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


async def test_the_raid_screen_shows_the_base_without_a_ticket(db, bot):
    """Экран рейда: охрана, расписание и «вход свободный» вместо билета."""
    from bot.webapp.raid import gate_payload, kind_row, plates_payload

    service = make_service(db, bot)
    player = (await soldier_hunters(db, 1))[0]

    row = kind_row(MILITARY_RAID)
    gate = await gate_payload(player, service, MILITARY_RAID)

    assert row["code"] == "military" and row["house"] == BASE
    assert row["foes"] == 10 and row["min_party"] == 1
    assert row["purse"] == MILITARY_PURSE
    assert gate["pass_code"] == "" and gate["pass_title"] == ""
    assert gate["pass_price"] == 0 and gate["passes"] == 0
    assert gate["can_afford"] is True, "за свободный вход всегда хватает"
    assert gate["schedule"] == "по понедельникам с 18:00 до 00:00 мск"
    # Плашка на карте есть у каждого рейдового дома, и у базы тоже
    plates = await plates_payload(player, service)
    assert BASE in plates


def test_the_level_of_the_guards_matches_their_uniform():
    """Уровень охраны и уровень её формы — одно число, а не два."""
    assert MILITARY_LEVEL == 12 <= MAX_LEVEL
    assert MILITARY_RAID.foe_level == MILITARY_LEVEL
    assert {one.level_required for one in MILITARY_KIT} == {MILITARY_LEVEL}
    assert {one.level_required for one in CYBER_WEAPONS} == {MILITARY_LEVEL}
    assert CATALOGUE["military_sword"].level_required == MILITARY_LEVEL
