// Стенд сцены боя: выбрать бойцов, задать размен, увидеть его глазами
// игрока.
//
// Главное здесь — что стенд не содержит своей копии сцены. Он берёт
// настоящий загрузчик, настоящий адаптер событий и настоящую
// раскадровку, а ход просит у сервера, где тот собирается настоящим
// движком и тем же сериализатором, что в бою. Нарисуй я ход в браузере
// сам — стенд проверял бы сцену против моего представления о сервере, а
// не против сервера, и разошлись бы они молча.

var ZONES = [];
var CLASSES = [];
var OUTCOMES = [];

// Исходы, у которых зона ни при чём: пропуск её не имеет вовсе
var ZONELESS = ["skip"];
// У кого в исходе бывает своя цифра урона
var WITH_DAMAGE = ["hit", "crit", "break"];

// Та же дверь, что у карточки: стенд ходит на сервер с подписью
// Telegram, а не в обход проверки
function benchHead() {
  var tg = window.Telegram && window.Telegram.WebApp;
  return {
    "Content-Type": "application/json",
    "X-Telegram-Init-Data": (tg && tg.initData) || "",
  };
}

// Спросить ход у сервера. Собирает его движок — стенд не умеет и не
// должен уметь собирать ход сам
function benchAsk(body) {
  return fetch("api/scene", {
    method: "POST",
    headers: benchHead(),
    body: JSON.stringify(body),
  }).then(function (answer) {
    if (!answer.ok) {
      return answer.text().then(function (why) {
        throw new Error("сервер отказал: " + (why || answer.status));
      });
    }
    return answer.json();
  });
}

// Готовые размены. Тестировщику не обязательно знать, что у движка
// семь исходов и пять зон: он жмёт «Контрудар» и смотрит, похоже ли
// это на контрудар. Ручной сбор остаётся ниже — для тех, кому нужен
// именно свой случай
var SCENARIOS = [
  {
    title: "Удар",
    strikes: [{ by: "left", zone: "chest", outcome: "hit" }],
  },
  {
    title: "Крит",
    strikes: [{ by: "left", zone: "head", outcome: "crit" }],
  },
  {
    title: "Блок",
    strikes: [{ by: "left", zone: "belly", outcome: "block" }],
  },
  {
    title: "Пробитый блок",
    strikes: [{ by: "left", zone: "belt", outcome: "break" }],
  },
  {
    title: "Уворот",
    strikes: [{ by: "left", zone: "legs", outcome: "dodge" }],
  },
  {
    title: "Контрудар",
    strikes: [{ by: "left", zone: "head", outcome: "counter" }],
  },
  {
    title: "Пропуск",
    strikes: [{ by: "left", zone: "chest", outcome: "skip" }],
  },
  {
    title: "Размен",
    strikes: [
      { by: "left", zone: "head", outcome: "hit" },
      { by: "right", zone: "belly", outcome: "hit" },
    ],
  },
  {
    title: "Разом",
    resolution: "simultaneous",
    strikes: [
      { by: "left", zone: "chest", outcome: "hit" },
      { by: "right", zone: "chest", outcome: "hit" },
    ],
  },
  {
    title: "Нокаут",
    strikes: [{ by: "left", zone: "head", outcome: "crit", damage: 999 }],
  },
];

var bench = {
  assets: null,
  stage: null,
  queue: null,
  ctx: null,
  scene: null,
  started: null,
  frame: null,
  lines: [],
};

function benchSay(words) {
  var box = document.getElementById("said");
  box.textContent = "";
  if (!words.length) {
    var empty = document.createElement("p");
    empty.className = "bench-line quiet";
    empty.textContent = "Судья промолчал.";
    box.appendChild(empty);
    return;
  }
  words.forEach(function (said) {
    var line = document.createElement("p");
    line.className = "bench-line";
    line.textContent = said;
    box.appendChild(line);
  });
}

function benchOption(value, title) {
  var one = document.createElement("option");
  one.value = value;
  one.textContent = title;
  return one;
}

// Одна строка размена. Собирается из тех же списков, что прислал
// сервер: зоны и исходы не выписаны здесь руками, иначе стенд однажды
// начал бы предлагать то, чего движок не знает
function benchStrikeRow() {
  var row = document.createElement("div");
  row.className = "bench-strike";

  var who = document.createElement("select");
  who.className = "bench-who";
  who.appendChild(benchOption("left", "слева →"));
  who.appendChild(benchOption("right", "← справа"));

  var zone = document.createElement("select");
  zone.className = "bench-zone";
  ZONES.forEach(function (one) {
    zone.appendChild(benchOption(one.code, one.title));
  });

  var outcome = document.createElement("select");
  outcome.className = "bench-outcome";
  OUTCOMES.forEach(function (one) {
    outcome.appendChild(benchOption(one.code, one.emoji + " " + one.title));
  });
  // Исходы приходят в порядке перечисления движка, а первый там —
  // «не бил»: открывать стенд с ним значит запереть зону и урон и
  // предложить человеку начать с бездействия
  outcome.value = "hit";

  var damage = document.createElement("input");
  damage.className = "bench-damage";
  damage.type = "number";
  damage.min = "0";
  damage.placeholder = "урон";

  var gone = document.createElement("button");
  gone.type = "button";
  gone.className = "bench-drop";
  gone.textContent = "✕";
  gone.addEventListener("click", function () {
    row.remove();
  });

  function fit() {
    // Зона у пропуска ни при чём, урон — только там, где он бывает
    zone.disabled = ZONELESS.indexOf(outcome.value) >= 0;
    damage.disabled = WITH_DAMAGE.indexOf(outcome.value) < 0;
    zone.classList.toggle("off", zone.disabled);
    damage.classList.toggle("off", damage.disabled);
  }
  outcome.addEventListener("change", fit);
  fit();

  [who, zone, outcome, damage, gone].forEach(function (one) {
    row.appendChild(one);
  });
  return row;
}

function benchStrikes() {
  var rows = document.querySelectorAll(".bench-strike");
  var out = [];
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i];
    var damage = row.querySelector(".bench-damage");
    out.push({
      by: row.querySelector(".bench-who").value,
      zone: row.querySelector(".bench-zone").value,
      outcome: row.querySelector(".bench-outcome").value,
      damage: damage.disabled || !damage.value ? null : Number(damage.value),
    });
  }
  return out;
}

function benchTick(now) {
  if (!bench.stage) return;
  if (bench.started === null) bench.started = now;
  bench.stage.at(now - bench.started);
  try {
    drawStage(bench.ctx, bench.stage, bench.assets, bench.scene);
  } catch (error) {
    console.error("стенд не смог нарисовать кадр", error);
    return;
  }
  bench.frame = requestAnimationFrame(benchTick);
}

// Графика нужна той же, что в бою: загрузчик тот же, адреса те же
function benchLoad(lines) {
  var canvas = document.querySelector(".fight-canvas");
  var veil = document.querySelector(".fight-veil");
  var bar = document.querySelector(".fight-veil-bar");
  veil.classList.remove("gone");

  bench.assets = fightAssets({ base: document.body.dataset.combat || "" });
  var watch = setInterval(function () {
    bar.style.width = Math.round(bench.assets.progress() * 100) + "%";
  }, 80);

  return bench.assets
    .open(lines)
    .then(function (scene) {
      bench.scene = scene;
      var ratio = Math.min(
        window.devicePixelRatio || 1,
        scene.rendering.maximumDevicePixelRatio,
      );
      canvas.width = scene.logicalSize.width * ratio;
      canvas.height = scene.logicalSize.height * ratio;
      bench.ctx = canvas.getContext("2d");
      bench.ctx.scale(ratio, ratio);
      return bench.assets.prepare();
    })
    .then(function () {
      clearInterval(watch);
      bar.style.width = "100%";
      veil.classList.add("gone");
      return bench.assets.finish().catch(function (error) {
        console.warn("увороты догрузить не удалось", error);
      });
    })
    .catch(function (error) {
      clearInterval(watch);
      veil.classList.remove("gone");
      document.querySelector(".fight-veil-word").textContent =
        "Графика не приехала: " + error.message;
      throw error;
    });
}

function benchCast(fighter, side) {
  return {
    id: fighter.user_id,
    name: fighter.name,
    line: fighter.art_line,
    hp: fighter.hp,
    maxHp: fighter.max_hp,
    side: side,
  };
}

function benchTimings(line) {
  var names = [
    "approach", "attack", "contact", "hitReaction", "breakHold",
    "dodge", "retreat", "counterRetreat", "ko", "swayPeriod",
  ];
  var out = {};
  for (var i = 0; i < names.length; i += 1) {
    out[names[i]] = bench.assets.timing(line, names[i]);
  }
  return out;
}

// Разложить готовый размен по строкам формы: человек видит, из чего
// он собран, и может доправить. Иначе кнопка была бы чёрным ящиком
function benchFill(script) {
  var box = document.getElementById("strikes");
  box.textContent = "";
  document.getElementById("resolution").value =
    script.resolution || "sequential";
  script.strikes.forEach(function (one) {
    var row = benchStrikeRow();
    row.querySelector(".bench-outcome").value = one.outcome;
    // Запертость зоны и урона зависит от исхода, поэтому пересчитываем
    // её после того, как исход выбран, а не до
    row.querySelector(".bench-outcome").dispatchEvent(new Event("change"));
    row.querySelector(".bench-who").value = one.by;
    row.querySelector(".bench-zone").value = one.zone;
    if (one.damage) {
      row.querySelector(".bench-damage").value = String(one.damage);
    }
    box.appendChild(row);
  });
}

async function benchPlay() {
  var left = document.getElementById("left-class").value;
  var right = document.getElementById("right-class").value;
  var data = await benchAsk({
    left: left,
    right: right,
    resolution: document.getElementById("resolution").value,
    strikes: benchStrikes(),
  });

  if (bench.frame) cancelAnimationFrame(bench.frame);
  bench.frame = null;
  bench.stage = null;

  var lines = [];
  data.fighters.forEach(function (one) {
    if (lines.indexOf(one.art_line) < 0) lines.push(one.art_line);
  });
  await benchLoad(lines);

  bench.queue = fightQueue().reset("стенд");
  bench.queue.push(data.turn);
  bench.stage = fightStage({
    timing: benchTimings(data.fighters[0].art_line),
  });
  bench.stage.cast(
    benchCast(data.fighters[0], "left"),
    benchCast(data.fighters[1], "right"),
  );
  bench.stage.feed(bench.queue.drain());
  bench.started = null;
  bench.frame = requestAnimationFrame(benchTick);
  benchSay(data.turn.lines || []);
}

async function benchStart() {
  // Списки классов, зон и исходов приходят от сервера вместе с первым
  // ходом: справочники в игре одни, и вторых тут заводить нельзя
  var data = await benchAsk({ left: "warrior", right: "tank", strikes: [] });
  ZONES = data.zones;
  CLASSES = data.classes;
  OUTCOMES = data.outcomes;

  ["left-class", "right-class"].forEach(function (id, index) {
    var picker = document.getElementById(id);
    CLASSES.forEach(function (one) {
      picker.appendChild(benchOption(one.code, one.emoji + " " + one.title));
    });
    picker.value = CLASSES[index % CLASSES.length].code;
  });

  var strikes = document.getElementById("strikes");
  var ready = document.getElementById("ready");
  SCENARIOS.forEach(function (script) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn secondary bench-ready";
    button.textContent = script.title;
    button.addEventListener("click", function () {
      benchFill(script);
      benchPlay().catch(function (error) {
        benchSay(["Не вышло: " + error.message]);
      });
    });
    ready.appendChild(button);
  });

  document.getElementById("add").addEventListener("click", function () {
    strikes.appendChild(benchStrikeRow());
  });
  document.getElementById("again").addEventListener("click", function () {
    benchPlay().catch(function (error) {
      benchSay(["Не вышло: " + error.message]);
    });
  });
  document.getElementById("play").addEventListener("click", function () {
    benchPlay().catch(function (error) {
      benchSay(["Не вышло: " + error.message]);
    });
  });

  // Открываемся на обычном размене: пустой стенд пришлось бы сперва
  // заполнять, а первое, что хочет увидеть тестировщик, — что тут
  // вообще происходит
  benchFill(SCENARIOS[7]);
}

// Тема — по тому же правилу, что у карточки. Без этого стенд выходил
// наполовину светлым: заголовок на белом, а поля и сцена тёмные
function benchTheme() {
  var tg = window.Telegram && window.Telegram.WebApp;
  var dark = tg
    ? tg.colorScheme === "dark"
    : window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.body.classList.toggle("dark", !!dark);
  if (tg && tg.onEvent) {
    tg.onEvent("themeChanged", function () {
      document.body.classList.toggle("dark", tg.colorScheme === "dark");
    });
  }
  if (tg && tg.ready) tg.ready();
  if (tg && tg.expand) tg.expand();
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", function () {
    benchTheme();
    benchStart().catch(function (error) {
      benchSay(["Стенд не поднялся: " + error.message]);
    });
  });
}
