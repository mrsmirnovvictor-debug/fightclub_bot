// Проверки адаптера сцены боя. Запускаются из pytest
// (`tests/test_fightscene.py`) настоящим node: адаптер — чистая логика,
// браузер ей не нужен, а приёмка карточки требует именно unit-тестов.
//
// Каждая проверка названа фразой и падает с осмысленным текстом: это те
// же правила, что и у питоновских тестов проекта.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "..", "bot", "webapp", "static", "fightscene.js"),
  "utf8",
);
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const fightQueue = sandbox.fightQueue;

let failed = 0;

function check(what, how) {
  try {
    how();
    console.log("  ок   " + what);
  } catch (error) {
    failed += 1;
    console.log("  ПАЛО " + what + ": " + error.message);
  }
}

function ok(truth, why) {
  if (!truth) {
    throw new Error(why || "ожидалось истинное");
  }
}

function same(got, want, why) {
  if (got !== want) {
    throw new Error((why || "не совпало") + ": " + got + " вместо " + want);
  }
}

function kinds(events) {
  return events.map((one) => one.kind).join(",");
}

function strike(over) {
  return Object.assign(
    {
      attacker_id: 1, defender_id: 2, zone: "head", block: [],
      outcome: "hit", weapon: "кулаки", damage: 12, counter: 0, armor: 0,
      hp_after: 88, counter_hp_after: 100, ability: "", defence_ability: "",
      ability_title: "", defence_ability_title: "",
      missed_turn: false,
    },
    over || {},
  );
}

function turn(over) {
  return Object.assign(
    {
      number: 1, round: 1, turn: 1, strikes: [strike()],
      hp_before: { 1: 100, 2: 100 }, hp_after: { 1: 100, 2: 88 },
      resolution: "sequential", finished: false, winner_id: null, lines: [],
    },
    over || {},
  );
}

console.log("адаптер сцены боя:");

check("обычный удар разворачивается в замах и реакцию", () => {
  const q = fightQueue().reset("d1");
  q.push(turn());
  const events = q.drain();
  same(kinds(events), "attack,hit,settle");
  same(events[1].damage, 12, "урон взят у сервера");
  same(events[1].hp, 88, "остаток защищавшегося");
  ok(!events[1].critical, "обычный удар не крит");
});

check("крит отличается от обычного удара пометкой, а не новой анимацией", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ strikes: [strike({ outcome: "crit", damage: 30 })] }));
  const events = q.drain();
  same(kinds(events), "attack,hit,settle");
  ok(events[1].critical, "крит помечен");
  same(events[1].zone, "head", "зона та же, что выбрал боец");
});

check("чистый блок не снимает здоровья и не даёт реакции", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ strikes: [strike({ outcome: "block", damage: 0 })] }));
  const events = q.drain();
  same(kinds(events), "attack,block,settle");
  ok(events.every((one) => one.kind !== "hit"), "реакции нет");
});

check("пробитие — два этапа одного удара, урон один раз", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ strikes: [strike({ outcome: "break", damage: 20 })] }));
  const events = q.drain();
  same(kinds(events), "attack,block,hit,settle");
  ok(events[1].breaking, "первый этап — выставленная защита");
  ok(events[2].broken, "второй — сорванная");
  ok(events[2].delay > 0, "между ними сопротивление");
  same(events.filter((one) => one.kind === "hit").length, 1, "урон один раз");
});

check("контрудар — это уворот и ответ, и он не съедает следующий удар", () => {
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      strikes: [
        strike({ outcome: "counter", damage: 0, counter: 9, counter_hp_after: 91 }),
        strike({ attacker_id: 2, defender_id: 1, outcome: "hit", damage: 11 }),
      ],
    }),
  );
  const events = q.drain();
  same(kinds(events), "attack,dodge,counter,attack,hit,settle");
  const answer = events[2];
  same(answer.attacker, 2, "отвечает тот, кто уворачивался");
  same(answer.damage, 9);
  same(answer.hp, 91, "остаток контратакованного, а не защищавшегося");
  ok(answer.assumed_zone, "зоны сервер не дал — это презентация");
  ok(!answer.critical, "критом без поля сервера не объявляем");
});

check("зону контрудара берём у сервера, если она есть", () => {
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      strikes: [
        strike({ outcome: "counter", counter: 5, counter_zone: "belly",
                 counter_critical: true }),
      ],
    }),
  );
  const answer = q.drain()[2];
  same(answer.zone, "belly");
  ok(!answer.assumed_zone, "это уже данные, а не догадка");
  ok(answer.critical, "крит объявлен сервером");
});

check("пропуск хода — слова, без удара и без здоровья", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ strikes: [strike({ outcome: "skip", damage: 0, missed_turn: true })] }));
  const events = q.drain();
  same(kinds(events), "skip,settle");
});

check("неизвестный исход не ломает очередь", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ strikes: [strike({ outcome: "непонятно" })] }));
  const events = q.drain();
  same(kinds(events), "text,settle");
  ok(events[0].unknown, "помечен как неразобранный");
});

check("повторный опрос того же хода не добавляет ничего", () => {
  const q = fightQueue().reset("d1");
  same(q.push(turn()), 3, "первый раз — три события");
  same(q.push(turn()), 0, "второй раз — ни одного");
  same(q.drain().length, 3);
});

check("несколько ходов за один опрос идут по номерам", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ number: 1 }));
  q.push(turn({ number: 2 }));
  const events = q.drain();
  same(events.filter((one) => one.kind === "settle").length, 2);
  same(q.seen(), 2);
});

check("ход с меньшим номером после большого не отыгрывается", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ number: 5 }));
  q.drain();
  same(q.push(turn({ number: 3 })), 0, "старый снимок не возвращает бой назад");
});

check("одновременный размен помечает оба удара общим окном", () => {
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      resolution: "simultaneous",
      strikes: [strike(), strike({ attacker_id: 2, defender_id: 1 })],
    }),
  );
  const events = q.drain();
  ok(events.every((one) => one.together), "все события группы вместе");
  ok(!events[events.length - 1].assumed_sequential, "сервер сказал прямо");
});

check("старый журнал без очереди играется подряд и говорит об этом", () => {
  const q = fightQueue().reset("d1");
  const old = turn();
  delete old.resolution;
  q.push(old);
  const settle = q.drain().pop();
  ok(settle.assumed_sequential, "данных об одновременности не было");
  ok(!settle.together);
});

check("смена боя стирает очередь начисто", () => {
  const q = fightQueue().reset("d1");
  q.push(turn());
  q.reset("d2");
  same(q.pending(), 0);
  same(q.seen(), 0);
  same(q.push(turn()), 3, "в новом бою тот же номер хода играется заново");
});

check("битый ход не подвешивает адаптер", () => {
  const q = fightQueue().reset("d1");
  same(q.push(null), 0);
  same(q.push({}), 0);
  same(q.push({ number: 1 }), 1, "без ударов остаётся только итог");
});

check("серверное здоровье отделено от показанного", () => {
  const q = fightQueue().reset("d1");
  q.push(turn({ hp_after: { 1: 100, 2: 88 } }));
  const events = q.drain();
  same(events[1].hp, 88, "у реакции своё — его применяют на контакте");
  same(q.authoritative()[2], 88, "а это итог хода, он авторитетный");
});

check("приёмы доезжают до событий своей стороной", () => {
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      strikes: [strike({
        ability: "strong_hit", ability_title: "Сильный удар",
        defence_ability: "parry", defence_ability_title: "Парирование",
        outcome: "block",
      })],
    }),
  );
  const events = q.drain();
  same(events[0].ability, "Сильный удар", "у замаха приём атакующего");
  same(events[1].ability, "Парирование", "у блока приём защищающегося");
});

check("подпись приёма берётся из названия, а не из кода", () => {
  // Над бойцом рисуется то, что адаптер положил в событие, и код
  // приёма там читался латиницей: вместо «Проворности» в анимации
  // проскакивало `nimble`. Коды в ходе остаются — по ним лежит
  // история, — но подписью они больше не становятся
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      strikes: [strike({
        outcome: "dodge", damage: 0, hp_after: 100,
        defence_ability: "nimble", defence_ability_title: "Проворность",
      })],
    }),
  );
  const dodge = q.drain().find((one) => one.kind === "dodge");
  same(dodge.ability, "Проворность", "подпись по-русски");
});

check("приём без названия подписывается словом сцены, а не кодом", () => {
  // Старые журналы лежат в базе без названий, и выдумывать их на
  // экране нечем: пусть сцена скажет своё слово, как при приёме,
  // которого не было вовсе
  const q = fightQueue().reset("d1");
  q.push(
    turn({
      strikes: [strike({
        outcome: "dodge", damage: 0, hp_after: 100,
        defence_ability: "nimble", defence_ability_title: undefined,
      })],
    }),
  );
  const dodge = q.drain().find((one) => one.kind === "dodge");
  same(dodge.ability, "", "названия нет — и подписи нет");
});

if (failed) {
  console.log("пало проверок: " + failed);
  process.exit(1);
}
console.log("все проверки прошли");
