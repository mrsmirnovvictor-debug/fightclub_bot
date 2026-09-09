"""Хранилище на SQLite: бойцы, арены и история дуэлей."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from bot.game.economy import RATING_START
from bot.game.equipment import MAX_WEAR, OwnedItem, Slot, get_item
from bot.game.health import now_ts
from bot.game.modes import FightMode, mode_of
from bot.game.potions import ActiveEffect, get_potion
from bot.game.world import DEFAULT_CITY
from bot.models import Player, Ring

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
    city           TEXT    NOT NULL DEFAULT 'Vegas City',
    look           TEXT    NOT NULL DEFAULT '',
    gender         TEXT    NOT NULL DEFAULT '',
    pro_until      INTEGER NOT NULL DEFAULT 0,
    birthplace     TEXT,
    wins           INTEGER NOT NULL DEFAULT 0,
    losses         INTEGER NOT NULL DEFAULT 0,
    draws          INTEGER NOT NULL DEFAULT 0,
    -- Подвал считается отдельно от боёв с людьми
    raid_wins      INTEGER NOT NULL DEFAULT 0,
    raid_fights    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    code     TEXT    NOT NULL,
    wear     INTEGER NOT NULL DEFAULT 0,
    max_wear INTEGER NOT NULL DEFAULT 20,
    slot     TEXT,
    bought_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory(user_id);

-- Купленные образы: платный образ покупается один раз и остаётся навсегда,
-- переключаться между своими можно сколько угодно.
CREATE TABLE IF NOT EXISTS player_looks (
    user_id   INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    bought_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, code)
);

-- Эликсиры лежат стопкой: у одного бойца их может быть сколько угодно
-- штук, а различать их между собой незачем — они одинаковые.
CREATE TABLE IF NOT EXISTS potions (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, code)
);

-- Действующие эффекты: храним момент, до которого эффект живёт, а не
-- остаток. Так он переживает перезапуск бота и не требует фоновых задач.
CREATE TABLE IF NOT EXISTS effects (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    until   INTEGER NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS rings (
    chat_id   INTEGER NOT NULL,
    thread_id INTEGER,
    number    INTEGER NOT NULL DEFAULT 1,
    mode      TEXT    NOT NULL DEFAULT 'fist',
    title     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, mode, number)
);

-- Одна ветка — один ринг: иначе в ней столкнулись бы два боя
CREATE UNIQUE INDEX IF NOT EXISTS idx_rings_thread
    ON rings(chat_id, thread_id);

CREATE TABLE IF NOT EXISTS duels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Пусто — бой шёл в мини-аппе, ветки у него нет
    chat_id       INTEGER,
    thread_id     INTEGER,
    challenger_id INTEGER NOT NULL,
    opponent_id   INTEGER NOT NULL,
    winner_id     INTEGER,
    rounds        INTEGER NOT NULL DEFAULT 0,
    end_reason    TEXT,
    mode          TEXT NOT NULL DEFAULT 'fist',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_duels_chat ON duels(chat_id, created_at DESC);

-- Кто с кем дрался: по строке на бойца. Отдельной таблицей, потому что
-- история бойца — это выборка по нему, а не по чату, и без неё пришлось бы
-- каждый раз просматривать оба столбца боя.
CREATE TABLE IF NOT EXISTS duel_sides (
    duel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (duel_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_duel_sides_user ON duel_sides(user_id, duel_id DESC);

-- Разбор боя по ходам: строка на ход, удары внутри — json.
-- Json, а не колонки: удары читаются целиком и только на экране одного боя,
-- искать по ним нечего, а форма у них ровно та, что уходит в мини-апп.
CREATE TABLE IF NOT EXISTS duel_log (
    duel_id INTEGER NOT NULL,
    number  INTEGER NOT NULL,
    strikes TEXT    NOT NULL,
    PRIMARY KEY (duel_id, number)
);

-- Комиссионка: вещь ушла из инвентаря продавца и ждёт покупателя. Износ
-- переезжает вместе с ней — покупают её такой, какая есть.
CREATE TABLE IF NOT EXISTS market (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    wear      INTEGER NOT NULL DEFAULT 0,
    max_wear  INTEGER NOT NULL,
    price     INTEGER NOT NULL,
    listed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_market_seller ON market(seller_id, id DESC);

-- Рейды: отряд живых бойцов против одного босса. Запись заводится в момент
-- сбора — по ней же считается «один рейд в сутки на созывающего», — и
-- закрывается итогом, когда бой кончился.
CREATE TABLE IF NOT EXISTS raids (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    thread_id  INTEGER,
    opener_id  INTEGER NOT NULL,
    boss       TEXT    NOT NULL,
    boss_level INTEGER NOT NULL DEFAULT 0,
    size       INTEGER NOT NULL,
    outcome    TEXT,
    waves      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_raids_opener ON raids(opener_id, id DESC);

CREATE TABLE IF NOT EXISTS raid_members (
    raid_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    damage  INTEGER NOT NULL DEFAULT 0,
    alive   INTEGER NOT NULL DEFAULT 1,
    prize   TEXT,
    PRIMARY KEY (raid_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_raid_members_user
    ON raid_members(user_id, raid_id DESC);

-- Попытки бойца в одном окне рейда: пропуск тратится один раз на окно, а
-- побеждают в окне тоже один раз. Ключ — боец и начало окна, поэтому
-- повторный заход в то же окно ничего не списывает.
CREATE TABLE IF NOT EXISTS raid_window (
    user_id   INTEGER NOT NULL,
    opened_at INTEGER NOT NULL,  -- начало окна, секунды эпохи
    won       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, opened_at)
);

CREATE TABLE IF NOT EXISTS battles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    thread_id  INTEGER,
    kind       TEXT    NOT NULL,
    mode       TEXT    NOT NULL DEFAULT 'armed',
    rounds     INTEGER NOT NULL DEFAULT 0,
    winner_team INTEGER,
    draw       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS battle_members (
    battle_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    team      INTEGER NOT NULL DEFAULT 0,
    won       INTEGER NOT NULL DEFAULT 0,
    survived  INTEGER NOT NULL DEFAULT 0,
    damage    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (battle_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_battles_chat ON battles(chat_id, created_at DESC);

-- Турнир живёт сутками и обязан пережить перезапуск бота, поэтому он весь
-- в базе: и запись участников, и сетка, и то, на каком круге остановились.
CREATE TABLE IF NOT EXISTS tournaments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    thread_id  INTEGER,
    title      TEXT    NOT NULL DEFAULT '',
    size       INTEGER NOT NULL DEFAULT 8,
    mode       TEXT    NOT NULL DEFAULT 'armed',
    min_level  INTEGER NOT NULL DEFAULT 1,
    max_level  INTEGER NOT NULL DEFAULT 10,
    state      TEXT    NOT NULL DEFAULT 'registration',
    round      INTEGER NOT NULL DEFAULT 0,
    winner_id  INTEGER,
    opens_until TEXT   NOT NULL,
    message_id INTEGER,
    chat_title TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tournament_players (
    tournament_id INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    seed          INTEGER NOT NULL DEFAULT 0,
    out_at_round  INTEGER,
    PRIMARY KEY (tournament_id, user_id)
);

CREATE TABLE IF NOT EXISTS tournament_matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round         INTEGER NOT NULL,
    slot          INTEGER NOT NULL,
    first_id      INTEGER,
    second_id     INTEGER,
    winner_id     INTEGER,
    replays       INTEGER NOT NULL DEFAULT 0,
    state         TEXT    NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_tournaments_chat
    ON tournaments(chat_id, state);
CREATE INDEX IF NOT EXISTS idx_tournament_matches
    ON tournament_matches(tournament_id, round, slot);

-- Покупки за Telegram Stars. charge_id уникален: Telegram может прислать
-- один и тот же платёж дважды, а начислить кредиты мы обязаны один раз.
CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'credits',
    code       TEXT    NOT NULL,
    stars      INTEGER NOT NULL DEFAULT 0,
    credits    INTEGER NOT NULL DEFAULT 0,
    charge_id  TEXT    NOT NULL UNIQUE,
    refunded_at TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_purchases_user
    ON purchases(user_id, created_at DESC);

-- Ветка новостей: куда бот сам приносит объявления об изменениях.
-- Одна на группу, поэтому chat_id и есть ключ.
CREATE TABLE IF NOT EXISTS noticeboards (
    chat_id   INTEGER PRIMARY KEY,
    thread_id INTEGER,
    title     TEXT NOT NULL DEFAULT ''
);

-- Что уже объявляли. Ключ — код объявления и ветка: перезапуск бота
-- ничего не задваивает, а новая группа получает всё с начала.
CREATE TABLE IF NOT EXISTS announcements (
    chat_id   INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    posted_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, code)
);
"""

PLAYER_COLUMNS = (
    "user_id, nickname, class_code, avatar, avatar_file_id, look, strength, "
    "agility, intuition, endurance, free_points, level, exp, total_exp, "
    "micro_ups, credits, rating, hp, hp_at, wins, losses, draws, "
    "raid_wins, raid_fights, city, birthplace, pro_until, gender, created_at"
)

# Колонки, добавленные после первой версии: их дописываем в уже живые базы.
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("total_exp", "INTEGER NOT NULL DEFAULT 0"),
    ("micro_ups", "INTEGER NOT NULL DEFAULT 0"),
    ("credits", "INTEGER NOT NULL DEFAULT 0"),
    ("rating", f"INTEGER NOT NULL DEFAULT {RATING_START}"),
    ("hp", "INTEGER"),  # NULL — боец здоров
    ("hp_at", "INTEGER NOT NULL DEFAULT 0"),
    ("city", f"TEXT NOT NULL DEFAULT '{DEFAULT_CITY}'"),
    ("birthplace", "TEXT"),
    ("look", "TEXT NOT NULL DEFAULT ''"),
    ("pro_until", "INTEGER NOT NULL DEFAULT 0"),  # 0 — подписки нет
    ("gender", "TEXT NOT NULL DEFAULT ''"),  # пусто — бойца заводили до выбора
    ("raid_wins", "INTEGER NOT NULL DEFAULT 0"),
    ("raid_fights", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._ensure_directory()
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
        if "raid_fights" not in existing:
            await self._split_raids_from_record()
        await self._migrate_duels()
        await self._migrate_arenas()

    async def _split_raids_from_record(self) -> None:
        """Вынуть рейды из личного счёта бойца — один раз, при обновлении.

        Раньше подвал писался туда же, куда бои с людьми: победа над
        боссом шла победой, неудачный заход — поражением. Счёт врал в обе
        стороны, и по нему нельзя было понять, кого боец бил на самом деле.

        Пересчитываем по журналу рейдов: там есть и состав отряда, и исход
        каждого захода, поэтому историю терять не нужно — достаточно
        переложить её в свою колонку. Считаем только законченные рейды: у
        брошенного `outcome` пуст, и в счёт он не попадал.
        """

        def counted(outcome: str | None) -> str:
            """Сколько рейдов с таким исходом за плечами у этой строки."""
            filter_by = "r.outcome IS NOT NULL" if outcome is None else (
                f"r.outcome = '{outcome}'"
            )
            return (
                "SELECT COUNT(*) FROM raid_members m "
                "JOIN raids r ON r.id = m.raid_id "
                f"WHERE m.user_id = players.user_id AND {filter_by}"
            )

        # Все выражения UPDATE читают старые значения строки, поэтому
        # вычитание из побед и запись в raid_wins не мешают друг другу
        await self.conn.execute(
            f"""
            UPDATE players SET
                raid_fights = ({counted(None)}),
                raid_wins   = ({counted("win")}),
                wins   = MAX(0, wins   - ({counted("win")})),
                draws  = MAX(0, draws  - ({counted("draw")})),
                losses = MAX(0, losses - ({counted("loss")}))
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: рейды вынесены из личного счёта бойцов")

    async def _migrate_duels(self) -> None:
        """Записи боёв прошлой версии — кулачные: другого режима тогда не было."""
        async with self.conn.execute("PRAGMA table_info(duels)") as cursor:
            columns = {row["name"]: row for row in await cursor.fetchall()}
        if "mode" not in columns:
            await self.conn.execute(
                "ALTER TABLE duels ADD COLUMN mode TEXT NOT NULL DEFAULT 'fist'"
            )
            logger.info("База обновлена: у боёв появился режим")
        chat = columns.get("chat_id")
        if chat is not None and chat["notnull"]:
            # У боя из мини-аппа ветки нет, и chat_id должен уметь быть пустым.
            # Снять NOT NULL в SQLite можно только пересборкой таблицы.
            await self._rebuild_duels()
        await self._fill_duel_sides()

    async def _fill_duel_sides(self) -> None:
        """Записать в стороны боёв тех, кто дрался до появления этой таблицы.

        Без этого история бойца начиналась с первого боя новой версии: сами
        бои в базе лежали, но выбирать их было не по кому.
        """
        async with self.conn.execute(
            "SELECT COUNT(*) AS lost FROM duels "
            "WHERE id NOT IN (SELECT duel_id FROM duel_sides)"
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row["lost"]:
            return
        await self.conn.executescript(
            """
            INSERT OR IGNORE INTO duel_sides (duel_id, user_id)
                SELECT id, challenger_id FROM duels;
            INSERT OR IGNORE INTO duel_sides (duel_id, user_id)
                SELECT id, opponent_id FROM duels;
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: в историю вернулись %s боёв", row["lost"])

    async def _rebuild_duels(self) -> None:
        """Пересобрать таблицу боёв, чтобы chat_id мог быть пустым."""
        await self.conn.executescript(
            """
            ALTER TABLE duels RENAME TO duels_old;
            CREATE TABLE duels (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       INTEGER,
                thread_id     INTEGER,
                challenger_id INTEGER NOT NULL,
                opponent_id   INTEGER NOT NULL,
                winner_id     INTEGER,
                rounds        INTEGER NOT NULL DEFAULT 0,
                end_reason    TEXT,
                mode          TEXT NOT NULL DEFAULT 'fist',
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO duels (
                id, chat_id, thread_id, challenger_id, opponent_id,
                winner_id, rounds, end_reason, mode, created_at
            )
            SELECT id, chat_id, thread_id, challenger_id, opponent_id,
                   winner_id, rounds, end_reason, mode, created_at
            FROM duels_old;
            DROP TABLE duels_old;
            CREATE INDEX IF NOT EXISTS idx_duels_chat
                ON duels(chat_id, created_at DESC);
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: бой может идти без ветки")

    async def _migrate_arenas(self) -> None:
        """Единственная арена прошлой версии становится первым кулачным рингом."""
        async with self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'arenas'"
        ) as cursor:
            if await cursor.fetchone() is None:
                return
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO rings (chat_id, thread_id, number, mode, title)
            SELECT chat_id, thread_id, 1, 'fist', title FROM arenas
            """
        )
        await self.conn.execute("DROP TABLE arenas")
        logger.info("База обновлена: арены переехали в ринги")

    def _ensure_directory(self) -> None:
        """Создать каталог под базу.

        На хостингах путь вроде /data/fightclub.db указывает на подключённый
        диск, но если диск ещё не примонтирован, каталога может не быть —
        и SQLite падает с невнятным «unable to open database file».
        """
        parent = Path(self.path).parent
        if self.path != ":memory:" and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

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
        if not row:
            return None
        player = _to_player(row)
        player.gear = await self.list_gear(player.user_id)
        player.potions = await self.list_potions(player.user_id)
        player.effects = await self.list_effects(player.user_id)
        return player

    async def save_player(self, player: Player) -> None:
        if not player.created_at:
            # День рождения персонажа ставим один раз, при первом сохранении
            player.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO players (
                user_id, nickname, class_code, avatar, avatar_file_id, look,
                strength, agility, intuition, endurance, free_points, level,
                exp, total_exp, micro_ups, credits, rating, hp, hp_at,
                wins, losses, draws, raid_wins, raid_fights,
                city, birthplace, pro_until, gender, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                nickname       = excluded.nickname,
                class_code     = excluded.class_code,
                avatar         = excluded.avatar,
                avatar_file_id = excluded.avatar_file_id,
                look           = excluded.look,
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
                city           = excluded.city,
                birthplace     = excluded.birthplace,
                wins           = excluded.wins,
                losses         = excluded.losses,
                draws          = excluded.draws,
                raid_wins      = excluded.raid_wins,
                raid_fights    = excluded.raid_fights,
                pro_until      = excluded.pro_until,
                gender         = excluded.gender
            """,
            (
                player.user_id,
                player.nickname,
                player.class_code,
                player.avatar,
                player.avatar_file_id,
                player.look,
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
                player.raid_wins,
                player.raid_fights,
                player.city,
                player.birthplace,
                player.pro_until,
                player.gender,
                player.created_at,
            ),
        )
        await self.conn.commit()

    async def delete_player(self, user_id: int) -> None:
        """Стереть бойца со всем нажитым.

        Купленные образы не трогаем: они привязаны к человеку, а не к
        персонажу, и оплачены отдельно. Вещи, склянки и выпитое уходят
        вместе с бойцом.
        """
        await self.conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM potions WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM effects WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def find_by_nickname(self, nickname: str) -> Player | None:
        """Боец по прозвищу. Регистр не важен: прозвище — не пароль."""
        async with self.conn.execute(
            f"SELECT {PLAYER_COLUMNS} FROM players WHERE lower(nickname) = lower(?)",
            (nickname,),
        ) as cursor:
            row = await cursor.fetchone()
        return await self.get_player(int(row["user_id"])) if row else None

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
        players = [_to_player(row) for row in rows]
        for player in players:
            player.gear = await self.list_gear(player.user_id)
        return players

    async def all_players(self, limit: int = 200) -> list[Player]:
        """Все бойцы клуба: сильные сверху. Экипировку не тянем — она не нужна."""
        async with self.conn.execute(
            f"""
            SELECT {PLAYER_COLUMNS} FROM players
            ORDER BY rating DESC, level DESC, wins DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_to_player(row) for row in rows]

    # ---------- инвентарь ----------

    async def list_gear(self, user_id: int) -> list[OwnedItem]:
        """Всё, что у бойца есть: и надетое, и лежащее в рюкзаке."""
        async with self.conn.execute(
            """
            SELECT id, code, wear, max_wear, slot FROM inventory
            WHERE user_id = ? ORDER BY id
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [owned for owned in map(_to_owned_item, rows) if owned is not None]

    async def add_gear(
        self, user_id: int, code: str, max_wear: int = MAX_WEAR, wear: int = 0
    ) -> OwnedItem:
        """Положить вещь в инвентарь. `wear` — если вещь уже пожили.

        Из лавки вещи приходят новыми, из комиссионки — с тем износом, с
        каким их сдали: покупают её такой, какая есть.
        """
        item = get_item(code)
        if item is None:
            raise ValueError(f"Неизвестный предмет: {code}")
        cursor = await self.conn.execute(
            "INSERT INTO inventory (user_id, code, max_wear, wear) VALUES (?,?,?,?)",
            (user_id, code, max_wear, wear),
        )
        await self.conn.commit()
        return OwnedItem(
            item=item, id=int(cursor.lastrowid or 0), max_wear=max_wear, wear=wear
        )

    async def save_gear(self, owned: OwnedItem) -> None:
        await self.conn.execute(
            "UPDATE inventory SET wear = ?, max_wear = ?, slot = ? WHERE id = ?",
            (owned.wear, owned.max_wear, owned.slot.value if owned.slot else None, owned.id),
        )
        await self.conn.commit()

    async def delete_gear(self, item_id: int) -> None:
        await self.conn.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        await self.conn.commit()

    # ---------- эликсиры ----------

    async def list_potions(self, user_id: int) -> dict[str, int]:
        """Что у бойца в склянках: код эликсира → сколько штук."""
        async with self.conn.execute(
            "SELECT code, count FROM potions WHERE user_id = ? AND count > 0",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            row["code"]: int(row["count"])
            for row in rows
            if get_potion(row["code"]) is not None
        }

    async def add_potion(self, user_id: int, code: str, count: int = 1) -> int:
        """Положить купленные склянки в рюкзак. Вернуть, сколько их стало."""
        if get_potion(code) is None:
            raise ValueError(f"Неизвестный эликсир: {code}")
        await self.conn.execute(
            """
            INSERT INTO potions (user_id, code, count) VALUES (?,?,?)
            ON CONFLICT(user_id, code) DO UPDATE SET count = count + excluded.count
            """,
            (user_id, code, count),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT count FROM potions WHERE user_id = ? AND code = ?", (user_id, code)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row else 0

    async def take_potion(self, user_id: int, code: str) -> bool:
        """Забрать одну склянку. False — брать было нечего."""
        cursor = await self.conn.execute(
            "UPDATE potions SET count = count - 1 WHERE user_id = ? AND code = ? "
            "AND count > 0",
            (user_id, code),
        )
        taken = cursor.rowcount > 0
        await self.conn.execute(
            "DELETE FROM potions WHERE user_id = ? AND code = ? AND count <= 0",
            (user_id, code),
        )
        await self.conn.commit()
        return taken

    async def list_effects(self, user_id: int) -> list[ActiveEffect]:
        """Действующие эффекты. Просроченные подчищаем тут же — их время вышло."""
        await self.conn.execute(
            "DELETE FROM effects WHERE user_id = ? AND until <= ?",
            (user_id, now_ts()),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT code, until FROM effects WHERE user_id = ? ORDER BY until",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            ActiveEffect(code=row["code"], until=int(row["until"]))
            for row in rows
            if get_potion(row["code"]) is not None
        ]

    async def drop_effect(self, user_id: int, code: str) -> None:
        """Погасить эффект досрочно: так уходит вытесненный эликсир."""
        await self.conn.execute(
            "DELETE FROM effects WHERE user_id = ? AND code = ?", (user_id, code)
        )
        await self.conn.commit()

    async def set_effect(self, user_id: int, code: str, until: int) -> None:
        """Завести эффект или сдвинуть его срок."""
        await self.conn.execute(
            """
            INSERT INTO effects (user_id, code, until) VALUES (?,?,?)
            ON CONFLICT(user_id, code) DO UPDATE SET until = excluded.until
            """,
            (user_id, code, until),
        )
        await self.conn.commit()

    # ---------- ринги ----------

    async def set_ring(
        self,
        chat_id: int,
        thread_id: int | None,
        number: int = 1,
        mode: FightMode = FightMode.FIST,
        title: str = "",
    ) -> Ring:
        """Отметить ветку рингом. Ветка занята другим рингом — тот освобождается."""
        await self.conn.execute(
            "DELETE FROM rings WHERE chat_id = ? AND thread_id IS ?",
            (chat_id, thread_id),
        )
        await self.conn.execute(
            """
            INSERT INTO rings (chat_id, thread_id, number, mode, title) VALUES (?,?,?,?,?)
            ON CONFLICT(chat_id, mode, number) DO UPDATE SET
                thread_id = excluded.thread_id,
                title     = excluded.title
            """,
            (chat_id, thread_id, number, mode.value, title),
        )
        await self.conn.commit()
        return Ring(
            chat_id=chat_id, thread_id=thread_id, number=number, mode=mode, title=title
        )

    async def get_ring(self, chat_id: int, thread_id: int | None) -> Ring | None:
        """Ринг этой ветки, если она отмечена."""
        async with self.conn.execute(
            """
            SELECT chat_id, thread_id, number, mode, title FROM rings
            WHERE chat_id = ? AND thread_id IS ?
            """,
            (chat_id, thread_id),
        ) as cursor:
            row = await cursor.fetchone()
        return _to_ring(row) if row else None

    async def list_rings(self, chat_id: int) -> list[Ring]:
        """Все ринги группы — кулачные по порядку, следом с оружием."""
        async with self.conn.execute(
            """
            SELECT chat_id, thread_id, number, mode, title FROM rings
            WHERE chat_id = ?
            ORDER BY CASE mode WHEN 'fist' THEN 0 ELSE 1 END, number
            """,
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_to_ring(row) for row in rows]

    async def drop_ring(self, chat_id: int, thread_id: int | None) -> None:
        await self.conn.execute(
            "DELETE FROM rings WHERE chat_id = ? AND thread_id IS ?",
            (chat_id, thread_id),
        )
        await self.conn.commit()

    # ---------- ветка новостей ----------

    async def set_noticeboard(
        self, chat_id: int, thread_id: int | None, title: str = ""
    ) -> None:
        """Отметить ветку доской объявлений. Одна на группу."""
        await self.conn.execute(
            """
            INSERT INTO noticeboards (chat_id, thread_id, title) VALUES (?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                title     = excluded.title
            """,
            (chat_id, thread_id, title),
        )
        await self.conn.commit()

    async def get_noticeboard(self, chat_id: int) -> tuple[int | None, str] | None:
        async with self.conn.execute(
            "SELECT thread_id, title FROM noticeboards WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    async def list_noticeboards(self) -> list[tuple[int, int | None, str]]:
        """Все доски объявлений: бот обходит их при запуске."""
        async with self.conn.execute(
            "SELECT chat_id, thread_id, title FROM noticeboards"
        ) as cursor:
            rows = await cursor.fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    async def drop_noticeboard(self, chat_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM noticeboards WHERE chat_id = ?", (chat_id,)
        )
        await self.conn.commit()

    async def announced(self, chat_id: int) -> set[str]:
        """Коды объявлений, уже отправленных в эту группу."""
        async with self.conn.execute(
            "SELECT code FROM announcements WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def mark_announced(self, chat_id: int, code: str) -> bool:
        """Отметить объявление отправленным. False — уже было."""
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO announcements (chat_id, code) VALUES (?,?)",
            (chat_id, code),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def forget_announcement(self, chat_id: int, code: str) -> None:
        """Забыть отправленное — чтобы объявить заново."""
        await self.conn.execute(
            "DELETE FROM announcements WHERE chat_id = ? AND code = ?",
            (chat_id, code),
        )
        await self.conn.commit()

    # ---------- дуэли ----------

    async def add_duel(
        self,
        chat_id: int | None,
        thread_id: int | None,
        challenger_id: int,
        opponent_id: int,
        winner_id: int | None,
        rounds: int,
        end_reason: str | None,
        mode: FightMode = FightMode.FIST,
        log: list[dict[str, Any]] | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO duels (
                chat_id, thread_id, challenger_id, opponent_id,
                winner_id, rounds, end_reason, mode
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                chat_id,
                thread_id,
                challenger_id,
                opponent_id,
                winner_id,
                rounds,
                end_reason,
                mode.value,
            ),
        )
        duel_id = int(cursor.lastrowid or 0)
        # Обе стороны отдельными строками: история бойца — выборка по нему
        await self.conn.executemany(
            "INSERT OR IGNORE INTO duel_sides (duel_id, user_id) VALUES (?,?)",
            [(duel_id, challenger_id), (duel_id, opponent_id)],
        )
        if log:
            await self.conn.executemany(
                "INSERT OR REPLACE INTO duel_log (duel_id, number, strikes) "
                "VALUES (?,?,?)",
                [
                    (duel_id, turn["number"], json.dumps(turn, ensure_ascii=False))
                    for turn in log
                ],
            )
        await self.conn.commit()
        return duel_id

    async def fights_of(
        self, user_id: int, limit: int = 30, before: int | None = None
    ) -> list[dict[str, Any]]:
        """Бои этого бойца, свежие сверху. `before` — листать дальше вглубь.

        Отдаём как есть, вместе с прозвищами обеих сторон: экрану истории
        нужен соперник по имени, а ходить за игроками по одному — лишние
        запросы на каждую строку списка.
        """
        async with self.conn.execute(
            """
            SELECT d.id, d.chat_id, d.challenger_id, d.opponent_id, d.winner_id,
                   d.rounds, d.end_reason, d.mode, d.created_at,
                   first.nickname AS challenger_name,
                   second.nickname AS opponent_name
            FROM duel_sides AS side
            JOIN duels AS d ON d.id = side.duel_id
            LEFT JOIN players AS first ON first.user_id = d.challenger_id
            LEFT JOIN players AS second ON second.user_id = d.opponent_id
            WHERE side.user_id = ? AND (? IS NULL OR d.id < ?)
            ORDER BY d.id DESC LIMIT ?
            """,
            (user_id, before, before, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def duel_by_id(self, duel_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            """
            SELECT d.id, d.chat_id, d.challenger_id, d.opponent_id, d.winner_id,
                   d.rounds, d.end_reason, d.mode, d.created_at,
                   first.nickname AS challenger_name,
                   second.nickname AS opponent_name
            FROM duels AS d
            LEFT JOIN players AS first ON first.user_id = d.challenger_id
            LEFT JOIN players AS second ON second.user_id = d.opponent_id
            WHERE d.id = ?
            """,
            (duel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def duel_log(self, duel_id: int) -> list[dict[str, Any]]:
        """Разбор боя по ходам. Пусто — бой шёл до того, как их начали писать."""
        async with self.conn.execute(
            "SELECT strikes FROM duel_log WHERE duel_id = ? ORDER BY number",
            (duel_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [json.loads(row["strikes"]) for row in rows]

    # ---------- комиссионка ----------

    async def add_lot(
        self, seller_id: int, code: str, wear: int, max_wear: int, price: int
    ) -> int:
        cursor = await self.conn.execute(
            "INSERT INTO market (seller_id, code, wear, max_wear, price) "
            "VALUES (?,?,?,?,?)",
            (seller_id, code, wear, max_wear, price),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def market_lots(self) -> list[dict[str, Any]]:
        """Всё, что сейчас лежит на комиссии, вместе с прозвищем продавца."""
        async with self.conn.execute(
            """
            SELECT m.id, m.seller_id, m.code, m.wear, m.max_wear, m.price,
                   m.listed_at, p.nickname AS seller
            FROM market AS m
            LEFT JOIN players AS p ON p.user_id = m.seller_id
            ORDER BY m.id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def market_lot(self, lot_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            """
            SELECT m.id, m.seller_id, m.code, m.wear, m.max_wear, m.price,
                   m.listed_at, p.nickname AS seller
            FROM market AS m
            LEFT JOIN players AS p ON p.user_id = m.seller_id
            WHERE m.id = ?
            """,
            (lot_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def take_lot(self, lot_id: int) -> bool:
        """Снять лот с полки. False — его уже кто-то забрал.

        Это же и есть защита от двойной продажи: строку забирает тот, чей
        DELETE прошёл первым, а деньги и вещь считаются уже после.
        """
        cursor = await self.conn.execute("DELETE FROM market WHERE id = ?", (lot_id,))
        await self.conn.commit()
        return bool(cursor.rowcount)

    # ---------- рейды ----------

    async def open_raid_record(
        self,
        chat_id: int | None,
        thread_id: int | None,
        opener_id: int,
        boss: str,
        size: int,
    ) -> int:
        """Завести запись рейда в момент сбора.

        Пишем сразу, а не по итогу: по этой записи считается суточный запрет
        на новый сбор, и она должна появиться раньше, чем первый удар.
        """
        cursor = await self.conn.execute(
            "INSERT INTO raids (chat_id, thread_id, opener_id, boss, size) "
            "VALUES (?,?,?,?,?)",
            (chat_id, thread_id, opener_id, boss, size),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def drop_raid_record(self, raid_id: int) -> None:
        """Убрать запись несостоявшегося рейда: попытка не должна пропадать."""
        if not raid_id:
            return
        await self.conn.execute("DELETE FROM raid_members WHERE raid_id = ?", (raid_id,))
        await self.conn.execute("DELETE FROM raids WHERE id = ?", (raid_id,))
        await self.conn.commit()

    async def close_raid_record(
        self,
        raid_id: int,
        outcome: str,
        waves: int,
        boss_level: int,
        members: list[tuple[int, int, bool, str | None]],
    ) -> None:
        """Записать итог рейда и что вышло у каждого."""
        if not raid_id:
            return
        await self.conn.execute(
            "UPDATE raids SET outcome = ?, waves = ?, boss_level = ? WHERE id = ?",
            (outcome, waves, boss_level, raid_id),
        )
        await self.conn.executemany(
            "INSERT OR REPLACE INTO raid_members (raid_id, user_id, damage, alive, prize)"
            " VALUES (?,?,?,?,?)",
            [
                (raid_id, user_id, damage, 1 if alive else 0, prize)
                for user_id, damage, alive, prize in members
            ],
        )
        await self.conn.commit()

    async def raid_window(self, user_id: int, opened_at: int) -> dict[str, Any] | None:
        """Что у бойца в этом окне: тратил ли пропуск и победил ли уже."""
        async with self.conn.execute(
            "SELECT * FROM raid_window WHERE user_id = ? AND opened_at = ?",
            (user_id, opened_at),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def start_raid_window(self, user_id: int, opened_at: int) -> bool:
        """Открыть бойцу это окно. True — открыли впервые, пропуск нужен.

        Вставка атомарна: если строка уже была, ничего не меняется и мы
        отвечаем False. Так повторный заход в то же окно не спишет второй
        пропуск, даже если два нажатия придут разом.
        """
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO raid_window (user_id, opened_at) VALUES (?, ?)",
            (user_id, opened_at),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def drop_raid_window(self, user_id: int, opened_at: int) -> None:
        """Отпустить окно: вход не состоялся, пропуск не списан."""
        await self.conn.execute(
            "DELETE FROM raid_window WHERE user_id = ? AND opened_at = ? AND won = 0",
            (user_id, opened_at),
        )
        await self.conn.commit()

    async def close_raid_window(self, user_id: int, opened_at: int) -> None:
        """Отметить победу: в этом окне боец в подвал больше не пойдёт."""
        await self.conn.execute(
            "INSERT INTO raid_window (user_id, opened_at, won) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, opened_at) DO UPDATE SET won = 1",
            (user_id, opened_at),
        )
        await self.conn.commit()

    async def raids_of(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Рейды этого бойца, свежие сверху — вместе с теми, с кем ходил."""
        async with self.conn.execute(
            """
            SELECT r.id, r.boss, r.boss_level, r.outcome, r.waves, r.created_at,
                   m.damage, m.alive, m.prize,
                   (
                       SELECT group_concat(p.nickname, ', ')
                       FROM raid_members AS other
                       LEFT JOIN players AS p ON p.user_id = other.user_id
                       WHERE other.raid_id = r.id AND other.user_id != m.user_id
                   ) AS allies
            FROM raid_members AS m
            JOIN raids AS r ON r.id = m.raid_id
            WHERE m.user_id = ? AND r.outcome IS NOT NULL
            ORDER BY r.id DESC LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def add_battle(self, session, outcome) -> int:
        """Записать групповой бой и всех, кто в нём был."""
        cursor = await self.conn.execute(
            """
            INSERT INTO battles (
                chat_id, thread_id, kind, mode, rounds, winner_team, draw
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                session.chat_id,
                session.thread_id,
                session.kind.value,
                session.mode.value,
                session.round_number,
                outcome.winning_team,
                int(outcome.draw),
            ),
        )
        battle_id = int(cursor.lastrowid or 0)
        await self.conn.executemany(
            """
            INSERT INTO battle_members (battle_id, user_id, team, won, survived, damage)
            VALUES (?,?,?,?,?,?)
            """,
            [
                (
                    battle_id,
                    user_id,
                    session.teams.get(user_id, 0),
                    int(user_id in outcome.winners),
                    int(fighter.alive),
                    fighter.damage_dealt,
                )
                for user_id, fighter in session.fighters.items()
            ],
        )
        await self.conn.commit()
        return battle_id

    async def recent_battles(self, chat_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """
            SELECT id, kind, mode, rounds, winner_team, draw, created_at
            FROM battles WHERE chat_id = ? ORDER BY id DESC LIMIT ?
            """,
            (chat_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def battle_members(self, battle_id: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """
            SELECT user_id, team, won, survived, damage FROM battle_members
            WHERE battle_id = ? ORDER BY team, user_id
            """,
            (battle_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ---------- образы ----------

    async def owned_looks(self, user_id: int) -> set[str]:
        """Какие платные образы боец уже купил."""
        async with self.conn.execute(
            "SELECT code FROM player_looks WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["code"] for row in rows}

    async def drop_look(self, user_id: int, code: str) -> None:
        """Забрать образ обратно — так уходит возврат за подписку."""
        await self.conn.execute(
            "DELETE FROM player_looks WHERE user_id = ? AND code = ?",
            (user_id, code),
        )
        await self.conn.commit()

    async def add_look(self, user_id: int, code: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO player_looks (user_id, code) VALUES (?,?)",
            (user_id, code),
        )
        await self.conn.commit()

    # ---------- турниры ----------

    async def create_tournament(
        self,
        chat_id: int,
        thread_id: int | None,
        size: int,
        mode: FightMode,
        min_level: int,
        max_level: int,
        opens_until: str,
        title: str = "",
        chat_title: str = "",
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO tournaments (
                chat_id, thread_id, title, size, mode,
                min_level, max_level, opens_until, chat_title
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                chat_id,
                thread_id,
                title,
                size,
                mode.value,
                min_level,
                max_level,
                opens_until,
                chat_title,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def get_tournament(self, tournament_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def live_tournaments(self, chat_id: int | None = None) -> list[dict[str, Any]]:
        """Турниры, которые ещё идут: набирают состав или дерутся."""
        query = "SELECT * FROM tournaments WHERE state IN ('registration', 'running')"
        params: tuple = ()
        if chat_id is not None:
            query += " AND chat_id = ?"
            params = (chat_id,)
        async with self.conn.execute(query + " ORDER BY id", params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_tournament(self, tournament_id: int, **fields: Any) -> None:
        if not fields:  # pragma: no cover - вызывать без правок незачем
            return
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE tournaments SET {columns} WHERE id = ?",
            (*fields.values(), tournament_id),
        )
        await self.conn.commit()

    async def add_tournament_player(self, tournament_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO tournament_players (tournament_id, user_id) "
            "VALUES (?,?)",
            (tournament_id, user_id),
        )
        await self.conn.commit()

    async def drop_tournament_player(self, tournament_id: int, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM tournament_players WHERE tournament_id = ? AND user_id = ?",
            (tournament_id, user_id),
        )
        await self.conn.commit()

    async def tournament_players(self, tournament_id: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """
            SELECT user_id, seed, out_at_round FROM tournament_players
            WHERE tournament_id = ? ORDER BY seed, user_id
            """,
            (tournament_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def set_tournament_seeds(
        self, tournament_id: int, seeds: dict[int, int]
    ) -> None:
        await self.conn.executemany(
            "UPDATE tournament_players SET seed = ? "
            "WHERE tournament_id = ? AND user_id = ?",
            [(seed, tournament_id, user_id) for user_id, seed in seeds.items()],
        )
        await self.conn.commit()

    async def knock_out(self, tournament_id: int, user_id: int, round_number: int) -> None:
        await self.conn.execute(
            "UPDATE tournament_players SET out_at_round = ? "
            "WHERE tournament_id = ? AND user_id = ?",
            (round_number, tournament_id, user_id),
        )
        await self.conn.commit()

    async def add_matches(
        self, tournament_id: int, round_number: int, pairs: list[tuple]
    ) -> None:
        await self.conn.executemany(
            """
            INSERT INTO tournament_matches (
                tournament_id, round, slot, first_id, second_id
            ) VALUES (?,?,?,?,?)
            """,
            [
                (tournament_id, round_number, slot, first, second)
                for slot, (first, second) in enumerate(pairs)
            ],
        )
        await self.conn.commit()

    async def tournament_matches(
        self, tournament_id: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM tournament_matches WHERE tournament_id = ?"
        params: tuple = (tournament_id,)
        if round_number is not None:
            query += " AND round = ?"
            params = (tournament_id, round_number)
        async with self.conn.execute(query + " ORDER BY round, slot", params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_match(self, match_id: int, **fields: Any) -> None:
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE tournament_matches SET {columns} WHERE id = ?",
            (*fields.values(), match_id),
        )
        await self.conn.commit()

    # ---------- касса ----------

    async def add_purchase(
        self,
        user_id: int,
        code: str,
        stars: int,
        credits: int,
        charge_id: str,
        kind: str = "credits",
    ) -> bool:
        """Записать оплату. False значит, что этот платёж уже был учтён."""
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO purchases (
                user_id, kind, code, stars, credits, charge_id
            ) VALUES (?,?,?,?,?,?)
            """,
            (user_id, kind, code, stars, credits, charge_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_purchase(self, charge_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM purchases WHERE charge_id = ?", (charge_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def purchases_of(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Оплаченные покупки. Подарки сюда не идут: за них не платили."""
        async with self.conn.execute(
            """
            SELECT * FROM purchases WHERE user_id = ? AND kind <> 'gift'
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_refunded(self, charge_id: str) -> None:
        await self.conn.execute(
            "UPDATE purchases SET refunded_at = datetime('now') WHERE charge_id = ?",
            (charge_id,),
        )
        await self.conn.commit()

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
            SELECT challenger_id, opponent_id, winner_id, rounds, end_reason,
                   mode, created_at
            FROM duels WHERE chat_id = ? ORDER BY id DESC LIMIT ?
            """,
            (chat_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def _to_player(row: Iterable[Any]) -> Player:
    data = dict(row)  # type: ignore[arg-type]
    return Player(**data)


def _to_ring(row: Any) -> Ring:
    return Ring(
        chat_id=row["chat_id"],
        thread_id=row["thread_id"],
        number=int(row["number"]),
        mode=mode_of(row["mode"]),
        title=row["title"],
    )


def _to_owned_item(row: Any) -> OwnedItem | None:
    """Строка инвентаря → экземпляр предмета. Предметы из будущих версий пропускаем."""
    item = get_item(row["code"])
    if item is None:  # pragma: no cover - каталог урезали, а вещь осталась
        return None
    slot = None
    if row["slot"]:
        try:
            slot = Slot(row["slot"])
        except ValueError:  # pragma: no cover - слот из будущей версии
            slot = None
    return OwnedItem(
        item=item,
        id=int(row["id"]),
        wear=int(row["wear"]),
        max_wear=int(row["max_wear"]),
        slot=slot,
    )
