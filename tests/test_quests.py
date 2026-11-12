"""Квесты бармена: двадцать заданий, с которых начинается город.

Проверяем семь вещей, в таком порядке важности:

1. **Лестница и главы.** Берут по одному и по порядку, глава открыта
   уровнем, сданное не предлагают заново, и держит это сервер.
2. **Проверяет город, а не слово.** Задание закрывается событием или
   состоянием бойца — сказать «я сходил» и получить опыт нельзя. Событие
   до взятия не считается, а состояние считается уже выполненным.
3. **Деньги и вещи.** Аванс приходит при взятии, награда — при сдаче, а
   то, что бармен просил принести, он забирает — ровно одно.
4. **Разговор.** Бармен говорит то, что положено по состоянию:
   предлагает, ждёт, спрашивает, принимает работу, зовёт подрасти.
5. **Вопросы.** Набор у каждого задания свой, ошибка ничего не стоит,
   повтор ничего не задваивает.
6. **Место.** Задания берут и сдают у стойки: спрятанная кнопка
   обходится запросом мимо интерфейса.
7. **Перенос.** Первая версия была из десяти заданий одной лентой;
   сданные остаются сданными, активное доделывается по старой цене.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.content.bartender import TALKS, quiz_of
from bot.database import Database
from bot.game.classes import Stats
from bot.game.driving import School
from bot.game.health import now_ts
from bot.game.locations import Service, get_location
from bot.game.quests import (
    ARMOR_ZONES,
    CHAPTERS,
    OLD_CODES,
    OLD_MARK,
    OLD_REWARD,
    ORDER,
    QUESTS,
    Goal,
    chapter_quests,
    get_quest,
    given_mark,
    locked_by,
    market_mark,
    next_quest,
    quiz_done_mark,
    quiz_marks,
    quiz_open,
    reward_of,
    ring_mark,
)
from bot.models import Player
from bot.quest_service import (
    QuestError,
    answer,
    hand,
    journal,
    locked_level,
    notice,
    notice_seen,
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
    level: int = 5,
) -> Player:
    return Player(
        user_id=user_id, nickname=nickname, class_code="warrior", level=level,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )


async def enlist(db: Database, player: Player | None = None) -> Player:
    player = player or make_player()
    await db.save_player(player)
    return player


async def close_need(db: Database, player: Player, code: str, need) -> None:
    """Закрыть одно условие задания — как это сделал бы город."""
    if need.goal is Goal.RING:
        await notice(db, player, ring_mark(True))
    elif need.goal is Goal.ITEM:
        await db.add_gear(player.user_id, need.target)
        player.gear = await db.list_gear(player.user_id)
    elif need.goal is Goal.ACCOUNT:
        player.account_number = "0001"
        await db.save_player(player)
    elif need.goal is Goal.JOB:
        player.job_code = need.target
        await db.save_player(player)
    elif need.goal is Goal.SCHOOL:
        player.school = School(course_at=now_ts())
        await db.save_player(player)
    elif need.goal is Goal.ARMOR:
        await wear_armor(db, player)
    elif need.goal is Goal.MARKET:
        marks = set((await journal(db, player))[code].marks)
        item_id = next(
            int(one.split(":")[1]) for one in marks if one.startswith("given:")
        )
        await notice(db, player, market_mark(item_id))
    elif need.goal is Goal.SEEN:
        await notice_seen(db, player, need.target)
    elif need.goal is Goal.PLACE:
        await notice(db, player, f"place:{need.target}")
    elif need.goal is Goal.VISIT:
        await notice_visit(db, player, Service(need.target))
    else:
        await notice(db, player, need.mark)


async def wear_armor(db: Database, player: Player) -> None:
    """Надеть брони на три разные зоны — по-настоящему, а не отметкой."""
    from bot.content.items import ITEMS
    from bot.game.classes import Zone
    from bot.inventory_service import equip

    covered: set[Zone] = set()
    for item in ITEMS:
        if item.armor_max <= 0 or not item.zones:
            continue
        if set(item.zones) <= covered:
            continue
        owned = await db.add_gear(player.user_id, item.code)
        player.gear = await db.list_gear(player.user_id)
        await equip(db, player, owned.id)
        covered |= set(item.zones)
        if len(covered) >= ARMOR_ZONES:
            break
    player.gear = await db.list_gear(player.user_id)


async def walk(db: Database, player: Player, code: str) -> None:
    """Пройти задание до конца и сдать — чтобы добраться до следующего."""
    await take(db, player, code)
    quest = get_quest(code)
    for need in quest.needs:
        await close_need(db, player, code, need)
    # Вопросы задают не всегда: устроившемуся барменом их не задают
    # вовсе, и спрашивать за него нечего
    marks = set((await journal(db, player))[code].marks)
    if quiz_open(quest, marks, player):
        for step, question in enumerate(quiz_of(quest.quiz)):
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


def test_the_bartender_keeps_twenty_quests_five_to_a_chapter():
    """Двадцать заданий, четыре главы по пять, и ни одного повтора."""
    assert len(QUESTS) == 20 and len(ORDER) == 20
    assert len(set(ORDER)) == 20, "код задания встречается дважды"
    assert len(CHAPTERS) == 4
    for number, chapter in enumerate(CHAPTERS, start=1):
        assert chapter.level == number, "глава на уровень"
        assert len(chapter_quests(chapter.code)) == 5, chapter.code
    # Главы идут подряд, без дыр по уровню
    assert [one.level for one in CHAPTERS] == [1, 2, 3, 4]


def test_all_ten_quests_of_the_first_version_are_still_here():
    """Старые десять сохранены: их сдавали, и выкинуть их нельзя."""
    assert OLD_CODES <= set(ORDER)
    assert len(OLD_CODES) == 10
    for code in OLD_CODES:
        assert get_quest(code) is not None, code
    assert set(OLD_REWARD) == OLD_CODES


def test_every_quest_has_words_a_reward_and_something_to_do():
    """У каждого задания свои слова, награда и хотя бы одно условие."""
    assert sorted(TALKS) == sorted(quest.code for quest in QUESTS)
    for quest in QUESTS:
        talk = TALKS[quest.code]
        assert talk.offer and talk.hint and talk.done, quest.code
        assert quest.exp > 0 and quest.task, quest.code
        assert quest.needs, f"{quest.code}: нечего делать"
        assert 1 <= quest.min_level <= 4, quest.code


def test_every_quiz_set_belongs_to_a_quest_and_has_one_right_answer():
    """Набор вопросов есть у того задания, которое на него ссылается."""
    for quest in QUESTS:
        if not quest.quiz:
            continue
        questions = quiz_of(quest.quiz)
        assert questions, quest.code
        for question in questions:
            assert len(question.options) == 3
            assert 0 <= question.answer < 3
            assert question.right and question.wrong


def test_the_bar_hands_out_quests():
    """Бар раздаёт задания, и дверь на карте открывает бармена."""
    place = get_location(BAR)
    assert place is not None and place.allows(Service.TAVERN)
    assert place.services[0] is Service.TAVERN, "дверь должна открывать бармена"
    assert place.allows(Service.WORK), "смена за стойкой никуда не делась"


def test_the_bartender_does_not_promise_pro_an_insurance():
    """Бармен не обещает того, чего город не даёт.

    В первой версии он говорил, что подписка держит полис. На самом
    деле полис входит в PREMIUM, а не в PRO, — и реплика, которая врёт
    про правила, хуже отсутствия реплики: по ней тратят деньги.
    """
    from bot.game.pro import PREMIUM_TIER, PRO_TIER

    assert PRO_TIER.policy is False and PREMIUM_TIER.policy is True
    about = quiz_of("pro")[1]
    assert "страховк" in about.text.lower()
    assert about.options[about.answer] == "PREMIUM"
    assert "PREMIUM" in about.right and "не в PRO" in about.right


def test_the_gym_questions_take_their_numbers_from_the_gym():
    """Числа в вопросах — те же, что считают игру, а не набранные руками."""
    from bot.game.gym import SLOT_LIMIT, TRAINING_MINUTES

    span, seats = quiz_of("gym")
    assert span.options[span.answer] == f"{TRAINING_MINUTES} минут"
    assert seats.options[seats.answer] == str(SLOT_LIMIT)


def test_the_university_question_has_exactly_one_right_answer():
    """Отвлекающие направления выдуманы, а не взяты из города.

    В постановке стояли «Кадетский корпус» и «Медицинский колледж» —
    но оба в городе есть, и вопрос с тремя верными ответами проверял бы
    везение, а не внимательность.
    """
    from bot.game.study import COURSES

    titles = {one.title for one in COURSES}
    question = quiz_of("study")[0]
    right = question.options[question.answer]
    assert right in titles, "верный ответ должен быть настоящим направлением"
    for number, option in enumerate(question.options):
        if number == question.answer:
            continue
        assert option not in titles, f"«{option}» есть в университете"


def test_no_quest_reply_counts_quests_in_order_words():
    """Порядковых слов в репликах нет: заданий двадцать, и они главами."""
    banned = ("третье", "девятое", "последнее", "четвёртое", "пятое", "шестое")
    for code, talk in TALKS.items():
        said = (talk.offer + talk.hint + talk.done).lower()
        for word in banned:
            assert word not in said, f"{code}: «{word}»"


def test_no_quest_reply_promises_a_payback_or_a_discount():
    """Бармен не обещает окупаемости покупки и неподтверждённых скидок."""
    banned = ("окупает", "впятеро", "со скидк", "верну", "сдачу")
    for code, talk in TALKS.items():
        said = (talk.offer + talk.hint + talk.done).lower()
        for word in banned:
            assert word not in said, f"{code}: «{word}»"


# ---------- лестница и уровень ----------


def test_the_ladder_goes_one_step_at_a_time():
    """Следующее задание — первое несданное, и только когда руки пусты."""
    assert next_quest(set(), level=4).code == ORDER[0]
    assert next_quest({ORDER[0]}, level=4).code == ORDER[1]
    # Пока одно на руках, второго не дают
    assert next_quest(set(), taken=ORDER[0], level=4) is None
    # Все сданы — давать нечего
    assert next_quest(set(ORDER), level=4) is None


def test_a_chapter_opens_with_the_level_and_not_before():
    """Глава закрыта уровнем, и бармен говорит, до какого расти."""
    done = set(chapter_quests("city")[i].code for i in range(5))
    # Первая глава сдана, уровень всё ещё первый
    assert next_quest(done, level=1) is None
    assert locked_by(done, level=1) == 2
    # Подрос — дают дальше
    assert next_quest(done, level=2).code == chapter_quests("work")[0].code
    assert locked_by(done, level=2) == 0


def test_the_first_chapter_is_open_from_the_first_level():
    """На первом уровне бармену уже есть что дать."""
    assert next_quest(set(), level=1).code == ORDER[0]
    assert locked_by(set(), level=1) == 0


def test_nothing_is_locked_once_everything_is_done():
    """Все двадцать сданы — расти больше незачем, и это не замок."""
    assert locked_by(set(ORDER), level=1) == 0
    assert next_quest(set(ORDER), level=1) is None


async def test_the_server_refuses_a_quest_closed_by_level(db):
    """Уровень проверяет сервер, а не спрятанная кнопка.

    Спрятанная обходится запросом мимо интерфейса, и тогда главы —
    украшение.
    """
    player = await enlist(db, make_player(level=1))
    for quest in chapter_quests("city"):
        await walk(db, player, quest.code)

    assert await offered(db, player) is None
    assert await locked_level(db, player) == 2
    with pytest.raises(QuestError) as oops:
        await take(db, player, "work")
    assert "2 уровня" in str(oops.value)


async def test_growing_up_opens_the_next_chapter(db):
    """Подрос — то же задание берётся без разговоров."""
    player = await enlist(db, make_player(level=1))
    for quest in chapter_quests("city"):
        await walk(db, player, quest.code)

    player.level = 2
    await db.save_player(player)
    assert (await offered(db, player)).code == "work"
    assert await locked_level(db, player) == 0
    assert "взято" in (await take(db, player, "work")).lower()


async def test_a_quest_out_of_turn_is_refused(db):
    """Берут по порядку: третье через голову первого не дают."""
    player = await enlist(db)
    with pytest.raises(QuestError) as oops:
        await take(db, player, "bank")
    assert "не предлагал" in str(oops.value)


async def test_a_fighter_above_the_last_chapter_keeps_his_quests(db):
    """Пятый уровень не отменяет незавершённые задания.

    Квесты не блокируют повышение, а повышение не отменяет квестов:
    это разные лестницы.
    """
    player = await enlist(db, make_player(level=9))
    assert (await offered(db, player)).code == ORDER[0]
    await take(db, player, ORDER[0])
    player.level = 20
    await db.save_player(player)
    assert (await journal(db, player))[ORDER[0]].taken
    await close_need(db, player, ORDER[0], get_quest(ORDER[0]).needs[0])
    assert "сдано" in (await hand(db, player, ORDER[0])).lower()


# ---------- город проверяет сам ----------


async def test_an_event_before_the_quest_was_taken_does_not_count(db):
    """Сходил до того, как взял, — зачёта нет: задание про «после».

    Иначе первое же поручение закрывалось бы тем, что боец успел
    сделать за прошлый вечер.
    """
    player = await enlist(db)
    await walk(db, player, "ring")
    # Заходим в элитный магазин ещё до того, как взяли задание про него
    await notice_visit(db, player, Service.PREMIUM)

    await take(db, player, "pro")
    log = await journal(db, player)
    assert "visit:premium" not in log["pro"].marks
    with pytest.raises(QuestError) as oops:
        await hand(db, player, "pro")
    assert "сделано не всё" in str(oops.value)


async def test_a_standing_state_counts_even_if_it_was_already_there(db):
    """Счёт, открытый заранее, годится: это состояние, а не событие.

    «Если он уже есть — покажи, повторно открывать ничего не нужно».
    """
    player = await enlist(db)
    player.account_number = "0001"
    await db.save_player(player)
    await walk(db, player, "ring")
    await walk(db, player, "pro")

    await take(db, player, "bank")
    assert "сдано" in (await hand(db, player, "bank")).lower()


async def test_an_event_the_quest_does_not_wait_for_is_not_written_down(db):
    """Чужая отметка в журнал не ложится: иначе он зарастёт.

    По журналу потом читают, что боец делал по этому заданию, и чужие
    события в нём — мусор, который однажды закроет не то.
    """
    player = await enlist(db)
    await take(db, player, "ring")
    await notice_visit(db, player, Service.BANK)
    await notice(db, player, "train")

    log = await journal(db, player)
    assert log["ring"].marks == frozenset(), log["ring"].marks


# ---------- ринг ----------


async def test_the_ring_counts_a_loss_too(db):
    """Поражение закрывает задание: оно про то, чтобы выйти на ринг."""
    player = await enlist(db)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(False))

    said = await hand(db, player, "ring")
    assert "сдано" in said.lower()
    assert "за победу" not in said, "доплата за проигранный бой"
    assert (await db.get_player(42)).credits == 500, "доплата всё равно пришла"


async def test_the_ring_pays_a_bonus_for_a_win(db):
    """За победу бармен доплачивает — и говорит об этом отдельно."""
    quest = get_quest("ring")
    player = await enlist(db)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(True))

    said = await hand(db, player, "ring")
    assert f"{quest.win_bonus} за победу" in said
    assert (await db.get_player(42)).credits == 500 + quest.win_bonus


async def test_a_draw_on_the_ring_closes_the_quest_without_a_bonus(db):
    """Ничья — тоже завершённый бой, но не победа."""
    player = await enlist(db)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(None))

    said = await hand(db, player, "ring")
    assert "сдано" in said.lower() and "за победу" not in said


async def test_a_late_win_does_not_replace_a_counted_loss(db):
    """Зачёт даёт первый бой: поздняя победа бонуса не приносит.

    Иначе бонус за победу брался бы перебором: проиграл — подрался ещё
    раз, и так до победы, не беря нового задания.
    """
    player = await enlist(db)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(False))
    # Второй бой, уже выигранный, — но зачтён был первый
    await notice(db, player, ring_mark(True))

    log = await journal(db, player)
    assert ring_mark(False) in log["ring"].marks
    assert ring_mark(True) not in log["ring"].marks, "перезаписали исход"
    said = await hand(db, player, "ring")
    assert "за победу" not in said


async def test_a_fight_that_never_finished_does_not_count(db):
    """Отменённый бой не событие: о нём город и не рассказывает."""
    player = await enlist(db)
    await take(db, player, "ring")
    # Бой не доехал до итога — отметки нет вовсе
    log = await journal(db, player)
    assert not log["ring"].marks
    with pytest.raises(QuestError):
        await hand(db, player, "ring")


async def test_only_a_fist_duel_tells_the_bartender_about_the_ring(db):
    """В журнал ринга пишет только кулачная дуэль.

    Рейды, нападения и бои с NPC через дуэльную службу не идут вовсе, а
    бой с оружием — не кулачный: задание про кулаки.
    """
    import inspect

    from bot import duel_service

    source = inspect.getsource(duel_service)
    assert "ring_mark(" in source
    assert "FightMode.FIST" in source
    # И групповой бой про ринг молчит: задание про бой один на один
    from bot import battle_service

    assert "ring_mark(" not in inspect.getsource(battle_service)


# ---------- вопросы ----------


async def test_the_bartender_asks_only_after_the_errand(db):
    """Сперва сходить, потом отвечать: вопросы про то, что видно."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")

    with pytest.raises(QuestError) as oops:
        await answer(db, player, 0, 1)
    assert "сходи и посмотри" in str(oops.value)


async def test_a_wrong_answer_costs_nothing_and_can_be_repeated(db):
    """Ошибка без штрафа и с пояснением, попробовать можно снова."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)

    question = quiz_of("pro")[0]
    wrong = next(
        number for number in range(3) if number != question.answer
    )
    purse = (await db.get_player(42)).credits
    ok, said = await answer(db, player, 0, wrong)
    assert not ok and "Не совсем" in said and "Попробуй ещё раз" in said
    assert (await db.get_player(42)).credits == purse, "за ошибку списали"

    ok, said = await answer(db, player, 0, question.answer)
    assert ok and "Верно" in said


async def test_a_right_answer_is_remembered_once(db):
    """Верный ответ запоминается, и дважды его не зачтут."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)

    for _ in range(3):
        await answer(db, player, 0, quiz_of("pro")[0].answer)
    log = await journal(db, player)
    assert quiz_marks("pro", 0) in log["pro"].marks
    # Весь набор ещё не отвечен: отметки о конце разговора нет
    assert quiz_done_mark("pro") not in log["pro"].marks


async def test_the_bonus_for_answers_is_paid_once_at_hand_in(db):
    """Награда за ответы приходит один раз при сдаче, а не за попытку."""
    quest = get_quest("pro")
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)
    for step, question in enumerate(quiz_of("pro")):
        # Каждый вопрос — с ошибкой и потом верно
        wrong = next(n for n in range(3) if n != question.answer)
        await answer(db, player, step, wrong)
        await answer(db, player, step, question.answer)

    before = (await db.get_player(42)).credits
    await hand(db, player, "pro")
    assert (await db.get_player(42)).credits == before + quest.credits


async def test_a_question_from_another_quest_is_refused(db):
    """Набор берётся по активному заданию, а не по номеру шага.

    Иначе ответ про зал закрывал бы вопрос про полицию: номера шагов у
    наборов совпадают.
    """
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)

    # У набора про PRO три вопроса, у остальных — один или два
    with pytest.raises(QuestError) as oops:
        await answer(db, player, 9, 0)
    assert "Такого вопроса не было" in str(oops.value)

    log = await journal(db, player)
    # Отметки чужого набора в журнале нет и быть не может
    assert quiz_marks("police", 0) not in log["pro"].marks


async def test_an_answer_outside_the_options_is_refused(db):
    """Вариант проверяет сервер: за пределами списка — отказ."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)

    for option in (-1, 3, 99):
        with pytest.raises(QuestError) as oops:
            await answer(db, player, 0, option)
        assert "не предлагал" in str(oops.value)


async def test_answers_survive_a_restart(db):
    """Отметки лежат в базе: перезапуск разговор не сбрасывает."""
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)
    await answer(db, player, 0, quiz_of("pro")[0].answer)

    # Перечитываем бойца с нуля — как после перезапуска бота
    fresh = await db.get_player(42)
    log = await journal(db, fresh)
    assert quiz_marks("pro", 0) in log["pro"].marks
    # И следующий вопрос — второй, а не первый заново
    from bot.webapp.bar import talk_node

    node = talk_node(fresh, log)
    assert node["kind"] == "quiz" and node["step"] == 1


async def test_the_bartender_asks_nothing_without_a_quest_in_hand(db):
    player = await enlist(db)
    with pytest.raises(QuestError) as oops:
        await answer(db, player, 0, 0)
    assert "ни о чём не спрашивал" in str(oops.value)


# ---------- работа: обход для занятого ----------


async def test_a_bartender_by_trade_passes_the_shift_quest_at_once(db):
    """Устроился барменом — этого и хотели, вопросов не будет."""
    player = await enlist(db, make_player(level=2))
    for quest in chapter_quests("city"):
        await walk(db, player, quest.code)
    player.job_code = "bartender"
    await db.save_player(player)

    await take(db, player, "work")
    log = await journal(db, player)
    from bot.webapp.bar import talk_node

    assert talk_node(player, log)["kind"] == "ready"
    assert "сдано" in (await hand(db, player, "work")).lower()


async def test_a_fighter_with_another_job_studies_the_vacancy_instead(db):
    """Увольняться ради поручения незачем: изучи вакансию и расскажи."""
    player = await enlist(db, make_player(level=2))
    for quest in chapter_quests("city"):
        await walk(db, player, quest.code)
    player.job_code = "cashier"
    await db.save_player(player)

    await take(db, player, "work")
    # Пока не был в HR — сдавать нечего
    with pytest.raises(QuestError):
        await hand(db, player, "work")
    await notice_visit(db, player, Service.HIRE)
    # Теперь бармен спрашивает
    ok, _ = await answer(db, player, 0, quiz_of("work")[0].answer)
    assert ok
    assert "сдано" in (await hand(db, player, "work")).lower()
    # И работу бойцу не меняли
    assert (await db.get_player(42)).job_code == "cashier"


async def test_a_fighter_without_a_job_must_actually_get_hired(db):
    """Безработному обход не положен: ему как раз и нужна работа."""
    player = await enlist(db, make_player(level=2))
    for quest in chapter_quests("city"):
        await walk(db, player, quest.code)

    await take(db, player, "work")
    await notice_visit(db, player, Service.HIRE)
    # Вопросов ему не задают: обход открыт только занятым
    with pytest.raises(QuestError) as oops:
        await answer(db, player, 0, 0)
    assert "сходи и посмотри" in str(oops.value)
    with pytest.raises(QuestError):
        await hand(db, player, "work")

    player.job_code = "bartender"
    await db.save_player(player)
    assert "сдано" in (await hand(db, player, "work")).lower()


# ---------- броня ----------


async def test_three_armoured_zones_close_the_armour_quest(db):
    """Считаются зоны по настоящей защите, а не число вещей."""
    from bot.game.quests import armor_zones

    player = await enlist(db, make_player(level=3))
    for chapter in ("city", "work"):
        for quest in chapter_quests(chapter):
            await walk(db, player, quest.code)
    await walk(db, player, "knuckles")
    await walk(db, player, "ring_gift")

    await take(db, player, "armor_intro")
    assert armor_zones(player) < ARMOR_ZONES
    with pytest.raises(QuestError):
        await hand(db, player, "armor_intro")

    await wear_armor(db, player)
    assert armor_zones(player) >= ARMOR_ZONES
    assert "сдано" in (await hand(db, player, "armor_intro")).lower()


def test_a_weapon_is_not_armour():
    """Оружие и прибавки к характеристикам зонами не считаются."""
    from bot.content.items import ITEMS

    weapons = [one for one in ITEMS if one.slot.value == "weapon"]
    assert weapons, "в городе нет оружия — проверка потеряла смысл"
    for one in weapons:
        assert one.armor_max == 0, f"{one.code} даёт броню"


# ---------- деньги и вещи ----------


async def test_the_advance_comes_once_at_take_and_not_again(db):
    """Аванс выдаётся при взятии и ровно один раз."""
    quest = get_quest("knuckles")
    player = await enlist(db, make_player(level=3))
    for chapter in ("city", "work"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)

    purse = (await db.get_player(42)).credits
    said = await take(db, player, "knuckles")
    assert f"{quest.advance} 💰 вперёд" in said
    assert (await db.get_player(42)).credits == purse + quest.advance

    # Второй раз то же задание не взять
    with pytest.raises(QuestError):
        await take(db, player, "knuckles")
    assert (await db.get_player(42)).credits == purse + quest.advance


async def test_the_bartender_takes_exactly_one_item(db):
    """Бармен забирает один экземпляр, а не всё, что принесли."""
    player = await enlist(db, make_player(level=3))
    for chapter in ("city", "work"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)

    await take(db, player, "knuckles")
    for _ in range(3):
        await db.add_gear(player.user_id, "knuckles")
    player.gear = await db.list_gear(player.user_id)
    before = sum(1 for one in player.gear if one.code == "knuckles")

    await hand(db, player, "knuckles")
    after = [
        one
        for one in await db.list_gear(player.user_id)
        if one.code == "knuckles"
    ]
    assert len(after) == before - 1


async def test_a_worn_item_does_not_count_until_it_is_taken_off(db):
    """Надетая вещь не считается: «в рюкзаке, не в руке»."""
    player = await enlist(db, make_player(level=3))
    for chapter in ("city", "work"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)

    from bot.inventory_service import equip, unequip

    await take(db, player, "knuckles")
    owned = await db.add_gear(player.user_id, "knuckles")
    player.gear = await db.list_gear(player.user_id)
    await equip(db, player, owned.id)
    with pytest.raises(QuestError) as oops:
        await hand(db, player, "knuckles")
    assert "сделано не всё" in str(oops.value)

    # Снимаем по слоту: надетую вещь служба ищет именно по нему, а
    # после `equip` слот у вещи уже стоит — перечитываем её из базы
    player.gear = await db.list_gear(player.user_id)
    worn = next(one for one in player.gear if one.is_equipped)
    await unequip(db, player, worn.slot)
    player.gear = await db.list_gear(player.user_id)
    assert "сдано" in (await hand(db, player, "knuckles")).lower()


async def test_the_market_quest_wants_the_very_knuckles_it_gave(db):
    """Выставить надо именно выданный кастет, а не любой свой.

    Общей отметки «что-то выставил» не хватает: боец мог сдать свою
    вещь, а кастет бармена оставить себе.
    """
    player = await enlist(db, make_player(level=4))
    for chapter in ("city", "work", "gear"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)

    await take(db, player, "market")
    log = await journal(db, player)
    given = next(
        int(one.split(":")[1])
        for one in log["market"].marks
        if one.startswith("given:")
    )
    # Выставили чужой кастет — зачёта нет
    other = await db.add_gear(player.user_id, "knuckles")
    await notice(db, player, market_mark(other.id))
    with pytest.raises(QuestError):
        await hand(db, player, "market")

    await notice(db, player, market_mark(given))
    assert "сдано" in (await hand(db, player, "market")).lower()


async def test_the_gym_pass_comes_with_the_quest(db):
    """Абонемент даётся при взятии и один раз."""
    player = await enlist(db, make_player(level=2))
    for one in chapter_quests("city"):
        await walk(db, player, one.code)
    for code in ("work", "study_intro", "trade_intro", "gym_intro"):
        await walk(db, player, code)

    assert (await db.get_player(42)).gym_until == 0
    said = await take(db, player, "gym")
    assert "Абонемент" in said
    assert (await db.get_player(42)).gym_until > now_ts()


async def test_an_expired_pass_does_not_cancel_the_gym_quest(db):
    """Абонемент кончился — задание осталось: его доделают с новым."""
    player = await enlist(db, make_player(level=2))
    for one in chapter_quests("city"):
        await walk(db, player, one.code)
    for code in ("work", "study_intro", "trade_intro", "gym_intro"):
        await walk(db, player, code)
    await take(db, player, "gym")

    player.gym_until = 1
    await db.save_player(player)
    assert (await journal(db, player))["gym"].taken
    await notice(db, player, "train")
    assert "сдано" in (await hand(db, player, "gym")).lower()


# ---------- двойные нажатия ----------


async def test_handing_the_same_quest_twice_pays_once(db):
    """Два нажатия «сдать» не задваивают ни опыта, ни кредитов."""
    player = await enlist(db)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(True))
    await hand(db, player, "ring")

    paid = (await db.get_player(42)).credits
    exp = (await db.get_player(42)).exp
    with pytest.raises(QuestError) as oops:
        await hand(db, player, "ring")
    assert "на руках нет" in str(oops.value)
    assert (await db.get_player(42)).credits == paid
    assert (await db.get_player(42)).exp == exp


async def test_taking_two_quests_at_once_is_refused(db):
    """Берут по одному: второе через голову первого не дают."""
    player = await enlist(db)
    await take(db, player, "ring")
    with pytest.raises(QuestError) as oops:
        await take(db, player, "pro")
    assert "доделай" in str(oops.value)


async def test_a_quest_already_done_is_not_given_again(db):
    player = await enlist(db)
    await walk(db, player, "ring")
    with pytest.raises(QuestError) as oops:
        await take(db, player, "ring")
    assert "уже сдал" in str(oops.value)


# ---------- место ----------


async def test_quests_are_taken_and_handed_at_the_counter(db):
    """Бармен стоит в Баре: взять и сдать можно только у стойки."""
    player = await enlist(db, make_player(location=CLUB))
    with pytest.raises(QuestError) as oops:
        await take(db, player, "ring")
    assert "в Баре" in str(oops.value)

    player.location = BAR
    await db.save_player(player)
    await take(db, player, "ring")
    await notice(db, player, ring_mark(True))

    player.location = CLUB
    await db.save_player(player)
    with pytest.raises(QuestError) as oops:
        await hand(db, player, "ring")
    assert "в Баре" in str(oops.value)


async def test_answers_are_given_at_the_counter_too(db):
    player = await enlist(db)
    await walk(db, player, "ring")
    await take(db, player, "pro")
    await notice_visit(db, player, Service.PREMIUM)
    player.location = CLUB
    await db.save_player(player)
    with pytest.raises(QuestError) as oops:
        await answer(db, player, 0, 0)
    assert "в Баре" in str(oops.value)


# ---------- разговор ----------


async def test_the_bartender_says_what_the_journal_says(db):
    """Реплика выводится из состояния: предлагает, ждёт, принимает."""
    player = await enlist(db)
    log = await journal(db, player)
    screen = build_bar(player, log)
    assert screen["talk"]["kind"] == "offer"
    assert screen["npc"]["mark"] == "❗"
    assert "Новое лицо" in screen["talk"]["text"]

    await take(db, player, "ring")
    screen = build_bar(player, await journal(db, player))
    assert screen["talk"]["kind"] == "wait"
    assert "Пока не всё" in screen["talk"]["text"]
    assert screen["npc"]["mark"] == "🕓"

    await notice(db, player, ring_mark(True))
    screen = build_bar(player, await journal(db, player))
    assert screen["talk"]["kind"] == "ready"
    assert screen["npc"]["mark"] == "✅"


async def test_the_bartender_sends_a_short_fighter_to_grow(db):
    """Глава кончилась раньше уровня — бармен говорит, до какого расти.

    И значка над ним при этом нет: подходить пока незачем, а звать к
    стойке зря — обман.
    """
    player = await enlist(db, make_player(level=1))
    for one in chapter_quests("city"):
        await walk(db, player, one.code)

    screen = build_bar(player, await journal(db, player))
    assert screen["talk"]["kind"] == "grow"
    assert screen["talk"]["need_level"] == 2
    assert "Достигни уровня 2" in screen["talk"]["text"]
    assert screen["npc"]["mark"] == "", "позвал к стойке, а дела нет"


async def test_the_bartender_falls_silent_when_everything_is_done(db):
    player = await enlist(db, make_player(level=4))
    for code in ORDER:
        await walk(db, player, code)
    screen = build_bar(player, await journal(db, player))
    assert screen["talk"]["kind"] == "idle"
    assert "закончились" in screen["talk"]["text"]
    assert screen["done"] == 20 and screen["total"] == 20


async def test_the_journal_shows_four_chapters_with_their_levels(db):
    """Журнал идёт главами, и у каждой свой уровень и свой счёт."""
    player = await enlist(db, make_player(level=2))
    for one in chapter_quests("city"):
        await walk(db, player, one.code)

    screen = build_bar(player, await journal(db, player))
    chapters = screen["chapters"]
    assert [one["level"] for one in chapters] == [1, 2, 3, 4]
    assert [one["done"] for one in chapters] == [5, 0, 0, 0]
    assert [one["open"] for one in chapters] == [True, True, False, False]
    assert all(one["total"] == 5 for one in chapters)


async def test_the_journal_tells_the_two_locks_apart(db):
    """«Закрыто уровнем» и «ждёт очереди» — разные состояния.

    Первое игрок исправляет ростом, второе работой, и один серый цвет
    на оба не сказал бы, что делать.
    """
    player = await enlist(db, make_player(level=1))
    screen = build_bar(player, await journal(db, player))
    rows = {one["code"]: one for one in screen["quests"]}

    assert rows["ring"]["state"] == "open"
    assert rows["pro"]["state"] == "locked_order", "второе в своей главе"
    assert rows["work"]["state"] == "locked_level"
    assert rows["work"]["min_level"] == 2
    assert rows["work"]["mark"] == "🔒"
    assert rows["pro"]["mark"] == ""


async def test_the_journal_shows_the_reward_in_parts(db):
    """Награда, бонус, аванс и вещь показываются по отдельности."""
    player = await enlist(db, make_player(level=4))
    screen = build_bar(player, await journal(db, player))
    rows = {one["code"]: one for one in screen["quests"]}

    ring = rows["ring"]
    assert ring["exp"] == 50 and ring["win_bonus"] == 10
    assert ring["credits"] == 0
    knuckles = rows["knuckles"]
    assert knuckles["advance"] == 80 and knuckles["credits"] == 50
    assert rows["market"]["gives"] == "knuckles"
    assert rows["gym"]["gym_pass"] is True


async def test_the_promised_reward_is_the_one_that_arrives(db):
    """Что обещано на экране, то и приходит в карман.

    Суммы на экране выводятся из тех же полей, по которым платят, —
    иначе однажды разойдутся.
    """
    player = await enlist(db)
    screen = build_bar(player, await journal(db, player))
    about = screen["talk"]["quest"]
    assert about["code"] == "ring"

    await take(db, player, "ring")
    await notice(db, player, ring_mark(True))
    before = await db.get_player(42)
    await hand(db, player, "ring")
    after = await db.get_player(42)

    assert after.exp - before.exp == about["exp"]
    assert after.credits - before.credits == about["credits"] + about["win_bonus"]


# ---------- ручки ----------


async def test_the_api_takes_and_hands_a_quest(client, db):
    await enlist(db)
    answer_take = await client.post(
        "/api/bar", json={"action": "take", "quest": "ring"}, headers=headers()
    )
    assert answer_take.status == 200
    body = await answer_take.json()
    assert "взято" in body["said"].lower()

    await notice(db, await db.get_player(42), ring_mark(True))
    answer_hand = await client.post(
        "/api/bar", json={"action": "hand", "quest": "ring"}, headers=headers()
    )
    assert answer_hand.status == 200
    assert "сдано" in (await answer_hand.json())["said"].lower()


async def test_the_api_refuses_a_quest_closed_by_level(client, db):
    """Уровень держит ручка, а не страница."""
    player = await enlist(db, make_player(level=1))
    for one in chapter_quests("city"):
        await walk(db, player, one.code)

    answer_take = await client.post(
        "/api/bar", json={"action": "take", "quest": "work"}, headers=headers()
    )
    assert answer_take.status == 409
    assert "2 уровня" in (await answer_take.json())["error"]


async def test_the_journal_endpoint_returns_chapters(client, db):
    await enlist(db, make_player(level=2))
    body = await (await client.get("/api/quests", headers=headers())).json()
    assert len(body["chapters"]) == 4
    assert body["total"] == 20
    assert body["level"] == 2


async def test_the_catalog_card_endpoint_checks_the_place_and_the_code(client, db):
    """Карточку каталога засчитывают по месту и настоящему коду.

    Иначе задание закрывалось бы запросом с выдуманной строкой, не
    показав игроку ни одного приёма.
    """
    from bot.content.abilities import ABILITIES

    player = await enlist(db)
    await walk(db, player, "ring")
    player.location = "martial_arts_school"
    await db.save_player(player)

    # Выдуманный код — отказ
    missed = await client.post(
        "/api/seen", json={"catalog": "dojo", "code": "нет-такого"},
        headers=headers(),
    )
    assert missed.status == 404

    # Чужой каталог — отказ
    wrong = await client.post(
        "/api/seen", json={"catalog": "луна", "code": "x"}, headers=headers()
    )
    assert wrong.status == 400

    ok = await client.post(
        "/api/seen",
        json={"catalog": "dojo", "code": ABILITIES[0].code},
        headers=headers(),
    )
    assert ok.status == 200


async def test_the_catalog_card_is_not_counted_from_the_wrong_house(client, db):
    """Каталог приёмов смотрят в школе, а не из Бара."""
    from bot.content.abilities import ABILITIES

    await enlist(db)
    answer_seen = await client.post(
        "/api/seen",
        json={"catalog": "dojo", "code": ABILITIES[0].code},
        headers=headers(),
    )
    assert answer_seen.status == 409


# ---------- перенос с первой версии ----------


async def test_a_finished_old_quest_stays_finished_and_is_not_paid_twice(db):
    """Сданное старое задание остаётся сданным и повторно не платится."""
    player = await enlist(db)
    await walk(db, player, "ring")
    paid = (await db.get_player(42)).credits

    # Разовая работа по живой базе ничего не меняет в сданном
    from bot.chores import _carry_the_quests

    await _carry_the_quests(db)
    log = await journal(db, player)
    assert log["ring"].done
    assert OLD_MARK not in log["ring"].marks, "сданное помечено переносом"
    assert (await db.get_player(42)).credits == paid
    with pytest.raises(QuestError):
        await take(db, player, "ring")


async def test_an_active_old_quest_is_paid_by_the_old_price(db):
    """Активное старое задание доделывается по той цене, что обещали.

    Боец соглашался на старые условия, и менять их задним числом нельзя
    ни вверх, ни вниз.
    """
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=3))
    for chapter in ("city", "work"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)
    await take(db, player, "knuckles")

    said = await _carry_the_quests(db)
    assert "1" in said
    log = await journal(db, player)
    assert OLD_MARK in log["knuckles"].marks

    quest = get_quest("knuckles")
    old_exp, old_credits = OLD_REWARD["knuckles"]
    assert (old_exp, old_credits) != (quest.exp, quest.credits)
    assert reward_of(quest, set(log["knuckles"].marks)) == (old_exp, old_credits)

    await db.add_gear(player.user_id, "knuckles")
    player.gear = await db.list_gear(player.user_id)
    before = await db.get_player(42)
    said = await hand(db, player, "knuckles")

    after = await db.get_player(42)
    assert after.exp - before.exp == old_exp
    assert after.credits - before.credits == old_credits
    assert f"+{old_exp} опыта" in said


async def test_a_carried_quest_is_finished_whatever_the_new_level(db):
    """Перенесённое задание доделывается независимо от новой главы.

    Его дали по прежним правилам, когда уровней у заданий не было; не
    дать его закрыть значило бы запереть бойца навсегда.
    """
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=1))
    # Задание четвёртой главы на первом уровне: так бывает только у
    # того, кто взял его до перехода на главы
    await db.take_quest(player.user_id, "licence", now_ts())
    await _carry_the_quests(db)

    player.school = School(course_at=now_ts())
    await db.save_player(player)
    said = await hand(db, player, "licence")
    assert "сдано" in said.lower()
    old_exp, old_credits = OLD_REWARD["licence"]
    assert f"+{old_exp} опыта" in said
    assert (await db.get_player(42)).credits == 500 + old_credits


async def test_after_a_carried_quest_the_new_order_starts_from_the_top(db):
    """Сдал перенесённое — дальше первое доступное по новому порядку."""
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=1))
    await db.take_quest(player.user_id, "licence", now_ts())
    await _carry_the_quests(db)
    player.school = School(course_at=now_ts())
    await db.save_player(player)
    await hand(db, player, "licence")

    assert (await offered(db, player)).code == ORDER[0]


async def test_a_carried_quiz_quest_keeps_its_finished_conversation(db):
    """Перенесённый разговор не переспрашивают.

    В первой версии набор был один на весь город, и отметка о его конце
    звалась иначе. Тому, кто уже всё рассказал, вопросы задавать заново
    незачем.
    """
    from bot.chores import _carry_the_quests
    from bot.game.quests import OLD_QUIZ_DONE

    player = await enlist(db)
    await db.take_quest(player.user_id, "pro", now_ts())
    await db.mark_quest(player.user_id, "pro", "visit:premium")
    await db.mark_quest(player.user_id, "pro", OLD_QUIZ_DONE)
    await _carry_the_quests(db)

    said = await hand(db, player, "pro")
    old_exp, _ = OLD_REWARD["pro"]
    assert f"+{old_exp} опыта" in said


async def test_a_carried_market_quest_keeps_the_old_loose_check(db):
    """У перенесённой комиссионки номера вещи нет — годится любая.

    Записывать его тогда не умели, и угадывать, какой кастет был выдан
    полгода назад, мы не станем.
    """
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=1))
    await db.take_quest(player.user_id, "market", now_ts())
    await _carry_the_quests(db)
    # Старая отметка — без номера
    await notice(db, player, "market")

    log = await journal(db, player)
    assert given_mark(0) not in log["market"].marks
    assert "сдано" in (await hand(db, player, "market")).lower()


async def test_the_migration_leaves_an_empty_journal_alone(db):
    """Пустой журнал переносить нечего — и это не ошибка."""
    from bot.chores import _carry_the_quests

    await enlist(db)
    said = await _carry_the_quests(db)
    assert "0" in said
    assert await journal(db, await db.get_player(42)) == {}


async def test_the_migration_runs_once(db):
    """Второй заход ничего не помечает: отметка уже стоит."""
    from bot.chores import _carry_the_quests

    player = await enlist(db)
    await db.take_quest(player.user_id, "ring", now_ts())

    assert "1" in await _carry_the_quests(db)
    assert "0" in await _carry_the_quests(db)


async def test_a_fighter_with_all_ten_old_quests_gets_the_new_ten(db):
    """Прошедший старую десятку получает новые по уровню."""
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=4))
    for code in OLD_CODES:
        await db.take_quest(player.user_id, code, now_ts())
        await db.finish_quest(player.user_id, code, now_ts())
    await _carry_the_quests(db)

    ahead = await offered(db, player)
    assert ahead is not None and ahead.code not in OLD_CODES
    # Старые заново не предлагают
    log = await journal(db, player)
    assert all(log[code].done for code in OLD_CODES)
    screen = build_bar(player, log)
    assert screen["done"] == 10
    rows = {one["code"]: one for one in screen["quests"]}
    assert all(rows[code]["state"] == "done" for code in OLD_CODES)


async def test_the_migration_ignores_quests_that_are_not_from_the_old_set(db):
    """Новое задание на руках переносом не считается: его дали уже так."""
    from bot.chores import _carry_the_quests

    player = await enlist(db, make_player(level=4))
    await db.take_quest(player.user_id, "dojo_intro", now_ts())
    said = await _carry_the_quests(db)

    assert "0" in said
    log = await journal(db, player)
    assert OLD_MARK not in log["dojo_intro"].marks


async def test_a_fighter_who_already_drives_passes_the_licence_quest(db):
    """Права на руках — курс давно пройден, платить заново незачем."""
    player = await enlist(db, make_player(level=4))
    for chapter in ("city", "work", "gear"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)
    await walk(db, player, "market")

    # Права есть, а записи на курс в журнале нет: так бывает у того,
    # кому их выдали иначе
    player.school = School(licence_at=now_ts())
    await db.save_player(player)
    assert not player.school.enrolled and player.school.has_licence

    await take(db, player, "licence")
    assert "сдано" in (await hand(db, player, "licence")).lower()


async def test_a_full_bag_does_not_lose_the_bartenders_knuckles(db):
    """Рюкзак без предела: вещь бармена ложится в него всегда.

    Проверка держит это обещание: заведись однажды лимит — она упадёт, и
    вместе с ней встанет вопрос, что делать с выдачей при полном рюкзаке.
    """
    player = await enlist(db, make_player(level=4))
    for chapter in ("city", "work", "gear"):
        for one in chapter_quests(chapter):
            await walk(db, player, one.code)
    for _ in range(40):
        await db.add_gear(player.user_id, "knuckles")
    player.gear = await db.list_gear(player.user_id)

    said = await take(db, player, "market")
    assert "на стойку" in said
    log = await journal(db, player)
    assert any(one.startswith("given:") for one in log["market"].marks)


def test_every_visit_quest_points_at_a_real_service_in_a_real_house():
    """Услуга из задания существует, и дом с ней в городе есть.

    Иначе задание стало бы невыполнимым молча: услугу переименовали, а
    бармен всё так же посылает за ней.
    """
    from bot.game.locations import LOCATIONS

    for quest in QUESTS:
        for need in quest.needs:
            if need.goal is not Goal.VISIT:
                continue
            service = Service(need.target)
            houses = [one for one in LOCATIONS if one.allows(service)]
            assert houses, f"{quest.code}: услуги «{need.target}» нет ни в одном доме"


def test_every_place_quest_points_at_a_real_house():
    """Дом из задания есть на карте."""
    for quest in QUESTS:
        for need in quest.needs:
            if need.goal is not Goal.PLACE:
                continue
            assert get_location(need.target) is not None, quest.code


def test_every_visit_quest_is_checked_by_the_server_at_the_door():
    """Отметку о двери ставит только `_at` — та же проверка места.

    Маркер на карте посещением не считается: зачёт даёт успешный ответ
    сервера на запрос самого экрана. Держится это тем, что `notice_visit`
    зовут из одного места, и это место — проверка, что боец на месте.
    """
    import inspect

    from bot.webapp import server

    source = inspect.getsource(server)
    assert source.count("await notice_visit(") == 2, (
        "появился второй путь к отметке о двери — проверьте, что он "
        "тоже проверяет место бойца"
    )
    at_source = inspect.getsource(server._at)
    assert "notice_visit" in at_source
    assert "require(player, service)" in at_source


def test_every_seen_catalog_is_known_to_the_server():
    """Каталог из задания сервер умеет проверить."""
    from bot.webapp.server import SEEN_CATALOGS, _seen_has

    for quest in QUESTS:
        for need in quest.needs:
            if need.goal is not Goal.SEEN:
                continue
            assert need.target in SEEN_CATALOGS, quest.code
            # И проверка кода у него не заглушка
            assert not _seen_has(need.target, "нет-такого-кода")
