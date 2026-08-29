"""Регистрация бойца через настоящий Dispatcher aiogram (Telegram подменён)."""

from tests.harness import Client

from bot.keyboards import ClassCB, GenderCB, LookCB, StatCB
from bot.models import Player


async def test_character_creation_end_to_end(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)

    await client.send("/start")
    assert "Выбирай класс" in session.texts[0]

    await client.press(ClassCB(code="assassin").pack())
    assert "прозвище" in session.texts[-1]

    await client.send("Тайлер Дёрден")
    assert "За кого дерёмся?" in session.texts[-1]

    await client.press(GenderCB(code="male").pack())
    assert "Выбери образ" in session.texts[-1]

    await client.press(LookCB(code="racer").pack())
    assert "Осталось очков: <b>6</b>" in session.texts[-1]

    # четыре очка в силу — предел на старте, пятое должно отлететь
    for _ in range(4):
        await client.press(StatCB(action="add", stat="strength").pack())
    await client.press(StatCB(action="add", stat="strength").pack())
    assert any("в один стат" in alert for alert in session.alerts)

    await client.press(StatCB(action="add", stat="endurance").pack())
    await client.press(StatCB(action="add", stat="agility").pack())
    assert "Осталось очков: <b>0</b>" in session.texts[-1]

    await client.press(StatCB(action="done").pack())

    player = await client.player()
    assert player is not None
    assert player.nickname == "Тайлер Дёрден"
    assert player.class_code == "assassin"
    # образ выбран, и его значок стал значком бойца
    assert player.look == "racer" and player.gender == "male"
    assert player.avatar == "🏍"
    # базовые статы ассасина 4/4/4/2 плюс распределённые 4/1/0/1
    assert (player.strength, player.agility, player.intuition, player.endurance) == (
        8,
        5,
        4,
        3,
    )
    assert player.level == 1 and player.free_points == 0
    assert "Боец готов" in session.texts[-2]


async def create_character(client: Client, class_code: str, nickname: str) -> None:
    await client.send("/start")
    await client.press(ClassCB(code=class_code).pack())
    await client.send(nickname)
    await client.press(GenderCB(code="male").pack())
    await client.press(LookCB(code="rookie").pack())
    for _ in range(4):
        await client.press(StatCB(action="add", stat="strength").pack())
    for _ in range(2):
        await client.press(StatCB(action="add", stat="agility").pack())
    await client.press(StatCB(action="done").pack())


async def test_start_shows_existing_character(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)
    await create_character(client, "tank", "Стена")

    await client.send("/start")
    assert "С возвращением" in session.texts[-2]
    assert "Стена" in session.texts[-2]


async def test_nickname_length_is_validated(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)
    await client.send("/start")
    await client.press(ClassCB(code="warrior").pack())
    await client.send("Я")
    assert "символов" in session.texts[-1]
    await client.send("Боб")
    assert "За кого дерёмся?" in session.texts[-1]


async def test_reset_requires_confirmation(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)
    await create_character(client, "warrior", "Боб")
    assert await client.player() is not None

    await client.send("/reset")
    assert "Точно стереть бойца" in session.texts[-1]
    await client.press("reset:0")
    assert await client.player() is not None
    assert "остаётся в строю" in session.texts[-1]

    await client.send("/reset")
    await client.press("reset:1")
    assert await client.player() is None


async def test_upgrade_spends_free_points(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)
    await db.save_player(
        Player(
            user_id=client.user.id,
            nickname="Марла",
            class_code="rogue",
            strength=3,
            agility=5,
            intuition=3,
            endurance=3,
            level=2,
            free_points=2,
        )
    )
    await client.send("/upgrade")
    assert "Осталось очков: <b>2</b>" in session.texts[-1]
    await client.press(StatCB(action="add", stat="intuition").pack())
    await client.press(StatCB(action="add", stat="intuition").pack())
    await client.press(StatCB(action="done").pack())

    player = await client.player()
    assert player.intuition == 5
    assert player.free_points == 0


async def test_upgrade_without_points_is_refused(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)
    await create_character(client, "warrior", "Боб")
    await client.send("/upgrade")
    assert "Свободных очков нет" in session.texts[-1]


async def test_help_and_top_are_wired_up(dispatcher_env):
    """Справка и топ должны получать config и db из workflow-данных."""
    db, _, session = dispatcher_env
    client = Client(db)
    await create_character(client, "rogue", "Марла")

    await client.send("/help")
    assert "Бойцовский клуб" in session.texts[-1]
    assert "ап" in session.texts[-1]

    await client.send("/top")
    assert "Чемпионы клуба" in session.texts[-1]
    assert "Марла" in session.texts[-1]

    await client.send("/classes")
    assert "Танк" in session.texts[-1]


async def test_card_deep_link_shows_that_fighter(dispatcher_env):
    """t.me/бот?start=card_ID — запасной путь к карточке из чата боя."""
    db, _, session = dispatcher_env
    owner = Client(db)
    guest = Client(db)
    fighter = Player(
        user_id=owner.user.id,
        nickname="Тайлер",
        class_code="warrior",
        level=4,
        credits=321,
    )
    await db.save_player(fighter)

    await guest.send(f"/start card_{owner.user.id}")
    text = session.texts[-1]

    assert "Тайлер" in text
    assert "4 уровень" in text
    assert "321" not in text  # чужой кошелёк не показываем
    # и кнопка, открывающая карточку в мини-аппе
    button = session.method_calls("SendMessage")[-1].reply_markup
    assert button is None or "Карточка" in button.inline_keyboard[0][0].text


async def test_a_deep_link_to_nobody_falls_back_to_registration(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)

    await client.send("/start card_999999")
    assert "картотеке нет" in session.texts[-2]
    assert "Выбирай класс" in session.texts[-1]


async def test_an_unknown_deep_link_starts_the_usual_way(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)

    await client.send("/start reklama")
    assert "Выбирай класс" in session.texts[-1]


async def test_miniapp_command_shows_the_link_under_the_name(dispatcher_env):
    """/miniapp показывает ссылку целиком — по ней и видно, что настроено."""
    from bot.game.links import links

    db, _, session = dispatcher_env
    client = Client(db)
    try:
        links.configure("vegasfightclub_bot", "card")
        await client.send("/miniapp")
        text = session.texts[-1]

        assert f"https://t.me/vegasfightclub_bot/card?startapp={client.user.id}" in text
        assert "чат с ботом" in text  # подсказка про несозданное приложение
    finally:
        links.configure("", "")


async def test_the_fighter_picks_a_gender_and_gets_the_looks_that_fit(dispatcher_env):
    """Пол спрашивают до образа, и образы предлагают только своего пола."""
    db, _, session = dispatcher_env
    client = Client(db)

    await client.send("/start")
    await client.press(ClassCB(code="rogue").pack())
    await client.send("Марла")

    assert "За кого дерёмся?" in session.texts[-1]
    await client.press(GenderCB(code="female").pack())

    offered = [
        button.text
        for row in session.markups[-1].inline_keyboard
        for button in row
    ]
    assert offered == [
        "💥 Бунтарка",
        "🍸 Барменша",
        "👟 Бегунья",
        "📷 Загрузить своё фото",
    ]

    await client.press(LookCB(code="barmaid").pack())
    for _ in range(4):
        await client.press(StatCB(action="add", stat="agility").pack())
    for _ in range(2):
        await client.press(StatCB(action="add", stat="intuition").pack())
    await client.press(StatCB(action="done").pack())

    player = await client.player()
    assert player.gender == "female"
    assert player.look == "barmaid" and player.avatar == "🍸"


async def test_a_paid_look_cannot_be_grabbed_at_creation(dispatcher_env):
    """Кнопки платного образа на старте нет, но и по коду он не пройдёт."""
    db, _, session = dispatcher_env
    client = Client(db)
    await client.send("/start")
    await client.press(ClassCB(code="warrior").pack())
    await client.send("Тайлер")
    await client.press(GenderCB(code="male").pack())

    await client.press(LookCB(code="veteran").pack())  # платный, 1000 кр.

    assert any("на старте не выдаётся" in alert for alert in session.alerts)
    assert await client.player() is None


async def test_the_last_word_sends_the_newcomer_to_the_club(dispatcher_env):
    """После создания зовём в группу, а не объяснять про /arena."""
    db, _, session = dispatcher_env
    client = Client(db)
    await create_character(client, "warrior", "Тайлер")

    done = next(text for text in session.texts if "Боец готов" in text)
    assert "Бойцовский клуб Вегас" in done
    assert "https://t.me/" in done
    assert "/arena" not in done
