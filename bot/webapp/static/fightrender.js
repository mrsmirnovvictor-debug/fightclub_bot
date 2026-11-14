// Сцена кулачного боя: раскадровка и отрисовка.
//
// Здесь две разные вещи, и они нарочно разделены.
//
// `fightStage` — раскадровка. Чистая логика времени: получив события
// от `fightQueue`, она отвечает на вопрос «в какой позе каждый боец в
// момент t, где он стоит, сколько у него здоровья на экране и какая
// надпись висит». Ни canvas, ни картинок она не знает, поэтому
// проверяется на node без браузера — а проверять тут есть что: окно
// контакта, сорванный блок в два этапа, контрудар без лишнего отхода,
// нокаут в конце.
//
// `drawStage` — отрисовка. Тонкий слой: берёт позу у раскадровки, кадр
// у загрузчика и кладёт его на canvas. Арифметики в ней нет нарочно:
// всё, что можно посчитать, посчитано выше.
//
// **Источник истины — сервер.** Раскадровка не считает урон и не
// решает исходов: здоровье она берёт из событий, а не вычитает сама.

// Насколько боец сходится с противником на удар. Якоря фона стоят в
// 200 логических единицах друг от друга — это дистанция стойки, с неё
// кулаком не достать
var REACH = 62;

// Сколько висит надпись о приёме. Меньше — не прочитать, больше —
// догоняет следующий удар
var BADGE_MS = 800;

// Как зона удара превращается в ряд уворота. В движке у уворота зоны
// нет вовсе: он либо случился, либо нет. Это чистая презентация —
// от удара в голову уходят верхом, от удара в ноги подскакивают
var DODGE_ROWS = {
  head: "upper",
  chest: "upper",
  belly: "body",
  belt: "body",
  legs: "legs",
};

// Ряд стойки, шага и нокаута в наборе move
var STANCE_ROW = "stance_reference";
var STEP_ROW = "step";
var KO_ROW = "ko";

function fightStage(options) {
  var settings = options || {};
  // Тайминги спрашиваем у манифеста бойца: они лежат рядом с кадрами и
  // меняются вместе с ними
  var timing = settings.timing;
  // Спокойный режим: человек попросил систему не анимировать. Позы
  // остаются, но без плавности и покачивания
  var calm = !!settings.calm;

  var left = null;
  var right = null;
  var clock = 0;
  // Когда закончится уже расставленное. Новые события встают за этим
  var cursor = 0;
  var pending = null;
  var tracks = { left: [], right: [] };
  var drops = [];
  var badges = [];
  var fallen = {};
  var finished = false;

  // Какое здоровье показываем в этот момент. Вынесено отдельно: тем же
  // счётом пользуется сверка с сервером, а часы она трогать не должна
  function hpAt(side, when) {
    var fighter = side === "left" ? left : right;
    var shown = fighter ? fighter.hp : 0;
    for (var i = 0; i < drops.length; i += 1) {
      if (drops[i].side === side && when >= drops[i].at) {
        shown = drops[i].hp;
      }
    }
    return shown;
  }

  function sideOf(id) {
    if (left && left.id === id) return "left";
    if (right && right.id === id) return "right";
    return null;
  }

  function other(side) {
    return side === "left" ? "right" : "left";
  }

  function ms(name) {
    var found = timing ? timing[name] : undefined;
    // Тайминга нет — лучше заметная заминка, чем нулевая длительность:
    // кадр с нулём длины не покажется вообще
    return typeof found === "number" ? found : 300;
  }

  function lay(side, from, to, set, row, travel) {
    if (to <= from) {
      return;
    }
    tracks[side].push({
      from: from, to: to, set: set, row: row,
      // Куда ехать: 0 — свой якорь, 1 — вплотную к противнику
      travel: travel || null,
    });
  }

  function drop(at, side, hp, damage, critical, healing) {
    drops.push({
      at: at, side: side, hp: hp, damage: damage, critical: !!critical,
      // Лечение — такое же изменение здоровья, как урон, и показывать
      // его надо так же. Иначе человек жмёт приём на трёх очках
      // здоровья и не видит ничего
      healing: !!healing,
    });
  }

  function badge(at, side, text, assumed) {
    if (!text) {
      return;
    }
    badges.push({
      at: at, until: at + BADGE_MS, side: side, text: text,
      assumed: !!assumed,
    });
  }

  // Расставить один удар: замах атакующего плюс то, чем он кончился
  function strike(attack, answers) {
    var hitter = sideOf(attack.attacker);
    var taker = sideOf(attack.defender);
    if (!hitter || !taker) {
      return;
    }

    var t0 = cursor;
    var approach = ms("approach");
    var swing = ms("attack");
    var contact = t0 + approach + ms("contact");
    var swingEnd = t0 + approach + swing;
    var zone = attack.zone || "chest";

    lay(hitter, t0, t0 + approach, "move", STEP_ROW, { from: 0, to: 1 });
    lay(hitter, t0 + approach, swingEnd, "attack", zone, { from: 1, to: 1 });
    badge(t0 + approach, hitter, attack.ability);

    var mine = swingEnd + ms("retreat");
    var theirs = swingEnd;
    var counter = null;

    for (var i = 0; i < answers.length; i += 1) {
      var answer = answers[i];
      if (answer.kind === "block") {
        // Защита стоит от начала замаха: выставлять её в момент удара
        // поздно, этого не увидеть. Сорванный блок держится дольше —
        // это сопротивление, а не промах
        var hold = answer.breaking ? ms("breakHold") : 0;
        var guardEnd = contact + hold + (answer.breaking ? 0 : ms("breakHold"));
        lay(taker, t0 + approach, guardEnd, "block", zone);
        badge(t0 + approach, taker, answer.ability);
        theirs = Math.max(theirs, guardEnd);
      } else if (answer.kind === "hit") {
        // Пробитие доходит до тела позже: сперва сорвать защиту
        var when = contact + (answer.broken ? ms("breakHold") : 0);
        lay(taker, when, when + ms("hitReaction"), "hit", zone);
        drop(when, taker, answer.hp, answer.damage, answer.critical);
        theirs = Math.max(theirs, when + ms("hitReaction"));
      } else if (answer.kind === "dodge") {
        var row = DODGE_ROWS[zone] || "body";
        lay(taker, t0 + approach, t0 + approach + ms("dodge"), "dodge", row);
        badge(t0 + approach, taker, answer.ability);
        theirs = Math.max(theirs, t0 + approach + ms("dodge"));
      } else if (answer.kind === "counter") {
        counter = answer;
      }
    }

    if (counter) {
      // Контрудар — ответ с той же дистанции. Расходиться и снова
      // сходиться между уворотом и ответом нельзя: это один обмен
      var from = theirs;
      var back = from + swing;
      lay(taker, from, back, "attack", counter.zone, { from: 1, to: 1 });
      // У контрудара своего названия приёма нет: в движке он к зоне не
      // привязан и зональную броню игнорирует. Подписываем словом, а
      // если и зону пришлось додумать — тусклее, чтобы не выдавать
      // презентацию за данные сервера
      badge(from, taker, counter.ability || "ответ", counter.assumed_zone);
      var touch = from + ms("contact");
      lay(hitter, touch, touch + ms("hitReaction"), "hit", counter.zone);
      drop(touch, hitter, counter.hp, counter.damage, counter.critical);
      // Отходят оба, и защищавшийся — своим, более коротким отходом
      mine = Math.max(touch + ms("hitReaction"), back) + ms("retreat");
      lay(taker, back, back + ms("counterRetreat"), "move", STEP_ROW, {
        from: 1, to: 0,
      });
      theirs = back + ms("counterRetreat");
    }

    lay(hitter, mine - ms("retreat"), mine, "move", STEP_ROW, {
      from: 1, to: 0,
    });
    cursor = Math.max(mine, theirs);
  }

  function flush() {
    if (pending) {
      strike(pending.attack, pending.answers);
      pending = null;
    }
  }

  return {
    // Кто слева, кто справа. Порядок решает карточка: свой боец всегда
    // слева, и раскадровка его не переставляет
    cast: function (one, two) {
      left = one;
      right = two;
      return this;
    },

    // События от `fightQueue`, в их порядке. Замах и его исход
    // приходят вплотную, поэтому замах ждёт исхода, а не встаёт в
    // расписание один
    feed: function (events) {
      // Ходы приходят по опросу, а между ними в игре стоит кулдаун —
      // десятки секунд. Расписание за это время кончается, часы идут
      // дальше, и новый ход, поставленный от старого курсора, оказался
      // бы в прошлом: он проиграется мгновенно, человек увидит
      // дёрнувшиеся полосы вместо удара. Поэтому новое встаёт не
      // раньше текущего момента
      cursor = Math.max(cursor, clock);
      for (var i = 0; i < events.length; i += 1) {
        var event = events[i];
        if (event.kind === "attack") {
          flush();
          pending = { attack: event, answers: [] };
        } else if (
          event.kind === "block" || event.kind === "hit" ||
          event.kind === "dodge" || event.kind === "counter"
        ) {
          if (pending) {
            pending.answers.push(event);
          }
        } else if (event.kind === "skip") {
          flush();
          badge(cursor, sideOf(event.attacker), "пропуск");
          cursor += ms("approach");
        } else if (event.kind === "text") {
          flush();
          badge(cursor, sideOf(event.attacker), "удар", true);
          cursor += ms("approach");
        } else if (event.kind === "settle") {
          flush();
          // Сервер прислал итоговое здоровье хода. Сверяемся с ним, а
          // не только со своими ударами: лечение, яд и всё прочее
          // случается мимо разменов, и без этой сверки полоса остаётся
          // той, какой её оставил последний удар. Ставим на конец хода,
          // чтобы не опередить сам удар
          ["left", "right"].forEach(function (side) {
            var who = side === "left" ? left : right;
            if (!who) return;
            var told = event.hp[String(who.id)];
            if (told === undefined) return;
            var shown = hpAt(side, cursor);
            if (told !== shown) {
              drop(cursor, side, told, Math.abs(told - shown), false,
                told > shown);
            }
          });
          if (event.finished) {
            finished = true;
            var loser = null;
            if (event.winner && sideOf(event.winner)) {
              loser = other(sideOf(event.winner));
            }
            if (loser) {
              lay(loser, cursor, cursor + ms("ko"), "move", KO_ROW);
              fallen[loser] = cursor + ms("ko");
              cursor += ms("ko");
            }
          }
        }
      }
      return this;
    },

    at: function (now) {
      clock = now;
      return this;
    },

    // Поза бойца на текущий момент: набор, ряд, номер кадра, сдвиг от
    // своего якоря и лежит ли он
    look: function (side, columns) {
      var track = tracks[side];
      var span = null;
      for (var i = 0; i < track.length; i += 1) {
        if (clock >= track[i].from && clock < track[i].to) {
          span = track[i];
        }
      }
      var down = fallen[side] !== undefined && clock >= fallen[side];
      if (!span) {
        if (down) {
          // Упавший остаётся лежать последним кадром нокаута
          return { set: "move", row: KO_ROW, frame: (columns || 6) - 1,
            shift: 0, fallen: true };
        }
        // Вне своих отрезков боец стоит в стойке и дышит
        var period = ms("swayPeriod");
        var step = calm ? 0 : Math.floor(
          ((clock % period) / period) * (columns || 6)
        );
        return { set: "move", row: STANCE_ROW, frame: step, shift: 0,
          fallen: false };
      }
      var span_length = span.to - span.from;
      var gone = (clock - span.from) / span_length;
      var cells = columns || 6;
      var frame = Math.min(cells - 1, Math.floor(gone * cells));
      var shift = 0;
      if (span.travel) {
        var path = span.travel.from +
          (span.travel.to - span.travel.from) * (calm ? 1 : gone);
        shift = path * REACH;
      }
      return { set: span.set, row: span.row, frame: frame, shift: shift,
        fallen: down };
    },

    // Здоровье, которое показываем. Снимается в момент контакта, а не
    // в начале удара: полоса не должна опускаться раньше попадания
    hp: function (side) {
      return hpAt(side, clock);
    },

    // Сервер сказал, сколько у бойца здоровья прямо сейчас. Принимаем
    // это, только когда всё расставленное доиграно: пока размен идёт,
    // опрос знает БУДУЩЕЕ — здоровье после удара, который ещё не
    // показан, — и верить ему значило бы ронять полосу раньше удара
    sync: function (side, hp) {
      if (typeof hp !== "number" || clock < cursor) {
        return false;
      }
      var shown = hpAt(side, clock);
      if (hp === shown) {
        return false;
      }
      drop(clock, side, hp, Math.abs(hp - shown), false, hp > shown);
      return true;
    },

    // Свежий урон — его показывают всплывающим числом
    damage: function (side) {
      for (var i = drops.length - 1; i >= 0; i -= 1) {
        var one = drops[i];
        if (one.side === side && clock >= one.at && clock < one.at + BADGE_MS) {
          return one;
        }
      }
      return null;
    },

    badge: function (side) {
      for (var i = badges.length - 1; i >= 0; i -= 1) {
        var one = badges[i];
        if (one.side === side && clock >= one.at && clock < one.until) {
          return one;
        }
      }
      return null;
    },

    // Доиграно ли всё расставленное: по этому карточка понимает, что
    // можно показать итог боя
    done: function () {
      return clock >= cursor;
    },

    over: function () {
      return finished;
    },

    // Докуда расставлено расписание
    length: function () {
      return cursor;
    },

    cast_of: function (side) {
      return side === "left" ? left : right;
    },
  };
}

// Отрисовка одного кадра. Ничего не считает — берёт позу у
// раскадровки, кадр у загрузчика и кладёт на canvas
function drawStage(ctx, stage, assets, scene) {
  var floor = scene.floorY;
  var anchors = scene.fighterAnchors;

  ctx.clearRect(0, 0, scene.logicalSize.width, scene.logicalSize.height);
  var back = assets.backdrop();
  if (back) {
    ctx.drawImage(back, 0, 0, scene.logicalSize.width,
      scene.logicalSize.height);
  }

  ["left", "right"].forEach(function (side) {
    var who = stage.cast_of(side);
    if (!who) {
      return;
    }
    var set = assets.atlas(who.line, "move");
    var book = assets.sheet(who.line, "move");
    var pose = stage.look(side, book ? book.columns : 6);
    set = assets.atlas(who.line, pose.set);
    book = assets.sheet(who.line, pose.set);
    if (!set || !book) {
      return;
    }
    var row = assets.row(who.line, pose.set, pose.row);
    var frame = assets.frame(who.line, pose.set, row, pose.frame);
    if (!frame) {
      return;
    }

    var mirror = side === "right";
    var home = anchors[side].x;
    var x = home + (mirror ? -pose.shift : pose.shift);

    // Тень рисуем сами: в атласах её нет нарочно — она зависит от
    // позы, а не от кадра
    ctx.save();
    ctx.fillStyle = "#252835";
    ctx.beginPath();
    ctx.ellipse(x, floor + 2, pose.fallen ? 83 : 48, 5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.translate(x, anchors[side].y);
    if (mirror) {
      // Зеркалим всю местную систему координат, а не картинку: иначе
      // якорь уедет вместе с кадром
      ctx.scale(-1, 1);
    }
    ctx.drawImage(
      set,
      frame.rect.x, frame.rect.y, frame.rect.w, frame.rect.h,
      -frame.pivot.x, -frame.pivot.y,
      frame.drawSize.w, frame.drawSize.h,
    );
    ctx.restore();
  });

  drawHud(ctx, stage, scene);
}

// Имена и полосы здоровья. Стоят в сцене, а не над ней: ТЗ требует
// держать их внутри безопасной зоны фона, иначе на узком экране их
// срежет вместе с краями картинки
function drawHud(ctx, stage, scene) {
  var safe = scene.safeArea;
  var top = safe.top;
  var width = 150;
  var height = 9;

  ["left", "right"].forEach(function (side) {
    var who = stage.cast_of(side);
    if (!who) {
      return;
    }
    var mine = side === "left";
    var x = mine ? safe.left : safe.right - width;
    var hp = stage.hp(side);
    var full = who.maxHp || 1;
    var share = Math.max(0, Math.min(1, hp / full));

    ctx.save();
    ctx.fillStyle = "#15161c";
    ctx.fillRect(x, top, width, height);
    // Цвет полосы — по остатку, а не по тому, кто свой: человек должен
    // видеть опасность, а не симпатию
    ctx.fillStyle = share > 0.5 ? "#4caf50" : share > 0.25 ? "#e0a53a" : "#d2452f";
    ctx.fillRect(x, top, width * share, height);
    ctx.fillStyle = "#e8e8ea";
    ctx.font = "12px sans-serif";
    ctx.textAlign = mine ? "left" : "right";
    ctx.fillText(who.name, mine ? x : x + width, top - 5);
    ctx.textAlign = mine ? "right" : "left";
    ctx.fillText(hp + "/" + full, mine ? x + width : x, top - 5);
    ctx.restore();

    // Свежий урон — числом у того, кто его получил
    var hurt = stage.damage(side);
    if (hurt) {
      ctx.save();
      ctx.fillStyle = hurt.healing
        ? "#5bc87a"
        : hurt.critical
          ? "#ffd24a"
          : "#f2f2f4";
      ctx.font = (hurt.critical ? "bold 20px" : "16px") + " sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(
        (hurt.healing ? "+" : "−") + hurt.damage,
        scene.fighterAnchors[side].x,
        top + 46,
      );
      ctx.restore();
    }

    // Подпись приёма. Если зону додумала сцена, а не прислал сервер,
    // подпись идёт тусклее: выдавать догадку за данные нельзя
    var said = stage.badge(side);
    if (said) {
      ctx.save();
      ctx.fillStyle = said.assumed ? "#8a8d98" : "#cfd2dc";
      ctx.font = "13px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(said.text, scene.fighterAnchors[side].x, top + 24);
      ctx.restore();
    }
  });
}

