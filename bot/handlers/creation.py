"""Создание бойца в личке: класс → имя → аватар → характеристики."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.database import Database
from bot.game.classes import (
    ALL_STATS,
    FIGHTER_CLASSES,
    START_POINTS,
    START_POINTS_PER_STAT_CAP,
    FighterClass,
    Stat,
    Stats,
    get_class,
)
from bot.game.links import card_target
from bot.game.narrator import esc
from bot.handlers.common import (
    card_keyboard,
    combat_block,
    profile_text,
    send_profile,
    stats_block,
)
from bot.keyboards import (
    AvatarCB,
    ClassCB,
    StatCB,
    avatars_keyboard,
    classes_keyboard,
    stats_keyboard,
)
from bot.models import Player

router = Router(name="creation")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

NICK_MIN, NICK_MAX = 2, 20


class NickCB(CallbackData, prefix="nick"):
    keep: int = 1


class ResetCB(CallbackData, prefix="reset"):
    confirm: int = 0


class Creation(StatesGroup):
    choosing_class = State()
    entering_nickname = State()
    choosing_avatar = State()
    waiting_photo = State()
    distributing = State()


class Upgrade(StatesGroup):
    distributing = State()


WELCOME = (
    "🥊 <b>Бойцовский клуб</b>\n\n"
    "Первое правило клуба: никто не дерётся без персонажа.\n"
    "Собери бойца — класс, имя, аватар, характеристики, — "
    "а потом добавь меня в группу и вызывай кого-нибудь на кулаки.\n\n"
    "<b>Выбирай класс:</b>\n"
)


def classes_overview() -> str:
    return "\n\n".join(
        f"{fclass.label} — <i>{fclass.tagline}</i>\n{fclass.description}"
        for fclass in FIGHTER_CLASSES.values()
    )


def spent_stats(spent: dict[str, int]) -> Stats:
    return Stats(**{stat.value: spent.get(stat.value, 0) for stat in ALL_STATS})


def distribution_text(
    fclass: FighterClass,
    base: Stats,
    spent: dict[str, int],
    left: int,
    level: int = 1,
    cap: int | None = None,
) -> str:
    total = base.merge(spent_stats(spent))
    lines = [
        f"🧬 <b>Характеристики</b> ({fclass.label})",
        f"Осталось очков: <b>{left}</b>",
        "",
    ]
    for stat in ALL_STATS:
        added = spent.get(stat.value, 0)
        suffix = f" <i>(+{added})</i>" if added else ""
        lines.append(
            f"{stat.emoji} {stat.title.capitalize()}: <b>{total.get(stat)}</b>{suffix}"
        )
    if cap:
        lines.append(f"\n<i>При создании в один стат можно вложить не больше {cap}.</i>")
    lines += ["", "<b>Что получается:</b>", combat_block(fclass, total, level)]
    if left:
        lines.append("\nЖми «+1», пока очки не кончатся.")
    return "\n".join(lines)


# ---------- старт ----------


@router.message(CommandStart(deep_link=True))
async def cmd_start_card(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db: Database,
    config: Config,
) -> None:
    """Диплинк на карточку: t.me/бот?start=card_12345.

    Так имя бойца из чата открывается даже там, где именованного мини-аппа
    нет: человек попадает в личку, но не в пустоту — бот сразу показывает
    того самого бойца и кнопку, открывающую его карточку.
    """
    target = card_target(command.args or "")
    if target is None:
        await cmd_start(message, state, db)
        return

    player = await db.get_player(target)
    if player is None:
        await message.answer("Этого бойца в картотеке нет.")
        await cmd_start(message, state, db)
        return

    own = target == message.from_user.id
    keyboard = card_keyboard(config, target, private=True)
    await message.answer(profile_text(player, own=own), reply_markup=keyboard)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    player = await db.get_player(message.from_user.id)
    if player:
        await message.answer(
            f"С возвращением на ринг, <b>{esc(player.nickname)}</b>.\n"
            "Твой боец уже готов. /help — что можно делать, "
            "/reset — начать жизнь заново."
        )
        await send_profile(message, player)
        return
    await state.set_state(Creation.choosing_class)
    await message.answer(WELCOME + "\n" + classes_overview(), reply_markup=classes_keyboard())


@router.callback_query(Creation.choosing_class, ClassCB.filter())
async def pick_class(
    callback: CallbackQuery, callback_data: ClassCB, state: FSMContext
) -> None:
    fclass = get_class(callback_data.code)
    await state.update_data(class_code=fclass.code)
    await state.set_state(Creation.entering_nickname)
    default_name = callback.from_user.first_name or "Боец"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оставить «{default_name}»",
                    callback_data=NickCB(keep=1).pack(),
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"Класс выбран: {fclass.label} — <i>{fclass.tagline}</i>\n\n"
        "Как объявлять тебя на ринге? Пришли прозвище "
        f"({NICK_MIN}–{NICK_MAX} символов).",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(Creation.entering_nickname, NickCB.filter())
async def keep_nickname(callback: CallbackQuery, state: FSMContext) -> None:
    nickname = (callback.from_user.first_name or "Боец").strip()[:NICK_MAX]
    await state.update_data(nickname=nickname)
    await state.set_state(Creation.choosing_avatar)
    await callback.message.edit_text(
        f"Принято, <b>{esc(nickname)}</b>.\n\nТеперь выбери аватар:",
        reply_markup=avatars_keyboard(),
    )
    await callback.answer()


@router.message(Creation.entering_nickname, F.text)
async def set_nickname(message: Message, state: FSMContext) -> None:
    nickname = " ".join(message.text.split())
    if not NICK_MIN <= len(nickname) <= NICK_MAX:
        await message.answer(
            f"Прозвище должно быть от {NICK_MIN} до {NICK_MAX} символов. Ещё раз."
        )
        return
    await state.update_data(nickname=nickname)
    await state.set_state(Creation.choosing_avatar)
    await message.answer(
        f"Принято, <b>{esc(nickname)}</b>.\n\nТеперь выбери аватар:",
        reply_markup=avatars_keyboard(),
    )


@router.callback_query(Creation.choosing_avatar, AvatarCB.filter())
async def pick_avatar(
    callback: CallbackQuery, callback_data: AvatarCB, state: FSMContext
) -> None:
    if callback_data.value == "custom":
        await state.set_state(Creation.waiting_photo)
        await callback.message.edit_text(
            "Пришли фото одним сообщением — оно станет аватаром бойца.\n"
            "Передумал? /skip — вернёмся к эмодзи."
        )
        await callback.answer()
        return

    await state.update_data(avatar=callback_data.value, avatar_file_id=None)
    await _ask_stats(callback.message, state, edit=True)
    await callback.answer()


@router.message(Creation.waiting_photo, F.photo)
async def set_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(avatar="📷", avatar_file_id=file_id)
    await message.answer("Фото на месте. 📷")
    await _ask_stats(message, state, edit=False)


@router.message(Creation.waiting_photo, Command("skip"))
async def skip_photo(message: Message, state: FSMContext) -> None:
    await state.set_state(Creation.choosing_avatar)
    await message.answer("Ладно, выбирай из готовых:", reply_markup=avatars_keyboard())


async def _ask_stats(message: Message, state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    fclass = get_class(data["class_code"])
    await state.update_data(spent={}, left=START_POINTS)
    await state.set_state(Creation.distributing)
    text = distribution_text(
        fclass, fclass.base_stats, {}, START_POINTS, cap=START_POINTS_PER_STAT_CAP
    )
    markup = stats_keyboard(START_POINTS)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


# ---------- распределение при создании ----------


@router.callback_query(Creation.distributing, StatCB.filter())
async def distribute(
    callback: CallbackQuery, callback_data: StatCB, state: FSMContext, db: Database
) -> None:
    data = await state.get_data()
    fclass = get_class(data["class_code"])
    spent: dict[str, int] = dict(data.get("spent", {}))
    left: int = data.get("left", START_POINTS)

    if callback_data.action == "add":
        stat = Stat(callback_data.stat)
        if left <= 0:
            await callback.answer("Очки кончились.", show_alert=False)
            return
        if spent.get(stat.value, 0) >= START_POINTS_PER_STAT_CAP:
            await callback.answer(
                f"Больше {START_POINTS_PER_STAT_CAP} очков в один стат "
                "на старте вложить нельзя.",
                show_alert=True,
            )
            return
        spent[stat.value] = spent.get(stat.value, 0) + 1
        left -= 1
    elif callback_data.action == "reset":
        spent, left = {}, START_POINTS
    elif callback_data.action == "done":
        if left > 0:
            await callback.answer("Сначала раздай все очки.", show_alert=True)
            return
        await _create_player(callback, state, db, spent)
        return

    await state.update_data(spent=spent, left=left)
    await callback.message.edit_text(
        distribution_text(
            fclass, fclass.base_stats, spent, left, cap=START_POINTS_PER_STAT_CAP
        ),
        reply_markup=stats_keyboard(left),
    )
    await callback.answer()


async def _create_player(
    callback: CallbackQuery, state: FSMContext, db: Database, spent: dict[str, int]
) -> None:
    data = await state.get_data()
    fclass = get_class(data["class_code"])
    total = fclass.base_stats.merge(spent_stats(spent))
    player = Player(
        user_id=callback.from_user.id,
        nickname=data.get("nickname") or (callback.from_user.first_name or "Боец"),
        class_code=fclass.code,
        avatar=data.get("avatar", "🥊"),
        avatar_file_id=data.get("avatar_file_id"),
        strength=total.strength,
        agility=total.agility,
        intuition=total.intuition,
        endurance=total.endurance,
    )
    await db.save_player(player)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Боец готов.\n\n{stats_block(total)}\n\n"
        "Теперь добавь меня в группу, создай там ветку для боёв "
        "и отметь её командой /arena. Дальше — /duel и погнали.\n\n"
        "Полный список команд: /help"
    )
    await send_profile(callback.message, player)
    await callback.answer("Добро пожаловать в клуб!")


# ---------- прокачка после уровня ----------


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message, state: FSMContext, db: Database) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Сначала создай бойца: /start")
        return
    if player.free_points <= 0:
        await message.answer(
            "Свободных очков нет. Побеждай в дуэлях — за уровень дают новые."
        )
        return
    await state.set_state(Upgrade.distributing)
    await state.update_data(spent={}, left=player.free_points)
    await message.answer(
        distribution_text(
            player.fclass, player.stats, {}, player.free_points, level=player.level
        ),
        reply_markup=stats_keyboard(player.free_points),
    )


@router.callback_query(Upgrade.distributing, StatCB.filter())
async def upgrade_distribute(
    callback: CallbackQuery, callback_data: StatCB, state: FSMContext, db: Database
) -> None:
    player = await db.get_player(callback.from_user.id)
    if player is None:  # pragma: no cover - персонажа удалили в процессе
        await state.clear()
        await callback.answer("Персонаж не найден.", show_alert=True)
        return

    data = await state.get_data()
    spent: dict[str, int] = dict(data.get("spent", {}))
    left: int = data.get("left", player.free_points)

    if callback_data.action == "add":
        if left <= 0:
            await callback.answer("Очки кончились.")
            return
        spent[callback_data.stat] = spent.get(callback_data.stat, 0) + 1
        left -= 1
    elif callback_data.action == "reset":
        spent, left = {}, player.free_points
    elif callback_data.action == "done":
        gain = spent_stats(spent)
        total = player.stats.merge(gain)
        player.strength = total.strength
        player.agility = total.agility
        player.intuition = total.intuition
        player.endurance = total.endurance
        player.free_points = left
        await db.save_player(player)
        await state.clear()
        await callback.message.edit_text("✅ Характеристики обновлены.")
        await send_profile(callback.message, player)
        await callback.answer()
        return

    await state.update_data(spent=spent, left=left)
    await callback.message.edit_text(
        distribution_text(player.fclass, player.stats, spent, left, level=player.level),
        reply_markup=stats_keyboard(left),
    )
    await callback.answer()


# ---------- пересоздание ----------


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext, db: Database) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Нечего сбрасывать. /start — и вперёд.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 Да, начать заново",
                    callback_data=ResetCB(confirm=1).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=ResetCB(confirm=0).pack()
                ),
            ]
        ]
    )
    await message.answer(
        f"Точно стереть бойца <b>{esc(player.nickname)}</b> "
        f"({player.fclass.title}, {player.level} ур., побед: {player.wins})?\n"
        "Уровень, опыт и статистика пропадут навсегда.",
        reply_markup=keyboard,
    )


@router.callback_query(ResetCB.filter())
async def on_reset(
    callback: CallbackQuery, callback_data: ResetCB, state: FSMContext, db: Database
) -> None:
    if not callback_data.confirm:
        await callback.message.edit_text("Ладно, боец остаётся в строю.")
        await callback.answer()
        return
    await db.delete_player(callback.from_user.id)
    await state.clear()
    await state.set_state(Creation.choosing_class)
    await callback.message.edit_text(
        "Старый боец вычеркнут из списков клуба. Начнём заново.\n\n"
        + classes_overview(),
        reply_markup=classes_keyboard(),
    )
    await callback.answer()


@router.message(StateFilter(Creation.entering_nickname))
async def nickname_fallback(message: Message) -> None:
    await message.answer("Пришли прозвище текстом.")


@router.message(StateFilter(Creation.waiting_photo))
async def photo_fallback(message: Message) -> None:
    await message.answer(
        "Нужно именно фото (не файлом). Или /skip — вернёмся к эмодзи."
    )
