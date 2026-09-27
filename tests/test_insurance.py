"""Страховая компания: полис страхования жизни и здоровья.

Проверяем четыре вещи и в таком порядке важности:

1. **Срок.** «Ровно месяц» — это тридцать дней час в час, и продление
   кладётся сверху, а не съедает остаток.
2. **Скидку.** Восемьдесят процентов с цены лечения травмы — и только с
   неё: перевязка и полное выздоровление полисом не дешевеют.
3. **Автопродление.** Оно есть, его можно выключить, и оно не превращается
   в списание за месяцы, в которые за услугой не приходили.
4. **Дверь.** Полис оформляют в страховой и только там: спрятанная кнопка
   обходится запросом мимо интерфейса.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.database import Database
from bot.game.classes import Stats
from bot.game.health import now_ts
from bot.game.hospital import FULL_PRICE, PATCH_PRICE
from bot.game.injuries import HURT_PRICE, Hurt
from bot.game.insurance import (
    HEAL_DISCOUNT,
    POLICY_DAYS,
    POLICY_PRICE,
    POLICY_SECONDS,
    Policy,
    discounted,
    extended,
    fresh,
    policy_number,
    saved,
)
from bot.game.locations import Service, get_location, where_to
from bot.insurance_service import InsuranceError, buy_policy, price_for, set_renew, settle
from bot.injury_service import cure_price, hurt_player
from bot.models import Player
from bot.webapp.documents import build_documents, policy_document
from bot.webapp.insurance import build_insurance
from tests.test_inventory import FakeBot
from tests.test_webapp import TOKEN, make_init_data

OFFICE = "insurance_office"


def headers(user_id: int = 42) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def make_player(
    location: str = OFFICE, credits: int = 1000, pro: bool = False
) -> Player:
    player = Player(
        user_id=42, nickname="Тайлер", class_code="warrior", level=8,
        credits=credits, location=location,
        **Stats(strength=10, agility=10, intuition=10, endurance=30).as_dict(),
    )
    if pro:
        player.pro_until = now_ts() + 30 * 24 * 3600
    return player


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


@pytest.fixture
async def client(db):
    app = create_app(FakeBot(), db, Config(bot_token=TOKEN))
    async with TestClient(TestServer(app)) as client:
        yield client


from bot.webapp.server import create_app  # noqa: E402


# ---------- правила ----------


def test_the_office_sells_one_thing_and_it_lives_there():
    place = where_to(Service.INSURANCE)

    assert place is not None and place.code == OFFICE
    assert get_location(OFFICE).allows(Service.INSURANCE)
    # Дом заработал: записки «скоро здесь появится» у него больше нет
    assert not get_location(OFFICE).soon


def test_the_policy_costs_three_hundred_for_exactly_a_month():
    assert POLICY_PRICE == 300
    assert POLICY_DAYS == 30
    assert POLICY_SECONDS == 30 * 24 * 3600


def test_the_discount_is_four_fifths_of_the_price():
    assert HEAL_DISCOUNT == 0.80
    # Прайс больницы по полису: 100 → 20, 200 → 40, 300 → 60
    assert [discounted(HURT_PRICE[hurt]) for hurt in Hurt] == [20, 40, 60]
    assert [saved(HURT_PRICE[hurt]) for hurt in Hurt] == [80, 160, 240]


def test_the_discount_rounds_towards_the_hospital():
    """На цене, которая на пять не делится, копейка не достаётся бойцу."""
    assert discounted(9) == 2  # 1.8 → 2, а не 1
    assert discounted(1) == 1  # даром не лечат
    assert discounted(0) == 0


def test_a_fresh_policy_runs_a_month_from_the_hour_it_was_written():
    moment = 1_800_000_000

    policy = fresh(moment)

    assert policy.issued == moment
    assert policy.until == moment + POLICY_SECONDS
    assert policy.is_active(moment)
    assert policy.is_active(policy.until - 1)
    assert not policy.is_active(policy.until)
    # Автопродление включено по умолчанию — его выключают, а не включают
    assert policy.auto_renew


def test_renewing_a_live_policy_stacks_the_month_on_top():
    """Купивший второй месяц заранее получает два, а не один."""
    moment = 1_800_000_000
    live = fresh(moment)

    later = extended(live, moment + 10 * 24 * 3600)

    assert later.until == live.until + POLICY_SECONDS
    # Дата оформления не меняется: это один и тот же полис
    assert later.issued == live.issued


def test_renewing_a_dead_policy_starts_the_story_over():
    moment = 1_800_000_000
    dead = fresh(moment)

    again = extended(dead, dead.until + 5 * 24 * 3600)

    assert again.issued == dead.until + 5 * 24 * 3600
    assert again.until == again.issued + POLICY_SECONDS


def test_the_number_is_the_same_for_the_same_policy_and_is_not_stored():
    moment = 1_800_000_000

    first = policy_number(42, moment)

    assert first == policy_number(42, moment)
    assert first != policy_number(43, moment)
    assert first != policy_number(42, moment + POLICY_SECONDS)


def test_the_period_reads_as_two_moscow_dates():
    policy = fresh(1_800_000_000)

    text = policy.period_text()

    assert " — " in text
    assert text.count(".") == 4  # две даты вида 15.01.27


# ---------- оформление ----------


async def test_the_policy_is_written_for_three_hundred(db):
    player = await stand(db, make_player(credits=500))
    moment = now_ts()

    deal = await buy_policy(db, player, moment)

    assert deal.price == POLICY_PRICE and not deal.renewed
    assert player.credits == 200
    assert player.insured(moment)
    # Полис лёг в базу и читается вместе с бойцом
    fresh_player = await db.get_player(42)
    assert fresh_player.policy == deal.policy
    assert fresh_player.insured(moment)


async def test_an_empty_purse_gets_no_policy(db):
    player = await stand(db, make_player(credits=299))

    with pytest.raises(InsuranceError, match="Не хватает кредитов"):
        await buy_policy(db, player)

    assert player.credits == 299
    assert await db.policy_of(42) is None


async def test_a_second_payment_extends_the_same_policy(db):
    player = await stand(db, make_player(credits=1000))
    moment = now_ts()
    first = (await buy_policy(db, player, moment)).policy

    deal = await buy_policy(db, player, moment + 24 * 3600)

    assert deal.renewed
    assert deal.policy.until == first.until + POLICY_SECONDS
    assert player.credits == 400


async def test_a_subscription_writes_the_policy_for_its_own_term(db):
    """«С PRO полис даётся автоматически на время действия PRO»."""
    player = await stand(db, make_player(credits=0, pro=True))
    moment = now_ts()

    said = await settle(db, player, moment)

    assert "по подписке" in said
    assert player.credits == 0
    assert player.insured(moment)
    # Срок полиса — ровно срок подписки, а не месяц
    assert player.policy.until == player.pro_until
    assert (await db.policy_of(42)).until == player.pro_until


async def test_a_subscriber_cannot_stack_free_months(db):
    """Полис держится подпиской, а не выдаётся месяцами.

    Иначе «продлить» нажимали бы сколько угодно раз, и бесплатные месяцы
    жили бы после самой подписки, за которую их дали.
    """
    player = await stand(db, make_player(credits=0, pro=True))
    moment = now_ts()

    first = await settle(db, player, moment)
    said = [await settle(db, player, moment) for _ in range(9)]

    assert first, "первый раз полис всё-таки выписывают"
    # А дальше подписке нечего прибавить, и она молчит
    assert said == [""] * 9
    assert player.policy.until == player.pro_until, "срок не должен расти"
    # И купить месяц даром нельзя: цена одна для всех
    assert price_for(player) == POLICY_PRICE
    with pytest.raises(InsuranceError, match="Не хватает кредитов"):
        await buy_policy(db, player, moment)


async def test_the_policy_dies_with_the_subscription(db):
    player = await stand(db, make_player(credits=0, pro=True))
    moment = now_ts()
    await settle(db, player, moment)
    after_pro = player.pro_until + 1

    assert not player.insured(after_pro)
    # И сам собой не продлевается: за него ни разу не платили
    assert await settle(db, player, after_pro) == ""
    assert not player.insured(after_pro)


async def test_a_subscriber_who_pays_gets_a_month_after_the_subscription(db):
    """Подписчик покупает не полис, который у него есть, а время после."""
    player = await stand(db, make_player(credits=500, pro=True))
    moment = now_ts()
    await settle(db, player, moment)
    pro_end = player.pro_until

    deal = await buy_policy(db, player, moment)

    assert deal.price == POLICY_PRICE and deal.renewed
    assert player.credits == 200
    # Месяц лёг поверх срока подписки, а не вместо него
    assert deal.policy.until == pro_end + POLICY_SECONDS
    assert player.insured(pro_end + 1)


# ---------- скидка в больнице ----------


async def test_a_policy_makes_treating_an_injury_five_times_cheaper(db):
    from bot.game.injuries import get_injury

    player = await stand(db, make_player(location="hospital", credits=1000))
    broken = get_injury("broken_arm")
    await hurt_player(db, player, broken)

    assert cure_price(player, broken) == HURT_PRICE[Hurt.HEAVY]
    player.policy = fresh(now_ts())
    assert cure_price(player, broken) == 60


async def test_the_policy_does_not_touch_the_price_of_health(db):
    """Перевязка и выздоровление — это здоровье, а не травма."""
    from bot.webapp.hospital import build_hospital

    player = await stand(db, make_player(location="hospital"))
    player.set_hp(10)
    player.policy = fresh(now_ts())

    body = build_hospital(player)

    assert [cure["price"] for cure in body["cures"]] == [FULL_PRICE, PATCH_PRICE]


async def test_an_expired_policy_buys_no_discount(db):
    from bot.game.injuries import get_injury

    player = await stand(db, make_player(location="hospital"))
    moment = now_ts()
    player.policy = Policy(
        issued=moment - 2 * POLICY_SECONDS,
        until=moment - 1,
        auto_renew=False,
    )

    assert not player.insured(moment)
    assert cure_price(player, get_injury("broken_arm"), moment) == 300


async def test_the_hospital_screen_names_the_full_price_and_the_saving(db):
    from bot.game.injuries import get_injury
    from bot.webapp.hospital import build_hospital

    player = await stand(db, make_player(location="hospital"))
    await hurt_player(db, player, get_injury("broken_arm"))
    player.policy = fresh(now_ts())

    row = build_hospital(player)["injury"]

    assert (row["price"], row["full_price"], row["saved"]) == (60, 300, 240)
    assert row["insured"] and row["discount"] == 80


async def test_treating_an_injury_charges_the_discounted_price(db):
    from bot.game.injuries import get_injury
    from bot.injury_service import heal_injury

    player = await stand(db, make_player(location="hospital", credits=100))
    await hurt_player(db, player, get_injury("broken_arm"))
    player.policy = fresh(now_ts())

    await heal_injury(db, player)

    # Без полиса на сотне тяжёлую травму не вылечить вовсе
    assert player.credits == 40


# ---------- автопродление ----------


async def test_auto_renewal_is_switched_off_by_one_call(db):
    player = await stand(db, make_player())
    await buy_policy(db, player)

    off = await set_renew(db, player, False)

    assert not off.auto_renew
    assert not (await db.get_player(42)).policy.auto_renew
    # И включается обратно
    on = await set_renew(db, player, True)
    assert on.auto_renew


async def test_switching_renewal_without_a_policy_is_refused(db):
    player = await stand(db, make_player())

    with pytest.raises(InsuranceError, match="Полиса нет"):
        await set_renew(db, player, False)


async def test_a_lapsed_policy_renews_itself_and_charges_once(db):
    player = await stand(db, make_player(credits=1000))
    moment = now_ts()
    await buy_policy(db, player, moment)
    over = player.policy.until + 60

    said = await settle(db, player, over)

    assert "продлён автоматически" in said
    assert player.credits == 400
    assert player.insured(over)
    # Второй взгляд ничего не списывает: полис снова жив
    assert await settle(db, player, over) == ""
    assert player.credits == 400


async def test_half_a_year_away_costs_one_month_not_six(db):
    """Клуб берёт деньги за услугу, а не за время без неё."""
    player = await stand(db, make_player(credits=1000))
    moment = now_ts()
    await buy_policy(db, player, moment)
    much_later = moment + 180 * 24 * 3600

    await settle(db, player, much_later)

    assert player.credits == 400  # одно списание, а не шесть
    # И месяц считается вперёд от возвращения, а не от давно прошедшего срока
    assert player.policy.until == much_later + POLICY_SECONDS


async def test_a_switched_off_policy_is_left_to_die(db):
    player = await stand(db, make_player(credits=1000))
    moment = now_ts()
    await buy_policy(db, player, moment)
    await set_renew(db, player, False)
    over = player.policy.until + 60

    assert await settle(db, player, over) == ""
    assert player.credits == 700
    assert not player.insured(over)


async def test_an_empty_purse_switches_the_renewal_off_and_says_so(db):
    """Иначе полис съедал бы первый же кредит, пришедший на счёт."""
    player = await stand(db, make_player(credits=300))
    moment = now_ts()
    await buy_policy(db, player, moment)
    assert player.credits == 0
    over = player.policy.until + 60

    said = await settle(db, player, over)

    assert "Автопродление полиса выключено" in said
    assert not player.policy.auto_renew
    assert not player.insured(over)
    # И второй раз об этом не говорят: продление уже выключено
    assert await settle(db, player, over) == ""


async def test_extending_the_subscription_extends_the_policy_with_it(db):
    """Подписка продлилась — полис дотянулся до её нового конца."""
    from bot.game.pro import paid_offer
    from bot.pro_service import grant_pro

    player = await stand(db, make_player(credits=0, pro=True))
    moment = now_ts()
    await settle(db, player, moment)
    first = player.policy.until

    grant = await grant_pro(db, player, paid_offer(), moment)

    assert grant.policy, "выдача подписки должна дотянуть полис"
    assert player.policy.until == player.pro_until > first
    assert player.credits == 0


async def test_a_paid_month_is_not_shortened_by_a_subscription(db):
    """Подписка дотягивает срок, но не обрезает оплаченный."""
    player = await stand(db, make_player(credits=500))
    moment = now_ts()
    await buy_policy(db, player, moment)
    paid_until = player.policy.until
    # Подписка короче оплаченного месяца
    player.pro_until = moment + 3 * 24 * 3600
    await db.save_player(player)

    assert await settle(db, player, moment) == ""
    assert player.policy.until == paid_until


# ---------- документы ----------


async def test_the_policy_shows_up_in_the_documents(db):
    player = await stand(db, make_player())
    moment = now_ts()
    await buy_policy(db, player, moment)

    body = build_documents(player, moment)

    assert body["total"] == 1
    paper = body["documents"][0]
    # Всё, что просили видеть в описании: название, имя, период и что даёт
    assert paper["title"] == "Полис страхования жизни и здоровья"
    assert paper["holder"] == "Тайлер"
    assert paper["period"] == player.policy.period_text()
    assert any("80%" in line for line in paper["gives"])
    assert paper["active"] and paper["state"] == "Действует"
    assert paper["number"] == policy_number(42, player.policy.issued)


async def test_documents_are_empty_until_something_is_issued(db):
    player = await stand(db, make_player())

    body = build_documents(player)

    assert body["documents"] == [] and body["total"] == 0
    assert "страховой компании" in body["empty_note"]


async def test_an_expired_policy_stays_a_document(db):
    """Документ не исчезает — у него кончается срок."""
    player = await stand(db, make_player())
    moment = now_ts()
    await buy_policy(db, player, moment)
    await set_renew(db, player, False)
    over = player.policy.until + 60

    paper = policy_document(player, over)

    assert paper and not paper["active"]
    assert paper["state"] == "Срок вышел"
    assert paper["seconds_left"] == 0
    # И в базе он тоже остался: полис продлевают, а не выписывают заново
    assert await db.policy_of(42) is not None


async def test_the_card_carries_the_documents_only_to_their_owner(db):
    from bot.webapp.card import build_card

    player = await stand(db, make_player())
    await buy_policy(db, player)

    mine = build_card(player, TOKEN, viewer_id=42)
    his = build_card(player, TOKEN, viewer_id=43)

    assert len(mine["documents"]) == 1
    assert his["documents"] == [], "полис с чужим именем и сроком не показывают"


# ---------- прилавок страховой ----------


async def test_the_counter_says_the_subscription_holds_the_policy(db):
    player = await stand(db, make_player(credits=500, pro=True))
    await settle(db, player)

    body = build_insurance(player)

    # Цена та же для всех: подписчик платит за время после подписки
    assert body["price"] == POLICY_PRICE
    assert body["pro"] and body["by_pro"] and body["insured"]
    assert "держит подписка" in body["why"]
    # И обещания у такого полиса другие: не месяц, а срок подписки
    assert any("подписка" in line for line in body["gives"])
    assert body["action"] == "Продлить на месяц"


async def test_the_counter_says_nothing_about_a_subscription_to_the_rest(db):
    player = await stand(db, make_player(credits=500))

    body = build_insurance(player)

    assert not body["pro"] and not body["by_pro"] and body["why"] == ""
    assert any("месяц" in line for line in body["gives"])


async def test_the_counter_turns_into_a_renewal_once_the_policy_is_live(db):
    player = await stand(db, make_player())
    await buy_policy(db, player)

    body = build_insurance(player)

    assert body["insured"] and body["action"] == "Продлить на месяц"
    assert body["policy"]["active"]


async def test_the_counter_shows_the_hospital_price_list_both_ways(db):
    player = await stand(db, make_player())

    rows = build_insurance(player)["prices"]

    assert [(row["full"], row["price"]) for row in rows] == [
        (100, 20), (200, 40), (300, 60)
    ]


# ---------- дверь ----------


async def test_the_office_screen_is_closed_outside_the_office(client, db):
    await stand(db, make_player(location="hospital"))

    response = await client.get("/api/insurance", headers=headers())

    assert response.status == 409
    assert "Страховая" in (await response.json())["error"]


async def test_the_policy_cannot_be_bought_from_elsewhere(client, db):
    """Спрятанная кнопка обходится запросом мимо интерфейса."""
    await stand(db, make_player(location="hospital"))

    response = await client.post(
        "/api/insurance", json={"action": "buy"}, headers=headers()
    )

    assert response.status == 409
    assert await db.policy_of(42) is None


async def test_the_whole_policy_goes_through_the_page(client, db):
    await stand(db, make_player(credits=500))

    response = await client.post(
        "/api/insurance", json={"action": "buy"}, headers=headers()
    )
    body = await response.json()

    assert response.status == 200
    assert "Полис оформлен" in body["said"]
    assert body["insurance"]["insured"]
    assert len(body["card"]["documents"]) == 1
    assert (await db.get_player(42)).credits == 200

    # Выключить продление — тем же путём
    off = await client.post(
        "/api/insurance", json={"action": "renew", "on": False}, headers=headers()
    )
    said = (await off.json())["said"]
    assert "выключено" in said
    assert not (await db.policy_of(42)).auto_renew


async def test_an_unknown_action_is_refused(client, db):
    await stand(db, make_player())

    response = await client.post(
        "/api/insurance", json={"action": "танцевать"}, headers=headers()
    )

    assert response.status == 400


async def test_the_hospital_renews_the_policy_before_it_prices_the_cure(client, db):
    """Полис предъявляют в больнице, значит там же сводятся и его часы."""
    from bot.game.injuries import get_injury

    player = await stand(db, make_player(location="hospital", credits=1000))
    moment = now_ts()
    await buy_policy(db, player, moment)
    # Срок вышел, но автопродление включено: больница должна продлить сама
    await db.set_policy(42, Policy(issued=moment, until=moment - 1))
    await hurt_player(db, player, get_injury("broken_arm"))

    response = await client.get("/api/hospital", headers=headers())
    body = await response.json()

    assert "продлён автоматически" in body["said"]
    assert body["injury"]["price"] == 60


async def test_the_policy_of_a_subscriber_says_by_what_right_it_is_held(db):
    """На бланке видно, оплачен полис или держится подпиской."""
    player = await stand(db, make_player(credits=0, pro=True))
    moment = now_ts()
    await settle(db, player, moment)

    paper = policy_document(player, moment)

    assert paper["by_pro"] and paper["ground"] == "По подписке PRO"
    # Переключать такому полису нечего: им распоряжается срок подписки
    assert not paper["switchable"]
    assert any("подписка" in line for line in paper["gives"])


async def test_a_paid_policy_says_it_is_paid_and_keeps_its_switch(db):
    player = await stand(db, make_player(credits=500))
    await buy_policy(db, player)

    paper = policy_document(player, now_ts())

    assert not paper["by_pro"] and paper["ground"] == "Оплачен"
    assert paper["switchable"] and paper["auto_renew"]


async def test_a_month_bought_past_the_subscription_is_a_paid_policy_again(db):
    """Купил месяц поверх подписки — бланк снова оплаченный, со всеми правами."""
    player = await stand(db, make_player(credits=500, pro=True))
    moment = now_ts()
    await settle(db, player, moment)
    await buy_policy(db, player, moment)

    paper = policy_document(player, moment)

    assert not paper["by_pro"], "срок ушёл за подписку — держит его оплата"
    assert paper["switchable"]
