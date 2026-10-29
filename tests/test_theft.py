"""Номера, угон, штрафы и всё, что из них следует.

Порядок проверок — по тому, что дороже сломать:

1. **Номер.** У каждой машины свой знак по маске а000аа, и второго
   такого в городе нет. Без номера нельзя ни заявить об угоне, ни
   проверить машину.
2. **Руль без прав.** За руль пускают всякого — но патруль за это
   штрафует, и штраф дороже самих прав.
3. **Угон.** Уводят только надетую машину и только мафия. Стоянка —
   безопасное место: оттуда не уводят нигде.
4. **Заявление и розыск.** Пока хозяин не заявил, город в это дело не
   лезет. Заявил — машину изымают у кого угодно и возвращают ему.
5. **Перебитый номер.** Новый знак стирает прошлое, и это единственный
   способ снять розыск.
"""

from __future__ import annotations

import pytest

from bot.car_service import (
    CarError,
    buy,
    leave,
    mine,
    restamp,
    steal,
    sweep,
    wheel,
)
from bot.database import Database
from bot.game.cars import (
    IN_FLAT,
    IN_POLICE,
    PATROL_CAR,
    RESTAMP_PRICE,
    STEAL_WINDOW,
)
from bot.game.classes import Stats
from bot.game.clans import Clan, Rank, Standing
from bot.game.driving import School
from bot.game.plates import DIGITS, LETTERS, MASK, is_plate
from bot.game.police import FINE_NO_LICENCE
from bot.models import Player
from bot.police_service import (
    PoliceError,
    claim_car,
    deliver,
    file_theft,
    fine_driver,
    found_cars,
    take_from_thief,
    theft_suspects,
    traffic_sheet,
)

NOW = 1_800_000_000
HATCH = "street_compact"
SEDAN = "city_sedan"
STATION = "vcpd"


def make_player(
    user_id: int = 1, nickname: str = "Тайлер", where: str = "bar",
    credits: int = 50_000, seen: int = NOW,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=where, seen_at=seen,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


async def licensed(db: Database, player: Player) -> Player:
    await db.save_player(player)
    await db.set_school(player.user_id, School(passed_at=NOW, licence_at=NOW))
    fresh = await db.get_player(player.user_id)
    assert fresh is not None
    return fresh


async def mafioso(db: Database, player: Player) -> Player:
    """Боец в семье: угоном в городе занимается она."""
    player.clan = Standing(
        clan=Clan(
            id=1, name="Коза Ностра", don=player.user_id, created_at=NOW,
            sign="black_ravens", members=1,
        ),
        rank=Rank.DON,
    )
    await db.save_player(player)
    return player


async def with_car(
    db: Database, player: Player, code: str = HATCH, drive: bool = True
) -> dict:
    """Машина у бойца, и он на ней едет. Отдаёт строку машины."""
    await buy(db, player, code, NOW)
    if drive:
        await wheel(db, player, code, NOW)
    row = await db.car_row(player.user_id, code)
    assert row is not None
    return row


async def officer(db: Database, user_id: int = 9) -> Player:
    """Патрульный: значок решают рабочие места, а не список."""
    cop = make_player(user_id, "Патруль", where=STATION)
    cop.job_code = "police_patrol"
    await db.save_player(cop)
    return cop


# ---------- номер ----------


def test_the_plate_follows_the_city_mask():
    """Буква, три цифры, две буквы — и все из городской дюжины."""
    from bot.game.plates import random_plate

    assert MASK == "а000аа"
    assert len(LETTERS) == 12
    for _ in range(50):
        plate = random_plate()
        assert is_plate(plate), plate
        assert plate[0] in LETTERS and plate[4:] and plate[4] in LETTERS
        assert all(one in DIGITS for one in plate[1:4])
    # Чужое за номер не сходит
    assert not is_plate("АБ123В") and not is_plate("A123BC!")
    assert not is_plate("") and not is_plate("А12ВС")


async def test_every_car_gets_its_own_plate(db):
    """Номер выдают при покупке, и второго такого в городе нет."""
    first = await stand(db, make_player(1, "Первый", where="bar"))
    second = await stand(db, make_player(2, "Второй"))
    third = await stand(db, make_player(3, "Третий"))

    # Машина у бойца одна, поэтому три машины — это трое бойцов
    await buy(db, first, HATCH, NOW)
    await buy(db, second, SEDAN, NOW)
    await buy(db, third, HATCH, NOW)

    plates = [
        str(row["plate"])
        for one in (1, 2, 3)
        for row in await db.cars_of(one)
    ]
    assert len(plates) == 3
    assert all(is_plate(one) for one in plates), plates
    assert len(set(plates)) == 3, "два одинаковых знака в городе"


async def test_the_old_car_gets_a_plate_at_the_first_look(db):
    """Машина, записанная до номеров, получает знак, когда на неё смотрят."""
    player = await stand(db, make_player())
    await buy(db, player, HATCH, NOW)
    row = await db.car_row(player.user_id, HATCH)
    await db.conn.execute("UPDATE cars SET plate = '' WHERE id = ?", (row["id"],))
    await db.conn.commit()

    await sweep(db, NOW)

    fresh = await db.car_row(player.user_id, HATCH)
    assert is_plate(str(fresh["plate"]))


# ---------- руль без прав ----------


async def test_the_wheel_turns_without_a_licence(db):
    """За руль пускают и без прав: это нарушение, а не запрет."""
    player = await stand(db, make_player())
    await buy(db, player, HATCH, NOW)

    said = await wheel(db, player, HATCH, NOW)

    assert player.driving(NOW) == HATCH
    assert "Прав у тебя нет" in said


async def test_the_patrol_fines_the_driver_without_a_licence(db):
    """Штраф выписывает полиция, деньги идут в копилку участка."""
    driver = await stand(db, make_player(credits=50_000))
    row = await with_car(db, driver)
    before = (await db.get_player(driver.user_id)).credits
    cop = await officer(db)

    said = await fine_driver(db, cop, int(row["id"]), NOW)

    fresh = await db.get_player(driver.user_id)
    assert str(FINE_NO_LICENCE) in said
    assert fresh.credits == before - FINE_NO_LICENCE
    assert await db.service_vault("police") == FINE_NO_LICENCE
    # Из-под нарушителя машину не забирают — только ключи зажигания
    assert fresh.driving(NOW) == "" and fresh.car == ""
    assert [one["code"] for one in await mine(db, fresh, NOW)] == [HATCH]


async def test_the_licensed_driver_is_not_fined(db):
    """С правами штрафовать не за что, и своего не штрафуют тоже."""
    driver = await licensed(db, make_player(credits=5_000))
    row = await with_car(db, driver)
    cop = await officer(db)

    with pytest.raises(PoliceError, match="Права у него есть"):
        await fine_driver(db, cop, int(row["id"]), NOW)

    # И штраф — дело полиции: гражданский никого не штрафует
    stranger = await stand(db, make_player(7, "Прохожий"))
    with pytest.raises(PoliceError, match="выписывает полиция"):
        await fine_driver(db, stranger, int(row["id"]), NOW)


async def test_the_empty_pocket_pays_what_it_has(db):
    """Пустой карман не повод отпускать, но и долгов город не пишет."""
    driver = await stand(db, make_player())
    row = await with_car(db, driver)
    # Машину купили на деньги, а к проверке карман почти пуст
    driver = await db.get_player(driver.user_id)
    driver.credits = 100
    await db.save_player(driver)
    cop = await officer(db)

    await fine_driver(db, cop, int(row["id"]), NOW)

    fresh = await db.get_player(driver.user_id)
    assert fresh.credits == 0 and fresh.account_balance == 0
    assert await db.service_vault("police") == 100


# ---------- угон ----------


async def test_only_the_mafia_steals(db):
    """Угоном в городе занимается семья, а не кто придётся."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    loner = await stand(db, make_player(2, "Одиночка"))

    with pytest.raises(CarError, match="мафия"):
        await steal(db, loner, int(row["id"]), NOW)


async def test_the_car_left_on_the_owner_is_stolen(db):
    """Хозяин вышел из игры, а машину оставил на себе — её уводят."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор"))

    said = await steal(db, thief, int(row["id"]), NOW)

    assert str(row["plate"]) in said
    assert [one["code"] for one in await db.cars_of(2)] == [HATCH]
    assert await db.cars_of(1) == []
    # Из-под хозяина машину увели вместе с ключами
    left = await db.get_player(1)
    assert left.car == "" and left.driving(NOW) == ""
    # Номер у неё прежний, а в учёте она числится угнанной
    fresh = await db.car_row(2, HATCH)
    assert str(fresh["plate"]) == str(row["plate"])
    assert int(fresh["stolen_from"]) == 1 and not fresh["stolen_filed"]


async def test_the_parked_car_is_safe_everywhere(db):
    """Стоянка — безопасное место: ни у квартиры, ни у участка не уводят."""
    thief = await mafioso(db, make_player(2, "Вор"))
    owner = await stand(db, make_player(1, "Жертва", where=STATION, seen=0))
    row = await with_car(db, owner)

    await leave(db, owner, HATCH, IN_POLICE, 0, NOW)

    with pytest.raises(CarError, match="охраняемом месте"):
        await steal(db, thief, int(row["id"]), NOW)

    # И то же самое у квартиры: место разное, правило одно
    await db.unpark_car(owner.user_id, HATCH)
    await wheel(db, owner, HATCH, NOW)
    await leave(db, owner, HATCH, IN_FLAT, 1, NOW)
    with pytest.raises(CarError, match="охраняемом месте"):
        await steal(db, thief, int(row["id"]), NOW)


async def test_the_owner_in_the_city_must_be_beaten_first(db):
    """Хозяин в сети — сперва нападение: победа и есть выбор «угнать»."""
    owner = await stand(db, make_player(1, "Жертва", seen=NOW))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор"))

    with pytest.raises(CarError, match="Сперва нападение"):
        await steal(db, thief, int(row["id"]), NOW)

    # Выигранное нападение открывает ключи — и ненадолго
    await db.open_case(1, 2, "bar", 100, 1, NOW - STEAL_WINDOW - 60)
    with pytest.raises(CarError, match="Сперва нападение"):
        await steal(db, thief, int(row["id"]), NOW)

    await db.open_case(1, 2, "bar", 100, 1, NOW - 60)
    await steal(db, thief, int(row["id"]), NOW)
    assert [one["code"] for one in await db.cars_of(2)] == [HATCH]


async def test_the_car_across_the_city_is_out_of_reach(db):
    """Угоняют то, что стоит рядом, а не то, что нашлось в учёте."""
    owner = await stand(db, make_player(1, "Жертва", where="casino", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор", where="bar"))

    with pytest.raises(CarError, match="не здесь"):
        await steal(db, thief, int(row["id"]), NOW)


async def test_a_car_at_hand_is_not_a_car_under_the_owner(db):
    """Не надета — уводить нечего: ключи в кармане, а не в замке."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner, drive=False)
    thief = await mafioso(db, make_player(2, "Вор"))

    with pytest.raises(CarError, match="не под хозяином"):
        await steal(db, thief, int(row["id"]), NOW)


# ---------- заявление, розыск и изъятие ----------


async def test_the_theft_is_reported_at_the_station(db):
    """Заявление пишут в участке, и с него начинается розыск."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор"))
    await steal(db, thief, int(row["id"]), NOW)

    fresh = await db.get_player(1)
    with pytest.raises(PoliceError, match="в участке"):
        await file_theft(db, fresh, int(row["id"]), NOW)

    fresh.location = STATION
    await db.save_player(fresh)
    said = await file_theft(db, fresh, int(row["id"]), NOW)

    assert str(row["plate"]) in said
    stolen = await db.stolen_from(1, NOW)
    assert len(stolen) == 1 and stolen[0]["stolen_filed"]
    # Второй раз заявление не принимают
    with pytest.raises(PoliceError, match="уже написано"):
        await file_theft(db, fresh, int(row["id"]), NOW)


async def test_the_traffic_sheet_shows_the_city_and_its_sins(db):
    """Автоучёт: номер, хозяин, права и розыск — всё в одной строке."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор", where="bar"))
    await steal(db, thief, int(row["id"]), NOW)
    fresh = await db.get_player(1)
    fresh.location = STATION
    await db.save_player(fresh)
    await file_theft(db, fresh, int(row["id"]), NOW)
    cop = await officer(db)

    sheet = await traffic_sheet(db, cop, NOW)

    assert len(sheet) == 1
    line = sheet[0]
    assert line["plate"] == str(row["plate"]) and line["owner"] == "Вор"
    assert line["stolen"] and line["wanted"] and not line["licence"]
    assert line["place"] == "bar", "в учёте видно, где машина"
    # Гражданскому автоучёт не отдают вовсе
    assert await traffic_sheet(db, await db.get_player(2), NOW) == []


async def test_the_sleeping_thief_loses_the_car_without_a_fight(db):
    """Угонщика нет в сети — машину забирают молча и везут в участок."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор", seen=0))
    await steal(db, thief, int(row["id"]), NOW)
    cop = await officer(db)

    # Без заявления город в это дело не лезет
    with pytest.raises(PoliceError, match="не в розыске"):
        await take_from_thief(db, cop, int(row["id"]), NOW)

    victim = await db.get_player(1)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)

    # До машины надо доехать: она там, где угонщик
    with pytest.raises(PoliceError, match="не здесь"):
        await take_from_thief(db, cop, int(row["id"]), NOW)

    cop.location = "bar"
    await db.save_player(cop)
    said = await take_from_thief(db, cop, int(row["id"]), NOW)

    assert "в участок" in said
    assert await db.cars_of(2) == [], "у вора машины больше нет"
    kept = await db.car_row(1, HATCH)
    assert kept is not None and kept["parked"] == "impound"
    assert str(kept["at_place"]) == STATION


async def test_the_owner_takes_the_car_from_the_station(db):
    """Машина лежит в участке до востребования, и берёт её хозяин."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор", seen=0))
    await steal(db, thief, int(row["id"]), NOW)
    victim = await db.get_player(1)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)
    cop = await officer(db)
    cop.location = "bar"
    await db.save_player(cop)
    await take_from_thief(db, cop, int(row["id"]), NOW)

    # Чужую из участка не отдают
    stranger = await stand(db, make_player(5, "Прохожий", where=STATION))
    with pytest.raises(PoliceError, match="не твоя"):
        await claim_car(db, stranger, int(row["id"]), NOW)

    mine = await db.get_player(1)
    said = await claim_car(db, mine, int(row["id"]), NOW)

    assert str(row["plate"]) in said
    kept = await db.car_row(1, HATCH)
    assert not kept["parked"] and not kept["at_place"]


async def test_the_abandoned_car_is_driven_to_the_station(db):
    """Брошенную у здания машину полиция отвозит в участок."""
    from bot.car_service import leave_here

    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор", seen=0))
    await steal(db, thief, int(row["id"]), NOW)
    thief = await db.get_player(2)
    await leave_here(db, thief, HATCH, NOW)
    victim = await db.get_player(1)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)

    cop = await officer(db)
    sheet = await found_cars(db, cop, NOW)
    assert len(sheet) == 1 and sheet[0]["street"]
    assert sheet[0]["where"] == "bar", "участок говорит, где машина"
    assert not sheet[0]["can_deliver"], "до неё ещё надо доехать"

    cop.location = "bar"
    await db.save_player(cop)
    said = await deliver(db, cop, int(row["id"]), NOW)

    assert "в участок" in said
    kept = await db.car_row(1, HATCH)
    assert kept["parked"] == "impound" and str(kept["at_place"]) == STATION


# ---------- перебитый номер ----------


async def test_the_new_plate_wipes_the_past(db):
    """Перебитый номер снимает розыск — за деньги и только на мойке."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    # Угоняют там, где стоит хозяин, а перебивают — на мойке
    thief = await mafioso(db, make_player(2, "Вор"))
    await steal(db, thief, int(row["id"]), NOW)
    victim = await db.get_player(1)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)

    rich = await db.get_player(2)
    rich.location = "car_wash"
    await db.save_player(rich)
    before = rich.credits
    said = await restamp(db, rich, HATCH, NOW)

    fresh = await db.car_row(2, HATCH)
    assert str(fresh["plate"]) != str(row["plate"])
    assert is_plate(str(fresh["plate"])) and str(fresh["plate"]) in said
    assert not fresh["stolen_from"] and not fresh["stolen_filed"]
    assert (await db.get_player(2)).credits == before - RESTAMP_PRICE
    # И в розыске у хозяина её больше нет
    assert await db.stolen_from(1, NOW) == []


async def test_the_empty_pocket_keeps_the_old_plate(db):
    """Нечем платить — номер остаётся прежним."""
    player = await stand(db, make_player(where="car_wash"))
    row = await with_car(db, player)
    player.credits = 10
    await db.save_player(player)

    with pytest.raises(CarError, match="Стоит"):
        await restamp(db, player, HATCH, NOW)

    assert str((await db.car_row(1, HATCH))["plate"]) == str(row["plate"])


# ---------- машина у здания ----------


async def test_the_car_is_left_at_the_building_and_seen_by_all(db):
    """Оставленная машина стоит у здания, и видит её каждый, кто придёт."""
    from bot.car_service import here_rows, leave_here, pick_here

    owner = await stand(db, make_player(1, "Хозяин"))
    row = await with_car(db, owner)

    said = await leave_here(db, owner, HATCH, NOW)

    assert "у здания" in said
    fresh = await db.car_row(1, HATCH)
    assert fresh["parked"] == "street" and str(fresh["at_place"]) == "bar"
    assert (await db.get_player(1)).driving(NOW) == "", "за рулём её больше нет"

    # Видит её и чужой, кто сюда пришёл
    stranger = await stand(db, make_player(2, "Прохожий"))
    seen = await here_rows(db, stranger, NOW)
    assert [one["plate"] for one in seen] == [str(row["plate"])]
    assert seen[0]["mine"] is False and seen[0]["owner"] == "Хозяин"

    # Чужую не забрать, свою — пожалуйста
    with pytest.raises(CarError, match="чужая"):
        await pick_here(db, stranger, int(row["id"]), NOW)
    mine = await db.get_player(1)
    await pick_here(db, mine, int(row["id"]), NOW)
    assert not (await db.car_row(1, HATCH))["parked"]


async def test_the_street_is_not_a_parking(db):
    """Брошенную у здания уводят: улица — не охраняемое место."""
    from bot.car_service import leave_here

    owner = await stand(db, make_player(1, "Хозяин", seen=NOW))
    row = await with_car(db, owner)
    await leave_here(db, owner, HATCH, NOW)
    thief = await mafioso(db, make_player(2, "Вор"))

    # Хозяин в сети, а машина брошена: драться за неё не надо
    said = await steal(db, thief, int(row["id"]), NOW)

    assert str(row["plate"]) in said
    assert [one["code"] for one in await db.cars_of(2)] == [HATCH]


async def test_the_patrol_car_hides_nowhere(db):
    """Служебную угоняют, но ни спрятать, ни перебить её нельзя."""
    from bot.car_service import leave, leave_here, restamp, take
    from bot.game.cars import IN_FLAT

    cop = make_player(1, "Патруль", where="vcpd")
    cop.job_code = "police_patrol"
    cop.diplomas = ["police"]
    await db.save_player(cop)
    await take(db, cop, PATROL_CAR, NOW)

    with pytest.raises(CarError, match="гараже службы"):
        await leave(db, cop, PATROL_CAR, IN_FLAT, 1, NOW)
    with pytest.raises(CarError, match="городской"):
        await restamp(db, cop, PATROL_CAR, NOW)

    # А у здания — можно, и оттуда её уводят
    said = await leave_here(db, cop, PATROL_CAR, NOW)
    assert "у здания" in said
    row = await db.car_row(1, PATROL_CAR)
    thief = await mafioso(db, make_player(2, "Вор", where="vcpd"))
    await steal(db, thief, int(row["id"]), NOW)
    assert [one["code"] for one in await db.cars_of(2)] == [PATROL_CAR]


# ---------- платная стоянка ----------


async def test_the_station_parking_is_paid_once_and_frees_for_nothing(db):
    """Место у участка платное: берут при постановке, выезд даром."""
    from bot.car_service import pick_up
    from bot.game.cars import IN_POLICE, parking_fee
    from bot.police_service import park_at_station

    owner = await stand(db, make_player(1, "Хозяин"))
    await with_car(db, owner)
    fee = parking_fee(HATCH)
    assert fee == 15, "полдня аренды хэтчбека"

    rich = await db.get_player(1)
    rich.credits = 1_000
    rich.location = STATION
    await db.save_player(rich)
    owner = rich
    said = await park_at_station(db, owner, HATCH, NOW)

    assert str(fee) in said
    assert (await db.car_row(1, HATCH))["parked"] == IN_POLICE
    assert (await db.get_player(1)).credits == 1_000 - fee
    # Деньги города идут в копилку участка, как и штрафы
    assert await db.service_vault("police") == fee

    # Забрать своё — бесплатно: выезд оплачен въездом
    mine = await db.get_player(1)
    await pick_up(db, mine, HATCH, NOW)
    assert (await db.get_player(1)).credits == 1_000 - fee
    assert not (await db.car_row(1, HATCH))["parked"]


async def test_the_dearer_the_car_the_dearer_the_place(db):
    """Цена места считается от машины: платит тот, кому есть что терять."""
    from bot.game.cars import parking_fee

    assert parking_fee("city_sedan") == 25
    assert parking_fee("apex_hypercar") == 300
    # Служебную на стоянку не ставят, и цены у неё нет
    assert parking_fee(PATROL_CAR) == 0


async def test_the_empty_pocket_does_not_park(db):
    """Не хватило на место — машину не поставили и денег не взяли."""
    from bot.police_service import PoliceError, park_at_station

    owner = await stand(db, make_player(1, "Бедный"))
    await with_car(db, owner)
    broke = await db.get_player(1)
    broke.credits = 10
    broke.location = STATION
    await db.save_player(broke)

    with pytest.raises(PoliceError, match="Место стоит 15"):
        await park_at_station(db, broke, HATCH, NOW)

    assert not (await db.car_row(1, HATCH))["parked"]
    assert (await db.get_player(1)).credits == 10
    assert await db.service_vault("police") == 0

    # И машину на стоянку приводят: из бара её туда не поставить
    far = await db.get_player(1)
    far.credits = 1_000
    far.location = "bar"
    await db.save_player(far)
    with pytest.raises(PoliceError, match="приводят"):
        await park_at_station(db, far, HATCH, NOW)
    assert not (await db.car_row(1, HATCH))["parked"]


async def test_the_patrol_car_does_not_take_the_city_lot(db):
    """Служебную городская стоянка не берёт: ей место в гараже службы."""
    from bot.car_service import take
    from bot.police_service import PoliceError, park_at_station

    cop = make_player(1, "Патруль", where=STATION)
    cop.job_code = "police_patrol"
    cop.diplomas = ["police"]
    await db.save_player(cop)
    await take(db, cop, PATROL_CAR, NOW)

    with pytest.raises(PoliceError, match="гараже службы"):
        await park_at_station(db, cop, PATROL_CAR, NOW)


# ---------- патруль на месте ----------


async def test_the_street_row_carries_the_buttons_of_the_place(db):
    """Кнопки живут у здания: забрать своё, угнать чужое, увезти в участок."""
    from bot.car_service import here_rows, leave_here

    row = await a_theft(db)
    thief = await db.get_player(2)
    await leave_here(db, thief, HATCH, NOW)

    # Патрульный в участке машину не видит: она у бара
    cop = await officer(db)
    assert await here_rows(db, cop, NOW) == []

    # Пришёл туда, где она стоит, — и кнопка появилась
    cop.location = "bar"
    await db.save_player(cop)
    seen = await here_rows(db, cop, NOW)
    assert [one["can_deliver"] for one in seen] == [True]
    assert [one["plate"] for one in seen] == [str(row["plate"])]

    # Прохожему без семьи угонять нечем, и сказано почему
    passer = await stand(db, make_player(3, "Прохожий"))
    plain = await here_rows(db, passer, NOW)
    assert plain[0]["can_steal"] is False and "семью" in plain[0]["why"]
    assert plain[0]["can_deliver"] is False, "без значка машин не возят"

    # Мафии — угнать: улица не стоянка, и драться за неё не надо
    second = await mafioso(db, make_player(4, "Вор второй"))
    assert [one["can_steal"] for one in await here_rows(db, second, NOW)] == [True]
    await steal(db, second, int(row["id"]), NOW)
    assert [one["code"] for one in await db.cars_of(4)] == [HATCH]

    # Хозяину — забрать своё, и только у того же здания
    owner = await stand(db, make_player(6, "Хозяин"))
    his = await with_car(db, owner, SEDAN)
    await leave_here(db, owner, SEDAN, NOW)
    assert [
        one["can_steal"] for one in await here_rows(db, await db.get_player(6), NOW)
        if int(one["id"]) == int(his["id"])
    ] == [False], "свою не угоняют"


async def test_the_badge_sees_the_stolen_car_in_his_hands(db):
    """Угнанную держат на руках — и у дома это видно значку, а не всем."""
    from bot.robbery_service import RobberyService
    from bot.webapp.crowd import build_crowd, wanted_car_mark

    row = await a_theft(db)
    assert await theft_suspects(db) == {2}
    # Хозяину машины ордер не выписывают: он её себе не угонял
    assert 1 not in await theft_suspects(db)

    cop = await officer(db)
    thief = await db.get_player(2)
    cop.location = thief.where(NOW)
    await db.save_player(cop)

    # Тот же дом глазами прохожего: ни ордера, ни отметки о розыске
    passer = await stand(db, make_player(3, "Прохожий", where=thief.where(NOW)))
    service = RobberyService(db)
    try:
        body = await build_crowd(db, cop, service, NOW)
        plain = await build_crowd(db, passer, service, NOW)
    finally:
        await service.close()

    seen = next(one for one in body["crowd"] if one["user_id"] == 2)
    assert seen["wanted"] is True, "держит угнанную — значит, под ордером"
    assert seen["can_arrest"] is True, "он в сети: его задерживают дракой"
    assert seen["wanted_car"]["plate"] == str(row["plate"])
    assert seen["wanted_car"]["can_take"] is False, "в сети — только дракой"

    mine = next(one for one in plain["crowd"] if one["user_id"] == 2)
    assert mine["wanted"] is False and mine["wanted_car"] == {}

    # Угонщик вышел из игры — машину забирают молча
    thief.seen_at = NOW - 10 * 3_600
    await db.save_player(thief)
    mark = await wanted_car_mark(db, cop, await db.get_player(2), NOW)
    assert mark["can_take"] is True


# ---------- сверка номеров ----------


async def test_the_restamped_plate_is_known_to_the_base(db):
    """Перебитый номер прячет машину от розыска, но не от базы."""
    from bot.car_service import restamp
    from bot.police_service import theft_of

    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    first = str(row["plate"])
    thief = await mafioso(db, make_player(2, "Вор"))
    await steal(db, thief, int(row["id"]), NOW)
    victim = await db.get_player(1)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)

    rich = await db.get_player(2)
    rich.location = "car_wash"
    await db.save_player(rich)
    await restamp(db, rich, HATCH, NOW)

    fresh = await db.car_by_id(int(row["id"]))
    assert str(fresh["plate"]) != first
    assert str(fresh["plate_first"]) == first, "выданный номер остался в базе"
    assert not fresh["stolen_filed"], "с машины розыск снят"

    # И всё же она в розыске: сверка номера с базой её узнаёт
    case = await theft_of(db, fresh)
    assert case is not None and str(case["plate"]) == first

    cop = await officer(db)
    line = next(
        one for one in await traffic_sheet(db, cop, NOW)
        if int(one["id"]) == int(row["id"])
    )
    assert line["restamped"] and line["plate_first"] == first


# ---------- суд ----------


async def judge_at_court(db: Database, user_id: int = 8) -> Player:
    """Судья в зале: приговоры выносит он."""
    one = make_player(user_id, "Судья", where="courthouse")
    one.job_code = "chief_judge"
    await db.save_player(one)
    return one


async def a_theft(
    db: Database,
    thief_id: int = 2,
    owner_id: int = 1,
    code: str = HATCH,
) -> dict:
    """Угон с заявлением: дело, готовое к суду."""
    owner = await stand(db, make_player(owner_id, "Жертва", seen=0))
    row = await with_car(db, owner, code)
    thief = await mafioso(db, make_player(thief_id, "Вор"))
    await steal(db, thief, int(row["id"]), NOW)
    victim = await db.get_player(owner_id)
    victim.location = STATION
    await db.save_player(victim)
    await file_theft(db, victim, int(row["id"]), NOW)
    return row


async def test_the_court_sentences_by_the_ladder(db):
    """Первый угон — штраф, второй — штраф и срок, третий — вдвое."""
    from bot.court_service import CourtError, docket, sentence
    from bot.game.court import FINES, JAIL_HOURS

    row = await a_theft(db)
    judge = await judge_at_court(db)

    cases = await docket(db, NOW)
    assert len(cases) == 1
    assert cases[0]["times"] == 1
    assert (cases[0]["fine"], cases[0]["jail_hours"]) == (FINES[0], JAIL_HOURS[0])

    said = await sentence(db, judge, int(cases[0]["id"]), NOW)
    assert "300" in said

    # Второй раз по тому же делу приговор не выносят
    with pytest.raises(CourtError, match="уже вынесен"):
        await sentence(db, judge, int(cases[0]["id"]), NOW)
    assert await docket(db, NOW) == []
    del row


async def test_only_the_robe_sentences(db):
    """Приговор выносит судья и только в зале суда."""
    from bot.court_service import CourtError, docket, sentence

    await a_theft(db)
    cases = await docket(db, NOW)
    clerk = make_player(7, "Секретарь", where="courthouse")
    clerk.job_code = "court_clerk"
    await db.save_player(clerk)

    with pytest.raises(CourtError, match="выносит судья"):
        await sentence(db, clerk, int(cases[0]["id"]), NOW)

    judge = await judge_at_court(db)
    judge.location = "bar"
    await db.save_player(judge)
    with pytest.raises(CourtError, match="в суде"):
        await sentence(db, judge, int(cases[0]["id"]), NOW)


async def test_the_fine_comes_off_the_card_and_blocks_the_account(db):
    """Штраф берут со счёта; не хватило — счёт заблокирован до пополнения."""
    from bot.bank_service import deposit, open_account
    from bot.court_service import apply_pending, sentence
    from bot.game.court import FINES

    await a_theft(db)
    thief = await db.get_player(2)
    await open_account(db, thief)
    thief.account_balance = 100
    thief.credits = 10_000
    await db.save_player(thief)
    judge = await judge_at_court(db)
    cases = await db.theft_cases(judged=False)

    await sentence(db, judge, int(cases[0]["id"]), NOW)

    fresh = await db.get_player(2)
    assert fresh.account_balance == 0
    assert fresh.account_debt == FINES[0] - 100
    assert fresh.account_blocked and not fresh.card_works(NOW)
    assert fresh.credits == 10_000, "наличные при счёте не трогают"

    # Пополнение гасит долг первым делом
    await deposit(db, fresh, 500, NOW)
    assert fresh.account_debt == 0 and not fresh.account_blocked
    assert await apply_pending(db, fresh, NOW) == "", "приговор исполнен"


async def test_without_an_account_the_fine_turns_into_hours(db):
    """Счёта нет — берут наличными; не хватило — час за каждые 300 кр."""
    from bot.court_service import sentence
    from bot.game.court import EXTRA_PER, FINES

    await a_theft(db)
    thief = await db.get_player(2)
    thief.credits = 50
    await db.save_player(thief)
    judge = await judge_at_court(db)
    cases = await db.theft_cases(judged=False)

    await sentence(db, judge, int(cases[0]["id"]), NOW)

    fresh = await db.get_player(2)
    assert fresh.credits == 0
    assert fresh.account_debt == 0, "долг пишут только на счёт"
    until, _ = await db.jail_term(2)
    unpaid = FINES[0] - 50
    assert until == NOW + -(-unpaid // EXTRA_PER) * 3_600
    assert fresh.location == "prison"


async def test_the_city_judges_when_no_one_wears_the_robe(db):
    """Кресло судьи пустое — приговор выносит сам город, и сразу."""
    from bot.court_service import apply_pending, city_judges, robe_in_city
    from bot.game.city import JUDGE_ID

    row = await a_theft(db)
    assert await robe_in_city(db) is False

    said = await city_judges(db, NOW)

    assert len(said) == 1 and str(row["plate"]) in said[0]
    case = (await db.theft_cases(judged=True, thief_id=2))[0]
    assert int(case["judge_id"]) == JUDGE_ID, "подписал город, а не человек"
    assert (int(case["fine"]), int(case["jail_hours"])) == (300, 0)

    # Второй заход ничего не задваивает
    assert await city_judges(db, NOW) == []

    # И экран суда говорит, кто теперь судит
    from bot.webapp.court import build_court

    screen = await build_court(db, await db.get_player(1), NOW)
    assert screen["city_judge"] is True
    assert "не назначен" in screen["note"]

    # И исполняется он так же, как людской: деньги уходят
    thief = await db.get_player(2)
    before = thief.credits
    done = await apply_pending(db, thief, NOW)
    assert "300" in done
    assert (await db.get_player(2)).credits == before - 300


async def test_the_live_robe_keeps_the_case_for_itself(db):
    """Пока в городе есть судья, город за него не решает."""
    from bot.court_service import city_judges, docket, robe_in_city

    await a_theft(db)
    await judge_at_court(db)
    assert await robe_in_city(db) is True

    assert await city_judges(db, NOW) == []
    assert len(await docket(db, NOW)) == 1, "дело ждёт человека"

    # Судья ушёл с места — лежащее дело разбирает город
    judge = await db.get_player(8)
    judge.job_code = ""
    await db.save_player(judge)
    assert len(await city_judges(db, NOW)) == 1
    assert await docket(db, NOW) == []


async def test_the_card_catches_the_city_verdict_on_its_own(db):
    """Заочный приговор от города исполняется при первом же взгляде."""
    from bot.court_service import apply_pending, pending_for

    await a_theft(db)
    thief = await db.get_player(2)

    # Дело ещё никто не рассматривал — и всё же приговор уже исполнен
    said = await apply_pending(db, thief, NOW)

    assert "300" in said
    assert await pending_for(db, 2) == []


async def test_the_absent_thief_serves_on_his_return(db):
    """Приговор заочный: исполняется, когда угонщик появится в городе."""
    from bot.car_service import leave_here
    from bot.court_service import apply_pending, pending_for, sentence

    await a_theft(db)
    thief = await db.get_player(2)
    thief.credits = 10_000
    await db.save_player(thief)
    judge = await judge_at_court(db)
    cases = await db.theft_cases(judged=False)

    # Приговор выносят, и он исполняется сразу: угонщик в городе есть
    await sentence(db, judge, int(cases[0]["id"]), NOW)
    assert await pending_for(db, 2) == []

    # Угнанную бросают у здания: руки свободны под вторую
    await leave_here(db, await db.get_player(2), HATCH, NOW)

    # Второй угон — и снова заочно, но теперь с решёткой
    row = await a_theft(db, thief_id=2, owner_id=3, code=SEDAN)
    second = await db.theft_cases(judged=False)
    await db.judge_theft(int(second[0]["id"]), judge.user_id, 600, 3, NOW)
    assert len(await pending_for(db, 2)) == 1

    said = await apply_pending(db, await db.get_player(2), NOW)

    assert "600" in said and "3 ч" in said
    assert await pending_for(db, 2) == []
    until, _ = await db.jail_term(2)
    assert until == NOW + 3 * 3_600
    del row
