"""Групповая часть: ринг, вызовы и нажатия во время боя."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from bot.battle_service import BattleError, BattleService
from bot.database import Database
from bot.duel_service import DuelError, DuelService
from bot.game.battle import (
    MIN_ROYALE,
    MIN_TEAM_SIZE,
    BattleKind,
)
from bot.game.modes import FIST_RINGS, FightMode
from bot.game.narrator import esc, name_link, plain
from bot.handlers.common import thread_id_of
from bot.keyboards import BattleCB, ChallengeCB, FightCB, LobbyCB, StandoffCB, TourCB
from bot.tournament_service import TournamentError, TournamentService
from bot.models import Player, Ring

logger = logging.getLogger(__name__)

router = Router(name="group")
GROUP_TYPES = {"group", "supergroup"}

NO_CHARACTER = (
    "У тебя нет бойца. Напиши мне в личку /start — соберём персонажа за минуту."
)


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in {"creator", "administrator"}


def _rings_list(rings: list[Ring]) -> str:
    return "\n".join(f"• {ring.label} — {ring.command}" for ring in rings)


async def _ring_for(message: Message, db: Database, mode: FightMode) -> Ring | None:
    """Ринг этой ветки, если здесь дерутся именно в этом режиме.

    Пока в группе не размечен ни один ринг, драться можно где угодно — так
    клуб заводится в один шаг. Как только ринги появились, бои идут только
    в них и только в своём режиме.
    """
    thread_id = thread_id_of(message)
    rings = await db.list_rings(message.chat.id)
    if not rings:
        return Ring(chat_id=message.chat.id, thread_id=thread_id, mode=mode)

    ring = await db.get_ring(message.chat.id, thread_id)
    if ring is None:
        await message.reply(
            "Здесь не дерутся. Ринги клуба:\n" + _rings_list(rings)
        )
        return None
    if ring.mode is not mode:
        await message.reply(
            f"Это {ring.label}. Вызов здесь бросают командой "
            f"{ring.mode.command}."
        )
        return None
    return ring


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def added_to_group(event: ChatMemberUpdated) -> None:
    if event.new_chat_member.status not in {"member", "administrator"}:
        return
    await event.bot.send_message(
        event.chat.id,
        "🥊 <b>Бойцовский клуб открыт.</b>\n\n"
        "Создайте ветки для боёв и отметьте их: /arena1, /arena2, /arena3 — "
        "кулачные ринги, /arena_gear — ринг с оружием. В каждом ринге идёт "
        "свой бой, так что драк может быть несколько разом.\n"
        "Бойцы регистрируются у меня в личке командой /start, дерутся "
        "здесь: /duel на кулаках, /fight с оружием.\n\n"
        "Подробности — /help",
    )


FIST_COMMANDS = tuple(f"arena{number}" for number in range(1, FIST_RINGS + 1))
GEAR_COMMANDS = ("arena_gear", "armory")


def _ring_number(command: str | None) -> int:
    """«arena2» → 2. Голая /arena — первый ринг."""
    digits = "".join(ch for ch in (command or "") if ch.isdigit())
    return int(digits) if digits else 1


async def _mark_ring(
    message: Message,
    command: CommandObject,
    db: Database,
    bot: Bot,
    mode: FightMode,
    number: int,
) -> None:
    if not await _is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("Ринг назначают администраторы группы.")
        return

    thread_id = thread_id_of(message)
    if thread_id is None and (number > 1 or mode.armed):
        await message.reply(
            "Второй ринг живёт в отдельной ветке форума. Включите темы в "
            "настройках группы, создайте ветку и повторите команду там."
        )
        return

    title = (command.args or "").strip()[:64]
    ring = await db.set_ring(message.chat.id, thread_id, number, mode, title)
    rings = await db.list_rings(message.chat.id)
    where = "Эта ветка" if thread_id is not None else "Этот чат"
    await message.reply(
        f"✅ {where} — {ring.label}. Вызов: {mode.command}\n\n"
        f"Ринги клуба:\n{_rings_list(rings)}\n\n"
        "В каждом ринге идёт свой бой: сколько рингов, столько и боёв разом."
    )


@router.message(Command("arena", *FIST_COMMANDS), F.chat.type.in_(GROUP_TYPES))
async def cmd_arena(
    message: Message, command: CommandObject, db: Database, bot: Bot
) -> None:
    """Кулачный ринг: /arena1, /arena2, /arena3."""
    await _mark_ring(
        message, command, db, bot, FightMode.FIST, _ring_number(command.command)
    )


@router.message(Command(*GEAR_COMMANDS), F.chat.type.in_(GROUP_TYPES))
async def cmd_arena_gear(
    message: Message, command: CommandObject, db: Database, bot: Bot
) -> None:
    """Ринг с оружием: здесь дерутся в полной экипировке."""
    await _mark_ring(message, command, db, bot, FightMode.ARMED, 1)


@router.message(Command("rings", "arenas"), F.chat.type.in_(GROUP_TYPES))
async def cmd_rings(message: Message, db: Database, duels: DuelService) -> None:
    """Где идут бои и какие ринги сейчас свободны."""
    rings = await db.list_rings(message.chat.id)
    if not rings:
        await message.reply(
            "Ринги ещё не размечены. Админ создаёт ветку и отправляет там "
            "/arena1 (кулачный) или /arena_gear (с оружием)."
        )
        return
    lines = ["🥊 <b>Ринги клуба</b>", ""]
    for ring in rings:
        busy = duels.duel_in_chat(ring.chat_id, ring.thread_id) is not None
        state = "🔴 идёт бой" if busy else "🟢 свободен"
        lines.append(f"• {ring.label} — {state}, вызов: {ring.mode.command}")
    await message.reply("\n".join(lines))


async def _open_duel(message: Message, db: Database, duels: DuelService, mode: FightMode) -> None:
    ring = await _ring_for(message, db, mode)
    if ring is None:
        return

    challenger = await db.get_player(message.from_user.id)
    if challenger is None:
        await message.reply(NO_CHARACTER)
        return

    target: Player | None = None
    reply = _challenged_message(message)
    if reply is not None:
        target = await db.get_player(reply.from_user.id)
        if target is None:
            await message.reply(
                f"У {esc(reply.from_user.first_name or 'этого человека')} "
                "ещё нет бойца — пусть напишет мне в личку /start."
            )
            return

    try:
        await duels.open_challenge(
            message.chat.id,
            thread_id_of(message),
            challenger,
            target,
            chat_title=message.chat.title or "",
            mode=mode,
        )
    except DuelError as error:
        await message.reply(str(error))


@router.message(Command("duel"), F.chat.type.in_(GROUP_TYPES))
async def cmd_duel(message: Message, db: Database, duels: DuelService) -> None:
    """Кулачный вызов: вещи остаются в раздевалке."""
    await _open_duel(message, db, duels, FightMode.FIST)


@router.message(Command("fight", "armed"), F.chat.type.in_(GROUP_TYPES))
async def cmd_fight(message: Message, db: Database, duels: DuelService) -> None:
    """Вызов с оружием: дерутся в том, что надето."""
    await _open_duel(message, db, duels, FightMode.ARMED)


# ---------- бои на много бойцов ----------


def _parse_levels(parts: list[str]) -> tuple[int, int] | None:
    """«5-8» или «5 8» в рамках уровней. Ничего не поняли — рамок нет."""
    numbers: list[int] = []
    for part in parts:
        for chunk in part.replace("—", "-").replace("–", "-").split("-"):
            if chunk.strip().isdigit():
                numbers.append(int(chunk))
    if len(numbers) < 2:
        return None
    return numbers[0], numbers[1]


async def _open_lobby(
    message: Message,
    command: CommandObject,
    db: Database,
    battles: BattleService,
    kind: BattleKind,
) -> None:
    ring = await _ring_for_battle(message, db)
    if ring is None:
        return

    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.reply(NO_CHARACTER)
        return

    parts = (command.args or "").split()
    default = MIN_TEAM_SIZE if kind is BattleKind.TEAM else MIN_ROYALE
    size = int(parts[0]) if parts and parts[0].isdigit() else default
    levels = _parse_levels(parts[1:] if parts and parts[0].isdigit() else parts)

    try:
        await battles.open_lobby(
            message.chat.id,
            thread_id_of(message),
            player,
            kind,
            size,
            mode=ring.mode,
            levels=levels,
            chat_title=message.chat.title or "",
        )
    except BattleError as error:
        await message.reply(str(error))


async def _ring_for_battle(message: Message, db: Database) -> Ring | None:
    """Групповые бои идут в любом ринге — режим берётся у ринга."""
    thread_id = thread_id_of(message)
    rings = await db.list_rings(message.chat.id)
    if not rings:
        return Ring(chat_id=message.chat.id, thread_id=thread_id, mode=FightMode.ARMED)
    ring = await db.get_ring(message.chat.id, thread_id)
    if ring is None:
        await message.reply("Здесь не дерутся. Ринги клуба:\n" + _rings_list(rings))
        return None
    return ring


@router.message(Command("battle", "team"), F.chat.type.in_(GROUP_TYPES))
async def cmd_battle(
    message: Message, command: CommandObject, db: Database, battles: BattleService
) -> None:
    """Командный бой: /battle 3 5-8 — трое на трое, уровни с 5 по 8."""
    await _open_lobby(message, command, db, battles, BattleKind.TEAM)


@router.message(Command("royale", "royal"), F.chat.type.in_(GROUP_TYPES))
async def cmd_royale(
    message: Message, command: CommandObject, db: Database, battles: BattleService
) -> None:
    """Королевская битва: /royale 6 — каждый сам за себя."""
    await _open_lobby(message, command, db, battles, BattleKind.ROYALE)


@router.callback_query(LobbyCB.filter())
async def on_lobby(
    callback: CallbackQuery, callback_data: LobbyCB, db: Database, battles: BattleService
) -> None:
    if callback_data.action == "leave":
        try:
            await battles.leave(callback_data.lobby_id, callback.from_user.id)
        except BattleError as error:
            await callback.answer(plain(str(error)), show_alert=True)
        else:
            await callback.answer("Вышел из состава.")
        return

    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer(NO_CHARACTER, show_alert=True)
        return
    try:
        await battles.join(callback_data.lobby_id, player, callback_data.team)
    except BattleError as error:
        await callback.answer(plain(str(error)), show_alert=True)
    else:
        await callback.answer("Записан!")


@router.callback_query(BattleCB.filter())
async def on_battle_choice(
    callback: CallbackQuery, callback_data: BattleCB, battles: BattleService
) -> None:
    try:
        hint = await battles.handle_choice(
            callback_data.battle_id,
            callback.from_user.id,
            callback_data.action,
            callback_data.zone,
            callback_data.slot,
        )
    except BattleError as error:
        await callback.answer(plain(str(error)), show_alert=True)
    except Exception:  # pragma: no cover - чтобы бой не завис из-за случайной ошибки
        logger.exception("Ошибка при обработке хода группового боя")
        await callback.answer("Судья запутался. Попробуй ещё раз.", show_alert=True)
    else:
        await callback.answer(hint)


# ---------- турнир ----------


@router.message(Command("tournament", "tour"), F.chat.type.in_(GROUP_TYPES))
async def cmd_tournament(
    message: Message,
    command: CommandObject,
    db: Database,
    bot: Bot,
    tournaments: TournamentService,
) -> None:
    """Объявить турнир: /tournament 16 5-8 Кубок подвала."""
    if not await _is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("Турнир объявляют администраторы клуба.")
        return
    ring = await _ring_for_battle(message, db)
    if ring is None:
        return

    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.reply(NO_CHARACTER)
        return

    parts = (command.args or "").split()
    size = int(parts[0]) if parts and parts[0].isdigit() else 8
    rest = parts[1:] if parts and parts[0].isdigit() else parts
    levels = _parse_levels(rest[:1])
    title = " ".join(rest[1:] if levels else rest)[:48]

    try:
        await tournaments.create(
            message.chat.id,
            thread_id_of(message),
            player,
            size,
            mode=ring.mode,
            levels=levels,
            title=title,
            chat_title=message.chat.title or "",
        )
    except TournamentError as error:
        await message.reply(str(error))


@router.message(Command("bracket", "setka"), F.chat.type.in_(GROUP_TYPES))
async def cmd_bracket(
    message: Message, db: Database, tournaments: TournamentService
) -> None:
    """Показать сетку идущего турнира."""
    live = await db.live_tournaments(message.chat.id)
    if not live:
        await message.reply("Сейчас турнира нет. Объявить: /tournament 8")
        return
    tournament_id = live[0]["id"]
    if live[0]["state"] == "registration":
        await message.reply("Запись ещё идёт — сетки пока нет.")
        return
    await message.reply(await tournaments.bracket(tournament_id))


@router.message(Command("tourstop"), F.chat.type.in_(GROUP_TYPES))
async def cmd_tourstop(
    message: Message, db: Database, bot: Bot, tournaments: TournamentService
) -> None:
    if not await _is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("Турнир останавливают администраторы клуба.")
        return
    live = await db.live_tournaments(message.chat.id)
    if not live:
        await message.reply("Останавливать нечего.")
        return
    await tournaments.stop(live[0]["id"])


@router.callback_query(TourCB.filter())
async def on_tournament(
    callback: CallbackQuery,
    callback_data: TourCB,
    db: Database,
    tournaments: TournamentService,
) -> None:
    if callback_data.action == "leave":
        try:
            await tournaments.leave(callback_data.tournament_id, callback.from_user.id)
        except TournamentError as error:
            await callback.answer(plain(str(error)), show_alert=True)
        else:
            await callback.answer("Вычеркнул из списка.")
        return

    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer(NO_CHARACTER, show_alert=True)
        return
    try:
        await tournaments.join(callback_data.tournament_id, player)
    except TournamentError as error:
        await callback.answer(plain(str(error)), show_alert=True)
    else:
        await callback.answer("В списке!")


def _challenged_message(message: Message) -> Message | None:
    """Чьё сообщение мы считаем вызовом конкретному бойцу.

    В ветках форума Telegram подставляет в reply_to_message служебное
    сообщение о создании темы, а его автор — тот, кто тему завёл. Без этой
    проверки бот принимал автора темы за соперника, и человек, отправивший
    /duel в собственной ветке, получал «с самим собой драться нельзя».
    Ответ на своё же сообщение тоже не вызов, а обычный открытый вызов.
    """
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.is_bot:
        return None
    if reply.forum_topic_created is not None:
        return None
    if message.message_thread_id and reply.message_id == message.message_thread_id:
        return None
    if reply.from_user.id == message.from_user.id:
        return None
    return reply


@router.callback_query(ChallengeCB.filter())
async def on_challenge(
    callback: CallbackQuery, callback_data: ChallengeCB, db: Database, duels: DuelService
) -> None:
    if callback_data.action == "cancel":
        try:
            await duels.cancel_challenge(callback_data.challenge_id, callback.from_user.id)
        except DuelError as error:
            await callback.answer(plain(str(error)), show_alert=True)
        else:
            await callback.answer("Вызов отозван.")
        return

    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer(NO_CHARACTER, show_alert=True)
        return
    try:
        await duels.accept_challenge(callback_data.challenge_id, player)
    except DuelError as error:
        await callback.answer(plain(str(error)), show_alert=True)
    else:
        await callback.answer("В бой!")


@router.callback_query(FightCB.filter())
async def on_fight(
    callback: CallbackQuery, callback_data: FightCB, duels: DuelService
) -> None:
    try:
        hint = await duels.handle_choice(
            callback_data.duel_id,
            callback.from_user.id,
            callback_data.action,
            callback_data.zone,
            callback_data.slot,
        )
    except DuelError as error:
        await callback.answer(plain(str(error)), show_alert=True)
    except Exception:  # pragma: no cover - чтобы бой не завис из-за случайной ошибки
        logger.exception("Ошибка при обработке хода")
        await callback.answer("Судья запутался. Попробуй ещё раз.", show_alert=True)
    else:
        await callback.answer(hint)


@router.callback_query(StandoffCB.filter())
async def on_standoff(
    callback: CallbackQuery, callback_data: StandoffCB, duels: DuelService
) -> None:
    """Последнее слово перед гонгом: выходим или расходимся."""
    try:
        if callback_data.action == "start":
            await duels.confirm_duel(callback_data.duel_id, callback.from_user.id)
            await callback.answer("Гонг!")
        else:
            await duels.decline_duel(callback_data.duel_id, callback.from_user.id)
            await callback.answer("Бой отменён.")
    except DuelError as error:
        await callback.answer(plain(str(error)), show_alert=True)


@router.message(Command("history"), F.chat.type.in_(GROUP_TYPES))
async def cmd_history(message: Message, db: Database) -> None:
    duels_rows = await db.recent_duels(message.chat.id, 5)
    if not duels_rows:
        await message.reply("Здесь ещё никто не дрался.")
        return
    lines = ["📜 <b>Последние бои</b>", ""]
    for row in duels_rows:
        challenger = await db.get_player(row["challenger_id"])
        opponent = await db.get_player(row["opponent_id"])
        first = (
            name_link(challenger.user_id, challenger.nickname) if challenger else "боец"
        )
        second = (
            name_link(opponent.user_id, opponent.nickname) if opponent else "боец"
        )
        if row["winner_id"] is None:
            outcome = "ничья"
        elif row["winner_id"] == row["challenger_id"]:
            outcome = f"победил {first}"
        else:
            outcome = f"победил {second}"
        lines.append(f"• {first} vs {second} — {outcome}, раундов: {row['rounds']}")
    await message.reply("\n".join(lines))
