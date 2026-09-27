"""Документы бойца: то, что у него на руках, а не на плечах.

Раздел заведён под то, что будет копиться: страховой полис первый, за ним
пойдут права из автошколы, пропуска и всё прочее, что выдают конторы
города. Поэтому наружу отдаётся **список**, а не «полис в карточке» —
страница рисует документы одной вёрсткой и не знает, сколько их.

У каждого документа набор полей один и тот же: название, на чьё имя, срок,
что даёт. Этого хватает и полису, и любому будущему бланку.

У полиса есть два состояния сверх «действует» и «просрочен»: он бывает
оплачен и бывает выписан подпиской. Обещания у них разные — «месяц с часа
оформления» против «пока жива подписка», — и переключатель автопродления
есть только у первого: вторым распоряжается не боец, а срок его PRO.
"""

from __future__ import annotations

from typing import Any

from bot.game.clock import club_moment
from bot.game.health import now_ts
from bot.game.insurance import (
    BENEFITS,
    PRO_BENEFITS,
    CODE,
    COVERS,
    EMOJI,
    HEAL_DISCOUNT,
    INSURER,
    NOTE,
    POLICY_PRICE,
    TITLE,
    policy_number,
)
from bot.models import Player


def policy_document(player: Player, moment: int) -> dict[str, Any]:
    """Страховой полис как документ. Пустой словарь — полиса не было."""
    policy = player.policy
    if policy is None:
        return {}
    live = policy.is_active(moment)
    # Полис держит подписка, когда его срок и есть её срок. Тогда и
    # обещания у него другие: не «месяц с часа оформления», а «пока жива
    # подписка», и продлевать его незачем
    by_pro = player.is_pro(moment) and policy.until <= player.pro_until
    return {
        "code": CODE,
        "kind": "insurance",
        "emoji": EMOJI,
        "title": TITLE,
        "issuer": INSURER,
        "by_pro": by_pro,
        "number": policy_number(player.user_id, policy.issued),
        # Имя застрахованного — прозвище бойца: другого имени у него нет
        "holder": player.nickname,
        "issued": club_moment(policy.issued),
        "until": club_moment(policy.until),
        "period": policy.period_text(),
        "active": live,
        "seconds_left": policy.seconds_left(moment),
        "covers": COVERS,
        "gives": list(PRO_BENEFITS if by_pro else BENEFITS),
        "discount": round(HEAL_DISCOUNT * 100),
        # Полисом подписки управляет подписка: переключать ему нечего, и
        # переключатель на таком бланке обещал бы власть, которой нет
        "auto_renew": policy.auto_renew,
        "switchable": not by_pro,
        "price": POLICY_PRICE,
        "note": NOTE,
        # Просроченный полис остаётся документом, и по нему видно, что
        # именно кончилось: срок, а не сам документ
        "state": "Действует" if live else "Срок вышел",
        # По какому праву он на руках: по оплате или по подписке
        "ground": "По подписке PRO" if by_pro else "Оплачен",
    }


def build_documents(player: Player, now: int | None = None) -> dict[str, Any]:
    """Раздел «Документы» целиком."""
    moment = now_ts() if now is None else now
    papers = [row for row in (policy_document(player, moment),) if row]
    return {
        "documents": papers,
        "total": len(papers),
        # Пусто — говорим, где документы берут: пустой раздел без этого
        # выглядит поломанным, а не новым
        "empty_note": (
            "Пока ни одного документа. Полис страхования жизни и здоровья "
            "оформляют в страховой компании."
        ),
    }


__all__ = ["build_documents", "policy_document"]
