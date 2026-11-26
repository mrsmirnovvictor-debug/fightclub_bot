"""Раздача очков владения: что проверяется, прежде чем очко ляжет.

Правила — в `bot.game.mastery`, хранение — в карточке бойца. Здесь
только одно действие: вложить очки в направление, — и проверки вокруг
него.

**Очки не создаются.** Запас обоих пулов считается от уровня, а не
копится в базе: свободный остаток — это уровень минус вложенное.
Поэтому повторный запрос ничего не создаёт и перескок уровней ничего не
теряет; проверять «не начислили ли дважды» попросту нечего.

**Пул выбирает сервер, а не страница.** Код направления однозначно
говорит, из какого он пула: «мечи» — вид, «щиты» — хват. Если бы пул
приезжал с нажатием, его можно было бы подменить и перелить очко вида в
хват — ровно то, что правила запрещают.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from bot.database import Database
from bot.game.mastery import (
    FAMILY_CODES,
    HOLD_CODES,
    HoldType,
    WeaponFamily,
    free_points,
)
from bot.models import Player

logger = logging.getLogger(__name__)


class MasteryError(Exception):
    """Вложить не вышло: нет такого направления или не хватает очков."""


def pool_of(code: str) -> str:
    """Из какого пула это направление: «вид» или «хват».

    Пусто — направления с таким кодом нет вовсе.
    """
    if code in FAMILY_CODES:
        return "family"
    if code in HOLD_CODES:
        return "hold"
    return ""


def title_of(code: str) -> str:
    """Как направление называется в отказе и в журнале."""
    if code in FAMILY_CODES:
        return WeaponFamily(code).title
    if code in HOLD_CODES:
        return HoldType(code).title
    return code


def parse_draft(raw: dict) -> dict[str, int]:
    """Разобрать заявку «куда сколько». Мусор и минусы не проходят.

    Заявка приходит с экрана одной пачкой: боец раскладывает очки, видит
    итог и подтверждает. Поэтому разбор строгий — непонятное число или
    чужой код останавливают всю заявку, а не тихо выпадают из неё.
    """
    draft: dict[str, int] = {}
    for code, value in (raw or {}).items():
        if not pool_of(str(code)):
            raise MasteryError(f"Такого направления нет: {code}.")
        try:
            amount = int(value)
        except (TypeError, ValueError) as error:
            raise MasteryError(
                f"Непонятно, сколько очков в «{title_of(str(code))}»."
            ) from error
        if amount < 0:
            raise MasteryError("Очки можно только вкладывать, не отнимать.")
        if amount:
            draft[str(code)] = amount
    if not draft:
        raise MasteryError("Не выбрано ни одного очка.")
    return draft


async def spend(db: Database, player: Player, raw: dict) -> dict[str, int]:
    """Разложить очки владения пачкой. Вернуть то, что легло.

    Пулы считаются порознь: перебор в видах не трогает хваты, и
    наоборот. Заявка проходит целиком или не проходит вовсе — половина
    разложенного хуже отказа: боец не поймёт, что именно легло.
    """
    draft = parse_draft(raw)
    for pool, points, kind in (
        ("family", player.mastery, "вида оружия"),
        ("hold", player.holds, "типа владения"),
    ):
        asked = sum(
            amount for code, amount in draft.items() if pool_of(code) == pool
        )
        left = free_points(player.level, points)
        if asked > left:
            raise MasteryError(
                f"Столько очков {kind} нет: свободных {left}, "
                f"а разложено {asked}."
            )

    for code, amount in draft.items():
        points = player.mastery if pool_of(code) == "family" else player.holds
        points[code] = max(0, points.get(code, 0)) + amount
    await db.save_player(player)
    logger.info(
        "Боец %s разложил очки владения: %s",
        player.user_id,
        {title_of(code): amount for code, amount in draft.items()},
    )
    return draft


def needed_for(hands: Iterable[tuple[str, str]]) -> tuple[dict[str, int], dict[str, int]]:
    """Какое владение требует надетое в руках: по видам и по хватам.

    На входе то, что отдаёт база: код вещи и клетка, в которой она
    лежит. Клетка важна — лёгкое оружие в основной руке требует только
    вида, а во второй ещё и хвата «Второе оружие»; щит требует своего
    хвата, где бы он ни лежал.

    Два одинаковых требования не складываются: нужен максимум, а не
    сумма. Владение седьмого уровня держит и меч седьмого, и нож
    четвёртого — очки вкладывают в направление, а не в вещь.
    """
    from bot.game.equipment import Slot, get_item
    from bot.game.gear import hold_required

    families: dict[str, int] = {}
    holds: dict[str, int] = {}
    for code, cell in hands:
        item = get_item(code)
        if item is None:  # pragma: no cover - вещь из будущей версии
            continue
        need = item.mastery_required
        if not need:
            continue
        try:
            slot = Slot(cell)
        except ValueError:  # pragma: no cover - клетка из будущей версии
            continue
        if item.family:
            families[item.family] = max(families.get(item.family, 0), need)
        hold = hold_required(item, slot)
        if hold:
            holds[hold] = max(holds.get(hold, 0), need)
    return families, holds


def plan_for(
    level: int, points: dict[str, int], needs: dict[str, int]
) -> dict[str, int]:
    """Что доложить в пул, чтобы носить надетое, не выходя за остаток.

    **Кладём только целые требования.** Половина порога не держит
    ничего, а очки, положенные в неё, уже не переложить: лучше оставить
    их свободными, чем закопать. Поэтому направление, которое целиком не
    влезает, пропускаем — и идём дальше: меньшее за ним может влезть.

    **Начинаем с самого дорогого.** Крупное требование — это обычно
    главное оружие бойца, то, которым он дерётся; им и закрываемся в
    первую очередь, пока очки есть.

    Повторный вызов ничего не возвращает: вложенное уже покрывает
    порог, и докладывать нечего. На этом стоит идемпотентность всей
    миграции — она не помнит, что делала, а считает заново.
    """
    left = free_points(level, points)
    plan: dict[str, int] = {}
    for code, need in sorted(needs.items(), key=lambda one: -one[1]):
        short = need - max(0, points.get(code, 0))
        if short <= 0 or short > left:
            continue
        plan[code] = short
        left -= short
    return plan


async def settle_worn(
    db: Database, player: Player, hands: Iterable[tuple[str, str]]
) -> tuple[dict[str, int], list[str]]:
    """Вложить бойцу минимум под то, что у него уже надето.

    Возвращает вложенное и названия направлений, на которые очков не
    хватило. Надетое при этом не трогаем вовсе: правила рук и владения
    спрашивают, когда вещь надевают, — снимать задним числом то, в чём
    боец вышел на ринг, было бы наказанием за смену правил.

    Не хватить может по-настоящему: очков в пуле столько, сколько
    уровней, а надетого в руках бывает два, и пороги у них свои. Меч
    девятого уровня с ножом четвёртого — это тринадцать очков вида на
    девять имеющихся. Такой боец доигрывает в своей сборке и
    перераспределяет очки к следующей смене оружия; случай попадает в
    журнал, потому что сам по себе он не чинится.
    """
    families, holds = needed_for(hands)
    done: dict[str, int] = {}
    for points, needs in ((player.mastery, families), (player.holds, holds)):
        for code, amount in plan_for(player.level, points, needs).items():
            points[code] = max(0, points.get(code, 0)) + amount
            done[code] = amount
    if done:
        await db.save_player(player)

    short = [
        title_of(code)
        for points, needs in ((player.mastery, families), (player.holds, holds))
        for code, need in sorted(needs.items())
        if max(0, points.get(code, 0)) < need
    ]
    return done, short


async def invest(
    db: Database, player: Player, code: str, amount: int = 1
) -> int:
    """Вложить очки в направление. Возвращает, сколько в нём стало.

    Проверяет всё, что можно проверить только на сервере: есть ли такое
    направление, положительное ли вложение и хватает ли свободного
    остатка того пула, которому направление принадлежит. Страница
    считает то же самое, чтобы не рисовать мёртвых кнопок, но решает
    сервер: нажатие приходит из браузера, и верить ему нельзя.
    """
    pool = pool_of(code)
    if not pool:
        raise MasteryError("Такого направления нет.")
    if amount <= 0:
        raise MasteryError("Вложить можно только целое число очков.")

    points = player.mastery if pool == "family" else player.holds
    left = free_points(player.level, points)
    if amount > left:
        kind = "вида оружия" if pool == "family" else "типа владения"
        raise MasteryError(
            f"Свободных очков {kind} — {left}. Больше не вложить."
        )

    points[code] = max(0, points.get(code, 0)) + amount
    await db.save_player(player)
    logger.info(
        "Боец %s вложил %s очк. во владение «%s»",
        player.user_id, amount, title_of(code),
    )
    return points[code]


__all__ = [
    "MasteryError",
    "invest",
    "needed_for",
    "parse_draft",
    "plan_for",
    "pool_of",
    "settle_worn",
    "spend",
    "title_of",
]
