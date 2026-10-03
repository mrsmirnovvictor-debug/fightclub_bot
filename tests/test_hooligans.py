"""Стычка с футбольными фанатами: второй рейд города и всё, чем он не первый.

Отличий от подвала четыре, и каждое здесь проверяется отдельно: твёрдое
расписание вместо жребия, свой пропуск, порог в три бойца и — главное —
противник не один, а банда, которую валят по человеку.

Баланс здесь не проверяется: за него отвечает `scripts/gang_raid.py`.
Тесты про механику — кто против кого встал, кто кого сменил, кому что
досталось.
"""

import random
from datetime import date, datetime

import pytest

from bot.config import Config
from bot.game.locations import Service, get_location
from bot.game.potions import RAID_PASS, STADIUM_PASS, get_potion
from bot.game.raid import (
    BOSS_ID,
    CELLAR_RAID,
    GANG_ASSASSIN,
    GANG_LEVEL,
    GANG_PARTY,
    GANG_PURSE,
    GANG_ROGUE,
    GANG_WARRIOR,
    HOOLIGAN_RAID,
    MOSCOW,
    RAID_KINDS,
    RaidEnd,
    Weekly,
    foe_id,
    foe_titles,
    judge_raid,
    kind_at,
    kind_of_boss,
    raid_foes,
)
from bot.raid_service import RaidError, RaidService
from tests.test_duel_flow import FakeBot
from tests.test_battle_flow import make_player

CHAT_ID = -100500
THREAD_ID = 7

# Среда, 7 октября 2026 года: день, в который фанаты выходят
WEDNESDAY = date(2026, 10, 7)
TUESDAY = date(2026, 10, 6)


def moscow(day: date, hour: int, minute: int = 0) -> int:
    return int(
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW).timestamp()
    )


@pytest.fixture
def bot():
    return FakeBot()


def make_service(bot, db, **over) -> RaidService:
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


async def fill(db, count: int, level: int = GANG_LEVEL) -> list:
    """Бойцы с билетами на матч: без них на стадион не пускают."""
    players = []
    for index in range(count):
        player = make_player(1 + index, f"Боец{1 + index}")
        player.level = level
        player.credits = 500
        await db.save_player(player)
        player.potions[STADIUM_PASS] = await db.add_potion(
            player.user_id, STADIUM_PASS
        )
        players.append(player)
    return players


async def gather(service, db, count: int = GANG_PARTY):
    """Собрать отряд и выйти на банду."""
    players = await fill(db, count)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], HOOLIGAN_RAID, chat_title="Клуб"
    )
    for player in players[1:]:
        await service.join(lobby.id, player)
    if service.raid_of_user(players[0].user_id) is None:
        await service.start_now(lobby.id, players[0].user_id)
    return players, service.raid_of_user(players[0].user_id)


async def punch(service, session, user_id: int, zone: str = "head") -> None:
    await service.handle_choice(session.id, user_id, "attack", zone)
    await service.handle_choice(session.id, user_id, "block", "belt")


def toughen(session, hp: int = 50_000) -> None:
    """Подпереть отряду здоровье: проверяем не живучесть, а механику.

    Пишем прямо в `hp`, и выше его потолка: `extra_hp` читается один раз
    при сборке бойца, и после неё уже ничего не меняет.
    """
    for fighter in session.fighters.values():
        fighter.hp = hp


def floor_gang(session, hp: int = 1) -> None:
    """Оставить банде на один удар каждому."""
    for enemy in session.enemies.values():
        enemy.hp = hp


async def storm(service, session, players, waves: int = 40) -> None:
    """Молотить волну за волной, пока рейд не кончится.

    Одной волны мало даже по обескровленной банде: бойцов трое,
    противников пятеро, и удары ещё и промахиваются. Поэтому гоняем до
    конца, а не считаем, что всё решится с первого нажатия.
    """
    for _ in range(waves):
        if service.raid_of_user(players[0].user_id) is None:
            return
        for player in players:
            if service.raid_of_user(player.user_id) is None:
                return
            if session.fighters[player.user_id].alive:
                await punch(service, session, player.user_id)


# ---------- расписание ----------


def test_the_gang_comes_out_on_wednesdays_and_saturdays():
    """Среда и суббота, с полудня до шести — и больше никогда."""
    schedule = HOOLIGAN_RAID.schedule

    assert [window.title for window in schedule.windows_on(WEDNESDAY)] == [
        "с 12:00 до 18:00 мск"
    ]
    assert schedule.windows_on(date(2026, 10, 10))  # суббота
    for quiet in (5, 6, 8, 9, 11):  # пн, вт, чт, пт, вс
        assert schedule.windows_on(date(2026, 10, quiet)) == ()


def test_the_window_is_six_hours_and_not_a_minute_more():
    """В 11:59 закрыто, в 12:00 открыто, в 18:00 снова закрыто."""
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 11, 59)) is None
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 12)) is not None
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 17, 59)) is not None
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 18)) is None


def test_the_schedule_is_spoken_once_and_for_all():
    """Жребия тут нет: расписание называется одними и теми же словами."""
    said = HOOLIGAN_RAID.schedule_text(moscow(TUESDAY, 10))
    assert said == "по средам и субботам с 12:00 до 18:00 мск"
    # и во вторник, и в среду — то же самое: выучить его можно и нужно
    assert HOOLIGAN_RAID.schedule_text(moscow(WEDNESDAY, 13)) == said


def test_the_next_window_reaches_across_the_quiet_days():
    """От воскресенья до среды четыре дня — расписание их проходит.

    Прошлому поиску хватало двух дней вперёд, потому что подвал открыт
    каждый день. Недельному расписанию двух дней мало, и без этого
    «ближайшее окно» упиралось бы в пустоту.
    """
    sunday = moscow(date(2026, 10, 11), 20)
    assert HOOLIGAN_RAID.next_window(sunday).start == moscow(date(2026, 10, 14), 12)
    # в среду вечером следующее — суббота
    after = moscow(WEDNESDAY, 19)
    assert HOOLIGAN_RAID.next_window(after).start == moscow(date(2026, 10, 10), 12)


def test_the_two_raids_keep_their_own_schedules():
    """Расписание казино осталось жребием, а стадиона — твёрдым."""
    assert isinstance(HOOLIGAN_RAID.schedule, Weekly)
    assert not isinstance(CELLAR_RAID.schedule, Weekly)
    # и в среду в полдень может быть открыто и там, и там
    noon = moscow(WEDNESDAY, 12, 30)
    assert HOOLIGAN_RAID.window_now(noon) is not None


def test_a_six_hour_window_counts_as_one_even_without_a_schedule():
    """Выключатель расписания не должен ломать «одна победа на окно».

    При снятом расписании окно нарезается по длине своего: у стадиона она
    шестичасовая, и часовая клетка дала бы шесть побед за вечер.
    """
    noon = HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 12, 30))
    evening = HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 17, 30))
    assert noon.start == evening.start
    assert noon.end - noon.start == 6 * 3600
    assert HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 18, 30)).start != noon.start


# ---------- кто выходит ----------


def test_the_gang_is_five_against_three():
    """Лидер, трикстер, воин и два ассасина — как и заказано."""
    roster = HOOLIGAN_RAID.roster(GANG_PARTY)

    assert len(roster) == 5
    assert roster[0] is HOOLIGAN_RAID.leader
    assert [one.class_code for one in roster] == [
        "tank", "rogue", "warrior", "assassin", "assassin"
    ]


@pytest.mark.parametrize(
    "party,gang,added",
    [
        (3, 5, []),
        (4, 6, ["rogue"]),
        (5, 7, ["rogue", "warrior"]),
        (6, 8, ["rogue", "warrior", "assassin"]),
        (7, 9, ["rogue", "warrior", "assassin", "rogue"]),
        (10, 12, ["rogue", "warrior", "assassin"] * 2 + ["rogue"]),
    ],
)
def test_every_extra_fighter_brings_one_more_hooligan(party, gang, added):
    """За каждого сверх трёх — ещё один, по кругу с трикстера."""
    roster = HOOLIGAN_RAID.roster(party)

    assert len(roster) == gang
    assert [one.class_code for one in roster[5:]] == added


def test_the_leader_wears_the_fan_shop_and_the_rest_do_not():
    """Лидер — в фанатском из «Северного Вала», рядовые — в клубном.

    Это не косметика, а то, чем рейд держится проходимым: в одинаковой с
    отрядом экипировке впятером против трёх побед выходит 4%.
    """
    from bot.game.raid import boss_kit

    leader = boss_kit(HOOLIGAN_RAID.leader)
    assert all(
        owned.item.code.startswith("fan_boss_")
        for slot, owned in leader.items.items()
        if slot.value in ("weapon", "offhand", "shirt", "jacket", "pants")
    )
    crew = boss_kit(GANG_WARRIOR)
    worn = [owned.item.code for owned in crew.items.values()]
    assert "fan_warrior_bat" in worn, "своё оружие у него фанатское"
    assert sum(code.startswith("fan_") for code in worn) == 1


def test_the_gang_stands_on_its_own_level_whoever_comes():
    """Гопники не подстраиваются под отряд, в отличие от босса казино."""
    rookies = raid_foes(HOOLIGAN_RAID, [1, 1, 1])
    veterans = raid_foes(HOOLIGAN_RAID, [10, 10, 10])

    assert {one.level for one in rookies.values()} == {GANG_LEVEL}
    assert {one.level for one in veterans.values()} == {GANG_LEVEL}
    # и здоровья от толпы не набирают: у банды вместо прибавки лишние тела
    assert [one.max_hp for one in rookies.values()] == [
        one.max_hp for one in veterans.values()
    ]


def test_each_hooligan_gets_his_own_number_and_the_leader_keeps_the_old_one():
    """Номера идут от BOSS_ID вниз: рейд на одного босса остался прежним."""
    foes = raid_foes(HOOLIGAN_RAID, [10] * 3)

    assert list(foes) == [BOSS_ID, -2, -3, -4, -5]
    assert foes[BOSS_ID].name == "Лидер банды"
    assert foe_id(0) == BOSS_ID
    # у казино он один и под тем же номером
    assert list(raid_foes(CELLAR_RAID, [5, 5])) == [BOSS_ID]


def test_the_twins_are_told_apart_by_a_number():
    """Два ассасина с одним именем на табло слились бы в одного."""
    titles = foe_titles(HOOLIGAN_RAID.roster(GANG_PARTY))

    assert titles == (
        "Лидер банды", "Трикстер", "Воин", "Ассасин №1", "Ассасин №2"
    )
    # а одиночка остаётся без номера: «Лидер банды №1» — это не имя
    assert foe_titles((GANG_ROGUE,)) == ("Трикстер",)


def test_each_hooligan_has_his_own_habits():
    """Повадки у них разные: трикстер метит по ногам, ассасин — в живот."""
    assert max(GANG_ROGUE.temper.swings, key=GANG_ROGUE.temper.swings.get) == "legs"
    assert (
        max(GANG_ASSASSIN.temper.swings, key=GANG_ASSASSIN.temper.swings.get)
        == "belly"
    )
    assert max(
        HOOLIGAN_RAID.leader.temper.swings,
        key=HOOLIGAN_RAID.leader.temper.swings.get,
    ) in ("head", "chest")


# ---------- приговор ----------


def test_the_raid_is_won_only_when_the_whole_gang_is_down():
    """Один гопник на ногах — рейд идёт, сколько бы их ни легло."""
    from bot.game.classes import get_class
    from bot.game.combat import Fighter

    fclass = get_class("warrior")

    def one(user_id: int, hp: int) -> Fighter:
        fighter = Fighter(user_id, f"N{user_id}", fclass, fclass.base_stats)
        fighter.hp = hp
        return fighter

    party = {1: one(1, 50)}
    gang = [one(-1, 0), one(-2, 0), one(-3, 1)]

    assert judge_raid(gang, party) is None, "последний ещё стоит"
    gang[2].hp = 0
    assert judge_raid(gang, party).end is RaidEnd.WIN


def test_the_whole_gang_down_with_the_last_of_the_party_is_a_draw():
    from bot.game.classes import get_class
    from bot.game.combat import Fighter

    fclass = get_class("warrior")
    dead = [Fighter(-1, "A", fclass, fclass.base_stats)]
    dead[0].hp = 0
    party = {1: Fighter(1, "B", fclass, fclass.base_stats)}
    party[1].hp = 0

    assert judge_raid(dead, party).end is RaidEnd.DRAW


# ---------- дом и пропуск ----------


def test_the_stadium_is_where_the_gang_is_met():
    """Рейд у дома свой: казино — подвал, стадион — стычка."""
    assert kind_at("stadium") is HOOLIGAN_RAID
    assert kind_at("casino") is CELLAR_RAID
    assert kind_at("bar") is None
    stadium = get_location("stadium")
    assert stadium.allows(Service.RAID) and stadium.works


def test_the_history_knows_which_raid_a_hooligan_belongs_to():
    """По любому из банды видно, что это была стычка, а не подвал."""
    assert kind_of_boss("gang_assassin") is HOOLIGAN_RAID
    assert kind_of_boss("gang_leader") is HOOLIGAN_RAID
    assert kind_of_boss("cellar_boss") is CELLAR_RAID


def test_the_ticket_is_its_own_pass_and_costs_fifty():
    """Талон казино на стадионе не спрашивают, и наоборот."""
    ticket = get_potion(STADIUM_PASS)

    assert HOOLIGAN_RAID.pass_code == STADIUM_PASS
    assert CELLAR_RAID.pass_code == RAID_PASS
    assert ticket.price == 50
    assert ticket.is_pass is True  # его не пьют, его предъявляют


def test_every_raid_asks_for_its_own_pass_and_sells_it_somewhere():
    """Пропуск у каждого рейда свой и лежит в лавке, а не из воздуха."""
    codes = {kind.pass_code for kind in RAID_KINDS}

    assert len(codes) == len(RAID_KINDS), "два рейда с одним пропуском"
    for code in codes:
        assert get_potion(code) is not None


async def test_the_casino_ticket_does_not_open_the_stadium(bot, db):
    """В рюкзаке талон казино — на стадион всё равно не пустят."""
    service = make_service(bot, db)
    player = make_player(1, "Боец1")
    player.credits = 0
    await db.save_player(player)
    player.potions[RAID_PASS] = await db.add_potion(player.user_id, RAID_PASS)

    with pytest.raises(RaidError, match="Билет на матч"):
        await service.open_raid(CHAT_ID, THREAD_ID, player, HOOLIGAN_RAID)


async def test_a_win_on_the_stadium_leaves_the_cellar_open(bot, db):
    """Окна двух рейдов могут начаться в один час — путать их нельзя.

    В среду в полдень открыты и казино, и стадион, и начало окна у них
    одно и то же число. Пока рейд не стоял в ключе, победа на стадионе
    закрывала подвал, а один билет проходил за два.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    floor_gang(session)
    await storm(service, session, players)

    window = service.window_now(kind=HOOLIGAN_RAID)
    mine = await db.raid_window(players[0].user_id, window.start, "hooligans")
    assert mine and mine["won"]
    # а в казино то же окно чистое: ни билета не списано, ни победы
    assert await db.raid_window(players[0].user_id, window.start, "cellar") is None


# ---------- порог в три бойца ----------


async def test_two_fighters_are_not_let_out_against_five(bot, db):
    """Созвавший не может вывести отряд меньше трёх."""
    service = make_service(bot, db)
    players = await fill(db, 2)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], HOOLIGAN_RAID
    )
    await service.join(lobby.id, players[1])

    assert lobby.can_start is False
    with pytest.raises(RaidError, match="хотя бы 3"):
        await service.start_now(lobby.id, players[0].user_id)


async def test_the_third_fighter_makes_the_party(bot, db):
    """Трое — и отряд уже можно вывести."""
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], HOOLIGAN_RAID
    )
    for player in players[1:]:
        await service.join(lobby.id, player)

    assert lobby.can_start is True
    session = await service.start_now(lobby.id, players[0].user_id)
    assert session is not None and len(session.fighters) == 3


async def test_a_lone_opener_waits_out_the_three_minutes_and_goes_home(bot, db):
    """Никто не пришёл за три минуты — рейд не начинается."""
    service = make_service(bot, db, raid_lobby_timeout=0)
    players = await fill(db, 1)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], HOOLIGAN_RAID
    )

    await service._lobby_timer(lobby)

    assert service.raid_of_user(players[0].user_id) is None
    assert service.get_lobby(lobby.id) is None
    assert "отменён" in bot.edits[-1].text


async def test_the_countdown_is_three_minutes_long():
    """Три минуты на сбор — ровно столько, сколько заказано."""
    assert Config(bot_token="x").raid_lobby_timeout == 3 * 60


async def test_a_party_that_shrank_below_three_does_not_go_out(bot, db):
    """Собрались втроём, двое отвалились по здоровью — рейда нет."""
    service = make_service(bot, db)
    players = await fill(db, 3)
    lobby = await service.open_raid(
        CHAT_ID, THREAD_ID, players[0], HOOLIGAN_RAID
    )
    for player in players[1:]:
        await service.join(lobby.id, player)
    for player in players[1:]:
        hurt = await db.get_player(player.user_id)
        hurt.set_hp(0)
        await db.save_player(hurt)

    assert await service.start_now(lobby.id, players[0].user_id) is None
    assert "разбежался" in bot.edits[-1].text


# ---------- линия: кто против кого ----------


async def test_everyone_gets_his_own_hooligan(bot, db):
    """Трое против пятерых: у каждого свой противник, двое ждут."""
    service = make_service(bot, db)
    players, session = await gather(service, db)

    assert len(session.enemies) == 5
    assert sorted(session.pairs) == [one.user_id for one in players]
    assert len(set(session.pairs.values())) == 3, "каждому свой, не один на всех"
    waiting = set(session.standing) - set(session.pairs.values())
    assert len(waiting) == 2, "двое из банды ещё не в линии"


async def test_a_neighbour_s_kill_does_not_change_your_own_opponent(bot, db):
    """Добили кого-то рядом — твой противник остаётся твоим.

    Пары держатся, а не раздаются заново каждую волну. Без этого смерть
    чужого гопника перетряхивала всю линию: боец, который полволны бил
    одного, со следующей волны вставал против другого — целого, — а его
    битый уходил к соседу.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    was = dict(session.pairs)
    middle = players[1].user_id
    # Валим противника среднего бойца, остальных не трогаем
    session.enemies[was[middle]].hp = 0

    for player in players:
        await punch(service, session, player.user_id)

    assert session.wave == 2
    assert session.pairs[players[0].user_id] == was[players[0].user_id]
    assert session.pairs[players[2].user_id] == was[players[2].user_id]
    # А осиротевший встал против того, кто ждал на скамейке
    assert session.pairs[middle] not in was.values()
    assert session.enemies[session.pairs[middle]].alive


async def test_the_bench_steps_in_for_the_fallen(bot, db):
    """Добил своего — со следующей волной выходит тот, кто ждал."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    mine = players[0].user_id
    first = session.pairs[mine]
    # Валим его наверняка, а не ударом: удар может и не дойти, и тогда
    # тест проверял бы кости, а не смену в линии
    session.enemies[first].hp = 0

    for player in players:
        await punch(service, session, player.user_id)

    assert session.enemies[first].alive is False
    assert session.wave == 2, "волна закрылась, линия построена заново"
    assert session.pairs[mine] != first, "на его место вышел следующий"
    assert session.enemies[session.pairs[mine]].alive


async def test_the_last_hooligan_is_ganged_up_on(bot, db):
    """Противников меньше, чем бойцов, — наваливаются на одного.

    Этим же правилом держится и подвал: трое против одного босса — это
    трое на нём, а не один, пока двое курят.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    last = session.pairs[players[0].user_id]
    for number, enemy in session.enemies.items():
        if number != last:
            enemy.hp = 0
    session.pairs = {}
    session.form_line()

    assert set(session.pairs.values()) == {last}
    assert len(session.pairs) == 3


async def test_the_fighter_hits_his_own_hooligan_and_not_the_leader(bot, db):
    """Урон идёт тому, с кем боец стоит, а не первому в списке."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    # ставим бойца против не-лидера: иначе проверка ничего не различает
    mate = next(number for number in session.standing if number != BOSS_ID)
    session.pairs[players[0].user_id] = mate
    full = {number: one.hp for number, one in session.enemies.items()}

    await punch(service, session, players[0].user_id)

    assert session.enemies[mate].hp < full[mate] or session.rounds
    assert session.enemies[BOSS_ID].hp == full[BOSS_ID], "лидера он не трогал"


async def test_a_swing_at_a_finished_gang_is_not_an_exchange(bot, db):
    """Банда кончилась посреди волны — остальные бьют в воздух, а не в труп.

    Своего противника у бойца в этот миг уже нет: он лежит, а нового
    линия даст только со следующей волны. Такой ход не должен ни считаться
    разменом, ни тем более роняться об отсутствующего соперника.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    for enemy in session.enemies.values():
        enemy.hp = 0
    rounds = len(session.rounds)

    await punch(service, session, players[0].user_id)

    assert len(session.rounds) == rounds, "размена не было: бить некого"
    assert players[0].user_id in session.acted


# ---------- итог ----------


async def test_a_beaten_gang_pays_everyone_in_full(bot, db):
    """Кошель не делится: каждому своё, за свой билет."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    floor_gang(session)
    await storm(service, session, players)

    assert session.finished
    assert set(session.shares.values()) == {GANG_PURSE}
    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.credits == 500 + GANG_PURSE


async def test_the_cellar_still_splits_its_purse(bot, db):
    """Правило кошелька своё у каждого рейда, и подвал не задело."""
    assert CELLAR_RAID.split is True
    assert HOOLIGAN_RAID.split is False
    assert CELLAR_RAID.shares(3) == [34, 33, 33]
    assert HOOLIGAN_RAID.shares(3) == [GANG_PURSE] * 3


def test_the_config_default_matches_the_rules():
    """Настройка и правило называют одно число, а не два разных."""
    from bot.game.raid import RAID_PURSE

    assert Config(bot_token="x").raid_gang_purse == GANG_PURSE
    assert Config(bot_token="x").raid_purse == RAID_PURSE


async def test_the_record_remembers_the_gang_by_its_leader(bot, db):
    """В истории рейд стоит под лидером: по нему и узнаётся стычка."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    floor_gang(session)
    await storm(service, session, players)

    rows = await db.raids_of(players[0].user_id)
    assert rows[0]["boss"] == "gang_leader"
    assert rows[0]["boss_level"] == GANG_LEVEL
    assert kind_of_boss(rows[0]["boss"]) is HOOLIGAN_RAID


# ---------- стадион глазами мини-аппа ----------


@pytest.fixture
async def stadium(db):
    """Мини-апп и служба рейдов. Трое стоят на стадионе с билетами."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot.webapp.server import create_app
    from tests.test_fight_app import make_player as app_player
    from tests.test_webapp import FakeBot as WebBot, TOKEN

    raids = make_service(FakeBot(), db)
    config = Config(bot_token=TOKEN, webapp_url="https://club.example")
    for user_id, nickname in ((42, "Тайлер"), (43, "Марла"), (44, "Зевака")):
        player = app_player(user_id, nickname)
        player.credits = 500
        player.level = GANG_LEVEL
        # Стычку собирают на стадионе: дом и решает, какой это рейд
        player.location = "stadium"
        await db.save_player(player)
        await db.add_potion(user_id, STADIUM_PASS)
    app = create_app(WebBot(), db, config, raids=raids)
    async with TestClient(TestServer(app)) as client:
        yield client, raids, db
    await raids.shutdown()


async def screen(client, user_id: int = 42) -> dict:
    from tests.test_fight_app import headers

    response = await client.get("/api/raid", headers=headers(user_id))
    assert response.status == 200
    return await response.json()


async def act(client, user_id: int, **payload) -> tuple[int, dict]:
    from tests.test_fight_app import headers

    response = await client.post("/api/raid", json=payload, headers=headers(user_id))
    return response.status, await response.json()


async def test_the_stadium_screen_is_about_the_gang(stadium):
    """Какой рейд собирают, решает дом, в котором боец стоит."""
    client, _, _ = stadium

    body = await screen(client)

    assert body["kind"]["code"] == "hooligans"
    assert body["kind"]["title"] == "Стычка с футбольными фанатами"
    assert body["kind"]["min_party"] == GANG_PARTY
    assert body["kind"]["foes"] == 5 and body["kind"]["grows"] is True
    assert body["kind"]["alone"] is False
    assert body["where"] == "Стадион" and body["here"] is True
    # и пропуск спрашивают здешний, а не талон казино
    assert body["gate"]["pass_code"] == STADIUM_PASS
    assert body["gate"]["pass_price"] == 50


async def test_the_screen_shows_the_whole_gang_before_the_fight(stadium):
    """Под кнопкой «i» — весь состав, а не один босс."""
    client, _, _ = stadium

    body = await screen(client)

    assert len(body["roster"]) == 5
    assert [one["title"] for one in body["roster"]] == [
        "Лидер банды", "Трикстер", "Воин", "Ассасин №1", "Ассасин №2"
    ]
    for card in body["roster"]:
        assert card["alone"] is False and card["live"] is False
        assert card["level"] == GANG_LEVEL and card["max_hp"] > 0
        assert card["slots"], "кукла собирается, как у бойца"


async def test_the_casino_screen_is_still_about_the_boss(stadium, db):
    """Отошёл в казино — и экран снова про подвал, со своим пропуском."""
    client, _, _ = stadium
    player = await db.get_player(42)
    player.location = "casino"
    await db.save_player(player)

    body = await screen(client)

    assert body["kind"]["code"] == "cellar"
    assert body["kind"]["alone"] is True and body["kind"]["foes"] == 1
    assert body["gate"]["pass_code"] == RAID_PASS
    assert len(body["roster"]) == 1


async def test_the_board_names_everyone_and_marks_your_own(stadium):
    """В идущем рейде видна вся банда, и свой противник помечен."""
    client, raids, _ = stadium
    await act(client, 42, action="open")
    lobby = raids.lobby_of_user(42)
    await act(client, 43, action="join", lobby_id=lobby.id)
    await act(client, 44, action="join", lobby_id=lobby.id)
    await act(client, 42, action="go")

    body = await screen(client)
    gang = body["raid"]["gang"]

    assert len(gang) == 5
    assert sum(one["yours"] for one in gang) == 1, "свой ровно один"
    assert body["raid"]["foe"]["yours"] is True
    assert body["raid"]["foe"]["number"] == gang[
        [one["yours"] for one in gang].index(True)
    ]["number"]
    # и у каждого видно, против кого он стоит
    engaged = [one for one in gang if one["against"] is not None]
    assert len(engaged) == 3


async def test_the_analyst_reads_your_own_hooligan(stadium, db):
    """Подписчику разбирают того, с кем он стоит, а не лидера банды."""
    client, raids, _ = stadium
    for user_id in (42, 43, 44):
        player = await db.get_player(user_id)
        player.pro_until = 2_000_000_000
        await db.save_player(player)
    await act(client, 42, action="open")
    lobby = raids.lobby_of_user(42)
    await act(client, 43, action="join", lobby_id=lobby.id)
    await act(client, 44, action="join", lobby_id=lobby.id)
    await act(client, 42, action="go")
    session = raids.raid_of_user(42)
    # Ставим бойца против не-лидера: иначе разбор лидера и разбор своего
    # противника выглядели бы одинаково
    mate = next(number for number in session.standing if number != BOSS_ID)
    session.pairs[42] = mate

    scout = (await screen(client))["raid"]["scout"]

    assert scout is not None
    assert session.enemies[mate].name in scout["title"]
    assert session.template_of(mate).manner in scout["title"]


async def test_the_stadium_lobby_is_not_offered_in_the_casino(stadium, db):
    """Чужие сборы видно только те, что идут здесь же.

    Из казино в стычку на стадионе всё равно не записаться, и показывать
    её там значило бы рисовать кнопку, на которую сервер ответит отказом.
    """
    client, raids, _ = stadium
    await act(client, 42, action="open")

    other = await db.get_player(43)
    other.location = "stadium"
    await db.save_player(other)
    assert len((await screen(client, 43))["lobbies"]) == 1

    other.location = "casino"
    await db.save_player(other)
    assert (await screen(client, 43))["lobbies"] == []


async def test_the_map_counts_both_raids_separately(stadium):
    """Плашки на карте по дому: у казино своя, у стадиона своя."""
    from tests.test_fight_app import headers

    client, _, _ = stadium

    response = await client.get("/api/map", headers=headers(42))
    body = await response.json()

    assert set(body["raids"]) == {"casino", "stadium"}
    # расписание в этих тестах снято, значит открыты оба
    assert body["raids"]["stadium"]["state"] == "open"
    assert body["raids"]["casino"]["state"] == "open"
