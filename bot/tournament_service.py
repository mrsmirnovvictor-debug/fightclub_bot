"""Турнир: сутки на регистрацию, сетка плей-офф и бои до одного победителя.

Турнир — единственная часть клуба, которая живёт дольше одного вечера, и
поэтому целиком хранится в базе: перезапуск бота её не роняет. В памяти
только таймеры, а они восстанавливаются при старте.

Пары дерутся по очереди в ветке турнира. Перед каждым боем бот лечит обоих
до полного здоровья — турнирный бой должен решаться мастерством, а не тем,
кто раньше успел отлежаться. Проигравший выбывает, ничья переигрывается.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from bot.config import Config
from bot.database import Database
from bot.duel_service import DuelService
from bot.game.bracket import (
    ALLOWED_SIZES,
    MAX_REPLAYS,
    MIN_PLAYERS,
    Match,
    bracket_size,
    first_round,
    next_round,
    round_title,
)
from bot.game.modes import FightMode
from bot.game.narrator import bracket_text, tournament_card, tournament_winner
from bot.keyboards import TourCB, tournament_keyboard
from bot.messaging import Announcer
from bot.models import Player

logger = logging.getLogger(__name__)

# Сколько времени идёт запись, если не сказано иначе
DEFAULT_REGISTRATION = 24 * 60 * 60
# Пауза между боями круга: чтобы ветка не превращалась в стену текста
MATCH_PAUSE = 3


class TournamentError(Exception):
    """Ошибка, которую можно показать игроку как есть."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


@dataclass
class Tournament:
    """Турнир как он лежит в базе, но с разобранными типами."""

    id: int
    chat_id: int
    thread_id: int | None
    title: str
    size: int
    mode: FightMode
    min_level: int
    max_level: int
    state: str
    round: int
    winner_id: int | None
    opens_until: datetime
    message_id: int | None
    chat_title: str

    @classmethod
    def from_row(cls, row: dict) -> "Tournament":
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            thread_id=row["thread_id"],
            title=row["title"],
            size=row["size"],
            mode=FightMode(row["mode"]),
            min_level=row["min_level"],
            max_level=row["max_level"],
            state=row["state"],
            round=row["round"],
            winner_id=row["winner_id"],
            opens_until=_parse(row["opens_until"]),
            message_id=row["message_id"],
            chat_title=row["chat_title"],
        )

    @property
    def seconds_left(self) -> int:
        return max(0, int((self.opens_until - _now()).total_seconds()))


class TournamentService:
    """Запись, сетка и проведение боёв турнира."""

    def __init__(
        self,
        bot: Bot,
        db: Database,
        config: Config,
        duels: DuelService,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.duels = duels
        self.voice = Announcer(bot)
        self._timers: dict[int, asyncio.Task] = {}
        self._running: set[int] = set()  # турниры, где сейчас идёт бой

    # ---------- создание и запись ----------

    async def create(
        self,
        chat_id: int,
        thread_id: int | None,
        opener: Player,
        size: int,
        mode: FightMode = FightMode.ARMED,
        levels: tuple[int, int] | None = None,
        title: str = "",
        chat_title: str = "",
        registration: int | None = None,
    ) -> Tournament:
        if size not in ALLOWED_SIZES:
            raise TournamentError(
                "В турнир зовут " + ", ".join(map(str, ALLOWED_SIZES)) + " бойцов. "
                "Например: /tournament 16"
            )
        if await self.db.live_tournaments(chat_id):
            raise TournamentError(
                "В клубе уже идёт турнир. Дождитесь конца или закройте его: /tourstop"
            )

        low, high = levels or (1, 99)
        if low > high:
            low, high = high, low
        if not low <= opener.level <= high:
            raise TournamentError(
                f"Сам не проходишь по уровню: рамки {low}–{high}, у тебя {opener.level}."
            )

        seconds = registration or self.config.tournament_registration
        tournament_id = await self.db.create_tournament(
            chat_id=chat_id,
            thread_id=thread_id,
            size=size,
            mode=mode,
            min_level=low,
            max_level=high,
            opens_until=_stamp(_now() + timedelta(seconds=seconds)),
            title=title,
            chat_title=chat_title,
        )
        await self.db.add_tournament_player(tournament_id, opener.user_id)

        tournament = await self.load(tournament_id)
        message = await self.voice.send(
            chat_id,
            thread_id,
            await self._card(tournament),
            reply_markup=tournament_keyboard(tournament_id),
        )
        if message:
            await self.db.update_tournament(tournament_id, message_id=message.message_id)
            tournament.message_id = message.message_id
        self._arm(tournament)
        return tournament

    async def load(self, tournament_id: int) -> Tournament:
        row = await self.db.get_tournament(tournament_id)
        if row is None:
            raise TournamentError("Этот турнир уже не найти.")
        return Tournament.from_row(row)

    async def join(self, tournament_id: int, player: Player) -> Tournament:
        tournament = await self.load(tournament_id)
        if tournament.state != "registration":
            raise TournamentError("Запись уже закрыта.")
        players = await self.db.tournament_players(tournament_id)
        if any(row["user_id"] == player.user_id for row in players):
            raise TournamentError("Ты уже в списке.")
        if len(players) >= tournament.size:
            raise TournamentError("Мест больше нет.")
        if not tournament.min_level <= player.level <= tournament.max_level:
            raise TournamentError(
                f"Уровень не тот: пускают {tournament.min_level}–"
                f"{tournament.max_level}, у тебя {player.level}."
            )

        await self.db.add_tournament_player(tournament_id, player.user_id)
        if len(players) + 1 >= tournament.size:
            await self.start(tournament_id)
        else:
            await self._refresh(tournament)
        return tournament

    async def leave(self, tournament_id: int, user_id: int) -> Tournament:
        tournament = await self.load(tournament_id)
        if tournament.state != "registration":
            raise TournamentError("Из начатого турнира не уходят.")
        await self.db.drop_tournament_player(tournament_id, user_id)
        await self._refresh(tournament)
        return tournament

    async def _refresh(self, tournament: Tournament) -> None:
        await self.voice.edit(
            tournament.chat_id,
            tournament.message_id,
            await self._card(tournament),
            reply_markup=tournament_keyboard(tournament.id),
        )

    async def _card(self, tournament: Tournament) -> str:
        players = await self.db.tournament_players(tournament.id)
        names = []
        for row in players:
            player = await self.db.get_player(row["user_id"])
            if player is not None:
                names.append((player.user_id, player.nickname, player.rating))
        return tournament_card(tournament, names)

    def _arm(self, tournament: Tournament) -> None:
        """Поставить будильник на закрытие записи."""
        self._cancel_timer(tournament.id)
        self._timers[tournament.id] = asyncio.create_task(
            self._registration_timer(tournament.id, tournament.seconds_left)
        )

    def _cancel_timer(self, tournament_id: int) -> None:
        task = self._timers.pop(tournament_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _registration_timer(self, tournament_id: int, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:  # pragma: no cover - обычная отмена
            return
        with contextlib.suppress(TournamentError):
            await self.start(tournament_id)

    # ---------- запуск сетки ----------

    async def start(self, tournament_id: int) -> Tournament:
        """Закрыть запись, посеять бойцов и развести первый круг."""
        tournament = await self.load(tournament_id)
        if tournament.state != "registration":
            raise TournamentError("Турнир уже идёт.")
        self._cancel_timer(tournament_id)

        seeded = await self._seed(tournament)
        if len(seeded) < MIN_PLAYERS:
            await self.db.update_tournament(tournament_id, state="cancelled")
            await self.voice.edit(
                tournament.chat_id,
                tournament.message_id,
                "🚫 Турнир отменён: бойцов не набралось.",
            )
            return tournament

        pairs = first_round(seeded)
        await self.db.add_matches(tournament_id, 1, pairs)
        await self.db.update_tournament(tournament_id, state="running", round=1)
        tournament = await self.load(tournament_id)

        await self.voice.edit(
            tournament.chat_id,
            tournament.message_id,
            "🔔 Запись закрыта, сетка составлена.",
        )
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            f"🏆 <b>Турнир начинается.</b> Бойцов: {len(seeded)}, "
            f"сетка на {bracket_size(len(seeded))}.\n\n"
            + await self.bracket(tournament_id),
        )
        await self._advance(tournament_id)
        return tournament

    async def _seed(self, tournament: Tournament) -> list[int]:
        """Посев по рейтингу: сильные разведены по разным углам сетки."""
        rows = await self.db.tournament_players(tournament.id)
        rated: list[tuple[int, int]] = []
        for row in rows:
            player = await self.db.get_player(row["user_id"])
            if player is not None:
                rated.append((player.rating, player.user_id))
        rated.sort(reverse=True)
        seeded = [user_id for _, user_id in rated]
        await self.db.set_tournament_seeds(
            tournament.id, {user_id: index + 1 for index, user_id in enumerate(seeded)}
        )
        return seeded

    # ---------- ход турнира ----------

    async def _advance(self, tournament_id: int) -> None:
        """Провести следующую пару круга или перейти к следующему кругу."""
        if tournament_id in self._running:
            return
        tournament = await self.load(tournament_id)
        if tournament.state != "running":
            return

        matches = [
            Match(
                round=row["round"],
                slot=row["slot"],
                first_id=row["first_id"],
                second_id=row["second_id"],
                winner_id=row["winner_id"],
                replays=row["replays"],
            )
            for row in await self.db.tournament_matches(tournament_id, tournament.round)
        ]
        rows = await self.db.tournament_matches(tournament_id, tournament.round)

        for row, match in zip(rows, matches):
            if match.is_done:
                continue
            if match.is_empty:
                await self.db.update_match(row["id"], state="done")
                continue
            if match.is_bye:
                await self.db.update_match(
                    row["id"], winner_id=match.bye_winner, state="done"
                )
                await self._announce_bye(tournament, match)
                continue
            await self._run_match(tournament, row, match)
            return

        await self._finish_round(tournament)

    async def _announce_bye(self, tournament: Tournament, match: Match) -> None:
        player = await self.db.get_player(match.bye_winner)
        if player is None:  # pragma: no cover - персонажа удалили
            return
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            f"🎟 <b>{player.nickname}</b> проходит дальше без боя: соперника нет.",
        )

    async def _run_match(self, tournament: Tournament, row: dict, match: Match) -> None:
        """Вылечить обоих и дать гонг."""
        first = await self.db.get_player(match.first_id)
        second = await self.db.get_player(match.second_id)
        for player, rival in ((first, second), (second, first)):
            if player is None:
                winner = rival.user_id if rival else None
                await self.db.update_match(row["id"], winner_id=winner, state="done")
                await self._advance(tournament.id)
                return

        for player in (first, second):
            player.heal_full()
            await self.db.save_player(player)

        self._running.add(tournament.id)
        await self.db.update_match(row["id"], state="running")
        replay = " (переигровка)" if match.replays else ""
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            f"🥊 <b>{round_title(len(await self.db.tournament_matches(tournament.id, tournament.round)))}"
            f"{replay}</b>: {first.nickname} — {second.nickname}. "
            "Оба вылечены до полного здоровья.",
        )
        try:
            await self.duels.start_duel(
                tournament.chat_id,
                tournament.thread_id,
                first,
                second,
                chat_title=tournament.chat_title,
                mode=tournament.mode,
                on_finish=self._make_handler(tournament.id, row["id"]),
            )
        except Exception:
            self._running.discard(tournament.id)
            logger.exception("Не удалось начать турнирный бой")
            await self.db.update_match(row["id"], state="pending")

    def _make_handler(self, tournament_id: int, match_id: int):
        async def handler(session, result) -> None:
            await self._on_duel_finished(tournament_id, match_id, result)

        return handler

    async def _on_duel_finished(
        self, tournament_id: int, match_id: int, result
    ) -> None:
        self._running.discard(tournament_id)
        rows = await self.db.tournament_matches(tournament_id)
        row = next((item for item in rows if item["id"] == match_id), None)
        if row is None:  # pragma: no cover - матч удалили
            return
        tournament = await self.load(tournament_id)

        if result.winner_id is not None:
            await self.db.update_match(match_id, winner_id=result.winner_id, state="done")
            loser = (
                row["second_id"]
                if result.winner_id == row["first_id"]
                else row["first_id"]
            )
            await self.db.knock_out(tournament_id, loser, tournament.round)
        else:
            await self._handle_draw(tournament, row)

        await asyncio.sleep(MATCH_PAUSE)
        await self._advance(tournament_id)

    async def _handle_draw(self, tournament: Tournament, row: dict) -> None:
        """Ничья — переигровка. Если и она не решает, судья смотрит на рейтинг."""
        replays = row["replays"] + 1
        if replays <= MAX_REPLAYS:
            await self.db.update_match(row["id"], replays=replays, state="pending")
            await self.voice.send(
                tournament.chat_id,
                tournament.thread_id,
                f"🤝 Ничья. Судья назначает повторный бой ({replays} из {MAX_REPLAYS}).",
            )
            return

        first = await self.db.get_player(row["first_id"])
        second = await self.db.get_player(row["second_id"])
        winner, loser = (first, second)
        if second and first and second.rating > first.rating:
            winner, loser = (second, first)
        await self.db.update_match(row["id"], winner_id=winner.user_id, state="done")
        await self.db.knock_out(tournament.id, loser.user_id, tournament.round)
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            f"🤝 Снова ничья. По рейтингу дальше идёт <b>{winner.nickname}</b>.",
        )

    async def _finish_round(self, tournament: Tournament) -> None:
        """Круг сыгран: собрать победителей и развести следующий."""
        rows = await self.db.tournament_matches(tournament.id, tournament.round)
        winners = [row["winner_id"] for row in rows]
        alive = [user_id for user_id in winners if user_id is not None]

        if len(alive) <= 1:
            await self._crown(tournament, alive[0] if alive else None)
            return

        pairs = next_round(winners)
        round_number = tournament.round + 1
        await self.db.add_matches(tournament.id, round_number, pairs)
        await self.db.update_tournament(tournament.id, round=round_number)
        tournament = await self.load(tournament.id)
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            f"➡️ <b>{round_title(len(pairs))}.</b>\n\n"
            + await self.bracket(tournament.id),
        )
        await self._advance(tournament.id)

    async def _crown(self, tournament: Tournament, winner_id: int | None) -> None:
        await self.db.update_tournament(
            tournament.id, state="finished", winner_id=winner_id
        )
        winner = await self.db.get_player(winner_id) if winner_id else None
        await self.voice.send(
            tournament.chat_id,
            tournament.thread_id,
            tournament_winner(tournament, winner)
            + "\n\n"
            + await self.bracket(tournament.id),
        )

    # ---------- сетка и остановка ----------

    async def bracket(self, tournament_id: int) -> str:
        tournament = await self.load(tournament_id)
        rows = await self.db.tournament_matches(tournament_id)
        names: dict[int, str] = {}
        for row in await self.db.tournament_players(tournament_id):
            player = await self.db.get_player(row["user_id"])
            if player is not None:
                names[player.user_id] = player.nickname
        return bracket_text(tournament, rows, names)

    async def stop(self, tournament_id: int) -> None:
        """Снять турнир: и запись, и недоигранную сетку."""
        tournament = await self.load(tournament_id)
        self._cancel_timer(tournament_id)
        self._running.discard(tournament_id)
        await self.db.update_tournament(tournament_id, state="cancelled")
        await self.voice.send(
            tournament.chat_id, tournament.thread_id, "🚫 Турнир остановлен."
        )

    async def resume(self) -> None:
        """После перезапуска: поднять таймеры записи и доиграть начатое."""
        for row in await self.db.live_tournaments():
            tournament = Tournament.from_row(row)
            if tournament.state == "registration":
                self._arm(tournament)
                continue
            # Бой, который шёл в момент перезапуска, начинаем заново
            for match in await self.db.tournament_matches(
                tournament.id, tournament.round
            ):
                if match["state"] == "running":
                    await self.db.update_match(match["id"], state="pending")
            await self._advance(tournament.id)

    async def shutdown(self) -> None:
        for task in list(self._timers.values()):
            if not task.done():
                task.cancel()
        for task in list(self._timers.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._timers.clear()


__all__ = ["TourCB", "Tournament", "TournamentError", "TournamentService"]
