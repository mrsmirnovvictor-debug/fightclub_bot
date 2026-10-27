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
from bot.game.cars import IN_FLAT, IN_POLICE, RESTAMP_PRICE, STEAL_WINDOW
from bot.game.classes import Stats
from bot.game.clans import Clan, Rank, Standing
from bot.game.driving import School
from bot.game.plates import DIGITS, LETTERS, MASK, is_plate
from bot.game.police import FINE_NO_LICENCE
from bot.models import Player
from bot.police_service import (
    PoliceError,
    file_theft,
    fine_driver,
    seize_car,
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
    assert line["stolen"] and line["wanted"] and line["can_seize"]
    assert not line["licence"]
    # Гражданскому автоучёт не отдают вовсе
    assert await traffic_sheet(db, await db.get_player(2), NOW) == []


async def test_the_wanted_car_is_seized_and_returned(db):
    """Изымают то, что в розыске, и возвращают хозяину."""
    owner = await stand(db, make_player(1, "Жертва", seen=0))
    row = await with_car(db, owner)
    thief = await mafioso(db, make_player(2, "Вор"))
    await steal(db, thief, int(row["id"]), NOW)
    cop = await officer(db)

    # Без заявления город в это дело не лезет
    with pytest.raises(PoliceError, match="не в розыске"):
        await seize_car(db, cop, int(row["id"]), NOW)

    fresh = await db.get_player(1)
    fresh.location = STATION
    await db.save_player(fresh)
    await file_theft(db, fresh, int(row["id"]), NOW)

    said = await seize_car(db, cop, int(row["id"]), NOW)

    assert "Жертва" in said
    assert [one["code"] for one in await db.cars_of(1)] == [HATCH]
    assert await db.cars_of(2) == []
    back = await db.car_row(1, HATCH)
    assert not back["stolen_from"] and not back["stolen_filed"]


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
