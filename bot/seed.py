"""Разовые выдачи и правки при запуске — то, что нужно на время тестов.

Здесь несколько действий, и каждое делается ровно один раз, сколько бы раз бот ни
перезапустился. Защита у обоих одна: запись в журнале покупок с уникальным
`charge_id`. Не легла строка — значит это уже делали.

1. Световой меч бойцу Victor, чтобы вещь мага можно было пощупать в бою,
   не покупая её за звёзды.
1a. Четыре усиленные вещи двум бойцам, на которых гоняют бой руками.
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


# ---------- усиленные вещи под ручные тесты ----------
#
# Числа у этих четырёх задал хозяин клуба, и они нарочно выше потолка,
# который держит остальной прилавок: так в бою видно и крит, и уворот, и
# контрудар, не отыгрывая до девятого уровня. Пока они такие, лавка продаёт
# их всем — это не подарочные копии, а сам товар.
TEST_GEAR: tuple[str, ...] = ("bandana", "wraps", "sneakers", "wife_beater")
# Кому кладём их в рюкзак без покупки
TEST_FIGHTERS: tuple[str, ...] = ("Victor", "x RED x")

# Набор ассасина той же породы: числа заданы вручную и тоже выше потолка,
# но эти вещи никому не выдаются — они лежат на прилавке с третьего уровня и
# покупаются за кредиты, как обычный товар.
ASSASSIN_GEAR: tuple[str, ...] = (
    "assassin_stiletto",
    "throwing_belt",
    "shadow_coat",
    "sheath_pants",
)

# Всё, что сознательно выведено за потолок процентов. По этому списку тесты
# понимают, что правило сейчас нарочно нарушено, и не спорят с хозяином клуба.
BOOSTED_GEAR: tuple[str, ...] = TEST_GEAR + ASSASSIN_GEAR


async def grant_test_gear(db: Database) -> int:
    """Выдать усиленные вещи бойцам, на которых гоняют бой. Сколько выдали.

    Как и меч, ровно один раз на бойца и вещь: защита — строка в журнале
    покупок. Бойца ещё нет — молча ждём следующего запуска.
    """
    given = 0
    for nickname in TEST_FIGHTERS:
        player = await db.find_by_nickname(nickname)
        if player is None:
            logger.info("Бойца %s пока нет — усиленные вещи подождут", nickname)
            continue
        for code in TEST_GEAR:
            fresh = await db.add_purchase(
                user_id=player.user_id,
                code=code,
                stars=0,
                credits=0,
                charge_id=f"gift:{code}:{player.user_id}",
                kind="gift",
            )
            if not fresh:
                continue
            await db.add_gear(player.user_id, code)
            given += 1
            logger.info("Тестовая выдача: %s получает %s", nickname, code)
    return given


__all__ = [
    "ASSASSIN_GEAR",
    "BOOSTED_GEAR",
    "GIFT_ID",
    "TEST_FIGHTERS",
    "TEST_GEAR",
    "TEST_FIGHTER",
    "TEST_RELIC",
    "fix_promo_overrun",
    "grant_test_gear",
    "grant_test_relic",
]
