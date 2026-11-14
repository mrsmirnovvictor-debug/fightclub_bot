"""Эликсиры: то, что выпивают, а не надевают.

Эликсир не занимает слот и не снашивается — он расходуется. Их два рода:

* восстановление — доливает здоровье прямо сейчас, но не выше потолка;
* временный эффект — держит прибавку два часа и сам сходит на нет.

**Склянки лечат по прайсу больницы.** Перевязка и полное выздоровление
стоят там же и дают то же: сто единиц за двадцать пять кредитов и
полный запас за полтинник. Аптека продаёт не другое лечение, а то же
самое навынос — и брать за дорогу с собой дороже или дешевле значило бы
сказать, что здоровье в склянке другое.

Эффект хранится сроком, а не тикающим счётчиком: в базе лежит момент, до
которого он живёт. Так он переживает перезапуск бота и не требует фоновых
задач — ровно как отдых между боями.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.game.art import potion as potion_art
from bot.game.art import raid_ticket
from bot.game.classes import ALL_STATS, Stats
from bot.game.health import now_ts
from bot.game.hospital import FULL_PRICE, PATCH_HEAL, PATCH_PRICE

# Сколько держится временный эффект
EFFECT_SECONDS = 2 * 60 * 60

# Код обычного рейд-пасса. Премиальный — отдельная задача.
RAID_PASS = "raid_pass"
# Пропуск на стычку с фанатами. Пропуск под каждый рейд свой: талон
# казино на стадионе не спрашивают, и наоборот
STADIUM_PASS = "stadium_pass"
# Пропуск на военный полигон. Дороже билета на матч на полсотни: за
# базой и приз другой — кибероружие, которого нет ни в одной лавке
BASE_PASS = "base_pass"

# Ярлык раздела на витрине: эликсиры лежат отдельно от того, что надевают
SECTION_CODE = "misc"
SECTION_TITLE = "Прочее"
SECTION_EMOJI = "🧪"

# Пропуска стоят своей полкой у оружейника: их не пьют, а предъявляют
# на входе. В аптеке они и были-то по недоразумению — лежат в той же
# стопке, что склянки, вот и попали на тот же прилавок
PASS_CODE = "tickets"
PASS_TITLE = "Билеты"
PASS_EMOJI = "🎟"


class PotionKind(str, Enum):
    HEAL = "heal"  # доливает здоровье сразу
    BOOST = "boost"  # держит прибавку два часа
    # Рейд-пасс: его не пьют, его предъявляют на входе в подвал. Лежит в той
    # же стопке, что и склянки, — не занимает слот, не снашивается, копится
    # без счёта. Тратит его рейд, а не игрок.
    PASS = "pass"


@dataclass(frozen=True)
class Potion:
    """Эликсир: что делает, сколько стоит и с какого уровня продаётся."""

    code: str
    title: str
    emoji: str
    kind: PotionKind
    note: str = ""
    image: str = ""
    # Сколько здоровья долить сразу. Ноль у лечебной склянки — сколько
    # угодно, до потолка: та же мерка, что у полного выздоровления в
    # больнице (`bot.game.hospital`)
    heal: int = 0
    strength: int = 0
    agility: int = 0
    intuition: int = 0
    endurance: int = 0
    hp: int = 0  # прибавка к запасу здоровья на время действия
    seconds: int = 0
    level_required: int = 1
    price: int = 0
    # На сколько применений вещи хватает. У склянок нуль: их не снашивают,
    # а выпивают. У пропуска один — он срабатывает ровно раз, и это видно
    # в рюкзаке полосой износа, как у оружия
    max_wear: int = 0

    @property
    def picture(self) -> str:
        """Картинка склянки лежит под её кодом: potions/heal_small.png.

        Поле `image` остаётся на случай файла с другим именем.
        """
        return self.image or potion_art(self.code)

    @property
    def full(self) -> bool:
        """Доливает ли склянка до потолка, сколько бы ни не хватало."""
        return self.kind is PotionKind.HEAL and not self.heal

    @property
    def bonus(self) -> Stats:
        return Stats(
            strength=self.strength,
            agility=self.agility,
            intuition=self.intuition,
            endurance=self.endurance,
        )

    @property
    def is_boost(self) -> bool:
        return self.kind is PotionKind.BOOST

    @property
    def is_pass(self) -> bool:
        """Пропуск, а не склянка: его нельзя выпить."""
        return self.kind is PotionKind.PASS

    @property
    def shape(self) -> str:
        """Какой клеткой рисуется: квадрат или билет.

        Пропуска перерисовали билетами: 1200 на 600, прозрачный фон,
        название рейда и отрывная часть. В квадратной клетке от такого
        билета оставалась бы середина — ни названия, ни корешка, — и
        три пропуска выглядели бы одинаковой мутью.

        Форма живёт тут, а не на странице: там, где лежит и картинка.
        Иначе перерисованный товар пришлось бы заводить в двух местах,
        и одно из них однажды забыли бы.
        """
        return "ticket" if self.is_pass else "square"

    def describe_wear(self) -> str:
        """«0/1» — столько износа на пропуске, пока он лежит в рюкзаке.

        Второго значения не бывает: износ тратится в тот миг, когда
        пропуск предъявляют на входе, и предъявленный талон из рюкзака
        уходит. Показывать «1/1» было бы некому.
        """
        return f"0/{self.max_wear}" if self.max_wear else ""

    def describe(self) -> str:
        """Короткая строка для списка: «💪 +10 на 2 ч»."""
        if self.is_pass:
            return "Пропуск в подвал"
        parts = []
        if self.full:
            parts.append("❤️ до полного")
        elif self.heal:
            parts.append(f"❤️ +{self.heal} сразу")
        for stat in ALL_STATS:
            value = self.bonus.get(stat)
            if value:
                parts.append(f"{stat.emoji} +{value}")
        if self.hp:
            parts.append(f"❤️ +{self.hp}")
        line = ", ".join(parts)
        if self.seconds:
            line += f" на {spell_duration(self.seconds)}"
        return line


def spell_duration(seconds: int) -> str:
    """«2 ч», «1 ч 47 мин», «12 мин» — то, что читается вслух."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


POTIONS: tuple[Potion, ...] = (
    # ---------- восстановление: доливает здоровье прямо сейчас ----------
    Potion(
        "heal_small",
        "Эликсир восстановления",
        "🧪",
        PotionKind.HEAL,
        note=(
            f"Перевязка навынос: {PATCH_HEAL} единиц здоровья, не вставая "
            "в очередь."
        ),
        heal=PATCH_HEAL,
        price=PATCH_PRICE,
    ),
    Potion(
        "heal_big",
        "Большой эликсир восстановления",
        "⚗️",
        PotionKind.HEAL,
        note="Полное выздоровление в бутылке: сколько бы ни не хватало.",
        price=FULL_PRICE,
    ),
    # ---------- временные: держат прибавку два часа ----------
    Potion(
        "boost_strength",
        "Эликсир силы",
        "💪",
        PotionKind.BOOST,
        note="Два часа кулак весит вдвое больше.",
        image=potion_art("power"),
        strength=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_agility",
        "Эликсир проворства",
        "🤸",
        PotionKind.BOOST,
        note="Два часа ноги сами уводят из-под удара.",
        image=potion_art("agile"),
        agility=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_intuition",
        "Эликсир интуиции",
        "🔮",
        PotionKind.BOOST,
        note="Два часа знаешь, куда он ударит, раньше него самого.",
        image=potion_art("int"),
        intuition=10,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=200,
    ),
    Potion(
        "boost_hp",
        "Эликсир жизней",
        "❤️",
        PotionKind.BOOST,
        note="Два часа держишь на шестьдесят ударов больше.",
        image=potion_art("health"),
        hp=60,
        seconds=EFFECT_SECONDS,
        level_required=5,
        price=150,
    ),
    # ---------- пропуска: их не пьют ----------
    Potion(
        RAID_PASS,
        "Билет в казино",
        "🎟",
        PotionKind.PASS,
        note=(
            "Билет позволит вам участвовать в рейде «Ограбление Босса "
            "Казино»."
        ),
        image=raid_ticket("casino-boss"),
        price=10,
        max_wear=1,
    ),
    Potion(
        STADIUM_PASS,
        "Билет на матч",
        "🎫",
        PotionKind.PASS,
        note=(
            "Билет позволит вам участвовать в рейде «Стычка с футбольными "
            "фанатами»."
        ),
        # Пропуска перерисовали все разом и выгрузили своей папкой:
        # имя файла там по рейду, а не по коду вещи
        image=raid_ticket("football-fans"),
        price=30,
        max_wear=1,
    ),
    Potion(
        BASE_PASS,
        "Билет в военную часть",
        "🪪",
        PotionKind.PASS,
        note=(
            "Билет позволит вам участвовать в рейде «Захват военной "
            "части»."
        ),
        image=raid_ticket("military-base"),
        price=40,
        max_wear=1,
    ),
)

BY_CODE: dict[str, Potion] = {potion.code: potion for potion in POTIONS}


def get_potion(code: str) -> Potion | None:
    return BY_CODE.get(code)


def potions_unlocked_at(level: int) -> tuple[Potion, ...]:
    return tuple(potion for potion in POTIONS if potion.level_required == level)


@dataclass
class ActiveEffect:
    """Эффект, который сейчас на бойце: какой эликсир и до какого момента."""

    code: str
    until: int

    @property
    def potion(self) -> Potion | None:
        return get_potion(self.code)

    def seconds_left(self, now: int | None = None) -> int:
        moment = now_ts() if now is None else now
        return max(0, self.until - moment)

    def is_active(self, now: int | None = None) -> bool:
        return self.seconds_left(now) > 0


def effects_bonus(effects: list[ActiveEffect], now: int | None = None) -> Stats:
    """Сколько характеристик добавляют действующие эффекты."""
    total = Stats()
    for effect in effects:
        potion = effect.potion
        if potion is not None and effect.is_active(now):
            total = total.merge(potion.bonus)
    return total


def effects_hp(effects: list[ActiveEffect], now: int | None = None) -> int:
    """Прибавка к запасу здоровья от действующих эффектов."""
    return sum(
        effect.potion.hp
        for effect in effects
        if effect.potion is not None and effect.is_active(now)
    )


__all__ = [
    "BASE_PASS",
    "BY_CODE",
    "EFFECT_SECONDS",
    "POTIONS",
    "RAID_PASS",
    "STADIUM_PASS",
    "PASS_CODE",
    "PASS_EMOJI",
    "PASS_TITLE",
    "SECTION_CODE",
    "SECTION_EMOJI",
    "SECTION_TITLE",
    "ActiveEffect",
    "Potion",
    "PotionKind",
    "effects_bonus",
    "effects_hp",
    "get_potion",
    "potions_unlocked_at",
    "spell_duration",
]
