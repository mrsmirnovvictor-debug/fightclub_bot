"""Стычка с футбольными фанатами: второй рейд города и всё, чем он не первый.

Отличий от подвала четыре, и каждое здесь проверяется отдельно: твёрдое
расписание вместо жребия, свой пропуск, порог в три бойца и — главное —
противник не один, а банда, которую валят по человеку.

Баланс здесь не проверяется: за него отвечает `scripts/gang_raid.py`.
Тесты про механику — кто против кого встал, кто кого сменил, кому что
досталось.
"""

import random
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest

from bot.config import Config
from bot.database import Database
from bot.game.locations import Service, get_location
from bot.game.potions import RAID_PASS, STADIUM_PASS, get_potion
from bot.game.raid import (
    BOSS_ID,
    CELLAR_RAID,
    GANG_ASSASSINS,
    GANG_LEVEL,
    GANG_PARTY,
    GANG_PURSE,
    GANG_ROGUES,
    GANG_WARRIORS,
    HOOLIGAN_RAID,
    MOSCOW,
    RAID_KINDS,
    RaidEnd,
    Weekly,
    foe_id,
    judge_raid,
    kind_at,
    kind_of_boss,
    raid_foes,
    window_of,
)
from bot.raid_service import RaidError, RaidService
from tests.test_duel_flow import FakeBot
from tests.test_battle_flow import make_player

CHAT_ID = -100500
THREAD_ID = 7

# Среда, 7 октября 2026 года: день, в который фанаты выходят
WEDNESDAY = date(2026, 10, 7)
TUESDAY = date(2026, 10, 6)
# Четверг нужен окну: оно кончается в полночь, то есть уже назавтра
THURSDAY = date(2026, 10, 8)


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


@asynccontextmanager
async def one_fight(bot, seed: int):
    """Победный бой на своей базе: своё зерно — своя добыча.

    База закрывается и когда проверка упала: иначе упавший тест оставлял
    бы за собой открытую базу и живую службу, и прогон вис бы на уборке
    вместо того, чтобы показать поломку. Это стоило одного зависшего
    укуса — поэтому уборка здесь в `finally`, а не после проверок.
    """
    db = Database(":memory:")
    await db.connect()
    service = make_service(bot, db)
    service.rng = random.Random(seed)
    try:
        players, session = await gather(service, db)
        toughen(session)
        floor_gang(session)
        await storm(service, session, players)
        yield db, players, session
    finally:
        await service.shutdown()
        await db.close()


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
    """Среда и суббота, с шести вечера до полуночи — и больше никогда."""
    schedule = HOOLIGAN_RAID.schedule

    assert [window.title for window in schedule.windows_on(WEDNESDAY)] == [
        "с 18:00 до 00:00 мск"
    ]
    assert schedule.windows_on(date(2026, 10, 10))  # суббота
    for quiet in (5, 6, 8, 9, 11):  # пн, вт, чт, пт, вс
        assert schedule.windows_on(date(2026, 10, quiet)) == ()


def test_the_window_is_six_hours_and_not_a_minute_more():
    """В 17:59 закрыто, в 18:00 открыто, в полночь снова закрыто.

    Окно упирается в полночь, и это его единственное место, где день
    кончается раньше окна. В 23:59 его открыла среда, в 00:00 четверга
    оно уже кончилось — а четверг своего окна не открывает вовсе.
    """
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 17, 59)) is None
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 18)) is not None
    assert HOOLIGAN_RAID.window_now(moscow(WEDNESDAY, 23, 59)) is not None
    assert HOOLIGAN_RAID.window_now(moscow(THURSDAY, 0)) is None
    assert HOOLIGAN_RAID.window_now(moscow(THURSDAY, 1)) is None

    # и кончается оно ровно в полночь, а не в двадцать четыре часа среды
    window = HOOLIGAN_RAID.schedule.windows_on(WEDNESDAY)[0]
    assert window.end == moscow(THURSDAY, 0)
    assert window.end - window.start == 6 * 3600


def test_the_schedule_is_spoken_once_and_for_all():
    """Жребия тут нет: расписание называется одними и теми же словами."""
    said = HOOLIGAN_RAID.schedule_text(moscow(TUESDAY, 10))
    assert said == "по средам и субботам с 18:00 до 00:00 мск"
    # и во вторник, и в среду — то же самое: выучить его можно и нужно
    assert HOOLIGAN_RAID.schedule_text(moscow(WEDNESDAY, 13)) == said
    # и сказано оно так же, как подписано окно в карточке: окно кончается
    # в полночь, а не в двадцать четыре часа
    assert HOOLIGAN_RAID.schedule.windows_on(WEDNESDAY)[0].title in said


def test_the_next_window_reaches_across_the_quiet_days():
    """От воскресенья до среды четыре дня — расписание их проходит.

    Прошлому поиску хватало двух дней вперёд, потому что подвал открыт
    каждый день. Недельному расписанию двух дней мало, и без этого
    «ближайшее окно» упиралось бы в пустоту.
    """
    sunday = moscow(date(2026, 10, 11), 20)
    assert HOOLIGAN_RAID.next_window(sunday).start == moscow(date(2026, 10, 14), 18)
    # в ночь после матча следующее — суббота
    after = moscow(THURSDAY, 1)
    assert HOOLIGAN_RAID.next_window(after).start == moscow(date(2026, 10, 10), 18)


def test_a_window_that_crosses_midnight_is_opened_by_the_day_before():
    """Окно, начавшееся вчера, сегодня всё ещё вчерашнее.

    Стадион упирается в полночь ровно и за неё не переходит, казино
    кончает в десять вечера, — ни одно нынешнее расписание через полночь
    не идёт. Но `window_of` смотрит и вчерашний день, и это не лишняя
    строка, а единственное, на чём держалось бы такое окно: по
    сегодняшнему расписанию ночь после матча не открыта ничем.

    Проверяем на расписании, которого в игре нет: иначе эту строку
    снесли бы как мёртвую, а следующее же окно за полночь тихо
    закрывалось бы в 00:00.
    """
    through = Weekly(weekdays=(2,), hour=22, hours=4)

    assert window_of(moscow(WEDNESDAY, 23), through) is not None
    # час ночи четверга: четверг своего окна не открывает вовсе, и это
    # окно нашлось только потому, что заглянули во вчера
    night = window_of(moscow(THURSDAY, 1), through)
    assert night is not None and night.start == moscow(WEDNESDAY, 22)
    assert window_of(moscow(THURSDAY, 2), through) is None


def test_the_two_raids_keep_their_own_schedules():
    """Расписание казино осталось жребием, а стадиона — твёрдым."""
    assert isinstance(HOOLIGAN_RAID.schedule, Weekly)
    assert not isinstance(CELLAR_RAID.schedule, Weekly)
    # и в среду вечером может быть открыто и там, и там: у казино в этот
    # день выпал слот с восьми, у стадиона окно с шести до полуночи
    evening = moscow(WEDNESDAY, 20, 30)
    assert HOOLIGAN_RAID.window_now(evening) is not None
    assert CELLAR_RAID.window_now(evening) is not None


def test_a_six_hour_window_counts_as_one_even_without_a_schedule():
    """Выключатель расписания не должен ломать «одна победа на окно».

    При снятом расписании окно нарезается по длине своего: у стадиона она
    шестичасовая, и часовая клетка дала бы шесть побед за вечер.
    """
    evening = HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 18, 30))
    midnight = HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 23, 30))
    assert evening.start == midnight.start
    assert evening.end - evening.start == 6 * 3600
    assert HOOLIGAN_RAID.any_window(moscow(WEDNESDAY, 17, 30)).start != evening.start


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


def test_the_whole_gang_wears_the_fan_shop():
    """Вся банда — в фанатском из «Северного Вала», своей линией на класс.

    Фанатских вещей на класс семь: шапка, оружие, футболка, пояс,
    куртка, штаны и кроссовки. Перчаток и щитов в той линии нет, и
    пустые слоты добираются клубным — так же, как у игрока, который
    скупил «Северный Вал» целиком.
    """
    from bot.game.raid import GANG, boss_kit

    lines = {
        "tank": "fan_boss_",
        "rogue": "fan_rogue_",
        "warrior": "fan_warrior_",
        "assassin": "fan_assassin_",
    }
    for one in GANG:
        worn = [owned.item.code for owned in boss_kit(one).items.values()]
        mine = [code for code in worn if code.startswith(lines[one.class_code])]
        # У линии лидера есть ещё и щит — восьмая вещь; у остальных семь
        assert len(mine) == (8 if one.class_code == "tank" else 7), (
            f"{one.title}: не свой комплект — {worn}"
        )
        # Из чужой линии — только щит, и только тому, кому он разрешён:
        # щит в «Северном Вале» один на весь магазин, лежит в линии
        # лидера, и воину его носить можно
        foreign = [
            code
            for kind, prefix in lines.items()
            if kind != one.class_code
            for code in worn
            if code.startswith(prefix)
        ]
        assert set(foreign) <= {"fan_boss_shield"}, (
            f"{one.title} надел чужую линию: {foreign}"
        )


def test_an_npc_never_wears_what_his_class_cannot():
    """Снаряжение NPC видно по кнопке «i» — и врать в нём нельзя.

    Пустые слоты добираются с прилавка самым дорогим, что в них лезет, а
    своей вещи у класса там может не оказаться вовсе: ассасину так
    достаётся щит, который живой ассасин с земли не поднял бы — и заодно
    лишает его второй руки. Теперь такое не надевается, и слот остаётся
    пустым.
    """
    from bot.game.raid import BOSSES, GANG, boss_kit

    for one in (*GANG, *BOSSES):
        for slot, owned in boss_kit(one).items.items():
            allowed = owned.item.for_classes
            assert not allowed or one.class_code in allowed, (
                f"{one.title}: {owned.item.code} не для класса {one.class_code}"
            )


def test_gear_named_by_hand_overrides_the_class_rule():
    """Что боссу прописали руками, то на нём и останется.

    Правило класса сторожит добор с прилавка — там выбирает не человек, а
    «самое дорогое, что лезет в слот». Названное же выбрано руками, и
    если кому-то однажды выпишут чужую вещь нарочно (уникальный трофей,
    чей-то щит), снимать её не наше дело. Порядок поэтому такой: сперва
    фильтруем прилавок, потом кладём названное сверху.
    """
    from dataclasses import replace

    from bot.game.raid import boss_kit

    # Щит фанатского сектора ассасину не положен — но если выписать его
    # руками, он должен остаться
    stubborn = replace(GANG_ASSASSINS[0], gear=("fan_boss_shield",))
    worn = {owned.item.code for owned in boss_kit(stubborn).items.values()}

    assert "fan_boss_shield" in worn
    # А тот же ассасин без приписки щита не носит
    assert "fan_boss_shield" not in {
        owned.item.code for owned in boss_kit(GANG_ASSASSINS[0]).items.values()
    }


def test_the_gang_is_dressed_like_fighters_but_not_trained_like_them():
    """Выучка — вот чем рейд держится проходимым, а не одеждой.

    Рядовому гопнику характеристики распределены по седьмому, хотя
    сам он десятого и одет по-боевому: форма есть, зала нет. Выучи их
    полностью — и впятером против трёх побед выходит 5%, то есть рейд
    непроходим. А лидер — боец настоящий, и накачан он по своему уровню.
    """
    from bot.game.raid import GANG_BUILD

    assert GANG_BUILD == 7
    assert HOOLIGAN_RAID.leader.build_level == 0, "лидер качан по своему уровню"
    for one in (*GANG_ROGUES, *GANG_WARRIORS, *GANG_ASSASSINS):
        assert one.build_level == GANG_BUILD

    # Сравниваем не с голой характеристикой, а с тем же гопником, выучи
    # его полностью: прибавки от фанатского комплекта у обоих одни и те
    # же, и разницу даёт ровно выучка
    from dataclasses import replace

    from bot.game.raid import boss_fighter

    for template in HOOLIGAN_RAID.roster(GANG_PARTY):
        plain = boss_fighter(template, [GANG_LEVEL], 0.0, level=GANG_LEVEL)
        trained = boss_fighter(
            replace(template, build_level=0), [GANG_LEVEL], 0.0, level=GANG_LEVEL
        )
        if template.build_level:
            assert plain.stats.total() < trained.stats.total(), (
                f"{template.title} накачан как боец своего уровня"
            )
            assert plain.max_hp < trained.max_hp
        else:
            assert plain.stats.total() == trained.stats.total()


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
    assert foes[BOSS_ID].name == "Лидер банды — Майор"
    assert foe_id(0) == BOSS_ID
    # у казино он один и под тем же номером
    assert list(raid_foes(CELLAR_RAID, [5, 5])) == [BOSS_ID]


def test_everyone_in_the_gang_has_his_own_nickname():
    """Кличка у каждого своя: на табло из тринадцати номера не читаются."""
    from bot.game.raid import GANG

    nicks = [one.title for one in GANG]

    assert len(nicks) == 13
    assert len(set(nicks)) == 13, "две одинаковые клички в банде"
    assert nicks[0] == "Лидер банды — Майор"
    assert {"Мажорчик", "Валера", "Серый", "Тощий"} <= set(nicks)
    assert {"Ярый", "Седой", "Дубина", "Аркадич"} <= set(nicks)
    assert {"Бритва", "Кастет", "Мелкий", "Киллер"} <= set(nicks)
    # и в любом отряде на поле нет двух с одной кличкой
    for party in range(GANG_PARTY, HOOLIGAN_RAID.max_party + 1):
        out = [one.title for one in HOOLIGAN_RAID.roster(party)]
        assert len(out) == len(set(out)), f"повтор при отряде из {party}"


def test_the_gang_looks_at_you_from_the_avatars_folder():
    """Портреты гопников лежат среди образов, а не среди боссов.

    Их четыре на тринадцать человек — по лицу на класс: рисовали их
    вместе с фанатской линией одежды, и там же они и выгружены. Босс
    казино по-прежнему смотрит из своей папки.
    """
    from bot.game import art
    from bot.game.raid import GANG

    assert GANG[0].image == f"{art.AVATARS}/fan_boss.jpeg"
    faces = {one.image for one in GANG}
    assert faces == {
        f"{art.AVATARS}/fan_boss.jpeg",
        f"{art.AVATARS}/fan_rogue.jpeg",
        f"{art.AVATARS}/fan_warrior.jpeg",
        f"{art.AVATARS}/fan_assassin.jpeg",
    }
    # и лицо у всех одного класса общее
    assert len({one.image for one in GANG_ROGUES}) == 1
    # а подвал как смотрел из bosses/, так и смотрит
    assert CELLAR_RAID.leader.image == art.boss("cellar_boss")


def test_each_hooligan_has_his_own_habits():
    """Повадки у них разные: трикстер метит по ногам, ассасин — в живот."""
    rogue, assassin = GANG_ROGUES[0], GANG_ASSASSINS[0]
    assert max(rogue.temper.swings, key=rogue.temper.swings.get) == "legs"
    assert max(assassin.temper.swings, key=assassin.temper.swings.get) == "belly"
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
    assert kind_of_boss("gang_britva") is HOOLIGAN_RAID
    assert kind_of_boss("gang_major") is HOOLIGAN_RAID
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


# ---------- круг: кого бьём сейчас ----------


async def test_everyone_starts_on_the_leader(bot, db):
    """Круг один на отряд, и начинается он с первого — с Майора."""
    service = make_service(bot, db)
    players, session = await gather(service, db)

    assert len(session.enemies) == 5
    assert sorted(session.aim) == [one.user_id for one in players]
    assert set(session.aim.values()) == {BOSS_ID}
    assert session.enemies[BOSS_ID].name == "Лидер банды — Майор"


async def test_a_strike_moves_the_fighter_on_to_the_next(bot, db):
    """Отработал своего — дальше по кругу, не дожидаясь волны."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    mine = players[0].user_id

    await punch(service, session, mine)

    assert session.aim[mine] == -2, "следующий по порядку банды"


async def test_the_circle_walks_the_whole_gang_and_comes_back(bot, db):
    """Пять противников — пять ходов, и шестой снова по лидеру."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    # Банду держим на ногах: круг проверяем, а не то, кто кого свалит
    for enemy in session.enemies.values():
        enemy.hp = 100_000
    mine = players[0].user_id
    seen = []

    for _ in range(6):
        seen.append(session.aim[mine])
        for player in players:
            await punch(service, session, player.user_id)

    assert seen == [BOSS_ID, -2, -3, -4, -5, BOSS_ID]


async def test_the_circle_steps_over_the_dead(bot, db):
    """Упавшего круг перешагивает: бить труп не дают."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    for enemy in session.enemies.values():
        enemy.hp = 100_000
    mine = players[0].user_id
    session.enemies[-2].hp = 0  # следующий по кругу уже лежит

    await punch(service, session, mine)

    assert session.aim[mine] == -3


async def test_the_last_one_standing_is_everyone_s_target(bot, db):
    """Из банды остался один — круг сводится к нему."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    last = -4
    for number, enemy in session.enemies.items():
        if number != last:
            enemy.hp = 0
    session.enemies[last].hp = 100_000
    session.aim = {}
    session.take_aim()

    assert set(session.aim.values()) == {last}
    assert len(session.aim) == 3
    # и круг из одного человека с него же и не уходит
    assert session.next_foe(last) == last


async def test_the_fighter_hits_the_one_he_is_aiming_at(bot, db):
    """Урон идёт тому, на кого наведён боец, а не первому в списке."""
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    mate = -3
    session.aim[players[0].user_id] = mate
    full = {number: one.hp for number, one in session.enemies.items()}

    await punch(service, session, players[0].user_id)

    assert session.enemies[mate].hp < full[mate] or session.rounds
    assert session.enemies[BOSS_ID].hp == full[BOSS_ID], "лидера он не трогал"


async def test_a_silent_turn_is_punished_by_that_one_hooligan_only(bot, db):
    """Промолчал минуту — ударит тот, кого он должен был бить.

    Не вся банда сразу: остальным до него очередь не дошла. И круг при
    этом сдвигается, иначе молчание ничего бы не меняло и боец стоял бы
    против одного и того же до конца боя.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    mine = players[0].user_id
    target = session.aim[mine]
    was = session.fighters[mine].hp
    full = {number: one.hp for number, one in session.enemies.items()}

    # Время вышло: за молчавших дожимает служба
    await service.skip_the_rest(session)

    assert session.fighters[mine].hp <= was, "его ударил его же противник"
    assert session.aim[mine] != target, "круг сдвинулся и без удара"
    # Сам он не ударил никого: в журнале его размен есть, но урона нет
    assert session.enemies[target].hp == full[target]


async def test_the_gang_gives_a_whole_minute_for_a_strike(bot, db):
    """Минута на удар, а не полминуты: цель каждый раз новая."""
    from bot.game.raid import GANG_TURN_SECONDS

    assert GANG_TURN_SECONDS == 60
    assert Config(bot_token="x").raid_gang_turn_timeout == 60
    # А у подвала свой срок, и он остался прежним
    assert Config(bot_token="x").raid_turn_timeout == 30

    service = make_service(bot, db, raid_gang_turn_timeout=60)
    _, session = await gather(service, db)
    assert session.kind.turn_seconds == 60


async def test_a_swing_at_a_finished_gang_is_not_an_exchange(bot, db):
    """Банда кончилась посреди волны — остальные бьют в воздух, а не в труп.

    Цели у бойца в этот миг уже нет: круг обошёл всех, и все лежат. Такой
    ход не должен ни считаться разменом, ни роняться об отсутствующего.
    """
    service = make_service(bot, db)
    players, session = await gather(service, db)
    toughen(session)
    for enemy in session.enemies.values():
        enemy.hp = 0
    session.aim = {}
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
    assert GANG_PURSE == 250


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
    assert rows[0]["boss"] == "gang_major"
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


@pytest.fixture
async def cellar_app(db):
    """Тот же апп, но боец стоит в казино и с талоном подвала."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot.webapp.server import create_app
    from tests.test_fight_app import make_player as app_player
    from tests.test_webapp import FakeBot as WebBot, TOKEN

    raids = make_service(FakeBot(), db)
    player = app_player(42, "Тайлер")
    player.credits = 500
    player.location = "casino"
    await db.save_player(player)
    await db.add_potion(42, RAID_PASS)
    app = create_app(WebBot(), db, Config(bot_token=TOKEN), raids=raids)
    async with TestClient(TestServer(app)) as client:
        yield client, raids
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
        "Лидер банды — Майор", "Мажорчик", "Ярый", "Бритва", "Кастет"
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


async def test_the_casino_sends_no_target_of_its_own(cellar_app):
    """Цель — дело банды: в подвале босс один, и подпись ни к чему."""
    client, raids = cellar_app
    await act(client, 42, action="open")
    # В подвал пускают и одного: отряд выводит созвавший
    await act(client, 42, action="go")
    session = raids.raid_of_user(42)

    body = await screen(client)

    assert session is not None and not session.gang
    assert body["raid"]["foe"] is None
    assert len(body["raid"]["gang"]) == 1


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
    assert [one["title"] for one in gang][:2] == [
        "Лидер банды — Майор", "Мажорчик"
    ]
    # Круг начинается с лидера, значит на нём сейчас весь отряд
    assert gang[0]["yours"] is True and gang[0]["against"] == 42
    assert body["raid"]["foe"]["number"] == gang[0]["number"]
    assert sum(one["yours"] for one in gang) == 1, "свой ровно один"


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
    session.aim[42] = mate

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


# ---------- добыча ----------


def test_the_gang_drops_a_fan_thing_to_the_best_and_a_potion_to_all():
    """Три разных броска, и путать их нельзя."""
    from bot.content.items import FAN_ITEMS
    from bot.game.raid import GANG_POTIONS, GANG_SPOILS

    assert GANG_SPOILS.item_chance == 0.75
    assert GANG_SPOILS.potion_chance == 0.8
    # Вещь — с фанатского прилавка, и только оттуда
    shop = {item.code for item in FAN_ITEMS}
    for seed in range(60):
        code = GANG_SPOILS.item_for(random.Random(seed))
        assert code is None or code in shop
    # Склянки — ровно те четыре, что заказаны
    assert set(GANG_POTIONS) == {
        "boost_strength", "boost_agility", "boost_endurance", "boost_hp"
    }
    for seed in range(60):
        code = GANG_SPOILS.potion_for(random.Random(seed))
        assert code is None or code in GANG_POTIONS
    # Склянки лучшему по урону банда не даёт — это награда подвала
    assert GANG_SPOILS.elixir_for(random.Random(1)) is None


def test_the_chances_are_really_those_chances():
    """Три четверти и четыре пятых — не на глаз, а по прогону."""
    from bot.game.raid import GANG_SPOILS

    rng = random.Random(11)
    things = sum(GANG_SPOILS.item_for(rng) is not None for _ in range(4000))
    potions = sum(GANG_SPOILS.potion_for(rng) is not None for _ in range(4000))

    assert 0.72 < things / 4000 < 0.78
    assert 0.77 < potions / 4000 < 0.83


def test_the_cellar_keeps_its_own_spoils():
    """Подвал по-прежнему роняет склянку лучшему, и ничего больше."""
    from bot.game.raid import CELLAR_SPOILS

    assert CELLAR_SPOILS.item_for(random.Random(1)) is None
    assert CELLAR_SPOILS.potion_for(random.Random(1)) is None
    assert any(
        CELLAR_SPOILS.elixir_for(random.Random(seed)) for seed in range(20)
    )


def test_a_prize_is_read_whether_it_is_a_thing_or_a_potion():
    """Код приза один, а приз бывает и вещью, и склянкой."""
    from bot.game.raid import prize_of

    assert prize_of("fan_assassin_belt")[1] == "Компактный тактический пояс"
    assert prize_of("boost_endurance")[1] == "Эликсир выносливости"
    assert prize_of("нет такого") is None


async def test_only_the_best_takes_a_thing_and_never_two(bot, db):
    """Вещь падает лучшему по урону, по одной и больше никому.

    Гоняем несколько боёв, а не один: три четверти — это и четверть
    пустых рук, и привязывать тест к зерну значило бы проверять зерно.
    Правило же в другом — кому и сколько, а не выпало ли в этот раз.
    """
    from bot.content.items import FAN_ITEMS

    shop = {item.code for item in FAN_ITEMS}
    lucky = 0
    for seed in range(8):
        async with one_fight(bot, seed) as (fresh, players, session):
            assert session.finished
            best = max(
                session.fighters, key=lambda uid: session.fighters[uid].damage_dealt
            )
            for player in players:
                bag = await fresh.list_gear(player.user_id)
                dropped = [one.item.code for one in bag if one.item.code in shop]
                if player.user_id == best:
                    assert len(dropped) <= 1, "за бой падает одна вещь, не стопка"
                    lucky += len(dropped)
                else:
                    assert not dropped, "вещь ушла не лучшему по урону"

    assert lucky, "за восемь боёв не упало ни одной вещи"


async def test_the_potion_falls_to_everyone_or_to_nobody(bot, db):
    """Один бросок на отряд: либо склянка у всех, либо ни у кого."""
    from bot.game.raid import GANG_POTIONS

    seen = set()
    for seed in range(6):
        async with one_fight(bot, seed) as (fresh, players, session):
            assert session.finished
            got = []
            for player in players:
                rows = await fresh.list_potions(player.user_id)
                got.append(
                    sum(count for code, count in rows.items() if code in GANG_POTIONS)
                )
            assert len(set(got)) == 1, f"зерно {seed}: склянка досталась не всем"
            assert got[0] in (0, 1), "за бой падает одна склянка, а не стопка"
            seen.add(got[0])

    assert seen == {0, 1}, "за шесть боёв не выпало и того, и другого"


async def test_a_lost_raid_drops_nothing_at_all(bot, db):
    """Проиграли — ни кредитов, ни вещей, ни склянок."""
    from bot.content.items import FAN_ITEMS
    from bot.game.raid import GANG_POTIONS

    service = make_service(bot, db)
    players, session = await gather(service, db)
    for fighter in session.fighters.values():
        fighter.hp = 1
    for enemy in session.enemies.values():
        enemy.hp = 100_000
    await storm(service, session, players)

    assert session.finished and not session.shares
    shop = {item.code for item in FAN_ITEMS}
    for player in players:
        fresh = await db.get_player(player.user_id)
        assert fresh.credits == 500
        assert not [one for one in await db.list_gear(player.user_id)
                    if one.item.code in shop]
        rows = await db.list_potions(player.user_id)
        assert not [code for code in rows if code in GANG_POTIONS]
