"""Проведение дуэлей в ветке группы: вызовы, раунды, таймеры, итоги."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import math
import random
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.config import Config
from bot.database import Database
from bot.game.classes import Zone
from bot.game.combat import (
    MAX_MISSED_TURNS,
    Action,
    Fighter,
    RoundResult,
    resolve_round,
)
from bot.game.economy import (
    DRAW_CREDITS,
    DRAW_EXP_SHARE,
    LOSS_CREDITS,
    LOSS_EXP_SHARE,
    REPEAT_WINDOW_HOURS,
    apply_share,
    consolation_exp,
    rating_delta,
    repeat_share,
    win_credits,
    win_exp,
)
from bot.game.narrator import (
    duel_intro,
    esc,
    finish_report,
    health_warning,
    hp_bar,
    rewards_report,
    round_report,
)
from bot.keyboards import challenge_keyboard, fight_keyboard
from bot.models import Player, ProgressReport

logger = logging.getLogger(__name__)


class DuelError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


ChatKey = tuple[int, int | None]


@dataclass
class Challenge:
    id: int
    chat_id: int
    thread_id: int | None
    challenger: Player
    target_id: int | None = None
    message_id: int | None = None
    task: asyncio.Task | None = None


@dataclass
class Choice:
    """Незавершённый выбор бойца на раунд."""

    attack: Zone | None = None
    blocks: list[Zone] = field(default_factory=list)

    def is_ready(self, needed_blocks: int) -> bool:
        return self.attack is not None and len(self.blocks) == needed_blocks

    def to_action(self) -> Action:
        """Что боец успел нажать, то и уходит в раунд."""
        return Action(attack=self.attack, blocks=tuple(self.blocks))

    @property
    def is_empty(self) -> bool:
        return self.attack is None and not self.blocks

    def describe(self) -> str:
        attack = self.attack.title if self.attack else "—"
        blocks = ", ".join(z.title for z in self.blocks) if self.blocks else "—"
        return f"👊 {attack}   🛡 {blocks}"


@dataclass
class DuelSession:
    id: int
    chat_id: int
    thread_id: int | None
    fighters: dict[int, Fighter]
    order: tuple[int, int]
    round_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    prompt_message_id: int | None = None
    timer: asyncio.Task | None = None
    resolving: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> ChatKey:
        return (self.chat_id, self.thread_id)

    def opponent_of(self, user_id: int) -> Fighter:
        other_id = next(uid for uid in self.order if uid != user_id)
        return self.fighters[other_id]

    def choice_of(self, user_id: int) -> Choice:
        return self.choices.setdefault(user_id, Choice())


class DuelService:
    """Держит активные вызовы и бои, шлёт судейские сообщения в ветку."""

    def __init__(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.rng = rng or random.Random()
        self._ids = itertools.count(1)
        self._challenges: dict[int, Challenge] = {}
        self._duels: dict[int, DuelSession] = {}
        self._duel_by_chat: dict[ChatKey, int] = {}
        self._busy: dict[int, str] = {}  # user_id -> "challenge" | "duel"

    # ---------- вызовы ----------

    async def open_challenge(
        self,
        chat_id: int,
        thread_id: int | None,
        challenger: Player,
        target: Player | None = None,
    ) -> Challenge:
        if self._busy.get(challenger.user_id):
            raise DuelError("Ты уже в бою или у тебя висит незакрытый вызов.")
        if not challenger.can_fight():
            raise DuelError(health_warning(challenger))
        if target is not None:
            if target.user_id == challenger.user_id:
                raise DuelError("С самим собой драться — это уже другой фильм.")
            if self._busy.get(target.user_id) == "duel":
                raise DuelError(f"{target.nickname} сейчас на ринге. Дождись конца боя.")
            if not target.can_fight():
                raise DuelError(health_warning(target, is_self=False))
        if (chat_id, thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой. Один ринг — одна пара.")

        challenge = Challenge(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            challenger=challenger,
            target_id=target.user_id if target else None,
        )
        if target is None:
            text = (
                f"🥊 <b>{esc(challenger.nickname)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                "вызывает любого желающего на кулачный бой.\n\n"
                "Кто примет вызов?"
            )
        else:
            text = (
                f"🥊 <b>{esc(challenger.nickname)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                f"вызывает <b>{esc(target.nickname)}</b> "
                f"({target.fclass.label}, {target.level} ур.) на кулачный бой.\n\n"
                "Слово за вызванным."
            )
        message = await self._send(
            chat_id, thread_id, text, reply_markup=challenge_keyboard(challenge.id)
        )
        challenge.message_id = message.message_id
        challenge.task = asyncio.create_task(self._expire_challenge(challenge))
        self._challenges[challenge.id] = challenge
        self._busy[challenger.user_id] = "challenge"
        return challenge

    async def _expire_challenge(self, challenge: Challenge) -> None:
        try:
            await asyncio.sleep(self.config.challenge_timeout)
        except asyncio.CancelledError:
            return
        if self._challenges.get(challenge.id) is not challenge:
            return
        self._drop_challenge(challenge)
        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🥱 Вызов от <b>{esc(challenge.challenger.nickname)}</b> "
            "остался без ответа. Ринг свободен.",
        )

    def _drop_challenge(self, challenge: Challenge) -> None:
        self._challenges.pop(challenge.id, None)
        if self._busy.get(challenge.challenger.user_id) == "challenge":
            self._busy.pop(challenge.challenger.user_id, None)
        if challenge.task and not challenge.task.done():
            challenge.task.cancel()

    async def _withdraw_challenges_of(self, user_id: int) -> None:
        for challenge in list(self._challenges.values()):
            if challenge.challenger.user_id != user_id:
                continue
            self._drop_challenge(challenge)
            await self._edit(
                challenge.chat_id,
                challenge.message_id,
                f"↩️ Вызов от <b>{esc(challenge.challenger.nickname)}</b> снят: "
                "боец ушёл драться в другую пару.",
            )

    async def cancel_challenge(self, challenge_id: int, user_id: int) -> None:
        challenge = self._challenges.get(challenge_id)
        if challenge is None:
            raise DuelError("Этот вызов уже неактуален.")
        if challenge.challenger.user_id != user_id:
            raise DuelError("Отозвать вызов может только тот, кто его бросил.")
        self._drop_challenge(challenge)
        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🚪 <b>{esc(challenge.challenger.nickname)}</b> передумал(а) драться.",
        )

    async def accept_challenge(self, challenge_id: int, opponent: Player) -> DuelSession:
        challenge = self._challenges.get(challenge_id)
        if challenge is None:
            raise DuelError("Этот вызов уже неактуален.")
        if opponent.user_id == challenge.challenger.user_id:
            raise DuelError("Свой собственный вызов принять нельзя.")
        if challenge.target_id is not None and challenge.target_id != opponent.user_id:
            raise DuelError("Этот вызов адресован другому бойцу.")
        if self._busy.get(opponent.user_id) == "duel":
            raise DuelError("Ты уже на ринге в другом бою.")
        if not opponent.can_fight():
            raise DuelError(health_warning(opponent))
        if self._busy.get(opponent.user_id) == "challenge":
            # у принимающего висел свой вызов — снимаем его, драка важнее
            await self._withdraw_challenges_of(opponent.user_id)
        if (challenge.chat_id, challenge.thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой.")

        self._drop_challenge(challenge)
        challenger = await self.db.get_player(challenge.challenger.user_id)
        if challenger is None:  # pragma: no cover - персонажа удалили посреди вызова
            raise DuelError("Соперник куда-то пропал вместе со своим персонажем.")
        if not challenger.can_fight():
            # Подстраховка: здоровье само по себе только растёт, но вызов мог
            # провисеть дольше, чем живут наши записи о том, кто чем занят.
            raise DuelError(health_warning(challenger, is_self=False))

        await self._edit(
            challenge.chat_id,
            challenge.message_id,
            f"🥊 <b>{esc(opponent.nickname)}</b> принимает вызов "
            f"<b>{esc(challenger.nickname)}</b>. Ринг занят!",
        )
        return await self.start_duel(
            challenge.chat_id, challenge.thread_id, challenger, opponent
        )

    # ---------- бой ----------

    async def start_duel(
        self,
        chat_id: int,
        thread_id: int | None,
        first: Player,
        second: Player,
    ) -> DuelSession:
        if (chat_id, thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой.")
        for player in (first, second):
            if not player.can_fight():
                raise DuelError(health_warning(player, is_self=False))

        fighter_a = Fighter.from_player(first)
        fighter_b = Fighter.from_player(second)
        session = DuelSession(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            fighters={fighter_a.user_id: fighter_a, fighter_b.user_id: fighter_b},
            order=(fighter_a.user_id, fighter_b.user_id),
        )
        self._duels[session.id] = session
        self._duel_by_chat[session.key] = session.id
        self._busy[fighter_a.user_id] = "duel"
        self._busy[fighter_b.user_id] = "duel"

        await self._send(chat_id, thread_id, duel_intro(fighter_a, fighter_b))
        await self._start_round(session)
        return session

    async def _start_round(self, session: DuelSession) -> None:
        session.round_number += 1
        session.choices = {}
        session.resolving = False
        message = await self._send(
            session.chat_id,
            session.thread_id,
            self._prompt_text(session),
            reply_markup=fight_keyboard(session.id),
        )
        session.prompt_message_id = message.message_id
        session.timer = asyncio.create_task(
            self._round_timer(session, session.round_number)
        )

    def _prompt_text(self, session: DuelSession) -> str:
        lines = [
            f"<b>🔔 Раунд {session.round_number}. Бойцы, выбирайте.</b>",
            "",
        ]
        for user_id in session.order:
            fighter = session.fighters[user_id]
            choice = session.choices.get(user_id, Choice())
            ready = choice.is_ready(fighter.derived.block_zones)
            mark = "✅ готов" if ready else "⏳ думает"
            warning = ""
            left = MAX_MISSED_TURNS - fighter.missed_turns
            if fighter.missed_turns:
                warning = (
                    f" — ⚠️ пропусков подряд: {fighter.missed_turns}, "
                    f"осталось {left}"
                )
            lines.append(
                f"{fighter.fclass.emoji} {esc(fighter.name)} "
                f"{hp_bar(fighter.hp, fighter.max_hp)} {fighter.hp}/{fighter.max_hp} "
                f"— блоков: {fighter.derived.block_zones} — {mark}{warning}"
            )
        lines += [
            "",
            "👊 — куда бьёшь (одна зона), 🛡 — что закрываешь.",
            f"⏱ {self.config.turn_timeout} сек. Успеете оба раньше — "
            "раунд посчитается сразу.",
            "Что успел нажать, то и работает: без зоны удара боец не бьёт, "
            "незакрытая зона остаётся открытой.",
            f"Не нажал ничего — пропуск хода. {MAX_MISSED_TURNS} пропуска подряд — "
            "техническое поражение.",
            "Нажатия соперника ты не видишь: бот отвечает только тому, кто нажал.",
        ]
        return "\n".join(lines)

    async def _round_timer(self, session: DuelSession, round_number: int) -> None:
        try:
            await asyncio.sleep(self.config.turn_timeout)
        except asyncio.CancelledError:
            return
        async with session.lock:
            if session.round_number != round_number or session.resolving:
                return
            if session.id not in self._duels:
                return
            session.resolving = True
        await self._resolve(session)

    async def handle_choice(
        self, duel_id: int, user_id: int, action: str, zone_value: str
    ) -> str:
        """Обработать нажатие бойца. Возвращает текст для приватного ответа."""
        session = self._duels.get(duel_id)
        if session is None:
            raise DuelError("Этот бой уже закончился.")
        if user_id not in session.fighters:
            raise DuelError("Ты не участвуешь в этом бою. Болей за своих.")

        async with session.lock:
            if session.resolving:
                raise DuelError("Раунд уже считается, поздно.")
            fighter = session.fighters[user_id]
            choice = session.choice_of(user_id)
            was_ready = choice.is_ready(fighter.derived.block_zones)
            if action not in {"attack", "block"}:
                raise DuelError("Непонятное действие.")
            try:
                zone = Zone(zone_value)
            except ValueError as error:  # устаревшая кнопка из прошлой версии
                raise DuelError("Эта кнопка уже не работает.") from error

            if action == "attack":
                choice.attack = zone
            else:
                if zone in choice.blocks:
                    choice.blocks.remove(zone)
                else:
                    choice.blocks.append(zone)
                    if len(choice.blocks) > fighter.derived.block_zones:
                        choice.blocks.pop(0)

            now_ready = choice.is_ready(fighter.derived.block_zones)
            both_ready = all(
                session.choice_of(uid).is_ready(
                    session.fighters[uid].derived.block_zones
                )
                for uid in session.order
            )
            if both_ready:
                session.resolving = True

        if both_ready:
            await self._resolve(session)
        elif was_ready != now_ready:
            await self._edit(
                session.chat_id,
                session.prompt_message_id,
                self._prompt_text(session),
                reply_markup=fight_keyboard(session.id),
            )

        need = fighter.derived.block_zones - len(choice.blocks)
        hint = "" if now_ready else f"\nОсталось закрыть зон: {max(0, need)}"
        if choice.attack is None:
            hint = "\nВыбери зону удара."
        return f"{choice.describe()}{hint}"

    async def _resolve(self, session: DuelSession) -> None:
        self._cancel_timer(session)
        first_id, second_id = session.order
        first, second = session.fighters[first_id], session.fighters[second_id]
        actions = {
            user_id: session.choices.get(user_id, Choice()).to_action()
            for user_id in session.order
        }

        result = resolve_round(
            first,
            actions[first_id],
            second,
            actions[second_id],
            session.round_number,
            self.rng,
        )

        await self._edit(
            session.chat_id,
            session.prompt_message_id,
            f"🔒 Раунд {session.round_number}: ставки сделаны, судья считает.",
        )
        await self._send(
            session.chat_id,
            session.thread_id,
            round_report(result, session.fighters, self.rng),
        )

        if result.finished:
            await self._finish(session, result)
        else:
            await self._start_round(session)

    async def _finish(self, session: DuelSession, result: RoundResult) -> None:
        self._cancel_timer(session)
        await self._edit(
            session.chat_id,
            session.prompt_message_id,
            f"🔒 Раунд {session.round_number}: бой окончен.",
        )
        self._duels.pop(session.id, None)
        self._duel_by_chat.pop(session.key, None)
        for user_id in session.order:
            self._busy.pop(user_id, None)

        rewards = await self._apply_results(session, result)
        text = finish_report(result, session.fighters, self.rng)
        if rewards:
            text += "\n\n" + rewards
        await self._send(session.chat_id, session.thread_id, text)
        await self.db.add_duel(
            chat_id=session.chat_id,
            thread_id=session.thread_id,
            challenger_id=session.order[0],
            opponent_id=session.order[1],
            winner_id=result.winner_id,
            rounds=session.round_number,
            end_reason=result.end_reason.value if result.end_reason else None,
        )

    async def _apply_results(self, session: DuelSession, result: RoundResult) -> str:
        """Начислить опыт, кредиты и рейтинг. Вернуть блок текста с итогами."""
        players: dict[int, Player] = {}
        for user_id in session.order:
            player = await self.db.get_player(user_id)
            if player is not None:
                players[user_id] = player
        if not players:  # pragma: no cover - персонажей удалили по ходу боя
            return ""

        previous_fights = await self.db.count_recent_duels_between(
            session.order[0], session.order[1], REPEAT_WINDOW_HOURS
        )
        share = repeat_share(previous_fights)
        # Уровни фиксируем до начисления: иначе взятый в этом же бою уровень
        # перекосит награду второго бойца.
        levels = {
            user_id: players[user_id].level
            if user_id in players
            else session.fighters[user_id].level
            for user_id in session.order
        }

        rows: list[tuple[Player, ProgressReport]] = []
        for user_id, player in players.items():
            fighter = session.fighters[user_id]
            opponent = session.opponent_of(user_id)
            my_level = levels[user_id]
            opponent_level = levels[opponent.user_id]
            won = result.winner_id == user_id

            full_exp = win_exp(fighter.damage_dealt, my_level, opponent_level)
            if won:
                player.wins += 1
                exp, credits = full_exp, win_credits(self.rng)
            elif result.winner_id is None:
                player.draws += 1
                exp = consolation_exp(full_exp, DRAW_EXP_SHARE)
                credits = DRAW_CREDITS
            else:
                player.losses += 1
                exp = consolation_exp(full_exp, LOSS_EXP_SHARE)
                credits = LOSS_CREDITS

            delta = rating_delta(won, my_level, opponent_level)
            if share < 1.0:
                exp = apply_share(exp, share)
                credits = apply_share(credits, share)
                delta = int(math.copysign(apply_share(abs(delta), share), delta))

            player.set_hp(fighter.hp)
            report = player.grant_exp(exp)
            report.credits += credits
            report.rating_delta = delta
            player.grant_credits(credits)
            player.apply_rating(delta)
            await self.db.save_player(player)
            rows.append((player, report))

        return rewards_report(rows, share, previous_fights)

    def _cancel_timer(self, session: DuelSession) -> None:
        """Снять таймер раунда.

        Раунд может считаться прямо из задачи-таймера (когда время вышло),
        поэтому себя же отменять нельзя — иначе CancelledError оборвёт подсчёт
        итогов на первом же await.
        """
        timer = session.timer
        session.timer = None
        if timer and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    # ---------- состояние ----------

    def duel_in_chat(self, chat_id: int, thread_id: int | None) -> DuelSession | None:
        duel_id = self._duel_by_chat.get((chat_id, thread_id))
        return self._duels.get(duel_id) if duel_id else None

    def duel_of_user(self, user_id: int) -> DuelSession | None:
        for session in self._duels.values():
            if user_id in session.fighters:
                return session
        return None

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._busy

    async def shutdown(self) -> None:
        for session in list(self._duels.values()):
            self._cancel_timer(session)
            with contextlib.suppress(Exception):
                await self._send(
                    session.chat_id,
                    session.thread_id,
                    "⚠️ Судья прерывает бой: бот уходит на перезагрузку. "
                    "Результат не засчитан.",
                )
        for challenge in list(self._challenges.values()):
            if challenge.task and not challenge.task.done():
                challenge.task.cancel()
        self._duels.clear()
        self._duel_by_chat.clear()
        self._challenges.clear()
        self._busy.clear()

    # ---------- отправка ----------

    async def _send(self, chat_id: int, thread_id: int | None, text: str, **kwargs):
        return await self.bot.send_message(
            chat_id, text, message_thread_id=thread_id, **kwargs
        )

    async def _edit(self, chat_id: int, message_id: int | None, text: str, **kwargs):
        if message_id is None:
            return None
        try:
            return await self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, **kwargs
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                logger.warning("Не удалось отредактировать сообщение: %s", error)
            return None
