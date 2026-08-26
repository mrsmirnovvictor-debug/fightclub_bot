"""Регистрация бойца через настоящий Dispatcher aiogram (Telegram подменён)."""

from tests.harness import Client

from bot.keyboards import AvatarCB, ClassCB, StatCB
from bot.models import Player


async def test_character_creation_end_to_end(dispatcher_env):
    db, _, session = dispatcher_env
    client = Client(db)

    await client.send("/start")
    assert "Выбирай класс" in session.texts[0]

    await client.press(ClassCB(code="assassin").pack())
    assert "прозвище" in session.texts[-1]

    await client.send("Тайлер Дёрден")
    assert "выбери аватар" in session.texts[-1].lower()

    await client.press(AvatarCB(value="🥷").pack())
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
    assert player.avatar == "🥷"
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
    await client.press(AvatarCB(value="🐻").pack())
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
    assert "аватар" in session.texts[-1].lower()


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
