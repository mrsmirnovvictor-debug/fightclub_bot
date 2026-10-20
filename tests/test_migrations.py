"""Обновление живой базы: та, что уже работает, обязана пережить выкатку.

Все прочие тесты заводят базу с нуля, и потому не видят целого класса
поломок: на пустой базе `CREATE TABLE` создаёт таблицу со всеми
колонками разом, а на живой он не делает ничего — таблица уже есть,
колонки дописываются `ALTER TABLE` потом. Всё, что в `SCHEMA` стоит
рядом с таблицей и смотрит на новую колонку — индекс, триггер,
ограничение, — на пустой базе проходит, а на живой роняет запуск с «no
such column».

Так и случилось с банком: индекс по номеру счёта лежал в `SCHEMA`, и бот
на выкатке не поднялся ни разу, хотя полторы тысячи тестов были зелёные.
Поэтому здесь база собирается **прошлой версией** — текущая схема без
новых колонок, — и только потом ей дают подключиться.
"""

from __future__ import annotations

import re
import sqlite3

import aiosqlite
import pytest

from bot.bank_service import deposit, issue_card, open_account
from bot.database import SCHEMA, Database
from bot.game.bank import CARD
from bot.game.classes import Stats
from bot.models import Player

# Колонки, которых в прошлой версии базы не было. Список ровно тот, что
# ушёл в `MIGRATIONS` вместе с банком
BANK_COLUMNS: tuple[str, ...] = (
    "account_number",
    "account_balance",
    "account_at",
    "card_at",
    "card_paid_until",
    "pay_from",
)


def schema_without(columns: tuple[str, ...]) -> str:
    """Схема прошлой версии: нынешняя, из которой убраны эти колонки.

    Вместе с колонками убираем и всё, что на них смотрит: в прошлой
    версии этого не было тоже. Иначе сломался бы сам сбор базы, и тест
    падал бы до того, как дошёл до проверяемого — до `connect()`.
    """
    legacy = SCHEMA
    for column in columns:
        legacy = re.sub(rf"^\s*{column}\s+.*\n", "", legacy, flags=re.M)
    legacy = "".join(
        piece
        for piece in re.split(r"(?<=;)", legacy)
        if not any(column in piece for column in columns)
    )
    for column in columns:
        assert column not in legacy, f"{column} осталась в схеме прошлой версии"
    return legacy


@pytest.fixture
def old_db_path(tmp_path):
    """Файл базы, собранный схемой прошлой версии, с бойцом внутри."""
    path = tmp_path / "live.db"
    raw = sqlite3.connect(path)
    raw.executescript(schema_without(BANK_COLUMNS))
    raw.execute(
        "INSERT INTO players (user_id, nickname, class_code, credits, level) "
        "VALUES (1, 'Тайлер', 'warrior', 500, 8)"
    )
    raw.commit()
    raw.close()
    return str(path)


async def test_a_live_database_survives_the_upgrade(old_db_path):
    """Главный тест файла: бот обязан подняться на базе прошлой версии.

    Он и ловит ту самую поломку: индекс по дописанной колонке, стоящий в
    `SCHEMA`, роняет `connect()` на «no such column».
    """
    db = Database(old_db_path)

    await db.connect()  # именно здесь оно и падало

    player = await db.get_player(1)
    assert player is not None and player.nickname == "Тайлер"
    # Деньги на месте, а банковские поля пришли со своими значениями
    assert player.credits == 500
    assert player.account_number == "" and player.account_balance == 0
    assert player.card_at == 0
    # Умолчание — карта: у кого её нет, всё равно платит наличными
    assert player.pay_from == CARD
    await db.close()


async def test_connecting_twice_is_no_worse_than_once(old_db_path):
    """Выкатка — не единственный запуск: бот перезапускают и просто так."""
    for _ in range(3):
        db = Database(old_db_path)
        await db.connect()
        await db.close()

    db = Database(old_db_path)
    await db.connect()
    assert (await db.get_player(1)) is not None
    await db.close()


async def test_the_account_index_is_built_on_the_upgraded_base(old_db_path):
    """Индекс по номеру счёта обязан появиться и на живой базе тоже.

    Убрать его из `SCHEMA` было половиной починки. Вторая половина —
    завести его там, где колонки уже дописаны; без неё два бойца могли бы
    получить один номер, и поймал бы это только перевод не тому.
    """
    db = Database(old_db_path)
    await db.connect()

    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("players_account",),
    ) as cursor:
        assert await cursor.fetchone() is not None, "индекс по счёту не создан"

    # И он правда уникален: второй такой же номер база не примет
    await db.conn.execute(
        "UPDATE players SET account_number = 'VB-1111-2222-3333' WHERE user_id = 1"
    )
    await db.conn.execute(
        "INSERT INTO players (user_id, nickname, class_code) VALUES (2, 'Марла', 'rogue')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await db.conn.execute(
            "UPDATE players SET account_number = 'VB-1111-2222-3333' WHERE user_id = 2"
        )
    await db.close()


async def test_the_bank_works_on_a_base_that_predates_it(old_db_path):
    """Боец, заведённый до банка, открывает счёт и платит картой."""
    db = Database(old_db_path)
    await db.connect()
    player = await db.get_player(1)

    number = await open_account(db, player)
    await deposit(db, player, 300)
    await issue_card(db, player, 1_800_000_000)

    assert number.startswith("VB-")
    assert player.account_balance == 200  # 300 внесли, 100 ушло на карту
    fresh = await db.get_player(1)
    assert fresh.account_number == number
    assert fresh.account_balance == 200
    assert fresh.has_card
    await db.close()


async def test_a_fighter_saved_by_the_new_code_keeps_his_bank_row(old_db_path):
    """Сохранение бойца на обновлённой базе не теряет банковских полей."""
    db = Database(old_db_path)
    await db.connect()

    player = Player(
        user_id=7, nickname="Марла", class_code="rogue", credits=100,
        account_number="VB-9999-8888-7777", account_balance=1_234,
        card_at=1_700_000_000, card_paid_until=1_900_000_000, pay_from="cash",
        **Stats(strength=10, agility=10, intuition=10, endurance=10).as_dict(),
    )
    await db.save_player(player)

    fresh = await db.get_player(7)
    assert fresh.account_number == "VB-9999-8888-7777"
    assert fresh.account_balance == 1_234
    assert fresh.card_paid_until == 1_900_000_000
    assert fresh.pay_from == "cash"
    await db.close()


async def test_the_statement_gains_a_comment_without_losing_old_rows(tmp_path):
    """Комментарий дописан к живой выписке, а старые строки остались.

    Выписка появилась раньше комментария: на базе прошлой версии таблица
    уже есть, и `CREATE TABLE IF NOT EXISTS` колонку в неё не добавит.
    """
    path = str(tmp_path / "old_log.db")
    old = await aiosqlite.connect(path)
    old.row_factory = aiosqlite.Row
    await old.execute(
        """
        CREATE TABLE card_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            at      INTEGER NOT NULL,
            amount  INTEGER NOT NULL,
            kind    TEXT    NOT NULL,
            note    TEXT    NOT NULL DEFAULT '',
            balance INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    await old.execute(
        "INSERT INTO card_log (user_id, at, amount, kind, note, balance) "
        "VALUES (1, 1700000000, -120, 'pay', 'Магазин одежды', 880)"
    )
    await old.commit()
    await old.close()

    db = Database(path)
    await db.connect()
    moves = await db.card_log(1)
    # Старая строка на месте, и комментарий у неё пустой: дорисовать его
    # задним числом не из чего
    assert len(moves) == 1
    assert moves[0].note == "Магазин одежды" and moves[0].about == ""

    # А новая операция комментарий уже несёт
    await db.log_card(1, -135, "pay", "Ювелирный магазин", 745,
                      1700000100, about="Покупка Кольцо силы")
    fresh = await db.card_log(1)
    assert fresh[0].about == "Покупка Кольцо силы"
    await db.close()


async def test_a_failed_connect_closes_the_base_behind_itself(old_db_path, monkeypatch):
    """Сорвалось обновление — соединение закрыто, и процесс может упасть.

    `aiosqlite` держит свой поток, и брошенное на полпути соединение не
    даёт процессу завершиться: бот висит вместо того, чтобы упасть с
    понятной ошибкой в логах. Именно так эта поломка и выглядела бы,
    случись она чуть иначе, — а висящий бот чинится дольше упавшего.
    """
    import bot.database as database_module

    monkeypatch.setattr(
        database_module,
        "SCHEMA",
        SCHEMA + "\nCREATE INDEX IF NOT EXISTS boom ON players(no_such_column);\n",
    )
    db = Database(old_db_path)

    with pytest.raises(sqlite3.OperationalError):
        await db.connect()

    assert db._conn is None, "соединение осталось открытым, и процесс не завершится"


def test_the_schema_holds_no_index_over_a_migrated_column():
    """Индексам по дописанным колонкам в `SCHEMA` не место.

    Проверяем не поведение, а правило: всякий `CREATE INDEX` в схеме
    обязан смотреть на колонку, которая в этой же схеме и заведена с
    самого начала. Колонка из `MIGRATIONS` там означает падение запуска
    на каждой живой базе — и ни один другой тест этого не увидит.

    Дописанных колонок у клуба два списка: свои у бойца и свои у машин,
    и правило одно на оба.
    """
    from bot.database import MIGRATIONS, Database

    migrated = {column for column, _ in MIGRATIONS}
    migrated |= {column for column, _ in Database.CAR_COLUMNS}
    indexes = re.findall(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX[^;]+;", SCHEMA, flags=re.I | re.S
    )
    assert indexes, "в схеме не нашлось ни одного индекса — проверка сломалась"
    for statement in indexes:
        for column in migrated:
            assert not re.search(rf"\b{column}\b", statement), (
                f"индекс по дописанной колонке {column} в SCHEMA: "
                "на живой базе её ещё нет, и запуск упадёт"
            )


# ---------- окна рейдов: в ключ добавился сам рейд ----------
#
# Рейдов в городе стало два, и окна у них могут начаться в один час: в
# среду в полдень открыты и казино, и стадион. Прежний ключ (боец, час)
# этого не различал, а дописать колонку в первичный ключ SQLite не умеет
# — таблицу приходится собирать заново и переливать строки. Это та же
# засада, что с индексом по счёту: на пустой базе новая схема создаётся
# сразу правильной, и без этих тестов поломку увидел бы только живой бот.

# Схема окон прошлой версии: ключ без рейда
OLD_RAID_WINDOW = """
CREATE TABLE raid_window (
    user_id   INTEGER NOT NULL,
    opened_at INTEGER NOT NULL,
    won       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, opened_at)
);
"""


@pytest.fixture
def old_windows_path(tmp_path):
    """База прошлой версии с двумя записями об окнах подвала."""
    path = tmp_path / "windows.db"
    legacy = re.sub(
        r"CREATE TABLE IF NOT EXISTS raid_window \(.*?\);",
        "",
        schema_without(BANK_COLUMNS),
        flags=re.S,
    )
    raw = sqlite3.connect(path)
    raw.executescript(legacy + OLD_RAID_WINDOW)
    raw.execute(
        "INSERT INTO players (user_id, nickname, class_code) "
        "VALUES (1, 'Тайлер', 'warrior')"
    )
    # одно окно отдано пропуском, во втором боец победил
    raw.execute("INSERT INTO raid_window (user_id, opened_at, won) VALUES (1, 100, 0)")
    raw.execute("INSERT INTO raid_window (user_id, opened_at, won) VALUES (1, 200, 1)")
    raw.commit()
    raw.close()
    return str(path)


async def test_old_raid_windows_become_the_cellars(old_windows_path):
    """Всё, что лежало в таблице до стадиона, — это подвал казино.

    Терять эти строки нельзя: по ним считается «одна победа на окно», и
    забытая победа пустила бы бойца в подвал второй раз за то же окно.
    """
    db = Database(old_windows_path)
    await db.connect()

    assert await db.raid_window(1, 100, "cellar") == {
        "user_id": 1, "raid": "cellar", "opened_at": 100, "won": 0
    }
    won = await db.raid_window(1, 200, "cellar")
    assert won and won["won"] == 1
    # а на стадионе этих окон нет: там боец ещё не был
    assert await db.raid_window(1, 200, "hooligans") is None
    await db.close()


async def test_after_the_upgrade_two_raids_share_one_hour(old_windows_path):
    """Главное, ради чего таблицу и пересобрали: один час, два рейда.

    До пересборки вторая вставка молча проваливалась об первичный ключ, и
    билет на матч сходил за талон казино.
    """
    db = Database(old_windows_path)
    await db.connect()

    assert await db.start_raid_window(1, 300, "cellar") is True
    assert await db.start_raid_window(1, 300, "hooligans") is True
    assert await db.start_raid_window(1, 300, "cellar") is False

    await db.close_raid_window(1, 300, "hooligans")
    assert (await db.raid_window(1, 300, "hooligans"))["won"] == 1
    assert (await db.raid_window(1, 300, "cellar"))["won"] == 0
    await db.close()


async def test_the_window_upgrade_runs_once_and_keeps_running(old_windows_path):
    """Перезапуск не должен ни падать, ни терять переписанное."""
    for _ in range(3):
        db = Database(old_windows_path)
        await db.connect()
        await db.close()

    db = Database(old_windows_path)
    await db.connect()
    assert (await db.raid_window(1, 200, "cellar"))["won"] == 1
    async with db.conn.execute("PRAGMA table_info(raid_window)") as cursor:
        columns = {row["name"] for row in await cursor.fetchall()}
    assert "raid" in columns
    # и старая таблица за собой не осталась
    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'raid_window_old'"
    ) as cursor:
        assert await cursor.fetchone() is None
    await db.close()


# Таблица групповых боёв прошлой версии: ветка была обязательной
OLD_BATTLES = """
CREATE TABLE battles (
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
"""


@pytest.fixture
def old_battles_path(tmp_path):
    """База прошлой версии с одним групповым боем из чата."""
    path = tmp_path / "battles.db"
    legacy = re.sub(
        r"CREATE TABLE IF NOT EXISTS battles \(.*?\);",
        "",
        schema_without(BANK_COLUMNS),
        flags=re.S,
    )
    raw = sqlite3.connect(path)
    # Таблица идёт первой: на неё в схеме смотрит индекс по ветке, и
    # без неё он не создастся
    raw.executescript(OLD_BATTLES + legacy)
    raw.execute(
        "INSERT INTO battles (id, chat_id, thread_id, kind, rounds, winner_team) "
        "VALUES (7, -100, NULL, 'team', 4, 1)"
    )
    raw.commit()
    raw.close()
    return str(path)


async def test_a_group_battle_may_now_go_without_a_thread(old_battles_path):
    """Главное, ради чего таблицу и пересобрали: бой без ветки.

    Групповые бои завелись в чате, и `chat_id` у них был всегда. Бой из
    мини-аппа идёт без ветки — и запись о нём падала об `NOT NULL`
    прямо в развязке, оставляя бойцов в кончившемся бою навсегда.
    """
    db = Database(old_battles_path)
    await db.connect()

    await db.conn.execute(
        "INSERT INTO battles (chat_id, kind, rounds) VALUES (NULL, 'royale', 3)"
    )
    await db.conn.commit()
    async with db.conn.execute(
        "SELECT chat_id, kind FROM battles ORDER BY id"
    ) as cursor:
        rows = [dict(one) for one in await cursor.fetchall()]
    # Прежний бой из чата на месте, рядом с ним — бой из аппа
    assert rows == [
        {"chat_id": -100, "kind": "team"},
        {"chat_id": None, "kind": "royale"},
    ]
    await db.close()


async def test_the_battle_upgrade_runs_once_and_keeps_running(old_battles_path):
    """Перезапуск не должен ни падать, ни терять записанные бои."""
    for _ in range(3):
        db = Database(old_battles_path)
        await db.connect()
        await db.close()

    db = Database(old_battles_path)
    await db.connect()
    rows = await db.recent_battles(-100)
    assert len(rows) == 1
    kept = rows[0]
    assert kept["id"] == 7 and kept["kind"] == "team" and kept["rounds"] == 4
    assert kept["winner_team"] == 1 and kept["draw"] == 0
    await db.close()


async def test_the_dead_family_garage_is_dropped(tmp_path):
    """Пустая таблица гаража уходит: место машины лежит у самой машины.

    `clan_cars` приехала пустой — писать в неё никто не успел, — и
    второй источник правды о том, где стоит машина, не нужен. Живая
    база, где она ещё есть, после обновления работает без неё.
    """
    path = tmp_path / "garage.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        schema_without(BANK_COLUMNS)
        + """
        CREATE TABLE IF NOT EXISTS clan_cars (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_id INTEGER NOT NULL,
            code    TEXT    NOT NULL,
            put_by  INTEGER NOT NULL DEFAULT 0,
            put_at  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS clan_cars_clan ON clan_cars(clan_id);
        """
    )
    raw.commit()
    raw.close()

    db = Database(str(path))
    await db.connect()
    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'clan_cars'"
    ) as cursor:
        assert await cursor.fetchone() is None, "мёртвая таблица осталась"
    # А у машин появились колонки парковки
    async with db.conn.execute("PRAGMA table_info(cars)") as cursor:
        columns = {row["name"] for row in await cursor.fetchall()}
    assert {"parked", "parked_id"} <= columns
    await db.close()


# Таблица машин первой версии: машина жила только при хозяине
OLD_CARS = """
CREATE TABLE cars (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    until   INTEGER NOT NULL DEFAULT 0,
    took_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, code)
);
"""


async def test_a_car_from_the_old_base_keeps_its_owner(tmp_path):
    """Купленная до парковок машина остаётся при хозяине, а не на ней."""
    path = tmp_path / "cars.db"
    legacy = re.sub(
        r"CREATE TABLE IF NOT EXISTS cars \(.*?\);",
        "",
        schema_without(BANK_COLUMNS),
        flags=re.S,
    )
    raw = sqlite3.connect(path)
    # Таблица идёт первой: на неё в схеме смотрят индексы
    raw.executescript(OLD_CARS + legacy)
    raw.execute(
        "INSERT INTO players (user_id, nickname, class_code) "
        "VALUES (1, 'Тайлер', 'warrior')"
    )
    raw.execute(
        "INSERT INTO cars (user_id, code, until, took_at) "
        "VALUES (1, 'street_compact', 0, 100)"
    )
    raw.commit()
    raw.close()

    db = Database(str(path))
    await db.connect()
    rows = await db.cars_of(1)
    assert len(rows) == 1
    assert rows[0]["code"] == "street_compact"
    assert rows[0]["parked"] == "" and rows[0]["parked_id"] == 0
    await db.close()
