"""Экраны мафии: особняк со списком кланов и вкладка своего клана.

Здесь только то, что видно на странице. Правила живут в
`bot.game.clans`, действия — в `bot.clan_service`, хранение — в
`bot.database`.

Один экран, три вкладки и одна строка. Особняк — про весь город:
«Кланы» заводят и выбирают семью, «Состав» разбирает чины и заявки,
«Имущество» держит сейф и хранилище. Тот же состав едет отдельным
ответом в карточку бойца: под местоположением стоит имя клана, и по
нему открывается то же окно — чтобы поднять или выгнать бойца, не
переходя через полгорода.

А строка одна на все списки города: класс, знак клана, ник, премиум,
уровень — ровно в этом порядке, и собирает её `member_row`.
"""

from __future__ import annotations

from typing import Any

from bot.content.clans import SIGNS, get_sign
from bot.game.clans import (
    CLAN_PRICE,
    MAX_CAPOS,
    MAX_MEMBERS,
    NAME_MAX,
    NAME_MIN,
    RENAME_DAYS,
    RENAME_PRICE,
    VAULT_SLOTS,
    Clan,
    Knock,
    Member,
    Rank,
    Roster,
    Stored,
)
from bot.game.equipment import get_item
from bot.game.gear import OwnedItem
from bot.game.classes import get_class
from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.game.pro import PRO_BADGE
from bot.models import Player
from bot.webapp.bank import purse_payload


def sign_payload(code: str) -> dict[str, Any]:
    """Знак клана: код, имя из раздачи и картинка. Пусто — знака нет."""
    sign = get_sign(code)
    if sign is None:
        return {}
    return {"code": sign.code, "title": sign.title, "picture": sign.picture}


def clan_payload(
    clan: Clan | None, bot_token: str = "", now: int | None = None
) -> dict[str, Any]:
    """Клан одной записью: имя, знак, сколько людей и сколько мест.

    `bot_token` здесь уже ни при чём — знаки лежат в бакете и открыты
    всем, — но место в подписи он держит: списки клуба зовут этот сбор
    через `fighter_badges`, и менять там подпись ради одного пустого
    слова незачем.
    """
    if clan is None:
        return {}
    moment = now_ts() if now is None else now
    return {
        "id": clan.id,
        "name": clan.name,
        "sign": clan.sign,
        "emblem": clan.picture,
        "members": clan.members,
        "max_members": MAX_MEMBERS,
        "free": clan.free_seats,
        "full": clan.full,
        "founded": club_moment(clan.created_at) if clan.created_at else "",
        # Смена имени: можно ли сейчас и сколько ждать, если нет
        "can_rename": clan.can_rename(moment),
        "rename_left": clan.rename_left(moment),
        "rename_at": (
            club_moment(clan.rename_at()) if not clan.can_rename(moment) else ""
        ),
    }


def fighter_badges(
    player: Player, bot_token: str = "", now: int | None = None
) -> dict:
    """Знак клана для строки списка. Пусто — боец сам по себе."""
    standing = player.clan
    if standing is None:
        return {}
    row = clan_payload(standing.clan, bot_token, now)
    row["rank"] = standing.rank.value
    row["rank_title"] = standing.rank.title
    return row


def member_row(
    member: Member,
    clan: Clan,
    bot_token: str,
    moment: int,
    viewer: Player | None = None,
) -> dict[str, Any]:
    """Строка участника клана — та самая, со знаком семьи.

    Порядок в строке: класс, знак клана, ник, премиум, уровень. Кнопки
    старших приезжают той же строкой: страница не должна гадать, кого ей
    можно выгнать, — это решает сервер.
    """
    fclass = get_class(member.class_code)
    standing = viewer.clan if viewer is not None else None
    boss = bool(
        standing
        and standing.can_admin
        and standing.clan.id == clan.id
        and member.rank is not Rank.DON
        and member.user_id != viewer.user_id
    )
    don = bool(standing and standing.is_don and standing.clan.id == clan.id)
    return {
        "user_id": member.user_id,
        "nickname": member.nickname,
        "level": member.level,
        "pro": member.pro_until > moment,
        "pro_badge": PRO_BADGE,
        "emblem": clan.picture,
        "clan": clan.name,
        "rank": member.rank.value,
        "rank_title": member.rank.title,
        "fclass": {
            "code": fclass.code,
            "title": fclass.title,
            "emoji": fclass.emoji,
        },
        "joined": club_moment(member.joined_at) if member.joined_at else "",
        "is_self": bool(viewer and member.user_id == viewer.user_id),
        # Что с этим бойцом может сделать тот, кто смотрит
        "can_expel": boss,
        "can_promote": boss and member.rank is Rank.SOLDIER,
        "can_demote": boss and member.rank is Rank.CAPO,
        # Передача дел стоит на строке самого Дона: отдаёт он их, а не
        # получает, и выбирает преемника списком в окне. На чужой строке
        # такой кнопки нет — иначе их было бы столько же, сколько людей
        "can_handover": bool(don and member.user_id == viewer.user_id),
    }


def sign_picture(code: str) -> str:
    """Картинка знака по коду. Пусто — такого знака не рисовали."""
    sign = get_sign(code)
    return sign.picture if sign else ""


def knock_row(
    knock: Knock, bot_token: str, moment: int, clan: Clan | None = None
) -> dict[str, Any]:
    """Строка заявки или приглашения.

    Заявка рассказывает про бойца — его и принимают. Приглашение
    рассказывает про клан: боец решает, идти ли туда.
    """
    row: dict[str, Any] = {
        "clan_id": knock.clan_id,
        "user_id": knock.user_id,
        "kind": knock.kind.value,
        "when": club_moment(knock.created_at) if knock.created_at else "",
    }
    if knock.nickname:
        fclass = get_class(knock.class_code)
        row.update(
            {
                "nickname": knock.nickname,
                "level": knock.level,
                "pro": knock.pro_until > moment,
                "pro_badge": PRO_BADGE,
                "fclass": {
                    "code": fclass.code,
                    "title": fclass.title,
                    "emoji": fclass.emoji,
                },
            }
        )
    if knock.clan_name:
        row.update(
            {
                "clan": knock.clan_name,
                "emblem": (clan.picture if clan else sign_picture(knock.clan_sign)),
                "by": knock.by_name,
            }
        )
    return row


def build_mansion(
    player: Player,
    clans: list[Clan],
    knocks: list[Knock],
    bot_token: str,
    now: int | None = None,
    family: dict[str, Any] | None = None,
    estate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Экран особняка тремя вкладками: кланы, состав и имущество.

    `knocks` — заявки самого бойца: по ним в списке и видно, куда он уже
    стучался и что можно отозвать. `family` и `estate` — две внутренние
    вкладки: их собирают `build_clan` и `build_estate`, а здесь они
    только складываются в один ответ, чтобы страница не ходила за
    особняком трижды. Пусто — боец ни в какой семье, и показывать там
    нечего.
    """
    moment = now_ts() if now is None else now
    standing = player.clan
    mine = {knock.clan_id for knock in knocks}
    price = player.price_here(CLAN_PRICE, moment, Service.MAFIA)
    return {
        "title": "Особняк мафии",
        "price": price,
        "full_price": CLAN_PRICE,
        "credits": player.credits,
        # Чем платят за клан: наличные или счёт, как в лавке
        "purse": purse_payload(player, moment, Service.MAFIA),
        "can_pay": player.can_afford(price, moment, Service.MAFIA),
        "max_members": MAX_MEMBERS,
        "max_capos": MAX_CAPOS,
        "name_min": NAME_MIN,
        "name_max": NAME_MAX,
        "rename_price": RENAME_PRICE,
        "rename_days": RENAME_DAYS,
        # Знаки: все нарисованные, и у занятых сказано, кем. Выбирают из
        # свободных, и когда свободных нет — новый клан не завести
        "signs": signs_payload(clans),
        "free_signs": sum(1 for one in signs_payload(clans) if not one["taken"]),
        # Свой клан: он же первый в списке и с кнопкой «уйти»
        "clan": fighter_badges(player, bot_token, moment),
        "in_clan": standing is not None,
        "is_don": bool(standing and standing.is_don),
        "clans": [
            dict(
                clan_payload(clan, bot_token, moment),
                mine=bool(standing and standing.clan.id == clan.id),
                asked=clan.id in mine,
            )
            for clan in clans
        ],
        # Две внутренние вкладки одним ответом: «Состав» и «Имущество»
        "family": family
        if family is not None
        else build_clan(player, None, [], [], bot_token, moment),
        "estate": estate
        if estate is not None
        else build_estate(player, [], moment),
        "note": mansion_note(player),
        "said": "",
    }


def signs_payload(clans: list[Clan]) -> list[dict[str, Any]]:
    """Все знаки города: какие свободны, а какие уже чьи."""
    taken = {clan.sign: clan.name for clan in clans}
    return [
        dict(
            sign_payload(sign.code),
            taken=sign.code in taken,
            owner=taken.get(sign.code, ""),
        )
        for sign in SIGNS
    ]


def mansion_note(player: Player) -> str:
    """Строка сверху экрана: что тут делают."""
    standing = player.clan
    if standing is None:
        return (
            f"Клан заводят здесь: {CLAN_PRICE} 💰, имя и свой знак. "
            f"В семье до {MAX_MEMBERS} человек. Можно и постучаться в чужую."
        )
    if standing.is_don:
        return (
            f"Ты Дон клана «{standing.clan.name}». Состав и имущество — на "
            "соседних вкладках; уйти можно, только передав дела другому."
        )
    return (
        f"Ты в клане «{standing.clan.name}», чин — "
        f"{standing.rank.title.lower()}. Состав и имущество — на соседних "
        "вкладках."
    )


def stored_row(player: Player, stored: Stored, moment: int) -> dict[str, Any]:
    """Вещь с полки хранилища: та же строка, что в рюкзаке, плюс чья она.

    Строку собирает рюкзачный `item_payload`: вещь на полке — та же
    вещь, и показывать её вторым способом значило бы рассказывать про
    износ и требования дважды. Зовётся он отсюда, а не сверху файла:
    карточка сама берёт у этого модуля знак клана, и встречный импорт
    закольцевал бы оба.
    """
    from bot.webapp.card import item_payload

    item = get_item(stored.code)
    if item is None:  # pragma: no cover - вещь выкинули из раздачи
        return {}
    owned = OwnedItem(
        item=item,
        id=stored.id,
        wear=stored.wear,
        max_wear=stored.max_wear,
        mod=stored.mod,
        mod_value=stored.mod_value,
    )
    row = item_payload(player, owned)
    row["by"] = stored.by_name
    row["by_id"] = stored.by_id
    row["when"] = club_moment(stored.put_at) if stored.put_at else ""
    return row


def build_estate(
    player: Player, items: list[Stored], now: int | None = None
) -> dict[str, Any]:
    """Вкладка «Имущество»: сейф с кредитами и склад на тридцать вещей.

    Остаток сейфа видит вся семья — общий карман тем и общий, что в него
    видно. А брать из него и с полок дают только старшим, и это решает
    не страница: кнопки она прячет по `can_take`, но отказывает сервер.
    """
    from bot.webapp.card import item_payload

    moment = now_ts() if now is None else now
    standing = player.clan
    if standing is None:
        return {
            "in_clan": False,
            "vault": 0,
            "slots": VAULT_SLOTS,
            "free": VAULT_SLOTS,
            "can_take": False,
            "items": [],
            "bag": [],
        }
    rows = [
        row
        for row in (stored_row(player, one, moment) for one in items)
        if row
    ]
    return {
        "in_clan": True,
        "clan": standing.clan.name,
        "vault": standing.clan.vault,
        "slots": VAULT_SLOTS,
        "free": max(0, VAULT_SLOTS - len(rows)),
        # Класть можно всем, брать — только Дону и приближённым
        "can_take": standing.can_admin,
        "credits": player.credits,
        "purse": purse_payload(player, moment, Service.MAFIA),
        "items": rows,
        # Что боец может положить: снятое из рюкзака. Надетое на полку не
        # ложится — сперва его снимают в инвентаре
        "bag": [item_payload(player, owned) for owned in player.backpack],
    }


def build_clan(
    player: Player,
    roster: Roster | None,
    requests: list[Knock],
    invites: list[Knock],
    bot_token: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Вкладка «Клан»: кто в семье, кто стучится и кого позвали.

    Пустой ответ — бойца в клане нет, и вкладки у него тоже нет.
    """
    moment = now_ts() if now is None else now
    if roster is None or player.clan is None:
        return {"in_clan": False, "clan": {}, "ranks": [], "said": ""}
    clan = roster.clan
    standing = player.clan
    rows = [
        member_row(member, clan, bot_token, moment, player)
        for member in sorted(
            roster.members, key=lambda one: (one.rank.order, one.joined_at)
        )
    ]
    by_rank = {rank: [row for row in rows if row["rank"] == rank.value] for rank in Rank}
    return {
        "in_clan": True,
        "clan": dict(
            clan_payload(clan, bot_token, moment),
            rank=standing.rank.value,
            rank_title=standing.rank.title,
        ),
        "can_admin": standing.can_admin,
        "is_don": standing.is_don,
        "max_capos": MAX_CAPOS,
        "free_capo_seats": roster.free_capo_seats,
        # Семья тремя рядами: Дон, приближённые, мафиози
        "ranks": [
            {
                "code": rank.value,
                "title": rank.plural,
                "members": by_rank[rank],
                "limit": MAX_CAPOS if rank is Rank.CAPO else 0,
            }
            for rank in Rank
        ],
        "members": rows,
        # Заявки и свои приглашения видят только старшие: мафиози эти
        # списки не решают, и показывать ему чужие ники незачем
        "requests": [
            knock_row(knock, bot_token, moment) for knock in requests
        ]
        if standing.can_admin
        else [],
        "invites": [
            knock_row(knock, bot_token, moment) for knock in invites
        ]
        if standing.can_admin
        else [],
        "said": "",
    }


def invites_payload(
    knocks: list[Knock], clans: dict[int, Clan], bot_token: str, now: int | None = None
) -> list[dict]:
    """Приглашения, которые ждут ответа бойца.

    Они едут в карточке: всплывающее окно показывают при входе в игру, а
    не в особняке, — позвали тебя однажды, и ответить надо один раз.
    """
    moment = now_ts() if now is None else now
    return [
        knock_row(knock, bot_token, moment, clans.get(knock.clan_id))
        for knock in knocks
    ]


__all__ = [
    "build_clan",
    "build_estate",
    "build_mansion",
    "clan_payload",
    "fighter_badges",
    "invites_payload",
    "knock_row",
    "mansion_note",
    "member_row",
    "sign_payload",
    "sign_picture",
    "signs_payload",
    "stored_row",
]
