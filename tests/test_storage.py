"""Как база разговаривает с диском: чем это стоит и когда она пишет.

Отдельный файл, потому что проверяется здесь не правило игры, а цена
запроса. На ноутбуке её не видно: локальный SSD синхронизируется за
микросекунды, и разница между настройками тонет в шуме. На Railway база
лежит на подключённом томе, и каждый fsync там стоит десятки
миллисекунд — ровно поэтому игра однажды и встала.

История такая. Каждая ручка мини-аппа начинается с чтения бойца, а
чтение бойца открывало **две сделки на запись**: `list_effects` и
`injury_of` безусловно стирали просроченное, даже когда стирать было
нечего. Вместе с журналом откатом и `synchronous = FULL` это давало
несколько fsync на каждый запрос, а `aiosqlite` держит одно соединение
на один поток — то есть все запросы выстраивались в очередь за самым
медленным. В логах это выглядело так: запросы, пришедшие в одну секунду,
заканчивались тоже в одну, через пять-десять секунд.

Поэтому здесь два вида проверок: чтение не пишет, и соединение настроено
под тот диск, на котором живёт.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from bot.database import Database
from bot.game.classes import get_class
from bot.game.health import now_ts
from bot.models import Player


def make_player(user_id: int = 1, nickname: str = "Тайлер") -> Player:
    fclass = get_class("warrior")
    return Player(
        user_id=user_id,
        nickname=nickname,
        class_code="warrior",
        level=5,
        **fclass.base_stats.as_dict(),
    )


class Counter:
    """Соглядатай за соединением: сколько сделок на запись оно открыло."""

    def __init__(self, db: Database) -> None:
        self.commits = 0
        self.writes = 0
        self._db = db
        self._execute = db.conn.execute
        self._commit = db.conn.commit

    def __enter__(self) -> "Counter":
        def execute(sql, *args, **kwargs):
            head = str(sql).lstrip().split(None, 1)[0].upper() if sql else ""
            if head in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
                self.writes += 1
            return self._execute(sql, *args, **kwargs)

        def commit(*args, **kwargs):
            self.commits += 1
            return self._commit(*args, **kwargs)

        self._db.conn.execute = execute
        self._db.conn.commit = commit
        return self

    def __exit__(self, *exc) -> None:
        self._db.conn.execute = self._execute
        self._db.conn.commit = self._commit


# ---------- чтение не пишет ----------


async def test_reading_a_fighter_writes_nothing(db):
    """Поднять бойца — это чтение. Ни одной записи, ни одного коммита.

    Это и есть та самая починка: бойца читает каждая ручка, и две сделки
    на запись за каждое чтение складывались в очередь, в которой стояли
    все остальные запросы.
    """
    await db.save_player(make_player())

    with Counter(db) as spy:
        player = await db.get_player(1)

    assert player is not None and player.nickname == "Тайлер"
    assert spy.writes == 0, "чтение бойца открыло сделку на запись"
    assert spy.commits == 0, "чтение бойца сделало коммит"


async def test_reading_a_fighter_with_live_effects_writes_nothing(db):
    """Действующий эликсир стирать не нужно — значит, и писать незачем."""
    await db.save_player(make_player())
    await db.set_effect(1, "boost_strength", now_ts() + 3600)

    with Counter(db) as spy:
        player = await db.get_player(1)

    assert [one.code for one in player.effects] == ["boost_strength"]
    assert spy.writes == 0 and spy.commits == 0


async def test_an_expired_effect_is_still_swept_away(db):
    """Просроченное всё так же стирается — иначе чтение копило бы мусор."""
    await db.save_player(make_player())
    await db.set_effect(1, "boost_strength", now_ts() - 1)
    await db.set_effect(1, "boost_agility", now_ts() + 3600)

    with Counter(db) as spy:
        player = await db.get_player(1)

    assert [one.code for one in player.effects] == ["boost_agility"]
    assert spy.writes == 1, "просроченное стирается одним запросом"
    # А второе чтение уже чистое: мусор убран, писать больше нечего
    with Counter(db) as again:
        await db.get_player(1)
    assert again.writes == 0 and again.commits == 0


async def test_an_expired_injury_is_still_swept_away(db):
    """То же и с травмой: отлежавшую стираем, живую не трогаем."""
    await db.save_player(make_player())
    await db.set_injury(1, "leg_bruise", now_ts() - 1)

    with Counter(db) as spy:
        player = await db.get_player(1)

    assert player.injury is None
    assert spy.writes == 1
    with Counter(db) as again:
        await db.get_player(1)
    assert again.writes == 0 and again.commits == 0


async def test_a_live_injury_survives_the_read(db):
    """Живую травму чтение не стирает — иначе боец лечился бы просмотром."""
    await db.save_player(make_player())
    await db.set_injury(1, "leg_bruise", now_ts() + 3600)

    with Counter(db) as spy:
        player = await db.get_player(1)

    assert player.injury is not None and player.injury.code == "leg_bruise"
    assert spy.writes == 0 and spy.commits == 0


# ---------- настройки под диск ----------


@asynccontextmanager
async def opened(path: str):
    """База, которая закроется, даже если проверка упала.

    Закрытие — в `finally` не для красоты: `aiosqlite` держит на
    соединении свой поток, и брошенное открытым оно не даёт прогону
    кончиться. Упавший тест тогда не падает, а вешает весь прогон — это
    стоило двух укусов, прежде чем попало сюда.
    """
    db = Database(path)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def pragma(db: Database, name: str):
    async with db.conn.execute(f"PRAGMA {name}") as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def test_the_file_database_runs_in_wal(tmp_path):
    """На файле база ведёт журнал в WAL и не синхронизирует каждый коммит.

    Журнал откатом на сетевом томе означает несколько fsync на коммит.
    WAL дописывает в конец отдельного файла, а `synchronous = NORMAL`
    снимает fsync с коммита вовсе: при обрыве питания теряются последние
    сделки, но база остаётся целой.
    """
    async with opened(str(tmp_path / "live.db")) as db:
        assert str(await pragma(db, "journal_mode")).lower() == "wal"
        assert int(await pragma(db, "synchronous")) == 1, "synchronous = NORMAL"
        # Занятую базу ждём, а не отказываем бойцу. Своей строки на это
        # нет и не нужно: драйвер открывает соединение с timeout=5.0.
        # Проверяем всё равно — важно само правило, а не кто его поставил
        assert int(await pragma(db, "busy_timeout")) >= 1000


async def test_memory_database_still_opens():
    """Память WAL не умеет, и это не беда: тесты гоняют именно её."""
    async with opened(":memory:") as db:
        await db.save_player(make_player())
        assert (await db.get_player(1)) is not None


async def test_wal_survives_a_restart(tmp_path):
    """Режим лежит в самом файле: второй запуск не должен его терять."""
    path = str(tmp_path / "live.db")
    async with opened(path) as first:
        await first.save_player(make_player())

    async with opened(path) as second:
        assert str(await pragma(second, "journal_mode")).lower() == "wal"
        assert (await second.get_player(1)) is not None
