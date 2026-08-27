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
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from bot.config import Config
from bot.database import Database
from bot.game.classes import BLOCK_WIDTH, Zone, block_combo, block_title
from bot.game.equipment import BARE_HANDS_ICON
from bot.game.modes import FightMode
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
    mention,
    name_link,
    standoff_card,
    esc,
    finish_report,
    health_warning,
    hp_bar,
    rewards_report,
    round_report,
)
from bot.inventory_service import wear_after_fight
from bot.keyboards import challenge_keyboard, fight_keyboard, standoff_keyboard
from bot.models import Player, ProgressReport

logger = logging.getLogger(__name__)

# Сколько раз пробуем отправить сообщение боя и сколько ждём на флуд-контроле
SEND_ATTEMPTS = 3
MAX_FLOOD_WAIT = 30


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
    chat_title: str = ""
    mode: FightMode = FightMode.FIST


@dataclass
class Choice:
    """Незавершённый выбор бойца на раунд."""

    attacks: dict[int, Zone] = field(default_factory=dict)
    block: tuple[Zone, ...] = ()

    def is_ready(self, weapons: int) -> bool:
        chosen = [slot for slot in range(weapons) if slot in self.attacks]
        return len(chosen) == weapons and bool(self.block)

    def to_action(self, weapons: int) -> Action:
        """Что боец успел нажать, то и уходит в раунд."""
        return Action(
            attacks=tuple(self.attacks.get(slot) for slot in range(weapons)),
            block=self.block,
        )

    @property
    def is_empty(self) -> bool:
        return not self.attacks and not self.block

    def describe(self, fighter: Fighter) -> str:
        parts = []
        for slot, icon in enumerate(fighter.weapon_icons):
            zone = self.attacks.get(slot)
            parts.append(f"{icon} {zone.title if zone else '—'}")
        block = block_title(self.block) if self.block else "—"
        return "   ".join(parts) + f"\n🛡 {block}"


@dataclass
class DuelSession:
    id: int
    chat_id: int
    thread_id: int | None
    fighters: dict[int, Fighter]
    order: tuple[int, int]
    chat_title: str = ""  # название группы — станет местом рождения новичка
    mode: FightMode = FightMode.FIST
    round_number: int = 0
    choices: dict[int, Choice] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    prompt_message_id: int | None = None
    standoff_message_id: int | None = None
    started: bool = False  # гонг прозвучал
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

    @property
    def challenger_id(self) -> int:
        """Тот, кто бросил вызов: ему и решать, выходить ли на ринг."""
        return self.order[0]

    def is_ready(self, user_id: int) -> bool:
        fighter = self.fighters[user_id]
        return self.choice_of(user_id).is_ready(fighter.attacks_per_round)

    @property
    def panel(self) -> tuple[tuple[str, ...], int]:
        """Одна панель на двоих: значки ударов и ширина блока.

        В кулачном бою у обоих всё одинаково. Если однажды снаряжение
        разойдётся, панель показывает общий знаменатель, а нажатие
        считается по снаряжению того, кто нажал.
        """
        sides = [self.fighters[user_id] for user_id in self.order]
        icons = sides[0].weapon_icons
        if any(side.weapon_icons != icons for side in sides):
            icons = (BARE_HANDS_ICON,) * max(s.attacks_per_round for s in sides)
        width = sides[0].block_width
        if any(side.block_width != width for side in sides):
            width = BLOCK_WIDTH
        return icons, width


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
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
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
            chat_title=chat_title,
            mode=mode,
        )
        if target is None:
            text = (
                f"🥊 <b>{name_link(challenger.user_id, challenger.nickname)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                "вызывает любого желающего на кулачный бой.\n\n"
                "Кто примет вызов?"
            )
        else:
            text = (
                f"🥊 <b>{name_link(challenger.user_id, challenger.nickname)}</b> "
                f"({challenger.fclass.label}, {challenger.level} ур.) "
                f"вызывает <b>{name_link(target.user_id, target.nickname)}</b> "
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
        return await self.open_standoff(
            challenge.chat_id,
            challenge.thread_id,
            challenger,
            opponent,
            challenge.chat_title,
            challenge.mode,
        )

    # ---------- бой ----------

    async def open_standoff(
        self,
        chat_id: int,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> DuelSession:
        """Свести бойцов лицом к лицу и дать вызвавшему посмотреть на соперника."""
        session = self._make_session(chat_id, thread_id, first, second, chat_title, mode)
        message = await self._send(
            chat_id,
            thread_id,
            standoff_card(first, second, mode=mode),
            reply_markup=standoff_keyboard(session.id),
        )
        session.standoff_message_id = message.message_id
        session.timer = asyncio.create_task(self._standoff_timer(session))
        return session

    async def confirm_duel(self, duel_id: int, user_id: int) -> DuelSession:
        """Вызвавший посмотрел на соперника и выходит на ринг."""
        session = self._pending(duel_id)
        if user_id != session.challenger_id:
            raise DuelError("Решает тот, кто бросил вызов. Жди.")
        self._cancel_timer(session)
        session.started = True
        await self._edit(
            session.chat_id,
            session.standoff_message_id,
            standoff_card(*self._standoff_players(session), decision="✅ Бойцы сошлись."),
        )
        await self._send(
            session.chat_id,
            session.thread_id,
            duel_intro(*(session.fighters[uid] for uid in session.order)),
        )
        await self._start_round(session)
        return session

    async def decline_duel(self, duel_id: int, user_id: int) -> None:
        """Разойтись без боя: соперник оказался не по зубам."""
        session = self._pending(duel_id)
        if user_id not in session.fighters:
            raise DuelError("Это не твой бой.")
        self._cancel_timer(session)
        who = session.fighters[user_id].name
        self._forget(session)
        await self._edit(
            session.chat_id,
            session.standoff_message_id,
            standoff_card(
                *self._standoff_players(session),
                decision=f"🚪 <b>{esc(who)}</b> отказывается от боя. Ринг свободен.",
            ),
        )

    def _pending(self, duel_id: int) -> DuelSession:
        session = self._duels.get(duel_id)
        if session is None:
            raise DuelError("Этот бой уже неактуален.")
        if session.started:
            raise DuelError("Бой уже идёт.")
        return session

    def _standoff_players(self, session: DuelSession) -> tuple[Player, Player]:
        return tuple(session.players[uid] for uid in session.order)

    async def _standoff_timer(self, session: DuelSession) -> None:
        try:
            await asyncio.sleep(self.config.challenge_timeout)
        except asyncio.CancelledError:
            return
        if session.started or session.id not in self._duels:
            return
        self._forget(session)
        await self._edit(
            session.chat_id,
            session.standoff_message_id,
            standoff_card(
                *self._standoff_players(session),
                decision="🥱 Никто не вышел на ринг. Бой не состоялся.",
            ),
        )

    def _make_session(
        self,
        chat_id: int,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> DuelSession:
        if (chat_id, thread_id) in self._duel_by_chat:
            raise DuelError("В этой ветке уже идёт бой.")
        for player in (first, second):
            if not player.can_fight():
                raise DuelError(health_warning(player, is_self=False))

        fighter_a = Fighter.from_player(first, armed=mode.armed)
        fighter_b = Fighter.from_player(second, armed=mode.armed)
        session = DuelSession(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            fighters={fighter_a.user_id: fighter_a, fighter_b.user_id: fighter_b},
            order=(fighter_a.user_id, fighter_b.user_id),
            chat_title=chat_title,
            mode=mode,
            players={first.user_id: first, second.user_id: second},
        )
        self._duels[session.id] = session
        self._duel_by_chat[session.key] = session.id
        self._busy[fighter_a.user_id] = "duel"
        self._busy[fighter_b.user_id] = "duel"
        return session

    def _forget(self, session: DuelSession) -> None:
        self._duels.pop(session.id, None)
        self._duel_by_chat.pop(session.key, None)
        for user_id in session.order:
            self._busy.pop(user_id, None)

    async def start_duel(
        self,
        chat_id: int,
        thread_id: int | None,
        first: Player,
        second: Player,
        chat_title: str = "",
        mode: FightMode = FightMode.FIST,
    ) -> DuelSession:
        """Свести бойцов и сразу дать гонг — без стойки."""
        session = self._make_session(chat_id, thread_id, first, second, chat_title, mode)
        session.started = True
        await self._send(
            chat_id,
            thread_id,
            duel_intro(*(session.fighters[uid] for uid in session.order), mode=mode),
        )
        await self._start_round(session)
        return session

    async def _start_round(self, session: DuelSession) -> None:
        session.round_number += 1
        session.choices = {}
        session.resolving = False
        icons, block_width = session.panel
        message = await self._send(
            session.chat_id,
            session.thread_id,
            self._prompt_text(session),
            reply_markup=fight_keyboard(session.id, icons, block_width),
        )
        session.prompt_message_id = message.message_id if message else None
        session.timer = asyncio.create_task(
            self._round_timer(session, session.round_number)
        )

    def _prompt_text(self, session: DuelSession) -> str:
        lines = [f"<b>🔔 Раунд {session.round_number}. Бойцы, выбирайте.</b>", ""]
        for user_id in session.order:
            side = session.fighters[user_id]
            mark = "✅ готов" if session.is_ready(user_id) else "⏳ думает"
            warning = ""
            if side.missed_turns:
                left = MAX_MISSED_TURNS - side.missed_turns
                warning = (
                    f" — ⚠️ пропусков подряд: {side.missed_turns}, осталось {left}"
                )
            lines.append(
                f"{side.fclass.emoji} {mention(side)} "
                f"{hp_bar(side.hp, side.max_hp)} {side.hp}/{side.max_hp} "
                f"— {mark}{warning}"
            )

        lines += [
            "",
            f"⏱️ {self.config.turn_timeout} сек. Выберите удар и блок.",
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
        self, duel_id: int, user_id: int, action: str, zone_value: str, slot: int = 0
    ) -> str:
        """Обработать нажатие бойца. Возвращает текст для приватного ответа."""
        session = self._duels.get(duel_id)
        if session is None:
            raise DuelError("Этот бой уже закончился.")
        if user_id not in session.fighters:
            raise DuelError("Ты не участвуешь в этом бою. Болей за своих.")
        if not session.started:
            raise DuelError("Гонга ещё не было.")

        async with session.lock:
            if session.resolving:
                raise DuelError("Раунд уже считается, поздно.")
            fighter = session.fighters[user_id]
            choice = session.choice_of(user_id)
            was_ready = choice.is_ready(fighter.attacks_per_round)
            if action not in {"attack", "block"}:
                raise DuelError("Непонятное действие.")
            try:
                zone = Zone(zone_value)
            except ValueError as error:  # устаревшая кнопка из прошлой версии
                raise DuelError("Эта кнопка уже не работает.") from error

            if action == "attack":
                if not 0 <= slot < fighter.attacks_per_round:
                    raise DuelError("Такого оружия у тебя нет.")
                choice.attacks[slot] = zone
            else:
                choice.block = block_combo(zone, fighter.block_width)

            now_ready = choice.is_ready(fighter.attacks_per_round)
            both_ready = all(session.is_ready(uid) for uid in session.order)
            if both_ready:
                session.resolving = True

        if both_ready:
            await self._resolve(session)
        elif was_ready != now_ready:
            await self._repaint_prompt(session)

        return f"{choice.describe(fighter)}{self._hint(choice, fighter)}"

    def _hint(self, choice: Choice, fighter: Fighter) -> str:
        missing = [
            fighter.weapon_icons[slot]
            for slot in range(fighter.attacks_per_round)
            if slot not in choice.attacks
        ]
        if missing:
            return "\nОсталось выбрать удар: " + " ".join(missing)
        if not choice.block:
            return "\nОсталось выбрать блок."
        return "\nГотов. Ждём соперника."

    async def _repaint_prompt(self, session: DuelSession) -> None:
        """Обновить панель: изменилась готовность бойцов.

        Правка косметическая: если Telegram упрётся в лимит, её просто
        не будет, а бой поедет дальше.
        """
        icons, block_width = session.panel
        await self._edit(
            session.chat_id,
            session.prompt_message_id,
            self._prompt_text(session),
            reply_markup=fight_keyboard(session.id, icons, block_width),
        )

    async def _resolve(self, session: DuelSession) -> None:
        self._cancel_timer(session)
        first_id, second_id = session.order
        first, second = session.fighters[first_id], session.fighters[second_id]
        actions = {
            user_id: session.choices.get(user_id, Choice()).to_action(
                session.fighters[user_id].attacks_per_round
            )
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
        self._forget(session)

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
            mode=session.mode,
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
        broken: list[tuple[Player, list]] = []
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

            if player.birthplace is None and session.chat_title:
                # Место рождения — группа, где боец впервые вышел на ринг
                player.birthplace = session.chat_title
            # Вещи снашиваются до того, как фиксируем здоровье: развалившаяся
            # экипировка уменьшает запас, и HP надо обрезать по новому потолку.
            # На кулаках вещи остались в раздевалке — снашивать нечего.
            ruined = (
                await wear_after_fight(self.db, player, won, self.rng)
                if session.mode.armed
                else []
            )
            if ruined:
                broken.append((player, ruined))
            player.set_hp(fighter.hp)
            report = player.grant_exp(exp)
            report.credits += credits
            report.rating_delta = delta
            player.grant_credits(credits)
            player.apply_rating(delta)
            await self.db.save_player(player)
            rows.append((player, report))

        return rewards_report(rows, share, previous_fights, broken)

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
        """Отправить сообщение боя, переждав лимит Telegram.

        Ход боя держится на этих сообщениях, поэтому при флуд-контроле ждём
        столько, сколько попросили, и пробуем ещё раз.
        """
        for attempt in range(SEND_ATTEMPTS):
            try:
                return await self.bot.send_message(
                    chat_id, text, message_thread_id=thread_id, **kwargs
                )
            except TelegramRetryAfter as error:
                delay = min(error.retry_after, MAX_FLOOD_WAIT)
                logger.warning(
                    "Telegram просит подождать %s сек (попытка %s)", delay, attempt + 1
                )
                await asyncio.sleep(delay)
            except TelegramBadRequest as error:  # pragma: no cover - битый чат
                logger.warning("Не удалось отправить сообщение: %s", error)
                return None
        logger.error("Сообщение так и не ушло после %s попыток", SEND_ATTEMPTS)
        return None

    async def _edit(self, chat_id: int, message_id: int | None, text: str, **kwargs):
        """Поправить сообщение. Правки косметические — на лимите просто пропускаем."""
        if message_id is None:
            return None
        try:
            return await self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, **kwargs
            )
        except TelegramRetryAfter as error:
            logger.info(
                "Правка пропущена: Telegram просит подождать %s сек", error.retry_after
            )
            return None
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                logger.warning("Не удалось отредактировать сообщение: %s", error)
            return None
