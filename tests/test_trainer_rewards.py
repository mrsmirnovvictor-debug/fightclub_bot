"""Что остаётся после урока: опыт по счёту суток и запись в рейды.

Урок у Тренера не идёт ни в победы, ни в ELO, ни в недельный рейтинг.
Поэтому здесь проверяется ровно то, что должно меняться, — и отдельно
то, что меняться не должно: опыт, рейды и квест бармена против побед,
рейтинга и журнала ELO.
"""

import pytest

from bot.config import Config
from bot.duel_service import DuelService
from bot.game.classes import Stats
from bot.game.clock import MOSCOW, day_opens
from bot.game.combat import DuelEnd, RoundResult
from bot.game.health import now_ts
from bot.game.quests import ring_mark
from bot.game.trainer import (
    TRAINER_ID,
    TRAINER_NAME,
    WIN_SHARES,
    lesson_exp,
    win_share,
)
from bot.models import Player
from bot.quest_service import journal, take
from bot.trainer_service import ensure_trainer, rested


class FakeBot:
    async def send_message(self, *args, **kwargs):
        return None

    async def edit_message_text(self, *args, **kwargs):
        return None


@pytest.fixture
def duels(db) -> DuelService:
    return DuelService(
        bot=FakeBot(),
        db=db,
        config=Config(bot_token="test", db_path=":memory:", round_break=0),
    )


def a_learner(level: int = 1, user_id: int = 42) -> Player:
    stats = Stats(strength=4, agility=3, intuition=3, endurance=4)
    return Player(
        user_id=user_id,
        nickname="Новичок",
        class_code="warrior",
        strength=stats.strength,
        agility=stats.agility,
        intuition=stats.intuition,
        endurance=stats.endurance,
        level=level,
    )


def a_verdict(winner_id: int) -> RoundResult:
    """Итог боя: нокаут в пользу названного."""
    return RoundResult(
        number=1,
        strikes=[],
        hp_after={},
        winner_id=winner_id,
        finished=True,
        end_reason=DuelEnd.KO,
    )


async def _lesson(duels, db, learner: Player, *, won: bool, damage: int = 40):
    """Провести урок до итога и вернуть ученика, каким он вышел."""
    session = await duels.start_trainer_duel(learner)
    session.fighters[learner.user_id].damage_dealt = damage
    result = a_verdict(learner.user_id if won else TRAINER_ID)
    await duels._apply_results(session, result)
    await duels._record(session, result)
    duels._forget(session)
    return await db.get_player(learner.user_id)


async def test_the_first_win_of_the_day_pays_in_full_and_the_second_half(duels, db):
    """Первая победа за сутки — полный опыт, вторая — половина."""
    learner = a_learner()
    await db.save_player(learner)

    first = await _lesson(duels, db, learner, won=True)
    assert first.total_exp > 0, "за первую победу не дали ничего"

    trainer = await ensure_trainer(db)
    trainer.set_hp(trainer.max_hp)
    await db.save_player(trainer)

    before = first.total_exp
    second = await _lesson(duels, db, first, won=True)
    got = second.total_exp - before
    assert 0 < got < before, f"вторая победа дала {got} при первой в {before}"
    # Половина, а не произвольная доля
    assert got == lesson_exp(before, 1)


async def test_the_third_win_of_the_day_gives_nothing(duels, db):
    """Третья победа за сутки не даёт опыта вовсе."""
    learner = a_learner()
    await db.save_player(learner)

    for _ in range(len(WIN_SHARES)):
        learner = await _lesson(duels, db, learner, won=True)
        trainer = await ensure_trainer(db)
        trainer.set_hp(trainer.max_hp)
        await db.save_player(trainer)

    before = learner.total_exp
    after = await _lesson(duels, db, learner, won=True)
    assert after.total_exp == before, "третья победа всё равно дала опыт"


async def test_a_loss_leaves_the_learner_with_nothing(duels, db):
    """За поражение не дают ничего — ни опыта, ни рейтинга."""
    learner = a_learner()
    await db.save_player(learner)

    after = await _lesson(duels, db, learner, won=False)
    assert after.total_exp == 0
    assert after.rating == learner.rating


async def test_the_lesson_is_counted_by_the_learners_level(duels, db):
    """Опыт считается от уровня ученика, а не от уровня Тренера.

    Тренер выходит старше, и ставка по старшему из двоих была бы платой
    за его фору, которой ученик не брал.
    """
    learner = a_learner(level=4, user_id=44)
    await db.save_player(learner)
    session = await duels.start_trainer_duel(learner)
    session.fighters[learner.user_id].damage_dealt = 40
    fighter = session.fighters[learner.user_id]
    teacher = session.fighters[TRAINER_ID]
    assert teacher.level > fighter.level, "Тренер вышел не старше ученика"

    from bot.game.economy import win_exp

    by_learner = win_exp(40, learner.level, learner.level,
                         fighter.max_hp + teacher.max_hp, 2)
    by_trainer = win_exp(40, learner.level, teacher.level,
                         fighter.max_hp + teacher.max_hp, 2)
    assert by_learner < by_trainer, "уровни считаются одинаково, проверка слепа"

    await duels._apply_results(session, a_verdict(learner.user_id))
    after = await db.get_player(learner.user_id)
    assert after.total_exp == by_learner, f"начислили {after.total_exp}"


async def test_the_lesson_goes_into_raids_and_not_into_wins(duels, db):
    """Бой идёт в рейды. В победы, поражения и ничьи — нет."""
    learner = a_learner()
    await db.save_player(learner)

    won = await _lesson(duels, db, learner, won=True)
    assert (won.raid_fights, won.raid_wins) == (1, 1)
    assert (won.wins, won.losses, won.draws) == (0, 0, 0)

    trainer = await ensure_trainer(db)
    trainer.set_hp(trainer.max_hp)
    await db.save_player(trainer)

    lost = await _lesson(duels, db, won, won=False)
    assert (lost.raid_fights, lost.raid_wins) == (2, 1), "поражение не попало в рейды"
    assert (lost.wins, lost.losses, lost.draws) == (0, 0, 0)


async def test_no_elo_and_no_weekly_rating_come_out_of_the_lesson(duels, db):
    """Рейтинг не двигается и в журнал ELO бой не ложится.

    По журналу считается недельный рейтинг: запись о бое с Тренером
    затащила бы урок и туда.
    """
    learner = a_learner()
    await db.save_player(learner)
    was = learner.rating

    after = await _lesson(duels, db, learner, won=True)
    assert after.rating == was, "победа над Тренером подняла рейтинг"

    async with db.conn.execute(
        "SELECT COUNT(*) AS rows FROM rating_log WHERE user_id = ?",
        (learner.user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert int(row["rows"]) == 0, "урок записался в журнал ELO"


async def test_the_lesson_closes_the_bartenders_first_quest(duels, db):
    """Этот бой бармен принимает за первый кулачный."""
    learner = a_learner()
    learner.location = "bar"
    await db.save_player(learner)
    await take(db, learner, "ring")

    await _lesson(duels, db, learner, won=True)

    log = await journal(db, learner)
    assert ring_mark(True) in log["ring"].marks, (
        f"бармен урока не заметил: {log['ring'].marks}"
    )


async def test_a_beaten_trainer_keeps_his_wounds_after_the_lesson(duels, db):
    """Здоровье Тренера после боя сохраняется — долей, а не числом.

    На ринге у него было тело ученика, и записанное число единиц
    означало бы в его собственном теле совсем другую долю: против
    крепкого ученика побитый Тренер вернулся бы почти целым.
    """
    learner = a_learner()
    await db.save_player(learner)

    session = await duels.start_trainer_duel(learner)
    coach = session.fighters[TRAINER_ID]
    coach.hp = coach.max_hp // 4
    await duels._apply_results(session, a_verdict(learner.user_id))

    trainer = await db.get_player(TRAINER_ID)
    left = trainer.current_hp() / trainer.max_hp
    assert 0.2 <= left <= 0.3, f"доля не сохранилась: {left:.0%}"
    assert not rested(trainer), "побитый Тренер снова зовёт на бой"


async def test_the_day_counter_starts_at_moscow_midnight(duels, db):
    """Счёт побед обнуляется в полночь по Москве, а не в полночь UTC."""
    from datetime import datetime

    moment = datetime(2026, 11, 21, 2, 30, tzinfo=MOSCOW).timestamp()
    opens = day_opens(moment)
    here = datetime.fromtimestamp(opens, MOSCOW)
    assert (here.hour, here.minute, here.second) == (0, 0, 0)
    assert here.date() == datetime(2026, 11, 21, tzinfo=MOSCOW).date()
    assert opens <= moment


async def test_wins_over_the_trainer_are_counted_only_from_today(duels, db):
    """Вчерашние победы в сегодняшний счёт не идут."""
    learner = a_learner()
    await db.save_player(learner)
    await db.add_duel(
        chat_id=None, thread_id=None,
        challenger_id=learner.user_id, opponent_id=TRAINER_ID,
        winner_id=learner.user_id, rounds=1, end_reason="knockout",
    )
    today = day_opens(now_ts())
    assert await db.count_wins_over(learner.user_id, TRAINER_ID, today) == 1

    # Тот же бой, но считаем от завтрашней полуночи: в счёт он не входит
    tomorrow = today + 60 * 60 * 24
    assert await db.count_wins_over(learner.user_id, TRAINER_ID, tomorrow) == 0

    # Проигранный бой в счёт побед не идёт
    await db.add_duel(
        chat_id=None, thread_id=None,
        challenger_id=learner.user_id, opponent_id=TRAINER_ID,
        winner_id=TRAINER_ID, rounds=1, end_reason="knockout",
    )
    assert await db.count_wins_over(learner.user_id, TRAINER_ID, today) == 1


def test_the_share_of_the_day_is_one_then_a_half_then_nothing():
    """Доли опыта по счёту побед: 100%, 50%, ноль."""
    assert win_share(0) == 1.0
    assert win_share(1) == 0.5
    assert win_share(2) == 0.0
    assert win_share(9) == 0.0

    assert lesson_exp(100, 0) == 100
    assert lesson_exp(100, 1) == 50
    assert lesson_exp(100, 2) == 0
    # Половина мелкой награды не обнуляется: опыт за вторую победу есть
    assert lesson_exp(1, 1) == 1


async def test_the_trainer_is_in_no_list_of_players(duels, db):
    """Тренера нет ни в списках клуба, ни в рейтинге, ни в поиске.

    Запись у него человеческая — та же таблица, тот же рейтинг по
    умолчанию, — и без отбора он встал бы в «Список игроков» между
    живыми бойцами, попал бы в верхушку рейтинга и нашёлся бы поиском
    по нику. Он не игрок: драться с ним можно, соревноваться — нет.
    """
    learner = a_learner()
    await db.save_player(learner)
    trainer = await ensure_trainer(db)
    assert trainer is not None, "Тренера не завели — проверка слепа"

    everybody = await db.all_players()
    assert TRAINER_ID not in {one.user_id for one in everybody}

    top = await db.top_players(limit=50)
    assert TRAINER_ID not in {one.user_id for one in top}

    page = await db.rated_players(offset=0, limit=50)
    assert TRAINER_ID not in {one.user_id for one in page}

    assert await db.search_players("Трен") == []
    assert await db.find_by_nickname(TRAINER_NAME) is None

    # Число страниц считается по тем же бойцам, что и строки
    assert await db.count_players() == 1
    assert await db.level_spread() == [learner.level], (
        f"уровень Тренера попал в отбор: {await db.level_spread()}"
    )

    # И в доме его тоже нет: по городу Тренер не ходит
    here = await db.players_at(trainer.location)
    assert TRAINER_ID not in {one.user_id for one in here}


async def test_the_history_reads_a_fight_against_the_trainer(duels, db):
    """В списке боёв урок стоит строкой «Победа — бой против Тренера»."""
    from bot.webapp.fight import fight_row

    learner = a_learner()
    await db.save_player(learner)
    await _lesson(duels, db, learner, won=True)

    rows = await db.fights_of(learner.user_id)
    assert rows, "урок не попал в историю боёв"
    line = fight_row(rows[0], learner.user_id)
    assert line["caption"] == f"Победа — бой против {TRAINER_NAME}", line["caption"]
    assert line["result"] == "win"
