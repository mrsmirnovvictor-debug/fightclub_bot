"""Кланы: группировки мафии, их чины и правила приёма.

Клан заводят в Особняке мафии за тысячу кредитов, и завести его может
кто угодно. Кто завёл — тот Дон; он же выбирает себе до трёх
приближённых, и вчетвером они решают, кого в семью принимать, а кого
выставить. Всех остальных зовут мафиози, и больше двадцати пяти человек
в семье не бывает.

**Чинов три, и прав у них два набора.** Дон и приближённые могут всё:
принимать заявки, звать людей, выгонять, назначать и снимать
приближённых. Дон сверх того один — он передаёт дела, и после передачи
остаётся в семье тем, кем сам решит. Мафиози не может ничего, кроме как
уйти.

**В семью приходят двумя дорогами.** Либо боец сам подаёт заявку в
особняке, и её одобряет кто-то из старших, либо старший зовёт бойца, и
тот соглашается. И то и другое — одна запись в таблице заявок: разница
в том, кто сделал первый шаг и чьё слово нужно вторым.

**Знак у семьи один, и он её собственный.** Дон выбирает его при
создании из тех, что нарисованы, и выбранный уходит из списка: двух
семей под одним знаком в городе не бывает. Знаки лежат в
`bot.content.clans`, и пока их семнадцать — столько кланов в городе и
может быть. Общего значка мафии нет: по знаку клана и так видно, что
боец не сам по себе.

**Имя пишет игрок.** При создании — какое захочет, потом его можно
сменить за 200 кредитов, но не чаще раза в полгода: имя семьи — не
вывеска на один вечер.

**Имущество у семьи общее.** В сейфе лежат кредиты, в хранилище —
двенадцать вещей. Кладут туда все, кто в клане; берут только Дон и
приближённые. Так общий карман остаётся общим: мафиози несёт в семью, а
распоряжается семьёй старший.

Здесь только правила: что можно, кому и в каких границах. Сами действия
— в `bot.clan_service`, хранение — в `bot.database`, экраны — в
`bot.webapp.clans`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# Где заводят и где ищут: дом один, и это Особняк мафии
MANSION = "mafia_mansion"

# Сколько стоит завести клан и сколько человек он держит
CLAN_PRICE = 1000
MAX_MEMBERS = 25
# Приближённых у Дона трое — не больше. Вместе с ним это четверо
# старших на двадцать пять человек
MAX_CAPOS = 3

# Имя клана: от трёх до двадцати четырёх знаков, буквы, цифры, пробел и
# тире. Длинное не влезает в строку, пустое не отличить от соседнего
NAME_MIN = 3
NAME_MAX = 24
NAME_ALLOWED = re.compile(r"^[0-9A-Za-zА-Яа-яЁё №\"'«».,!?\-–—()]+$")

# Смена имени: двести кредитов и не чаще раза в полгода. Полгода —
# ровно сто восемьдесят два дня: календарные месяцы тут считать незачем
RENAME_PRICE = 200
RENAME_DAYS = 182
RENAME_COOLDOWN = RENAME_DAYS * 24 * 60 * 60

# Имущество семьи: сейф с кредитами и хранилище на двенадцать вещей.
# Кладут все, берут только старшие — в этом и смысл общего кармана
VAULT_SLOTS = 12


class Rank(str, Enum):
    """Чин в клане. Порядок — старшинство: по нему и сортируют списки."""

    DON = "don"
    CAPO = "capo"
    SOLDIER = "soldier"

    @property
    def title(self) -> str:
        return RANK_TITLES[self]

    @property
    def plural(self) -> str:
        return RANK_PLURALS[self]

    @property
    def order(self) -> int:
        return RANK_ORDER[self]

    @property
    def can_admin(self) -> bool:
        """Может ли этот чин решать за клан: звать, принимать, выгонять."""
        return self in (Rank.DON, Rank.CAPO)


RANK_TITLES: dict[Rank, str] = {
    Rank.DON: "Дон",
    Rank.CAPO: "Приближённый",
    Rank.SOLDIER: "Мафиози",
}
RANK_PLURALS: dict[Rank, str] = {
    Rank.DON: "Дон",
    Rank.CAPO: "Приближённые",
    Rank.SOLDIER: "Мафиози",
}
RANK_ORDER: dict[Rank, int] = {Rank.DON: 0, Rank.CAPO: 1, Rank.SOLDIER: 2}


class Petition(str, Enum):
    """Кто сделал первый шаг: боец постучался или семья позвала."""

    REQUEST = "request"  # заявка бойца: ждёт слова старших
    INVITE = "invite"  # приглашение от старших: ждёт слова бойца


@dataclass(frozen=True)
class Clan:
    """Клан как запись: имя, знак, основатель и сколько в нём людей."""

    id: int
    name: str
    don: int
    created_at: int = 0
    # Код знака из `bot.content.clans`. Пусто — знака нет вовсе (такого
    # клана быть не должно: знак берут при создании)
    sign: str = ""
    members: int = 0
    # Когда имя меняли в последний раз. 0 — не меняли ни разу, и первая
    # смена доступна сразу
    renamed_at: int = 0
    # Сколько кредитов в сейфе семьи
    vault: int = 0

    @property
    def full(self) -> bool:
        return self.members >= MAX_MEMBERS

    @property
    def free_seats(self) -> int:
        return max(0, MAX_MEMBERS - self.members)

    @property
    def picture(self) -> str:
        """Картинка знака. Пусто — знака у клана нет."""
        from bot.content.clans import get_sign

        sign = get_sign(self.sign)
        return sign.picture if sign else ""

    def rename_at(self) -> int:
        """С какого часа имя можно менять снова."""
        return self.renamed_at + RENAME_COOLDOWN if self.renamed_at else 0

    def can_rename(self, now: int) -> bool:
        return now >= self.rename_at()

    def rename_left(self, now: int) -> int:
        """Сколько секунд ждать до следующей смены имени."""
        return max(0, self.rename_at() - now)


@dataclass(frozen=True)
class Standing:
    """Чем боец состоит в клане: сам клан и его чин в нём."""

    clan: Clan
    rank: Rank
    joined_at: int = 0

    @property
    def can_admin(self) -> bool:
        return self.rank.can_admin

    @property
    def is_don(self) -> bool:
        return self.rank is Rank.DON


@dataclass(frozen=True)
class Member:
    """Строка списка клана: боец, его чин и то, что видно в строке."""

    user_id: int
    rank: Rank
    nickname: str
    level: int
    class_code: str
    # До какого часа у бойца подписка: значок премиума в строке ставят
    # по нему. Самого значка здесь нет — это уже дело экрана
    pro_until: int = 0
    joined_at: int = 0


@dataclass(frozen=True)
class Stored:
    """Вещь в хранилище клана: чья она была и когда её положили."""

    id: int
    code: str
    wear: int = 0
    max_wear: int = 0
    mod: str = ""
    mod_value: int = 0
    by_id: int = 0
    by_name: str = ""
    put_at: int = 0


@dataclass(frozen=True)
class Knock:
    """Заявка или приглашение: кто, в какой клан и кто позвал."""

    clan_id: int
    user_id: int
    kind: Petition
    created_at: int = 0
    # Кто позвал. У заявки ноль: боец пришёл сам
    by_id: int = 0
    # То, что видно в строке. У заявки — про бойца, у приглашения —
    # про клан: списки разные, а запись одна
    nickname: str = ""
    level: int = 0
    class_code: str = ""
    pro_until: int = 0
    clan_name: str = ""
    clan_sign: str = ""
    by_name: str = ""


@dataclass
class Roster:
    """Клан со всеми, кто в нём: список для экрана клана."""

    clan: Clan
    members: list[Member] = field(default_factory=list)

    def by_rank(self, rank: Rank) -> list[Member]:
        """Все этого чина, от старших к новичкам."""
        return [one for one in self.members if one.rank is rank]

    @property
    def don(self) -> Member | None:
        rank = self.by_rank(Rank.DON)
        return rank[0] if rank else None

    @property
    def capos(self) -> list[Member]:
        return self.by_rank(Rank.CAPO)

    @property
    def free_capo_seats(self) -> int:
        return max(0, MAX_CAPOS - len(self.capos))


# ---------- имя ----------


def clean_name(raw: str) -> str:
    """Имя клана без лишних пробелов по краям и внутри."""
    return re.sub(r"\s+", " ", (raw or "").strip())


def name_problem(raw: str) -> str:
    """Что не так с именем клана. Пустая строка — всё так."""
    name = clean_name(raw)
    if len(name) < NAME_MIN:
        return f"Имя клана — от {NAME_MIN} знаков."
    if len(name) > NAME_MAX:
        return f"Имя клана — не длиннее {NAME_MAX} знаков."
    if not NAME_ALLOWED.match(name):
        return "В имени клана только буквы, цифры и знаки препинания."
    return ""


# ---------- знак ----------


def sign_problem(code: str, taken: set[str] | frozenset[str] = frozenset()) -> str:
    """Что не так с выбранным знаком. Пустая строка — всё так.

    `taken` — знаки, которые уже разобрали другие семьи: занятый знак
    второй раз не достаётся никому.
    """
    from bot.content.clans import get_sign

    if not code:
        return "Выбери знак клана."
    if get_sign(code) is None:
        return "Такого знака нет."
    if code in taken:
        return "Этот знак уже у другой семьи — выбери свободный."
    return ""


__all__ = [
    "CLAN_PRICE",
    "MANSION",
    "MAX_CAPOS",
    "MAX_MEMBERS",
    "NAME_MAX",
    "NAME_MIN",
    "RANK_PLURALS",
    "RANK_TITLES",
    "RENAME_COOLDOWN",
    "RENAME_DAYS",
    "RENAME_PRICE",
    "VAULT_SLOTS",
    "Clan",
    "Knock",
    "Member",
    "Petition",
    "Rank",
    "Roster",
    "Standing",
    "Stored",
    "clean_name",
    "name_problem",
    "sign_problem",
]
