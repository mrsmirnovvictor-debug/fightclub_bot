"""Бои, где дерутся больше двух: команда на команду и королевская битва.

Сначала лобби: бойцы записываются кнопками, пока не наберётся состав или не
выйдет время. Потом бой раундами — на каждом ходу бойцы разбиты по парам, и
каждая пара считается тем же движком, что и обычная дуэль. Кто упал, тот
больше не жмёт кнопки; остальные продолжают, пока не останется одна сторона.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import random
from dataclasses import dataclass, field

from aiogram import Bot

from bot.config import Config
from bot.database import Database
from bot.game.battle import (
    BLUE,
    MAX_BATTLE_ROUNDS,
    MAX_ROYALE,
    MAX_TEAM_SIZE,
    MIN_ROYALE,
    MIN_TEAM_SIZE,
    RED,
    BattleKind,
    BattleOutcome,
    judge,
    pair_up,
    team_name,
)
from bot.game.classes import BLOCK_WIDTH, Zone, block_combo, block_title
from bot.game.combat import Action, Fighter, resolve_round
from bot.game.economy import (
    rating_delta,
    win_exp,
)
from bot.game.equipment import BARE_HANDS_ICON
from bot.game.modes import FightMode
from bot.game.narrator import (
    battle_intro,
    battle_result,
    battle_rewards_report,
    battle_round_report,
    esc,
    health_warning,
    hp_bar,
    lobby_card,
    mention,
)
from bot.inventory_service import wear_after_fight
from bot.keyboards import BattleCB, LobbyCB, battle_keyboard, lobby_keyboard
from bot.messaging import Announcer
from bot.models import Player, ProgressReport

logger = logging.getLogger(__name__)

ChatKey = tuple[int, int | None]

# На сколько уровней вверх и вниз пускают в бой, если рамки не задали
DEFAULT_LEVEL_SPREAD = 2


class BattleError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


@dataclass
class Choice:
    """Незавершённый выбор бойца на раунд."""

    attacks: dict[int, Zone] = field(default_factory=dict)
    block: tuple[Zone, ...] = ()

    def is_ready(self, weapons: int) -> bool:
        chosen = [slot for slot in range(weapons) if slot in self.attacks]
        return len(chosen) == weapons and bool(self.block)

    def to_action(self, weapons: int) -> Action:
        return Action(
            attacks=tuple(self.attacks.get(slot) for slot in range(weapons)),
            block=self.block,
        )

    @property
    def is_empty(self) -> bool:
        return not self.attacks and not self.block


@dataclass
class Lobby:
    """Сбор состава: кто записался и за какую сторону."""

    id: int
    chat_id: int
    thread_id: int | None
    kind: BattleKind
    mode: FightMode
    size: int  # сколько бойцов в стороне (команда) или всего (мясорубка)
    min_level: int
    max_level: int
    opener_id: int
    chat_title: str = ""
    members: dict[int, int] = field(default_factory=dict)  # боец → сторона
    names: dict[int, str] = field(default_factory=dict)
    message_id: int | None = None
    task: asyncio.Task | None = None

    @property
    def total(self) -> int:
        return len(self.members)

    @property
    def capacity(self) -> int:
        return self.size * 2 if self.kind is BattleKind.TEAM else self.size

    def side(self, team: int) -> list[int]:
        return [user_id for user_id, value in self.members.items() if value == team]

    @property
    def is_full(self) -> bool:
        if self.kind is BattleKind.TEAM:
            return len(self.side(RED)) == self.size and len(self.side(BLUE)) == self.size
        return self.total >= self.size

    @property
    def can_start(self) -> bool:
        """Состав, при котором бой имеет смысл, даже если время вышло."""
        if self.kind is BattleKind.TEAM:
            return bool(self.side(RED)) and bool(self.side(BLUE))
        return self.total >= 2

    def fits(self, player: Player) -> bool:
        return self.min_level <= player.level <= self.max_level


@dataclass
class BattleSession:
    """Идущий бой: бойцы, стороны, пары этого раунда."""

    id: int
    chat_id: int
    thread_id: int | None
    kind: BattleKind
    mode: FightMode
    fighters: dict[int, Fighter]
    teams: dict[int, int]
    chat_title: str = ""
    round_number: int = 0
    pairs: list[tuple[int, int]] = field(default_factory=list)
    idle: list[int] = field(default_factory=list)
    choices: dict[int, Choice] = field(default_factory=dict)
    prompt_message_id: int | None = None
    timer: asyncio.Task | None = None
    resolving: bool = False
    # О ком судья уже сказал: чтобы не повторять одно и то же каждый раунд
    announced_out: set[int] = field(default_factory=set)
    announced_idle: tuple[int, ...] = ()

    @property
    def order(self) -> list[int]:
        return list(self.fighters)

    def opponent_of(self, user_id: int) -> int | None:
        for first, second in self.pairs:
            if first == user_id:
                return second
            if second == user_id:
                return first
        return None

    def is_fighting(self, user_id: int) -> bool:
        return self.opponent_of(user_id) is not None

    def is_ready(self, user_id: int) -> bool:
        choice = self.choices.get(user_id)
        fighter = self.fighters[user_id]
        return bool(choice and choice.is_ready(fighter.attacks_per_round))

    @property
    def everyone_ready(self) -> bool:
        """Все, кто в этом ходу дерётся, уже нажали. Упавших не ждём."""
        return all(
            self.is_ready(user_id)
            for pair in self.pairs
            for user_id in pair
            if self.fighters[user_id].alive
        )

    @property
    def panel(self) -> tuple[tuple[str, ...], int]:
        """Одна панель на всех: общие значки и общая ширина блока."""
        sides = [self.fighters[user_id] for user_id in self.fighters]
        icons = sides[0].weapon_icons
        if any(side.weapon_icons != icons for side in sides):
            icons = (BARE_HANDS_ICON,) * max(s.attacks_per_round for s in sides)
        width = sides[0].block_width
        if any(side.block_width != width for side in sides):
            width = BLOCK_WIDTH
        return icons, width


class BattleService:
    """Лобби и бои на много бойцов."""

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
        self.voice = Announcer(bot)
        self.rng = rng or random.Random()
        self._ids = itertools.count(1)
        self._lobbies: dict[int, Lobby] = {}
        self._battles: dict[int, BattleSession] = {}
        self._by_chat: dict[ChatKey, int] = {}  # ветка → лобби или бой
        self._busy: dict[int, str] = {}  # боец → «lobby» или «battle»

    # ---------- лобби ----------

    async def open_lobby(
        self,
        chat_id: int,
        thread_id: int | None,
        opener: Player,
        kind: BattleKind,
        size: int,
        mode: FightMode = FightMode.ARMED,
        levels: tuple[int, int] | None = None,
        chat_title: str = "",
    ) -> Lobby:
        if (chat_id, thread_id) in self._by_chat:
            raise BattleError("В этой ветке уже собирают бой или дерутся.")
        if self._busy.get(opener.user_id):
            raise BattleError("Ты уже записан в бой.")
        if not opener.can_fight():
            raise BattleError(health_warning(opener))

        size = self._check_size(kind, size)
        low, high = levels or (
            max(1, opener.level - DEFAULT_LEVEL_SPREAD),
            opener.level + DEFAULT_LEVEL_SPREAD,
        )
        if low > high:
            low, high = high, low
        if not low <= opener.level <= high:
            raise BattleError(
                f"Сам не проходишь по уровню: рамки {low}–{high}, у тебя "
                f"{opener.level}."
            )

        lobby = Lobby(
            id=next(self._ids),
            chat_id=chat_id,
            thread_id=thread_id,
            kind=kind,
            mode=mode,
            size=size,
            min_level=low,
            max_level=high,
            opener_id=opener.user_id,
            chat_title=chat_title,
        )
        lobby.members[opener.user_id] = RED
        lobby.names[opener.user_id] = opener.nickname
        self._lobbies[lobby.id] = lobby
        self._by_chat[(chat_id, thread_id)] = lobby.id
        self._busy[opener.user_id] = "lobby"

        message = await self.voice.send(
            chat_id,
            thread_id,
            lobby_card(lobby, self.config.lobby_timeout),
            reply_markup=lobby_keyboard(lobby),
        )
        lobby.message_id = message.message_id if message else None
        lobby.task = asyncio.create_task(self._lobby_timer(lobby))
        return lobby

    def _check_size(self, kind: BattleKind, size: int) -> int:
        if kind is BattleKind.TEAM:
            if not MIN_TEAM_SIZE <= size <= MAX_TEAM_SIZE:
                raise BattleError(
                    f"Команда — от {MIN_TEAM_SIZE} до {MAX_TEAM_SIZE} бойцов. "
                    "Например: /battle 3"
                )
        elif not MIN_ROYALE <= size <= MAX_ROYALE:
            raise BattleError(
                f"В королевскую битву зовут от {MIN_ROYALE} до {MAX_ROYALE} бойцов. "
                "Например: /royale 6"
            )
        return size

    async def join(self, lobby_id: int, player: Player, team: int = RED) -> Lobby:
        lobby = self._lobbies.get(lobby_id)
        if lobby is None:
            raise BattleError("Этот сбор уже закрыт.")
        if player.user_id in lobby.members:
            raise BattleError("Ты уже записан.")
        if self._busy.get(player.user_id):
            raise BattleError("Ты уже записан в другой бой.")
        if not player.can_fight():
            raise BattleError(health_warning(player))
        if not lobby.fits(player):
            raise BattleError(
                f"Уровень не тот: пускают {lobby.min_level}–{lobby.max_level}, "
                f"у тебя {player.level}."
            )
        if lobby.kind is BattleKind.TEAM:
            if len(lobby.side(team)) >= lobby.size:
                raise BattleError(f"{team_name(team)} уже в полном составе.")
        elif lobby.total >= lobby.size:
            raise BattleError("Мест больше нет.")

        lobby.members[player.user_id] = team if lobby.kind is BattleKind.TEAM else RED
        lobby.names[player.user_id] = player.nickname
        self._busy[player.user_id] = "lobby"

        if lobby.is_full:
            await self._start_from_lobby(lobby)
        else:
            await self._refresh_lobby(lobby)
        return lobby

    async def leave(self, lobby_id: int, user_id: int) -> Lobby:
        lobby = self._lobbies.get(lobby_id)
        if lobby is None:
            raise BattleError("Этот сбор уже закрыт.")
        if user_id not in lobby.members:
            raise BattleError("Тебя и так нет в составе.")
        lobby.members.pop(user_id, None)
        self._busy.pop(user_id, None)
        if not lobby.members:
            await self._cancel_lobby(lobby, "Все разошлись — сбор закрыт.")
        else:
            await self._refresh_lobby(lobby)
        return lobby

    async def _refresh_lobby(self, lobby: Lobby) -> None:
        await self.voice.edit(
            lobby.chat_id,
            lobby.message_id,
            lobby_card(lobby, self.config.lobby_timeout),
            reply_markup=lobby_keyboard(lobby),
        )

    async def _lobby_timer(self, lobby: Lobby) -> None:
        try:
            await asyncio.sleep(self.config.lobby_timeout)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        if lobby.id not in self._lobbies:
            return
        if lobby.can_start:
            await self._start_from_lobby(lobby)
        else:
            await self._cancel_lobby(lobby, "Состав не собрался — бой отменён.")

    async def _cancel_lobby(self, lobby: Lobby, why: str) -> None:
        self._forget_lobby(lobby)
        await self.voice.edit(lobby.chat_id, lobby.message_id, f"🚫 {why}")

    def _forget_lobby(self, lobby: Lobby) -> None:
        self._lobbies.pop(lobby.id, None)
        if self._by_chat.get((lobby.chat_id, lobby.thread_id)) == lobby.id:
            self._by_chat.pop((lobby.chat_id, lobby.thread_id), None)
        for user_id in lobby.members:
            if self._busy.get(user_id) == "lobby":
                self._busy.pop(user_id, None)
        if lobby.task and not lobby.task.done():
            if lobby.task is not asyncio.current_task():
                lobby.task.cancel()

    # ---------- бой ----------

    async def _start_from_lobby(self, lobby: Lobby) -> BattleSession | None:
        members = dict(lobby.members)
        self._forget_lobby(lobby)

        players: dict[int, Player] = {}
        for user_id in members:
            player = await self.db.get_player(user_id)
            if player is not None and player.can_fight():
                players[user_id] = player
        teams = {user_id: members[user_id] for user_id in players}

        if not self._enough(lobby.kind, teams):
            await self.voice.edit(
                lobby.chat_id, lobby.message_id, "🚫 Бойцы разбежались — бой отменён."
            )
            return None

        session = BattleSession(
            id=next(self._ids),
            chat_id=lobby.chat_id,
            thread_id=lobby.thread_id,
            kind=lobby.kind,
            mode=lobby.mode,
            fighters={
                user_id: Fighter.from_player(player, armed=lobby.mode.armed)
                for user_id, player in players.items()
            },
            teams=teams,
            chat_title=lobby.chat_title,
        )
        self._battles[session.id] = session
        self._by_chat[(session.chat_id, session.thread_id)] = session.id
        for user_id in session.fighters:
            self._busy[user_id] = "battle"

        await self.voice.edit(
            lobby.chat_id, lobby.message_id, "🔔 Состав собран, бой начинается."
        )
        await self.voice.send(session.chat_id, session.thread_id, battle_intro(session))
        await self._start_round(session)
        return session

    @staticmethod
    def _enough(kind: BattleKind, teams: dict[int, int]) -> bool:
        if kind is BattleKind.TEAM:
            sides = set(teams.values())
            return RED in sides and BLUE in sides
        return len(teams) >= 2

    async def _start_round(self, session: BattleSession) -> None:
        session.round_number += 1
        session.choices = {}
        session.pairs, session.idle = pair_up(
            session.fighters, session.teams, session.kind, session.round_number
        )
        if not session.pairs:  # pragma: no cover - судья закрывает бой раньше
            await self._finish(session, judge(
                session.fighters, session.teams, session.kind, MAX_BATTLE_ROUNDS
            ))
            return

        icons, width = session.panel
        text = self._prompt_text(session)
        session.announced_idle = tuple(session.idle)
        message = await self.voice.send(
            session.chat_id,
            session.thread_id,
            text,
            reply_markup=battle_keyboard(session.id, icons, width),
        )
        session.prompt_message_id = message.message_id if message else None
        session.timer = asyncio.create_task(
            self._round_timer(session, session.round_number)
        )

    def _prompt_text(self, session: BattleSession) -> str:
        lines = [f"<b>🔔 Раунд {session.round_number}. Пары этого хода:</b>", ""]
        for first, second in session.pairs:
            lines.append(
                f"{self._fighter_line(session, first)}  ⚔️  "
                f"{self._fighter_line(session, second)}"
            )
        if session.idle and tuple(session.idle) != session.announced_idle:
            names = ", ".join(
                esc(session.fighters[user_id].name) for user_id in session.idle
            )
            lines.append("")
            lines.append(
                f"😐 Без пары в этом ходу: {names} — ход пропускается без потерь."
            )
        lines += ["", f"⏱️ {self.config.turn_timeout} сек. Выберите удар и блок."]
        return "\n".join(lines)

    def _fighter_line(self, session: BattleSession, user_id: int) -> str:
        fighter = session.fighters[user_id]
        mark = "✅" if session.is_ready(user_id) else "⏳"
        return (
            f"{mark} {fighter.fclass.emoji} {mention(fighter)} "
            f"{hp_bar(fighter.hp, fighter.max_hp)} {fighter.hp}"
        )

    async def _round_timer(self, session: BattleSession, round_number: int) -> None:
        try:
            await asyncio.sleep(self.config.turn_timeout)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        if session.id in self._battles and session.round_number == round_number:
            await self._resolve(session)

    async def handle_choice(
        self, battle_id: int, user_id: int, action: str, zone: str, slot: int = 0
    ) -> str:
        session = self._battles.get(battle_id)
        if session is None:
            raise BattleError("Этот бой уже закончился.")
        fighter = session.fighters.get(user_id)
        if fighter is None:
            raise BattleError("Ты не в этом бою.")
        if not fighter.alive:
            raise BattleError("Ты уже вне боя — смотри со стороны.")
        if not session.is_fighting(user_id):
            raise BattleError("В этом ходу тебе не досталось соперника.")
        if session.resolving:
            raise BattleError("Раунд уже считается.")

        choice = session.choices.setdefault(user_id, Choice())
        if action == "attack":
            choice.attacks[slot] = Zone(zone)
        else:
            choice.block = block_combo(Zone(zone), fighter.block_width)

        await self.voice.edit(
            session.chat_id,
            session.prompt_message_id,
            self._prompt_text(session),
            reply_markup=battle_keyboard(session.id, *session.panel),
        )
        if session.everyone_ready:
            await self._resolve(session)
        return self._hint(session, user_id)

    def _hint(self, session: BattleSession, user_id: int) -> str:
        choice = session.choices.get(user_id, Choice())
        fighter = session.fighters[user_id]
        parts = []
        for slot, icon in enumerate(fighter.weapon_icons):
            zone = choice.attacks.get(slot)
            parts.append(f"{icon} {zone.title if zone else '—'}")
        block = block_title(choice.block) if choice.block else "—"
        return "   ".join(parts) + f"\n🛡 {block}"

    async def _resolve(self, session: BattleSession) -> None:
        if session.resolving:
            return
        session.resolving = True
        self._cancel_timer(session)
        try:
            await self._play_round(session)
        finally:
            session.resolving = False

    async def _play_round(self, session: BattleSession) -> None:
        standing = {
            user_id for user_id, fighter in session.fighters.items() if fighter.alive
        }
        results = []
        for first_id, second_id in session.pairs:
            first, second = session.fighters[first_id], session.fighters[second_id]
            results.append(
                resolve_round(
                    first,
                    self._action_of(session, first_id),
                    second,
                    self._action_of(session, second_id),
                    session.round_number,
                    self.rng,
                )
            )

        await self.voice.edit(
            session.chat_id,
            session.prompt_message_id,
            f"🔒 Раунд {session.round_number}: ставки сделаны, судья считает.",
        )
        fallen = [
            user_id
            for user_id in standing
            if not session.fighters[user_id].alive
            and user_id not in session.announced_out
        ]
        session.announced_out.update(fallen)
        await self.voice.send(
            session.chat_id,
            session.thread_id,
            battle_round_report(session, results, fallen, self.rng),
        )

        outcome = judge(
            session.fighters, session.teams, session.kind, session.round_number
        )
        if outcome.finished:
            await self._finish(session, outcome)
        else:
            await self._start_round(session)

    def _action_of(self, session: BattleSession, user_id: int) -> Action:
        weapons = session.fighters[user_id].attacks_per_round
        return session.choices.get(user_id, Choice()).to_action(weapons)

    async def _finish(self, session: BattleSession, outcome: BattleOutcome) -> None:
        self._cancel_timer(session)
        await self.voice.edit(
            session.chat_id,
            session.prompt_message_id,
            f"🔒 Раунд {session.round_number}: бой окончен.",
        )
        self._forget_battle(session)

        rewards = await self._apply_results(session, outcome)
        text = battle_result(session, outcome)
        if rewards:
            text += "\n\n" + rewards
        await self.voice.send(session.chat_id, session.thread_id, text)
        await self.db.add_battle(session, outcome)

    async def _apply_results(
        self, session: BattleSession, outcome: BattleOutcome
    ) -> str:
        players: dict[int, Player] = {}
        for user_id in session.fighters:
            player = await self.db.get_player(user_id)
            if player is not None:
                players[user_id] = player
        if not players:  # pragma: no cover - персонажей удалили по ходу боя
            return ""

        levels = {
            user_id: session.fighters[user_id].level for user_id in session.fighters
        }
        rows: list[tuple[Player, ProgressReport, Fighter, bool]] = []
        broken: list[tuple[Player, list]] = []

        for user_id, player in players.items():
            fighter = session.fighters[user_id]
            rivals = [other for other in levels if other != user_id]
            rival_level = round(sum(levels[o] for o in rivals) / max(1, len(rivals)))
            won = user_id in outcome.winners

            if won:
                player.wins += 1
                exp = win_exp(fighter.damage_dealt, levels[user_id], rival_level)
            elif outcome.draw:
                player.draws += 1
                exp = 0
            else:
                player.losses += 1
                exp = 0

            delta = rating_delta(won, levels[user_id], rival_level)
            if player.birthplace is None and session.chat_title:
                player.birthplace = session.chat_title
            ruined = (
                await wear_after_fight(self.db, player, won, self.rng)
                if session.mode.armed
                else []
            )
            if ruined:
                broken.append((player, ruined))
            player.set_hp(fighter.hp)
            report = player.grant_exp(exp)
            report.rating_delta = delta
            player.apply_rating(delta)
            await self.db.save_player(player)
            rows.append((player, report, fighter, won))

        return battle_rewards_report(rows, broken=broken)

    def _cancel_timer(self, session: BattleSession) -> None:
        timer = session.timer
        session.timer = None
        if timer and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    def _forget_battle(self, session: BattleSession) -> None:
        self._battles.pop(session.id, None)
        if self._by_chat.get((session.chat_id, session.thread_id)) == session.id:
            self._by_chat.pop((session.chat_id, session.thread_id), None)
        for user_id in session.fighters:
            if self._busy.get(user_id) == "battle":
                self._busy.pop(user_id, None)

    # ---------- состояние ----------

    def lobby_in_chat(self, chat_id: int, thread_id: int | None) -> Lobby | None:
        room_id = self._by_chat.get((chat_id, thread_id))
        return self._lobbies.get(room_id) if room_id else None

    def battle_in_chat(self, chat_id: int, thread_id: int | None) -> BattleSession | None:
        room_id = self._by_chat.get((chat_id, thread_id))
        return self._battles.get(room_id) if room_id else None

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._busy

    async def shutdown(self) -> None:
        """Погасить таймеры при остановке бота."""
        tasks = [lobby.task for lobby in self._lobbies.values() if lobby.task]
        tasks += [battle.timer for battle in self._battles.values() if battle.timer]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


__all__ = [
    "BattleError",
    "BattleKind",
    "BattleService",
    "BattleSession",
    "Lobby",
    "LobbyCB",
    "BattleCB",
]
