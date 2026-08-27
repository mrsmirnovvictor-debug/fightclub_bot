import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.database import Database  # noqa: E402
from bot.battle_service import BattleService  # noqa: E402
from bot.duel_service import DuelService  # noqa: E402
from bot.tournament_service import TournamentService  # noqa: E402
from tests.harness import BOT, DISPATCHER, SESSION  # noqa: E402


@pytest.fixture
async def db(tmp_path):
    """Чистая база на каждый тест."""
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def dispatcher_env(db):
    """Общий диспетчер, но со свежей базой и пустой историей вызовов."""
    config = Config(bot_token="42:TESTTOKEN", db_path=":memory:", turn_timeout=600)
    duels = DuelService(bot=BOT, db=db, config=config)
    battles = BattleService(bot=BOT, db=db, config=config)
    tournaments = TournamentService(bot=BOT, db=db, config=config, duels=duels)
    DISPATCHER["db"] = db
    DISPATCHER["duels"] = duels
    DISPATCHER["battles"] = battles
    DISPATCHER["tournaments"] = tournaments
    DISPATCHER["config"] = config
    SESSION.calls.clear()
    yield db, duels, SESSION
    await duels.shutdown()
    await battles.shutdown()
    await tournaments.shutdown()


@pytest.fixture
async def arena(dispatcher_env):
    """Псевдоним для групповых тестов: база, сервис дуэлей и лог вызовов."""
    return dispatcher_env


@pytest.fixture
def battles():
    """Сервис групповых боёв того же диспетчера."""
    return DISPATCHER["battles"]


@pytest.fixture
def tournaments():
    """Турнирный сервис того же диспетчера."""
    return DISPATCHER["tournaments"]
