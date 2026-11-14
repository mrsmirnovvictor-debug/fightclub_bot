// Проверки раскадровки сцены. Запускаются из pytest настоящим node.
//
// Ходы прогоняются через настоящий `fightQueue`, а не выдуманные
// события: иначе проверялась бы договорённость адаптера с самим собой.
// Тайминги берутся из настоящего манифеста бойца.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..", "..");
const sandbox = {};
vm.createContext(sandbox);
for (const name of ["fightscene.js", "fightrender.js"]) {
  vm.runInContext(
    fs.readFileSync(path.join(root, "bot", "webapp", "static", name), "utf8"),
    sandbox,
  );
}
const { fightQueue, fightStage, DODGE_ROWS } = sandbox;

const book = JSON.parse(
  fs.readFileSync(
    path.join(root, "bot", "webapp", "static", "fight", "warrior", "manifest.json"),
    "utf8",
  ),
);
const T = book.timingMs;

const ME = 1;
const FOE = 2;

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
  if (!truth) throw new Error(why || "ожидалось истинное");
}

function same(got, want, why) {
  if (got !== want) {
    throw new Error((why || "не совпало") + ": " + got + " вместо " + want);
  }
}

// Стенд: ход через адаптер, затем в раскадровку
function stage(turn, extra) {
  const queue = fightQueue().reset("бой-1");
  queue.push(turn);
  const it = fightStage(Object.assign({ timing: T }, extra || {}));
  it.cast(
    { id: ME, name: "Тайлер", line: "warrior", hp: 100, maxHp: 100 },
    { id: FOE, name: "Марла", line: "tank", hp: 100, maxHp: 100 },
  );
  it.feed(queue.drain());
  return it;
}

function strike(over) {
  return Object.assign(
    {
      attacker_id: ME, defender_id: FOE, outcome: "hit", zone: "chest",
      damage: 12, armor: 0, hp_after: 88, ability: "",
    },
    over || {},
  );
}

function turn(strikes, over) {
  return Object.assign(
    { number: 1, strikes: strikes, resolution: "sequential", hp_after: {} },
    over || {},
  );
}

console.log("раскадровка сцены боя:");

check("здоровье снимается в контакт, а не в начале замаха", () => {
  const it = stage(turn([strike()]));
  const contact = T.approach + T.contact;
  it.at(contact - 20);
  same(it.hp("right"), 100, "до контакта полоса полная");
  it.at(contact + 1);
  same(it.hp("right"), 88, "в контакт — по серверу");
});

check("чистый блок не снимает здоровья вовсе", () => {
  const it = stage(turn([strike({ outcome: "block", hp_after: 100 })]));
  it.at(it.length());
  same(it.hp("right"), 100);
  ok(!it.damage("right"), "и всплывающего числа нет");
});

check("уворот не снимает здоровья", () => {
  const it = stage(turn([strike({ outcome: "dodge", hp_after: 100 })]));
  it.at(it.length());
  same(it.hp("right"), 100);
});

check("пробитый блок доходит до тела позже чистого удара", () => {
  const clean = stage(turn([strike()]));
  const broken = stage(turn([strike({ outcome: "break" })]));
  const when = T.approach + T.contact;
  clean.at(when + 1);
  broken.at(when + 1);
  same(clean.hp("right"), 88, "чистый удар дошёл");
  same(broken.hp("right"), 100, "пробитие ещё держат");
  broken.at(when + T.breakHold + 1);
  same(broken.hp("right"), 88, "и дошло через выдержку");
});

check("защита стоит с начала замаха, а не в момент удара", () => {
  const it = stage(turn([strike({ outcome: "block", hp_after: 100 })]));
  it.at(T.approach + 5);
  same(it.look("right", 6).set, "block", "блок уже выставлен");
});

check("атакующий сходится на удар и возвращается", () => {
  const it = stage(turn([strike()]));
  it.at(1);
  const start = it.look("left", 6).shift;
  it.at(T.approach - 5);
  const near = it.look("left", 6).shift;
  ok(near > start, "сошёлся: " + start + " → " + near);
  it.at(it.length() - 1);
  const back = it.look("left", 6).shift;
  ok(back < near, "и отошёл обратно: " + back);
});

check("между уворотом и контрударом никто не расходится", () => {
  const it = stage(
    turn([
      strike({
        outcome: "counter", hp_after: 100, counter: 9,
        counter_hp_after: 91, counter_zone: "head",
      }),
    ]),
  );
  // Контрудар идёт сразу после уворота — защищавшийся бьёт с той же
  // дистанции, и всё это время он близко
  const after = T.approach + T.dodge;
  it.at(after + 5);
  const pose = it.look("right", 6);
  same(pose.set, "attack", "отвечает, а не отходит");
  ok(pose.shift > 0, "и делает это вблизи: " + pose.shift);
  it.at(it.length() - 1);
  same(it.hp("left"), 91, "ответ снял здоровье по серверу");
});

check("нокаут кладёт проигравшего и оставляет лежать", () => {
  const it = stage(
    turn([strike({ damage: 100, hp_after: 0 })], {
      finished: true, winner_id: ME,
    }),
  );
  ok(it.over(), "бой помечен законченным");
  it.at(it.length() - 1);
  same(it.look("right", 6).row, "ko", "падает");
  it.at(it.length() + 10000);
  const late = it.look("right", 6);
  ok(late.fallen, "и спустя время всё ещё лежит");
  same(late.row, "ko");
  ok(!it.look("left", 6).fallen, "победитель стоит");
});

check("вне своих отрезков боец дышит в стойке", () => {
  const it = stage(turn([strike()]));
  it.at(it.length() + 100);
  const a = it.look("left", 6);
  same(a.set, "move");
  same(a.row, "stance_reference");
  it.at(it.length() + 100 + T.swayPeriod / 2);
  const b = it.look("left", 6);
  ok(a.frame !== b.frame, "кадр стойки меняется: " + a.frame + "/" + b.frame);
});

check("спокойный режим не качает и не ведёт плавно", () => {
  const it = stage(turn([strike()]), { calm: true });
  it.at(it.length() + 100);
  const a = it.look("left", 6);
  it.at(it.length() + 100 + T.swayPeriod / 2);
  same(it.look("left", 6).frame, a.frame, "стойка не качается");
  it.at(5);
  same(it.look("left", 6).shift, 62, "и сразу на дистанции удара");
});

check("приём подписан, и подпись не висит весь бой", () => {
  const it = stage(turn([strike({ ability: "Двойка" })]));
  it.at(T.approach + 10);
  const shown = it.badge("left");
  ok(shown, "подпись есть");
  same(shown.text, "Двойка");
  it.at(T.approach + 10 + 900);
  ok(!it.badge("left"), "и ушла");
});

check("два удара в ходу идут друг за другом, а не поверх", () => {
  const one = stage(turn([strike()]));
  const two = stage(
    turn([strike(), strike({ attacker_id: FOE, defender_id: ME, hp_after: 90 })]),
  );
  ok(two.length() > one.length() * 1.5, "второй размен занял своё время");
  // На первом контакте бьёт только первый
  two.at(T.approach + T.contact + 1);
  same(two.hp("left"), 100, "второй ещё не бил");
  two.at(two.length() - 1);
  same(two.hp("left"), 90, "а потом ударил");
});

check("неизвестный исход не ломает раскадровку", () => {
  const it = stage(turn([strike({ outcome: "непонятно" })]));
  ok(it.length() > 0, "время под него всё равно отведено");
  it.at(10);
  ok(it.badge("left"), "и о нём сказано словами");
  same(it.hp("right"), 100, "здоровья при этом не снимает");
});

check("пропуск хода — это ожидание, а не удар", () => {
  const it = stage(turn([strike({ outcome: "skip" })]));
  it.at(10);
  same(it.badge("left").text, "пропуск");
  it.at(it.length() - 1);
  same(it.hp("right"), 100);
  same(it.look("left", 6).set, "move", "никто никуда не бил");
});

check("уворот от удара в ноги и в голову — разные ряды", () => {
  const high = stage(turn([strike({ outcome: "dodge", zone: "head", hp_after: 100 })]));
  const low = stage(turn([strike({ outcome: "dodge", zone: "legs", hp_after: 100 })]));
  high.at(T.approach + 10);
  low.at(T.approach + 10);
  same(high.look("right", 6).row, "upper");
  same(low.look("right", 6).row, "legs");
  // Ряды должны быть те, что есть в манифесте. Без карты проверка
  // была бы пустой, поэтому сперва убеждаемся, что она вообще есть
  ok(DODGE_ROWS && Object.keys(DODGE_ROWS).length, "карта уворотов объявлена");
  for (const row of Object.values(DODGE_ROWS)) {
    ok(book.rowMap.dodge.indexOf(row) >= 0, "ряд " + row + " есть в манифесте");
  }
  for (const zone of book.zones) {
    ok(DODGE_ROWS[zone], "зона " + zone + " в карте есть");
  }
});

check("раскадровка доиграна только в конце, а не сразу", () => {
  const it = stage(turn([strike()]));
  it.at(0);
  ok(!it.done(), "в начале не доиграна");
  it.at(it.length() / 2);
  ok(!it.done(), "в середине тоже");
  it.at(it.length());
  ok(it.done(), "а в конце — да");
});

check("все зоны движка умеют уворот", () => {
  for (const zone of book.zones) {
    const it = stage(turn([strike({ outcome: "dodge", zone: zone, hp_after: 100 })]));
    it.at(T.approach + 10);
    const row = it.look("right", 6).row;
    ok(
      book.rowMap.dodge.indexOf(row) >= 0,
      "зона " + zone + " дала ряд " + row,
    );
  }
});


check("ход, пришедший после кулдауна, не проигрывается мгновенно", () => {
  // В игре между ходами стоит кулдаун в десятки секунд. К приходу
  // второго хода расписание первого давно кончилось, а часы идут —
  // и если новое ставить от старого курсора, оно окажется в прошлом
  const queue = fightQueue().reset("бой-1");
  const it = fightStage({ timing: T });
  it.cast(
    { id: ME, name: "Тайлер", line: "warrior", hp: 100, maxHp: 100 },
    { id: FOE, name: "Марла", line: "tank", hp: 100, maxHp: 100 },
  );
  queue.push(turn([strike()]));
  it.feed(queue.drain());
  const first = it.length();

  // Прошло полминуты: человек выбирал удар
  it.at(first + 30000);
  ok(it.done(), "первый размен давно доигран");

  queue.push(turn([strike({ hp_after: 70 })], { number: 2 }));
  it.feed(queue.drain());
  ok(
    it.length() > first + 30000,
    "второй размен поставлен в будущее: " + it.length(),
  );
  ok(!it.done(), "и ещё не доигран");
  // Здоровье упадёт не сразу, а в свой контакт
  same(it.hp("right"), 88, "пока держится счёт первого размена");
  it.at(first + 30000 + T.approach + T.contact + 1);
  same(it.hp("right"), 70, "и снялось в контакт второго");
});

// ---------- отрисовка ----------

// Заглушка canvas: запоминает вызовы и местную систему координат.
// Проверять надо не красоту, а что кадр лёг по якорю и что правый
// боец зеркален — на картинке это видно, а в тесте без записи нет
function recorder() {
  let scale = { x: 1, y: 1 };
  let shift = { x: 0, y: 0 };
  const stack = [];
  const calls = { images: [], shadows: [], bars: [], words: [] };
  return {
    calls: calls,
    ctx: {
      fillStyle: "",
      clearRect() {},
      beginPath() {},
      fill() {},
      save() {
        stack.push({ scale: scale, shift: shift });
      },
      restore() {
        const was = stack.pop();
        scale = was.scale;
        shift = was.shift;
      },
      translate(x, y) {
        shift = { x: shift.x + x * scale.x, y: shift.y + y * scale.y };
      },
      scale(x, y) {
        scale = { x: scale.x * x, y: scale.y * y };
      },
      ellipse(x, y, rx, ry) {
        calls.shadows.push({ x: x, y: y, rx: rx, ry: ry });
      },
      fillRect(x, y, w, h) {
        calls.bars.push({ x: x, y: y, w: w, h: h, fill: this.fillStyle });
      },
      fillText(text, x, y) {
        calls.words.push({ text: text, x: x, y: y, fill: this.fillStyle,
          font: this.font, align: this.textAlign });
      },
      drawImage(image, sx, sy, sw, sh, dx, dy, dw, dh) {
        if (arguments.length <= 5) {
          calls.images.push({ image: image, background: true });
          return;
        }
        calls.images.push({
          image: image,
          source: { x: sx, y: sy, w: sw, h: sh },
          // Куда кадр лёг на самом деле, с учётом сдвига и зеркала
          at: { x: shift.x + dx * scale.x, y: shift.y + dy * scale.y },
          size: { w: dw, h: dh },
          mirrored: scale.x < 0,
        });
      },
    },
  };
}

const scene = JSON.parse(
  fs.readFileSync(
    path.join(root, "bot", "webapp", "static", "fight", "background.json"),
    "utf8",
  ),
);

// Загрузчик на стенде: настоящие манифесты, картинки — метки
function shelf() {
  const books = {};
  for (const line of ["warrior", "tank"]) {
    books[line] = JSON.parse(
      fs.readFileSync(
        path.join(root, "bot", "webapp", "static", "fight", line, "manifest.json"),
        "utf8",
      ),
    );
  }
  return {
    backdrop: () => "фон",
    sheet: (line, name) => books[line].sets[name],
    atlas: (line, name) => line + "/" + name,
    row: (line, name, what) => (books[line].rowMap[name] || []).indexOf(what),
    frame(line, name, row, number) {
      const frames = books[line].sets[name].frames;
      for (const one of frames) {
        if (one.row === row && one.frame === number) return one;
      }
      return null;
    },
  };
}

function paint(it, when) {
  const tape = recorder();
  it.at(when);
  sandbox.drawStage(tape.ctx, it, shelf(), scene);
  return tape.calls;
}

check("фон кладётся до бойцов, и ровно один раз", () => {
  const drawn = paint(stage(turn([strike()])), 10);
  const backs = drawn.images.filter((one) => one.background);
  same(backs.length, 1, "фон один");
  same(drawn.images[0].background, true, "и он первый");
});

check("правый боец зеркален, левый — нет", () => {
  const drawn = paint(stage(turn([strike()])), 10);
  const fighters = drawn.images.filter((one) => !one.background);
  same(fighters.length, 2, "оба бойца на месте");
  same(fighters[0].mirrored, false, "левый как есть");
  same(fighters[1].mirrored, true, "правый зеркален");
});

// Какой кадр раскадровка просит в этот момент. Берём у неё, а не
// выписываем номер руками: иначе тест сверял бы отрисовку не с позой,
// а с моим представлением о расписании
function asked(it, side, line, when) {
  it.at(when);
  const pose = it.look(side, shelf().sheet(line, "move").columns);
  const sheet = shelf().sheet(line, pose.set);
  const fresh = it.look(side, sheet.columns);
  const row = shelf().row(line, fresh.set, fresh.row);
  return { pose: fresh, frame: shelf().frame(line, fresh.set, row, fresh.frame) };
}

check("кадр встаёт по своему якорю, а не по углу картинки", () => {
  const it = stage(turn([strike()]));
  const want = asked(it, "left", "warrior", 10);
  const mine = paint(it, 10).images.filter((one) => !one.background)[0];
  const anchor = scene.fighterAnchors.left;
  same(mine.at.y, anchor.y - want.frame.pivot.y, "по вертикали — на полу");
  same(
    mine.at.x,
    anchor.x + want.pose.shift - want.frame.pivot.x,
    "по горизонтали — от якоря на величину сближения",
  );
});

check("кадр рисуется логическим размером, а не размером обрезка", () => {
  const it = stage(turn([strike()]));
  const want = asked(it, "left", "warrior", 10);
  const mine = paint(it, 10).images.filter((one) => !one.background)[0];
  same(mine.size.w, want.frame.drawSize.w);
  same(mine.size.h, want.frame.drawSize.h);
  ok(mine.size.w !== mine.source.w, "и это разные числа");
  same(mine.source.w, want.frame.rect.w, "обрезок — из манифеста");
});

check("тень своя у каждого, и у лежачего она шире", () => {
  const up = paint(stage(turn([strike()])), 10);
  same(up.shadows.length, 2, "по тени на бойца");
  same(up.shadows[0].y, scene.floorY + 2, "тень на полу");
  const ko = stage(turn([strike({ damage: 100, hp_after: 0 })], {
    finished: true, winner_id: ME,
  }));
  const down = paint(ko, ko.length() + 100);
  const wide = down.shadows[1].rx;
  ok(wide > up.shadows[1].rx, "лежачий шире: " + wide);
});

check("тень ездит вместе с бойцом", () => {
  const it = stage(turn([strike()]));
  const near = paint(it, T.approach - 5).shadows[0].x;
  const home = paint(it, it.length() + 100).shadows[0].x;
  ok(near > home, "сошёлся — и тень с ним: " + home + " → " + near);
});

// ---------- имена и здоровье в сцене ----------

check("имя и здоровье стоят внутри безопасной зоны фона", () => {
  const drawn = paint(stage(turn([strike()])), 10);
  ok(drawn.words.length >= 4, "по имени и по числу на каждого");
  for (const one of drawn.words) {
    ok(
      one.y > 0 && one.y < scene.logicalSize.height,
      "надпись «" + one.text + "» на сцене, y=" + one.y,
    );
  }
  for (const one of drawn.bars) {
    ok(
      one.x >= scene.safeArea.left - 1 &&
        one.x + one.w <= scene.safeArea.right + 1,
      "полоса в безопасной зоне: " + one.x + "+" + one.w,
    );
  }
  const names = drawn.words.map((one) => one.text);
  ok(names.indexOf("Тайлер") >= 0 && names.indexOf("Марла") >= 0, names.join(","));
});

check("полоса здоровья укорачивается ровно по остатку", () => {
  const it = stage(turn([strike()]));
  const before = paint(it, 10).bars;
  const after = paint(it, it.length() - 1).bars;
  // Пара на бойца: подложка и остаток. Подложка не меняется
  same(before[0].w, after[0].w, "подложка на месте");
  ok(after[3].w < before[3].w, "остаток справа упал: " + after[3].w);
  const full = before[2].w;
  ok(
    Math.abs(after[3].w - full * 0.88) < 1,
    "и ровно на 88/100: " + after[3].w + " от " + full,
  );
});

check("цвет полосы говорит об опасности, а не о том, кто свой", () => {
  const healthy = paint(stage(turn([strike()])), 10).bars;
  same(healthy[1].fill, healthy[3].fill, "оба целы — цвет один");
  const hurt = stage(turn([strike({ damage: 90, hp_after: 10 })]));
  const low = paint(hurt, hurt.length() - 1).bars;
  ok(low[3].fill !== healthy[3].fill, "почти добитый — другим цветом");
});

check("критический урон виден отдельно от обычного", () => {
  const plain = stage(turn([strike()]));
  const crit = stage(turn([strike({ outcome: "crit" })]));
  const when = T.approach + T.contact + 10;
  const a = paint(plain, when).words.filter((one) => one.text[0] === "\u2212")[0];
  const b = paint(crit, when).words.filter((one) => one.text[0] === "\u2212")[0];
  ok(a && b, "число урона показано в обоих случаях");
  same(a.text, "\u221212");
  ok(b.font !== a.font, "крит крупнее: " + b.font + " против " + a.font);
  ok(b.fill !== a.fill, "и другим цветом");
});

check("догаданная зона контрудара подписана тусклее", () => {
  const told = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
    counter_zone: "head", defence_ability: "Уход",
  })]));
  const guessed = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
    defence_ability: "Уход",
  })]));
  const when = T.approach + T.dodge + 10;
  const a = paint(told, when).words.filter((one) => one.text === "ответ");
  const b = paint(guessed, when).words.filter((one) => one.text === "ответ");
  ok(a.length && b.length, "ответ подписан в обоих случаях");
  ok(a[0].fill !== b[0].fill, "но цвет разный: " + a[0].fill + "/" + b[0].fill);
});

if (failed) {
  console.log("пало проверок: " + failed);
  process.exit(1);
}
console.log("все проверки прошли");
