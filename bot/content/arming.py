"""Чем и как берут: вид оружия и хват у каждой вещи в руках.

Правила владения — в `bot.game.mastery`, сами вещи — в
`bot.content.items`. Здесь таблица между ними: какому оружию какое
семейство и какой рукой его держат. Таблица, а не поле у каждой вещи, —
потому что размечать пришлось весь каталог разом, и таблицу видно
целиком: в ней сразу заметно, что у ассасина вся энергетическая линейка
собрана из кинжалов, а у трикстера нет ни одного меча.

**Семейств четыре, и в них сведено всё.** Цепи, трости, зонты, ломы и
посохи — дубины и булавы: ими машут. Тесак, секира и колун — молоты и
топоры: они тяжёлые. Длинные клинки — мечи, короткие и кастеты — ножи.
Пятое семейство под экзотику завести было можно, но первая версия от
него только раздулась бы: пятый столбец на экране и пятый пул очков
ради трёх тростей.

**Огнестрелу владение не требуется.** Он вне четырёх семейств, и
стрелять — это не держать: своё владение ему заведут, когда огнестрела
станет больше одного.

**Хват — это про вещь.** `OFFHAND` значит «годится и во вторую руку», а
не «только во вторую»: нож остаётся ножом в любой руке. Двуручных в
первой версии двое — те, чьё название само об этом говорит; остальным
тяжёлым оружием хват решают вместе с балансом двуручного урона.
"""

from __future__ import annotations

from bot.game.gear import WeaponHand
from bot.game.mastery import WeaponFamily

KNIVES = WeaponFamily.KNIVES.value
CLUBS = WeaponFamily.CLUBS.value
HAMMERS = WeaponFamily.HAMMERS.value
SWORDS = WeaponFamily.SWORDS.value

# Код вещи → семейство. Хват по умолчанию основной; кто берётся и второй
# рукой, перечислен ниже отдельно, а двуручные — своим списком
FAMILIES: dict[str, str] = {
    # ---------- ножи и кастеты ----------
    "hidden_blade": KNIVES,
    "knuckles": KNIVES,
    "awl": KNIVES,
    "knife": KNIVES,
    "switchblade": KNIVES,
    "assassin_stiletto": KNIVES,
    "stiletto": KNIVES,
    "balisong": KNIVES,
    "ice_pick": KNIVES,
    "needle": KNIVES,
    "razor": KNIVES,
    "shiv": KNIVES,
    "fan_assassin_knife": KNIVES,
    "vcpd_stun_device": KNIVES,
    "set_boxer_weapon": KNIVES,
    "set_camouflage_weapon": KNIVES,
    "set_fighter_weapon": KNIVES,
    "set_lovkach_weapon": KNIVES,
    "set_cardsharp_weapon": KNIVES,
    "set_killer_weapon": KNIVES,
    "set_mercenary_weapon": KNIVES,
    "assassin_energy_dagger": KNIVES,
    "assassin_energy_karambit": KNIVES,
    "assassin_energy_push_dagger": KNIVES,
    "trickster_energy_hookblade": KNIVES,
    "trickster_energy_sickle": KNIVES,
    # ---------- дубины и булавы ----------
    "chief_baton": CLUBS,
    "crowbar": CLUBS,
    "pipe": CLUBS,
    "cardsharp_cane": CLUBS,
    "pit_fighter_baton": CLUBS,
    "bat": CLUBS,
    "chain": CLUBS,
    "pry_bar": CLUBS,
    "nail_bat": CLUBS,
    "fan_boss_bat": CLUBS,
    "fan_rogue_umbrella": CLUBS,
    "fan_warrior_bat": CLUBS,
    "vcpd_baton": CLUBS,
    "set_sport_weapon": CLUBS,
    "set_plut_weapon": CLUBS,
    "set_croupier_weapon": CLUBS,
    "set_strangler_weapon": CLUBS,
    "set_bouncer_weapon": CLUBS,
    "trickster_energy_baton": CLUBS,
    "trickster_energy_staff": CLUBS,
    "warrior_energy_mace": CLUBS,
    # ---------- молоты и топоры ----------
    "bouncer_sledge": HAMMERS,
    "sledge": HAMMERS,
    "fire_axe": HAMMERS,
    "cleaver": HAMMERS,
    "splitting_axe": HAMMERS,
    "viking_axe": HAMMERS,
    "boss_sledge": HAMMERS,
    "set_avenger_weapon": HAMMERS,
    "set_biker_weapon": HAMMERS,
    "set_army_weapon": HAMMERS,
    "tank_energy_cleaver": HAMMERS,
    "tank_energy_greataxe": HAMMERS,
    "tank_energy_hammer": HAMMERS,
    "tank_energy_maul": HAMMERS,
    "warrior_energy_axe": HAMMERS,
    # ---------- мечи ----------
    "lightsaber": SWORDS,
    "machete": SWORDS,
    "katana": SWORDS,
    "greatsword": SWORDS,
    "military_sword": SWORDS,
    "assassin_energy_wakizashi": SWORDS,
    "warrior_energy_sabre": SWORDS,
    "warrior_energy_sword": SWORDS,
    "set_hero_weapon": SWORDS,
}

# Кто берётся и второй рукой. Это всё лёгкое: короткий клинок, кастет,
# заточка. Тяжёлым второй руки не положено — ею его не удержать, и
# парная кувалда была бы не сборкой, а шуткой
OFFHAND_READY: frozenset[str] = frozenset(
    code for code, family in FAMILIES.items() if family == KNIVES
)

# Двуручные. Пока их двое, и обоих выдаёт собственное название:
# двуручный меч и длинный меч героя. Остальному тяжёлому оружию хват
# назначают вместе с балансом двуручного урона — иначе кувалда просто
# лишилась бы щита, ничего не получив взамен
TWO_HANDED: frozenset[str] = frozenset({"greatsword", "set_hero_weapon"})


def family_of(code: str) -> str:
    """Семейство этой вещи. Пусто — вида у неё нет."""
    return FAMILIES.get(code, "")


def hand_of(code: str) -> WeaponHand:
    """Каким хватом берут эту вещь."""
    if code in TWO_HANDED:
        return WeaponHand.TWO_HANDED
    if code in OFFHAND_READY:
        return WeaponHand.OFFHAND
    return WeaponHand.MAIN


__all__ = [
    "FAMILIES",
    "OFFHAND_READY",
    "TWO_HANDED",
    "family_of",
    "hand_of",
]
