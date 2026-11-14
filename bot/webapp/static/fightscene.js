// Сцена кулачного боя: перевод серверного хода в очередь показа.
//
// Это адаптер и только он. Он ничего не рисует, не знает ни про canvas,
// ни про атласы, ни про тайминги кадров — он отвечает на один вопрос:
// «что показать и в каком порядке, чтобы получился этот ход».
// Поэтому его можно проверить без браузера, и именно это требует
// приёмка: повторный snapshot, несколько ходов за один опрос, пропуск,
// неизвестный исход, старый журнал, контрудар, одновременный размен,
// смена боя.
//
// **Источник истины — сервер.** Адаптер не считает урон, не бросает
// кости, не решает, кто бил первым. Проворство пересчитывать в браузере
// нельзя: второй счёт однажды разошёлся бы с первым, и экран начал бы
// врать уверенно.

// Сколько держится сопротивление сорванного блока, прежде чем удар
// дойдёт до тела. Два этапа одного удара, а не два удара
var BREAK_HOLD_MS = 180;

// Куда рисовать контрудар, если журнал не сказал зоны. Контрудар в
// движке к зоне не привязан и зональную броню игнорирует; это условная
// презентация, а не новая игровая зона
var COUNTER_FALLBACK_ZONE = "chest";

// Исходы, которые адаптер умеет разворачивать. Всё остальное уходит в
// текст: неизвестный исход не должен ни ломать очередь, ни подвешивать
// сцену
var KNOWN_OUTCOMES = ["hit", "crit", "break", "block", "dodge", "counter", "skip"];

function fightQueue() {
  var duelId = null;
  var lastTurn = 0;
  var events = [];
  var keys = {};
  var authority = {};

  function add(event) {
    // Ключ собран из боя, хода, номера удара и подсобытия. Повторный
    // опрос приносит те же ходы, и без ключа сцена проигрывала бы их
    // заново — урон, нокаут и всё остальное по второму разу
    if (keys[event.key]) {
      return false;
    }
    keys[event.key] = true;
    events.push(event);
    return true;
  }

  function strikeEvents(turn, strike, index, together) {
    var base = duelId + ":" + turn.number + ":" + index;
    var outcome = strike.outcome;
    var zone = strike.zone || null;
    var made = [];

    if (KNOWN_OUTCOMES.indexOf(outcome) < 0) {
      // Безопасный откат: про удар говорим словами и идём дальше
      made.push({
        key: base + ":text", kind: "text", unknown: true,
        outcome: outcome, attacker: strike.attacker_id,
        defender: strike.defender_id, together: together,
      });
      return made;
    }

    if (outcome === "skip") {
      // Пропуск — это ожидание, а не удар: ни контакта, ни здоровья
      made.push({
        key: base + ":skip", kind: "skip", attacker: strike.attacker_id,
        defender: strike.defender_id, together: together,
      });
      return made;
    }

    // Замах есть у всякого удара, чем бы он ни кончился
    made.push({
      key: base + ":attack", kind: "attack", zone: zone,
      attacker: strike.attacker_id, defender: strike.defender_id,
      ability: strike.ability || "", together: together,
    });

    if (outcome === "block") {
      // Чистый блок: контакт с защитой, без реакции и без здоровья
      made.push({
        key: base + ":block", kind: "block", zone: zone,
        defender: strike.defender_id,
        ability: strike.defence_ability || "", together: together,
      });
      return made;
    }

    if (outcome === "dodge" || outcome === "counter") {
      made.push({
        key: base + ":dodge", kind: "dodge", zone: zone,
        defender: strike.defender_id,
        ability: strike.defence_ability || "", together: together,
      });
      if (outcome === "counter") {
        // Контрудар — ответ защищавшегося с близкой дистанции, и он
        // НЕ замещает следующий обычный удар: тот остаётся в strikes и
        // своей очереди не теряет. Расходиться и снова подходить между
        // уворотом и ответом нельзя
        made.push({
          key: base + ":counter", kind: "counter",
          zone: strike.counter_zone || COUNTER_FALLBACK_ZONE,
          // Зоны в старом журнале нет, и выдавать догадку за данные
          // сервера нельзя: пусть видно, что это презентация
          assumed_zone: !strike.counter_zone,
          attacker: strike.defender_id, defender: strike.attacker_id,
          damage: strike.counter || 0,
          // Остаток того, кого контратаковали. `hp_after` относится к
          // защищавшемуся, и брать его здесь значило бы снять урон не
          // с того бойца
          hp: strike.counter_hp_after,
          critical: !!strike.counter_critical,
          together: together,
        });
      }
      return made;
    }

    if (outcome === "break") {
      // Пробитие — два этапа одного удара: выставленная защита,
      // сопротивление, сорванный блок и попадание. Урон один раз, во
      // втором этапе
      made.push({
        key: base + ":guard", kind: "block", zone: zone,
        defender: strike.defender_id, breaking: true,
        ability: strike.defence_ability || "", together: together,
      });
      made.push({
        key: base + ":hit", kind: "hit", zone: zone, broken: true,
        delay: BREAK_HOLD_MS, attacker: strike.attacker_id,
        defender: strike.defender_id, damage: strike.damage || 0,
        armor: strike.armor || 0, hp: strike.hp_after,
        critical: outcome === "crit", together: together,
      });
      return made;
    }

    // hit и crit: обычная реакция выбранной зоны
    made.push({
      key: base + ":hit", kind: "hit", zone: zone,
      attacker: strike.attacker_id, defender: strike.defender_id,
      damage: strike.damage || 0, armor: strike.armor || 0,
      hp: strike.hp_after, critical: outcome === "crit",
      together: together,
    });
    return made;
  }

  return {
    // Смена боя или закрытие панели: старая очередь не должна догонять
    // новый бой ни одним событием
    reset: function (id) {
      duelId = id === undefined ? null : id;
      lastTurn = 0;
      events = [];
      keys = {};
      authority = {};
      return this;
    },

    // Добавить ход. Возвращает, сколько событий прибавилось: ноль
    // значит «этот ход уже проигран», и это нормальный ответ на
    // повторный опрос, а не ошибка
    push: function (turn) {
      if (!turn || typeof turn.number !== "number") {
        return 0;
      }
      if (turn.number <= lastTurn) {
        return 0;
      }
      var strikes = turn.strikes || [];
      // «Разом» означает общее контактное окно и согласованный итог:
      // вторую атаку нельзя отменить из-за визуально более раннего
      // нокаута, а нокаут ставится на завершении группы
      var together = turn.resolution === "simultaneous";
      var added = 0;
      for (var i = 0; i < strikes.length; i += 1) {
        var made = strikeEvents(turn, strikes[i], i, together);
        for (var j = 0; j < made.length; j += 1) {
          if (add(made[j])) {
            added += 1;
          }
        }
      }
      // Итог хода — последним и один раз. До него показывать конечное
      // здоровье опроса нельзя: сперва его удары
      if (
        add({
          key: duelId + ":" + turn.number + ":end",
          kind: "settle", number: turn.number, hp: turn.hp_after || {},
          finished: !!turn.finished, winner: turn.winner_id || null,
          together: together,
          // Старый журнал про одновременность молчит. Играем подряд, но
          // помечаем, что данных не было, — реконструкцию нельзя
          // выдавать за серверные
          assumed_sequential: turn.resolution === undefined,
        })
      ) {
        added += 1;
      }
      lastTurn = turn.number;
      authority = turn.hp_after || authority;
      return added;
    },

    // Забрать накопленное. Очередь отдаётся один раз: ключи остаются,
    // поэтому повторный опрос тех же ходов ничего не добавит
    drain: function () {
      var out = events;
      events = [];
      return out;
    },

    pending: function () {
      return events.length;
    },

    // Последний ход, который адаптер принял
    seen: function () {
      return lastTurn;
    },

    // Серверное здоровье на последнем принятом ходу. Это не то, что
    // сейчас на экране: экранное меняется в момент контакта
    authoritative: function () {
      return authority;
    },
  };
}
