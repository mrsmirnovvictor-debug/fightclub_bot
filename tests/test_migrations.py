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
    """
    from bot.database import MIGRATIONS

    migrated = {column for column, _ in MIGRATIONS}
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
