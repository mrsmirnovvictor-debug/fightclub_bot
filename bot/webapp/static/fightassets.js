// Графика сцены боя: что грузить, в каком порядке и когда можно начинать.
//
// Загрузчик и только он: кадров он не рисует, событий не знает. Его дело —
// принести параметры сцены, манифесты участников и атласы, сказать, когда
// минимального набора хватает для первого кадра, и не уронить бой, если
// что-то из этого не приехало.
//
// **Грузим не всё.** Полный комплект класса — около 2,4 МиБ, классов
// четыре, и предзагружать их целиком нельзя: в бою участвуют двое, и
// только их образы нужны. Остальные два комплекта не нужны вовсе.
//
// **Грузим не сразу всё и у этих двоих.** Сперва то, без чего не
// показать первый кадр и первый размен — стойка, шаг, удар, реакция,
// блок, — и только потом увороты. Иначе прелоадер держит игрока лишнюю
// секунду ради кадров, которые понадобятся не в этом размене.
//
// **Кэш по адресу.** Повторный опрос, переоткрытие панели и реплей того
// же боя не должны качать один и тот же атлас дважды.

// Сколько линий образов берём на один бой. В дуэли их ровно две, и
// третья означала бы ошибку в вызове, а не щедрость
var LINES_PER_FIGHT = 2;

// Что нужно до первого кадра, а что догоняет. `move` первым: без стойки
// сцену нельзя показать вообще
var CRITICAL_SETS = ["move", "attack", "hit", "block"];
var LATE_SETS = ["dodge"];

function fightAssets(options) {
  var settings = options || {};
  var base = settings.base || "";
  // Загрузку json и картинок передают снаружи: так загрузчик
  // проверяется без браузера, а в бою работает на настоящих fetch/Image
  var fetchJson = settings.fetchJson;
  var loadImage = settings.loadImage;
  var statics = settings.statics || "static/fight/";

  var scene = null;
  var manifests = {};
  var atlases = {};
  var waiting = {};
  var wanted = [];

  function once(url, make) {
    // Один адрес — одна загрузка. Вторая попытка ждёт первую, а не
    // заводит свою: иначе переоткрытая панель качает всё заново
    if (atlases[url]) {
      return Promise.resolve(atlases[url]);
    }
    if (waiting[url]) {
      return waiting[url];
    }
    waiting[url] = Promise.resolve(make()).then(
      function (image) {
        atlases[url] = image;
        delete waiting[url];
        return image;
      },
      function (error) {
        // Неудачу не кэшируем: по кнопке «ещё раз» загрузка должна
        // пойти заново, а не вернуть вчерашнюю ошибку
        delete waiting[url];
        throw error;
      },
    );
    return waiting[url];
  }

  function atlasUrl(line, name) {
    var set = manifests[line].sets[name];
    // Путь в манифесте относительный нарочно: адрес бакета живёт в
    // `bot.game.art`, и база приходит оттуда
    return base + set.url;
  }

  function loadSets(line, names) {
    return Promise.all(
      names.map(function (name) {
        var url = atlasUrl(line, name);
        return once(url, function () {
          return loadImage(url);
        });
      }),
    );
  }

  return {
    // Параметры сцены и манифесты участников. Их немного, они лежат в
    // статике приложения и грузятся с его же origin: json через fetch
    // упёрся бы в CORS бакета
    open: function (lines) {
      if (!lines || !lines.length) {
        return Promise.reject(new Error("некого выводить на сцену"));
      }
      if (lines.length > LINES_PER_FIGHT) {
        return Promise.reject(
          new Error("на бой берут не больше " + LINES_PER_FIGHT + " линий"),
        );
      }
      wanted = lines.slice();
      var jobs = [fetchJson(statics + "background.json")];
      for (var i = 0; i < lines.length; i += 1) {
        jobs.push(fetchJson(statics + lines[i] + "/manifest.json"));
      }
      return Promise.all(jobs).then(function (answers) {
        scene = answers[0];
        for (var j = 0; j < wanted.length; j += 1) {
          manifests[wanted[j]] = answers[j + 1];
        }
        return scene;
      });
    },

    // Критический путь: фон и то, без чего не показать размен. Сцену
    // открываем по готовности именно этого
    prepare: function () {
      var jobs = [
        once(base + scene.asset, function () {
          return loadImage(base + scene.asset);
        }),
      ];
      for (var i = 0; i < wanted.length; i += 1) {
        jobs.push(loadSets(wanted[i], CRITICAL_SETS));
      }
      return Promise.all(jobs);
    },

    // Остальное догоняет уже при открытой сцене
    finish: function () {
      var jobs = [];
      for (var i = 0; i < wanted.length; i += 1) {
        jobs.push(loadSets(wanted[i], LATE_SETS));
      }
      return Promise.all(jobs);
    },

    // Доля готовности для прелоадера: фон плюс критические наборы обоих
    progress: function () {
      var need = 1 + wanted.length * CRITICAL_SETS.length;
      var have = scene && atlases[base + scene.asset] ? 1 : 0;
      for (var i = 0; i < wanted.length; i += 1) {
        if (!manifests[wanted[i]]) {
          continue;
        }
        for (var j = 0; j < CRITICAL_SETS.length; j += 1) {
          if (atlases[atlasUrl(wanted[i], CRITICAL_SETS[j])]) {
            have += 1;
          }
        }
      }
      return have / need;
    },

    ready: function () {
      return this.progress() >= 1;
    },

    scene: function () {
      return scene;
    },

    // Кадр из манифеста: набор, ряд, номер. Логический размер и якорь
    // берутся отсюда же — подменять их размером обрезанной картинки
    // нельзя, кадр тогда поедет
    frame: function (line, name, row, number) {
      var book = manifests[line];
      if (!book || !book.sets[name]) {
        return null;
      }
      var frames = book.sets[name].frames;
      for (var i = 0; i < frames.length; i += 1) {
        if (frames[i].row === row && frames[i].frame === number) {
          return frames[i];
        }
      }
      return null;
    },

    // Какой ряд отвечает за эту зону или состояние
    row: function (line, name, what) {
      var book = manifests[line];
      if (!book) {
        return -1;
      }
      return (book.rowMap[name] || []).indexOf(what);
    },

    timing: function (line, name) {
      var book = manifests[line];
      return book && book.timingMs ? book.timingMs[name] : undefined;
    },

    atlas: function (line, name) {
      return manifests[line] ? atlases[atlasUrl(line, name)] : undefined;
    },

    // Сколько атласов уже в памяти. По нему видно, что кэш работает
    loaded: function () {
      return Object.keys(atlases).length;
    },

    lines: function () {
      return wanted.slice();
    },
  };
}
