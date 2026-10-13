"""Кланы: как заводят семью, кого в неё берут и кто чем в ней распоряжается.

Проверяем пять вещей, в таком порядке важности:

1. **Границы семьи.** Двадцать пять человек, трое приближённых, один
   Дон — и ни одной лазейки мимо этих чисел.
2. **Кто что решает.** Мафиози не решает ничего, приближённый — всё,
   кроме чина Дона, Дон — всё.
3. **Две дороги в семью.** Заявка бойца и приглашение старших, и обе
   кончаются одним: одной записью в клане и ни одной лишней заявкой.
4. **Знак клана.** Квадратная картинка png или jpeg — и ничего больше.
5. **Место.** Заводят клан, стучатся, уходят и ведут дела сейфа в
   особняке; чины и заявки решают откуда угодно.
6. **Общий карман.** В сейф и на полки кладут все, берут только Дон и
   приближённые, и полок ровно двенадцать.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.clan_service import (
    ClanError,
    accept,
    rename,
    apply,
    approve,
    decline,
    demote,
    deposit,
    expel,
    found,
    handover,
    invite,
    leave,
    promote,
    refuse,
    stash,
    take,
    withdraw,
    withdraw_vault,
)
from bot.config import Config
from bot.database import Database
from bot.game.classes import Stats
from bot.content.clans import SIGNS, get_sign
from bot.game.clans import (
    CLAN_PRICE,
    MANSION,
    MAX_CAPOS,
    MAX_MEMBERS,
    RENAME_COOLDOWN,
    RENAME_PRICE,
    VAULT_SLOTS,
    Petition,
    Rank,
    clean_name,
    name_problem,
    sign_problem,
)
from bot.game.locations import Service, get_location, where_to
from bot.models import Player
from bot.webapp.clans import build_clan, build_estate, build_mansion
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

CLUB = "fight_club"

SIGN = "black_ravens"  # знак, которым заводят клан в тестах


def make_player(
    user_id: int = 42,
    nickname: str = "Тайлер",
    location: str = MANSION,
    credits: int = CLAN_PRICE,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(
    db: Database, user_id: int, nickname: str, location: str = MANSION
) -> Player:
    player = make_player(user_id, nickname, location, credits=0)
    await db.save_player(player)
    return player


async def family(db: Database, members: int = 0) -> tuple[Player, list[Player]]:
    """Клан с Доном и нужным числом мафиози сверх него."""
    don = make_player()
    await db.save_player(don)
    await found(db, don, "Коза Ностра", SIGN)
    rest = []
    for number in range(members):
        soldier = await enlist(db, 100 + number, f"Боец-{number}")
        await db.add_member(soldier.user_id, don.clan.clan.id, Rank.SOLDIER)
        soldier.clan = await db.standing_of(soldier.user_id)
        rest.append(soldier)
    don.clan = await db.standing_of(don.user_id)
    return don, rest


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


# ---------- знак клана ----------


def test_every_sign_is_drawn_and_belongs_to_one_family():
    """Знаков семнадцать, у каждого своё имя и своя картинка в бакете.

    Знак достаётся одной семье: занятый второй раз не берут. Отсюда и
    потолок — пока нарисовано семнадцать знаков, столько кланов в городе
    и может быть.
    """
    from bot.game.art import CLANS

    assert len(SIGNS) == 17
    codes = [sign.code for sign in SIGNS]
    assert len(set(codes)) == len(codes), "два знака под одним кодом"
    titles = [sign.title for sign in SIGNS]
    assert len(set(titles)) == len(titles), "два знака под одним именем"
    for sign in SIGNS:
        assert sign.picture == f"{CLANS}/{sign.code}.png"
        assert get_sign(sign.code) is sign
    # Первый знак приехал под именем файла `mafia` — его рисовали раньше
    # прочих, и переименовывать чужой бакет не наше дело
    assert SIGNS[0].code == "mafia" and SIGNS[0].title == "Кровавые черепа"
    assert get_sign("не рисовали") is None

    assert sign_problem(SIGN) == ""
    assert sign_problem("") == "Выбери знак клана."
    assert sign_problem("не рисовали") == "Такого знака нет."
    assert "у другой семьи" in sign_problem(SIGN, {SIGN})


def test_the_clan_name_is_cleaned_and_measured():
    """Имя клана: от трёх до двадцати четырёх знаков, без лишних пробелов."""
    assert clean_name("  Коза   Ностра  ") == "Коза Ностра"
    assert name_problem("Коза Ностра") == ""
    assert name_problem("Ко") and name_problem("  ")
    assert name_problem("К" * 25)
    assert name_problem("Коза <b>Ностра</b>"), "в имени не должно быть разметки"


# ---------- завести клан ----------


async def test_the_mansion_is_where_a_clan_is_founded(db):
    """Клан заводят в особняке, за тысячу, и основатель становится Доном."""
    player = make_player()
    await db.save_player(player)

    clan = await found(db, player, "Коза Ностра", SIGN)

    assert clan.name == "Коза Ностра" and clan.don == player.user_id
    assert clan.members == 1
    assert player.credits == 0, "тысяча не списана"
    assert player.clan.rank is Rank.DON and player.clan.can_admin
    # Знак записан кодом, а картинка считается от него
    assert clan.sign == SIGN and clan.picture.endswith(f"/{SIGN}.png")
    assert await db.taken_signs() == {SIGN}
    # И услуга за домом та самая
    assert where_to(Service.MAFIA).code == MANSION
    assert get_location(MANSION).allows(Service.MAFIA)


async def test_a_clan_is_not_founded_anywhere_else(db):
    """Из бойцовского клуба клан не завести: за этим — в особняк."""
    player = make_player(location=CLUB)
    await db.save_player(player)

    with pytest.raises(ClanError, match="Особняке мафии"):
        await found(db, player, "Коза Ностра", SIGN)
    assert player.credits == CLAN_PRICE, "деньги списали мимо особняка"


async def test_a_clan_costs_a_thousand_and_a_name_of_its_own(db):
    """Без тысячи, без имени и с занятым именем клан не заводят."""
    poor = make_player(credits=CLAN_PRICE - 1)
    await db.save_player(poor)
    with pytest.raises(ClanError, match="Клан стоит"):
        await found(db, poor, "Коза Ностра", SIGN)

    rich = make_player(43, "Марла")
    await db.save_player(rich)
    await found(db, rich, "Коза Ностра", SIGN)

    twin = make_player(44, "Ангел")
    await db.save_player(twin)
    with pytest.raises(ClanError, match="уже есть"):
        await found(db, twin, "коза ностра", "silver_wolves")
    assert twin.credits == CLAN_PRICE, "за отказ денег не берут"


async def test_a_sign_belongs_to_one_family_only(db):
    """Знак достаётся первому: второй клан под ним не завести."""
    don, _ = await family(db)
    twin = make_player(44, "Ангел")
    await db.save_player(twin)

    with pytest.raises(ClanError, match="у другой семьи"):
        await found(db, twin, "Другая семья", SIGN)
    assert len(await db.all_clans()) == 1
    assert twin.credits == CLAN_PRICE, "за отказ денег не берут"

    with pytest.raises(ClanError, match="Такого знака нет"):
        await found(db, twin, "Другая семья", "не рисовали")
    assert await db.taken_signs() == {SIGN}


async def test_one_clan_at_a_time(db):
    """Из двух семей сразу не выйдет: заводить вторую нельзя."""
    don, _ = await family(db)
    with pytest.raises(ClanError, match="уже в клане"):
        await found(db, don, "Другая семья", "golden_serpents")


# ---------- заявки ----------


async def test_a_request_waits_for_the_elders(db):
    """Заявка подаётся в особняке и ждёт слова старших."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")

    said = await apply(db, guest, don.clan.clan.id)
    assert "подана" in said
    knocks = await db.petitions_of_clan(don.clan.clan.id, Petition.REQUEST)
    assert [one.user_id for one in knocks] == [guest.user_id]
    assert knocks[0].nickname == "Гость"
    # Пока заявку не одобрили, боец сам по себе
    assert await db.standing_of(guest.user_id) is None

    await approve(db, don, guest.user_id)
    standing = await db.standing_of(guest.user_id)
    assert standing is not None and standing.rank is Rank.SOLDIER
    assert await db.petitions_of_clan(don.clan.clan.id, Petition.REQUEST) == []


async def test_a_request_is_made_at_the_mansion_only(db):
    """Стучатся в семью ногами: с ринга заявку не подать."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость", location=CLUB)
    with pytest.raises(ClanError, match="Особняке мафии"):
        await apply(db, guest, don.clan.clan.id)


async def test_a_request_can_be_withdrawn_and_declined(db):
    """Заявку отзывает сам боец, отклоняют — старшие."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")
    clan_id = don.clan.clan.id

    await apply(db, guest, clan_id)
    await withdraw(db, guest, clan_id)
    assert await db.petition(clan_id, guest.user_id) is None

    await apply(db, guest, clan_id)
    await decline(db, don, guest.user_id)
    assert await db.petition(clan_id, guest.user_id) is None
    assert await db.standing_of(guest.user_id) is None


async def test_joining_one_family_drops_the_knocks_at_other_doors(db):
    """Принятый в семью перестаёт стучаться во все остальные двери."""
    first, _ = await family(db)
    second = make_player(43, "Марла")
    await db.save_player(second)
    await found(db, second, "Другая семья", "golden_serpents")

    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, first.clan.clan.id)
    await apply(db, guest, second.clan.clan.id)
    assert len(await db.petitions_of_user(guest.user_id, Petition.REQUEST)) == 2

    await approve(db, first, guest.user_id)
    assert await db.petitions_of_user(guest.user_id, Petition.REQUEST) == []


# ---------- приглашения ----------


async def test_an_invite_waits_for_the_fighter(db):
    """Приглашение зовёт бойца, и ответ за ним."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость", location=CLUB)

    said = await invite(db, don, "Гость")
    assert "приглашён" in said
    knocks = await db.petitions_of_user(guest.user_id, Petition.INVITE)
    assert len(knocks) == 1 and knocks[0].clan_name == "Коза Ностра"
    assert knocks[0].by_name == don.nickname
    assert await db.standing_of(guest.user_id) is None

    # Отвечают откуда угодно: окно показывают при входе в игру
    await accept(db, guest, don.clan.clan.id)
    assert guest.clan is not None and guest.clan.rank is Rank.SOLDIER


async def test_an_invite_can_be_refused(db):
    """Отказ от приглашения снимает его, и в семью бойца не тянут."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")
    await invite(db, don, "Гость")

    await refuse(db, guest, don.clan.clan.id)
    assert await db.petitions_of_user(guest.user_id, Petition.INVITE) == []
    assert await db.standing_of(guest.user_id) is None
    with pytest.raises(ClanError, match="приглашения нет"):
        await accept(db, guest, don.clan.clan.id)


async def test_the_two_roads_meet_in_the_middle(db):
    """Позвали того, кто и сам стучался, — он сразу в семье.

    Две дороги в семью — это одна запись в таблице заявок. Когда они
    сходятся, обеим сторонам уже нечего решать: и боец постучался, и
    старший позвал.
    """
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)

    said = await invite(db, don, "Гость")
    assert "стучался" in said
    standing = await db.standing_of(guest.user_id)
    assert standing is not None and standing.clan.id == don.clan.clan.id

    # И наоборот: позвали, а боец подал заявку — это согласие
    other = await enlist(db, 51, "Другой")
    await invite(db, don, "Другой")
    said = await apply(db, other, don.clan.clan.id)
    assert "Приглашение принято" in said
    assert other.clan is not None


async def test_only_the_elders_invite_and_decide(db):
    """Мафиози не решает ничего: ни звать, ни принимать, ни выгонять."""
    don, (soldier,) = await family(db, members=1)
    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)

    for action, args in (
        (invite, ("Гость",)),
        (approve, (guest.user_id,)),
        (decline, (guest.user_id,)),
        (expel, (don.user_id,)),
        (promote, (don.user_id,)),
    ):
        with pytest.raises(ClanError, match="Дон и приближённые"):
            await action(db, soldier, *args)


# ---------- чины ----------


async def test_the_don_keeps_three_men_close(db):
    """Приближённых трое — четвёртого не назначить."""
    don, soldiers = await family(db, members=MAX_CAPOS + 1)
    for soldier in soldiers[:MAX_CAPOS]:
        await promote(db, don, soldier.user_id)
    assert await db.count_rank(don.clan.clan.id, Rank.CAPO) == MAX_CAPOS

    with pytest.raises(ClanError, match=f"уже {MAX_CAPOS}"):
        await promote(db, don, soldiers[MAX_CAPOS].user_id)

    # Снял одного — место освободилось
    await demote(db, don, soldiers[0].user_id)
    await promote(db, don, soldiers[MAX_CAPOS].user_id)
    assert await db.count_rank(don.clan.clan.id, Rank.CAPO) == MAX_CAPOS


async def test_a_capo_decides_like_the_don_but_for_his_own_rank(db):
    """Приближённый решает всё, кроме чина Дона: его не снять и не выгнать."""
    don, (right_hand, soldier) = await family(db, members=2)
    await promote(db, don, right_hand.user_id)
    right_hand.clan = await db.standing_of(right_hand.user_id)

    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)
    assert "одобрена" in await approve(db, right_hand, guest.user_id)
    assert "выставлен" in await expel(db, right_hand, soldier.user_id)

    with pytest.raises(ClanError, match="Дона из семьи не выгоняют"):
        await expel(db, right_hand, don.user_id)
    with pytest.raises(ClanError, match="Дона не снимают"):
        await demote(db, right_hand, don.user_id)


async def test_the_don_hands_over_the_family(db):
    """Дон передаёт дела и остаётся в семье — кем сам решит."""
    don, (heir,) = await family(db, members=1)
    clan_id = don.clan.clan.id

    said = await handover(db, don, heir.user_id, keep=Rank.CAPO.value)
    assert "переданы" in said

    assert (await db.standing_of(heir.user_id)).rank is Rank.DON
    assert (await db.standing_of(don.user_id)).rank is Rank.CAPO
    assert (await db.clan_by_id(clan_id)).don == heir.user_id
    # Прежний Дон больше не Дон: новых дел он не передаёт
    with pytest.raises(ClanError, match="Дела передаёт Дон"):
        await handover(db, don, heir.user_id)


async def test_the_old_don_can_stay_a_plain_mafioso(db):
    """Отдав дела, Дон может уйти в мафиози — это его выбор."""
    don, (heir,) = await family(db, members=1)
    await handover(db, don, heir.user_id, keep=Rank.SOLDIER.value)
    assert (await db.standing_of(don.user_id)).rank is Rank.SOLDIER


async def test_the_family_holds_twenty_five(db):
    """Двадцать пять человек — и ни одного сверх, ни одной дорогой."""
    don, _ = await family(db, members=MAX_MEMBERS - 1)
    clan = await db.clan_by_id(don.clan.clan.id)
    assert clan.members == MAX_MEMBERS and clan.full

    guest = await enlist(db, 90, "Лишний")
    with pytest.raises(ClanError, match="мест нет"):
        await apply(db, guest, clan.id)
    with pytest.raises(ClanError, match="мест нет"):
        await invite(db, don, "Лишний")

    # И даже с уже поданной заявкой: место кончилось, пока её читали
    await db.add_petition(clan.id, guest.user_id, Petition.REQUEST)
    with pytest.raises(ClanError, match="мест нет"):
        await approve(db, don, guest.user_id)

    # И с приглашением, выписанным до того, как семья набралась: согласие
    # упирается в ту же стену
    await db.add_petition(clan.id, guest.user_id, Petition.INVITE, by_id=don.user_id)
    with pytest.raises(ClanError, match="мест нет"):
        await accept(db, guest, clan.id)
    assert await db.standing_of(guest.user_id) is None


# ---------- уйти ----------


async def test_a_soldier_walks_out_of_the_mansion(db):
    """Из семьи уходят ногами — через особняк."""
    don, (soldier,) = await family(db, members=1)
    soldier.location = CLUB
    with pytest.raises(ClanError, match="Особняке мафии"):
        await leave(db, soldier)

    soldier.location = MANSION
    said = await leave(db, soldier)
    assert "больше не в клане" in said
    assert await db.standing_of(soldier.user_id) is None
    assert (await db.clan_by_id(don.clan.clan.id)).members == 1


async def test_the_don_leaves_last_and_the_family_ends(db):
    """Дон уходит, только передав дела; уходя последним — распускает семью."""
    don, (soldier,) = await family(db, members=1)
    with pytest.raises(ClanError, match="передав дела"):
        await leave(db, don)

    soldier.location = MANSION
    await leave(db, soldier)
    said = await leave(db, don)
    assert "распущен" in said
    assert await db.all_clans() == []
    assert await db.standing_of(don.user_id) is None


async def test_disbanding_takes_the_petitions_with_it(db):
    """Распущенная семья уносит и заявки: стучаться больше некуда."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)

    await leave(db, don)
    assert await db.petitions_of_user(guest.user_id, Petition.REQUEST) == []


# ---------- экраны ----------


async def test_the_mansion_screen_shows_the_city_and_the_price(db):
    """Экран особняка: цена, свои заявки и все кланы города."""
    don, _ = await family(db)
    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)

    clans = await db.all_clans()
    knocks = await db.petitions_of_user(guest.user_id, Petition.REQUEST)
    body = build_mansion(guest, clans, knocks, TOKEN)

    assert body["price"] == CLAN_PRICE and body["in_clan"] is False
    assert body["max_members"] == MAX_MEMBERS
    # Чем платить, видно здесь же: наличные, счёт и что выбрано сейчас
    assert body["purse"]["cash"] == guest.credits
    assert body["purse"]["purse"] in ("cash", "card")
    assert body["purse"]["titles"] and body["purse"]["note"]
    row = body["clans"][0]
    assert row["name"] == "Коза Ностра" and row["asked"] is True
    assert row["members"] == 1 and row["sign"] == SIGN
    assert row["emblem"] == get_sign(SIGN).picture

    # Знаки города: занятый подписан чужой семьёй и выбору не подлежит
    signs = {one["code"]: one for one in body["signs"]}
    assert len(signs) == len(SIGNS)
    assert signs[SIGN]["taken"] is True and signs[SIGN]["owner"] == "Коза Ностра"
    assert signs["silver_wolves"]["taken"] is False
    assert body["free_signs"] == len(SIGNS) - 1

    # А у Дона тот же экран говорит про его семью и не зовёт заводить вторую
    mine = build_mansion(don, clans, [], TOKEN)
    assert mine["in_clan"] is True and mine["is_don"] is True
    assert mine["clans"][0]["mine"] is True
    assert "Дон клана" in mine["note"]


async def test_the_clan_screen_sorts_the_family_by_rank(db):
    """Вкладка клана: Дон, приближённые, мафиози — и кнопки по чину."""
    don, (right_hand, soldier) = await family(db, members=2)
    await promote(db, don, right_hand.user_id)
    right_hand.clan = await db.standing_of(right_hand.user_id)
    guest = await enlist(db, 50, "Гость")
    await apply(db, guest, don.clan.clan.id)

    clan_id = don.clan.clan.id
    roster = await db.roster_of(clan_id)
    requests = await db.petitions_of_clan(clan_id, Petition.REQUEST)
    body = build_clan(don, roster, requests, [], TOKEN)

    assert body["in_clan"] is True and body["is_don"] is True
    assert [rank["code"] for rank in body["ranks"]] == ["don", "capo", "soldier"]
    assert [one["nickname"] for one in body["ranks"][0]["members"]] == ["Тайлер"]
    assert [one["nickname"] for one in body["ranks"][1]["members"]] == ["Боец-0"]
    assert [one["nickname"] for one in body["ranks"][2]["members"]] == ["Боец-1"]
    assert body["free_capo_seats"] == MAX_CAPOS - 1
    assert [one["nickname"] for one in body["requests"]] == ["Гость"]

    # Строка несёт знаки в том порядке, в каком они стоят на экране
    row = body["ranks"][2]["members"][0]
    assert row["fclass"]["emoji"] and row["emblem"] == get_sign(SIGN).picture
    assert row["clan"] == "Коза Ностра" and row["rank_title"] == "Мафиози"
    assert row["can_expel"] and row["can_promote"] and not row["can_demote"]
    # Себя Дон не выгоняет, а вот передача прав стоит именно на его
    # строке: отдаёт дела он, и преемника выбирает списком в окне
    own = body["ranks"][0]["members"][0]
    assert not own["can_expel"] and own["can_handover"]
    assert not row["can_handover"], "права передают с дона, а не на мафиози"

    # Мафиози тех же людей видит, а кнопок и заявок — нет
    plain = build_clan(soldier, roster, requests, [], TOKEN)
    assert plain["can_admin"] is False and plain["requests"] == []
    assert not plain["ranks"][0]["members"][0]["can_expel"]

    # А приближённый решает всё, кроме чина Дона: на самом Доне кнопок нет
    second = build_clan(right_hand, roster, requests, [], TOKEN)
    assert second["can_admin"] is True and second["is_don"] is False
    chief = second["ranks"][0]["members"][0]
    assert chief["nickname"] == "Тайлер"
    assert not chief["can_expel"] and not chief["can_demote"]
    assert not chief["can_handover"], "дела передаёт Дон, а не приближённый"
    assert second["ranks"][2]["members"][0]["can_expel"]


async def test_a_loner_has_no_clan_screen(db):
    """У бойца без клана вкладка пустая — и это видно по ответу."""
    loner = await enlist(db, 50, "Один")
    body = build_clan(loner, None, [], [], TOKEN)
    assert body["in_clan"] is False and body["ranks"] == []


# ---------- ручки ----------


async def test_the_mansion_answers_only_at_the_mansion(client, db):
    """Ручка особняка проверяет место сама: спрятанная кнопка не защита."""
    player = make_player(location=CLUB)
    await db.save_player(player)

    answer = await client.get("/api/mafia", headers=headers())
    assert answer.status == 409
    assert "Особняк мафии" in (await answer.json())["error"]

    player.location = MANSION
    await db.save_player(player)
    answer = await client.get("/api/mafia", headers=headers())
    assert answer.status == 200
    body = await answer.json()
    assert body["price"] == CLAN_PRICE and body["clans"] == []


async def test_the_whole_road_walks_through_the_app(client, db):
    """Клан заводят, зовут в него и отвечают на приглашение через мини-апп."""
    don = make_player()
    await db.save_player(don)
    await enlist(db, 50, "Гость", location=CLUB)

    answer = await client.post(
        "/api/mafia",
        headers=headers(),
        json={"action": "found", "name": "Коза Ностра", "sign": SIGN},
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert "заведён" in body["said"] and body["in_clan"] is True
    clan_id = body["clans"][0]["id"]
    # Карточка в том же ответе уже знает про семью: по ней зажигается вкладка
    assert body["card"]["clan"]["name"] == "Коза Ностра"

    # Зовём гостя — и он видит приглашение в своей карточке
    answer = await client.post(
        "/api/clan",
        headers=headers(),
        json={"action": "invite", "nickname": "Гость"},
    )
    assert answer.status == 200, await answer.text()
    card = await (await client.get("/api/card", headers=headers(50))).json()
    assert [one["clan"] for one in card["invites"]] == ["Коза Ностра"]
    assert card["invites"][0]["by"] == "Тайлер"

    # Гость соглашается, не выходя из клуба: приглашение отвечают откуда угодно
    answer = await client.post(
        "/api/clan", headers=headers(50), json={"action": "accept", "clan": clan_id}
    )
    assert answer.status == 200, await answer.text()
    body = await answer.json()
    assert body["in_clan"] is True and body["clan"]["rank"] == "soldier"
    assert body["card"]["clan"]["emblem"] == get_sign(SIGN).picture

    # И в списке клуба у обоих стоят знаки семьи
    club = await (await client.get("/api/club", headers=headers())).json()
    assert all(row["clan"]["name"] == "Коза Ностра" for row in club["fighters"])


async def test_the_sign_comes_from_the_bucket(client, db):
    """Знак клана — картинка из бакета: своей ручки у него нет.

    Пока знак грузил игрок, он лежал в базе и отдавался подписанной
    ссылкой. Теперь знаки рисует владелец, и они лежат там же, где вся
    остальная графика клуба.
    """
    don, _ = await family(db)
    body = await (await client.get("/api/mafia", headers=headers())).json()

    link = body["clans"][0]["emblem"]
    assert link == get_sign(SIGN).picture
    assert link.startswith("https://") and link.endswith(".png")
    assert (await client.get(f"/clan/{don.clan.clan.id}/emblem")).status == 404


async def test_a_sign_nobody_drew_does_not_found_a_clan(client, db):
    """Код знака с потолка разворачивают на входе, а не в базе."""
    player = make_player()
    await db.save_player(player)

    answer = await client.post(
        "/api/mafia",
        headers=headers(),
        json={"action": "found", "name": "Коза Ностра", "sign": "не рисовали"},
    )
    assert answer.status == 409
    assert "Такого знака нет" in (await answer.json())["error"]
    assert await db.all_clans() == []


async def test_the_name_changes_for_two_hundred_once_in_half_a_year(db):
    """Имя меняет Дон: двести кредитов и не чаще раза в полгода."""
    don, (soldier,) = await family(db, members=1)
    don.credits = RENAME_PRICE * 3
    await db.save_player(don)

    with pytest.raises(ClanError, match="меняет Дон"):
        await rename(db, soldier, "Чужое имя")

    said = await rename(db, don, "  Чёрная   рука ")
    assert "Чёрная рука" in said
    clan = await db.clan_by_id(don.clan.clan.id)
    assert clan.name == "Чёрная рука" and clan.renamed_at > 0
    assert don.credits == RENAME_PRICE * 2, "двести не списаны"

    # Второй раз — только через полгода
    with pytest.raises(ClanError, match="не чаще"):
        await rename(db, don, "Снова другое")
    assert clan.can_rename(clan.renamed_at + RENAME_COOLDOWN)
    later = clan.renamed_at + RENAME_COOLDOWN
    await rename(db, don, "Снова другое", now=later)
    assert (await db.clan_by_id(clan.id)).name == "Снова другое"
    assert don.credits == RENAME_PRICE


async def test_the_new_name_is_still_one_of_a_kind(db):
    """Переименоваться в чужое имя нельзя — оно одно на город."""
    don, _ = await family(db)
    don.credits = RENAME_PRICE
    await db.save_player(don)
    other = make_player(43, "Марла")
    await db.save_player(other)
    await found(db, other, "Чёрная рука", "silver_wolves")

    with pytest.raises(ClanError, match="уже есть"):
        await rename(db, don, "чёрная рука")
    with pytest.raises(ClanError, match="нынешнее имя"):
        await rename(db, don, "Коза Ностра")
    assert don.credits == RENAME_PRICE, "за отказ денег не берут"


async def test_the_name_changes_in_the_mansion_and_nowhere_else(db):
    """За имя платят, а платят в особняке: с ринга его не сменить."""
    don, _ = await family(db)
    don.credits = RENAME_PRICE
    don.location = CLUB
    with pytest.raises(ClanError, match="Особняке мафии"):
        await rename(db, don, "Чёрная рука")


# ---------- имущество семьи ----------


async def stock(db: Database, player: Player, code: str = "bandana") -> int:
    """Положить бойцу вещь в рюкзак и вернуть её номер."""
    owned = await db.add_gear(player.user_id, code)
    player.gear = await db.list_gear(player.user_id)
    return owned.id


async def test_everyone_fills_the_safe_and_only_elders_empty_it(db):
    """Сейф: кладут все, кто в семье, берут Дон и приближённые."""
    don, (capo, soldier) = await family(db, members=2)
    await promote(db, don, capo.user_id)
    capo.clan = await db.standing_of(capo.user_id)
    soldier.credits = 500
    await db.save_player(soldier)

    said = await deposit(db, soldier, 300)
    assert "300" in said and soldier.credits == 200
    assert (await db.clan_by_id(don.clan.clan.id)).vault == 300

    # Остаток видит вся семья — он лежит в ответе особняка у каждого
    soldier.clan = await db.standing_of(soldier.user_id)
    assert build_estate(soldier, [])["vault"] == 300
    assert build_estate(soldier, [])["can_take"] is False

    with pytest.raises(ClanError, match="Дон и приближённые"):
        await withdraw_vault(db, soldier, 100)
    assert (await db.clan_by_id(don.clan.clan.id)).vault == 300

    said = await withdraw_vault(db, capo, 200)
    assert "200" in said and capo.credits == 200
    assert (await db.clan_by_id(don.clan.clan.id)).vault == 100

    # Больше, чем лежит, не взять — и остаток от этого не меняется
    with pytest.raises(ClanError, match="столько не взять"):
        await withdraw_vault(db, don, 101)
    assert (await db.clan_by_id(don.clan.clan.id)).vault == 100


async def test_the_safe_takes_nothing_from_an_empty_pocket(db):
    """Пустой карман сейф не наполняет: отказ до списания."""
    don, _ = await family(db)
    don.credits = 10

    with pytest.raises(ClanError, match="наличными 10"):
        await deposit(db, don, 50)
    with pytest.raises(ClanError, match="Напиши число"):
        await deposit(db, don, 0)
    assert don.credits == 10
    assert (await db.clan_by_id(don.clan.clan.id)).vault == 0


async def test_the_safe_is_in_the_mansion(db):
    """Общий карман лежит в доме семьи: до него доходят ногами."""
    don, _ = await family(db)
    don.credits = 100
    don.location = CLUB

    with pytest.raises(ClanError, match="Особняке мафии"):
        await deposit(db, don, 50)
    with pytest.raises(ClanError, match="Особняке мафии"):
        await withdraw_vault(db, don, 50)
    with pytest.raises(ClanError, match="Особняке мафии"):
        await stash(db, don, 1)
    with pytest.raises(ClanError, match="Особняке мафии"):
        await take(db, don, 1)


async def test_a_loner_has_no_safe(db):
    """Своего сейфа у одиночки нет — и общего тоже."""
    loner = await enlist(db, 60, "Один")
    loner.credits = 100
    with pytest.raises(ClanError, match="не в клане"):
        await deposit(db, loner, 50)
    empty = build_estate(loner, [])
    assert empty["in_clan"] is False and empty["vault"] == 0


async def test_things_go_on_the_shelves_and_elders_take_them_off(db):
    """Хранилище: кладут все, забирают Дон и приближённые."""
    don, (soldier,) = await family(db, members=1)
    item_id = await stock(db, soldier)

    said = await stash(db, soldier, item_id)
    assert "Бандана" in said
    assert await db.list_gear(soldier.user_id) == [] and soldier.gear == []
    shelf = await db.clan_items(don.clan.clan.id)
    assert [one.code for one in shelf] == ["bandana"]
    assert shelf[0].by_name == "Боец-0"

    # Мафиози с полки не берёт — даже своё
    with pytest.raises(ClanError, match="Дон и приближённые"):
        await take(db, soldier, shelf[0].id)
    assert len(await db.clan_items(don.clan.clan.id)) == 1

    said = await take(db, don, shelf[0].id)
    assert "Бандана" in said
    assert [one.code for one in don.gear] == ["bandana"]
    assert await db.clan_items(don.clan.clan.id) == []
    with pytest.raises(ClanError, match="уже нет"):
        await take(db, don, shelf[0].id)


async def test_the_shelves_hold_twelve_things_and_not_a_thirteenth(db):
    """Полок двенадцать: тринадцатую вещь положить некуда."""
    don, _ = await family(db)
    for _ in range(VAULT_SLOTS):
        await stash(db, don, await stock(db, don))
    extra = await stock(db, don)

    with pytest.raises(ClanError, match=f"{VAULT_SLOTS} полок"):
        await stash(db, don, extra)
    assert len(await db.clan_items(don.clan.clan.id)) == VAULT_SLOTS
    assert [one.id for one in don.gear] == [extra], "вещь осталась у бойца"


async def test_the_worn_thing_goes_nowhere(db):
    """Надетое на полку не кладут: сперва его снимают."""
    don, _ = await family(db)
    item_id = await stock(db, don)
    owned = don.gear[0]
    owned.slot = owned.item.slot
    await db.save_gear(owned)
    don.gear = await db.list_gear(don.user_id)

    with pytest.raises(ClanError, match="сними вещь"):
        await stash(db, don, item_id)
    with pytest.raises(ClanError, match="Такой вещи"):
        await stash(db, don, item_id + 100)
    assert await db.clan_items(don.clan.clan.id) == []


async def test_the_mansion_opens_a_clan_with_its_three_tabs(client, db):
    """Особняк в два колена: список кланов, а по нажатию — сам клан.

    Открытый клан едет полем `opened`: состав, имущество и настройки
    одной записью. Пока клан не открыт, в ответе его нет — список
    кланов тащить за собой чужие сейфы не должен.
    """
    don, (soldier,) = await family(db, members=1)
    don.credits = 500
    await db.save_player(don)
    item_id = await stock(db, don)
    clan_id = don.clan.clan.id

    body = await (await client.get("/api/mafia", headers=headers())).json()
    assert body["opened"] == {}, "список кланов открывается без клана"
    assert [one["id"] for one in body["clans"]] == [clan_id]

    body = await (
        await client.get(f"/api/mafia?clan={clan_id}", headers=headers())
    ).json()
    opened = body["opened"]
    assert opened["id"] == clan_id and opened["mine"] is True
    assert opened["clan"]["rank_title"] == "Дон"
    assert [one["nickname"] for one in opened["members"]] == ["Тайлер", "Боец-0"]
    assert opened["can_admin"] is True and opened["is_don"] is True
    assert opened["estate"]["vault"] == 0

    # Действия помнят, какой клан открыт: страница шлёт его сама
    answer = await client.post(
        "/api/mafia",
        headers=headers(),
        json={"action": "deposit", "amount": 400, "open": clan_id},
    )
    assert answer.status == 200, await answer.text()
    estate = (await answer.json())["opened"]["estate"]
    assert estate["vault"] == 400 and estate["can_take"] is True
    assert estate["slots"] == VAULT_SLOTS
    assert [one["title"] for one in estate["bag"]] == ["Бандана"]

    answer = await client.post(
        "/api/mafia",
        headers=headers(),
        json={"action": "stash", "item": item_id, "open": clan_id},
    )
    assert answer.status == 200, await answer.text()
    estate = (await answer.json())["opened"]["estate"]
    stored = estate["items"]
    assert [one["title"] for one in stored] == ["Бандана"]
    assert stored[0]["by"] == "Тайлер" and estate["free"] == VAULT_SLOTS - 1
    assert estate["bag"] == []

    # Мафиози видит и сейф, и полки, а кнопок «взять» у него нет
    body = await (
        await client.get(f"/api/mafia?clan={clan_id}", headers=headers(100))
    ).json()
    assert body["opened"]["estate"]["vault"] == 400
    assert body["opened"]["estate"]["can_take"] is False
    assert body["opened"]["requests"] == [], "заявки — дело старших"

    answer = await client.post(
        "/api/mafia",
        headers=headers(100),
        json={"action": "take", "item": stored[0]["id"], "open": clan_id},
    )
    assert answer.status == 409
    assert "Дон и приближённые" in (await answer.json())["error"]

    answer = await client.post(
        "/api/mafia",
        headers=headers(),
        json={"action": "take", "item": stored[0]["id"], "open": clan_id},
    )
    assert answer.status == 200, await answer.text()
    estate = (await answer.json())["opened"]["estate"]
    assert estate["items"] == []
    assert [one["title"] for one in estate["bag"]] == ["Бандана"]


async def test_a_strangers_clan_opens_with_its_roster_alone(client, db):
    """Чужой клан открывается составом: ни сейфа, ни заявок в ответе нет."""
    don, _ = await family(db, members=1)
    clan_id = don.clan.clan.id
    guest = await enlist(db, 60, "Гость")
    await db.save_player(guest)

    body = await (
        await client.get(f"/api/mafia?clan={clan_id}", headers=headers(60))
    ).json()
    opened = body["opened"]
    assert opened["id"] == clan_id and opened["mine"] is False
    assert [one["nickname"] for one in opened["members"]] == ["Тайлер", "Боец-0"]
    assert opened["estate"] == {} and opened["requests"] == []
    assert opened["can_admin"] is False and opened["is_don"] is False
    # И кнопок на чужих бойцах у гостя нет никаких
    assert not any(
        one["can_expel"] or one["can_promote"] or one["can_handover"]
        for one in opened["members"]
    )

    # Клана с таким номером нет — открывается список
    body = await (
        await client.get("/api/mafia?clan=999", headers=headers(60))
    ).json()
    assert body["opened"] == {}
