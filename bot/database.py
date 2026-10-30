"""Хранилище на SQLite: бойцы, арены и история дуэлей."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from bot.game.bank import CARD
from bot.game.economy import RATING_START
from bot.game.locations import FIGHT_CLUB
from bot.game.abilities import Loadout
from bot.game.equipment import MAX_WEAR, OwnedItem, Slot, get_item
from bot.game.clans import (
    Clan,
    Knock,
    Member,
    Petition,
    Rank,
    Roster,
    Standing,
    Stored,
)
from bot.game.driving import School
from bot.game.credit import Deposit, Loan
from bot.game.estate import Kept, Lease
from bot.game.post import Parcel
from bot.game.health import now_ts
from bot.game.injuries import ActiveInjury, get_injury
from bot.game.insurance import Policy
from bot.game.progress import QuestLog
from bot.game.turf import Owner
from bot.game.looks import MALE
from bot.game.modes import FightMode, mode_of
from bot.game.potions import ActiveEffect, get_potion
from bot.game.world import DEFAULT_CITY
from bot.models import CardMove, Player, Ring

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
    -- Где боец на карте города и куда идёт
    -- Когда мини-апп последний раз отзывался от имени этого бойца
    seen_at        INTEGER NOT NULL DEFAULT 0,
    location       TEXT    NOT NULL DEFAULT 'fight_club',
    travel_to      TEXT,
    arrives_at     INTEGER NOT NULL DEFAULT 0,
    -- Vegas Банк: счёт и карта. Деньги на счету — второй кошелёк бойца,
    -- и лежит он там же, где первый: в одной строке с наличными
    account_number  TEXT    NOT NULL DEFAULT '',
    account_balance INTEGER NOT NULL DEFAULT 0,
    -- Когда счёт открыт: кредит дают тому, кого банк знает хотя бы
    -- неделю. Ноль у счетов, открытых до этого правила — их банк знает
    -- давно, и спрашивать с них нечего
    account_at      INTEGER NOT NULL DEFAULT 0,
    card_at         INTEGER NOT NULL DEFAULT 0,
    card_paid_until INTEGER NOT NULL DEFAULT 0,
    pay_from        TEXT    NOT NULL DEFAULT 'card',
    -- Работа: одна на бойца, и потому живёт в его же строке
    job_code        TEXT    NOT NULL DEFAULT '',
    job_since       INTEGER NOT NULL DEFAULT 0,
    job_experience  INTEGER NOT NULL DEFAULT 0,
    job_week        INTEGER NOT NULL DEFAULT 0,
    job_minutes     INTEGER NOT NULL DEFAULT 0,
    job_salary      INTEGER NOT NULL DEFAULT 0,
    shift_until     INTEGER NOT NULL DEFAULT 0,
    shift_day       TEXT    NOT NULL DEFAULT '',
    shift_minutes   INTEGER NOT NULL DEFAULT 0,
    -- Учёба: курс один на бойца, как и работа, и живёт там же.
    -- `diplomas` — коды сданных курсов через запятую: их бывает семь, и
    -- таблица под семь слов дороже самих слов
    study_code      TEXT    NOT NULL DEFAULT '',
    study_since     INTEGER NOT NULL DEFAULT 0,
    study_lessons   INTEGER NOT NULL DEFAULT 0,
    study_day       TEXT    NOT NULL DEFAULT '',
    study_exam_at   INTEGER NOT NULL DEFAULT 0,
    study_tries     INTEGER NOT NULL DEFAULT 0,
    lesson_until    INTEGER NOT NULL DEFAULT 0,
    diplomas        TEXT    NOT NULL DEFAULT '',
    -- За рулём: код машины и до какого часа она на руках. Пусто — боец
    -- идёт пешком. Машины лежат в `cars`, а здесь — та, что под ним
    -- сейчас: её спрашивают и дорога, и карточка, то есть все и всегда
    car             TEXT    NOT NULL DEFAULT '',
    car_until       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    code     TEXT    NOT NULL,
    wear     INTEGER NOT NULL DEFAULT 0,
    max_wear INTEGER NOT NULL DEFAULT 20,
    slot     TEXT,
    -- Модификация: какой модификатор лёг на вещь и сколько выпало. Пусто —
    -- вещь как из лавки. Модифицируют один раз, поэтому пары полей хватает
    mod       TEXT    NOT NULL DEFAULT '',
    mod_value INTEGER NOT NULL DEFAULT 0,
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

-- Модификаторы лежат стопкой, как эликсиры: купленные заточки одинаковы
-- между собой, и различать их незачем — число выпадает при модификации,
-- а не при покупке.
CREATE TABLE IF NOT EXISTS mods (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, code)
);

-- Входы в клуб: сколько разных дней боец заглядывал за месяц и до какого
-- дня награды уже забраны. Считается по часам Москвы — там же, где
-- кончаются сутки рейда. `last_day` хранится строкой ISO, чтобы сутки
-- пережили перезапуск и не зависели от часов машины.
CREATE TABLE IF NOT EXISTS visits (
    user_id  INTEGER PRIMARY KEY,
    month    TEXT    NOT NULL DEFAULT '',
    days     INTEGER NOT NULL DEFAULT 0,
    last_day TEXT    NOT NULL DEFAULT '',
    claimed  INTEGER NOT NULL DEFAULT 0
);

-- Выученные приёмы: код и ступень, на которой боец его взял. Ступень
-- хранится потому, что от неё зависит цена в энергии: «Сильный удар»
-- стоит три своим и шесть, если взят кросс-классом на третьем уровне.
-- Слотов четыре, но потолок держит не база, а `Loadout`: забытый приём
-- уходит отсюда совсем.
CREATE TABLE IF NOT EXISTS abilities (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    tier    INTEGER NOT NULL,
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

-- Травма у бойца одна: сломанная рука не отменяет сломанную ногу, но
-- показывать и лечить две разом — это уже лазарет, а не клуб
CREATE TABLE IF NOT EXISTS injuries (
    user_id INTEGER PRIMARY KEY,
    code    TEXT    NOT NULL,
    until   INTEGER NOT NULL
);

-- Тренажёрный зал. Три таблицы, потому что у них разная жизнь: абонемент
-- кончается по часам, прогресс копится навсегда, а тренировка живёт
-- Запрет подавать заявку: неделя после отказа на тесте и после
-- увольнения. Строка на пару «боец и вакансия»: отказ в баре не мешает
-- проситься на почту
-- Заявки на кресло начальника: город не нанимает их сам, их назначает
-- мэр. Заявка живёт до назначения или до того, как боец её заберёт;
-- на одно кресло от бойца одна
-- Дела об угоне: по ним судит суд, а не участок. Номер в деле тот,
-- что стоял на машине в час угона: перебитый знак дело не закрывает
CREATE TABLE IF NOT EXISTS theft_cases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thief_id   INTEGER NOT NULL,
    owner_id   INTEGER NOT NULL,
    car_id     INTEGER NOT NULL,
    plate      TEXT    NOT NULL DEFAULT '',
    code       TEXT    NOT NULL DEFAULT '',
    stolen_at  INTEGER NOT NULL DEFAULT 0,
    filed_at   INTEGER NOT NULL DEFAULT 0,
    seized_at  INTEGER NOT NULL DEFAULT 0,
    judged_at  INTEGER NOT NULL DEFAULT 0,
    judge_id   INTEGER NOT NULL DEFAULT 0,
    fine       INTEGER NOT NULL DEFAULT 0,
    jail_hours INTEGER NOT NULL DEFAULT 0,
    served_at  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS theft_thief ON theft_cases(thief_id);
CREATE INDEX IF NOT EXISTS theft_owner ON theft_cases(owner_id);

-- «Чёрный пуск»: рейд на электростанцию. Весь изменчивый состав
-- инстанса лежит одним json в `state`, и это не лень, а замок: рейд
-- читается и пишется целиком под одной транзакцией, поэтому две
-- команды не могут выдать предмет дважды или открыть два боя. Снаружи
-- json видят только служба и экран; база хранит рядом то, по чему
-- ищут и что обязано быть уникальным
CREATE TABLE IF NOT EXISTS blackout_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle     TEXT    NOT NULL DEFAULT '',
    phase     TEXT    NOT NULL DEFAULT 'lobby',
    leader_id INTEGER NOT NULL DEFAULT 0,
    opened_at INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER NOT NULL DEFAULT 0,
    closed_at INTEGER NOT NULL DEFAULT 0,
    version   INTEGER NOT NULL DEFAULT 0,
    state     TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS blackout_phase ON blackout_runs(phase);

-- Кто в каком рейде. Отдельной таблицей, потому что по ней ищут: «мой
-- рейд» — это запрос по игроку, а не разбор json всех инстансов
CREATE TABLE IF NOT EXISTS blackout_members (
    run_id  INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    left_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, user_id)
);

CREATE INDEX IF NOT EXISTS blackout_member ON blackout_members(user_id);

-- Выплаты. Уникальность «игрок + цикл + часть награды» держит первичный
-- ключ, а не проверка в коде: повторный запрос и второй заход в том же
-- цикле не должны платить дважды
CREATE TABLE IF NOT EXISTS blackout_rewards (
    user_id INTEGER NOT NULL,
    cycle   TEXT    NOT NULL,
    stage   TEXT    NOT NULL,
    amount  INTEGER NOT NULL DEFAULT 0,
    run_id  INTEGER NOT NULL DEFAULT 0,
    paid_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, cycle, stage)
);

-- Выполненные команды: по `command_id` повторный запрос отдаёт прежний
-- ответ, а не делает второй шаг. Это и есть идемпотентность, которой
-- требует кооператив: сеть теряет ответы, клиент жмёт ещё раз
CREATE TABLE IF NOT EXISTS blackout_commands (
    run_id     INTEGER NOT NULL,
    command_id TEXT    NOT NULL,
    said       TEXT    NOT NULL DEFAULT '',
    at         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, command_id)
);

CREATE TABLE IF NOT EXISTS job_bids (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    at      INTEGER NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS job_blocks (
    user_id INTEGER NOT NULL,
    code    TEXT    NOT NULL,
    until   INTEGER NOT NULL,
    reason  TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, code)
);

-- пятнадцать минут и исчезает. Расписание не хранится вовсе — оно
-- разыгрывается по номеру недели, см. bot/game/gym.py
CREATE TABLE IF NOT EXISTS gym_passes (
    user_id INTEGER PRIMARY KEY,
    until   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gym_progress (
    user_id INTEGER NOT NULL,
    stat    TEXT    NOT NULL,
    points  INTEGER NOT NULL DEFAULT 0,
    ups     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, stat)
);

-- Идущая тренировка: одна на бойца. `slot` — ключ клетки расписания, по
-- нему и видно, что в этом слоте боец уже отработал
CREATE TABLE IF NOT EXISTS gym_visits (
    user_id INTEGER NOT NULL,
    slot    TEXT    NOT NULL,
    stat    TEXT    NOT NULL,
    until   INTEGER NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, slot)
);

-- Документы бойца. Пока их вид один — полис страхования, — и у него своя
-- таблица, а не столбец в players: документы будут копиться, и у каждого
-- свои поля. Один полис на бойца: второй такой же не выдают, его продлевают
CREATE TABLE IF NOT EXISTS policies (
    user_id     INTEGER PRIMARY KEY,
    issued      INTEGER NOT NULL,
    until       INTEGER NOT NULL,
    auto_renew  INTEGER NOT NULL DEFAULT 1
);

-- Автошкола: курс, попытка экзамена и выданные права. Одна строка на
-- бойца, как у полиса: это документ, а не характеристика, и в players
-- ему места нет. Попытка живёт здесь же — она короткая, пятнадцать
-- минут, и держать под неё вторую таблицу незачем
CREATE TABLE IF NOT EXISTS driving (
    user_id    INTEGER PRIMARY KEY,
    course_at  INTEGER NOT NULL DEFAULT 0,
    exam_until INTEGER NOT NULL DEFAULT 0,
    step       INTEGER NOT NULL DEFAULT 0,
    wrong      INTEGER NOT NULL DEFAULT 0,
    passed_at  INTEGER NOT NULL DEFAULT 0,
    licence_at INTEGER NOT NULL DEFAULT 0,
    -- Час последней неудачной попытки: по нему считают цену пересдачи и
    -- то, не в этом ли окне боец уже сидел за билетом
    tried_at   INTEGER NOT NULL DEFAULT 0
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

-- Авторынок: та же комиссионка, но для машин. Износа у машины нет, и
-- колонок под него тоже: кроме кода и цены, записывать нечего.
-- Выставленная машина с бойца снята — она стоит на рынке, а не у него
CREATE TABLE IF NOT EXISTS car_market (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    price     INTEGER NOT NULL,
    listed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_car_market_seller
    ON car_market(seller_id, id DESC);

-- Зарядка на АЗС: три колонки на весь город. Слот — он же ключ: двух
-- машин в одной колонке не бывает, и очередь к ней — часть игры
CREATE TABLE IF NOT EXISTS charging (
    slot      INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    percent   INTEGER NOT NULL,
    ready_at  INTEGER NOT NULL,
    started_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_charging_user ON charging(user_id);

-- Боевая ведомость: что боец сделал своими руками за всю жизнь.
-- Точные удары, уворот, криты и удержанные блоки — ими платят за приём
-- в школе боевых искусств
CREATE TABLE IF NOT EXISTS deeds (
    user_id INTEGER NOT NULL,
    deed    TEXT    NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, deed)
);

-- На что боец записан в школе. Строк у него может быть несколько, и
-- это не недосмотр: учится разом один приём, но набранный ждёт
-- подтверждения сколько угодно долго, и всё это время боец учит
-- следующий. Готов приём или ещё нет — видно из самой ведомости
-- (`deeds` минус `baseline`), и хранить это отдельно значило бы держать
-- две правды об одном
CREATE TABLE IF NOT EXISTS dojo (
    user_id    INTEGER NOT NULL,
    code       TEXT    NOT NULL,
    deed       TEXT    NOT NULL,
    baseline   INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, code)
);

CREATE INDEX IF NOT EXISTS idx_dojo_user ON dojo(user_id, started_at);

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
-- побеждают в окне тоже один раз. Ключ — боец, рейд и начало окна,
-- поэтому повторный заход в то же окно ничего не списывает.
--
-- Рейд в ключе не для красоты: рейдов в городе два, расписания у них
-- свои, и окна могут начаться в один и тот же час — в среду в полдень
-- открыты и казино, и стадион. Без кода рейда один билет прошёл бы за
-- два, а победа на стадионе закрыла бы подвал.
CREATE TABLE IF NOT EXISTS raid_window (
    user_id   INTEGER NOT NULL,
    raid      TEXT    NOT NULL DEFAULT 'cellar',
    opened_at INTEGER NOT NULL,  -- начало окна, секунды эпохи
    won       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, raid, opened_at)
);

CREATE TABLE IF NOT EXISTS battles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Ветки может не быть вовсе: групповой бой, собранный в мини-аппе,
    -- идёт без чата, и писать его всё равно нужно
    chat_id    INTEGER,
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
-- Ветки, куда бот приносит объявления о начатых боях и рейдах. На каждый
-- вид событий своя ветка, размечает их админ группы. Ветки нет — объявлять
-- некуда, и бот молчит: это не ошибка, а не настроенный клуб.
CREATE TABLE IF NOT EXISTS announce_threads (
    chat_id   INTEGER NOT NULL,
    kind      TEXT    NOT NULL,  -- fist, armed, raid
    thread_id INTEGER,
    title     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (chat_id, kind)
);

CREATE TABLE IF NOT EXISTS noticeboards (
    chat_id   INTEGER PRIMARY KEY,
    thread_id INTEGER,
    title     TEXT NOT NULL DEFAULT ''
);

-- Кланы мафии. Клан заводят в особняке за тысячу кредитов, и у него
-- свой знак — один из нарисованных (`bot/content/clans.py`). Знак
-- достаётся одной семье: отсюда и уникальный индекс по нему
CREATE TABLE IF NOT EXISTS clans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    name_key    TEXT    NOT NULL,
    sign        TEXT    NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT 0,
    renamed_at  INTEGER NOT NULL DEFAULT 0,
    vault       INTEGER NOT NULL DEFAULT 0,
    don         INTEGER NOT NULL
);

-- Знак один на клан: двух семей под одной картинкой в городе не бывает
CREATE UNIQUE INDEX IF NOT EXISTS clans_sign ON clans(sign);

-- Имя клана одно на город, и регистр тут ни при чём: «Коза Ностра» и
-- «коза ностра» — один и тот же клан. Ключ считает питон, а не база:
-- sqlite складывает регистр только у латиницы, и для кириллицы
-- `COLLATE NOCASE` не работает вовсе
CREATE UNIQUE INDEX IF NOT EXISTS clans_name ON clans(name_key);

-- Кто в клане и каким чином. Один клан на бойца — отсюда и первичный
-- ключ по бойцу: в двух семьях сразу не состоят
CREATE TABLE IF NOT EXISTS clan_members (
    user_id   INTEGER PRIMARY KEY,
    clan_id   INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
    rank      TEXT    NOT NULL DEFAULT 'soldier',
    joined_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS clan_members_clan ON clan_members(clan_id);

-- Заявки и приглашения: одна таблица на обе дороги в семью. `kind`
-- говорит, кто сделал первый шаг, `by_id` — кто позвал (у заявки ноль).
-- Пара «клан и боец» одна: позвали в тот же клан, куда боец и так
-- стучался, — это по-прежнему одна запись
CREATE TABLE IF NOT EXISTS clan_petitions (
    clan_id    INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'request',
    created_at INTEGER NOT NULL DEFAULT 0,
    by_id      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (clan_id, user_id)
);

CREATE INDEX IF NOT EXISTS clan_petitions_user ON clan_petitions(user_id);

-- Склад семьи: тридцать вещей на клан. Вещь не копируется — она
-- переезжает сюда из инвентаря целиком, со своим износом и своей
-- модификацией, и так же возвращается обратно
CREATE TABLE IF NOT EXISTS clan_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_id   INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
    code      TEXT    NOT NULL,
    wear      INTEGER NOT NULL DEFAULT 0,
    max_wear  INTEGER NOT NULL DEFAULT 20,
    mod       TEXT    NOT NULL DEFAULT '',
    mod_value INTEGER NOT NULL DEFAULT 0,
    put_by    INTEGER NOT NULL DEFAULT 0,
    put_at    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS clan_items_clan ON clan_items(clan_id);

-- Склад службы: пятнадцать вещей на кабинет. Устроен как склад семьи —
-- вещь переезжает сюда из инвентаря целиком, со своим износом и своей
-- модификацией, — и отличается одним: у службы нет номера, её зовут по
-- коду (`police`), и кабинет у неё один на город
CREATE TABLE IF NOT EXISTS service_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    service   TEXT    NOT NULL,
    code      TEXT    NOT NULL,
    wear      INTEGER NOT NULL DEFAULT 0,
    max_wear  INTEGER NOT NULL DEFAULT 20,
    mod       TEXT    NOT NULL DEFAULT '',
    mod_value INTEGER NOT NULL DEFAULT 0,
    put_by    INTEGER NOT NULL DEFAULT 0,
    put_at    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS service_items_service ON service_items(service);

-- Снятая квартира. Строка есть — квартира занята; свободные в базе не
-- лежат вовсе, их считает планировка (`bot.game.estate`). Один боец —
-- одна квартира, и это держит сама база, а не проверка в службе
CREATE TABLE IF NOT EXISTS flats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    house      TEXT    NOT NULL,
    number     INTEGER NOT NULL,
    user_id    INTEGER NOT NULL UNIQUE REFERENCES players(user_id) ON DELETE CASCADE,
    rooms      INTEGER NOT NULL,
    rented_at  INTEGER NOT NULL DEFAULT 0,
    paid_until INTEGER NOT NULL DEFAULT 0,
    vault      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (house, number)
);

CREATE INDEX IF NOT EXISTS flats_house ON flats(house);

-- Кладовая квартиры: шесть вещей на комнату. Вещь переезжает сюда из
-- инвентаря целиком, со своим износом и модификацией, и так же
-- возвращается — и при выселении, и когда квартиру освобождают за
-- неуплату
CREATE TABLE IF NOT EXISTS flat_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    flat_id   INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    code      TEXT    NOT NULL,
    wear      INTEGER NOT NULL DEFAULT 0,
    max_wear  INTEGER NOT NULL DEFAULT 20,
    mod       TEXT    NOT NULL DEFAULT '',
    mod_value INTEGER NOT NULL DEFAULT 0,
    put_at    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS flat_items_flat ON flat_items(flat_id);

-- Посылка на почте. Вещь переезжает сюда из инвентаря целиком и так же
-- выдаётся — получателю или, если срок вышел, обратно отправителю.
-- `keep_until` ноль — до востребования; `back` — посылка едет назад
CREATE TABLE IF NOT EXISTS parcels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL,
    to_id      INTEGER NOT NULL,
    code       TEXT    NOT NULL,
    wear       INTEGER NOT NULL DEFAULT 0,
    max_wear   INTEGER NOT NULL DEFAULT 20,
    mod        TEXT    NOT NULL DEFAULT '',
    mod_value  INTEGER NOT NULL DEFAULT 0,
    price      INTEGER NOT NULL DEFAULT 0,
    sent_at    INTEGER NOT NULL DEFAULT 0,
    keep_until INTEGER NOT NULL DEFAULT 0,
    back       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS parcels_to ON parcels(to_id);
CREATE INDEX IF NOT EXISTS parcels_from ON parcels(from_id);

-- Выписка по карте: всё, что прошло по счёту. Наличные сюда не
-- попадают — они ходят из рук в руки и следа не оставляют. `amount`
-- со знаком: минус — списание, плюс — приход; `balance` — остаток
-- после операции, чтобы выписку можно было сверить глазами
CREATE TABLE IF NOT EXISTS card_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
    at      INTEGER NOT NULL,
    amount  INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    note    TEXT    NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 0,
    -- За что именно: «Покупка Кольцо силы». `note` отвечает «где»
    about   TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS card_log_user ON card_log(user_id, at);

-- Территории: по строке на точку города. Владелец — пара «вид» и «кто»:
-- `clan`/номер или `service`/код. Два разных вида владельцев в одной
-- колонке были бы путаницей, а две таблицы на одно и то же — двумя
-- правдами. `paid_until` — час, до которого доход уже начислен: остаток
-- суток не пропадает, он переезжает в следующий расчёт
CREATE TABLE IF NOT EXISTS turf (
    code       TEXT    PRIMARY KEY,
    owner_kind TEXT    NOT NULL DEFAULT 'none',
    owner_key  TEXT    NOT NULL DEFAULT '',
    taken_at   INTEGER NOT NULL DEFAULT 0,
    paid_until INTEGER NOT NULL DEFAULT 0,
    -- С какого часа на точке никого не стоит. Ноль — охрана на месте.
    -- По этому часу и считается, сколько семья держит дом впустую:
    -- сутки без человека, и дом возвращается городу
    empty_since INTEGER NOT NULL DEFAULT 0,
    -- До какого часа время владения уже записано в бонусный журнал.
    -- Секунды, а не часы: обзор заглядывает часто, и округление на
    -- каждом взгляде съедало бы по полчаса
    counted_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS turf_owner ON turf(owner_kind, owner_key);

-- Казна городской службы. У клана сейф лежит в его же строке, а у
-- службы своей строки нет вовсе: служба — это взгляд на рабочие места,
-- и состава у неё не хранится. Казна — единственное, что у неё своё.
-- `split` — как начальник велел делить недельный бонусный пулл
CREATE TABLE IF NOT EXISTS service_vault (
    code   TEXT    PRIMARY KEY,
    vault  INTEGER NOT NULL DEFAULT 0,
    split  TEXT    NOT NULL DEFAULT 'even'
);

-- Турнирная арена: по строке на неделю. Неделя — дата воскресенья, в
-- которое играют; расписание внутри дня считается, а не хранится
CREATE TABLE IF NOT EXISTS arena_weeks (
    week       TEXT    PRIMARY KEY,
    -- open — идёт запись, live — сетка посеяна и идут круги,
    -- done — сыграно, void — не собрались и вернули взносы
    state      TEXT    NOT NULL DEFAULT 'open',
    pot        INTEGER NOT NULL DEFAULT 0,
    rounds     INTEGER NOT NULL DEFAULT 0,
    winner_id  INTEGER,
    runner_id  INTEGER,
    started_at INTEGER NOT NULL DEFAULT 0,
    ended_at   INTEGER NOT NULL DEFAULT 0
);

-- Кто записался: уровень на час записи (по нему сеют), место в посеве
-- и чем турнир для бойца кончился
CREATE TABLE IF NOT EXISTS arena_players (
    week      TEXT    NOT NULL,
    user_id   INTEGER NOT NULL,
    level     INTEGER NOT NULL DEFAULT 1,
    seed      INTEGER NOT NULL DEFAULT 0,
    out_round INTEGER NOT NULL DEFAULT 0,
    place     INTEGER NOT NULL DEFAULT 0,
    paid      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (week, user_id)
);

-- Пары сетки: круг, место в круге, кто с кем и чем кончилось
CREATE TABLE IF NOT EXISTS arena_matches (
    week       TEXT    NOT NULL,
    round      INTEGER NOT NULL,
    slot       INTEGER NOT NULL,
    red_id     INTEGER,
    blue_id    INTEGER,
    winner_id  INTEGER,
    -- waiting — ждут выхода, live — дерутся, done — сыграно
    state      TEXT    NOT NULL DEFAULT 'waiting',
    red_ready  INTEGER NOT NULL DEFAULT 0,
    blue_ready INTEGER NOT NULL DEFAULT 0,
    opened_at  INTEGER NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (week, round, slot)
);

-- Недельный журнал бонусов: по строке на сторону, неделю и мерку.
-- Стороны тут те же, что у точек, — `clan:3` или `service:police`, — и
-- журнал на обе один: считается в нём одно и то же, сделанное за
-- неделю, и разводить его по таблицам значило бы писать дважды
CREATE TABLE IF NOT EXISTS bonus_weeks (
    side_kind TEXT    NOT NULL,
    side_key  TEXT    NOT NULL,
    week      INTEGER NOT NULL,
    metric    TEXT    NOT NULL,
    value     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (side_kind, side_key, week, metric)
);

-- Что за неделю уже выдано. Строка есть — пулл роздан, и второй раз
-- его не дадут: отметка и проверка тут один запрос
CREATE TABLE IF NOT EXISTS bonus_paid (
    side_kind TEXT    NOT NULL,
    side_key  TEXT    NOT NULL,
    week      INTEGER NOT NULL,
    amount    INTEGER NOT NULL DEFAULT 0,
    paid_at   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (side_kind, side_key, week)
);

-- Лицензии на ограбление: пачки прав, у которых нет срока. `attacks` —
-- сколько нападений ещё осталось, `bought` — сколько пачек боец взял
-- за всю жизнь, и это число не про остаток, а про то, кто сколько
-- покупал. Недели тут нет нарочно: лицензия не кончается с ней
CREATE TABLE IF NOT EXISTS rob_passes (
    user_id INTEGER PRIMARY KEY,
    attacks INTEGER NOT NULL DEFAULT 0,
    bought  INTEGER NOT NULL DEFAULT 0
);

-- Недельные лицензии старого правила. Таблицу не трогаем: по ней видно,
-- что с базой уже случалось, а читать её больше некому
CREATE TABLE IF NOT EXISTS rob_licences (
    user_id INTEGER NOT NULL,
    week    INTEGER NOT NULL,
    bought  INTEGER NOT NULL DEFAULT 0,
    spent   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, week)
);

-- Покой ограбленного: час последнего нападения, кто бы ни победил.
-- Строки нет — бойца не трогали вовсе
CREATE TABLE IF NOT EXISTS rob_shield (
    user_id   INTEGER PRIMARY KEY,
    robbed_at INTEGER NOT NULL DEFAULT 0
);

-- Недельный счёт семьи: сколько нападений довели до развязки и выдана
-- ли за это премия. Премия одна на неделю, и `paid` — именно про неё,
-- а не про сумму: сумму знает `bot.game.robbery`
CREATE TABLE IF NOT EXISTS clan_weeks (
    clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
    week    INTEGER NOT NULL,
    attacks INTEGER NOT NULL DEFAULT 0,
    paid    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (clan_id, week)
);

-- Дежурная часть: по строке на каждое нападение, доведённое до
-- развязки. Полиция видит их все — прятать от неё нечего. `filed_at`
-- — заявление жертвы: пока оно не написано, задерживать не за что;
-- `arrested_at` — задержание; `repaid_at` — жертва забрала своё
CREATE TABLE IF NOT EXISTS police_cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    victim_id   INTEGER NOT NULL,
    suspect_id  INTEGER NOT NULL,
    place       TEXT    NOT NULL DEFAULT '',
    loot        INTEGER NOT NULL DEFAULT 0,
    won         INTEGER NOT NULL DEFAULT 0,
    robbed_at   INTEGER NOT NULL DEFAULT 0,
    filed_at    INTEGER NOT NULL DEFAULT 0,
    arrested_at INTEGER NOT NULL DEFAULT 0,
    arrested_by INTEGER NOT NULL DEFAULT 0,
    repaid_at   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS police_cases_suspect ON police_cases(suspect_id);
CREATE INDEX IF NOT EXISTS police_cases_victim ON police_cases(victim_id);

-- Тюрьма: до какого часа сидит и в который раз. Счёт не обнуляется —
-- срок растёт от того, сколько раз человека уже брали
CREATE TABLE IF NOT EXISTS jail (
    user_id INTEGER PRIMARY KEY,
    until   INTEGER NOT NULL DEFAULT 0,
    times   INTEGER NOT NULL DEFAULT 0
);

-- Недельный счёт службы: задержаний за неделю и роздана ли копилка.
-- У клана такой счёт свой (`clan_weeks`), и это не дубль: у семьи
-- считаются нападения, у службы — задержания
CREATE TABLE IF NOT EXISTS service_weeks (
    code    TEXT    NOT NULL,
    week    INTEGER NOT NULL,
    arrests INTEGER NOT NULL DEFAULT 0,
    paid    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, week)
);

-- Выборы мэра. Сами выборы не хранятся — их считает календарь
-- (`bot.game.elections`); строка заводится в тот час, когда голоса
-- сведены. `day` — дата голосования по Москве, она же ключ
CREATE TABLE IF NOT EXISTS elections (
    day       TEXT    PRIMARY KEY,
    closed_at INTEGER NOT NULL DEFAULT 0,
    winner_id INTEGER NOT NULL DEFAULT 0,
    votes     INTEGER NOT NULL DEFAULT 0,
    turnout   INTEGER NOT NULL DEFAULT 0
);

-- Кандидаты этих выборов. `dropped_at` — снят: кандидат сел в тюрьму,
-- пока шло выдвижение, и в бюллетень не попал
CREATE TABLE IF NOT EXISTS candidates (
    day        TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    program    TEXT    NOT NULL DEFAULT '',
    filed_at   INTEGER NOT NULL DEFAULT 0,
    dropped_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);

-- Голоса. Один боец — один голос, и это держит сама база
CREATE TABLE IF NOT EXISTS ballots (
    day     TEXT    NOT NULL,
    user_id INTEGER NOT NULL,
    for_id  INTEGER NOT NULL,
    cast_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);

CREATE INDEX IF NOT EXISTS ballots_for ON ballots(day, for_id);

-- Кто сейчас мэр. Строка одна на город: мэр один, и кресло у него одно
CREATE TABLE IF NOT EXISTS mayor (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    user_id INTEGER NOT NULL DEFAULT 0,
    since   INTEGER NOT NULL DEFAULT 0,
    day     TEXT    NOT NULL DEFAULT ''
);

-- Нанятые: наёмники семьи и патрульные города. Принадлежат бойцу, а
-- не стороне: платит за них человек, и в его счёт они идут. `spot`
-- пусто — нанятый в резерве, не выставлен никуда
CREATE TABLE IF NOT EXISTS hirelings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    side     TEXT    NOT NULL,
    grade    TEXT    NOT NULL,
    spot     TEXT    NOT NULL DEFAULT '',
    hired_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS hirelings_owner ON hirelings(owner_id);
CREATE INDEX IF NOT EXISTS hirelings_spot ON hirelings(spot);

-- Разовые работы по живой базе: сброс точек, пересчёт — всё, что
-- делается один раз и не должно повториться при перезапуске. Миграции
-- колонок лежат отдельно (`MIGRATIONS`): там форма, тут данные
CREATE TABLE IF NOT EXISTS chores (
    code    TEXT PRIMARY KEY,
    done_at INTEGER NOT NULL DEFAULT 0
);

-- Квесты бойца: по строке на взятое задание. Нет строки — задание ещё
-- не предлагали или от него отказались. `marks` — отметки города: что
-- он уже видел по этому заданию (бой выигран, дверь открыта, вопрос
-- отвечен). Хранится строкой через запятую: отметок у задания две-три,
-- и таблица на них была бы дороже самих отметок
CREATE TABLE IF NOT EXISTS quests (
    user_id INTEGER NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
    code    TEXT    NOT NULL,
    state   TEXT    NOT NULL DEFAULT 'taken',
    marks   TEXT    NOT NULL DEFAULT '',
    taken_at INTEGER NOT NULL DEFAULT 0,
    done_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, code)
);

CREATE INDEX IF NOT EXISTS quests_user ON quests(user_id, state);

-- Вклады: по строке на сделку. Вкладов у бойца может быть несколько —
-- это не кошелёк, а сделка. Срок обещан, но не заперт: забрать можно
-- когда угодно, досрочный вклад просто не приносит процентов
CREATE TABLE IF NOT EXISTS deposits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    months     INTEGER NOT NULL,
    opened_at  INTEGER NOT NULL DEFAULT 0,
    paid_until INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS deposits_user ON deposits(user_id);

-- Кредит: один на бойца. Платёж считается от его дохода, и второй
-- кредит ломал бы само условие. `left_pays` — сколько платежей ещё не
-- внесено, `due_at` — час следующего списания с карты. `owed` —
-- платежи, которых на карте не хватило: они ждут денег и обрастают
-- пенями, а `penalty_at` помнит, по какой час пени уже начислены
CREATE TABLE IF NOT EXISTS loans (
    user_id    INTEGER PRIMARY KEY REFERENCES players(user_id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    months     INTEGER NOT NULL,
    payment    INTEGER NOT NULL,
    left_pays  INTEGER NOT NULL,
    taken_at   INTEGER NOT NULL DEFAULT 0,
    due_at     INTEGER NOT NULL DEFAULT 0,
    owed       INTEGER NOT NULL DEFAULT 0,
    penalty    INTEGER NOT NULL DEFAULT 0,
    penalty_at INTEGER NOT NULL DEFAULT 0
);

-- Общежитие: комната на студента, пока он учится. Платы и срока в
-- сутках у неё нет — срок это сама учёба, и записан он курсом. Учёба
-- кончилась — ставим `closed_at`: комната заперта, а вещи в ней ждут
-- хозяина у администрации
CREATE TABLE IF NOT EXISTS dorm_rooms (
    user_id   INTEGER PRIMARY KEY REFERENCES players(user_id) ON DELETE CASCADE,
    number    INTEGER NOT NULL,
    course    TEXT    NOT NULL DEFAULT '',
    given_at  INTEGER NOT NULL DEFAULT 0,
    closed_at INTEGER NOT NULL DEFAULT 0,
    vault     INTEGER NOT NULL DEFAULT 0
);

-- Что лежит в комнате. Полки те же, что в квартире, и строка та же:
-- вещь переезжает сюда из рюкзака целиком и так же возвращается
CREATE TABLE IF NOT EXISTS dorm_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES dorm_rooms(user_id) ON DELETE CASCADE,
    code      TEXT    NOT NULL,
    wear      INTEGER NOT NULL DEFAULT 0,
    max_wear  INTEGER NOT NULL DEFAULT 20,
    mod       TEXT    NOT NULL DEFAULT '',
    mod_value INTEGER NOT NULL DEFAULT 0,
    put_at    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS dorm_items_user ON dorm_items(user_id);

-- Машины бойца: по строке на машину. `until` — до какого часа она его:
-- ноль у купленной (своя насовсем), срок у снятой в салоне и у
-- служебной, взятой на смену. Служебных в гараже столько, сколько
-- мест: сами места не хранятся, они считаются строками этой таблицы
CREATE TABLE IF NOT EXISTS cars (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
    code    TEXT    NOT NULL,
    until   INTEGER NOT NULL DEFAULT 0,
    took_at INTEGER NOT NULL DEFAULT 0,
    -- Где машина стоит: пусто — при хозяине, `clan` — на парковке
    -- особняка, `flat` — на месте при своей квартире. `parked_id`
    -- называет, чья это парковка: семья или съём. Хозяин у машины один
    -- и тот же, где бы она ни стояла: парковка — место, а не передача
    parked    TEXT    NOT NULL DEFAULT '',
    parked_id INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, code)
);

CREATE INDEX IF NOT EXISTS cars_code ON cars(code);
-- Индекса по стоянке здесь нет нарочно: колонки `parked` и `parked_id`
-- дописываются миграцией, а на живой базе `CREATE TABLE IF NOT EXISTS`
-- не делает ничего — и индекс по ещё не существующей колонке уронил бы
-- запуск. Он заводится там же, где колонки (`_migrate_cars`)

-- Что уже объявляли. Ключ — код объявления и ветка: перезапуск бота
-- ничего не задваивает, а новая группа получает всё с начала
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
    "raid_wins, raid_fights, seen_at, location, travel_to, arrives_at, "
    "city, birthplace, pro_until, gender, created_at, "
    "account_number, account_balance, account_at, "
    "card_at, card_paid_until, pay_from, "
    "job_code, job_since, job_experience, job_week, job_minutes, job_salary, "
    "account_debt, shift_until, "
    "shift_day, shift_minutes, "
    "study_code, study_since, study_lessons, study_day, study_exam_at, "
    "study_tries, lesson_until, diplomas, car, car_until"
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
    # Все, кто завёл бойца до карты, стоят там же, где начинают новые
    ("location", f"TEXT NOT NULL DEFAULT '{FIGHT_CLUB}'"),
    ("travel_to", "TEXT"),
    ("arrives_at", "INTEGER NOT NULL DEFAULT 0"),
    ("seen_at", "INTEGER NOT NULL DEFAULT 0"),
    # Vegas Банк: счёт и карта лежат на бойце, а не отдельной таблицей.
    # Деньги на счету — это второй кошелёк, и хранить его там же, где
    # первый, значит, что списание с любого из них сохраняется одним
    # `save_player`, а перевод между бойцами — одной сделкой на две строки
    ("account_number", "TEXT NOT NULL DEFAULT ''"),  # пусто — счёта нет
    ("account_balance", "INTEGER NOT NULL DEFAULT 0"),
    # Час открытия счёта. Ноль — счёт открыт до этого правила, и банк
    # знает его давно: кредит такому не задерживают
    ("account_at", "INTEGER NOT NULL DEFAULT 0"),
    ("card_at", "INTEGER NOT NULL DEFAULT 0"),  # 0 — карты нет
    ("card_paid_until", "INTEGER NOT NULL DEFAULT 0"),
    ("pay_from", f"TEXT NOT NULL DEFAULT '{CARD}'"),
    # Работа. Как и счёт, она лежит на бойце: работа одна, и держать под
    # неё таблицу из одной строки на человека незачем
    ("job_code", "TEXT NOT NULL DEFAULT ''"),  # пусто — не работает
    ("job_since", "INTEGER NOT NULL DEFAULT 0"),
    # Опыт работы: секунды, отложенные с прошлых мест
    ("job_experience", "INTEGER NOT NULL DEFAULT 0"),
    ("job_week", "INTEGER NOT NULL DEFAULT 0"),  # начало оплачиваемой недели
    ("job_minutes", "INTEGER NOT NULL DEFAULT 0"),  # отработано за неделю
    # Жалованье из вилки, назначенное мэром. Ноль — оклад твёрдый
    ("job_salary", "INTEGER NOT NULL DEFAULT 0"),
    # Долг по судебному штрафу. Пока он есть, счёт заблокирован: карта
    # не платит, а всё, что приходит на счёт, уходит в долг
    ("account_debt", "INTEGER NOT NULL DEFAULT 0"),
    ("shift_until", "INTEGER NOT NULL DEFAULT 0"),  # 0 — смена не идёт
    ("shift_day", "TEXT NOT NULL DEFAULT ''"),  # какие это сутки по Москве
    ("shift_minutes", "INTEGER NOT NULL DEFAULT 0"),  # отработано за сутки
    # Учёба. Курс один на бойца, и лежит он там же, где работа
    ("study_code", "TEXT NOT NULL DEFAULT ''"),  # пусто — не учится
    ("study_since", "INTEGER NOT NULL DEFAULT 0"),
    ("study_lessons", "INTEGER NOT NULL DEFAULT 0"),  # отстояно занятий
    ("study_day", "TEXT NOT NULL DEFAULT ''"),  # сутки последнего занятия
    ("study_exam_at", "INTEGER NOT NULL DEFAULT 0"),  # час последней попытки
    ("study_tries", "INTEGER NOT NULL DEFAULT 0"),
    ("lesson_until", "INTEGER NOT NULL DEFAULT 0"),  # 0 — занятие не идёт
    ("diplomas", "TEXT NOT NULL DEFAULT ''"),  # коды сданных курсов
    # За рулём: что под бойцом и до какого часа. Пусто — пешком
    ("car", "TEXT NOT NULL DEFAULT ''"),
    ("car_until", "INTEGER NOT NULL DEFAULT 0"),
)


# Колонки инвентаря, добавленные после первой версии
INVENTORY_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("mod", "TEXT NOT NULL DEFAULT ''"),
    ("mod_value", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Открыть базу и довести её до нынешней версии.

        Сорвалось посреди — закрываем за собой. Соединение `aiosqlite`
        держит свой поток, и брошенное на полпути оно не даёт процессу
        завершиться: бот молча висит вместо того, чтобы упасть с
        понятной ошибкой. Ошибку при этом не глотаем — она и есть ответ
        на вопрос, почему бот не поднялся.
        """
        self._ensure_directory()
        self._conn = await aiosqlite.connect(self.path)
        try:
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys = ON")
            await self._tune()
            await self._conn.executescript(SCHEMA)
            await self._migrate()
            await self._conn.commit()
        except Exception:
            await self.close()
            raise

    async def _tune(self) -> None:
        """Настроить соединение под диск, на котором живёт база.

        На Railway база лежит на подключённом томе, а не на локальном
        диске, и там каждый fsync стоит не микросекунды, а десятки
        миллисекунд. С настройками по умолчанию SQLite пишет журнал
        откатом и синхронизирует его на каждом коммите — то есть платит
        этот fsync несколько раз за одну запись.

        `journal_mode = WAL` переводит коммит в дописывание в конец
        отдельного файла, а читателей перестаёт запирать за писателем.
        `synchronous = NORMAL` снимает fsync с каждого коммита: в WAL это
        безопасно — при обрыве питания теряются последние сделки, но база
        не рушится. Для игры такая цена честная, а разница на сетевом
        томе — в разы.

        `busy_timeout` нужен на случай, когда в базу смотрит кто-то ещё
        (миграция, бэкап): лучше подождать пять секунд, чем ответить
        бойцу «база занята».

        Режим WAL хранится в самом файле и переживает перезапуск, а
        остальные — свойства соединения, поэтому ставятся каждый раз.
        Память (`:memory:`) WAL не умеет, и это не беда: у неё и диска
        нет. Поэтому ответ только пишем в журнал, а не требуем.
        """
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        async with self._conn.execute("PRAGMA journal_mode = WAL") as cursor:
            row = await cursor.fetchone()
        mode = (row[0] if row else "") or "?"
        if mode.lower() != "wal":
            logger.info("Журнал базы: %s — WAL на этом диске недоступен", mode)

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
        # Индексы по дописанным колонкам — только здесь, после самих
        # колонок. В `SCHEMA` им не место: на живой базе `CREATE TABLE IF
        # NOT EXISTS` ничего не делает, колонка ещё не дописана, и индекс
        # по ней роняет запуск на «no such column». На пустой базе такой
        # индекс проходит — потому и не виден ниоткуда, кроме обновления
        await self._index_migrated_columns()
        await self._settle_genders()
        await self._migrate_inventory()
        await self._migrate_duels()
        await self._migrate_battles()
        await self._migrate_cars()
        await self._migrate_driving()
        await self._migrate_arenas()
        await self._migrate_raid_windows()
        await self._migrate_clans()
        await self._migrate_turf()
        await self._migrate_service_vault()
        await self._migrate_card_log()
        await self._migrate_dojo()

    async def _migrate_turf(self) -> None:
        """Привести живые точки к нынешней форме.

        Две правки, и обе про колонки: час опустения точки появился
        позже самих точек, а час боя больше не нужен — отдыха у точки
        нет. На живой базе `CREATE TABLE IF NOT EXISTS` ни того, ни
        другого не сделает: таблица уже есть.
        """
        async with self.conn.execute("PRAGMA table_info(turf)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns:
            return
        if "empty_since" not in columns:
            await self.conn.execute(
                "ALTER TABLE turf ADD COLUMN empty_since "
                "INTEGER NOT NULL DEFAULT 0"
            )
            await self.conn.commit()
            logger.info("База обновлена: добавлена колонка turf.empty_since")
        if "counted_at" not in columns:
            await self.conn.execute(
                "ALTER TABLE turf ADD COLUMN counted_at "
                "INTEGER NOT NULL DEFAULT 0"
            )
            await self.conn.commit()
            logger.info("База обновлена: добавлена колонка turf.counted_at")
        if "fought_at" in columns:
            # Отдых точки отменён: дом ходит из рук в руки сколько
            # угодно. Колонку убираем, а не оставляем лежать с мёртвыми
            # часами — по ней читали бы правило, которого нет
            await self.conn.execute("ALTER TABLE turf DROP COLUMN fought_at")
            await self.conn.commit()
            logger.info("База обновлена: убрана колонка turf.fought_at")

    async def _migrate_service_vault(self) -> None:
        """Дописать службе, как она делит бонусный пулл.

        Казна службы появилась раньше пулла, и на живой базе `CREATE
        TABLE IF NOT EXISTS` колонку не добавит: таблица уже есть. У
        старых строк дележ поровну — так участок делил копилку и до
        всякой настройки.
        """
        async with self.conn.execute(
            "PRAGMA table_info(service_vault)"
        ) as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns or "split" in columns:
            return
        await self.conn.execute(
            "ALTER TABLE service_vault ADD COLUMN split "
            "TEXT NOT NULL DEFAULT 'even'"
        )
        await self.conn.commit()
        logger.info("База обновлена: добавлена колонка service_vault.split")

    async def _migrate_card_log(self) -> None:
        """Дописать комментарий к уже записанным операциям по карте.

        Выписка появилась раньше комментария, и на живой базе `CREATE
        TABLE IF NOT EXISTS` колонку не добавит: таблица уже есть. У
        старых строк комментарий пустой — экран такую строку рисует
        одним заголовком, как рисовал всегда.
        """
        async with self.conn.execute("PRAGMA table_info(card_log)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns or "about" in columns:
            return
        await self.conn.execute(
            "ALTER TABLE card_log ADD COLUMN about TEXT NOT NULL DEFAULT ''"
        )
        await self.conn.commit()
        logger.info("База обновлена: добавлена колонка card_log.about")

    async def _migrate_clans(self) -> None:
        """Дописать сейф в уже заведённые кланы.

        Кланы появились раньше имущества, и на живой базе `CREATE TABLE
        IF NOT EXISTS` колонку не добавит: таблица уже есть.
        """
        async with self.conn.execute("PRAGMA table_info(clans)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns or "vault" in columns:
            return
        await self.conn.execute(
            "ALTER TABLE clans ADD COLUMN vault INTEGER NOT NULL DEFAULT 0"
        )
        await self.conn.commit()
        logger.info("База обновлена: добавлена колонка clans.vault")

    async def _index_migrated_columns(self) -> None:
        """Индексы по колонкам, которых в первой версии базы не было.

        Номер счёта называют вслух, чтобы принять перевод, и по нему же
        ищут чужой счёт. Уникальность держит база: розыгрыш номера может
        совпасть, и ловить это в питоне значило бы ловить с опозданием.
        """
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS players_account "
            "ON players(account_number) WHERE account_number <> ''"
        )
        # Мест на вакансии бывает несколько: патрульных в городе четверо,
        # а управляющий банком один. Уникальный индекс по `job_code`,
        # который держал «одно место на город», для этого не годится — и
        # на живой базе его надо снять, иначе второй патрульный не
        # запишется. Считает места теперь сам запрос в `take_job`:
        # условие стоит там же, где запись, и щели между ними нет
        await self.conn.execute("DROP INDEX IF EXISTS players_job")
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS players_job "
            "ON players(job_code) WHERE job_code <> ''"
        )

    async def _settle_genders(self) -> None:
        """Бойцам без пола поставить мужской.

        Пол спрашивают при создании, но бойцы, заведённые до этого вопроса,
        остались с пустой строкой — и гардероб не знал, какие образы им
        показывать. Ставим мужской: все эти бойцы выбирали внешность из
        мужского набора, другого тогда и не было.

        Правка идёт на каждом запуске и ничего не стоит: если пустых нет,
        `UPDATE` не трогает ни строки.
        """
        cursor = await self.conn.execute(
            "UPDATE players SET gender = ? WHERE gender = '' OR gender IS NULL",
            (MALE,),
        )
        if cursor.rowcount > 0:
            logger.info("База обновлена: пол проставлен %s бойцам", cursor.rowcount)

    async def _migrate_inventory(self) -> None:
        """Дописать колонки модификации в уже живой инвентарь."""
        async with self.conn.execute("PRAGMA table_info(inventory)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        for column, definition in INVENTORY_MIGRATIONS:
            if column in columns:
                continue
            await self.conn.execute(
                f"ALTER TABLE inventory ADD COLUMN {column} {definition}"
            )
            logger.info("База обновлена: добавлена колонка inventory.%s", column)
        await self.conn.commit()

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

    async def _migrate_dojo(self) -> None:
        """В ключ записи добавился приём — таблицу приходится пересобрать.

        Записывались в школу по одному приёму, и ключом был сам боец.
        Потом правило разошлось: учится разом один, но набранный приём
        ждёт подтверждения сколько угодно долго, и всё это время боец
        учит следующий. Дописать колонку в первичный ключ SQLite не
        умеет — только собрать таблицу заново и перелить строки.
        """
        async with self.conn.execute("PRAGMA table_info(dojo)") as cursor:
            rows = await cursor.fetchall()
        if not rows or sum(1 for row in rows if row["pk"]) != 1:
            return
        await self.conn.executescript(
            """
            ALTER TABLE dojo RENAME TO dojo_old;
            CREATE TABLE dojo (
                user_id    INTEGER NOT NULL,
                code       TEXT    NOT NULL,
                deed       TEXT    NOT NULL,
                baseline   INTEGER NOT NULL DEFAULT 0,
                started_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, code)
            );
            INSERT INTO dojo (user_id, code, deed, baseline, started_at)
                SELECT user_id, code, deed, baseline, started_at
                FROM dojo_old;
            DROP TABLE dojo_old;
            CREATE INDEX IF NOT EXISTS idx_dojo_user
                ON dojo(user_id, started_at);
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: набранный приём ждёт, пока учится другой")

    async def _migrate_raid_windows(self) -> None:
        """В ключ окна добавился рейд — таблицу приходится пересобрать.

        Рейдов в городе стало два, и окна у них могут начаться в один
        час. Прежний ключ (боец, час) этого не различал, а дописать
        колонку в первичный ключ SQLite не умеет — только собрать таблицу
        заново и перелить строки. Всё, что в ней лежало, — подвал казино,
        поэтому старым строкам ставится его код.
        """
        async with self.conn.execute("PRAGMA table_info(raid_window)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns or "raid" in columns:
            return
        await self.conn.executescript(
            """
            ALTER TABLE raid_window RENAME TO raid_window_old;
            CREATE TABLE raid_window (
                user_id   INTEGER NOT NULL,
                raid      TEXT    NOT NULL DEFAULT 'cellar',
                opened_at INTEGER NOT NULL,
                won       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, raid, opened_at)
            );
            INSERT INTO raid_window (user_id, raid, opened_at, won)
                SELECT user_id, 'cellar', opened_at, won FROM raid_window_old;
            DROP TABLE raid_window_old;
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: окна рейдов различаются по рейду")

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

    async def _migrate_battles(self) -> None:
        """Групповой бой тоже может идти без ветки — chat_id должен пустеть.

        Групповые бои завелись в чате, и ветка у них была всегда. В
        мини-аппе её нет, а NOT NULL снимается в SQLite только
        пересборкой таблицы.
        """
        async with self.conn.execute("PRAGMA table_info(battles)") as cursor:
            columns = {row["name"]: row for row in await cursor.fetchall()}
        chat = columns.get("chat_id")
        if chat is None or not chat["notnull"]:
            return
        await self.conn.executescript(
            """
            ALTER TABLE battles RENAME TO battles_old;
            CREATE TABLE battles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER,
                thread_id  INTEGER,
                kind       TEXT    NOT NULL,
                mode       TEXT    NOT NULL DEFAULT 'armed',
                rounds     INTEGER NOT NULL DEFAULT 0,
                winner_team INTEGER,
                draw       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO battles (
                id, chat_id, thread_id, kind, mode, rounds,
                winner_team, draw, created_at
            )
            SELECT id, chat_id, thread_id, kind, mode, rounds,
                   winner_team, draw, created_at
            FROM battles_old;
            DROP TABLE battles_old;
            CREATE INDEX IF NOT EXISTS idx_battles_chat
                ON battles(chat_id, created_at DESC);
            """
        )
        await self.conn.commit()
        logger.info("База обновлена: групповой бой может идти без ветки")

    # Колонки парковки: в первой версии таблицы машин их не было —
    # машина жила только при хозяине
    CAR_COLUMNS: tuple[tuple[str, str], ...] = (
        ("parked", "TEXT NOT NULL DEFAULT ''"),
        ("parked_id", "INTEGER NOT NULL DEFAULT 0"),
        # Сколько машина проехала с последней зарядки. Ноль — полный
        # бак, и это же делает миграцию бесплатной: у купленных до
        # этого правила пробег нулевой, то есть заряд полный
        ("driven", "INTEGER NOT NULL DEFAULT 0"),
        # Номер машины по маске а000аа (`bot.game.plates`). Пусто —
        # машина записана до номеров; номер ей выдадут, когда на неё
        # посмотрят. Уникальность держит индекс, он заводится рядом
        ("plate", "TEXT NOT NULL DEFAULT ''"),
        # Угон: у кого машину увели и когда. Ноль — машина не угнана.
        # Заявление хозяина (`stolen_filed`) и есть розыск: до него
        # полиция машину не изымает, после — изымает у кого угодно
        ("stolen_from", "INTEGER NOT NULL DEFAULT 0"),
        ("stolen_at", "INTEGER NOT NULL DEFAULT 0"),
        ("stolen_filed", "INTEGER NOT NULL DEFAULT 0"),
        # Где машина брошена: код дома. Пусто — она не на улице.
        # Сюда же пишется участок, когда машину изымают на хранение
        ("at_place", "TEXT NOT NULL DEFAULT ''"),
        # Номер, который город выдал при выпуске. Перебитый знак от
        # него отличается — по этому полиция подделку и узнаёт
        ("plate_first", "TEXT NOT NULL DEFAULT ''"),
        ("restamped", "INTEGER NOT NULL DEFAULT 0"),
    )

    async def _migrate_cars(self) -> None:
        """У машин появилось место стоянки, а у семьи — живой гараж.

        Пустую таблицу `clan_cars` убираем: гараж особняка теперь
        считается по самим машинам, и второй источник правды о том, где
        стоит машина, не нужен. Писать в неё никто не успел — она
        приехала пустой и такой же уходит.
        """
        async with self.conn.execute("PRAGMA table_info(cars)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns:
            return
        for column, definition in self.CAR_COLUMNS:
            if column in columns:
                continue
            await self.conn.execute(
                f"ALTER TABLE cars ADD COLUMN {column} {definition}"
            )
            logger.info("База обновлена: у машин появилась колонка %s", column)
        # Номер один на город: два одинаковых знака сделали бы проверку
        # полиции бессмысленной. Индекс частичный — пустые номера у
        # старых машин друг другу не мешают
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS cars_plate "
            "ON cars(plate) WHERE plate <> ''"
        )
        # Индекс по стоянке — только здесь: в схеме он смотрел бы на
        # колонку, которой на живой базе ещё нет
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS cars_parked ON cars(parked, parked_id)"
        )
        await self.conn.execute("DROP INDEX IF EXISTS clan_cars_clan")
        await self.conn.execute("DROP TABLE IF EXISTS clan_cars")
        await self.conn.commit()

    async def _migrate_driving(self) -> None:
        """У автошколы появился час последней попытки.

        Прежде пересдача была бесплатной и без очереди, и помнить о
        попытке было нечего. Теперь помнить надо: у тех, кто сдавал до
        этого правила, час нулевой — их следующая попытка считается
        первой и ничего не стоит. Наказывать за то, чего база не
        записала, нечестно.
        """
        async with self.conn.execute("PRAGMA table_info(driving)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        if not columns or "tried_at" in columns:
            return
        await self.conn.execute(
            "ALTER TABLE driving ADD COLUMN tried_at INTEGER NOT NULL DEFAULT 0"
        )
        await self.conn.commit()
        logger.info("База обновлена: у автошколы появился час последней попытки")

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
        player.injury = await self.injury_of(player.user_id)
        player.policy = await self.policy_of(player.user_id)
        player.school = await self.school_of(player.user_id)
        player.clan = await self.standing_of(player.user_id)
        player.flat = await self.lease_of(player.user_id)
        player.gym_until = await self.gym_pass_of(player.user_id)
        player.loadout = await self.list_abilities(player.user_id)
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
                wins, losses, draws, raid_wins, raid_fights, seen_at,
                location, travel_to, arrives_at,
                city, birthplace, pro_until, gender, created_at,
                account_number, account_balance, account_at,
                card_at, card_paid_until, pay_from,
                job_code, job_since, job_experience, job_week, job_minutes,
                job_salary, account_debt, shift_until, shift_day, shift_minutes,
                study_code, study_since, study_lessons, study_day,
                study_exam_at, study_tries, lesson_until, diplomas,
                car, car_until
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                seen_at        = excluded.seen_at,
                location       = excluded.location,
                travel_to      = excluded.travel_to,
                arrives_at     = excluded.arrives_at,
                pro_until      = excluded.pro_until,
                gender         = excluded.gender,
                account_number  = excluded.account_number,
                account_balance = excluded.account_balance,
                account_at      = excluded.account_at,
                card_at         = excluded.card_at,
                card_paid_until = excluded.card_paid_until,
                pay_from        = excluded.pay_from,
                job_code        = excluded.job_code,
                job_experience  = excluded.job_experience,
                job_since       = excluded.job_since,
                job_week        = excluded.job_week,
                job_minutes     = excluded.job_minutes,
                job_salary      = excluded.job_salary,
                account_debt    = excluded.account_debt,
                shift_until     = excluded.shift_until,
                shift_day       = excluded.shift_day,
                shift_minutes   = excluded.shift_minutes,
                study_code      = excluded.study_code,
                study_since     = excluded.study_since,
                study_lessons   = excluded.study_lessons,
                study_day       = excluded.study_day,
                study_exam_at   = excluded.study_exam_at,
                study_tries     = excluded.study_tries,
                lesson_until    = excluded.lesson_until,
                diplomas        = excluded.diplomas,
                car             = excluded.car,
                car_until       = excluded.car_until
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
                player.seen_at,
                player.location,
                player.travel_to,
                player.arrives_at,
                player.city,
                player.birthplace,
                player.pro_until,
                player.gender,
                player.created_at,
                player.account_number,
                player.account_balance,
                player.account_at,
                player.card_at,
                player.card_paid_until,
                player.pay_from,
                player.job_code,
                player.job_since,
                player.job_experience,
                player.job_week,
                player.job_minutes,
                player.job_salary,
                player.account_debt,
                player.shift_until,
                player.shift_day,
                player.shift_minutes,
                player.study_code,
                player.study_since,
                player.study_lessons,
                player.study_day,
                player.study_exam_at,
                player.study_tries,
                player.lesson_until,
                ",".join(player.diplomas),
                player.car,
                player.car_until,
            ),
        )
        await self.conn.commit()
        # Выписка, принесённая из лавки: боец платил там, а пишем здесь
        await self.flush_card_moves(player)

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
        await self.conn.execute("DELETE FROM injuries WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM policies WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM job_blocks WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM gym_passes WHERE user_id = ?", (user_id,))
        await self.conn.execute(
            "DELETE FROM gym_progress WHERE user_id = ?", (user_id,)
        )
        await self.conn.execute("DELETE FROM gym_visits WHERE user_id = ?", (user_id,))
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

    async def players_at(self, location: str, limit: int = 50) -> list[Player]:
        """Кто сейчас в этой локации. Недавние сверху — они и в сети.

        Дорога считается по времени прибытия, и вышедший десять секунд
        назад числится ещё там, откуда вышел. Поэтому список отдаётся
        как есть, а кто где на самом деле, решает уже `Player.where`.
        """
        async with self.conn.execute(
            f"""
            SELECT {PLAYER_COLUMNS} FROM players
            WHERE location = ? OR travel_to = ?
            ORDER BY seen_at DESC
            LIMIT ?
            """,
            (location, location, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_to_player(row) for row in rows]

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
        players = [_to_player(row) for row in rows]
        # Клан дочитываем одним запросом на весь список: знак мафии стоит
        # у ника в каждой строке, а спрашивать про каждого по отдельности
        # — это двести запросов на один экран
        standings = await self.standings_in([one.user_id for one in players])
        for player in players:
            player.clan = standings.get(player.user_id)
        return players

    async def standings_in(self, user_ids: list[int]) -> dict[int, Standing]:
        """Чины всех этих бойцов разом: для списков, где строк много."""
        if not user_ids:
            return {}
        holes = ",".join("?" * len(user_ids))
        async with self.conn.execute(
            f"""
            SELECT m.user_id, m.rank, m.joined_at,
                   c.id, c.name, c.don, c.created_at, c.sign, c.renamed_at, c.vault,
                   (SELECT COUNT(*) FROM clan_members k WHERE k.clan_id = c.id)
                       AS members
            FROM clan_members m JOIN clans c ON c.id = m.clan_id
            WHERE m.user_id IN ({holes})
            """,
            tuple(user_ids),
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            int(row["user_id"]): Standing(
                clan=self._clan(row),
                rank=Rank(str(row["rank"])),
                joined_at=int(row["joined_at"]),
            )
            for row in rows
        }

    # ---------- инвентарь ----------

    async def list_gear(self, user_id: int) -> list[OwnedItem]:
        """Всё, что у бойца есть: и надетое, и лежащее в рюкзаке."""
        async with self.conn.execute(
            """
            SELECT id, code, wear, max_wear, slot, mod, mod_value FROM inventory
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
            "UPDATE inventory SET wear = ?, max_wear = ?, slot = ?, mod = ?, "
            "mod_value = ? WHERE id = ?",
            (
                owned.wear,
                owned.max_wear,
                owned.slot.value if owned.slot else None,
                owned.mod,
                owned.mod_value,
                owned.id,
            ),
        )
        await self.conn.commit()

    # ---------- Vegas Банк ----------

    async def open_account(
        self, user_id: int, number: str, now: int = 0
    ) -> bool:
        """Завести счёт. False — счёт уже есть или номер занят.

        Проверка `account_number = ''` стоит в самом запросе: два нажатия
        подряд иначе выписали бы бойцу второй номер поверх первого, а
        вместе с ним потеряли бы деньги на старом счету.

        Час открытия пишем тем же запросом: с него банк считает неделю
        знакомства, без которой кредита не даёт.
        """
        try:
            cursor = await self.conn.execute(
                "UPDATE players SET account_number = ?, account_at = ? "
                "WHERE user_id = ? AND account_number = ''",
                (number, now, user_id),
            )
        except sqlite3.IntegrityError:
            # Номер разыгран, а такой уже у кого-то есть: служба разыграет
            # следующий. Столкновение редкое, но молчать о нём нельзя
            return False
        await self.conn.commit()
        return cursor.rowcount > 0

    async def account_holder(self, number: str) -> Player | None:
        """Чей это счёт. None — такого номера в банке нет."""
        if not number:
            return None
        async with self.conn.execute(
            f"SELECT {PLAYER_COLUMNS} FROM players WHERE account_number = ?",
            (number,),
        ) as cursor:
            row = await cursor.fetchone()
        return _to_player(row) if row else None

    async def account_money(self, user_id: int) -> int:
        """Остаток на счету одним числом — для строки чужой выписки."""
        async with self.conn.execute(
            "SELECT account_balance FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["account_balance"]) if row else 0

    async def move_to_account(self, user_id: int, amount: int) -> bool:
        """Положить наличные на свой счёт. False — столько наличных нет.

        Обе половины — одним запросом: между «проверили» и «списали» иначе
        помещается покупка в соседнем окне, и на счёт легло бы то, чего
        уже нет в наличных.
        """
        if amount <= 0:
            return False
        cursor = await self.conn.execute(
            "UPDATE players SET credits = credits - ?, account_balance = "
            "account_balance + ? WHERE user_id = ? AND credits >= ? "
            "AND account_number <> ''",
            (amount, amount, user_id, amount),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def move_to_cash(self, user_id: int, amount: int) -> bool:
        """Снять со счёта наличные. False — столько на счету нет."""
        if amount <= 0:
            return False
        cursor = await self.conn.execute(
            "UPDATE players SET account_balance = account_balance - ?, "
            "credits = credits + ? WHERE user_id = ? AND account_balance >= ?",
            (amount, amount, user_id, amount),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def send_to_account(self, user_id: int, number: str, amount: int) -> bool:
        """Перевести со своего счёта на чужой по номеру.

        Две строки правятся одной сделкой: если вторая половина не
        прошла — чужой счёт закрыли между проверкой и переводом, — откат
        возвращает деньги отправителю целиком. Половины перевода не
        бывает.
        """
        if amount <= 0:
            return False
        taken = await self.conn.execute(
            "UPDATE players SET account_balance = account_balance - ? "
            "WHERE user_id = ? AND account_balance >= ? AND account_number <> ?",
            (amount, user_id, amount, number),
        )
        if taken.rowcount <= 0:
            await self.conn.rollback()
            return False
        given = await self.conn.execute(
            "UPDATE players SET account_balance = account_balance + ? "
            "WHERE account_number = ?",
            (amount, number),
        )
        if given.rowcount <= 0:
            await self.conn.rollback()
            return False
        await self.conn.commit()
        return True

    async def issue_card(self, user_id: int, issued: int, paid_until: int) -> bool:
        """Выпустить карту. False — карта уже есть или счёта нет.

        Условие `card_at = 0` в запросе: два нажатия подряд иначе выпустили
        бы вторую карту и взяли за неё вторую сотню.
        """
        cursor = await self.conn.execute(
            "UPDATE players SET card_at = ?, card_paid_until = ? "
            "WHERE user_id = ? AND card_at = 0 AND account_number <> ''",
            (issued, paid_until, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def set_pay_from(self, user_id: int, purse: str) -> None:
        """Чем боец предпочитает платить."""
        await self.conn.execute(
            "UPDATE players SET pay_from = ? WHERE user_id = ?", (purse, user_id)
        )
        await self.conn.commit()

    # ---------- работа ----------

    async def take_job(
        self, user_id: int, code: str, now: int, week: int, seats: int = 1
    ) -> bool:
        """Занять место. False — свободных нет или боец где-то работает.

        Оба условия стоят в самом запросе. Проверить в питоне, а потом
        записать, значит оставить щель, в которую пролезает лишний
        патрульный: двое, нажавшие «устроиться» в одну секунду, оба
        прошли бы проверку. Считаем занятых прямо в `WHERE` — один
        оператор, и база выполняет его целиком или никак.
        """
        cursor = await self.conn.execute(
            "UPDATE players SET job_code = ?, job_since = ?, job_week = ?, "
            "job_minutes = 0, job_salary = 0, shift_until = 0, shift_day = '', "
            "shift_minutes = 0 "
            "WHERE user_id = ? AND job_code = '' "
            "AND (SELECT COUNT(*) FROM players WHERE job_code = ?) < ?",
            (code, now, week, user_id, code, max(1, seats)),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ---------- заявки на кресло ----------

    async def place_bid(self, user_id: int, code: str, at: int) -> None:
        """Подать заявку на назначаемое кресло. Повторная — та же одна."""
        await self.conn.execute(
            "INSERT INTO job_bids (user_id, code, at) VALUES (?,?,?) "
            "ON CONFLICT(user_id, code) DO UPDATE SET at = excluded.at",
            (user_id, code, at),
        )
        await self.conn.commit()

    async def drop_bid(self, user_id: int, code: str = "") -> None:
        """Забрать заявку. Без кода — все заявки этого бойца."""
        if code:
            await self.conn.execute(
                "DELETE FROM job_bids WHERE user_id = ? AND code = ?",
                (user_id, code),
            )
        else:
            await self.conn.execute(
                "DELETE FROM job_bids WHERE user_id = ?", (user_id,)
            )
        await self.conn.commit()

    async def clear_bids(self, code: str) -> None:
        """Снять все заявки на кресло: назначение их исчерпало."""
        await self.conn.execute("DELETE FROM job_bids WHERE code = ?", (code,))
        await self.conn.commit()

    async def bids_for(self, code: str) -> list[dict]:
        """Кто подал на это кресло — в порядке подачи."""
        async with self.conn.execute(
            "SELECT b.user_id, b.at, p.nickname, p.level, p.diplomas, "
            "p.job_code, p.job_experience, p.job_since "
            "FROM job_bids b JOIN players p ON p.user_id = b.user_id "
            "WHERE b.code = ? ORDER BY b.at, b.user_id",
            (code,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def bids_of(self, user_id: int) -> list[str]:
        """На какие кресла этот боец подал."""
        async with self.conn.execute(
            "SELECT code FROM job_bids WHERE user_id = ? ORDER BY at",
            (user_id,),
        ) as cursor:
            return [str(row["code"]) for row in await cursor.fetchall()]

    async def players_with_diploma(self, diploma: str) -> list[dict]:
        """Кто в городе вообще с таким дипломом — для пустого списка заявок.

        Мэр назначает из подавших, а когда не подал никто — из любого
        с нужной бумагой: кресло начальника пустым город не держит.
        """
        async with self.conn.execute(
            "SELECT user_id, nickname, level, diplomas, job_code, "
            "job_experience, job_since FROM players "
            "WHERE diplomas <> '' ORDER BY nickname",
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        return [
            row for row in rows
            if diploma in str(row["diplomas"] or "").split(",")
        ]

    async def taken_jobs(self) -> dict[str, list[dict]]:
        """Кто на каких местах: код работы → список занявших.

        Список, а не один человек: мест на вакансии бывает четыре, и
        доска должна показать всех, кто их занял.

        Неделя нужна тому, кто смотрит на доску: держателя, у которого
        она давно кончилась, пора рассчитать и снять с места. Иначе
        боец, переставший заходить, занимал бы место вечно — а мест в
        городе счётное число.
        """
        async with self.conn.execute(
            "SELECT user_id, job_code, nickname, job_week "
            "FROM players WHERE job_code <> '' ORDER BY job_since, user_id"
        ) as cursor:
            out: dict[str, list[dict]] = {}
            for row in await cursor.fetchall():
                out.setdefault(str(row["job_code"]), []).append(
                    {
                        "user_id": row["user_id"],
                        "nickname": row["nickname"],
                        "week": row["job_week"],
                    }
                )
            return out

    async def leave_job(self, user_id: int, experience: int = 0) -> None:
        """Уйти с работы: место освобождается, часы обнуляются.

        `experience` — весь опыт бойца вместе с только что отработанным
        на этом месте: уходя, он забирает его с собой. Отложить опыт
        надо здесь же, одним запросом с уходом, — иначе ушедший в ту
        секунду, когда упал процесс, потерял бы свой стаж.
        """
        await self.conn.execute(
            "UPDATE players SET job_code = '', job_since = 0, job_week = 0, "
            "job_minutes = 0, job_salary = 0, shift_until = 0, shift_day = '', "
            "shift_minutes = 0, job_experience = ? "
            "WHERE user_id = ?",
            (max(0, experience), user_id),
        )
        await self.conn.commit()

    async def block_job(
        self, user_id: int, code: str, until: int, reason: str = ""
    ) -> None:
        """Закрыть вакансию для этого бойца до этого часа."""
        await self.conn.execute(
            "INSERT INTO job_blocks (user_id, code, until, reason) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, code) DO UPDATE SET "
            "until = excluded.until, reason = excluded.reason",
            (user_id, code, until, reason),
        )
        await self.conn.commit()

    async def job_blocks_of(self, user_id: int, now: int) -> dict[str, dict]:
        """Какие вакансии бойцу сейчас закрыты и до какого часа.

        Просроченные строки тут же и убираем: запрет кончился, и держать
        его в базе значит однажды показать игроку запрет из прошлого года.
        """
        await self.conn.execute(
            "DELETE FROM job_blocks WHERE user_id = ? AND until <= ?", (user_id, now)
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT code, until, reason FROM job_blocks WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            return {
                row["code"]: {"until": row["until"], "reason": row["reason"]}
                for row in await cursor.fetchall()
            }

    async def hand_over_gear(self, item_id: int, giver_id: int, taker_id: int) -> bool:
        """Передать вещь другому бойцу. False — вещь уже не у того хозяина.

        Вещь не копируется и не пересоздаётся: у неё тот же номер, тот же
        износ и та же модификация, меняется только хозяин. И слот сразу
        пустой — чужая вещь попадает в рюкзак, а не сразу на плечи.

        Хозяина проверяет сам запрос. Между «выложил на стол» и «оба
        согласились» проходят минуты, и за это время вещь могли продать в
        комиссионку; условие в `WHERE` — единственное место, где это
        видно наверняка.
        """
        cursor = await self.conn.execute(
            "UPDATE inventory SET user_id = ?, slot = NULL WHERE id = ? AND user_id = ?",
            (taker_id, item_id, giver_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def hand_over_potions(
        self, code: str, giver_id: int, taker_id: int, count: int
    ) -> bool:
        """Передать склянки. False — столько их у хозяина уже нет.

        Склянки не нумерованы, поэтому передаётся не вещь, а число: у
        одного отнимается, другому прибавляется. Условие `count >= ?`
        делает списание и проверку одним движением.
        """
        if count <= 0:
            return False
        cursor = await self.conn.execute(
            "UPDATE potions SET count = count - ? "
            "WHERE user_id = ? AND code = ? AND count >= ?",
            (count, giver_id, code, count),
        )
        if cursor.rowcount <= 0:
            await self.conn.commit()
            return False
        await self.conn.execute(
            "DELETE FROM potions WHERE user_id = ? AND code = ? AND count <= 0",
            (giver_id, code),
        )
        await self.conn.execute(
            """
            INSERT INTO potions (user_id, code, count) VALUES (?,?,?)
            ON CONFLICT(user_id, code) DO UPDATE SET count = count + excluded.count
            """,
            (taker_id, code, count),
        )
        await self.conn.commit()
        return True

    async def delete_gear(self, item_id: int) -> None:
        await self.conn.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        await self.conn.commit()

    # ---------- модификаторы ----------

    # ---------- входы в клуб ----------

    async def visit_row(self, user_id: int) -> dict:
        """Счёт входов бойца. Пусто — он ещё ни разу не заходил."""
        async with self.conn.execute(
            "SELECT month, days, last_day, claimed FROM visits WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return {"month": "", "days": 0, "last_day": "", "claimed": 0}
        return {
            "month": row["month"],
            "days": int(row["days"]),
            "last_day": row["last_day"],
            "claimed": int(row["claimed"]),
        }

    async def save_visit(
        self, user_id: int, month: str, days: int, last_day: str, claimed: int
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO visits (user_id, month, days, last_day, claimed)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                month = excluded.month,
                days = excluded.days,
                last_day = excluded.last_day,
                claimed = excluded.claimed
            """,
            (user_id, month, days, last_day, claimed),
        )
        await self.conn.commit()

    async def count_visit(self, user_id: int, day: str, month: str) -> tuple[dict, bool]:
        """Отметить вход этим днём. Отдаёт счёт и то, был ли день новым.

        Новый месяц обнуляет и счёт, и забранное: лестница начинается
        заново. День, уже отмеченный, второй раз не считается — зашёл
        трижды за вечер, а вход всё равно один.
        """
        row = await self.visit_row(user_id)
        if row["month"] != month:
            row = {"month": month, "days": 0, "last_day": "", "claimed": 0}
        if row["last_day"] == day:
            return row, False
        row["days"] += 1
        row["last_day"] = day
        await self.save_visit(
            user_id, month, row["days"], day, row["claimed"]
        )
        return row, True

    # ---------- приёмы ----------

    async def list_abilities(self, user_id: int) -> Loadout:
        """Что боец выучил: приём → ступень, на которой он его взял."""
        from bot.content.abilities import get_ability

        # По ступени, а не по порядку изучения: ступень — это и цена
        # приёма, а смотрят на приёмы снизу вверх, от трёх энергий к
        # двенадцати. Порядок строк в базе этого не обещает: загрузка
        # перезаписывает их целиком, и кросс-классовый приём, взятый
        # позже своего, вставал в середину
        async with self.conn.execute(
            "SELECT code, tier FROM abilities WHERE user_id = ? "
            "ORDER BY tier, code",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        # Приём, которого больше нет в каталоге, молча пропускаем: правки
        # содержимого не должны ронять карточку
        return Loadout(
            slots={
                row["code"]: int(row["tier"])
                for row in rows
                if get_ability(row["code"]) is not None
            }
        )

    async def save_abilities(self, user_id: int, loadout: Loadout) -> None:
        """Переписать слоты целиком: забытое исчезает вместе с записью."""
        await self.conn.execute("DELETE FROM abilities WHERE user_id = ?", (user_id,))
        if loadout.slots:
            await self.conn.executemany(
                "INSERT INTO abilities (user_id, code, tier) VALUES (?,?,?)",
                [(user_id, code, tier) for code, tier in loadout.slots.items()],
            )
        await self.conn.commit()

    async def list_mods(self, user_id: int) -> dict[str, int]:
        """Что у бойца в модификаторах: код → сколько штук."""
        from bot.content.mods import get_mod

        async with self.conn.execute(
            "SELECT code, count FROM mods WHERE user_id = ? AND count > 0",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            row["code"]: int(row["count"])
            for row in rows
            if get_mod(row["code"]) is not None
        }

    async def add_mod(self, user_id: int, code: str, count: int = 1) -> int:
        """Положить купленный модификатор в рюкзак. Вернуть, сколько стало."""
        from bot.content.mods import get_mod

        if get_mod(code) is None:
            raise ValueError(f"Неизвестный модификатор: {code}")
        await self.conn.execute(
            """
            INSERT INTO mods (user_id, code, count) VALUES (?,?,?)
            ON CONFLICT(user_id, code) DO UPDATE SET count = count + excluded.count
            """,
            (user_id, code, count),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT count FROM mods WHERE user_id = ? AND code = ?", (user_id, code)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row else 0

    async def take_mod(self, user_id: int, code: str) -> bool:
        """Забрать один модификатор. False — брать было нечего.

        Списание атомарное: два нажатия подряд не потратят один и тот же
        модификатор дважды.
        """
        cursor = await self.conn.execute(
            "UPDATE mods SET count = count - 1 WHERE user_id = ? AND code = ? "
            "AND count > 0",
            (user_id, code),
        )
        taken = cursor.rowcount > 0
        await self.conn.execute(
            "DELETE FROM mods WHERE user_id = ? AND code = ? AND count <= 0",
            (user_id, code),
        )
        await self.conn.commit()
        return taken

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
        """Действующие эффекты. Просроченные подчищаем — но только если есть.

        Сначала читаем, и лишь найдя просроченное, стираем. Раньше
        `DELETE` шёл всегда, то есть каждое чтение бойца открывало сделку
        на запись — а бойца читает каждая ручка. На сетевом томе это
        самая дорогая операция в запросе, и платилась она чаще всего зря:
        стирать обычно нечего.
        """
        moment = now_ts()
        async with self.conn.execute(
            "SELECT code, until FROM effects WHERE user_id = ? ORDER BY until",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        if any(int(row["until"]) <= moment for row in rows):
            await self.conn.execute(
                "DELETE FROM effects WHERE user_id = ? AND until <= ?",
                (user_id, moment),
            )
            await self.conn.commit()
        return [
            ActiveEffect(code=row["code"], until=int(row["until"]))
            for row in rows
            if int(row["until"]) > moment and get_potion(row["code"]) is not None
        ]

    # ---------- травмы ----------

    async def injury_of(self, user_id: int) -> ActiveInjury | None:
        """Травма бойца. Отлежавшую своё стираем — но только если она есть.

        Как и с эффектами: читаем, и лишь найдя отлежавшую, стираем.
        Безусловный `DELETE` здесь стоил сделки на запись каждому чтению
        бойца.
        """
        moment = now_ts()
        async with self.conn.execute(
            "SELECT code, until FROM injuries WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None and int(row["until"]) <= moment:
            await self.conn.execute(
                "DELETE FROM injuries WHERE user_id = ? AND until <= ?",
                (user_id, moment),
            )
            await self.conn.commit()
            return None
        if row is None or get_injury(row["code"]) is None:
            return None
        return ActiveInjury(code=row["code"], until=int(row["until"]))

    # ---------- тренажёрный зал ----------

    async def gym_pass_of(self, user_id: int) -> int:
        """До какого часа у бойца абонемент. 0 — абонемента не было."""
        async with self.conn.execute(
            "SELECT until FROM gym_passes WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["until"]) if row else 0

    async def set_gym_pass(self, user_id: int, until: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO gym_passes (user_id, until) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET until = excluded.until
            """,
            (user_id, until),
        )
        await self.conn.commit()

    async def gym_progress_of(self, user_id: int) -> dict[str, tuple[int, int]]:
        """Прогресс бойца: характеристика → (очки, сделанные улучшения)."""
        async with self.conn.execute(
            "SELECT stat, points, ups FROM gym_progress WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["stat"]: (int(row["points"]), int(row["ups"])) for row in rows}

    async def add_gym_point(self, user_id: int, stat: str) -> int:
        """Записать отработанную тренировку. Вернуть, сколько очков стало."""
        await self.conn.execute(
            """
            INSERT INTO gym_progress (user_id, stat, points, ups) VALUES (?,?,1,0)
            ON CONFLICT(user_id, stat) DO UPDATE SET points = points + 1
            """,
            (user_id, stat),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT points FROM gym_progress WHERE user_id = ? AND stat = ?",
            (user_id, stat),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["points"]) if row else 0

    async def spend_gym_points(self, user_id: int, stat: str, price: int) -> bool:
        """Списать очки на улучшение. False — их уже не хватает.

        Списание и проверка одним запросом: между «хватает» и «списали»
        боец успел бы нажать кнопку второй раз, и одно улучшение вышло бы
        дважды по цене одного.
        """
        cursor = await self.conn.execute(
            """
            UPDATE gym_progress SET points = points - ?, ups = ups + 1
            WHERE user_id = ? AND stat = ? AND points >= ?
            """,
            (price, user_id, stat, price),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def gym_visit_of(self, user_id: int) -> dict | None:
        """Тренировка, которую боец ещё не забрал. None — забирать нечего."""
        async with self.conn.execute(
            """
            SELECT slot, stat, until FROM gym_visits
            WHERE user_id = ? AND done = 0 ORDER BY until LIMIT 1
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "slot": str(row["slot"]),
            "stat": str(row["stat"]),
            "until": int(row["until"]),
        }

    async def start_gym_visit(
        self, user_id: int, slot: str, stat: str, until: int, limit: int = 0
    ) -> bool:
        """Встать на тренировку. False — места нет или боец уже вставал.

        Ключ таблицы — боец и слот, поэтому вторая запись в тот же слот не
        проходит вовсе: «одна тренировка на слот» держит база, а не проверка
        перед вставкой, которую две быстрые кнопки обошли бы.

        Так же и с числом мест: сначала встаём, потом считаем, и лишний
        уходит сам. Проверка до вставки пропустила бы шестого, пока пятый
        ещё не записался, — а тут запись либо есть, либо её нет.
        """
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO gym_visits (user_id, slot, stat, until)
            VALUES (?,?,?,?)
            """,
            (user_id, slot, stat, until),
        )
        if cursor.rowcount <= 0:
            await self.conn.commit()
            return False
        if limit and await self.gym_slot_crowd(slot) > limit:
            await self.conn.execute(
                "DELETE FROM gym_visits WHERE user_id = ? AND slot = ?",
                (user_id, slot),
            )
            await self.conn.commit()
            return False
        await self.conn.commit()
        return True

    async def close_gym_visit(self, user_id: int, slot: str) -> bool:
        """Отметить тренировку отработанной. False — её уже закрыли."""
        cursor = await self.conn.execute(
            "UPDATE gym_visits SET done = 1 WHERE user_id = ? AND slot = ? AND done = 0",
            (user_id, slot),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def gym_slots_taken(
        self, user_id: int, slots: list[str]
    ) -> dict[str, bool]:
        """Слоты бойца из этого списка: ключ → отработан ли до конца.

        Отработанное и идущее различаются, потому что различаются на
        табло: ✅ против часов. Одним множеством они выглядели бы
        одинаково, и боец не видел бы, что стоит на занятии прямо сейчас.
        """
        if not slots:
            return {}
        marks = ",".join("?" for _ in slots)
        async with self.conn.execute(
            f"SELECT slot, done FROM gym_visits "
            f"WHERE user_id = ? AND slot IN ({marks})",
            (user_id, *slots),
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["slot"]): bool(row["done"]) for row in rows}

    async def gym_visits_today(self, user_id: int, day: str) -> int:
        """Сколько занятий боец уже занял в этих сутках.

        Считаются и отработанные, и идущее: место в дне занято тем и
        другим, и цена следующего занятия растёт от обоих.
        """
        async with self.conn.execute(
            "SELECT COUNT(*) AS taken FROM gym_visits "
            "WHERE user_id = ? AND slot LIKE ?",
            (user_id, f"{day}:%"),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["taken"]) if row else 0

    async def gym_slot_crowd(self, slot: str) -> int:
        """Сколько бойцов в этом занятии — вместе с теми, кто ещё стоит."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS taken FROM gym_visits WHERE slot = ?", (slot,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["taken"]) if row else 0

    async def gym_slots_crowd(self, slots: list[str]) -> dict[str, int]:
        """Сколько народу в каждом из этих занятий — одним запросом."""
        if not slots:
            return {}
        marks = ",".join("?" for _ in slots)
        async with self.conn.execute(
            f"SELECT slot, COUNT(*) AS taken FROM gym_visits "
            f"WHERE slot IN ({marks}) GROUP BY slot",
            tuple(slots),
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["slot"]): int(row["taken"]) for row in rows}

    # ---------- документы: страховой полис ----------

    async def policy_of(self, user_id: int) -> Policy | None:
        """Полис бойца. Просроченный не стираем — он остаётся документом.

        Тем и отличается от травмы: травма отболела и её нет, а полис с
        вышедшим сроком по-прежнему лежит в документах. По нему видно, до
        какого часа он действовал, и его продлевают, а не выписывают заново.
        """
        async with self.conn.execute(
            "SELECT issued, until, auto_renew FROM policies WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Policy(
            issued=int(row["issued"]),
            until=int(row["until"]),
            auto_renew=bool(row["auto_renew"]),
        )

    # ---------- кланы: семьи мафии ----------
    #
    # Клан живёт в трёх таблицах: сам клан со знаком, кто в нём и каким
    # чином, и заявки с приглашениями. Знак лежит в базе двоичным полем —
    # приносит его игрок, и класть его в бакет некому.

    @staticmethod
    def _clan(row) -> Clan:
        return Clan(
            id=int(row["id"]),
            name=str(row["name"]),
            don=int(row["don"]),
            created_at=int(row["created_at"]),
            sign=str(row["sign"]),
            members=int(row["members"] if "members" in row.keys() else 0),
            renamed_at=int(row["renamed_at"]),
            vault=int(row["vault"]),
        )

    async def create_clan(
        self,
        name: str,
        don: int,
        sign: str,
        now: int | None = None,
    ) -> Clan:
        """Завести клан и посадить в него Дона.

        Имя или знак заняты — IntegrityError: и то и другое держит база,
        а не питон. Двое, нажавшие «завести» в одну секунду, иначе оба
        прошли бы проверку.

        Клан и его первый человек появляются одной сделкой: клан без Дона
        был бы семьёй без главы, и показывать его в особняке было бы
        нечего.
        """
        moment = now_ts() if now is None else now
        cursor = await self.conn.execute(
            """
            INSERT INTO clans (name, name_key, sign, created_at, don)
            VALUES (?,?,?,?,?)
            """,
            (name, name.lower(), sign, moment, don),
        )
        clan_id = int(cursor.lastrowid)
        await self.conn.execute(
            """
            INSERT INTO clan_members (user_id, clan_id, rank, joined_at)
            VALUES (?,?,?,?)
            """,
            (don, clan_id, Rank.DON.value, moment),
        )
        # Старые заявки основателя больше не нужны: он уже при семье
        await self.conn.execute(
            "DELETE FROM clan_petitions WHERE user_id = ?", (don,)
        )
        await self.conn.commit()
        logger.info("Заведён клан %s (%s), Дон %s", name, clan_id, don)
        return Clan(
            id=clan_id,
            name=name,
            don=don,
            created_at=moment,
            sign=sign,
            members=1,
        )

    async def clan_by_id(self, clan_id: int) -> Clan | None:
        async with self.conn.execute(
            """
            SELECT c.id, c.name, c.don, c.created_at, c.sign, c.renamed_at, c.vault,
                   (SELECT COUNT(*) FROM clan_members m WHERE m.clan_id = c.id)
                       AS members
            FROM clans c WHERE c.id = ?
            """,
            (clan_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._clan(row) if row else None

    async def clan_by_name(self, name: str) -> Clan | None:
        """Клан по имени, не глядя на регистр: имя одно на город."""
        async with self.conn.execute(
            """
            SELECT c.id, c.name, c.don, c.created_at, c.sign, c.renamed_at, c.vault,
                   (SELECT COUNT(*) FROM clan_members m WHERE m.clan_id = c.id)
                       AS members
            FROM clans c WHERE c.name_key = ?
            """,
            (name.lower(),),
        ) as cursor:
            row = await cursor.fetchone()
        return self._clan(row) if row else None

    async def all_clans(self) -> list[Clan]:
        """Все кланы города: сперва большие, при равенстве — старшие."""
        async with self.conn.execute(
            """
            SELECT c.id, c.name, c.don, c.created_at, c.sign, c.renamed_at, c.vault,
                   (SELECT COUNT(*) FROM clan_members m WHERE m.clan_id = c.id)
                       AS members
            FROM clans c
            ORDER BY members DESC, c.created_at ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._clan(row) for row in rows]

    async def taken_signs(self) -> set[str]:
        """Знаки, которые уже разобрали: свободные и считаются по ним."""
        async with self.conn.execute("SELECT sign FROM clans") as cursor:
            rows = await cursor.fetchall()
        return {str(row["sign"]) for row in rows}

    async def rename_clan(self, clan_id: int, name: str, now: int) -> None:
        """Сменить имя клана и запомнить час смены: следующая — через полгода."""
        await self.conn.execute(
            "UPDATE clans SET name = ?, name_key = ?, renamed_at = ? WHERE id = ?",
            (name, name.lower(), now, clan_id),
        )
        await self.conn.commit()
        logger.info("Клан %s переименован в %s", clan_id, name)

    async def standing_of(self, user_id: int) -> Standing | None:
        """В каком клане боец и каким чином. None — он сам по себе."""
        async with self.conn.execute(
            """
            SELECT m.rank, m.joined_at,
                   c.id, c.name, c.don, c.created_at, c.sign, c.renamed_at, c.vault,
                   (SELECT COUNT(*) FROM clan_members k WHERE k.clan_id = c.id)
                       AS members
            FROM clan_members m JOIN clans c ON c.id = m.clan_id
            WHERE m.user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Standing(
            clan=self._clan(row),
            rank=Rank(str(row["rank"])),
            joined_at=int(row["joined_at"]),
        )

    async def roster_of(self, clan_id: int) -> Roster | None:
        """Клан со всеми, кто в нём: чины, ники, уровни и подписка."""
        clan = await self.clan_by_id(clan_id)
        if clan is None:
            return None
        async with self.conn.execute(
            """
            SELECT m.user_id, m.rank, m.joined_at,
                   p.nickname, p.level, p.class_code, p.pro_until
            FROM clan_members m JOIN players p ON p.user_id = m.user_id
            WHERE m.clan_id = ?
            ORDER BY m.joined_at ASC
            """,
            (clan_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return Roster(
            clan=clan,
            members=[
                Member(
                    user_id=int(row["user_id"]),
                    rank=Rank(str(row["rank"])),
                    nickname=str(row["nickname"]),
                    level=int(row["level"]),
                    class_code=str(row["class_code"]),
                    pro_until=int(row["pro_until"]),
                    joined_at=int(row["joined_at"]),
                )
                for row in rows
            ],
        )

    async def add_member(
        self, user_id: int, clan_id: int, rank: Rank, now: int | None = None
    ) -> None:
        """Принять бойца в клан и снять все его заявки разом.

        Заявки снимаются все, а не только та, по которой приняли: боец
        может стучаться в три семьи сразу, и после приёма в одну
        остальные превращаются в обещание, которого он уже не сдержит.
        """
        moment = now_ts() if now is None else now
        await self.conn.execute(
            """
            INSERT INTO clan_members (user_id, clan_id, rank, joined_at)
            VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                clan_id   = excluded.clan_id,
                rank      = excluded.rank,
                joined_at = excluded.joined_at
            """,
            (user_id, clan_id, rank.value, moment),
        )
        await self.conn.execute(
            "DELETE FROM clan_petitions WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

    async def set_rank(self, user_id: int, rank: Rank) -> None:
        await self.conn.execute(
            "UPDATE clan_members SET rank = ? WHERE user_id = ?",
            (rank.value, user_id),
        )
        await self.conn.commit()

    async def set_don(self, clan_id: int, user_id: int) -> None:
        """Записать нового Дона в сам клан. Чины меняет вызывающий."""
        await self.conn.execute(
            "UPDATE clans SET don = ? WHERE id = ?", (user_id, clan_id)
        )
        await self.conn.commit()

    async def remove_member(self, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM clan_members WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()
        # Машина с парковки особняка уезжает вместе с хозяином: чужой на
        # ней не свой, а терять её из-за ухода из семьи незачем
        await self.drive_home(user_id, "clan")

    async def delete_clan(self, clan_id: int) -> None:
        """Распустить клан. Люди и заявки уходят за ним по внешнему ключу."""
        # Парковку освобождаем до роспуска: машины едут к хозяевам, а
        # внешнего ключа у них нет — они не имущество семьи
        await self.empty_parking("clan", clan_id)
        await self.conn.execute("DELETE FROM clans WHERE id = ?", (clan_id,))
        await self.conn.commit()
        logger.info("Клан %s распущен", clan_id)

    async def count_rank(self, clan_id: int, rank: Rank) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM clan_members WHERE clan_id = ? AND rank = ?",
            (clan_id, rank.value),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    # ---------- имущество семьи: сейф и хранилище ----------

    async def move_vault(self, clan_id: int, amount: int) -> bool:
        """Положить в сейф или взять из него. False — в сейфе столько нет.

        Проверка стоит в самом запросе: между «посмотрели остаток» и
        «списали» успевает пройти чужая рука, и ловить это питоном
        значило бы ловить с опозданием.
        """
        cursor = await self.conn.execute(
            "UPDATE clans SET vault = vault + ? WHERE id = ? AND vault + ? >= 0",
            (amount, clan_id, amount),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clan_items(self, clan_id: int) -> list[Stored]:
        """Что лежит в хранилище семьи, сперва положенное раньше."""
        async with self.conn.execute(
            """
            SELECT i.id, i.code, i.wear, i.max_wear, i.mod, i.mod_value,
                   i.put_by, i.put_at,
                   (SELECT nickname FROM players WHERE user_id = i.put_by) AS by_name
            FROM clan_items i WHERE i.clan_id = ? ORDER BY i.put_at, i.id
            """,
            (clan_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Stored(
                id=int(row["id"]),
                code=str(row["code"]),
                wear=int(row["wear"]),
                max_wear=int(row["max_wear"]),
                mod=str(row["mod"]),
                mod_value=int(row["mod_value"]),
                by_id=int(row["put_by"]),
                by_name=str(row["by_name"] or ""),
                put_at=int(row["put_at"]),
            )
            for row in rows
        ]

    async def stash_gear(
        self, item_id: int, user_id: int, clan_id: int, limit: int, now: int
    ) -> bool:
        """Переложить вещь из рюкзака в хранилище клана.

        False — вещи у бойца нет, она надета или в хранилище нет места.
        Вещь не копируется: сначала её забирают из инвентаря и только
        потом кладут на полку, и если забрать не вышло, класть нечего.
        """
        async with self.conn.execute(
            """
            SELECT code, wear, max_wear, mod, mod_value FROM inventory
            WHERE id = ? AND user_id = ? AND slot IS NULL
            """,
            (item_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM clan_items WHERE clan_id = ?", (clan_id,)
        ) as cursor:
            crowd = await cursor.fetchone()
        if int(crowd["n"]) >= limit:
            return False
        cursor = await self.conn.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ? AND slot IS NULL",
            (item_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь увели между двумя запросами
            await self.conn.commit()
            return False
        await self.conn.execute(
            """
            INSERT INTO clan_items
                (clan_id, code, wear, max_wear, mod, mod_value, put_by, put_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                clan_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
                user_id,
                now,
            ),
        )
        await self.conn.commit()
        return True

    async def take_clan_item(
        self, stored_id: int, clan_id: int, user_id: int
    ) -> str:
        """Забрать вещь с полки себе в рюкзак. Пусто — её там уже нет.

        Возвращает код вещи: по нему службе есть что сказать бойцу.
        """
        async with self.conn.execute(
            "SELECT code, wear, max_wear, mod, mod_value FROM clan_items "
            "WHERE id = ? AND clan_id = ?",
            (stored_id, clan_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return ""
        cursor = await self.conn.execute(
            "DELETE FROM clan_items WHERE id = ? AND clan_id = ?",
            (stored_id, clan_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь забрали между запросами
            await self.conn.commit()
            return ""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
            ),
        )
        await self.conn.commit()
        return str(row["code"])

    # ---------- склад службы: кабинет начальника ----------
    #
    # Тот же склад, что у семьи, с одной разницей: службу зовут по коду,
    # а не по номеру. Запросы повторяют клановые нарочно — склад и есть
    # тот же склад, и сводить их в один общий на двух хозяев значило бы
    # усложнить обоим ради экономии десяти строк

    async def service_items(self, service: str) -> list[Stored]:
        """Что лежит на складе службы, сперва положенное раньше."""
        async with self.conn.execute(
            """
            SELECT i.id, i.code, i.wear, i.max_wear, i.mod, i.mod_value,
                   i.put_by, i.put_at,
                   (SELECT nickname FROM players WHERE user_id = i.put_by) AS by_name
            FROM service_items i WHERE i.service = ? ORDER BY i.put_at, i.id
            """,
            (service,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Stored(
                id=int(row["id"]),
                code=str(row["code"]),
                wear=int(row["wear"]),
                max_wear=int(row["max_wear"]),
                mod=str(row["mod"]),
                mod_value=int(row["mod_value"]),
                by_id=int(row["put_by"]),
                by_name=str(row["by_name"] or ""),
                put_at=int(row["put_at"]),
            )
            for row in rows
        ]

    async def stash_service_gear(
        self, item_id: int, user_id: int, service: str, limit: int, now: int
    ) -> bool:
        """Переложить вещь из рюкзака на склад службы.

        False — вещи у бойца нет, она надета или на складе нет места.
        """
        async with self.conn.execute(
            """
            SELECT code, wear, max_wear, mod, mod_value FROM inventory
            WHERE id = ? AND user_id = ? AND slot IS NULL
            """,
            (item_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM service_items WHERE service = ?",
            (service,),
        ) as cursor:
            crowd = await cursor.fetchone()
        if int(crowd["n"]) >= limit:
            return False
        cursor = await self.conn.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ? AND slot IS NULL",
            (item_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь увели между запросами
            await self.conn.commit()
            return False
        await self.conn.execute(
            """
            INSERT INTO service_items
                (service, code, wear, max_wear, mod, mod_value, put_by, put_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                service,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
                user_id,
                now,
            ),
        )
        await self.conn.commit()
        return True

    async def take_service_item(
        self, stored_id: int, service: str, user_id: int
    ) -> str:
        """Забрать вещь со склада службы себе в рюкзак."""
        async with self.conn.execute(
            "SELECT code, wear, max_wear, mod, mod_value FROM service_items "
            "WHERE id = ? AND service = ?",
            (stored_id, service),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return ""
        cursor = await self.conn.execute(
            "DELETE FROM service_items WHERE id = ? AND service = ?",
            (stored_id, service),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь забрали между запросами
            await self.conn.commit()
            return ""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
            ),
        )
        await self.conn.commit()
        return str(row["code"])

    # ---------- жильё: квартиры, сейф и кладовая ----------
    #
    # Свободные квартиры в базе не лежат: их считает планировка, а здесь
    # только снятые. Нет строки — квартира свободна, и ключ `(house,
    # number)` не даёт сдать её дважды.

    @staticmethod
    def _lease(row) -> Lease:
        return Lease(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            house=str(row["house"]),
            number=int(row["number"]),
            rooms=int(row["rooms"]),
            rented_at=int(row["rented_at"]),
            paid_until=int(row["paid_until"]),
            vault=int(row["vault"]),
        )

    async def lease_of(self, user_id: int) -> Lease | None:
        """Квартира бойца. None — живёт где придётся."""
        async with self.conn.execute(
            "SELECT * FROM flats WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._lease(row) if row else None

    async def lease_at(self, house: str, number: int) -> Lease | None:
        """Кто снимает эту квартиру. None — свободна."""
        async with self.conn.execute(
            "SELECT * FROM flats WHERE house = ? AND number = ?", (house, number)
        ) as cursor:
            row = await cursor.fetchone()
        return self._lease(row) if row else None

    async def leases_in(self, house: str) -> dict[int, dict]:
        """Кто живёт в этом доме: {номер квартиры: жилец}.

        Ник жильца едет той же строкой: список квартир показывают всем, и
        по нему видно, свободна дверь или за ней уже живут.
        """
        async with self.conn.execute(
            """
            SELECT f.number, f.user_id, f.paid_until, f.rooms,
                   (SELECT nickname FROM players WHERE user_id = f.user_id)
                       AS nickname
            FROM flats f WHERE f.house = ?
            """,
            (house,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            int(row["number"]): {
                "user_id": int(row["user_id"]),
                "nickname": str(row["nickname"] or ""),
                "paid_until": int(row["paid_until"]),
                "rooms": int(row["rooms"]),
            }
            for row in rows
        }

    async def rent_flat(
        self, user_id: int, house: str, number: int, rooms: int,
        paid_until: int, now: int,
    ) -> Lease:
        """Снять квартиру. IntegrityError — её уже сняли или у бойца есть своя."""
        cursor = await self.conn.execute(
            """
            INSERT INTO flats
                (house, number, user_id, rooms, rented_at, paid_until, vault)
            VALUES (?,?,?,?,?,?,0)
            """,
            (house, number, user_id, rooms, now, paid_until),
        )
        await self.conn.commit()
        logger.info("Боец %s снял %s кв. %s", user_id, house, number)
        return Lease(
            id=int(cursor.lastrowid or 0),
            user_id=user_id,
            house=house,
            number=number,
            rooms=rooms,
            rented_at=now,
            paid_until=paid_until,
        )

    async def extend_lease(self, lease_id: int, paid_until: int) -> None:
        """Продлить срок аренды до этого часа."""
        await self.conn.execute(
            "UPDATE flats SET paid_until = ? WHERE id = ?", (paid_until, lease_id)
        )
        await self.conn.commit()

    async def move_flat_vault(self, lease_id: int, amount: int) -> bool:
        """Положить в сейф квартиры или взять оттуда. False — столько нет.

        Проверка стоит в самом запросе — по той же причине, что и у
        клановой: между «посмотрели остаток» и «списали» проходит время.
        """
        cursor = await self.conn.execute(
            "UPDATE flats SET vault = vault + ? WHERE id = ? AND vault + ? >= 0",
            (amount, lease_id, amount),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def flat_items(self, lease_id: int) -> list[Kept]:
        """Что лежит в кладовой, сперва положенное раньше."""
        async with self.conn.execute(
            """
            SELECT id, code, wear, max_wear, mod, mod_value, put_at
            FROM flat_items WHERE flat_id = ? ORDER BY put_at, id
            """,
            (lease_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Kept(
                id=int(row["id"]),
                code=str(row["code"]),
                wear=int(row["wear"]),
                max_wear=int(row["max_wear"]),
                mod=str(row["mod"]),
                mod_value=int(row["mod_value"]),
                put_at=int(row["put_at"]),
            )
            for row in rows
        ]

    async def stash_in_flat(
        self, item_id: int, user_id: int, lease_id: int, limit: int, now: int
    ) -> bool:
        """Переложить вещь из рюкзака в кладовую.

        False — вещи у бойца нет, она надета или в кладовой нет места.
        Вещь не копируется: сперва её забирают из инвентаря и только
        потом кладут на полку.
        """
        async with self.conn.execute(
            """
            SELECT code, wear, max_wear, mod, mod_value FROM inventory
            WHERE id = ? AND user_id = ? AND slot IS NULL
            """,
            (item_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM flat_items WHERE flat_id = ?", (lease_id,)
        ) as cursor:
            crowd = await cursor.fetchone()
        if int(crowd["n"]) >= limit:
            return False
        cursor = await self.conn.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ? AND slot IS NULL",
            (item_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь увели между запросами
            await self.conn.commit()
            return False
        await self.conn.execute(
            """
            INSERT INTO flat_items
                (flat_id, code, wear, max_wear, mod, mod_value, put_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                lease_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
                now,
            ),
        )
        await self.conn.commit()
        return True

    async def take_flat_item(
        self, kept_id: int, lease_id: int, user_id: int
    ) -> str:
        """Забрать вещь из кладовой в рюкзак. Пусто — её там уже нет."""
        async with self.conn.execute(
            "SELECT code, wear, max_wear, mod, mod_value FROM flat_items "
            "WHERE id = ? AND flat_id = ?",
            (kept_id, lease_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return ""
        cursor = await self.conn.execute(
            "DELETE FROM flat_items WHERE id = ? AND flat_id = ?",
            (kept_id, lease_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь забрали между запросами
            await self.conn.commit()
            return ""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
            ),
        )
        await self.conn.commit()
        return str(row["code"])

    async def release_flat(self, lease_id: int) -> tuple[int, int, int]:
        """Освободить квартиру, вернув хозяину всё, что в ней лежало.

        Возвращает (боец, кредиты из сейфа, сколько вещей вернулось).
        Имущество не пропадает ни при выселении, ни при неуплате: вещи
        едут в рюкзак, кредиты — в наличные. Пустой ответ (0, 0, 0) —
        квартиры уже нет.

        Кредиты дописываются запросом, а не через `save_player`: хозяина
        может не быть в сети вовсе, и собирать его целиком ради одного
        числа незачем.
        """
        async with self.conn.execute(
            "SELECT user_id, vault FROM flats WHERE id = ?", (lease_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:  # pragma: no cover - квартиру освободили дважды
            return 0, 0, 0
        user_id = int(row["user_id"])
        money = int(row["vault"])
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM flat_items WHERE flat_id = ?", (lease_id,)
        ) as cursor:
            counted = await cursor.fetchone()
        things = int(counted["n"])
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            SELECT ?, code, wear, max_wear, mod, mod_value
            FROM flat_items WHERE flat_id = ?
            """,
            (user_id, lease_id),
        )
        await self.conn.execute("DELETE FROM flat_items WHERE flat_id = ?", (lease_id,))
        # Машина с парковочного места уезжает к хозяину, как и вещи из
        # кладовой: пропадать имуществу при выселении не с чего
        await self.conn.execute(
            "UPDATE cars SET parked = '', parked_id = 0 "
            "WHERE parked = 'flat' AND parked_id = ?",
            (lease_id,),
        )
        if money:
            await self.conn.execute(
                "UPDATE players SET credits = credits + ? WHERE user_id = ?",
                (money, user_id),
            )
        await self.conn.execute("DELETE FROM flats WHERE id = ?", (lease_id,))
        await self.conn.commit()
        logger.info(
            "Квартира %s освобождена: бойцу %s вернулось %s вещей и %s кредитов",
            lease_id, user_id, things, money,
        )
        return user_id, money, things

    async def overdue_leases(self, before: int) -> list[Lease]:
        """Квартиры, оплаченные только до этого часа и раньше.

        Час сюда передают уже со скидкой на просрочку: неделю после
        конца срока квартира ждёт хозяина, и освобождать её рано.
        """
        async with self.conn.execute(
            "SELECT * FROM flats WHERE paid_until < ? ORDER BY id", (before,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._lease(row) for row in rows]

    # ---------- территории ----------

    @staticmethod
    def _turf(row) -> "TurfRow":
        keys = row.keys()
        return TurfRow(
            code=str(row["code"]),
            owner=Owner(str(row["owner_kind"]), str(row["owner_key"])),
            taken_at=int(row["taken_at"]),
            paid_until=int(row["paid_until"]),
            # Колонки дописали позже: на старой базе их может ещё не быть
            empty_since=int(row["empty_since"]) if "empty_since" in keys else 0,
            counted_at=int(row["counted_at"]) if "counted_at" in keys else 0,
        )

    async def turf_rows(self) -> dict[str, "TurfRow"]:
        """Все точки, которые кто-то когда-то трогал: код → запись.

        Нетронутой точки в базе нет вовсе, и это не упущение: ничья
        точка — это строка, которой нет, а не строка с пустым владельцем.
        Правила читают её как «под городской охраной».
        """
        async with self.conn.execute("SELECT * FROM turf") as cursor:
            rows = await cursor.fetchall()
        return {str(row["code"]): self._turf(row) for row in rows}

    async def turf_row(self, code: str) -> "TurfRow | None":
        async with self.conn.execute(
            "SELECT * FROM turf WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._turf(row) if row else None

    async def take_turf(
        self, code: str, kind: str, key: str, now: int
    ) -> None:
        """Записать нового владельца точки.

        Отдыха после боя у точки нет: взяли её минуту назад — можно
        идти и брать назад. Поэтому запись одна, про владельца, и
        никаких часов неприкосновенности в ней не хранится.

        Час бонусного счёта новый хозяин начинает с нуля — с этой
        минуты: часы прежнего дописывает тот, кто меняет владельца,
        прежде чем позвать сюда.
        """
        await self.conn.execute(
            """
            INSERT INTO turf (code, owner_kind, owner_key, taken_at,
                              paid_until, counted_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
                owner_kind = excluded.owner_kind,
                owner_key  = excluded.owner_key,
                taken_at   = excluded.taken_at,
                paid_until = excluded.paid_until,
                counted_at = excluded.counted_at
            """,
            (code, kind, key, now, now, now),
        )
        await self.conn.commit()
        logger.info("Точка %s теперь за %s:%s", code, kind, key)

    async def pay_turf(self, code: str, paid_until: int) -> None:
        """Передвинуть час, до которого доход уже начислен."""
        await self.conn.execute(
            "UPDATE turf SET paid_until = ? WHERE code = ?", (paid_until, code)
        )
        await self.conn.commit()

    async def service_vault(self, code: str) -> int:
        async with self.conn.execute(
            "SELECT vault FROM service_vault WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["vault"]) if row else 0

    async def add_to_service_vault(self, code: str, amount: int) -> int:
        """Положить в казну службы. Возвращает остаток после."""
        await self.conn.execute(
            """
            INSERT INTO service_vault (code, vault) VALUES (?,?)
            ON CONFLICT(code) DO UPDATE SET vault = vault + excluded.vault
            """,
            (code, amount),
        )
        await self.conn.commit()
        return await self.service_vault(code)

    async def clans_of(self, user_ids: list[int]) -> dict[int, tuple[int, str]]:
        """Чья семья у каждого из этих бойцов: номер и имя.

        Одним запросом, а не чтением бойцов по одному: список «кто
        здесь» бывает на полсотни человек, и полсотни походов в базу
        ради одного столбца — это дорого ни за что.
        """
        if not user_ids:
            return {}
        marks = ",".join("?" * len(user_ids))
        async with self.conn.execute(
            f"""
            SELECT m.user_id AS user_id, c.id AS clan_id, c.name AS name
            FROM clan_members m JOIN clans c ON c.id = m.clan_id
            WHERE m.user_id IN ({marks})
            """,
            tuple(user_ids),
        ) as cursor:
            rows = await cursor.fetchall()
        return {int(row["user_id"]): (int(row["clan_id"]), str(row["name"]))
                for row in rows}

    # ---------- ограбление: лицензии, покой и недельный счёт ----------

    async def licences(self, user_id: int) -> tuple[int, int]:
        """Сколько нападений осталось и сколько пачек куплено всего."""
        async with self.conn.execute(
            "SELECT attacks, bought FROM rob_passes WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row["attacks"]), int(row["bought"])) if row else (0, 0)

    async def buy_licences(self, user_id: int, count: int, attacks: int) -> None:
        """Записать покупку: пачки на счёт, нападения в остаток."""
        await self.conn.execute(
            """
            INSERT INTO rob_passes (user_id, attacks, bought) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                attacks = attacks + excluded.attacks,
                bought  = bought + excluded.bought
            """,
            (user_id, attacks, count),
        )
        await self.conn.commit()

    async def spend_licence(self, user_id: int) -> bool:
        """Списать одно нападение. False — списывать было нечего.

        Условие стоит в самом `UPDATE`, а не в проверке перед ним: две
        кнопки, нажатые разом, иначе потратили бы одно право дважды.
        """
        cursor = await self.conn.execute(
            "UPDATE rob_passes SET attacks = attacks - 1 "
            "WHERE user_id = ? AND attacks > 0",
            (user_id,),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def old_licences(self) -> dict[int, int]:
        """Неиспользованные недельные лицензии старого правила.

        Это для разовой работы: по ней их переносят в бессрочные.
        """
        async with self.conn.execute(
            "SELECT user_id, SUM(bought - spent) AS rest FROM rob_licences "
            "GROUP BY user_id HAVING rest > 0"
        ) as cursor:
            rows = await cursor.fetchall()
        return {int(row["user_id"]): int(row["rest"]) for row in rows}

    async def robbed_at(self, user_id: int) -> int:
        """Час последнего нападения на бойца. Ноль — не трогали."""
        async with self.conn.execute(
            "SELECT robbed_at FROM rob_shield WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["robbed_at"]) if row else 0

    async def mark_robbed(self, user_id: int, now: int) -> None:
        """Отметить нападение: отсюда и считается покой."""
        await self.conn.execute(
            """
            INSERT INTO rob_shield (user_id, robbed_at) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET robbed_at = excluded.robbed_at
            """,
            (user_id, now),
        )
        await self.conn.commit()

    async def clan_week(self, clan_id: int, week: int) -> tuple[int, int]:
        """Нападений за неделю и выдана ли премия."""
        async with self.conn.execute(
            "SELECT attacks, paid FROM clan_weeks WHERE clan_id = ? AND week = ?",
            (clan_id, week),
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row["attacks"]), int(row["paid"])) if row else (0, 0)

    async def add_clan_attack(self, clan_id: int, week: int) -> int:
        """Засчитать семье нападение. Возвращает, сколько их стало."""
        await self.conn.execute(
            """
            INSERT INTO clan_weeks (clan_id, week, attacks, paid)
            VALUES (?,?,1,0)
            ON CONFLICT(clan_id, week) DO UPDATE SET attacks = attacks + 1
            """,
            (clan_id, week),
        )
        await self.conn.commit()
        attacks, _ = await self.clan_week(clan_id, week)
        return attacks

    async def pay_clan_goal(self, clan_id: int, week: int) -> bool:
        """Отметить премию выданной. False — её уже выдавали.

        Отметка и проверка — один запрос: премию за неделю выдают один
        раз, а сводят её все, кто заглянул на страницу семьи.
        """
        cursor = await self.conn.execute(
            """
            UPDATE clan_weeks SET paid = 1
            WHERE clan_id = ? AND week = ? AND paid = 0
            """,
            (clan_id, week),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ---------- разовые работы ----------

    async def chore_done(self, code: str) -> bool:
        """Делали ли уже эту работу."""
        async with self.conn.execute(
            "SELECT 1 FROM chores WHERE code = ?", (code,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_chore(self, code: str, now: int) -> bool:
        """Отметить работу сделанной. False — её уже делали.

        Отметка и проверка — один запрос: бот перезапускается, и два
        захода подряд не должны сбросить точки дважды.
        """
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO chores (code, done_at) VALUES (?,?)",
            (code, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_turf_empty(self, code: str, since: int) -> None:
        """Отметить, с какого часа точка стоит пустой. Ноль — не пустая."""
        await self.conn.execute(
            "UPDATE turf SET empty_since = ? WHERE code = ?", (since, code)
        )
        await self.conn.commit()

    async def mark_turf_counted(self, code: str, upto: int) -> None:
        """Отметить, до какого часа владение точкой уже записано."""
        await self.conn.execute(
            "UPDATE turf SET counted_at = ? WHERE code = ?", (upto, code)
        )
        await self.conn.commit()

    # ---------- турнирная арена ----------

    async def arena_week(self, week: str) -> dict | None:
        """Турнир этой недели. Пусто — на неё ещё никто не записывался."""
        async with self.conn.execute(
            "SELECT * FROM arena_weeks WHERE week = ?", (week,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def open_arena_week(self, week: str) -> None:
        """Завести неделю, если её ещё нет. Второй заход ничего не делает."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO arena_weeks (week) VALUES (?)", (week,)
        )
        await self.conn.commit()

    async def update_arena_week(self, week: str, **fields) -> None:
        if not fields:  # pragma: no cover - вызов без полей
            return
        places = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE arena_weeks SET {places} WHERE week = ?",
            (*fields.values(), week),
        )
        await self.conn.commit()

    async def arena_players(self, week: str) -> list[dict]:
        """Записанные: сильные первыми — по ним и сеется сетка."""
        async with self.conn.execute(
            "SELECT * FROM arena_players WHERE week = ? "
            "ORDER BY level DESC, user_id",
            (week,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def add_arena_player(
        self, week: str, user_id: int, level: int, paid: int
    ) -> bool:
        """Записать бойца. False — он уже в списке."""
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO arena_players (week, user_id, level, paid) "
            "VALUES (?,?,?,?)",
            (week, user_id, level, paid),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def update_arena_player(
        self, week: str, user_id: int, **fields
    ) -> None:
        if not fields:  # pragma: no cover - вызов без полей
            return
        places = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE arena_players SET {places} WHERE week = ? AND user_id = ?",
            (*fields.values(), week, user_id),
        )
        await self.conn.commit()

    async def arena_matches(self, week: str, round: int = 0) -> list[dict]:
        """Пары сетки: всей или одного круга."""
        query = "SELECT * FROM arena_matches WHERE week = ?"
        args: tuple = (week,)
        if round:
            query += " AND round = ?"
            args = (week, round)
        async with self.conn.execute(query + " ORDER BY round, slot", args) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def add_arena_match(
        self, week: str, round: int, slot: int,
        red_id: int | None, blue_id: int | None, **fields
    ) -> None:
        """Записать пару круга. Второй заход её не задваивает."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO arena_matches "
            "(week, round, slot, red_id, blue_id) VALUES (?,?,?,?,?)",
            (week, round, slot, red_id, blue_id),
        )
        await self.conn.commit()
        if fields:
            await self.update_arena_match(week, round, slot, **fields)

    async def update_arena_match(
        self, week: str, round: int, slot: int, **fields
    ) -> None:
        if not fields:  # pragma: no cover - вызов без полей
            return
        places = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE arena_matches SET {places} "
            "WHERE week = ? AND round = ? AND slot = ?",
            (*fields.values(), week, round, slot),
        )
        await self.conn.commit()

    # ---------- недельный бонусный журнал ----------

    async def add_bonus(
        self, kind: str, key: str, week: int, metric: str, amount: int
    ) -> int:
        """Засчитать стороне сделанное. Возвращает, сколько стало."""
        await self.conn.execute(
            """
            INSERT INTO bonus_weeks (side_kind, side_key, week, metric, value)
            VALUES (?,?,?,?,?)
            ON CONFLICT(side_kind, side_key, week, metric)
            DO UPDATE SET value = value + excluded.value
            """,
            (kind, key, week, metric, amount),
        )
        await self.conn.commit()
        tally = await self.bonus_week(kind, key, week)
        return tally.get(metric, 0)

    async def bonus_week(
        self, kind: str, key: str, week: int
    ) -> dict[str, int]:
        """Что эта сторона сделала за ту неделю: мерка → число."""
        async with self.conn.execute(
            """
            SELECT metric, value FROM bonus_weeks
            WHERE side_kind = ? AND side_key = ? AND week = ?
            """,
            (kind, key, week),
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["metric"]): int(row["value"]) for row in rows}

    async def owed_bonus_weeks(self, before: int) -> list[tuple[str, str, int]]:
        """Чьи недели кончились, а пулл за них ещё не выдан."""
        async with self.conn.execute(
            """
            SELECT DISTINCT w.side_kind AS kind, w.side_key AS key,
                   w.week AS week
            FROM bonus_weeks AS w
            LEFT JOIN bonus_paid AS p
              ON p.side_kind = w.side_kind AND p.side_key = w.side_key
             AND p.week = w.week
            WHERE w.week < ? AND p.week IS NULL
            ORDER BY w.week
            """,
            (before,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            (str(row["kind"]), str(row["key"]), int(row["week"]))
            for row in rows
        ]

    async def pay_bonus_week(
        self, kind: str, key: str, week: int, amount: int, now: int
    ) -> bool:
        """Отметить пулл выданным. False — его уже выдавали.

        Отметка и проверка — один запрос: пулл за неделю выдают один
        раз, а сводят его все, кто заглянул на экран.
        """
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO bonus_paid
                (side_kind, side_key, week, amount, paid_at)
            VALUES (?,?,?,?,?)
            """,
            (kind, key, week, amount, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def bonus_payout(
        self, kind: str, key: str, week: int
    ) -> dict[str, int] | None:
        """Что стороне выдали за ту неделю. Пусто — ещё не выдавали."""
        async with self.conn.execute(
            """
            SELECT amount, paid_at FROM bonus_paid
            WHERE side_kind = ? AND side_key = ? AND week = ?
            """,
            (kind, key, week),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return {"amount": int(row["amount"]), "paid_at": int(row["paid_at"])}

    async def service_split(self, code: str) -> str:
        """Как служба делит бонусный пулл."""
        async with self.conn.execute(
            "SELECT split FROM service_vault WHERE code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
        return str(row["split"]) if row else "even"

    async def set_service_split(self, code: str, split: str) -> None:
        """Записать, как делить пулл. Строки нет — заводим её пустой."""
        await self.conn.execute(
            """
            INSERT INTO service_vault (code, vault, split) VALUES (?,0,?)
            ON CONFLICT(code) DO UPDATE SET split = excluded.split
            """,
            (code, split),
        )
        await self.conn.commit()

    async def free_turf(self, code: str) -> bool:
        """Снять с точки владельца: она снова под городской охраной.

        Строки у ничьей точки нет вовсе — её и удаляем. Это и есть
        «вернуть городу»: дом остаётся с городской охраной, как был до
        первого захвата.
        """
        cursor = await self.conn.execute(
            "DELETE FROM turf WHERE code = ?", (code,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def wipe_turf(self) -> int:
        """Снять всех владельцев с точек. Возвращает, сколько сняли."""
        cursor = await self.conn.execute("DELETE FROM turf")
        await self.conn.commit()
        return cursor.rowcount

    async def hands_of_potion(self, code: str) -> dict[int, int]:
        """У кого на руках эта склянка и сколько. Это для разовых работ.

        Читаем напрямую: снятую с прилавка склянку `list_potions` уже не
        показывает — она не знает кодов, которых в игре нет, — а
        вернуть за неё деньги надо как раз по этим строкам.
        """
        async with self.conn.execute(
            "SELECT user_id, count FROM potions WHERE code = ? AND count > 0",
            (code,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {int(row["user_id"]): int(row["count"]) for row in rows}

    async def drop_potion_everywhere(self, code: str) -> int:
        """Убрать склянку из всех рюкзаков и снять её действие."""
        cursor = await self.conn.execute(
            "DELETE FROM potions WHERE code = ?", (code,)
        )
        await self.conn.execute("DELETE FROM effects WHERE code = ?", (code,))
        await self.conn.commit()
        return cursor.rowcount

    # ---------- нанятые: наёмники и патрульные ----------

    async def hire(
        self, owner_id: int, side: str, grade: str, now: int
    ) -> int:
        """Записать нанятого. Возвращает его номер."""
        cursor = await self.conn.execute(
            "INSERT INTO hirelings (owner_id, side, grade, hired_at) "
            "VALUES (?,?,?,?)",
            (owner_id, side, grade, now),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def hirelings_of(self, owner_id: int) -> list[dict]:
        """Все нанятые этого бойца: и в резерве, и на точках."""
        async with self.conn.execute(
            "SELECT * FROM hirelings WHERE owner_id = ? ORDER BY id",
            (owner_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def hirelings_at(self, spot: str) -> list[dict]:
        """Кто стоит на этой точке. Пусто — только городская охрана."""
        async with self.conn.execute(
            "SELECT * FROM hirelings WHERE spot = ? ORDER BY id", (spot,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def posted_counts(self) -> dict[str, int]:
        """Сколько нанятых на каждой точке — для карты одним запросом."""
        async with self.conn.execute(
            "SELECT spot, COUNT(*) AS total FROM hirelings "
            "WHERE spot <> '' GROUP BY spot"
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["spot"]): int(row["total"]) for row in rows}

    async def post_hireling(self, hire_id: int, owner_id: int, spot: str) -> bool:
        """Выставить своего нанятого на точку. False — он не твой или уже стоит."""
        cursor = await self.conn.execute(
            "UPDATE hirelings SET spot = ? WHERE id = ? AND owner_id = ? "
            "AND spot = ''",
            (spot, hire_id, owner_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def drop_hireling(self, hire_id: int) -> bool:
        """Снять одного нанятого насовсем: его убили на точке."""
        cursor = await self.conn.execute(
            "DELETE FROM hirelings WHERE id = ?", (hire_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clear_spot(self, spot: str) -> int:
        """Вынести всех нанятых с точки. Они гибнут насовсем."""
        cursor = await self.conn.execute(
            "DELETE FROM hirelings WHERE spot = ?", (spot,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def clear_all_hirelings(self) -> int:
        """Распустить всех нанятых разом. Это для разовых работ."""
        cursor = await self.conn.execute("DELETE FROM hirelings")
        await self.conn.commit()
        return cursor.rowcount

    # ---------- выборы мэра ----------

    async def nominate(
        self, day: str, user_id: int, program: str, now: int
    ) -> bool:
        """Записать кандидата. False — он уже выдвигался на эти выборы."""
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO candidates (day, user_id, program, filed_at)
            VALUES (?,?,?,?)
            """,
            (day, user_id, program, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def candidates(self, day: str) -> list[dict]:
        """Кандидаты этих выборов, снятые вместе со всеми."""
        async with self.conn.execute(
            "SELECT * FROM candidates WHERE day = ? ORDER BY filed_at, user_id",
            (day,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def drop_candidate(self, day: str, user_id: int, now: int) -> bool:
        """Снять кандидата. False — он уже снят или не выдвигался."""
        cursor = await self.conn.execute(
            """
            UPDATE candidates SET dropped_at = ?
            WHERE day = ? AND user_id = ? AND dropped_at = 0
            """,
            (now, day, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def cast_ballot(
        self, day: str, user_id: int, for_id: int, now: int
    ) -> bool:
        """Отдать голос. False — этот боец уже голосовал.

        Один голос на человека держит первичный ключ, а не проверка
        перед запросом: две кнопки, нажатые разом, иначе дали бы два.
        """
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO ballots (day, user_id, for_id, cast_at)
            VALUES (?,?,?,?)
            """,
            (day, user_id, for_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def ballot_of(self, day: str, user_id: int) -> int:
        """За кого проголосовал этот боец. Ноль — ещё не голосовал."""
        async with self.conn.execute(
            "SELECT for_id FROM ballots WHERE day = ? AND user_id = ?",
            (day, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["for_id"]) if row else 0

    async def tally(self, day: str) -> dict[int, int]:
        """Сколько голосов у каждого кандидата."""
        async with self.conn.execute(
            "SELECT for_id, COUNT(*) AS votes FROM ballots WHERE day = ? "
            "GROUP BY for_id",
            (day,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {int(row["for_id"]): int(row["votes"]) for row in rows}

    async def election(self, day: str) -> dict | None:
        async with self.conn.execute(
            "SELECT * FROM elections WHERE day = ?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def close_election(
        self, day: str, winner_id: int, votes: int, turnout: int, now: int
    ) -> bool:
        """Записать итог выборов. False — их уже сводили."""
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO elections
                (day, closed_at, winner_id, votes, turnout)
            VALUES (?,?,?,?,?)
            """,
            (day, now, winner_id, votes, turnout),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mayor(self) -> dict:
        """Кто сейчас мэр. Пустой номер — кресло свободно."""
        async with self.conn.execute(
            "SELECT user_id, since, day FROM mayor WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else {"user_id": 0, "since": 0, "day": ""}

    async def seat_mayor(self, user_id: int, since: int, day: str) -> None:
        """Посадить мэра в кресло. Прежний в тот же миг его освобождает."""
        await self.conn.execute(
            """
            INSERT INTO mayor (id, user_id, since, day) VALUES (1,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                since   = excluded.since,
                day     = excluded.day
            """,
            (user_id, since, day),
        )
        await self.conn.commit()

    # ---------- «Чёрный пуск»: рейд на электростанцию ----------
    #
    # Инстанс читается и пишется целиком: `blackout_read` отдаёт json с
    # версией, `blackout_write` кладёт его обратно, если версия не
    # сменилась. Этого хватает за глаза — рейд один на отряд, а SQLite
    # пишет по одному — и даёт ровно то, чего требует кооператив: две
    # команды не выдадут предмет дважды.

    async def blackout_open(
        self, cycle: str, leader_id: int, now: int, state: str
    ) -> int:
        """Завести инстанс рейда. Отдаёт его номер."""
        cursor = await self.conn.execute(
            "INSERT INTO blackout_runs "
            "(cycle, phase, leader_id, opened_at, version, state) "
            "VALUES (?,'lobby',?,?,1,?)",
            (cycle, leader_id, now, state),
        )
        run_id = int(cursor.lastrowid)
        await self.conn.execute(
            "INSERT OR IGNORE INTO blackout_members (run_id, user_id) "
            "VALUES (?,?)",
            (run_id, leader_id),
        )
        await self.conn.commit()
        return run_id

    async def blackout_read(self, run_id: int) -> dict | None:
        """Инстанс целиком: строка таблицы вместе с json состояния."""
        async with self.conn.execute(
            "SELECT * FROM blackout_runs WHERE id = ?", (run_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def blackout_write(
        self, run_id: int, version: int, phase: str, state: str, now: int
    ) -> bool:
        """Записать состояние, если его не сменили под нами.

        False — версия разошлась: кто-то успел походить первым, и
        команду надо считать заново на свежем состоянии.
        """
        cursor = await self.conn.execute(
            "UPDATE blackout_runs SET state = ?, phase = ?, version = ?, "
            "closed_at = CASE WHEN ? = 'over' AND closed_at = 0 THEN ? "
            "ELSE closed_at END WHERE id = ? AND version = ?",
            (state, phase, version + 1, phase, now, run_id, version),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def blackout_set_leader(self, run_id: int, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE blackout_runs SET leader_id = ? WHERE id = ?",
            (user_id, run_id),
        )
        await self.conn.commit()

    async def blackout_start(self, run_id: int, now: int) -> None:
        await self.conn.execute(
            "UPDATE blackout_runs SET started_at = ?, phase = 'run' "
            "WHERE id = ? AND started_at = 0",
            (now, run_id),
        )
        await self.conn.commit()

    async def blackout_join(self, run_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO blackout_members (run_id, user_id, left_at) "
            "VALUES (?,?,0) ON CONFLICT(run_id, user_id) DO UPDATE SET "
            "left_at = 0",
            (run_id, user_id),
        )
        await self.conn.commit()

    async def blackout_part(self, run_id: int, user_id: int, now: int) -> None:
        await self.conn.execute(
            "UPDATE blackout_members SET left_at = ? "
            "WHERE run_id = ? AND user_id = ?",
            (now, run_id, user_id),
        )
        await self.conn.commit()

    async def blackout_of(self, user_id: int) -> int:
        """В каком рейде этот боец. Ноль — ни в каком."""
        async with self.conn.execute(
            "SELECT m.run_id FROM blackout_members m "
            "JOIN blackout_runs r ON r.id = m.run_id "
            "WHERE m.user_id = ? AND m.left_at = 0 AND r.phase <> 'over' "
            "ORDER BY m.run_id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["run_id"]) if row else 0

    async def blackout_live(self, cycle: str = "") -> list[dict]:
        """Открытые рейды: по ним собирают отряд."""
        where = "phase = 'lobby'"
        args: tuple = ()
        if cycle:
            where += " AND cycle = ?"
            args = (cycle,)
        async with self.conn.execute(
            f"SELECT * FROM blackout_runs WHERE {where} ORDER BY id", args
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def blackout_done(
        self, run_id: int, command_id: str
    ) -> str | None:
        """Что ответили на эту команду в прошлый раз. None — впервые."""
        if not command_id:
            return None
        async with self.conn.execute(
            "SELECT said FROM blackout_commands WHERE run_id = ? "
            "AND command_id = ?",
            (run_id, command_id),
        ) as cursor:
            row = await cursor.fetchone()
        return str(row["said"]) if row else None

    async def blackout_remember(
        self, run_id: int, command_id: str, said: str, now: int
    ) -> None:
        if not command_id:
            return
        await self.conn.execute(
            "INSERT OR IGNORE INTO blackout_commands "
            "(run_id, command_id, said, at) VALUES (?,?,?,?)",
            (run_id, command_id, said, now),
        )
        await self.conn.commit()

    async def blackout_pay(
        self, user_id: int, cycle: str, stage: str, amount: int,
        run_id: int, now: int,
    ) -> bool:
        """Отметить выплату. False — за этот цикл её уже платили.

        Уникальность держит первичный ключ: повторный запрос, второй
        заход в том же цикле и гонка двух команд упираются в базу, а не
        в проверку питоном.
        """
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO blackout_rewards "
            "(user_id, cycle, stage, amount, run_id, paid_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, cycle, stage, amount, run_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def blackout_paid(self, user_id: int, cycle: str) -> list[dict]:
        """Что этому бойцу уже заплатили за этот цикл."""
        async with self.conn.execute(
            "SELECT stage, amount, paid_at FROM blackout_rewards "
            "WHERE user_id = ? AND cycle = ?",
            (user_id, cycle),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ---------- дежурная часть, ордера и тюрьма ----------

    async def open_case(
        self, victim_id: int, suspect_id: int, place: str, loot: int,
        won: int, now: int,
    ) -> int:
        """Записать нападение в дежурную часть. Возвращает номер дела."""
        cursor = await self.conn.execute(
            """
            INSERT INTO police_cases
                (victim_id, suspect_id, place, loot, won, robbed_at)
            VALUES (?,?,?,?,?,?)
            """,
            (victim_id, suspect_id, place, loot, won, now),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def police_cases(self, limit: int = 60) -> list[dict]:
        """Дежурная часть целиком: свежее сверху."""
        async with self.conn.execute(
            "SELECT * FROM police_cases ORDER BY robbed_at DESC, id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cases_of_victim(self, victim_id: int) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM police_cases WHERE victim_id = ? ORDER BY id DESC",
            (victim_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cases_of_suspect(self, suspect_id: int) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM police_cases WHERE suspect_id = ? ORDER BY id DESC",
            (suspect_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def file_case(self, case_id: int, victim_id: int, now: int) -> bool:
        """Принять заявление. False — дело чужое или заявление уже есть.

        Жертва в условии, а не в проверке перед запросом: заявление по
        чужому делу не должно проходить даже в гонке двух нажатий.
        """
        cursor = await self.conn.execute(
            """
            UPDATE police_cases SET filed_at = ?
            WHERE id = ? AND victim_id = ? AND filed_at = 0 AND arrested_at = 0
            """,
            (now, case_id, victim_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def wanted(self, now: int = 0) -> list[dict]:
        """По кому выписан ордер: заявление есть, задержания нет."""
        async with self.conn.execute(
            """
            SELECT suspect_id,
                   COUNT(*)   AS cases,
                   SUM(loot)  AS loot,
                   MIN(filed_at) AS since
            FROM police_cases
            WHERE filed_at > 0 AND arrested_at = 0
            GROUP BY suspect_id
            ORDER BY since
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close_cases(
        self, suspect_id: int, officer_id: int, now: int
    ) -> int:
        """Закрыть задержанием все дела этого мафиози. Отдаёт сумму.

        Сумму считаем до записи: после неё `arrested_at` уже стоит, и
        отличить только что закрытые дела от закрытых вчера нельзя.
        """
        async with self.conn.execute(
            """
            SELECT COALESCE(SUM(loot), 0) AS loot FROM police_cases
            WHERE suspect_id = ? AND filed_at > 0 AND arrested_at = 0
            """,
            (suspect_id,),
        ) as cursor:
            row = await cursor.fetchone()
        loot = int(row["loot"]) if row else 0
        await self.conn.execute(
            """
            UPDATE police_cases SET arrested_at = ?, arrested_by = ?
            WHERE suspect_id = ? AND filed_at > 0 AND arrested_at = 0
            """,
            (now, officer_id, suspect_id),
        )
        await self.conn.commit()
        return loot

    async def repay_case(self, case_id: int, victim_id: int, now: int) -> int:
        """Выдать жертве её деньги. Ноль — выдавать было нечего."""
        async with self.conn.execute(
            """
            SELECT loot FROM police_cases
            WHERE id = ? AND victim_id = ? AND arrested_at > 0 AND repaid_at = 0
            """,
            (case_id, victim_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or not int(row["loot"]):
            return 0
        cursor = await self.conn.execute(
            """
            UPDATE police_cases SET repaid_at = ?
            WHERE id = ? AND victim_id = ? AND arrested_at > 0 AND repaid_at = 0
            """,
            (now, case_id, victim_id),
        )
        await self.conn.commit()
        return int(row["loot"]) if cursor.rowcount else 0

    async def jail_term(self, user_id: int) -> tuple[int, int]:
        """До какого часа сидит и в который раз его взяли."""
        async with self.conn.execute(
            "SELECT until, times FROM jail WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row["until"]), int(row["times"])) if row else (0, 0)

    async def inmates(self, now: int) -> list[tuple[int, int, int]]:
        """Кто сидит сейчас: номер, до какого часа и в который раз.

        Отсидевшие остаются строкой — по ней считается, в который раз
        человека берут, — и в список они не идут: срок кончился.
        """
        async with self.conn.execute(
            "SELECT user_id, until, times FROM jail WHERE until > ? "
            "ORDER BY until",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            (int(row["user_id"]), int(row["until"]), int(row["times"]))
            for row in rows
        ]

    async def put_in_jail(self, user_id: int, until: int, times: int) -> None:
        """Посадить до этого часа и записать, который это раз.

        Срок считает служба, а не база: он зависит от числа отсидок, и
        это правило, а не хранение.
        """
        await self.conn.execute(
            """
            INSERT INTO jail (user_id, until, times) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                until = excluded.until,
                times = excluded.times
            """,
            (user_id, until, times),
        )
        await self.conn.commit()

    async def service_week(self, code: str, week: int) -> tuple[int, int]:
        """Задержаний за неделю и роздана ли копилка."""
        async with self.conn.execute(
            "SELECT arrests, paid FROM service_weeks WHERE code = ? AND week = ?",
            (code, week),
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row["arrests"]), int(row["paid"])) if row else (0, 0)

    async def add_service_arrest(self, code: str, week: int) -> int:
        await self.conn.execute(
            """
            INSERT INTO service_weeks (code, week, arrests, paid)
            VALUES (?,?,1,0)
            ON CONFLICT(code, week) DO UPDATE SET arrests = arrests + 1
            """,
            (code, week),
        )
        await self.conn.commit()
        arrests, _ = await self.service_week(code, week)
        return arrests

    async def pay_service_week(self, code: str, week: int) -> bool:
        """Отметить копилку розданной. False — её уже раздавали."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO service_weeks (code, week) VALUES (?,?)",
            (code, week),
        )
        cursor = await self.conn.execute(
            "UPDATE service_weeks SET paid = 1 WHERE code = ? AND week = ? "
            "AND paid = 0",
            (code, week),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def attacks_in_week(self, week: int) -> int:
        """Сколько нападений город видел за эту неделю — по всем семьям."""
        async with self.conn.execute(
            "SELECT COALESCE(SUM(attacks), 0) AS total FROM clan_weeks WHERE week = ?",
            (week,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    # ---------- вклады и кредиты ----------

    @staticmethod
    def _deposit(row) -> Deposit:
        return Deposit(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            amount=int(row["amount"]),
            months=int(row["months"]),
            opened_at=int(row["opened_at"]),
            paid_until=int(row["paid_until"]),
        )

    async def deposits_of(self, user_id: int) -> list[Deposit]:
        """Вклады бойца, сперва те, что кончаются раньше."""
        async with self.conn.execute(
            "SELECT * FROM deposits WHERE user_id = ? ORDER BY paid_until, id",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._deposit(row) for row in rows]

    async def deposit_row(self, deposit_id: int, user_id: int) -> Deposit | None:
        """Один вклад бойца. None — такого у него нет."""
        async with self.conn.execute(
            "SELECT * FROM deposits WHERE id = ? AND user_id = ?",
            (deposit_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return self._deposit(row) if row else None

    async def open_deposit(
        self, user_id: int, amount: int, months: int, now: int, until: int
    ) -> int:
        """Записать вклад. Отдаёт его номер."""
        cursor = await self.conn.execute(
            """
            INSERT INTO deposits (user_id, amount, months, opened_at, paid_until)
            VALUES (?,?,?,?,?)
            """,
            (user_id, amount, months, now, until),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def close_deposit(self, deposit_id: int, user_id: int) -> bool:
        """Снять вклад. False — его уже сняли."""
        cursor = await self.conn.execute(
            "DELETE FROM deposits WHERE id = ? AND user_id = ?",
            (deposit_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _loan(row) -> Loan:
        return Loan(
            user_id=int(row["user_id"]),
            amount=int(row["amount"]),
            months=int(row["months"]),
            payment=int(row["payment"]),
            left=int(row["left_pays"]),
            taken_at=int(row["taken_at"]),
            due_at=int(row["due_at"]),
            owed=int(row["owed"]),
            penalty=int(row["penalty"]),
            penalty_at=int(row["penalty_at"]),
        )

    async def loan_of(self, user_id: int) -> Loan | None:
        """Кредит бойца. None — кредита нет."""
        async with self.conn.execute(
            "SELECT * FROM loans WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._loan(row) if row else None

    async def open_loan(
        self,
        user_id: int,
        amount: int,
        months: int,
        payment: int,
        now: int,
        due_at: int,
    ) -> bool:
        """Записать кредит. False — он у бойца уже есть."""
        try:
            await self.conn.execute(
                """
                INSERT INTO loans (
                    user_id, amount, months, payment, left_pays,
                    taken_at, due_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (user_id, amount, months, payment, months, now, due_at),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - второй кредит
            return False
        await self.conn.commit()
        return True

    async def save_loan(
        self,
        user_id: int,
        left: int,
        due_at: int,
        owed: int,
        penalty: int,
        penalty_at: int,
    ) -> None:
        """Переписать состояние кредита после списаний и пеней."""
        await self.conn.execute(
            """
            UPDATE loans SET left_pays = ?, due_at = ?, owed = ?,
                             penalty = ?, penalty_at = ?
            WHERE user_id = ?
            """,
            (left, due_at, owed, penalty, penalty_at, user_id),
        )
        await self.conn.commit()

    async def close_loan(self, user_id: int) -> bool:
        """Закрыть кредит: выплачен целиком."""
        cursor = await self.conn.execute(
            "DELETE FROM loans WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ---------- общежитие ----------

    @staticmethod
    def _room(row) -> "DormRoom":
        return DormRoom(
            user_id=int(row["user_id"]),
            number=int(row["number"]),
            course=str(row["course"]),
            given_at=int(row["given_at"]),
            closed_at=int(row["closed_at"]),
            vault=int(row["vault"]),
        )

    async def dorm_room(self, user_id: int) -> "DormRoom | None":
        """Комната бойца. None — комнаты у него нет."""
        async with self.conn.execute(
            "SELECT * FROM dorm_rooms WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._room(row) if row else None

    async def dorm_numbers(self) -> list[int]:
        """Занятые номера комнат — по ним и выбирают свободный."""
        async with self.conn.execute(
            "SELECT number FROM dorm_rooms ORDER BY number"
        ) as cursor:
            rows = await cursor.fetchall()
        return [int(row["number"]) for row in rows]

    async def give_dorm_room(
        self, user_id: int, number: int, course: str, now: int
    ) -> bool:
        """Выдать комнату. False — у бойца она уже есть."""
        try:
            await self.conn.execute(
                "INSERT INTO dorm_rooms (user_id, number, course, given_at) "
                "VALUES (?,?,?,?)",
                (user_id, number, course, now),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - вторая комната
            return False
        await self.conn.commit()
        return True

    async def close_dorm_room(self, user_id: int, now: int) -> bool:
        """Запереть комнату: учёба кончилась. False — она уже заперта."""
        cursor = await self.conn.execute(
            "UPDATE dorm_rooms SET closed_at = ? "
            "WHERE user_id = ? AND closed_at = 0",
            (now, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def move_dorm_vault(self, user_id: int, amount: int) -> bool:
        """Положить в сейф комнаты или взять из него. False — столько нет."""
        cursor = await self.conn.execute(
            "UPDATE dorm_rooms SET vault = vault + ? "
            "WHERE user_id = ? AND vault + ? >= 0",
            (amount, user_id, amount),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def dorm_items(self, user_id: int) -> list[Kept]:
        """Что лежит в комнате, сперва положенное раньше."""
        async with self.conn.execute(
            """
            SELECT id, code, wear, max_wear, mod, mod_value, put_at
            FROM dorm_items WHERE user_id = ? ORDER BY put_at, id
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Kept(
                id=int(row["id"]),
                code=str(row["code"]),
                wear=int(row["wear"]),
                max_wear=int(row["max_wear"]),
                mod=str(row["mod"]),
                mod_value=int(row["mod_value"]),
                put_at=int(row["put_at"]),
            )
            for row in rows
        ]

    async def stash_in_dorm(
        self, item_id: int, user_id: int, limit: int, now: int
    ) -> bool:
        """Переложить вещь из рюкзака в комнату. Та же сделка, что в квартире."""
        async with self.conn.execute(
            """
            SELECT code, wear, max_wear, mod, mod_value FROM inventory
            WHERE id = ? AND user_id = ? AND slot IS NULL
            """,
            (item_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM dorm_items WHERE user_id = ?", (user_id,)
        ) as cursor:
            crowd = await cursor.fetchone()
        if int(crowd["n"]) >= limit:
            return False
        cursor = await self.conn.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ? AND slot IS NULL",
            (item_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь увели между запросами
            await self.conn.commit()
            return False
        await self.conn.execute(
            """
            INSERT INTO dorm_items
                (user_id, code, wear, max_wear, mod, mod_value, put_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                user_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
                now,
            ),
        )
        await self.conn.commit()
        return True

    async def take_dorm_item(self, kept_id: int, user_id: int) -> str:
        """Забрать вещь из комнаты в рюкзак. Пусто — её там уже нет."""
        async with self.conn.execute(
            "SELECT code, wear, max_wear, mod, mod_value FROM dorm_items "
            "WHERE id = ? AND user_id = ?",
            (kept_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return ""
        cursor = await self.conn.execute(
            "DELETE FROM dorm_items WHERE id = ? AND user_id = ?",
            (kept_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь забрали между запросами
            await self.conn.commit()
            return ""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id,
                str(row["code"]),
                int(row["wear"]),
                int(row["max_wear"]),
                str(row["mod"]),
                int(row["mod_value"]),
            ),
        )
        await self.conn.commit()
        return str(row["code"])

    async def clear_dorm_room(self, user_id: int) -> tuple[int, int]:
        """Отдать хозяину всё из комнаты и освободить её.

        Возвращает (сколько вещей вернулось, сколько кредитов). Вещи
        едут в рюкзак, деньги из сейфа — наличными: комната кончилась,
        но имущество остаётся имуществом.
        """
        async with self.conn.execute(
            "SELECT vault FROM dorm_rooms WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return 0, 0
        money = int(row["vault"])
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM dorm_items WHERE user_id = ?", (user_id,)
        ) as cursor:
            counted = await cursor.fetchone()
        things = int(counted["n"])
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            SELECT user_id, code, wear, max_wear, mod, mod_value
            FROM dorm_items WHERE user_id = ?
            """,
            (user_id,),
        )
        await self.conn.execute(
            "DELETE FROM dorm_items WHERE user_id = ?", (user_id,)
        )
        if money:
            await self.conn.execute(
                "UPDATE players SET credits = credits + ? WHERE user_id = ?",
                (money, user_id),
            )
        await self.conn.execute(
            "DELETE FROM dorm_rooms WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()
        logger.info(
            "Комната общежития бойца %s освобождена: %s вещей и %s кредитов",
            user_id, things, money,
        )
        return things, money

    # ---------- машины ----------

    async def cars_of(self, user_id: int) -> list[dict]:
        """Машины бойца: свои и взятые. Порядок — как брали."""
        async with self.conn.execute(
            """
            SELECT id, code, until, took_at, parked, parked_id, driven,
                   plate, stolen_from, stolen_at, stolen_filed,
                   at_place, plate_first, restamped
            FROM cars WHERE user_id = ? ORDER BY took_at, id
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def car_row(self, user_id: int, code: str) -> dict | None:
        """Эта машина у этого бойца. None — нет такой."""
        async with self.conn.execute(
            "SELECT id, code, until, took_at, parked, parked_id, driven, "
            "plate, stolen_from, stolen_at, stolen_filed, "
            "at_place, plate_first, restamped "
            "FROM cars WHERE user_id = ? AND code = ?",
            (user_id, code),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    # ---------- номера ----------

    async def _fresh_plate(self) -> str:
        """Свободный номер по городской маске.

        Бросок, а не счётчик: номера в городе не идут подряд. Повтор
        ловится запросом — миллион семьсот тысяч комбинаций, и до
        второго круга город не доживёт, но проверить дешевле, чем
        однажды выдать два одинаковых знака.
        """
        from bot.game.plates import random_plate

        for _ in range(50):
            plate = random_plate()
            async with self.conn.execute(
                "SELECT 1 FROM cars WHERE plate = ?", (plate,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    return plate
        return ""  # pragma: no cover - город столько машин не держит

    async def ensure_plates(self) -> int:
        """Выдать номера тем машинам, что записаны до номеров.

        Зовётся оттуда же, откуда сметают кончившиеся сроки: номер
        нужен в тот миг, когда на машину посмотрели, а не раньше.
        """
        async with self.conn.execute(
            "SELECT id FROM cars WHERE plate = ''"
        ) as cursor:
            rows = [int(row["id"]) for row in await cursor.fetchall()]
        for car_id in rows:
            plate = await self._fresh_plate()
            if plate:
                await self.conn.execute(
                    "UPDATE cars SET plate = ?, plate_first = ? WHERE id = ?",
                    (plate, plate, car_id),
                )
        if rows:
            await self.conn.commit()
        return len(rows)

    async def set_plate(self, car_id: int, plate: str) -> bool:
        """Перебить номер. False — такой знак в городе уже есть.

        Выданный городом знак (`plate_first`) остаётся при машине: по
        нему полиция и узнаёт подделку. Стереть его нельзя — иначе
        перебитый номер делал бы машину новой, а он делает её краденой.
        """
        try:
            cursor = await self.conn.execute(
                "UPDATE cars SET plate = ?, restamped = restamped + 1 "
                "WHERE id = ?",
                (plate, car_id),
            )
        except sqlite3.IntegrityError:
            return False
        await self.conn.commit()
        return cursor.rowcount > 0

    async def car_by_id(self, car_id: int) -> dict | None:
        """Машина по номеру строки — вместе с хозяином и его именем."""
        async with self.conn.execute(
            """
            SELECT c.id, c.user_id, c.code, c.until, c.took_at, c.parked,
                   c.parked_id, c.driven, c.plate, c.stolen_from,
                   c.stolen_at, c.stolen_filed, c.at_place, c.plate_first,
                   c.restamped,
                   p.nickname, p.car AS driving, p.seen_at,
                   p.location, p.travel_to, p.arrives_at
            FROM cars c JOIN players p ON p.user_id = c.user_id
            WHERE c.id = ?
            """,
            (car_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def won_case_since(
        self, suspect_id: int, victim_id: int, since: int
    ) -> bool:
        """Бил ли этот того и выиграл ли — за последнее время.

        По этому следу и дают увести машину у того, кто в сети: победа
        над живым человеком и есть выбор «угнать или нет», а не отдельная
        кнопка рядом с ним.
        """
        async with self.conn.execute(
            "SELECT 1 FROM police_cases WHERE suspect_id = ? AND victim_id = ? "
            "AND won = 1 AND robbed_at >= ? LIMIT 1",
            (suspect_id, victim_id, since),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def car_by_plate(self, plate: str) -> dict | None:
        """Чья это машина. None — такого номера в городе нет."""
        async with self.conn.execute(
            "SELECT id, user_id, code, until, parked, parked_id, plate, "
            "stolen_from, stolen_at, stolen_filed FROM cars WHERE plate = ?",
            (plate,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def city_cars(self, now: int) -> list[dict]:
        """Все живые машины города: чья, какая, где стоит и в розыске ли.

        Это и есть автоучёт: по нему полиция и смотрит, кого проверять.
        Машины с кончившимся сроком в учёт не идут — их уже нет.
        """
        async with self.conn.execute(
            """
            SELECT c.id, c.user_id, c.code, c.until, c.parked, c.parked_id,
                   c.plate, c.stolen_from, c.stolen_at, c.stolen_filed,
                   c.at_place, c.plate_first, c.restamped,
                   p.nickname, p.car AS driving, p.seen_at,
                   p.credits, p.location,
                   COALESCE(d.licence_at, 0) AS licence_at
            FROM cars c
            JOIN players p ON p.user_id = c.user_id
            LEFT JOIN driving d ON d.user_id = c.user_id
            WHERE c.until = 0 OR c.until > ?
            ORDER BY p.nickname, c.took_at, c.id
            """,
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ---------- машина на улице и в участке ----------

    async def drop_car_at(
        self, user_id: int, code: str, place: str, now: int
    ) -> bool:
        """Оставить машину у здания. Хозяин прежний, место — город."""
        cursor = await self.conn.execute(
            "UPDATE cars SET parked = 'street', parked_id = 0, at_place = ? "
            "WHERE user_id = ? AND code = ?",
            (place, user_id, code),
        )
        await self.conn.commit()
        if cursor.rowcount:
            await self.conn.execute(
                "UPDATE players SET car = '', car_until = 0 "
                "WHERE user_id = ? AND car = ?",
                (user_id, code),
            )
            await self.conn.commit()
        return cursor.rowcount > 0

    async def impound_car(
        self, car_id: int, place: str, now: int, owner_id: int = 0
    ) -> dict | None:
        """Изъять машину на хранение в участок и вернуть её хозяину.

        Хозяином снова становится потерпевший, но машина не у него на
        руках: она лежит в участке, пока он за ней не придёт. Угон при
        этом с неё снимается — дело уже у суда, а не у машины.

        `owner_id` — кому возвращать, если на самой машине отметки
        угона уже нет: у перебитой её стирает новый номер, и хозяина
        тогда называет дело, а не строка.
        """
        async with self.conn.execute(
            "SELECT id, user_id, code, plate, stolen_from FROM cars "
            "WHERE id = ?",
            (car_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        holder = int(row["user_id"])
        owner = owner_id or int(row["stolen_from"]) or holder
        # У хозяина за время угона могла завестись такая же машина:
        # тогда возвращать некуда, и машина просто пропадает из города
        async with self.conn.execute(
            "SELECT 1 FROM cars WHERE user_id = ? AND code = ? AND id <> ?",
            (owner, str(row["code"]), car_id),
        ) as cursor:
            double = await cursor.fetchone() is not None
        if double:
            await self.conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        else:
            await self.conn.execute(
                "UPDATE cars SET user_id = ?, parked = 'impound', "
                "parked_id = 0, at_place = ?, took_at = ?, "
                "stolen_from = 0, stolen_at = 0, stolen_filed = 0 "
                "WHERE id = ?",
                (owner, place, now, car_id),
            )
        await self.conn.execute(
            "UPDATE players SET car = '', car_until = 0 "
            "WHERE user_id = ? AND car = ?",
            (holder, str(row["code"])),
        )
        await self.conn.commit()
        return {
            "id": car_id,
            "code": str(row["code"]),
            "plate": str(row["plate"]),
            "owner": owner,
            "holder": holder,
            "kept": not double,
        }

    async def cars_at(self, place: str, now: int) -> list[dict]:
        """Что стоит у этого здания: брошенное и сданное на хранение."""
        async with self.conn.execute(
            """
            SELECT c.id, c.user_id, c.code, c.plate, c.plate_first,
                   c.restamped, c.parked, c.at_place, c.until,
                   c.stolen_from, c.stolen_filed, p.nickname
            FROM cars c JOIN players p ON p.user_id = c.user_id
            WHERE c.at_place = ? AND c.parked IN ('street', 'impound')
              AND (c.until = 0 OR c.until > ?)
            ORDER BY c.took_at, c.id
            """,
            (place, now),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def street_cars(self, now: int) -> list[dict]:
        """Все брошенные машины города: где стоят и чьи."""
        async with self.conn.execute(
            """
            SELECT c.id, c.user_id, c.code, c.plate, c.plate_first,
                   c.restamped, c.at_place, c.stolen_from, c.stolen_filed,
                   p.nickname
            FROM cars c JOIN players p ON p.user_id = c.user_id
            WHERE c.parked = 'street' AND (c.until = 0 OR c.until > ?)
            ORDER BY c.took_at, c.id
            """,
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ---------- дела об угоне ----------

    async def open_theft(
        self, thief_id: int, owner_id: int, car_id: int, plate: str,
        code: str, now: int,
    ) -> int:
        """Завести дело об угоне. Судит по нему суд, а не участок."""
        cursor = await self.conn.execute(
            """
            INSERT INTO theft_cases
                (thief_id, owner_id, car_id, plate, code, stolen_at)
            VALUES (?,?,?,?,?,?)
            """,
            (thief_id, owner_id, car_id, plate, code, now),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def file_theft_case(self, car_id: int, owner_id: int, now: int) -> bool:
        """Заявление хозяина: с него дело идёт в суд и в розыск."""
        cursor = await self.conn.execute(
            "UPDATE theft_cases SET filed_at = ? "
            "WHERE car_id = ? AND owner_id = ? AND filed_at = 0",
            (now, car_id, owner_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_theft_seized(self, car_id: int, now: int) -> None:
        """Машина изъята: дело ждёт приговора, а не возврата."""
        await self.conn.execute(
            "UPDATE theft_cases SET seized_at = ? "
            "WHERE car_id = ? AND seized_at = 0 AND judged_at = 0",
            (now, car_id),
        )
        await self.conn.commit()

    async def theft_cases(
        self,
        judged: bool | None = None,
        thief_id: int = 0,
        owner_id: int = 0,
        filed: bool | None = True,
    ) -> list[dict]:
        """Дела об угоне: все, ждущие приговора или одного человека.

        `filed` по умолчанию True: суду и розыску дело видно с
        заявления, до него угон — дело двоих. `None` показывает и
        незаявленные — это нужно одному экрану, хозяйскому: он и есть
        то место, где заявление пишут.
        """
        where = []
        args: list = []
        if filed is True:
            where.append("filed_at <> 0")
        elif filed is False:
            where.append("filed_at = 0")
        if judged is True:
            where.append("judged_at <> 0")
        elif judged is False:
            where.append("judged_at = 0")
        if thief_id:
            where.append("thief_id = ?")
            args.append(thief_id)
        if owner_id:
            where.append("owner_id = ?")
            args.append(owner_id)
        if not where:
            where.append("1 = 1")
        async with self.conn.execute(
            "SELECT * FROM theft_cases WHERE " + " AND ".join(where)
            + " ORDER BY stolen_at, id",
            tuple(args),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def theft_case(self, case_id: int) -> dict | None:
        async with self.conn.execute(
            "SELECT * FROM theft_cases WHERE id = ?", (case_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def judge_theft(
        self, case_id: int, judge_id: int, fine: int, hours: int, now: int
    ) -> bool:
        """Вынести приговор. False — по делу его уже выносили."""
        cursor = await self.conn.execute(
            "UPDATE theft_cases SET judged_at = ?, judge_id = ?, fine = ?, "
            "jail_hours = ? WHERE id = ? AND judged_at = 0",
            (now, judge_id, fine, hours, case_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def serve_theft(self, case_id: int, now: int) -> bool:
        """Отметить приговор исполненным: деньги взяты, срок пошёл."""
        cursor = await self.conn.execute(
            "UPDATE theft_cases SET served_at = ? "
            "WHERE id = ? AND judged_at <> 0 AND served_at = 0",
            (now, case_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def theft_count(self, thief_id: int, before: int) -> int:
        """Который это угон по счёту: по нему и считается наказание."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS total FROM theft_cases "
            "WHERE thief_id = ? AND judged_at <> 0 AND judged_at <= ?",
            (thief_id, before),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["total"] if row else 0)

    # ---------- угон ----------

    async def steal_car(self, car_id: int, thief_id: int, now: int) -> dict | None:
        """Увести машину: хозяин меняется, номер и розыск остаются.

        Одним запросом и с условием в нём же: двое, нажавшие угон в
        одну секунду, не уведут одну машину дважды. Машина при этом
        слетает со стоянки и из-под прежнего хозяина — за рулём
        угнанной он больше не едет.
        """
        async with self.conn.execute(
            "SELECT id, user_id, code, plate, stolen_from FROM cars "
            "WHERE id = ?",
            (car_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        owner = int(row["user_id"])
        if owner == thief_id:
            return None
        # Та же машина у угонщика уже есть — второй такой не бывает
        async with self.conn.execute(
            "SELECT 1 FROM cars WHERE user_id = ? AND code = ?",
            (thief_id, str(row["code"])),
        ) as cursor:
            if await cursor.fetchone() is not None:
                return None
        cursor = await self.conn.execute(
            "UPDATE cars SET user_id = ?, parked = '', parked_id = 0, "
            "at_place = '', took_at = ?, stolen_from = ?, stolen_at = ?, "
            "stolen_filed = 0 "
            "WHERE id = ? AND user_id = ?",
            (thief_id, now, int(row["stolen_from"]) or owner, now, car_id, owner),
        )
        await self.conn.commit()
        if not cursor.rowcount:
            return None
        # Хозяин больше не за рулём: угнанную машину из-под него увели
        await self.conn.execute(
            "UPDATE players SET car = '', car_until = 0 "
            "WHERE user_id = ? AND car = ?",
            (owner, str(row["code"])),
        )
        await self.conn.commit()
        return {
            "id": car_id,
            "code": str(row["code"]),
            "plate": str(row["plate"]),
            "owner": owner,
        }

    async def clear_theft(self, car_id: int) -> None:
        """Снять с машины её прошлое: перебитый номер стирает розыск."""
        await self.conn.execute(
            "UPDATE cars SET stolen_from = 0, stolen_at = 0, stolen_filed = 0 "
            "WHERE id = ?",
            (car_id,),
        )
        await self.conn.commit()

    async def file_stolen(self, car_id: int, now: int) -> bool:
        """Поставить на машину отметку розыска.

        Отметку ставят по делу, а не по флажку на самой машине:
        перебитый номер флажок стирает, а дело — нет. Кто вправе
        заявить, решает `file_theft`, и он же сюда и ходит.
        """
        cursor = await self.conn.execute(
            "UPDATE cars SET stolen_filed = ? WHERE id = ? AND stolen_filed = 0",
            (now, car_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def stolen_from(self, user_id: int, now: int) -> list[dict]:
        """Что у бойца угнали и числится ли оно в розыске."""
        async with self.conn.execute(
            """
            SELECT c.id, c.code, c.plate, c.stolen_at, c.stolen_filed,
                   c.user_id, p.nickname
            FROM cars c JOIN players p ON p.user_id = c.user_id
            WHERE c.stolen_from = ? AND (c.until = 0 OR c.until > ?)
            ORDER BY c.stolen_at DESC
            """,
            (user_id, now),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def put_car(
        self, user_id: int, code: str, seconds: int, now: int
    ) -> int:
        """Записать машину на бойца. Отдаёт её час возврата.

        `seconds` — на сколько берут; ноль значит «насовсем», то есть
        куплена. Та же машина второй раз — продлеваем: живая аренда
        продлевается со своего конца, кончившаяся — с этого часа. Своя
        насовсем срока больше не получает ни при каком продлении.
        """
        row = await self.car_row(user_id, code)
        until = 0 if seconds <= 0 else now + seconds
        if row is not None and (not row["until"] or not seconds):
            until = 0
        elif row is not None:
            until = max(int(row["until"]), now) + seconds
        if row is None:
            plate = await self._fresh_plate()
            await self.conn.execute(
                "INSERT INTO cars "
                "(user_id, code, until, took_at, plate, plate_first) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, code, until, now, plate, plate),
            )
        else:
            await self.conn.execute(
                "UPDATE cars SET until = ? WHERE id = ?", (until, row["id"])
            )
        await self.conn.commit()
        return until

    async def set_driven(self, user_id: int, code: str, driven: int) -> None:
        """Записать пробег машины с последней зарядки."""
        await self.conn.execute(
            "UPDATE cars SET driven = ? WHERE user_id = ? AND code = ?",
            (max(0, driven), user_id, code),
        )
        await self.conn.commit()

    # ---------- зарядка на АЗС ----------

    async def charging_rows(self) -> list[dict]:
        """Все занятые колонки, по порядку слотов."""
        async with self.conn.execute(
            "SELECT slot, user_id, code, percent, ready_at, started_at "
            "FROM charging ORDER BY slot"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def charging_row(self, slot: int) -> dict | None:
        async with self.conn.execute(
            "SELECT slot, user_id, code, percent, ready_at, started_at "
            "FROM charging WHERE slot = ?",
            (slot,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def plug_in(
        self, slot: int, user_id: int, code: str, percent: int,
        ready_at: int, now: int,
    ) -> bool:
        """Занять колонку. False — её заняли первой."""
        try:
            await self.conn.execute(
                "INSERT INTO charging "
                "(slot, user_id, code, percent, ready_at, started_at) "
                "VALUES (?,?,?,?,?,?)",
                (slot, user_id, code, percent, ready_at, now),
            )
        except sqlite3.IntegrityError:
            return False
        await self.conn.commit()
        return True

    async def unplug(self, slot: int) -> bool:
        """Освободить колонку. False — она уже пуста."""
        cursor = await self.conn.execute(
            "DELETE FROM charging WHERE slot = ?", (slot,)
        )
        await self.conn.commit()
        return bool(cursor.rowcount)

    async def drop_car(self, user_id: int, code: str) -> bool:
        """Снять машину с бойца: сдал в гараж или кончилась аренда."""
        cursor = await self.conn.execute(
            "DELETE FROM cars WHERE user_id = ? AND code = ?", (user_id, code)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def car_holders(self, code: str, now: int) -> list[dict]:
        """У кого на руках эта машина. Кончившиеся сроки не считаются."""
        async with self.conn.execute(
            """
            SELECT user_id, until, took_at FROM cars
            WHERE code = ? AND (until = 0 OR until > ?)
            ORDER BY took_at, user_id
            """,
            (code, now),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def park_car(
        self, user_id: int, code: str, parked: str, parked_id: int, limit: int
    ) -> bool:
        """Поставить машину на парковку. False — мест нет или машины нет.

        Счёт занятых мест идёт в самом запросе, вместе с постановкой:
        между «посмотрели, сколько свободно» и «поставили» успевает
        проехать чужая машина, и ловить это питоном значило бы ловить с
        опозданием.
        """
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM cars WHERE parked = ? AND parked_id = ?",
            (parked, parked_id),
        ) as cursor:
            row = await cursor.fetchone()
        if int(row["n"]) >= limit:
            return False
        cursor = await self.conn.execute(
            "UPDATE cars SET parked = ?, parked_id = ? "
            "WHERE user_id = ? AND code = ? AND parked = ''",
            (parked, parked_id, user_id, code),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def unpark_car(self, user_id: int, code: str) -> bool:
        """Забрать машину с парковки. False — её там нет."""
        cursor = await self.conn.execute(
            "UPDATE cars SET parked = '', parked_id = 0, at_place = '' "
            "WHERE user_id = ? AND code = ? AND parked <> ''",
            (user_id, code),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def parked_cars(self, parked: str, parked_id: int, now: int) -> list[dict]:
        """Что стоит на этой парковке. Кончившиеся аренды не считаются.

        Номер строки отдаём наружу: на одной парковке могут стоять два
        одинаковых хэтчбека разных хозяев, и «забери хэтчбек» тогда
        ничего не означает.
        """
        async with self.conn.execute(
            """
            SELECT id, user_id, code, until, took_at, driven FROM cars
            WHERE parked = ? AND parked_id = ? AND (until = 0 OR until > ?)
            ORDER BY took_at, id
            """,
            (parked, parked_id, now),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def parked_car(
        self, car_id: int, parked: str, parked_id: int
    ) -> dict | None:
        """Одна машина с парковки по её строке. None — её там нет."""
        async with self.conn.execute(
            "SELECT id, user_id, code, until FROM cars "
            "WHERE id = ? AND parked = ? AND parked_id = ?",
            (car_id, parked, parked_id),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def take_parked_car(
        self, car_id: int, parked: str, parked_id: int, user_id: int
    ) -> dict | None:
        """Забрать машину с общей парковки себе. None — её там уже нет.

        Хозяина переписываем у той же строки: ни машина, ни срок аренды
        от смены ключей не меняются. Двух одинаковых моделей на одного
        бойца база не держит (`UNIQUE (user_id, code)`) — тогда
        возвращаем отказ, а не роняем запрос.
        """
        row = await self.parked_car(car_id, parked, parked_id)
        if row is None:
            return None
        code = str(row["code"])
        was = int(row["user_id"])
        if was != user_id and await self.car_row(user_id, code) is not None:
            return {"taken": False, "code": code, "owner": was}
        await self.conn.execute(
            "UPDATE cars SET user_id = ?, parked = '', parked_id = 0 "
            "WHERE id = ?",
            (user_id, car_id),
        )
        await self.conn.commit()
        return {"taken": True, "code": code, "owner": was}

    async def empty_parking(self, parked: str, parked_id: int) -> int:
        """Выгнать с парковки всё: семьи больше нет или съём кончился.

        Машины не пропадают — они возвращаются к хозяину: парковка это
        место, а не владение, и терять её вместе с квартирой было бы
        всё равно что терять вещи из кладовой.
        """
        cursor = await self.conn.execute(
            "UPDATE cars SET parked = '', parked_id = 0 "
            "WHERE parked = ? AND parked_id = ?",
            (parked, parked_id),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def drive_home(self, user_id: int, parked: str) -> int:
        """Вернуть бойцу его машины с этой парковки: он на ней больше не свой."""
        cursor = await self.conn.execute(
            "UPDATE cars SET parked = '', parked_id = 0 "
            "WHERE user_id = ? AND parked = ?",
            (user_id, parked),
        )
        await self.conn.commit()
        return cursor.rowcount

    # ---------- школа боевых искусств ----------

    async def add_deeds(self, user_id: int, counts: dict[str, int]) -> None:
        """Дописать боевые дела в ведомость бойца."""
        rows = [
            (user_id, deed, int(count))
            for deed, count in counts.items()
            if int(count) > 0
        ]
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO deeds (user_id, deed, count) VALUES (?,?,?) "
            "ON CONFLICT(user_id, deed) DO UPDATE SET count = count + ?",
            [(one[0], one[1], one[2], one[2]) for one in rows],
        )
        await self.conn.commit()

    async def deed_counts(self, user_id: int) -> dict[str, int]:
        """Вся ведомость бойца: дело — сколько сделано."""
        async with self.conn.execute(
            "SELECT deed, count FROM deeds WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["deed"]): int(row["count"]) for row in rows}

    async def dojo_rows(self, user_id: int) -> list[dict]:
        """На что боец записан в школе, по порядку записи."""
        async with self.conn.execute(
            "SELECT user_id, code, deed, baseline, started_at FROM dojo "
            "WHERE user_id = ? ORDER BY started_at, code",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def dojo_row(self, user_id: int, code: str) -> dict | None:
        """Одна запись бойца. None — на этот приём он не записывался."""
        async with self.conn.execute(
            "SELECT user_id, code, deed, baseline, started_at FROM dojo "
            "WHERE user_id = ? AND code = ?",
            (user_id, code),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_dojo(
        self, user_id: int, code: str, deed: str, baseline: int, now: int
    ) -> None:
        """Записать бойца на приём. Та же запись перезаводится заново."""
        await self.conn.execute(
            "INSERT INTO dojo (user_id, code, deed, baseline, started_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, code) DO UPDATE SET "
            "deed = excluded.deed, baseline = excluded.baseline, "
            "started_at = excluded.started_at",
            (user_id, code, deed, max(0, baseline), now),
        )
        await self.conn.commit()

    async def clear_dojo(self, user_id: int, code: str) -> bool:
        """Снять запись: выучил или бросил. False — записи и не было."""
        cursor = await self.conn.execute(
            "DELETE FROM dojo WHERE user_id = ? AND code = ?",
            (user_id, code),
        )
        await self.conn.commit()
        return bool(cursor.rowcount)

    async def sweep_cars(self, now: int) -> int:
        """Убрать машины, у которых кончился срок. Отдаёт, сколько убрал."""
        cursor = await self.conn.execute(
            "DELETE FROM cars WHERE until > 0 AND until <= ?", (now,)
        )
        await self.conn.commit()
        return cursor.rowcount

    # ---------- квесты ----------

    @staticmethod
    def _quest(row) -> QuestLog:
        return QuestLog(
            code=str(row["code"]),
            state=str(row["state"]),
            marks=frozenset(
                one for one in str(row["marks"] or "").split(",") if one
            ),
            taken_at=int(row["taken_at"]),
            done_at=int(row["done_at"]),
        )

    async def quests_of(self, user_id: int) -> list[QuestLog]:
        """Все записи по заданиям бойца: и взятые, и сданные."""
        async with self.conn.execute(
            "SELECT * FROM quests WHERE user_id = ? ORDER BY taken_at, rowid",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._quest(row) for row in rows]

    async def take_quest(self, user_id: int, code: str, now: int) -> None:
        """Записать взятое задание. Второй раз то же не берут."""
        await self.conn.execute(
            """
            INSERT INTO quests (user_id, code, state, marks, taken_at)
            VALUES (?,?,'taken','',?)
            ON CONFLICT(user_id, code) DO NOTHING
            """,
            (user_id, code, now),
        )
        await self.conn.commit()
        logger.info("Боец %s взял задание %s", user_id, code)

    async def mark_quest(self, user_id: int, code: str, mark: str) -> bool:
        """Поставить отметку города. False — задания на руках нет.

        Отметки складываются в одну строку, и дважды одна и та же в неё
        не ложится: условие на `instr` дешевле, чем читать и писать
        строку двумя запросами.
        """
        cursor = await self.conn.execute(
            """
            UPDATE quests
               SET marks = CASE WHEN marks = '' THEN ?
                                ELSE marks || ',' || ? END
             WHERE user_id = ? AND code = ? AND state = 'taken'
               AND instr(',' || marks || ',', ',' || ? || ',') = 0
            """,
            (mark, mark, user_id, code, mark),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def finish_quest(self, user_id: int, code: str, now: int) -> bool:
        """Закрыть задание. False — его уже сдали или не брали."""
        cursor = await self.conn.execute(
            "UPDATE quests SET state = 'done', done_at = ? "
            "WHERE user_id = ? AND code = ? AND state = 'taken'",
            (now, user_id, code),
        )
        await self.conn.commit()
        if cursor.rowcount > 0:
            logger.info("Боец %s сдал задание %s", user_id, code)
        return cursor.rowcount > 0

    # ---------- выписка по карте ----------

    async def log_card(
        self, user_id: int, amount: int, kind: str, note: str,
        balance: int, now: int | None = None, about: str = "",
    ) -> None:
        """Записать одну операцию по счёту."""
        await self.conn.execute(
            """
            INSERT INTO card_log (user_id, at, amount, kind, note, balance, about)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                user_id, now_ts() if now is None else now, amount, kind,
                note, balance, about,
            ),
        )
        await self.conn.commit()

    async def flush_card_moves(self, player) -> None:
        """Сложить в базу выписку, которую боец принёс с собой.

        Зовётся из `save_player`: платят в лавке, а пишут здесь — иначе
        каждая лавка ходила бы в базу сама и половина забывала бы.
        """
        moves = list(player.card_moves)
        if not moves:
            return
        await self.conn.executemany(
            """
            INSERT INTO card_log (user_id, at, amount, kind, note, balance, about)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                (
                    player.user_id, one.at, one.amount, one.kind, one.note,
                    one.balance, one.about,
                )
                for one in moves
            ],
        )
        await self.conn.commit()
        player.card_moves.clear()

    async def card_log(
        self, user_id: int, since: int = 0, until: int = 0, limit: int = 200
    ) -> list[CardMove]:
        """Выписка за срок, новые операции первыми.

        `since` и `until` — границы в секундах; нули означают «с начала»
        и «по сей час». Потолок в двести строк — не экономия, а длина
        экрана: дальше его всё равно никто не листает, а за точными
        числами есть границы срока.
        """
        where = ["user_id = ?"]
        args: list[int] = [user_id]
        if since:
            where.append("at >= ?")
            args.append(since)
        if until:
            where.append("at <= ?")
            args.append(until)
        args.append(limit)
        async with self.conn.execute(
            f"""
            SELECT at, amount, kind, note, balance, about FROM card_log
            WHERE {" AND ".join(where)}
            ORDER BY at DESC, id DESC LIMIT ?
            """,
            tuple(args),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            CardMove(
                at=int(row["at"]),
                amount=int(row["amount"]),
                kind=str(row["kind"]),
                note=str(row["note"]),
                balance=int(row["balance"]),
                about=str(row["about"] or ""),
            )
            for row in rows
        ]

    async def card_turnover(
        self, user_id: int, since: int = 0, until: int = 0
    ) -> tuple[int, int]:
        """Приход и расход за срок: два числа под выпиской."""
        where = ["user_id = ?"]
        args: list[int] = [user_id]
        if since:
            where.append("at >= ?")
            args.append(since)
        if until:
            where.append("at <= ?")
            args.append(until)
        async with self.conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS income,
                COALESCE(SUM(CASE WHEN amount < 0 THEN -amount END), 0) AS spent
            FROM card_log WHERE {" AND ".join(where)}
            """,
            tuple(args),
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row["income"]), int(row["spent"])) if row else (0, 0)

    # ---------- почта: посылки между бойцами ----------

    @staticmethod
    def _parcel(row) -> Parcel:
        return Parcel(
            id=int(row["id"]),
            from_id=int(row["from_id"]),
            to_id=int(row["to_id"]),
            code=str(row["code"]),
            wear=int(row["wear"]),
            max_wear=int(row["max_wear"]),
            mod=str(row["mod"]),
            mod_value=int(row["mod_value"]),
            price=int(row["price"]),
            sent_at=int(row["sent_at"]),
            keep_until=int(row["keep_until"]),
            back=bool(row["back"]),
            from_name=str(row["from_name"] or "") if "from_name" in row.keys() else "",
            to_name=str(row["to_name"] or "") if "to_name" in row.keys() else "",
        )

    _PARCEL_COLUMNS = """
        p.*,
        (SELECT nickname FROM players WHERE user_id = p.from_id) AS from_name,
        (SELECT nickname FROM players WHERE user_id = p.to_id) AS to_name
    """

    async def parcel(self, parcel_id: int) -> Parcel | None:
        """Одна посылка по номеру. None — её уже выдали."""
        async with self.conn.execute(
            f"SELECT {self._PARCEL_COLUMNS} FROM parcels p WHERE p.id = ?",
            (parcel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._parcel(row) if row else None

    async def parcels_for(self, user_id: int) -> list[Parcel]:
        """Что ждёт бойца на почте: присланное ему и вернувшееся к нему."""
        async with self.conn.execute(
            f"""
            SELECT {self._PARCEL_COLUMNS} FROM parcels p
            WHERE (p.back = 0 AND p.to_id = ?) OR (p.back = 1 AND p.from_id = ?)
            ORDER BY p.sent_at, p.id
            """,
            (user_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._parcel(row) for row in rows]

    async def parcels_from(self, user_id: int) -> list[Parcel]:
        """Что боец отправил и чего ещё не забрали."""
        async with self.conn.execute(
            f"""
            SELECT {self._PARCEL_COLUMNS} FROM parcels p
            WHERE p.from_id = ? AND p.back = 0 AND p.to_id <> ?
            ORDER BY p.sent_at, p.id
            """,
            (user_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._parcel(row) for row in rows]

    async def waiting_count(self, user_id: int) -> int:
        """Сколько посылок уже ждёт этого бойца."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM parcels WHERE to_id = ? AND back = 0",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def post_gear(
        self, item_id: int, user_id: int, to_id: int, price: int,
        keep_until: int, now: int,
    ) -> Parcel | None:
        """Отправить вещь из рюкзака посылкой. None — вещи нет или надета.

        Вещь не копируется: сперва её забирают из инвентаря и только
        потом кладут на полку почты.
        """
        async with self.conn.execute(
            """
            SELECT code, wear, max_wear, mod, mod_value FROM inventory
            WHERE id = ? AND user_id = ? AND slot IS NULL
            """,
            (item_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        cursor = await self.conn.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ? AND slot IS NULL",
            (item_id, user_id),
        )
        if cursor.rowcount <= 0:  # pragma: no cover - вещь увели между запросами
            await self.conn.commit()
            return None
        cursor = await self.conn.execute(
            """
            INSERT INTO parcels
                (from_id, to_id, code, wear, max_wear, mod, mod_value,
                 price, sent_at, keep_until, back)
            VALUES (?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                user_id, to_id, str(row["code"]), int(row["wear"]),
                int(row["max_wear"]), str(row["mod"]), int(row["mod_value"]),
                price, now, keep_until,
            ),
        )
        await self.conn.commit()
        logger.info("Боец %s отправил посылку бойцу %s", user_id, to_id)
        return Parcel(
            id=int(cursor.lastrowid or 0),
            from_id=user_id,
            to_id=to_id,
            code=str(row["code"]),
            wear=int(row["wear"]),
            max_wear=int(row["max_wear"]),
            mod=str(row["mod"]),
            mod_value=int(row["mod_value"]),
            price=price,
            sent_at=now,
            keep_until=keep_until,
        )

    async def hand_parcel(self, parcel_id: int, user_id: int) -> str:
        """Выдать посылку этому бойцу: вещь уходит к нему в рюкзак.

        Возвращает код вещи. Пусто — посылку уже выдали: между взглядом
        на полку и нажатием кнопки проходит время.
        """
        async with self.conn.execute(
            "SELECT code, wear, max_wear, mod, mod_value FROM parcels WHERE id = ?",
            (parcel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return ""
        cursor = await self.conn.execute(
            "DELETE FROM parcels WHERE id = ?", (parcel_id,)
        )
        if cursor.rowcount <= 0:  # pragma: no cover - посылку выдали дважды
            await self.conn.commit()
            return ""
        await self.conn.execute(
            """
            INSERT INTO inventory (user_id, code, wear, max_wear, mod, mod_value)
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id, str(row["code"]), int(row["wear"]), int(row["max_wear"]),
                str(row["mod"]), int(row["mod_value"]),
            ),
        )
        await self.conn.commit()
        return str(row["code"])

    async def return_parcel(self, parcel_id: int) -> None:
        """Отправить посылку обратно: теперь её ждёт отправитель.

        Наложенный платёж с возврата снимаем — за дорогу уже заплачено, и
        брать с отправителя его же цену было бы разбоем. Срока у
        возврата нет: это последняя полка, с которой вещь забирают.
        """
        await self.conn.execute(
            "UPDATE parcels SET back = 1, price = 0, keep_until = 0 WHERE id = ?",
            (parcel_id,),
        )
        await self.conn.commit()

    async def overdue_parcels(self, now: int) -> list[Parcel]:
        """Посылки, чей срок хранения вышел."""
        async with self.conn.execute(
            f"""
            SELECT {self._PARCEL_COLUMNS} FROM parcels p
            WHERE p.back = 0 AND p.keep_until > 0 AND p.keep_until <= ?
            ORDER BY p.id
            """,
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._parcel(row) for row in rows]

    async def pay_cash(self, user_id: int, amount: int) -> None:
        """Дописать бойцу наличные, не собирая его целиком.

        Нужно там, где деньги получает тот, кого сейчас нет в сети:
        продавец на почте, хозяин съехавшей квартиры.
        """
        await self.conn.execute(
            "UPDATE players SET credits = credits + ? WHERE user_id = ?",
            (amount, user_id),
        )
        await self.conn.commit()

    # ---------- заявки и приглашения ----------

    async def add_petition(
        self,
        clan_id: int,
        user_id: int,
        kind: Petition,
        by_id: int = 0,
        now: int | None = None,
    ) -> None:
        """Записать заявку или приглашение. Повторное — поверх прежнего."""
        moment = now_ts() if now is None else now
        await self.conn.execute(
            """
            INSERT INTO clan_petitions (clan_id, user_id, kind, created_at, by_id)
            VALUES (?,?,?,?,?)
            ON CONFLICT(clan_id, user_id) DO UPDATE SET
                kind       = excluded.kind,
                created_at = excluded.created_at,
                by_id      = excluded.by_id
            """,
            (clan_id, user_id, kind.value, moment, by_id),
        )
        await self.conn.commit()

    async def petition(self, clan_id: int, user_id: int) -> Knock | None:
        async with self.conn.execute(
            """
            SELECT clan_id, user_id, kind, created_at, by_id
            FROM clan_petitions WHERE clan_id = ? AND user_id = ?
            """,
            (clan_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Knock(
            clan_id=int(row["clan_id"]),
            user_id=int(row["user_id"]),
            kind=Petition(str(row["kind"])),
            created_at=int(row["created_at"]),
            by_id=int(row["by_id"]),
        )

    async def drop_petition(self, clan_id: int, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM clan_petitions WHERE clan_id = ? AND user_id = ?",
            (clan_id, user_id),
        )
        await self.conn.commit()

    async def petitions_of_clan(self, clan_id: int, kind: Petition) -> list[Knock]:
        """Что лежит у семьи: заявки к ней или её же приглашения."""
        async with self.conn.execute(
            """
            SELECT k.clan_id, k.user_id, k.kind, k.created_at, k.by_id,
                   p.nickname, p.level, p.class_code, p.pro_until
            FROM clan_petitions k JOIN players p ON p.user_id = k.user_id
            WHERE k.clan_id = ? AND k.kind = ?
            ORDER BY k.created_at ASC
            """,
            (clan_id, kind.value),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Knock(
                clan_id=int(row["clan_id"]),
                user_id=int(row["user_id"]),
                kind=Petition(str(row["kind"])),
                created_at=int(row["created_at"]),
                by_id=int(row["by_id"]),
                nickname=str(row["nickname"]),
                level=int(row["level"]),
                class_code=str(row["class_code"]),
                pro_until=int(row["pro_until"]),
            )
            for row in rows
        ]

    async def petitions_of_user(self, user_id: int, kind: Petition) -> list[Knock]:
        """Что лежит у бойца: его заявки или зовущие его приглашения."""
        async with self.conn.execute(
            """
            SELECT k.clan_id, k.user_id, k.kind, k.created_at, k.by_id,
                   c.name AS clan_name, c.sign,
                   (SELECT nickname FROM players WHERE user_id = k.by_id)
                       AS by_name
            FROM clan_petitions k JOIN clans c ON c.id = k.clan_id
            WHERE k.user_id = ? AND k.kind = ?
            ORDER BY k.created_at ASC
            """,
            (user_id, kind.value),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Knock(
                clan_id=int(row["clan_id"]),
                user_id=int(row["user_id"]),
                kind=Petition(str(row["kind"])),
                created_at=int(row["created_at"]),
                by_id=int(row["by_id"]),
                clan_name=str(row["clan_name"]),
                clan_sign=str(row["sign"]),
                by_name=str(row["by_name"] or ""),
            )
            for row in rows
        ]

    async def drop_petitions_of(self, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM clan_petitions WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

    # ---------- документы: автошкола и права ----------

    async def school_of(self, user_id: int) -> School:
        """Что у бойца с автошколой. Пустая запись — он туда не ходил."""
        async with self.conn.execute(
            """
            SELECT course_at, exam_until, step, wrong, passed_at,
                   licence_at, tried_at
            FROM driving WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return School()
        return School(
            course_at=int(row["course_at"]),
            exam_until=int(row["exam_until"]),
            step=int(row["step"]),
            wrong=int(row["wrong"]),
            passed_at=int(row["passed_at"]),
            licence_at=int(row["licence_at"]),
            tried_at=int(row["tried_at"]),
        )

    async def set_school(self, user_id: int, school: School) -> None:
        """Записать состояние автошколы: запись, попытку или выданные права."""
        await self.conn.execute(
            """
            INSERT INTO driving (
                user_id, course_at, exam_until, step, wrong, passed_at,
                licence_at, tried_at
            )
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                course_at  = excluded.course_at,
                exam_until = excluded.exam_until,
                step       = excluded.step,
                wrong      = excluded.wrong,
                passed_at  = excluded.passed_at,
                licence_at = excluded.licence_at,
                tried_at   = excluded.tried_at
            """,
            (
                user_id,
                school.course_at,
                school.exam_until,
                school.step,
                school.wrong,
                school.passed_at,
                school.licence_at,
                school.tried_at,
            ),
        )
        await self.conn.commit()

    async def set_policy(self, user_id: int, policy: Policy) -> None:
        """Записать полис. Продление ложится поверх прежнего."""
        await self.conn.execute(
            """
            INSERT INTO policies (user_id, issued, until, auto_renew)
            VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                issued = excluded.issued,
                until = excluded.until,
                auto_renew = excluded.auto_renew
            """,
            (user_id, policy.issued, policy.until, int(policy.auto_renew)),
        )
        await self.conn.commit()

    async def set_policy_renew(self, user_id: int, auto_renew: bool) -> bool:
        """Включить или выключить автопродление. False — полиса нет."""
        cursor = await self.conn.execute(
            "UPDATE policies SET auto_renew = ? WHERE user_id = ?",
            (int(auto_renew), user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def drop_policy(self, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM policies WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

    async def set_injury(self, user_id: int, code: str, until: int) -> None:
        """Записать травму. Новая ложится поверх прежней."""
        await self.conn.execute(
            """
            INSERT INTO injuries (user_id, code, until) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                code = excluded.code, until = excluded.until
            """,
            (user_id, code, until),
        )
        await self.conn.commit()

    async def drop_injury(self, user_id: int) -> None:
        """Снять травму досрочно: так её и лечат."""
        await self.conn.execute(
            "DELETE FROM injuries WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

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

    async def set_announce_thread(
        self, chat_id: int, kind: str, thread_id: int | None, title: str = ""
    ) -> None:
        """Отметить ветку, куда бот приносит объявления этого вида."""
        await self.conn.execute(
            """
            INSERT INTO announce_threads (chat_id, kind, thread_id, title)
            VALUES (?,?,?,?)
            ON CONFLICT(chat_id, kind) DO UPDATE SET
                thread_id = excluded.thread_id,
                title     = excluded.title
            """,
            (chat_id, kind, thread_id, title),
        )
        await self.conn.commit()

    async def drop_announce_thread(self, chat_id: int, kind: str) -> None:
        await self.conn.execute(
            "DELETE FROM announce_threads WHERE chat_id = ? AND kind = ?",
            (chat_id, kind),
        )
        await self.conn.commit()

    async def announce_threads(self, kind: str) -> list[tuple[int, int | None]]:
        """Все ветки этого вида: чат и ветка в нём.

        Клубов может быть несколько, и вызов из мини-аппа принимает кто
        угодно, — поэтому объявление идёт во все размеченные ветки, а не в
        одну «родную» группу.
        """
        async with self.conn.execute(
            "SELECT chat_id, thread_id FROM announce_threads WHERE kind = ?",
            (kind,),
        ) as cursor:
            return [(row["chat_id"], row["thread_id"]) for row in await cursor.fetchall()]

    async def announce_threads_of(self, chat_id: int) -> dict[str, int | None]:
        """Что размечено в этом чате: вид события — ветка."""
        async with self.conn.execute(
            "SELECT kind, thread_id FROM announce_threads WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            return {row["kind"]: row["thread_id"] for row in await cursor.fetchall()}

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

    async def recent_duel_logs(
        self, user_id: int, limit: int = 10
    ) -> list[list[dict[str, Any]]]:
        """Разборы последних боёв бойца — по списку ходов на бой.

        Одним запросом, а не боем за раз: аналитик поднимает их перед
        каждым боем, и десять отдельных походов в базу на это жалко.
        Бои без лога (шли до того, как их начали писать) просто не придут.
        """
        async with self.conn.execute(
            """
            SELECT log.duel_id, log.strikes
            FROM duel_log AS log
            JOIN (
                SELECT d.id FROM duel_sides AS side
                JOIN duels AS d ON d.id = side.duel_id
                WHERE side.user_id = ?
                ORDER BY d.id DESC LIMIT ?
            ) AS recent ON recent.id = log.duel_id
            ORDER BY log.duel_id DESC, log.number
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        fights: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            fights.setdefault(row["duel_id"], []).append(json.loads(row["strikes"]))
        return list(fights.values())

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

    # ---------- авторынок ----------

    async def add_car_lot(self, seller_id: int, code: str, price: int) -> int:
        """Выставить машину на рынок. Отдаёт номер лота."""
        cursor = await self.conn.execute(
            "INSERT INTO car_market (seller_id, code, price) VALUES (?,?,?)",
            (seller_id, code, price),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def car_lots(self) -> list[dict[str, Any]]:
        """Всё, что стоит на рынке, вместе с прозвищем продавца."""
        async with self.conn.execute(
            """
            SELECT m.id, m.seller_id, m.code, m.price, m.listed_at,
                   p.nickname AS seller
            FROM car_market AS m
            LEFT JOIN players AS p ON p.user_id = m.seller_id
            ORDER BY m.id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def car_lot(self, lot_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            """
            SELECT m.id, m.seller_id, m.code, m.price, m.listed_at,
                   p.nickname AS seller
            FROM car_market AS m
            LEFT JOIN players AS p ON p.user_id = m.seller_id
            WHERE m.id = ?
            """,
            (lot_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def take_car_lot(self, lot_id: int) -> bool:
        """Снять лот с рынка. False — его уже забрали.

        Это же и защита от двойной продажи: машину забирает тот, чей
        DELETE прошёл первым, а деньги считаются уже после.
        """
        cursor = await self.conn.execute(
            "DELETE FROM car_market WHERE id = ?", (lot_id,)
        )
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

    async def mark_seen(self, user_id: int, moment: int) -> None:
        """Отметить, что боец только что был в клубе.

        Отдельным запросом, а не через save_player: тот пишет бойца
        целиком и затёр бы то, что успело поменяться рядом.
        """
        await self.conn.execute(
            "UPDATE players SET seen_at = ? WHERE user_id = ?", (moment, user_id)
        )
        await self.conn.commit()

    async def raid_window(
        self, user_id: int, opened_at: int, raid: str = "cellar"
    ) -> dict[str, Any] | None:
        """Что у бойца в этом окне: тратил ли пропуск и победил ли уже."""
        async with self.conn.execute(
            "SELECT * FROM raid_window "
            "WHERE user_id = ? AND raid = ? AND opened_at = ?",
            (user_id, raid, opened_at),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def start_raid_window(
        self, user_id: int, opened_at: int, raid: str = "cellar"
    ) -> bool:
        """Открыть бойцу это окно. True — открыли впервые, пропуск нужен.

        Вставка атомарна: если строка уже была, ничего не меняется и мы
        отвечаем False. Так повторный заход в то же окно не спишет второй
        пропуск, даже если два нажатия придут разом.
        """
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO raid_window (user_id, raid, opened_at) "
            "VALUES (?, ?, ?)",
            (user_id, raid, opened_at),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def drop_raid_window(
        self, user_id: int, opened_at: int, raid: str = "cellar"
    ) -> None:
        """Отпустить окно: вход не состоялся, пропуск не списан."""
        await self.conn.execute(
            "DELETE FROM raid_window WHERE user_id = ? AND raid = ? "
            "AND opened_at = ? AND won = 0",
            (user_id, raid, opened_at),
        )
        await self.conn.commit()

    async def close_raid_window(
        self, user_id: int, opened_at: int, raid: str = "cellar"
    ) -> None:
        """Отметить победу: в это окно боец в этот рейд больше не пойдёт."""
        await self.conn.execute(
            "INSERT INTO raid_window (user_id, raid, opened_at, won) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(user_id, raid, opened_at) DO UPDATE SET won = 1",
            (user_id, raid, opened_at),
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


@dataclass(frozen=True)
class DormRoom:
    """Комната общежития: чья, какая и под какой курс выдана.

    Срока в сутках у неё нет — срок это сама учёба. `closed_at` ноль,
    пока студент учится; поставленный час значит, что комната заперта и
    вещи в ней ждут хозяина у администрации.
    """

    user_id: int
    number: int
    course: str = ""
    given_at: int = 0
    closed_at: int = 0
    vault: int = 0

    @property
    def closed(self) -> bool:
        return self.closed_at > 0

    @property
    def title(self) -> str:
        from bot.game.dorm import room_title

        return room_title(self.number)


@dataclass(frozen=True)
class TurfRow:
    """Строка таблицы точек: чья она и с какого часа."""

    code: str
    owner: Owner
    taken_at: int = 0
    paid_until: int = 0
    # С какого часа точка стоит без охраны. Ноль — на ней кто-то есть
    empty_since: int = 0
    # До какого часа владение этой точкой записано в бонусный журнал
    counted_at: int = 0


def _to_player(row: Iterable[Any]) -> Player:
    data = dict(row)  # type: ignore[arg-type]
    # Дипломы лежат строкой через запятую, а у бойца это список: семь
    # слов не стоят своей таблицы, но и строкой по коду ходить незачем
    data["diplomas"] = [
        one for one in str(data.get("diplomas") or "").split(",") if one
    ]
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
    keys = row.keys()
    return OwnedItem(
        item=item,
        id=int(row["id"]),
        wear=int(row["wear"]),
        max_wear=int(row["max_wear"]),
        slot=slot,
        # Вещи из баз, заведённых до мастерской, приходят без этих колонок
        mod=str(row["mod"]) if "mod" in keys and row["mod"] else "",
        mod_value=int(row["mod_value"]) if "mod_value" in keys else 0,
    )
