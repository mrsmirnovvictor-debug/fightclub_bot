"""Траты кредитов: респек, смена класса, прозвища и аватара."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot.config import Config
from bot.database import Database
from bot.game.classes import get_class
from bot.game.economy import PRICE_APPEARANCE, PRICE_CLASS_CHANGE, PRICE_RESPEC
from bot.game.equipment import (
    MAX_WEAR,
    REPAIR_PRICE_PER_POINT,
    describe_requirements,
    get_item,
    items_unlocked_at,
    shop_sections,
)
from bot.game.narrator import esc
from bot.game.potions import POTIONS, get_potion, spell_duration
from bot.handlers.common import send_profile
from bot.inventory_service import InventoryError, buy, settle_gear
from bot.potions_service import (
    PotionError,
    buy_potion,
    use_potion,
    would_replace,
)
from bot.keyboards import (
    AvatarCB,
    BuyCB,
    ClassCB,
    DrinkCB,
    avatars_keyboard,
    classes_keyboard,
    potions_keyboard,
    showcase_keyboard,
)
from bot.models import Player

router = Router(name="shop")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

NICK_MIN, NICK_MAX = 2, 20


class RespecCB(CallbackData, prefix="respec"):
    confirm: int = 0


class Shop(StatesGroup):
    changing_avatar = State()
    waiting_photo = State()
    changing_class = State()


def undressed(dropped: list) -> str:
    """Строка про то, что слетело вместе с характеристиками. Пусто — ничего."""
    if not dropped:
        return ""
    names = ", ".join(esc(owned.title) for owned in dropped)
    return f"\n👕 Ушло в рюкзак, требования больше не выполнены: {names}.\n"


def price_list(player: Player) -> str:
    return (
        "🏪 <b>Лавка клуба</b>\n\n"
        f"На счету: <b>{player.credits}</b> 💰\n\n"
        f"<b>{PRICE_RESPEC}</b> 💰 — /respec, снести характеристики "
        "и раздать очки заново\n"
        f"<b>{PRICE_CLASS_CHANGE}</b> 💰 — /class, сменить класс бойца\n"
        f"<b>{PRICE_APPEARANCE}</b> 💰 — /rename &lt;прозвище&gt;, новое имя на ринге\n"
        f"<b>{PRICE_APPEARANCE}</b> 💰 — /avatar, новая аватарка\n"
        "🛒 /buy — витрина: оружие, броня и всё, что надевается\n"
        "🧪 /potions — эликсиры: восстановить здоровье и разогнать характеристики\n\n"
        "Купленное лежит в инвентаре — открой карточку (/card) и надень.\n"
        f"Вещи снашиваются в боях (запас — {MAX_WEAR} пунктов износа) и чинятся "
        f"там же: {REPAIR_PRICE_PER_POINT} 💰 за пункт.\n\n"
        "Кредиты капают за апы и уровни — сам бой денег не приносит. "
        "Не хочется копить: /topup."
    )


async def _require_player(message: Message, db: Database) -> Player | None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Сначала создай бойца: /start")
    return player


def _not_enough(player: Player, price: int) -> str:
    return (
        f"Не хватает кредитов: нужно <b>{price}</b> 💰, "
        f"а на счету <b>{player.credits}</b> 💰.\n"
        "Кредиты приходят за апы и новые уровни, а докупить их можно "
        "в кассе: /topup."
    )


@router.message(Command("shop", "credits"))
async def cmd_shop(message: Message, db: Database) -> None:
    player = await _require_player(message, db)
    if player:
        await message.answer(price_list(player))


# ---------- витрина ----------


def showcase_text(player: Player) -> str:
    """Что открыто бойцу прямо сейчас — по типам вещей."""
    lines = [
        "🏪 <b>Лавка клуба</b>",
        "",
        f"На счету: <b>{player.credits}</b> 💰 · уровень {player.level}",
        "",
    ]
    for slot, items in shop_sections():
        open_now = [item for item in items if item.level_required <= player.level]
        if not open_now:
            continue
        lines.append(f"{slot.emoji} <b>{slot.section.capitalize()}</b>")
        for item in open_now:
            bonus = item.describe_bonus()
            lines.append(
                f"   {item.emoji} {esc(item.title)} — {item.price} 💰"
                + (f" · {bonus}" if bonus else "")
                + f" · нужно {describe_requirements(item)}"
            )

    locked = [
        item.level_required
        for _, items in shop_sections()
        for item in items
        if item.level_required > player.level
    ]
    if locked:
        level = min(locked)
        names = ", ".join(esc(item.title) for item in items_unlocked_at(level))
        lines += ["", f"🔒 На {level} уровне откроются: {names}."]
    lines += [
        "",
        "Купленное падает в инвентарь — надеть можно в карточке (/card), "
        "хоть сразу, хоть когда дорастёшь до требований.",
        "🧪 Эликсиры лежат отдельной полкой — «Прочее»: /potions.",
    ]
    return "\n".join(lines)


def shop_keyboard(config: Config, player: Player) -> InlineKeyboardMarkup:
    """Кнопка в лавку мини-аппа, а под ней — свежая партия товара кнопками."""
    builder = showcase_keyboard(player.level, player.credits)
    if not config.webapp_enabled:
        return builder
    rows = [
        [
            InlineKeyboardButton(
                text="🏪 Открыть лавку",
                web_app=WebAppInfo(url=f"{config.webapp_url}/?view=shop"),
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows + builder.inline_keyboard)


@router.message(Command("buy", "items"))
async def cmd_buy(message: Message, db: Database, config: Config) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    await message.answer(
        showcase_text(player), reply_markup=shop_keyboard(config, player)
    )


@router.callback_query(BuyCB.filter())
async def on_buy(callback: CallbackQuery, callback_data: BuyCB, db: Database) -> None:
    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer("Сначала создай бойца: /start", show_alert=True)
        return

    potion = get_potion(callback_data.code)
    if potion is not None:
        try:
            await buy_potion(db, player, potion.code)
        except PotionError as error:
            await callback.answer(str(error), show_alert=True)
            return
        await callback.message.answer(
            f"🛍 Куплено: {potion.emoji} <b>{esc(potion.title)}</b> "
            f"за {potion.price} 💰.\n"
            f"Осталось: <b>{player.credits}</b> 💰. Выпить — /potions",
            reply_markup=potions_keyboard(
                player.level, player.credits, player.potions
            ),
        )
        await callback.answer("В рюкзак!")
        return

    item = get_item(callback_data.code)
    try:
        await buy(db, player, callback_data.code)
    except InventoryError as error:
        await callback.answer(str(error), show_alert=True)
        return

    ready = (
        "Надевай в карточке: /card"
        if player.can_equip(item)
        else f"Пока не наденешь: нужно {describe_requirements(item)}"
    )
    await callback.message.answer(
        f"🛍 Куплено: {item.emoji} <b>{esc(item.title)}</b> за {item.price} 💰.\n"
        f"Осталось: <b>{player.credits}</b> 💰. {ready}"
    )
    await callback.answer("В рюкзак!")


# ---------- эликсиры ----------


def potions_text(player: Player) -> str:
    """Что действует, что в рюкзаке и что можно докупить."""
    lines = [
        "🧪 <b>Эликсиры</b>",
        "",
        f"На счету: <b>{player.credits}</b> 💰 · уровень {player.level}",
    ]

    working = player.active_effects()
    if working:
        lines += ["", "<b>Сейчас действует</b>"]
        for effect in working:
            potion = effect.potion
            lines.append(
                f"   {potion.emoji} {esc(potion.title)} — ещё "
                f"{spell_duration(effect.seconds_left())}"
            )

    bag = player.potions_in_bag()
    if bag:
        lines += ["", "<b>В рюкзаке</b>"]
        for potion, count in bag:
            lines.append(f"   {potion.emoji} {esc(potion.title)} — {count} шт.")

    lines += ["", "<b>На прилавке</b>"]
    for potion in POTIONS:
        if potion.level_required > player.level:
            continue
        lines.append(
            f"   {potion.emoji} {esc(potion.title)} — {potion.price} 💰 · "
            f"{potion.describe()}"
        )
    locked = [p.level_required for p in POTIONS if p.level_required > player.level]
    if locked:
        lines += ["", f"🔒 Следующие склянки откроются на {min(locked)} уровне."]
    lines += [
        "",
        "Восстановление доливает здоровье сразу, но не выше потолка. Их "
        "можно пить сколько угодно и подряд.",
        "",
        "Временный эликсир на бойце один. Тот же самый продлевает срок, а не "
        "удваивает прибавку; другой — гасит нынешний и встаёт на его место. "
        "Перед такой заменой бот переспросит.",
    ]
    return "\n".join(lines)


def swap_keyboard(potion) -> InlineKeyboardMarkup:
    """Кнопка «всё равно выпить» — второй шаг после предупреждения."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🥤 Всё равно выпить {potion.title}",
                    callback_data=DrinkCB(code=potion.code, confirm=1).pack(),
                )
            ]
        ]
    )


@router.message(Command("potions", "elixirs"))
async def cmd_potions(message: Message, db: Database) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    await message.answer(
        potions_text(player),
        reply_markup=potions_keyboard(player.level, player.credits, player.potions),
    )


@router.callback_query(DrinkCB.filter())
async def on_drink(
    callback: CallbackQuery, callback_data: DrinkCB, db: Database
) -> None:
    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer("Сначала создай бойца: /start", show_alert=True)
        return

    # Другой временный эликсир погасит нынешний: спрашиваем до глотка,
    # потому что склянка тратится в любом случае.
    doomed = would_replace(player, callback_data.code)
    if doomed is not None and not callback_data.confirm:
        potion = get_potion(callback_data.code)
        await callback.message.answer(
            f"⚠️ Сейчас действует {doomed.emoji} <b>{esc(doomed.title)}</b>.\n"
            f"Если выпьешь {potion.emoji} <b>{esc(potion.title)}</b>, "
            "действие предыдущего эликсира закончится.",
            reply_markup=swap_keyboard(potion),
        )
        await callback.answer()
        return

    try:
        result = await use_potion(db, player, callback_data.code)
    except PotionError as error:
        await callback.answer(str(error), show_alert=True)
        return

    potion = result.potion
    if result.healed:
        news = (
            f"❤️ Здоровья прибавилось на <b>{result.healed}</b> — "
            f"стало {player.current_hp()}/{player.max_hp}."
        )
    else:
        verb = "продлён" if result.extended else "пошёл"
        news = (
            f"⏳ Эффект {verb}: {potion.describe()}. "
            f"Держится ещё {spell_duration(result.seconds_left())}."
        )
    left = (
        f"Осталось таких: {result.left} шт."
        if result.left
        else "Это была последняя."
    )
    gone = (
        "\n🚫 Закончилось действие: "
        + ", ".join(esc(item.title) for item in result.replaced)
        + "."
        if result.replaced
        else ""
    )
    await callback.message.answer(
        f"🥤 Выпит {potion.emoji} <b>{esc(potion.title)}</b>.\n{news}{gone}\n{left}",
        reply_markup=potions_keyboard(player.level, player.credits, player.potions),
    )
    await callback.answer("До дна!")


# ---------- респек ----------


@router.message(Command("respec"))
async def cmd_respec(message: Message, db: Database) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    if not player.can_afford(PRICE_RESPEC):
        await message.answer(_not_enough(player, PRICE_RESPEC))
        return
    if player.spent_points <= 0:
        await message.answer("Пока нечего пересобирать: вложенных очков нет.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"♻️ Да, {PRICE_RESPEC} 💰",
                    callback_data=RespecCB(confirm=1).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=RespecCB(confirm=0).pack()
                ),
            ]
        ]
    )
    await message.answer(
        f"Снести характеристики и раздать заново?\n"
        f"Вернётся очков: <b>{player.spent_points}</b>. "
        f"Стоимость: <b>{PRICE_RESPEC}</b> 💰 "
        f"(останется {player.credits - PRICE_RESPEC}).",
        reply_markup=keyboard,
    )


@router.callback_query(RespecCB.filter())
async def on_respec(
    callback: CallbackQuery, callback_data: RespecCB, db: Database
) -> None:
    if not callback_data.confirm:
        await callback.message.edit_text("Ладно, оставляем как есть.")
        await callback.answer()
        return

    player = await db.get_player(callback.from_user.id)
    if player is None or not player.can_afford(PRICE_RESPEC):
        await callback.answer("Кредитов больше не хватает.", show_alert=True)
        return

    player.pay(PRICE_RESPEC)
    returned = player.reset_stats()
    await db.save_player(player)
    # Характеристики просели до базы: часть надетого могла стать не по плечу
    dropped = await settle_gear(db, player)
    await callback.message.edit_text(
        f"♻️ Характеристики сброшены до базы {player.fclass.label}.\n"
        f"Свободных очков: <b>{player.free_points}</b> (вернулось {returned}).\n"
        f"Осталось кредитов: <b>{player.credits}</b> 💰\n"
        f"{undressed(dropped)}\n"
        "Раскидывай заново: /upgrade"
    )
    await callback.answer()


# ---------- прозвище ----------


@router.message(Command("rename"))
async def cmd_rename(message: Message, command: CommandObject, db: Database) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    nickname = " ".join((command.args or "").split())
    if not nickname:
        await message.answer(
            f"Как тебя теперь объявлять? Напиши так: <code>/rename Тайлер</code>\n"
            f"Стоимость: {PRICE_APPEARANCE} 💰"
        )
        return
    if not NICK_MIN <= len(nickname) <= NICK_MAX:
        await message.answer(
            f"Прозвище должно быть от {NICK_MIN} до {NICK_MAX} символов."
        )
        return
    if not player.can_afford(PRICE_APPEARANCE):
        await message.answer(_not_enough(player, PRICE_APPEARANCE))
        return

    old = player.nickname
    player.pay(PRICE_APPEARANCE)
    player.nickname = nickname
    await db.save_player(player)
    await message.answer(
        f"📛 <b>{esc(old)}</b> теперь <b>{esc(nickname)}</b>. "
        f"Списано {PRICE_APPEARANCE} 💰, осталось {player.credits} 💰."
    )


# ---------- аватар ----------


@router.message(Command("avatar"))
async def cmd_avatar(message: Message, state: FSMContext, db: Database) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    if not player.can_afford(PRICE_APPEARANCE):
        await message.answer(_not_enough(player, PRICE_APPEARANCE))
        return
    await state.set_state(Shop.changing_avatar)
    await message.answer(
        f"Выбирай новый аватар. Смена стоит <b>{PRICE_APPEARANCE}</b> 💰 "
        f"(на счету {player.credits} 💰).",
        reply_markup=avatars_keyboard(),
    )


@router.callback_query(Shop.changing_avatar, AvatarCB.filter())
async def on_avatar(
    callback: CallbackQuery, callback_data: AvatarCB, state: FSMContext, db: Database
) -> None:
    if callback_data.value == "custom":
        await state.set_state(Shop.waiting_photo)
        await callback.message.edit_text(
            "Пришли фото одним сообщением. /cancel — передумать."
        )
        await callback.answer()
        return

    player = await _charge_appearance(callback, db, state)
    if player is None:
        return
    player.avatar = callback_data.value
    player.avatar_file_id = None
    # Значок и образ — одна рамка на карточке: выбрал значок, образ снимаем
    player.look = ""
    await db.save_player(player)
    await callback.message.edit_text(
        f"🖼 Новый аватар: {player.avatar}. "
        f"Списано {PRICE_APPEARANCE} 💰, осталось {player.credits} 💰."
    )
    await callback.answer()


@router.message(Shop.waiting_photo, F.photo)
async def on_avatar_photo(message: Message, state: FSMContext, db: Database) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None or not player.can_afford(PRICE_APPEARANCE):
        await state.clear()
        await message.answer("Кредитов не хватает.")
        return
    player.pay(PRICE_APPEARANCE)
    player.avatar = "📷"
    player.avatar_file_id = message.photo[-1].file_id
    player.look = ""
    await db.save_player(player)
    await state.clear()
    await message.answer(
        f"🖼 Аватар обновлён. Списано {PRICE_APPEARANCE} 💰, "
        f"осталось {player.credits} 💰."
    )
    await send_profile(message, player)


@router.message(Shop.waiting_photo, Command("cancel"))
async def cancel_photo(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Аватар остался прежним, кредиты целы.")


async def _charge_appearance(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> Player | None:
    player = await db.get_player(callback.from_user.id)
    if player is None or not player.can_afford(PRICE_APPEARANCE):
        await state.clear()
        await callback.answer("Кредитов не хватает.", show_alert=True)
        return None
    player.pay(PRICE_APPEARANCE)
    await state.clear()
    return player


# ---------- смена класса ----------


@router.message(Command("class"))
async def cmd_class(message: Message, state: FSMContext, db: Database) -> None:
    player = await _require_player(message, db)
    if player is None:
        return
    if not player.can_afford(PRICE_CLASS_CHANGE):
        await message.answer(_not_enough(player, PRICE_CLASS_CHANGE))
        return
    await state.set_state(Shop.changing_class)
    await message.answer(
        f"Сейчас ты {player.fclass.label}. Смена класса стоит "
        f"<b>{PRICE_CLASS_CHANGE}</b> 💰 (на счету {player.credits} 💰).\n\n"
        f"Все вложенные очки (<b>{player.spent_points}</b>) вернутся свободными — "
        "раскидаешь их заново под новый класс. Уровень, опыт и рейтинг останутся.\n\n"
        "Кем станешь?",
        reply_markup=classes_keyboard(),
    )


@router.callback_query(Shop.changing_class, ClassCB.filter())
async def on_class_change(
    callback: CallbackQuery, callback_data: ClassCB, state: FSMContext, db: Database
) -> None:
    player = await db.get_player(callback.from_user.id)
    if player is None or not player.can_afford(PRICE_CLASS_CHANGE):
        await state.clear()
        await callback.answer("Кредитов не хватает.", show_alert=True)
        return
    new_class = get_class(callback_data.code)
    if new_class.code == player.class_code:
        await callback.answer("Ты и так этого класса.", show_alert=True)
        return

    old = player.fclass
    player.pay(PRICE_CLASS_CHANGE)
    returned = player.switch_class(new_class.code)
    await db.save_player(player)
    dropped = await settle_gear(db, player)
    await state.clear()
    await callback.message.edit_text(
        f"🔄 {old.label} → {new_class.label}\n"
        f"Свободных очков: <b>{player.free_points}</b> (вернулось {returned}).\n"
        f"Списано {PRICE_CLASS_CHANGE} 💰, осталось {player.credits} 💰.\n"
        f"{undressed(dropped)}\n"
        "Собирай билд заново: /upgrade"
    )
    await callback.answer("Новый класс!")
