"""Кланы: завести семью, принять в неё и выставить из неё.

Правила держит `bot.game.clans`, хранение — `bot.database`, а здесь то,
что меняет состояние: списать тысячу за клан, одобрить заявку, позвать
бойца, выгнать, назначить приближённого и передать дела.

Места проверяются здесь, а не на странице: спрятанная кнопка обходится
запросом мимо интерфейса. Заводят клан, подают заявку, уходят из семьи и
ведут её имущество в Особняке мафии — туда и надо дойти ногами: общий
карман лежит в доме, а не в кармане на ходу. А вот дела внутри семьи
(заявки, приглашения, чины) решают откуда угодно: состав у бойца в
карточке, и бежать ради чина через весь город незачем.
"""

from __future__ import annotations

import logging
import sqlite3

from bot.database import Database
from bot.game.clans import (
    CLAN_PRICE,
    MANSION,
    MAX_CAPOS,
    RENAME_DAYS,
    RENAME_PRICE,
    VAULT_SLOTS,
    Clan,
    Petition,
    Rank,
    Standing,
    clean_name,
    name_problem,
    sign_problem,
)
from bot.game.equipment import get_item
from bot.game.health import format_duration
from bot.game.health import now_ts
from bot.game.locations import Service
from bot.models import Player

logger = logging.getLogger(__name__)


class ClanError(Exception):
    """Отказ по делам семьи, который можно показать игроку как есть."""


def _at_mansion(player: Player, moment: int, what: str) -> None:
    if player.where(moment) != MANSION:
        raise ClanError(f"{what} можно в Особняке мафии, а не отсюда.")


def _standing(player: Player) -> Standing:
    if player.clan is None:
        raise ClanError("Ты не в клане.")
    return player.clan


def _admin(player: Player) -> Standing:
    standing = _standing(player)
    if not standing.can_admin:
        raise ClanError("Это решают Дон и приближённые.")
    return standing


async def _refresh(db: Database, player: Player) -> Standing | None:
    """Перечитать чин бойца: после каждого хода он мог измениться."""
    player.clan = await db.standing_of(player.user_id)
    return player.clan


# ---------- завести клан ----------


async def found(
    db: Database,
    player: Player,
    name: str,
    sign: str,
    now: int | None = None,
) -> Clan:
    """Завести клан: тысяча кредитов, своё имя и свободный знак."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Завести клан")
    if player.clan is not None:
        raise ClanError("Ты уже в клане: из двух семей сразу не выйдет.")

    trouble = name_problem(name)
    if trouble:
        raise ClanError(trouble)
    trouble = sign_problem(sign, await db.taken_signs())
    if trouble:
        raise ClanError(trouble)
    title = clean_name(name)
    if await db.clan_by_name(title) is not None:
        raise ClanError(f"Клан «{title}» в городе уже есть.")

    price = player.price_here(CLAN_PRICE, moment, Service.MAFIA)
    if not player.can_afford(price, moment, Service.MAFIA):
        raise ClanError(
            f"Клан стоит {price} 💰, а {player.purse_note(moment, Service.MAFIA)}."
        )

    try:
        clan = await db.create_clan(title, player.user_id, sign, moment)
    except sqlite3.IntegrityError as error:
        # Имя или знак заняли между проверкой и записью: и то и другое
        # держит база, и ловить это питоном значило бы ловить с опозданием
        raise ClanError(
            f"Клан «{title}» или этот знак уже заняли. Попробуй ещё раз."
        ) from error

    player.pay(price, moment, Service.MAFIA)
    await db.save_player(player)
    await _refresh(db, player)
    logger.info("Боец %s завёл клан %s", player.user_id, clan.id)
    return clan


async def rename(
    db: Database, player: Player, name: str, now: int | None = None
) -> str:
    """Сменить имя клана: двести кредитов и не чаще раза в полгода.

    Меняет Дон: имя семьи — его слово, а не слово приближённого. Срок
    считается от прошлой смены, а не от основания: первое имя меняют
    когда угодно.

    Место — особняк, как и у основания: за имя платят, а платят в
    городе, а не из кармана на ходу.
    """
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Сменить имя клана")
    standing = _standing(player)
    if not standing.is_don:
        raise ClanError("Имя семьи меняет Дон.")
    clan = await db.clan_by_id(standing.clan.id)
    if clan is None:  # pragma: no cover - клан не исчезает на ходу
        raise ClanError("Такого клана нет.")
    if not clan.can_rename(moment):
        left = format_duration(clan.rename_left(moment))
        raise ClanError(
            f"Имя меняют не чаще раза в {RENAME_DAYS} дней — ждать ещё {left}."
        )

    trouble = name_problem(name)
    if trouble:
        raise ClanError(trouble)
    title = clean_name(name)
    if title.lower() == clan.name.lower():
        raise ClanError("Это и есть нынешнее имя.")
    twin = await db.clan_by_name(title)
    if twin is not None and twin.id != clan.id:
        raise ClanError(f"Клан «{title}» в городе уже есть.")

    price = player.price_here(RENAME_PRICE, moment, Service.MAFIA)
    if not player.can_afford(price, moment, Service.MAFIA):
        raise ClanError(
            f"Смена имени стоит {price} 💰, а "
            f"{player.purse_note(moment, Service.MAFIA)}."
        )
    try:
        await db.rename_clan(clan.id, title, moment)
    except sqlite3.IntegrityError as error:
        raise ClanError(f"Клан «{title}» в городе уже есть.") from error
    player.pay(price, moment, Service.MAFIA)
    await db.save_player(player)
    await _refresh(db, player)
    logger.info("Клан %s сменил имя на %s", clan.id, title)
    return f"Теперь это клан «{title}». Следующая смена — через полгода."


# ---------- дорога в семью ----------


async def apply(
    db: Database, player: Player, clan_id: int, now: int | None = None
) -> str:
    """Подать заявку в клан. Если туда уже звали — это и есть согласие."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Подать заявку")
    if player.clan is not None:
        raise ClanError("Ты уже в клане. Сначала уйди из нынешнего.")
    clan = await db.clan_by_id(clan_id)
    if clan is None:
        raise ClanError("Такого клана нет.")

    knock = await db.petition(clan_id, player.user_id)
    if knock is not None and knock.kind is Petition.INVITE:
        await _join(db, player, clan, moment)
        return f"Приглашение принято: ты в клане «{clan.name}»."
    if knock is not None:
        raise ClanError("Заявка в этот клан уже подана.")
    if clan.full:
        raise ClanError(f"В клане «{clan.name}» мест нет.")
    await db.add_petition(clan_id, player.user_id, Petition.REQUEST, now=moment)
    return f"Заявка в клан «{clan.name}» подана. Ждите ответа старших."


async def withdraw(
    db: Database, player: Player, clan_id: int, now: int | None = None
) -> str:
    """Отозвать свою заявку."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Отозвать заявку")
    knock = await db.petition(clan_id, player.user_id)
    if knock is None or knock.kind is not Petition.REQUEST:
        raise ClanError("Такой заявки нет.")
    await db.drop_petition(clan_id, player.user_id)
    return "Заявка отозвана."


async def accept(
    db: Database, player: Player, clan_id: int, now: int | None = None
) -> str:
    """Принять приглашение. Отвечают на него откуда угодно."""
    moment = now_ts() if now is None else now
    if player.clan is not None:
        raise ClanError("Ты уже в клане. Сначала уйди из нынешнего.")
    knock = await db.petition(clan_id, player.user_id)
    if knock is None or knock.kind is not Petition.INVITE:
        raise ClanError("Такого приглашения нет.")
    clan = await db.clan_by_id(clan_id)
    if clan is None:  # pragma: no cover - клан распустили, пока думали
        raise ClanError("Такого клана нет.")
    await _join(db, player, clan, moment)
    return f"Ты в клане «{clan.name}»."


async def refuse(
    db: Database, player: Player, clan_id: int, now: int | None = None
) -> str:
    """Отказаться от приглашения."""
    knock = await db.petition(clan_id, player.user_id)
    if knock is None or knock.kind is not Petition.INVITE:
        raise ClanError("Такого приглашения нет.")
    await db.drop_petition(clan_id, player.user_id)
    return "Приглашение отклонено."


async def _join(db: Database, player: Player, clan: Clan, moment: int) -> None:
    """Посадить бойца в клан мафиози и снять все его заявки."""
    if clan.full:
        raise ClanError(f"В клане «{clan.name}» мест нет.")
    await db.add_member(player.user_id, clan.id, Rank.SOLDIER, moment)
    await _refresh(db, player)
    logger.info("Боец %s вошёл в клан %s", player.user_id, clan.id)


# ---------- дела старших ----------


async def approve(
    db: Database, player: Player, user_id: int, now: int | None = None
) -> str:
    """Одобрить заявку: боец становится мафиози."""
    moment = now_ts() if now is None else now
    standing = _admin(player)
    knock = await db.petition(standing.clan.id, user_id)
    if knock is None or knock.kind is not Petition.REQUEST:
        raise ClanError("Такой заявки нет.")
    if await db.standing_of(user_id) is not None:
        await db.drop_petition(standing.clan.id, user_id)
        raise ClanError("Этот боец уже в клане — заявка снята.")
    clan = await db.clan_by_id(standing.clan.id)
    if clan is None or clan.full:  # pragma: no cover - клан не исчезает на ходу
        raise ClanError("В клане мест нет.")
    await db.add_member(user_id, clan.id, Rank.SOLDIER, moment)
    logger.info("В клан %s принят боец %s", clan.id, user_id)
    return "Заявка одобрена: в семье новый мафиози."


async def decline(
    db: Database, player: Player, user_id: int, now: int | None = None
) -> str:
    """Отклонить заявку."""
    standing = _admin(player)
    knock = await db.petition(standing.clan.id, user_id)
    if knock is None or knock.kind is not Petition.REQUEST:
        raise ClanError("Такой заявки нет.")
    await db.drop_petition(standing.clan.id, user_id)
    return "Заявка отклонена."


async def invite(
    db: Database, player: Player, nickname: str, now: int | None = None
) -> str:
    """Позвать бойца по нику. Ответит он сам — всплывающим окном при входе."""
    moment = now_ts() if now is None else now
    standing = _admin(player)
    name = (nickname or "").strip()
    if not name:
        raise ClanError("Кого звать-то? Напиши ник.")
    guest = await db.find_by_nickname(name)
    if guest is None:
        raise ClanError(f"Бойца с ником «{name}» в клубе нет.")
    if guest.user_id == player.user_id:
        raise ClanError("Ты и так в семье.")
    if await db.standing_of(guest.user_id) is not None:
        raise ClanError(f"{guest.nickname} уже в клане.")
    clan = await db.clan_by_id(standing.clan.id)
    if clan is None:  # pragma: no cover - клан не исчезает на ходу
        raise ClanError("Такого клана нет.")
    if clan.full:
        raise ClanError("В клане мест нет: сперва освободи место.")
    knock = await db.petition(clan.id, guest.user_id)
    if knock is not None and knock.kind is Petition.INVITE:
        raise ClanError(f"{guest.nickname} уже приглашён.")
    if knock is not None and knock.kind is Petition.REQUEST:
        # Боец и сам стучался — звать его незачем, достаточно одобрить
        await db.add_member(guest.user_id, clan.id, Rank.SOLDIER, moment)
        logger.info("В клан %s принят боец %s по встречной заявке", clan.id,
                    guest.user_id)
        return f"{guest.nickname} и так стучался — теперь он в семье."
    await db.add_petition(
        clan.id, guest.user_id, Petition.INVITE, by_id=player.user_id, now=moment
    )
    return f"{guest.nickname} приглашён. Ответит, когда зайдёт в игру."


async def expel(
    db: Database, player: Player, user_id: int, now: int | None = None
) -> str:
    """Выгнать из клана. Дона не выгоняют — его только сменяют."""
    standing = _admin(player)
    if user_id == player.user_id:
        raise ClanError("Себя не выгоняют — из семьи выходят.")
    target = await db.standing_of(user_id)
    if target is None or target.clan.id != standing.clan.id:
        raise ClanError("Этот боец не в твоём клане.")
    if target.rank is Rank.DON:
        raise ClanError("Дона из семьи не выгоняют.")
    await db.remove_member(user_id)
    logger.info("Из клана %s выгнан боец %s", standing.clan.id, user_id)
    return "Боец выставлен из семьи."


async def promote(
    db: Database, player: Player, user_id: int, now: int | None = None
) -> str:
    """Назначить приближённого. Их трое — больше мест нет."""
    standing = _admin(player)
    target = await db.standing_of(user_id)
    if target is None or target.clan.id != standing.clan.id:
        raise ClanError("Этот боец не в твоём клане.")
    if target.rank is Rank.DON:
        raise ClanError("Дон и так старший.")
    if target.rank is Rank.CAPO:
        raise ClanError("Он уже приближённый.")
    if await db.count_rank(standing.clan.id, Rank.CAPO) >= MAX_CAPOS:
        raise ClanError(f"Приближённых уже {MAX_CAPOS}: больше мест нет.")
    await db.set_rank(user_id, Rank.CAPO)
    await _refresh(db, player)
    return "В семье новый приближённый."


async def demote(
    db: Database, player: Player, user_id: int, now: int | None = None
) -> str:
    """Снять приближённого: он остаётся в семье мафиози."""
    standing = _admin(player)
    target = await db.standing_of(user_id)
    if target is None or target.clan.id != standing.clan.id:
        raise ClanError("Этот боец не в твоём клане.")
    if target.rank is Rank.DON:
        raise ClanError("Дона не снимают: он сам передаёт дела.")
    if target.rank is not Rank.CAPO:
        raise ClanError("Он и так мафиози.")
    await db.set_rank(user_id, Rank.SOLDIER)
    await _refresh(db, player)
    return "Приближённый снова мафиози."


async def handover(
    db: Database,
    player: Player,
    user_id: int,
    keep: str = Rank.CAPO.value,
    now: int | None = None,
) -> str:
    """Передать дела. Прежний Дон остаётся в семье — кем сам решит."""
    standing = _standing(player)
    if not standing.is_don:
        raise ClanError("Дела передаёт Дон.")
    if user_id == player.user_id:
        raise ClanError("Себе дела не передают.")
    target = await db.standing_of(user_id)
    if target is None or target.clan.id != standing.clan.id:
        raise ClanError("Этот боец не в твоём клане.")
    try:
        mine = Rank(keep)
    except ValueError as error:
        raise ClanError("Таким в семье не остаются.") from error
    if mine is Rank.DON:
        raise ClanError("Дон в семье один.")

    clan_id = standing.clan.id
    capos = await db.count_rank(clan_id, Rank.CAPO)
    # Новый Дон освобождает своё место приближённого, если был им
    if target.rank is Rank.CAPO:
        capos -= 1
    if mine is Rank.CAPO and capos >= MAX_CAPOS:
        raise ClanError(
            f"Приближённых уже {MAX_CAPOS}: снимай кого-то или оставайся мафиози."
        )

    await db.set_rank(user_id, Rank.DON)
    await db.set_rank(player.user_id, mine)
    await db.set_don(clan_id, user_id)
    await _refresh(db, player)
    logger.info("В клане %s новый Дон: %s", clan_id, user_id)
    return f"Дела переданы. Ты теперь {mine.title.lower()}."


# ---------- имущество семьи ----------


async def deposit(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Положить кредиты в сейф клана. Кладут все, кто в семье."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Открыть сейф")
    standing = _standing(player)
    if amount <= 0:
        raise ClanError("Сколько положить-то? Напиши число.")
    if not player.can_afford(amount, moment, Service.MAFIA):
        raise ClanError(
            f"В сейф нужно {amount} 💰, а "
            f"{player.purse_note(moment, Service.MAFIA)}."
        )
    # Сперва сейф, потом карман: если сейфа уже нет, с бойца и не снято
    if not await db.move_vault(standing.clan.id, amount):  # pragma: no cover
        raise ClanError("Такого клана нет.")
    player.pay(amount, moment, Service.MAFIA)
    await db.save_player(player)
    await _refresh(db, player)
    logger.info("Боец %s положил %s в сейф клана %s", player.user_id, amount,
                standing.clan.id)
    return f"В сейф семьи легло {amount} 💰."


async def withdraw_vault(
    db: Database, player: Player, amount: int, now: int | None = None
) -> str:
    """Взять кредиты из сейфа. Берут только Дон и приближённые."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Открыть сейф")
    standing = _admin(player)
    if amount <= 0:
        raise ClanError("Сколько взять-то? Напиши число.")
    if not await db.move_vault(standing.clan.id, -amount):
        clan = await db.clan_by_id(standing.clan.id)
        have = clan.vault if clan else 0
        raise ClanError(f"В сейфе {have} 💰 — столько не взять.")
    player.credits += amount
    await db.save_player(player)
    await _refresh(db, player)
    logger.info("Боец %s взял %s из сейфа клана %s", player.user_id, amount,
                standing.clan.id)
    return f"Из сейфа взято {amount} 💰 наличными."


async def stash(
    db: Database, player: Player, item_id: int, now: int | None = None
) -> str:
    """Положить вещь в хранилище семьи. Кладут все, кто в семье."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Положить вещь на полку")
    standing = _standing(player)
    owned = player.find_gear(item_id)
    if owned is None:
        raise ClanError("Такой вещи у тебя нет.")
    if owned.is_equipped:
        raise ClanError("Сперва сними вещь — надетое на полку не кладут.")
    if not await db.stash_gear(
        item_id, player.user_id, standing.clan.id, VAULT_SLOTS, moment
    ):
        raise ClanError(f"В хранилище {VAULT_SLOTS} полок, и все заняты.")
    player.drop_gear(owned)
    logger.info("Боец %s положил вещь %s в хранилище клана %s", player.user_id,
                item_id, standing.clan.id)
    return f"«{owned.item.title}» лежит в хранилище семьи."


async def take(
    db: Database, player: Player, stored_id: int, now: int | None = None
) -> str:
    """Забрать вещь из хранилища. Берут только Дон и приближённые."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Взять вещь с полки")
    standing = _admin(player)
    code = await db.take_clan_item(stored_id, standing.clan.id, player.user_id)
    if not code:
        raise ClanError("Этой вещи в хранилище уже нет.")
    player.gear = await db.list_gear(player.user_id)
    item = get_item(code)
    logger.info("Боец %s забрал вещь %s из хранилища клана %s", player.user_id,
                stored_id, standing.clan.id)
    title = item.title if item else code
    return f"«{title}» теперь у тебя в рюкзаке."


# ---------- уйти ----------


async def leave(db: Database, player: Player, now: int | None = None) -> str:
    """Выйти из клана. Дон уходит, только передав дела или распустив семью."""
    moment = now_ts() if now is None else now
    _at_mansion(player, moment, "Выйти из клана")
    standing = _standing(player)
    clan = await db.clan_by_id(standing.clan.id)
    if clan is None:  # pragma: no cover - клан не исчезает на ходу
        raise ClanError("Такого клана нет.")
    if standing.is_don and clan.members > 1:
        raise ClanError(
            "Дон уходит, только передав дела: выбери преемника в составе семьи."
        )
    await db.remove_member(player.user_id)
    said = f"Ты больше не в клане «{clan.name}»."
    if standing.is_don:
        # Уходил последний: семьи больше нет, и держать её незачем
        await db.delete_clan(clan.id)
        said = f"Клан «{clan.name}» распущен."
    await _refresh(db, player)
    logger.info("Боец %s вышел из клана %s", player.user_id, clan.id)
    return said


__all__ = [
    "ClanError",
    "accept",
    "apply",
    "approve",
    "decline",
    "demote",
    "deposit",
    "expel",
    "found",
    "handover",
    "invite",
    "leave",
    "promote",
    "rename",
    "refuse",
    "stash",
    "take",
    "withdraw",
    "withdraw_vault",
]
