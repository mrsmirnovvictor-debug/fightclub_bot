"""Чем и как берут: вид оружия и вес у каждой вещи в руках.

Правила владения — в `bot.game.mastery`, сами вещи — в
`bot.content.items`. Здесь таблица между ними: какому оружию какой вид
и какой вес. Таблица, а не поле у каждой вещи, — потому что размечать
пришлось весь каталог разом, и таблицу видно целиком: в ней сразу
заметно, что рубящего на весь класс воина семь вещей, а дробящего
двадцать шесть.

**Вид — это тип урона.** Колющее (ножи, кинжалы, кастеты, трости) —
трикстеру, рубящее (топоры, секиры) — воину, дробящее (дубины, молоты)
— танку, режущее (мечи) — ассасину. Прежде топоры лежали вместе с
молотами у танка, а дубины — у воина; теперь они разъехались по типу
удара, и это главная правка этой таблицы.

**Вес решает хват.** Лёгкое берут любой рукой: им можно взять второе
оружие или оставить руку под щит. Тяжёлое — секиры, кувалды, посох,
двуручные мечи — занимает обе руки, и рядом с ним не встаёт ни щит, ни
второе оружие. Третьего веса нет: «одноручное, но в пару не годится»
пришлось бы объяснять в каждой карточке, а объяснить это нечем.

**У колющих тяжёлых не бывает.** Двуручного ножа в клубе нет, и
отдача колющего — парный хват, за который платят очками навыка
«Второе оружие». Проверка держит это правило на всём каталоге.

**Огнестрелу владение не требуется.** Он вне четырёх видов, и стрелять
— это не держать: своё владение ему заведут, когда огнестрела станет
больше одного.
"""

from __future__ import annotations

from bot.game.gear import WeaponHand
from bot.game.mastery import WeaponFamily

PIERCING = WeaponFamily.PIERCING.value
CHOPPING = WeaponFamily.CHOPPING.value
CRUSHING = WeaponFamily.CRUSHING.value
SLASHING = WeaponFamily.SLASHING.value

# Код вещи → вид оружия. Вес перечислен ниже отдельным списком: тяжёлых
# меньше, и списком их видно наперечёт
FAMILIES: dict[str, str] = {
    # ---------- колющее: ножи, кинжалы, кастеты, трости ----------
    "hidden_blade": PIERCING,
    "knuckles": PIERCING,
    "awl": PIERCING,
    "knife": PIERCING,
    "switchblade": PIERCING,
    "assassin_stiletto": PIERCING,
    "stiletto": PIERCING,
    "balisong": PIERCING,
    "ice_pick": PIERCING,
    "needle": PIERCING,
    "razor": PIERCING,
    "shiv": PIERCING,
    "fan_assassin_knife": PIERCING,
    "vcpd_stun_device": PIERCING,
    # Трость и зонт — сюда же, как сказано в постановке: ими колют, а не
    # машут, и у трикстера это его оружие по виду и по повадке
    "cardsharp_cane": PIERCING,
    "fan_rogue_umbrella": PIERCING,
    "set_lovkach_weapon": PIERCING,
    "set_cardsharp_weapon": PIERCING,
    "set_plut_weapon": PIERCING,
    "set_croupier_weapon": PIERCING,
    "set_plut_pair": PIERCING,
    "set_croupier_pair": PIERCING,
    "set_lovkach_pair": PIERCING,
    "set_cardsharp_pair": PIERCING,
    "assassin_energy_dagger": PIERCING,
    "assassin_energy_karambit": PIERCING,
    "assassin_energy_push_dagger": PIERCING,
    "trickster_energy_hookblade": PIERCING,
    "trickster_energy_sickle": PIERCING,
    # ---------- рубящее: топоры и секиры ----------
    "fire_axe": CHOPPING,
    "cleaver": CHOPPING,
    "splitting_axe": CHOPPING,
    "viking_axe": CHOPPING,
    "warrior_energy_axe": CHOPPING,
    "tank_energy_cleaver": CHOPPING,
    "tank_energy_greataxe": CHOPPING,
    # Воинские наборы: рубящее своего класса. Топор байкера и стальной
    # топор звались булавами, пока воину советовали дубины, а меч героя
    # — мечом: виды разъехались по типу удара, и они вернулись к топорам
    "set_sport_weapon": CHOPPING,
    "set_biker_weapon": CHOPPING,
    "set_fighter_weapon": CHOPPING,
    "set_hero_weapon": CHOPPING,
    "set_sport_heavy": CHOPPING,
    "set_biker_heavy": CHOPPING,
    "set_fighter_heavy": CHOPPING,
    "set_hero_pair": CHOPPING,
    "carpenter_axe": CHOPPING,
    "butcher_axe": CHOPPING,
    "logger_axe": CHOPPING,
    "woodsman_axe": CHOPPING,
    "battle_axe": CHOPPING,
    # ---------- дробящее: дубины и молоты ----------
    "chief_baton": CRUSHING,
    "crowbar": CRUSHING,
    "pipe": CRUSHING,
    "pit_fighter_baton": CRUSHING,
    "bat": CRUSHING,
    "chain": CRUSHING,
    "pry_bar": CRUSHING,
    "nail_bat": CRUSHING,
    "fan_boss_bat": CRUSHING,
    "fan_warrior_bat": CRUSHING,
    "vcpd_baton": CRUSHING,
    "bouncer_sledge": CRUSHING,
    "sledge": CRUSHING,
    "boss_sledge": CRUSHING,
    "set_avenger_weapon": CRUSHING,
    "set_boxer_weapon": CRUSHING,
    "set_army_weapon": CRUSHING,
    "set_bouncer_weapon": CRUSHING,
    "set_avenger_heavy": CRUSHING,
    "set_boxer_heavy": CRUSHING,
    "set_bouncer_heavy": CRUSHING,
    "set_army_pair": CRUSHING,
    "trickster_energy_baton": CRUSHING,
    "trickster_energy_staff": CRUSHING,
    "tank_energy_hammer": CRUSHING,
    "tank_energy_maul": CRUSHING,
    "warrior_energy_mace": CRUSHING,
    # ---------- режущее: мечи ----------
    "lightsaber": SLASHING,
    "machete": SLASHING,
    "katana": SLASHING,
    "greatsword": SLASHING,
    "military_sword": SLASHING,
    "assassin_energy_wakizashi": SLASHING,
    "warrior_energy_sabre": SLASHING,
    "warrior_energy_sword": SLASHING,
    "set_mercenary_weapon": SLASHING,
    "set_strangler_weapon": SLASHING,
    "set_camouflage_weapon": SLASHING,
    "set_killer_weapon": SLASHING,
    "set_mercenary_heavy": SLASHING,
    "set_strangler_heavy": SLASHING,
    "set_camouflage_heavy": SLASHING,
    "set_killer_heavy": SLASHING,
    "dirk": SLASHING,
    "shashka": SLASHING,
    "twohand_sabre": SLASHING,
    "broadsword": SLASHING,
    "falchion": SLASHING,
}

# Тяжёлое: занимает обе руки. Секиры по постановке двуручны всегда,
# кувалды и колун — по весу, посох — потому что его держат двумя
# руками, двуручные мечи — по названию. Остальное лёгкое, и лёгкое
# годится в любую руку.
#
# **Кувалды Босса здесь нет, хотя она кувалда.** Её носят двое — Босс
# Казино и Громила из затмения, — и оба со щитом: у Громилы на щите
# держатся обе фазы боя («Щит держит», «Щит сломан»), а броня Босса
# выверена вместе со щитом. Игроку она не достаётся ни покупкой, ни
# наградой, так что правило хвата её не касается; а двуручный множитель
# лёг бы поверх чисел, которые считались плоскими (10–40 — средний урон
# десятой ступени, разложенный в разброс), и Босс Казино прибавил бы
# треть мощи ни за что. Что двуручное и вторая рука у городских не
# встречаются, проверяет `tests/test_weapon_hands.py`
HEAVY: frozenset[str] = frozenset(
    {
        # рубящее
        "viking_axe",
        "splitting_axe",
        "tank_energy_greataxe",
        "set_sport_heavy",
        "set_biker_heavy",
        "set_fighter_heavy",
        "woodsman_axe",
        # дробящее
        "sledge",
        "bouncer_sledge",
        "set_army_weapon",
        "tank_energy_maul",
        "trickster_energy_staff",
        "set_avenger_heavy",
        "set_boxer_heavy",
        "set_bouncer_heavy",
        # режущее
        "greatsword",
        "set_hero_weapon",
        "set_mercenary_heavy",
        "set_strangler_heavy",
        "set_camouflage_heavy",
        "set_killer_heavy",
        "twohand_sabre",
    }
)


def family_of(code: str) -> str:
    """Вид этой вещи. Пусто — вида у неё нет."""
    return FAMILIES.get(code, "")


def hand_of(code: str) -> WeaponHand:
    """Каким хватом берут эту вещь: обеими руками или любой.

    Третьего ответа нет: тяжёлое — двуручное, всё остальное лёгкое, а
    лёгкое по правилам годится и во вторую руку. `MAIN` остаётся
    значением по умолчанию у вещей, которые в руки не берут вовсе.
    """
    if code in HEAVY:
        return WeaponHand.TWO_HANDED
    return WeaponHand.OFFHAND


def heavy(code: str) -> bool:
    """Тяжёлая ли это вещь. Только для проверок и экранов."""
    return code in HEAVY


__all__ = [
    "CHOPPING",
    "CRUSHING",
    "FAMILIES",
    "HEAVY",
    "PIERCING",
    "SLASHING",
    "family_of",
    "hand_of",
    "heavy",
]
