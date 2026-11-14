// Проверки загрузчика графики сцены. Запускаются из pytest
// (`tests/test_fightscene.py`) настоящим node.
//
// Манифесты берутся настоящие, из статики приложения: загрузчик должен
// работать на том, что реально опубликовано, а не на выдумке. А вот
// сеть подменена — картинки здесь не нужны, нужен порядок и учёт.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..", "..");
const source = fs.readFileSync(
  path.join(root, "bot", "webapp", "static", "fightassets.js"),
  "utf8",
);
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const fightAssets = sandbox.fightAssets;

const STATIC = path.join(root, "bot", "webapp", "static", "fight");
const BASE = "https://example.test/combat/2026-11-12-v1/";

let failed = 0;

function check(what, how) {
  return Promise.resolve()
    .then(how)
    .then(
      () => console.log("  ок   " + what),
      (error) => {
        failed += 1;
        console.log("  ПАЛО " + what + ": " + error.message);
      },
    );
}

function ok(truth, why) {
  if (!truth) throw new Error(why || "ожидалось истинное");
}

function same(got, want, why) {
  if (got !== want) {
    throw new Error((why || "не совпало") + ": " + got + " вместо " + want);
  }
}

// Сеть на стенде: json читаем с диска, картинки считаем
function stand(over) {
  const asked = [];
  const broken = (over || {}).broken || [];
  return {
    asked,
    assets: fightAssets({
      base: BASE,
      statics: "static/fight/",
      fetchJson(url) {
        const file = url.replace("static/fight/", "");
        return Promise.resolve(
          JSON.parse(fs.readFileSync(path.join(STATIC, file), "utf8")),
        );
      },
      loadImage(url) {
        asked.push(url);
        if (broken.some((one) => url.indexOf(one) >= 0)) {
          return Promise.reject(new Error("не приехала картинка"));
        }
        return Promise.resolve({ url: url });
      },
    }),
  };
}

const run = [];

console.log("загрузчик графики сцены:");

run.push(
  check("на бой берутся только образы участников, а не все четыре", async () => {
    const it = stand();
    await it.assets.open(["warrior", "tank"]);
    await it.assets.prepare();
    ok(
      it.asked.every((url) => !/trickster|assassin/.test(url)),
      "в бою их нет — и грузить их нечего",
    );
    same(it.assets.lines().join(","), "warrior,tank");
  }),
);

run.push(
  check("третью линию на один бой не берут", async () => {
    const it = stand();
    let said = "";
    await it.assets.open(["warrior", "tank", "assassin"]).catch((error) => {
      said = error.message;
    });
    ok(said.indexOf("не больше") >= 0, "отказ назван прямо: " + said);
  }),
);

run.push(
  check("критический путь грузится раньше уворотов", async () => {
    const it = stand();
    await it.assets.open(["warrior", "assassin"]);
    await it.assets.prepare();
    ok(
      it.asked.every((url) => url.indexOf("dodge") < 0),
      "уворотов в критическом пути нет",
    );
    ok(it.asked.some((url) => url.indexOf("move") >= 0), "стойка есть");
    ok(it.asked.some((url) => url.indexOf("background") >= 0), "фон есть");
    const before = it.asked.length;
    await it.assets.finish();
    ok(it.asked.length > before, "увороты догрузились потом");
    ok(it.asked.some((url) => url.indexOf("dodge") >= 0));
  }),
);

run.push(
  check("прелоадер доходит до единицы ровно по критическому пути", async () => {
    const it = stand();
    await it.assets.open(["warrior", "tank"]);
    same(it.assets.progress(), 0, "до загрузки ничего не готово");
    ok(!it.assets.ready());
    await it.assets.prepare();
    same(it.assets.progress(), 1, "фон и по четыре набора на двоих");
    ok(it.assets.ready(), "сцену можно открывать");
  }),
);

run.push(
  check("один адрес качается один раз", async () => {
    const it = stand();
    await it.assets.open(["warrior", "warrior"]);
    await it.assets.prepare();
    await it.assets.prepare();
    const unique = new Set(it.asked);
    same(it.asked.length, unique.size, "повторных запросов нет");
    same(it.assets.loaded(), unique.size);
  }),
);

run.push(
  check("упавшая картинка не кэшируется и повтор качает заново", async () => {
    const it = stand({ broken: ["hit.webp"] });
    let said = "";
    await it.assets.open(["warrior"]);
    await it.assets.prepare().catch((error) => {
      said = error.message;
    });
    ok(said.length > 0, "о неудаче сказано, а не проглочено");
    const before = it.asked.filter((url) => url.indexOf("hit.webp") >= 0).length;
    await it.assets.prepare().catch(() => {});
    const after = it.asked.filter((url) => url.indexOf("hit.webp") >= 0).length;
    ok(after > before, "по кнопке «ещё раз» загрузка пошла заново");
  }),
);

run.push(
  check("кадр берётся из манифеста со своим размером и якорем", async () => {
    const it = stand();
    await it.assets.open(["warrior"]);
    const frame = it.assets.frame("warrior", "attack", 0, 0);
    ok(frame, "кадр нашёлся");
    ok(frame.rect.w > 0 && frame.rect.h > 0, "прямоугольник в атласе");
    ok(frame.drawSize.w > 0 && frame.drawSize.h > 0, "логический размер свой");
    ok(frame.drawSize.w !== frame.rect.w, "и он не равен обрезанному");
    ok(frame.pivot.y > 0, "якорь на полу");
    same(it.assets.frame("warrior", "attack", 9, 9), null, "чего нет — того нет");
  }),
);

run.push(
  check("ряд находится по зоне, а не по номеру наугад", async () => {
    const it = stand();
    await it.assets.open(["warrior"]);
    same(it.assets.row("warrior", "attack", "head"), 0);
    same(it.assets.row("warrior", "attack", "legs"), 4);
    same(it.assets.row("warrior", "move", "ko"), 2);
    same(it.assets.row("warrior", "attack", "waist"), -1, "такой зоны нет");
  }),
);

run.push(
  check("тайминги берутся у манифеста, а не из кода сцены", async () => {
    const it = stand();
    await it.assets.open(["warrior"]);
    same(it.assets.timing("warrior", "attack"), 380);
    same(it.assets.timing("warrior", "hitReaction"), 540);
    same(it.assets.timing("warrior", "breakHold"), 180);
    same(it.assets.timing("warrior", "ko"), 950);
  }),
);

run.push(
  check("адрес атласа складывается из базы, а не из baseUrl манифеста", async () => {
    // Берём воина нарочно: в его манифесте лежит `baseUrl` с адресом
    // бакета. Читать его нельзя — все адреса картинок в игре живут в
    // одном месте, `bot.game.art`, и версию бакета меняют там. Начни
    // загрузчик слушать манифест, и один боец грузился бы из старой
    // версии, а остальные из новой
    const it = stand();
    const book = JSON.parse(
      fs.readFileSync(path.join(STATIC, "warrior", "manifest.json"), "utf8"),
    );
    ok(book.baseUrl, "в манифесте воина baseUrl есть — иначе проверка пустая");
    await it.assets.open(["warrior"]);
    await it.assets.prepare();
    ok(
      it.asked.indexOf(BASE + "runtime/warrior/attack.webp") >= 0,
      "адрес собран по правилу: " + it.asked.join(" "),
    );
    ok(
      it.asked.every((url) => url.indexOf(book.baseUrl) < 0),
      "и ни один адрес не взят из манифеста",
    );
  }),
);

run.push(
  check("без участников сцену не открывают", async () => {
    const it = stand();
    let said = "";
    await it.assets.open([]).catch((error) => {
      said = error.message;
    });
    ok(said.length > 0, "отказ, а не пустая сцена");
  }),
);

Promise.all(run).then(() => {
  if (failed) {
    console.log("пало проверок: " + failed);
    process.exit(1);
  }
  console.log("все проверки прошли");
});
