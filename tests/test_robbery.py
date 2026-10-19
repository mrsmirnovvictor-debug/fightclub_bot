"""Ограбление: лицензии, нападение, вмешательство и добыча.

Порядок проверок — по тому, что дороже сломать:

1. **Деньги.** Десятая часть кармана и только она; счёт в банке цел, а
   добыча идёт одному напавшему.
2. **Лицензии.** Десять на бойца в неделю, и вмешательство стоит той же
   лицензии, что и нападение.
3. **Кого нельзя.** Своих, не в сети, уже дерущегося и недавно
   ограбленного.
4. **Вмешательство.** Только за свою семью и только на месте.
5. **Недельная цель.** Закрыли — премия в сейф, и ровно один раз.
"""

from __future__ import annotations

import random

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.clan_service import found, _join
from bot.config import Config
from bot.database import Database
from bot.game.battle import BLUE, RED
from bot.game.classes import Stats
from bot.game.clans import GARAGE_SLOTS
from bot.game.health import now_ts
from bot.game.robbery import (
    GOAL_REWARD,
    ATTACKS_PER_LICENCE,
    LICENCE_PRICE,
    MAX_SIDE,
    SAFE_SECONDS,
    WEEKLY_GOAL,
    goal_done,
    goal_left,
    attacks_in,
    loot_of,
    safe_left,
)
from bot.game.work import week_of
from bot.models import Player
from bot.robbery_service import RobberyError, RobberyService
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

NOW = 1_800_000_000
MANSION = "mafia_mansion"
BAR = "bar"


def headers(user_id: int = 1) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as one:
        yield one


def make_player(
    user_id: int = 1, nickname: str = "Боец", level: int = 10,
    clazz: str = "warrior", credits: int = 5_000, where: str = MANSION,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code=clazz, level=level,
        credits=credits, location=where, seen_at=NOW,
        **Stats(strength=25, agility=25, intuition=25, endurance=25).as_dict(),
    )


async def family(
    db: Database, size: int = 3, first: int = 1,
    name: str = "Коза Ностра", sign: str = "black_ravens",
    where: str = MANSION,
):
    """Семья: дон и бойцы, все в одном доме."""
    don = make_player(first, f"Дон-{first}", where=where)
    await db.save_player(don)
    clan = await found(db, don, name, sign, now=NOW)
    squad = [don.user_id]
    for number in range(first + 1, first + size):
        one = make_player(number, f"Боец-{number}", clazz="assassin", where=where)
        await db.save_player(one)
        await _join(db, one, clan, NOW)
        squad.append(number)
    return await db.get_player(don.user_id), squad, clan


async def licensed(
    db: Database, user_ids: list[int], count: int = 3, when: int | None = None
) -> None:
    """Выдать бойцам право на столько нападений, минуя кассу.

    Тест не про покупку, а про то, что ею оплачено: `count` — это
    нападения, а не пачки. `when` тут больше ни на что не влияет —
    срока у лицензии нет, — и остался ради прежних вызовов.
    """
    for one in user_ids:
        await db.buy_licences(one, 0, count)


async def stand(
    db: Database, user_ids: list[int], code: str, when: int = NOW
) -> None:
    """Поставить бойцов в один дом и отметить их в сети на этот час."""
    for one in user_ids:
        player = await db.get_player(one)
        assert player is not None
        player.location = code
        player.travel_to = None
        player.arrives_at = 0
        player.seen_at = when
        await db.save_player(player)


async def fight_out(service: RobberyService, raid, rounds: int = 200) -> None:
    """Добить драку до развязки: кто кого — неважно."""
    for _ in range(rounds):
        if raid.finished:
            return
        for one in list(raid.paired):
            if raid.finished or not raid.fighters[one].alive:
                continue
            await service.choose(raid.id, one, "attack", "head")
            if raid.finished:
                return
            await service.choose(raid.id, one, "block", "belly")
    raise AssertionError("драка не кончилась")


# ---------- правила ----------


def test_the_loot_is_a_tenth_of_the_pocket():
    """Берут десятую часть наличных, и мелочь не трогают вовсе."""
    assert loot_of(1_000) == 100
    assert loot_of(5_555) == 555
    assert loot_of(9) == 0
    assert loot_of(0) == 0
    assert loot_of(-50) == 0


def test_one_licence_is_a_pack_of_attacks():
    """Лицензия — пачка на двадцать пять нападений, и срока у неё нет."""
    assert ATTACKS_PER_LICENCE == 25
    assert LICENCE_PRICE == 100
    assert attacks_in(1) == ATTACKS_PER_LICENCE
    assert attacks_in(3) == 3 * ATTACKS_PER_LICENCE
    assert attacks_in(0) == 0 and attacks_in(-2) == 0


def test_the_robbed_are_left_alone_for_six_hours():
    """Покой ограбленного: шесть часов, кто бы ни победил."""
    assert safe_left(0, NOW) == 0
    assert safe_left(NOW, NOW) == SAFE_SECONDS
    assert safe_left(NOW - SAFE_SECONDS, NOW) == 0
    assert safe_left(NOW - SAFE_SECONDS // 2, NOW) == SAFE_SECONDS // 2


def test_the_weekly_goal_counts_up_to_the_premium():
    """Недельная цель: сколько осталось и когда она закрыта."""
    assert goal_left(0) == WEEKLY_GOAL
    assert goal_left(WEEKLY_GOAL - 1) == 1
    assert goal_left(WEEKLY_GOAL + 3) == 0
    assert goal_done(WEEKLY_GOAL) is True
    assert goal_done(WEEKLY_GOAL - 1) is False


# ---------- лицензии ----------


async def test_the_licence_is_sold_in_the_mansion_to_family_only(db):
    """Лицензию выписывают своим и только в особняке."""
    service = RobberyService(db)
    loner = make_player(9, "Одиночка")
    await db.save_player(loner)
    with pytest.raises(RobberyError, match="вступи в семью|семью"):
        await service.buy(loner, 1, now=NOW)

    don, squad, _ = await family(db)
    before = don.credits
    await service.buy(don, 2, now=NOW)
    mine = await service.licences(don.user_id, NOW)
    assert mine["left"] == 2 * ATTACKS_PER_LICENCE
    assert mine["bought"] == 2 and mine["pack"] == ATTACKS_PER_LICENCE
    # Деньги списаны из кармана бойца, а не из сейфа семьи
    fresh = await db.get_player(don.user_id)
    assert fresh.credits == before - LICENCE_PRICE * 2

    # А из бара за лицензией не ходят
    await stand(db, [don.user_id], BAR)
    away = await db.get_player(don.user_id)
    with pytest.raises(RobberyError, match="особняк"):
        await service.buy(away, 1, now=NOW)


async def test_the_licence_does_not_burn_out_with_the_week(db):
    """Срока у лицензии нет: купленное лежит, пока не потратят.

    Прежнее правило выдавало десять в неделю и сжигало остаток в
    понедельник. Теперь неделя лицензии не касается вовсе — её касается
    только нападение.
    """
    service = RobberyService(db)
    don, _, _ = await family(db)
    don.credits = 100_000
    await db.save_player(don)

    await service.buy(don, 1, now=NOW)
    later = NOW + 8 * 24 * 3600
    assert (await service.licences(don.user_id, later))["left"] == (
        ATTACKS_PER_LICENCE
    )
    # И покупать можно сколько угодно: потолок тут цена
    await service.buy(don, 5, now=later)
    assert (await service.licences(don.user_id, later))["left"] == (
        6 * ATTACKS_PER_LICENCE
    )


async def test_an_empty_purse_buys_nothing(db):
    """Лицензия стоит денег, и без них её не выписывают."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    don.credits = LICENCE_PRICE - 1
    await db.save_player(don)
    with pytest.raises(RobberyError, match="Не хватает"):
        await service.buy(don, 1, now=NOW)


# ---------- нападение ----------


async def test_the_robbery_takes_a_tenth_and_leaves_the_card_alone(db):
    """Удачное ограбление: десятая часть кармана напавшему, счёт цел."""
    service = RobberyService(db, rng=random.Random(7))
    don, squad, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1, credits=2_000, where=MANSION)
    victim.strength = victim.agility = 5
    victim.account_balance = 10_000
    victim.account_number = "4000 0000 0000 0001"
    await db.save_player(victim)
    await licensed(db, [don.user_id])
    purse = don.credits

    raid = await service.rob(don, victim.user_id, now=NOW)
    assert raid.place == MANSION
    assert raid.sides[don.user_id] == RED and raid.sides[victim.user_id] == BLUE
    await fight_out(service, raid)

    robbed = await db.get_player(victim.user_id)
    robber = await db.get_player(don.user_id)
    if raid.won:
        assert raid.loot == loot_of(2_000)
        assert robbed.credits == 2_000 - raid.loot
        assert robber.credits == purse + raid.loot
    else:
        assert raid.loot == 0
        assert robbed.credits == 2_000
    # На карте деньги не трогают ни при каком исходе
    assert robbed.account_balance == 10_000
    # И покой ограбленного встал, кто бы ни победил
    assert await db.robbed_at(victim.user_id) > 0


async def test_the_robbery_spends_a_licence(db):
    """Нападение стоит лицензии, и без неё его нет."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)

    with pytest.raises(RobberyError, match="Нет лицензии"):
        await service.rob(don, victim.user_id, now=NOW)

    await licensed(db, [don.user_id], 1)
    await service.rob(don, victim.user_id, now=NOW)
    assert (await service.licences(don.user_id, NOW))["left"] == 0


async def test_the_family_is_not_robbed(db):
    """Своих не трогают: соклановец в списке целей не стоит."""
    service = RobberyService(db)
    don, squad, _ = await family(db)
    await licensed(db, [don.user_id])
    with pytest.raises(RobberyError, match="Своих"):
        await service.rob(don, squad[1], now=NOW)


async def test_the_offline_and_the_distant_are_not_robbed(db):
    """Грабят того, кто в сети и стоит рядом."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    await licensed(db, [don.user_id])

    sleeper = make_player(50, "Спящий")
    sleeper.seen_at = NOW - 3600
    await db.save_player(sleeper)
    with pytest.raises(RobberyError, match="не в сети|нет в сети"):
        await service.rob(don, sleeper.user_id, now=NOW)

    far = make_player(51, "Дальний", where=BAR)
    await db.save_player(far)
    with pytest.raises(RobberyError, match="тут уже нет"):
        await service.rob(don, far.user_id, now=NOW)


async def test_the_robbed_rest_before_the_next_one(db):
    """Ограбленного шесть часов не трогают — ни тот же, ни другой."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    await licensed(db, [don.user_id], 2)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    await db.mark_robbed(victim.user_id, NOW)

    with pytest.raises(RobberyError, match="уже грабили"):
        await service.rob(don, victim.user_id, now=NOW + 60)
    # А через шесть часов — можно, если жертва снова в сети
    later = NOW + SAFE_SECONDS + 60
    await stand(db, [victim.user_id], MANSION, when=later)
    fresh = await db.get_player(don.user_id)
    raid = await service.rob(fresh, victim.user_id, now=later)
    assert raid.victim == victim.user_id


# ---------- вмешательство ----------


async def test_the_family_steps_in_on_its_own_side(db):
    """Вмешиваются за свою семью, и это стоит лицензии."""
    service = RobberyService(db)
    don, squad, _ = await family(db)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    await licensed(db, [don.user_id, squad[1]], 1)

    raid = await service.rob(don, victim.user_id, now=NOW)
    mate = await db.get_player(squad[1])
    await service.join(raid.id, mate, now=NOW)
    assert raid.sides[mate.user_id] == RED
    assert (await service.licences(mate.user_id, NOW))["left"] == 0
    assert service.is_busy(mate.user_id) is True


async def test_a_stranger_does_not_step_in(db):
    """Чужая семья в драку не лезет: ни одна сторона ей не своя."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    rival, rivals, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    await licensed(db, [don.user_id, rival.user_id], 1)

    raid = await service.rob(don, victim.user_id, now=NOW)
    with pytest.raises(RobberyError, match="не твоя драка"):
        await service.join(raid.id, rival, now=NOW)


async def test_the_victims_family_steps_in_too(db):
    """У жертвы есть семья — она и вступается: сторон в драке две."""
    service = RobberyService(db)
    don, _, _ = await family(db)
    theirs, their_squad, _ = await family(
        db, first=20, name="Бешеные псы", sign="silver_wolves"
    )
    await licensed(db, [don.user_id, their_squad[1]], 1)

    raid = await service.rob(don, theirs.user_id, now=NOW)
    mate = await db.get_player(their_squad[1])
    await service.join(raid.id, mate, now=NOW)
    assert raid.sides[mate.user_id] == BLUE


async def test_the_side_does_not_take_more_than_five(db):
    """На сторону лезут впятером, не больше."""
    service = RobberyService(db)
    don, squad, _ = await family(db, size=MAX_SIDE + 2)
    victim = make_player(60, "Прохожий")
    await db.save_player(victim)
    await licensed(db, squad, 1)

    raid = await service.rob(don, victim.user_id, now=NOW)
    for one in squad[1:MAX_SIDE]:
        await service.join(raid.id, await db.get_player(one), now=NOW)
    assert len(raid.side_ids(RED)) == MAX_SIDE

    extra = await db.get_player(squad[MAX_SIDE])
    with pytest.raises(RobberyError, match="мест нет"):
        await service.join(raid.id, extra, now=NOW)


async def test_the_brawl_pays_experience_like_a_ring_bout(db):
    """Опыт за драку — тот же, что за бой на ринге. Рейтинг не трогаем."""
    service = RobberyService(db, rng=random.Random(4))
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1, credits=1_000)
    victim.strength = victim.agility = 5
    await db.save_player(victim)
    await licensed(db, [don.user_id], 1)
    before = await db.get_player(don.user_id)

    raid = await service.rob(before, victim.user_id, now=NOW)
    await fight_out(service, raid)

    assert raid.won, "первого уровня против десятого не выстоять"
    after = await db.get_player(don.user_id)
    assert after.total_exp > before.total_exp, "за драку дают опыт"
    assert after.rating == before.rating, "рейтинг — лестница ринга"
    # Проигравшему — столько же, сколько на ринге, то есть ничего:
    # `LOSS_EXP_SHARE` в клубе нулевая, и здесь она та же
    beaten = await db.get_player(victim.user_id)
    assert beaten.total_exp == 0


async def test_the_helper_gets_nothing(db):
    """Добыча идёт одному напавшему: помощники не получают ничего."""
    service = RobberyService(db, rng=random.Random(3))
    don, squad, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1, credits=3_000)
    victim.strength = victim.agility = 5
    await db.save_player(victim)
    await licensed(db, [don.user_id, squad[1]], 1)

    raid = await service.rob(don, victim.user_id, now=NOW)
    mate = await db.get_player(squad[1])
    before = mate.credits
    await service.join(raid.id, mate, now=NOW)
    await fight_out(service, raid)

    helper = await db.get_player(squad[1])
    assert helper.credits == before, "помощнику не досталось ни кредита"


# ---------- недельная цель ----------


async def test_the_weekly_goal_pays_the_vault_once(db):
    """Цель закрыта — премия в сейф, и ровно один раз."""
    service = RobberyService(db)
    don, _, clan = await family(db)
    week = week_of(NOW)
    for _ in range(WEEKLY_GOAL - 1):
        await db.add_clan_attack(clan.id, week)
    attacks, paid = await db.clan_week(clan.id, week)
    assert attacks == WEEKLY_GOAL - 1 and paid == 0

    raid = type("Fake", (), {"bonus": 0})()
    await service._count_for_clan(raid, clan.id, NOW)
    assert raid.bonus == GOAL_REWARD
    fresh = await db.clan_by_id(clan.id)
    assert fresh.vault == GOAL_REWARD

    # Следующее нападение той же недели премии не повторяет
    again = type("Fake", (), {"bonus": 0})()
    await service._count_for_clan(again, clan.id, NOW)
    assert again.bonus == 0
    fresh = await db.clan_by_id(clan.id)
    assert fresh.vault == GOAL_REWARD


# ---------- ручки и экран ----------
#
# Ручки идут по настоящим часам сервера, а не по `NOW`: лицензии ложатся
# в ту неделю, которая сейчас, и «в сети» считается от этого же часа.
# Поэтому у проверок ручек свой заход — `awake`.


async def post(client, user_id: int, **body):
    return await client.post("/api/crowd", json=body, headers=headers(user_id))


async def awake(db: Database, user_ids: list[int], licences: int = 2) -> None:
    """Отметить бойцов в сети сейчас и выписать им лицензии на эту неделю."""
    moment = now_ts()
    for one in user_ids:
        player = await db.get_player(one)
        assert player is not None
        player.seen_at = moment
        await db.save_player(player)
    if licences:
        await licensed(db, user_ids, licences, when=moment)


async def test_the_crowd_shows_who_is_here(db, client):
    """Экран дома: кто рядом, кто в сети и кого можно взять."""
    don, squad, _ = await family(db)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    sleeper = make_player(51, "Спящий")
    await db.save_player(sleeper)
    await awake(db, [don.user_id, squad[1], victim.user_id])
    # Спящего в сети нет: последний раз его видели сутки назад
    sleeper.seen_at = now_ts() - 86_400
    await db.save_player(sleeper)

    body = await (await client.get(
        "/api/crowd", headers=headers(don.user_id)
    )).json()
    assert body["place"] == MANSION
    rows = {one["name"]: one for one in body["crowd"]}
    # Соклановцы, прохожий и спящий — все здесь
    assert "Прохожий" in rows and "Спящий" in rows and "Боец-2" in rows
    assert rows["Прохожий"]["can_rob"] is True
    assert rows["Боец-2"]["same_clan"] is True
    assert rows["Боец-2"]["can_rob"] is False
    assert rows["Спящий"]["can_rob"] is False
    assert "не в сети" in rows["Спящий"]["why"].lower()
    # В сети — выше: с ними что-то можно сделать сейчас
    assert body["crowd"][-1]["name"] == "Спящий"
    assert body["licences"]["left"] == 2


async def test_the_handler_robs_and_shows_the_fight(db, client):
    """Нажал «Ограбить» — драка идёт и видна всем, кто тут стоит."""
    don, squad, _ = await family(db)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    await awake(db, [don.user_id, squad[1], victim.user_id], licences=1)

    answer = await post(client, don.user_id, action="rob", user=victim.user_id)
    assert answer.status == 200
    body = await answer.json()
    fight = body["fight"]
    assert fight is not None and fight["round"] == 1
    assert fight["yours"] is True and fight["side"] == RED
    assert len(fight["red"]) == 1 and len(fight["blue"]) == 1

    # Соклановец видит драку в списке дома и может вмешаться
    body = await (await client.get(
        "/api/crowd", headers=headers(squad[1])
    )).json()
    assert len(body["fights"]) == 1
    row = body["fights"][0]
    assert row["can_join"] is True and row["side"] == RED
    answer = await post(client, squad[1], action="join", raid_id=row["id"])
    assert answer.status == 200
    assert (await answer.json())["fight"]["side"] == RED


async def test_the_road_is_shut_while_the_fists_fly(db, client):
    """Из драки не уходят: дорога спрашивает ограбление, и оно не пускает."""
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий")
    await db.save_player(victim)
    await awake(db, [don.user_id, victim.user_id], licences=1)
    await post(client, don.user_id, action="rob", user=victim.user_id)

    answer = await client.post(
        "/api/travel", json={"to": BAR}, headers=headers(victim.user_id)
    )
    assert answer.status == 409
    assert "бой" in (await answer.json())["error"]


async def test_the_licence_is_bought_on_the_family_page(db, client):
    """Лицензию берут в особняке, на вкладке имущества семьи."""
    don, _, clan = await family(db)

    answer = await client.post(
        "/api/mafia",
        json={"action": "licence", "count": 2, "open": clan.id},
        headers=headers(don.user_id),
    )
    assert answer.status == 200
    body = await answer.json()
    assert "Лицензий выписано: 2" in body["said"]
    assert "Нападений на руках: 50" in body["said"]
    shop = body["opened"]["estate"]["robbery"]
    assert shop["left"] == 2 * ATTACKS_PER_LICENCE and shop["bought"] == 2
    assert shop["pack"] == ATTACKS_PER_LICENCE
    assert shop["goal"] == WEEKLY_GOAL and shop["reward"] == GOAL_REWARD


async def test_the_garage_holds_three_places(db, client):
    """Гараж семьи: три места, и пока они пустые."""
    don, _, clan = await family(db)

    body = await (await client.get(
        f"/api/mafia?clan={clan.id}", headers=headers(don.user_id)
    )).json()
    garage = body["opened"]["estate"]["garage"]
    assert garage["slots"] == GARAGE_SLOTS == 3
    assert garage["free"] == GARAGE_SLOTS and garage["cars"] == []
    assert garage["soon"] is True


# ---------- драка обязана кончиться ----------


async def test_a_broken_ending_still_lets_the_fighters_out(db, monkeypatch):
    """Развязка сорвалась — драка всё равно кончена, и бойцы на воле.

    Висящая драка хуже любой ошибки в ней: из неё не выйти, по городу
    не пройти, и чинится это только перезапуском бота. Поэтому
    развязка отгорожена, а бойцов отпускают в любом случае.
    """
    service = RobberyService(db, rng=random.Random(5))
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1)
    await db.save_player(victim)
    await licensed(db, [don.user_id])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("касса сломалась")

    monkeypatch.setattr(service, "_finish_robbery", boom)
    raid = await service.rob(don, victim.user_id, now=NOW)
    await fight_out(service, raid)

    assert raid.finished and raid.summary
    for one in (don.user_id, victim.user_id):
        assert service.of_user(one) is None
        assert service.result_of(one) is raid


async def test_a_broken_round_still_lets_the_fighters_out(db, monkeypatch):
    """Сорвался сам размен — судья разводит всех по углам."""
    service = RobberyService(db, rng=random.Random(5))
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1)
    await db.save_player(victim)
    await licensed(db, [don.user_id])
    raid = await service.rob(don, victim.user_id, now=NOW)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("размен сломался")

    monkeypatch.setattr(service, "_round", boom)
    await service.choose(raid.id, don.user_id, "attack", "head")
    await service.choose(raid.id, don.user_id, "block", "belly")
    await service.choose(raid.id, victim.user_id, "attack", "head")
    await service.choose(raid.id, victim.user_id, "block", "belly")

    assert raid.finished
    assert "оборвалась" in raid.summary[0]
    for one in (don.user_id, victim.user_id):
        assert service.of_user(one) is None
        assert service.result_of(one) is raid


async def test_the_done_button_is_the_way_out_of_a_hung_fight(db):
    """«Понятно» обязана работать всегда — даже у сломанной драки.

    Драка, которая кончилась, но не развела бойцов, держала их у себя
    насовсем: ни уйти из дома, ни ударить. Эта кнопка — последний выход
    из неё, не требующий перезапуска бота.
    """
    service = RobberyService(db, rng=random.Random(5))
    don, _, _ = await family(db)
    victim = make_player(50, "Прохожий", level=1)
    await db.save_player(victim)
    await licensed(db, [don.user_id])
    raid = await service.rob(don, victim.user_id, now=NOW)
    # Так выглядит зависшая драка: кончена, а бойцы всё ещё в ней
    raid.finished = True

    service.forget_result(don.user_id)

    assert service.of_user(don.user_id) is None
    assert service.at(MANSION) == []
    # Каждый выходит сам, своей кнопкой: пока ограбленный её не нажал,
    # он всё ещё занят, и второй раз его не возьмут
    with pytest.raises(RobberyError, match="держат"):
        await service.rob(await db.get_player(don.user_id), 50, now=NOW)
    service.forget_result(victim.user_id)
    # Живую драку эта кнопка не разводит: из неё выходят ногами вперёд
    second = await service.rob(await db.get_player(don.user_id), 50, now=NOW)
    service.forget_result(don.user_id)
    assert service.of_user(don.user_id) is second
