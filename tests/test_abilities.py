"""Приёмы: чему боец учится, как копит энергию и что приём делает в бою.

Три правила проверяются строже прочих. Приём срабатывает наверняка — в
этом вся его цена: нажал «Проворность», и бросок уворота уже не спросят.
Заготовка ждёт своего момента, а не конца раунда: ушедший в блок удар не
тратит «Сильный удар». И энергия живёт только внутри боя — принести её с
собой нельзя.
"""

import random

import pytest

from bot.content.abilities import (
    ABILITIES,
    CATALOGUE,
    CHOICES,
    STARTER,
    choices_at,
    starter_of,
)
from bot.game.abilities import (
    MAX_ABILITIES,
    MAX_ENERGY,
    TIER_COST,
    TIERS,
    Effect,
    Loadout,
    Source,
    energy_gain,
)
from bot.game.classes import ASSASSIN, FIGHTER_CLASSES, ROGUE, TANK, WARRIOR, Zone
from bot.game.combat import (
    Action,
    Fighter,
    Outcome,
    Strike,
    resolve_round,
    strike_of,
)
from bot.game.equipment import CATALOGUE as GEAR, Equipment, OwnedItem, Slot

HEAD, CHEST, BELT, LEGS = Zone.HEAD, Zone.CHEST, Zone.BELT, Zone.LEGS


def make(fclass=WARRIOR, user_id=1, name="Боец", **kwargs):
    return Fighter(
        user_id=user_id, name=name, fclass=fclass, stats=fclass.base_stats, **kwargs
    )


def armed(fighter: Fighter, code: str, slot: Slot = Slot.WEAPON) -> Fighter:
    fighter.equipment = Equipment(items={slot: OwnedItem(item=GEAR[code], slot=slot)})
    return fighter


def teach(fighter: Fighter, code: str, tier: int = 1) -> Fighter:
    """Выучить приём и сразу дать энергии ровно на него."""
    fighter.loadout.learn(code, tier)
    fighter.gain_energy(TIER_COST[tier])
    return fighter


def hit(attacker, defender, block=(), zone=HEAD, seed=1):
    """Один удар при известном броске."""
    return strike_of(
        attacker, defender, zone, "кулаки", 0, Action(block=block), 1,
        random.Random(seed),
    )


# ---------- чему учат ----------


def test_the_ladder_has_four_steps_and_four_slots():
    """Четыре ступени, четыре слота: своим ходом боец заполняет их ровно."""
    assert TIERS == (1, 3, 6, 10)
    assert len(TIERS) == MAX_ABILITIES
    assert [TIER_COST[tier] for tier in TIERS] == [3, 6, 9, 12]


@pytest.mark.parametrize("code", sorted(FIGHTER_CLASSES))
def test_every_class_comes_with_its_own_and_grows_by_threes(code):
    """На первой ступени выбора нет, дальше — три приёма, свой первым."""
    assert starter_of(code).tier == 1

    for tier in (3, 6, 10):
        options = choices_at(code, tier)
        assert len(options) == 3, (code, tier)
        # классовый приём стоит первым и принадлежит своей ступени
        assert options[0].tier == tier
        # кросс-классовые взяты с этой же или более низкой ступени: приём
        # выше своего уровня чужим не достаётся
        assert all(one.tier <= tier for one in options), (code, tier)


def test_a_fighter_never_learns_the_same_thing_twice():
    """По пути бойца один приём не встречается дважды.

    Иначе развилка оказывается пустой: выбрал — а приём уже есть, и слот
    сгорел впустую.
    """
    for code in FIGHTER_CLASSES:
        path = [STARTER[code]]
        for tier in (3, 6, 10):
            # худший случай: боец каждый раз берёт классовый приём
            path.append(CHOICES[code][tier][0])
        assert len(set(path)) == len(path), code


def test_the_tank_can_learn_to_dodge():
    """Танку уворот взяться неоткуда — приём единственный способ.

    Это и есть смысл кросс-классовых развилок: они дают то, чего у класса
    нет от рождения.
    """
    assert "nimble" in [one.code for one in choices_at("tank", 3)]


def test_every_ability_says_what_it_does():
    for one in ABILITIES:
        assert one.title and one.note.endswith("."), one.code
        assert one.tier in TIERS, one.code
        # у приёма ровно один эффект, и числа под него подобраны
        if one.effect is Effect.DAMAGE:
            assert one.damage > 0, one.code
        if one.effect is Effect.HEAL:
            assert 0 < one.heal <= 1, one.code


# ---------- слоты ----------


def test_four_slots_and_not_a_fifth():
    kit = Loadout()
    for tier in TIERS:
        kit.learn(f"stub{tier}", tier)

    assert kit.full and len(kit) == MAX_ABILITIES
    with pytest.raises(ValueError, match="Все слоты заняты"):
        kit.learn("stub_extra", 10)


def test_forgetting_frees_the_slot_for_good():
    """Забытый приём уходит совсем — вернуть его нельзя."""
    kit = Loadout()
    for tier in TIERS:
        kit.learn(f"stub{tier}", tier)

    kit.relearn("stub1", "newcomer", 10)

    assert "stub1" not in kit and "newcomer" in kit
    assert len(kit) == MAX_ABILITIES


def test_the_price_belongs_to_the_step_not_to_the_trick():
    """«Сильный удар» стоит 3 своим и 6 — взятый кросс-классом на третьем.

    Платят за то, когда научился, а не за то, чему научился.
    """
    own, cross = Loadout(), Loadout()
    own.learn("strong_hit", 1)
    cross.learn("strong_hit", 3)

    assert own.cost_of("strong_hit") == 3
    assert cross.cost_of("strong_hit") == 6


# ---------- энергия ----------


def test_the_warrior_fills_his_bar_by_hitting():
    """Воин копит ударами: блок ему энергии не приносит вовсе."""
    warrior, tank = make(WARRIOR, user_id=1), make(TANK, user_id=2)

    # воин бьёт в незакрытую голову и доходит; танк бьёт в закрытый пояс
    resolve_round(warrior, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  tank, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(4))

    assert warrior.energy == energy_gain("warrior", Source.HIT)
    assert tank.energy == 0, "танк ничего не блокировал и не бил в цель"

    # а теперь воин бьёт в закрытую голову: платят за это танку, не ему
    was = warrior.energy
    resolve_round(warrior, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  tank, Action(attacks=(BELT,), block=(HEAD, CHEST)), 2,
                  random.Random(4))

    assert tank.energy == energy_gain("tank", Source.BLOCK)
    assert warrior.energy == was, "блок воину энергии не приносит"


def test_a_shield_block_is_worth_two():
    """Щит копит вдвое быстрее — за это слот второй руки и держат."""
    plain, shielded = make(TANK, user_id=1), make(TANK, user_id=2)
    armed(shielded, "riot_shield", Slot.OFFHAND)
    assert shielded.has_shield and not plain.has_shield

    resolve_round(plain, Action(attacks=(HEAD,), block=(HEAD, CHEST)),
                  shielded, Action(attacks=(HEAD,), block=(HEAD, CHEST)), 1,
                  random.Random(2))

    assert plain.energy == energy_gain("tank", Source.BLOCK)
    assert shielded.energy == 2 * plain.energy


def test_the_bar_has_a_ceiling():
    fighter = make()
    fighter.gain_energy(MAX_ENERGY * 3)

    assert fighter.energy == MAX_ENERGY


def test_each_class_fills_its_bar_with_its_own_work():
    """Шкала одна, но кормится тем, в чём класс силён.

    Событие, классу не свойственное, приносит ноль — и это не
    забывчивость, а рычаг: класс, чьи приёмы сильнее, копит медленнее.
    """
    assert energy_gain("warrior", Source.HIT) > 0
    assert energy_gain("warrior", Source.DODGE) == 0
    assert energy_gain("rogue", Source.DODGE) > 0
    assert energy_gain("tank", Source.BLOCK) > 0
    # редкое событие стоит дороже частого: критов за бой меньше одного
    assert energy_gain("assassin", Source.CRIT) > energy_gain("assassin", Source.HIT)


def test_a_shield_doubles_what_a_block_brings():
    assert energy_gain("tank", Source.BLOCK, shield=True) == 2 * energy_gain(
        "tank", Source.BLOCK
    )
    # у щита нет власти над тем, чего класс не копит вовсе
    assert energy_gain("rogue", Source.BLOCK, shield=True) == 0


def test_a_trick_never_pays_for_itself():
    """Исход, устроенный приёмом, энергии не приносит.

    Это главное правило шкалы, и держится на нём весь баланс. Без него
    гарантированный уворот начисляет за уворот, этого хватает на следующую
    «Проворность», и трикстер уворачивается вечно: петля не закрывается ни
    при какой цене события. Круг классов переворачивался именно здесь.
    """
    rogue = teach(make(ROGUE, user_id=2), "nimble")
    rogue.use("nimble")
    assert rogue.energy == 0

    resolve_round(make(user_id=1), Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  rogue, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(13))

    assert rogue.energy == 0, "приём оплатил сам себя — петля вернулась"


def test_a_dodge_you_earned_still_pays():
    """Ушёл своим броском — энергия начисляется как обычно."""
    attacker, dodger = make(user_id=1), make(ROGUE, user_id=2)

    resolve_round(attacker, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  dodger, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(1))

    assert dodger.energy == energy_gain("rogue", Source.DODGE)


def test_a_broken_block_pays_nobody_for_holding():
    """Пробитый блок защитнику не засчитывается: он не удержался."""
    attacker, defender = make(ASSASSIN, user_id=1), make(user_id=2)
    teach(attacker, "breach", tier=3)
    attacker.use("breach")

    strike = hit(attacker, defender, block=(HEAD, CHEST))

    assert strike.outcome is Outcome.BREAK
    assert defender.energy == 0


# ---------- что приём делает ----------


def test_pressing_costs_energy_and_leaves_a_charge():
    fighter = teach(make(), "strong_hit")

    fighter.use("strong_hit")

    assert fighter.energy == 0
    assert len(fighter.charges) == 1


def test_an_empty_bar_presses_nothing():
    fighter = make()
    fighter.loadout.learn("strong_hit", 1)

    assert not fighter.can_use("strong_hit")
    with pytest.raises(ValueError, match="Не хватает энергии"):
        fighter.use("strong_hit")


def test_the_dead_press_nothing():
    """Мёртвый не лечится и не накладывает приёмов на других."""
    fighter = teach(make(TANK), "recovery")
    fighter.hp = 0

    assert not fighter.can_use("recovery")
    with pytest.raises(ValueError, match="Мёртвый"):
        fighter.use("recovery")


def test_a_strong_hit_adds_its_damage():
    """Прибавка доходит до тела — и её видно в сравнении с тем же броском."""
    attacker, defender = make(user_id=1), make(user_id=2)
    plain = hit(attacker, defender, seed=7)

    boosted_attacker = teach(make(user_id=1), "strong_hit")
    boosted_attacker.use("strong_hit")
    boosted = hit(boosted_attacker, make(user_id=2), seed=7)

    assert boosted.damage > plain.damage
    assert boosted.ability == "strong_hit"
    assert not boosted_attacker.charges, "заготовка сработала и снялась"


def test_nimbleness_never_asks_the_dice():
    """Уворот по приёму не спрашивает броска — в этом его цена.

    Танк уворачиваться не умеет вовсе, поэтому проверяем на нём: своим
    броском он бы не ушёл никогда.
    """
    attacker = make(ASSASSIN, user_id=1)
    tank = teach(make(TANK, user_id=2), "nimble", tier=3)
    tank.use("nimble")

    strike = hit(attacker, tank, seed=3)

    assert strike.outcome is Outcome.DODGE
    assert strike.defence_ability == "nimble"


def test_cunning_dodges_and_answers():
    attacker = make(user_id=1)
    rogue = teach(make(ROGUE, user_id=2), "cunning", tier=3)
    rogue.use("cunning")

    strike = hit(attacker, rogue, seed=5)

    assert strike.outcome is Outcome.COUNTER
    assert strike.counter_damage > 0


def test_guile_answers_with_a_critical():
    """«Коварство» — та же «Хитрость», но ответ бьёт критом."""
    plain_rogue = teach(make(ROGUE, user_id=2), "cunning", tier=3)
    plain_rogue.use("cunning")
    plain = hit(make(user_id=1), plain_rogue, seed=5)

    sly = teach(make(ROGUE, user_id=2), "guile", tier=6)
    sly.use("guile")
    sharp = hit(make(user_id=1), sly, seed=5)

    assert sharp.counter_damage > plain.counter_damage


def test_a_critical_hit_needs_no_luck():
    attacker = teach(make(TANK, user_id=1), "crit_hit", tier=3)
    attacker.use("crit_hit")

    strike = hit(attacker, make(user_id=2), seed=11)

    assert strike.outcome is Outcome.CRIT


def test_a_breach_opens_a_closed_zone():
    """«Пролом» проходит сквозь блок без броска."""
    attacker = teach(make(ASSASSIN, user_id=1), "breach", tier=3)
    attacker.use("breach")

    strike = hit(attacker, make(TANK, user_id=2), block=(HEAD, CHEST), seed=9)

    assert strike.outcome is Outcome.BREAK and strike.damage > 0


def test_a_deadly_hit_doubles_what_lands():
    crit = teach(make(ASSASSIN, user_id=1), "crit_hit", tier=1)
    crit.use("crit_hit")
    ordinary = hit(crit, make(user_id=2), seed=13)

    deadly = teach(make(ASSASSIN, user_id=1), "deadly_hit", tier=6)
    deadly.use("deadly_hit")
    doubled = hit(deadly, make(user_id=2), seed=13)

    assert doubled.outcome is Outcome.CRIT
    assert doubled.damage > ordinary.damage


def test_a_parry_lets_the_hit_through_and_eats_it():
    """Удар доходит, но урона не наносит — хоть обычный, хоть крит."""
    attacker = teach(make(ASSASSIN, user_id=1), "crit_hit")
    attacker.use("crit_hit")
    tank = teach(make(TANK, user_id=2), "parry", tier=6)
    tank.use("parry")

    strike = hit(attacker, tank, seed=6)

    assert strike.outcome is Outcome.CRIT, "приём не мешает удару быть критом"
    assert strike.damage == 0
    assert strike.defence_ability == "parry"


def test_healing_is_instant_and_capped():
    tank = teach(make(TANK), "recovery")
    tank.hp = 10

    gained = tank.heal_by(CATALOGUE["recovery"].heal)

    assert gained == round(tank.max_hp * 0.10)
    tank.hp = tank.max_hp - 1
    assert tank.heal_by(0.5) == 1, "выше запаса не лечит"


# ---------- заготовка ждёт своего момента ----------


def test_a_charge_waits_for_the_hit_that_lands():
    """Удар ушёл в блок — «Сильный удар» остался ждать следующего."""
    attacker = teach(make(user_id=1), "strong_hit")
    attacker.use("strong_hit")

    blocked = hit(attacker, make(TANK, user_id=2), block=(HEAD, CHEST), seed=2)

    assert blocked.outcome is Outcome.BLOCK
    assert attacker.charges, "заготовка не должна тратиться на блоке"


def test_a_dodge_charge_is_not_spent_on_a_blocked_strike():
    """Удар, пришедший в собственный блок, «Проворность» не тратит."""
    attacker = make(user_id=1)
    tank = teach(make(TANK, user_id=2), "nimble", tier=3)
    tank.use("nimble")

    strike = hit(attacker, tank, block=(HEAD, CHEST), seed=8)

    assert strike.outcome is Outcome.BLOCK
    assert tank.charges, "уворот ждёт удара, который надо уворачивать"


# ---------- хранение и выбор на уровне ----------


def grown(level: int, class_code: str = "warrior"):
    """Боец нужного уровня, без единого выученного приёма."""
    from bot.models import Player

    fclass = FIGHTER_CLASSES[class_code]
    return Player(
        user_id=42, nickname="Тайлер", class_code=class_code, level=level,
        **fclass.base_stats.as_dict(),
    )


async def test_the_class_trick_comes_without_asking(db):
    """Первая ступень выбора не знает: класс приходит со своим приёмом."""
    from bot.abilities_service import ensure_starter, pending_choice

    player = grown(1, "tank")
    await db.save_player(player)

    given = await ensure_starter(db, player)

    assert given.code == "recovery"
    assert pending_choice(player) is None, "на первой ступени не выбирают"
    # и он лёг в базу, а не только в память
    assert (await db.list_abilities(42)).slots == {"recovery": 1}


async def test_the_debt_is_counted_from_the_fighter_himself(db):
    """Долг по приёму не хранится: он виден из уровня и слотов.

    Поэтому боец, выросший до третьего уровня задолго до появления
    приёмов, получает развилку сам — без разовой раздачи, о которой
    однажды забыли бы.
    """
    from bot.abilities_service import ensure_starter, pending_choice, pending_tier

    old_timer = grown(6, "rogue")
    await db.save_player(old_timer)
    await ensure_starter(db, old_timer)

    # шестой уровень, а взят только стартовый — должен две ступени
    assert pending_tier(old_timer) == 3
    choice = pending_choice(old_timer)
    assert choice.tier == 3
    assert [one.code for one in choice.options] == list(CHOICES["rogue"][3])


async def test_the_steps_are_taken_from_the_bottom(db):
    """Перескочивший через ступень выбирает по одной, снизу вверх."""
    from bot.abilities_service import ensure_starter, learn, pending_tier

    player = grown(10, "warrior")
    await db.save_player(player)
    await ensure_starter(db, player)

    assert pending_tier(player) == 3
    await learn(db, player, "power_hit")
    assert pending_tier(player) == 6, "шестая ступень не даётся раньше третьей"
    await learn(db, player, "crushing_hit")
    assert pending_tier(player) == 10


async def test_a_trick_from_another_step_is_refused(db):
    from bot.abilities_service import AbilityError, ensure_starter, learn

    player = grown(3, "warrior")
    await db.save_player(player)
    await ensure_starter(db, player)

    with pytest.raises(AbilityError, match="выбирают из трёх"):
        await learn(db, player, "mass_hit")  # приём десятой ступени

    assert (await db.list_abilities(42)).slots == {"strong_hit": 1}


async def test_nothing_to_choose_before_the_next_step(db):
    from bot.abilities_service import AbilityError, ensure_starter, learn

    player = grown(2, "tank")
    await db.save_player(player)
    await ensure_starter(db, player)

    with pytest.raises(AbilityError, match="Выбирать нечего"):
        await learn(db, player, "will_to_win")


async def test_what_is_learned_survives_a_reload(db):
    """Приёмы поднимаются вместе с бойцом — как вещи и склянки."""
    from bot.abilities_service import ensure_starter, learn

    player = grown(3, "assassin")
    await db.save_player(player)
    await ensure_starter(db, player)
    await learn(db, player, "breach")

    fresh = await db.get_player(42)

    assert fresh.loadout.slots == {"crit_hit": 1, "breach": 3}
    assert fresh.loadout.cost_of("breach") == 6


async def test_the_fighter_takes_his_tricks_to_the_ring_but_not_his_energy(db):
    """На ринг приёмы едут, энергия — нет: она копится с нуля каждый бой."""
    from bot.abilities_service import ensure_starter, learn

    player = grown(3, "rogue")
    await db.save_player(player)
    await ensure_starter(db, player)
    await learn(db, player, "cunning")

    fighter = Fighter.from_player(await db.get_player(42))

    assert fighter.loadout.slots == {"nimble": 1, "cunning": 3}
    assert fighter.energy == 0
    # слоты — копия: бой не должен править запись игрока
    fighter.loadout.slots.clear()
    assert (await db.get_player(42)).loadout.slots


# ---------- вторая половина приёмов десятой ступени ----------


def squad(*codes, side=0, start=1):
    """Отряд из бойцов одной стороны: id → боец, id → сторона."""
    fighters, sides = {}, {}
    for offset, code in enumerate(codes):
        uid = start + offset
        fighters[uid] = make(FIGHTER_CLASSES[code], user_id=uid, name=f"Боец{uid}")
        sides[uid] = side
    return fighters, sides


def struck(attacker_id, defender_id, ability="", defence=""):
    """Удар, на котором сработал приём, — так его запоминает движок."""
    from bot.game.combat import Strike

    return Strike(
        attacker_id=attacker_id, defender_id=defender_id, zone=HEAD,
        outcome=Outcome.HIT, ability=ability, defence_ability=defence,
    )


def test_a_mass_hit_reaches_everyone_but_the_one_already_struck():
    """«Массовый удар» достаёт остальных врагов — и только врагов."""
    from bot.game.combat import group_aftermath

    ours, our_side = squad("warrior", "tank", side=0, start=1)
    theirs, their_side = squad("rogue", "assassin", "warrior", side=1, start=10)
    everyone = {**ours, **theirs}
    sides = {**our_side, **their_side}
    before = {uid: one.hp for uid, one in everyone.items()}

    echoes = group_aftermath([struck(1, 10, ability="mass_hit")], everyone, sides)

    echo = echoes[0]
    # тому, кого ударили, второй раз не достаётся: ему хватило удара
    assert set(echo.splashed) == {11, 12}
    assert all(damage == 15 for damage in echo.splashed.values())
    assert everyone[10].hp == before[10], "уже получивший не платит дважды"
    assert everyone[2].hp == before[2], "свои под массовый удар не попадают"
    assert everyone[1].damage_dealt == 30, "разлёт идёт в счёт нанесённого"


def test_the_trickster_god_hands_nimbleness_to_the_squad():
    """«Бог обмана» кладёт «Проворность» союзникам — даром."""
    from bot.game.combat import group_aftermath

    ours, our_side = squad("rogue", "warrior", "tank", side=0, start=1)
    theirs, their_side = squad("assassin", side=1, start=10)
    everyone, sides = {**ours, **theirs}, {**our_side, **their_side}

    echoes = group_aftermath(
        [struck(10, 1, defence="trickster_god")], everyone, sides
    )

    assert echoes[0].blessed == (2, 3)
    for uid in (2, 3):
        assert [c.ability.code for c in everyone[uid].charges] == ["nimble"]
        assert everyone[uid].energy == 0, "за чужой приём союзник не платит"
    # врагу не достаётся, и себе тоже — своя половина уже сработала
    assert not everyone[10].charges and not everyone[1].charges


def test_a_gift_is_not_stacked_twice():
    """Две одинаковые заготовки уводят от одного удара — вторая лишняя."""
    from bot.game.combat import group_aftermath, lay_charge

    ours, sides = squad("rogue", "warrior", side=0, start=1)
    assert lay_charge(ours[2], "nimble")

    group_aftermath([struck(2, 1, defence="trickster_god")], ours, sides)

    assert len(ours[1].charges) <= 1
    assert not lay_charge(ours[2], "nimble"), "дважды одно не кладётся"


def test_the_dead_are_neither_healed_nor_blessed():
    """Приём лечит, а не воскрешает."""
    from bot.game.combat import group_aftermath, lay_charge, spread_heal

    ours, our_side = squad("tank", "warrior", "rogue", side=0, start=1)
    theirs, their_side = squad("assassin", side=1, start=10)
    everyone, sides = {**ours, **theirs}, {**our_side, **their_side}
    ours[2].hp = 0  # павший
    ours[3].hp = 5

    healed = spread_heal(CATALOGUE["life_master"], ours[1], [ours[2], ours[3]])

    assert 2 not in healed, "мёртвого не лечат"
    assert healed[3] > 0
    assert not lay_charge(ours[2], "nimble"), "мёртвому заготовку не кладут"

    # и в общей раздаче павший тоже пропущен: жив только третий
    echo = group_aftermath(
        [struck(1, 10, ability="blood_call")], everyone, sides
    )[0]

    assert echo.blessed == (3,)
    assert not ours[2].charges


def test_a_royale_has_no_allies_at_all():
    """В мясорубке каждый сам за себя: раздавать приём некому.

    Зато «Массовый удар» достаёт там вообще всех — в этом она и есть.
    """
    from bot.game.combat import group_aftermath

    fighters = {
        uid: make(WARRIOR, user_id=uid, name=f"Боец{uid}") for uid in (1, 2, 3, 4)
    }
    sides = {uid: uid for uid in fighters}  # каждый своей стороной

    blessing = group_aftermath([struck(2, 1, defence="trickster_god")], fighters, sides)
    assert blessing == [], "союзников нет — и раздачи нет"

    mass = group_aftermath([struck(1, 2, ability="mass_hit")], fighters, sides)
    assert set(mass[0].splashed) == {3, 4}


def test_a_raid_squad_shares_the_blessing_but_not_the_splash():
    """В рейде отряд заодно, а противник один — разлетаться некуда."""
    from bot.game.combat import group_aftermath

    BOSS = 999
    squad_ids = (1, 2, 3)
    fighters = {
        uid: make(FIGHTER_CLASSES["assassin"], user_id=uid, name=f"Боец{uid}")
        for uid in squad_ids
    }
    fighters[BOSS] = make(TANK, user_id=BOSS, name="Босс")
    sides = {uid: 0 for uid in squad_ids} | {BOSS: 1}

    call = group_aftermath([struck(1, BOSS, ability="blood_call")], fighters, sides)
    assert call[0].blessed == (2, 3)

    mass = group_aftermath([struck(1, BOSS, ability="mass_hit")], fighters, sides)
    assert mass == [], "бить больше некого — приём молчит второй половиной"


def test_a_splash_can_finish_someone_off():
    """Разлёт добивает — и судья обязан об этом сказать."""
    from bot.game.combat import group_aftermath

    ours, our_side = squad("warrior", side=0, start=1)
    theirs, their_side = squad("rogue", "rogue", side=1, start=10)
    theirs[11].hp = 5
    everyone, sides = {**ours, **theirs}, {**our_side, **their_side}

    echo = group_aftermath([struck(1, 10, ability="mass_hit")], everyone, sides)[0]

    assert echo.fallen == (11,) and not everyone[11].alive


def test_ordinary_tricks_leave_the_squad_alone():
    """Приёмы младших ступеней второй половины не имеют вовсе."""
    from bot.game.combat import group_aftermath

    ours, sides = squad("warrior", "tank", "rogue", side=0, start=1)

    assert group_aftermath([struck(1, 2, ability="strong_hit")], ours, sides) == []
    assert group_aftermath([struck(1, 2, defence="nimble")], ours, sides) == []


# ---------- до трёх приёмов за ход ----------


def test_three_tricks_a_turn_and_not_a_fourth():
    """Норма на ход — три приёма, четвёртый не пускают."""
    from bot.game.abilities import MAX_PER_TURN

    fighter = make(WARRIOR)
    for code, tier in (("strong_hit", 1), ("power_hit", 3),
                       ("crushing_hit", 6), ("mass_hit", 10)):
        fighter.loadout.learn(code, tier)
    fighter.gain_energy(MAX_ENERGY)

    for code in ("strong_hit", "power_hit", "crushing_hit"):
        fighter.use(code)

    assert fighter.pressed == MAX_PER_TURN and fighter.out_of_turns
    assert not fighter.can_use("mass_hit"), "энергия есть, а норма выбрана"
    with pytest.raises(ValueError, match="не больше"):
        fighter.use("mass_hit")


def test_the_same_trick_is_not_pressed_twice():
    """Две одинаковые заготовки — вторая просто сгорела бы."""
    fighter = teach(make(), "strong_hit")
    fighter.gain_energy(MAX_ENERGY)
    fighter.use("strong_hit")

    assert not fighter.can_use("strong_hit")
    with pytest.raises(ValueError, match="уже наготове"):
        fighter.use("strong_hit")


def test_the_turn_norm_starts_over_every_round():
    """Норма выбирается заново каждый раунд, а заготовки остаются."""
    first, second = make(user_id=1), make(user_id=2)
    first.loadout.learn("strong_hit", 1)
    first.gain_energy(MAX_ENERGY)
    first.use("strong_hit")
    assert first.pressed == 1

    resolve_round(first, Action(attacks=(HEAD,), block=(HEAD, CHEST)),
                  second, Action(attacks=(BELT,), block=(BELT, LEGS)), 1,
                  random.Random(5))

    assert first.pressed == 0, "норма на новый ход свежая"


def test_damage_bonuses_add_up():
    """Три прибавки к урону дают сумму, а не самую крупную из них."""
    plain = hit(make(user_id=1), make(user_id=2), seed=17)

    loaded = make(user_id=1)
    for code, tier in (("strong_hit", 1), ("power_hit", 3), ("crushing_hit", 6)):
        loaded.loadout.learn(code, tier)
    loaded.gain_energy(MAX_ENERGY)
    for code in ("strong_hit", "power_hit", "crushing_hit"):
        loaded.use(code)

    together = hit(loaded, make(user_id=2), seed=17)

    # +15, +30 и +45 — до брони, поэтому на теле видно чуть меньше суммы,
    # но заметно больше любой одной прибавки
    assert together.damage > plain.damage + 45
    assert not loaded.charges, "все три заготовки сработали разом"


def test_the_strongest_of_a_branch_works_and_the_rest_step_aside():
    """Из приёмов одной ветки срабатывает сильнейший, снимаются все."""
    crit_only = make(ASSASSIN, user_id=1)
    crit_only.loadout.learn("crit_hit", 1)
    crit_only.gain_energy(MAX_ENERGY)
    crit_only.use("crit_hit")
    ordinary = hit(crit_only, make(user_id=2), seed=21)

    both = make(ASSASSIN, user_id=1)
    both.loadout.learn("crit_hit", 1)
    both.loadout.learn("deadly_hit", 6)
    both.gain_energy(MAX_ENERGY)
    both.use("crit_hit")
    both.use("deadly_hit")
    doubled = hit(both, make(user_id=2), seed=21)

    assert doubled.damage > ordinary.damage, "работает удвоение, а не простой крит"
    assert not both.charges, "обе заготовки сняты: за них уже заплачено"
    assert doubled.ability == "deadly_hit"


def test_a_strike_and_a_dodge_work_in_the_same_turn():
    """Приёмы разных веток не мешают друг другу — в этом весь смысл."""
    rogue = make(ROGUE, user_id=2)
    rogue.loadout.learn("nimble", 1)
    rogue.loadout.learn("strong_hit", 3)
    rogue.gain_energy(MAX_ENERGY)
    rogue.use("nimble")
    rogue.use("strong_hit")

    # чужой удар уходит в пустоту: сработала «Проворность»
    incoming = hit(make(ASSASSIN, user_id=1), rogue, seed=3)
    assert incoming.outcome is Outcome.DODGE
    assert incoming.defence_ability == "nimble"

    # а прибавка к урону осталась ждать своего удара
    assert [charge.ability.code for charge in rogue.charges] == ["strong_hit"]
    mine = hit(rogue, make(user_id=1), seed=17)
    assert mine.ability == "strong_hit"


# ---------- почему приём не пошёл ----------


def test_the_refusal_names_the_real_reason():
    """Отказ обязан называть то, что не сложилось, а не всё сводить к энергии.

    Боец с полной шкалой читал «не хватает энергии» и справедливо не
    понимал, куда та энергия делась.
    """
    fighter = make()
    fighter.loadout = Loadout(slots={"strong_hit": 1, "nimble": 3, "crit_hit": 6,
                                     "guile": 10})
    fighter.energy = MAX_ENERGY

    fighter.use("strong_hit")
    with pytest.raises(ValueError, match="уже наготове"):
        fighter.use("strong_hit")

    fighter.use("nimble")
    fighter.use("crit_hit")
    with pytest.raises(ValueError, match="кончились"):
        fighter.use("guile")

    # а когда дело и правда в кошельке — говорим числами
    fighter.pressed = 0
    fighter.charges.clear()
    fighter.energy = 1
    with pytest.raises(ValueError, match="нужно 12, а накоплено 1"):
        fighter.use("guile")


def test_the_judge_names_the_ability_that_worked():
    """Приём, сработавший на ударе, судья называет вслух.

    Без этого заготовка срабатывала молча: энергия ушла, а строка боя
    ровно та же, что была бы без приёма. В рейде между нажатием и разменом
    проходит вся волна, и связать одно с другим было нечем.
    """
    from bot.game.narrator import ability_marks

    assert "Сильный удар" in ability_marks(
        Strike(1, 2, None, Outcome.HIT, ability="strong_hit")
    )
    assert "Проворность" in ability_marks(
        Strike(1, 2, None, Outcome.DODGE, defence_ability="nimble")
    )
    # обычный удар судья ничем не метит
    assert ability_marks(Strike(1, 2, None, Outcome.HIT)) == ""
    # сработали оба — назовём обоих
    both = ability_marks(
        Strike(1, 2, None, Outcome.HIT, ability="strong_hit", defence_ability="nimble")
    )
    assert "Сильный удар" in both and "Проворность" in both
