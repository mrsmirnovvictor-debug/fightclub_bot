"""Квесты бармена: десять заданий, с которых начинается город.

Проверяем пять вещей, в таком порядке важности:

1. **Лестница.** Берут по одному и по порядку: пока одно на руках,
   второго не дают, а сданное не предлагают заново.
2. **Проверяет город, а не слово.** Задание закрывается событием или
   состоянием бойца — сказать «я сходил» и получить опыт нельзя.
3. **Деньги и вещи.** Аванс приходит при взятии, награда — при сдаче,
   а то, что бармен просил принести, он забирает.
4. **Разговор.** Бармен говорит то, что положено по состоянию: предлагает,
   ждёт, спрашивает про PRO, принимает работу.
5. **Место.** Задания берут и сдают у стойки: спрятанная кнопка
   обходится запросом мимо интерфейса.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.bartender import QUIZ, TALKS
from bot.database import Database
from bot.game.classes import Stats
from bot.game.driving import School
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.quests import (
    ORDER,
    QUESTS,
    QUIZ_DONE,
    Goal,
    get_quest,
    next_quest,
)
from bot.models import Player
from bot.quest_service import (
    QuestError,
    answer,
    hand,
    journal,
    notice,
    notice_visit,
    offered,
    take,
)
from bot.webapp.bar import build_bar
from bot.webapp.server import create_app
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

BAR = "bar"
CLUB = "fight_club"


def make_player(
    user_id: int = 42,
    nickname: str = "Тайлер",
    location: str = BAR,
    credits: int = 500,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=5,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(db: Database, player: Player | None = None) -> Player:
    player = player or make_player()
    await db.save_player(player)
    return player


async def walk(db: Database, player: Player, code: str) -> None:
    """Пройти задание до конца и сдать его — чтобы добраться до следующего."""
    await take(db, player, code)
    one = get_quest(code)
    if one.by_event:
        await notice(db, player, one.mark)
    if one.goal is Goal.QUIZ:
        for step, question in enumerate(QUIZ):
            await answer(db, player, step, question.answer)
    await hand(db, player, code)


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


# ---------- раздача ----------


def test_the_bartender_keeps_ten_quests_and_words_for_each():
    """Десять заданий, и у каждого свои слова: без них не поговоришь."""
    assert len(QUESTS) == 10 and len(ORDER) == 10
    assert sorted(TALKS) == sorted(quest.code for quest in QUESTS)
    for quest in QUESTS:
        talk = TALKS[quest.code]
        assert talk.offer and talk.hint and talk.done
        assert talk.offer_yes and talk.offer_no and talk.hand
        assert quest.exp > 0 and quest.task


def test_the_bar_hands_out_quests():
    """Бар раздаёт задания, и дверь на карте открывает бармена."""
    place = get_location(BAR)
    assert place is not None and place.allows(Service.TAVERN)
    assert place.services[0] is Service.TAVERN, "дверь должна открывать бармена"
    assert place.allows(Service.WORK), "смена за стойкой никуда не делась"


def test_the_quiz_asks_three_questions_with_one_answer_each():
    """Вопросов про PRO три, и у каждого один верный ответ из трёх."""
    assert len(QUIZ) == 3
    for question in QUIZ:
        assert len(question.options) == 3
        assert 0 <= question.answer < 3
        assert question.right and question.wrong


def test_the_ladder_goes_one_step_at_a_time():
    """Следующее задание — первое несданное, и только когда руки пусты."""
    assert next_quest(set()).code == ORDER[0]
    assert next_quest({ORDER[0]}).code == ORDER[1]
    assert next_quest(set(), taken=ORDER[0]) is None
    assert next_quest(set(ORDER)) is None


# ---------- взять и сдать ----------


async def test_the_first_quest_is_taken_and_handed_over(db):
    """Первое задание: взял, выиграл бой, сдал — и получил опыт."""
    player = await enlist(db)
    exp = player.total_exp

    said = await take(db, player, "ring")
    assert "Первая кровь" in said
    log = await journal(db, player)
    assert log["ring"].taken and not log["ring"].marks

    # Пока город ничего не видел, бармен не принимает
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "ring")

    await notice(db, player, "fight")
    said = await hand(db, player, "ring")
    assert "+50 опыта" in said
    assert player.total_exp == exp + 50
    log = await journal(db, player)
    assert log["ring"].done and log["ring"].done_at > 0


async def test_one_quest_at_a_time_and_no_seconds(db):
    """Второе задание не дают, пока на руках первое; сданное — не дают вовсе."""
    player = await enlist(db)
    await take(db, player, "ring")

    with pytest.raises(QuestError, match="Сперва доделай"):
        await take(db, player, "pro")
    assert await offered(db, player) is None

    await notice(db, player, "fight")
    await hand(db, player, "ring")
    assert (await offered(db, player)).code == "pro"
    with pytest.raises(QuestError, match="уже сдал"):
        await take(db, player, "ring")
    # И через голову лестницы не перепрыгнуть
    with pytest.raises(QuestError, match="пока не предлагал"):
        await take(db, player, "bank")


async def test_quests_are_taken_and_handed_at_the_bar(db):
    """У стойки — значит у стойки: с ринга заданий не берут."""
    player = await enlist(db, make_player(location=CLUB))
    with pytest.raises(QuestError, match="в Баре"):
        await take(db, player, "ring")

    player.location = BAR
    await take(db, player, "ring")
    await notice(db, player, "fight")
    player.location = CLUB
    with pytest.raises(QuestError, match="в Баре"):
        await hand(db, player, "ring")
    with pytest.raises(QuestError, match="в Баре"):
        await answer(db, player, 0, 0)


# ---------- что город видит сам ----------


async def test_the_door_opens_the_quest(db):
    """Задание «сходи и посмотри» закрывается самой дверью."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")

    # Чужая дверь задание не закрывает
    await notice_visit(db, player, Service.CLOTHES)
    assert not (await journal(db, player))["pro"].marks

    await notice_visit(db, player, Service.PREMIUM)
    assert "visit:premium" in (await journal(db, player))["pro"].marks


async def test_the_bartender_asks_three_questions_about_pro(db):
    """Про PRO спрашивают после похода, и ошибаться можно сколько угодно."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")

    with pytest.raises(QuestError, match="сходи и посмотри"):
        await answer(db, player, 0, QUIZ[0].answer)
    await notice_visit(db, player, Service.PREMIUM)

    # Неверный ответ бармен поправляет и спрашивает снова
    wrong = (QUIZ[0].answer + 1) % 3
    right, said = await answer(db, player, 0, wrong)
    assert right is False and said == QUIZ[0].wrong
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "pro")

    for step, question in enumerate(QUIZ):
        right, said = await answer(db, player, step, question.answer)
        assert right is True and said == question.right
    assert QUIZ_DONE in (await journal(db, player))["pro"].marks

    said = await hand(db, player, "pro")
    assert "+50 опыта" in said


async def test_the_knuckles_are_bought_and_handed_over(db):
    """Третье задание: аванс вперёд, кастет бармену, награда в руки."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await walk(db, player, "pro")

    purse = player.credits
    said = await take(db, player, "knuckles")
    assert "80" in said and player.credits == purse + 80

    # Без кастета бармен не примет
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "knuckles")

    owned = await db.add_gear(player.user_id, "knuckles")
    player.gear = await db.list_gear(player.user_id)
    purse = player.credits
    said = await hand(db, player, "knuckles")
    assert "+100 💰" in said
    # Награда — сотня; уровень может добавить сверху своё, и это не беда
    assert player.credits >= purse + 100
    # Кастет ушёл под стойку
    assert owned.id not in [one.id for one in player.gear]
    assert await db.list_gear(player.user_id) == []


async def test_the_state_quests_watch_the_fighter(db):
    """Счёт, работа и запись в автошколу бармен видит сам."""
    player = await enlist(db)
    for code in ("ring", "pro", "knuckles"):
        if code == "knuckles":
            await take(db, player, code)
            await db.add_gear(player.user_id, "knuckles")
            player.gear = await db.list_gear(player.user_id)
            await hand(db, player, code)
            continue
        await walk(db, player, code)

    await take(db, player, "bank")
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "bank")
    player.account_number = "VB-1111-2222-3333"
    await hand(db, player, "bank")

    await walk(db, player, "policy")

    await take(db, player, "work")
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "work")
    player.job_code = "bartender"
    await hand(db, player, "work")

    await take(db, player, "licence")
    with pytest.raises(QuestError, match="сделано не всё"):
        await hand(db, player, "licence")
    player.school = School(course_at=now_ts())
    purse = player.credits
    said = await hand(db, player, "licence")
    assert "+300 💰" in said and player.credits >= purse + 300


async def test_the_last_quests_give_the_knuckles_and_the_pass(db):
    """Девятое даёт кастет на продажу, десятое — абонемент в зал."""
    player = await enlist(db)
    done = {quest.code for quest in QUESTS[:8]}
    for code in sorted(done, key=ORDER.index):
        await db.take_quest(player.user_id, code, now_ts())
        await db.finish_quest(player.user_id, code, now_ts())

    said = await take(db, player, "market")
    assert "на стойку" in said
    assert [one.code for one in player.gear] == ["knuckles"]
    await notice(db, player, "market")
    await hand(db, player, "market")

    moment = now_ts()
    said = await take(db, player, "gym", now=moment)
    assert "Абонемент" in said and player.gym_until > moment
    await notice(db, player, "train")
    said = await hand(db, player, "gym")
    assert "+100 опыта" in said
    assert await offered(db, player) is None, "лестница кончилась"


# ---------- разговор ----------


async def test_the_bartender_says_what_the_journal_says(db):
    """Что бармен говорит, решает журнал: предложил, ждёт, принимает."""
    player = await enlist(db)

    body = build_bar(player, await journal(db, player))
    assert body["talk"]["kind"] == "offer" and body["npc"]["mark"] == "❗"
    assert body["talk"]["quest"]["code"] == "ring"
    assert [one["code"] for one in body["talk"]["replies"]] == ["take", "later"]
    assert "Новенький" in body["talk"]["text"], "знакомство — один раз"

    await take(db, player, "ring")
    body = build_bar(player, await journal(db, player))
    assert body["talk"]["kind"] == "wait" and body["npc"]["mark"] == "🕓"
    assert body["quests"][0]["mark"] == "🕓"

    await notice(db, player, "fight")
    body = build_bar(player, await journal(db, player))
    assert body["talk"]["kind"] == "ready" and body["npc"]["mark"] == "✅"
    assert body["talk"]["replies"][0]["code"] == "hand"

    await hand(db, player, "ring")
    body = build_bar(player, await journal(db, player))
    assert body["talk"]["kind"] == "offer"
    assert body["talk"]["quest"]["code"] == "pro"
    assert "Новенький" not in body["talk"]["text"], "знакомятся один раз"
    assert body["quests"][0]["mark"] == "✅" and body["done"] == 1


async def test_the_quiz_stands_in_the_talk(db):
    """Вопрос про PRO бармен задаёт сам, как только боец сходил посмотреть."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)

    body = build_bar(player, await journal(db, player))
    talk = body["talk"]
    assert talk["kind"] == "quiz" and talk["step"] == 0 and talk["total"] == 3
    assert [one["code"] for one in talk["replies"]] == [
        "answer", "answer", "answer", "bye"
    ]
    assert talk["text"] == QUIZ[0].text

    await answer(db, player, 0, QUIZ[0].answer)
    body = build_bar(player, await journal(db, player))
    assert body["talk"]["step"] == 1, "отвеченное не переспрашивают"


async def test_the_journal_lists_every_quest(db):
    """Список заданий: сданные с галочкой, взятое с часами, прочие без."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")

    rows = build_bar(player, await journal(db, player))["quests"]
    assert len(rows) == 10
    assert [row["number"] for row in rows] == list(range(1, 11))
    assert rows[0]["state"] == "done" and rows[0]["mark"] == "✅"
    assert rows[1]["state"] == "taken" and rows[1]["mark"] == "🕓"
    assert rows[2]["state"] == "soon" and rows[2]["mark"] == ""
    assert rows[0]["task"] and rows[0]["exp"] == 50


# ---------- ручки ----------


async def test_the_bar_answers_only_at_the_bar(client, db):
    """Ручка бара проверяет место сама."""
    player = make_player(location=CLUB)
    await db.save_player(player)
    assert (await client.get("/api/bar", headers=headers())).status == 409

    player.location = BAR
    await db.save_player(player)
    answer_ = await client.get("/api/bar", headers=headers())
    assert answer_.status == 200
    body = await answer_.json()
    assert body["npc"]["picture"].endswith("/npc/bartender.png")
    assert body["total"] == 10 and body["done"] == 0


async def test_the_whole_first_quest_walks_through_the_app(client, db):
    """Взять задание, выиграть бой и сдать — через мини-апп."""
    player = make_player()
    await db.save_player(player)

    got = await client.post(
        "/api/bar", headers=headers(), json={"action": "take", "quest": "ring"}
    )
    assert got.status == 200, await got.text()
    body = await got.json()
    assert "Первая кровь" in body["said"]
    assert body["talk"]["kind"] == "wait" and body["npc"]["mark"] == "🕓"

    # Город видит победу — и бармен меняет лицо
    fresh = await db.get_player(42)
    await notice(db, fresh, "fight")
    body = await (await client.get("/api/bar", headers=headers())).json()
    assert body["talk"]["kind"] == "ready"

    got = await client.post(
        "/api/bar", headers=headers(), json={"action": "hand", "quest": "ring"}
    )
    assert got.status == 200, await got.text()
    body = await got.json()
    assert "+50 опыта" in body["said"]
    assert body["talk"]["quest"]["code"] == "pro"
    assert body["done"] == 1

    # Журнал открывается откуда угодно: он у бойца с собой
    away = await db.get_player(42)
    away.location = CLUB
    await db.save_player(away)
    body = await (await client.get("/api/quests", headers=headers())).json()
    assert body["total"] == 10 and body["done"] == 1
    assert body["quests"][0]["mark"] == "✅"
    # А вот разговор — только у стойки
    assert (await client.get("/api/bar", headers=headers())).status == 409

    away.location = BAR
    await db.save_player(away)
    got = await client.post(
        "/api/bar", headers=headers(), json={"action": "выпить"}
    )
    assert got.status == 400


async def test_the_quiz_walks_through_the_app(client, db):
    """Вопросы про PRO: неверный ответ поправляют, верные закрывают задание."""
    player = make_player()
    await db.save_player(player)
    await db.take_quest(42, "ring", now_ts())
    await db.finish_quest(42, "ring", now_ts())

    await client.post(
        "/api/bar", headers=headers(), json={"action": "take", "quest": "pro"}
    )
    # Сходили в элитный магазин: ручка лавки сама расскажет бармену
    player.location = "premium_shop"
    await db.save_player(player)
    place = get_location("premium_shop")
    assert place is not None and place.allows(Service.PREMIUM)
    await client.get("/api/magic", headers=headers())

    player.location = BAR
    await db.save_player(player)
    body = await (await client.get("/api/bar", headers=headers())).json()
    assert body["talk"]["kind"] == "quiz"

    wrong = (QUIZ[0].answer + 1) % 3
    got = await client.post(
        "/api/bar",
        headers=headers(),
        json={"action": "answer", "step": 0, "option": wrong},
    )
    assert got.status == 200
    assert (await got.json())["said"] == QUIZ[0].wrong

    for step, question in enumerate(QUIZ):
        got = await client.post(
            "/api/bar",
            headers=headers(),
            json={"action": "answer", "step": step, "option": question.answer},
        )
        assert got.status == 200, await got.text()
    body = await got.json()
    assert body["talk"]["kind"] == "ready"
