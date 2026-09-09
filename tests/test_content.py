"""Договор о содержимом лавки: что должно быть верно про любую вещь.

Вещи живут в `bot/content/items.py`, и правит их не движок. Этот файл —
граница, за которую содержимому выходить нельзя: потолок процентов,
лестница урона и плоских прибавок, выносливости на вещах нет, картинка у
каждой своя. Прошла новая вещь этот тест — значит, она не ломает баланс
классов; не прошла — числа надо править, а не тест.

Баланс тестом не заменить: круг классов считает `tests/test_combat.py`,
и после каждого пака вещей его стоит прогнать целиком.
"""


from bot.game.classes import FIGHTER_CLASSES, Zone
from bot.game.economy import credits_per_level
from bot.game.equipment import (
    ALL_SLOTS,
    ART,
    CATALOGUE,
    EARLY_LEVELS,
    EARLY_SHARE_CAP,
    ITEMS,
    LATE_SHARE_CAP,
    MAGIC_ITEMS,
    SHOWCASE,
    Item,
    Slot,
    items_unlocked_at,
)


# ---------- чем вещь вообще может быть ----------


def test_catalogue_items_know_their_slot_and_price():
    for item in CATALOGUE.values():
        # За что вещь берут: кредиты в лавке клуба, звёзды у мага — или
        # никак, если это награда: её выдают, а не продают.
        assert item.price > 0 or item.stars > 0 or item.reward
        assert not (item.price and item.stars), f"{item.title}: и кредиты, и звёзды"
        assert not (item.reward and (item.price or item.stars)), (
            f"{item.title}: награда с ценником"
        )
        assert item.slot in item.slots
        assert isinstance(item, Item)
        # второе место есть только у оружия: его берут во вторую руку
        assert item.slots == (
            (Slot.WEAPON, Slot.OFFHAND) if item.is_weapon else (item.slot,)
        )


def test_the_magic_counter_is_kept_out_of_the_club_shop():
    """Звёздный товар не лежит на прилавке за кредиты и не путается с ним."""
    assert MAGIC_ITEMS, "у мага пусто"
    for item in MAGIC_ITEMS:
        assert item.stars > 0 and item.price == 0
        assert item not in SHOWCASE
    assert all(not item.is_magic for item in SHOWCASE)


def test_the_whole_catalogue_lives_in_one_bucket():
    """Первого бакета больше нет: всё лежит в общем, включая кеды."""
    from bot.game.equipment import ART

    for item in CATALOGUE.values():
        assert item.picture.startswith(ART), f"{item.code}: не из общего бакета"


def test_percent_bonuses_stay_within_their_caps():
    """Проценты растут со ступенью, но не настолько, чтобы стирать класс.

    Потолок держит лавку клуба — то, что берут за кредиты и что определяет
    баланс между классами. Товар мага живёт по своим правилам: он и должен
    быть заметно сильнее, иначе за него не платили бы звёздами.

    Вещи с числами выше потолка в игре есть — те, что выдают руками на
    тестовых бойцов. Но они помечены наградой, на прилавок не попадают, и
    правило их не касается: витрина проверяется целиком, без исключений.
    """
    for item in SHOWCASE:
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        cap = EARLY_SHARE_CAP if item.level_required <= EARLY_LEVELS else LATE_SHARE_CAP
        assert max(shares) <= cap + 1e-9, f"{item.title}: {max(shares):.0%} > {cap:.0%}"


def test_items_never_hand_out_endurance():
    """Выносливость растят только руками — вещи дают лишь запас здоровья."""
    for item in CATALOGUE.values():
        assert item.bonus.endurance == 0, item.title
    assert any(item.hp for item in CATALOGUE.values())


def test_every_weapon_adds_damage_and_it_grows_with_the_tier():
    """Лестница ступеней — про лавку клуба: у мага своя цена и свой отсчёт.

    Исключений в лестнице нет: всё, что лежит на прилавке, в неё встаёт.
    Вещи с ручными числами торгуются не здесь — их выдают.
    """
    weapons = [item for item in SHOWCASE if item.is_weapon]
    assert weapons
    by_level: dict[int, list[float]] = {}
    for item in weapons:
        assert item.damage_min > 0 and item.damage_max >= item.damage_min
        by_level.setdefault(item.level_required, []).append(
            (item.damage_min + item.damage_max) / 2
        )
    levels = sorted(by_level)
    for lower, upper in zip(levels, levels[1:]):
        assert max(by_level[lower]) < min(by_level[upper]), (
            f"оружие {upper} уровня не сильнее оружия {lower}"
        )


def test_weapon_spread_matches_the_character_of_its_class():
    """У ассасина оружие рвано́е, у танка ровное, у воина с трикстером середина."""
    def spread(code: str) -> float:
        item = CATALOGUE[code]
        return (item.damage_max - item.damage_min) / (item.damage_min + item.damage_max)

    for tier in (
        ("pipe", "switchblade", "awl", "crowbar"),
        ("bat", "machete", "stiletto", "sledge"),
        ("fire_axe", "balisong", "ice_pick", "chain"),
        ("cleaver", "razor", "needle", "pry_bar"),
    ):
        warrior, rogue, assassin, tank = (spread(code) for code in tier)
        assert assassin > warrior > tank, tier
        assert assassin > rogue > tank, tier
        # среднее у всех четверых одно: разводим разброс, а не силу
        averages = {
            (CATALOGUE[code].damage_min + CATALOGUE[code].damage_max) / 2
            for code in tier
        }
        assert len(averages) == 1, f"{tier}: средний урон разъехался — {averages}"


def test_armour_covers_the_zone_it_is_worn_on():
    coverage = {
        "moto_helmet": (Zone.HEAD,),
        "biker_jacket": (Zone.CHEST, Zone.BELLY),
        "buckle_belt": (Zone.BELT,),
        "padded_pants": (Zone.BELT, Zone.LEGS),
        "army_boots": (Zone.LEGS,),
    }
    for code, zones in coverage.items():
        assert CATALOGUE[code].zones == zones, code
    # перчатки и оружие брони не дают вовсе
    assert CATALOGUE["battered_gloves"].zones == ()
    assert CATALOGUE["cleaver"].zones == ()


def test_every_tier_has_something_for_every_class():
    """В каждой партии товара есть вещь под каждый класс."""
    for level in (4, 5, 6, 7, 8, 9):
        covered = {code for item in items_unlocked_at(level) for code in item.for_classes}
        assert covered == set(FIGHTER_CLASSES), f"{level} уровень обошли: {covered}"


def test_a_tier_never_fits_into_one_level_of_income():
    """Развилка: за уровень партию не выкупить, но что-то из неё по карману."""
    income = credits_per_level()
    for level in (4, 5, 6, 7, 8):
        items = items_unlocked_at(level)
        cheapest_per_slot: dict = {}
        for item in items:
            best = cheapest_per_slot.get(item.slot)
            if best is None or item.price < best.price:
                cheapest_per_slot[item.slot] = item
        full_set = sum(item.price for item in cheapest_per_slot.values())
        assert full_set > income, f"{level} уровень: партия за {full_set} — не выбор"
        assert min(item.price for item in items) <= income * 4, (
            f"{level} уровень: даже самое дешёвое копить вечность"
        )


def test_pictures_are_wired_to_the_right_bucket():
    """Картинки предметов лежат в R2 и не повторяются у разных вещей."""
    from bot.game.equipment import ART, SHOWCASE

    pictures = [item.image for item in SHOWCASE if item.image]
    assert pictures, "картинок нет вовсе"
    assert len(set(pictures)) == len(pictures), "две вещи делят одну картинку"
    assert all(picture.startswith("https://") for picture in pictures)

    for item in SHOWCASE:
        if item.is_weapon:
            assert item.image.startswith(ART), f"{item.code}: не из бакета клуба"


def test_the_whole_catalogue_is_drawn():
    """Каждая вещь на прилавке нарисована — значков-заглушек не осталось."""
    from bot.game.equipment import SHOWCASE

    naked = [item.code for item in SHOWCASE if not item.picture]
    assert not naked, f"без картинки: {naked}"


def test_pictures_are_not_shared_between_items():
    """Одна картинка на две вещи — почти всегда промах при раскладке файлов."""
    from bot.game.equipment import SHOWCASE

    seen: dict[str, str] = {}
    for item in SHOWCASE:
        twin = seen.setdefault(item.picture, item.code)
        assert twin == item.code, f"{item.code} и {twin} делят картинку"


def test_the_boosted_gear_is_what_the_owner_asked_for():
    """Числа этих четырёх заданы вручную — держим их под присмотром."""
    from bot.game.equipment import get_item

    bandana = get_item("test_bandana")
    assert (bandana.intuition, bandana.crit, bandana.anticrit) == (5, 0.35, 0.25)

    wraps = get_item("test_wraps")
    assert (wraps.strength, wraps.dodge, wraps.counter, wraps.accuracy) == (
        5, 0.15, 0.15, 0.05
    )

    sneakers = get_item("test_sneakers")
    assert (sneakers.agility, sneakers.dodge, sneakers.crit) == (5, 0.15, 0.15)

    shirt = get_item("test_shirt")
    assert (shirt.strength, shirt.agility, shirt.intuition, shirt.hp) == (3, 3, 3, 60)


def test_the_boosted_gear_never_reaches_the_counter():
    """Стендовые вещи — награды: их не купить и в эталонный комплект не взять.

    Это и есть шов между стендом и балансом: числа выше потолка живут
    только в руках тестовых бойцов, а витрину держит потолок.
    """
    from bot.game.equipment import SHOWCASE, get_item
    from bot.game.reference import best_kit
    from bot.seed import GRANTED_GEAR

    shelf = {item.code for item in SHOWCASE}
    for code in GRANTED_GEAR:
        assert get_item(code).reward, code
        assert code not in shelf, code

    for fclass in FIGHTER_CLASSES.values():
        for level in range(1, 11):
            kit = {item.code for item in best_kit(fclass, level).values()}
            assert not kit & set(GRANTED_GEAR), (fclass.code, level)


# ---------- лестницы: чем выше уровень, тем крупнее числа ----------


def flat_cap(level: int) -> int:
    """Потолок плоской прибавки к силе, ловкости или интуиции.

    Прибавка растёт по ступени за два уровня. Потолок нужен именно
    плоским числам: проценты режет `MAX_DODGE_CHANCE` и его соседи уже в
    бою, а +5 к ловкости на вещи первого уровня ничем не режется и
    перебивает всю разницу между классами. На этом круг классов и
    ломался, пока усиленные вещи лежали на прилавке.
    """
    return max(1, (level - 1) // 2)


def test_flat_bonuses_grow_by_the_ladder():
    """Плоские прибавки не обгоняют свой уровень."""
    for item in SHOWCASE:
        for stat in ("strength", "agility", "intuition"):
            value = getattr(item, stat)
            assert value <= flat_cap(item.level_required), (
                f"{item.title}: +{value} к {stat} на {item.level_required} уровне"
            )


def test_prices_grow_with_the_level_inside_a_slot():
    """В своём слоте вещь дороже предыдущей ступени — иначе выбор пустой."""
    for slot in ALL_SLOTS:
        by_level: dict[int, list[int]] = {}
        for item in SHOWCASE:
            if item.slot is slot:
                by_level.setdefault(item.level_required, []).append(item.price)
        levels = sorted(by_level)
        for lower, upper in zip(levels, levels[1:]):
            assert max(by_level[lower]) <= max(by_level[upper]), (
                f"{slot.value}: {upper} уровень не дороже {lower}"
            )


# ---------- дыры на прилавке ----------


LAG = 3  # на сколько уровней класс может отстать в одном слоте


def test_no_class_is_left_without_a_slot_for_long():
    """У каждого класса в каждом слоте есть что-то не слишком старое.

    Считает не «есть ли вещь вообще», а как далеко она отстала от
    свежайшей в этом же слоте. Танк сидел на поясе второго уровня до
    самого шестого, пока весь пятый уровень поясов разобрали ассасин с
    трикстером, — и проигрывал ассасину там, где должен выигрывать.
    """
    for fclass in FIGHTER_CLASSES.values():
        for level in range(2, 11):
            for slot in ALL_SLOTS:
                shelf = [
                    item
                    for item in SHOWCASE
                    if item.slot is slot and item.level_required <= level
                ]
                if not shelf:
                    continue
                newest = max(item.level_required for item in shelf)
                mine = [item for item in shelf if fclass.code in item.for_classes]
                if not mine:
                    continue  # своего нет вовсе — возьмёт чужое, это честно
                best = max(item.level_required for item in mine)
                assert newest - best <= LAG, (
                    f"{fclass.title}: в слоте {slot.value} на {level} уровне "
                    f"вещь {best} уровня против {newest}"
                )


# ---------- картинки ----------


# Файлы, чьи имена сложились до правила «картинка лежит под кодом вещи».
# Новым вещам сюда не добавляться: их адрес считается из кода.
LEGACY_PICTURES = frozenset(
    item.code for item in ITEMS if item.image and not item.image.endswith(
        f"/items/{item.code}.jpeg"
    )
)


def test_new_items_take_their_picture_from_their_code():
    """Адрес картинки не пишут руками: его даёт код вещи.

    Исключение — старые файлы: их имена сложились раньше правила, и
    список закрыт — он собирается из того, что уже лежит в каталоге.
    Появилась вещь вне списка с явным `image=` — значит, файл назвали не
    по коду, и его проще переименовать, чем заводить второе правило.

    Правило одно на все слоты: и футболки, и оружие новых паков лежат в
    `items/` под своим кодом. Отдельные папки (`shirts/`, `weapons/`,
    `add/`) остались только у старых файлов.
    """
    from bot.game import art

    for item in ITEMS:
        if item.code in LEGACY_PICTURES:
            continue
        assert not item.image, (
            f"{item.code}: адрес картинки задан руками — назовите файл "
            f"{item.code}.jpeg и положите в items/, строка image= не нужна"
        )
        assert item.picture == art.item(item.code)


def test_every_item_has_a_picture_of_its_own():
    """У каждой вещи свой файл: общая картинка — промах при раскладке."""
    seen: dict[str, str] = {}
    for item in SHOWCASE:
        assert item.picture.startswith(ART), f"{item.code}: не из бакета клуба"
        twin = seen.setdefault(item.picture, item.code)
        assert twin == item.code, f"{item.code} и {twin} делят картинку"
