"""Пополнение счёта: пачки кредитов за Telegram Stars.

Оплата идёт только звёздами — так требуют правила Telegram для цифровых
товаров в ботах и мини-аппах. Ни карт, ни внешних касс здесь нет.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
)

from bot.database import Database
from bot.game.equipment import describe_requirements, get_item
from bot.game import pro
from bot.game.potions import spell_duration
from bot.game.pro import ProOffer, current_offer
from bot.game.store import PACKS, get_pack, pro_payload
from bot.pro_service import ProError, claim_free_pro
from bot.keyboards import ProCB, TopUpCB
from bot.store_service import StoreError, StoreService, spent_stars

logger = logging.getLogger(__name__)

router = Router(name="store")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

NO_CHARACTER = "Сначала заведи бойца: /start"


def topup_text(credits: int) -> str:
    lines = [
        "💳 <b>Касса клуба</b>",
        "",
        f"На счету: <b>{credits}</b> 💰",
        "",
        "Кредиты тратятся в лавке (/buy) на оружие и броню, на починку вещей "
        "и на смену класса с прозвищем. Уровень и характеристики за деньги "
        "не продаются — их берут в боях.",
        "",
        "<b>Пачки</b>",
    ]
    for pack in PACKS:
        lines.append(f"{pack.describe()}")
        if pack.note:
            lines.append(f"<i>{pack.note}</i>")
    lines += [
        "",
        "Оплата — звёздами Telegram. Не понравилось — /refund вернёт звёзды, "
        "пока кредиты целы.",
    ]
    return "\n".join(lines)


def topup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{pack.label} · {pack.total} кр. · {pack.stars} ⭐",
                    callback_data=TopUpCB(code=pack.code).pack(),
                )
            ]
            for pack in PACKS
        ]
    )


@router.message(Command("topup", "donate", "stars"))
async def cmd_topup(message: Message, db: Database) -> None:
    """Показать кассу: пачки кредитов и цену в звёздах."""
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer(NO_CHARACTER)
        return
    await message.answer(topup_text(player.credits), reply_markup=topup_keyboard())


@router.callback_query(TopUpCB.filter())
async def on_topup(
    callback: CallbackQuery, callback_data: TopUpCB, bot: Bot, store: StoreService
) -> None:
    """Выставить счёт на выбранную пачку."""
    pack = get_pack(callback_data.code)
    if pack is None:
        await callback.answer("Этой пачки больше нет в кассе.", show_alert=True)
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=pack.label,
        description=store.description(pack),
        payload=f"pack:{pack.code}:{callback.from_user.id}",
        currency="XTR",
        prices=[{"label": pack.label, "amount": pack.stars}],
    )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, store: StoreService) -> None:
    """Последняя проверка перед списанием звёзд."""
    try:
        store.check(query.invoice_payload)
    except StoreError as error:
        await query.answer(ok=False, error_message=str(error))
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, store: StoreService) -> None:
    """Звёзды списаны — начисляем кредиты."""
    try:
        grant = await store.grant(message.from_user.id, message.successful_payment)
    except StoreError as error:
        logger.warning("Оплата не начислена: %s", error)
        await message.answer(
            f"{error}\n\nЗвёзды не пропали: верну по команде /refund."
        )
        return

    if grant.already:
        await message.answer(f"Этот платёж уже учтён: <b>{grant.label}</b>.")
        return

    if grant.is_pro:
        got = grant.pro
        extras = []
        if got and got.blade:
            extras.append("🗡 Клинок ассасина — в инвентаре")
        if got and got.look:
            extras.append("🥷 Образ ассасина — в гардеробе")
        await message.answer(
            f"💎 <b>{grant.label}</b> "
            f"{'продлена' if got and got.renewed else 'оформлена'} на "
            f"{grant.goods.days} дней.\n"
            + ("\n".join(extras) + "\n" if extras else "")
            + "\nОпыт за бои теперь идёт в полтора раза. Подробности: /pro"
        )
        return

    if grant.is_relic:
        await message.answer(
            f"✨ <b>{grant.label}</b> — вещь мага уже в рюкзаке.\n"
            f"{grant.goods.describe_bonus()}\n\n"
            f"Надеть можно в карточке (/card), как дорастёшь до требований: "
            f"{describe_requirements(grant.goods)}."
        )
        return

    await message.answer(
        f"✅ <b>{grant.label}</b>\n"
        f"Начислено {grant.credits} 💰, на счету теперь <b>{grant.balance}</b> 💰.\n\n"
        "За вещами — в лавку: /buy"
    )


# ---------- подписка ----------


def pro_text(player, offer: ProOffer) -> str:
    lines = [f"{pro.EMOJI} <b>{pro.TITLE}</b>", ""]
    left = player.pro_left()
    if left:
        lines += [f"✅ Подписка активна, осталось <b>{spell_duration(left)}</b>.", ""]
    lines.append("<b>Что даёт</b>")
    lines += [f"• {line}" for line in pro.BENEFITS]
    lines += ["", pro.NOTE]
    promo = pro.promo_note(offer)
    if promo:
        lines += ["", promo]
    else:
        lines += ["", f"Цена: <b>{offer.stars}</b> ⭐ за {offer.days} дней."]
    return "\n".join(lines)


def pro_keyboard(offer: ProOffer, active: bool) -> InlineKeyboardMarkup:
    verb = "Продлить" if active else ("Забрать" if offer.free else "Оформить")
    tail = "бесплатно" if offer.free else f"{offer.stars} ⭐"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{pro.EMOJI} {verb} · {tail}",
                    callback_data=ProCB(free=int(offer.free)).pack(),
                )
            ]
        ]
    )


@router.message(Command("pro", "subscription"))
async def cmd_pro(message: Message, db: Database) -> None:
    """Подписка: что даёт, сколько стоит и как её взять."""
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer(NO_CHARACTER)
        return
    offer = current_offer()
    await message.answer(
        pro_text(player, offer),
        reply_markup=pro_keyboard(offer, player.is_pro()),
    )


@router.callback_query(ProCB.filter())
async def on_pro(
    callback: CallbackQuery, callback_data: ProCB, bot: Bot, db: Database,
    store: StoreService,
) -> None:
    """Забрать подписку: даром по акции, иначе счётом в звёздах."""
    player = await db.get_player(callback.from_user.id)
    if player is None:
        await callback.answer(NO_CHARACTER, show_alert=True)
        return

    offer = current_offer()
    if offer.free:
        try:
            grant = await claim_free_pro(db, player)
        except ProError as error:
            await callback.answer(str(error), show_alert=True)
            return
        extras = []
        if grant.blade:
            extras.append("🗡 Клинок ассасина — в инвентаре")
        if grant.look:
            extras.append("🥷 Образ ассасина — в гардеробе")
        await callback.message.answer(
            f"💎 Подписка {'продлена' if grant.renewed else 'оформлена'} на "
            f"{grant.offer.days} дней — бесплатно, по акции.\n"
            + ("\n".join(extras) + "\n" if extras else "")
            + "\nОпыт за бои теперь идёт в полтора раза."
        )
        await callback.answer("Добро пожаловать в PRO!")
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{pro.EMOJI} {pro.TITLE}",
        description=store.description(offer),
        payload=pro_payload(callback.from_user.id),
        currency="XTR",
        prices=[{"label": pro.TITLE, "amount": offer.stars}],
    )
    await callback.answer()


@router.message(Command("refund"))
async def cmd_refund(
    message: Message, command: CommandObject, store: StoreService
) -> None:
    """Вернуть звёзды за покупку, пока кредиты не потрачены."""
    charge_id = (command.args or "").strip()
    try:
        row = await store.refund(message.from_user.id, charge_id)
    except StoreError as error:
        await message.answer(str(error))
        return
    item = get_item(row["code"])
    taken = (
        f"«{item.title}» ушёл обратно к магу."
        if item is not None and item.is_magic
        else f"Кредиты ({row['credits']} 💰) сняты со счёта."
    )
    await message.answer(f"↩️ Звёзды вернулись: {row['stars']} ⭐. {taken}")


@router.message(Command("purchases"))
async def cmd_purchases(message: Message, store: StoreService) -> None:
    """История покупок — она же список номеров для /refund."""
    rows = await store.history(message.from_user.id)
    if not rows:
        await message.answer("Покупок пока не было. Касса: /topup")
        return

    lines = ["🧾 <b>Покупки</b>", ""]
    for row in rows:
        pack = get_pack(row["code"])
        item = get_item(row["code"])
        if pack is not None:
            title, what = pack.label, f"{row['credits']} 💰"
        elif item is not None:
            title, what = f"{item.emoji} {item.title}", "вещь мага"
        else:  # pragma: no cover - товар убрали из каталога
            title, what = row["code"], f"{row['credits']} 💰"
        mark = " (возвращено)" if row["refunded_at"] else ""
        lines.append(
            f"{row['created_at'][:10]} · {title} · {what} "
            f"за {row['stars']} ⭐{mark}\n<code>{row['charge_id']}</code>"
        )
    lines += ["", f"Всего занесено: <b>{spent_stars(rows)}</b> ⭐"]
    await message.answer("\n".join(lines))
