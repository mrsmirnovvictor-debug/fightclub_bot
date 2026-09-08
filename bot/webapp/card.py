"""Сборка данных карточки персонажа для мини-аппа."""

from __future__ import annotations

import time
from datetime import datetime

from bot.game.classes import ALL_STATS, ALL_ZONES, FighterClass, Stats, get_class
from bot.game.combat import (
    MAX_ACCURACY,
    MAX_ANTICRIT,
    MAX_BLOCK_HOLD,
    MAX_COUNTER_CHANCE,
    MAX_CRIT_CHANCE,
    MAX_DODGE_CHANCE,
    total_accuracy,
    total_anticrit,
    total_block_hold,
    total_counter,
    total_crit,
    total_dodge,
)
from bot.game.economy import MAX_LEVEL, MICRO_UPS_PER_LEVEL
from bot.game.equipment import (
    ALL_SLOTS,
    LEFT_SLOTS,
    UNDER_SLOTS,
    MAGIC_ITEMS,
    RIGHT_SLOTS,
    Equipment,
    Item,
    OwnedItem,
    Slot,
    get_item,
    shop_sections,
)
from bot.game.market import FEE as MARKET_FEE, buyback
from bot.game.health import FULL_REGEN_SECONDS, HealthState, format_duration
from bot.game.looks import DEFAULT_LOOK, get_look
from bot.game import pro
from bot.game.pro import PRO_BADGE, current_offer
from bot.game.potions import (
    POTIONS,
    SECTION_CODE,
    SECTION_EMOJI,
    SECTION_TITLE,
    ActiveEffect,
    Potion,
    spell_duration,
)
from bot.game.stats import derive
from bot.game.store import PACKS
from bot.models import Player
from bot.webapp.auth import sign_avatar

# Ссылка на аватар живёт час — столько же, сколько открытая карточка
AVATAR_TTL = 60 * 60

STATE_COLORS = {
    HealthState.HURT: "red",
    HealthState.RECOVERING: "yellow",
    HealthState.READY: "green",
}


def format_birthday(created_at: str | None) -> str:
    """«2013-10-26 22:31:00» → «26.10.13 22:31»."""
    if not created_at:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(created_at[:19], fmt).strftime("%d.%m.%y %H:%M")
        except ValueError:
            continue
    return created_at  # pragma: no cover - формат из будущей версии


# Как клетка называется на кукле. Верхняя одежда и футболка делят одну
# клетку — она и называется по месту, а не по вещи.
CELL_TITLES: dict[Slot, str] = {Slot.JACKET: "тело"}


def worn_payload(owned: OwnedItem, fclass: FighterClass | None = None) -> dict:
    """Надетая вещь так, как её показывает клетка куклы."""
    in_hands = weapon_in_hands(owned.item, fclass)
    return {
        "id": owned.id,
        "slot": owned.item.slot.value,
        "code": owned.code,
        "title": owned.title,
        "icon": owned.emoji,
        "image": owned.image,
        "bonus": owned.describe_bonus(),
        # Класс меняет урон оружия — говорим об этом там же, где число
        "in_hands": f"У {fclass.title.lower()}а в руках: {in_hands}"
        if in_hands
        else "",
        "wear": owned.wear,
        "max_wear": owned.max_wear,
    }


def slot_payload(
    equipment: Equipment, slot: Slot, fclass: FighterClass | None = None
) -> dict:
    owned = equipment.get(slot)
    # Что надето под этой вещью: футболка под верхней одеждой
    under_slot = UNDER_SLOTS.get(slot)
    under = equipment.get(under_slot) if under_slot else None
    in_hands = weapon_in_hands(owned.item, fclass) if owned else ""
    return {
        "slot": slot.value,
        "title": slot.title,
        # Название клетки: у тела оно своё, потому что вещей в ней две
        "cell_title": CELL_TITLES.get(slot, slot.title),
        "under_title": under_slot.title if under_slot else "",
        "under": worn_payload(under, fclass) if under else None,
        "placeholder": slot.emoji,
        "placeholder_image": slot.placeholder,
        "item": None
        if owned is None
        else {
            "id": owned.id,
            "code": owned.code,
            "title": owned.title,
            "icon": owned.emoji,
            "image": owned.image,
            "bonus": owned.describe_bonus(),
            # Класс меняет урон оружия — говорим об этом там же, где число
            "in_hands": f"У {fclass.title.lower()}а в руках: {in_hands}"
            if in_hands
            else "",
            "wear": owned.wear,
            "max_wear": owned.max_wear,
        },
    }


def requirements_payload(player: Player, item: Item) -> list[dict]:
    """Что нужно, чтобы надеть вещь, и что из этого у бойца уже есть.

    «Есть» считается по надетому: меч с прибавкой к интуиции открывает нож,
    до которого боец сам не дотягивается, — значит и в требованиях должно
    стоять то число, по которому решает сервер.
    """
    missing = set(player.missing_for(item))
    rows = [
        {
            "code": "level",
            "title": "Уровень",
            "need": item.level_required,
            "have": player.level,
            "ok": "level" not in missing,
        }
    ]
    for stat in ALL_STATS:
        need = item.requires.get(stat)
        if need:
            rows.append(
                {
                    "code": stat.value,
                    "title": stat.title.capitalize(),
                    "emoji": stat.emoji,
                    "need": need,
                    "have": player.worn_stats.get(stat),
                    "ok": stat.value not in missing,
                }
            )
    return rows


def item_payload(player: Player, owned: OwnedItem) -> dict:
    """Строка инвентаря: картинка, тип, износ, требования, свойства, кнопки."""
    item = owned.item
    return {
        "id": owned.id,
        "code": owned.code,
        "title": owned.title,
        "icon": owned.emoji,
        "image": owned.image,
        "kind": item.kind.value,
        "slot": item.slot.value,
        "slot_title": item.slot.section.capitalize(),
        "slots": [
            {"slot": slot.value, "title": slot.title} for slot in item.slots
        ],
        "wear": owned.wear,
        "max_wear": owned.max_wear,
        "wear_text": owned.describe_wear(),
        "repair_price": owned.repair_price,
        # Сколько даст лавка, если сдать вещь. Ноль — такое она не берёт
        "buyback": buyback(item),
        "requirements": requirements_payload(player, item),
        "can_equip": player.can_equip(item),
        "bonus": item.describe_bonus(),
        "bonuses": bonuses_payload(item, player.fclass),
    }


def weapon_in_hands(item: Item, fclass: FighterClass | None) -> str:
    """Урон оружия в руках этого класса. Пусто — класс ничего не меняет.

    Одну и ту же биту воин проворачивает хуже ассасина, и боевой движок это
    считает. Значит и вещь должна говорить, во что она превратится: иначе на
    карточке рядом стоят «7–15» у меча и «6–14» в ударе, и это читается как
    ошибка, хотя оба числа верные.
    """
    if fclass is None or not item.damage_max:
        return ""
    low = round(item.damage_min * fclass.damage_mult)
    high = round(item.damage_max * fclass.damage_mult)
    if (low, high) == (item.damage_min, item.damage_max):
        return ""
    return f"{low}–{high}"


def bonuses_payload(item: Item, fclass: FighterClass | None = None) -> list[dict]:
    """Что вещь даёт, когда надета.

    Строка с диапазоном («Урон: 13–21») приходит текстом, прибавка к
    характеристике — числом: на экране они рисуются по-разному.
    """
    rows: list[dict] = []
    if item.damage_max:
        row = {"emoji": "👊", "title": "Урон", "text": item.describe_damage()}
        in_hands = weapon_in_hands(item, fclass)
        if in_hands:
            row["hint"] = f"у {fclass.title.lower()}а {in_hands}"
        rows.append(row)
    if item.armor_max:
        rows.append({"emoji": "🛡", "title": "Броня", "text": item.describe_armor()})
    rows += [
        {"emoji": stat.emoji, "title": stat.title.capitalize(), "value": value}
        for stat, value in ((stat, item.bonus.get(stat)) for stat in ALL_STATS)
        if value
    ]
    if item.hp:
        rows.append({"emoji": "❤️", "title": "Здоровье", "value": item.hp})
    rows += [
        {"emoji": emoji, "title": title, "text": f"{share:.0%}"}
        for emoji, title, share in (
            ("🎯", "Точность", item.accuracy),
            ("🌀", "Уворот", item.dodge),
            ("💥", "Крит", item.crit),
            ("🚫", "Антикрит", item.anticrit),
            ("🔄", "Контрудар", item.counter),
        )
        if share
    ]
    return rows


def suits_payload(item: Item) -> list[dict]:
    """Кому вещь в первую очередь — подсказка для витрины."""
    return [
        {"code": code, "title": get_class(code).title, "emoji": get_class(code).emoji}
        for code in item.for_classes
    ]


def capped_share(base: float, gear: float, total, cap: float) -> dict:
    """Доля вместе с надетым — и что от неё отрезал потолок.

    Без этого карточка молча съедает лишнее: вещи дают +100% уворота, в
    строке стоит 60%, и выглядит это как ошибка счёта, хотя выше потолка
    уворот просто не растёт — ни в карточке, ни на ринге.
    """
    raw = base + gear
    return {
        "value": round(total(base, gear) * 100),
        "own": round(base * 100),
        "gear": round(gear * 100),
        "raw": round(raw * 100),
        "cap": round(cap * 100),
        "capped": raw > cap + 1e-9,
    }


def goods_payload(player: Player, item: Item, owned: int) -> dict:
    """Строка витрины: цена, требования, свойства и кому подходит."""
    return {
        "code": item.code,
        "title": item.title,
        "icon": item.emoji,
        "image": item.image,
        "kind": item.kind.value,
        "slot": item.slot.value,
        "slot_title": item.slot.section.capitalize(),
        "price": item.price,
        "level_required": item.level_required,
        "unlocked": player.level >= item.level_required,
        "affordable": player.can_afford(item.price),
        "can_equip": player.can_equip(item),
        "owned": owned,
        "requirements": requirements_payload(player, item),
        "bonuses": bonuses_payload(item, player.fclass),
        "suits": suits_payload(item),
    }


def potion_gains_payload(potion: Potion) -> list[dict]:
    """Что эликсир делает — теми же строчками, что и свойства вещи."""
    rows: list[dict] = []
    if potion.heal:
        rows.append(
            {"emoji": "❤️", "title": "Восстановит", "text": f"до +{potion.heal}"}
        )
    rows += [
        {"emoji": stat.emoji, "title": stat.title.capitalize(), "value": value}
        for stat, value in ((stat, potion.bonus.get(stat)) for stat in ALL_STATS)
        if value
    ]
    if potion.hp:
        rows.append({"emoji": "❤️", "title": "Запас здоровья", "value": potion.hp})
    if potion.seconds:
        rows.append(
            {"emoji": "⏳", "title": "Держится", "text": spell_duration(potion.seconds)}
        )
    return rows


def potion_payload(player: Player, potion: Potion, owned: int) -> dict:
    """Склянка на витрине и в рюкзаке. Ключи те же, что у вещи: рисует их
    одна и та же карточка, а `consumable` разводит кнопки."""
    return {
        "code": potion.code,
        "title": potion.title,
        "icon": potion.emoji,
        "image": potion.picture,
        "kind": potion.kind.value,
        "consumable": True,
        # Временный вытесняет другой временный, восстановление — никого
        "boost": potion.is_boost,
        "slot": SECTION_CODE,
        "slot_title": SECTION_TITLE,
        "note": potion.note,
        "price": potion.price,
        "level_required": potion.level_required,
        "unlocked": player.level >= potion.level_required,
        "affordable": player.can_afford(potion.price),
        "owned": owned,
        "requirements": [
            {
                "code": "level",
                "title": "Уровень",
                "need": potion.level_required,
                "have": player.level,
                "ok": player.level >= potion.level_required,
            }
        ],
        "bonuses": potion_gains_payload(potion),
        "gain_title": "Что делает",
        "suits": [],
    }


def potions_section(player: Player) -> dict:
    """Раздел «Прочее»: то, что пьют, а не надевают."""
    rows = [
        potion_payload(player, potion, player.potion_count(potion.code))
        for potion in POTIONS
    ]
    return {
        "slot": SECTION_CODE,
        "title": SECTION_TITLE,
        "emoji": SECTION_EMOJI,
        "open": sum(1 for row in rows if row["unlocked"]),
        "items": rows,
    }


def effect_payload(effect: ActiveEffect, now: int) -> dict:
    """Действующий эффект: чем держится и сколько ему осталось."""
    potion = effect.potion
    left = effect.seconds_left(now)
    return {
        "code": effect.code,
        "boost": bool(potion and potion.is_boost),
        "title": potion.title if potion else effect.code,
        "emoji": potion.emoji if potion else "🧪",
        "gain": potion.describe() if potion else "",
        "seconds_left": left,
        "left_text": spell_duration(left),
    }


def relic_payload(player: Player, item: Item, owned: int) -> dict:
    """Товар мага. Ключи те же, что у вещи в лавке, только цена в звёздах."""
    row = goods_payload(player, item, owned)
    row["stars"] = item.stars
    row["price"] = 0
    # За звёзды берут в любой момент: копить не надо, значит и «не по карману»
    # тут не бывает — платит Telegram, а не кошелёк бойца.
    row["affordable"] = True
    row["magic"] = True
    return row


def pro_payload(
    player: Player, now: int | None = None, promo_claimed: bool = False
) -> dict:
    """Карточка подписки: она всегда стоит первой на прилавке мага.

    `promo_claimed` — забирал ли боец бесплатную неделю. Она даётся один
    раз, поэтому кнопка после этого всегда ведёт в счёт: иначе «продлить
    бесплатно» тыкается до бесконечности.
    """
    moment = int(time.time()) if now is None else now
    offer = current_offer()
    paid = pro.paid_offer()
    free = offer.promo and not promo_claimed
    left = player.pro_left(moment)
    return {
        "title": pro.TITLE,
        "emoji": pro.EMOJI,
        "image": pro.IMAGE,
        "benefits": list(pro.BENEFITS),
        "note": pro.NOTE,
        # Что предлагают этому бойцу: бесплатная неделя или обычный месяц
        "stars": 0 if free else paid.stars,
        "days": offer.days if free else paid.days,
        "free": free,
        "promo": offer.promo,
        "promo_claimed": promo_claimed,
        "promo_note": pro.promo_note(offer, promo_claimed),
        # Цена продления — всегда обычная, даже пока идёт акция
        "renew_stars": paid.stars,
        "renew_days": paid.days,
        "price_text": "бесплатно" if free else paid.price_text,
        "term_text": (offer if free else paid).term_text,
        "active": bool(left),
        "seconds_left": left,
        "left_text": spell_duration(left) if left else "",
    }


def build_magic(
    player: Player, now: int | None = None, promo_claimed: bool = False
) -> dict:
    """Лавка мага: подписка сверху, за ней товар за звёзды."""
    mine: dict[str, int] = {}
    for owned in player.gear:
        mine[owned.code] = mine.get(owned.code, 0) + 1
    return {
        "credits": player.credits,
        "level": player.level,
        "pro": pro_payload(player, now, promo_claimed),
        "items": [
            relic_payload(player, item, mine.get(item.code, 0))
            for item in MAGIC_ITEMS
        ],
    }


def build_shop(player: Player) -> dict:
    """Магазин: товары, разложенные по типам вещей."""
    mine: dict[str, int] = {}
    for owned in player.gear:
        mine[owned.code] = mine.get(owned.code, 0) + 1

    sections = []
    for slot, items in shop_sections():
        rows = [goods_payload(player, item, mine.get(item.code, 0)) for item in items]
        sections.append(
            {
                "slot": slot.value,
                "title": slot.section.capitalize(),
                "emoji": slot.emoji,
                "open": sum(1 for row in rows if row["unlocked"]),
                "items": rows,
            }
        )
    # Эликсиры идут последними: их не надевают, и слота у них нет
    sections.append(potions_section(player))
    return {
        "credits": player.credits,
        "level": player.level,
        "fclass": {"code": player.fclass.code, "title": player.fclass.title},
        "sections": sections,
    }


# ---------- комиссионка ----------


def lot_payload(player: Player, lot: dict) -> dict:
    """Строка комиссионки: чья вещь, с каким износом и за сколько."""
    from bot.game.equipment import MAX_WEAR
    from bot.game.market import fee_of, payout

    item = get_item(lot["code"])
    owned = OwnedItem(
        item=item, wear=lot["wear"], max_wear=lot["max_wear"] or MAX_WEAR
    )
    price = int(lot["price"])
    return {
        "id": lot["id"],
        "code": item.code,
        "title": item.title,
        "icon": item.emoji,
        "image": item.image,
        "slot": item.slot.value,
        "slot_title": item.slot.section.capitalize(),
        "price": price,
        # Продавцу видно, сколько дойдёт до него, покупателю — сколько отдать
        "payout": payout(price),
        "fee": fee_of(price),
        "seller_id": lot["seller_id"],
        "seller": lot["seller"] or "боец без имени",
        "mine": lot["seller_id"] == player.user_id,
        "wear": owned.wear,
        "max_wear": owned.max_wear,
        "wear_text": owned.describe_wear(),
        "affordable": player.can_afford(price),
        "can_equip": player.can_equip(item),
        "requirements": requirements_payload(player, item),
        "bonuses": bonuses_payload(item, player.fclass),
        "shop_price": item.price if item.on_sale and not item.is_magic else 0,
    }


def sellable_payload(player: Player, owned: OwnedItem) -> dict:
    """Вещь из рюкзака, которую можно выставить: с рамками цены."""
    from bot.game.market import price_range
    from bot.market_service import price_hint

    limits = price_range(owned.item)
    return {
        "id": owned.id,
        "code": owned.code,
        "title": owned.title,
        "icon": owned.emoji,
        "image": owned.image,
        "slot": owned.item.slot.value,
        "slot_title": owned.item.slot.section.capitalize(),
        "wear": owned.wear,
        "max_wear": owned.max_wear,
        "wear_text": owned.describe_wear(),
        "min_price": limits[0] if limits else 1,
        "max_price": limits[1] if limits else 0,  # 0 — потолка нет
        "hint": price_hint(owned.item),
        "shop_price": owned.item.price if limits else 0,
    }


def build_market(player: Player, lots: list[dict]) -> dict:
    """Комиссионка: полки по типам вещей, свои лоты и что можно выставить."""
    rows = [lot_payload(player, lot) for lot in lots]
    sections = []
    for slot in ALL_SLOTS:
        goods = [row for row in rows if row["slot"] == slot.value]
        if not goods:
            continue
        sections.append(
            {
                "slot": slot.value,
                "title": slot.section.capitalize(),
                "emoji": slot.emoji,
                "open": len(goods),
                "items": goods,
            }
        )
    return {
        "credits": player.credits,
        "fee": round(MARKET_FEE * 100),
        "sections": sections,
        "mine": [row for row in rows if row["mine"]],
        # Выставить можно только то, что не надето: надетое сначала снимают
        "sellable": [sellable_payload(player, owned) for owned in player.backpack],
    }


def avatar_payload(player: Player, avatar_url: str) -> dict:
    """Что показать в рамке аватара.

    Загруженное фото важнее образа: боец поставил своё лицо осознанно.
    Нет фото — показываем картинку образа, нет и её — значок образа.
    """
    look = get_look(player.look)
    # Образ не выбирали — оставляем всё как было: значок бойца
    own_face = bool(player.avatar_file_id) or look is None
    return {
        "emoji": player.avatar if own_face else look.emoji,
        "url": avatar_url or ("" if own_face else look.picture),
        "look": look.code if look else DEFAULT_LOOK,
        "look_title": look.title if look else "",
        "photo": bool(player.avatar_file_id),
    }


def fighter_row(player: Player, viewer_id: int | None) -> dict:
    """Строка списка клуба: только то, что видно в самой строке.

    Карточку по кнопке «i» отдаёт /api/card — там уже есть и аватар, и слоты,
    и счёт. Тянуть всё это в список значило бы читать экипировку каждого
    бойца ради строчки из трёх слов.
    """
    return {
        "user_id": player.user_id,
        "nickname": player.nickname,
        "level": player.level,
        "pro": player.is_pro(),
        "is_self": player.user_id == viewer_id,
        "fclass": {
            "code": player.fclass.code,
            "title": player.fclass.title,
            "emoji": player.fclass.emoji,
        },
    }


def build_club(players: list[Player], viewer_id: int | None) -> dict:
    """Список клуба: кто записан, какого уровня и что у него в карточке."""
    return {
        "fighters": [fighter_row(player, viewer_id) for player in players],
        "total": len(players),
    }


def build_topup(player: Player, open_for_business: bool = True) -> dict:
    """Касса: счёт бойца и пачки кредитов, которые можно купить за звёзды."""
    return {
        "credits": player.credits,
        "open": open_for_business,
        "packs": [
            {
                "code": pack.code,
                "title": pack.title,
                "emoji": pack.emoji,
                "credits": pack.credits,
                "bonus": pack.bonus,
                "total": pack.total,
                "stars": pack.stars,
                "note": pack.note,
                # Насколько пачка выгоднее самой маленькой, в процентах
                "profit": round(
                    (1 - pack.stars_per_hundred / PACKS[0].stars_per_hundred) * 100
                ),
            }
            for pack in PACKS
        ],
    }


def stats_payload(base: Stats, bonus: Stats) -> list[dict]:
    return [
        {
            "code": stat.value,
            "title": stat.title.capitalize(),
            # «+1 к силе»: прибавку страница называет по-русски
            "dative": stat.dative,
            "emoji": stat.emoji,
            "base": base.get(stat),
            "bonus": bonus.get(stat),
            "total": base.get(stat) + bonus.get(stat),
        }
        for stat in ALL_STATS
    ]


def build_card(
    player: Player,
    bot_token: str,
    viewer_id: int | None = None,
    now: int | None = None,
) -> dict:
    """Всё, что рисует карточка: имя, здоровье, слоты, характеристики, история."""
    moment = int(time.time()) if now is None else now
    is_self = viewer_id == player.user_id
    equipment = player.equipment
    fclass = player.fclass
    derived = derive(fclass, player.stats, player.level, player.extra_hp)

    current_hp = player.current_hp(moment)
    state = player.health_state(moment)
    ready_in = player.seconds_until_ready(moment)
    full_in = player.seconds_until_full(moment)

    avatar_url = None
    if player.avatar_file_id:
        expires = moment + AVATAR_TTL
        token = sign_avatar(player.user_id, bot_token, expires)
        avatar_url = f"avatar/{player.user_id}?expires={expires}&token={token}"

    return {
        "user_id": player.user_id,
        "name": player.nickname,
        "level": player.level,
        "is_self": is_self,
        "pro": {
            "active": player.is_pro(moment),
            "seconds_left": player.pro_left(moment),
            "left_text": spell_duration(player.pro_left(moment)),
            "badge": PRO_BADGE,
        },
        "fclass": {
            "code": fclass.code,
            "title": fclass.title,
            "emoji": fclass.emoji,
            "tagline": fclass.tagline,
        },
        "avatar": avatar_payload(player, avatar_url),
        "hp": {
            "current": current_hp,
            "max": derived.max_hp,
            "percent": round(player.hp_percent(moment) * 100),
            "state": state.value,
            "state_title": state.title,
            "color": STATE_COLORS[state],
            "can_fight": state.can_fight,
            "ready_in": ready_in,
            "ready_in_text": format_duration(ready_in) if ready_in else "",
            "full_in": full_in,
            "regen_seconds": FULL_REGEN_SECONDS,
            "full_in_text": format_duration(full_in) if full_in else "",
        },
        "stats": stats_payload(player.base_stats, equipment.bonus),
        "slots": {
            "left": [slot_payload(equipment, slot, fclass) for slot in LEFT_SLOTS],
            "right": [slot_payload(equipment, slot, fclass) for slot in RIGHT_SLOTS],
        },
        # Рюкзак показываем только хозяину карточки
        "inventory": [item_payload(player, owned) for owned in player.backpack]
        if is_self
        else [],
        # Склянки лежат там же, но своей стопкой: их пьют, а не надевают
        "potions": [
            potion_payload(player, potion, count)
            for potion, count in player.potions_in_bag()
        ]
        if is_self
        else [],
        # Что сейчас действует — видно всем: эффект уже учтён в характеристиках
        "effects": [
            effect_payload(effect, moment)
            for effect in player.active_effects(moment)
        ],
        "city": player.city,
        "progress": {
            "exp": player.exp,
            "exp_needed": player.exp_needed,
            "total_exp": player.total_exp,
            "micro_ups": player.micro_ups,
            "ups_per_level": MICRO_UPS_PER_LEVEL,
            "exp_to_next_up": player.exp_to_next_up,
            "capped": player.at_max_level,
            "max_level": MAX_LEVEL,
            "free_points": player.free_points,
        },
        "record": {
            "wins": player.wins,
            "losses": player.losses,
            "draws": player.draws,
            "rating": player.rating,
            # Чужой кошелёк не наше дело: карточку соседа открывают из чата боя
            "credits": player.credits if is_self else 0,
        },
        "birthplace": player.home,
        "birthday": format_birthday(player.created_at),
        "combat": {
            "damage_min": derived.damage_min,
            "damage_max": derived.damage_max,
            # Класс проворачивает оружие по-своему, поэтому показываем то,
            # что реально долетит до соперника. Значок оружия рядом нужен,
            # чтобы «6–14» читалось как прибавка от меча, а не как загадка:
            # у самого меча в описании стоит 7–15.
            "weapon_damage": [
                {
                    "min": round(low * fclass.damage_mult),
                    "max": round(high * fclass.damage_mult),
                    "icon": equipment.weapon_icon,
                    "title": equipment.weapon_title,
                    # Собственный урон вещи: рядом с итогом видно, откуда он
                    "base": f"{low}–{high}",
                }
                for low, high in [equipment.weapon_damage]
                if high
            ],
            # Проценты считаем той же арифметикой, что и ринг: карточка
            # показывает то, с чем боец действительно выйдет драться.
            "crit_chance": round(
                total_crit(derived.crit_chance, equipment.crit) * 100
            ),
            "crit_power": derived.crit_power,
            "anticrit": round(
                total_anticrit(derived.anticrit, equipment.anticrit) * 100
            ),
            "dodge_chance": round(
                total_dodge(derived.dodge_chance, equipment.dodge) * 100
            ),
            "accuracy": round(
                total_accuracy(derived.accuracy, equipment.accuracy) * 100
            ),
            "counter_chance": round(
                total_counter(derived.counter_chance, equipment.counter) * 100
            ),
            "resist": round(derived.resist * 100),
            "penetration": round(derived.penetration * 100),
            # Устойчивость блока к пробитию критом. Щит держит крепче, поэтому
            # число считается вместе с надетым — как и все остальные проценты.
            "block_hold": round(total_block_hold(derived.block_hold) * 100),
            # Те же числа, но с потолком и слагаемыми: карточка показывает,
            # сколько дали характеристики, сколько вещи и что срезал потолок
            "caps": {
                "crit_chance": capped_share(
                    derived.crit_chance, equipment.crit, total_crit, MAX_CRIT_CHANCE
                ),
                "anticrit": capped_share(
                    derived.anticrit, equipment.anticrit, total_anticrit, MAX_ANTICRIT
                ),
                "dodge_chance": capped_share(
                    derived.dodge_chance, equipment.dodge, total_dodge,
                    MAX_DODGE_CHANCE,
                ),
                "accuracy": capped_share(
                    derived.accuracy, equipment.accuracy, total_accuracy, MAX_ACCURACY
                ),
                "counter_chance": capped_share(
                    derived.counter_chance, equipment.counter, total_counter,
                    MAX_COUNTER_CHANCE,
                ),
                "block_hold": capped_share(
                    derived.block_hold, 0.0, total_block_hold, MAX_BLOCK_HOLD
                ),
            },
        },
        "armor": [
            {
                "zone": zone.value,
                "title": zone.title.capitalize(),
                "emoji": zone.emoji,
                "min": low,
                "max": high,
            }
            for zone, (low, high) in (
                (zone, equipment.armor_range(zone)) for zone in ALL_ZONES
            )
        ],
    }
