"""Разовые выдачи и правки при запуске — то, что нужно на время тестов.

Здесь два действия, и оба делаются ровно один раз, сколько бы раз бот ни
перезапустился. Защита у обоих одна: запись в журнале покупок с уникальным
`charge_id`. Не легла строка — значит это уже делали.

1. Световой меч бойцу Victor, чтобы вещь мага можно было пощупать в бою,
   не покупая её за звёзды.
2. Правка сроков подписки: бесплатную неделю по акции можно было забирать
   сколько угодно раз, и у бойца набежал месяц вместо недели.

Подарки в историю покупок не попадают и не возвращаются: за них не платили.

Когда тесты закончатся, файл удаляется целиком, а из `bot/main.py` уходят
два вызова.
"""

from __future__ import annotations

import logging

from bot.database import Database
from bot.game.health import now_ts
from bot.game.pro import DAY, PROMO_DAYS
from bot.pro_service import promo_claim_id

logger = logging.getLogger(__name__)

# Кому и что: прозвище бойца и код вещи из каталога
TEST_FIGHTER = "Victor"
TEST_RELIC = "lightsaber"
# Ключ в журнале: он же защита от повторной выдачи
GIFT_ID = f"gift:{TEST_RELIC}:{TEST_FIGHTER.lower()}"


async def grant_test_relic(db: Database) -> bool:
    """Выдать тестовый меч. True — выдали прямо сейчас, впервые."""
    player = await db.find_by_nickname(TEST_FIGHTER)
    if player is None:
        logger.info("Бойца %s пока нет — тестовый меч подождёт", TEST_FIGHTER)
        return False

    fresh = await db.add_purchase(
        user_id=player.user_id,
        code=TEST_RELIC,
        stars=0,
        credits=0,
        charge_id=GIFT_ID,
        kind="gift",
    )
    if not fresh:
        return False

    await db.add_gear(player.user_id, TEST_RELIC)
    logger.info("Тестовая выдача: %s получает %s", TEST_FIGHTER, TEST_RELIC)
    return True


async def fix_promo_overrun(db: Database) -> bool:
    """Урезать подписку, набежавшую от повторных нажатий «забрать даром».

    Кнопку можно было тыкать без счёта, и каждое нажатие добавляло неделю.
    Ставим ровно одну неделю от этого момента — столько акция и обещала, —
    и заодно отмечаем акцию забранной, чтобы её нельзя было взять снова.

    Правим только того бойца, у кого это точно случилось: у остальных срок
    честный, и трогать его нельзя.
    """
    player = await db.find_by_nickname(TEST_FIGHTER)
    if player is None:
        return False

    # Тот же ключ, что ставит claim_free_pro: легла строка — акцию за бойцом
    # ещё не числили, значит это те самые лишние недели.
    first = await db.add_purchase(
        user_id=player.user_id,
        code="pro",
        stars=0,
        credits=0,
        charge_id=promo_claim_id(player.user_id),
        kind="gift",
    )
    if not first or not player.is_pro():
        return False

    was = player.pro_until
    player.pro_until = now_ts() + PROMO_DAYS * DAY
    if player.pro_until >= was:
        # Срок и так короче обещанного — оставляем как есть
        player.pro_until = was
        return False
    await db.save_player(player)
    logger.info(
        "Правка подписки: %s было до %s, стало до %s",
        TEST_FIGHTER,
        was,
        player.pro_until,
    )
    return True


__all__ = [
    "GIFT_ID",
    "TEST_FIGHTER",
    "TEST_RELIC",
    "fix_promo_overrun",
    "grant_test_relic",
]
