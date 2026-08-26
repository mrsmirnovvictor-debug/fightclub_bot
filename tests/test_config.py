"""Настройки: адрес и порт мини-аппа берутся из окружения хостинга."""

import pytest

from bot.config import load_config

ENV_KEYS = (
    "BOT_TOKEN",
    "WEBAPP_URL",
    "WEBAPP_HOST",
    "WEBAPP_PORT",
    "MINIAPP_NAME",
    "PORT",
    "RAILWAY_PUBLIC_DOMAIN",
    "DB_PATH",
    "TURN_TIMEOUT",
    "CHALLENGE_TIMEOUT",
)


@pytest.fixture
def env(monkeypatch):
    """Чистое окружение с одним лишь токеном."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "42:TESTTOKEN")
    return monkeypatch


def test_token_is_required(env):
    env.delenv("BOT_TOKEN")
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_config()


def test_without_a_url_the_card_stays_off(env):
    config = load_config()
    assert config.webapp_url == ""
    assert config.webapp_enabled is False
    assert config.webapp_port == 8080


def test_railway_domain_becomes_the_card_address(env):
    env.setenv("RAILWAY_PUBLIC_DOMAIN", "fightclub-production.up.railway.app")
    config = load_config()
    assert config.webapp_url == "https://fightclub-production.up.railway.app"
    assert config.webapp_enabled is True


def test_explicit_url_wins_over_the_hosting_one(env):
    env.setenv("RAILWAY_PUBLIC_DOMAIN", "fightclub-production.up.railway.app")
    env.setenv("WEBAPP_URL", "https://club.example.ru/")
    assert load_config().webapp_url == "https://club.example.ru"


def test_hosting_port_wins_over_our_own(env):
    env.setenv("WEBAPP_PORT", "8080")
    env.setenv("PORT", "7777")
    assert load_config().webapp_port == 7777


def test_junk_port_falls_back_to_the_default(env):
    env.setenv("PORT", "не число")
    env.setenv("WEBAPP_PORT", "9090")
    assert load_config().webapp_port == 9090


def test_timeouts_and_paths_are_read(env):
    env.setenv("DB_PATH", "/data/fightclub.db")
    env.setenv("TURN_TIMEOUT", "45")
    env.setenv("CHALLENGE_TIMEOUT", "300")
    env.setenv("MINIAPP_NAME", "card")
    config = load_config()
    assert config.db_path == "/data/fightclub.db"
    assert (config.turn_timeout, config.challenge_timeout) == (45, 300)
    assert config.miniapp_name == "card"
