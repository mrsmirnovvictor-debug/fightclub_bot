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
)
from bot.game.classes import ASSASSIN, FIGHTER_CLASSES, ROGUE, TANK, WARRIOR, Zone
from bot.game.combat import Action, Fighter, Outcome, resolve_round, strike_of
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


def test_energy_comes_from_hits_and_blocks():
    """Точный удар — единица, удержанный блок — единица."""
    first, second = make(user_id=1), make(WARRIOR, user_id=2)

    resolve_round(first, Action(attacks=(HEAD,), block=(BELT, LEGS)),
                  second, Action(attacks=(BELT,), block=(HEAD, CHEST)), 1,
                  random.Random(4))

    # первый бил в закрытую голову, второй — в закрытый пояс: по блоку каждому
    assert first.energy >= 1 and second.energy >= 1


def test_a_shield_block_is_worth_two():
    """Щит копит вдвое быстрее — за это слот второй руки и держат."""
    plain, shielded = make(TANK, user_id=1), make(TANK, user_id=2)
    armed(shielded, "riot_shield", Slot.OFFHAND)
    assert shielded.has_shield and not plain.has_shield

    for one in (plain, shielded):
        one.gain_energy(0)
    resolve_round(plain, Action(attacks=(HEAD,), block=(HEAD, CHEST)),
                  shielded, Action(attacks=(HEAD,), block=(HEAD, CHEST)), 1,
                  random.Random(2))

    assert shielded.energy == 2 * plain.energy == 2


def test_a_dodge_fills_nobody():
    """Шкала растёт от попаданий и блоков, а не от промахов мимо."""
    attacker, dodger = make(user_id=1), make(ROGUE, user_id=2)

    # уворот своим броском, без всякого приёма
    strike = hit(attacker, dodger, block=(BELT, LEGS), seed=1)

    assert strike.outcome is Outcome.DODGE, "нужен именно ушедший мимо удар"
    assert dodger.energy == 0 and attacker.energy == 0


def test_the_bar_has_a_ceiling():
    fighter = make()
    fighter.gain_energy(MAX_ENERGY * 3)

    assert fighter.energy == MAX_ENERGY


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
