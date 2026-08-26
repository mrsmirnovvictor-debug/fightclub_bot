"""Хранилище на SQLite: бойцы, арены и история дуэлей."""

from __future__ import annotations

import logging
from typing import Any, Iterable

import aiosqlite

from bot.game.economy import RATING_START
from bot.models import Arena, Player

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    user_id        INTEGER PRIMARY KEY,
    nickname       TEXT    NOT NULL,
    class_code     TEXT    NOT NULL,
    avatar         TEXT    NOT NULL DEFAULT '🥊',
    avatar_file_id TEXT,
    strength       INTEGER NOT NULL DEFAULT 0,
    agility        INTEGER NOT NULL DEFAULT 0,
    intuition      INTEGER NOT NULL DEFAULT 0,
    endurance      INTEGER NOT NULL DEFAULT 0,
    free_points    INTEGER NOT NULL DEFAULT 0,
    level          INTEGER NOT NULL DEFAULT 1,
    exp            INTEGER NOT NULL DEFAULT 0,
    total_exp      INTEGER NOT NULL DEFAULT 0,
    micro_ups      INTEGER NOT NULL DEFAULT 0,
    credits        INTEGER NOT NULL DEFAULT 0,
    rating         INTEGER NOT NULL DEFAULT 1000,
    hp             INTEGER,
    hp_at          INTEGER NOT NULL DEFAULT 0,
    wins           INTEGER NOT NULL DEFAULT 0,
    losses         INTEGER NOT NULL DEFAULT 0,
    draws          INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS arenas (
    chat_id   INTEGER PRIMARY KEY,
    thread_id INTEGER,
    title     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS duels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    thread_id     INTEGER,
    challenger_id INTEGER NOT NULL,
    opponent_id   INTEGER NOT NULL,
    winner_id     INTEGER,
    rounds        INTEGER NOT NULL DEFAULT 0,
    end_reason    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_duels_chat ON duels(chat_id, created_at DESC);
"""

PLAYER_COLUMNS = (
    "user_id, nickname, class_code, avatar, avatar_file_id, strength, agility, "
    "intuition, endurance, free_points, level, exp, total_exp, micro_ups, "
    "credits, rating, hp, hp_at, wins, losses, draws"
)

# Колонки, добавленные после первой версии: их дописываем в уже живые базы.
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("total_exp", "INTEGER NOT NULL DEFAULT 0"),
    ("micro_ups", "INTEGER NOT NULL DEFAULT 0"),
    ("credits", "INTEGER NOT NULL DEFAULT 0"),
    ("rating", f"INTEGER NOT NULL DEFAULT {RATING_START}"),
    ("hp", "INTEGER"),  # NULL — боец здоров
    ("hp_at", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Дописать колонки, которых нет в базе, созданной прошлой версией."""
        async with self.conn.execute("PRAGMA table_info(players)") as cursor:
            existing = {row["name"] for row in await cursor.fetchall()}
        for column, definition in MIGRATIONS:
            if column not in existing:
                await self.conn.execute(
                    f"ALTER TABLE players ADD COLUMN {column} {definition}"
                )
                logger.info("База обновлена: добавлена колонка players.%s", column)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:  # pragma: no cover - защита от неверного порядка вызовов
            raise RuntimeError("База не подключена: сначала вызови connect()")
        return self._conn

    # ---------- бойцы ----------

    async def get_player(self, user_id: int) -> Player | None:
        async with self.conn.execute(
            f"SELECT {PLAYER_COLUMNS} FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _to_player(row) if row else None

    async def save_player(self, player: Player) -> None:
        await self.conn.execute(
            """
            INSERT INTO players (
                user_id, nickname, class_code, avatar, avatar_file_id, strength,
                agility, intuition, endurance, free_points, level, exp,
                total_exp, micro_ups, credits, rating, hp, hp_at,
                wins, losses, draws
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                nickname       = excluded.nickname,
                class_code     = excluded.class_code,
                avatar         = excluded.avatar,
                avatar_file_id = excluded.avatar_file_id,
                strength       = excluded.strength,
                agility        = excluded.agility,
                intuition      = excluded.intuition,
                endurance      = excluded.endurance,
                free_points    = excluded.free_points,
                level          = excluded.level,
                exp            = excluded.exp,
                total_exp      = excluded.total_exp,
                micro_ups      = excluded.micro_ups,
                credits        = excluded.credits,
                rating         = excluded.rating,
                hp             = excluded.hp,
                hp_at          = excluded.hp_at,
                wins           = excluded.wins,
                losses         = excluded.losses,
                draws          = excluded.draws
            """,
            (
                player.user_id,
                player.nickname,
                player.class_code,
                player.avatar,
                player.avatar_file_id,
                player.strength,
                player.agility,
                player.intuition,
                player.endurance,
                player.free_points,
                player.level,
                player.exp,
                player.total_exp,
                player.micro_ups,
                player.credits,
                player.rating,
                player.hp,
                player.hp_at,
                player.wins,
                player.losses,
                player.draws,
            ),
        )
        await self.conn.commit()

    async def delete_player(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def top_players(self, limit: int = 10) -> list[Player]:
        async with self.conn.execute(
            f"""
            SELECT {PLAYER_COLUMNS} FROM players
            ORDER BY rating DESC, wins DESC, level DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_to_player(row) for row in rows]

    # ---------- арены ----------

    async def set_arena(self, chat_id: int, thread_id: int | None, title: str = "") -> None:
        await self.conn.execute(
            """
            INSERT INTO arenas (chat_id, thread_id, title) VALUES (?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                title     = excluded.title
            """,
            (chat_id, thread_id, title),
        )
        await self.conn.commit()

    async def get_arena(self, chat_id: int) -> Arena | None:
        async with self.conn.execute(
            "SELECT chat_id, thread_id, title FROM arenas WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return Arena(chat_id=row["chat_id"], thread_id=row["thread_id"], title=row["title"])

    # ---------- дуэли ----------

    async def add_duel(
        self,
        chat_id: int,
        thread_id: int | None,
        challenger_id: int,
        opponent_id: int,
        winner_id: int | None,
        rounds: int,
        end_reason: str | None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO duels (
                chat_id, thread_id, challenger_id, opponent_id,
                winner_id, rounds, end_reason
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (chat_id, thread_id, challenger_id, opponent_id, winner_id, rounds, end_reason),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def count_recent_duels_between(
        self, first_id: int, second_id: int, hours: int = 24
    ) -> int:
        """Сколько боёв эта пара уже провела за последние часы — для антифарма."""
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS fights FROM duels
            WHERE ((challenger_id = ? AND opponent_id = ?)
                OR (challenger_id = ? AND opponent_id = ?))
              AND created_at >= datetime('now', ?)
            """,
            (first_id, second_id, second_id, first_id, f"-{int(hours)} hours"),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["fights"]) if row else 0

    async def recent_duels(self, chat_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """
            SELECT challenger_id, opponent_id, winner_id, rounds, end_reason, created_at
            FROM duels WHERE chat_id = ? ORDER BY id DESC LIMIT ?
            """,
            (chat_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def _to_player(row: Iterable[Any]) -> Player:
    data = dict(row)  # type: ignore[arg-type]
    return Player(**data)
