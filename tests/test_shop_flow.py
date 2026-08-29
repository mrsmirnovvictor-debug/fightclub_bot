"""Траты кредитов через настоящий Dispatcher: респек, класс, прозвище, аватар."""

import pytest

from tests.harness import Client

from bot.game.economy import PRICE_APPEARANCE, PRICE_CLASS_CHANGE, PRICE_RESPEC
from bot.game.equipment import CATALOGUE
from bot.game.potions import get_potion
from bot.keyboards import AvatarCB, BuyCB, ClassCB, DrinkCB
from bot.models import Player

HEAL = get_potion("heal_small")


def make_player(user_id: int, **kwargs) -> Player:
    data = dict(
        user_id=user_id,
        nickname="Тайлер",
        class_code="warrior",
        strength=8,  # база воина 4/3/3/4 плюс шесть вложенных очков
        agility=4,
        intuition=4,
        endurance=6,
        level=3,
        credits=300,
    )
    data.update(kwargs)
    return Player(**data)


@pytest.fixture
async def client(dispatcher_env):
    db, _, _ = dispatcher_env
    client = Client(db)
    await db.save_player(make_player(client.user.id))
    return client


async def test_shop_shows_balance_and_prices(client, dispatcher_env):
    _, _, session = dispatcher_env
    await client.send("/shop")
    text = session.texts[-1]
    assert "300" in text
    assert str(PRICE_RESPEC) in text and str(PRICE_CLASS_CHANGE) in text


async def test_showcase_lists_open_goods_by_type(client, dispatcher_env):
    _, _, session = dispatcher_env
    await client.send("/buy")
    text = session.texts[-1]
    assert "Кеды" in text and str(CATALOGUE["sneakers"].price) in text
    assert "уровень 3, сила 6" in text  # требования кастета
    assert "Оружие" in text and "Обувь" in text  # разложено по типам
    # товар не по уровню на прилавок не выкладывают, но о нём предупреждают
    assert f"Деревянная бита — {CATALOGUE['pipe'].price}" not in text
    unlocks = next(line for line in text.splitlines() if "На 4 уровне откроются" in line)
    assert "Деревянная бита" in unlocks


async def test_showcase_grows_with_the_fighter(client, dispatcher_env):
    db, _, session = dispatcher_env
    player = await client.player()
    player.level = 6
    await db.save_player(player)

    await client.send("/buy")
    text = session.texts[-1]

    assert "Деревянная бита" in text and "Бита" in text
    assert "На 7 уровне откроются" in text
    # кнопками в чате — только свежая партия, остальное в лавке мини-аппа
    buttons = session.calls[-1].reply_markup.inline_keyboard
    labels = [b.text for row in buttons for b in row]
    assert any("Бита" in label for label in labels)
    assert not any("Кастет" in label for label in labels)


async def test_level_locked_goods_are_not_sold_in_chat(client, dispatcher_env):
    _, _, session = dispatcher_env
    await client.press(BuyCB(code="bat").pack())

    assert "открывается на 6 уровне" in session.alerts[-1]
    assert (await client.player()).gear == []


async def test_buying_takes_credits_and_fills_the_backpack(client, dispatcher_env):
    db, _, session = dispatcher_env
    await client.send("/buy")
    await client.press(BuyCB(code="sneakers").pack())

    player = await client.player()
    assert player.credits == 300 - CATALOGUE["sneakers"].price
    assert [item.code for item in player.backpack] == ["sneakers"]
    assert "Куплено" in session.texts[-1]


async def test_empty_wallet_stops_the_purchase(client, dispatcher_env):
    db, _, session = dispatcher_env
    player = await client.player()
    player.credits = 5
    await db.save_player(player)

    await client.press(BuyCB(code="sneakers").pack())

    assert "Не хватает кредитов" in session.alerts[-1]
    assert (await client.player()).gear == []


async def test_shop_explains_wear_and_repair(client, dispatcher_env):
    _, _, session = dispatcher_env
    await client.send("/shop")
    text = session.texts[-1]
    assert "инвентаре" in text and "износ" in text.lower()


async def test_respec_asks_before_charging(client, dispatcher_env):
    _, _, session = dispatcher_env
    before = await client.player()
    spent = before.spent_points

    await client.send("/respec")
    assert "Снести характеристики" in session.texts[-1]

    await client.press("respec:0")  # отмена
    unchanged = await client.player()
    assert unchanged.credits == before.credits
    assert unchanged.stats == before.stats

    await client.send("/respec")
    await client.press("respec:1")
    after = await client.player()
    assert after.credits == before.credits - PRICE_RESPEC
    # выносливость, накопленная уровнями, респеком не сносится
    assert after.stats == after.base_with_levels()
    assert after.endurance == after.fclass.base_stats.endurance + after.level_endurance
    assert after.free_points == before.free_points + spent
    assert after.level == before.level  # уровень и рейтинг не трогаем
    assert after.rating == before.rating


async def test_respec_is_refused_without_credits(client, dispatcher_env):
    db, _, session = dispatcher_env
    player = await client.player()
    player.credits = 5
    await db.save_player(player)

    await client.send("/respec")
    assert "Не хватает кредитов" in session.texts[-1]
    assert (await client.player()).credits == 5


async def test_class_change_rebases_stats(client, dispatcher_env):
    _, _, session = dispatcher_env
    before = await client.player()
    spent = before.spent_points

    await client.send("/class")
    assert str(PRICE_CLASS_CHANGE) in session.texts[-1]
    await client.press(ClassCB(code="tank").pack())

    after = await client.player()
    assert after.class_code == "tank"
    assert after.stats == after.base_with_levels()
    assert after.free_points == before.free_points + spent
    assert after.credits == before.credits - PRICE_CLASS_CHANGE
    assert after.level == before.level


async def test_class_change_to_the_same_class_is_refused(client, dispatcher_env):
    _, _, session = dispatcher_env
    before = await client.player()
    await client.send("/class")
    await client.press(ClassCB(code="warrior").pack())
    assert (await client.player()).credits == before.credits
    assert any("и так этого класса" in alert for alert in session.alerts)


async def test_rename_costs_credits(client, dispatcher_env):
    _, _, session = dispatcher_env
    before = await client.player()

    await client.send("/rename")
    assert "rename" in session.texts[-1]  # подсказка про синтаксис
    assert (await client.player()).credits == before.credits

    await client.send("/rename Я")
    assert "символов" in session.texts[-1]

    await client.send("/rename Тайлер Дёрден")
    after = await client.player()
    assert after.nickname == "Тайлер Дёрден"
    assert after.credits == before.credits - PRICE_APPEARANCE


async def test_avatar_change_costs_credits(client, dispatcher_env):
    _, _, session = dispatcher_env
    before = await client.player()

    await client.send("/avatar")
    assert str(PRICE_APPEARANCE) in session.texts[-1]
    await client.press(AvatarCB(value="🐺").pack())

    after = await client.player()
    assert after.avatar == "🐺"
    assert after.credits == before.credits - PRICE_APPEARANCE


async def test_nobody_can_hand_the_club_their_own_picture(client, dispatcher_env):
    """Своё фото больше не грузят: ни кнопкой, ни присланным снимком."""
    _, _, session = dispatcher_env
    before = await client.player()
    await client.send("/avatar")

    offered = [
        button.text for row in session.markups[-1].inline_keyboard for button in row
    ]
    assert not any("фото" in text.lower() for text in offered)

    await client.send_photo("my-photo")

    after = await client.player()
    assert after.avatar_file_id is None
    assert after.credits == before.credits  # ни списаний, ни аватара


async def test_shop_needs_a_character(dispatcher_env):
    db, _, session = dispatcher_env
    stranger = Client(db)
    await stranger.send("/shop")
    assert "создай бойца" in session.texts[-1]


# ---------- эликсиры ----------


async def test_potions_shelf_lists_what_is_open_and_what_is_locked(
    client, dispatcher_env
):
    _, _, session = dispatcher_env
    await client.send("/potions")
    text = session.texts[-1]

    assert "Эликсир восстановления" in text and str(HEAL.price) in text
    # временные открываются с 5 уровня, а бойцу тут третий
    assert "Эликсир силы" not in text
    assert "откроются на 5 уровне" in text


async def test_a_potion_is_bought_and_then_drunk_from_the_chat(
    client, dispatcher_env
):
    db, _, session = dispatcher_env
    player = await client.player()
    player.set_hp(player.max_hp - 100)
    await db.save_player(player)

    await client.press(BuyCB(code="heal_small").pack())
    assert (await client.player()).credits == 300 - HEAL.price
    assert await db.list_potions(client.user.id) == {"heal_small": 1}

    await client.press(DrinkCB(code="heal_small").pack())
    text = session.texts[-1]

    assert "Выпит" in text and f"на <b>{HEAL.heal}</b>" in text
    assert "Это была последняя" in text
    assert await db.list_potions(client.user.id) == {}


async def test_the_temporary_effect_shows_up_in_the_profile(client, dispatcher_env):
    db, _, session = dispatcher_env
    player = await client.player()
    player.level = 5
    await db.save_player(player)

    await client.press(BuyCB(code="boost_strength").pack())
    await client.press(DrinkCB(code="boost_strength").pack())
    assert "Эффект пошёл" in session.texts[-1]

    await client.send("/profile")
    text = session.texts[-1]

    assert "🧪 Действует: 💪 Эликсир силы" in text
    # характеристики в профиле уже с прибавкой
    assert "Сила: <b>18</b>" in text


async def test_the_chat_warns_before_swapping_a_running_elixir(client, dispatcher_env):
    """Другой временный эликсир гасит нынешний — бот переспрашивает."""
    db, _, session = dispatcher_env
    player = await client.player()
    player.level = 5
    player.credits = 1000  # на два временных эликсира по 200
    await db.save_player(player)

    await client.press(BuyCB(code="boost_strength").pack())
    await client.press(BuyCB(code="boost_agility").pack())
    await client.press(DrinkCB(code="boost_strength").pack())
    assert "Эффект пошёл" in session.texts[-1]

    # первый заход по другому эликсиру только предупреждает
    await client.press(DrinkCB(code="boost_agility").pack())
    warning = session.texts[-1]
    assert "Сейчас действует" in warning and "Эликсир силы" in warning
    assert "действие предыдущего эликсира закончится" in warning
    assert await db.list_potions(client.user.id) == {"boost_agility": 1}

    # согласился — меняем, и бот говорит, что именно погасло
    await client.press(DrinkCB(code="boost_agility", confirm=1).pack())
    done = session.texts[-1]
    assert "Закончилось действие: Эликсир силы" in done
    assert [e.code for e in (await client.player()).active_effects()] == [
        "boost_agility"
    ]


async def test_the_chat_pours_healing_without_any_questions(client, dispatcher_env):
    """Восстановление ничего не гасит — переспрашивать не о чем."""
    db, _, session = dispatcher_env
    player = await client.player()
    player.level = 5
    player.credits = 1000
    player.set_hp(player.max_hp - 100)
    await db.save_player(player)

    await client.press(BuyCB(code="boost_strength").pack())
    await client.press(DrinkCB(code="boost_strength").pack())
    await client.press(BuyCB(code="heal_small").pack())
    await client.press(DrinkCB(code="heal_small").pack())

    text = session.texts[-1]
    assert "Здоровья прибавилось" in text
    assert "Закончилось действие" not in text
    assert [e.code for e in (await client.player()).active_effects()] == [
        "boost_strength"
    ]
