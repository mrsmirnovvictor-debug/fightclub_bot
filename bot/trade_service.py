"""Обмен на рынке: двое, один стол и по кнопке у каждого.

Служба живёт в памяти, как и бои: обмен идёт минуты, а не дни, и
переживать перезапуск ему незачем. Всё, что должно остаться, уходит в
базу одним движением — в тот миг, когда оба согласились.

Дверей три, и каждая заперта на сервере, а не прятанием кнопки:
позвать можно только того, кто стоит на рынке; править можно только
свою половину стола; обмен проходит, только если обе стороны нажали
«готов» и всё выложенное на месте.

Согласие сбрасывается любой правкой — и своей, и чужой. Это главное
правило стола: нажатие относится к тому, что лежало перед глазами, и ни
к чему больше.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from bot.database import Database
from bot.game.locations import Service, where_to
from bot.game.potions import get_potion
from bot.game.trade import IDLE_SECONDS, INVITE_SECONDS, MAX_ITEMS
from bot.models import Player
from bot.travel_service import require

logger = logging.getLogger(__name__)


class TradeError(Exception):
    """Отказ, который показывают игроку как есть."""


GEAR = "gear"  # вещь: у неё номер, износ и один хозяин
POTION = "potion"  # склянки: не нумерованы, передаются числом


@dataclass(frozen=True)
class Offer:
    """Одна выкладка на стол.

    Вещь и склянки лежат рядом, но считаются по-разному: у вещи есть
    номер и она всегда одна, склянки одинаковы и передаются числом.
    Отсюда `kind`: без него «три эликсира» пришлось бы выкладывать
    тремя строками из четырёх возможных.
    """

    kind: str
    key: str  # номер вещи или код эликсира
    count: int = 1

    @property
    def item_id(self) -> int:
        return int(self.key)

    def same(self, kind: str, key: str) -> bool:
        return self.kind == kind and self.key == key


@dataclass
class Side:
    """Половина стола: что этот боец выложил и согласен ли."""

    user_id: int
    nickname: str
    credits: int = 0
    offers: list[Offer] = field(default_factory=list)
    ready: bool = False

    def find(self, kind: str, key: str) -> Offer | None:
        return next((one for one in self.offers if one.same(kind, key)), None)

    @property
    def empty(self) -> bool:
        return not self.offers and self.credits <= 0


@dataclass
class Trade:
    """Идущий обмен. Живёт, пока его не закрыли или не бросили."""

    id: int
    sides: dict[int, Side]
    # Растёт на каждую правку: по нему страница и понимает, что стол
    # поменялся, — сравнивать половины целиком ей незачем
    version: int = 1
    touched: float = field(default_factory=time.monotonic)
    done: bool = False

    def other(self, user_id: int) -> Side:
        return next(side for code, side in self.sides.items() if code != user_id)

    def side(self, user_id: int) -> Side:
        return self.sides[user_id]

    def stale(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        return moment - self.touched > IDLE_SECONDS

    def touch(self) -> None:
        """Стол поменялся: согласия сброшены, счётчик правок сдвинут."""
        self.version += 1
        self.touched = time.monotonic()
        for side in self.sides.values():
            side.ready = False


@dataclass
class Invite:
    """Приглашение к столу. Живёт минуту."""

    from_id: int
    from_name: str
    to_id: int
    until: float

    def alive(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) < self.until

    def seconds_left(self, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        return max(0, int(self.until - moment))


class TradeService:
    """Кто кого позвал и кто с кем меняется."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._invites: dict[int, Invite] = {}  # кого позвали → приглашение
        self._trades: dict[int, Trade] = {}  # боец → его обмен
        self._done: dict[int, str] = {}  # боец → чем кончилось, до первого взгляда
        self._next_id = 1
        # Службы, у которых спрашивают, занят ли боец боем
        self._keepers: list = []

    def watch(self, *services) -> None:
        self._keepers.extend(service for service in services if service is not None)

    def busy(self, user_id: int) -> bool:
        return any(keeper.is_busy(user_id) for keeper in self._keepers)

    # ---------- где что лежит ----------

    def trade_of(self, user_id: int) -> Trade | None:
        trade = self._trades.get(user_id)
        if trade is None:
            return None
        if trade.stale():
            self.close(trade, "Стол бросили — обмен закрылся сам.")
            return None
        return trade

    def invite_to(self, user_id: int) -> Invite | None:
        """Приглашение, которое ждёт ответа этого бойца."""
        invite = self._invites.get(user_id)
        if invite is None:
            return None
        if not invite.alive():
            self._invites.pop(user_id, None)
            return None
        return invite

    def invite_from(self, user_id: int) -> Invite | None:
        """Приглашение, которое этот боец отправил и ждёт ответа."""
        for invite in list(self._invites.values()):
            if invite.from_id != user_id:
                continue
            if not invite.alive():
                self._invites.pop(invite.to_id, None)
                return None
            return invite
        return None

    def take_done(self, user_id: int) -> str:
        """Чем кончился прошлый обмен. Забирается один раз."""
        return self._done.pop(user_id, "")

    # ---------- приглашение ----------

    async def invite(self, player: Player, to_id: int) -> Invite:
        """Позвать бойца к столу. Ответа ждём минуту."""
        if to_id == player.user_id:
            raise TradeError("Сам с собой не меняются.")
        if self.trade_of(player.user_id) is not None:
            raise TradeError("Ты уже за столом. Закончи этот обмен.")
        if self.trade_of(to_id) is not None:
            raise TradeError("Он сейчас меняется с другим. Подожди.")
        if self.busy(player.user_id) or self.busy(to_id):
            raise TradeError("Кто-то из вас занят боем. Меняться будете после.")

        other = await self.db.get_player(to_id)
        if other is None:
            raise TradeError("Такого бойца в клубе нет.")
        self._require_market(other, his=True)

        # Позвали того, кто уже позвал тебя: звать заново незачем —
        # приглашение уже лежит и ждёт нажатия
        mine = self.invite_to(player.user_id)
        if mine is not None and mine.from_id == to_id:
            raise TradeError(f"{other.nickname} уже позвал тебя — соглашайся.")

        invite = Invite(
            from_id=player.user_id,
            from_name=player.nickname,
            to_id=to_id,
            until=time.monotonic() + INVITE_SECONDS,
        )
        self._invites[to_id] = invite
        logger.info("Обмен: %s зовёт %s", player.user_id, to_id)
        return invite

    async def accept(self, player: Player) -> Trade:
        """Согласиться на обмен. Стол открывается у обоих."""
        invite = self.invite_to(player.user_id)
        if invite is None:
            raise TradeError("Приглашение уже сгорело.")
        other = await self.db.get_player(invite.from_id)
        if other is None:  # pragma: no cover - позвавшего удалили
            self._invites.pop(player.user_id, None)
            raise TradeError("Позвавший куда-то делся.")
        self._require_market(other, his=True)
        if self.busy(player.user_id) or self.busy(invite.from_id):
            raise TradeError("Кто-то из вас занят боем. Меняться будете после.")

        self._invites.pop(player.user_id, None)
        trade = Trade(
            id=self._next_id,
            sides={
                player.user_id: Side(player.user_id, player.nickname),
                other.user_id: Side(other.user_id, other.nickname),
            },
        )
        self._next_id += 1
        self._trades[player.user_id] = trade
        self._trades[other.user_id] = trade
        logger.info("Обмен №%s открыт: %s и %s", trade.id, player.user_id, other.user_id)
        return trade

    def decline(self, user_id: int) -> None:
        """Отказаться от приглашения."""
        if self._invites.pop(user_id, None) is None:
            raise TradeError("Приглашения уже нет.")

    def withdraw(self, user_id: int) -> None:
        """Забрать своё приглашение обратно."""
        invite = self.invite_from(user_id)
        if invite is None:
            raise TradeError("Ты никого не звал.")
        self._invites.pop(invite.to_id, None)

    # ---------- стол ----------

    def mine(self, player: Player) -> Trade:
        """Обмен, за которым сидит этот боец. Нет — значит нет."""
        trade = self.trade_of(player.user_id)
        if trade is None:
            raise TradeError("Ты сейчас ни с кем не меняешься.")
        return trade

    def put_credits(self, player: Player, amount: int) -> Trade:
        """Положить на стол кредиты. Столько, сколько есть."""
        trade = self.mine(player)
        if amount < 0:
            raise TradeError("Отрицательных кредитов не бывает.")
        if amount > player.credits:
            raise TradeError(
                f"На счету {player.credits} 💰 — больше положить нечего."
            )
        trade.side(player.user_id).credits = amount
        trade.touch()
        return trade

    def put_item(self, player: Player, kind: str, key: str, count: int = 1) -> Trade:
        """Выложить вещь или склянки. Ноль штук — забрать со стола обратно.

        Одна ручка и на выкладывание, и на «передать не три, а две»:
        страница всегда говорит, сколько должно лежать, а не насколько
        поменять. Так число на столе не зависит от того, дошло ли
        предыдущее нажатие.
        """
        trade = self.mine(player)
        side = trade.side(player.user_id)
        was = side.find(kind, key)

        if count <= 0:
            if was is None:
                raise TradeError("Этого на столе нет.")
            side.offers.remove(was)
            trade.touch()
            return trade

        if was is None and len(side.offers) >= MAX_ITEMS:
            raise TradeError(f"За раз меняют не больше {MAX_ITEMS} предметов.")

        offer = self._weigh(player, kind, key, count)
        if was is not None:
            if was == offer:
                return trade  # ничего не поменялось — и согласия не трогаем
            side.offers[side.offers.index(was)] = offer
        else:
            side.offers.append(offer)
        trade.touch()
        return trade

    def _weigh(self, player: Player, kind: str, key: str, count: int) -> Offer:
        """Проверить, что боец правда может это отдать, и во столько штук."""
        if kind == GEAR:
            try:
                item_id = int(key)
            except ValueError as error:
                raise TradeError("Такой вещи у тебя нет.") from error
            owned = player.find_gear(item_id)
            if owned is None:
                raise TradeError("Такой вещи у тебя нет.")
            if owned.is_equipped:
                raise TradeError(f"«{owned.title}» на тебе надета. Сними её сначала.")
            return Offer(GEAR, str(item_id), 1)
        if kind == POTION:
            have = player.potions.get(key, 0)
            potion = get_potion(key)
            if potion is None or have <= 0:
                raise TradeError("Таких склянок у тебя нет.")
            if count > have:
                raise TradeError(f"«{potion.title}»: всего {have} шт., больше нет.")
            return Offer(POTION, key, count)
        raise TradeError("Это не меняется.")

    def take_item(self, player: Player, kind: str, key: str) -> Trade:
        """Забрать со стола обратно."""
        return self.put_item(player, kind, key, 0)

    def confirm(self, player: Player) -> Trade:
        """Сказать «готов». Обмен ждёт второго."""
        trade = self.mine(player)
        trade.side(player.user_id).ready = True
        return trade

    def unconfirm(self, player: Player) -> Trade:
        """Передумать, не закрывая стол."""
        trade = self.mine(player)
        trade.side(player.user_id).ready = False
        return trade

    def cancel(self, player: Player) -> None:
        """Отказаться. Стол закрывается у обоих — так и просили."""
        trade = self.mine(player)
        self.close(trade, f"{player.nickname} отказался от обмена.")
        self._done[player.user_id] = "Ты отказался от обмена."

    async def alive(self, trade: Trade, now: int | None = None) -> Trade | None:
        """Оба ли ещё на рынке. Ушедший закрывает стол.

        Дорогу обмен не запирает: держать человека на рынке, потому что
        кто-то открыл ему стол, — плохой размен. Зато и ждать ушедшего
        незачем: обмен всё равно не прошёл бы, а второй сидел бы перед
        столом, который уже мёртв.
        """
        for side in list(trade.sides.values()):
            one = await self.db.get_player(side.user_id)
            gone = one is None
            if one is not None:
                try:
                    require(one, Service.TRADE, now)
                except Exception:
                    gone = True
            if gone:
                self.close(trade, f"{side.nickname} ушёл с рынка — обмен закрылся.")
                return None
        return trade

    def close(self, trade: Trade, said: str) -> None:
        for user_id in trade.sides:
            if self._trades.get(user_id) is trade:
                self._trades.pop(user_id, None)
            if said:
                self._done[user_id] = said

    # ---------- сам обмен ----------

    async def _hand_over(self, offer: Offer, giver: Player, taker: Player) -> str:
        """Передать одну выкладку. Не вышло — обмен не состоялся целиком."""
        if offer.kind == GEAR:
            owned = giver.find_gear(offer.item_id)
            moved = owned is not None and await self.db.hand_over_gear(
                offer.item_id, giver.user_id, taker.user_id
            )
            if not moved or owned is None:  # pragma: no cover - вещь ушла из-под рук
                raise TradeError("Вещь ушла из рук — обмен не состоялся.")
            giver.drop_gear(owned)
            return owned.title

        potion = get_potion(offer.key)
        moved = await self.db.hand_over_potions(
            offer.key, giver.user_id, taker.user_id, offer.count
        )
        if not potion or not moved:  # pragma: no cover - склянки выпили за столом
            raise TradeError("Склянки кончились — обмен не состоялся.")
        left = giver.potions.get(offer.key, 0) - offer.count
        if left > 0:
            giver.potions[offer.key] = left
        else:
            giver.potions.pop(offer.key, None)
        taker.potions[offer.key] = taker.potions.get(offer.key, 0) + offer.count
        return f"{potion.title} ×{offer.count}"

    async def settle(self, trade: Trade) -> str:
        """Развести вещи и деньги по хозяевам. Зовётся, когда готовы оба."""
        first, second = list(trade.sides.values())
        players = {}
        for side in (first, second):
            player = await self.db.get_player(side.user_id)
            if player is None:  # pragma: no cover - бойца удалили за столом
                raise TradeError("Один из вас куда-то делся.")
            players[side.user_id] = player

        # Проверяем заново всё, что проверяли при выкладывании: между
        # нажатиями человек мог надеть вещь, выпить склянку или потратить
        # кредиты в другом окне
        for side in (first, second):
            player = players[side.user_id]
            self._require_market(player, his=player.user_id != first.user_id)
            if side.credits > player.credits:
                raise TradeError(
                    f"У бойца {player.nickname} уже нет столько кредитов."
                )
            for offer in side.offers:
                self._weigh(player, offer.kind, offer.key, offer.count)

        moved: list[str] = []
        for side in (first, second):
            taker = players[trade.other(side.user_id).user_id]
            giver = players[side.user_id]
            for offer in side.offers:
                title = await self._hand_over(offer, giver, taker)
                moved.append(f"{title}: {giver.nickname} → {taker.nickname}")
            giver.credits -= side.credits
            taker.credits += side.credits

        for player in players.values():
            await self.db.save_player(player)
        logger.info("Обмен №%s прошёл: %s", trade.id, "; ".join(moved) or "без вещей")

        trade.done = True
        self.close(trade, "")
        for side in (first, second):
            self._done[side.user_id] = "Обмен прошёл."
        return "Обмен прошёл."

    async def ready(self, player: Player) -> tuple[Trade, bool]:
        """Нажать «готов». Второй флаг — прошёл ли обмен прямо сейчас."""
        trade = self.confirm(player)
        if not all(side.ready for side in trade.sides.values()):
            return trade, False
        await self.settle(trade)
        return trade, True

    # ---------- кто на рынке ----------

    def _require_market(self, player: Player, his: bool = False) -> None:
        """Стоит ли боец на рынке. Иначе меняться не с кем и негде."""
        try:
            require(player, Service.TRADE)
        except Exception as error:
            if his:
                raise TradeError(f"{player.nickname} сейчас не на рынке.") from error
            raise TradeError(str(error)) from error

    async def crowd(self, player: Player, now: int | None = None) -> list[Player]:
        """Кто сейчас на рынке, кроме тебя.

        Вышедший за дверь в списке не стоит, хотя его локация ещё рынок:
        в пути он ни там, ни там, и позвать его всё равно не выйдет.
        """
        place = where_to(Service.TRADE)
        if place is None:  # pragma: no cover - услуга без адреса
            return []
        here = await self.db.players_at(place.code)
        return [
            one
            for one in here
            if one.user_id != player.user_id
            and one.where(now) == place.code
            and not one.in_transit(now)
        ]


__all__ = [
    "GEAR",
    "POTION",
    "Invite",
    "Offer",
    "Side",
    "Trade",
    "TradeError",
    "TradeService",
]
