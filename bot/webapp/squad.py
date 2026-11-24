"""Отряд глазами мини-аппа: вкладка, карточка бойца и витрина найма.

Правил тут нет — их держат `bot.game.squad` и `bot.squad_service`.
Здесь перевод в json.

**Карточка бойца — это карточка игрока.** Боец лежит в `players`, и
`build_card` рисует его тем же кодом, что и человека: те же
характеристики, тот же инвентарь, те же слоты. Своя карточка для NPC
означала бы вторую вёрстку, которая отстаёт от первой на одну правку.

Сверху к ней дописано то, чего у игрока не бывает: звёздность, лицо из
пула, хозяин и где боец сейчас — на точке, в больнице или свободен.

**Витрина найма ничего не решает.** Она показывает то, что уже лежит в
базе; бросок, цены и пересмотры — в службе. Поэтому открыть вкладку
можно сколько угодно раз, и это ничего не стоит и ничего не меняет.
"""

from __future__ import annotations

from typing import Any

from bot.database import Database
from bot.game.art import recruit_face
from bot.game.classes import FIGHTER_CLASSES
from bot.game.health import now_ts
from bot.game.squad import SQUAD_LIMIT, hire_price
from bot.models import Player
from bot.squad_service import fighters, showcase
from bot.webapp.card import build_card


def where_is(row: dict) -> tuple[str, str]:
    """Где боец и что об этом написать: код состояния и слова.

    Три состояния, и у каждого своя кнопка на экране: лежачего
    забирают, стоящего на точке снимают, свободного ставят.
    """
    if row["hospital_at"]:
        return "hospital", "В больнице"
    if row["spot"]:
        return "spot", "Держит точку"
    return "free", "Свободен"


def fighter_row(row: dict, npc: Player, now: int | None = None) -> dict[str, Any]:
    """Боец строкой во вкладке «Отряды»: лицо, класс, уровень, звёзды."""
    moment = now_ts() if now is None else now
    fclass = FIGHTER_CLASSES[npc.class_code]
    state, said = where_is(row)
    hp = npc.current_hp(moment)
    return {
        "id": npc.user_id,
        "name": npc.nickname,
        "level": npc.level,
        "stars": row["stars"],
        "face": recruit_face(row["face"]),
        "fclass": npc.class_code,
        "class_title": fclass.title,
        "class_emoji": fclass.emoji,
        "hp": hp,
        "max_hp": npc.max_hp,
        "state": state,
        "state_text": said,
        "spot": row["spot"],
    }


async def build_squad(
    db: Database, owner: Player, now: int | None = None
) -> dict[str, Any]:
    """Вкладка «Отряды»: кто есть и сколько мест осталось."""
    moment = now_ts() if now is None else now
    held = await fighters(db, owner.user_id)
    return {
        "limit": SQUAD_LIMIT,
        "rows": [fighter_row(row, npc, moment) for row, npc in held],
        # Пустой отряд — не ошибка, а начало: про это и пишут словами
        "note": (
            ""
            if held
            else "В вашем отряде пока нет бойцов. Нанять их можно "
            "в Школе боевых искусств."
        ),
    }


async def build_fighter(
    db: Database,
    owner: Player,
    npc_id: int,
    bot_token: str,
    now: int | None = None,
) -> dict[str, Any] | None:
    """Карточка бойца: всё, что у игрока, плюс звёзды и место службы.

    Смотреть её может только хозяин: чужой отряд — это чужая сила, и
    разглядывать её до боя незачем. Пусто — боец не его или его нет.
    """
    moment = now_ts() if now is None else now
    row = await db.squad_row(npc_id)
    if row is None or row["owner_id"] != owner.user_id:
        return None
    npc = await db.get_player(npc_id)
    if npc is None:
        return None
    state, said = where_is(row)
    lot = await db.transfer_of(npc_id)
    # Смотрит хозяин, и карточка должна быть «своей»: иначе страница
    # спрячет инвентарь, который он как раз и пришёл менять
    card = build_card(npc, bot_token, viewer_id=npc_id, now=moment)
    card.update(
        {
            "squad": True,
            "stars": row["stars"],
            "face": recruit_face(row["face"]),
            "owner_id": row["owner_id"],
            "state": state,
            "state_text": said,
            "spot": row["spot"],
            # Выставлен ли на трансферном рынке и почём
            "listed": bool(lot),
            "price": int(lot["price"]) if lot else 0,
        }
    )
    return card


async def build_hire(
    db: Database, player: Player, now: int | None = None
) -> dict[str, Any]:
    """Вкладка «Найм бойцов» в школе: трое на неделю и цена пересмотра."""
    moment = now_ts() if now is None else now
    shop = await showcase(db, player, moment)
    cards = []
    for one in shop["cards"]:
        fclass = FIGHTER_CLASSES[one["class_code"]]
        cards.append(
            {
                "slot": one["slot"],
                "name": one["name"],
                "stars": one["stars"],
                "price": hire_price(one["stars"]),
                "face": recruit_face(one["face"]),
                "fclass": one["class_code"],
                "class_title": fclass.title,
                "class_emoji": fclass.emoji,
                "taken": bool(one["taken"]),
            }
        )
    return {
        "cards": cards,
        "squad": shop["squad"],
        "limit": shop["limit"],
        "rolls_left": shop["rolls_left"],
        "roll_price": shop["roll_price"],
        "credits": player.credits,
        # Все кандидаты выходят первым уровнем: про это написано на
        # экране, иначе звёздность читалась бы как сила сегодня
        "note": (
            "Все кандидаты приходят первого уровня. Звёзды — это не сила "
            "сейчас, а то, насколько быстро боец растёт."
        ),
    }


__all__ = ["build_fighter", "build_hire", "build_squad", "fighter_row", "where_is"]
