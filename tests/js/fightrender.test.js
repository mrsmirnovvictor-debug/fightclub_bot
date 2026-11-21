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
      damage: 12, armor: 0, hp_after: 88, ability: "", ability_title: "",
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
  const it = stage(turn([strike({ ability_title: "Двойка" })]));
  it.at(T.approach + 10);
  const shown = it.badge("left");
  ok(shown, "подпись есть");
  // Раскадровка хранит название как есть; заглавными его пишет уже
  // отрисовка — это её комиксный приём, а не данные
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

// ---------- нокаут не ждёт конца хода ----------

check("после смертельного удара боец падает сразу, а не ждёт пропуска", () => {
  // В ходу за смертельным ударом стоит пропуск: убитому нечем
  // отвечать. Пока нокаут клали на конец хода, сцена честно
  // отыгрывала этот пропуск, и мёртвый стоял с нулём на шкале
  const quick = stage(
    turn([strike({ damage: 100, hp_after: 0 })], {
      finished: true, winner_id: ME,
    }),
  );
  const waited = stage(
    turn(
      [
        strike({ damage: 100, hp_after: 0 }),
        strike({ attacker_id: FOE, defender_id: ME, outcome: "skip" }),
      ],
      { finished: true, winner_id: ME },
    ),
  );

  // Падение начинается в один и тот же миг, был пропуск или нет
  const falls = (it) => {
    for (let ms = 0; ms < 6000; ms += 5) {
      it.at(ms);
      if (it.look("right", 6).row === "ko") return ms;
    }
    return -1;
  };
  const a = falls(quick);
  const b = falls(waited);
  ok(a > 0, "падение нашлось");
  ok(
    Math.abs(a - b) < 20,
    "пропуск не отодвигает падение: " + a + " против " + b,
  );
  // И пропуска на экране нет ни в один миг: бой решён, ждать нечего.
  // Смотреть надо всю ленту: подпись живёт восемьсот миллисекунд и в
  // одной наугад взятой точке её легко не застать
  for (let ms = 0; ms < waited.length() + 1000; ms += 25) {
    waited.at(ms);
    for (const side of ["left", "right"]) {
      const said = waited.badge(side);
      ok(
        !said || said.text !== "пропуск",
        "о пропуске объявили на " + ms + " мс",
      );
    }
  }
});

check("падение идёт сразу за реакцией на удар", () => {
  const it = stage(
    turn([strike({ damage: 100, hp_after: 0 })], {
      finished: true, winner_id: ME,
    }),
  );
  const contact = T.approach + T.contact;
  it.at(contact + T.hitReaction - 20);
  same(it.look("right", 6).set, "hit", "ещё отыгрывает попадание");
  it.at(contact + T.hitReaction + 20);
  same(it.look("right", 6).row, "ko", "и сразу валится");
});

check("разом бьющие: падение ждёт обоих", () => {
  // Общее контактное окно и согласованный итог: вторую атаку нельзя
  // отменить из-за визуально более раннего нокаута
  const it = stage(
    turn(
      [
        strike({ damage: 100, hp_after: 0 }),
        strike({ attacker_id: FOE, defender_id: ME, damage: 7, hp_after: 93 }),
      ],
      { finished: true, winner_id: ME, resolution: "simultaneous" },
    ),
  );
  let fell = -1;
  for (let ms = 0; ms < 8000; ms += 5) {
    it.at(ms);
    if (it.look("right", 6).row === "ko") { fell = ms; break; }
  }
  ok(fell > 0, "падение нашлось");
  it.at(fell - 10);
  same(it.hp("left"), 93, "ответный удар успел дойти до падения");
});

check("победитель на полу не оказывается", () => {
  const it = stage(
    turn([strike({ damage: 100, hp_after: 0 })], {
      finished: true, winner_id: ME,
    }),
  );
  it.at(it.length() + 500);
  ok(it.look("right", 6).fallen, "проигравший лежит");
  ok(!it.look("left", 6).fallen, "победитель стоит");
});

// ---------- здоровье мимо ударов: лечение ----------

check("лечение между разменами доезжает до полосы", () => {
  // Игрок жмёт лечение на трёх очках здоровья. Удара при этом нет, и
  // пока раскадровке нечего доигрывать, она обязана поверить серверу
  const it = stage(turn([strike({ damage: 97, hp_after: 3 })]));
  it.at(it.length());
  same(it.hp("right"), 3, "размен довёл до трёх");

  ok(it.sync("right", 25), "число принято");
  same(it.hp("right"), 25, "полоса поднялась");
  const shown = it.damage("right");
  ok(shown, "и всплывающее число есть");
  ok(shown.healing, "помечено как лечение, а не урон");
  same(shown.damage, 22, "на столько и подлечился");
});

check("опрос не роняет полосу раньше удара", () => {
  // Опрос знает БУДУЩЕЕ: здоровье после удара, который ещё не показан
  const it = stage(turn([strike()]));
  it.at(10);
  ok(!it.sync("right", 88), "пока размен идёт, число не принимается");
  same(it.hp("right"), 100, "полоса держится до контакта");
  it.at(it.length());
  same(it.hp("right"), 88, "и падает в свой срок");
});

check("то же число второй раз ничего не двигает", () => {
  const it = stage(turn([strike()]));
  // Отходим подальше: число урона от самого удара висит свои 800 мс,
  // и на самом конце расписания оно ещё на экране
  it.at(it.length() + 1000);
  ok(!it.sync("right", 88), "сервер подтвердил то, что и так показано");
  ok(!it.damage("right"), "всплывающего числа из ниоткуда нет");
});

check("итог хода сверяется с сервером, а не только с ударами", () => {
  // Сервер говорит, что у бойца 40, хотя удары объясняют только 88:
  // между разменами он лечился, и судья это записал
  const it = stage(
    turn([strike()], { hp_after: { [String(FOE)]: 40, [String(ME)]: 100 } }),
  );
  it.at(it.length());
  same(it.hp("right"), 40, "поверили серверу, а не своему счёту");
  it.at(T.approach + T.contact + 1);
  same(it.hp("right"), 88, "но не раньше, чем прошёл сам удар");
});

check("сверка не трогает того, про кого сервер промолчал", () => {
  const it = stage(turn([strike()], { hp_after: {} }));
  it.at(it.length());
  same(it.hp("right"), 88, "осталось как посчитали удары");
  same(it.hp("left"), 100);
});

// ---------- отрисовка ----------

// Заглушка canvas: запоминает вызовы и местную систему координат.
// Проверять надо не красоту, а что кадр лёг по якорю и что правый
// боец зеркален — на картинке это видно, а в тесте без записи нет
function recorder() {
  let scale = { x: 1, y: 1 };
  let shift = { x: 0, y: 0 };
  let turned = 0;
  const stack = [];
  const calls = { images: [], shadows: [], bars: [], words: [], strokes: [] };
  return {
    calls: calls,
    ctx: {
      fillStyle: "",
      clearRect() {},
      beginPath() {},
      fill() {},
      save() {
        stack.push({ scale: scale, shift: shift, turned: turned });
      },
      restore() {
        const was = stack.pop();
        scale = was.scale;
        shift = was.shift;
        turned = was.turned;
      },
      translate(x, y) {
        shift = { x: shift.x + x * scale.x, y: shift.y + y * scale.y };
      },
      scale(x, y) {
        scale = { x: scale.x * x, y: scale.y * y };
      },
      rotate(angle) {
        turned += angle;
      },
      // Обводку пишем отдельно от заливки: это две разные вещи, и
      // проверкам нужно различать их, а не считать одну за другую
      strokeText(text, x, y) {
        calls.strokes.push({
          text: text, x: shift.x + x * scale.x, y: shift.y + y * scale.y,
          fill: this.strokeStyle, width: this.lineWidth, font: this.font,
          shadow: this.shadowColor, alpha: this.globalAlpha,
          scale: scale.x, turned: turned,
        });
      },
      ellipse(x, y, rx, ry) {
        calls.shadows.push({ x: x, y: y, rx: rx, ry: ry });
      },
      fillRect(x, y, w, h) {
        calls.bars.push({ x: x, y: y, w: w, h: h, fill: this.fillStyle });
      },
      // Ширина буквы примерно половина кегля — для проверки
      // наложения этого довольно, а точные метрики тут не нужны
      measureText(text) {
        const px = Number(/(\d+)px/.exec(this.font || "16px")[1]);
        return { width: text.length * px * 0.5 };
      },
      fillText(text, x, y) {
        calls.words.push({
          text: text, x: shift.x + x * scale.x, y: shift.y + y * scale.y,
          fill: this.fillStyle, font: this.font, align: this.textAlign,
          shadow: this.shadowColor, alpha: this.globalAlpha,
          scale: scale.x, turned: turned,
        });
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
    counter_zone: "head", defence_ability_title: "Уход",
  })]));
  const guessed = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
    defence_ability_title: "Уход",
  })]));
  const when = T.approach + T.dodge + 10;
  const a = paint(told, when).words.filter((one) => one.text === "КОНТРУДАР");
  const b = paint(guessed, when).words.filter((one) => one.text === "КОНТРУДАР");
  ok(a.length && b.length, "контрудар подписан в обоих случаях");
  ok(a[0].fill !== b[0].fill, "но цвет разный: " + a[0].fill + "/" + b[0].fill);
});

// ---------- подписи: слова, цвет и размер ----------

// Какого размера шрифт в подписи. Из строки вида "bold 30px sans-serif"
function size(font) {
  return Number(/(\d+)px/.exec(font)[1]);
}

check("защита подписана словом, если приёма не было", () => {
  const cases = {
    block: "БЛОК",
    dodge: "УВОРОТ",
  };
  for (const [outcome, word] of Object.entries(cases)) {
    const it = stage(turn([strike({ outcome: outcome, hp_after: 100 })]));
    const drawn = paint(it, T.approach + 10).words.map((one) => one.text);
    ok(drawn.indexOf(word) >= 0, outcome + " подписан «" + word + "»: " + drawn);
  }
  // Контрудар — позже, после уворота
  const counter = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
    counter_zone: "head",
  })]));
  const said = paint(counter, T.approach + T.dodge + 10).words.map((o) => o.text);
  ok(said.indexOf("КОНТРУДАР") >= 0, said.join(","));
});

check("названный приём вытесняет общее слово", () => {
  const it = stage(turn([strike({
    outcome: "block", hp_after: 100, defence_ability_title: "Железная защита",
  })]));
  const drawn = paint(it, T.approach + 10).words.map((one) => one.text);
  ok(drawn.indexOf("ЖЕЛЕЗНАЯ ЗАЩИТА") >= 0, drawn.join(","));
  ok(drawn.indexOf("БЛОК") < 0, "двух подписей разом нет: " + drawn.join(","));
});

check("обычный удар синим, критический красным, лечение зелёным", () => {
  const when = T.approach + T.contact + 10;
  const plain = paint(stage(turn([strike()])), when).words
    .filter((one) => one.text[0] === "\u2212")[0];
  const crit = paint(stage(turn([strike({ outcome: "crit" })])), when).words
    .filter((one) => one.text[0] === "\u2212")[0];

  const heal = stage(turn([strike({ damage: 97, hp_after: 3 })]));
  heal.at(heal.length() + 2000);
  heal.sync("right", 25);
  const green = paint(heal, heal.length() + 2000).words
    .filter((one) => one.text[0] === "+")[0];

  ok(plain && crit && green, "все три числа показаны");
  const colours = [plain.fill, crit.fill, green.fill];
  same(new Set(colours).size, 3, "три разных цвета: " + colours.join(" "));
  // Синий: синего в нём больше, чем красного. Красный — наоборот
  const channel = (hex, at) => parseInt(hex.slice(at, at + 2), 16);
  ok(channel(plain.fill, 5) > channel(plain.fill, 1), "обычный синий: " + plain.fill);
  ok(channel(crit.fill, 1) > channel(crit.fill, 5), "критический красный: " + crit.fill);
  ok(channel(green.fill, 3) > channel(green.fill, 1), "лечение зелёное: " + green.fill);
});

check("подписи крупные: читаются на телефоне", () => {
  const when = T.approach + T.contact + 10;
  const drawn = paint(stage(turn([strike({ ability_title: "Двойка" })])), when);
  const number = drawn.words.filter((one) => one.text[0] === "\u2212")[0];
  const named = drawn.words.filter((one) => one.text === "ДВОЙКА")[0];
  const hud = drawn.words.filter((one) => one.text === "Тайлер")[0];

  ok(size(number.font) >= 24, "число урона " + number.font);
  ok(named && size(named.font) >= 20, "подпись приёма " + (named || {}).font);
  ok(size(hud.font) >= 17, "имя в сцене " + hud.font);

  const crit = paint(stage(turn([strike({ outcome: "crit" })])), when).words
    .filter((one) => one.text[0] === "\u2212")[0];
  ok(size(crit.font) > size(number.font), "критический крупнее обычного");
});

check("подписи не вылезают за края сцены", () => {
  const when = T.approach + T.contact + 10;
  const drawn = paint(stage(turn([strike({ ability_title: "Двойка" })])), when);
  for (const one of drawn.words) {
    ok(one.y > 0 && one.y < scene.logicalSize.height,
      "«" + one.text + "» по вертикали на сцене, y=" + one.y);
    ok(one.x > 0 && one.x < scene.logicalSize.width,
      "«" + one.text + "» по горизонтали на сцене, x=" + one.x);
  }
});

check("имя и остаток здоровья не налезают друг на друга", () => {
  // Прозвища бывают длинные. «Растафарайчик» при подписи в полтора
  // раза крупнее перекрывал «40/100» — на экране читалось
  // «Растафарай40/100»
  const it = stage(turn([strike()]));
  it.cast(
    { id: ME, name: "Растафарайчик-Длинноимённый", line: "warrior",
      hp: 40, maxHp: 100 },
    { id: FOE, name: "М", line: "tank", hp: 90, maxHp: 95 },
  );
  const tape = recorder();
  it.at(10);
  sandbox.drawStage(tape.ctx, it, shelf(), scene);

  const px = (f) => Number(/(\d+)px/.exec(f)[1]);
  const span = (one) => {
    const w = one.text.length * px(one.font) * 0.5;
    if (one.align === "left") return [one.x, one.x + w];
    if (one.align === "right") return [one.x - w, one.x];
    return [one.x - w / 2, one.x + w / 2];
  };

  const pairs = [["40/100", 0], ["90/95", 0]];
  for (const [number] of pairs) {
    const said = tape.calls.words.find((one) => one.text === number);
    ok(said, "остаток «" + number + "» показан целиком, без подрезки");
    const numbers = span(said);
    // Имя — та подпись на той же строке, что не число
    const name = tape.calls.words.find(
      (one) => one.y === said.y && one !== said && !/^\d+\/\d+$/.test(one.text)
    );
    ok(name, "имя рядом с «" + number + "» есть");
    const names = span(name);
    ok(
      names[1] <= numbers[0] + 0.5 || numbers[1] <= names[0] + 0.5,
      "«" + name.text + "» " + names.map(Math.round) +
        " и «" + number + "» " + numbers.map(Math.round) + " пересеклись",
    );
  }
  // Длинное имя подрезано, короткое — нет
  ok(
    tape.calls.words.some((one) => one.text.endsWith("…")),
    "длинное прозвище подрезано многоточием",
  );
  ok(
    tape.calls.words.some((one) => one.text === "М"),
    "короткое оставлено как есть",
  );
});

check("подпись события стоит по середине шкалы своего бойца", () => {
  // Над фигурами двое в размене сходятся вплотную, и подпись
  // оказывалась между ними — непонятно, чья
  const it = stage(turn([strike({ outcome: "block", hp_after: 100 })]));
  const drawn = paint(it, T.approach + 40);
  const said = drawn.words.find((one) => one.text === "БЛОК");
  ok(said, "блок подписан: " + drawn.words.map((o) => o.text).join(","));
  same(said.align, "center", "подпись центрирована");

  // Шкала защищавшегося — правая: её подложка это самый широкий
  // прямоугольник в правой половине
  const bars = drawn.bars.filter((one) => one.x > scene.logicalSize.width / 2);
  ok(bars.length, "шкала справа есть");
  const bar = bars[0];
  same(
    Math.round(said.x),
    Math.round(bar.x + bar.w / 2),
    "подпись по середине шкалы, а не над фигурой",
  );
  ok(
    Math.abs(said.x - scene.fighterAnchors.right.x) > 1,
    "и это не якорь фигуры",
  );
  ok(said.y > bar.y, "под шкалой, а не поверх неё");
});

check("подпись события синяя, догадка — приглушённее, но тем же тоном", () => {
  const told = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
    counter_zone: "head",
  })]));
  const guessed = stage(turn([strike({
    outcome: "counter", hp_after: 100, counter: 9, counter_hp_after: 91,
  })]));
  const when = T.approach + T.dodge + 40;
  const a = paint(told, when).words.find((one) => one.text === "КОНТРУДАР");
  const b = paint(guessed, when).words.find((one) => one.text === "КОНТРУДАР");
  ok(a && b, "контрудар подписан в обоих случаях");

  const channel = (hex, at) => parseInt(hex.slice(at, at + 2), 16);
  for (const one of [a, b]) {
    // «Больше синего, чем красного» мало: у серого #cfd2dc синего тоже
    // чуть больше. Синий должен преобладать заметно
    const blue = channel(one.fill, 5) - channel(one.fill, 1);
    ok(blue >= 40, "синяя с запасом (" + blue + "): " + one.fill);
  }
  ok(channel(a.fill, 5) > channel(b.fill, 5), "догадка приглушена: "
    + a.fill + " против " + b.fill);
});

// ---------- комиксный вид ----------

check("у надписи есть обводка, и она темнее и толще самих букв", () => {
  const when = T.approach + T.contact + 20;
  const drawn = paint(stage(turn([strike()])), when);
  const fill = drawn.words.find((one) => one.text[0] === "\u2212");
  const line = drawn.strokes.find((one) => one.text[0] === "\u2212");
  ok(fill && line, "число и нарисовано, и обведено");
  same(line.text, fill.text, "обводка по тем же буквам");
  ok(line.width >= 3, "обводка заметная: " + line.width);
  const bright = (hex) =>
    parseInt(hex.slice(1, 3), 16) + parseInt(hex.slice(3, 5), 16) +
    parseInt(hex.slice(5, 7), 16);
  ok(bright(line.fill) < bright(fill.fill), "обводка темнее: "
    + line.fill + " против " + fill.fill);
});

check("тень падает от обводки, а не от заливки", () => {
  // Силуэт у обводки шире: тень получается от всей надписи, а не от
  // тонких букв. На заливке её гасим, иначе удвоится и замылит
  const when = T.approach + T.contact + 20;
  const drawn = paint(stage(turn([strike()])), when);
  const line = drawn.strokes.find((one) => one.text[0] === "\u2212");
  const fill = drawn.words.find((one) => one.text[0] === "\u2212");
  ok(line.shadow && line.shadow !== "transparent", "у обводки тень: " + line.shadow);
  same(fill.shadow, "transparent", "у заливки тени нет");
});

check("надпись выскакивает: сперва крупнее цели, потом садится", () => {
  const it = stage(turn([strike()]));
  const at = T.approach + T.contact;
  const sizes = [2, 60, 110, 300].map((after) => {
    const one = paint(it, at + after).words.find((w) => w.text[0] === "\u2212");
    return one.scale;
  });
  ok(sizes[0] < 0.8, "в первый миг мельче: " + sizes[0]);
  ok(Math.max.apply(null, sizes) > 1.05, "перелетает цель: " + sizes.join(" "));
  ok(Math.abs(sizes[3] - 1) < 0.01, "и садится на место: " + sizes[3]);
});

check("надпись всплывает и тает, а не висит столбом", () => {
  const it = stage(turn([strike()]));
  const at = T.approach + T.contact;
  const early = paint(it, at + 20).words.find((w) => w.text[0] === "\u2212");
  const late = paint(it, at + 700).words.find((w) => w.text[0] === "\u2212");
  ok(late.y < early.y - 10, "поднялась: " + early.y + " → " + late.y);
  ok(late.alpha < early.alpha, "и тает: " + early.alpha + " → " + late.alpha);
  same(early.alpha, 1, "но не сразу");
});

check("надписи наклонены, и бойцы — в разные стороны", () => {
  const it = stage(turn([
    strike(),
    strike({ attacker_id: FOE, defender_id: ME, hp_after: 90 }),
  ]));
  const left = paint(it, it.length() - 60).words
    .find((one) => one.text[0] === "\u2212" && one.turned !== 0);
  const at = T.approach + T.contact + 20;
  const right = paint(it, at).words.find((one) => one.text[0] === "\u2212");
  ok(right && right.turned !== 0, "наклон есть: " + (right || {}).turned);
  ok(left && Math.sign(left.turned) !== Math.sign(right.turned),
    "в разные стороны: " + left.turned + " и " + right.turned);
});

check("критический наклонён сильнее обычного", () => {
  const when = T.approach + T.contact + 20;
  const plain = paint(stage(turn([strike()])), when).words
    .find((one) => one.text[0] === "\u2212");
  const crit = paint(stage(turn([strike({ outcome: "crit" })])), when).words
    .find((one) => one.text[0] === "\u2212");
  ok(Math.abs(crit.turned) > Math.abs(plain.turned),
    "крит круче наклонён: " + crit.turned + " против " + plain.turned);
});

check("слова событий написаны заглавными", () => {
  const it = stage(turn([strike({ outcome: "block", hp_after: 100 })]));
  const drawn = paint(it, T.approach + 40).words.map((one) => one.text);
  ok(drawn.indexOf("БЛОК") >= 0, drawn.join(","));
  ok(drawn.indexOf("блок") < 0, "строчными не пишем");
});

check("слова событий тоже обведены и наклонены", () => {
  const it = stage(turn([strike({ outcome: "dodge", hp_after: 100 })]));
  const drawn = paint(it, T.approach + 40);
  const line = drawn.strokes.find((one) => one.text === "УВОРОТ");
  const fill = drawn.words.find((one) => one.text === "УВОРОТ");
  ok(line, "обводка есть");
  ok(line.width >= 3, "и она заметная: " + line.width);
  ok(fill.turned !== 0, "наклон есть: " + fill.turned);
});

check("подпись не наезжает на шкалу здоровья, пока всплывает", () => {
  // Надпись поднимается по ходу жизни. Начни она вплотную к шкале — к
  // концу села бы на неё буквами
  const it = stage(turn([strike({ outcome: "block", hp_after: 100 })]));
  const at = T.approach;
  for (const after of [10, 200, 400, 600, 780]) {
    const drawn = paint(it, at + after);
    const said = drawn.words.find((one) => one.text === "БЛОК");
    if (!said) continue;
    const size = Number(/(\d+)px/.exec(said.font)[1]) * said.scale;
    const top = said.y - size;  // буквы идут вверх от базовой линии
    // Шкалы — прямоугольники подложки; берём самый низкий их край
    const bottom = Math.max.apply(
      null, drawn.bars.map((one) => one.y + one.h),
    );
    ok(top >= bottom, "через " + after + " мс верх букв " + Math.round(top)
      + " против низа шкалы " + Math.round(bottom));
  }
});

if (failed) {
  console.log("пало проверок: " + failed);
  process.exit(1);
}
console.log("все проверки прошли");
