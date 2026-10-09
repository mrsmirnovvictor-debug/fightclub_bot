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
    RING_SLOTS,
    SHOWCASE,
    Item,
    Slot,
    gear_share_cap,
    items_unlocked_at,
    weapon_share_cap,
    weapon_share_floor,
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
        # Мест у вещи больше одного ровно в двух случаях: оружие берут во
        # вторую руку, а кольцо ложится в любую из трёх клеток под кольца
        if item.is_weapon:
            places = (Slot.WEAPON, Slot.OFFHAND)
        elif item.is_ring:
            places = RING_SLOTS
        else:
            places = (item.slot,)
        assert item.slots == places, item.code


def test_no_two_items_share_a_code():
    """Код — ключ вещи и в каталоге, и в базе у каждого, кто её купил.

    Каталог собирается словарём, и двойник не ссорится, а молча затирает
    первую запись: у купленной вещи меняются числа, картинка и название,
    а в лавке одной вещи просто не оказывается. Коды у новых паков
    считаются от набора и слота (`set_<набор>_<слот>`), так что столкнуть
    два набора — дело одной опечатки.
    """
    codes = [item.code for item in ITEMS]
    doubles = sorted({code for code in codes if codes.count(code) > 1})
    assert not doubles, f"код на две вещи: {doubles}"
    assert len(CATALOGUE) == len(ITEMS)


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
    """Проценты идут лестницей, и у одежды с оружием она разная.

    Главные доли живут на оружии: 10–15% на пятом уровне, 45–50% на
    десятом. Числа крупные намеренно — в бою они вычитаются парами, и
    решает разница, а не величина. Мелкая доля тонула в характеристиках:
    у трикстера с ассасином уворот и крит и без вещей упирались в свой
    потолок, и написанное на оружии в бой просто не доходило.

    У одежды полоса своя и держит сумму долей на одной вещи, а не каждую
    по отдельности: набор воина размазывает проценты по всем четырём
    парам, и мелкими долями из него собралась бы вещь сильнее крупной.
    Полоса идёт по уровню, от 5% на втором до 80% на девятом, — это и
    есть лестница сетов.

    Товар мага живёт по своим правилам: он и должен быть заметно сильнее,
    иначе за него не платили бы звёздами. Вещи с ручными числами помечены
    наградой и на прилавок не попадают — витрина проверяется целиком.
    """
    for item in SHOWCASE:
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        if item.is_weapon:
            cap = weapon_share_cap(item.level_required)
            assert max(shares) <= cap + 1e-9, (
                f"{item.title}: {max(shares):.0%} > {cap:.0%}"
            )
            continue
        cap = gear_share_cap(item.level_required)
        assert sum(shares) <= cap + 1e-9, (
            f"{item.title}: всего {sum(shares):.0%} > {cap:.0%}"
        )


def test_every_weapon_of_its_tier_carries_the_share_of_its_tier():
    """Оружие своей ступени прибавляет заметно — иначе доля ничего не решает.

    Полоса узкая (пять пунктов), и главная доля обязана в неё попасть.
    Вторая доля — ответ той паре, которой этот класс держит удар, — может
    быть и меньше: у трикстера контрудар вполовину уворота, потому что
    гасить крит — дело танка, и в этом весь круг.
    """
    for item in SHOWCASE:
        if not item.is_weapon or item.level_required < EARLY_LEVELS:
            continue
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        # Катана с двуручником долей не дают вовсе: они меняют проценты на
        # урон, и это честный выбор. А вот половина полосы — это уже
        # обещание, которого вещь не держит
        if not max(shares):
            continue
        floor = weapon_share_floor(item.level_required)
        assert max(shares) >= floor - 1e-9, (
            f"{item.title}: {max(shares):.0%} — ниже полосы {floor:.0%}"
        )


def test_the_pair_of_a_share_grows_along_with_it():
    """У каждой доли есть пара, и она идёт по той же лестнице.

    Уворот сбивается точностью, крит — антикритом. Если одна сторона
    выросла, а вторая осталась внизу, потолок и класс решают бой сами:
    трикстер становится неуязвимым, ассасин — неостановимым. Поэтому на
    каждой ступени оружия обе стороны обеих пар есть у кого-то из
    четверых, и в одной полосе.
    """
    from bot.game.equipment import CATALOGUE

    tiers: dict[int, list] = {}
    for item in CATALOGUE.values():
        if item.is_weapon and not item.is_magic and item.level_required >= EARLY_LEVELS:
            tiers.setdefault(item.level_required, []).append(item)

    for level, items in sorted(tiers.items()):
        floor = weapon_share_floor(level)
        for one, other in (("dodge", "accuracy"), ("crit", "anticrit")):
            mine = max(getattr(item, one) for item in items)
            answer = max(getattr(item, other) for item in items)
            if mine < floor:
                continue  # этой стороны на ступени нет вовсе — спорить не с чем
            assert answer >= floor - 1e-9, (
                f"{level} уровень: {one} на {mine:.0%}, а {other} только на "
                f"{answer:.0%} — пара разъехалась"
            )


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
        ("pit_fighter_baton", "cardsharp_cane", "assassin_stiletto", "bouncer_sledge"),
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

    pictures = [item.picture for item in SHOWCASE]
    assert pictures, "картинок нет вовсе"
    assert len(set(pictures)) == len(pictures), "две вещи делят одну картинку"
    assert all(picture.startswith("https://") for picture in pictures)

    for item in SHOWCASE:
        # Адрес обычно считается по коду; явный `image=` остался у старых
        # файлов, чьи имена под это правило не подходят
        assert item.picture.startswith(ART), f"{item.code}: не из бакета клуба"


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

    Ступеньку сдвинул пак сетов: футболка в нём только статами и торгует
    (брони на ней нет вовсе), и на шестом уровне даёт +4 в профильное.
    Лестница от этого не пропала — просто начинается на ступень выше.
    """
    return max(1, (level + 2) // 2)


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


# ---------- снятое с прилавка ----------


def test_the_sets_took_over_the_whole_wardrobe():
    """Пак закрыл все семь носимых слотов для всех четырёх классов."""
    from bot.content.items import SET_PIECES
    from bot.game.equipment import Slot

    assert len(SET_PIECES) == 84
    wardrobe = {
        Slot.HEAD, Slot.SHIRT, Slot.BELT, Slot.GLOVES,
        Slot.JACKET, Slot.PANTS, Slot.BOOTS,
    }
    for slot in wardrobe:
        covered = {
            code
            for item in SET_PIECES
            if item.slot is slot
            for code in item.for_classes
        }
        assert covered == set(FIGHTER_CLASSES), f"{slot.value}: {covered}"
    for item in SET_PIECES:
        assert not item.shelf and not item.retired and item.price > 0, item.code
        assert item.code.startswith("set_"), item.code


def test_the_old_wardrobe_left_the_counter_but_not_the_catalogue():
    """Снятая вещь уходит с витрины и остаётся всюду, где она уже есть.

    Цену у неё никто не отбирал: по ней лавка принимает вещь обратно, по
    ней же комиссионка держит рамки. Иначе снятие с прилавка тихо
    превратило бы чужой гардероб в вещи без цены — а их на комиссионке
    можно просить сколько угодно.
    """
    from bot.content.items import RETIRED_GEAR
    from bot.game.equipment import CATALOGUE, SHOWCASE
    from bot.game.market import buyback, has_counter_price

    shelf = {item.code for item in SHOWCASE}
    assert RETIRED_GEAR, "снимать оказалось нечего"
    for code in RETIRED_GEAR:
        item = CATALOGUE[code]
        assert item.retired, code
        assert code not in shelf, f"{code} остался на витрине"
        assert not item.is_weapon and not item.is_shield, f"{code}: оружие не снимали"
        assert has_counter_price(item) and buyback(item) > 0, code

    # Снятого в витрине нет вовсе, и наоборот
    assert not shelf & RETIRED_GEAR
    for item in SHOWCASE:
        assert not item.retired, item.code


def test_the_only_old_shirt_left_is_the_one_the_pack_rewrote():
    """Майку пак не заменил, а переписал: код и картинка прежние."""
    from bot.game.art import shirt
    from bot.game.equipment import CATALOGUE, SHOWCASE

    майка = CATALOGUE["wife_beater"]
    assert майка in SHOWCASE and not майка.retired
    assert (майка.hp, майка.price, майка.level_required) == (30, 50, 1)
    assert майка.picture == shirt("wife_beater")
    assert not майка.for_classes, "майка всем"

    old = [
        item
        for item in SHOWCASE
        if not item.is_weapon and not item.is_shield
        and not item.code.startswith("set_")
    ]
    assert [item.code for item in old] == ["wife_beater"]


# ---------- фанатский магазин: свой прилавок ----------
#
# «Северный Вал» торгует не следующей ступенью клубной лавки, а своей
# линией: десятый уровень, от тысячи кредитов — десять уровней дохода за
# одну вещь. Поэтому в витрину клуба эти вещи не входят и лестницу цен за
# собой не тянут. Но потолки процентов и плоских прибавок на них те же:
# именно они держат круг классов, а круг в фанатских комплектах
# проверяет tests/test_combat.py.


def fan_items():
    from bot.game.equipment import FAN_ITEMS

    return FAN_ITEMS


def test_the_fan_shelf_is_a_counter_of_its_own():
    """Фанатский товар не лежит на витрине клуба и не путается с ней."""
    from bot.game.equipment import FAN_SHELF, SHOWCASE

    assert len(fan_items()) == 29, "пак приехал не целиком"
    shelf = {item.code for item in SHOWCASE}
    for item in fan_items():
        assert item.shelf == FAN_SHELF, item.code
        assert item.code not in shelf, f"{item.code} попал на витрину клуба"
        assert item.price >= 1000, f"{item.code}: {item.price} — не фанатская цена"
        assert item.level_required == 10, item.code
        assert not item.stars and not item.reward, item.code


def test_the_fan_shelf_has_a_ceiling_of_its_own():
    """У фанатской одежды полоса своя и на ступень выше клубной.

    Раньше потолок был общий и маленький — десять процентов на вещь.
    Пока клубная одежда торговала теми же крохами, это держалось; с
    паком сетов клубная вещь девятого уровня стала нести до 80%, и
    фанатская за тысячу с лишним кредитов оказалась слабее сета за
    пятьсот. Приз с рейда превратился в утешительный.

    Полоса потолком и осталась — просто своя: 1.30 против клубных 0.80.
    Плоские прибавки по-прежнему считаются по общей лестнице, и
    выносливости на фанатских вещах нет, как и на всех прочих.
    """
    from bot.game.equipment import FAN_SHARE_CAP

    for item in fan_items():
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        if item.is_weapon:
            cap = weapon_share_cap(10)
            assert max(shares) <= cap + 1e-9, f"{item.title}: {max(shares):.0%}"
        else:
            assert sum(shares) <= FAN_SHARE_CAP + 1e-9, (
                f"{item.title}: всего {sum(shares):.0%}"
            )
        for stat in ("strength", "agility", "intuition"):
            assert getattr(item, stat) <= flat_cap(item.level_required), item.title
        assert item.bonus.endurance == 0, item.title


def test_the_fan_shelf_beats_the_club_sets_it_costs_twice_as_much_as():
    """За фанатскую вещь просят вдвое — и она обходит сет девятого.

    Это и есть смысл «Северного Вала»: приз с рейда и цель, ради которой
    копят. Сравниваем по слоту с тем, что боец этого класса надел бы из
    клубной лавки, — ни по одному числу фанатская вещь уступать не может.
    """
    from bot.game.classes import FIGHTER_CLASSES
    from bot.game.reference import best_kit

    # Чья это линия: ассасинскую вещь носит и трикстер, но мерят её по
    # ассасину — свою линию трикстер и так возьмёт, она дороже
    primary = {
        "fan_assassin": "assassin", "fan_rogue": "rogue",
        "fan_warrior": "warrior", "fan_boss": "tank",
    }
    club = {code: best_kit(fclass, 10) for code, fclass in FIGHTER_CLASSES.items()}
    numbers = (
        "strength", "agility", "intuition", "hp", "armor_min", "armor_max",
        "accuracy", "dodge", "crit", "anticrit", "counter",
    )

    checked = 0
    for item in fan_items():
        if item.is_weapon or item.is_shield:
            continue  # оружию и щиту клубного соперника в паке нет
        line = next(key for key in primary if item.code.startswith(key))
        rival = club[primary[line]][item.slot]
        for name in numbers:
            assert getattr(item, name) >= getattr(rival, name), (
                f"{item.title} слабее, чем «{rival.title}», по «{name}»: "
                f"{getattr(item, name)} против {getattr(rival, name)}"
            )
        assert item.price > rival.price * 2, f"{item.code}: дешевле двух сетовых"
        checked += 1
    assert checked == 24, "фанатская одежда приехала не целиком"


def test_the_fan_shelf_dresses_every_class():
    """Четыре линии, и каждому классу есть что надеть.

    Тяжёлую носит танк, среднюю — воин, лёгких две: у ассасина она под
    крит, у трикстера под уворот. Класс, которому в магазине нечего
    взять, туда и не пойдёт, а вещи оттуда встретит на чужих плечах.
    """
    covered = {code for item in fan_items() for code in item.for_classes}
    assert covered == set(FIGHTER_CLASSES), covered

    for fclass in FIGHTER_CLASSES.values():
        mine = [item for item in fan_items() if fclass.code in item.for_classes]
        assert len(mine) >= 6, f"{fclass.title}: в магазине всего {len(mine)} вещей"


def test_fan_weapons_are_worth_their_price():
    """Фанатское оружие сильнее лучшего клубного — иначе за него не платят."""
    from bot.game.equipment import SHOWCASE

    def average(item):
        return (item.damage_min + item.damage_max) / 2

    club = max(average(item) for item in SHOWCASE if item.is_weapon)
    fan = [item for item in fan_items() if item.is_weapon]
    assert len(fan) == 4
    for item in fan:
        assert average(item) > club, f"{item.code}: {average(item)} против {club}"
        assert item.price > max(one.price for one in SHOWCASE if one.is_weapon)


def test_the_fan_shelf_is_drawn_and_not_shared():
    """У каждой фанатской вещи своя картинка в общем бакете."""
    from bot.game.equipment import SHOWCASE

    seen = {item.picture for item in SHOWCASE}
    for item in fan_items():
        assert not item.image, f"{item.code}: адрес картинки задан руками"
        assert item.picture.startswith(ART), item.code
        assert item.picture not in seen, f"{item.code} делит картинку с витриной"
        seen.add(item.picture)


# ---------- ювелир: кольца и ожерелья ----------
#
# У ювелира свой прилавок, как у «Северного Вала»: в витрину клуба он не
# входит, лестницу цен за собой не тянет и эталонного бойца не трогает —
# круг классов считается по клубной лавке. Потолки процентов и плоских
# прибавок на украшениях при этом клубные: именно они держат круг.


def jewels():
    from bot.game.equipment import JEWEL_ITEMS

    return JEWEL_ITEMS


def test_the_jeweller_is_a_counter_of_its_own():
    """Украшения лежат у ювелира и больше нигде."""
    from bot.game.equipment import JEWEL_SHELF, JEWEL_SLOTS, SHOWCASE

    assert len(jewels()) == 39, "пак приехал не целиком"
    shelf = {item.code for item in SHOWCASE}
    for item in jewels():
        assert item.shelf == JEWEL_SHELF, item.code
        assert item.code not in shelf, f"{item.code} попал на витрину клуба"
        assert item.slot in JEWEL_SLOTS, item.code
        assert item.price > 0 and not item.stars and not item.reward, item.code
        assert not item.retired, item.code


def test_the_jeweller_opens_exactly_two_shelves():
    """Полок ровно две — кольца и ожерелья, как их и просили.

    Клетки под кольца три, а полка одна: кольцо записано в правую и само
    раздаёт себе остальные две. Три полки «Кольца» рядом были бы ошибкой
    раскладки, а не выбором.
    """
    from bot.game.equipment import JEWEL_SHELF, Slot, shop_sections

    full = [(slot, items) for slot, items in shop_sections(JEWEL_SHELF) if items]
    assert [slot for slot, _ in full] == [Slot.NECKLACE, Slot.RING_RIGHT]
    assert [len(items) for _, items in full] == [16, 23]
    assert [slot.section for slot, _ in full] == ["ожерелья", "кольца"]


def test_the_jeweller_keeps_to_the_club_ceilings():
    """Потолки на украшениях клубные: по ним и держится круг классов.

    Полосу процентов пак прошёл как есть: кольцо несёт одну долю, кулон
    девятого уровня — до 80%, ровно столько же, сколько сет того же
    уровня. Выносливости на украшениях нет, как и на всех прочих вещах.
    """
    for item in jewels():
        shares = (item.accuracy, item.dodge, item.crit, item.anticrit, item.counter)
        cap = gear_share_cap(item.level_required)
        assert sum(shares) <= cap + 1e-9, (
            f"{item.title}: всего {sum(shares):.0%} > {cap:.0%}"
        )
        for stat in ("strength", "agility", "intuition"):
            assert getattr(item, stat) <= flat_cap(item.level_required), item.title
        assert item.bonus.endurance == 0, item.title


def test_only_the_necklace_holds_a_blow():
    """Броня есть на старших кулонах, и она приходит в грудь.

    Кольцо не прикрывает ничего, и броня на нём осталась бы надписью:
    в бою она складывается по зонам, а у кольца зоны нет. Поэтому числа
    брони у ювелира живут только на ожерельях — и только там, где ей есть
    куда прийти.
    """
    from bot.game.classes import Zone
    from bot.game.equipment import RING_SLOTS, Slot

    armoured = [item for item in jewels() if item.armor_max]
    assert {item.slot for item in armoured} == {Slot.NECKLACE}
    assert len(armoured) == 4, "броня осталась только на старших кулонах"
    for item in armoured:
        assert item.zones == (Zone.CHEST,), item.code
        assert item.level_required == 9, item.code
    for item in jewels():
        if item.slot in RING_SLOTS:
            assert not item.armor_max and not item.armor_min, item.code


def test_the_jeweller_dresses_every_class():
    """На каждой ступени есть украшение под каждый класс.

    Иначе полка превращается в полку одного класса: кольца носят сразу по
    три, и класс, которому брать нечего, отстаёт втройне.
    """
    from bot.game.classes import FIGHTER_CLASSES
    from bot.game.equipment import Slot

    for slot in (Slot.NECKLACE, Slot.RING_RIGHT):
        shelf = [item for item in jewels() if item.slot is slot]
        tiers: dict[int, set[str]] = {}
        for item in shelf:
            tiers.setdefault(item.level_required, set()).update(item.for_classes)
        for level, covered in sorted(tiers.items()):
            assert covered == set(FIGHTER_CLASSES), (
                f"{slot.value}, {level} уровень: обошли {set(FIGHTER_CLASSES) - covered}"
            )


def test_the_jewels_are_drawn_and_not_shared():
    """У каждого украшения своя картинка в общем бакете.

    Адрес у них задан строкой, и это не оплошность: кольцо линии лежит
    под именем украшения (`ring.png`), а клеток под кольцо три — по коду
    клетки файла в бакете нет вовсе.
    """
    from bot.game.art import SETS
    from bot.game.equipment import SHOWCASE

    seen = {item.picture for item in SHOWCASE}
    for item in jewels():
        assert item.picture.startswith(f"{SETS}/"), item.code
        assert item.picture not in seen, f"{item.code} делит картинку"
        seen.add(item.picture)


def test_the_jewelled_kit_fills_all_four_cells():
    """Комплект с украшениями надевает ожерелье и три кольца, а не одно.

    Кольцо в нём одно и то же во все три клетки: кольца не уникальны, и
    лучшее своё боец купит трижды. Клубный гардероб при этом остаётся —
    украшения его не вытесняют.
    """
    from bot.game.classes import FIGHTER_CLASSES
    from bot.game.equipment import RING_SLOTS, Slot
    from bot.game.reference import best_kit, jewel_kit

    for fclass in FIGHTER_CLASSES.values():
        bare, dressed = best_kit(fclass, 9), jewel_kit(fclass, 9)
        rings = [dressed[slot] for slot in RING_SLOTS]
        assert len(set(rings)) == 1, fclass.code
        assert rings[0].is_ring and rings[0].level_required == 9, fclass.code
        necklace = dressed[Slot.NECKLACE]
        assert necklace.slot is Slot.NECKLACE and necklace.level_required == 9
        for code in (necklace.code, rings[0].code):
            assert code not in {item.code for item in bare.values()}
        # Клубное на месте: украшения добавились, а не заменили
        for slot, item in bare.items():
            assert dressed[slot] == item, slot.value
