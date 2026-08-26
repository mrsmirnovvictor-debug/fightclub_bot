"""Групповая часть: ринг, вызовы и нажатия во время боя."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from bot.database import Database
from bot.duel_service import DuelError, DuelService
from bot.game.narrator import esc
from bot.handlers.common import thread_id_of
from bot.keyboards import ChallengeCB, FightCB
from bot.models import Player

logger = logging.getLogger(__name__)

router = Router(name="group")
GROUP_TYPES = {"group", "supergroup"}

NO_CHARACTER = (
    "У тебя нет бойца. Напиши мне в личку /start — соберём персонажа за минуту."
)


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in {"creator", "administrator"}


async def _arena_guard(message: Message, db: Database) -> bool:
    """Проверить, что команда пришла в ту ветку, где клуб проводит бои."""
    arena = await db.get_arena(message.chat.id)
    if arena is None:
        return True
    if arena.thread_id == thread_id_of(message):
        return True
    where = f"«{esc(arena.title)}»" if arena.title else "отдельной ветке клуба"
    await message.reply(f"Бои проходят в {where}. Здесь — только разговоры.")
    return False


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def added_to_group(event: ChatMemberUpdated) -> None:
    if event.new_chat_member.status not in {"member", "administrator"}:
        return
    await event.bot.send_message(
        event.chat.id,
        "🥊 <b>Бойцовский клуб открыт.</b>\n\n"
        "Создайте отдельную ветку для боёв и отправьте там /arena — "
        "буду судить только в ней.\n"
        "Бойцы регистрируются у меня в личке командой /start, "
        "дерутся здесь командой /duel.\n\n"
        "Подробности — /help",
    )


@router.message(Command("arena"), F.chat.type.in_(GROUP_TYPES))
async def cmd_arena(
    message: Message, command: CommandObject, db: Database, bot: Bot
) -> None:
    if not await _is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("Ринг назначают администраторы группы.")
        return

    thread_id = thread_id_of(message)
    title = (command.args or "").strip()[:64]
    await db.set_arena(message.chat.id, thread_id, title)
    if thread_id is None:
        await message.reply(
            "✅ Ринг — этот чат целиком. Если создадите отдельную ветку форума "
            "и повторите /arena там, бои переедут в неё."
        )
    else:
        await message.reply(
            "✅ Эта ветка отмечена как ринг клуба. Все бои — только здесь.\n"
            "Бросай вызов: /duel"
        )


@router.message(Command("duel", "fight"), F.chat.type.in_(GROUP_TYPES))
async def cmd_duel(message: Message, db: Database, duels: DuelService) -> None:
    if not await _arena_guard(message, db):
        return

    challenger = await db.get_player(message.from_user.id)
    if challenger is None:
        await message.reply(NO_CHARACTER)
        return

    target: Player | None = None
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        target = await db.get_player(reply.from_user.id)
        if target is None:
            await message.reply(
                f"У {esc(reply.from_user.first_name or 'этого человека')} "
                "ещё нет бойца — пусть напишет мне в личку /start."
            )
            return

    try:
        await duels.open_challenge(
            message.chat.id, thread_id_of(message), challenger, target
        )
    except DuelError as error:
        await message.reply(str(error))


@router.callback_query(ChallengeCB.filter())
async def on_challenge(
    callback: CallbackQuery, callback_data: ChallengeCB, db: Database, duels: DuelService
) -> None:
    if callback_data.action == "cancel":
        try:
            await duels.cancel_challenge(callback_data.challenge_id, callback.from_user.id)
        except DuelError as error:
            await callback.answer(str(error), show_alert=True)
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
        await callback.answer(str(error), show_alert=True)
    else:
        await callback.answer("В бой!")


@router.callback_query(FightCB.filter())
async def on_fight(
    callback: CallbackQuery, callback_data: FightCB, duels: DuelService
) -> None:
    try:
        if callback_data.action == "giveup":
            await duels.give_up(callback_data.duel_id, callback.from_user.id)
            await callback.answer("Ты выбросил полотенце.")
            return
        hint = await duels.handle_choice(
            callback_data.duel_id,
            callback.from_user.id,
            callback_data.action,
            callback_data.zone,
        )
    except DuelError as error:
        await callback.answer(str(error), show_alert=True)
    except Exception:  # pragma: no cover - чтобы бой не завис из-за случайной ошибки
        logger.exception("Ошибка при обработке хода")
        await callback.answer("Судья запутался. Попробуй ещё раз.", show_alert=True)
    else:
        await callback.answer(hint)


@router.message(Command("giveup"), F.chat.type.in_(GROUP_TYPES))
async def cmd_giveup(message: Message, duels: DuelService) -> None:
    session = duels.duel_of_user(message.from_user.id)
    if session is None:
        await message.reply("Ты сейчас не дерёшься.")
        return
    try:
        await duels.give_up(session.id, message.from_user.id)
    except DuelError as error:
        await message.reply(str(error))


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
        first = esc(challenger.nickname) if challenger else "боец"
        second = esc(opponent.nickname) if opponent else "боец"
        if row["winner_id"] is None:
            outcome = "ничья"
        elif row["winner_id"] == row["challenger_id"]:
            outcome = f"победил {first}"
        else:
            outcome = f"победил {second}"
        lines.append(f"• {first} vs {second} — {outcome}, раундов: {row['rounds']}")
    await message.reply("\n".join(lines))
