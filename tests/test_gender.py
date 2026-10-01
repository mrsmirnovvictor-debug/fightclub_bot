"""Пол бойца: кто он и из чего одевается.

Пол спрашивают при создании персонажа, и дальше он решает ровно одно —
какие образы боец видит в гардеробе. На бой он не влияет ничем: ни на
характеристики, ни на урон, ни на подбор соперника.

Проверяем три вещи:

1. **Бойцы без пола получают мужской** — их заводили до вопроса, и
   выбирали они из мужского набора, другого тогда и не было.
2. **Гардероб показывает своё** — чужой пол не прячется запретом, он
   просто не приходит на страницу.
3. **Купленное и надетое не отнимают** — образ чужого пола остаётся у
   того, кому он уже принадлежит.
"""

import pytest

from bot.database import Database
from bot.game.looks import FEMALE, LOOKS, MALE, looks_of_gender
from bot.looks_service import LookError, choose_look, wardrobe
from bot.models import Player


def make_player(gender: str = MALE, credits: int = 0) -> Player:
    return Player(
        user_id=42,
        nickname="Тайлер",
        class_code="warrior",
        credits=credits,
        gender=gender,
    )


async def stand(db: Database, player: Player) -> Player:
    await db.save_player(player)
    return player


# ---------- сам пол ----------


def test_the_club_knows_two_genders_and_dresses_both():
    assert (MALE, FEMALE) == ("male", "female")
    # Поровну: три открытых и три за кредиты на каждый пол
    for gender in (MALE, FEMALE):
        mine = [look for look in looks_of_gender(gender) if not look.pro]
        assert len(mine) == 6, gender
        assert sum(1 for look in mine if not look.price) == 3, gender


def test_a_fighter_without_a_gender_counts_as_male():
    """Запасной ответ нужен самой карточке, а не старым записям."""
    assert make_player(gender="").sex == MALE
    assert make_player(gender=FEMALE).sex == FEMALE


async def test_old_fighters_are_given_the_male_gender(tmp_path):
    """Их заводили до вопроса, и выбирали они из мужского набора."""
    path = str(tmp_path / "old.db")
    db = Database(path)
    await db.connect()
    await stand(db, make_player(gender=""))
    # Пишем пустой пол в обход модели — так он и лежал в старой базе
    await db.conn.execute("UPDATE players SET gender = '' WHERE user_id = 42")
    await db.conn.commit()
    await db.close()

    # Следующий запуск бота поднимает ту же базу
    again = Database(path)
    await again.connect()
    player = await again.get_player(42)
    await again.close()

    assert player.gender == MALE


async def test_the_gender_of_those_who_chose_is_left_alone(tmp_path):
    path = str(tmp_path / "her.db")
    db = Database(path)
    await db.connect()
    await stand(db, make_player(gender=FEMALE))
    await db.close()

    again = Database(path)
    await again.connect()
    player = await again.get_player(42)
    await again.close()

    assert player.gender == FEMALE


# ---------- гардероб ----------


async def test_a_woman_sees_only_womens_looks(db):
    player = await stand(db, make_player(gender=FEMALE))

    rows = await wardrobe(db, player)

    assert rows, "гардероб не должен быть пустым"
    assert {row["gender"] for row in rows} == {FEMALE}
    assert {row["code"] for row in rows} == {
        look.code for look in looks_of_gender(FEMALE)
    }


async def test_a_man_sees_only_mens_looks(db):
    player = await stand(db, make_player(gender=MALE))

    rows = await wardrobe(db, player)

    assert {row["gender"] for row in rows} == {MALE}
    # Женские образы не прячутся запретом — их просто нет на странице
    assert not {row["code"] for row in rows} & {
        look.code for look in looks_of_gender(FEMALE)
    }


async def test_a_bought_look_of_the_other_gender_is_never_taken_away(db):
    """Бойцам без пола поставили мужской — купленное лицо им оставили."""
    player = await stand(db, make_player(gender=MALE, credits=0))
    await db.add_look(player.user_id, "queen")  # женский, купленный прежде

    rows = await wardrobe(db, player)
    row = next(one for one in rows if one["code"] == "queen")
    choice = await choose_look(db, player, "queen")

    assert row["owned"] and row["gender"] == FEMALE
    assert not choice.bought and choice.credits == 0
    assert (await db.get_player(42)).look == "queen"


async def test_a_worn_look_of_the_other_gender_stays_on_the_shelf(db):
    """Надетое видно, даже если оно чужого пола: иначе его нечем сменить."""
    player = make_player(gender=MALE)
    player.look = "runner"  # женский образ остался с прошлой версии
    await stand(db, player)

    rows = await wardrobe(db, player)

    assert "runner" in {row["code"] for row in rows}
    assert [row["code"] for row in rows if row["current"]] == ["runner"]


async def test_a_look_of_the_other_gender_is_not_for_sale(db):
    """Чужого пола нет на полке — и купить его нельзя тоже.

    Спрятанная кнопка обходится запросом мимо страницы. Без проверки на
    сервере боец мог бы купить лицо, которого потом не увидит в своём
    гардеробе, — и остаться без кредитов и без выбора.
    """
    player = await stand(db, make_player(gender=MALE, credits=10_000))

    shelf = {row["code"] for row in await wardrobe(db, player)}

    assert "queen" not in shelf
    with pytest.raises(LookError, match="не из твоего гардероба"):
        await choose_look(db, player, "queen")
    assert (await db.get_player(42)).credits == 10_000


async def test_the_old_handout_is_hidden_from_everyone_who_lacks_it(db):
    player = await stand(db, make_player(gender=MALE))

    shelf = {row["code"] for row in await wardrobe(db, player)}

    assert "assassin" not in shelf
    with pytest.raises(LookError, match="больше не выдают"):
        await choose_look(db, player, "assassin")


def test_every_look_belongs_to_a_gender():
    """Образ без пола не показался бы никому."""
    for look in LOOKS:
        assert look.gender in (MALE, FEMALE), look.code
