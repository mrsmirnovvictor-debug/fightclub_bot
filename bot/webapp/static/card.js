"use strict";

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

const el = (id) => document.getElementById(id);

// Падения мини-аппа не видно ниоткуда: консоль вебвью Telegram не
// показывает, а на телефоне её не открыть — со стороны карточка просто
// «не работает», и в логах сервера при этом чисто. Поэтому свою ошибку
// клиент относит на сервер сам. Больше трёх за сеанс не носим: если
// сломался цикл отрисовки, отчёты пойдут потоком и зальют журнал.
let oopsLeft = 3;

function reportOops(error, screen) {
  if (oopsLeft <= 0) return;
  oopsLeft -= 1;
  try {
    fetch("api/oops", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify({
        message: (error && error.message) || String(error),
        stack: (error && error.stack) || "",
        screen: screen || (typeof lastTab === "string" ? lastTab : "?"),
        agent: navigator.userAgent,
      }),
      keepalive: true,
    }).catch(() => {});
  } catch (ignored) {
    // Отчёт об ошибке не имеет права уронить страницу второй раз
  }
}

window.addEventListener("error", (event) => {
  reportOops(event.error || new Error(event.message), null);
});
window.addEventListener("unhandledrejection", (event) => {
  reportOops(event.reason, null);
});
const numberFormat = new Intl.NumberFormat("ru-RU");
const num = (value) => numberFormat.format(value);

function share(combat, name, label, text) {
  // Строка процента: если потолок срезал лишнее, так и говорим. Иначе
  // «+100% уворота с вещей» и «60%» в строке выглядят как ошибка счёта.
  const cap = (combat.caps || {})[name];
  // Потолок в 100% — это снятый потолок: писать о нём нечего
  const limited = Boolean(cap) && cap.cap < 100;
  const value = text || combat[name] + "%";
  // Срезанный потолком процент красим золотом, а не подписываем: подпись
  // ломала строку, а объяснение всё равно живёт в подсказке
  const line = row(label, value, limited && cap.capped ? "capped" : "");
  if (cap) {
    const sum = label + ": своё " + cap.own + "% + вещи " + cap.gear + "%";
    line.title = !limited
      ? sum + " (потолков сейчас нет)"
      : cap.capped
        ? sum + " = " + cap.raw + "%, но выше " + cap.cap + "% не растёт"
        : sum + " (потолок " + cap.cap + "%)";
  }
  return line;
}

function row(label, value, extraClass) {
  const li = document.createElement("li");
  const left = document.createElement("span");
  left.className = "label";
  left.textContent = label;
  const right = document.createElement("span");
  right.className = "value" + (extraClass ? " " + extraClass : "");
  if (value instanceof Node) {
    right.appendChild(value);
  } else {
    right.textContent = value;
  }
  li.append(left, right);
  return li;
}

function statValue(stat) {
  const box = document.createElement("span");
  box.textContent = num(stat.total);
  if (stat.bonus) {
    const bonus = document.createElement("span");
    bonus.className = "bonus";
    bonus.textContent = ` (${stat.base} + ${stat.bonus})`;
    box.appendChild(bonus);
  }
  // Потеря от травмы — красным и отдельным числом: «Ловкость -5 (-10)».
  // В общую прибавку её не складываем, иначе видно только итог, а он и
  // сам по себе бывает отрицательным
  if (stat.loss) {
    box.classList.add("hurt");
    const loss = document.createElement("span");
    loss.className = "loss";
    loss.textContent = ` (${stat.loss})`;
    box.appendChild(loss);
  }
  return box;
}

// Вибрация — украшение, и вести себя она должна как украшение.
//
// Телефон её умеет, настольный клиент — не всегда. Объект `HapticFeedback` в
// SDK есть всегда, поэтому проверка «а есть ли он» проходила, а сам вызов на
// старом клиенте бросал WebAppMethodUnsupported. Бросок случался до `try`, и
// флаг «занято» у хода оставался поднятым навсегда: удары после этого
// переставали нажиматься молча, без единого слова на экране.
function haptic(run) {
  try {
    if (tg && tg.HapticFeedback) run(tg.HapticFeedback);
  } catch (error) {
    // Не завибрировало — и ладно. Ход от этого срываться не должен
  }
}

function popup(title, message) {
  if (tg && tg.showPopup) {
    tg.showPopup({ title, message, buttons: [{ type: "close" }] });
  } else {
    alert(title + "\n\n" + message);
  }
}

function confirmAction(question) {
  // Спрашиваем силами Telegram, а без него — обычным confirm
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) {
      tg.showConfirm(question, (ok) => resolve(Boolean(ok)));
    } else {
      resolve(window.confirm(question));
    }
  });
}

function askConfirm(title, message, yes) {
  // То же согласие, но с нашими подписями на кнопках: «Подтвердить» и
  // «Отмена». Telegram умеет их называть, браузер — нет, поэтому вне
  // Telegram спрашиваем обычным окном тем же текстом.
  return new Promise((resolve) => {
    if (tg && tg.showPopup) {
      tg.showPopup(
        {
          title: title,
          message: message,
          buttons: [
            { id: "yes", type: "default", text: yes || "Подтвердить" },
            { id: "no", type: "cancel", text: "Отмена" },
          ],
        },
        (id) => resolve(id === "yes")
      );
    } else {
      resolve(window.confirm(title + "\n\n" + message));
    }
  });
}

function picture(src, alt, fallback, onFail) {
  // Картинка со значком на случай, если файл не доехал
  const img = document.createElement("img");
  img.src = src;
  img.alt = alt;
  // Картинок в лавке полсотни, и каждая тяжёлая: тянем по мере прокрутки
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => {
    img.replaceWith(document.createTextNode(fallback));
    if (onFail) onFail();
  });
  return img;
}

// ---------- износ ----------
//
// Вещь рассыпается молча: запас прочности кончился — и её нет. Чтобы это
// не случалось «вдруг», последние пункты запаса вещь говорит о себе сама,
// и одинаково везде: в рюкзаке, в кукле и в мастерской.
//
// Три — не круглое число, а расстояние: столько боёв у бойца есть, чтобы
// дойти до мастерской и не остаться без вещи.
const WEAR_WARN = 3;

function wearLeft(item) {
  // Сколько пунктов запаса осталось. Ноль — вещи уже нет
  return (item.max_wear || 0) - (item.wear || 0);
}

function wearState(item) {
  // "" — беспокоиться не о чем, "worn" — вещь потрёпана, "aging" —
  // просится в починку, "dying" — последний бой
  if (!item || !item.max_wear) return "";
  if (item.kind === "pass") return "";  // пропуск не ветшает, а отрабатывает
  const left = wearLeft(item);
  if (left <= 1) return "dying";
  if (left <= WEAR_WARN) return "aging";
  return item.wear ? "worn" : "";
}

function fightWord(count) {
  // 1 бой, 2–4 боя, 5 и больше — боёв
  const last = count % 10;
  const pair = count % 100;
  if (pair >= 11 && pair <= 14) return "боёв";
  if (last === 1) return "бой";
  if (last >= 2 && last <= 4) return "боя";
  return "боёв";
}

function wearNote(item) {
  // Что приписать к «Износ: 18/20». Пусто — ничего, запас велик
  const state = wearState(item);
  const left = wearLeft(item);
  if (state === "dying") return " — ещё один бой, и рассыплется";
  if (state === "aging") return " — в запасе " + left + " " + fightWord(left);
  return "";
}

function wearRow(item) {
  // Строка износа: одна на весь клуб, чтобы «ещё один бой» и цвет не
  // разошлись между рюкзаком, мастерской и куклой
  const box = document.createElement("div");
  const state = wearState(item);
  box.className = "thing-wear" + (state ? " " + state : "");
  box.textContent = "🔧 Износ: " + item.wear_text;
  if (item.kind === "pass") box.textContent += " — хватает на один рейд";
  else box.textContent += wearNote(item);
  return box;
}

function slotPicture(item, placeholder) {
  if (item && item.image) {
    return picture(item.image, item.title, item.icon);
  }
  return document.createTextNode(item ? item.icon : placeholder);
}

function emptySlotPicture(slot, box) {
  // Тень того, что сюда надевается
  if (!slot.placeholder_image) {
    box.classList.add("no-art");
    return document.createTextNode(slot.placeholder);
  }
  // Не загрузилась подложка — гасим слот по-старому и показываем значок
  return picture(slot.placeholder_image, slot.title, slot.placeholder, () =>
    box.classList.add("no-art")
  );
}

function wornLine(item, slotTitle) {
  const parts = [item.title + " — " + slotTitle];
  if (item.mod && item.mod.star) {
    parts.push(item.mod.star + " " + item.mod.title + ": " + item.mod.gain);
  }
  if (item.bonus) parts.push(item.bonus);
  if (item.in_hands) parts.push(item.in_hands);
  // Вещь на исходе — в подсказке клетки, а не только в рюкзаке: на куклу
  // смотрят чаще, и рассыпается вещь именно надетой
  if (wearState(item) === "dying" || wearState(item) === "aging") {
    parts.push("🔧 Износ: " + item.wear_text + wearNote(item));
  }
  return parts.join("\n");
}

function slotHint(slot) {
  // Подсказка при наведении: что надето и что это даёт. В клетке тела вещей
  // может быть две — верхняя одежда и футболка под ней, — и рассказываем про
  // обе: картинкой видно только верхнюю.
  const lines = [];
  if (slot.item) lines.push(wornLine(slot.item, slot.title));
  if (slot.under) lines.push(wornLine(slot.under, slot.under_title));
  if (!lines.length) return "Пусто: " + slot.cell_title;
  return lines.join("\n\n");
}

// Кукла бывает двух родов. В инвентаре она про сборы: нажатие снимает
// вещь. В персонаже — про самого бойца: нажатие рассказывает, что вещь
// даёт, и ничего с ней не делает. Снять надетое случайным нажатием на
// экране, куда заходят посмотреть характеристики, — из тех потерь, за
// которые игра и получает своё «опять слетело».
//
// `own` — своя ли это карточка, `info` — кукла только для показа.
// Клетки колонки, разложенные по рядам. Обычная клетка — ряд из себя
// одной; три колечка идут одним рядом, и ряд этот ровно той же ширины,
// что и квадратная клетка над ним. Группирует страница, а не сервер:
// серверу довольно сказать, какой формы клетка, — где её рисовать,
// видно уже по соседям.
function slotRows(slots) {
  const rows = [];
  slots.forEach((slot) => {
    const last = rows[rows.length - 1];
    if (slot.shape === "ring" && last && last.shape === "ring") {
      last.cells.push(slot);
      return;
    }
    rows.push({ shape: slot.shape || "square", cells: [slot] });
  });
  return rows;
}

function renderSlots(container, slots, own, info) {
  container.textContent = "";
  slotRows(slots).forEach((row) => {
    let box = container;
    if (row.shape === "ring") {
      box = document.createElement("div");
      box.className = "slot-row rings";
      container.appendChild(box);
    }
    row.cells.forEach((slot) => renderSlot(box, slot, own, info));
  });
}

function renderSlot(container, slot, own, info) {
  // Картинкой показываем верхнюю вещь; если её нет, а нижняя есть — нижнюю.
  // Пустой клетка считается, только когда в ней нет ни одной.
  const shown = slot.item || slot.under;
  const box = document.createElement("div");
  box.className =
    "slot " + (slot.shape || "square") + (shown ? "" : " empty");
  box.title = slotHint(slot);
  box.appendChild(
    shown ? slotPicture(shown, slot.placeholder) : emptySlotPicture(slot, box)
  );
  // Обводка ступени на надетой вещи. Кукла открыта всем, кто смотрит
  // карточку, — по цвету рамки соперник и понимает, что вещь не простая
  if (shown && shown.mod && shown.mod.level) {
    box.classList.add("tier", "lvl" + shown.mod.level);
  }
  // Вещь на исходе видно прямо в клетке: рюкзак открывают не каждый
  // день, а рассыпается вещь надетой и посреди боя
  const dying = worstWear(slot);
  if (dying) {
    box.classList.add(dying);
    box.appendChild(wearBadge(dying, worstLeft(slot)));
  }
  box.addEventListener("click", () => {
    haptic((feedback) => feedback.selectionChanged());
    // В клетке тела вещей две: разбираем ту, что видно
    const item = slot.item || slot.under;
    const title = slot.item ? slot.title : slot.under_title;
    if (!item) {
      popup("Слот пуст", "Сюда надевается: " + slot.cell_title + ".");
      return;
    }
    if (info) {
      openWorn(item, title, slot);
      return;
    }
    if (!own) {
      // Чужая кукла внутри всплывающей карточки: открывать над ней
      // вторую створку некуда, и вещь рассказывает о себе запиской
      popup(item.title, slotHint(slot));
      return;
    }
    // Клик по надетой вещи возвращает её в инвентарь, но не молча:
    // промахнуться по слоту легко, а вещь при этом слетает. Спрашиваем
    // не голым «вы уверены?», а той же створкой со свойствами: перед
    // тем как снять, полезно увидеть, что именно теряешь
    openWorn(item, title, slot, () =>
      act("api/unequip", { slot: item.slot || slot.slot })
    );
  });
  container.appendChild(box);
}

function worstWear(slot) {
  // Самая изношенная вещь клетки: под курткой может доживать футболка
  const states = [slot.item, slot.under].filter(Boolean).map(wearState);
  if (states.includes("dying")) return "dying";
  if (states.includes("aging")) return "aging";
  return "";
}

function worstLeft(slot) {
  // Сколько боёв осталось у самой изношенной вещи клетки. По этому числу
  // и красится ключ: три — жёлтый, два — оранжевый, один — красный
  const lives = [slot.item, slot.under]
    .filter((one) => one && wearState(one))
    .map(wearLeft);
  return lives.length ? Math.min(...lives) : 0;
}

// Квадрат в углу клетки: сколько боёв вещи осталось. Три — жёлтый, два —
// оранжевый, один — красный. Сам квадрат и есть предупреждение: ключ
// внутри цветного кружка читался хуже, чем чистый цвет, и требовал
// подложки, которая на пёстрой картинке выглядела случайным овалом
const WEAR_LIGHTS = ["🟥", "🟥", "🟧", "🟨"];

/** Метка износа на клетке. Цвет говорит, сколько боёв вещи осталось. */
function wearBadge(state, left) {
  const mark = document.createElement("span");
  mark.className = "slot-wear " + state;
  mark.textContent = WEAR_LIGHTS[left] || WEAR_LIGHTS[3];
  mark.title = left
    ? "Осталось " + left + " " + fightWord(left)
    : "Вещь на исходе";
  return mark;
}

// Что надето: картинка, свойства и износ. Открывается с куклы персонажа
// и с чужой карточки — там, где вещь показывают, а не снимают
// `takeOff` — что сделать по кнопке «Снять». Не задан, значит вещь
// только показывают: так открывается створка с куклы персонажа.
function openWorn(item, slotTitle, slot, takeOff) {
  openSheet(item.title, slotTitle, true);
  el("sheet-list").appendChild(wornCard(item));
  // Под курткой бывает футболка, и её свойства тоже чьи-то: показываем
  // обе, иначе половина брони так и останется незамеченной
  const other = item === slot.item ? slot.under : null;
  if (other) {
    const head = document.createElement("p");
    head.className = "sheet-note";
    head.textContent = "Под ней: " + slot.under_title;
    el("sheet-list").append(head, wornCard(other));
  }
  if (takeOff) {
    el("sheet-list").appendChild(undressButtons(takeOff));
    return;
  }
  // Снять вещь можно в инвентаре, и сказать об этом стоит здесь: иначе
  // игрок ищет кнопку на экране, где её нарочно нет. Одной строкой на
  // всё окно, а не на каждой карточке: вещей в клетке бывает две, и
  // дважды повторённая подсказка читается как две разные
  const where = document.createElement("p");
  where.className = "sheet-note";
  where.textContent = "Снять — в инвентаре, нажатием на эту же клетку.";
  el("sheet-list").appendChild(where);
}

// Две кнопки под свойствами вещи. «Оставить» стоит первой и просто
// закрывает створку: промахнуться по слоту легко, и уход отсюда без
// последствий должен быть ближе, чем снятие
function undressButtons(takeOff) {
  const row = document.createElement("div");
  row.className = "thing-buttons";
  row.appendChild(
    button("Оставить", { secondary: true, onClick: closeSheet })
  );
  row.appendChild(
    button("Снять", {
      onClick: () => {
        // Створку закрываем до запроса: карточка после снятия
        // перерисовывается целиком, и оставленная поверх неё створка
        // показывала бы вещь, которой на бойце уже нет
        closeSheet();
        takeOff();
      },
    })
  );
  return row;
}

// Клетка картинки у строки с вещью. Форму задаёт слот, а не страница:
// ожерелье рисуется полосой 3:1 — такой же, как в кукле, — иначе квадрат
// срезает цепь с боков, и на прилавке не разглядеть, что покупаешь.
// Кольцо остаётся квадратным: в кукле оно у́же, но в списке товара
// колечко размером с ноготь не разглядел бы никто
function thingPic(item) {
  const pic = document.createElement("div");
  pic.className = "thing-pic" + (item.shape === "wide" ? " wide" : "");
  return pic;
}

function wornCard(item) {
  const box = document.createElement("div");
  box.className = "thing";

  const pic = thingPic(item);
  if (item.mod && item.mod.level) pic.classList.add("tier", "lvl" + item.mod.level);
  pic.appendChild(slotPicture(item, item.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = item.title;
  if (item.mod && item.mod.star) {
    const star = document.createElement("span");
    star.className = "thing-star lvl" + item.mod.level;
    star.textContent = item.mod.star;
    star.title = item.mod.title + ": " + item.mod.gain;
    title.appendChild(star);
  }
  body.appendChild(title);

  if (item.max_wear) body.appendChild(wearRow(item));

  if (item.bonuses && item.bonuses.length) {
    const label = document.createElement("div");
    label.className = "thing-label";
    label.textContent = "Даёт надетой";
    body.append(label, bonusList(item));
  }
  if (item.in_hands) {
    const hands = document.createElement("div");
    hands.className = "thing-note";
    hands.textContent = item.in_hands;
    body.appendChild(hands);
  }
  const worn_kit = kitNote(item);
  if (worn_kit) body.appendChild(worn_kit);

  box.appendChild(body);
  return box;
}

// ---------- инвентарь ----------

function requirementList(item) {
  const list = document.createElement("ul");
  list.className = "thing-req";
  item.requirements.forEach((need) => {
    const li = document.createElement("li");
    if (!need.ok) li.className = "bad";
    const label = need.emoji ? need.emoji + " " + need.title : need.title;
    li.textContent = label + ": " + need.need + " (есть " + need.have + ")";
    list.appendChild(li);
  });
  return list;
}

// Строка комплекта: сколько вещей набора надето и во что это превращает
// его оружие. Текст приходит с сервера целиком — лестницу считает он, а
// не страница: иначе два места считали бы одно и то же по-разному
function kitNote(item) {
  if (!item.kit || !item.kit.code) return null;
  const note = document.createElement("div");
  note.className = "thing-kit" + (item.kit.power > 100 ? " strong" : "");
  note.textContent = item.kit.text;
  return note;
}

function bonusList(item) {
  const list = document.createElement("ul");
  list.className = "thing-gain";
  item.bonuses.forEach((gain) => {
    const li = document.createElement("li");
    // диапазоны и проценты пишем через двоеточие, прибавки — со знаком плюс
    li.textContent =
      gain.text === undefined
        ? gain.emoji + " " + gain.title + " +" + gain.value
        : gain.emoji + " " + gain.title + ": " + gain.text;
    // Сколько в этом числе от мастера: «урон 12–16 (+5)». Число уже
    // посчитано с модификацией, и без подписи не видно, что она там есть
    if (gain.plus) {
      const plus = document.createElement("span");
      plus.className = "gain-plus";
      plus.textContent = " (" + gain.plus + ")";
      li.appendChild(plus);
    }
    // Класс проворачивает оружие по-своему: рядом с числом вещи говорим,
    // во что оно превратится в этих руках.
    if (gain.hint) {
      const hint = document.createElement("span");
      hint.className = "gain-hint";
      hint.textContent = " (" + gain.hint + ")";
      li.appendChild(hint);
    }
    list.appendChild(li);
  });
  return list;
}

function button(text, options) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn" + (options && options.secondary ? " secondary" : "");
  btn.textContent = text;
  if (options && options.disabled) {
    btn.disabled = true;
    if (options.hint) {
      // Заголовок общий: погасшая кнопка бывает не только у вещи —
      // у точки, у работы, у перевода, — и «нельзя надеть» на экране
      // территорий говорило бы про чужое
      btn.addEventListener("click", () => popup("Нельзя", options.hint));
    }
  } else if (options && options.onClick) {
    btn.addEventListener("click", options.onClick);
  }
  return btn;
}

// `bare` — карточка без своих кнопок: вещь показывают, но ничего с ней
// отсюда не делают. Так она выглядит у мастера, где с вещью делают ровно
// одно, и это одно приписывает сама вкладка
function thingCard(item, credits, shop, bare) {
  const box = document.createElement("div");
  box.className = "thing" + (shop && !item.unlocked ? " locked" : "");

  const pic = thingPic(item);
  // Модифицированную вещь обводим цветом ступени — так же, как в кукле
  if (item.mod && item.mod.level) pic.classList.add("tier", "lvl" + item.mod.level);
  pic.appendChild(slotPicture(item, item.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = item.title;
  // Звёздочка модификации: цвет говорит о ступени, подсказка — что дала
  if (item.mod && item.mod.star) {
    const star = document.createElement("span");
    star.className = "thing-star lvl" + item.mod.level;
    star.textContent = item.mod.star;
    star.title = item.mod.title + ": " + item.mod.gain;
    title.appendChild(star);
  }
  body.appendChild(title);

  const kind = document.createElement("div");
  kind.className = "thing-kind";
  kind.textContent = item.slot_title;
  body.appendChild(kind);

  // Пропуск копится стопкой, как склянка, хоть его и не пьют: без счёта
  // полоса износа «0/1» говорила бы, что талон один, а их может быть пять
  if (!shop && (item.consumable || item.kind === "pass")) {
    const have = document.createElement("div");
    have.className = "thing-have";
    have.textContent = "В рюкзаке: " + item.owned + " шт.";
    body.appendChild(have);

    const running = item.boost ? runningBoost() : null;
    if (running && running.code !== item.code) {
      const warn = document.createElement("div");
      warn.className = "thing-warn";
      warn.textContent = "⚠️ Вытеснит «" + running.title + "»";
      body.appendChild(warn);
    }
  }

  // Полосу износа рисуем только тому, у кого есть шкала: у склянки её нет,
  // и без этой проверки пропуск получал «Износ: undefined»
  if (!shop && !item.consumable && item.max_wear) {
    body.appendChild(wearRow(item));
  }

  if (shop) {
    const price = document.createElement("div");
    price.className = "thing-price" + (item.magic ? " stars" : "");
    price.textContent = item.magic ? item.stars + " ⭐" : item.price + " 💰";
    body.appendChild(price);

    if (item.suits.length) {
      const suits = document.createElement("div");
      suits.className = "thing-suits";
      suits.textContent =
        "Кому: " + item.suits.map((c) => c.emoji + " " + c.title).join(", ");
      body.appendChild(suits);
    }
    if (item.owned) {
      const owned = document.createElement("div");
      owned.className = "thing-owned";
      // Склянки копят стопкой, вещи — штуками: и говорим о них по-разному
      owned.textContent = item.consumable
        ? "🎒 В рюкзаке: " + item.owned + " шт."
        : item.owned > 1
          ? "✔ уже есть, штук: " + item.owned
          : "✔ уже есть";
      body.appendChild(owned);
    }
  }

  const reqLabel = document.createElement("div");
  reqLabel.className = "thing-label";
  reqLabel.textContent = "Требования";
  body.append(reqLabel, requirementList(item));

  if (item.note) {
    const note = document.createElement("div");
    note.className = "thing-note";
    note.textContent = item.note;
    body.appendChild(note);
  }

  if (item.bonuses.length) {
    const gainLabel = document.createElement("div");
    gainLabel.className = "thing-label";
    gainLabel.textContent = item.gain_title || "Даёт надетой";
    body.append(gainLabel, bonusList(item));
  }
  const kit = kitNote(item);
  if (kit) body.appendChild(kit);

  if (shop) {
    const buy = document.createElement("div");
    buy.className = "thing-buttons";
    if (!item.unlocked) {
      const locked = document.createElement("div");
      locked.className = "thing-locked";
      locked.textContent = "🔒 Откроется на " + item.level_required + " уровне";
      body.appendChild(locked);
    } else {
      buy.appendChild(
        item.magic
          ? button("Купить · " + item.stars + " ⭐", {
              onClick: () => buyRelic(item),
            })
          // Цена стоит на кнопке всегда, даже когда её нечем заплатить:
          // «не хватает кредитов» вместо числа не говорит, сколько
          // копить, — а счёт лежит тут же, сверху экрана
          : button("Купить · " + item.price + " 💰", {
              disabled: !item.affordable,
              onClick: () => purchase(item),
            })
      );
      body.appendChild(buy);
    }
    box.appendChild(body);
    return box;
  }

  if (bare) {
    box.appendChild(body);
    return box;
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";

  if (item.consumable) {
    buttons.appendChild(button("Использовать", { onClick: () => usePotion(item) }));
    body.appendChild(buttons);
    box.appendChild(body);
    return box;
  }

  // Пропуск не надевают и не пьют — он ждёт входа в подвал
  if (item.kind === "pass") {
    box.appendChild(body);
    return box;
  }

  // У кольца клеток три, и они равноправны: кнопка одна, а клетку
  // выбирают в окне — там видно, какая свободна, а какую новое кольцо
  // сменит. Три кнопки «надеть» подряд не сказали бы об этом ничего
  if (item.shape === "ring") {
    buttons.appendChild(
      button("Надеть", {
        disabled: !item.can_equip,
        hint: "Нужно подрасти: " + requirementText(item),
        onClick: () => askRingSlot(item),
      })
    );
  } else {
    item.slots.forEach((slot, index) => {
      const text = index === 0 ? "Надеть" : "Во вторую руку";
      buttons.appendChild(
        button(text, {
          secondary: index > 0,
          disabled: !item.can_equip,
          hint: "Нужно подрасти: " + requirementText(item),
          onClick: () => act("api/equip", { item_id: item.id, slot: slot.slot }),
        })
      );
    });
  }

  // Кнопки починки здесь больше нет: чинят у мастера, а не на ходу
  // Сдать можно любую вещь с прилавка, хоть разбитую: износ на выплату
  // не влияет
  if (item.buyback > 0) {
    buttons.appendChild(
      button("Сдать · " + item.buyback + " 💰", {
        secondary: true,
        onClick: () => handIn(item),
      })
    );
  }
  body.appendChild(buttons);

  box.appendChild(body);
  return box;
}

// Три клетки колец с карточки — в том порядке, в каком они на бойце.
// Берём их из последней отрисованной карточки: что в какой клетке лежит,
// знает она, а не строка рюкзака
function ringCells() {
  if (!cardData) return [];
  return cardData.slots.left
    .concat(cardData.slots.right)
    .filter((cell) => cell.shape === "ring");
}

// Окно выбора клетки под кольцо. Клетки равноправны, поэтому решает
// игрок: пустую видно пустой, занятую — с тем кольцом, которое в ней
// стоит, и нажатие на неё кольцо меняет.
function askRingSlot(item) {
  const cells = ringCells();
  if (!cells.length) {
    // Карточка ещё не пришла — надеваем как раньше, в первую клетку:
    // лучше надеть, чем показать пустое окно
    act("api/equip", { item_id: item.id, slot: item.slots[0].slot });
    return;
  }
  openSheet("В какой слот?", "Занятую клетку кольцо сменит: прежнее уйдёт в рюкзак.", true);
  const row = document.createElement("div");
  row.className = "ring-pick";
  cells.forEach((cell) => {
    const box = document.createElement("button");
    box.type = "button";
    box.className = "ring-cell" + (cell.item ? " busy" : "");

    const pic = document.createElement("div");
    pic.className = "slot ring" + (cell.item ? "" : " empty");
    pic.appendChild(
      cell.item ? slotPicture(cell.item, cell.placeholder) : emptySlotPicture(cell, pic)
    );

    const name = document.createElement("span");
    name.className = "ring-cell-name";
    name.textContent = cell.item ? cell.item.title : "Пусто";

    box.append(pic, name);
    box.addEventListener("click", () => {
      haptic((feedback) => feedback.selectionChanged());
      closeSheet();
      act("api/equip", { item_id: item.id, slot: cell.slot });
    });
    row.appendChild(box);
  });
  el("sheet-list").appendChild(row);
}

function requirementText(item) {
  return item.requirements
    .filter((need) => !need.ok)
    .map((need) => need.title.toLowerCase() + " " + need.need)
    .join(", ");
}

// ---------- награда за вход ----------
//
// Окно всплывает при первом за сутки открытии карточки, а если награду не
// забрали — всплывает и дальше, пока не заберут. Невзятое не сгорает, и
// прятать его было бы обманом.

// Окно всплывает один раз за открытие мини-аппа. Карточка перерисовывается
// много раз — после каждого действия, — и решать видимость на каждой
// отрисовке нельзя: окно закрывалось бы само собой под руками.
let dailyShown = false;

// Последняя награда за вход — чтобы окно можно было открыть кнопкой, а не
// только при заходе. Карточка перерисовывается часто, состояние живёт тут
let dailyState = null;

function renderDaily(card) {
  const state = card.daily;
  if (!state) return;
  dailyState = state;
  // Кнопку рисует `paintHeroTabs` — он идёт следом, уже зная про календарь

  const veil = el("daily-veil");
  if (dailyShown) {
    // Уже показывали: содержимое обновим, а прятать или открывать заново
    // не будем — это решает игрок
    if (veil.classList.contains("hidden")) return;
  } else if (state.fresh || state.waiting.length) {
    dailyShown = true;
  } else {
    veil.classList.add("hidden");
    return;
  }
  fillDaily(state);
  veil.classList.remove("hidden");
}

// Кнопка «Ежедневные награды» на карточке: календарь можно открыть в любой
// момент, а не только когда окно всплыло само
function openDaily() {
  if (!dailyState) return;
  dailyShown = true;
  fillDaily(dailyState);
  el("daily-veil").classList.remove("hidden");
}

function fillDaily(state) {
  el("daily-head").textContent = "🎁 Вход в клуб · день " + state.days;
  el("daily-note").textContent = state.waiting.length
    ? "Забирайте — награда ваша."
    : state.next_day
      ? "Сегодня награда получена. Приходите завтра."
      : "Календарь этого месяца пройден. В следующем начнётся заново.";

  // Сетка по семь в ряд. Клетка — это по счёту вход за месяц, а не число
  // месяца: подписывать её днями недели было бы враньём
  const ladder = el("daily-ladder");
  ladder.textContent = "";
  state.ladder.forEach((step) => {
    ladder.appendChild(giftCell(step, state));
  });
  describeGift(state.ladder.find((step) => step.ready) || null, state);

  const buttons = el("daily-buttons");
  buttons.textContent = "";
  if (state.waiting.length) {
    buttons.appendChild(button("Забрать", { onClick: claimDaily }));
  }
  buttons.appendChild(
    button(state.waiting.length ? "Потом" : "Закрыть", {
      secondary: true,
      onClick: () => el("daily-veil").classList.add("hidden"),
    })
  );
}

function giftCell(step, state) {
  const cell = document.createElement("button");
  cell.type = "button";
  cell.className =
    "gift" +
    (step.ready ? " ready" : "") +
    (step.done ? " done" : "") +
    (step.big ? " big" : "");
  cell.title = step.day + "-й день · " + step.title;

  const day = document.createElement("span");
  day.className = "gift-day";
  day.textContent = step.day;
  cell.appendChild(day);

  // Вещь показываем ею самой: 🧪 на все склянки разом ничего не говорит,
  // а картинка — та же, что потом ляжет в рюкзак. За кредиты картинки нет:
  // это не вещь, там и остаётся значок
  const icon = document.createElement("span");
  icon.className = "gift-icon";
  icon.textContent = step.icon;
  cell.appendChild(icon);
  if (step.image) {
    cell.classList.add("has-pic");
    const pic = document.createElement("img");
    pic.className = "gift-pic";
    pic.src = step.image;
    pic.alt = step.title;
    // Не доехал файл — убираем картинку, и клетка возвращается к значку:
    // он всё это время лежал под ней, просто был спрятан
    pic.addEventListener("error", () => {
      pic.remove();
      cell.classList.remove("has-pic");
    });
    cell.appendChild(pic);
  }

  // Забранное помечено зелёной галочкой — по ней видно пройденный месяц
  if (step.done) {
    const mark = document.createElement("span");
    mark.className = "gift-mark";
    mark.textContent = "✔";
    cell.appendChild(mark);
  }

  cell.addEventListener("click", () => describeGift(step, state));
  return cell;
}

// Строка под календарём: что лежит в выбранной клетке. Тридцать значков
// сами за себя не скажут, а подписывать каждый негде
function describeGift(step, state) {
  const box = el("daily-pick");
  if (!step) {
    box.textContent = state.next_day
      ? "Нажмите на клетку, чтобы посмотреть, что в ней лежит."
      : "Весь календарь пройден.";
    return;
  }
  const mark = step.done ? " · забрано" : step.ready ? " · ждёт вас" : "";
  box.textContent =
    step.day + "-й день: " + step.icon + " " + step.title + mark +
    (step.note ? " — " + step.note : "");
}

async function claimDaily() {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/daily", {});
    // Окно закрываем до сообщения, а не после: сообщение блокирует поток
    // до нажатия «ок», и закрытие повисало бы за ним
    el("daily-veil").classList.add("hidden");
    render(data.card, true);
    const done = data.done;
    const parts = done.rewards.map((one) => one.icon + " " + one.title);
    popup("Награда за вход", parts.join("\n"));
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// Приёмы в карточке: что выучено и что предстоит выбрать. Список видят
// все, кто открыл карточку, — соперник должен знать, чего ждать. Развилку
// показываем только хозяину: это про то, чего у бойца ещё нет.
function renderSkills(card) {
  const box = el("skills-box");
  const state = card.abilities;
  if (!state || (!state.known.length && !state.choice)) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  el("skills-count").textContent = "· " + state.known.length + " из " + state.slots;

  const list = el("skills-list");
  list.textContent = "";
  state.known.forEach((trick) => list.appendChild(skillCard(trick)));

  // Развилка — отдельной плашкой под списком: её нельзя не заметить
  const old = box.querySelector(".fork");
  if (old) old.remove();
  if (state.choice) box.appendChild(forkCard(state.choice));

  const note = el("skills-note");
  note.textContent = state.choice
    ? ""
    : state.next_tier
      ? "Следующий приём — на " + state.next_tier + " уровне."
      : // Ступени кончились, а приёмы нет: дальше их берут в школе
        // боевых искусств — за дела в бою и по наименьшей цене
        "Все приёмы своих ступеней взяты. Новый берут в школе боевых " +
        "искусств — за дела в бою.";
}

function skillCard(trick) {
  const box = document.createElement("div");
  box.className = "skill";

  const pic = document.createElement("div");
  pic.className = "skill-pic";
  pic.appendChild(slotPicture(trick, trick.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "skill-body";

  const title = document.createElement("div");
  title.className = "skill-title";
  title.textContent = trick.title;
  body.appendChild(title);

  const cost = document.createElement("div");
  cost.className = "skill-cost";
  cost.textContent = trick.cost + " ⚡ · выучен на " + trick.tier + " уровне";
  body.appendChild(cost);

  const note = document.createElement("div");
  note.className = "skill-note";
  note.textContent = trick.note;
  body.appendChild(note);

  box.appendChild(body);
  return box;
}

function forkCard(choice) {
  const box = document.createElement("div");
  box.className = "fork";

  const head = document.createElement("p");
  head.className = "fork-head";
  head.textContent = "🎓 " + choice.tier + " уровень: выберите приём";
  box.appendChild(head);

  const warn = document.createElement("p");
  warn.className = "fork-warn";
  warn.textContent = "Выбор один и навсегда: два других приёма не достанутся.";
  box.appendChild(warn);

  choice.options.forEach((option) => {
    const card = skillCard({ ...option, tier: choice.tier });
    card.classList.add("pickable");
    if (option.own) {
      const mark = document.createElement("span");
      mark.className = "fork-own";
      mark.textContent = "свой";
      card.querySelector(".skill-title").appendChild(mark);
    }
    card.addEventListener("click", () => pickAbility(option, choice.tier));
    box.appendChild(card);
  });
  return box;
}

async function pickAbility(option, tier) {
  if (busy) return;
  const ok = await confirmAction(
    "Выучить «" + option.title + "»?\n\n" +
      "Два других приёма " + tier + " уровня больше не предложат."
  );
  if (!ok) return;
  busy = true;
  try {
    const data = await post("api/ability", { code: option.code });
    render(data.card, true);
    popup(data.done.icon + " " + data.done.title, data.done.note);
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

function renderBag(card) {
  const bag = el("bag-box");
  if (!card.is_self) {
    bag.classList.add("hidden");
    return;
  }
  bag.classList.remove("hidden");
  const potions = card.potions || [];
  el("bag-count").textContent = card.inventory.length
    ? "· " + card.inventory.length
    : "";
  // «Рюкзак пуст» — только когда пуст совсем: склянки тоже вещи
  el("bag-empty").classList.toggle(
    "hidden",
    card.inventory.length > 0 || potions.length > 0
  );

  const list = el("bag-list");
  list.textContent = "";
  card.inventory.forEach((item) => {
    list.appendChild(thingCard(item, card.record.credits));
  });

  // Склянки стоят своей полкой: их пьют, а не надевают
  const box = el("potion-box");
  box.classList.toggle("hidden", potions.length === 0);
  el("potion-count").textContent = potions.length ? "· " + potions.length : "";
  const shelf = el("potion-list");
  shelf.textContent = "";
  potions.forEach((potion) => {
    shelf.appendChild(thingCard(potion, card.record.credits));
  });
}

// ---------- раздача свободных очков ----------

// Черновик живёт на странице, пока его не применили: пока боец щёлкает
// плюсами, сервер об этом ничего не знает и знать не должен.
let draft = {};
let cardStats = [];
let freePoints = 0;

function draftTotal() {
  return Object.values(draft).reduce((sum, value) => sum + value, 0);
}

function draftLeft() {
  return freePoints - draftTotal();
}

function statStep(stat) {
  const row = document.createElement("li");
  row.className = "up-row";

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = stat.emoji + " " + stat.title;

  const value = document.createElement("span");
  value.className = "up-value";
  const added = draft[stat.code] || 0;
  value.textContent = stat.base + (added ? " + " + added : "");
  if (added) value.classList.add("added");

  const minus = document.createElement("button");
  minus.type = "button";
  minus.className = "step";
  minus.textContent = "−";
  minus.disabled = !added;
  minus.addEventListener("click", () => {
    draft[stat.code] = Math.max(0, (draft[stat.code] || 0) - 1);
    if (!draft[stat.code]) delete draft[stat.code];
    paintUpgrade();
  });

  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "step";
  plus.textContent = "+";
  plus.disabled = draftLeft() <= 0;
  plus.addEventListener("click", () => {
    if (draftLeft() <= 0) return;
    draft[stat.code] = (draft[stat.code] || 0) + 1;
    haptic((feedback) => feedback.selectionChanged());
    paintUpgrade();
  });

  row.append(label, minus, value, plus);
  return row;
}

function paintUpgrade() {
  const box = el("upgrade");
  box.textContent = "";
  box.classList.toggle("hidden", freePoints <= 0);
  if (freePoints <= 0) {
    draft = {};
    return;
  }

  const head = document.createElement("div");
  head.className = "up-head";
  head.textContent =
    "✨ Свободных очков: " + draftLeft() + " из " + freePoints;
  box.appendChild(head);

  const rows = document.createElement("ul");
  rows.className = "rows";
  cardStats.forEach((stat) => rows.appendChild(statStep(stat)));
  box.appendChild(rows);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Сохранить", {
      disabled: draftTotal() <= 0,
      onClick: applyUpgrade,
    })
  );
  if (draftTotal() > 0) {
    buttons.appendChild(
      button("Сбросить", {
        secondary: true,
        onClick: () => {
          draft = {};
          paintUpgrade();
        },
      })
    );
  }
  box.appendChild(buttons);

  const note = document.createElement("div");
  note.className = "up-note";
  note.textContent = "После сохранения поменять выбор будет уже нельзя";
  box.appendChild(note);
}

async function applyUpgrade() {
  if (busy || draftTotal() <= 0) return;
  // Называем выбор словами, а не числом очков: человек соглашается с тем,
  // что увидит на карточке, — «+2 к силе», а не «2 очка».
  const chosen = cardStats
    .filter((stat) => draft[stat.code])
    .map((stat) => "+" + draft[stat.code] + " к " + stat.dative)
    .join("\n");
  const ok = await confirmAction(
    "Сохранить выбор:\n" + chosen + "\n\nПоменять его будет уже нельзя."
  );
  if (!ok) return;

  busy = true;
  const spent = draftTotal();
  try {
    const data = await post("api/upgrade", draft);
    draft = {};
    render(data.card, true);
    haptic((feedback) => feedback.notificationOccurred("success"));
    popup(
      "✨ Характеристики выросли",
      "Вложено очков: " + spent + "."
        + (data.left ? "\nОсталось свободных: " + data.left + "." : "")
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// ---------- действующие эффекты ----------

// Сколько эффекту осталось, считаем от отметки, снятой при отрисовке:
// часы на телефоне могут расходиться с серверными.
let effects = [];
let effectsAt = 0;

function spell(seconds) {
  const left = Math.max(0, Math.round(seconds));
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  if (hours && minutes) return hours + " ч " + minutes + " мин";
  if (hours) return hours + " ч";
  if (minutes) return minutes + " мин";
  return left + " сек";
}

function paintEffects() {
  const passed = (Date.now() - effectsAt) / 1000;
  const live = effects.filter((effect) => effect.seconds_left - passed > 0);
  // Травма живёт в той же строке, что и эликсиры: и то и другое висит на
  // бойце по часам. Только эликсир даёт, а травма отнимает — отсюда и
  // красная плашка, и место в начале строки
  const hurt = injuryChip(passed);
  ["effects", "hero-effects"].forEach((id) => {
    const box = el(id);
    box.textContent = "";
    box.classList.toggle("hidden", live.length === 0 && !hurt);
    if (hurt) box.appendChild(hurt.cloneNode(true));
    live.forEach((effect) => {
      const chip = document.createElement("span");
      chip.className = "effect";
      chip.textContent =
        effect.emoji + " " + effect.title + " · " + spell(effect.seconds_left - passed);
      chip.title = effect.gain;
      box.appendChild(chip);
    });
  });
  // Эффект догорел — в характеристиках он больше не учитывается, значит
  // карточку пора перечитать. Список сужаем сразу, иначе будем звать сервер
  // каждую секунду.
  if (live.length < effects.length) {
    effects = live;
    refresh();
  }
  // То же и с травмой: отлежала своё — характеристика вернулась, и
  // карточку надо перечитать. Забываем её сразу, чтобы не звать сервер
  // на каждой секунде
  if (injury && !hurt) {
    injury = null;
    refresh();
  }
}

// Травма бойца по последней карточке: код, слова и сколько осталось
let injury = null;

function injuryChip(passed) {
  if (!injury || !injury.text) return null;
  const left = Math.max(0, (injury.seconds_left || 0) - passed);
  if (left <= 0) return null;
  const chip = document.createElement("span");
  chip.className = "effect hurt";
  // Слова приходят с сервера целиком, а тикающий остаток дописываем на
  // месте: перечитывать карточку каждую секунду ради минуты незачем
  chip.textContent =
    "🤕 " + injury.hurt_title[0].toUpperCase() + injury.hurt_title.slice(1) +
    ": " + injury.title + ". Ещё " + longSpell(left) + ".";
  if (injury.crippled) chip.textContent += " Драться нельзя.";
  return chip;
}

// «10 часов 15 минут» — словами, как это написано в карточке
function longSpell(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const parts = [];
  if (hours) parts.push(hours + " " + plural(hours, "час", "часа", "часов"));
  if (minutes) {
    parts.push(minutes + " " + plural(minutes, "минута", "минуты", "минут"));
  }
  return parts.length ? parts.join(" ") : "меньше минуты";
}

function plural(count, one, few, many) {
  const last = count % 10;
  const pair = count % 100;
  if (pair >= 11 && pair <= 14) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

function startEffects(list, hurt) {
  effects = list || [];
  injury = hurt && hurt.text ? hurt : null;
  effectsAt = Date.now();
  paintEffects();
}

// ---------- магазин ----------

// Что показывать на прилавке. Фильтр один — тип вещи; уровень не фильтруем:
// закрытое и так свёрнуто в конце каждой полки.
const filters = { slot: "all" };

/** Поле с подписью: подпись сверху, само поле под ней.
 *
 * Числовое поле без подписи читается как попало: ноль в нём может
 * означать что угодно, и угадывать это игрок не должен.
 */
function field(title, inner) {
  const box = document.createElement("div");
  box.className = "field";
  const head = document.createElement("div");
  head.className = "field-head";
  head.textContent = title;
  box.append(head, inner);
  return box;
}

function chip(label, active, onClick, extraClass) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chip" + (active ? " on" : "") + (extraClass ? " " + extraClass : "");
  btn.textContent = label;
  btn.addEventListener("click", () => {
    haptic((feedback) => feedback.selectionChanged());
    onClick();
  });
  return btn;
}

function pickSlot(value) {
  filters.slot = value;
  renderShop(shopData);
}

function shownItems(section) {
  return section.items.filter((item) => item.unlocked);
}

function hiddenItems(section) {
  return section.items.filter((item) => !item.unlocked);
}

function renderFilters(data) {
  const types = el("filter-type");
  types.textContent = "";
  types.appendChild(chip("Все", filters.slot === "all", () => pickSlot("all")));
  data.sections.forEach((section) => {
    types.appendChild(
      chip(section.title, filters.slot === section.slot, () => pickSlot(section.slot))
    );
  });
}

function shelf(section) {
  const shown = shownItems(section);
  const locked = hiddenItems(section);
  // Пустую полку прячем, только когда её отфильтровали. Раздел, в котором
  // товара ещё нет вовсе, показываем: пусть видно, что он готовится.
  if (!shown.length && !locked.length && section.items.length) return null;

  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = section.title;
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = "открыто " + section.open + " из " + section.items.length;
  head.appendChild(count);
  box.appendChild(head);

  const list = document.createElement("div");
  list.className = "shelf-list";
  if (!section.items.length) {
    const soon = document.createElement("p");
    soon.className = "shelf-empty";
    soon.textContent = "Скоро завезут.";
    list.appendChild(soon);
  }
  shown.forEach((item) => list.appendChild(thingCard(item, 0, true)));
  box.appendChild(list);

  // Закрытое не мозолит глаза, но посмотреть, к чему готовиться, можно
  if (locked.length) {
    const hidden = document.createElement("div");
    hidden.className = "shelf-list hidden";
    locked.forEach((item) => hidden.appendChild(thingCard(item, 0, true)));

    const toggle = button("🔒 Показать закрытые · " + locked.length, {
      secondary: true,
      onClick: () => {
        const stashed = hidden.classList.toggle("hidden");
        toggle.textContent = stashed
          ? "🔒 Показать закрытые · " + locked.length
          : "Свернуть закрытые";
      },
    });
    toggle.classList.add("shelf-toggle");
    box.append(toggle, hidden);
  }
  return box;
}

function shopNote(data) {
  // Фанатский прилавок открывается целиком и сразу: там одна ступень,
  // и «следующая партия» на ней не бывает
  if (data.service === "fan") {
    return "Экипировка своей команды: одна ступень, десятый уровень. "
      + "Копится долго — зато носится до конца.";
  }
  const next = data.sections
    .flatMap((section) => section.items)
    .filter((item) => !item.unlocked)
    .reduce((min, item) => Math.min(min, item.level_required), 99);
  return next < 99
    ? "Товар открывается уровнем. Следующая партия — на " + next + " уровне."
    : "Открыто всё, что есть на прилавке.";
}

// Какая лавка открыта на вкладке магазинов
let shopSection = "club";

// Как называется прилавок, на который смотрят. Раньше лавка была одна и
// звалась «Лавкой клуба»; теперь это дома на карте, и у каждого своё имя
const SHOP_TITLES = {
  weapons: "🗡 Оружейный магазин",
  clothes: "👕 Магазин одежды",
  potions: "💊 Аптека",
  fan: "🧣 Магазин «Северный Вал»",
  jewel: "💍 Ювелирный магазин",
  market: "🤝 Комиссионный магазин",
};

function openShop() {
  pickShopSection("club");
  showTab("shop");
}

function pickShopSection(name) {
  shopSection = name;
  el("shop-club").classList.toggle("hidden", name !== "club");
  el("shop-market").classList.toggle("hidden", name !== "market");
  if (name === "market") {
    el("shop-title").textContent = SHOP_TITLES.market;
    loadMarket();
  }
}

function renderShop(data) {
  shopData = data;
  el("shop-purse").textContent = "";
  el("shop-purse").appendChild(purse(data.credits));
  el("shop-note").textContent = shopNote(data);
  el("shop-title").textContent = SHOP_TITLES[data.service] || "🏪 Лавка";
  renderFilters(data);

  const list = el("shop-list");
  list.textContent = "";
  // Кошелёк — над прилавком: цены на витрине уже посчитаны под него,
  // и переключают его здесь же, а не сходив за этим в банк
  list.appendChild(walletBar(data.purse, loadShop));
  data.sections
    .filter((section) => filters.slot === "all" || section.slot === filters.slot)
    .forEach((section) => {
      const shelved = shelf(section);
      if (shelved) list.appendChild(shelved);
    });
  el("shop-empty").classList.toggle("hidden", list.childElementCount > 0);
}

// ---------- комиссионка ----------
//
// Вещи игроков лежат на тех же полках по типам, что и товар лавки. Всё, что
// тут можно сделать, уходит одной ручкой: выставить своё, снять своё, купить
// чужое — и в ответ приходит вся полка целиком.

let marketData = null;
let marketBusy = false;

async function loadMarket() {
  try {
    const response = await fetch("api/market", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (response.status === 404) {
      el("market-note").textContent = "Сначала заведи бойца в личке бота.";
      return;
    }
    if (!response.ok) throw new Error("Комиссионка не отвечает.");
    renderMarket(await response.json());
  } catch (error) {
    el("market-note").textContent = error.message;
  }
}

async function buyLot(lot) {
  // Чужая вещь стоит столько, сколько попросили, — спрашиваем так же, как
  // и на прилавке клуба
  const ok = await askConfirm(
    "🛍 Покупка",
    "Вы приобретаете предмет " + lot.title + " за " + lot.price + " кредитов"
  );
  if (ok) marketAction({ action: "buy", lot_id: lot.id });
}

async function marketAction(payload) {
  if (marketBusy) return;
  marketBusy = true;
  // Всё, что между поднятым флагом и `finally`, обязано лежать внутри `try`:
  // иначе любой бросок оставляет флаг поднятым, и кнопки замолкают навсегда
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/market", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      popup("Комиссионка", body.error || "Не вышло.");
      return;
    }
    renderMarket(body);
    // Кошелёк и рюкзак поменялись — перечитываем карточку и лавку
    shopData = null;
    refresh();
  } catch (error) {
    popup("Комиссионка", error.message);
  } finally {
    marketBusy = false;
  }
}

function marketFollowsBag() {
  // Открыта комиссионка — перечитываем сразу, закрыта — забываем прошлый
  // ответ, чтобы при следующем заходе он не подсунул старый рюкзак
  const open = shopSection === "market" && !el("shop").classList.contains("hidden");
  if (open) loadMarket();
  else marketData = null;
}

// Комиссионка живёт двумя делами, и они не смешиваются: за одним сюда
// приходят купить, за другим — сдать. Раньше они лежали одним свитком,
// и покупателю приходилось пролистывать собственный рюкзак, чтобы
// добраться до чужих лотов.
const MARKET_TABS = [
  ["shelf", "🛒 Прилавок"],
  ["mine", "🤝 Продать своё"],
];
let marketTab = "shelf";

function pickMarketTab(name) {
  marketTab = name;
  if (marketData) renderMarket(marketData);
}

// Прилавок — это чужое. Свои лоты стоят на второй вкладке, рядом с
// рюкзаком: снять с продажи — дело продавца, а не покупателя
function othersShelves(data) {
  return data.sections
    .map((section) => ({
      ...section,
      items: section.items.filter((lot) => !lot.mine),
    }))
    .filter((section) => section.items.length);
}

function renderMarket(data) {
  marketData = data;
  el("shop-purse").textContent = "";
  el("shop-purse").appendChild(purse(data.credits));

  const shelves = othersShelves(data);
  const strangers = shelves.reduce((all, row) => all + row.items.length, 0);
  const mine = data.mine || [];
  el("market-note").textContent = marketTab === "shelf"
    ? strangers
      ? "Вещи игроков клуба. Клуб берёт " + data.fee + "% с каждой продажи."
      : "На прилавке пусто: чужого никто не выставил."
    : "Выставленное ждёт покупателя. Клуб берёт " + data.fee + "% с продажи.";

  const body = el("market-body");
  body.textContent = "";
  body.appendChild(walletBar(data.purse, loadMarket));

  const tabs = document.createElement("div");
  tabs.className = "bubbles";
  tabs.id = "market-tabs";
  MARKET_TABS.forEach(([code, label]) => {
    const count = code === "shelf" ? strangers : mine.length + data.sellable.length;
    tabs.appendChild(
      chip(
        count ? label + " · " + count : label,
        marketTab === code,
        () => pickMarketTab(code)
      )
    );
  });
  body.appendChild(tabs);

  if (marketTab === "shelf") {
    if (!shelves.length) body.appendChild(emptyShelf("Чужих вещей сейчас нет."));
    shelves.forEach((section) => body.appendChild(marketShelf(section)));
    return;
  }
  if (mine.length) body.appendChild(mineBox(mine));
  body.appendChild(sellBox(data));
}

function emptyShelf(said) {
  const box = document.createElement("section");
  box.className = "shelf";
  const empty = document.createElement("p");
  empty.className = "shelf-empty";
  empty.textContent = said;
  box.appendChild(empty);
  return box;
}

function mineBox(mine) {
  // Что уже стоит на прилавке от тебя: отсюда же и снимается
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "📤 На продаже";
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = "лотов " + mine.length;
  head.appendChild(count);
  box.appendChild(head);

  const list = document.createElement("div");
  list.className = "shelf-list";
  mine.forEach((lot) => list.appendChild(lotCard(lot)));
  box.appendChild(list);
  return box;
}

function sellBox(data) {
  // Что можно выставить: всё, что лежит в рюкзаке и не надето
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🎒 В рюкзаке";
  box.appendChild(head);

  if (!data.sellable.length) {
    const empty = document.createElement("p");
    empty.className = "shelf-empty";
    empty.textContent = "В рюкзаке пусто. Надетое сначала снимают.";
    box.appendChild(empty);
    return box;
  }
  const list = document.createElement("div");
  list.className = "shelf-list";
  data.sellable.forEach((row) => list.appendChild(sellCard(row)));
  box.appendChild(list);
  return box;
}

function sellCard(row) {
  const box = document.createElement("div");
  box.className = "thing";

  const pic = thingPic(row);
  pic.appendChild(slotPicture(row, row.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = row.title;
  const kind = document.createElement("div");
  kind.className = "thing-kind";
  kind.textContent = row.slot_title;
  // Износ — той же строкой, что и в рюкзаке: вещь на грани здесь видно
  // особенно кстати, за такую на комиссионке не дадут ничего
  const wear = wearRow(row);
  const hint = document.createElement("div");
  hint.className = "thing-note";
  hint.textContent = row.hint;
  body.append(title, kind, wear, hint);

  const line = document.createElement("div");
  line.className = "sell-line";
  const price = document.createElement("input");
  price.type = "number";
  price.className = "sell-price";
  price.min = String(row.min_price);
  if (row.max_price) price.max = String(row.max_price);
  price.value = String(row.min_price);
  price.setAttribute("aria-label", "Цена");
  line.appendChild(price);
  line.appendChild(
    button("Выставить", {
      onClick: () =>
        marketAction({
          action: "sell",
          item_id: row.id,
          price: Number(price.value) || 0,
        }),
    })
  );
  body.appendChild(line);

  box.appendChild(body);
  return box;
}

function marketShelf(section) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = section.title;
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = "лотов " + section.items.length;
  head.appendChild(count);
  box.appendChild(head);

  const list = document.createElement("div");
  list.className = "shelf-list";
  section.items.forEach((lot) => list.appendChild(lotCard(lot)));
  box.appendChild(list);
  return box;
}

function lotCard(lot) {
  const box = document.createElement("div");
  box.className = "thing" + (lot.mine ? " mine" : "");

  const pic = thingPic(lot);
  pic.appendChild(slotPicture(lot, lot.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = lot.title;
  const kind = document.createElement("div");
  kind.className = "thing-kind";
  kind.textContent = lot.slot_title;
  const seller = document.createElement("div");
  seller.className = "thing-seller";
  seller.textContent = lot.mine ? "Твой лот" : "Продаёт: " + lot.seller;
  // Чужую вещь на грани покупатель обязан видеть до, а не после покупки
  const wear = wearRow(lot);
  const price = document.createElement("div");
  price.className = "thing-price";
  price.textContent = lot.price + " 💰";
  if (lot.shop_price) price.textContent += " · в лавке " + lot.shop_price + " 💰";
  body.append(title, kind, seller, wear, price);

  if (lot.mine) {
    const take = document.createElement("div");
    take.className = "thing-note";
    take.textContent = "Продадут — придёт " + lot.payout + " 💰 (клуб возьмёт " +
      lot.fee + ")";
    body.appendChild(take);
  }

  const reqLabel = document.createElement("div");
  reqLabel.className = "thing-label";
  reqLabel.textContent = "Требования";
  body.append(reqLabel, requirementList(lot));

  if (lot.bonuses.length) {
    const gainLabel = document.createElement("div");
    gainLabel.className = "thing-label";
    gainLabel.textContent = "Даёт надетой";
    body.append(gainLabel, bonusList(lot));
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    lot.mine
      ? button("Снять с продажи", {
          secondary: true,
          onClick: () => marketAction({ action: "withdraw", lot_id: lot.id }),
        })
      // Цена на кнопке стоит всегда — и когда кредитов не хватает тоже
      : button("Купить · " + lot.price + " 💰", {
          disabled: !lot.affordable,
          onClick: () => buyLot(lot),
        })
  );
  body.appendChild(buttons);

  box.appendChild(body);
  return box;
}

const SCREENS = [
  "club", "map", "shop", "magic", "workshop", "hospital", "insurance", "gym",
  "bank", "hr", "work", "school", "police", "mafia", "flats", "post", "tavern",
  "study", "turf", "crowd", "elections", "trade", "house", "prison", "arena",
  "cars", "dorm", "charge", "dojo", "barber", "bag", "hero",
];
// Вкладок меньше, чем экранов: лавки открываются с карты, а не с панели.
// Пока в них стоишь, горит «Карта» — оттуда в них и пришли.
// Своей вкладки у клана нет: состав и имущество лежат в особняке, а
// быстрая правка состава — в карточке, под местоположением
const TABS = ["club", "map", "bag", "hero"];
const OPENED_FROM = {
  shop: "map", magic: "map", workshop: "map", hospital: "map", trade: "map",
  insurance: "map", gym: "map", bank: "map", hr: "map", work: "map",
  school: "map", police: "map", mafia: "map", flats: "map", post: "map",
  tavern: "map", study: "map", turf: "map", crowd: "map",
  elections: "map", house: "map", prison: "map", arena: "map", cars: "map",
  dorm: "map", charge: "map", dojo: "map", barber: "map",
};
let lastTab = "hero";

function showTab(name) {
  SCREENS.forEach((screen) => {
    el(screen).classList.toggle("hidden", screen !== name);
  });
  const lit = OPENED_FROM[name] || name;
  TABS.forEach((tab) => {
    el("tab-" + tab).classList.toggle("active", tab === lit);
  });
  el("topup").classList.add("hidden");
  lastTab = name;
  window.scrollTo(0, 0);
  // На этих двух экранах живут уровень и свободные очки: заходим — сверяемся
  if (name === "hero" || name === "bag") catchUp();
  // Прилавок у каждой лавки свой — перечитываем при каждом заходе.
  // Комиссионка живёт на том же экране, но это другой дом: её полка
  // приходит своей ручкой, и грузить поверх неё лавку нельзя
  if (name === "shop") {
    if (shopSection === "market") pickShopSection("market");
    else loadShop();
  }
  if (name === "workshop") loadWorkshop();
  if (name === "hospital") loadHospital();
  if (name === "insurance") loadInsurance();
  // Часы тренировки идут, только пока на зал смотрят
  if (name === "gym") loadGym();
  else stopGymClock();
  if (name === "bank") loadBank();
  if (name === "hr") loadHr();
  // Часы билета идут, только пока на экзамен смотрят: ушёл с экрана —
  // время всё равно течёт, его держит сервер, а стрелки рисовать некому
  if (name === "school") loadSchool();
  else stopExamClock();
  if (name === "police") loadPolice();
  // Особняк перечитываем при каждом заходе: заявки, чины и сейф
  // меняются чужими руками
  if (name === "mafia") loadMafia();
  // И подъезд тоже: квартиры снимают и освобождают без нас
  if (name === "flats") loadFlats();
  // Почта: посылки кладут и забирают чужими руками
  if (name === "post") loadPost();
  // Бар: у бармена могло появиться дело, пока нас не было
  if (name === "tavern") loadBar();
  // Учёба: занятие могло кончиться, пока на неё не смотрели.
  // Часы занятия идут, только пока экран открыт — время всё равно
  // считает сервер, а стрелки рисовать некому
  if (name === "study") loadStudy();
  else stopStudyClock();
  // Война: пока на карту войны смотрят — опрашиваем, ушли — молчим
  if (name === "turf") {
    loadTurf();
    armTurfPoll(10000);
  } else stopTurfPoll();
  // Соседи и драки: то же самое, и по той же причине — драка идёт без
  // нас, и узнать о ней можно только спросив
  if (name === "crowd") {
    loadCrowd();
    armCrowdPoll(10000);
  } else stopCrowdPoll();
  // Администрация: фаза цикла могла смениться, пока на неё не смотрели
  if (name === "elections") loadElections();
  // Тюрьма: сроки идут и без нас, но стрелки рисуем, только пока на
  // них смотрят. Ушёл с экрана — часы гасим
  if (name === "prison") loadPrison();
  else stopPrisonClock();
  // Арена: круги идут по часам, и пока на неё смотрят — спрашиваем
  // сервер, не пора ли следующий
  if (name === "arena") {
    loadArena();
    armArenaPoll();
  } else stopArenaPoll();
  // Салон и гараж: машины берут и сдают чужими руками — в гараже три
  // места, и занять их могли, пока мы шли
  if (name === "cars") loadCars();
  // Общежитие: комната могла запереться, пока в него не заходили —
  // учёба кончается и без нас
  if (name === "dorm") loadDorm();
  // АЗС: колонки общие на город, и занять их могли, пока мы ехали.
  // Ушли с экрана — часы колонок гасим: тикать им в пустоту незачем
  if (name === "charge") loadCharge();
  else stopChargeClock();
  // Школа: ведомость растёт в боях, и к ней возвращаются после них —
  // спрашиваем на каждый заход
  if (name === "dojo") loadDojo();
  // Полка образов: кредитов могло стать больше или меньше, а образ —
  // купиться в другом доме того же пола
  if (name === "barber") loadBarber();
  // Ключи при бойце: лежат они в инвентаре, вместе со всем, что боец
  // носит с собой. Машина могла кончиться арендой, пока туда не
  // заглядывали, — спрашиваем на каждый заход
  if (name === "bag") loadKeys();
  // Часы смены идут, только пока на работу смотрят
  if (name === "work") loadWork();
  else stopWorkClock();
  // Кнопка «Работа» встаёт в шапку того дома, что сейчас открыт
  paintWorkEntry(name);
  // А рядом с ней — «Рейд», если в этом доме он идёт. Дверь дома может
  // вести не к рейду: на полигоне она ведёт на занятия, и без этой
  // кнопки штурм пришлось бы искать во вкладке «Клуб»
  paintRaidEntry(name);
  // Стол на рынке общий: пока на него смотрят — опрашиваем, ушли — молчим
  if (name === "trade") startTradeWatch();
  else stopTradeWatch();
  if (name === "map") loadMap();
  // Часы рейда идут, только пока на карту смотрят
  if (name === "map") startRaidClock();
  else stopRaidClock();
  if (name === "club" && !clubData) loadClub();
  if (name === "magic" && !magicData) loadMagic();
  // Заходим на экран клуба — раздел пересобирается под здешний дом
  if (name === "club") pickClubSection(clubSection);
  // Ринг опрашиваем, только пока на него смотрят и только там, где
  // дерутся: ушли со вкладки или стоите в аптеке — молчим
  if (name === "club" && clubHouse === "club" && canFightHere()) {
    startWatchingFights();
  } else {
    stopWatchingFights();
  }
}

// Касса — не вкладка, а лист поверх экрана: в панель она не попадает
function showTopUp() {
  SCREENS.forEach((screen) => el(screen).classList.add("hidden"));
  el("topup").classList.remove("hidden");
  window.scrollTo(0, 0);
  loadTopUp();
}

// ---------- карта города ----------
//
// Дома — кнопки поверх картинки. Класть их по размеру окна нельзя:
// картинка показывается целиком (`object-fit: contain`), и на экране с
// другим соотношением сторон сверху и снизу появляются поля. Поэтому
// каждый раз меряем, куда картинка легла на самом деле, и от этого
// прямоугольника и считаем.

let mapData = null;
let mapShown = "";  // какой район открыт: смотреть можно любой
let mapWas = "";    // где боец стоял в прошлый раз — чтобы идти за ним
let roadTimer = null;

async function loadMap() {
  try {
    const response = await fetch("api/map", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Карта не открылась.");
    renderMap(await response.json());
  } catch (error) {
    popup("Карта", error.message);
  }
}

function renderMap(data) {
  mapData = data;
  // Пришли на карту — показываем тот район, где стоим. Дальше человек
  // листает сам, и его выбор не сбрасывается каждым обновлением. Но
  // когда боец сам перешёл в другой район, карта идёт за ним: иначе
  // после дороги он стоит на одной карте, а смотрит на другую
  if (
    !mapShown
    || !data.districts.some((one) => one.code === mapShown)
    || (data.district && data.district !== mapWas)
  ) {
    mapShown = data.district || data.districts[0].code;
  }
  mapWas = data.district || "";
  paintDistrict();
  paintRoad();
  paintTurfBand();
  paintCrowdChip();
}

// ---------- война за территории на карте ----------
//
// Точку берут ногами, и карта — единственный экран, с которого это
// делают. Под каждой точкой висит флажок с владельцем, а тому, кто до
// точки дошёл, снизу выезжает плашка с кнопкой сбора.

function turfOnMap() {
  return (mapData && mapData.turf) || { spots: {}, here: null };
}

// Видна ли плашка под ногами. В пути — нет: боец ещё числится у старого
// дома, и кнопка сбора под ногами уходящего была бы обманом
function turfBandShown() {
  const going = Boolean(mapData && mapData.road && mapData.road.going);
  return Boolean(turfOnMap().here) && !going;
}

function paintTurfBand() {
  const box = el("map-turf");
  box.textContent = "";
  const band = turfBandShown() ? turfOnMap().here : null;
  box.classList.toggle("hidden", !band);
  if (!band) return;

  // Чип в углу: жетон владельца и кнопка. Названия и дохода тут нет
  // нарочно — дом, у двери которого стоишь, подписан на самой карте, а
  // плашка висит поверх города: чем она меньше, тем меньше закрывает
  const head = document.createElement("p");
  head.className = "turf-band-head";
  if (band.badge) {
    const badge = picture(band.badge, band.owner, "");
    badge.className = "band-badge";
    head.appendChild(badge);
  } else {
    head.appendChild(document.createTextNode(band.mark));
  }
  box.appendChild(head);

  if (!band.button) {
    // Кнопки нет — вместо неё причина: ради неё плашка и становится
    // шире жетона
    const said = document.createElement("p");
    said.className = "turf-band-note";
    said.textContent = band.text;
    box.appendChild(said);
    return;
  }
  const go = document.createElement("button");
  go.type = "button";
  go.className = "btn band-go";
  go.id = "turf-call";
  // Слово на кнопке приходит с сервера: захват, задержание, охрана.
  // Нажатие открывает экран точки, а не начинает бой — драку выбирают
  // там, где видно, кто на точке стоит
  go.textContent = band.button === "arrest" || band.button === "free"
    ? "🚔 " + band.title
    : band.button === "seize" ? "🚩 " + band.title
    : band.button === "guard" ? "🛡 " + band.title
    : band.title || "Открыть";
  go.addEventListener("click", () => openTurf("map"));
  box.appendChild(go);
}

function shownDistrict() {
  return mapData.districts.find((one) => one.code === mapShown);
}

// Карты, которые не доехали. Помнить их приходится по той же причине,
// что и виды изнутри: за упавшую картинку браузер второй раз не
// возьмётся, а район перерисовывается на каждом шаге по городу
const brokenMaps = new Set();

function paintDistrict() {
  const district = shownDistrict();
  if (!district) return;
  const pic = el("map-pic");
  if (pic.getAttribute("src") !== district.image) pic.src = district.image;
  pic.alt = "Район: " + district.title;
  showBlankMap(district);
  placeZones();
  placeArrows();
}

// Вместо битой картинки — имя района словами. Ходить это не мешает:
// двери лежат поверх рамки и считаются от неё, а не от картинки
function showBlankMap(district) {
  const gone = brokenMaps.has(district.image);
  // Не `hidden`: холст с домами ложится по рамке самой картинки, и
  // убери её из вёрстки — двери поедут следом. Поэтому картинка
  // остаётся на месте, её просто не видно
  el("map-pic").classList.toggle("blank", gone);
  const note = el("map-blank");
  note.classList.toggle("hidden", !gone);
  if (gone) note.textContent = district.title + ": карта не загрузилась";
}

el("map-pic").addEventListener("error", () => {
  brokenMaps.add(el("map-pic").getAttribute("src"));
  if (mapData) paintDistrict();
});

// Стрелки в соседние районы. Города целиком не видно, и без них шесть
// карт остаются шестью картинками: по ним и понятно, что это один город.
const ARROW_SIGNS = { up: "▲", down: "▼", left: "◀", right: "▶" };

function placeArrows() {
  const box = el("map-arrows");
  box.textContent = "";
  const district = shownDistrict();
  if (!district) return;
  Object.entries(district.around || {}).forEach(([side, code]) => {
    const to = mapData.districts.find((one) => one.code === code);
    if (!to) return;
    const arrow = document.createElement("button");
    arrow.type = "button";
    arrow.className = "map-arrow " + side;
    arrow.dataset.side = side;
    arrow.dataset.to = code;
    arrow.title = to.title;
    arrow.setAttribute("aria-label", "Смотреть район: " + to.title);
    arrow.textContent = ARROW_SIGNS[side] || "•";
    arrow.addEventListener("click", () => {
      mapShown = code;
      paintDistrict();
    });
    box.appendChild(arrow);
  });
}

// Дома рисуются поверх карты одним SVG. Так вышло проще и точнее, чем
// считать поля руками: `preserveAspectRatio="xMidYMid meet"` вписывает
// холст ровно так же, как `object-fit: contain` вписывает картинку, —
// значит, координаты силуэтов совпадают с домами на рисунке при любом
// экране. Нажатие ловит сам многоугольник, а не рамка вокруг него.
const MAP_W = 941;
const MAP_H = 1672;
// Целятся в дверь, а она на карте с ноготь. Поэтому касание ловит не
// сама дверь, а рамка вокруг неё с запасом, — её присылает сервер.
// Подсветка при этом остаётся ровно на двери: расширение невидимо

// Просвет между порогом двери и верхом подписи, в единицах карты.
// Подпись выровнена по верхнему краю (`hanging`), а не по базовой линии:
// иначе отступ пришлось бы считать от высоты букв. Просвета всё равно
// берём с запасом: обводка подписи и значок «здесь» вылезают вверх за
// её край, и без запаса они цепляют порог
const SIGN_DROP = 34;

function svgNode(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    node.setAttribute(key, value);
  });
  return node;
}

function pointsOf(polygon) {
  return polygon.map(([x, y]) => x * MAP_W + "," + y * MAP_H).join(" ");
}

function placeZones() {
  const district = shownDistrict();
  const box = el("map-zones");
  box.textContent = "";
  if (!district) return;

  const canvas = svgNode("svg", {
    viewBox: "0 0 " + MAP_W + " " + MAP_H,
    preserveAspectRatio: "xMidYMid meet",
    class: "map-svg",
  });
  district.places.forEach((place) => canvas.appendChild(houseShape(place)));
  box.appendChild(canvas);
  // Подписи двигаем, когда холст уже в странице: до этого их нечем мерить
  canvas.querySelectorAll(".zone-sign").forEach(fitSign);
}

// Подпись стоит под серединой двери и шире её. У двери с краю карты она
// уезжает за рамку: в жилом квартале четыре дома по углам, и два из них
// стоят почти у самого края. Поэтому подпись поджимаем внутрь картинки.
//
// Ширину спрашиваем у браузера. Когда карта ещё не на экране, холст
// ничего не меряет и отвечает нулём — тогда прикидываем по числу букв.
const SIGN_EDGE = 16;   // поле от края картинки
const SIGN_CHAR = 21;   // ширина буквы на глаз: 38 пикселей жирным
// Жетон владельца у названия: сторона и просвет до первой буквы
const BADGE_SIZE = 40;
const BADGE_GAP = 8;

function fitSign(sign) {
  const measured = sign.getComputedTextLength ? sign.getComputedTextLength() : 0;
  const half = (measured || sign.textContent.length * SIGN_CHAR) / 2;
  const badge = sign.parentNode
    ? sign.parentNode.querySelector(".zone-badge")
    : null;
  // Жетон висит слева от подписи, и середину делят они вдвоём: иначе
  // название уезжало бы от двери на полжетона
  const shift = badge ? (BADGE_SIZE + BADGE_GAP) / 2 : 0;
  const middle = Number(sign.dataset.base || sign.getAttribute("x")) + shift;
  const left = SIGN_EDGE + half + shift * 2;
  const right = MAP_W - SIGN_EDGE - half;
  if (left > right) return;  // подпись шире карты — двигать её некуда
  const x = Math.min(Math.max(middle, left), right);
  sign.setAttribute("x", x);
  if (badge) badge.setAttribute("x", x - half - BADGE_GAP - BADGE_SIZE);
}

// Жетон владельца у названия дома. Место ему найдёт `fitSign`, когда
// подпись уже на холсте и её есть чем мерить
function houseBadge(held, top) {
  const badge = svgNode("image", {
    x: 0,
    y: top - 4,
    width: BADGE_SIZE,
    height: BADGE_SIZE,
    class: "zone-badge",
    href: held.badge,
    preserveAspectRatio: "xMidYMid meet",
  });
  // Старым webkit нужен xlink, новым хватает href: ставим оба
  badge.setAttributeNS(
    "http://www.w3.org/1999/xlink", "xlink:href", held.badge
  );
  // Не доехал — возвращаем значок словом: без него не понять, что дом
  // вообще держат
  badge.addEventListener("error", () => {
    const sign = badge.parentNode
      ? badge.parentNode.querySelector(".zone-sign")
      : null;
    badge.remove();
    if (!sign || sign.textContent.indexOf(held.mark) >= 0) return;
    sign.textContent = sign.textContent.replace(
      /^(📍 )?/, (pin) => (pin || "") + held.mark + " "
    );
    fitSign(sign);
  });
  return badge;
}

function houseShape(place) {
  const group = svgNode("g", {
    class: "zone-house" + (place.here ? " here" : "") + (place.works ? "" : " soon"),
    role: "button",
    tabindex: "0",
    "aria-label": place.title,
  });
  group.dataset.code = place.code;

  // Невидимая рамка под палец. Она и ловит нажатие
  group.appendChild(svgNode("rect", {
    x: place.touch.x * MAP_W,
    y: place.touch.y * MAP_H,
    width: place.touch.w * MAP_W,
    height: place.touch.h * MAP_H,
    rx: 12,
    class: "zone-touch",
  }));
  // Видимая подсветка — ровно по двери, как она нарисована
  group.appendChild(svgNode("polygon", {
    points: pointsOf(place.entrance),
    class: "zone-line",
  }));

  // Подпись под дверью. Над ней нельзя: там на рисунке нарисована
  // настоящая вывеска дома, и наша ложится прямо на неё
  // Отметка войны — перед названием. У семьи и у службы это их
  // нарисованный жетон: по шляпе не видно, какая именно семья держит
  // дом, а по знаку видно. У ничьего дома жетона нет — там кружок
  // словом: городская охрана не фракция, и своего знака у неё нет
  const held = (turfOnMap().spots || {})[place.code];
  const middle = (place.zone.x + place.zone.w / 2) * MAP_W;
  const top = (place.zone.y + place.zone.h) * MAP_H + SIGN_DROP;
  const sign = svgNode("text", {
    x: middle,
    y: top,
    class: "zone-sign" + (held && held.mine ? " ours" : ""),
    "text-anchor": "middle",
    "dominant-baseline": "hanging",
  });
  sign.dataset.base = String(middle);
  sign.textContent = (place.here ? "📍 " : "")
    + (held && !held.badge ? held.mark + " " : "") + place.title;
  if (held && held.badge) group.appendChild(houseBadge(held, top));
  group.appendChild(sign);

  // Под вывеской казино — отсчёт рейда: подвал открыт не всегда, и
  // единственное место, где это видно заранее, — карта
  if ((place.services || []).includes("raid")) {
    const plate = raidPlate(place);
    if (plate) group.appendChild(plate);
  }
  const enter = () => enterHouse(place);
  group.addEventListener("click", enter);
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") enter();
  });
  return group;
}

// ---------- отсчёт рейда на карте ----------
//
// Плашка под вывеской казино: синяя за час до окна, жёлтая пока подвал
// открыт, зелёная тому, кто своё уже взял. Живёт в том же холсте, что и
// подписи, — значит, растёт вместе с картой и стоит там же на любом экране.
const PLATE_W = 500;
const PLATE_DROP = 54;  // просвет между подписью дома и плашкой
const PLATE_LINE = 46;  // высота строки внутри плашки
const PLATE_PAD = 14;   // поля сверху и снизу

function plateClock(seconds) {
  const parts = [
    Math.floor(seconds / 3600),
    Math.floor(seconds / 60) % 60,
    seconds % 60,
  ];
  return parts.map((one) => String(one).padStart(2, "0")).join(":");
}

function raidPlate(place) {
  // Плашка своя у каждого рейдового дома: расписания у казино и стадиона
  // разные, и общая показала бы под одной вывеской часы другой
  const raid = ((mapData && mapData.raids) || {})[place.code] || {};
  if (!raid.state) return null;

  const lines = raid.state === "done" ? 1 : 2;
  const middle = (place.zone.x + place.zone.w / 2) * MAP_W;
  const top = (place.zone.y + place.zone.h) * MAP_H + SIGN_DROP + PLATE_DROP;
  const height = PLATE_PAD * 2 + lines * PLATE_LINE;
  // Строки стоят от середины плашки, а не от её верха: так и одна
  // строка, и две лежат в боксе ровно посередине
  const centre = top + height / 2;

  const plate = svgNode("g", { class: "zone-plate " + raid.state });
  plate.appendChild(svgNode("rect", {
    x: middle - PLATE_W / 2,
    y: top,
    width: PLATE_W,
    height: height,
    rx: 18,
    class: "plate-box",
  }));

  const word = svgNode("text", {
    x: middle,
    y: lines > 1 ? centre - PLATE_LINE / 2 : centre,
    class: "plate-word",
    "text-anchor": "middle",
    "dominant-baseline": "central",
  });
  word.textContent = raid.text;
  plate.appendChild(word);

  if (lines > 1) {
    const clock = svgNode("text", {
      x: middle,
      y: centre + PLATE_LINE / 2,
      // Часы у каждого дома свои: id с кодом дома, иначе две плашки
      // спорили бы за один узел и тикала бы только первая
      id: "raid-clock-" + place.code,
      class: "plate-clock",
      "text-anchor": "middle",
      "dominant-baseline": "central",
    });
    clock.textContent = plateClock(raid.seconds_left);
    plate.appendChild(clock);
  }
  // Сколько места заняла плашка: под казино за ней встаёт ещё флажок
  // войны, и ему надо знать, с какой высоты начинать
  return plate;
}

let raidClockTimer = null;

function startRaidClock() {
  if (raidClockTimer) return;
  raidClockTimer = setInterval(tickRaidClock, 1000);
}

function stopRaidClock() {
  if (!raidClockTimer) return;
  clearInterval(raidClockTimer);
  raidClockTimer = null;
}

function tickRaidClock() {
  // Плашек столько, сколько рейдовых домов: тикают все, и у каждой свой
  // счётчик на карте
  const plates = (mapData && mapData.raids) || {};
  for (const house of Object.keys(plates)) {
    const raid = plates[house];
    if (!raid || !raid.state || raid.state === "done") continue;
    raid.seconds_left -= 1;
    if (raid.seconds_left <= 0) {
      // Время вышло: что теперь с рейдом, знает сервер — спрашиваем его,
      // а не переписываем плашку сами
      loadMap();
      return;
    }
    const shown = el("raid-clock-" + house);
    if (shown) shown.textContent = plateClock(raid.seconds_left);
  }
}

/** Что открывает дом, если боец уже в нём. */
const HOUSE_SCREENS = {
  fight: () => openClub(),
  raid: (place) => openRaidHouse(place && place.code),
  weapons: () => openShop(),
  clothes: () => openShop(),
  potions: () => openShop(),
  fan: () => openShop(),
  jewel: () => openShop(),
  premium: () => showTab("magic"),
  market: () => {
    pickShopSection("market");
    showTab("shop");
  },
  repair: () => openWorkshop(),
  heal: () => showTab("hospital"),
  trade: () => showTab("trade"),
  insurance: () => showTab("insurance"),
  train: () => showTab("gym"),
  bank: () => showTab("bank"),
  hire: () => showTab("hr"),
  work: () => showTab("work"),
  estate: () => showTab("flats"),
  post: () => showTab("post"),
  tavern: () => showTab("tavern"),
  study: () => showTab("study"),
  vote: () => showTab("elections"),
  school: () => showTab("school"),
  police: () => showTab("police"),
  mafia: () => showTab("mafia"),
  prison: () => openPrison(),
  arena: () => openArena(),
  cars: () => openCars(),
  // Рынок открывает тот же экран, что салон и гаражи: машина везде
  // рисуется одинаково, а дом подставляет свою полку
  used_cars: () => openCars(),
  dorm: () => openDorm(),
  charge: () => openCharge(),
  dojo: () => openDojo(),
  looks: () => openBarber(),
};

// Дом, за которым услуги ещё нет. Раньше он отвечал всплывашкой, и боец
// оставался на карте — то есть внутрь не попадал вовсе, и вид изнутри
// показать было негде. Теперь у него свой экран: картинка сверху и
// записка о том, чего тут ждать.
function openHouse(place) {
  el("house-title").textContent = place.title;
  el("house-soon").textContent = place.soon
    ? "Скоро здесь появится новая услуга: " + place.soon + "."
    : "Дом пока пустует. Загляни позже.";
  showTab("house");
}

async function enterHouse(place) {
  if (place.here) {
    if (!place.works) {
      openHouse(place);
      return;
    }
    const open = HOUSE_SCREENS[place.services[0]];
    if (open) open(place);
    return;
  }
  if (mapData.road.going) {
    popup("Ты в пути", mapData.road.text);
    return;
  }
  // Согласия не спрашиваем: дорога занимает секунды, и окно «идём?» на
  // каждый шаг превращает город в анкету. Ошиблись домом — дойдите и
  // пойдите дальше, потеряв те же секунды
  // До дома без услуги дойти можно: город не должен выглядеть
  // наполовину нарисованным. Что он пока пуст, скажем уже на месте
  await travelTo(place.code);
}

async function travelTo(code) {
  try {
    const body = await post("api/travel", { to: code });
    renderMap(body.map);
    render(body.card, true);
  } catch (error) {
    popup("Не выйдет", error.message);
  }
}

// Отсчёт дороги — батарейка в углу: клеток ровно столько, сколько
// секунд идти, и каждую секунду загорается ещё одна. Так видно не только
// «сколько осталось», но и сколько уже прошло, — а числом под ней
// написано то же самое, для тех, кому клетки считать неохота.
const ROAD_CELLS_MAX = 12;

function roadClock(left) {
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
}

function stopRoad() {
  if (roadTimer) clearInterval(roadTimer);
  roadTimer = null;
}

function paintRoad() {
  const road = mapData.road;
  const box = el("map-road");
  box.classList.toggle("hidden", !road.going);
  if (!road.going) {
    stopRoad();
    return;
  }

  // Длинная дорога не должна разносить батарейку на пол-экрана: клеток
  // берём не больше дюжины, и тогда одна стоит несколько секунд
  const whole = Math.max(road.seconds || road.seconds_left, 1);
  const cells = Math.min(whole, ROAD_CELLS_MAX);
  const strip = el("road-cells");
  strip.textContent = "";
  const lamps = [];
  for (let i = 0; i < cells; i += 1) {
    const cell = document.createElement("i");
    cell.className = "road-cell";
    strip.appendChild(cell);
    lamps.push(cell);
  }
  box.title = road.text;
  box.setAttribute("aria-label", road.text);

  let left = road.seconds_left;
  const tick = () => {
    const gone = Math.min(Math.max(whole - left, 0), whole);
    const lit = Math.round((gone / whole) * cells);
    lamps.forEach((cell, i) => cell.classList.toggle("on", i < lit));
    el("road-clock").textContent = roadClock(Math.max(left, 0));
    if (left <= 0) {
      stopRoad();
      loadMap();
      catchUp();
      return;
    }
    left -= 1;
  };
  tick();
  stopRoad();
  roadTimer = setInterval(tick, 1000);
}

// Пересчитывать зоны при смене размера не нужно: холст SVG вписывается
// в рамку тем же правилом, что и картинка, и едет вместе с ней сам

// ---------- магазин «Элита» ----------

let magicData = null;

function proCard(pro) {
  const box = document.createElement("section");
  box.className = "thing pro" + (pro.active ? " on" : "");

  const pic = document.createElement("div");
  pic.className = "thing-pic";
  pic.appendChild(slotPicture({ image: pro.image, icon: pro.emoji, title: pro.title },
    pro.emoji));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = pro.emoji + " " + pro.title;
  body.appendChild(title);

  const price = document.createElement("div");
  price.className = "thing-price" + (pro.free ? " free" : " stars");
  price.textContent = pro.free
    ? "Бесплатно · " + pro.term_text
    : pro.stars + " ⭐ · " + pro.term_text;
  body.appendChild(price);

  if (pro.promo_note) {
    const promo = document.createElement("div");
    promo.className = "thing-promo";
    promo.textContent = pro.promo_note;
    body.appendChild(promo);
  }

  if (pro.active) {
    const left = document.createElement("div");
    left.className = "thing-have";
    left.textContent = "✔ Подписка активна — осталось " + pro.left_text;
    body.appendChild(left);
  }

  const label = document.createElement("div");
  label.className = "thing-label";
  label.textContent = "Что даёт";
  body.appendChild(label);

  const gains = document.createElement("ul");
  gains.className = "thing-gain";
  pro.benefits.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    gains.appendChild(li);
  });
  body.appendChild(gains);

  // Оговорка под списком бывает пустой: трём строкам она не нужна, а
  // пустая строка под ними читается как обрыв
  if (pro.note) {
    const note = document.createElement("div");
    note.className = "thing-note";
    note.textContent = pro.note;
    body.appendChild(note);
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  // Бесплатная неделя — разовый вход, а не способ продления: забрал один
  // раз, дальше кнопка всегда ведёт в счёт.
  const text = pro.free
    ? "Забрать бесплатно"
    : (pro.active ? "Продлить · " : "Оформить · ") + pro.stars + " ⭐";
  buttons.appendChild(button(text, { onClick: () => takePro(pro) }));
  body.appendChild(buttons);

  box.appendChild(body);
  return box;
}

function renderMagic(data) {
  magicData = data;
  el("magic-note").textContent =
    "Товары здесь берут за звёзды Telegram — кредиты тут не в ходу. "
    + "Купленное падает в инвентарь.";
  // Подписка стоит первой и никуда не девается: прилавок может быть пуст,
  // а она — нет.
  const head = el("pro-card");
  head.textContent = "";
  if (data.pro) head.appendChild(proCard(data.pro));
  el("magic-empty").classList.toggle("hidden", data.items.length > 0);

  const list = el("magic-list");
  list.textContent = "";
  data.items.forEach((item) => list.appendChild(thingCard(item, 0, true)));
}

async function takePro(pro) {
  if (busy) return;
  // Бесплатную забираем прямо здесь, платную — через счёт Telegram
  if (!pro.free) {
    await buyRelic({ code: "month", title: pro.title, kind: "pro" });
    return;
  }
  busy = true;
  try {
    const data = await post("api/pro", {});
    render(data.card, true);
    renderMagic(data.magic);
    haptic((feedback) => feedback.notificationOccurred("success"));
    const got = data.pro;
    popup(
      "💎 " + pro.title,
      (got.renewed ? "Подписка продлена на " : "Подписка на ") + got.days + " дней."
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

async function loadMagic() {
  try {
    const response = await fetch("api/magic", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Магазин «Элита» закрыт.");
    renderMagic(await response.json());
  } catch (error) {
    el("magic-note").textContent = error.message;
  }
}

async function buyRelic(item) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/invoice", {
      code: item.code,
      kind: item.kind === "pro" ? "pro" : "relic",
    });
    if (!tg || !tg.openInvoice) {
      popup("Оплата", "Счёт открывается только в Telegram.");
      return;
    }
    tg.openInvoice(data.link, (status) => {
      if (status === "paid") {
        // Товар выдаёт бот, когда Telegram подтвердит списание, — здесь
        // просто перечитываем карточку и прилавок.
        magicData = null;
        refresh();
        loadMagic();
        popup(
          "✨ " + item.title,
          item.kind === "pro"
            ? "Оплачено. Подписка уже действует."
            : "Оплачено. Вещь ждёт в инвентаре."
        );
      } else if (status === "failed") {
        popup("Не вышло", "Telegram не принял оплату.");
      }
    });
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// ---------- бойцовский клуб ----------

let clubData = null;

function fighterRow(fighter) {
  const box = document.createElement("div");
  box.className = "fighter" + (fighter.is_self ? " me" : "");

  const face = document.createElement("span");
  face.className = "fighter-class";
  face.textContent = fighter.fclass.emoji;

  const name = document.createElement("span");
  name.className = "fighter-name";
  // Порядок знаков в строке один на весь город: класс, знак мафии, знак
  // клана, ник, премиум, уровень
  name.textContent = fighter.pro
    ? fighter.nickname + " 💎"
    : fighter.nickname;

  const level = document.createElement("span");
  level.className = "fighter-level";
  level.textContent = "[" + fighter.level + "]";

  // Один вход на строку: значок ведёт в карточку, а статистика живёт
  // уже там. Две кнопки на строку делали список выше и заставляли
  // выбирать между ними, ни разу не показав, что за боец внутри
  const info = document.createElement("button");
  info.type = "button";
  info.className = "fighter-info";
  info.textContent = "ℹ️";
  info.title = "Карточка бойца";
  info.setAttribute("aria-label", "Карточка бойца " + fighter.nickname);
  info.addEventListener("click", () => showFighter(fighter));

  box.append(
    face,
    ...clanMarks(fighter.clan),
    ...serviceMarks(fighter.service),
    name,
    level,
    info
  );
  return box;
}

function raidScore(record) {
  // «4 / 10» — побед из походов. Одним числом здесь не обойтись: рейд
  // проигрывают отрядом, и десять заходов с четырьмя победами говорят о
  // бойце совсем не то же, что четыре захода с четырьмя
  return num(record.raid_wins) + " / " + num(record.raid_fights);
}

function sheetRows(pairs) {
  const list = document.createElement("ul");
  list.className = "rows";
  pairs.forEach(([label, value]) => list.appendChild(row(label, value)));
  return list;
}

function sheetDoll(card) {
  // Аватар и слоты — то же, что на карточке, только помельче
  const doll = document.createElement("section");
  doll.className = "doll sheet-doll";

  const left = document.createElement("div");
  left.className = "slots";
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  const right = document.createElement("div");
  right.className = "slots";

  renderSlots(left, card.slots.left, false);
  renderSlots(right, card.slots.right, false);
  paintAvatar(avatar, card, false);

  doll.append(left, avatar, right);
  return doll;
}

function sheetHealth(hp) {
  const box = document.createElement("div");
  box.className = "hp sheet-hp " + (hp.color || "green");

  const fill = document.createElement("div");
  fill.className = "hp-fill";
  fill.style.width = Math.max(0, Math.min(100, hp.percent)) + "%";

  const text = document.createElement("div");
  text.className = "hp-text";
  text.textContent = num(Math.floor(hp.current)) + " / " + num(hp.max);

  box.append(fill, text);
  return box;
}

function fighterCard(card) {
  const box = document.createDocumentFragment();
  box.appendChild(sheetDoll(card));

  // Здоровье — первое, что хотят знать о чужом бойце: цел он или
  // отлёживается. Под шкалой не «готов к бою» — это и так видно по самой
  // шкале, — а в клубе ли он сейчас: вызывать ушедшего некому
  if (card.hp) {
    box.appendChild(sheetHealth(card.hp));
    const note = document.createElement("p");
    note.className = "hp-note sheet-hp-note";
    if (card.seen && card.seen.online) note.classList.add("online");
    note.textContent = (card.seen && card.seen.text) || "";
    // Сколько ему до строя — единственное, чего по шкале не понять
    if (card.hp.ready_in_text) {
      note.textContent += " · в строю через " + card.hp.ready_in_text;
    }
    box.appendChild(note);
  }

  // Где боец сейчас. Вызывать его есть смысл, только если он в клубе, —
  // а по карточке это иначе не понять
  if (card.place) {
    const where = document.createElement("p");
    where.className = "sheet-place";
    where.textContent = card.place.seconds_left
      ? "🚶 В пути до дома «" + card.place.going_to + "» — "
        + card.place.seconds_left + " сек"
      : "📍 " + card.place.title;
    box.appendChild(where);
  }

  const panel = document.createElement("section");
  panel.className = "panel";
  panel.appendChild(
    sheetRows(card.stats.map((stat) => [stat.emoji + " " + stat.title, num(stat.total)]))
  );
  panel.appendChild(document.createElement("hr")).className = "rule";
  panel.appendChild(
    sheetRows([
      ["Уровень", num(card.level)],
      ["Опыт", num(card.progress.total_exp)],
    ])
  );
  panel.appendChild(document.createElement("hr")).className = "rule";
  panel.appendChild(
    sheetRows([
      ["Побед", num(card.record.wins)],
      ["Поражений", num(card.record.losses)],
      ["Ничьих", num(card.record.draws)],
      ["Рейды", raidScore(card.record)],
      ["Рейтинг", num(card.record.rating)],
    ])
  );
  panel.appendChild(document.createElement("hr")).className = "rule";
  panel.appendChild(
    sheetRows([
      ["Место рождения", card.birthplace],
      ["День рождения персонажа", card.birthday],
    ])
  );
  box.appendChild(panel);

  // Статистика боёв переехала со строки списка сюда: смотреть её идут,
  // уже увидев, кто перед тобой
  const actions = document.createElement("div");
  actions.className = "thing-buttons sheet-actions";
  actions.appendChild(
    button("📊 Статистика боёв", {
      onClick: () => {
        closeSheet();
        pickClubSection("stats");
        loadHistory(card.is_self ? null : card.user_id);
      },
    })
  );
  box.appendChild(actions);
  return box;
}

async function showFighter(fighter) {
  openSheet(
    (fighter.pro ? fighter.nickname + " 💎" : fighter.nickname)
      + " [" + fighter.level + "]",
    fighter.fclass.emoji + " " + fighter.fclass.title
  );
  try {
    const response = await fetch("api/card?user_id=" + fighter.user_id, {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Карточка не открылась.");
    const card = await response.json();
    el("sheet-note").textContent =
      card.fclass.emoji + " " + card.fclass.title + " · " + card.city;
    el("sheet-list").textContent = "";
    el("sheet-list").appendChild(fighterCard(card));
  } catch (error) {
    el("sheet-note").textContent = error.message;
  }
}

// ---------- бои ----------
//
// Правил здесь нет: страница показывает то, что отдал сервер, и шлёт
// обратно нажатия. Ходы считает тот же движок, что и в ветке, поэтому
// драться можно откуда удобнее — экран и чат ведут один и тот же бой.

let fightsData = null;
let fightsTimer = null;
// Итог прошлого ответа: по нему видно, что бой только что кончился
let fightWasOver = false;
let clubSection = "fights";
// Пока запрос в пути, второй не шлём: иначе двойное нажатие уходит дважды
let fightBusy = false;

function startWatchingFights() {
  if (fightsTimer) return;
  loadFights();
  fightsTimer = setInterval(loadFights, 2000);
}

function stopWatchingFights() {
  if (!fightsTimer) return;
  clearInterval(fightsTimer);
  fightsTimer = null;
}

function pickClubSection(name) {
  const house = CLUB_HOUSES[clubHouse];
  const allowed = clubSections().map(([code]) => code);
  // Рейд открывается только в рейдовом доме, бои — только в клубе.
  // Пришли не с тем разделом (например, вернулись из казино) —
  // показываем здешний
  if (name !== house.start && !allowed.includes(name)) name = house.start;
  clubSection = name;
  ["fights", "battle", "raid", "players", "stats"].forEach((section) => {
    el("club-" + section).classList.toggle("hidden", section !== name);
  });
  renderClubSections();
  // Ринг и отряд собираются в клубе. В других домах раздел открывается,
  // но вместо вызовов в нём записка: так видно, что бои есть, просто не
  // здесь, — а кнопки, на которые сервер всё равно ответит отказом, не
  // рисуются вовсе
  const locked = (name === "fights" || name === "battle") && !canFightHere();
  if (locked) sayNoFightsHere(name);
  if (name === "players" && !clubData) loadClub();
  if (name === "stats" && !statsData) loadHistory(statsWho);
  // Рейд живёт волнами: пока раздел открыт, спрашиваем состояние
  if (name === "raid") startWatchingRaid();
  else stopWatchingRaid();
  // Групповой бой тоже идёт раундами и без тебя — следим так же
  if (name === "battle" && !locked) startWatchingBattle();
  else stopWatchingBattle();
}

function sayNoFightsHere(name) {
  const note = name === "fights" ? "fights-note" : "battle-note";
  const body = name === "fights" ? "fights-body" : "battle-body";
  el(note).textContent = "";
  const said = document.createElement("p");
  said.className = "club-locked";
  said.textContent = NO_FIGHTS_HERE;
  el(body).textContent = "";
  el(body).appendChild(said);
}

// Один экран обслуживает два дома: клуб и казино. Казино — не вкладка, а
// дом: его открывает дверь на карте, и только она. Кнопка внизу остаётся
// «Клубом» всегда: она ведёт в клуб, где бы боец ни стоял, — а раздел
// рейда за ней не прячется, иначе, выйдя из казино, он бы там и остался.
const CLUB_HOUSES = {
  club: { title: "🥊 Бойцовский клуб", start: "fights" },
  casino: { title: "🎲 Казино", start: "raid" },
  // Второй рейдовый дом. Экран тот же, что у казино, — какой именно
  // рейд в нём собирают, говорит сервер: он смотрит, где боец стоит
  stadium: { title: "🏟 Стадион", start: "raid" },
  // Третий. На полигоне дверь ведёт на занятия — рейд в нём раз в
  // неделю, а кадеты ходят каждый день, — и попадают сюда кнопкой
  // «Рейд» в шапке открытого дома
  indoor_training_ground: { title: "🎖 Крытый полигон", start: "raid" },
};

let clubHouse = "club";

function inRaidHouse() {
  return ((myPlace && myPlace.services) || []).includes("raid");
}

// Драться можно не везде: в аптеке боёв нет, и вкладку «Бои» там
// заменяет записка. Прячем не саму вкладку — человеку полезно видеть,
// что у клуба есть, — а только её содержимое
function canFightHere() {
  return ((myPlace && myPlace.services) || []).includes("fight");
}

const NO_FIGHTS_HERE = "Бои между игроками недоступны в данной локации.";

function clubSections() {
  // В рейдовом доме разделов нет вовсе: там одно дело — рейд
  if (RAID_HOUSES.includes(clubHouse)) return [];
  return [
    ["fights", "Бои"],
    ["battle", "Отряд"],
    ["players", "Игроки"],
    ["stats", "Статистика"],
  ];
}

function openClub() {
  clubHouse = "club";
  showTab("club");
}

// Дома, в которых идут рейды. Экран у них общий, различается только
// вывеска: что именно за рейд, решает сервер по месту бойца
const RAID_HOUSES = ["casino", "stadium", "indoor_training_ground"];

function openRaidHouse(code) {
  clubHouse = RAID_HOUSES.includes(code) ? code : "casino";
  clubSection = "raid";
  showTab("club");
}

function renderClubSections() {
  const box = el("club-sections");
  box.textContent = "";
  el("club-title").textContent = CLUB_HOUSES[clubHouse].title;
  clubSections().forEach(([code, label]) => {
    box.appendChild(chip(label, clubSection === code, () => pickClubSection(code)));
  });
}

async function loadFights() {
  try {
    const response = await fetch("api/fights", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (response.status === 404) {
      el("fights-note").textContent = "Сначала заведи бойца в личке бота.";
      return;
    }
    if (!response.ok) throw new Error("Ринг не отвечает.");
    renderFights(await response.json());
  } catch (error) {
    el("fights-note").textContent = error.message;
  }
}

async function fightAction(payload) {
  if (fightBusy) return;
  fightBusy = true;
  // Всё, что между поднятым флагом и `finally`, обязано лежать внутри `try`:
  // иначе любой бросок оставляет флаг поднятым, и кнопки замолкают навсегда
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/fight", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      popup("Ринг", body.error || "Не вышло.");
      return;
    }
    renderFights(body);
  } catch (error) {
    popup("Ринг", error.message);
  } finally {
    fightBusy = false;
  }
}

let lastTurn = null;

function renderFights(data) {
  // Новый ход — намётки прошлого сбрасываем: иначе на экран вернётся
  // подсвеченным то, что уже ушло судье
  const turn = data.duel ? data.duel.id + ":" + data.duel.round + ":" + data.duel.turn : null;
  if (turn !== lastTurn) {
    lastTurn = turn;
    turnDraft = { attacks: {}, block: null };
  }
  // Бой доигран — уровень и награда уже записаны: перечитываем карточку
  if (data.duel && data.duel.finished && !fightWasOver) catchUp();
  fightWasOver = Boolean(data.duel && data.duel.finished);
  fightsData = data;
  const body = el("fights-body");
  body.textContent = "";
  if (data.duel) {
    el("fights-note").textContent = "";
    body.appendChild(duelPanel(data));
  } else if (data.challenge) {
    el("fights-note").textContent = "Вызов брошен. Ждём, кто выйдет.";
    body.appendChild(myChallenge(data.challenge));
    if (data.challenges.length) body.appendChild(challengeList(data));
  } else {
    el("fights-note").textContent = data.can_fight
      ? "Брось вызов или прими чужой."
      : "Здоровье не то — сначала отдышись.";
    body.appendChild(openForm(data));
    if (data.challenges.length) body.appendChild(challengeList(data));
  }
}

function openForm(data) {
  const box = document.createElement("div");
  box.className = "fight-open";
  data.modes.forEach((mode) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn wide";
    btn.textContent = mode.emoji + " Вызвать на " + mode.title;
    btn.disabled = !data.can_fight;
    btn.addEventListener("click", () =>
      fightAction({ action: "open", mode: mode.code })
    );
    box.appendChild(btn);
  });
  return box;
}

function myChallenge(challenge) {
  const box = document.createElement("div");
  box.className = "fight-card";
  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent = challenge.mode.emoji + " Твой вызов на " + challenge.mode.title;
  box.appendChild(head);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn secondary wide";
  btn.textContent = "Отозвать";
  btn.addEventListener("click", () => fightAction({ action: "cancel" }));
  box.appendChild(btn);
  return box;
}

function challengeList(data) {
  const box = document.createElement("div");
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Кто зовёт драться";
  box.appendChild(head);
  data.challenges.forEach((challenge) => {
    const card = document.createElement("div");
    card.className = "fight-card";
    const line = document.createElement("p");
    line.className = "fight-line";
    line.textContent =
      challenge.mode.emoji + " " + challenge.challenger.emoji + " " +
      challenge.challenger.name + " [" + challenge.challenger.level + "] — " +
      challenge.mode.title + (challenge.personal ? ", лично тебе" : "");
    card.appendChild(line);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn wide";
    btn.textContent = "Принять вызов";
    btn.disabled = !data.can_fight;
    btn.addEventListener("click", () =>
      fightAction({ action: "join", challenge_id: challenge.id })
    );
    card.appendChild(btn);
    box.appendChild(card);
  });
  return box;
}

function fightBar(fighter) {
  const bar = document.createElement("div");
  bar.className = "fight-bar";
  const fill = document.createElement("span");
  fill.style.width = Math.max(0, Math.min(100, fighter.percent)) + "%";
  fill.className =
    fighter.percent < 20 ? "low" : fighter.percent < 80 ? "hurt" : "full";
  bar.appendChild(fill);
  return bar;
}

function fighterSide(fighter) {
  const box = document.createElement("div");
  box.className = "fight-side" + (fighter.you ? " you" : "");
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent = fighter.emoji + " " + fighter.name + " [" + fighter.level + "]";
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = fighter.hp + "/" + fighter.max_hp;
  const mark = document.createElement("p");
  mark.className = "fight-mark";
  mark.textContent = fighter.ready ? "✅ Готов" : "⏳ Думает";
  box.appendChild(name);
  box.appendChild(hp);
  box.appendChild(fightBar(fighter));
  box.appendChild(mark);
  return box;
}

// Что боец наметил, но ещё не отправил. Выбор живёт на странице до
// нажатия «Вперёд!»: передумать можно сколько угодно, судья узнает один раз.
// Ударов столько, сколько рук с оружием: со вторым оружием их два.
let turnDraft = { attacks: {}, block: null };

function draftReady(hands) {
  return Boolean(turnDraft.block) && hands.every((row) => turnDraft.attacks[row.hand]);
}

function turnPayload() {
  return { action: "turn", attacks: turnDraft.attacks, block: turnDraft.block };
}

function zoneList(column, repaint) {
  const box = document.createElement("div");
  box.className = "zone-list";
  column.rows.forEach((row) => {
    const label = document.createElement("label");
    label.className = "zone" + (column.chosen() === row.zone ? " on" : "");
    if (row.hint) label.title = row.hint;
    const dot = document.createElement("input");
    dot.type = "radio";
    dot.name = column.name;
    dot.value = row.zone;
    dot.checked = column.chosen() === row.zone;
    dot.addEventListener("change", () => {
      column.pick(row.zone);
      repaint();
    });
    const text = document.createElement("span");
    text.textContent = row.title;
    label.appendChild(dot);
    label.appendChild(text);
    box.appendChild(label);
  });
  return box;
}

function zoneColumns(hands, attacks, blocks, draft, prefix, repaint) {
  // Столбцы выбора хода: по столбцу на руку с оружием и один на защиту.
  const box = document.createElement("div");
  box.className = "zone-columns" + (hands.length > 1 ? " three" : "");

  // Заголовок короткий — «Удар 1», — а чем именно бьёт эта рука, говорит
  // подсказка: столбцов бывает три, и название оружия в них не помещается
  const columns = hands.map((hand, index) => ({
    title:
      hand.icon + " " +
      (hand.label || (hands.length > 1 ? "Удар " + (index + 1) : "Удар")),
    hint: hand.title,
    rows: attacks,
    name: prefix + "-attack-" + hand.hand,
    pick: (zone) => {
      draft().attacks[hand.hand] = zone;
    },
    chosen: () => draft().attacks[hand.hand],
  }));
  columns.push({
    title: "🛡 Блок",
    hint: "Что закрываем",
    rows: blocks,
    name: prefix + "-block",
    pick: (zone) => {
      draft().block = zone;
    },
    chosen: () => draft().block,
  });
  // Сначала все заголовки, потом все списки: они лежат двумя рядами одной
  // сетки, поэтому кнопки во всех столбцах начинаются на одной высоте —
  // даже когда длинное название оружия переносится на вторую строку.
  columns.forEach((column) => {
    const head = document.createElement("p");
    head.className = "zone-head";
    head.textContent = column.title;
    if (column.hint) head.title = column.hint;
    box.appendChild(head);
  });
  columns.forEach((column) => box.appendChild(zoneList(column, repaint)));
  return box;
}

// Советы подписчика — строкой над кнопками хода: слева про удар, справа
// про блок, по половине ширины на каждый. Раньше они стояли столбец в
// столбец с кнопками, и совет по удару растягивался на две руки — при
// трёх столбцах от этого разъезжалась вся сетка, а места совет занимал
// вдвое больше, чем нужно. Стоять ровно над своей кнопкой ему незачем:
// значок и так говорит, о чём он.
function tipsRow(scout) {
  const attack = scout && scout.attack_tip;
  const block = scout && scout.block_tip;
  if (!(attack && attack.move) && !(block && block.move)) return null;
  const box = document.createElement("div");
  box.className = "tips";
  box.appendChild(tipCell(attack));
  box.appendChild(tipCell(block));
  return box;
}

function tipCell(tip) {
  const box = document.createElement("div");
  box.className = "zone-tip";
  if (!tip || !tip.move) return box;  // советовать нечего — половина пустая
  const move = document.createElement("span");
  move.className = "zone-tip-move";
  move.textContent = "💡 " + tip.move;
  box.appendChild(move);
  if (tip.why) {
    const why = document.createElement("span");
    why.className = "zone-tip-why";
    why.textContent = tip.why;
    box.appendChild(why);
  }
  return box;
}

function paintDraft() {
  // Подсветка выбранного и кнопка отправки: пока не выбраны все удары и
  // блок, отправлять нечего
  document.querySelectorAll("#club-fights .zone").forEach((label) => {
    const dot = label.querySelector("input");
    label.classList.toggle("on", Boolean(dot && dot.checked));
  });
  const go = el("turn-go");
  const hands = (fightsData && fightsData.duel && fightsData.duel.hands) || [];
  if (go) go.disabled = !draftReady(hands);
}

// ---------- аналитик ----------
//
// Подсказки подписчика над кнопками хода. Строки две: одна помогает
// выбрать удар, другая — блок, и у каждой свой значок. Столбцы на
// телефоне узкие, предложение в них не помещается, поэтому панель стоит
// над ними целиком — но выше кнопок, как ей и положено.
//
// Разбор при этом свёрнут. Развёрнутый он занимает четыре строки и
// выталкивает с экрана шкалы здоровья — то самое, на что в бою смотрят
// непрерывно. Совет из разбора («бей в ноги») остаётся на виду всегда:
// он в клетках над кнопками, а не здесь.

// Развернул один раз — остаётся развёрнутым: экран рейда перерисовывается
// каждые две секунды, и без этого разбор захлопывался бы на глазах
let scoutOpen = false;

function scoutPanel(scout) {
  if (!scout || (!scout.attack && !scout.block)) return null;
  const box = document.createElement("details");
  box.className = "scout";
  box.open = scoutOpen;
  box.addEventListener("toggle", () => {
    scoutOpen = box.open;
  });

  const head = document.createElement("summary");
  head.className = "scout-head";
  head.textContent = "🔍 Аналитик" + (scout.title ? " · " + scout.title : "");
  box.appendChild(head);

  [["⚔️", scout.attack], ["🛡", scout.block]].forEach(([sign, text]) => {
    if (!text) return;
    const line = document.createElement("p");
    line.className = "scout-line";
    const mark = document.createElement("span");
    mark.className = "scout-mark";
    mark.textContent = sign;
    line.appendChild(mark);
    line.appendChild(document.createTextNode(text));
    box.appendChild(line);
  });
  return box;
}

// Приёмы бойца и его шкала — над кнопками хода. Приём жмут до выбора
// удара и блока, поэтому и стоит он выше: порядок на экране повторяет
// порядок в бою.
//
// Значок приёма цветной, когда энергии хватает, и серый, когда нет, — так
// и просили. Нажатый приём светится: заготовка ждёт своего момента и может
// провисеть несколько ходов, и боец должен видеть, что она в деле.
function abilityPanel(state, send) {
  if (!state || !state.tricks || !state.tricks.length) return null;
  // Куда слать нажатие: у ринга, отряда и рейда свои адреса, а панель одна
  send = send || ((trick) => useAbility("api/fight", trick, renderFights));
  const box = document.createElement("section");
  box.className = "tricks";

  // Сначала сами приёмы, шкала под ними. Приём выбирают по картинке, а
  // на шкалу смотрят уже потом — «хватит ли», — и подпись под шкалой
  // заодно перестала разрывать ряд и кнопки хода
  const row = document.createElement("div");
  row.className = "trick-row";
  state.tricks.forEach((trick) => {
    const card = document.createElement("button");
    card.type = "button";
    // Готов — цветной, не по карману или норма выбрана — серый, нажатый —
    // в зелёной обводке. Три состояния, и все видны с одного взгляда
    card.className =
      "trick" + (trick.ready ? "" : " cold") + (trick.armed ? " armed" : "");
    card.disabled = !trick.ready || trick.armed;
    card.title = trick.title + " · " + trick.cost + " ⚡\n" + trick.note;

    const pic = document.createElement("div");
    pic.className = "trick-pic";
    pic.appendChild(slotPicture(trick, trick.icon));

    // Цена лежит на самой картинке, на подложке. Подпись с названием
    // ушла: на телефоне она занимала столько же места, сколько картинка,
    // а прочесть её всё равно не выходило — приём узнают по рисунку.
    // Название осталось в подсказке по долгому нажатию
    const price = document.createElement("span");
    price.className = "trick-cost";
    price.textContent = trick.cost + " ⚡";
    pic.appendChild(price);
    card.appendChild(pic);

    card.addEventListener("click", () => send(trick));
    row.appendChild(card);
  });
  box.appendChild(row);

  const bar = document.createElement("div");
  bar.className = "energy";
  const fill = document.createElement("div");
  fill.className = "energy-fill";
  fill.style.width = Math.round((state.energy / state.max) * 100) + "%";
  bar.appendChild(fill);
  const label = document.createElement("span");
  label.className = "energy-label";
  label.textContent = "⚡ " + state.energy + " / " + state.max;
  bar.appendChild(label);
  box.appendChild(bar);

  const note = document.createElement("p");
  note.className = "energy-note";
  // Сколько даёт событие и сколько приёмов осталось на ход — два числа,
  // без которых шкала и лимит выглядят произволом
  const parts = [];
  if (state.source) parts.push(state.source);
  if (state.per_turn) {
    parts.push(
      state.left > 0
        ? "за ход осталось приёмов: " + state.left
        : "приёмы на этот ход кончились"
    );
  }
  note.textContent = parts.join(" · ");
  if (parts.length) box.appendChild(note);
  return box;
}

async function useAbility(where, trick, repaint) {
  if (busy) return;
  busy = true;
  try {
    const data = await post(where, { action: "ability", code: trick.code });
    repaint(data);
    haptic((feedback) => feedback.impactOccurred("medium"));
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

function turnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";

  // Приёмы выше всего: их жмут до удара и блока
  const tricks = abilityPanel(data.duel.abilities);
  if (tricks) box.appendChild(tricks);

  // Разбор соперника — над кнопками: его видит только тот, кому он пришёл
  const scout = scoutPanel(data.duel.scout);
  if (scout) box.appendChild(scout);

  // Столбцов ударов столько, сколько рук с оружием, а блок бывает шире:
  // со щитом он держит три зоны вместо двух
  const hands = data.duel.hands || [{ hand: 0, icon: "👊", title: "Кулаки" }];
  const blocks = data.duel.blocks || data.blocks;

  const tips = tipsRow(data.duel.scout);
  if (tips) box.appendChild(tips);
  box.appendChild(
    zoneColumns(hands, data.attacks, blocks, () => turnDraft, "turn", paintDraft)
  );

  const go = document.createElement("button");
  go.type = "button";
  go.id = "turn-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !draftReady(hands);
  go.addEventListener("click", () => {
    const move = turnPayload();
    turnDraft = { attacks: {}, block: null };
    fightAction(move);
  });
  box.appendChild(go);
  return box;
}

function duelPanel(data) {
  const duel = data.duel;
  const box = document.createElement("div");
  box.className = "fight-panel";

  const head = document.createElement("p");
  head.className = "fight-round";
  head.textContent = duel.finished
    ? "🔔 Бой окончен"
    : duel.started
      ? "🔔 Раунд " + duel.round + " из " + duel.rounds +
        ", удар " + duel.turn + " из " + duel.turns_per_round
      : "🥊 Бойцы сошлись. Гонга ещё не было";
  box.appendChild(head);

  const board = document.createElement("div");
  board.className = "fight-board";
  duel.fighters.forEach((fighter, index) => {
    if (index) {
      const vs = document.createElement("span");
      vs.className = "fight-vs";
      vs.textContent = "VS.";
      board.appendChild(vs);
    }
    board.appendChild(fighterSide(fighter));
  });
  box.appendChild(board);

  if (duel.finished) {
    box.appendChild(finishCard(duel));
  } else if (!duel.started) {
    box.appendChild(standoff(duel));
  } else if (duel.yours && !duel.resting && !duel.chosen.attack) {
    box.appendChild(turnForm(data));
  } else if (duel.yours && duel.chosen.attack) {
    const wait = document.createElement("p");
    wait.className = "fight-line";
    wait.textContent = "Выбор принят. Ждём соперника.";
    box.appendChild(wait);
  } else if (duel.resting) {
    const rest = document.createElement("p");
    rest.className = "fight-line";
    rest.textContent = "Судья развёл по углам. Следующий раунд вот-вот.";
    box.appendChild(rest);
  }

  if (duel.log.length) box.appendChild(fightLog(duel));
  return box;
}

function standoff(duel) {
  // Гонг даёт тот, кто звал: соперник вышел, и его надо разглядеть до
  // первого удара. Второй в это время ждёт и может уйти.
  const box = document.createElement("div");
  box.className = "fight-standoff";
  const line = document.createElement("p");
  line.className = "fight-line";
  line.textContent = duel.yours_to_start
    ? "Соперник вышел. Начинать?"
    : "Ждём, пока вызвавший даст гонг.";
  box.appendChild(line);
  if (duel.yours_to_start) {
    const go = document.createElement("button");
    go.type = "button";
    go.className = "btn wide";
    go.textContent = "🥊 Выйти на ринг";
    go.addEventListener("click", () => fightAction({ action: "go" }));
    box.appendChild(go);
  }
  if (duel.yours) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "btn secondary wide";
    back.textContent = "Отказаться";
    back.addEventListener("click", () => fightAction({ action: "back" }));
    box.appendChild(back);
  }
  return box;
}

function finishCard(duel) {
  // Итог теми же словами, что судья сказал в ветке
  const box = document.createElement("div");
  box.className = "fight-finish";
  (duel.summary || []).forEach((said) => {
    if (!said) return;
    const line = document.createElement("p");
    line.className = "log-line";
    line.textContent = said;
    box.appendChild(line);
  });
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn wide";
  close.textContent = "Завершить бой";
  close.addEventListener("click", () => fightAction({ action: "done" }));
  box.appendChild(close);
  return box;
}

// Слова судьи об одном ударе. Урон в них подсвечен: обычный синим,
// критический и пробитый блок — красным. Порядок строк и ударов один и тот
// же, поэтому по номеру строки видно, каким был размен.
const DAMAGE = /−\d+/;

function judgeLine(text, strike) {
  const line = document.createElement("p");
  line.className = "log-line";
  const hit = DAMAGE.exec(text);
  if (!hit) {
    line.textContent = text;
    return line;
  }
  const heavy = strike && (strike.outcome === "crit" || strike.outcome === "break");
  const amount = document.createElement("span");
  amount.className = heavy ? "dmg crit" : "dmg";
  amount.textContent = hit[0];
  line.appendChild(document.createTextNode(text.slice(0, hit.index)));
  line.appendChild(amount);
  line.appendChild(document.createTextNode(text.slice(hit.index + hit[0].length)));
  return line;
}

function judgeLines(turn, into, newestFirst) {
  const lines = (turn.lines || []).map((said, index) => [
    said,
    (turn.strikes || [])[index],
  ]);
  if (newestFirst) lines.reverse();
  lines.forEach(([said, strike]) => into.appendChild(judgeLine(said, strike)));
  return into;
}

function fightLog(duel) {
  // Слова судьи сплошным текстом, свежее сверху. Раундов не считаем: в
  // ветке их держит заголовок сообщения, а здесь лента и так короткая.
  const box = document.createElement("div");
  box.className = "fight-log";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ход боя";
  box.appendChild(head);
  duel.log.slice().reverse().forEach((turn) => judgeLines(turn, box));
  return box;
}

// ---------- рейд ----------
//
// Отряд против одного босса. Волна — это по разу на каждого: нажал «Вперёд!» —
// размен посчитан сразу, не нажал за полминуты — пропустил удар. Экран
// опрашивает сервер, потому что волна может кончиться и без тебя.

let raidData = null;
let raidTimer = null;
let raidRate = 0;
let raidWasOver = false;
// Развёрнута ли карточка босса под заголовком
let bossOpen = false;
let raidBusy = false;
let raidClock = null;
// Что нарисовано на экране: по этой метке видно, изменилось ли хоть что-то
let raidPainted = null;
let raidDraft = { attacks: {}, block: null };
let raidWave = null;

// Как часто спрашивать состояние рейда. Пока идёт бой или собирается
// отряд — каждые две секунды: там волна идёт на секундах, и опоздать
// нельзя. Когда ничего не идёт, спрашивать так же часто незачем: экран
// всё равно показывает одно и то же, а каждый опрос — это девять
// запросов в базу и двадцать шесть килобайт по мобильной сети. На
// пустом экране это тридцать опросов в минуту ни для чего.
const RAID_POLL_LIVE = 2000;
const RAID_POLL_IDLE = 10000;

function raidPollRate(data) {
  return data && (data.raid || data.lobby || (data.lobbies || []).length)
    ? RAID_POLL_LIVE
    : RAID_POLL_IDLE;
}

function startWatchingRaid() {
  if (raidTimer) return;
  loadRaid();
  armRaidPoll(RAID_POLL_LIVE);
  // Часы тикают чаще, чем ходит опрос: секунда на экране должна быть секундой
  if (!raidClock) raidClock = setInterval(paintClocks, 1000);
}

function armRaidPoll(rate) {
  if (raidTimer && raidRate === rate) return;
  if (raidTimer) clearInterval(raidTimer);
  raidRate = rate;
  raidTimer = setInterval(loadRaid, rate);
}

function stopWatchingRaid() {
  if (!raidTimer) return;
  clearInterval(raidTimer);
  raidTimer = null;
  raidRate = 0;
  if (raidClock) clearInterval(raidClock);
  raidClock = null;
}

/** Срок в часах, минутах и секундах: 01:59:03.

    Длинные сроки — смена, занятие, тренировка, отсидка — читают именно
    так: «осталось 119:03» про два часа не говорит ничего, а «01:59:03»
    говорит всё. Рядом живёт `longClock` — тот же срок словами, для
    мест, где секунды не нужны вовсе. */
function fullClock(seconds) {
  const left = Math.max(0, Math.round(seconds));
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  const rest = left % 60;
  return [hours, minutes, rest]
    .map((one) => String(one).padStart(2, "0"))
    .join(":");
}

function clockText(seconds) {
  if (seconds <= 0) return "время вышло";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes
    ? minutes + ":" + String(rest).padStart(2, "0")
    : rest + " сек";
}

function paintClocks() {
  // Срок сбора приезжает с сервера один раз на опрос, а рисуем мы его каждую
  // секунду: до чего осталось, считаем по часам телефона от того ответа.
  document.querySelectorAll(".raid-clock").forEach((node) => {
    const until = Number(node.dataset.until || 0);
    const left = Math.max(0, Math.round((until - Date.now()) / 1000));
    node.textContent = "⏳ Выходим через " + clockText(left);
  });
}

function lobbyClock(lobby) {
  const line = document.createElement("p");
  line.className = "fight-row-note raid-clock";
  line.dataset.until = String(Date.now() + (lobby.seconds_left || 0) * 1000);
  line.textContent = "⏳ Выходим через " + clockText(lobby.seconds_left || 0);
  return line;
}

async function loadRaid() {
  try {
    const response = await fetch("api/raid", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (response.status === 404) {
      el("raid-note").textContent = "Сначала заведи бойца в личке бота.";
      return;
    }
    if (!response.ok) throw new Error("Подвал не отвечает.");
    renderRaid(await response.json());
  } catch (error) {
    el("raid-note").textContent = error.message;
  }
}

async function raidAction(payload) {
  if (raidBusy) return;
  raidBusy = true;
  // Всё, что между поднятым флагом и `finally`, обязано лежать внутри `try`:
  // иначе любой бросок оставляет флаг поднятым, и кнопки замолкают навсегда
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/raid", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      popup("Рейд", body.error || "Не вышло.");
      return;
    }
    renderRaid(body);
  } catch (error) {
    popup("Рейд", error.message);
  } finally {
    raidBusy = false;
  }
}

function raidShape(data) {
  // Из чего собран экран прямо сейчас. Времени здесь нет намеренно: остаток
  // до выхода меняется каждую секунду, а перерисовывать раздел ради него не
  // нужно — часы досчитывают на месте.
  const raid = data.raid;
  const lobby = data.lobby;
  const gate = data.gate || {};
  return JSON.stringify([
    bossOpen,
    data.can_fight,
    [gate.open, gate.won, gate.spent, gate.passes, gate.window],
    raid && [
      raid.id, raid.wave, raid.resting, raid.finished, raid.acted, raid.alive,
      raid.log.length,
      // Вся банда, а не один босс: пока у неё падает шестой из пяти,
      // «здоровье босса» не меняется вовсе, и табло замирало бы на
      // первой волне
      (raid.gang || [raid.boss]).map((one) => [one.hp, one.alive, one.against]),
      // Кого бьём сейчас. Цель меняется каждый ход, и без этой строки
      // подпись над кнопками оставалась бы от прошлого размена
      raid.foe && raid.foe.number,
      raid.party.map((one) => [one.user_id, one.hp, one.alive, one.acted]),
      // Шкала и заготовки. Без них нажатый приём не доезжал до экрана:
      // сервер честно списывал энергию и клал заготовку, а раздел не
      // перерисовывался — ничего из перечисленного выше от нажатия не
      // меняется, и подпись оставалась прежней до следующей волны
      raid.abilities && [
        raid.abilities.energy,
        raid.abilities.left,
        raid.abilities.tricks.map((one) => [one.code, one.armed, one.ready]),
      ],
      // Слова аналитика. Они меняются от размена к размену — он читает
      // прошлый ход босса, — а из перечисленного выше от этого не
      // меняется ничего: в чужой размен ни своя шкала, ни свои кнопки
      // не двигаются. Без этой строки подписчик до конца волны смотрел
      // бы на совет по позапрошлому ходу
      raid.scout && [
        raid.scout.attack,
        raid.scout.block,
        raid.scout.attack_tip.move,
        raid.scout.block_tip.move,
      ],
    ],
    lobby && [lobby.id, lobby.total, lobby.size, lobby.can_start],
    data.lobbies.map((one) => [one.id, one.total, one.size]),
  ]);
}

function renderRaid(data) {
  // Новая волна — намётки прошлой сбрасываем: то, что ушло судье, обратно
  // подсвечивать нечего
  const wave = data.raid ? data.raid.id + ":" + data.raid.wave : null;
  if (wave !== raidWave) {
    raidWave = wave;
    raidDraft = { attacks: {}, block: null };
  }
  if (data.raid && data.raid.finished && !raidWasOver) catchUp();
  raidWasOver = Boolean(data.raid && data.raid.finished);
  raidData = data;
  // Частоту опроса подбираем под то, что на экране: бой идёт — две
  // секунды, пусто — десять. Делаем это после ответа, а не до: до него
  // неизвестно, началось ли что-нибудь
  if (raidTimer) armRaidPoll(raidPollRate(data));

  // Ничего не поменялось — не трогаем экран. Опрос идёт каждые две секунды,
  // и перерисовка схлопывала бы под пальцем открытый список, гасила фокус и
  // сбрасывала прокрутку. Часы в объявлениях тикают отдельно, сами.
  const shape = raidShape(data);
  const body = el("raid-body");
  if (shape === raidPainted && body.firstChild) return;
  raidPainted = shape;
  body.textContent = "";
  if (data.kind) {
    body.appendChild(raidHead(data.kind));
    // Под «i» — весь состав: в казино один босс, на стадионе вся банда
    if (bossOpen) (data.roster || []).forEach(
      (one) => body.appendChild(bossStats(one))
    );
  }
  if (data.raid) {
    el("raid-note").textContent = "";
    body.appendChild(raidPanel(data));
  } else if (data.lobby) {
    el("raid-note").textContent = "Отряд собирается. Ждём остальных.";
    body.appendChild(raidLobby(data.lobby, true));
  } else {
    el("raid-note").textContent = data.can_fight
      ? "Собери отряд или влезь в чужой."
      : "Здоровье не то — сначала отдышись.";
    body.appendChild(raidOpenForm(data));
    data.lobbies.forEach((lobby) => body.appendChild(raidLobby(lobby, false)));
  }
}

function raidHead(kind) {
  // «Ограбление Босса Казино (i)» — заголовок и всё о нём
  const box = document.createElement("div");
  box.className = "raid-head";
  const title = document.createElement("h2");
  title.className = "shelf-head";
  title.textContent = kind.title;
  box.appendChild(title);

  const info = document.createElement("button");
  info.type = "button";
  info.className = "info-btn";
  info.id = "boss-info";
  info.textContent = "i";
  info.title = kind.alone
    ? "Характеристики босса"
    : kind.clones
      ? "Характеристики солдата"
      : "Состав банды";
  info.addEventListener("click", () => {
    bossOpen = !bossOpen;
    renderRaid(raidData);
  });
  box.appendChild(info);

  // Рассказ о рейде — под заголовком, во всю ширину: что это за дело, с
  // кем драться и когда пускают. Числа боец видит ниже, в условиях
  if (kind.story) {
    const story = document.createElement("p");
    story.className = "raid-story";
    story.textContent = kind.story;
    box.appendChild(story);
  }
  return box;
}

function bossStats(boss) {
  const box = document.createElement("div");
  box.className = "boss-stats";

  // В банде карточек несколько, и без заголовка они слипаются в одну
  // простыню: чья кукла и чьи числа, видно только по имени
  if (!boss.alone) {
    const who = document.createElement("h3");
    who.className = "shelf-head";
    who.textContent = boss.emoji + " " + boss.title;
    box.appendChild(who);
  }

  // Кукла босса — тем же кодом, что и карточка бойца: аватар в середине,
  // слоты по бокам, под пустыми — подложки.
  //
  // Под куклой ничего не пишем. Прежде тут стояли две строки — повадка
  // противника и объяснение, как он подстраивается под отряд, — но про
  // сам рейд теперь рассказывает его рассказ под заголовком, а здесь
  // место для чисел: уровень, здоровье, урон, броня
  if (boss.slots) box.appendChild(sheetDoll(boss));

  const rows = document.createElement("div");
  rows.className = "boss-rows";
  const add = (label, value) => {
    const row = document.createElement("p");
    row.className = "boss-row";
    const name = document.createElement("span");
    name.textContent = label;
    const val = document.createElement("b");
    val.textContent = value;
    row.appendChild(name);
    row.appendChild(val);
    rows.appendChild(row);
  };
  add("Уровень", String(boss.level));
  add("Класс", boss.fclass_emoji + " " + boss.fclass);
  add("Здоровье", String(boss.max_hp));
  add(
    "Удар " + boss.weapon_icon + " " + boss.weapon,
    boss.damage[0] + "–" + boss.damage[1]
  );
  add("💪 Сила", String(boss.stats.strength));
  add("🤸 Ловкость", String(boss.stats.agility));
  add("🔮 Интуиция", String(boss.stats.intuition));
  add("🫀 Выносливость", String(boss.stats.endurance));
  add("🎯 Точность", boss.combat.accuracy + "%");
  add("🌀 Уворот", boss.combat.dodge_chance + "%");
  add("💥 Крит", boss.combat.crit_chance + "%");
  add("🚫 Антикрит", boss.combat.anticrit + "%");
  add("🔄 Контрудар", boss.combat.counter_chance + "%");
  add("🪨 Сопротивление", boss.combat.resist + "%");
  add("🗡 Пробивание", boss.combat.penetration + "%");
  add("🛡🩸 Держит блок", boss.combat.block_hold + "%");
  boss.armor.forEach((zone) => {
    if (zone.max) add(zone.emoji + " Броня: " + zone.title, zone.min + "–" + zone.max);
  });
  box.appendChild(rows);
  return box;
}

async function raidTicket(gate, what) {
  // Окно согласия на вход в подвал. Пропуск есть — тратим его; пропуска
  // нет — покупаем тут же, одним «Подтвердить»; окно уже оплачено — не
  // спрашиваем вовсе.
  if (gate.spent) return { go: true, buy: false };
  if (gate.passes > 0) {
    const ok = await askConfirm(
      gate.pass_emoji + " " + what,
      "Вы используете " + gate.pass_title + " из инвентаря. Останется: " +
        (gate.passes - 1)
    );
    return { go: ok, buy: false };
  }
  if (!gate.can_afford) {
    popup(
      "Нужен " + gate.pass_title,
      "В инвентаре пусто, и на счету меньше " + gate.pass_price +
        " 💰. Купить его можно в лавке клуба, раздел «Прочее»."
    );
    return { go: false, buy: false };
  }
  const ok = await askConfirm(
    gate.pass_emoji + " " + what,
    "В инвентаре нет пропусков. Купить " + gate.pass_title + " за " +
      gate.pass_price + " кредитов и войти?",
    "Купить и войти"
  );
  return { go: ok, buy: true };
}

async function raidEnter(gate, what, payload) {
  const answer = await raidTicket(gate, what);
  if (!answer.go) return;
  raidAction({ ...payload, buy: answer.buy });
}

function raidOpenForm(data) {
  const gate = data.gate || {};
  const kind = data.kind || {};
  const box = document.createElement("div");
  box.className = "fight-open";

  const line = document.createElement("p");
  line.className = "fight-line";
  line.textContent = gate.open
    ? "Открыто " + gate.window + "."
    : "Закрыто. Пускают " + gate.schedule + ".";
  box.appendChild(line);

  const note = document.createElement("p");
  note.className = "fight-row-note";
  note.textContent = gate.won
    ? "Своё в это окно ты уже взял. Следующее — " + gate.next_window + "."
    : gate.spent
      ? "Пропуск за это окно отдан — заходи хоть до самого конца."
      : gate.open
        ? gate.pass_emoji + " " + gate.pass_title + ", в инвентаре: " +
          (gate.passes || 0) + " шт."
        : "Ближайшее окно " + gate.next_window + ".";
  box.appendChild(note);

  // Чем рейд встречает: сколько противников и растёт ли их число с
  // отрядом. Без этой строки «сложный» рейд выглядел бы как обычный
  const terms = document.createElement("p");
  terms.className = "fight-row-note";
  terms.textContent = kind.alone
    ? "Босс подстроится под отряд и будет выше него на четыре уровня."
    : "Против отряда выходит " + kind.foes + " " +
      plural(kind.foes, "человек", "человека", "человек") + " " +
      kind.foe_level + "-го уровня" +
      (kind.grows ? ", и ещё по одному за каждого бойца сверх " +
        kind.min_party + "." : ".") +
      " Нужно собрать хотя бы " + kind.min_party + ".";
  box.appendChild(terms);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.id = "raid-open";
  btn.className = "btn wide";
  btn.textContent = "🩸 Собрать отряд";
  btn.disabled = !data.can_fight || !gate.open || gate.won;
  btn.addEventListener("click", () =>
    raidEnter(gate, "Собрать отряд", { action: "open" })
  );
  box.appendChild(btn);
  return box;
}

function raidLobby(lobby, mine) {
  const box = document.createElement("div");
  box.className = "fight-card";
  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent =
    lobby.boss.emoji + " " + lobby.boss.title + " — отряд " +
    lobby.total + "/" + lobby.size;
  if (lobby.total < lobby.min_party) {
    head.textContent += " (нужно " + lobby.min_party + ")";
  }
  box.appendChild(head);

  const names = document.createElement("p");
  names.className = "fight-row-note";
  names.textContent = lobby.members
    .map((row) => row.name + " [" + row.level + "]")
    .join(", ");
  box.appendChild(names);
  box.appendChild(lobbyClock(lobby));

  // Не ждать отсчёта может только созвавший — и только когда отряд собран
  // хотя бы наполовину боеспособно
  if (lobby.mine && lobby.can_start) {
    const go = document.createElement("button");
    go.type = "button";
    go.id = "raid-start-now";
    go.className = "btn wide spaced";
    go.textContent = "⚔️ Начать сейчас";
    go.addEventListener("click", () => raidAction({ action: "go" }));
    box.appendChild(go);
  }

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = mine ? "btn secondary wide" : "btn wide";
  btn.textContent = mine ? "Выйти из отряда" : "🩸 В отряд";
  btn.addEventListener("click", () => {
    if (mine) {
      raidAction({ action: "leave" });
      return;
    }
    raidEnter((raidData && raidData.gate) || {}, "В отряд", {
      action: "join",
      lobby_id: lobby.id,
    });
  });
  box.appendChild(btn);
  return box;
}

function raidPanel(data) {
  const raid = data.raid;
  const box = document.createElement("div");
  box.className = "fight-panel";

  // Номер волны с экрана убран: он ни на что не влияет, а стоял над
  // шкалами здоровья — тем единственным, на что в бою смотрят не
  // отрываясь. Передышку и конец рейда говорим по-прежнему: это не
  // счёт, а состояние, и от него зависит, чего ждать
  const said = raid.finished
    ? "🔔 Рейд окончен"
    : raid.resting
      ? "😮‍💨 Передышка"
      : "";
  if (said) {
    const head = document.createElement("p");
    head.className = "fight-round";
    head.textContent = said;
    box.appendChild(head);
  }
  box.appendChild(raidBoard(raid));

  if (raid.finished) {
    box.appendChild(raidFinish(raid));
  } else if (!raid.alive) {
    const out = document.createElement("p");
    out.className = "fight-line";
    out.textContent = "Тебя вынесли. Отряд дерётся дальше.";
    box.appendChild(out);
  } else if (raid.resting) {
    const rest = document.createElement("p");
    rest.className = "fight-line";
    rest.textContent = "Отряд переводит дух. Следующая волна вот-вот.";
    box.appendChild(rest);
    box.appendChild(restingTricks(raid));
  } else if (raid.acted) {
    const wait = document.createElement("p");
    wait.className = "fight-line";
    wait.textContent = "Удар засчитан. Ждём остальных.";
    box.appendChild(wait);
    // Шкала остаётся на виду: в рейде между ходом и разменом проходит вся
    // волна, и если панель убрать, боец так и не увидит, куда делась
    // энергия и сработала ли заготовка
    box.appendChild(restingTricks(raid));
  } else {
    box.appendChild(raidTurnForm(data));
  }

  if (raid.log.length) box.appendChild(raidLog(raid));
  return box;
}

// Доска боя — две колоды, по колоде на сторону, и обе лежат на нижнем
// краю. Верхняя карта колоды — та, что ближе всех к смотрящему, — лежит
// у самого низа и во всю величину: слева это ты, справа тот, с кем ты
// дерёшься сейчас. Следующая карта колоды выглядывает из-под неё сверху,
// за ней третья, и так вверх по экрану: чем глубже карта в колоде, тем
// выше она стоит и тем меньше её видно — только верхний край с именем и
// шкалой.
//
// Колода противников крутится вместе с очередью: верхней становится тот,
// с кем размен идёт сейчас, за ним — следующий по кругу, и так до конца
// колоды, после чего круг начинается заново.
//
// Павшие ложатся глубже всех, то есть в самом верху экрана: помочь и
// добить можно только живых, а павший посреди колоды каждый раз сбивает
// счёт.
//
// Раньше обе стороны стояли списками во всю высоту, и десять карточек
// отряда выдавливали с экрана кнопки хода. Колода показывает каждого, но
// одной строкой.
function raidBoard(raid) {
  const box = document.createElement("div");
  box.className = "raid-board";
  const party = raid.party || [];
  const you = party.find((member) => member.you) || party[0] || null;
  const foes = raid.gang || (raid.boss ? [raid.boss] : []);
  const foe = currentFoe(raid, foes);

  const mates = party.filter((member) => !samePlace(member, you, "user_id"));
  box.appendChild(
    deckOf("raid-mates", mateCards(mates), you ? youCard(you) : null)
  );
  const swords = document.createElement("p");
  swords.className = "versus";
  swords.textContent = "⚔️";
  box.appendChild(swords);
  box.appendChild(
    deckOf(
      "raid-foes",
      foeCards(inTurnOrder(foes, foe)),
      foe ? bossCard(foe, true) : null
    )
  );
  return box;
}

// Колода противников в порядке очереди: сразу за текущим идёт тот, на
// кого размен перейдёт следующим, и так по кругу. Сам текущий из колоды
// уходит — он лежит сверху крупной картой.
//
// Круг здесь тот же, что и в бою (`next_foe` в службе): он идёт по
// порядку, в котором банда выставляет своих, и перешагивает павших.
// Поэтому достаточно провернуть список до текущего — дальше очередь
// совпадает сама собой.
function inTurnOrder(foes, current) {
  const at = foes.findIndex((one) => samePlace(one, current, "number"));
  const ring = at < 0 ? foes.slice() : foes.slice(at).concat(foes.slice(0, at));
  return ring.filter((one) => !samePlace(one, current, "number"));
}

// Тот же боец, что и в крупной карточке? Сравниваем по номеру, а не по
// самому объекту: сервер собирает цель и табло по отдельности, и это две
// разные записи с одинаковым содержимым. По объектам своего соперника
// видно было бы дважды — и крупно, и в стопке.
function samePlace(one, other, key) {
  return Boolean(one) && Boolean(other) && one[key] === other[key];
}

// С кем смотрящий стоит прямо сейчас. В банде это его цель, и сервер
// называет её сам; в казино противник один — он и есть текущий. Цель
// добили, а новая ещё не вышла — показываем первого живого: пустое место
// на этой половине экрана читалось бы как «рейд кончился».
function currentFoe(raid, foes) {
  if (raid.foe) return raid.foe;
  return foes.find((one) => one.alive) || foes[0] || null;
}

// Карточка смотрящего: крупная, как и карточка его соперника. Урон тут
// не для красоты — по нему считают, кому достанется вещь с банды.
function youCard(member) {
  const box = document.createElement("div");
  box.className = "raid-you" + (member.alive ? "" : " down");
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent =
    (member.alive ? (member.acted ? "✅ " : "⏳ ") : "💀 ") +
    member.emoji + " " + member.name + " [" + member.level + "]" +
    (member.you ? " — ты" : "");
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = member.hp + "/" + member.max_hp + " · урона " +
    member.damage_dealt;
  box.appendChild(name);
  box.appendChild(hp);
  box.appendChild(fightBar(member));
  return box;
}

// Карточка соперника: сначала лицо, под ним имя и шкала. Порядок именно
// такой — на кого смотрят, то и сверху, а числа читают уже под ним.
// Рамка синяя у того, с кем стоишь, серая у всех остальных: подпись
// «твой» для этого была лишней, рамку видно быстрее слова.
function bossCard(boss, withFace) {
  const box = document.createElement("div");
  box.className = "boss-card";
  if (!boss.alive) box.classList.add("dropped");
  if (withFace) box.classList.add("mine");

  if (withFace && boss.image) {
    const img = document.createElement("img");
    img.className = "boss-face";
    img.src = boss.image;
    img.alt = boss.title;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => img.remove());
    box.appendChild(img);
  }

  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent = boss.emoji + " " + boss.title + " [" + boss.level + "]";
  box.appendChild(name);
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = boss.alive ? boss.hp + "/" + boss.max_hp : "💀 готов";
  box.appendChild(hp);
  if (boss.alive) box.appendChild(fightBar(boss));
  return box;
}

// Стопка: карточки лежат внахлёст, и у каждой видна верхняя часть — имя
// и шкала. Больше в стопке и не нужно: по ней смотрят, кто ещё держится,
// а не чем он одет.
// Колода одной стороны: карты снизу вверх. Верхняя карта колоды лежит у
// самого низа и во всю величину, под ней (то есть выше на экране) вторая,
// за ней третья — и чем глубже карта в колоде, тем выше она на экране.
//
// Поэтому список карт и разворачивается: в разметке первой идёт самая
// глубокая. Порядок в разметке — это и порядок слоёв: каждая следующая
// карта рисуется поверх предыдущей и прячет у неё низ, оставляя на виду
// верхний край с именем и шкалой. Положи их в прямом порядке — и колода
// ляжет лицом вниз: прятался бы верх карты, а не низ.
function deckOf(kind, peeks, top) {
  const box = document.createElement("div");
  box.className = "raid-deck " + kind;
  peeks.slice().reverse().forEach((card) => box.appendChild(card));
  if (top) box.appendChild(top);
  return box;
}

// Живые сверху, павшие вниз: помочь можно только тем, кто ещё дерётся, а
// павший в середине стопки каждый раз сбивает счёт. Сортировка
// устойчивая, поэтому порядок внутри половины остаётся прежним.
function byTheLiving(crowd) {
  return crowd.slice().sort((one, other) => Number(other.alive) - Number(one.alive));
}

function stackCard(kind, alive) {
  const card = document.createElement("div");
  card.className = "stack-card " + kind + (alive ? "" : " down");
  return card;
}

// Голова карточки в стопке: имя слева, остаток справа. Двумя строками, а
// не тремя, — иначе стопка из двенадцати займёт весь экран.
function stackHead(card, title, note) {
  const head = document.createElement("div");
  head.className = "stack-head";
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent = title;
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = note;
  head.appendChild(name);
  head.appendChild(hp);
  card.appendChild(head);
}

// Готов ли боец к ходу. В рейде это `acted` — удар уже отправлен; в
// командном бою `ready` — то же самое другим словом
function mateMark(member) {
  if (!member.alive) return "💀 ";
  return (member.acted || member.ready) ? "✅ " : "⏳ ";
}

// Карты отряда, в порядке колоды: сразу за верхней идёт ближайший
// союзник, павшие ложатся глубже всех
function mateCards(party) {
  return byTheLiving(party).map((member) => {
    const card = stackCard("mate", member.alive);
    stackHead(
      card,
      mateMark(member) + member.emoji + " " + member.name,
      member.alive ? member.hp + "/" + member.max_hp : "выбыл"
    );
    card.appendChild(fightBar(member));
    return card;
  });
}

function foeCards(foes) {
  return byTheLiving(foes).map((foe) => {
    const card = stackCard("foe", foe.alive);
    stackHead(
      card,
      (foe.alive ? "" : "💀 ") + foe.emoji + " " + foe.title,
      foe.alive ? foe.hp + "/" + foe.max_hp : "готов"
    );
    card.appendChild(fightBar(foe));
    return card;
  });
}

// Шкала и заготовки, пока ход уже сделан: смотреть можно, нажимать
// нечего. Приём жмут перед ударом, и разрешать это после «Вперёд» значило
// бы заряжать вслепую в следующую волну
function restingTricks(state) {
  const panel = abilityPanel(state && state.abilities, () => {});
  if (!panel) return document.createDocumentFragment();
  panel.classList.add("watching");
  panel.querySelectorAll(".trick").forEach((card) => {
    card.disabled = true;
  });
  // «За ход осталось приёмов: 3» над кнопками, которые не нажимаются, —
  // обещание, которого панель сейчас не держит. Говорим, как есть
  const note = panel.querySelector(".energy-note");
  if (note) note.textContent = "Ход уже сделан — заготовки ждут размена.";
  return panel;
}

function raidHands(data) {
  return (data.raid && data.raid.hands) || [{ hand: 0, icon: "👊", title: "Кулаки" }];
}

function raidReady(hands) {
  return Boolean(raidDraft.block) && hands.every((row) => raidDraft.attacks[row.hand]);
}

function raidAim(foe) {
  // В казино босс один, и подпись «бьём его» ничего не добавляет
  if (!foe || !foe.alive) return null;
  const line = document.createElement("p");
  line.className = "raid-aim";
  line.textContent = "Бьём: " + foe.emoji + " " + foe.title +
    " [" + foe.hp + "/" + foe.max_hp + "]";
  return line;
}

function raidTurnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";
  const hands = raidHands(data);
  const blocks = (data.raid && data.raid.blocks) || data.blocks;

  // Кого бьём сейчас — строкой над кнопками, и первой: цель в банде
  // меняется каждый ход, и нажимать удар, не зная, по кому, нельзя
  const aim = raidAim(data.raid && data.raid.foe);
  if (aim) box.appendChild(aim);

  const tricks = abilityPanel(data.raid && data.raid.abilities, (trick) =>
    useAbility("api/raid", trick, renderRaid)
  );
  if (tricks) box.appendChild(tricks);

  // Совет — строкой над кнопками: одно действие и одно число, его
  // читают перед самым нажатием
  const tips = tipsRow(data.raid && data.raid.scout);
  if (tips) box.appendChild(tips);

  box.appendChild(
    zoneColumns(hands, data.attacks, blocks, () => raidDraft, "raid", paintRaidDraft)
  );

  const go = document.createElement("button");
  go.type = "button";
  go.id = "raid-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !raidReady(hands);
  go.addEventListener("click", () => {
    const move = { action: "turn", attacks: raidDraft.attacks, block: raidDraft.block };
    raidDraft = { attacks: {}, block: null };
    raidAction(move);
  });
  box.appendChild(go);

  // Разбор повадок — под кнопками и свёрнутый. Он длинный, а место над
  // кнопками занято тем, ради чего сюда смотрят: шкалами и советом.
  // Кому нужен весь разбор — развернёт, и он таким и останется
  const scout = scoutPanel(data.raid && data.raid.scout);
  if (scout) box.appendChild(scout);
  return box;
}

function paintRaidDraft() {
  document.querySelectorAll("#club-raid .zone").forEach((label) => {
    const dot = label.querySelector("input");
    label.classList.toggle("on", Boolean(dot && dot.checked));
  });
  const go = el("raid-go");
  if (go) go.disabled = !raidReady(raidHands(raidData || {}));
}

function raidFinish(raid) {
  const box = document.createElement("div");
  box.className = "fight-finish";
  (raid.summary || []).forEach((said) => {
    if (!said) return;
    const line = document.createElement("p");
    line.className = "log-line";
    line.textContent = said;
    box.appendChild(line);
  });
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn wide";
  close.textContent = "Завершить рейд";
  close.addEventListener("click", () => raidAction({ action: "done" }));
  box.appendChild(close);
  return box;
}

function raidLog(raid) {
  const box = document.createElement("div");
  box.className = "fight-log";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ход рейда";
  box.appendChild(head);
  // Свежее сверху — и между разменами, и внутри размена. Размены и
  // раньше шли от нового к старому, а вот строки внутри одного
  // оставались в прямом порядке: лента читалась то вниз, то вверх, и в
  // волне на десять бойцов понять, что было позже, не получалось
  raid.log.slice().reverse().forEach((turn) => judgeLines(turn, box, true));
  return box;
}

// ---------- групповой бой ----------
//
// Команда на команду или мясорубка. Состав собирают здесь же или в ветке
// группы, а дерутся только тут: набор кнопок у каждого свой — по оружию и
// щиту. Раунд считается, когда нажали все, кому в этом ходу досталась пара.

let battleData = null;
let battleTimer = null;
let battleWasOver = false;
let battleBusy = false;
let battleDraft = { attacks: {}, block: null };
let battleRound = null;

function startWatchingBattle() {
  if (battleTimer) return;
  loadBattle();
  battleTimer = setInterval(loadBattle, 2000);
}

function stopWatchingBattle() {
  if (!battleTimer) return;
  clearInterval(battleTimer);
  battleTimer = null;
}

async function loadBattle() {
  try {
    const response = await fetch("api/battle", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (response.status === 404) {
      el("battle-note").textContent = "Сначала заведи бойца в личке бота.";
      return;
    }
    if (!response.ok) throw new Error("Клуб не отвечает.");
    renderBattle(await response.json());
  } catch (error) {
    el("battle-note").textContent = error.message;
  }
}

async function battleAction(payload) {
  if (battleBusy) return;
  battleBusy = true;
  // Всё, что между поднятым флагом и `finally`, обязано лежать внутри `try`:
  // иначе любой бросок оставляет флаг поднятым, и кнопки замолкают навсегда
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/battle", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      popup("Отряд", body.error || "Не вышло.");
      return;
    }
    renderBattle(body);
  } catch (error) {
    popup("Отряд", error.message);
  } finally {
    battleBusy = false;
  }
}

function renderBattle(data) {
  // Новый раунд — намётки прошлого сбрасываем
  const round = data.battle ? data.battle.id + ":" + data.battle.round : null;
  if (round !== battleRound) {
    battleRound = round;
    battleDraft = { attacks: {}, block: null };
  }
  if (data.battle && data.battle.finished && !battleWasOver) catchUp();
  battleWasOver = Boolean(data.battle && data.battle.finished);
  battleData = data;
  const body = el("battle-body");
  body.textContent = "";
  if (data.battle) {
    el("battle-note").textContent = "";
    body.appendChild(battlePanel(data));
  } else if (data.lobby) {
    el("battle-note").textContent = "Состав собирается. Ждём остальных.";
    body.appendChild(battleLobby(data.lobby, true));
  } else {
    el("battle-note").textContent = data.can_fight
      ? "Собери состав или влезь в чужой."
      : "Здоровье не то — сначала отдышись.";
    body.appendChild(battleOpenForm(data));
    data.lobbies.forEach((lobby) => body.appendChild(battleLobby(lobby, false)));
  }
}

// Что выбрано в форме сбора: чем дерутся, сколько зовут, какого
// уровня и сколько ждут. Живёт между перерисовками — ответ ручки
// приходит каждые пару секунд, и сбрасывать выбор на каждом было бы
// издевательством
const battleForm = {
  kind: "team",
  mode: "armed",
  size: 3,
  levels: "near",
  wait: 5,
};

/** Форма сбора: четыре вопроса и одна кнопка.

    Раньше тут стояли четыре готовые кнопки — «командный на 2», «на 5»
    и столько же для королевской битвы, — и всё остальное решалось за
    игрока: оружие, уровни и срок сбора. Теперь спрашиваем. */
function battleOpenForm(data) {
  const box = document.createElement("div");
  box.className = "fight-open";

  const line = document.createElement("p");
  line.className = "fight-line";
  line.textContent =
    "Командный бой — сторона на сторону, королевская битва — каждый сам "
    + "за себя, и выигрывает последний на ногах.";
  box.appendChild(line);

  // Чем дерутся и какой это бой
  box.appendChild(formRow("Бой", (data.kinds || []).map((one) => ({
    code: one.code,
    title: one.emoji + " " + one.title,
  })), "kind", () => renderBattle(battleData)));
  box.appendChild(formRow("Чем", (data.modes || []).map((one) => ({
    code: one.code,
    title: one.emoji + " " + one.title,
  })), "mode"));

  // Сколько бойцов: у команды — с каждой стороны, у королевской — всего
  const kind = (data.kinds || []).find((one) => one.code === battleForm.kind)
    || { min: 2, max: 10 };
  const sizes = [];
  for (let size = kind.min; size <= kind.max; size += 1) {
    sizes.push({ code: size, title: String(size) });
  }
  if (battleForm.size < kind.min) battleForm.size = kind.min;
  if (battleForm.size > kind.max) battleForm.size = kind.max;
  box.appendChild(formRow(
    battleForm.kind === "team" ? "С каждой стороны" : "Бойцов", sizes, "size"
  ));

  box.appendChild(formRow("Уровень соперников",
    (data.levels || []).map((one) => ({ code: one.code, title: one.title })),
    "levels"));
  box.appendChild(formRow("На сбор",
    (data.waits || []).map((one) => ({ code: one.minutes, title: one.title })),
    "wait"));

  const note = document.createElement("p");
  note.className = "fight-row-note";
  const level = (data.levels || []).find((one) => one.code === battleForm.levels);
  note.textContent =
    (level ? level.note + " " : "")
    + "Время вышло, а с каждой стороны есть хоть один — гонг.";
  box.appendChild(note);

  const go = document.createElement("button");
  go.type = "button";
  go.className = "btn wide";
  go.textContent = "⚔️ Собрать состав";
  go.disabled = !data.can_fight;
  go.addEventListener("click", () => battleAction({
    action: "open",
    kind: battleForm.kind,
    mode: battleForm.mode,
    size: battleForm.size,
    levels: battleForm.levels,
    wait: battleForm.wait,
  }));
  box.appendChild(go);
  return box;
}

/** Строка выбора: подпись и чипы. Выбранный горит, нажатый меняет. */
function formRow(label, options, field, after) {
  const row = document.createElement("div");
  row.className = "form-row";
  const name = document.createElement("span");
  name.className = "form-label";
  name.textContent = label;
  row.appendChild(name);

  const chips = document.createElement("div");
  chips.className = "bubbles";
  options.forEach((one) => {
    chips.appendChild(chip(one.title, battleForm[field] === one.code, () => {
      battleForm[field] = one.code;
      if (after) after();
      else renderBattle(battleData);
    }));
  });
  row.appendChild(chips);
  return row;
}

function battleLobby(lobby, mine) {
  const box = document.createElement("div");
  box.className = "fight-card";
  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent =
    lobby.emoji + " " + lobby.kind_title + " " + lobby.mode.emoji + " — " +
    lobby.total + "/" + lobby.capacity +
    " · уровни " + lobby.min_level + "–" + lobby.max_level +
    " · сбор " + lobby.wait_text;
  box.appendChild(head);

  lobby.teams.forEach((team) => {
    const row = document.createElement("p");
    row.className = "fight-row-note";
    const names = team.members.map((one) => one.name).join(", ");
    row.textContent =
      (lobby.kind === "team" ? team.title + ": " : "") + (names || "пусто");
    box.appendChild(row);
    if (mine || lobby.joined) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn wide";
    btn.textContent = lobby.kind === "team" ? "В " + team.title : "⚔️ В бой";
    btn.disabled = team.free <= 0;
    btn.addEventListener("click", () =>
      battleAction({ action: "join", lobby_id: lobby.id, team: team.team })
    );
    box.appendChild(btn);
  });

  if (mine || lobby.joined) {
    const out = document.createElement("button");
    out.type = "button";
    out.className = "btn secondary wide";
    out.textContent = "Выйти из состава";
    out.addEventListener("click", () => battleAction({ action: "leave" }));
    box.appendChild(out);
  }
  return box;
}

function battlePanel(data) {
  const battle = data.battle;
  const box = document.createElement("div");
  box.className = "fight-panel";

  const head = document.createElement("p");
  head.className = "fight-round";
  head.textContent = battle.finished
    ? "🔔 Бой окончен"
    : battle.emoji + " " + battle.kind_title + " — раунд " + battle.round;
  box.appendChild(head);
  box.appendChild(battleBoard(battle));

  if (battle.finished) {
    box.appendChild(battleFinish(battle));
  } else if (!battle.alive) {
    const out = document.createElement("p");
    out.className = "fight-line";
    out.textContent = "Тебя вынесли. Остальные дерутся дальше.";
    box.appendChild(out);
  } else if (!battle.fighting) {
    const idle = document.createElement("p");
    idle.className = "fight-line";
    idle.textContent = "В этом ходу пары не досталось — ждём следующий раунд.";
    box.appendChild(idle);
  } else if (battle.acted) {
    const wait = document.createElement("p");
    wait.className = "fight-line";
    wait.textContent = "Ход засчитан. Ждём остальных.";
    box.appendChild(wait);
    box.appendChild(restingTricks(battle));
  } else {
    box.appendChild(battleTurnForm(data));
  }

  if (battle.log.length) box.appendChild(battleLog(battle));
  return box;
}

// Табло командного боя: та же доска, что и в рейде. Внизу ты и тот, с
// кем тебя свела пара этого хода, — верхние карты своих колод; выше
// остальные: слева свои, справа чужие. В бою «каждый сам за себя» своих
// нет вовсе, и слева пусто: это честно, там и правда никого.
function battleBoard(battle) {
  const box = document.createElement("div");
  box.className = "raid-board";
  const party = battle.party || [];
  const you = party.find((member) => member.you) || party[0] || null;
  const rival =
    you && you.rival_id != null
      ? party.find((member) => member.user_id === you.rival_id) || null
      : null;

  const rest = party.filter(
    (member) => member !== you && member !== rival
  );
  const ours = (member) =>
    battle.kind === "team" && you && member.team === you.team;

  box.appendChild(
    deckOf(
      "raid-mates",
      mateCards(rest.filter(ours)),
      you ? battleCard(battle, you, "raid-you") : null
    )
  );
  const swords = document.createElement("p");
  swords.className = "versus";
  swords.textContent = "⚔️";
  box.appendChild(swords);
  box.appendChild(
    deckOf(
      "raid-foes",
      mateCards(rest.filter((member) => !ours(member))),
      rival ? battleCard(battle, rival, "boss-card mine") : noRivalCard()
    )
  );
  return box;
}

// Крупная карточка командного боя. Имя с командой и уроном: в групповом
// бою по урону считают, кто вытащил раунд, и прятать его некуда.
function battleCard(battle, member, kind) {
  const box = document.createElement("div");
  box.className = kind + (member.alive ? "" : " down dropped");
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent =
    mateMark(member) + member.emoji + " " + member.name +
    " [" + member.level + "]" +
    (battle.kind === "team" ? " · " + member.team_title : "") +
    (member.you ? " — ты" : "");
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = member.alive
    ? member.hp + "/" + member.max_hp + " · урона " + member.damage_dealt
    : "💀 выбыл";
  box.appendChild(name);
  box.appendChild(hp);
  if (member.alive) box.appendChild(fightBar(member));
  return box;
}

// Пары в этом ходу не досталось: место соперника всё равно занято — иначе
// доска съезжает, и непонятно, пропал соперник или пропала карточка
function noRivalCard() {
  const box = document.createElement("div");
  box.className = "boss-card empty";
  const said = document.createElement("p");
  said.className = "fight-hp";
  said.textContent = "без пары в этом ходу";
  box.appendChild(said);
  return box;
}

function battleHands(data) {
  return (
    (data.battle && data.battle.hands) || [{ hand: 0, icon: "👊", title: "Кулаки" }]
  );
}

function battleReady(hands) {
  return (
    Boolean(battleDraft.block) && hands.every((row) => battleDraft.attacks[row.hand])
  );
}

function battleTurnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";
  const hands = battleHands(data);
  const blocks = (data.battle && data.battle.blocks) || data.blocks;

  const tricks = abilityPanel(data.battle && data.battle.abilities, (trick) =>
    useAbility("api/battle", trick, renderBattle)
  );
  if (tricks) box.appendChild(tricks);

  box.appendChild(
    zoneColumns(
      hands, data.attacks, blocks, () => battleDraft, "battle", paintBattleDraft
    )
  );

  const go = document.createElement("button");
  go.type = "button";
  go.id = "battle-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !battleReady(hands);
  go.addEventListener("click", () => {
    const move = {
      action: "turn",
      attacks: battleDraft.attacks,
      block: battleDraft.block,
    };
    battleDraft = { attacks: {}, block: null };
    battleAction(move);
  });
  box.appendChild(go);
  return box;
}

function paintBattleDraft() {
  document.querySelectorAll("#club-battle .zone").forEach((label) => {
    const dot = label.querySelector("input");
    label.classList.toggle("on", Boolean(dot && dot.checked));
  });
  const go = el("battle-go");
  if (go) go.disabled = !battleReady(battleHands(battleData || {}));
}

function battleFinish(battle) {
  const box = document.createElement("div");
  box.className = "fight-finish";
  (battle.summary || []).forEach((said) => {
    if (!said) return;
    const line = document.createElement("p");
    line.className = "log-line";
    line.textContent = said;
    box.appendChild(line);
  });
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn wide";
  close.textContent = "Завершить бой";
  close.addEventListener("click", () => battleAction({ action: "done" }));
  box.appendChild(close);
  return box;
}

function battleLog(battle) {
  const box = document.createElement("div");
  box.className = "fight-log";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ход боя";
  box.appendChild(head);
  battle.log.slice().reverse().forEach((turn) => judgeLines(turn, box));
  return box;
}

// ---------- статистика боёв ----------
//
// Список боёв по дням, а из него — провал в разбор одного боя по ходам.
// Смотреть можно и чужую историю: кнопка «i» в списке клуба ведёт сюда же.

let statsData = null;
let statsWho = null; // null — своя история

async function loadHistory(userId) {
  statsWho = userId || null;
  el("stats-note").textContent = "Открываем...";
  try {
    const query = statsWho ? "?user_id=" + statsWho : "";
    const response = await fetch("api/history" + query, {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Историю не открыть.");
    renderHistory(body);
  } catch (error) {
    el("stats-note").textContent = error.message;
    el("stats-body").textContent = "";
  }
}

function renderHistory(data) {
  statsData = data;
  const counts = data.counts;
  const people = counts.win + counts.loss + counts.draw;
  const raids = data.raids || { wins: 0, total: 0 };
  // Рейды считаем отдельной припиской: в списке они идут вперемешку с
  // дуэлями, но складывать победу над боссом с победой над человеком
  // нельзя — счёт от этого врёт в обе стороны
  const tail = raids.total
    ? " · рейды " + raids.wins + " / " + raids.total
    : "";
  el("stats-note").textContent = data.total
    ? data.name + ": " + people + " " +
      plural(people, "бой", "боя", "боёв") + " — " +
      counts.win + " побед, " + counts.loss + " поражений, " +
      counts.draw + " ничьих" + tail
    : data.name + " ещё не дрался.";

  const body = el("stats-body");
  body.textContent = "";
  if (statsWho) {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "btn secondary wide";
    back.textContent = "← Моя статистика";
    back.addEventListener("click", () => loadHistory(null));
    body.appendChild(back);
  }
  data.days.forEach((day) => {
    const head = document.createElement("h2");
    head.className = "shelf-head";
    head.textContent = prettyDay(day.date);
    body.appendChild(head);
    day.fights.forEach((fight) => body.appendChild(fightRow(fight)));
  });
}

function prettyDay(date) {
  // «2026-09-03» → «3 сентября»: год в списке за сегодня только мешает
  const months = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
  ];
  const parts = (date || "").split("-");
  if (parts.length !== 3) return date || "";
  const month = months[Number(parts[1]) - 1];
  return month ? Number(parts[2]) + " " + month : date;
}

function fightRow(fight) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "fight-row " + fight.result;
  const line = document.createElement("span");
  line.className = "fight-row-line";
  line.textContent = fight.emoji + " " + fight.caption;
  const note = document.createElement("span");
  note.className = "fight-row-note";
  const raid = fight.kind === "raid";
  note.textContent = raid
    ? fight.boss_emoji + " " + fight.boss + ", " + fight.boss_level +
      " ур. · волн " + fight.waves + " · урона " + fight.damage
    : fight.mode.emoji + " " + fight.mode.title + ", раундов " + fight.rounds;
  row.appendChild(line);
  row.appendChild(note);
  // У рейда разбора по ходам нет: показываем, чем он кончился и что унесли
  row.addEventListener("click", () =>
    raid ? renderRaidRow(fight) : openFightLog(fight.id)
  );
  return row;
}

function renderRaidRow(fight) {
  const body = el("stats-body");
  body.textContent = "";

  const back = document.createElement("button");
  back.type = "button";
  back.className = "btn secondary wide";
  back.textContent = "← К списку боёв";
  back.addEventListener("click", () => renderHistory(statsData));
  body.appendChild(back);

  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent = fight.emoji + " " + fight.caption;
  body.appendChild(head);

  const rows = document.createElement("div");
  rows.className = "boss-rows";
  const add = (label, value) => {
    const row = document.createElement("p");
    row.className = "boss-row";
    const name = document.createElement("span");
    name.textContent = label;
    const val = document.createElement("b");
    val.textContent = value;
    row.appendChild(name);
    row.appendChild(val);
    rows.appendChild(row);
  };
  add("Чем кончилось", fight.verdict);
  add("Босс", fight.boss + ", " + fight.boss_level + " ур.");
  add("Волн", String(fight.waves));
  add("Нанесено урона", String(fight.damage));
  add("Вышел из подвала", fight.alive ? "да" : "нет");
  if (fight.allies) add("Ходили вместе", fight.allies);
  if (fight.prize) add("Приз", fight.prize);
  body.appendChild(rows);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = "Разбор по ходам в рейде не ведётся — только итог.";
  body.appendChild(note);
}

async function openFightLog(fightId) {
  try {
    const response = await fetch("api/fight/" + fightId, {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Бой не открылся.");
    renderFightLog(body);
  } catch (error) {
    popup("Бой", error.message);
  }
}

function renderFightLog(data) {
  const body = el("stats-body");
  body.textContent = "";

  const back = document.createElement("button");
  back.type = "button";
  back.className = "btn secondary wide";
  back.textContent = "← К списку боёв";
  back.addEventListener("click", () => renderHistory(statsData));
  body.appendChild(back);

  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent =
    data.fight.emoji + " " + data.fight.caption + ", " + data.fight.mode.title;
  body.appendChild(head);

  if (!data.has_log) {
    const empty = document.createElement("p");
    empty.className = "screen-note";
    empty.textContent = "Этот бой шёл до того, как клуб начал вести разбор.";
    body.appendChild(empty);
    return;
  }
  body.appendChild(turnList(data.turns));
}

function turnList(turns) {
  // Разбор старого боя — теми же словами, какими судья говорил тогда
  const box = document.createElement("div");
  box.className = "fight-log";
  turns.forEach((turn) => judgeLines(turn, box));
  return box;
}

function renderClub(data) {
  clubData = data;
  el("club-count").textContent = data.total
    ? data.total + " " + plural(data.total, "боец", "бойца", "бойцов")
    : "";
  el("club-note").textContent = data.total
    ? "Все, кто завёл бойца. Значок ℹ️ открывает его карточку."
    : "В клубе пока никого.";

  const list = el("club-list");
  list.textContent = "";
  data.fighters.forEach((fighter) => list.appendChild(fighterRow(fighter)));
}

function plural(count, one, few, many) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return many;
  const last = count % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

async function loadClub() {
  try {
    const response = await fetch("api/club", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Картотека не открылась.");
    renderClub(await response.json());
  } catch (error) {
    el("club-note").textContent = error.message;
  }
}

// ---------- касса ----------

function plusButton() {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "plus";
  btn.textContent = "+";
  btn.title = "Пополнить счёт";
  btn.setAttribute("aria-label", "Пополнить счёт");
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    showTopUp();
  });
  return btn;
}

function purse(credits) {
  const box = document.createElement("span");
  box.className = "purse";
  const amount = document.createElement("span");
  amount.textContent = num(credits) + " 💰";
  box.append(amount, plusButton());
  return box;
}

function packCard(pack) {
  const box = document.createElement("div");
  box.className = "pack";

  const head = document.createElement("div");
  head.className = "pack-head";
  const title = document.createElement("span");
  title.className = "pack-title";
  title.textContent = pack.emoji + " " + pack.title;
  head.appendChild(title);
  if (pack.profit > 0) {
    const badge = document.createElement("span");
    badge.className = "pack-profit";
    badge.textContent = "выгоднее на " + pack.profit + "%";
    head.appendChild(badge);
  }

  const amount = document.createElement("div");
  amount.className = "pack-amount";
  amount.textContent = num(pack.total) + " 💰";
  if (pack.bonus) {
    const bonus = document.createElement("span");
    bonus.className = "pack-bonus";
    bonus.textContent = " " + num(pack.credits) + " + " + num(pack.bonus) + " сверху";
    amount.appendChild(bonus);
  }

  box.append(head, amount);
  if (pack.note) {
    const note = document.createElement("div");
    note.className = "pack-note";
    note.textContent = pack.note;
    box.appendChild(note);
  }
  box.appendChild(
    button(pack.stars + " ⭐ — купить", { onClick: () => buyPack(pack) })
  );
  return box;
}

function renderTopUp(data) {
  el("topup-purse").textContent = "";
  el("topup-purse").appendChild(document.createTextNode(num(data.credits) + " 💰"));
  el("topup-note").textContent = data.open
    ? "Кредиты падают на счёт сразу после оплаты."
    : "Касса закрыта: оплату принимает бот командой /topup.";

  const list = el("topup-list");
  list.textContent = "";
  data.packs.forEach((pack) => list.appendChild(packCard(pack)));
}

async function loadTopUp() {
  try {
    const response = await fetch("api/topup", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Касса не открылась.");
    renderTopUp(await response.json());
  } catch (error) {
    el("topup-note").textContent = error.message;
  }
}

async function buyPack(pack) {
  if (busy) return;
  if (!tg || !tg.openInvoice) {
    popup("Касса", "Открой кассу в самом боте: команда /topup.");
    return;
  }
  busy = true;
  try {
    const data = await post("api/invoice", { code: pack.code });
    tg.openInvoice(data.link, async (status) => {
      if (status === "paid") {
        haptic((feedback) => feedback.notificationOccurred("success"));
        shopData = null;  // кредитов стало больше
        await refresh();
        await loadTopUp();
        popup(
          "✅ " + pack.emoji + " " + pack.title,
          "На счёт упало " + num(pack.total) + " 💰. За вещами — в лавку."
        );
      } else if (status === "failed") {
        popup("Не прошло", "Оплата не прошла. Звёзды остались у тебя.");
      }
    });
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

async function refresh() {
  try {
    const response = await fetch("api/card", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (response.ok) render(await response.json(), true);
  } catch (error) {
    console.error("card refresh failed", error);
  }
}

// ---------- карточка не отстаёт от боя ----------
//
// Уровень берут в бою — в ветке группы или на ринге, — а карточка грузится
// один раз за сеанс. Поэтому перечитываем её сами: когда вернулись в
// приложение, когда открыли «Персонажа» или рюкзак, и понемногу, пока
// приложение открыто. Иначе новый уровень и свободные очки видно только
// после того, как мини-апп закроют и откроют заново.
const CARD_HEARTBEAT = 20000;

function cardIsBusy() {
  // Человек раскладывает очки — перечитать карточку значит стереть то,
  // что он уже нащёлкал. Дождёмся, пока применит или откажется.
  return draftTotal() > 0;
}

async function catchUp() {
  if (document.hidden || cardIsBusy()) return;
  await refresh();
  // Здоровье затягивается само, и прайс больницы с ним стареет: пока
  // боец читает, ему уже нужно долить меньше. Полоса тикает сама, а
  // числа в прайсе приходят с сервера — обновляем их тем же ударом
  if (lastTab === "hospital") await loadHospital();
}

async function loadShop() {
  try {
    const response = await fetch("api/shop", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Лавка закрыта.");
    renderShop(await response.json());
  } catch (error) {
    el("shop-note").textContent = error.message;
  }
}

async function purchase(item) {
  if (busy) return;
  const ok = await askConfirm(
    "🛍 Покупка",
    "Вы приобретаете предмет " + item.title + " за " + item.price + " кредитов"
  );
  if (!ok) return;
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/buy", { code: item.code });
    render(data.card, true);
    renderShop(data.shop);
    haptic((feedback) => feedback.notificationOccurred("success"));
    popup("🛍 " + data.bought.title, boughtNote(data.bought));
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

async function handIn(item) {
  if (busy) return;
  const ok = await askConfirm(
    "🏪 Сдать в лавку",
    "Вы сдаете " + item.title + " в Лавку клуба и получите за это " +
      item.buyback + " кредитов"
  );
  if (!ok) return;
  busy = true;
  try {
    const data = await post("api/handin", { item_id: item.id });
    render(data.card, true);
    renderShop(data.shop);
    haptic((feedback) => feedback.notificationOccurred("success"));
    popup(
      "🏪 " + data.handin.title,
      "Сдано в лавку за " + data.handin.paid + " 💰. На счету " +
        data.handin.credits + " 💰."
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

function boughtNote(bought) {
  const paid = "Куплено за " + bought.price + " 💰. ";
  if (bought.consumable) {
    return paid + "Склянка ждёт в инвентаре — там же её и выпить.";
  }
  return bought.can_equip
    ? paid + "Вещь ждёт в инвентаре — надеть можно на вкладке «Боец»."
    : paid + "Надеть пока нечем: требования не выполнены — вещь полежит "
      + "в инвентаре.";
}

function runningBoost() {
  // Временный эликсир на бойце один: возвращаем его, если он есть
  return effects.find((effect) => effect.boost) || null;
}

async function usePotion(potion) {
  if (busy) return;

  // Другой временный вытеснит нынешний, и человек должен узнать об этом
  // до глотка, а не после: склянка тратится в любом случае.
  const running = potion.boost ? runningBoost() : null;
  if (running && running.code !== potion.code) {
    const ok = await confirmAction(
      "Сейчас действует «" + running.title + "»."
        + "\nЕсли выпьешь «" + potion.title + "», прежний эффект закончится."
        + "\nПродолжить?"
    );
    if (!ok) return;
  }

  busy = true;
  try {
    const data = await post("api/use", { code: potion.code });
    render(data.card, true);
    shopData = null;  // «уже есть» на витрине изменилось
    haptic((feedback) => feedback.notificationOccurred("success"));
    const used = data.used;
    const left = used.left
      ? "\nОсталось таких: " + used.left + " шт."
      : "\nЭто была последняя.";
    const gone = (used.replaced || []).length
      ? "\nЗакончилось действие: " + used.replaced.join(", ") + "."
      : "";
    popup(
      potion.icon + " " + used.title,
      (used.healed
        ? "Здоровья прибавилось на " + used.healed + "."
        : (used.extended ? "Эффект продлён — держится " : "Эффект пошёл — держится ")
          + spell(used.seconds_left) + ".") + gone + left
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// ---------- действия ----------

let busy = false;
let shopData = null;

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": (tg && tg.initData) || "",
    },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Судья не разрешил.");
  return data;
}

async function act(url, body) {
  if (busy) return;
  busy = true;
  try {
    const card = await post(url, body);
    render(card, true);
    shopData = null;  // «уже есть» на витрине могло измениться
    haptic((feedback) => feedback.impactOccurred("light"));
    // Вещи держатся друг за друга: сняли меч — ушёл и нож, который стоял
    // на его прибавке. Молчать об этом нельзя, слот пустеет сам собой.
    if (card.undressed && card.undressed.length) {
      popup(
        "👕 Слетело с бойца",
        "Требования больше не выполнены, ушло в рюкзак:\n"
          + card.undressed.join("\n")
      );
    }
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// ---------- мастерская ----------
//
// Три вкладки одной локации: починка, прилавок модификаторов и мастер.
// Мастер — два слота: вещь и модификатор. Пока в слотах не оба, кнопка
// молчит; при нажатии слоты сходятся, вспыхивает модификация, и на вещи
// остаётся звёздочка своей ступени.

let workshopData = null;
let workshopTab = "repair";
let masterPick = { item: null, mod: null };

const WORKSHOP_TABS = [
  ["repair", "🔧 Ремонт"],
  ["shop", "🛒 Модификаторы"],
  ["master", "✨ Мастер"],
];

// ---------- больница ----------
//
// Здоровье затягивается само, и это бесплатно: десять минут с нуля до
// полного. Больница продаёт не здоровье, а время — тому, кого только
// что избили, до ринга ещё восемь минут, и он либо ждёт, либо платит.
//
// Поэтому на экране сначала полоса здоровья, а уже под ней прайс. И
// цена стоит на кнопке всегда, даже когда кредитов не хватает: «у вас
// недостаточно» вместо цены не говорит, сколько нужно накопить.
let hospitalData = null;

async function loadHospital() {
  try {
    const response = await fetch("api/hospital", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Приём закончен.");
    renderHospital(await response.json());
  } catch (error) {
    popup("Больница", error.message);
  }
}

function renderHospital(data) {
  hospitalData = data;
  el("shop-purse-hospital").textContent = "";
  el("shop-purse-hospital").appendChild(purse(data.credits));

  const whole = data.hp.missing <= 0 && !(data.injury && data.injury.code);
  el("hospital-note").textContent = whole
    ? "Врач осматривает тебя и разводит руками: лечить нечего."
    : "Здоровье затягивается и само — больница просто не даёт ждать.";

  const list = el("hospital-list");
  list.textContent = "";
  list.appendChild(walletBar(data.purse, loadHospital));
  // Травма лечится отдельно от здоровья: другая беда, другая цена — и
  // стоит она первой, потому что с ней в клуб не пускают вовсе
  if (data.injury && data.injury.code) list.appendChild(injuryCure(data.injury));
  // Дешёвое лечение, которого хватает целиком, делает дорогое бессмысленным
  const enough = data.cures.filter((cure) => cure.healed >= data.hp.missing);
  const cheapest = enough.length
    ? Math.min(...enough.map((cure) => cure.price))
    : 0;
  data.cures.forEach((cure) => list.appendChild(cureCard(cure, cheapest)));
}

function injuryCure(hurt) {
  const box = document.createElement("div");
  box.className = "cure hurt";

  const title = document.createElement("div");
  title.className = "cure-title";
  title.textContent = "🤕 " + hurt.title;
  box.appendChild(title);

  const note = document.createElement("div");
  note.className = "cure-note";
  note.textContent = hurt.text;
  box.appendChild(note);

  const gain = document.createElement("div");
  gain.className = "cure-gain";
  gain.textContent = hurt.crippled
    ? "После лечения — " + hurt.cure_minutes + " мин, и снова на ринг"
    : "После лечения — " + hurt.cure_minutes + " мин, и как новый";
  box.appendChild(gain);

  // По полису лечение дешевле впятеро, и сказать об этом надо здесь:
  // одно дешёвое число выглядело бы просто дешёвым лечением, а не
  // работой документа, за который заплачено
  if (hurt.insured) {
    const saved = document.createElement("div");
    saved.className = "cure-insured";
    saved.textContent =
      "📄 По полису: −" + hurt.discount + "%, вместо " + num(hurt.full_price)
      + " 💰. Экономия " + num(hurt.saved) + " 💰.";
    box.appendChild(saved);
  }

  box.appendChild(
    button("Лечить · " + num(hurt.price) + " 💰", {
      disabled: !hurt.affordable,
      onClick: () => takeInjuryCure(hurt),
    })
  );
  return box;
}

async function takeInjuryCure(hurt) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/injury", {});
    render(data.card, true);
    renderHospital(data.hospital);
    popup(
      "🤕 " + data.done.title,
      "Вылечили. Отлежаться — ещё " + data.done.minutes + " мин."
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

function cureCard(cure, cheapest) {
  const box = document.createElement("div");
  box.className = "cure";

  const title = document.createElement("div");
  title.className = "cure-title";
  title.textContent = cure.title;
  box.appendChild(title);

  const note = document.createElement("div");
  note.className = "cure-note";
  note.textContent = cure.note;
  box.appendChild(note);

  // Сколько дольют именно этому бойцу: у полного выздоровления число
  // своё на каждый раз, да и сотня перевязки упирается в потолок
  const gain = document.createElement("div");
  gain.className = "cure-gain";
  gain.textContent = cure.useful
    ? "❤️ Дольют " + num(cure.healed)
    : "❤️ Доливать нечего";
  box.appendChild(gain);

  // Переплату называем вслух: за то же самое рядом просят меньше
  if (cure.useful && cheapest && cure.price > cheapest) {
    const cheaper = document.createElement("div");
    cheaper.className = "cure-cheaper";
    cheaper.textContent = "Столько же дольют за " + num(cheapest) + " 💰";
    box.appendChild(cheaper);
  }

  // Цена на кнопке стоит всегда, даже когда её нечем заплатить: «не
  // хватает кредитов» вместо числа не говорит, сколько копить. Почему
  // кнопка серая, видно тут же — по счёту сверху и по строке «дольют»
  box.appendChild(
    button("Лечиться · " + num(cure.price) + " 💰", {
      disabled: !cure.useful || !cure.affordable,
      onClick: () => takeCure(cure),
    })
  );
  return box;
}

async function takeCure(cure) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/heal", { cure: cure.code });
    render(data.card, true);
    renderHospital(data.hospital);
    popup(
      "🏥 " + data.done.title,
      "Здоровья прибавилось на " + num(data.done.healed) +
        ". Списано " + num(data.done.price) + " 💰."
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

async function loadWorkshop() {
  try {
    const response = await fetch("api/workshop", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Мастерская закрыта.");
    renderWorkshop(await response.json());
  } catch (error) {
    popup("Мастерская", error.message);
  }
}

function openWorkshop() {
  pickWorkshopTab("repair");
  showTab("workshop");
}

function pickWorkshopTab(name) {
  workshopTab = name;
  WORKSHOP_TABS.forEach(([code]) => {
    el("workshop-" + code).classList.toggle("hidden", code !== name);
  });
  if (workshopData) renderWorkshop(workshopData);
}

function renderWorkshop(data) {
  workshopData = data;
  el("shop-purse-workshop").textContent = "";
  el("shop-purse-workshop").appendChild(purse(data.credits));

  const tabs = el("workshop-tabs");
  tabs.textContent = "";
  WORKSHOP_TABS.forEach(([code, label]) => {
    tabs.appendChild(chip(label, workshopTab === code, () => pickWorkshopTab(code)));
  });

  renderRepairTab(data);
  renderModsTab(data);
  renderMasterTab(data);
}

function renderRepairTab(data) {
  el("repair-note").textContent = data.repair.length
    ? "Чинят только снятое: сначала снимите вещь в инвентаре, потом несите сюда."
    : "Чинить нечего: всё снятое целое, а надетое сюда не берут.";
  const list = el("repair-list");
  list.textContent = "";
  list.appendChild(walletBar(data.purse, loadWorkshop));
  // На вкладке лежит только побитое, и делают тут одно — чинят. Поэтому
  // на карточке одна кнопка: не хватает на всю починку — чиним на сколько
  // хватает, и это написано прямо на ней
  // Считаем по тому кошельку, которым здесь платят: у бойца с картой
  // деньги лежат на счету, и наличные о них ничего не знают
  const money = spendable(data);
  data.repair.forEach((item) => {
    const card = thingCard(item, money, false, true);
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    const affordable = Math.min(item.wear, money);
    const full = item.repair_price <= money;
    buttons.appendChild(
      button(
        full
          ? "Чинить · " + item.repair_price + " 💰"
          : affordable > 0
            ? "Чинить на " + affordable + " 💰"
            : "Чинить · " + item.repair_price + " 💰",
        {
          secondary: true,
          disabled: affordable <= 0,
          onClick: () => repair(item, full ? null : affordable),
        }
      )
    );
    card.querySelector(".thing-body").appendChild(buttons);
    list.appendChild(card);
  });
}

function modCard(mod, buyable) {
  const box = document.createElement("div");
  box.className = "mod lvl" + mod.level;

  // Картинка модификатора. Точки ступени здесь нет намеренно: она
  // принадлежит вещи, а не заточке. Ступень на прилавке видно по
  // названию, полосе и цветной кромке карточки
  const pic = document.createElement("div");
  pic.className = "mod-pic";
  pic.appendChild(slotPicture(mod, mod.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "mod-body";

  const title = document.createElement("div");
  title.className = "mod-title";
  title.textContent = mod.icon + " " + mod.title;
  body.appendChild(title);

  const gain = document.createElement("div");
  gain.className = "mod-gain";
  gain.textContent = mod.gain;
  body.appendChild(gain);

  if (mod.owned) {
    const have = document.createElement("div");
    have.className = "mod-have";
    have.textContent = "🎒 В рюкзаке: " + mod.owned + " шт.";
    body.appendChild(have);
  }

  if (buyable) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    // Цена стоит на кнопке всегда, даже когда денег не хватает: на
    // прилавке смотрят, сколько это стоит, а не сколько у тебя в
    // кошельке. Что кредитов мало, видно по серой кнопке — и по счёту
    // в шапке, который стоит рядом
    buttons.appendChild(
      button("Купить · " + mod.price + " 💰", {
        disabled: !mod.can_afford,
        onClick: () => buyMod(mod),
      })
    );
    body.appendChild(buttons);
  }
  box.appendChild(body);
  return box;
}

function renderModsTab(data) {
  el("mods-note").textContent =
    "Заточка добавляет оружию урон, щиту броню, модификатор — одну "
    + "характеристику. Сколько именно, решает бросок у мастера.";
  const list = el("mods-list");
  list.textContent = "";
  list.appendChild(walletBar(data.purse, loadWorkshop));
  data.shop.forEach((section) => {
    const head = document.createElement("h2");
    head.className = "shelf-head";
    head.textContent = section.icon + " " + section.title;
    list.appendChild(head);
    const rows = document.createElement("div");
    rows.className = "mod-list";
    section.items.forEach((mod) => rows.appendChild(modCard(mod, true)));
    list.appendChild(rows);
  });
}

// Слот мастера: пустой ждёт выбора, полный показывает, что в него положили
function fillSlot(box, chosen, empty) {
  box.textContent = "";
  box.classList.toggle("full", Boolean(chosen));
  if (!chosen) {
    const hint = document.createElement("span");
    hint.className = "master-empty";
    hint.textContent = empty;
    box.appendChild(hint);
    return;
  }
  const pic = document.createElement("div");
  pic.className = "master-pic";
  pic.appendChild(slotPicture(chosen, chosen.icon || chosen.star));
  box.appendChild(pic);
  const title = document.createElement("span");
  title.className = "master-title";
  title.textContent = chosen.title;
  box.appendChild(title);
}

function renderMasterTab(data) {
  const item = masterPick.item
    ? data.targets.find((row) => row.id === masterPick.item.id)
    : null;
  const mod = masterPick.mod
    ? data.mods.find((row) => row.code === masterPick.mod.code)
    : null;
  masterPick = { item: item || null, mod: mod || null };

  el("master-note").textContent = data.targets.length
    ? "Модифицировать вещь можно один раз — второй звёздочки не будет."
    : "Модифицировать нечего: на всех вещах уже стоят звёздочки.";
  fillSlot(el("master-item"), masterPick.item, "Вещь");
  fillSlot(el("master-mod"), masterPick.mod, "Модификатор");
  el("master-go").disabled = !(masterPick.item && masterPick.mod);
  renderMasterPicker(data);
}

let masterPicking = "";

function renderMasterPicker(data) {
  const box = el("master-picker");
  box.textContent = "";
  if (!masterPicking) return;

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent =
    masterPicking === "item" ? "Что модифицируем" : "Чем модифицируем";
  box.appendChild(head);

  if (masterPicking === "item") {
    // Если модификатор уже выбран, показываем только то, на что он ложится
    const fit = masterPick.mod ? masterPick.mod.kind : null;
    const rows = data.targets.filter((row) => !fit || row.mod_kind === fit);
    if (!rows.length) {
      box.appendChild(emptyLine("Подходящих вещей нет."));
      return;
    }
    const list = document.createElement("div");
    list.className = "bag-list";
    rows.forEach((row) => {
      const card = thingCard(row, data.credits, false, true);
      card.classList.add("pickable");
      card.addEventListener("click", () => {
        masterPick.item = row;
        masterPicking = "";
        renderWorkshop(data);
      });
      list.appendChild(card);
    });
    box.appendChild(list);
    return;
  }

  const fit = masterPick.item ? masterPick.item.mod_kind : null;
  const rows = data.mods.filter((row) => !fit || row.kind === fit);
  if (!rows.length) {
    box.appendChild(
      emptyLine(
        data.mods.length
          ? "Под эту вещь в рюкзаке модификаторов нет."
          : "Модификаторов в рюкзаке нет — их покупают на соседней вкладке."
      )
    );
    return;
  }
  const list = document.createElement("div");
  list.className = "mod-list";
  rows.forEach((row) => {
    const card = modCard(row, false);
    card.classList.add("pickable");
    card.addEventListener("click", () => {
      masterPick.mod = row;
      masterPicking = "";
      renderWorkshop(data);
    });
    list.appendChild(card);
  });
  box.appendChild(list);
}

function emptyLine(text) {
  const line = document.createElement("p");
  line.className = "screen-note";
  line.textContent = text;
  return line;
}

async function buyMod(mod) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/mod", { action: "buy", code: mod.code });
    render(data.card, true);
    renderWorkshop(data.workshop);
    popup(mod.title, "Куплено за " + data.done.price + " 💰. Модификатор в рюкзаке.");
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// Вспышка модификации: слоты сходятся, гремит взрыв, и только потом
// показываем, что выпало. Запрос идёт параллельно — ждём оба
function playBurst() {
  const stage = document.querySelector(".master");
  if (!stage) return Promise.resolve();
  stage.classList.add("going");
  return new Promise((done) => {
    setTimeout(() => {
      stage.classList.add("boom");
      setTimeout(() => {
        stage.classList.remove("going", "boom");
        done();
      }, 520);
    }, 420);
  });
}

async function runMaster() {
  if (busy || !(masterPick.item && masterPick.mod)) return;
  busy = true;
  const item = masterPick.item;
  const mod = masterPick.mod;
  try {
    const [data] = await Promise.all([
      post("api/mod", { action: "apply", item_id: item.id, code: mod.code }),
      playBurst(),
    ]);
    masterPick = { item: null, mod: null };
    render(data.card, true);
    renderWorkshop(data.workshop);
    const done = data.done;
    popup(
      done.star + " " + done.title,
      mod.title + ": " + done.gain + ".\nЗвёздочка осталась на вещи навсегда."
    );
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

async function repair(item, points) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/repair", { item_id: item.id, points: points });
    render(data.card, true);
    // Список ремонта приходит вместе с ответом: починенная вещь исчезает
    // из него тут же, а не ждёт, пока дверь откроют заново
    if (data.workshop) renderWorkshop(data.workshop);
    shopData = null;  // кредитов стало меньше
    const done = data.repair;
    let text = "Снято износа: " + done.points + ", списано " + done.price + " 💰.";
    if (done.destroyed) {
      text = "Чинить было уже нечего: «" + item.title + "» рассыпалась в труху.";
    } else if (done.degraded) {
      text += "\nЗапас прочности просел на пункт — вещь стареет.";
    }
    popup(item.title, text);
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

// ---------- образ бойца ----------

function closeSheet() {
  el("sheet").classList.add("hidden");
  // Окно одно, и состав семьи — один из его жильцов: закрыли окно —
  // перерисовывать туда больше нечего
  clanSheetOpen = false;
}

// `middle` — показать не листом снизу, а окном посередине экрана. Лист
// хорош для длинного списка: гардероб и чужая карточка листаются, и
// начинать их от нижнего края правильно. Вещь же — это одна карточка и
// две кнопки, и ради них лист уезжал бы на пол-экрана
function openSheet(title, note, middle) {
  el("sheet-title").textContent = title;
  el("sheet-note").textContent = note || "";
  el("sheet-list").textContent = "";
  el("sheet").classList.toggle("middle", Boolean(middle));
  el("sheet").classList.remove("hidden");
}

function lookTile(look, onPick) {
  const box = document.createElement("button");
  box.type = "button";
  box.className =
    "look" + (look.current ? " current" : "") + (look.owned ? "" : " locked");

  const pic = document.createElement("div");
  pic.className = "look-pic";
  if (look.image) {
    const img = document.createElement("img");
    img.src = look.image;
    img.alt = look.title;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => {
      img.replaceWith(document.createTextNode(look.emoji));
    });
    pic.appendChild(img);
  } else {
    pic.textContent = look.emoji;
  }

  const title = document.createElement("div");
  title.className = "look-title";
  title.textContent = look.title;

  const tag = document.createElement("div");
  tag.className = "look-tag";
  if (look.current) {
    tag.textContent = "надет";
    tag.classList.add("on");
  } else if (look.owned) {
    tag.textContent = look.price ? "куплен" : "доступен";
  } else {
    tag.textContent = num(look.price) + " 💰";
    if (!look.affordable) tag.classList.add("poor");
  }

  box.append(pic, title, tag);
  // Нажатие приходит снаружи: в карточке плитка надевает образ, на полке
  // дома — покупает. Плитка от этого не меняется, и рисовать её дважды
  // было бы незачем
  box.addEventListener("click", () => (onPick || pickLook)(look));
  return box;
}

// Гардероб приходит уже своего пола: делить его на «мужские» и «женские»
// нечего, и заголовок над единственной группой только занимал строку
function renderLooks(looks) {
  const list = el("sheet-list");
  list.textContent = "";
  const grid = document.createElement("div");
  grid.className = "look-grid";
  looks.forEach((look) => grid.appendChild(lookTile(look)));
  list.appendChild(grid);
}

async function openLooks() {
  openSheet("Образ бойца", "Загружаю гардероб…");
  try {
    const response = await fetch("api/looks", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Гардероб не открылся.");
    const data = await response.json();
    el("sheet-note").textContent =
      "Здесь только свои образы, и смена между ними бесплатна. Новые "
      + "берут в барбершопе и в салоне красоты. На бой образ не влияет.";
    renderLooks(data.looks);
  } catch (error) {
    el("sheet-note").textContent = error.message;
  }
}

async function pickLook(look) {
  if (busy || look.current) return;
  busy = true;
  try {
    const data = await post("api/look", { code: look.code });
    render(data.card, true);
    renderLooks(data.looks);
    haptic((feedback) => feedback.notificationOccurred("success"));
  } catch (error) {
    popup("Не вышло", error.message);
  } finally {
    busy = false;
  }
}

function paintAvatar(box, card, clickable) {
  box.textContent = "";
  box.classList.toggle("clickable", clickable);
  const emoji = () => {
    const span = document.createElement("div");
    span.className = "emoji";
    span.textContent = card.avatar.emoji || "🥊";
    return span;
  };
  if (!card.avatar.url) {
    box.appendChild(emoji());
  } else {
    const img = document.createElement("img");
    img.src = card.avatar.url;
    img.alt = card.name;
    img.addEventListener("error", () => {
      // Образ не доехал — возвращаем значок, но машину из-за бойца не
      // убираем: она тут не при чём
      const car = box.querySelector(".avatar-car");
      box.textContent = "";
      box.appendChild(emoji());
      if (car) box.appendChild(car);
    });
    box.appendChild(img);
  }
  paintAvatarCar(box, card);
}

/** Машина за бойцом: полосой по верхнему краю рамки. Пешком — ничего.
 *
 * Слой второй из трёх: над фоном рамки, под самой фигурой. Порядок
 * держит css (`z-index`), а не порядок узлов, — поэтому машину можно
 * дописывать последней, не перекладывая образ.
 */
function paintAvatarCar(box, card) {
  box.classList.remove("has-car");
  if (!card.car || !card.car.image) return;
  const shot = document.createElement("img");
  shot.className = "avatar-car";
  // Вырезанная версия: у рамки свой фон, и студийный фон машины лёг бы
  // на него заплаткой. Нет вырезанной — берём обычную, с фоном: машина
  // с заплаткой лучше, чем её отсутствие
  shot.src = card.car.cutout || card.car.image;
  shot.alt = card.car.title;
  shot.title = card.car.title + " · дорога " + card.car.times_text;
  // Не доехала вырезанная — пробуем обычную, и только потом сдаёмся.
  // Совсем нет картинки — просто нет машины: подписью это не заменяют,
  // иначе за бойцом висела бы строка вместо машины
  shot.addEventListener("error", () => {
    if (card.car.cutout && shot.src !== card.car.image) {
      shot.src = card.car.image;
      return;
    }
    shot.remove();
    box.classList.remove("has-car");
  });
  box.classList.add("has-car");
  box.appendChild(shot);
}

function renderAvatar(card) {
  // Образ меняют на карточке персонажа, в инвентаре рамка просто показывает
  paintAvatar(el("hero-avatar"), card, Boolean(card.is_self));
  paintAvatar(el("avatar"), card, false);
}

// Здоровье тикает на клиенте: сервер отдаёт срез, дальше считаем сами
let health = null;
let ticker = null;

function paintOneBar(prefix) {
  const bar = el(prefix + "hp");
  if (!bar) return;
  const percent = health.max ? Math.min(100, (health.current / health.max) * 100) : 0;
  bar.classList.remove("green", "yellow", "red");
  bar.classList.add(percent < 20 ? "red" : percent < 80 ? "yellow" : "green");
  el(prefix + "hp-fill").style.width = percent.toFixed(1) + "%";
  el(prefix + "hp-text").textContent =
    num(Math.floor(health.current)) + " / " + num(health.max);

  const note = el(prefix + "hp-note");
  if (percent >= 80) {
    note.textContent =
      health.current >= health.max
        ? "🟢 Полный порядок — можно на ринг"
        : "🟢 Можно на ринг, здоровье ещё затягивается";
  } else {
    const left = Math.max(0, Math.ceil((health.max * 0.8 - health.current) / health.rate));
    const minutes = Math.floor(left / 60);
    const seconds = left % 60;
    const time = minutes ? `${minutes} мин ${seconds} сек` : `${seconds} сек`;
    note.textContent = `${percent < 20 ? "🔴" : "🟡"} Драться можно с 80% — через ${time}`;
  }
}

function paintHealth() {
  // Полоска стоит в инвентаре, на карточке персонажа и в больнице:
  // тикают все три. В больнице она главная — за ней туда и приходят
  if (!health) return;
  paintOneBar("");
  paintOneBar("hero-");
  paintOneBar("hospital-");
}

function startHealthTicker(hp) {
  health = {
    current: hp.current,
    max: hp.max,
    rate: hp.max / (hp.regen_seconds || 600),
  };
  paintHealth();
  if (ticker) clearInterval(ticker);
  // Один таймер на всё, что тикает: здоровье затягивается, эффекты догорают
  ticker = setInterval(() => {
    paintEffects();
    if (health.current >= health.max) return;
    health.current = Math.min(health.max, health.current + health.rate);
    paintHealth();
  }, 1000);
}

function renderHead(prefix, card) {
  el(prefix + "class").textContent = card.fclass.emoji;
  // Значок подписки идёт с именем везде, где боец назван по имени
  el(prefix + "name").textContent =
    card.pro && card.pro.active ? card.name + " " + card.pro.badge : card.name;
  el(prefix + "level").textContent = "[" + card.level + "]";
}

// Последняя карточка: по ней рисуется кнопка входа на работу в шапке
// того дома, где боец стоит. Карточка перерисовывается часто, и держать
// её здесь дешевле, чем спрашивать сервер на каждом переключении экрана
let cardData = null;

function render(card, keepTab) {
  cardData = card;
  renderHead("bag-", card);
  renderHead("hero-", card);
  document.title = card.name + " [" + card.level + "]";

  startHealthTicker(card.hp);
  startEffects(card.effects, card.injury);
  renderAvatar(card);
  renderSlots(el("slots-left"), card.slots.left, card.is_self);
  renderSlots(el("slots-right"), card.slots.right, card.is_self);
  // Кукла персонажа — для показа: нажатие рассказывает о вещи, а снимают
  // её в инвентаре
  renderSlots(el("hero-slots-left"), card.slots.left, card.is_self, true);
  renderSlots(el("hero-slots-right"), card.slots.right, card.is_self, true);
  renderSkills(card);
  renderClanMarks(card);
  renderDaily(card);
  renderPapers(card);
  // Карточка приезжает после каждого действия: состояние выборов в ней
  // свежее того, что держит плашка в углу
  if (card.is_self && card.ballot) setBallot(card.ballot);
  renderBag(card);
  // Рюкзак поменялся — значит поменялось и то, что можно выставить на
  // комиссию. Без этого экран комиссионки остаётся с прежним списком: он
  // грузится один раз, и надетая или проданная вещь висит в нём как живая.
  marketFollowsBag();
  paintCity(card);

  const stats = el("stats");
  stats.textContent = "";
  card.stats.forEach((stat) => {
    stats.appendChild(row(stat.title, statValue(stat)));
  });

  // Раздача очков — только на своей карточке: чужие характеристики не наши
  cardStats = card.is_self ? card.stats : [];
  freePoints = card.is_self ? card.progress.free_points : 0;
  paintUpgrade();

  const progress = el("progress");
  progress.textContent = "";
  progress.appendChild(row("Опыт", num(card.progress.total_exp)));
  progress.appendChild(row("Уровень", num(card.level)));
  if (card.progress.capped) {
    progress.appendChild(row("До уровня", "потолок"));
  } else {
    progress.appendChild(
      row("До уровня", num(card.progress.exp) + " / " + num(card.progress.exp_needed))
    );
    progress.appendChild(
      row(
        "До улучшения",
        num(card.progress.exp_to_next_up) +
          " (" +
          card.progress.micro_ups +
          "/" +
          card.progress.ups_per_level +
          ")"
      )
    );
  }
  if (card.progress.free_points) {
    progress.appendChild(row("Свободных очков", num(card.progress.free_points), "good"));
  }

  const combat = el("combat");
  combat.textContent = "";
  const c = card.combat;
  // В строке урона — только своё: то, что боец выбивает руками. Оружие
  // стоит отдельными строками, по строке на руку, и там уже реальный
  // урон — тот, что долетит до соперника в руках этого класса.
  combat.appendChild(row("👊 Урон", c.damage_min + "–" + c.damage_max));
  c.weapon_damage.forEach((w) => {
    combat.appendChild(
      row(w.title || "Оружие", (w.icon || "") + w.min + "–" + w.max, "weapon")
    );
  });
  combat.appendChild(
    share(c, "crit_chance", "🩸 Крит", c.crit_chance + "% ×" + c.crit_power)
  );
  combat.appendChild(share(c, "anticrit", "🚫 Антикрит"));
  combat.appendChild(share(c, "dodge_chance", "🌀 Уворот"));
  combat.appendChild(share(c, "accuracy", "🎯 Точность"));
  combat.appendChild(share(c, "counter_chance", "🔄 Контрудар"));
  // Насколько крепко держится блок, когда в него упирается крит: то, что
  // не удержалось, проходит половиной максимального урона
  combat.appendChild(share(c, "block_hold", "🛡🩸 Держит блок"));
  combat.appendChild(row("🪨 Сопротивление", c.resist + "%"));
  combat.appendChild(row("🛡💥 Пробивание", c.penetration + "%"));
  // Броня — по строке на зону: в одну строку пять диапазонов не читаются
  card.armor
    .filter((zone) => zone.max > 0)
    .forEach((zone) => {
      combat.appendChild(row(zone.title, zone.min + "–" + zone.max));
    });

  const record = el("record");
  record.textContent = "";
  record.appendChild(row("Побед", num(card.record.wins)));
  record.appendChild(row("Поражений", num(card.record.losses)));
  record.appendChild(row("Ничьих", num(card.record.draws)));
  record.appendChild(row("Рейды", raidScore(card.record)));
  record.appendChild(row("Рейтинг", num(card.record.rating)));
  if (card.is_self) {
    record.appendChild(row("Кредиты", purse(card.record.credits)));
  }
  record.appendChild(row("Место рождения", card.birthplace));
  record.appendChild(row("День рождения персонажа", card.birthday));

  el("foot").textContent =
    card.fclass.emoji + " " + card.fclass.title + " — " + card.fclass.tagline;

  el("loader").classList.add("hidden");
  // Чужую карточку показываем одним экраном: ни панели, ни рюкзака
  el("bar").classList.toggle("hidden", !card.is_self);
  pickClubSection(clubSection);
  if (!keepTab) showTab("hero");
}

// Где боец стоит, по последней карточке: по этому и решается, чем
// показывать экран клуба и что на нём можно
let myPlace = null;

// ---------- тренажёрный зал ----------
//
// Экран собран сверху вниз по срочности: идущая тренировка, потом слот, к
// которому можно присоединиться, потом накопленное, и только затем
// расписание на неделю. Абонемента нет — всё остальное не показываем
// вовсе: без него в зал не пускают, и полоса прогресса под запертой
// дверью только дразнит.
//
// Часы тикают на странице, а очко засчитывает сервер. Страница лишь
// перестаёт ждать и спрашивает заново, когда пятнадцать минут вышли:
// решать, отработана ли тренировка, ей нельзя — она не видит, в зале ли
// боец на самом деле.

let gymData = null;
let gymBusy = false;
let gymTimer = null;

// Две вкладки: занятия и абонемент. Без абонемента вкладок нет вовсе —
// выбирать не из чего, и вторая была бы единственной
const GYM_TABS = [
  ["train", "Тренировки"],
  ["pass", "Абонемент"],
];
let gymTab = "train";

function pickGymTab(name) {
  gymTab = name;
  if (gymData) renderGym(gymData);
}

async function loadGym() {
  try {
    const response = await fetch("api/gym", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("gym-note").textContent = "Зал не открылся.";
      return;
    }
    renderGym(await response.json());
  } catch (error) {
    el("gym-note").textContent = error.message;
  }
}

async function gymAction(payload) {
  if (gymBusy) return;
  gymBusy = true;
  try {
    const response = await fetch("api/gym", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Зал", data.error || "Не получилось.");
      await loadGym();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderGym(data);
    if (data.said) popup("Зал", data.said);
  } catch (error) {
    popup("Зал", "Сервер не ответил.");
  } finally {
    gymBusy = false;
  }
}

function renderGym(data) {
  gymData = data;
  el("shop-purse-gym").textContent = "";
  el("shop-purse-gym").appendChild(purse(data.credits));
  el("gym-note").textContent = data.said || gymHint(data);

  const body = el("gym-body");
  body.textContent = "";
  if (!data.pass.active) {
    body.appendChild(walletBar(data.purse, loadGym));
    body.appendChild(passShelf(data));
    return;
  }

  const tabs = document.createElement("div");
  tabs.className = "bubbles";
  tabs.id = "gym-tabs";
  GYM_TABS.forEach(([code, label]) => {
    tabs.appendChild(chip(label, gymTab === code, () => pickGymTab(code)));
  });
  body.appendChild(tabs);

  if (gymTab === "pass") {
    body.appendChild(passTab(data));
    return;
  }
  body.appendChild(walletBar(data.purse, loadGym));
  if (data.visit && data.visit.stat) body.appendChild(visitBox(data.visit));
  else body.appendChild(slotBox(data));
  body.appendChild(dayLine(data.day));
  body.appendChild(progressBox(data));
  body.appendChild(scheduleBox(data.schedule));
  startGymClock();
}

/** Вкладка «Абонемент»: что есть, почём докупать и что ещё продают. */
function passTab(data) {
  const box = document.createElement("div");
  box.appendChild(walletBar(data.purse, loadGym));
  box.appendChild(passLine(data.pass));
  box.appendChild(dayPrices(data.day));
  box.appendChild(passShelf(data, "Продлить абонемент"));
  return box;
}

/** Прайс дня лесенкой: во что обойдётся день, если ходить до вечера. */
function dayPrices(day) {
  const box = document.createElement("section");
  box.className = "day-prices";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Занятия в день";
  box.appendChild(head);

  day.prices.forEach((row) => {
    const line = document.createElement("div");
    line.className = "day-price"
      + (row.number <= day.taken ? " spent" : "")
      + (row.number === day.taken + 1 ? " next" : "");
    const number = document.createElement("span");
    number.className = "day-price-number";
    number.textContent = row.number + "-е";
    const price = document.createElement("span");
    price.className = "day-price-value";
    price.textContent = row.free ? "по абонементу" : num(row.price) + " 💰";
    line.append(number, price);
    box.appendChild(line);
  });

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = day.full
    ? "На сегодня всё: " + day.limit + " занятий — это потолок суток."
    : "Сегодня занято " + day.taken + " из " + day.limit + ".";
  box.appendChild(note);
  return box;
}

/** Строка под кнопкой: сколько сегодня взято и почём следующее. */
function dayLine(day) {
  const box = document.createElement("p");
  box.className = "gym-day-line";
  box.textContent = day.full
    ? "Сегодня отработано " + day.taken + " из " + day.limit + " — на сегодня всё."
    : "Сегодня " + day.taken + " из " + day.limit + " · следующее "
      + (day.price ? num(day.price) + " 💰" : "по абонементу");
  return box;
}

function gymHint(data) {
  if (!data.pass.active) {
    return "Без абонемента на тренировки не пускают.";
  }
  if (data.visit && data.visit.stat) return "Идёт тренировка. Не уходи из зала.";
  if (data.day && data.day.full) {
    return "На сегодня всё: " + data.day.limit + " занятий — потолок суток.";
  }
  if (data.now && data.now.state === "open") return "Занятие идёт — можно вставать.";
  if (data.now && data.now.state === "late") {
    return "Занятие заканчивается: записываться поздно.";
  }
  if (data.now && data.now.state === "full") {
    return "В этом занятии мест нет. Приходи на следующее.";
  }
  if (data.now && data.now.state === "done") {
    return "В этом занятии ты уже отработал. Следующее — по расписанию.";
  }
  if (data.now && data.now.state === "training") {
    return "Ты стоишь на этом занятии.";
  }
  return "Сейчас занятий нет. Ближайшее — в расписании.";
}

// ---------- абонемент ----------

function passShelf(data, title) {
  const box = document.createElement("section");
  box.className = "tickets";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = title || "Абонемент";
  box.appendChild(head);
  data.pass.tickets.forEach((ticket) => box.appendChild(ticketCard(ticket)));
  return box;
}

function ticketCard(ticket) {
  const box = document.createElement("div");
  box.className = "ticket";

  const title = document.createElement("div");
  title.className = "ticket-title";
  title.textContent = ticket.title;
  const note = document.createElement("div");
  note.className = "ticket-note";
  note.textContent = ticket.note;
  // Цена дня — по ней и видно, что год выгоднее месяца
  const perDay = document.createElement("div");
  perDay.className = "ticket-day";
  perDay.textContent = ticket.per_day + " 💰 в день";
  box.append(title, note, perDay);

  box.appendChild(
    button(num(ticket.price) + " 💰", {
      // Цену с кнопки не снимаем, даже когда платить нечем: по ней и
      // видно, сколько не хватает
      disabled: !ticket.affordable,
      hint: "Не хватает кредитов: абонемент стоит " + num(ticket.price) + " 💰.",
      onClick: () => gymAction({ action: "pass", code: ticket.code }),
    })
  );
  return box;
}

function passLine(ticket) {
  const box = document.createElement("p");
  box.className = "gym-pass";
  box.textContent =
    "🎟 Абонемент до " + ticket.until + " — осталось "
    + ticket.days_left + " " + plural(ticket.days_left, "день", "дня", "дней");
  return box;
}

// ---------- идущая тренировка и ближайший слот ----------

function visitBox(visit) {
  const box = document.createElement("section");
  box.className = "gym-now training";

  const title = document.createElement("div");
  title.className = "gym-now-title";
  title.textContent = visit.emoji + " " + visit.title;
  const clock = document.createElement("div");
  clock.className = "gym-clock";
  clock.id = "gym-clock";
  box.append(title, clock);

  box.appendChild(
    button("Уйти с тренировки", {
      secondary: true,
      onClick: () => gymAction({ action: "leave" }),
    })
  );
  const note = document.createElement("p");
  note.className = "gym-now-note";
  note.textContent = "Уйдёшь из зала до конца — занятие не зачтётся.";
  box.appendChild(note);
  return box;
}

function slotBox(data) {
  const box = document.createElement("section");
  box.className = "gym-now";
  const slot = data.now && data.now.id ? data.now : data.next;
  if (!slot || !slot.id) return box;

  const title = document.createElement("div");
  title.className = "gym-now-title";
  title.textContent = slot.emoji + " " + slot.title;
  const when = document.createElement("div");
  when.className = "gym-now-when";
  when.textContent = slot.clock + " · " + slot.gains;
  box.append(title, when);

  // Кнопка живёт при идущем слоте, а не в строке табло: сорок две кнопки
  // на расписании означали бы сорок один отказ
  // Сколько мест занято — видно до того, как нажмёшь
  const seats = document.createElement("div");
  seats.className = "gym-now-seats";
  seats.textContent = "Мест занято: " + slot.taken + " из " + slot.limit;
  box.appendChild(seats);

  if (data.now && data.now.state === "open" && !data.day.full) {
    const price = data.day.price;
    box.appendChild(
      button(
        "Присоединиться · " + (price ? num(price) + " 💰" : "по абонементу"),
        {
          // Цену с кнопки не снимаем, даже когда платить нечем.
          // Хватает ли денег, решает сервер: кошелька два, и сравнение с
          // наличными запирало кнопку тому, у кого деньги на счету
          disabled: !data.day.affordable,
          hint: "Занятие сверх абонемента стоит " + num(price)
            + " 💰, а " + purseNote(data.purse) + ".",
          onClick: () => gymAction({ action: "join" }),
        }
      )
    );
  } else {
    const note = document.createElement("p");
    note.className = "gym-now-note";
    note.textContent = data.now && data.now.id
      ? gymHint(data)
      : "Ближайшее занятие — " + slot.clock + ".";
    box.appendChild(note);
  }
  return box;
}

/** Часы тренировки тикают на странице, а очко засчитывает сервер. */
function startGymClock() {
  stopGymClock();
  if (!gymData || !gymData.visit || !gymData.visit.stat) return;
  gymTimer = setInterval(tickGym, 1000);
  tickGym();
}

function stopGymClock() {
  if (gymTimer) clearInterval(gymTimer);
  gymTimer = null;
}

function tickGym() {
  const shown = el("gym-clock");
  if (!shown || !gymData || !gymData.visit) return;
  const left = Math.max(0, gymData.visit.until - Math.floor(Date.now() / 1000));
  if (!left) {
    // Время вышло — спрашиваем сервер: отработана ли тренировка, решает он
    stopGymClock();
    loadGym();
    return;
  }
  shown.textContent = "Осталось " + fullClock(left);
}

// ---------- прогресс ----------

function progressBox(data) {
  const box = document.createElement("section");
  box.className = "gym-progress";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Прогресс";
  box.appendChild(head);
  data.progress.forEach((row) => box.appendChild(progressRow(row)));

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Улучшения дорожают: " + data.steps.join(", ") + " тренировок. "
    + "Всего на пять — " + data.total + ".";
  box.appendChild(note);
  return box;
}

function progressRow(row) {
  const box = document.createElement("div");
  box.className = "grow" + (row.ready ? " ready" : "") + (row.maxed ? " maxed" : "");

  const head = document.createElement("div");
  head.className = "grow-head";
  const title = document.createElement("span");
  title.className = "grow-title";
  title.textContent = row.emoji + " " + row.title;
  const ups = document.createElement("span");
  ups.className = "grow-ups";
  ups.textContent = row.ups + " / " + row.max_ups;
  head.append(title, ups);
  box.appendChild(head);

  const bar = document.createElement("div");
  bar.className = "grow-bar";
  const fill = document.createElement("div");
  fill.className = "grow-fill";
  fill.style.width = row.percent + "%";
  bar.appendChild(fill);
  box.appendChild(bar);

  const said = document.createElement("div");
  said.className = "grow-note";
  said.textContent = row.maxed
    ? "Потолок зала: больше не растёт"
    : row.training_emoji + " " + row.training + " · " + row.points + " из "
      + row.price;
  box.appendChild(said);

  if (!row.maxed) {
    box.appendChild(
      button("Улучшить · +" + row.gain, {
        disabled: !row.ready,
        hint: "Не хватает тренировок: нужно ещё " + row.left + ".",
        onClick: () => gymAction({ action: "upgrade", stat: row.stat }),
      })
    );
  }
  return box;
}

// ---------- расписание ----------

function scheduleBox(days) {
  const box = document.createElement("section");
  box.className = "gym-schedule";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Расписание на неделю";
  box.appendChild(head);
  days.forEach((day) => box.appendChild(scheduleDay(day)));
  return box;
}

function scheduleDay(day) {
  const box = document.createElement("div");
  box.className = "gym-day" + (day.today ? " today" : "");
  const head = document.createElement("div");
  head.className = "gym-day-head";
  head.textContent = day.title;
  box.appendChild(head);
  day.slots.forEach((slot) => box.appendChild(scheduleSlot(slot)));
  return box;
}

// Значками, а не словами: в строке расписания на слово места нет, а
// часы и галочка читаются с одного взгляда
const SLOT_MARKS = {
  open: "идёт",
  late: "заканчивается",
  training: "🕗",
  done: "✅",
  full: "мест нет",
  past: "",
  ahead: "",
};

function scheduleSlot(slot) {
  const box = document.createElement("div");
  box.className = "gym-slot " + slot.state;

  const clock = document.createElement("span");
  clock.className = "gym-slot-clock";
  clock.textContent = slot.clock;
  const title = document.createElement("span");
  title.className = "gym-slot-title";
  title.textContent = slot.emoji + " " + slot.title;
  box.append(clock, title);

  const mark = SLOT_MARKS[slot.state];
  if (mark) {
    const tag = document.createElement("span");
    tag.className = "gym-slot-mark";
    tag.textContent = mark;
    box.appendChild(tag);
  }
  return box;
}

// ---------- Vegas Банк ----------
//
// Три вкладки: «Новый продукт», «Счета» и «Банкомат». Порядок в них от
// пустого банка к обжитому, а открывается банк на той, которая бойцу
// сейчас нужна: без счёта — на «Новом продукте», со счётом — на «Счетах».
// Открывать пустой банкомат человеку, которому нечего снимать, незачем.

let bankData = null;
let bankTab = "";
let bankBusy = false;
// За какой срок просим выписку: число дней (0 — всё время) или свои
// границы в секундах. Срок живёт на странице, а не на сервере: сервер
// отвечает на тот вопрос, который ему задали
let bankDays = 30;
let bankSince = 0;
let bankUntil = 0;

function pickBankTab(name) {
  bankTab = name;
  if (bankData) renderBank(bankData);
}

/** Строка запроса выписки: либо срок в днях, либо свои границы. */
function statementQuery() {
  if (bankSince || bankUntil) {
    return "?since=" + bankSince + "&until=" + bankUntil;
  }
  return "?days=" + bankDays;
}

function pickStatement(days) {
  bankDays = days;
  bankSince = 0;
  bankUntil = 0;
  loadBank();
}

async function loadBank() {
  try {
    const response = await fetch("api/bank" + statementQuery(), {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("bank-note").textContent = "Банк не открылся.";
      return;
    }
    renderBank(await response.json());
  } catch (error) {
    el("bank-note").textContent = error.message;
  }
}

async function bankAction(payload) {
  if (bankBusy) return;
  bankBusy = true;
  try {
    const response = await fetch("api/bank", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Банк", data.error || "Не получилось.");
      await loadBank();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderBank(data);
    if (data.said) popup("Банк", data.said);
  } catch (error) {
    popup("Банк", "Сервер не ответил.");
  } finally {
    bankBusy = false;
  }
}

function renderBank(data) {
  bankData = data;
  el("shop-purse-bank").textContent = "";
  el("shop-purse-bank").appendChild(purse(data.credits));
  el("bank-note").textContent = data.said || bankHint(data);

  // Без счёта показывать в «Счетах» и «Банкомате» нечего: банк
  // открывается там, где бойцу есть что делать
  if (!bankTab) bankTab = data.account.open ? "accounts" : "new";
  if (!data.account.open) bankTab = "new";

  const body = el("bank-body");
  body.textContent = "";

  const tabs = document.createElement("div");
  tabs.className = "bubbles";
  tabs.id = "bank-tabs";
  data.tabs.forEach((tab) => {
    tabs.appendChild(
      chip(tab.title, bankTab === tab.code, () => pickBankTab(tab.code))
    );
  });
  body.appendChild(tabs);

  if (bankTab === "new") body.appendChild(newProductTab(data));
  else if (bankTab === "atm") body.appendChild(atmTab(data));
  else if (bankTab === "log") body.appendChild(statementTab(data));
  else if (bankTab === "deposits") body.appendChild(depositsTab(data));
  else if (bankTab === "loans") body.appendChild(loansTab(data));
  else body.appendChild(accountsTab(data));
}

// ---------- вклады и кредиты ----------
//
// То, за что банк и зовётся банком: он платит за чужие деньги и берёт за
// свои. Считает всё сервер — и доход вклада, и аннуитет: повторять
// ставку и формулу на странице значило бы однажды разойтись с банком в
// цене. Страница рисует таблицу, которую ей дали.

function depositsTab(data) {
  const box = document.createElement("div");
  const state = (data.credit || {}).deposit || {};

  const shelf = document.createElement("section");
  shelf.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "💰 Вклад под " + state.rate + "% годовых";
  shelf.appendChild(head);

  shelf.appendChild(
    paperRows([
      ["Минимум", num(state.min) + " 💰"],
      ["Ставка", state.rate + "% годовых"],
      ["Срок", "1, 2 или 3 месяца"],
      ["На счету", num(state.balance) + " 💰"],
    ])
  );

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Вклад открывают со счёта и возвращают на счёт. Срок — обещание, а "
    + "не замок: забрать можно когда угодно, но досрочный вклад процентов "
    + "не приносит.";
  shelf.appendChild(note);

  // По кнопке на срок: в каждой — что принесёт самый малый вклад
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  (state.terms || []).forEach((term) => {
    buttons.appendChild(
      button(term.months + " мес. · +" + num(term.income) + " 💰 с " + num(state.min), {
        secondary: true,
        disabled: !state.can_open,
        hint: "Вклад открывают со счёта, и на нём нужно не меньше "
          + num(state.min) + " 💰.",
        onClick: () =>
          openMoneySheet({
            title: "Вклад на " + term.months + " мес.",
            note: "Со счёта: " + num(state.balance) + " 💰, от " + num(state.min),
            min: state.min,
            max: state.balance,
            hint: "На счету не хватает: вклад открывают от "
              + num(state.min) + " 💰.",
            onSum: (amount) =>
              bankAction({
                action: "open_deposit",
                amount: amount,
                months: term.months,
              }),
          }),
      })
    );
  });
  shelf.appendChild(buttons);
  box.appendChild(shelf);

  const rows = state.rows || [];
  const mine = document.createElement("section");
  mine.className = "shelf";
  const head2 = document.createElement("h2");
  head2.className = "shelf-head";
  head2.textContent = "Мои вклады";
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = rows.length
    ? rows.length + " " + plural(rows.length, "вклад", "вклада", "вкладов")
      + " на " + num(state.total) + " 💰"
    : "ни одного";
  head2.appendChild(count);
  mine.appendChild(head2);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "shelf-empty";
    empty.textContent = "Вкладов нет. Их можно открыть сколько угодно: "
      + "вклад это сделка, а не кошелёк.";
    mine.appendChild(empty);
  }
  rows.forEach((one) => mine.appendChild(depositCard(one)));
  box.appendChild(mine);
  return box;
}

function depositCard(one) {
  const box = document.createElement("article");
  box.className = "paper" + (one.ripe ? " ready" : "");
  box.appendChild(
    paperRows([
      ["Вклад", num(one.amount) + " 💰 на " + one.months + " мес."],
      ["К сроку", num(one.total) + " 💰 · доход " + num(one.income)],
      [one.ripe ? "Срок" : "Осталось", one.ripe ? "вышел" : one.left_text],
      ["Открыт", one.opened],
    ])
  );
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button(one.ripe ? "Забрать с процентами" : "Забрать досрочно", {
      secondary: !one.ripe,
      onClick: () =>
        one.ripe
          ? bankAction({ action: "close_deposit", id: one.id })
          : askConfirm(
              "Забрать досрочно?",
              "Проценты не начислят: банк платит за срок, а срок не вышел. "
                + "Вернётся ровно " + num(one.amount) + " 💰.",
              "Забрать"
            ).then((ok) =>
              ok ? bankAction({ action: "close_deposit", id: one.id }) : null
            ),
    })
  );
  box.appendChild(buttons);
  return box;
}

function loansTab(data) {
  const box = document.createElement("div");
  const state = (data.credit || {}).loan || {};
  const mine = state.mine || {};

  if (mine.amount) {
    box.appendChild(loanCard(state, mine));
    return box;
  }

  const shelf = document.createElement("section");
  shelf.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🏦 Кредит под " + state.rate + "% годовых";
  shelf.appendChild(head);

  shelf.appendChild(
    paperRows([
      ["Сумма", num(state.min) + " – " + num(state.max) + " 💰"],
      ["Срок", state.terms[0] + " – " + state.terms[state.terms.length - 1]
        + " месяцев"],
      ["Платёж", "аннуитетный, раз в месяц с карты"],
      ["Твой доход", num(state.weekly) + " 💰 в неделю"],
      ["Просрочка", state.late_rate + "% в день от платежа"],
    ])
  );

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Платёж не может быть больше недельного дохода — это и есть главное "
    + "условие. Выдача моментальная, на карту; первое списание ровно через "
    + "месяц.";
  shelf.appendChild(note);

  if (state.why) {
    const why = document.createElement("p");
    why.className = "thing-warn";
    why.textContent = state.why;
    shelf.appendChild(why);
  }
  box.appendChild(shelf);

  box.appendChild(loanCalc(state));
  return box;
}

/** Калькулятор: по строке на срок, с платежом и потолком суммы. */
function loanCalc(state) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Калькулятор";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Чем дольше срок, тем меньше платёж и больше переплата. Серая строка "
    + "значит, что такой платёж твой доход не выносит.";
  box.appendChild(note);

  const list = document.createElement("ul");
  list.className = "rows paper-rows";
  list.id = "loan-calc";
  (state.calc || []).forEach((row) => {
    const li = document.createElement("li");
    if (!row.fits) li.className = "dim";
    const left = document.createElement("span");
    left.className = "label";
    left.textContent = row.months + " мес.";
    const right = document.createElement("span");
    right.className = "value";
    right.textContent = row.fits
      ? "до " + num(row.top) + " 💰 · платёж " + num(row.payment)
        + " 💰 · переплата " + num(row.overpay)
      : "платёж от " + num(row.min_payment) + " 💰 — не по доходу";
    li.append(left, right);
    list.appendChild(li);
  });
  box.appendChild(list);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button(state.can_take ? "Взять кредит" : "Кредит недоступен", {
      disabled: !state.can_take,
      hint: state.why || "При твоём доходе банк не даст и минимальной суммы.",
      onClick: () => openLoanSheet(state),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Створка кредита: сперва срок, потом сумма. */
function openLoanSheet(state) {
  const terms = (state.calc || []).filter((row) => row.fits);
  if (!terms.length) {
    popup("Кредит", "При твоём доходе банк не даст и минимальной суммы.");
    return;
  }
  openSheet(
    "Взять кредит",
    "Сперва срок: от него зависит платёж и потолок суммы.",
    true
  );
  const list = document.createElement("div");
  list.className = "thing-buttons";
  terms.forEach((row) => {
    list.appendChild(
      button(row.months + " мес. · до " + num(row.top) + " 💰", {
        secondary: true,
        onClick: () => {
          closeSheet();
          openMoneySheet({
            title: "Кредит на " + row.months + " мес.",
            note:
              "От " + num(state.min) + " до " + num(row.top) + " 💰. Платёж "
              + "посчитает банк: при потолке — " + num(row.payment) + " 💰 в месяц",
            min: state.min,
            max: row.top,
            hint: "Такую сумму банк не даст.",
            onSum: (amount) =>
              bankAction({
                action: "loan",
                amount: amount,
                months: row.months,
              }),
          });
        },
      })
    );
  });
  el("sheet-list").appendChild(list);
}

/** Свой кредит: график, долг и пени. */
function loanCard(state, mine) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🏦 Мой кредит";
  if (mine.late) {
    const mark = document.createElement("span");
    mark.className = "shelf-count";
    mark.textContent = "просрочка";
    head.appendChild(mark);
  }
  box.appendChild(head);

  const rows = [
    ["Выдан", num(mine.amount) + " 💰 · " + mine.taken],
    ["Платёж", num(mine.payment) + " 💰 в месяц"],
    ["Внесено", mine.paid + " из " + mine.months],
    ["Осталось выплатить", num(mine.body_left) + " 💰"],
  ];
  if (mine.pays_left) {
    // Час списания уже мог пройти: тогда «через» не о чем говорить
    rows.push([
      "Следующее списание",
      mine.due_text ? mine.due + " · через " + mine.due_text : mine.due,
    ]);
  }
  if (mine.late) {
    rows.push(["Просрочено", num(mine.owed) + " 💰"]);
    rows.push([
      "Пени",
      num(mine.penalty) + " 💰 · " + state.late_rate + "% в день"
        + (mine.late_days ? " · дней " + mine.late_days : ""),
    ]);
    rows.push(["Всего к оплате", num(mine.debt) + " 💰"]);
  }
  box.appendChild(paperRows(rows));

  const note = document.createElement("p");
  note.className = mine.late ? "thing-warn" : "thing-note";
  note.textContent = mine.late
    ? "Пени капают каждый день, пока долг не погашен. Списание идёт с "
      + "карты само, как только на ней появятся деньги."
    : "Списание с карты идёт само, ровно через месяц после выдачи и "
      + "дальше месяц в месяц. Приходить за этим в банк не надо.";
  box.appendChild(note);

  if (mine.late) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Погасить просрочку · " + num(mine.debt) + " 💰", {
        onClick: () => bankAction({ action: "repay" }),
      })
    );
    box.appendChild(buttons);
  }
  return box;
}

// ---------- вкладка «Выписка» ----------
//
// Банк видит только счёт: наличные ходят из рук в руки и следа не
// оставляют. Поэтому выписка — про карту, и говорит об этом сама, а не
// оставляет бойца гадать, почему покупки за наличные в ней не видно.

function statementTab(data) {
  const box = document.createElement("div");
  const log = data.statement || { rows: [], periods: [] };

  const periods = document.createElement("div");
  periods.className = "bubbles";
  periods.id = "log-periods";
  (log.periods || []).forEach((one) => {
    periods.appendChild(
      chip(one.title, !log.custom && one.on, () => pickStatement(one.days))
    );
  });
  periods.appendChild(
    chip("Свой срок", Boolean(log.custom), () => openPeriodSheet())
  );
  box.appendChild(periods);

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = log.custom
    ? "Выписка: " + (log.since || "начало") + " — " + (log.until || log.now)
    : "Выписка" + (log.days ? " за " + log.days + " дней" : " за всё время");
  box.appendChild(head);

  const sums = document.createElement("div");
  sums.className = "log-sums";
  [
    ["Поступления", log.income, "plus"],
    ["Списания", log.spent, "minus"],
  ].forEach(([title, money, mark]) => {
    const cell = document.createElement("div");
    // `log-plus`, а не `plus`: голым словом `plus` зовётся круглая синяя
    // кнопка кошелька, и сумма прихода надевала её одежду
    cell.className = "log-sum log-" + mark;
    const name = document.createElement("span");
    name.className = "log-sum-title";
    name.textContent = title;
    const sum = document.createElement("span");
    sum.className = "log-sum-money";
    sum.textContent = (mark === "plus" ? "+" : "−") + num(money || 0) + " 💰";
    cell.append(name, sum);
    sums.appendChild(cell);
  });
  box.appendChild(sums);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = log.note || "";
  box.appendChild(note);

  const list = document.createElement("div");
  list.className = "log-list";
  (log.rows || []).forEach((row) => list.appendChild(logRow(row)));
  box.appendChild(list);
  return box;
}

function logRow(row) {
  const box = document.createElement("div");
  box.className = "log-row";

  const left = document.createElement("div");
  left.className = "log-what";
  const note = document.createElement("div");
  note.className = "log-note";
  note.textContent = row.note;
  const when = document.createElement("div");
  when.className = "log-when";
  when.textContent = row.when + " · " + row.kind_title;
  left.append(note, when);
  // За что платили. Старым строкам комментария взять негде: до него
  // выписка помнила только дом, и дорисовывать его задним числом не из
  // чего — такая строка остаётся в две строки
  if (row.about) {
    const about = document.createElement("div");
    about.className = "log-about";
    about.textContent = row.about;
    left.appendChild(about);
  }
  box.appendChild(left);

  const right = document.createElement("div");
  right.className = "log-money";
  const sum = document.createElement("div");
  sum.className = "log-sum-value " + (row.plus ? "log-plus" : "log-minus");
  sum.textContent = (row.plus ? "+" : "−") + num(Math.abs(row.amount)) + " 💰";
  const rest = document.createElement("div");
  rest.className = "log-rest";
  rest.textContent = "остаток " + num(row.balance) + " 💰";
  right.append(sum, rest);
  box.appendChild(right);
  return box;
}

/** Свой срок: две даты и кнопка. Любой срок — значит любой. */
function openPeriodSheet() {
  openSheet("Свой срок", "Выписка за выбранные дни включительно", true);
  const list = el("sheet-list");

  const since = document.createElement("input");
  since.type = "date";
  since.className = "field-input";
  since.id = "log-since";
  list.appendChild(since);

  const until = document.createElement("input");
  until.type = "date";
  until.className = "field-input";
  until.id = "log-until";
  list.appendChild(until);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Показать", {
      onClick: () => {
        const from = Date.parse(el("log-since").value);
        const to = Date.parse(el("log-until").value);
        if (!from && !to) {
          popup("Выписка", "Выбери хотя бы одну дату.");
          return;
        }
        bankDays = 0;
        bankSince = from ? Math.floor(from / 1000) : 0;
        // Последний день входит в срок целиком: до его полуночи
        bankUntil = to ? Math.floor(to / 1000) + 24 * 60 * 60 - 1 : 0;
        closeSheet();
        loadBank();
      },
    })
  );
  buttons.appendChild(
    button("Отменить", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);
}

function bankHint(data) {
  if (!data.account.open) {
    return "Счёт открывается сразу и ничего не стоит.";
  }
  if (!data.bank_card.open) {
    return "Карта даёт скидки в городе и платит прямо со счёта.";
  }
  if (!data.bank_card.works) {
    return "Карта не обслуживается: пополни счёт, и она заработает.";
  }
  return "Счёт и карта на месте. Снять наличные — в банкомате.";
}

// ---------- вкладка «Новый продукт» ----------

function newProductTab(data) {
  const box = document.createElement("div");
  if (!data.account.open) box.appendChild(accountOffer(data.account));
  if (!data.bank_card.open) box.appendChild(cardOffer(data));
  if (data.account.open && data.bank_card.open) {
    const done = document.createElement("p");
    done.className = "screen-note";
    done.textContent =
      "Всё, что банк выдаёт, у тебя уже есть: счёт и карта. "
      + "Второй счёт и вторая карта на одного бойца не открываются.";
    box.appendChild(done);
  }
  box.appendChild(discountShelf(data.discounts));
  return box;
}

function accountOffer(account) {
  const box = document.createElement("section");
  box.className = "offer";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "💼 " + account.title;
  box.appendChild(head);

  box.appendChild(givesList(account.gives));

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = account.note;
  box.appendChild(note);

  box.appendChild(
    button("Открыть счёт — бесплатно", {
      onClick: () => bankAction({ action: "account" }),
    })
  );
  return box;
}

function cardOffer(data) {
  const card = data.bank_card;
  const box = document.createElement("section");
  box.className = "offer";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = card.emoji + " " + card.title;
  box.appendChild(head);

  box.appendChild(givesList(card.gives));

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = card.note;
  box.appendChild(note);

  // Счёта нет — кнопку не прячем, а объясняем: спрятанная кнопка не
  // говорит, чего не хватает
  const ready = data.account.open;
  box.appendChild(
    button("Выпустить карту · " + num(card.price) + " 💰", {
      disabled: !ready,
      hint: "Сначала открой счёт: карта выпускается к нему.",
      onClick: () => bankAction({ action: "card" }),
    })
  );
  return box;
}

function givesList(rows) {
  const list = document.createElement("ul");
  list.className = "gives";
  (rows || []).forEach((line) => {
    const item = document.createElement("li");
    item.textContent = line;
    list.appendChild(item);
  });
  return list;
}

/** Прайс скидок: где и сколько снимает карта. */
function discountShelf(rows) {
  const box = document.createElement("section");
  box.className = "day-prices";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Скидки по карте";
  box.appendChild(head);

  (rows || []).forEach((row) => {
    const line = document.createElement("div");
    line.className = "day-price";
    const where = document.createElement("span");
    where.className = "day-price-number";
    where.textContent = row.title;
    const off = document.createElement("span");
    off.className = "day-price-value";
    off.textContent = "−" + row.percent + "%";
    line.append(where, off);
    box.appendChild(line);
  });

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Где банк скидки не обещал — мастерская, комиссионка, лавка мага — "
    + "картой платится полная цена. На рынке из рук в руки карта не ходит.";
  box.appendChild(note);
  return box;
}

// ---------- вкладка «Счета» ----------

function accountsTab(data) {
  const box = document.createElement("div");
  box.appendChild(accountBox(data.account));
  if (data.bank_card.open) box.appendChild(bankCardBox(data.bank_card));
  else box.appendChild(cardOffer(data));
  box.appendChild(purseChoice(data));
  return box;
}

function accountBox(account) {
  const box = document.createElement("section");
  box.className = "account";
  const head = document.createElement("div");
  head.className = "account-head";
  head.textContent = "💼 " + account.title;
  const number = document.createElement("div");
  number.className = "account-number";
  number.id = "bank-number";
  number.textContent = account.number;
  // Номер называют вслух, чтобы принять перевод: нажал — скопировал
  number.title = "Нажми, чтобы скопировать";
  number.addEventListener("click", () => copyNumber(account.number));
  const sum = document.createElement("div");
  sum.className = "account-sum";
  sum.textContent = num(account.balance) + " 💰";
  box.append(head, number, sum);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = "По этому номеру тебе переведут деньги со счёта на счёт.";
  box.appendChild(note);
  return box;
}

function copyNumber(number) {
  try {
    navigator.clipboard.writeText(number);
    popup("Номер счёта", number + "\nСкопирован.");
  } catch (error) {
    // Буфера может не быть вовсе: тогда просто показываем номер целиком
    popup("Номер счёта", number);
  }
}

function bankCardBox(card) {
  const box = document.createElement("section");
  box.className = "plastic" + (card.works ? "" : " stale");

  const head = document.createElement("div");
  head.className = "plastic-head";
  const title = document.createElement("span");
  title.textContent = card.emoji + " " + card.title;
  const state = document.createElement("span");
  state.className = "plastic-state";
  state.textContent = card.state;
  head.append(title, state);

  const number = document.createElement("div");
  number.className = "plastic-number";
  number.textContent = card.number;
  const holder = document.createElement("div");
  holder.className = "plastic-holder";
  holder.textContent = card.holder;
  const paid = document.createElement("div");
  paid.className = "plastic-paid";
  paid.textContent = "Обслуживание оплачено по " + card.paid_until;
  box.append(head, number, holder, paid);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = card.works
    ? "Карта в «Документах» — там же, где полис и абонемент."
    : "На счету нет " + num(card.year_price) + " 💰 за год обслуживания. "
      + "Пополни счёт — карта заработает сама.";
  box.appendChild(note);
  return box;
}

/** Чем боец платит по умолчанию. Живёт в «Счетах»: это про деньги. */
function purseChoice(data) {
  const box = document.createElement("section");
  box.className = "purse-choice";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Чем платить в городе";
  box.appendChild(head);

  const row = document.createElement("div");
  row.className = "purse-row";
  data.purses.forEach((one) => {
    row.appendChild(purseChip(one));
  });
  box.appendChild(row);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = data.choice_note || data.purse.note;
  box.appendChild(note);
  return box;
}

function purseChip(one) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className =
    "purse-pick" + (one.chosen ? " on" : "") + (one.ready ? "" : " off");
  btn.dataset.purse = one.code;
  const title = document.createElement("span");
  title.className = "purse-pick-title";
  title.textContent = one.emoji + " " + one.title;
  const money = document.createElement("span");
  money.className = "purse-pick-money";
  money.textContent = num(one.money) + " 💰";
  btn.append(title, money);
  if (!one.ready) {
    btn.addEventListener("click", () =>
      popup("Банк", "Карты Vegas Банка у тебя нет — платить ею нечем.")
    );
  } else if (!one.chosen) {
    btn.addEventListener("click", () => choosePurse(one.code));
  }
  return btn;
}

// ---------- вкладка «Банкомат» ----------

function atmTab(data) {
  const box = document.createElement("div");

  const sums = document.createElement("div");
  sums.className = "atm-sums";
  sums.appendChild(atmSum("💰 Наличные", data.credits));
  sums.appendChild(atmSum("💼 На счету", data.account.balance));
  box.appendChild(sums);

  box.appendChild(
    moneyForm("Положить на счёт", "Положить", data.credits, (amount) =>
      bankAction({ action: "deposit", amount: amount })
    )
  );
  box.appendChild(
    moneyForm("Снять наличными", "Снять", data.account.balance, (amount) =>
      bankAction({ action: "withdraw", amount: amount })
    )
  );
  box.appendChild(sendForm(data));

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Банкомат и переводы — без комиссии: банк в клубе зарабатывает на "
    + "карте, а не на движении денег.";
  box.appendChild(note);
  return box;
}

function atmSum(title, amount) {
  const box = document.createElement("div");
  box.className = "atm-sum";
  const head = document.createElement("span");
  head.className = "atm-sum-title";
  head.textContent = title;
  const money = document.createElement("span");
  money.className = "atm-sum-money";
  money.textContent = num(amount) + " 💰";
  box.append(head, money);
  return box;
}

function moneyForm(title, action, most, onSend) {
  const box = document.createElement("section");
  box.className = "money-form";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = title;
  box.appendChild(head);

  const row = document.createElement("div");
  row.className = "money-row";
  const field = document.createElement("input");
  field.type = "number";
  field.className = "money-field";
  field.inputMode = "numeric";
  field.min = "1";
  field.max = String(most);
  field.placeholder = "Сколько";
  const all = document.createElement("button");
  all.type = "button";
  all.className = "money-all";
  all.textContent = "Всё";
  all.addEventListener("click", () => {
    field.value = String(most);
  });
  row.append(field, all);
  box.appendChild(row);

  box.appendChild(
    button(action, {
      disabled: most <= 0,
      hint: "Переносить нечего.",
      onClick: () => {
        const amount = Math.floor(Number(field.value) || 0);
        if (amount <= 0) {
          popup("Банк", "Сумма должна быть больше нуля.");
          return;
        }
        field.value = "";
        onSend(amount);
      },
    })
  );
  return box;
}

function sendForm(data) {
  const box = document.createElement("section");
  box.className = "money-form";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Перевести на чужой счёт";
  box.appendChild(head);

  const number = document.createElement("input");
  number.type = "text";
  number.className = "money-field wide";
  number.id = "bank-send-number";
  number.placeholder = "VB-0000-0000-0000";
  number.autocomplete = "off";
  box.appendChild(number);

  const row = document.createElement("div");
  row.className = "money-row";
  const field = document.createElement("input");
  field.type = "number";
  field.className = "money-field";
  field.inputMode = "numeric";
  field.min = "1";
  field.max = String(data.account.balance);
  field.placeholder = "Сколько";
  row.appendChild(field);
  box.appendChild(row);

  box.appendChild(
    button("Перевести", {
      disabled: data.account.balance <= 0,
      hint: "На счету пусто — переводить нечего.",
      onClick: () => {
        const amount = Math.floor(Number(field.value) || 0);
        if (!number.value.trim()) {
          popup("Банк", "Назови номер счёта получателя.");
          return;
        }
        if (amount <= 0) {
          popup("Банк", "Сумма должна быть больше нуля.");
          return;
        }
        bankAction({
          action: "send",
          number: number.value.trim(),
          amount: amount,
        });
      },
    })
  );
  return box;
}

// ---------- кошелёк на прилавке ----------
//
// Переключатель стоит на каждом экране с ценами, а не только в банке:
// выбирать, чем платить, надо там, где стоит цена. Цены на витрине уже
// посчитаны сервером под выбранный кошелёк — страница ничего не считает
// сама, иначе однажды показала бы скидку там, где её нет.

/** «наличными 50 💰» или «на счету 50 💰» — для отказа по деньгам.
 *
 * Та же строка, что у сервера, и по той же причине: кошелька два, и
 * отказ обязан называть тот, из которого здесь платят. Иначе боец с
 * тысячей на счету читает «не хватает» и не понимает, где его деньги.
 */
function purseNote(state) {
  if (!state) return "денег не хватает";
  return state.purse === "card"
    ? "на счету " + num(state.balance) + " 💰"
    : "наличными " + num(state.cash) + " 💰";
}

/** Сколько денег в том кошельке, которым здесь платят. */
function spendable(data) {
  const state = data && data.purse;
  if (!state) return data ? data.credits : 0;
  return state.purse === "card" ? state.balance : state.cash;
}

function walletBar(state, reload) {
  const box = document.createElement("section");
  box.className = "wallet";
  if (!state) return box;

  const row = document.createElement("div");
  row.className = "wallet-row";
  [
    ["cash", state.cash, true],
    ["card", state.balance, state.has_card && state.card_works && state.takes_card],
  ].forEach(([code, money, ready]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "wallet-pick"
      + (state.purse === code ? " on" : "")
      + (ready ? "" : " off");
    btn.dataset.purse = code;
    const title = document.createElement("span");
    title.className = "wallet-pick-title";
    title.textContent = state.emoji[code] + " " + state.titles[code];
    const sum = document.createElement("span");
    sum.className = "wallet-pick-money";
    sum.textContent = num(money) + " 💰";
    btn.append(title, sum);
    // Скидка видна на самой кнопке: по ней и понятно, зачем переключать
    if (code === "card" && state.discount) {
      const off = document.createElement("span");
      off.className = "wallet-pick-off";
      off.textContent = "−" + state.discount + "%";
      btn.appendChild(off);
    }
    if (ready && state.prefers !== code) {
      btn.addEventListener("click", () => choosePurse(code, reload));
    }
    row.appendChild(btn);
  });
  box.appendChild(row);

  const note = document.createElement("p");
  note.className = "wallet-note";
  note.textContent = state.note;
  box.appendChild(note);
  return box;
}

async function choosePurse(code, reload) {
  try {
    const data = await post("api/purse", { purse: code });
    if (data.card) render(data.card, true);
    if (reload) reload();
    else await loadBank();
  } catch (error) {
    popup("Банк", error.message);
  }
}

// ---------- HR-агентство ----------
//
// Доска из пяти мест. Место одно на город: занято — видно кем, и заявку
// подавать некуда. Тест приходит по нажатию «Подать заявку» и живёт
// ровно до ответа: верных ответов на странице нет вовсе, сверяет сервер.

let hrData = null;
let hrBusy = false;
let hrQuiz = null; // {code, questions, chosen}

async function loadHr() {
  try {
    const response = await fetch("api/hr", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("hr-note").textContent = "Агентство не открылось.";
      return;
    }
    renderHr(await response.json());
  } catch (error) {
    el("hr-note").textContent = error.message;
  }
}

async function hrAction(payload) {
  if (hrBusy) return;
  hrBusy = true;
  try {
    const response = await fetch("api/hr", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Агентство", data.error || "Не получилось.");
      await loadHr();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    if (data.quiz) {
      hrQuiz = { code: data.code, questions: data.quiz, chosen: [] };
    } else {
      hrQuiz = null;
    }
    renderHr(data);
    if (data.said) popup("Агентство", data.said);
  } catch (error) {
    popup("Агентство", "Сервер не ответил.");
  } finally {
    hrBusy = false;
  }
}

function renderHr(data) {
  hrData = data;
  el("hr-note").textContent = data.said || hrHint(data);

  const body = el("hr-body");
  body.textContent = "";

  // Идёт тест — на экране только он: уходить с половины некуда, а
  // доска под вопросами отвлекала бы от единственного, что сейчас важно
  if (hrQuiz) {
    body.appendChild(quizBox(data));
    return;
  }
  if (data.job && data.job.code) body.appendChild(myJobBox(data.job));
  body.appendChild(boardBox(data));
  body.appendChild(rulesBox(data));
}

function hrHint(data) {
  if (data.job && data.job.code) {
    return "Ты уже работаешь. Работа одна на бойца.";
  }
  const open = (data.vacancies || []).filter((one) => one.open).length;
  if (!open) return "Подходящих мест сейчас нет. Загляни позже.";
  return (
    "Открыто мест: " + open + ". С улицы берут по тесту, остальных — "
    + "по диплому и стажу."
  );
}

/** Своя работа: часы за неделю и когда платят. */
function myJobBox(job) {
  const box = document.createElement("section");
  box.className = "job";

  const head = document.createElement("div");
  head.className = "job-head";
  const title = document.createElement("span");
  title.className = "job-title";
  title.textContent = job.emoji + " " + job.title;
  const where = document.createElement("span");
  where.className = "job-where";
  where.textContent = job.place_title;
  head.append(title, where);
  box.appendChild(head);

  box.appendChild(hoursBar(job));

  const pay = document.createElement("p");
  pay.className = "job-pay";
  pay.textContent =
    "Жалованье " + num(job.salary) + " 💰 в неделю · сейчас набежало "
    + num(job.payout) + " 💰 · выплата " + job.payday;
  box.appendChild(pay);

  const extra = overtimeLine(job);
  if (extra) box.appendChild(extra);

  if (!job.safe) {
    const warn = document.createElement("p");
    warn.className = "job-warn";
    warn.textContent =
      "Меньше половины нормы — уволят в понедельник. Нужно отработать "
      + job.keep_hours + " ч.";
    box.appendChild(warn);
  }

  box.appendChild(
    button("Уйти с работы", {
      secondary: true,
      onClick: () => askQuit(job),
    })
  );
  return box;
}

/** Строка про переработку. Пусто — неделя в норму уложилась.
 *
 * Считает её сервер: сколько часов сверх нормы и сколько за них
 * доплатят. Страница только пишет — делить жалованье на часы в двух
 * местах значило бы однажды разойтись с кассой.
 */
function overtimeLine(job) {
  if (!job.overtime) return null;
  const row = document.createElement("p");
  row.className = "job-extra";
  row.textContent =
    "Сверхурочные: " + job.overtime + " ч сверх нормы по " +
    num(job.hour_rate) + " 💰 — это " + num(job.overtime_pay) + " 💰 сверху";
  return row;
}

function hoursBar(job) {
  const box = document.createElement("div");
  box.className = "hours";
  const line = document.createElement("div");
  line.className = "hours-line";
  const fill = document.createElement("div");
  fill.className = "hours-fill" + (job.safe ? "" : " short");
  fill.style.width = job.percent + "%";
  line.appendChild(fill);
  const text = document.createElement("div");
  text.className = "hours-text";
  text.textContent =
    "Отработано " + job.worked + " из " + job.hours + " ч за неделю";
  box.append(line, text);
  return box;
}

function askQuit(job) {
  // Уход стоит недели без этого места: спрашиваем до, а не после
  popup(
    "Уйти с работы",
    "Место освободится, а подать на него снова можно будет через неделю. "
      + "Заработанное за эту неделю выплатят сразу."
  );
  hrAction({ action: "quit" });
}

/** Доска вакансий: пять мест города. */
function boardBox(data) {
  const box = document.createElement("section");
  box.className = "board";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Вакансии города";
  box.appendChild(head);
  (data.vacancies || []).forEach((one) => box.appendChild(vacancyCard(one, data)));
  return box;
}

function vacancyCard(one, data) {
  const box = document.createElement("article");
  box.className =
    "vacancy" + (one.mine ? " mine" : "") + (one.taken && !one.mine ? " taken" : "");

  const head = document.createElement("div");
  head.className = "vacancy-head";
  const title = document.createElement("span");
  title.className = "vacancy-title";
  title.textContent = one.emoji + " " + one.title;
  const pay = document.createElement("span");
  pay.className = "vacancy-pay";
  pay.textContent = num(one.salary) + " 💰/нед";
  head.append(title, pay);
  box.appendChild(head);

  const facts = document.createElement("div");
  facts.className = "vacancy-facts";
  [
    ["Место", one.place_title],
    ["Норма", one.hours + " ч в неделю"],
    ["Образование", one.education],
    // Стаж спрашивают не везде: «без опыта» на каждой карточке было бы
    // шумом, а на местах под диплом он и есть главное условие
    ["Опыт", one.months ? one.months_text : ""],
    // Сколько кресел и сколько из них свободно. У мест с одним креслом
    // строка не нужна: «1 место, свободно 1» ничего не добавляет
    [
      "Мест",
      one.seats > 1
        ? one.seats + ", свободно " + one.free_seats
        : "",
    ],
  ].filter(([, value]) => value).forEach(([name, value]) => {
    const row = document.createElement("div");
    row.className = "vacancy-fact";
    const left = document.createElement("span");
    left.textContent = name;
    const right = document.createElement("span");
    right.textContent = value;
    row.append(left, right);
    facts.appendChild(row);
  });
  box.appendChild(facts);

  if (one.note) {
    const note = document.createElement("p");
    note.className = "vacancy-note";
    note.textContent = one.note;
    box.appendChild(note);
  }

  box.appendChild(vacancyButton(one, data));
  return box;
}

function vacancyButton(one, data) {
  if (one.mine) {
    const said = document.createElement("p");
    said.className = "vacancy-state";
    said.textContent = "Здесь работаешь ты.";
    return said;
  }
  if (one.taken) {
    const said = document.createElement("p");
    said.className = "vacancy-state";
    said.textContent =
      (one.seats > 1 ? "Все места заняты: " : "Занято: ") + one.taken_by + ".";
    return said;
  }
  // Нет диплома или стажа — говорим чего именно, а не прячем кнопку:
  // человек должен понимать, куда ему идти доучиваться
  if (one.lacks) {
    const said = document.createElement("p");
    said.className = "vacancy-state";
    said.textContent = one.lacks;
    return said;
  }
  if (one.blocked) {
    const said = document.createElement("p");
    said.className = "vacancy-state";
    said.textContent =
      blockWhy(one.block_reason) + " Снова можно через " + one.block_days + " дн.";
    return said;
  }
  const busy = Boolean(data.job && data.job.code);
  // Где тест есть — сперва вопросы; где его нет, дверь закрыта дипломом,
  // и экзамен по ней уже сдан в училище: берут сразу
  return button(one.has_quiz ? "Подать заявку" : "Устроиться", {
    disabled: busy,
    hint: "У тебя уже есть работа. Работа одна на бойца.",
    onClick: () =>
      hrAction(
        one.has_quiz
          ? { action: "quiz", code: one.code }
          : { action: "apply", code: one.code, answers: [] }
      ),
  });
}

function blockWhy(reason) {
  if (reason === "test") return "Тест сюда ты уже провалил.";
  if (reason === "fired") return "Отсюда тебя уволили.";
  if (reason === "quit") return "Отсюда ты ушёл сам.";
  return "Сюда сейчас нельзя.";
}

function rulesBox(data) {
  const box = document.createElement("section");
  box.className = "day-prices";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Как это устроено";
  box.appendChild(head);
  [
    ["Тест", data.questions + " вопросов, нужно " + data.pass_percent + "%"],
    ["Диплом", "на места под диплом теста нет: его сдали в училище"],
    ["Опыт", "считается по всем местам и остаётся при уходе"],
    ["Смена", data.shift_hours + " ч, не больше " + data.day_hours + " ч в сутки"],
    ["Выплата", "в понедельник в 9:00 по Москве"],
    ["Мало часов", "меньше половины нормы — увольнение"],
    ["После отказа", "заявка снова через " + data.cooldown_days + " дн."],
  ].forEach(([name, value]) => {
    const line = document.createElement("div");
    line.className = "day-price";
    const left = document.createElement("span");
    left.className = "day-price-number";
    left.textContent = name;
    const right = document.createElement("span");
    right.className = "day-price-value";
    right.textContent = value;
    line.append(left, right);
    box.appendChild(line);
  });
  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = data.note;
  box.appendChild(note);
  return box;
}

// ---------- мини-тест ----------

function quizBox(data) {
  const box = document.createElement("section");
  box.className = "quiz";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Тест на место";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Пять вопросов, ответить верно надо на " + data.pass_percent
    + "%. Ошибиться можно один раз.";
  box.appendChild(note);

  hrQuiz.questions.forEach((question, index) => {
    box.appendChild(questionCard(question, index));
  });

  const row = document.createElement("div");
  row.className = "quiz-buttons";
  row.appendChild(
    button("Ответить", {
      disabled: hrQuiz.chosen.filter((one) => one !== undefined).length
        < hrQuiz.questions.length,
      hint: "Ответь на все вопросы.",
      onClick: () =>
        hrAction({
          action: "apply",
          code: hrQuiz.code,
          answers: hrQuiz.chosen,
        }),
    })
  );
  row.appendChild(
    button("Передумал", {
      secondary: true,
      onClick: () => {
        hrQuiz = null;
        renderHr(hrData);
      },
    })
  );
  box.appendChild(row);
  return box;
}

function questionCard(question, index) {
  const box = document.createElement("div");
  box.className = "question";
  const text = document.createElement("div");
  text.className = "question-text";
  text.textContent = index + 1 + ". " + question.text;
  box.appendChild(text);

  question.options.forEach((option, at) => {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "answer" + (hrQuiz.chosen[index] === at ? " on" : "");
    pick.textContent = option;
    pick.addEventListener("click", () => {
      hrQuiz.chosen[index] = at;
      renderHr(hrData);
    });
    box.appendChild(pick);
  });
  return box;
}

// ---------- рабочее место ----------

let workData = null;
let workBusy = false;
let workTimer = null;

async function loadWork() {
  try {
    const response = await fetch("api/work", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("work-note").textContent = "Сюда не попасть.";
      return;
    }
    renderWork(await response.json());
  } catch (error) {
    el("work-note").textContent = error.message;
  }
}

async function workAction(payload) {
  if (workBusy) return;
  workBusy = true;
  try {
    const response = await fetch("api/work", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Работа", data.error || "Не получилось.");
      await loadWork();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderWork(data);
    if (data.said) popup("Работа", data.said);
  } catch (error) {
    popup("Работа", "Сервер не ответил.");
  } finally {
    workBusy = false;
  }
}

function renderWork(data) {
  workData = data;
  el("work-title").textContent = data.job.code
    ? data.job.emoji + " " + data.job.title
    : "💼 Работа";
  el("work-note").textContent = data.said || data.note;

  const body = el("work-body");
  body.textContent = "";

  if (data.shift && data.shift.seconds_left) {
    body.appendChild(shiftBox(data));
    startWorkClock();
    return;
  }
  stopWorkClock();
  if (!data.job.code) {
    body.appendChild(strangerBox(data));
    return;
  }
  body.appendChild(hoursBar(data.job));
  body.appendChild(dayBox(data));
  body.appendChild(payBox(data.job));
}

/** Кнопки места: устроиться тут же или подать заявку на кресло.
 *
 * Кресло начальника не занимают — на него подают заявку, а сажает мэр
 * и сам называет жалованье из вилки. Поэтому у такого места кнопка
 * другая, и отменить её можно тем же нажатием.
 */
function houseHire(one) {
  if (!one.at_house || one.mine) return null;
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (one.appointed) {
    buttons.appendChild(
      button(one.bid ? "Отозвать заявку" : "Подать заявку", {
        disabled: !one.can_bid && !one.bid,
        hint: one.lacks || "Сюда не подают.",
        onClick: () => workAction({
          action: one.bid ? "unbid" : "bid", job: one.code,
        }),
      })
    );
    return buttons;
  }
  buttons.appendChild(
    button("Устроиться", {
      disabled: !one.can_take,
      hint: one.lacks
        || (one.limited && !one.free_seats ? "Мест нет." : "Работа одна на бойца."),
      onClick: () => workAction({ action: "hire", job: one.code }),
    })
  );
  return buttons;
}

/** Идёт смена: часы тикают на странице, а время считает сервер. */
function shiftBox(data) {
  const box = document.createElement("section");
  box.className = "shift";
  const head = document.createElement("div");
  head.className = "shift-head";
  head.textContent = "Смена идёт";
  const clock = document.createElement("div");
  clock.className = "shift-clock";
  clock.id = "work-clock";
  box.append(head, clock);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Часы уже засчитаны. Из дома не выйти, пока смена не кончится — "
    + "ты на работе.";
  box.appendChild(note);
  return box;
}

function startWorkClock() {
  stopWorkClock();
  workTimer = setInterval(tickWork, 1000);
  tickWork();
}

function stopWorkClock() {
  if (workTimer) clearInterval(workTimer);
  workTimer = null;
}

function tickWork() {
  const shown = el("work-clock");
  if (!shown || !workData || !workData.shift) return;
  const left = Math.max(
    0,
    workData.shift.until - Math.floor(Date.now() / 1000)
  );
  if (!left) {
    stopWorkClock();
    loadWork();
    return;
  }
  shown.textContent = "Осталось " + fullClock(left);
}

function dayBox(data) {
  const box = document.createElement("section");
  box.className = "shift";
  const head = document.createElement("div");
  head.className = "shift-head";
  head.textContent =
    "Сегодня отработано " + data.job.today + " из " + data.job.day_hours + " ч";
  box.appendChild(head);

  box.appendChild(
    button("Начать работать · " + data.shift_hours + " ч", {
      disabled: !data.can_start,
      hint: "На сегодня хватит: в сутки работают "
        + data.job.day_hours + " часа.",
      onClick: () => workAction({ action: "start" }),
    })
  );
  return box;
}

function payBox(job) {
  const box = document.createElement("div");
  const line = document.createElement("p");
  line.className = "job-pay";
  line.textContent =
    "Набежало " + num(job.payout) + " 💰 · выплата " + job.payday
    + " на счёт в банке";
  box.appendChild(line);
  const extra = overtimeLine(job);
  if (extra) box.appendChild(extra);
  return box;
}

/** Чужое рабочее место: что тут вообще работают.
 *
 * Мест в доме бывает несколько — в банке три, — и перечисляем все: за
 * любым из них идти в одну и ту же дверь, но знать о них надо.
 */
function strangerBox(data) {
  const box = document.createElement("section");
  box.className = "offer";
  const here = data.vacancies || [];
  if (!here.length) return box;

  here.forEach((one) => {
    const head = document.createElement("h2");
    head.className = "shelf-head";
    head.textContent = one.emoji + " " + one.title;
    box.appendChild(head);

    const facts = document.createElement("div");
    facts.className = "vacancy-facts";
    [
      ["Жалованье", one.pay_text + " в неделю"],
      ["Норма", one.hours + " ч в неделю"],
      ["Образование", one.needs ? one.education : ""],
      ["Опыт", one.months ? one.months_text : ""],
      ["Мест", one.limited ? one.seats + ", занято " + one.taken_seats : "без счёта"],
      ["Работают", one.taken_by || ""],
    ].filter(([, value]) => value).forEach(([name, value]) => {
      const row = document.createElement("div");
      row.className = "vacancy-fact";
      const left = document.createElement("span");
      left.textContent = name;
      const right = document.createElement("span");
      right.textContent = value;
      row.append(left, right);
      facts.appendChild(row);
    });
    box.appendChild(facts);
    const buttons = houseHire(one);
    if (buttons) box.appendChild(buttons);
    if (one.lacks) {
      const why = document.createElement("p");
      why.className = "screen-note";
      why.textContent = one.lacks;
      box.appendChild(why);
    }
  });

  // Где берут: в доме или в агентстве — у разных мест по-разному, и
  // сказать это надо один раз на весь экран
  const note = document.createElement("p");
  note.className = "screen-note";
  const agency = here.filter((one) => !one.at_house).length;
  note.textContent = agency
    ? (agency > 1
        ? "Эти места занимают в HR-агентстве, а не здесь."
        : "Это место занимают в HR-агентстве, а не здесь.")
    : "Сюда берут на месте: диплом заменяет тест агентства.";
  box.appendChild(note);
  return box;
}

// ---------- вход на работу из своего дома ----------
//
// Кнопка живёт в шапке того экрана, который открыт: у зала свой экран, у
// клуба свой, а у бара — этот же. Рисовать её в каждом экране отдельно
// значило бы пять раз одно и то же, поэтому она одна и вешается на ту
// шапку, что сейчас на виду.

function paintWorkEntry(screen) {
  document.querySelectorAll(".work-entry").forEach((one) => one.remove());
  if (screen === "work" || screen === "hr") return;
  if (!cardData || !cardData.work || !cardData.work.here) return;
  const head = el(screen) && el(screen).querySelector(".screen-head");
  if (!head) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "work-entry" + (cardData.work.on_shift ? " on" : "");
  btn.textContent = cardData.work.on_shift ? "🕗 Смена" : "💼 Работа";
  btn.addEventListener("click", () => showTab("work"));
  head.appendChild(btn);
}

/** Кнопка «Рейд» в шапке дома, в котором идёт рейд.

 * Дверь дома открывает первую его услугу, и это не всегда рейд: на
 * полигоне она ведёт на занятия кадетов. Кнопка — тот же приём, которым
 * в казино открывают работу: дом один, дел в нём два.
 */
function paintRaidEntry(screen) {
  document.querySelectorAll(".raid-entry").forEach((one) => one.remove());
  if (!inRaidHouse()) return;
  const head = el(screen) && el(screen).querySelector(".screen-head");
  if (!head) return;

  // Рейд уже открыт — кнопка горит, как «Смена» у работы. Гасить её на
  // этом экране значило бы делать шапку в казино не такой, как на
  // полигоне, а однообразие тут и есть смысл кнопки
  const here = screen === "club" && clubSection === "raid";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "work-entry raid-entry" + (here ? " on" : "");
  btn.textContent = "🎖 Рейд";
  btn.addEventListener("click", () => openRaidHouse(myPlace && myPlace.code));
  head.appendChild(btn);
}

// ---------- документы бойца ----------
//
// Раздел заведён под то, что будет копиться: полис первый, за ним пойдут
// права из автошколы и всё прочее, что выдают конторы города. Поэтому
// рисуется список, а не «полис в карточке»: вёрстка одна на все бланки, и
// про число документов она ничего не знает.

const HERO_TABS = [
  ["stats", "Характеристики"],
  ["quests", "Квесты"],
  ["papers", "Документы"],
];
let heroTab = "stats";

function pickHeroTab(name) {
  heroTab = name;
  HERO_TABS.forEach(([code]) => {
    el("hero-" + code).classList.toggle("hidden", code !== name);
  });
  // Журнал заданий перечитываем при каждом заходе: бармен мог принять
  // работу, пока вкладка была закрыта
  if (name === "quests") loadQuests();
  paintHeroTabs();
}

/** Кнопка в ряду под бойцом. Одна вёрстка на все три.
 *
 * `dot` — точка сбоку: есть что забрать. Больше на этих кнопках ничего
 * не пишется: три слова в ряд читаются с одного взгляда, а значки и
 * счётчики рядом с ними превращали ряд в три разные надписи.
 */
function heroAct(label, on, onClick, dot) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "hero-act" + (on ? " on" : "");
  const text = document.createElement("span");
  text.className = "hero-act-text";
  text.textContent = label;
  btn.appendChild(text);
  if (dot) {
    const tag = document.createElement("span");
    tag.className = "hero-act-mark dot";
    btn.appendChild(tag);
  }
  btn.addEventListener("click", () => {
    haptic((feedback) => feedback.selectionChanged());
    onClick();
  });
  return btn;
}

function paintHeroTabs() {
  const tabs = el("hero-tabs");
  tabs.textContent = "";
  HERO_TABS.forEach(([code, label]) => {
    tabs.appendChild(heroAct(label, heroTab === code, () => pickHeroTab(code)));
  });
  // Награды — кнопка того же вида, но не раздел: она открывает окно и
  // потому никогда не горит выбранной. Нечего показывать — её нет вовсе
  if (dailyState) {
    const btn = heroAct(
      "Награды",
      false,
      openDaily,
      Boolean(dailyState.fresh || dailyState.waiting.length)
    );
    btn.id = "hero-daily";
    tabs.appendChild(btn);
  }
  // Четвёртая кнопка в ряд не встаёт: «Характеристики» обрезались бы
  // многоточием. Четыре — складываем в два этажа по две
  tabs.classList.toggle("folded", tabs.children.length > 3);
}

let papers = [];

function renderPapers(card) {
  // Документы приходят только на своей карточке: полис с чужим именем и
  // сроком — не то, что показывают сопернику
  papers = (card.is_self && card.documents) || [];
  const list = el("papers-list");
  list.textContent = "";
  papers.forEach((paper) => list.appendChild(paperCard(paper)));
  el("papers-note").textContent = papers.length
    ? ""
    : "Пока ни одного документа. Полис страхования жизни и здоровья "
      + "оформляют в страховой компании.";
  // Вкладки прячем на чужой карточке целиком: там второй раздел пустой
  // всегда, и вкладка без содержимого — это обещание, которого не будет
  el("hero-tabs").classList.toggle("hidden", !card.is_self);
  if (!card.is_self) pickHeroTab("stats");
  else paintHeroTabs();
}

/** Бланк документа: заголовок, поля и что он даёт. */
function paperCard(paper, bare) {
  const box = document.createElement("article");
  box.className = "paper" + (paper.active ? "" : " stale");

  const head = document.createElement("header");
  head.className = "paper-head";
  const title = document.createElement("h3");
  title.className = "paper-title";
  title.textContent = paper.emoji + " " + paper.title;
  const state = document.createElement("span");
  state.className = "paper-state";
  // Бланк говорит о себе сам: у карты вместо «срок вышел» стоит «не
  // обслуживается» — срока у неё нет вовсе, она бессрочна
  state.textContent = paper.state || (paper.active ? "Действует" : "Срок вышел");
  head.append(title, state);
  box.appendChild(head);

  const issuer = document.createElement("p");
  issuer.className = "paper-issuer";
  // Контора, номер и по какому праву документ на руках. Номер есть не у
  // всякого бланка: абонемент заводят на входе, а не выписывают, и
  // «№ » с пустотой после него выглядело бы потерянным полем
  issuer.textContent = [
    paper.issuer,
    paper.number ? "№ " + paper.number : "",
    paper.ground,
  ].filter(Boolean).join(" · ");
  box.appendChild(issuer);

  // Подписи полей приходят с сервером, а не зашиты здесь: у полиса
  // «Застрахован», у абонемента «Владелец», и бланк один на оба
  box.appendChild(
    paperRows([
      [paper.holder_title || "Застрахован", paper.holder],
      [paper.period_title || "Период страхования", paper.period],
      ["Покрытие", paper.covers],
    ])
  );

  const gives = document.createElement("ul");
  gives.className = "paper-gives";
  paper.gives.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    gives.appendChild(li);
  });
  box.appendChild(gives);

  if (paper.note) {
    const note = document.createElement("p");
    note.className = "paper-note";
    note.textContent = paper.note;
    box.appendChild(note);
  }

  // Полис просрочен — на бланке об этом сказано словами, а не только
  // серым цветом: цвет на светлой теме читается хуже, чем кажется
  if (!paper.active) {
    const dead = document.createElement("p");
    dead.className = "paper-dead";
    dead.textContent =
      "Срок вышел " + paper.until + ". Продлить можно в страховой компании.";
    box.appendChild(dead);
  }

  // На экране страховой бланк показывают вместе с кнопками; в документах
  // он сам по себе, и переключатель продления — единственное, что с ним
  // делают, не выходя из карточки. У полиса подписки переключать нечего:
  // им распоряжается не боец, а срок его PRO.
  //
  // И то и другое — дело полиса. Раньше условие стояло на одном
  // `switchable`, а его нет ни у абонемента, ни у карты, ни у прав: всем
  // троим бланк обещал подписку, которая их не держит, а правам ещё и
  // «до » с пустотой вместо срока
  if (!bare && paper.kind === "insurance") {
    box.appendChild(paper.switchable ? renewSwitch(paper) : proHeldNote(paper));
  }
  return box;
}

/** Полис держит подписка: сказать это словами вместо переключателя. */
function proHeldNote(paper) {
  const said = document.createElement("p");
  said.className = "paper-held";
  said.textContent =
    "💎 Полис держит подписка PRO — до " + paper.until
    + ". Оформлять и продлевать его не нужно.";
  return said;
}

function paperRows(pairs) {
  const list = document.createElement("ul");
  list.className = "rows paper-rows";
  // Пустое поле — это не поле: у диплома нет покрытия, и строка
  // «Покрытие» с пустотой после неё выглядела бы потерянной
  pairs
    .filter(([, value]) => value)
    .forEach(([label, value]) => list.appendChild(row(label, value)));
  return list;
}

/** Автопродление: одна кнопка, и она же говорит текущее состояние. */
function renewSwitch(paper) {
  const box = document.createElement("div");
  box.className = "paper-renew";
  const said = document.createElement("span");
  said.className = "paper-renew-state";
  said.textContent = paper.auto_renew
    ? "🔄 Автопродление включено"
    : "⏹ Автопродление выключено";
  box.appendChild(said);
  box.appendChild(
    button(paper.auto_renew ? "Отключить" : "Включить", {
      secondary: paper.auto_renew,
      onClick: () => policyAction({ action: "renew", on: !paper.auto_renew }),
    })
  );
  return box;
}

// ---------- страховая компания ----------

let insuranceData = null;
let insuranceBusy = false;

async function loadInsurance() {
  try {
    const response = await fetch("api/insurance", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("insurance-note").textContent = "Страховая не открылась.";
      return;
    }
    renderInsurance(await response.json());
  } catch (error) {
    el("insurance-note").textContent = error.message;
  }
}

async function policyAction(payload) {
  if (insuranceBusy) return;
  insuranceBusy = true;
  try {
    const response = await fetch("api/insurance", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Страховая", data.error || "Не получилось.");
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    // Полис лежит в документах, а деньги — в карточке: перерисовываем и её
    if (data.card) render(data.card, true);
    if (data.insurance) renderInsurance(data.insurance);
    if (data.said) popup("Страховая", data.said);
  } catch (error) {
    popup("Страховая", "Сервер не ответил.");
  } finally {
    insuranceBusy = false;
  }
}

function renderInsurance(data) {
  insuranceData = data;
  el("shop-purse-insurance").textContent = "";
  el("shop-purse-insurance").appendChild(purse(data.credits));
  el("insurance-note").textContent =
    data.said
    || (data.by_pro
      ? "Полис держит подписка — оформлять ничего не нужно."
      : data.insured
        ? "Полис на руках. Продлить можно в любой момент — месяц ляжет сверху."
        : "Первая услуга конторы: лечение травм по полису дешевле впятеро.");

  const body = el("insurance-body");
  body.textContent = "";
  body.appendChild(walletBar(data.purse, loadInsurance));
  // Полис на руках — показываем сам бланк: он же лежит в документах, и
  // это тот самый документ, а не его пересказ
  if (data.policy && data.policy.title) {
    body.appendChild(paperCard(data.policy));
  } else {
    body.appendChild(policyOffer(data));
  }
  body.appendChild(priceTable(data));
  body.appendChild(buyPolicyRow(data));
}

/** Полиса нет: что он такое и что даёт. */
function policyOffer(data) {
  const box = document.createElement("article");
  box.className = "paper offer";
  const title = document.createElement("h3");
  title.className = "paper-title";
  title.textContent = data.emoji + " " + data.title;
  const issuer = document.createElement("p");
  issuer.className = "paper-issuer";
  issuer.textContent = data.issuer;
  box.append(title, issuer);

  const gives = document.createElement("ul");
  gives.className = "paper-gives";
  data.gives.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    gives.appendChild(li);
  });
  box.appendChild(gives);

  const note = document.createElement("p");
  note.className = "paper-note";
  note.textContent = data.note;
  box.appendChild(note);
  return box;
}

/** Прайс больницы с полисом и без: проценты словами убеждают хуже чисел. */
function priceTable(data) {
  const box = document.createElement("section");
  box.className = "policy-prices";
  const head = document.createElement("h3");
  head.className = "policy-prices-head";
  head.textContent = "Лечение травм: без полиса и с ним";
  box.appendChild(head);
  data.prices.forEach((line) => {
    const item = document.createElement("div");
    item.className = "policy-price";
    const title = document.createElement("span");
    title.className = "policy-price-title";
    title.textContent = line.title.charAt(0).toUpperCase() + line.title.slice(1);
    const was = document.createElement("span");
    was.className = "policy-price-was";
    was.textContent = num(line.full) + " 💰";
    const now = document.createElement("span");
    now.className = "policy-price-now";
    now.textContent = num(line.price) + " 💰";
    item.append(title, was, now);
    box.appendChild(item);
  });
  return box;
}

/** Кнопка покупки. Подписчику рядом с нулём стоит зачёркнутая цена. */
function buyPolicyRow(data) {
  const box = document.createElement("div");
  box.className = "policy-buy";

  const price = document.createElement("div");
  price.className = "policy-buy-price";
  price.textContent = num(data.price) + " 💰 за " + data.days + " дней";
  box.appendChild(price);

  // Полис держит подписка — объясняем, за что тогда кнопка берёт деньги
  if (data.why) {
    const why = document.createElement("p");
    why.className = "policy-buy-why";
    why.textContent = data.why;
    box.appendChild(why);
  }

  box.appendChild(
    button(data.action, {
      // Цену с кнопки не снимаем, даже когда платить нечем: по ней и видно,
      // сколько не хватает
      disabled: !data.affordable,
      hint:
        "Не хватает кредитов: полис стоит " + num(data.price)
        + " 💰, а " + purseNote(data.purse) + ".",
      onClick: () => policyAction({ action: "buy" }),
    })
  );
  return box;
}

// ---------- рынок: обмен между бойцами ----------
//
// Стол один, а смотрят на него двое, и каждый со своей страницы. Отсюда
// два правила, которым здесь подчинено всё остальное.
//
// Первое: страница ничего не решает сама. Что лежит на столе, сколько
// там кредитов и нажаты ли кнопки — приходит с сервера целиком, и
// перерисовывается тоже целиком. Догадка вида «я только что выложил
// меч, значит он там» разошлась бы с чужой правкой в ту же секунду.
//
// Второе: перерисовываем только когда стол правда поменялся. У сервера
// для этого есть `version`, и по нему собирается подпись состояния:
// опрос идёт каждые две секунды, а в поле для кредитов в это время
// набирают число. Перерисовка на каждый опрос сбрасывала бы набранное.

let tradeData = null;
let tradeTimer = null;
let tradeBusy = false;
let tradeShown = "";
// Сколько секунд осталось приглашению — тикает на странице, а не
// приходит с сервера двадцать раз: до конца минуты опрос успевает
// пройти тридцать раз, и каждый его ответ перерисовывал бы экран
let tradeClock = null;
// Когда пришёл последний ответ: по нему и дотикивают секунды
let tradeAt = 0;
// Чем кончился прошлый обмен. Сервер говорит это один раз — второй
// опрос через две секунды пришёл бы уже без записки, и человек успевал
// прочитать полфразы. Поэтому держим её на странице до первого действия
let tradeDone = "";

/** Подпись состояния: по ней видно, надо ли перерисовывать. */
function tradeShape(data) {
  const table = data.trade || {};
  const sides = table.id
    ? [table.mine, table.his].map((side) => [
        side.credits,
        side.ready,
        side.items.map((one) => one.kind + one.key + "x" + one.count).join(","),
      ])
    : [];
  return JSON.stringify([
    data.credits,
    data.done,
    table.id || 0,
    table.version || 0,
    sides,
    // Секунды в подпись не идут: их дотикивает страница
    (data.invite && data.invite.from_id) || 0,
    (data.sent && data.sent.to_id) || 0,
    (data.crowd || []).map((one) => [one.user_id, one.online, one.callable]),
  ]);
}

async function loadTrade() {
  try {
    const response = await fetch("api/trade", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("trade-note").textContent = "Рынок не открылся.";
      return;
    }
    showTrade(await response.json());
  } catch (error) {
    el("trade-note").textContent = error.message;
  }
}

function showTrade(data) {
  tradeData = data;
  tradeAt = Date.now();
  if (data.done) tradeDone = data.done;
  // За новым столом прошлому обмену места нет
  if (data.trade && data.trade.id) tradeDone = "";
  el("shop-purse-trade").textContent = "";
  el("shop-purse-trade").appendChild(purse(data.credits));
  const shape = tradeShape(data);
  if (shape === tradeShown) {
    tickInvite();
    return;
  }
  tradeShown = shape;
  renderTrade(data);
}

/** Перерисовать, вернув палец туда, где он был. */
function renderTrade(data) {
  const was = document.activeElement;
  const spot = was && el("trade-body").contains(was) ? was.id : "";
  const body = el("trade-body");
  body.textContent = "";

  // Чем кончился прошлый обмен — первой строкой и один раз: сервер
  // отдаёт эту фразу единожды, дальше её на странице уже нет
  el("trade-note").textContent = tradeDone || tradeHint(data);
  if (data.trade && data.trade.id) {
    body.appendChild(tradeTable(data.trade));
  } else {
    if (data.invite && data.invite.from_id) body.appendChild(inviteBanner(data.invite));
    if (data.sent && data.sent.to_id) body.appendChild(sentBanner(data.sent));
    body.appendChild(tradeCrowd(data.crowd || []));
  }

  if (spot && el(spot)) {
    const back = el(spot);
    back.focus();
    if (back.setSelectionRange) {
      const end = String(back.value).length;
      try {
        back.setSelectionRange(end, end);
      } catch (error) {
        // Не всякое поле умеет каретку — и не всякому это нужно
      }
    }
  }
  tickInvite();
}

function tradeHint(data) {
  if (data.trade && data.trade.id) return "";
  if (data.invite && data.invite.from_id) return "Тебе предлагают обмен.";
  if (data.sent && data.sent.to_id) return "Ждём ответа.";
  if (!(data.crowd || []).length) {
    return "На рынке пусто. Обмен идёт из рук в руки — нужен второй.";
  }
  return "Позови бойца к столу — и он ответит в течение минуты.";
}

async function tradeAction(payload) {
  if (tradeBusy) return;
  tradeBusy = true;
  // Нажали — значит записку прочли
  tradeDone = "";
  tradeShown = "";
  try {
    const response = await fetch("api/trade", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Обмен", data.error || "Не получилось.");
      // Отказ мог случиться потому, что стол уже другой: перечитываем
      await loadTrade();
      return;
    }
    haptic((feedback) => feedback.impactOccurred("light"));
    showTrade(data);
  } catch (error) {
    popup("Обмен", "Сервер не ответил.");
  } finally {
    tradeBusy = false;
  }
}

// ---------- кто на рынке ----------

function tradeCrowd(crowd) {
  const box = document.createElement("div");
  box.className = "crowd";
  const online = crowd.filter((one) => one.online);
  const away = crowd.filter((one) => !one.online);
  // В сети — выше: с этим обмен состоится сейчас. Ушедшего звать можно,
  // но ответить он сможет, только пока приглашение живо
  if (online.length) box.appendChild(crowdGroup("🟢 В сети", online));
  if (away.length) box.appendChild(crowdGroup("Не в клубе", away));
  return box;
}

function crowdGroup(title, rows) {
  const box = document.createElement("section");
  box.className = "crowd-group";
  const head = document.createElement("h2");
  head.className = "crowd-head";
  head.textContent = title + " · " + rows.length;
  box.appendChild(head);
  rows.forEach((one) => box.appendChild(crowdRow(one)));
  return box;
}

function crowdRow(one) {
  const box = document.createElement("div");
  box.className = "fighter" + (one.online ? "" : " away");

  const face = document.createElement("span");
  face.className = "fighter-class";
  face.textContent = one.fclass.emoji;

  const name = document.createElement("span");
  name.className = "fighter-name";
  name.textContent = one.pro ? one.nickname + " 💎" : one.nickname;

  const level = document.createElement("span");
  level.className = "fighter-level";
  level.textContent = "[" + one.level + "]";

  box.append(face, name, level);
  if (one.callable) {
    box.appendChild(
      button("Обмен", {
        onClick: () => tradeAction({ action: "invite", user_id: one.user_id }),
      })
    );
  } else {
    const why = document.createElement("span");
    why.className = "crowd-busy";
    why.textContent = one.trading ? "Уже меняется" : "Занят";
    box.appendChild(why);
  }
  return box;
}

// ---------- приглашения ----------

function inviteBanner(invite) {
  const box = document.createElement("section");
  box.className = "invite";
  const text = document.createElement("p");
  text.className = "invite-text";
  text.textContent = invite.from_name + " предлагает обмен.";
  const clock = document.createElement("p");
  clock.className = "invite-clock";
  clock.id = "invite-clock";
  box.append(text, clock);

  const row = document.createElement("div");
  row.className = "invite-buttons";
  row.appendChild(button("Согласиться", { onClick: () => tradeAction({ action: "accept" }) }));
  row.appendChild(
    button("Отказаться", {
      secondary: true,
      onClick: () => tradeAction({ action: "decline" }),
    })
  );
  box.appendChild(row);
  return box;
}

function sentBanner(sent) {
  const box = document.createElement("section");
  box.className = "invite sent";
  const text = document.createElement("p");
  text.className = "invite-text";
  text.textContent = "Ждём ответа: " + sent.name + ".";
  const clock = document.createElement("p");
  clock.className = "invite-clock";
  clock.id = "invite-clock";
  box.append(text, clock);
  box.appendChild(
    button("Забрать приглашение", {
      secondary: true,
      onClick: () => tradeAction({ action: "withdraw" }),
    })
  );
  return box;
}

/** Секунды приглашения дотикиваются на месте, без нового запроса. */
function tickInvite() {
  const shown = el("invite-clock");
  if (!shown || !tradeData) return;
  const invite = (tradeData.invite && tradeData.invite.from_id)
    ? tradeData.invite
    : tradeData.sent;
  if (!invite) return;
  const left = Math.max(0, invite.seconds_left - Math.round(tradeAge() / 1000));
  shown.textContent = left
    ? "Осталось " + left + " " + plural(left, "секунда", "секунды", "секунд")
    : "Время вышло.";
}

function tradeAge() {
  return tradeAt ? Date.now() - tradeAt : 0;
}

// ---------- стол ----------

function tradeTable(table) {
  const box = document.createElement("section");
  box.className = "table";
  box.appendChild(tradeHalf(table.his, false, table));
  box.appendChild(tradeHalf(table.mine, true, table));
  box.appendChild(tradeButtons(table));
  return box;
}

function tradeHalf(side, own, table) {
  const box = document.createElement("div");
  box.className = "half" + (own ? " own" : "") + (side.ready ? " ready" : "");

  const head = document.createElement("div");
  head.className = "half-head";
  const who = document.createElement("span");
  who.className = "half-who";
  who.textContent = own ? "Ты отдаёшь" : side.nickname + " отдаёт";
  const mark = document.createElement("span");
  mark.className = "half-mark";
  mark.textContent = side.ready ? "✅ Готов" : "⏳ Думает";
  head.append(who, mark);
  box.appendChild(head);

  box.appendChild(own ? ownCredits(side, table) : theirCredits(side));

  const list = document.createElement("div");
  list.className = "half-items";
  if (!side.items.length) {
    const empty = document.createElement("p");
    empty.className = "half-empty";
    empty.textContent = own ? "Пока ничего не выложено." : "Пока ничего не выложил.";
    list.appendChild(empty);
  }
  side.items.forEach((one) => list.appendChild(tableItem(one, own)));
  box.appendChild(list);

  if (own) {
    box.appendChild(
      button("Выложить вещь", {
        secondary: true,
        onClick: () => openBasket(table),
      })
    );
  }
  return box;
}

function theirCredits(side) {
  const row = document.createElement("p");
  row.className = "half-credits";
  row.textContent = side.credits
    ? num(side.credits) + " 💰"
    : "Без кредитов";
  return row;
}

// Кредиты уходят на сервер по окончании набора, а не на каждую цифру:
// иначе «1000» успело бы отправиться как 1, 10 и 100, и каждое из них
// сбросило бы согласие соперника
function ownCredits(side, table) {
  const row = document.createElement("div");
  row.className = "half-credits own";
  const field = document.createElement("input");
  field.type = "number";
  field.id = "trade-credits";
  field.inputMode = "numeric";
  field.min = "0";
  field.max = String(table.max_credits);
  field.value = String(side.credits);
  field.addEventListener("change", () => {
    const amount = Math.max(0, Math.min(table.max_credits, Number(field.value) || 0));
    field.value = String(amount);
    if (amount !== side.credits) tradeAction({ action: "credits", credits: amount });
  });
  const tail = document.createElement("span");
  tail.className = "half-max";
  tail.textContent = "💰 из " + num(table.max_credits);
  row.append(field, tail);
  return row;
}

function tableItem(one, own) {
  const box = document.createElement("div");
  box.className = "lot";

  const pic = document.createElement("div");
  pic.className = "lot-pic";
  pic.appendChild(slotPicture(one, one.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "lot-body";
  const title = document.createElement("div");
  title.className = "lot-title";
  title.textContent = one.stack ? one.title + " ×" + one.count : one.title;
  body.appendChild(title);
  if (one.wear_text) {
    const wear = document.createElement("div");
    wear.className = "lot-wear";
    wear.textContent = one.wear_text;
    body.appendChild(wear);
  }
  box.appendChild(body);

  if (own) {
    const off = document.createElement("button");
    off.type = "button";
    off.className = "lot-off";
    off.textContent = "✕";
    off.title = "Убрать со стола";
    off.setAttribute("aria-label", "Убрать «" + one.title + "» со стола");
    off.addEventListener("click", () =>
      tradeAction({ action: "item", kind: one.kind, key: one.key, count: 0 })
    );
    box.appendChild(off);
  }
  return box;
}

function tradeButtons(table) {
  const row = document.createElement("div");
  row.className = "table-buttons";
  const mine = table.mine;
  if (mine.ready) {
    row.appendChild(
      button("Передумать", {
        secondary: true,
        onClick: () => tradeAction({ action: "unconfirm" }),
      })
    );
  } else {
    row.appendChild(
      button("Подтвердить", {
        disabled: mine.empty && table.his.empty,
        hint: "Пустой стол менять не на что.",
        onClick: () => tradeAction({ action: "confirm" }),
      })
    );
  }
  row.appendChild(
    button("Отказаться", {
      secondary: true,
      onClick: () => tradeAction({ action: "cancel" }),
    })
  );
  const note = document.createElement("p");
  note.className = "table-note";
  note.textContent = table.waiting
    ? "Ты готов. Ждём второго."
    : "Любая правка стола снимает оба согласия.";
  row.appendChild(note);
  return row;
}

// ---------- рюкзак у стола ----------
//
// Чужой рюкзак сюда не приходит вовсе: соперник видит выложенное, а не
// то, что у тебя есть.

function openBasket(table) {
  openSheet("Что выложить", "Не больше " + table.max_items + " предметов за раз");
  const list = el("sheet-list");
  if (!table.basket.length) {
    const empty = document.createElement("p");
    empty.className = "sheet-note";
    empty.textContent = "Рюкзак пуст. Надетое сначала снимают.";
    list.appendChild(empty);
    return;
  }
  table.basket.forEach((one) => list.appendChild(basketRow(one)));
}

function basketRow(one) {
  const box = document.createElement("div");
  box.className = "lot" + (one.on_table ? " on" : "");

  const pic = document.createElement("div");
  pic.className = "lot-pic";
  pic.appendChild(slotPicture(one, one.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "lot-body";
  const title = document.createElement("div");
  title.className = "lot-title";
  title.textContent = one.title;
  body.appendChild(title);
  const under = document.createElement("div");
  under.className = "lot-wear";
  under.textContent = one.stack
    ? "В рюкзаке: " + one.max_count + " шт."
    : one.wear_text;
  body.appendChild(under);
  box.appendChild(body);

  // У склянок вместо «выложить» — сколько штук: их передают числом, и
  // четыре разных эликсира не должны стоить четырёх мест на столе
  if (one.stack) {
    box.appendChild(stackPicker(one));
  } else {
    box.appendChild(
      button(one.on_table ? "Убрать" : "Выложить", {
        secondary: one.on_table,
        onClick: () => {
          closeSheet();
          tradeAction({
            action: "item",
            kind: one.kind,
            key: one.key,
            count: one.on_table ? 0 : 1,
          });
        },
      })
    );
  }
  return box;
}

function stackPicker(one) {
  const box = document.createElement("div");
  box.className = "stack";
  const field = document.createElement("input");
  field.type = "number";
  field.inputMode = "numeric";
  field.min = "0";
  field.max = String(one.max_count);
  field.value = String(one.count);
  field.className = "stack-count";
  box.appendChild(field);
  box.appendChild(
    button("На стол", {
      onClick: () => {
        const count = Math.max(0, Math.min(one.max_count, Number(field.value) || 0));
        closeSheet();
        tradeAction({ action: "item", kind: one.kind, key: one.key, count });
      },
    })
  );
  return box;
}

// ---------- опрос ----------
//
// Стол общий, и чужую правку видно только запросом: две секунды — то же
// сердцебиение, что на ринге.

function startTradeWatch() {
  loadTrade();
  if (tradeTimer) return;
  tradeTimer = setInterval(loadTrade, 2000);
  tradeClock = setInterval(tickInvite, 1000);
}

function stopTradeWatch() {
  if (tradeTimer) clearInterval(tradeTimer);
  if (tradeClock) clearInterval(tradeClock);
  tradeTimer = null;
  tradeClock = null;
}

// ---------- автошкола и участок ----------
//
// Один экран на весь путь: запись на курс, три дня учёбы, билет и то,
// чем он кончился. Какой шаг показывать, решает сервер — страница рисует
// тот, который ей назвали, и ничего не досчитывает сама. Верного ответа
// на странице нет вовсе: вопрос приходит по одному, ответ сверяет сервер.

let schoolData = null;
let schoolBusy = false;
let examTimer = null;
// Что выбрано в текущем вопросе. Подтверждают кнопкой: нажатие на
// вариант — это выбор, а не ответ, и передумать до подтверждения можно
let examPick = null;

async function loadSchool() {
  try {
    const response = await fetch("api/school", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("school-note").textContent = "Автошкола не открылась.";
      return;
    }
    renderSchool(await response.json());
  } catch (error) {
    el("school-note").textContent = error.message;
  }
}

async function schoolAction(payload) {
  if (schoolBusy) return;
  schoolBusy = true;
  try {
    const response = await fetch("api/school", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Автошкола", data.error || "Не получилось.");
      await loadSchool();
      return;
    }
    haptic((feedback) => feedback.impactOccurred("light"));
    if (data.card) render(data.card, true);
    examPick = null;
    renderSchool(data);
    // Итог экзамена говорим всплывашкой: это событие, а не строка на
    // экране, и пропустить его нельзя
    if (data.attempt && data.attempt.done) {
      popup(
        data.attempt.passed ? "Экзамен сдан" : "Экзамен не сдан",
        data.said
      );
    }
  } catch (error) {
    popup("Автошкола", "Сервер не ответил.");
  } finally {
    schoolBusy = false;
  }
}

function renderSchool(data) {
  schoolData = data;
  el("shop-purse-school").textContent = "";
  el("shop-purse-school").appendChild(purse(data.credits));
  el("school-note").textContent = data.said || data.note;

  const body = el("school-body");
  body.textContent = "";
  stopExamClock();

  // Идёт билет — на экране только он: уходить с половины экзамена
  // некуда, а доска с ценой курса под вопросом отвлекала бы
  if (data.stage === "exam" && data.question && data.question.text) {
    body.appendChild(examBox(data));
    startExamClock(data);
    return;
  }
  body.appendChild(courseBox(data));
  // Чем платить за курс — выбирают здесь же, как в лавке: за ним иначе
  // пришлось бы идти в банк и возвращаться
  if (data.stage === "new") body.appendChild(walletBar(data.purse, loadSchool));
}

/** Курс: цена, обратный отсчёт учёбы и запись на экзамен. */
function courseBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Курс на права";
  box.appendChild(head);

  const rows = [
    ["Курс", data.full_price + " 💰"],
    ["Самостоятельная учёба", data.study_days + " дня"],
    ["Экзамен", data.schedule],
    ["Билет", data.questions + " вопросов, " + data.minutes + " минут"],
    ["Ошибки", "прощается " + data.mistakes],
    // Цена пересдачи стоит на доске всегда, а не только у провалившего:
    // её называют до экзамена, а не после
    ["Пересдача", data.full_retake_price + " 💰, в следующий день приёма"],
  ];
  box.appendChild(sheetRows(rows));

  if (data.stage === "study") {
    const left = document.createElement("p");
    left.className = "screen-note";
    left.textContent =
      "До экзамена: " + studySpell(data.study_left) + " (с " + data.study_until + ")";
    box.appendChild(left);
  }
  if (data.stage === "ready" && !data.open) {
    const when = document.createElement("p");
    when.className = "screen-note";
    when.textContent = "Ближайший экзамен: " + data.next_exam;
    box.appendChild(when);
  }
  // Сидел за билетом в это же окно — ждать следующего: час следующего
  // приёма и считает сервер, страница его только показывает
  if (data.stage === "ready" && data.tried_today) {
    const again = document.createElement("p");
    again.className = "screen-note";
    again.textContent = "Пересдача: " + data.retake_at;
    box.appendChild(again);
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (data.stage === "new") {
    buttons.appendChild(
      button("Записаться · " + data.price + " 💰", {
        onClick: () => schoolAction({ action: "enroll" }),
      })
    );
  } else if (data.stage === "ready") {
    // Три состояния кнопки: открыто и можно, открыто но сегодня уже
    // сидел, закрыто вовсе. Цену пересдачи пишем прямо на кнопке —
    // платное действие не должно удивлять ценой после нажатия
    const shut = !data.open || data.tried_today;
    const title = data.tried_today
      ? "Сегодня уже сдавал"
      : !data.open
        ? "Экзамен закрыт"
        : data.retake
          ? "Пересдача · " + data.retake_price + " 💰"
          : "Записаться на экзамен";
    buttons.appendChild(
      button(title, {
        disabled: shut,
        hint: data.tried_today
          ? "Пересдача — в следующий день приёма: " + data.retake_at
          : "Экзамен принимают " + data.schedule,
        onClick: () => schoolAction({ action: "exam" }),
      })
    );
  } else if (data.stage === "passed") {
    const where = document.createElement("p");
    where.className = "screen-note";
    where.textContent =
      "Экзамен сдан " + data.passed_at + ". Права выдают в полицейском участке.";
    box.appendChild(where);
  }
  if (buttons.children.length) box.appendChild(buttons);
  return box;
}

/** Билет: вопрос, картинка, варианты и кнопка подтверждения. */
function examBox(data) {
  const one = data.question;
  const box = document.createElement("section");
  box.className = "exam";

  const head = document.createElement("div");
  head.className = "exam-head";
  const count = document.createElement("span");
  count.textContent = "Вопрос " + one.number + " из " + one.total;
  const clock = document.createElement("span");
  clock.id = "exam-clock";
  clock.className = "exam-clock";
  clock.textContent = clockText(data.seconds_left);
  head.append(count, clock);
  box.appendChild(head);

  // Ошибка на счету одна: показываем её сразу, а не в конце
  const mistakes = document.createElement("div");
  mistakes.className = "exam-wrong" + (data.wrong ? " on" : "");
  mistakes.textContent = data.wrong
    ? "Ошибка: " + data.wrong + " из " + (data.mistakes + 1)
    : "Ошибок нет";
  box.appendChild(mistakes);

  const text = document.createElement("div");
  text.className = "question-text";
  text.textContent = one.text;
  box.appendChild(text);

  if (one.image) {
    const pic = document.createElement("img");
    pic.className = "exam-pic";
    pic.src = one.image;
    pic.alt = "";
    box.appendChild(pic);
  }

  one.options.forEach((option, at) => {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "answer" + (examPick === at ? " on" : "");
    pick.textContent = option;
    pick.addEventListener("click", () => {
      examPick = at;
      renderSchool(schoolData);
    });
    box.appendChild(pick);
  });

  const row = document.createElement("div");
  row.className = "quiz-buttons";
  row.appendChild(
    button("Ответить", {
      disabled: examPick === null,
      hint: "Сначала выбери вариант.",
      onClick: () => schoolAction({ action: "answer", option: examPick }),
    })
  );
  box.appendChild(row);
  return box;
}

// «2 дня 4 часа» — для долгого ожидания: в часах трое суток читаются
// как «71 час», а человек ждёт дни
function studySpell(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const days = Math.floor(whole / 86400);
  const hours = Math.floor((whole % 86400) / 3600);
  const parts = [];
  if (days) parts.push(days + " " + plural(days, "день", "дня", "дней"));
  if (hours) parts.push(hours + " " + plural(hours, "час", "часа", "часов"));
  return parts.length ? parts.join(" ") : longSpell(whole);
}

function clockText(seconds) {
  const left = Math.max(0, seconds);
  const minutes = Math.floor(left / 60);
  return minutes + ":" + String(left % 60).padStart(2, "0");
}

// Часы билета идут на странице, а решает время сервер: истёкшая попытка
// отказывает в ответе, даже если стрелки на экране ещё не дошли
function startExamClock(data) {
  stopExamClock();
  let left = data.seconds_left;
  examTimer = setInterval(() => {
    left -= 1;
    const clock = el("exam-clock");
    if (!clock) {
      stopExamClock();
      return;
    }
    clock.textContent = clockText(left);
    if (left <= 0) {
      stopExamClock();
      loadSchool();
    }
  }, 1000);
}

function stopExamClock() {
  if (examTimer) clearInterval(examTimer);
  examTimer = null;
}

// ---------- бонусный пулл недели ----------

/** Полка бонусов: три дела, лестница у каждого и общий счёт.

    Одна на две стороны: у семьи и у города дела разные, а вид один —
    и рисовать его дважды значило бы однажды разойтись. Кто что
    считает, уже решено на сервере, здесь только числа. */
function bonusBox(bonus) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🏆 " + (bonus.title || "Бонусы недели");
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Неделя кончится " + bonus.ends_text + " — через " + bonus.left_text
    + ". Набежало " + num(bonus.pool) + " из " + num(bonus.top) + " 💰"
    + (bonus.to_vault ? " — уйдут в сейф семьи." : " — разойдутся по людям.");
  box.appendChild(note);

  (bonus.rows || []).forEach((row) => box.appendChild(bonusRow(row)));

  if (bonus.split) box.appendChild(splitBox(bonus.split));

  if (bonus.last) {
    const last = document.createElement("p");
    last.className = "thing-note";
    last.textContent =
      "За прошлую неделю пришло " + num(bonus.last.amount) + " 💰 ("
      + bonus.last.paid_text + ").";
    box.appendChild(last);
  }
  return box;
}

/** Одно дело: сколько сделано, на какой ступени и что дальше. */
function bonusRow(row) {
  const box = document.createElement("article");
  box.className = "bonus-row" + (row.step ? " earned" : "");

  const line = document.createElement("header");
  line.className = "bonus-head";
  const name = document.createElement("span");
  name.className = "bonus-name";
  name.textContent = row.title;
  const award = document.createElement("span");
  award.className = "bonus-award";
  award.textContent = row.award ? num(row.award) + " 💰" : "—";
  line.append(name, award);
  box.appendChild(line);

  const said = document.createElement("p");
  said.className = "bonus-note";
  said.textContent =
    "Сделано " + num(row.done) + " " + row.unit
    + (row.left
        ? " · до " + num(row.need) + " ещё " + num(row.left)
          + " (" + num(row.next_award) + " 💰)"
        : " · выше ступени нет");
  box.appendChild(said);

  // Лестница целиком: по ней видно не только где ты, но и куда идти
  const steps = document.createElement("div");
  steps.className = "bonus-steps";
  (row.steps || []).forEach((step, number) => {
    const one = document.createElement("span");
    const reached = number < row.step;
    one.className = "bonus-step"
      + (reached ? " done" : "")
      + (number === row.step - 1 ? " now" : "");
    one.textContent = "от " + num(step.need) + " · " + num(step.award);
    steps.appendChild(one);
  });
  box.appendChild(steps);
  return box;
}

/** Как участок делит пулл — строкой под делами. Читают все. */
function splitBox(split) {
  const box = document.createElement("div");
  box.className = "bonus-split";
  const said = document.createElement("p");
  said.className = "bonus-note";
  said.textContent = "Делим: " + split.title.toLowerCase() + ". " + split.note
    + (split.can_set ? " Поменять — в кабинете." : "");
  box.appendChild(said);
  return box;
}

/** Полка дележа в кабинете: слово начальника и две кнопки.

    Кнопки стоят здесь и только здесь: дележ — настройка, а не дело
    недели, и решают её заранее, в кабинете, а не на бегу в дежурной
    части. В сводке от неё остаётся строка о том, как делят сейчас. */
function splitShelf(bonus) {
  const split = bonus.split || {};
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Дележ премии";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Набежало за неделю " + num(bonus.pool) + " из " + num(bonus.top)
    + " 💰 — в понедельник уйдут сотрудникам сами. " + (split.note || "");
  box.appendChild(note);

  if (split.can_set) {
    const row = document.createElement("div");
    row.className = "bubbles";
    (split.options || []).forEach((one) => {
      row.appendChild(
        chip(one.title, one.mine, () => policeAction({
          action: "split", split: one.code,
        }))
      );
    });
    box.appendChild(row);
  }
  return box;
}

// ---------- участок: выдача прав ----------

let policeBusy = false;
let policeData = null;

async function loadPolice() {
  try {
    const response = await fetch("api/police", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("police-note").textContent = "Участок не открылся.";
      return;
    }
    renderPolice(await response.json());
  } catch (error) {
    el("police-note").textContent = error.message;
  }
}

async function takeLicence() {
  if (policeBusy) return;
  policeBusy = true;
  try {
    const response = await fetch("api/licence", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: "{}",
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Участок", data.error || "Не получилось.");
      await loadPolice();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderPolice(data);
    popup("🪪 Права получены", data.said);
  } catch (error) {
    popup("Участок", "Сервер не ответил.");
  } finally {
    policeBusy = false;
  }
}

// Какую половину участка смотрим: дежурную часть или кабинет
// начальника. У кабинета своя лента, как у вкладок семьи в особняке
let policeTab = "duty";

function pickPoliceTab(name) {
  policeTab = name;
  if (policeData) renderPolice(policeData);
}

function renderPolice(data) {
  policeData = data;
  el("police-note").textContent = data.said || data.note;
  const body = el("police-body");
  body.textContent = "";

  // Срок — первым делом: сидящему всё остальное пока не нужно
  if (data.jail && data.jail.left) body.appendChild(jailBox(data.jail));

  const office = data.office || {};
  if (!office.post) policeTab = "duty";
  // Лента вверху: дежурная часть, кабинет начальника и вход в войну.
  // Кабинет открывает должность, а не значок: в дежурную часть ходят
  // все сотрудники, за форму и казну отвечает один
  if (data.badge) {
    const tabs = document.createElement("div");
    tabs.className = "bubbles";
    tabs.id = "police-tabs";
    if (office.post) {
      tabs.appendChild(
        chip("🚓 Дежурная часть", policeTab === "duty",
             () => pickPoliceTab("duty"))
      );
      tabs.appendChild(
        chip("🗄 Кабинет", policeTab === "office",
             () => pickPoliceTab("office"))
      );
    }
    tabs.appendChild(chip("🚩 Территории", false, () => openTurf("police")));
    body.appendChild(tabs);
  }

  if (policeTab === "office" && office.post) {
    body.appendChild(officeBudgetBox(office));
    // В кабинете от пулла только дележ: сам счёт недели стоит в
    // дежурной части, и показывать его дважды незачем
    if (data.bonus && data.bonus.split) {
      body.appendChild(splitShelf(data.bonus));
    }
    body.appendChild(officeRackBox(office));
    body.appendChild(officeStoreBox(office));
    return;
  }

  // Свои дела — всем, сводка по городу — только при значке
  if ((data.mine || []).length) body.appendChild(myCasesBox(data));
  if (data.badge) {
    // Гараж участка — первым делом: машину берут и сдают, идя мимо, а
    // сводку читают сидя. Видят его все сотрудники: машины выдают им
    if ((data.garage || []).length) body.appendChild(stationGarageBox(data));
    body.appendChild(dutyWeekBox(data));
    // Бонусный пулл участка: зарабатывают его все вместе, и счёт
    // недели видит каждый сотрудник, а не только кресло
    if (data.bonus && data.bonus.rows) body.appendChild(bonusBox(data.bonus));
    if ((data.wanted || []).length) body.appendChild(wantedBox(data));
    body.appendChild(dutyBoardBox(data));
  }

  if ((data.jobs || []).length) body.appendChild(dutyJobsBox(data));

  if (data.licence && data.licence.title) {
    body.appendChild(paperCard(data.licence));
  }
  if (!data.can_take) return;

  const row = document.createElement("div");
  row.className = "thing-buttons";
  row.appendChild(button("Получить права", { onClick: () => takeLicence() }));
  body.appendChild(row);
}

/** Кабинет: казна города, норма недели и жалованье кресла. */
function officeBudgetBox(office) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Бюджет";
  box.appendChild(head);
  const week = office.week || {};
  box.appendChild(
    paperRows([
      ["В копилке", num(office.vault || 0) + " 💰"],
      ["На службе", String(office.officers || 0)],
      ["Задержаний", (week.arrests || 0) + " из " + (week.target || 0)],
      ["Премия за норму", num(week.bonus || 0) + " 💰"],
      ["Жалованье кресла", num(office.salary || 0) + " 💰 в неделю"],
    ])
  );
  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent = "Копилка делится поровну между всеми, кто на службе, "
    + "в понедельник. Норма — половина нападений за неделю.";
  box.appendChild(said);
  return box;
}

/** Кабинет: стойка с формой. Берёт её тот, кто в кресле. */
function officeRackBox(office) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Форма начальника · " + office.taken
    + " из " + (office.rack || []).length;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = "Форма числится за креслом, а не за тобой: продать, "
    + "переслать или спрятать её нельзя, а при уходе с должности она "
    + "вернётся на склад сама.";
  box.appendChild(note);

  (office.rack || []).forEach((one) => box.appendChild(rackRow(one)));

  if ((office.rack || []).some((one) => one.taken)) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Сдать всё", {
        secondary: true,
        onClick: () => policeAction({ action: "hand_kit" }),
      })
    );
    box.appendChild(buttons);
  }
  return box;
}

/** Строка стойки: вещь, где она сейчас и кнопка. */
function rackRow(one) {
  const row = document.createElement("article");
  row.className = "neighbour" + (one.taken ? "" : " away");
  const line = document.createElement("header");
  line.className = "neighbour-head";
  const name = document.createElement("span");
  name.className = "neighbour-name";
  if (one.picture) {
    const face = picture(one.picture, one.title, one.icon);
    face.className = "npc-face";
    name.appendChild(face);
  }
  name.appendChild(document.createTextNode(" " + one.title));
  const where = document.createElement("span");
  where.className = "neighbour-clan";
  where.textContent = one.where;
  line.append(name, where);
  row.appendChild(line);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (one.taken) {
    buttons.appendChild(
      button("Сдать", {
        secondary: true,
        onClick: () => policeAction({ action: "hand_kit", item: one.code }),
      })
    );
  } else {
    buttons.appendChild(
      button("Получить", {
        onClick: () => policeAction({ action: "take_kit", item: one.code }),
      })
    );
  }
  row.appendChild(buttons);
  return row;
}

/** Кабинет: склад на пятнадцать ячеек — тот же, что у семьи.

    Полки, лист вещи и выбор из рюкзака взяты у особняка целиком: склад
    и есть тот же склад, и рисовать его вторым способом значило бы
    рассказывать про износ и полки дважды. */
function officeStoreBox(office) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Склад кабинета · " + (office.store || []).length
    + " из " + office.slots;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = "Кабинет один на город: кладёт и забирает начальник. "
    + "Форму сюда не кладут — она и так числится за креслом.";
  box.appendChild(note);

  box.appendChild(
    shelfGrid({
      slots: office.slots,
      items: office.store || [],
      title: (one) => one.title + (one.by ? " · положил " + one.by : ""),
      onThing: (one) =>
        openThingSheet({
          thing: one,
          note:
            (one.by ? "Положил " + one.by : "Вещь кабинета")
            + (one.when ? " · " + one.when : ""),
          onTake: (thing) =>
            policeAction({ action: "unstash", stored: thing.id }),
        }),
      onFree: () =>
        openPickSheet({
          bag: office.bag || [],
          free: office.free,
          note: "Свободных ячеек: " + office.free,
          onPick: (one) => policeAction({ action: "stash", item_id: one.id }),
        }),
    })
  );
  return box;
}

/** Служба в полиции: три ступени, и берут на них здесь, а не в агентстве. */
function dutyJobsBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Служба в полиции";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = "Форму выдают здесь: тест агентства заменила академия. "
    + "Начинают патрульным, дальше — по стажу.";
  box.appendChild(note);

  data.jobs.forEach((one) => {
    // Своя рамка, а не `.case`: дело в дежурной части и место на
    // службе — разные вещи, и общий класс однажды уже их перепутал
    const row = document.createElement("article");
    row.className = "duty" + (one.mine ? " mine" : "");
    const line = document.createElement("header");
    line.className = "case-head";
    const who = document.createElement("span");
    who.className = "case-who";
    who.textContent = one.emoji + " " + one.title + (one.mine ? " · твоё" : "");
    const pay = document.createElement("span");
    pay.className = "case-sum";
    pay.textContent = one.pay_text + "/нед";
    line.append(who, pay);
    row.appendChild(line);

    const said = document.createElement("p");
    said.className = "case-note";
    said.textContent = [
      one.months_text,
      one.limited
        ? "мест " + one.seats + ", занято " + one.taken
        : "мест сколько угодно",
      one.note,
    ].filter(Boolean).join(" · ");
    row.appendChild(said);

    if (one.appointed && !one.mine) {
      // Кресло начальника не занимают: подают заявку, а сажает мэр
      const buttons = document.createElement("div");
      buttons.className = "thing-buttons";
      buttons.appendChild(
        button(one.bid ? "Отозвать заявку" : "Подать заявку", {
          disabled: !one.can_bid && !one.bid,
          hint: one.lacks || "Сюда не подают.",
          onClick: () => policeAction({
            action: one.bid ? "unbid" : "bid", job: one.code,
          }),
        })
      );
      row.appendChild(buttons);
    } else if (one.can_take) {
      const buttons = document.createElement("div");
      buttons.className = "thing-buttons";
      buttons.appendChild(
        button("Устроиться", {
          onClick: () => policeAction({ action: "hire", job: one.code }),
        })
      );
      row.appendChild(buttons);
    } else if (one.lacks && !one.mine) {
      const why = document.createElement("p");
      why.className = "case-note";
      why.textContent = one.lacks;
      row.appendChild(why);
    }
    box.appendChild(row);
  });
  return box;
}

async function policeAction(payload) {
  if (policeBusy) return;
  policeBusy = true;
  try {
    const data = await post("api/police", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    renderPolice(data);
  } catch (error) {
    popup("Участок", error.message);
    await loadPolice();
  } finally {
    policeBusy = false;
  }
}

/** Срок: сколько сидеть и в который раз взяли. */
function jailBox(term) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🚔 Ты сидишь";
  box.appendChild(head);
  const said = document.createElement("p");
  said.className = "screen-note";
  said.textContent = "До освобождения " + clockText(term.left)
    + ". Отсидка " + term.times + "-я. Из тюрьмы не выходят.";
  box.appendChild(said);
  return box;
}

/** Свои дела: по какому написать заявление и где забрать деньги. */
function myCasesBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Твои дела";
  box.appendChild(head);

  if (data.owed) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Забрать " + num(data.owed) + " 💰", {
        onClick: () => policeAction({ action: "collect" }),
      })
    );
    box.appendChild(buttons);
  }
  data.mine.forEach((one) => box.appendChild(caseCard(one, true)));
  return box;
}

function caseCard(one, mine) {
  const box = document.createElement("article");
  box.className = "case" + (one.arrested ? " closed" : "");

  const head = document.createElement("header");
  head.className = "case-head";
  const who = document.createElement("span");
  who.className = "case-who";
  // Названия домов лежат в именительном: «нападение в бар» читалось бы
  // хуже, чем дом отдельной строкой ниже
  who.textContent = mine ? "Нападение" : one.suspect + " → " + one.victim;
  const sum = document.createElement("span");
  sum.className = "case-sum";
  sum.textContent = one.loot ? "−" + num(one.loot) + " 💰" : "без потерь";
  head.append(who, sum);
  box.appendChild(head);

  const said = document.createElement("p");
  said.className = "case-note";
  said.textContent = [
    one.at,
    one.place_title,
    one.arrested ? "задержан" : one.filed ? "в розыске" : "без заявления",
    one.repaid ? "деньги возвращены" : "",
  ].filter(Boolean).join(" · ");
  box.appendChild(said);

  if (one.can_file) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Написать заявление", {
        onClick: () => policeAction({ action: "report", case: one.id }),
      })
    );
    box.appendChild(buttons);
  }
  return box;
}

/** Норма недели и копилка участка. */
function dutyWeekBox(data) {
  const week = data.week || {};
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Дежурная часть";
  box.appendChild(head);
  box.appendChild(
    paperRows([
      ["Нападений за неделю", String(week.attacks || 0)],
      ["Задержаний", (week.arrests || 0) + " из " + (week.target || 0)],
      ["В копилке", num(data.vault || 0) + " 💰"],
      ["На службе", String(data.officers || 0)],
    ])
  );
  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent = week.done
    ? "Норма выполнена: к копилке добавится " + num(week.bonus || 0)
      + " 💰, и всё разделится поровну в понедельник."
    : "Норма — половина нападений за неделю. Выполните — к копилке "
      + num(week.bonus || 0) + " 💰, и она разделится поровну.";
  box.appendChild(said);
  return box;
}

/** Розыск: по кому выписан ордер. */
function wantedBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "В розыске";
  box.appendChild(head);
  data.wanted.forEach((one) => {
    const row = document.createElement("article");
    row.className = "case wanted";
    const line = document.createElement("header");
    line.className = "case-head";
    const who = document.createElement("span");
    who.className = "case-who";
    who.textContent = "🔎 " + one.name;
    const sum = document.createElement("span");
    sum.className = "case-sum";
    sum.textContent = num(one.loot) + " 💰";
    line.append(who, sum);
    row.appendChild(line);
    const said = document.createElement("p");
    said.className = "case-note";
    said.textContent = "Дел: " + one.cases
      + ". Задержать можно там, где он стоит.";
    row.appendChild(said);
    box.appendChild(row);
  });
  return box;
}

/** Все нападения города: сводка, которую видит только значок. */
function dutyBoardBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Нападения";
  box.appendChild(head);
  if (!data.cases.length) {
    const said = document.createElement("p");
    said.className = "screen-note";
    said.textContent = "В городе тихо.";
    box.appendChild(said);
    return box;
  }
  data.cases.forEach((one) => box.appendChild(caseCard(one, false)));
  return box;
}

// ---------- вид изнутри ----------
//
// С карты у дома видно одну дверь, а всё остальное время боец проводит
// внутри. Картинку вешает не вёрстка, а локация: один экран обслуживает
// по несколько домов — клуб и казино, пять разных прилавков, — и что
// показывать, знает только то место, где боец сейчас стоит.

// ---------- мафия: особняк и своя семья ----------
//
// Два экрана и одно окно. Особняк — про весь город: завести клан,
// посмотреть чужие, постучаться. Вкладка «Клан» — про свою семью:
// чины, заявки и кнопки старших. Окно — про приглашение: его показывают
// при входе в игру, потому что позвали однажды, и ответить надо один раз.
//
// Страница здесь ничего не решает и ничего не досчитывает: кого можно
// выгнать, кого назначить и сколько осталось мест — всё приходит с
// сервера готовым. Спрятанная кнопка обходится запросом мимо интерфейса,
// поэтому прятать её — не защита, а вежливость.

let mafiaBusy = false;
let clanBusy = false;
// Особняк в два колена: список кланов города и открытый клан. `mafiaOpen`
// — номер открытого клана, ноль значит список. Внутри клана свои вкладки
let mafiaOpen = 0;
let clanTab = "family";
const CLAN_TABS = [
  ["family", "👔 Состав"],
  ["estate", "💼 Имущество"],
  ["settings", "⚙️ Настройки"],
];
// Последний ответ про свою семью и открыто ли её окно: состав правят из
// карточки, и после каждой кнопки окно надо перерисовать
let clanData = null;
let clanSheetOpen = false;
// Знак, выбранный в форме создания, и имя, если игрок его уже написал:
// подставлять имя знака поверх набранного нельзя
let signPicked = "";
let nameTyped = "";
// Последний ответ особняка: по нему перерисовывается решётка знаков
let mansionData = null;
// Какое приглашение показываем в окне: их может лежать несколько
let inviteShown = "";
// Своя семья: по ней зажигается вкладка. Чужая карточка её не трогает
let myClan = null;

/** Знак клана для строки — один на всю страницу.
 *
 * Берём его у того, что пришло с сервера: и у строки списка клуба, где
 * семья лежит отдельным полем, и у строки самого клана, где знак стоит
 * в ней же. Признак один на оба случая: есть картинка — есть и семья.
 */
function clanMarks(mark) {
  const marks = [];
  if (!mark || !mark.emblem) return marks;
  const name = mark.name || mark.clan || "";
  const sign = document.createElement("img");
  sign.className = "clan-sign";
  sign.src = mark.emblem;
  sign.alt = name || "Знак клана";
  sign.title = name ? "Клан «" + name + "»" : "Клан";
  marks.push(sign);
  return marks;
}

/** Значок службы: тот же размер и место, что у знака клана.
 *
 * Носить их разом нельзя — служивого в семью не пускают, мафиози на
 * службу не берут, — поэтому у ника всегда стоит либо одно, либо другое.
 */
function serviceMarks(service) {
  const marks = [];
  if (!service || !service.badge) return marks;
  const badge = document.createElement("img");
  badge.className = "clan-sign service-badge";
  badge.src = service.badge;
  badge.alt = service.title || "Значок службы";
  badge.title = service.title || "Служба";
  marks.push(badge);
  return marks;
}

/** Строка бойца в списках клана: класс, знаки, ник, премиум, уровень. */
function clanMemberRow(member, actions) {
  const box = document.createElement("div");
  box.className = "fighter clan-row" + (member.is_self ? " me" : "");

  const face = document.createElement("span");
  face.className = "fighter-class";
  face.textContent = member.fclass ? member.fclass.emoji : "";
  box.appendChild(face);
  clanMarks(member).forEach((mark) => box.appendChild(mark));

  const name = document.createElement("span");
  name.className = "fighter-name";
  name.textContent = member.pro
    ? member.nickname + " " + (member.pro_badge || "💎")
    : member.nickname;
  box.appendChild(name);

  const level = document.createElement("span");
  level.className = "fighter-level";
  level.textContent = "[" + member.level + "]";
  box.appendChild(level);

  if (actions && actions.length) {
    const tools = document.createElement("div");
    tools.className = "clan-tools";
    actions.forEach((one) => tools.appendChild(one));
    box.appendChild(tools);
  }
  return box;
}

// ---------- бар: бармен и его задания ----------
//
// Бармен стоит на картинке интерьера — png с прозрачным фоном, сам себе
// кнопка. Значок над ним говорит, есть ли дело, и по нему видно, стоит
// ли подходить: «!» — предлагает, часы — ждёт, галочка — принимает.
//
// Разговор собирает сервер: страница рисует его текст и кнопки ответов,
// не зная ни одного правила. Какую реплику показать, решает журнал
// заданий, а не браузер.

let barData = null;
let barBusy = false;
// Разговор раскрыт: у бармена, которому нечего сказать, он свёрнут в
// строку — бар всё-таки место, а не диалоговое окно
let barTalking = false;
// На какое дело разговор уже раскрывался сам. Нужно, чтобы «Отойти»
// держалось: без этого бармен с делом на руках раскрывал бы разговор
// заново на той же перерисовке, и отойти от него было бы нельзя
let barSeen = "";

async function loadBar() {
  // Заходим в бар заново — бармен заговаривает первым, даже если в
  // прошлый раз от него отошли
  barSeen = "";
  try {
    const response = await fetch("api/bar", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("tavern-note").textContent = "Бар не открылся.";
      return;
    }
    renderBar(await response.json());
  } catch (error) {
    el("tavern-note").textContent = error.message;
  }
}

async function barAction(payload) {
  if (barBusy) return;
  barBusy = true;
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/bar", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Бар", data.error || "Не получилось.");
      return;
    }
    // «Отойти» сворачивает разговор, всё прочее его продолжает
    barTalking = payload.action !== "bye";
    renderBar(data);
    if (data.card) render(data.card, true);
  } catch (error) {
    popup("Бар", error.message);
  } finally {
    barBusy = false;
  }
}

function renderBar(data) {
  barData = data;
  el("shop-purse-tavern").textContent = "";
  el("shop-purse-tavern").appendChild(purse(data.credits || 0));
  el("tavern-note").textContent =
    data.said || "Задания бармена: " + data.done + " из " + data.total + ".";

  paintBarman(data.npc || {});

  const body = el("tavern-body");
  body.textContent = "";
  // К бармену с делом подходят сами: разговор раскрыт, когда ему есть
  // что сказать, и свёрнут, когда он просто протирает стакан
  const kind = (data.talk || {}).kind;
  // Предложение, вопрос и приёмка раскрываются сами: бармен заговорил
  // первым, и прятать его слова за кнопкой «подойти» незачем. Но ровно
  // один раз на дело: отошёл — значит отошёл
  if (kind !== barSeen && ["offer", "quiz", "ready"].includes(kind)) {
    barTalking = true;
  }
  barSeen = kind;
  if (barTalking) body.appendChild(talkBox(data));
  else body.appendChild(approachBox(data));
  const current = (data.quests || []).find((one) => one.state === "taken");
  if (current) body.appendChild(taskBox(current));
}

/** Сам бармен: картинка поверх интерьера и значок дела над ней. */
function paintBarman(npc) {
  const box = el("tavern-npc");
  if (!box) return;
  const pic = el("tavern-npc-pic");
  if (npc.picture && pic.getAttribute("src") !== npc.picture) {
    pic.src = npc.picture;
    pic.alt = npc.name || "Бармен";
  }
  box.classList.toggle("hidden", !npc.picture);
  box.title = npc.hint || npc.name || "Бармен";
  const mark = el("tavern-npc-mark");
  mark.textContent = npc.mark || "";
  mark.classList.toggle("hidden", !npc.mark);
}

/** Свёрнутый разговор: одна строка и кнопка. */
function approachBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = (data.npc || {}).title || "Бармен";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = (data.npc || {}).hint || "Бармен не прочь поговорить.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Подойти к стойке", {
      onClick: () => {
        barTalking = true;
        renderBar(barData);
      },
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Сам разговор: реплика бармена и ответы бойца. */
function talkBox(data) {
  const talk = data.talk || {};
  const box = document.createElement("section");
  box.className = "talk";

  const who = document.createElement("div");
  who.className = "talk-who";
  who.textContent = (data.npc || {}).title || "Бармен";
  box.appendChild(who);

  const text = document.createElement("p");
  text.className = "talk-text";
  text.textContent = talk.text || "";
  box.appendChild(text);

  // Вопрос с номером: по нему видно, сколько ещё осталось
  if (talk.kind === "quiz") {
    const step = document.createElement("p");
    step.className = "talk-step";
    step.textContent = "Вопрос " + (talk.step + 1) + " из " + talk.total;
    box.appendChild(step);
  }
  if (talk.kind === "offer" && talk.quest) {
    box.appendChild(questPrize(talk.quest));
  }

  const replies = document.createElement("div");
  replies.className = "talk-replies";
  (talk.replies || []).forEach((one) => {
    replies.appendChild(
      button(one.text, {
        secondary: one.code === "bye" || one.code === "later",
        onClick: () => sayToBarman(one),
      })
    );
  });
  box.appendChild(replies);
  return box;
}

/** Что обещано за задание: строка под предложением. */
function questPrize(quest) {
  const note = document.createElement("p");
  note.className = "talk-prize";
  const prize = ["+" + quest.exp + " опыта"];
  if (quest.credits) prize.push("+" + num(quest.credits) + " 💰");
  note.textContent = quest.task + " · " + prize.join(", ");
  return note;
}

function sayToBarman(reply) {
  if (reply.code === "bye" || reply.code === "later") {
    barTalking = false;
    barAction({ action: reply.code });
    return;
  }
  barAction({
    action: reply.code,
    quest: reply.quest,
    step: reply.step,
    option: reply.option,
  });
}

/** Что сейчас на руках: задание одной карточкой. */
function taskBox(quest) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сейчас на руках";
  box.appendChild(head);
  box.appendChild(questCard(quest));
  return box;
}

/** Строка задания: значок состояния, название и что делать. */
function questCard(quest) {
  const box = document.createElement("div");
  box.className = "quest quest-" + quest.state + (quest.ready ? " ready" : "");

  const mark = document.createElement("span");
  mark.className = "quest-mark";
  mark.textContent = quest.mark || quest.number;
  box.appendChild(mark);

  const body = document.createElement("div");
  body.className = "quest-body";

  const title = document.createElement("div");
  title.className = "quest-title";
  title.textContent = quest.title;
  body.appendChild(title);

  const task = document.createElement("div");
  task.className = "quest-task";
  task.textContent = quest.task;
  body.appendChild(task);

  const prize = document.createElement("div");
  prize.className = "quest-prize";
  const parts = ["+" + quest.exp + " опыта"];
  if (quest.credits) parts.push("+" + num(quest.credits) + " 💰");
  if (quest.ready) parts.push("готово — к бармену");
  prize.textContent = parts.join(" · ");
  body.appendChild(prize);

  box.appendChild(body);
  return box;
}

// ---------- журнал заданий в карточке ----------

let questsData = null;

async function loadQuests() {
  try {
    const response = await fetch("api/quests", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) return;
    renderQuests(await response.json());
  } catch (error) {
    el("quests-note").textContent = error.message;
  }
}

function renderQuests(data) {
  questsData = data;
  el("quests-note").textContent =
    "Задания выдаёт " + (data.giver || "бармен") + ". Сдано " +
    data.done + " из " + data.total + ".";
  const list = el("quests-list");
  list.textContent = "";
  (data.quests || []).forEach((one) => list.appendChild(questCard(one)));
}

// Длинный отсчёт: от выдвижения до голосования трое суток, и `clockText`
// назвал бы их «4920:00». Единица тут одна, самая крупная из подходящих,
// — как `format_term` на сервере
function longClock(seconds) {
  const left = Math.max(0, seconds);
  if (left >= 86400) {
    const days = Math.round(left / 86400);
    return days + " " + plural(days, "день", "дня", "дней");
  }
  if (left >= 3600) {
    const hours = Math.floor(left / 3600);
    const minutes = Math.floor(left / 60) % 60;
    return hours + " ч" + (minutes ? " " + minutes + " мин" : "");
  }
  return clockText(left);
}

// ---------- выборы мэра ----------
//
// Два места и одно состояние. Экран администрации — про весь цикл:
// кто мэр, какая фаза, кто выдвинулся и с какой программой. Бюллетень —
// лист поверх всего: голосуют не в доме, а везде, и он приходит сам, с
// карточкой. Закрыл лист — в углу остаётся плашка с отсчётом.

let electionData = null;
let ballotState = null;
let ballotPick = 0;
let ballotClosed = false;
let voteTimer = null;

async function loadElections() {
  try {
    const response = await fetch("api/elections", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("elections-note").textContent = "Администрация не открылась.";
      return;
    }
    const data = await response.json();
    renderElections(data);
    setBallot(data.ballot);
  } catch (error) {
    el("elections-note").textContent = error.message;
  }
}

async function electionAction(payload) {
  try {
    const data = await post("api/elections", payload);
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderElections(data);
    setBallot(data.ballot);
    if (data.said) popup("Администрация", data.said);
  } catch (error) {
    popup("Администрация", error.message);
    await loadElections();
  }
}

function renderElections(data) {
  electionData = data;
  el("shop-purse-elections").textContent = "";
  el("shop-purse-elections").appendChild(purse((data.card || {}).credits || 0));
  el("elections-note").textContent = data.said || data.note || "";

  const body = el("elections-body");
  body.textContent = "";
  body.appendChild(cycleBox(data));
  if (data.can_nominate) body.appendChild(nominateBox(data));
  // Кресла начальников видит только мэр: назначает он, и список
  // кандидатов со всего города — его бумага, а не общая
  (data.seats || []).forEach((seat) => body.appendChild(seatBox(seat)));
  body.appendChild(candidatesBox(data));
}

/** Кресло начальника: кто сидит, кто подал и за сколько посадить.
 *
 * Жалованье тут не кнопка, а поле: вилка широкая, и называет число мэр.
 * Поле заполнено тем, что платят сейчас, — чаще всего менять его и не
 * нужно.
 */
function seatBox(seat) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = seat.emoji + " " + seat.title;
  box.appendChild(head);

  box.appendChild(
    paperRows([
      ["Дом", seat.place_title],
      ["Образование", seat.education],
      ["Вилка", seat.pay_text + " в неделю"],
      [
        "Сейчас в кресле",
        seat.holder.name
          ? seat.holder.name + " · " + num(seat.holder.salary) + " 💰"
          : "пусто",
      ],
    ])
  );

  const line = document.createElement("div");
  line.className = "sell-line";
  const pay = document.createElement("input");
  pay.className = "sell-price";
  pay.type = "number";
  pay.min = String(seat.pay_min);
  pay.max = String(seat.pay_max);
  pay.step = "100";
  pay.value = String(seat.holder.salary || seat.pay_min);
  const label = document.createElement("span");
  label.className = "thing-note";
  label.textContent = "💰 в неделю";
  line.append(pay, label);
  box.appendChild(line);

  const people = seat.candidates || [];
  if (!people.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent =
      "Назначать некого: заявок нет, и диплома такого ни у кого в городе нет.";
    box.appendChild(empty);
    return box;
  }

  const asked = people.some((one) => one.asked);
  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = asked
    ? "Подали заявку:"
    : "Заявок нет, поэтому город показывает всех с нужным дипломом:";
  box.appendChild(note);

  people.forEach((one) => {
    const row = document.createElement("div");
    row.className = "bracket-side";
    const name = document.createElement("span");
    name.className = "bracket-name";
    name.textContent = one.name + " · " + one.level + " ур. · "
      + one.months + " мес";
    row.appendChild(name);
    const take = button("Назначить", {
      disabled: Boolean(one.lacks),
      hint: one.lacks || "",
      onClick: () => electionAction({
        action: "appoint",
        job: seat.code,
        user: one.user_id,
        salary: Number(pay.value || seat.pay_min),
      }),
    });
    take.classList.add("btn-small");
    row.appendChild(take);
    box.appendChild(row);
  });
  return box;
}

/** Где сейчас цикл: фаза, отсчёт и кто в кресле. */
function cycleBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = data.phase_title;
  box.appendChild(head);
  box.appendChild(
    paperRows([
      ["Мэр города", data.mayor.name || "кресло пустует"],
      ["Кандидатов", String(data.total)],
      ["До конца этапа", longClock(data.seconds_left)],
    ])
  );
  return box;
}

/** Бланк выдвижения: программа и кнопка. Регистрация автоматическая. */
function nominateBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Выдвинуть кандидатуру";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = "Программу можно не писать — выдвинут и так. "
    + "Сядешь в тюрьму до субботы — снимут.";
  box.appendChild(note);

  const area = document.createElement("textarea");
  area.className = "program";
  area.id = "election-program";
  area.rows = 4;
  area.maxLength = data.program_max;
  area.placeholder = "Предвыборная программа (до " + data.program_max + " знаков)";
  box.appendChild(area);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Выдвинуться", {
      onClick: () =>
        electionAction({ action: "nominate", program: area.value }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Кандидаты с программами, а в подсчёт — ещё и с числом голосов. */
function candidatesBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Кандидаты";
  box.appendChild(head);
  if (!data.candidates.length) {
    const said = document.createElement("p");
    said.className = "screen-note";
    said.textContent = "Пока никого.";
    box.appendChild(said);
    return box;
  }
  data.candidates.forEach((one) => {
    const row = document.createElement("article");
    row.className = "candidate"
      + (one.user_id === data.mine ? " picked" : "")
      + (data.standing && one.user_id === (data.card || {}).user_id ? " mine" : "");
    const line = document.createElement("header");
    line.className = "candidate-head";
    const name = document.createElement("span");
    name.className = "candidate-name";
    name.textContent = (one.user_id === data.mine ? "✅ " : "") + one.name;
    line.appendChild(name);
    if (data.counting) {
      const votes = document.createElement("span");
      votes.className = "candidate-votes";
      votes.textContent = one.votes + " " + plural(
        one.votes, "голос", "голоса", "голосов"
      );
      line.appendChild(votes);
    }
    row.appendChild(line);
    if (one.program) {
      const said = document.createElement("p");
      said.className = "candidate-program";
      said.textContent = one.program;
      row.appendChild(said);
    }
    box.appendChild(row);
  });
  return box;
}

// ---------- бюллетень и плашка в углу ----------

function setBallot(ballot) {
  if (!ballot) return;
  const was = ballotState;
  ballotState = ballot;
  // Новое голосование — лист снова открываем: прошлый раз закрывали не
  // этот, а тот, которого уже нет
  if (!was || was.day !== ballot.day) {
    ballotClosed = false;
    ballotPick = 0;
  }
  paintVoteChip();
  if (ballot.shown && !ballotClosed) openBallot();
  else el("ballot-veil").classList.add("hidden");
}

function openBallot() {
  const ballot = ballotState;
  if (!ballot || !ballot.shown) return;
  ballotClosed = false;
  el("ballot-veil").classList.remove("hidden");
  el("ballot-head").textContent = "🗳 Выборы мэра";

  const list = el("ballot-list");
  list.textContent = "";
  const buttons = el("ballot-buttons");
  buttons.textContent = "";

  if (ballot.mine) {
    // Проголосовал — показываем отметку, а не второй бланк
    const chosen = ballot.candidates.find((one) => one.user_id === ballot.mine);
    el("ballot-note").textContent = "Твой голос отдан. Передумать нельзя.";
    const row = document.createElement("div");
    row.className = "ballot-row picked";
    row.textContent = "✅ " + ((chosen && chosen.name) || "кандидат");
    list.appendChild(row);
    buttons.appendChild(button("Закрыть", { onClick: () => closeBallot() }));
    return;
  }

  el("ballot-note").textContent =
    "Голос один, передумать нельзя. До конца голосования "
    + longClock(ballot.seconds_left) + ".";
  ballot.candidates.forEach((one) => {
    const row = document.createElement("label");
    row.className = "ballot-row" + (ballotPick === one.user_id ? " on" : "");
    const dot = document.createElement("input");
    dot.type = "radio";
    dot.name = "ballot";
    dot.value = String(one.user_id);
    dot.checked = ballotPick === one.user_id;
    dot.addEventListener("change", () => {
      ballotPick = one.user_id;
      openBallot();
    });
    const text = document.createElement("span");
    text.textContent = one.name;
    row.append(dot, text);
    list.appendChild(row);
  });

  buttons.appendChild(
    button("Проголосовать", {
      disabled: !ballotPick,
      hint: "Сначала выбери кандидата.",
      onClick: () => electionAction({ action: "vote", user: ballotPick }),
    })
  );
  buttons.appendChild(button("Позже", { onClick: () => closeBallot() }));
}

function closeBallot() {
  ballotClosed = true;
  el("ballot-veil").classList.add("hidden");
  paintVoteChip();
}

// Плашка в правом нижнем углу: идут выборы и сколько ещё идти. Висит
// поверх любого экрана — голосование не ждёт, пока на него посмотрят
function paintVoteChip() {
  const chip = el("vote-chip");
  const ballot = ballotState;
  const shown = Boolean(ballot && ballot.shown);
  chip.classList.toggle("hidden", !shown);
  if (!shown) return;
  chip.classList.toggle("done", Boolean(ballot.mine));
  chip.textContent = (ballot.mine ? "🗳 Голос отдан · " : "🗳 Идут выборы · ")
    + longClock(ballot.seconds_left);
}

function startVoteClock() {
  if (voteTimer) return;
  voteTimer = setInterval(() => {
    if (!ballotState || !ballotState.shown) return;
    ballotState.seconds_left -= 1;
    if (ballotState.seconds_left <= 0) {
      // Время вышло: что теперь с выборами, знает сервер
      loadBallot();
      return;
    }
    paintVoteChip();
  }, 1000);
}

async function loadBallot() {
  try {
    const response = await fetch("api/elections", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) return;
    const data = await response.json();
    setBallot(data.ballot);
  } catch (error) {
    // Молча: плашка в углу не повод показывать окно с ошибкой
  }
}

// ---------- турнирная арена: недельный плей-офф ----------

let arenaData = null;
let arenaTimer = null;
let arenaClock = null;

async function openArena() {
  showTab("arena");
}

function armArenaPoll() {
  stopArenaPoll();
  // Круги идут по часам, и развязку чужого боя приносит тот же ответ:
  // пока на арену смотрят — спрашиваем раз в пять секунд
  arenaTimer = setInterval(loadArena, 5000);
  arenaClock = setInterval(tickArena, 1000);
}

function stopArenaPoll() {
  if (arenaTimer) clearInterval(arenaTimer);
  if (arenaClock) clearInterval(arenaClock);
  arenaTimer = null;
  arenaClock = null;
}

async function loadArena() {
  try {
    const response = await fetch("api/arena", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Арена не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderArena(data);
  } catch (error) {
    el("arena-note").textContent = error.message;
  }
}

async function arenaAction(payload) {
  try {
    const data = await post("api/arena", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    renderArena(data);
  } catch (error) {
    popup("Арена", error.message);
    await loadArena();
  }
}

function renderArena(data) {
  arenaData = data;
  el("arena-note").textContent = data.said || data.note || "";
  el("shop-purse-arena").textContent = "";
  el("shop-purse-arena").appendChild(purse(data.credits || 0));
  const body = el("arena-body");
  body.textContent = "";

  body.appendChild(arenaHeadBox(data));
  // Свой бой — первым делом: на него выходят, и у выхода есть срок
  if (data.my_match) body.appendChild(myMatchBox(data));
  if ((data.rounds || []).length) body.appendChild(bracketBoard(data));
  else if ((data.players || []).length) body.appendChild(signedBox(data));
}

/** Шапка: пора, часы, призовой фонд и кнопка записи. */
function arenaHeadBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🏆 " + data.phase_title;
  box.appendChild(head);

  const rows = [
    ["Взнос", num(data.entry) + " 💰"],
    ["Записались", String(data.signed)],
    ["Призовой фонд", num(data.pot) + " 💰 · клуб добавляет "
      + data.club_share + "%"],
    ["Победителю", num(data.prizes.winner) + " 💰"],
    ["Второму месту", num(data.prizes.runner) + " 💰"],
  ];
  if (data.until > 0) {
    rows.push([arenaWhen(data), fullClock(Math.max(0, data.until - nowSec()))]);
  }
  box.appendChild(paperRows(rows));

  if (data.phase === "open") {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button(data.joined ? "Ты в списке" : "Записаться · " + num(data.entry) + " 💰", {
        disabled: !data.can_join,
        hint: data.joined
          ? "Ты уже записан: жди гонга."
          : "Не хватает кредитов на взнос.",
        onClick: () => arenaAction({ action: "join" }),
      })
    );
    box.appendChild(buttons);
  }
  return box;
}

function arenaWhen(data) {
  if (data.phase === "open") return "Запись закроется через";
  if (data.phase === "seeding") return "Первый бой через";
  if (data.phase === "done") return "Следующая запись через";
  return "Круг идёт";
}

/** Свой бой: соперник, часы выхода и кнопка. */
function myMatchBox(data) {
  const match = data.my_match;
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "⚔️ Твой бой";
  box.appendChild(head);

  const you = match.red && match.red.you ? match.red : match.blue;
  const rival = match.red && match.red.you ? match.blue : match.red;
  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent = rival
    ? "Соперник: " + rival.name + " · " + rival.level + " ур."
    : "Соперника нет — проходишь дальше без боя.";
  box.appendChild(said);

  if (!rival) return box;
  if (match.state === "live") {
    const live = document.createElement("p");
    live.className = "thing-note";
    live.textContent = "Бой идёт: вкладка «Клуб», раздел «Бои».";
    box.appendChild(live);
    return box;
  }

  const clock = document.createElement("div");
  clock.className = "shift-clock arena-clock";
  clock.dataset.until = String(nowSec() + (match.left || 0));
  clock.textContent = fullClock(match.left || 0);
  box.appendChild(clock);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = (you && you.ready)
    ? "Ты вышел. Ждём соперника: не выйдет — проходишь ты."
    : "Выйти на бой нужно за " + Math.round(match.confirm / 60)
      + " мин: не вышел — проходит соперник.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("🥊 Выйти на бой", {
      disabled: Boolean(you && you.ready) || !match.left,
      hint: you && you.ready ? "Ты уже вышел." : "Время вышло.",
      onClick: () => arenaAction({ action: "ready" }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Доска плей-офф: круг — колонка, слева направо, финал золотой.
 *
 * Прежде круги шли списком сверху вниз, и переход рисовался одним
 * цветом: дерево в ширину телефона не помещается. Оно и не помещается —
 * зато ездит вбок, и тогда видно главное, чего список не показывал:
 * откуда боец пришёл и куда ведёт его следующая линия.
 */
function bracketBoard(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🥊 Плей-офф";
  box.appendChild(head);

  const board = document.createElement("div");
  board.className = "bracket-board";
  // Финал — круг с одной парой, и неважно, который он по счёту:
  // в турнире на двоих финал и есть первый круг
  (data.rounds || []).forEach((round) => {
    board.appendChild(
      bracketColumn(round, round.matches.length === 1, data)
    );
  });
  box.appendChild(board);
  // Доска открывается там, где дело: на своей паре, а у доигранного
  // турнира — на финале. Иначе человек листал бы её вбок за собой
  requestAnimationFrame(() => showOnBoard(board, data));
  return box;
}

/** Подвинуть доску к своей паре, а у доигранного турнира — к финалу. */
function showOnBoard(board, data) {
  const mine = board.querySelector(".bracket.mine");
  const target = (mine || (data.champion
    ? board.querySelector(".bracket-col.final")
    : null));
  if (!target) return;
  const column = target.closest(".bracket-col");
  if (!column) return;
  const shift = column.getBoundingClientRect().left
    - board.getBoundingClientRect().left;
  board.scrollLeft = Math.max(0, board.scrollLeft + shift - 8);
}

/** Колонка круга: заголовок с часом и пары, растянутые поровну.
 *
 * Поровну — чтобы пара следующего круга сама встала против середины
 * своих двух. Тогда локти между колонками рисует рамка псевдоэлемента,
 * а не счёт пикселей в JS.
 */
function bracketColumn(round, last, data) {
  const col = document.createElement("div");
  col.className = "bracket-col" + (last ? " final" : "");

  const head = document.createElement("div");
  head.className = "bracket-col-head";
  const title = document.createElement("span");
  title.className = "bracket-col-title";
  title.textContent = round.title;
  const at = document.createElement("span");
  at.className = "bracket-col-at";
  at.textContent = round.at_text;
  head.append(title, at);
  col.appendChild(head);

  const cells = document.createElement("div");
  cells.className = "bracket-cells";
  round.matches.forEach((match) => {
    const cell = document.createElement("div");
    cell.className = "bracket-cell";
    // Кубок стоит над финальной парой: за него и дерутся
    if (last) {
      const cup = document.createElement("div");
      cup.className = "bracket-cup";
      cup.textContent = "🏆";
      cell.appendChild(cup);
    }
    cell.appendChild(matchCard(match));
    if (match.bye) {
      const bye = document.createElement("p");
      bye.className = "bracket-bye";
      bye.textContent = "Без боя";
      cell.appendChild(bye);
    }
    // Записка судьи — всё, кроме «без боя»: про это уже сказал ярлык
    if (match.note && !match.bye) {
      const note = document.createElement("p");
      note.className = "bracket-note";
      note.textContent = match.note;
      cell.appendChild(note);
    }
    if (last && data.champion) {
      const champ = document.createElement("p");
      champ.className = "bracket-champ";
      champ.textContent = "Победитель — " + data.champion.name;
      cell.appendChild(champ);
    }
    cells.appendChild(cell);
  });
  col.appendChild(cells);
  return col;
}

/** Пара: двое друг под другом, у каждого клетка исхода. */
function matchCard(match) {
  const card = document.createElement("article");
  card.className = "bracket" + (match.mine ? " mine" : "");
  [match.red, match.blue].forEach((side) => {
    card.appendChild(bracketSide(side, match));
  });
  return card;
}

/** Строка пары: имя и клетка — галка, крест или уровень. */
function bracketSide(side, match) {
  const line = document.createElement("div");
  line.className = "bracket-side"
    + (side && side.won ? " won" : "")
    + (side && side.out ? " out" : "")
    + (side && side.you ? " you" : "");
  const name = document.createElement("span");
  name.className = "bracket-name";
  name.textContent = side
    ? side.name + (side.you ? " · ты" : "")
    : (match.bye ? "Нет соперника" : "—");
  const mark = document.createElement("span");
  mark.className = "bracket-mark";
  // Пока пара не сыграна, в клетке стоит уровень и цвета нет: судить
  // до боя нечего, а знать, кто сильнее на бумаге, — полезно
  if (!side) {
    mark.textContent = match.bye ? "—" : "·";
    mark.title = match.bye ? "соперника нет" : "круга ещё не было";
  } else if (side.won) {
    mark.textContent = "✔";
    mark.title = "прошёл";
  } else if (side.out) {
    mark.textContent = "✘";
    mark.title = "вылетел";
  } else {
    mark.textContent = String(side.level);
    mark.title = side.level + " ур.";
  }
  line.append(name, mark);
  return line;
}

/** Записавшиеся, пока сетки ещё нет. */
function signedBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Записались · " + data.signed;
  box.appendChild(head);
  (data.players || []).forEach((one) => {
    const row = document.createElement("div");
    row.className = "bracket-side" + (one.you ? " you" : "");
    const name = document.createElement("span");
    name.className = "bracket-name";
    name.textContent = one.name + (one.you ? " · ты" : "");
    const mark = document.createElement("span");
    mark.className = "bracket-mark";
    mark.textContent = one.level + " ур.";
    row.append(name, mark);
    box.appendChild(row);
  });
  return box;
}

function nowSec() {
  return Math.floor(Date.now() / 1000);
}

function tickArena() {
  const node = document.querySelector("#arena .arena-clock");
  if (!node) return;
  const left = Math.max(0, Number(node.dataset.until || 0) - nowSec());
  node.textContent = fullClock(left);
  if (!left) loadArena();
}

// ---------- общежитие: комната студенту ----------
//
// Третье жильё города и первое бесплатное: комнату даёт учёба, а не
// кошелёк. Поэтому экран устроен вокруг одного вопроса — есть ли у тебя
// комната, — и всё остальное зависит от ответа.

let dormData = null;

async function openDorm() {
  showTab("dorm");
}

async function loadDorm() {
  try {
    const response = await fetch("api/dorm", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Общежитие не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderDorm(data);
  } catch (error) {
    el("dorm-note").textContent = error.message;
  }
}

async function dormAction(payload) {
  try {
    const data = await post("api/dorm", payload);
    haptic((feedback) => feedback.impactOccurred("light"));
    if (data.card) render(data.card, true);
    renderDorm(data);
  } catch (error) {
    popup("Общежитие", error.message);
    await loadDorm();
  }
}

function renderDorm(data) {
  dormData = data;
  el("dorm-note").textContent = data.said || data.note || "";
  el("shop-purse-dorm").textContent = "";
  el("shop-purse-dorm").appendChild(purse(data.credits || 0));

  const body = el("dorm-body");
  body.textContent = "";
  const room = data.room || {};
  if (!room.mine) {
    body.appendChild(dormOfferBox(data));
    return;
  }
  body.appendChild(dormRoomBox(data));
  if (room.closed) return;
  body.appendChild(dormVaultBox(data));
  body.appendChild(dormShelfBox(room));
}

/** Комнаты нет: кнопка для студента, записка для всех остальных. */
function dormOfferBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Комната в общежитии";
  box.appendChild(head);

  box.appendChild(
    paperRows([
      ["Плата", "бесплатно"],
      ["Срок", "пока учишься"],
      ["Мест под вещи", String(data.slots || 0)],
      ["Сейф", "в комнате"],
    ])
  );

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Получить комнату", {
      disabled: !data.can_take,
      hint: "Комнату дают студенту: сперва запишись на курс.",
      onClick: () => dormAction({ action: "room" }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Своя комната: номер, курс и — у запертой — выезд через администрацию. */
function dormRoomBox(data) {
  const room = data.room;
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = room.title.charAt(0).toUpperCase() + room.title.slice(1);
  if (room.closed) {
    const mark = document.createElement("span");
    mark.className = "shelf-count";
    mark.textContent = "заперта";
    head.appendChild(mark);
  }
  box.appendChild(head);

  box.appendChild(
    paperRows([
      ["Учёба", room.course_title],
      ["Выдана", room.given],
      [room.closed ? "Заперта" : "", room.closed ? room.closed_at : ""],
      ["Плата", "бесплатно"],
    ])
  );

  if (room.closed) {
    const note = document.createElement("p");
    note.className = "thing-note";
    note.textContent =
      "Администрация отдаёт всё разом: вещи в рюкзак, деньги из сейфа " +
      "наличными. После этого комнату можно получить снова — под новую учёбу.";
    box.appendChild(note);

    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Забрать у администрации", {
        onClick: () => dormAction({ action: "collect" }),
      })
    );
    box.appendChild(buttons);
  }
  return box;
}

/** Сейф комнаты: те же две кнопки, что у сейфа квартиры. */
function dormVaultBox(data) {
  const room = data.room;
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сейф комнаты";
  box.appendChild(head);
  box.appendChild(vaultSum(room.vault));

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Деньги в сейфе лежат отдельно от наличных. Учёба кончится — их " +
    "отдадут вместе с вещами, у администрации.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Внести", {
      onClick: () =>
        openMoneySheet({
          title: "Внести в сейф",
          note:
            "Из того кошелька, которым ты платишь здесь: " +
            num(spendable(data)) + " 💰",
          max: spendable(data),
          hint: "Платить нечем.",
          onSum: (amount) => dormAction({ action: "deposit", amount: amount }),
        }),
    })
  );
  buttons.appendChild(
    button("Снять", {
      secondary: true,
      disabled: !room.vault,
      hint: "Сейф пуст.",
      onClick: () =>
        openMoneySheet({
          title: "Снять из сейфа",
          note: "В сейфе " + num(room.vault) + " 💰. Снятое ляжет наличными",
          max: room.vault,
          hint: "Сейф пуст.",
          onSum: (amount) => dormAction({ action: "cash", amount: amount }),
        }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Полки комнаты: четыре места, те же клетки, что в кладовой квартиры. */
function dormShelfBox(room) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Полки · " + room.items.length + " из " + room.slots;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Кладут и забирают здесь же. Учёба кончится — забытое будет ждать " +
    "у администрации, а не пропадёт.";
  box.appendChild(note);

  box.appendChild(
    shelfGrid({
      id: "dorm-grid",
      slots: room.slots,
      items: room.items,
      title: (one) => one.title + (one.when ? " · положено " + one.when : ""),
      onThing: (one) =>
        openThingSheet({
          thing: one,
          note: one.when ? "Лежит с " + one.when : "Лежит в комнате",
          onTake: (thing) => dormAction({ action: "take", item: thing.id }),
        }),
      onFree: () =>
        openPickSheet({
          bag: room.bag,
          free: room.free,
          note: "Свободных мест: " + room.free,
          onPick: (one) => dormAction({ action: "stash", item: one.id }),
        }),
    })
  );
  return box;
}

// ---------- машины: салон, гараж службы и свои ключи ----------
//
// Экран один на три дома. В салоне на полке цены — сутки, месяц и
// покупка; в гараже участка и парка — места и те, кто в разъезде.
// Разница в том, что приходит в `shelf`, а не в том, какой это экран.

let carsData = null;

async function openCars() {
  showTab("cars");
}

async function loadCars() {
  try {
    const response = await fetch("api/cars", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Машины не отвечают.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderCars(data);
  } catch (error) {
    el("cars-note").textContent = error.message;
  }
}

async function carsAction(payload, where) {
  try {
    const data = await post(where || "api/cars", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    renderCars(data);
    if (data.said) popup("Машины", data.said);
  } catch (error) {
    popup("Машины", error.message);
    await loadCars();
  }
}

/** То же, но ручкой рынка: там торгуют не с городом, а друг с другом. */
function carMarketAction(payload) {
  return carsAction(payload, "api/used-cars");
}

function renderCars(data) {
  carsData = data;
  el("cars-title").textContent = (data.garage ? "🅿️ " : "🚗 ") + data.title;
  el("cars-note").textContent = data.said || data.note || "";
  el("shop-purse-cars").textContent = "";
  el("shop-purse-cars").appendChild(purse(data.credits || 0));

  const body = el("cars-body");
  body.textContent = "";
  // Рынок вместо витрины: в этом доме торгуют друг с другом
  if (data.market) {
    body.appendChild(marketBox(data.market));
    return;
  }
  const list = document.createElement("div");
  list.className = "shelf-list";
  (data.shelf || []).forEach((one) => {
    list.appendChild(data.garage ? garageCar(one) : showcaseCar(one));
  });
  body.appendChild(list);
}

// ---------- авторынок: машины из рук в руки ----------
//
// Три полки, и у каждой своя кнопка: чужое покупают, своё снимают с
// продажи, своё из гаража выставляют. Цену ставит продавец, но в рамках
// салона — считает их сервер, страница только показывает.

function marketBox(market) {
  const box = document.createElement("div");

  box.appendChild(
    carMarketShelf(
      "🚗 В продаже",
      market.lots,
      "Пока никто ничего не выставил.",
      (one) => marketLot(one, true)
    )
  );
  if ((market.mine || []).length) {
    box.appendChild(
      carMarketShelf("🏷 Моё на продаже", market.mine, "", (one) =>
        marketLot(one, false)
      )
    );
  }
  if ((market.offer || []).length) {
    box.appendChild(
      carMarketShelf("🔑 Можно выставить", market.offer, "", offerCar)
    );
  }
  return box;
}

function carMarketShelf(title, rows, empty, make) {
  const shelf = document.createElement("section");
  shelf.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = title;
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = String((rows || []).length);
  head.appendChild(count);
  shelf.appendChild(head);

  if (!(rows || []).length) {
    const none = document.createElement("p");
    none.className = "shelf-empty";
    none.textContent = empty;
    shelf.appendChild(none);
    return shelf;
  }
  const list = document.createElement("div");
  list.className = "shelf-list";
  rows.forEach((one) => list.appendChild(make(one)));
  shelf.appendChild(list);
  return shelf;
}

/** Лот: чужой покупают, свой снимают с продажи. */
function marketLot(one, theirs) {
  const box = carCard(one);
  box.appendChild(
    paperRows([
      ["Цена", num(one.price) + " 💰"],
      ["В салоне", num(one.salon) + " 💰"],
      [theirs ? "Продавец" : "Выставлена", theirs ? one.seller : one.listed],
    ])
  );
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    theirs
      ? button("Купить · " + num(one.price) + " 💰", {
          onClick: () => carMarketAction({ action: "buy", id: one.id }),
        })
      : button("Снять с продажи", {
          secondary: true,
          onClick: () => carMarketAction({ action: "withdraw", id: one.id }),
        })
  );
  box.appendChild(buttons);
  return box;
}

/** Своя машина из гаража: назвать цену и выставить. */
function offerCar(one) {
  const box = carCard(one);
  box.appendChild(
    paperRows([
      ["В салоне", num(one.salon) + " 💰"],
      ["Просить можно", one.hint],
    ])
  );
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Выставить на продажу", {
      onClick: () =>
        openMoneySheet({
          title: "Почём продаёшь?",
          note: one.title + ": " + one.hint,
          min: one.low,
          max: one.high,
          hint: "Цену держат в рамках салона.",
          onSum: (price) =>
            carMarketAction({ action: "sell", code: one.code, price: price }),
        }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Сама машина: картинка 16:9, скорость и подпись. */
function carPicture(car) {
  const box = document.createElement("div");
  box.className = "car-pic";
  if (!car.image) return box;
  const img = document.createElement("img");
  img.src = car.image;
  img.alt = car.title;
  img.addEventListener("error", () => {
    box.textContent = "🚗";
  });
  box.appendChild(img);
  return box;
}

function carHead(car) {
  const box = document.createElement("div");
  box.className = "car-head";
  const title = document.createElement("span");
  title.className = "car-title";
  title.textContent = car.title;
  const times = document.createElement("span");
  times.className = "car-times";
  // Скорость — главное и единственное свойство машины, и стоит она у
  // имени: по ней машины и выбирают
  times.textContent = "дорога " + car.times_text;
  box.append(title, times);
  return box;
}

function carCard(car) {
  const box = document.createElement("article");
  box.className = "car";
  box.append(carPicture(car), carHead(car));
  if (car.note) {
    const note = document.createElement("p");
    note.className = "thing-note";
    note.textContent = car.note;
    box.appendChild(note);
  }
  return box;
}

/** Машина на витрине салона: три цены и три кнопки. */
function showcaseCar(one) {
  const box = carCard(one);
  if (one.own) {
    const have = document.createElement("div");
    have.className = "thing-have";
    // Своя машина может стоять в гараже: без этой приписки «Твоя
    // насовсем» спорило бы с пустыми ключами в карточке
    have.textContent = one.parked_text
      ? "✓ Твоя насовсем · стоит: " + one.parked_text
      : "✓ Твоя насовсем";
    box.appendChild(have);
  } else if (one.rented) {
    const have = document.createElement("div");
    have.className = "thing-have";
    have.textContent = one.parked_text
      ? "Снята, осталось " + one.left_text + " · стоит: " + one.parked_text
      : "Снята, осталось " + one.left_text;
    box.appendChild(have);
  }
  box.appendChild(
    paperRows([
      ["Сутки", num(one.day) + " 💰"],
      ["Месяц", num(one.month) + " 💰 · " + one.month_days + " суток по цене дня"],
      ["Купить", num(one.price) + " 💰"],
    ])
  );
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  // Мест под машины нет — причина у кнопки одна на все три: не деньги,
  // а счёт машин, и говорить про деньги тут было бы неправдой
  const full = !one.fits;
  const why = full
    ? "Машин на руках столько, сколько держат. Поставь одну на парковку: "
      + "в особняке или у своей квартиры."
    : "";
  if (!one.own) {
    buttons.append(
      button("Сутки · " + num(one.day) + " 💰", {
        secondary: true,
        disabled: !one.can_rent_day,
        hint: why || "Не хватает денег на сутки.",
        onClick: () => carsAction({ action: "rent", code: one.code, span: "day" }),
      }),
      button("Месяц · " + num(one.month) + " 💰", {
        secondary: true,
        disabled: !one.can_rent_month,
        hint: why || "Не хватает денег на месяц.",
        onClick: () =>
          carsAction({ action: "rent", code: one.code, span: "month" }),
      }),
      button("Купить · " + num(one.price) + " 💰", {
        disabled: !one.can_buy,
        hint: why || "Не хватает денег на покупку.",
        onClick: () => carsAction({ action: "buy", code: one.code }),
      })
    );
  }
  if (one.rented) {
    buttons.appendChild(
      button("Сдать", {
        secondary: true,
        onClick: () => carsAction({ action: "give", code: one.code }),
      })
    );
  }
  if (buttons.childElementCount) box.appendChild(buttons);
  return box;
}

/** Машина в гараже службы: места, кто в разъезде и дадут ли тебе. */
function garageCar(one, act) {
  const press = act || carsAction;
  const box = carCard(one);
  const rows = [
    ["Мест в гараже", String(one.seats)],
    ["Свободно", String(one.free) + " из " + one.seats],
  ];
  if (one.price) rows.push(["За смену", num(one.price) + " 💰"]);
  if (one.holders.length) {
    rows.push([
      "В разъезде",
      one.holders.map((who) => (who.you ? "ты" : who.name)).join(", "),
    ]);
  }
  if (one.mine && one.left_text) rows.push(["У тебя до конца смены", one.left_text]);
  box.appendChild(paperRows(rows));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (one.mine) {
    buttons.appendChild(
      button("Сдать в гараж", {
        onClick: () => press({ action: "give", code: one.code }),
      })
    );
  } else {
    buttons.appendChild(
      button(one.price ? "Взять · " + num(one.price) + " 💰" : "Взять", {
        disabled: !one.can_take,
        hint: one.why || "Сейчас эту машину не дадут.",
        onClick: () => press({ action: "take", code: one.code }),
      })
    );
  }
  box.appendChild(buttons);
  if (one.why && !one.mine) {
    const why = document.createElement("p");
    why.className = "thing-note muted";
    why.textContent = one.why;
    box.appendChild(why);
  }
  return box;
}

// ---------- ключи: за руль садятся в карточке ----------
//
// Машина не вещь: в рюкзаке её нет, и слота у неё нет. Ключи лежат под
// куклой — там же, где машину и видно: поверх образа, снизу.

let keysData = [];

async function loadKeys() {
  try {
    const response = await fetch("api/keys", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) return;
    const data = await response.json();
    renderKeys(data.keys || [], data.limit || 0);
  } catch (error) {
    // Молча: ключи — не повод для окна с ошибкой
  }
}

async function keysAction(payload) {
  try {
    const data = await post("api/keys", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    renderKeys(data.keys || [], data.limit || 0);
    if (data.said) popup("Машина", data.said);
  } catch (error) {
    popup("Машина", error.message);
    await loadKeys();
  }
}

function renderKeys(keys, limit) {
  keysData = keys;
  const box = el("bag-keys");
  const list = el("keys-list");
  if (!box || !list) return;
  box.classList.toggle("hidden", keys.length === 0);
  // Счёт со потолком: по нему видно, что машина у бойца не одна из
  // многих, а одна из отведённых — и сколько их отведено
  el("keys-count").textContent = keys.length
    ? String(keys.length) + (limit ? " из " + limit : "")
    : "";
  list.textContent = "";
  keys.forEach((one) => list.appendChild(keyCard(one)));
}

function keyCard(one) {
  const box = carCard(one);
  if (one.driving) box.classList.add("driving");
  const rows = [
    ["Дорога короче", "в " + one.times + " раз"],
    [one.own ? "Твоя" : "Осталось", one.own ? "насовсем" : one.left_text],
    // Заряд — второе число машины после скорости: от него зависит, доедет
    // она вообще или встанет на полпути
    ["Заряд", one.charge + "% · " + one.km_left + " км"],
  ];
  if (one.charging) {
    rows.push(["На колонке", one.charging_slot + "-я · АЗС"]);
  }
  box.appendChild(paperRows(rows));
  box.appendChild(chargeBar(one.charge));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    one.driving
      ? button("Снять", {
          secondary: true,
          onClick: () => keysAction({ action: "park" }),
        })
      : button("За руль", {
          disabled: Boolean(one.empty || one.charging),
          hint: one.charging
            ? "Машина стоит на колонке АЗС. Сперва забери её оттуда."
            : "Заряд кончился — она не поедет. Заряжают на АЗС, у авторынка.",
          onClick: () => keysAction({ action: "wheel", code: one.code }),
        })
  );
  box.appendChild(buttons);
  return box;
}

// ---------- барбершоп и салон красоты: полка образов ----------
//
// Экран один, а домов два: в барбершопе мужские лица, в салоне женские.
// Смотрят полку все, берут с неё только своего пола — кнопка гаснет и
// говорит почему. Переключаются между своими в карточке, и там это
// бесплатно: торговли в карточке больше нет.

let barberData = null;

async function openBarber() {
  showTab("barber");
}

async function loadBarber() {
  try {
    const response = await fetch("api/barber", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Дом не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderBarber(data);
  } catch (error) {
    el("barber-note").textContent = error.message;
  }
}

async function barberAction(payload) {
  try {
    const data = await post("api/barber", payload);
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    renderBarber(data);
  } catch (error) {
    popup("Образы", error.message);
    await loadBarber();
  }
}

function renderBarber(data) {
  barberData = data;
  el("barber-title").textContent = data.title || "Образы";
  el("barber-note").textContent = data.said || data.note || "";
  el("shop-purse-barber").textContent = "";
  el("shop-purse-barber").appendChild(purse(data.credits || 0));

  const body = el("barber-body");
  body.textContent = "";
  const grid = document.createElement("div");
  grid.className = "look-grid";
  // Плитка та же, что в гардеробе, а нажатие другое: в карточке
  // образ надевают, здесь покупают
  (data.shelf || []).forEach((look) =>
    grid.appendChild(lookTile(look, buyLook))
  );
  body.appendChild(grid);
}

async function buyLook(look) {
  if (busy) return;
  if (!look.can_buy) {
    popup("Образы", look.why || "Этот образ взять нельзя.");
    return;
  }
  const ok = await confirmAction(
    "Купить образ «" + look.title + "» за " + num(look.price) + " кредитов?" +
      "\n\nОн станет твоим навсегда и сразу наденется. Переключаться " +
      "между своими можно в карточке, бесплатно."
  );
  if (!ok) return;
  busy = true;
  try {
    await barberAction({ action: "buy", code: look.code });
    shopData = null; // кредитов стало меньше
  } finally {
    busy = false;
  }
}

// ---------- школа боевых искусств: приём за дела в бою ----------
//
// Вкладок две, и они отвечают на разные вопросы. «Ваши приёмы» — что у
// бойца есть, «Обучение» — что он может взять и как идёт набор.
//
// Пока приём учится, каталог с экрана уходит: разом учат один, и
// держать перед глазами шестнадцать кнопок, из которых ни одна не
// нажимается, незачем. Набранный приём при этом место не занимает — он
// висит с кнопкой «Изучить» сколько угодно долго, и боец тем временем
// берётся за следующий.

let dojoData = null;
let dojoTab = "known";
// Фильтр каталога по цене в энергии. Ноль — все приёмы
let dojoCost = 0;
const DOJO_TABS = [
  ["known", "Ваши приёмы"],
  ["study", "Обучение"],
];

async function openDojo() {
  showTab("dojo");
}

async function loadDojo() {
  try {
    const response = await fetch("api/dojo", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Школа не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderDojo(data);
  } catch (error) {
    el("dojo-note").textContent = error.message;
  }
}

async function dojoAction(payload) {
  try {
    const data = await post("api/dojo", payload);
    haptic((feedback) => feedback.impactOccurred("light"));
    if (data.card) render(data.card, true);
    renderDojo(data);
  } catch (error) {
    popup("Школа", error.message);
    await loadDojo();
  }
}

function pickDojoTab(name) {
  dojoTab = name;
  if (dojoData) renderDojo(dojoData);
}

function renderDojo(data) {
  dojoData = data;
  el("dojo-note").textContent = data.said || data.note || "";

  const tabs = el("dojo-tabs");
  tabs.textContent = "";
  DOJO_TABS.forEach(([code, label]) => {
    tabs.appendChild(chip(label, dojoTab === code, () => pickDojoTab(code)));
  });

  const body = el("dojo-body");
  body.textContent = "";
  if (dojoTab === "known") {
    body.appendChild(dojoKnownBox(data));
    body.appendChild(dojoDeedsBox(data));
    return;
  }
  body.appendChild(dojoStudyBox(data));
}

/** Вкладка «Ваши приёмы»: что боец знает и почём они ему. */
function dojoKnownBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  const known = data.known || [];
  head.textContent = "Приёмы · " + known.length + " из " + (data.slots || 4);
  box.appendChild(head);
  if (!known.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent =
      "Приёмов пока нет: первый даёт класс, остальные — уровень и эта " +
      "школа.";
    box.appendChild(empty);
    return box;
  }
  known.forEach((one) => box.appendChild(dojoTrickCard(one)));
  return box;
}

/** Карточка приёма: картинка, цена в энергии и что он делает. */
function dojoTrickCard(one) {
  const card = skillCard(one);
  card.querySelector(".skill-cost").textContent =
    one.price + " ⚡ · приём " + one.tier + " ступени · " + one.branch_of;
  return card;
}

/** Ведомость: что боец сделал в боях за всю жизнь. */
function dojoDeedsBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сделано в боях";
  box.appendChild(head);
  box.appendChild(
    paperRows(
      (data.deeds || []).map((one) => [
        one.title + " · " + one.branch_title,
        num(one.count),
      ])
    )
  );
  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Считается сделанное самим бойцом в любых боях. Уворот от приёма и " +
    "крит от приёма в зачёт не идут: приём себя не кормит.";
  box.appendChild(note);
  return box;
}

/** Вкладка «Обучение»: набранное, идущее и каталог. */
function dojoStudyBox(data) {
  const box = document.createElement("div");
  (data.ready || []).forEach((one) => box.appendChild(dojoReadyCard(one)));
  if (data.started) {
    box.appendChild(dojoProgressCard(data.started));
    return box;
  }
  const shelf = data.shelf || [];
  if (!shelf.length) {
    if (!(data.ready || []).length) {
      const empty = document.createElement("p");
      empty.className = "thing-note";
      empty.textContent = "Учиться больше нечему: все приёмы клуба твои.";
      box.appendChild(empty);
    }
    return box;
  }
  box.appendChild(dojoFilter(data));
  box.appendChild(dojoShelf(data));
  return box;
}

/** Набранный приём: ждёт подтверждения столько, сколько нужно. */
function dojoReadyCard(one) {
  const box = document.createElement("section");
  box.className = "shelf dojo-ready";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Набрано: можно изучить";
  box.appendChild(head);
  box.appendChild(dojoTrickCard(one));
  box.appendChild(dojoBar(one.done, one.needed));
  box.appendChild(
    paperRows([
      [one.deed_title, one.done + " из " + one.needed],
      ["Забудется", one.forget ? "«" + one.forget.title + "»" : "ничего"],
    ])
  );
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Изучить", { onClick: () => dojoAskLearn(one) })
  );
  box.appendChild(buttons);
  return box;
}

/** Приём в обучении: шкала и сколько осталось. */
function dojoProgressCard(one) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "В обучении";
  box.appendChild(head);
  box.appendChild(dojoTrickCard(one));
  box.appendChild(dojoBar(one.done, one.needed));

  const count = document.createElement("div");
  count.className = "dojo-count";
  count.textContent =
    one.deed_title + " " + num(one.done) + "/" + num(one.needed);
  box.appendChild(count);

  box.appendChild(
    paperRows([
      ["Осталось", one.left + " " + one.left_name],
      ["Забудется", one.forget ? "«" + one.forget.title + "»" : "ничего"],
    ])
  );
  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Дела считаются в любых боях. Разом учат один приём — заходи и " +
    "смотри, как идёт.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Бросить", {
      secondary: true,
      onClick: () => dojoAskStop(one),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Фильтр каталога по цене в энергии. */
function dojoFilter(data) {
  const box = document.createElement("div");
  box.className = "bubbles";
  box.appendChild(chip("Все", !dojoCost, () => pickDojoCost(0)));
  (data.costs || []).forEach((cost) => {
    box.appendChild(
      chip(cost + " ⚡", dojoCost === cost, () => pickDojoCost(cost))
    );
  });
  return box;
}

function pickDojoCost(cost) {
  dojoCost = cost;
  if (dojoData) renderDojo(dojoData);
}

/** Каталог: компактные строки с кнопкой «+». */
function dojoShelf(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  const rows = (data.shelf || []).filter(
    (one) => !dojoCost || one.price === dojoCost
  );
  head.textContent = dojoCost
    ? "Приёмы по " + dojoCost + " ⚡ · " + rows.length
    : "Можно выучить · " + rows.length;
  box.appendChild(head);
  rows.forEach((one) => box.appendChild(dojoShelfLine(one)));
  return box;
}

/** Строка каталога: значок, имя, цена, описание и кнопка «+». */
function dojoShelfLine(one) {
  const row = document.createElement("div");
  row.className = "dojo-line";

  const pic = document.createElement("div");
  pic.className = "dojo-line-pic";
  pic.appendChild(slotPicture(one, one.icon));
  row.appendChild(pic);

  const body = document.createElement("div");
  body.className = "dojo-line-body";
  const title = document.createElement("div");
  title.className = "dojo-line-title";
  title.textContent = one.title;
  const cost = document.createElement("span");
  cost.className = "dojo-line-cost";
  cost.textContent = one.price + " ⚡";
  title.appendChild(cost);
  const note = document.createElement("div");
  note.className = "dojo-line-note";
  note.textContent = one.note;
  const price = document.createElement("div");
  price.className = "dojo-line-deed";
  price.textContent = num(one.needed) + " " + one.deed_name;
  body.append(title, note, price);
  row.appendChild(body);

  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "dojo-plus";
  plus.textContent = "+";
  plus.setAttribute("aria-label", "Взять в обучение");
  plus.addEventListener("click", () => dojoAskStart(one));
  row.appendChild(plus);
  return row;
}

/** Полоска набранного: число числом, а путь — на глаз. */
function dojoBar(done, total) {
  const bar = document.createElement("div");
  bar.className = "deed-bar";
  const fill = document.createElement("div");
  fill.className = "deed-fill";
  const share = total > 0 ? Math.min(100, (done * 100) / total) : 0;
  fill.style.width = share + "%";
  bar.appendChild(fill);
  return bar;
}

/** Окно «+»: что придётся делать и сколько раз. */
function dojoAskStart(one) {
  openSheet("Взять в обучение", "", true);
  const list = el("sheet-list");
  list.appendChild(dojoTrickCard(one));

  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent =
    "Чтобы выучить этот приём, вам нужно будет " + one.deed_act + " " +
    num(one.needed) + " раз. Дела считаются в любых боях.";
  list.appendChild(said);
  if (one.forget) {
    const warn = document.createElement("p");
    warn.className = "fork-warn";
    warn.textContent =
      "Когда приём будет изучен, «" + one.forget.title +
      "» забудется навсегда: он стоит столько же энергии.";
    list.appendChild(warn);
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.append(
    button("Начать изучать", {
      onClick: () => {
        closeSheet();
        dojoAction({ action: "start", code: one.code });
      },
    }),
    button("Отмена", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);
}

/** Окно «Изучить»: что уйдёт и что придёт — с картинками. */
function dojoAskLearn(one) {
  openSheet(one.title, "", true);
  const list = el("sheet-list");

  const swap = document.createElement("div");
  swap.className = "dojo-swap";
  if (one.forget) {
    swap.appendChild(dojoSwapSide(one.forget, "Забудется"));
    const arrow = document.createElement("div");
    arrow.className = "dojo-swap-arrow";
    arrow.textContent = "→";
    swap.appendChild(arrow);
  }
  swap.appendChild(dojoSwapSide(one, "Выучится"));
  list.appendChild(swap);

  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent = one.forget
    ? "«" + one.forget.title + "» забудется навсегда: место под приём с " +
      "этой ценой одно."
    : "Место под приём с этой ценой свободно — забывать нечего.";
  list.appendChild(said);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.append(
    button("Подтвердить", {
      onClick: () => {
        closeSheet();
        dojoAction({ action: "learn", code: one.code });
      },
    }),
    button("Отменить", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);
}

/** Одна сторона обмена: картинка приёма и подпись под ней. */
function dojoSwapSide(one, label) {
  const box = document.createElement("div");
  box.className = "dojo-swap-side";
  const tag = document.createElement("div");
  tag.className = "dojo-swap-tag";
  tag.textContent = label;
  const pic = document.createElement("div");
  pic.className = "skill-pic";
  pic.appendChild(slotPicture(one, one.icon));
  const title = document.createElement("div");
  title.className = "dojo-swap-title";
  title.textContent = one.title;
  const cost = document.createElement("div");
  cost.className = "dojo-line-cost";
  cost.textContent = one.price + " ⚡";
  box.append(tag, pic, title, cost);
  return box;
}

/** Окно «Бросить»: дела пропадут, и об этом надо сказать прямо. */
async function dojoAskStop(one) {
  const ok = await confirmAction(
    "Бросить обучение «" + one.title + "»?\n\nСделанные для него дела не " +
      "сохранятся."
  );
  if (!ok) return;
  await dojoAction({ action: "stop", code: one.code });
}

// ---------- АЗС: три колонки на весь город ----------
//
// Заряд тратит дорога, а наливают его только здесь. Экран устроен
// вокруг двух списков: колонки города сверху, свои машины снизу. В
// обе стороны ведёт одно и то же окно мерок — и из колонки выбирают
// машину, и из машины колонку, потому что бойцу бывает удобно и так и
// так: он то приехал заряжать конкретную машину, то увидел свободную
// колонку.

let chargeData = null;
let chargeTimer = null;
// Подпись под дозаряженной машиной. Её же ищут часы, чтобы не
// перерисовывать экран на каждом тике
const CHARGE_DONE = "можно забирать";

async function openCharge() {
  showTab("charge");
}

async function loadCharge() {
  try {
    const response = await fetch("api/charge", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("АЗС не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderCharge(data);
  } catch (error) {
    el("charge-note").textContent = error.message;
  }
}

async function chargeAction(payload) {
  try {
    const data = await post("api/charge", payload);
    haptic((feedback) => feedback.impactOccurred("light"));
    if (data.card) render(data.card, true);
    renderCharge(data);
  } catch (error) {
    popup("АЗС", error.message);
    await loadCharge();
  }
}

function renderCharge(data) {
  chargeData = data;
  el("charge-note").textContent = data.said || data.note || "";
  el("shop-purse-charge").textContent = "";
  el("shop-purse-charge").appendChild(purse(data.credits || 0));

  const body = el("charge-body");
  body.textContent = "";
  body.appendChild(chargeRow(data));
  // Чем платить — наличными или картой: цены на мерках считаются по
  // выбранному кошельку, и переключают его там же, где их видно
  body.appendChild(walletBar(data.purse, loadCharge));
  startChargeClock();
}

/** Три колонки в один ряд — станция и выглядит так. */
function chargeRow(data) {
  const row = document.createElement("div");
  row.className = "charge-row";
  (data.slots || []).forEach((one) => row.appendChild(chargeColumn(one)));
  return row;
}

/** Колонка: сама она, машина под ней и время под машиной.
 *
 * Вся колонка — кнопка: ряд из трёх, и отдельной кнопке под каждой
 * места в нём нет. Что случится от нажатия, написано тут же, под
 * картинкой, — свободна, занята, заряжается или можно забирать.
 */
function chargeColumn(one) {
  const box = document.createElement("button");
  box.type = "button";
  box.className =
    "charge-slot" +
    (one.busy ? " busy" : " free") +
    (one.mine ? " mine" : "") +
    (one.mine && one.ready ? " ready" : "");
  box.dataset.slot = String(one.slot);

  const pic = document.createElement("div");
  pic.className = "charge-pic";
  if (one.image) {
    const img = document.createElement("img");
    img.src = one.image;
    img.alt = one.slot + "-я колонка";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => {
      pic.textContent = "🔌";
    });
    pic.appendChild(img);
  } else {
    pic.textContent = "🔌";
  }
  box.appendChild(pic);

  // Машина встаёт под колонкой: пока идёт зарядка — с огоньком, как
  // только налилось — спокойно
  if (one.busy) {
    const car = document.createElement("div");
    car.className = "charge-car" + (one.ready ? " done" : " filling");
    if (one.car_image) {
      const img = document.createElement("img");
      img.src = one.car_image;
      img.alt = one.title;
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener("error", () => {
        img.replaceWith(document.createTextNode("🚗"));
      });
      car.appendChild(img);
    } else {
      car.textContent = "🚗";
    }
    const spark = document.createElement("span");
    spark.className = "charge-spark";
    spark.textContent = one.ready ? "✓" : "⚡";
    car.appendChild(spark);
    box.appendChild(car);
  }

  // Время — под машиной, отдельной строкой: его читают чаще всего
  // прочего на этом экране
  const clock = document.createElement("div");
  clock.className = "charge-clock";
  if (one.busy) {
    clock.dataset.until = String(Math.floor(Date.now() / 1000) + one.left);
    clock.textContent = one.ready ? CHARGE_DONE : fullClock(one.left);
  } else {
    clock.textContent = "свободна";
  }
  box.appendChild(clock);

  const who = document.createElement("div");
  who.className = "charge-who";
  who.textContent = one.busy
    ? (one.mine ? "+" + one.percent + "% · твоя" : one.owner)
    : one.slot + "-я колонка";
  box.appendChild(who);

  box.addEventListener("click", () => chargeTap(one));
  return box;
}

/** Нажатие на колонку: поставить, забрать или сказать, что занята. */
function chargeTap(one) {
  if (!one.busy) {
    chargeCarSheet(one.slot);
    return;
  }
  if (!one.mine) {
    popup(
      one.slot + "-я колонка занята",
      "Тут стоит чужая машина — " + one.title + ". Её заберёт хозяин."
    );
    return;
  }
  if (!one.ready) {
    popup(
      one.title,
      "Ещё заряжается: осталось " + fullClock(one.left) +
        ". Зарядка идёт и без тебя — вернись позже."
    );
    return;
  }
  chargeAction({ action: "collect", slot: one.slot });
}

/** Полоска заряда: число числом, а запас — на глаз. */
function chargeBar(percent) {
  const bar = document.createElement("div");
  bar.className = "charge-bar";
  const fill = document.createElement("div");
  fill.className =
    "charge-fill" + (percent <= 25 ? " low" : percent >= 75 ? " high" : "");
  fill.style.width = Math.max(0, Math.min(100, percent)) + "%";
  bar.appendChild(fill);
  return bar;
}

/** Окно выбора машины: картинки в два ряда, как в гараже.
 *
 * Заряжают только то, что при себе: машина, оставленная на парковке
 * дома, в этот список не приходит вовсе — за ней надо сходить.
 */
function chargeCarSheet(slot) {
  const data = chargeData || {};
  const cars = (data.cars || []).filter((one) => one.can_charge);
  openSheet(
    slot + "-я колонка",
    cars.length
      ? "Какую машину ставим?"
      : "Ставить нечего: к колонке подают то, что при себе, и только не " +
        "полную. Машину с парковки дома сюда не подать — за ней надо " +
        "сходить.",
    true
  );
  const list = el("sheet-list");
  const grid = document.createElement("div");
  grid.className = "charge-cars";
  cars.forEach((one) => grid.appendChild(chargeCarTile(one, slot)));
  list.appendChild(grid);
  list.appendChild(
    button("Отменить", { secondary: true, onClick: () => closeSheet() })
  );
}

/** Машина в окне выбора: картинка, имя и заряд. */
function chargeCarTile(one, slot) {
  const box = document.createElement("button");
  box.type = "button";
  box.className = "charge-car-tile";

  const pic = document.createElement("div");
  pic.className = "charge-car-pic";
  if (one.image) {
    const img = document.createElement("img");
    img.src = one.image;
    img.alt = one.title;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => {
      pic.textContent = "🚗";
    });
    pic.appendChild(img);
  } else {
    pic.textContent = "🚗";
  }
  box.appendChild(pic);

  const title = document.createElement("div");
  title.className = "charge-car-title";
  title.textContent = one.title;
  box.appendChild(title);

  box.appendChild(chargeBar(one.charge));
  const tag = document.createElement("div");
  tag.className = "charge-car-tag";
  tag.textContent = one.charge + "% · " + one.km_left + " км";
  box.appendChild(tag);

  box.addEventListener("click", () => {
    closeSheet();
    chargeStepSheet(one, slot);
  });
  return box;
}

/** Окно мерок: три квадрата в ряд, выбор и две кнопки внизу.
 *
 * Сколько прольётся мимо бака, написано на самой мерке: платить за
 * пролитое, не зная, что оно прольётся, обидно.
 */
function chargeStepSheet(car, slot) {
  const steps = car.steps || [];
  let picked = steps.length ? steps[0].percent : 0;
  openSheet(
    car.title + " · " + slot + "-я колонка",
    "Заряд " + car.charge + "%. Сверх полного бака заряд пропадает.",
    true
  );
  const list = el("sheet-list");

  const row = document.createElement("div");
  row.className = "charge-steps";
  const tiles = steps.map((step) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "charge-step" + (step.afford ? "" : " poor");
    tile.dataset.percent = String(step.percent);

    const head = document.createElement("div");
    head.className = "charge-step-head";
    head.textContent = "+" + step.percent + "%";
    const price = document.createElement("div");
    price.className = "charge-step-price";
    price.textContent = num(step.price) + " 💰";
    const time = document.createElement("div");
    time.className = "charge-step-time";
    time.textContent = step.time_text;
    tile.append(head, price, time);
    if (step.waste) {
      const spill = document.createElement("div");
      spill.className = "charge-step-waste";
      spill.textContent = "мимо " + step.waste + "%";
      tile.appendChild(spill);
    }

    tile.addEventListener("click", () => {
      if (!step.afford) {
        popup("Не хватает", "Столько кредитов у тебя нет.");
        return;
      }
      picked = step.percent;
      paint();
    });
    row.appendChild(tile);
    return tile;
  });
  list.appendChild(row);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  const go = button("Заряжать", {
    onClick: () => {
      closeSheet();
      chargeAction({
        action: "plug",
        code: car.code,
        slot: slot,
        percent: picked,
      });
    },
  });
  buttons.append(
    go,
    button("Отменить", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);

  function paint() {
    tiles.forEach((tile) => {
      tile.classList.toggle(
        "picked",
        Number(tile.dataset.percent) === picked
      );
    });
    const step = steps.find((one) => one.percent === picked);
    go.disabled = !step || !step.afford;
  }
  paint();
}

function startChargeClock() {
  stopChargeClock();
  chargeTimer = setInterval(tickCharge, 1000);
  tickCharge();
}

function stopChargeClock() {
  if (chargeTimer) clearInterval(chargeTimer);
  chargeTimer = null;
}

/** Часы под колонками: дошли до нуля — спрашиваем сервер заново.
 *
 * Часы есть только у занятых колонок, по ним и считаем: все три
 * свободны — тикать не над чем, и таймер останавливается сам.
 */
function tickCharge() {
  const clocks = document.querySelectorAll(
    "#charge-body .charge-clock[data-until]"
  );
  if (!clocks.length) {
    stopChargeClock();
    return;
  }
  const now = Math.floor(Date.now() / 1000);
  let ripe = false;
  clocks.forEach((clock) => {
    const left = Math.max(0, Number(clock.dataset.until || 0) - now);
    if (!left) {
      if (clock.textContent !== CHARGE_DONE) ripe = true;
      clock.textContent = CHARGE_DONE;
      return;
    }
    clock.textContent = fullClock(left);
  });
  // Забрать машину разрешает сервер, и узнать об этом можно только у
  // него: дотикали — перерисовываем экран целиком
  if (ripe) loadCharge();
}

// ---------- тюрьма: свой срок и те, кто сидит рядом ----------

let prisonData = null;
let prisonTimer = null;

async function openPrison() {
  showTab("prison");
  await loadPrison();
}

async function loadPrison() {
  try {
    const response = await fetch("api/prison", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Тюрьма не отвечает.");
    const data = await response.json();
    if (data.card) render(data.card, true);
    renderPrison(data);
  } catch (error) {
    el("prison-note").textContent = error.message;
  }
}

function renderPrison(data) {
  prisonData = data;
  el("prison-note").textContent = data.note || "";
  const body = el("prison-body");
  body.textContent = "";

  // Свой срок — первой полкой: сидящему всё остальное пока не важно
  if (data.mine) body.appendChild(myTermBox(data.mine));
  body.appendChild(inmatesBox(data));
  startPrisonClock();
}

/** Свой срок: часы идут на странице, а час выхода считает сервер. */
function myTermBox(term) {
  const box = document.createElement("section");
  box.className = "shift";
  const head = document.createElement("div");
  head.className = "shift-head";
  head.textContent = "Ты сидишь";
  const clock = document.createElement("div");
  clock.className = "shift-clock jail-clock";
  clock.dataset.until = String(term.until);
  clock.textContent = "Осталось " + fullClock(term.left);
  box.append(head, clock);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Отсидка " + term.times + "-я, срок " + term.hours + " ч. Выйдешь "
    + term.out_text + ". Из тюрьмы не выходят — ждут.";
  box.appendChild(note);
  return box;
}

/** Кто сидит сейчас: имя, который это раз и сколько осталось. */
function inmatesBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "За решёткой · " + (data.total || 0);
  box.appendChild(head);

  if (!(data.inmates || []).length) {
    const empty = document.createElement("p");
    empty.className = "shelf-empty";
    empty.textContent = "Камеры пусты.";
    box.appendChild(empty);
    return box;
  }

  (data.inmates || []).forEach((one) => {
    const row = document.createElement("article");
    row.className = "neighbour" + (one.mine ? " me" : "");
    const line = document.createElement("header");
    line.className = "neighbour-head";
    const name = document.createElement("span");
    name.className = "neighbour-name";
    name.textContent = one.name + (one.mine ? " · ты" : "");
    // Часы у каждого свои: час выхода приезжает с сервера, секунды
    // отсчитывает страница — как у сбора в рейд
    const left = document.createElement("span");
    left.className = "neighbour-clan jail-clock";
    left.dataset.until = String(one.until);
    left.textContent = fullClock(one.left);
    line.append(name, left);
    row.appendChild(line);

    const note = document.createElement("p");
    note.className = "neighbour-note";
    note.textContent =
      "Отсидка " + one.times + "-я, срок " + one.hours + " ч. Выйдет "
      + one.out_text + ".";
    row.appendChild(note);
    box.appendChild(row);
  });

  const ladder = document.createElement("p");
  ladder.className = "thing-note";
  ladder.textContent =
    "Срок растёт от числа отсидок: " + (data.terms || []).join(", ")
    + " ч, дальше столько же.";
  box.appendChild(ladder);
  return box;
}

function startPrisonClock() {
  stopPrisonClock();
  prisonTimer = setInterval(tickPrison, 1000);
  tickPrison();
}

function stopPrisonClock() {
  if (prisonTimer) clearInterval(prisonTimer);
  prisonTimer = null;
}

function tickPrison() {
  const nodes = document.querySelectorAll("#prison .jail-clock");
  if (!nodes.length) {
    stopPrisonClock();
    return;
  }
  const now = Math.floor(Date.now() / 1000);
  let over = false;
  nodes.forEach((node) => {
    const left = Math.max(0, Number(node.dataset.until || 0) - now);
    if (!left) over = true;
    const head = node.classList.contains("shift-clock") ? "Осталось " : "";
    node.textContent = head + fullClock(left);
  });
  // Чей-то срок вышел — спрашиваем сервер, кто ещё сидит: решать это
  // страница не должна
  if (over) loadPrison();
}

// ---------- кто здесь: соседи по дому и ограбления ----------
//
// Экран один на весь город, и дом у него тот, в котором боец стоит.
// Ответ сервера всегда полный: по нему видно, что рисовать — список
// соседей, идущую драку или её развязку. Опрашиваем часто, пока драка
// идёт, и редко, когда нет: список соседей меняется минутами, а раунд
// кончается секундами.

let crowdData = null;
let crowdTimer = null;
let crowdDraft = { attacks: {}, block: null };
let crowdRound = null;

async function loadCrowd() {
  try {
    const response = await fetch("api/crowd", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("crowd-note").textContent = "Дом не открылся.";
      return;
    }
    renderCrowd(await response.json());
  } catch (error) {
    el("crowd-note").textContent = error.message;
  }
}

async function crowdAction(payload) {
  try {
    const data = await post("api/crowd", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    renderCrowd(data);
    if (data.said) popup("Кто здесь", data.said);
  } catch (error) {
    popup("Кто здесь", error.message);
    await loadCrowd();
  }
}

function crowdPollRate(data) {
  return data && data.fight ? 2000 : 10000;
}

function armCrowdPoll(rate) {
  if (crowdTimer) clearInterval(crowdTimer);
  crowdTimer = setInterval(loadCrowd, rate);
}

function stopCrowdPoll() {
  if (crowdTimer) clearInterval(crowdTimer);
  crowdTimer = null;
}

function renderCrowd(data) {
  crowdData = data;
  // Новый раунд — намётки прошлого сбрасываем: ушедшее судье обратно
  // подсвечивать нечего
  const round = data.fight ? data.fight.id + ":" + data.fight.round : null;
  if (round !== crowdRound) {
    crowdRound = round;
    crowdDraft = { attacks: {}, block: null };
  }
  if (crowdTimer) armCrowdPoll(crowdPollRate(data));

  el("crowd-title").textContent = "👥 " + (data.place_title || "Кто здесь");
  el("shop-purse-crowd").textContent = "";
  el("shop-purse-crowd").appendChild(purse(data.credits || 0));
  el("crowd-note").textContent = data.said || data.note || "";

  const body = el("crowd-body");
  body.textContent = "";

  if (data.result) {
    body.appendChild(robberyBox(data.result));
    return;
  }
  if (data.fight) {
    body.appendChild(robberyBox(data.fight));
    return;
  }
  (data.fights || []).forEach((one) => body.appendChild(fightRowCard(one)));
  body.appendChild(crowdList(data));
}

/** Список соседей: кто в сети, чья семья и можно ли взять за карман. */
function crowdList(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "В этом доме";
  box.appendChild(head);
  if (!(data.crowd || []).length) {
    const said = document.createElement("p");
    said.className = "screen-note";
    said.textContent = "Пусто. Ты тут один.";
    box.appendChild(said);
    return box;
  }
  data.crowd.forEach((one) => box.appendChild(neighbourCard(one, data)));
  return box;
}

function neighbourCard(one, data) {
  const box = document.createElement("article");
  box.className = "neighbour" + (one.online ? "" : " away");

  const head = document.createElement("header");
  head.className = "neighbour-head";
  const name = document.createElement("span");
  name.className = "neighbour-name";
  // Розыск виден всем, не только значку: город знает, кого ищут
  name.textContent = (one.online ? "🟢 " : "⚪️ ") + (one.wanted ? "🔎 " : "")
    + one.name + " [" + one.level + "]";
  const where = document.createElement("span");
  where.className = "neighbour-clan";
  where.textContent = one.clan || "";
  head.append(name, where);
  box.appendChild(head);

  const said = document.createElement("p");
  said.className = "neighbour-note";
  said.textContent = one.online ? "В сети" : one.presence;
  box.appendChild(said);

  if (one.can_arrest) {
    // У значка своя кнопка: задержание не стоит лицензии
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("🚔 Задержать", {
        onClick: () => crowdAction({ action: "arrest", user: one.user_id }),
      })
    );
    box.appendChild(buttons);
  } else if (one.can_rob) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("💰 Ограбить", {
        onClick: () => crowdAction({ action: "rob", user: one.user_id }),
      })
    );
    box.appendChild(buttons);
  } else if (one.why) {
    // Почему нельзя — говорим, а не прячем кнопку молча
    const why = document.createElement("p");
    why.className = "neighbour-why";
    why.textContent = one.why;
    box.appendChild(why);
  }
  return box;
}

/** Идущая в доме драка: кто кого и можно ли вмешаться. */
function fightRowCard(one) {
  const box = document.createElement("article");
  box.className = "neighbour fight";

  const head = document.createElement("header");
  head.className = "neighbour-head";
  const name = document.createElement("span");
  name.className = "neighbour-name";
  name.textContent = (one.kind === "arrest" ? "🚔 " : "⚔️ ")
    + one.attacker + " → " + one.victim;
  const round = document.createElement("span");
  round.className = "neighbour-clan";
  round.textContent = "раунд " + one.round;
  head.append(name, round);
  box.appendChild(head);

  const said = document.createElement("p");
  said.className = "neighbour-note";
  said.textContent = one.sides.red.join(", ") + " — против — "
    + one.sides.blue.join(", ");
  box.appendChild(said);

  if (one.can_step_in) {
    // Полицейский встаёт за того, на кого напали, и без лицензии
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("🚔 Вмешаться", {
        onClick: () => crowdAction({ action: "step_in", raid_id: one.id }),
      })
    );
    box.appendChild(buttons);
  } else if (one.can_join) {
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Вмешаться " + one.side_title, {
        onClick: () => crowdAction({ action: "join", raid_id: one.id }),
      })
    );
    box.appendChild(buttons);
  } else if (one.why) {
    const why = document.createElement("p");
    why.className = "neighbour-why";
    why.textContent = one.why;
    box.appendChild(why);
  }
  return box;
}

/** Драка целиком: заголовок, доска, кнопки хода и журнал. */
function robberyBox(fight) {
  const box = document.createElement("div");
  box.className = "fight-panel";

  // Шапка как в групповом бою: кто на кого и какой раунд. Часы хода
  // тут же: кто не нажал, за того бьёт случай
  const head = document.createElement("p");
  head.className = "fight-round";
  head.textContent = fight.finished
    ? finishedTitle(fight)
    : (fight.kind === "arrest" ? "🚔 " : "⚔️ ")
      + fight.attacker + " → " + fight.victim + " — раунд " + fight.round;
  box.appendChild(head);

  const who = document.createElement("p");
  who.className = "fight-line";
  who.textContent = fight.finished
    ? fight.place_title + " · раундов: " + fight.round
    : fight.place_title
      + (fight.seconds_left ? " · ход " + clockText(fight.seconds_left) : "");
  box.appendChild(who);

  box.appendChild(robberyBoard(fight));
  box.appendChild(robberyFoot(fight));
  // Журнал внизу и всегда: ради него сюда и смотрят, когда свой ход уже
  // сделан, — и после драки он остаётся единственным, что было
  if ((fight.log || []).length) box.appendChild(robberyLog(fight));
  return box;
}

// Своя сторона слева, чужая справа — и так же у того, кто дерётся за
// другую. Смотрящему со стороны слева налётчики: драку начали они
function robberyBoard(fight) {
  const red = fight.red || [];
  const blue = fight.blue || [];
  const you = red.concat(blue).find((one) => one.is_self) || null;
  const ours = you && blue.indexOf(you) >= 0 ? blue : red;
  const theirs = ours === red ? blue : red;
  return brawlBoard(
    ours, theirs, you, fight.foe, Boolean(fight.yours) && !fight.finished
  );
}

function finishedTitle(fight) {
  if (fight.kind === "arrest") {
    return fight.won ? "🚔 Задержан" : "🏃 Ушёл";
  }
  return fight.won ? "💰 Карман взят" : "🛡 Отбились";
}

/** Что под доской: кнопки хода, строка со стороны или «Понятно». */
function robberyFoot(fight) {
  if (fight.finished) {
    const box = document.createElement("div");
    box.className = "fight-finish";
    (fight.summary || []).forEach((line) => {
      const said = document.createElement("p");
      said.className = "log-line";
      said.textContent = line;
      box.appendChild(said);
    });
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Понятно", { onClick: () => crowdAction({ action: "done" }) })
    );
    box.appendChild(buttons);
    return box;
  }
  if (!fight.yours || !fight.alive) {
    const said = document.createElement("p");
    said.className = "fight-line";
    said.textContent = fight.alive
      ? "Ты смотришь со стороны."
      : "Тебя вынесли — досматривай со стороны.";
    return said;
  }
  if (!fight.paired) {
    // Сторона больше другой: кому-то пары в этом раунде не досталось
    const said = document.createElement("p");
    said.className = "fight-line";
    said.textContent = "В этом раунде пары тебе не досталось. Ждём.";
    return said;
  }
  if (fight.acted) {
    // Строки «ход принят» тут нет нарочно: на доске у своей карточки
    // уже стоит галочка, и повторять то же самое словами незачем.
    // Остаются заготовки — по ним видно, что накоплено к размену
    return restingTricks(fight);
  }
  return robberyTurnForm(fight);
}

function robberyTurnForm(fight) {
  const box = document.createElement("div");
  box.className = "turn-form";
  const hands = fight.hands && fight.hands.length
    ? fight.hands
    : [{ hand: 0, icon: "👊", title: "Кулаки" }];

  if (fight.foe && fight.foe.alive) {
    const aim = document.createElement("p");
    aim.className = "raid-aim";
    aim.textContent = "Бьём: " + fight.foe.name
      + " [" + fight.foe.hp + "/" + fight.foe.max_hp + "]";
    box.appendChild(aim);
  }

  const tricks = abilityPanel(fight.abilities, (trick) =>
    useAbility("api/crowd", trick, renderCrowd)
  );
  if (tricks) box.appendChild(tricks);

  box.appendChild(
    zoneColumns(
      hands,
      fight.attacks,
      fight.blocks,
      () => crowdDraft,
      "crowd",
      paintCrowdDraft
    )
  );

  const go = document.createElement("button");
  go.type = "button";
  go.id = "crowd-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !crowdReady(hands);
  go.addEventListener("click", () => {
    const move = {
      action: "turn",
      attacks: crowdDraft.attacks,
      block: crowdDraft.block,
    };
    crowdDraft = { attacks: {}, block: null };
    crowdAction(move);
  });
  box.appendChild(go);
  return box;
}

function crowdReady(hands) {
  return Boolean(crowdDraft.block)
    && hands.every((row) => crowdDraft.attacks[row.hand]);
}

function paintCrowdDraft() {
  const go = el("crowd-go");
  if (!go || !crowdData || !crowdData.fight) return;
  const hands = crowdData.fight.hands && crowdData.fight.hands.length
    ? crowdData.fight.hands
    : [{ hand: 0 }];
  go.disabled = !crowdReady(hands);
}

/** Журнал драки словами судьи — тот же, что в рейде и на штурме. */
function robberyLog(fight) {
  const box = document.createElement("div");
  box.className = "fight-log";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ход драки";
  box.appendChild(head);
  fight.log.slice().reverse().forEach((turn) => judgeLines(turn, box, true));
  return box;
}

// Счётчик соседей в углу карты. Город большой, и это единственное
// место, откуда видно, что рядом кто-то есть
function paintCrowdChip() {
  const chip = el("map-crowd");
  const here = (mapData && mapData.crowd) || { total: 0, online: 0, fights: 0 };
  const going = Boolean(mapData && mapData.road && mapData.road.going);
  const shown = Boolean(here.total || here.fights) && !going;
  chip.classList.toggle("hidden", !shown);
  if (!shown) return;
  chip.textContent = here.fights
    ? "⚔️ " + here.fights + " · 👥 " + here.online
    : "👥 " + here.online + " из " + here.total;
  chip.classList.toggle("hot", Boolean(here.fights));
}

// ---------- война за территории ----------
//
// Один экран на всю войну, и что рисовать — решает не страница, а
// сервер: ответ всегда полный, и по нему видно, идёт ли сбор, волна
// или всё уже кончилось. Опрос как у рейда: пока что-то живое — две
// секунды, пусто — десять.

// Откуда зашли в войну: из особняка, из участка или с карты. Туда же
// и вернёт кнопка «назад» — иначе из территорий приходится выходить на
// карту и заново входить в дом, из которого пришёл
let turfFrom = "map";
const TURF_BACKS = {
  mafia: "← В особняк", police: "← В участок", map: "← На карту",
};

function openTurf(from) {
  turfFrom = TURF_BACKS[from] ? from : "map";
  el("turf-back").textContent = TURF_BACKS[turfFrom];
  showTab("turf");
}

let turfData = null;
let turfTimer = null;
let turfDraft = { attacks: {}, block: null };
let turfWave = null;

async function loadTurf() {
  try {
    const response = await fetch("api/turf", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("turf-note").textContent = "Карта войны не открылась.";
      return;
    }
    renderTurf(await response.json());
  } catch (error) {
    el("turf-note").textContent = error.message;
  }
}

async function turfAction(payload) {
  try {
    const data = await post("api/turf", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    // Без окна поверх боя: сказанное встаёт строкой под заголовком, как
    // в групповом бою. Окно посреди волны закрывает собой доску — и
    // закрывает её в тот самый миг, когда на неё и смотрят
    renderTurf(data);
  } catch (error) {
    popup("Территории", error.message);
    await loadTurf();
  }
}

/** Как часто опрашивать: идёт сбор или бой — часто, иначе редко. */
function turfPollRate(data) {
  return data && (data.muster || data.siege) ? 2000 : 10000;
}

function armTurfPoll(rate) {
  if (turfTimer) clearInterval(turfTimer);
  turfTimer = setInterval(loadTurf, rate);
}

function stopTurfPoll() {
  if (turfTimer) clearInterval(turfTimer);
  turfTimer = null;
}

function renderTurf(data) {
  turfData = data;
  // Новая волна — намётки прошлой сбрасываем: то, что ушло судье,
  // обратно подсвечивать нечего
  const wave = data.siege ? data.siege.id + ":" + data.siege.wave : null;
  if (wave !== turfWave) {
    turfWave = wave;
    turfDraft = { attacks: {}, block: null };
  }
  if (turfTimer) armTurfPoll(turfPollRate(data));

  el("shop-purse-turf").textContent = "";
  el("shop-purse-turf").appendChild(purse(data.credits || 0));
  el("turf-note").textContent = data.said || data.note || "";

  const body = el("turf-body");
  body.textContent = "";

  // Итог закрывают сами: пока его не прочли, он висит поверх всего.
  // Панель у идущего штурма и у итога одна: разница только в том, что
  // внизу — кнопки хода или «Понятно», а журнал нужен и там, и там
  if (data.result) {
    body.appendChild(siegeBox(data.result));
    // Взял точку — её надо держать: без выставленного она не платит. Тот,
    // кто вёл отряд, стоит ногами ровно на ней, и прилавок ему тут же:
    // иначе про охрану узнают через сутки, по пустой казне
    if (data.result.won && data.hire && data.hire.side) {
      body.appendChild(hireBox(data));
    }
    return;
  }
  if (data.siege) {
    body.appendChild(siegeBox(data.siege));
    return;
  }
  if (data.muster) body.appendChild(musterBox(data.muster));
  // Точка под ногами — первым делом: с её кнопки сюда и пришли, и
  // первое, что надо увидеть, — кто на ней стоит
  if (data.here_spot) body.appendChild(spotPanel(data.here_spot));
  body.appendChild(turfTallyBox(data));
  if (data.hire && data.hire.side) body.appendChild(hireBox(data));
  // Точка под ногами идёт первой: остальной список — сводка по доходу,
  // а кнопка сбора бывает только у неё, и искать её внизу незачем
  const spots = (data.spots || []).slice().sort(
    (a, b) => Number(b.here) - Number(a.here)
  );
  spots.forEach((one) => body.appendChild(spotCard(one, data)));
}

/** Свои дела: сколько точек, сколько приносят и что в казне. */
function turfTallyBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = data.side && data.side.title
    ? "За кого воюешь: " + data.side.title
    : "Ты воюешь сам за себя";
  box.appendChild(head);
  box.appendChild(
    paperRows([
      ["Точек", data.held + " из " + data.ceiling],
      ["Доход", data.income ? num(data.income) + " 💰 в сутки" : "пока ничего"],
      ["В казне", num(data.vault || 0) + " 💰"],
    ])
  );
  return box;
}

/** Строка точки на карте войны. */
function spotCard(one, data) {
  const box = document.createElement("article");
  box.className = "spot" + (one.mine ? " mine" : "") + (one.here ? " here" : "");

  const head = document.createElement("header");
  head.className = "spot-head";
  const title = document.createElement("h3");
  title.className = "spot-title";
  // Отметка впереди имени: по ней чья точка видно, не читая строки ниже
  title.textContent = (one.mark || "") + " " + one.title
    + (one.here ? " · ты здесь" : "");
  const income = document.createElement("span");
  income.className = "spot-income";
  income.textContent = num(one.income) + " 💰/сут";
  head.append(title, income);
  box.appendChild(head);

  const who = document.createElement("p");
  who.className = "spot-owner";
  // Нанятых считаем, городскую охрану называем: её не ставят и не
  // считают, она просто есть на всякой точке, где не хозяйничает семья.
  // Брошенная точка не платит, и об этом говорим прямо: деньги — не
  // за флажок на карте, а за то, что её кто-то держит
  const kept = one.posted
    ? "охрана: " + one.posted + " из " + one.max_posted
    : one.owner.nobody
      ? "городская охрана"
      : "без охраны — не платит";
  who.textContent = (one.mine ? "✅ Держите вы" : "Держит: " + one.owner.title)
    + " · " + kept;
  box.appendChild(who);

  if (one.note) {
    const note = document.createElement("p");
    note.className = "spot-note";
    note.textContent = one.note;
    box.appendChild(note);
  }

  // Точка, на которой не дерутся: воевать не за что, а охрану
  // поставить — надо, и без этой кнопки прилавок остаётся незамеченным
  if (one.action === "guard") {
    if (one.here && data.hire && data.hire.side) {
      const buttons = document.createElement("div");
      buttons.className = "thing-buttons";
      buttons.appendChild(
        button("🛡 Охрана", {
          disabled: !(data.hire || {}).can_post,
          hint: (data.hire || {}).why || "Охрану ставят на своей точке.",
          onClick: showHireShelf,
        })
      );
      box.appendChild(buttons);
    }
    return box;
  }

  // Почему нельзя — говорим, а не прячем кнопку
  if (one.busy) {
    box.appendChild(spotState("За точку уже дерутся."));
    return box;
  }
  if (data.side && data.side.nobody) {
    box.appendChild(spotState("Воевать за территории тебе пока не за кого."));
    return box;
  }
  // Отсюда только смотрят. Отряд собирают у самого дома, и это не
  // придирка интерфейса: так же думает и сервер
  if (!one.here) {
    box.appendChild(spotState("Захват берут на месте: дойди до дома."));
    return box;
  }
  if (!one.can_storm) {
    box.appendChild(spotState("Отсюда на эту точку не ходят."));
    return box;
  }
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (one.action === "free") {
    buttons.appendChild(
      button("🚔 " + one.action_title, {
        onClick: () => turfAction({ action: "free", spot: one.code }),
      })
    );
    box.appendChild(buttons);
    return box;
  }
  buttons.appendChild(
    button((one.action === "arrest" ? "🚔 " : "🚩 ") + one.action_title, {
      disabled: Boolean(data.muster),
      hint: "Ты уже собираешь отряд.",
      onClick: () => turfAction({ action: "call", spot: one.code }),
    })
  );
  box.appendChild(buttons);
  return box;
}

// Прилавок стоит выше списка точек, и после захвата до него не
// доходят — кнопка у точки просто подводит к нему
function showHireShelf() {
  const shelf = el("turf-hire");
  if (shelf && shelf.scrollIntoView) {
    shelf.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

/** Точка под ногами: кто её держит и кто на ней стоит.

    «Охрана: 1 из 10» в списке — число, а не ответ: кого поставили,
    чей он и сколько там городской охраны, по нему не видно. А решать,
    ставить ли ещё одного, надо именно по этому. */
function spotPanel(spot) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  if (spot.badge) {
    const badge = picture(spot.badge, spot.owner.title, "");
    badge.className = "band-badge";
    head.appendChild(badge);
    head.appendChild(document.createTextNode(" " + spot.title));
  } else {
    head.textContent = spot.mark + " " + spot.title;
  }
  box.appendChild(head);

  const rows = [
    ["Держит", spot.mine ? "ваши" : spot.owner.title],
    ["Доход", num(spot.income) + " 💰 в сутки"],
    [
      "Охрана",
      spot.posted.length + " из " + spot.max_posted
        + (spot.guards ? " · городская охрана: " + spot.guards : ""),
    ],
    // Деньги идут за то, что точку держат, а не за флажок на карте
    ["Платит", spot.guarded ? "да" : "нет — на ней никого"],
  ];
  box.appendChild(paperRows(rows));

  (spot.posted || []).forEach((one) => box.appendChild(postedRow(one)));
  if (spot.guards) box.appendChild(guardRow(spot));
  const deed = spotDeed(spot);
  if (deed) box.appendChild(deed);
  return box;
}

/** Кнопка дела под точкой: захват, задержание или охрана.

    Слово на кнопке — то же, что на плашке у дома: семья захватывает,
    город задерживает, а на своей точке и те и другие ставят охрану.
    Кнопка не прячется, когда нажать её нельзя, — гаснет и говорит
    почему: пустое место не объясняет ничего. */
function spotDeed(spot) {
  const hire = (turfData && turfData.hire) || {};
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (spot.action === "guard") {
    buttons.appendChild(
      button("🛡 " + spot.action_title, {
        disabled: !hire.can_post,
        hint: hire.why || "Охрану ставят на своей точке.",
        onClick: showHireShelf,
      })
    );
    return buttons;
  }
  if (spot.action === "free") {
    // Освобождение идёт без боя: драться на пустом доме не с кем
    buttons.appendChild(
      button("🚔 " + spot.action_title, {
        disabled: !spot.can_free,
        hint: "Освобождают на месте.",
        onClick: () => turfAction({ action: "free", spot: spot.code }),
      })
    );
    return buttons;
  }
  if (!spot.action) return null;
  const mark = spot.action === "arrest" ? "🚔 " : "🚩 ";
  buttons.appendChild(
    button(mark + spot.action_title, {
      disabled: !spot.can_storm || Boolean(turfData && turfData.muster),
      hint: spotWhyNot(spot),
      onClick: () => turfAction({ action: "call", spot: spot.code }),
    })
  );
  return buttons;
}

/** Почему кнопку дела нажать нельзя. Одна причина — настоящая.

    Гасить кнопку и объяснять её не тем — хуже, чем спрятать: боец
    уходит с готовым, но ложным ответом. Порядок тот же, что у
    сервера: чужая драка, свой сбор. */
function spotWhyNot(spot) {
  if (spot.busy) return "За точку уже дерутся. Подожди.";
  if (turfData && turfData.muster) return "Ты уже собираешь отряд.";
  return "Сюда с отрядом не ходят.";
}

/** Строка выставленного: лицо, ступень и от кого он тут стоит. */
function postedRow(one) {
  const row = document.createElement("article");
  row.className = "neighbour";
  const line = document.createElement("header");
  line.className = "neighbour-head";
  const name = document.createElement("span");
  name.className = "neighbour-name";
  if (one.picture) {
    const face = picture(one.picture, one.title, "");
    face.className = "npc-face";
    name.appendChild(face);
  }
  name.appendChild(document.createTextNode(
    " " + one.fclass + " " + one.title + " [" + one.level + "]"
  ));
  const from = document.createElement("span");
  from.className = "neighbour-clan";
  from.textContent = "от " + one.owner;
  line.append(name, from);
  row.appendChild(line);
  return row;
}

/** Городская охрана: её не ставят и не считают — она просто есть. */
function guardRow(spot) {
  const row = document.createElement("article");
  row.className = "neighbour away";
  const line = document.createElement("header");
  line.className = "neighbour-head";
  const name = document.createElement("span");
  name.className = "neighbour-name";
  if (spot.guard_picture) {
    const face = picture(spot.guard_picture, "Городская охрана", "");
    face.className = "npc-face";
    name.appendChild(face);
  }
  name.appendChild(document.createTextNode(
    " Городская охрана · " + spot.guards + " × " + spot.guard_level + " ур."
  ));
  const from = document.createElement("span");
  from.className = "neighbour-clan";
  from.textContent = "город";
  line.append(name, from);
  row.appendChild(line);
  return row;
}

/** Наём: кто на руках, кого ставить здесь и что предлагают взять. */
function hireBox(data) {
  const hire = data.hire;
  const box = document.createElement("section");
  box.className = "shelf";
  box.id = "turf-hire";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = hire.side === "merc" ? "Наёмники" : "Патрульные";
  box.appendChild(head);

  // Зачем это вообще. Прилавок сам по себе ничего не объясняет, а
  // стоящему на своей пустой точке надо сказать прямо: доход идёт за
  // то, что её держат, а не за флажок на карте
  if (hire.can_post) {
    const call = document.createElement("p");
    call.className = "thing-note hire-call";
    const ready = hire.mine.filter((one) => !one.spot).length;
    call.textContent = ready
      ? "🛡 " + hire.spot_title + " за вами. Выставь человека из резерва:"
        + " без охраны точка не платит."
      : "🛡 " + hire.spot_title + " за вами, охраны на ней нет. Найми ниже"
        + " и выставь здесь же: без охраны точка не платит.";
    box.appendChild(call);
  }

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = "На руках " + hire.hired + " из " + hire.cap
    + ". Платишь ты, и гибнут они насовсем.";
  box.appendChild(note);

  // Свои: кто в резерве и кого можно поставить здесь
  hire.mine.forEach((one) => {
    const row = document.createElement("article");
    row.className = "neighbour" + (one.spot ? " away" : "");
    const line = document.createElement("header");
    line.className = "neighbour-head";
    const name = document.createElement("span");
    name.className = "neighbour-name";
    if (one.picture) {
      const face = picture(one.picture, one.title, "");
      face.className = "npc-face";
      name.appendChild(face);
    }
    name.appendChild(
      document.createTextNode(" " + one.title + " [" + one.level + "]")
    );
    const where = document.createElement("span");
    where.className = "neighbour-clan";
    where.textContent = one.spot ? "на точке" : "в резерве";
    line.append(name, where);
    row.appendChild(line);
    if (!one.spot && hire.can_post) {
      const buttons = document.createElement("div");
      buttons.className = "thing-buttons";
      buttons.appendChild(
        button("Выставить здесь", {
          onClick: () => turfAction({ action: "post", hire_id: one.id }),
        })
      );
      row.appendChild(buttons);
    }
    box.appendChild(row);
  });

  if (hire.why) {
    const why = document.createElement("p");
    why.className = "thing-note";
    why.textContent = hire.why;
    box.appendChild(why);
  }

  // Прилавок: три ступени, и каждая со своим лицом и своей ценой
  const shelf = document.createElement("div");
  shelf.className = "hire-shelf";
  hire.grades.forEach((one) => {
    const card = document.createElement("article");
    card.className = "hire-card";
    // Лицо живёт в рамке, а не само по себе: не доехавшая картинка
    // заменяется значком класса — узлом вместо `img`, — и без рамки
    // карточка складывалась бы, а кнопка цены растягивалась на её место
    const frame = document.createElement("div");
    frame.className = "hire-frame";
    if (one.picture) {
      const face = picture(one.picture, one.title, one.fclass || "");
      face.className = "hire-face";
      frame.appendChild(face);
    } else {
      frame.textContent = one.fclass || "";
    }
    card.appendChild(frame);
    const title = document.createElement("p");
    title.className = "hire-title";
    title.textContent = one.title;
    card.appendChild(title);
    const said = document.createElement("p");
    said.className = "hire-note";
    said.textContent = one.fclass + " " + one.level + " ур.";
    card.appendChild(said);
    card.appendChild(
      button(num(one.price) + " 💰", {
        disabled: !hire.left,
        hint: "Больше " + hire.cap + " на руках не держат.",
        onClick: () => turfAction({ action: "hire", grade: one.code }),
      })
    );
    shelf.appendChild(card);
  });
  box.appendChild(shelf);
  return box;
}

function spotState(text) {
  const said = document.createElement("p");
  said.className = "spot-state";
  said.textContent = text;
  return said;
}

/** Сбор отряда: кто записан и сколько ждать. */
function musterBox(muster) {
  const box = document.createElement("section");
  box.className = "shelf muster";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Отряд на " + muster.spot_title;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "В отряде " + muster.total + " из " + muster.max
    + ". Сбор разойдётся через " + clockText(muster.seconds_left) + ".";
  box.appendChild(note);

  const list = document.createElement("div");
  list.className = "muster-list";
  muster.members.forEach((one) => {
    const row = document.createElement("div");
    row.className = "muster-row" + (one.is_self ? " me" : "");
    row.textContent =
      (one.user_id === muster.leader ? "★ " : "") + one.name;
    list.appendChild(row);
  });
  box.appendChild(list);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (muster.is_leader) {
    buttons.appendChild(
      button("Пошли", { onClick: () => turfAction({ action: "march" }) })
    );
  }
  buttons.appendChild(
    button(muster.is_leader ? "Распустить" : "Выйти", {
      secondary: true,
      onClick: () => turfAction({ action: "leave" }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Штурм целиком: заголовок, стороны, кнопки хода и журнал боя. */
function siegeBox(siege) {
  const box = document.createElement("div");
  box.className = "fight-panel";

  // Шапка как в групповом бою: строка про волну и строка про место.
  // Часы хода тут же: кто не нажал, за того бьёт случай
  const head = document.createElement("p");
  head.className = "fight-round";
  head.textContent = siege.finished
    ? (siege.won ? "🚩 Точка взята" : "🛡 Точка отбита")
    : "🚩 " + siege.spot_title + " — волна " + siege.wave;
  box.appendChild(head);

  const who = document.createElement("p");
  who.className = "fight-line";
  who.textContent = siege.finished
    ? siege.spot_title + " · волн: " + siege.wave
    : "Держит: " + siege.defender
      + (siege.seconds_left ? " · ход " + clockText(siege.seconds_left) : "");
  box.appendChild(who);

  box.appendChild(siegeBoard(siege));
  box.appendChild(siegeFoot(siege));
  // Журнал внизу и всегда: ради него сюда и смотрят, когда свой ход уже
  // сделан, — и после боя он остаётся единственным, что было
  if ((siege.log || []).length) box.appendChild(siegeLog(siege));
  return box;
}

// Доска боя отрядами — одна на штурм и на ограбление, и та же, что в
// групповом бою: внизу ты и тот, кого бьёшь в этом размене, выше
// остальные — свои слева, чужие справа. Шкалы двумя столбиками
// читались хуже: в них не видно, кто с кем сошёлся.
function brawlBoard(ours, theirs, you, foe, mine) {
  const box = document.createElement("div");
  box.className = "raid-board";
  box.appendChild(
    deckOf(
      "raid-mates",
      brawlCards(ours.filter((one) => one !== you), "mate", mine),
      you ? brawlCard(you, "raid-you", mine) : null
    )
  );
  const swords = document.createElement("p");
  swords.className = "versus";
  swords.textContent = "⚔️";
  box.appendChild(swords);
  box.appendChild(
    deckOf(
      "raid-foes",
      brawlCards(
        theirs.filter((one) => !foe || one.user_id !== foe.user_id),
        "foe", false
      ),
      foe ? brawlCard(foe, "boss-card mine", false) : noRivalCard()
    )
  );
  return box;
}

// Отряд слева, гарнизон справа — галочки только у отряда, и только
// пока бой идёт: в итоге «ход сделан» уже ничего не значит
function siegeBoard(siege) {
  const party = siege.party || [];
  const you = party.find((one) => one.is_self) || party[0] || null;
  return brawlBoard(
    party, siege.wall || [], you, siege.foe, !siege.finished
  );
}

// Карты стопки: живые сверху, павшие глубже — как в рейде и в бою
function brawlCards(rows, kind, ours) {
  return byTheLiving(rows).map((one) => {
    const card = stackCard(kind, one.alive);
    stackHead(
      card,
      brawlMark(one, ours) + brawlName(one),
      one.alive ? one.hp + "/" + one.max_hp : "выбыл"
    );
    brawlFace(card.querySelector(".fight-name"), one);
    card.appendChild(fightBar(one));
    return card;
  });
}

// Лицо нанятого или охранника — кружком перед именем. У игрока его нет:
// его карточка и так рядом, а место в строке одно
function brawlFace(into, one) {
  if (!into || !one.picture) return;
  const face = picture(one.picture, one.name, "");
  face.className = "npc-face";
  into.insertBefore(face, into.firstChild);
}

// Крупная карта: имя с уровнем, запас числом и шкала под ними
function brawlCard(one, kind, ours) {
  const box = document.createElement("div");
  box.className = kind + (one.alive ? "" : " down dropped");
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent = brawlMark(one, ours) + brawlName(one)
    + " [" + one.level + "]" + (one.is_self ? " — ты" : "");
  brawlFace(name, one);
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = one.alive ? one.hp + "/" + one.max_hp : "выбыл";
  box.append(name, hp, fightBar(one));
  return box;
}

// Галочку и часы ставим только своим: чужая сторона ведёт свой бросок,
// и «ждём его хода» про неё было бы неправдой
function brawlMark(one, ours) {
  if (!one.alive) return "💀 ";
  if (!ours) return "";
  return one.acted ? "✅ " : "⏳ ";
}

function brawlName(one) {
  return (one.fclass || "") + " " + one.name;
}

/** Что под доской: кнопки хода, строка со стороны или «Понятно». */
function siegeFoot(siege) {
  if (siege.finished) {
    const box = document.createElement("div");
    box.className = "fight-finish";
    (siege.summary || []).forEach((line) => {
      const said = document.createElement("p");
      said.className = "log-line";
      said.textContent = line;
      box.appendChild(said);
    });
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Понятно", { onClick: () => turfAction({ action: "done" }) })
    );
    box.appendChild(buttons);
    return box;
  }
  if (!siege.yours || !siege.alive) {
    const said = document.createElement("p");
    said.className = "fight-line";
    said.textContent = siege.alive
      ? "Ты смотришь со стороны."
      : "Тебя вынесли — отряд дерётся дальше.";
    return said;
  }
  if (siege.acted) {
    // Строки «ход принят» тут нет нарочно: на доске у своей карточки
    // уже стоит галочка, и повторять то же самое словами незачем.
    // Остаются заготовки — по ним видно, что накоплено к размену
    return restingTricks(siege);
  }
  return siegeTurnForm(siege);
}

/** Журнал штурма словами судьи — тот же, что в рейде и в дуэли. */
function siegeLog(siege) {
  const box = document.createElement("div");
  box.className = "fight-log";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ход штурма";
  box.appendChild(head);
  // Свежее сверху — и между разменами, и внутри размена: в волне на
  // десять бойцов иначе не понять, что было позже
  siege.log.slice().reverse().forEach((turn) => judgeLines(turn, box, true));
  return box;
}

/** Кнопки хода: прицел, приёмы, удар и блок. */
function siegeTurnForm(siege) {
  const box = document.createElement("div");
  box.className = "turn-form";
  const hands = siege.hands && siege.hands.length
    ? siege.hands
    : [{ hand: 0, icon: "👊", title: "Кулаки" }];

  if (siege.foe && siege.foe.alive) {
    const aim = document.createElement("p");
    aim.className = "raid-aim";
    aim.textContent = "Бьём: " + siege.foe.name
      + " [" + siege.foe.hp + "/" + siege.foe.max_hp + "]";
    box.appendChild(aim);
  }

  const tricks = abilityPanel(siege.abilities, (trick) =>
    useAbility("api/turf", trick, renderTurf)
  );
  if (tricks) box.appendChild(tricks);

  box.appendChild(
    zoneColumns(
      hands,
      siege.attacks,
      siege.blocks,
      () => turfDraft,
      "turf",
      paintTurfDraft
    )
  );

  const go = document.createElement("button");
  go.type = "button";
  go.id = "turf-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !turfReady(hands);
  go.addEventListener("click", () => {
    const move = {
      action: "turn",
      attacks: turfDraft.attacks,
      block: turfDraft.block,
    };
    turfDraft = { attacks: {}, block: null };
    turfAction(move);
  });
  box.appendChild(go);
  return box;
}

function turfReady(hands) {
  return Boolean(turfDraft.block)
    && hands.every((row) => turfDraft.attacks[row.hand]);
}

function paintTurfDraft() {
  const go = el("turf-go");
  if (!go || !turfData || !turfData.siege) return;
  const hands = turfData.siege.hands && turfData.siege.hands.length
    ? turfData.siege.hands
    : [{ hand: 0 }];
  go.disabled = !turfReady(hands);
}

// ---------- учёба: приёмная, занятие и выпускной билет ----------
//
// Экран один на пять домов. Что он покажет, решает не страница, а
// сервер: `courses` — что читают здесь, `study` — на чём стоит боец,
// `lesson` — идёт ли занятие прямо сейчас. Любой из трёх бывает пустым,
// и пустой означает ровно одно — этого тут нет.

let studyData = null;
let studyBusy = false;
let studyTimer = null;
// Открытый билет: вопросы с сервера и то, что боец уже выбрал. Верных
// ответов здесь нет — их знает только сервер
let studyExam = null;

async function loadStudy() {
  try {
    const response = await fetch("api/study", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("study-note").textContent = "Сюда не попасть.";
      return;
    }
    renderStudy(await response.json());
  } catch (error) {
    el("study-note").textContent = error.message;
  }
}

async function studyAction(payload) {
  if (studyBusy) return;
  studyBusy = true;
  try {
    const response = await fetch("api/study", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Учёба", data.error || "Не получилось.");
      await loadStudy();
      return;
    }
    haptic((feedback) => feedback.notificationOccurred("success"));
    if (data.card) render(data.card, true);
    // Билет приехал — раскладываем его и держим до ответа. Пришёл
    // результат — билет убираем: попытка кончилась
    if (data.exam) {
      studyExam = {
        course: data.exam.course,
        title: data.exam.title,
        subject: data.exam.subject,
        need: data.exam.pass_mark,
        questions: data.exam.questions,
        chosen: [],
      };
    } else if (data.attempt) {
      studyExam = null;
    }
    renderStudy(data);
    if (data.said) popup("Учёба", data.said);
  } catch (error) {
    popup("Учёба", "Сервер не ответил.");
  } finally {
    studyBusy = false;
  }
}

function renderStudy(data) {
  studyData = data;
  el("study-title").textContent = "🎓 " + (data.title || "Учёба");
  el("shop-purse-study").textContent = "";
  el("shop-purse-study").appendChild(purse(data.credits || 0));
  el("study-note").textContent = data.said || data.note || "";

  const body = el("study-body");
  body.textContent = "";

  // Занятие идёт — всё остальное подождёт: боец заперт на два часа, и
  // показывать ему приёмную с кнопками бессмысленно
  if (data.lesson && data.lesson.seconds_left) {
    body.appendChild(lessonBox(data));
    startStudyClock();
    return;
  }
  stopStudyClock();

  if (studyExam) {
    body.appendChild(studyExamBox());
    return;
  }
  if (data.study && data.study.code) body.appendChild(myCourseBox(data.study));
  (data.courses || []).forEach((one) => body.appendChild(courseCard(one, data)));
  // Чем платить за курс — выбирают здесь же, как в лавке и в автошколе:
  // жалованье приходит на счёт, а кошелёк по умолчанию наличный, и без
  // этого выбора боец с деньгами на карте читал «не хватает кредитов»
  if (!(data.study && data.study.code) && (data.courses || []).length) {
    body.appendChild(walletBar(data.purse, loadStudy));
  }
}

/** Строка приёмной: курс, цена, срок и кнопка записи. */
function courseCard(one, data) {
  const box = document.createElement("article");
  box.className = "course" + (one.done ? " done" : "");

  const head = document.createElement("header");
  head.className = "course-head";
  const title = document.createElement("h3");
  title.className = "course-title";
  title.textContent = one.emoji + " " + one.title;
  const price = document.createElement("span");
  price.className = "course-price";
  price.textContent = one.done ? "✅ сдан" : num(one.price) + " 💰";
  head.append(title, price);
  box.appendChild(head);

  if (one.note) {
    const note = document.createElement("p");
    note.className = "course-note";
    note.textContent = one.note;
    box.appendChild(note);
  }

  box.appendChild(
    paperRows([
      ["Учиться", one.weeks_text + " · " + one.lessons + " занятий"],
      ["Занятие", one.lesson_hours + " ч, по будням " + one.window],
      // Где учиться, говорим только если это другой дом: иначе строка
      // повторяет заголовок экрана
      ["Где", one.elsewhere ? one.place_title : ""],
      ["Экзамен", one.exam_size + " вопросов, нужно " + one.pass_mark],
      ["Диплом", one.diploma_title],
    ])
  );

  if (one.done) return box;
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Записаться · " + num(one.price) + " 💰", {
      disabled: !one.open || one.price > (data.credits || 0),
      hint: one.open
        ? "Не хватает кредитов."
        : "Сперва доучись то, на чём стоишь.",
      onClick: () =>
        askConfirm(
          "Записаться на «" + one.title + "»?",
          "Спишется " + num(one.price) + " 💰. Деньги за учёбу не возвращают."
        ).then((ok) => {
          if (ok) studyAction({ action: "enrol", course: one.code });
        }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Свой курс: сколько отстояно, что дальше и где это дальше. */
function myCourseBox(mine) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ты учишься: " + mine.title;
  box.appendChild(head);

  const bar = document.createElement("div");
  bar.className = "hours";
  const line = document.createElement("div");
  line.className = "hours-line";
  const fill = document.createElement("div");
  fill.className = "hours-fill";
  fill.style.width = mine.percent + "%";
  line.appendChild(fill);
  const text = document.createElement("div");
  text.className = "hours-text";
  text.textContent =
    "Занятий " + mine.lessons_done + " из " + mine.lessons
    + (mine.learned ? " — курс отучен" : "");
  bar.append(line, text);
  box.appendChild(bar);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = studyHint(mine);
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (!mine.learned) {
    buttons.appendChild(
      button("Пойти на занятие · " + mine.lesson_hours + " ч", {
        disabled: !mine.can_attend,
        hint: studyHint(mine),
        onClick: () => studyAction({ action: "attend" }),
      })
    );
  } else {
    buttons.appendChild(
      button("Сдавать экзамен", {
        disabled: !mine.exam_open || !mine.at_house,
        hint: mine.at_house
          ? "Пересдача раз в неделю: " + mine.retry_at
          : "Экзамен принимают в приёмной.",
        onClick: () => studyAction({ action: "exam" }),
      })
    );
  }
  buttons.appendChild(
    button("Бросить курс", {
      secondary: true,
      onClick: () =>
        askConfirm(
          "Бросить «" + mine.title + "»?",
          "Деньги за учёбу не возвращают, занятия обнулятся."
        ).then((ok) => {
          if (ok) studyAction({ action: "leave" });
        }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Почему кнопка занятия не нажимается — одной строкой. */
function studyHint(mine) {
  if (mine.learned) {
    return mine.exam_open
      ? "Курс отучен. Экзамен принимают в приёмной: " + mine.exam_size
        + " вопросов, нужно " + mine.pass_mark + "."
      : "Экзамен не сдан. Пересдача " + mine.retry_at + ".";
  }
  if (!mine.at_place) return "Занятия идут в другом доме: " + mine.place_title + ".";
  if (mine.today) return "На сегодня занятие отстояно. Приходи завтра.";
  if (!mine.open_now) return "Сейчас не учебное время. По будням " + mine.window + ".";
  if (!mine.fits) return "До конца приёма меньше двух часов. Приходи завтра.";
  return "Занятие идёт " + mine.lesson_hours + " часа. Уйти с него нельзя.";
}

/** Идёт занятие: часы тикают на странице, время считает сервер. */
function lessonBox(data) {
  const box = document.createElement("section");
  box.className = "shift";
  const head = document.createElement("div");
  head.className = "shift-head";
  head.textContent = "Занятие идёт";
  const clock = document.createElement("div");
  clock.className = "shift-clock";
  clock.id = "study-clock";
  box.append(head, clock);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    "Занятие уже засчитано. Из дома не выйти, пока оно не кончится — "
    + "ты на учёбе.";
  box.appendChild(note);
  return box;
}

function startStudyClock() {
  stopStudyClock();
  studyTimer = setInterval(tickStudy, 1000);
  tickStudy();
}

function stopStudyClock() {
  if (studyTimer) clearInterval(studyTimer);
  studyTimer = null;
}

function tickStudy() {
  const shown = el("study-clock");
  if (!shown || !studyData || !studyData.lesson) return;
  const left = Math.max(
    0,
    studyData.lesson.until - Math.floor(Date.now() / 1000)
  );
  if (!left) {
    stopStudyClock();
    loadStudy();
    return;
  }
  shown.textContent = "Осталось " + fullClock(left);
}

/** Выпускной билет: десять вопросов на одном экране и одна кнопка.
 *
 * `studyExamBox`, а не `examBox`: тем именем зовётся билет автошколы,
 * и в одном файле две функции с одним именем — это одна функция,
 * последняя. Экзамен по ПДД после такого показывал пустоту.
 */
function studyExamBox() {
  const box = document.createElement("section");
  box.className = "quiz";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = studyExam.title;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent =
    studyExam.questions.length + " вопросов про " + studyExam.subject
    + ". Нужно " + studyExam.need + " верных. Пересдача через неделю.";
  box.appendChild(note);

  studyExam.questions.forEach((question, index) => {
    box.appendChild(studyExamQuestion(question, index));
  });

  const row = document.createElement("div");
  row.className = "quiz-buttons";
  row.appendChild(
    button("Сдать", {
      disabled: studyExam.chosen.filter((one) => one !== undefined).length
        < studyExam.questions.length,
      hint: "Ответь на все вопросы.",
      onClick: () =>
        studyAction({ action: "pass", answers: studyExam.chosen }),
    })
  );
  row.appendChild(
    button("Передумал", {
      secondary: true,
      onClick: () => {
        studyExam = null;
        renderStudy(studyData);
      },
    })
  );
  box.appendChild(row);
  return box;
}

function studyExamQuestion(question, index) {
  const box = document.createElement("div");
  box.className = "question";
  const text = document.createElement("div");
  text.className = "question-text";
  text.textContent = index + 1 + ". " + question.text;
  box.appendChild(text);

  question.options.forEach((option, at) => {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "answer" + (studyExam.chosen[index] === at ? " on" : "");
    pick.textContent = option;
    pick.addEventListener("click", () => {
      studyExam.chosen[index] = at;
      renderStudy(studyData);
    });
    box.appendChild(pick);
  });
  return box;
}

// ---------- почта: посылки между бойцами ----------
//
// Две вкладки. «Отправить» — рюкзак: выбрал вещь, написал ник, назначил
// цену и срок. «Посылки» — две полки: что пришло тебе и что ты отправил
// сам. Вторая ничего не делает и нужна ровно затем, чтобы знать, у кого
// твоя вещь и сколько ей там лежать.

let postData = null;
let postBusy = false;
let postTab = "send";
// Что выбрано и написано в форме отправки. Форма перерисовывается от
// каждого нажатия — выбрали вещь, сменили срок, — и набранное должно
// это переживать: иначе ник стирается ровно в тот миг, когда он нужен
let postItem = 0;
let postTerm = "long";
let postWho = "";
let postCod = "0";
const POST_TABS = [
  ["send", "📦 Отправить"],
  ["parcels", "📬 Посылки"],
];

async function loadPost() {
  try {
    const response = await fetch("api/post", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("post-note").textContent = "Почта не открылась.";
      return;
    }
    renderPost(await response.json());
  } catch (error) {
    el("post-note").textContent = error.message;
  }
}

async function postAction(payload) {
  if (postBusy) return;
  postBusy = true;
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/post", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Почта", data.error || "Не получилось.");
      return;
    }
    if (payload.action === "send") {
      postItem = 0;
      postWho = "";
      postCod = "0";
    }
    renderPost(data);
    if (data.card) render(data.card, true);
    if (data.said) popup("Почта", data.said);
  } catch (error) {
    popup("Почта", error.message);
  } finally {
    postBusy = false;
  }
}

function pickPostTab(name) {
  postTab = name;
  if (postData) renderPost(postData);
  else paintPostTabs();
}

function paintPostTabs() {
  POST_TABS.forEach(([code]) => {
    el("post-" + code).classList.toggle("hidden", code !== postTab);
  });
}

function renderPost(data) {
  postData = data;
  el("shop-purse-post").textContent = "";
  el("shop-purse-post").appendChild(purse(data.credits));
  el("post-note").textContent = data.said || data.note;

  const tabs = el("post-tabs");
  tabs.textContent = "";
  POST_TABS.forEach(([code, label]) => {
    const count = code === "parcels" ? data.inbox.length : 0;
    tabs.appendChild(
      chip(count ? label + " · " + count : label, postTab === code, () =>
        pickPostTab(code)
      )
    );
  });
  paintPostTabs();

  renderSendTab(data);
  renderParcelsTab(data);
}

/** Вкладка «Отправить»: форма сверху, рюкзак под ней. */
function renderSendTab(data) {
  const body = el("post-send");
  body.textContent = "";
  body.appendChild(walletBar(data.purse, loadPost));
  body.appendChild(sendBox(data));
}

function sendBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Отправить посылку · " + num(data.price) + " 💰";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Вещь уйдёт названному бойцу. Наложенный платёж — цена, которую он " +
    "заплатит на почте; она придёт тебе. Отправка не возвращается.";
  box.appendChild(note);

  // Поля с подписями: в числовом поле стоит ноль, и без подписи понять,
  // что это наложенный платёж, нельзя
  const who = document.createElement("input");
  who.type = "text";
  who.className = "field-input";
  who.id = "post-who";
  who.placeholder = "Ник бойца";
  who.value = postWho;
  who.addEventListener("input", () => {
    postWho = who.value;
  });
  box.appendChild(field("Кому отправить", who));

  const price = document.createElement("input");
  price.type = "number";
  price.inputMode = "numeric";
  price.min = "0";
  price.max = String(data.max_cod);
  price.value = postCod;
  price.className = "field-input";
  price.id = "post-cod";
  price.addEventListener("input", () => {
    postCod = price.value;
  });
  box.appendChild(field("Наложенный платёж, 💰 (0 — подарок)", price));

  // Срок хранения: пузырями, как вкладки — выбор из трёх
  const terms = document.createElement("div");
  terms.className = "bubbles";
  terms.id = "post-terms";
  data.terms.forEach((term) => {
    terms.appendChild(
      chip(term.title, postTerm === term.code, () => {
        postTerm = term.code;
        if (postData) renderPost(postData);
      })
    );
  });
  box.appendChild(field("Срок хранения", terms));

  if (!data.bag.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Рюкзак пуст. Надетое сначала снимают в инвентаре.";
    box.appendChild(empty);
    return box;
  }
  data.bag.forEach((one) => {
    const row = thingCard(one, 0, false, true);
    if (one.id === postItem) row.classList.add("picked");
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button(one.id === postItem ? "Отправить" : "Выбрать", {
        secondary: one.id !== postItem,
        disabled: !data.can_pay,
        hint: "На отправку не хватает кредитов.",
        onClick: () => {
          if (one.id !== postItem) {
            postItem = one.id;
            renderPost(postData);
            return;
          }
          sendParcel(one, data);
        },
      })
    );
    row.querySelector(".thing-body").appendChild(buttons);
    box.appendChild(row);
  });
  return box;
}

async function sendParcel(item, data) {
  const who = el("post-who").value.trim();
  if (!who) {
    popup("Почта", "Напиши ник того, кому отправляешь.");
    return;
  }
  const cod = Math.max(0, Math.floor(Number(el("post-cod").value) || 0));
  const term = (data.terms.find((one) => one.code === postTerm) || {}).title;
  const ok = await askConfirm(
    "Отправить «" + item.title + "»?",
    "Получатель — " + who + ". " +
      (cod ? "Наложенный платёж: " + cod + " 💰. " : "Без платежа, подарком. ") +
      "Срок хранения: " + (term || "").toLowerCase() + ". " +
      "За отправку спишется " + data.price + " 💰, и они не возвращаются.",
    "Отправить"
  );
  if (!ok) return;
  postAction({
    action: "send",
    item: item.id,
    nickname: who,
    price: cod,
    term: postTerm,
  });
}

/** Вкладка «Посылки»: что пришло тебе и что ты отправил. */
function renderParcelsTab(data) {
  const body = el("post-parcels");
  body.textContent = "";
  body.appendChild(walletBar(data.purse, loadPost));
  body.appendChild(inboxBox(data));
  body.appendChild(outboxBox(data));
}

function inboxBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Тебе · " + data.inbox.length;
  box.appendChild(head);

  if (!data.inbox.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Посылок нет. Пусто на полке — не беда.";
    box.appendChild(empty);
    return box;
  }
  data.inbox.forEach((one) => box.appendChild(parcelCard(one, true)));
  return box;
}

function outboxBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Отправлено · " + data.outbox.length;
  box.appendChild(head);

  if (!data.outbox.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Твоих посылок на почте нет.";
    box.appendChild(empty);
    return box;
  }
  data.outbox.forEach((one) => box.appendChild(parcelCard(one, false)));
  return box;
}

/** Карточка посылки: вещь, от кого или кому, платёж и срок. */
function parcelCard(parcel, mine) {
  const row = thingCard(parcel, 0, false, true);
  row.classList.add("parcel");
  if (parcel.back) row.classList.add("back");
  const body = row.querySelector(".thing-body");

  const who = document.createElement("div");
  who.className = "thing-note";
  who.textContent = parcel.back
    ? "Возврат · не забрал: " + parcel.to + " · " + parcel.sent
    : (mine ? "От: " + parcel.from : "Кому: " + parcel.to) + " · " + parcel.sent;
  body.appendChild(who);

  const terms = document.createElement("div");
  terms.className = "thing-note";
  terms.textContent =
    (parcel.due ? "Наложенный платёж: " + num(parcel.due) + " 💰" : "Подарок") +
    " · " +
    (parcel.forever
      ? "до востребования"
      : parcel.left ? "лежит ещё " + parcel.left : "срок вышел");
  body.appendChild(terms);

  if (!mine) return row;

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button(parcel.due ? "Забрать · " + num(parcel.due) + " 💰" : "Забрать", {
      disabled: !parcel.can_take,
      hint: "На выкуп посылки не хватает кредитов.",
      onClick: () => postAction({ action: "take", parcel: parcel.parcel }),
    })
  );
  if (!parcel.back) {
    buttons.appendChild(
      button("Отказаться", {
        secondary: true,
        onClick: async () => {
          const ok = await askConfirm(
            "Отказаться от посылки?",
            "«" + parcel.title + "» поедет обратно к бойцу " + parcel.from + ".",
            "Отказаться"
          );
          if (ok) postAction({ action: "refuse", parcel: parcel.parcel });
        },
      })
    );
  }
  body.appendChild(buttons);
  return row;
}

// ---------- жилой дом: аренда квартиры ----------
//
// Экран один на все четыре дома и держит две вкладки: подъезд и свою
// квартиру. Подъезд видят все — по нему и понятно, что свободно; вкладка
// своей квартиры появляется только у того, кто снимает её здесь. Сейф и
// кладовая рисуются теми же кусками, что склад семьи: устроены они
// одинаково, а слова у них свои.

let flatsData = null;
let flatsBusy = false;
let flatsTab = "stairs";
const FLATS_TABS = [
  ["stairs", "🚪 Подъезд"],
  ["home", "🏠 Моя квартира"],
];

async function loadFlats() {
  try {
    const response = await fetch("api/flats", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("flats-note").textContent = "Дом не открылся.";
      return;
    }
    renderFlats(await response.json());
  } catch (error) {
    el("flats-note").textContent = error.message;
  }
}

async function flatAction(payload) {
  if (flatsBusy) return;
  flatsBusy = true;
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/flats", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Жилой дом", data.error || "Не получилось.");
      return;
    }
    // Сняли квартиру — показываем её: за этим и шли
    if (payload.action === "rent") flatsTab = "home";
    if (payload.action === "leave") flatsTab = "stairs";
    renderFlats(data);
    if (data.card) render(data.card, true);
  } catch (error) {
    popup("Жилой дом", error.message);
  } finally {
    flatsBusy = false;
  }
}

function pickFlatsTab(name) {
  flatsTab = name;
  if (flatsData) renderFlats(flatsData);
  else paintFlatsTabs();
}

function paintFlatsTabs() {
  FLATS_TABS.forEach(([code]) => {
    el("flats-" + code).classList.toggle("hidden", code !== flatsTab);
  });
}

function renderFlats(data) {
  flatsData = data;
  el("shop-purse-flats").textContent = "";
  el("shop-purse-flats").appendChild(purse(data.credits));
  el("flats-title").textContent = "🏠 " + data.title;
  el("flats-note").textContent = data.said || data.note;

  // Вкладка своей квартиры есть только у здешнего жильца: чужую семью и
  // чужой сейф никто не смотрит
  const home = Boolean(data.mine && data.mine.number);
  if (!home && flatsTab !== "stairs") flatsTab = "stairs";
  const tabs = el("flats-tabs");
  tabs.textContent = "";
  FLATS_TABS.forEach(([code, label]) => {
    if (code === "home" && !home) return;
    tabs.appendChild(chip(label, flatsTab === code, () => pickFlatsTab(code)));
  });
  paintFlatsTabs();

  renderStairs(data);
  renderHome(data);
}

/** Подъезд: этажи с квартирами, цена и кто за дверью. */
function renderStairs(data) {
  const body = el("flats-stairs");
  body.textContent = "";
  body.appendChild(walletBar(data.purse, loadFlats));
  body.appendChild(offerBox(data));
  if (data.elsewhere && data.elsewhere.number) {
    body.appendChild(elsewhereBox(data.elsewhere));
  }
  (data.levels || []).forEach((level) => body.appendChild(floorBox(level, data)));
}

/** Что сдают в этом доме: цена, вместимость и сколько свободно. */
function offerBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent =
    data.floors + " этажа · квартир " + data.flats + " · месяц " +
    data.rent_days + " дней";
  box.appendChild(head);

  (data.rooms || []).forEach((kind) => {
    const row = document.createElement("div");
    row.className = "offer-row";
    const title = document.createElement("span");
    title.className = "offer-title";
    title.textContent =
      kind.title.charAt(0).toUpperCase() + kind.title.slice(1) +
      " · " + kind.slots + " вещей";
    const free = document.createElement("span");
    free.className = "offer-free";
    free.textContent = "свободно " + kind.free + " из " + kind.total;
    const price = document.createElement("span");
    price.className = "offer-price";
    price.textContent = num(kind.price) + " 💰/мес";
    row.append(title, free, price);
    box.appendChild(row);
  });
  return box;
}

/** Своя квартира в другом доме: здесь можно только посмотреть. */
function elsewhereBox(mine) {
  const box = document.createElement("div");
  box.className = "thing";
  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = "🏠 " + mine.house_title + ", " + mine.title;
  body.appendChild(title);

  const note = document.createElement("div");
  note.className = "thing-note";
  note.textContent = mine.paid
    ? "Оплачена ещё " + mine.left + ". Квартира на бойца одна."
    : "Аренда кончилась. Освободят через " + mine.grace_left + ".";
  body.appendChild(note);

  box.appendChild(body);
  return box;
}

/** Этаж: его квартиры по порядку номеров. */
function floorBox(level, data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = level.level + "-й этаж";
  box.appendChild(head);

  level.flats.forEach((flat) => box.appendChild(flatRow(flat, data)));
  return box;
}

function flatRow(flat, data) {
  // Строка, а не карточка: квартир в подъезде шестнадцать, и каждая
  // карточкой превращала бы дом в простыню. Номер, комнатность и одна
  // кнопка — больше про дверь сказать нечего
  const box = document.createElement("div");
  box.className = "flat-row" + (flat.mine ? " me" : "");

  const number = document.createElement("span");
  number.className = "flat-no";
  number.textContent = (flat.mine ? "✅ " : "") + flat.title;
  box.appendChild(number);

  const kind = document.createElement("span");
  kind.className = "flat-kind";
  kind.textContent = flat.rooms_title + " · " + flat.slots + " вещей";
  box.appendChild(kind);

  if (flat.mine) {
    box.appendChild(
      button("Моя", { secondary: true, onClick: () => pickFlatsTab("home") })
    );
  } else if (flat.taken) {
    const who = document.createElement("span");
    who.className = "flat-tenant";
    who.textContent = flat.tenant || "занята";
    box.appendChild(who);
  } else {
    box.appendChild(
      button("Снять · " + num(flat.price) + " 💰", {
        disabled: !flat.can_rent || flat.price > spendable(data),
        hint: flat.can_rent
          ? "Не хватает кредитов на месяц."
          : "Квартира на бойца одна: сперва выселись из своей.",
        onClick: () => rentFlat(flat),
      })
    );
  }
  return box;
}

async function rentFlat(flat) {
  const ok = await askConfirm(
    "Снять " + flat.title + "?",
    flat.rooms_title.charAt(0).toUpperCase() + flat.rooms_title.slice(1) +
      ", " + flat.floor + "-й этаж. Спишется " + flat.price +
      " 💰 за месяц вперёд.",
    "Снять"
  );
  if (ok) flatAction({ action: "rent", flat: flat.number });
}

/** Моя квартира: срок, сейф и кладовая. */
function renderHome(data) {
  const body = el("flats-home");
  body.textContent = "";
  const mine = data.mine || {};
  if (!mine.number) return;
  body.appendChild(leaseBox(mine, data));
  body.appendChild(flatVaultBox(data.pantry, data));
  body.appendChild(pantryBox(data.pantry));
  if (data.parking && data.parking.mine) {
    body.appendChild(
      parkingBox(
        data.parking,
        flatAction,
        "🅿️ Место под машину",
        "Место при квартире одно, и оно твоё: машина стоит во дворе, "
          + "пока не понадобится."
      )
    );
  }
}

/** Карточка съёма: адрес, срок и две кнопки — продлить и выселиться. */
function leaseBox(mine, data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = mine.title + " · " + mine.rooms_title;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    mine.floor + "-й этаж · снята " + mine.rented +
    " · кладовая на " + mine.slots + " вещей";
  box.appendChild(note);

  const term = document.createElement("p");
  term.className = mine.paid ? "lease-term" : "lease-term late";
  term.textContent = mine.paid
    ? "Оплачена до " + mine.paid_until + " · осталось " + mine.left
    : "Аренда кончилась " + mine.paid_until + " · освободят через " +
      mine.grace_left;
  box.appendChild(term);

  box.appendChild(walletBar(data.purse, loadFlats));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Продлить · " + num(mine.price) + " 💰", {
      disabled: mine.price > spendable(data),
      hint: "Не хватает кредитов на месяц.",
      onClick: () => flatAction({ action: "extend" }),
    })
  );
  buttons.appendChild(
    button("Выселиться", {
      secondary: true,
      onClick: async () => {
        const ok = await askConfirm(
          "Выселиться из " + mine.title + "?",
          "Вещи из кладовой вернутся в рюкзак, кредиты из сейфа — на руки. " +
            "Квартиру сдадут другому.",
          "Подтвердить"
        );
        if (ok) flatAction({ action: "leave" });
      },
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Сейф квартиры: кладёт и берёт только хозяин. */
function flatVaultBox(pantry, data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сейф квартиры";
  box.appendChild(head);
  box.appendChild(vaultSum(pantry.vault));

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = pantry.locked
    ? "Квартира заперта: пока аренда не продлена, сейф не открыть."
    : "Деньги в сейфе лежат отдельно от наличных и ждут тебя здесь.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Внести", {
      disabled: pantry.locked,
      hint: "Сперва продли аренду.",
      onClick: () =>
        openMoneySheet({
          title: "Внести в сейф",
          note:
            "Из того кошелька, которым ты платишь здесь: " +
            num(spendable(data)) + " 💰",
          max: spendable(data),
          hint: "Платить нечем.",
          onSum: (amount) => flatAction({ action: "deposit", amount: amount }),
        }),
    })
  );
  buttons.appendChild(
    button("Снять", {
      secondary: true,
      disabled: pantry.locked || !pantry.vault,
      hint: pantry.locked ? "Сперва продли аренду." : "Сейф пуст.",
      onClick: () =>
        openMoneySheet({
          title: "Снять из сейфа",
          note: "В сейфе " + num(pantry.vault) + " 💰. Снятое ляжет наличными",
          max: pantry.vault,
          hint: "Сейф пуст.",
          onSum: (amount) => flatAction({ action: "cash", amount: amount }),
        }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Кладовая: шесть мест на комнату, и пустые тоже видно. */
function pantryBox(pantry) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent =
    "Кладовая · " + pantry.items.length + " из " + pantry.slots;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = pantry.locked
    ? "Квартира заперта: вещи лежат на месте, но достать их можно только " +
      "после продления."
    : "Кладут и забирают здесь же: вещи из кладовой в бой не берут.";
  box.appendChild(note);

  box.appendChild(
    shelfGrid({
      id: "pantry-grid",
      slots: pantry.slots,
      items: pantry.items,
      title: (one) => one.title + (one.when ? " · положено " + one.when : ""),
      onThing: (one) =>
        openThingSheet({
          thing: one,
          note: one.when ? "Лежит с " + one.when : "Лежит в кладовой",
          onTake: pantry.locked
            ? null
            : (thing) => flatAction({ action: "take", item: thing.id }),
          denial: "Квартира заперта: сперва продли аренду.",
        }),
      onFree: () => {
        if (pantry.locked) {
          popup("Кладовая", "Квартира заперта: сперва продли аренду.");
          return;
        }
        openPickSheet({
          bag: pantry.bag,
          free: pantry.free,
          note: "Свободных мест: " + pantry.free,
          onPick: (one) => flatAction({ action: "stash", item: one.id }),
        });
      },
    })
  );
  return box;
}

// ---------- мафия: особняк и своя семья ----------

// ---------- особняк ----------

async function loadMafia() {
  try {
    const response = await fetch(
      "api/mafia" + (mafiaOpen ? "?clan=" + mafiaOpen : ""), {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      el("mafia-note").textContent = "Особняк не открылся.";
      return;
    }
    renderMafia(await response.json());
  } catch (error) {
    el("mafia-note").textContent = error.message;
  }
}

async function mafiaAction(payload) {
  if (mafiaBusy) return;
  mafiaBusy = true;
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/mafia", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      // Какой клан открыт, страница помнит сама: сервер вернёт его же
      body: JSON.stringify(Object.assign({ open: mafiaOpen }, payload)),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Особняк мафии", data.error || "Не получилось.");
      return;
    }
    signPicked = "";
    nameTyped = "";
    renderMafia(data);
    if (data.card) render(data.card, true);
  } catch (error) {
    popup("Особняк мафии", error.message);
  } finally {
    mafiaBusy = false;
  }
}

/** Открыть клан из списка: за составом сервер сходит сам. */
function openClan(id) {
  mafiaOpen = id;
  clanTab = "family";
  loadMafia();
}

/** Вернуться к списку кланов. */
function backToClans() {
  mafiaOpen = 0;
  loadMafia();
}

function pickClanTab(name) {
  clanTab = name;
  if (mansionData) renderMafia(mansionData);
  else paintMafiaTabs();
}

function paintMafiaTabs() {
  const open = Boolean(mafiaOpen);
  el("mafia-clans").classList.toggle("hidden", open);
  CLAN_TABS.forEach(([code]) => {
    el("mafia-" + code).classList.toggle("hidden", !open || code !== clanTab);
  });
}

function renderMafia(data) {
  mansionData = data;
  el("shop-purse-mafia").textContent = "";
  el("shop-purse-mafia").appendChild(purse(data.credits));

  // Клан могли распустить или нас из него выгнать, пока он был открыт:
  // сервер вернёт пустую страницу, и мы возвращаемся к списку
  const opened = data.opened || {};
  if (!opened.id) mafiaOpen = 0;
  else mafiaOpen = opened.id;
  // Имущество и настройки — дела семьи: у чужого клана их нет вовсе
  if (!opened.mine && clanTab !== "family") clanTab = "family";

  el("mafia-note").textContent = data.said || mafiaNote(data, opened);

  const tabs = el("mafia-tabs");
  tabs.textContent = "";
  if (!mafiaOpen) {
    tabs.appendChild(chip("🎩 Кланы", true, () => backToClans()));
  } else {
    tabs.appendChild(chip("← Кланы", false, () => backToClans()));
    CLAN_TABS.forEach(([code, label]) => {
      if (code !== "family" && !opened.mine) return;
      tabs.appendChild(chip(label, clanTab === code, () => pickClanTab(code)));
    });
    // Война за территории — дело своей семьи, и вход в неё стоит среди
    // её вкладок. Чужому клану её не показывают вовсе: кто где воюет,
    // не его забота
    if (opened.mine) {
      tabs.appendChild(chip("🚩 Территории", false, () => openTurf("mafia")));
    }
  }
  paintMafiaTabs();

  renderClansTab(data);
  renderFamilyTab(opened);
  renderEstateTab(opened.estate || {});
  renderSettingsTab(opened, data);
}

/** Строка сверху: в списке — своя, в клане — про этот клан. */
function mafiaNote(data, opened) {
  if (!opened.id) return data.note;
  const clan = opened.clan || {};
  return (
    clan.name + " · в семье " + clan.members + " из " + clan.max_members +
    (opened.mine ? " · твой чин: " + (clan.rank_title || "") : "") +
    (clan.founded ? " · с " + clan.founded : "")
  );
}

/** Вкладка «Кланы»: список города. Свой с галочкой, чужие с дверью. */
function renderClansTab(data) {
  const body = el("mafia-clans");
  body.textContent = "";
  if (!data.in_clan) body.appendChild(foundBox(data));
  // Чем платить за клан и за смену имени: тот же выбор, что в лавке
  body.appendChild(walletBar(data.purse, loadMafia));
  body.appendChild(clansBox(data));
}

/** Вкладка «Состав»: три чина и кнопки старших. */
function renderFamilyTab(opened) {
  const body = el("mafia-family");
  body.textContent = "";
  if (!opened.id) return;
  body.appendChild(familyHead(opened));
  (opened.ranks || []).forEach((rank) => {
    if (!rank.members.length && rank.code !== "capo") return;
    body.appendChild(rankBox(rank, opened));
  });
}

/** Шапка состава: знак, имя семьи и её размер. */
function familyHead(opened) {
  const clan = opened.clan || {};
  const box = document.createElement("div");
  box.className = "clan-head";
  clanMarks(clan).forEach((mark) => {
    mark.classList.add("big");
    box.appendChild(mark);
  });
  const text = document.createElement("div");
  text.className = "clan-head-text";
  const name = document.createElement("div");
  name.className = "clan-head-name";
  name.textContent = clan.name || "";
  const note = document.createElement("div");
  note.className = "thing-note";
  note.textContent =
    "В семье " + clan.members + " из " + clan.max_members +
    (opened.mine ? " · твой чин: " + (clan.rank_title || "") : "") +
    (clan.founded ? " · семья с " + clan.founded : "");
  text.append(name, note);
  box.appendChild(text);
  return box;
}

/** Вкладка «Настройки»: заявки, приглашения, права и имя семьи.
 *
 * Всё, что меняет саму семью, собрано здесь: состав остался списком
 * людей, а дела — отдельной вкладкой. Мафиози в ней видит только то,
 * что его дела тут не решаются.
 */
function renderSettingsTab(opened, data) {
  const body = el("mafia-settings");
  body.textContent = "";
  if (!opened.id || !opened.mine) return;

  if (!opened.can_admin) {
    const note = document.createElement("p");
    note.className = "screen-note";
    note.textContent =
      "Заявки, приглашения и имя семьи решают Дон и приближённые.";
    body.appendChild(note);
    return;
  }
  body.appendChild(requestsBox(opened));
  body.appendChild(inviteBox(opened));
  if (opened.is_don) {
    body.appendChild(handoverBox(opened));
    body.appendChild(renameShelf(opened));
  }
}

/** Имя семьи: поле со своей полкой — его меняет Дон и платит за это. */
function renameShelf(opened) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сменить имя";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Имя семьи меняют не чаще раза в " + opened.rename_days + " дней, и " +
    "стоит это " + opened.rename_price + " 💰.";
  box.appendChild(note);

  box.appendChild(renameBox(opened.clan || {}, opened));
  return box;
}

/** Передача прав: кнопка, за которой окно со списком семьи. */
function handoverBox(opened) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Передать права";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Новый Дон получит все дела семьи. Ты останешься в ней тем, кого " +
    "выберешь сам.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Выбрать преемника", {
      secondary: true,
      disabled: (opened.members || []).length < 2,
      hint: "Передавать дела пока некому: в семье ты один.",
      onClick: () => handOver(opened),
    })
  );
  box.appendChild(buttons);
  return box;
}

// ---------- сейф и полки: общее у семьи и у квартиры ----------
//
// Устроены они одинаково: остаток одним числом, кнопки «внести» и
// «снять», решётка полок и три окна — сумма, вещь с полки и выбор вещи
// из рюкзака. Разница между складом семьи и кладовой квартиры — в прозе
// и в том, кому дают брать; поэтому здесь механика, а слова приходят с
// вызовом. Кнопки страница прячет по тому же признаку, по которому
// сервер отказывает, но решает всё равно сервер: спрятанная кнопка
// обходится запросом мимо интерфейса.

/** Остаток сейфа одним числом. */
function vaultSum(money) {
  const sum = document.createElement("div");
  sum.className = "vault-sum";
  sum.textContent = num(money) + " 💰";
  return sum;
}

/** Окно суммы: сколько внести или снять. `opts.onSum(amount)`.
 *
 * `opts.min` — нижняя граница, если она есть: у вклада и кредита суммой
 * в один кредит ничего не делают, и отказ лучше показать до запроса.
 */
function openMoneySheet(opts) {
  const money = Math.max(0, opts.max || 0);
  const least = Math.max(1, opts.min || 1);
  openSheet(opts.title, opts.note, true);
  const list = el("sheet-list");

  const field = document.createElement("input");
  field.type = "number";
  field.inputMode = "numeric";
  field.min = String(least);
  field.max = String(money);
  field.value = money >= least ? String(least) : "0";
  field.className = "field-input";
  field.id = "vault-sum";
  list.appendChild(field);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Подтвердить", {
      disabled: money < least,
      hint: opts.hint,
      onClick: () => {
        const amount = Math.floor(Number(el("vault-sum").value) || 0);
        if (amount <= 0) {
          popup(opts.title, "Напиши число больше нуля.");
          return;
        }
        if (amount < least) {
          popup(opts.title, "Меньше " + num(least) + " 💰 нельзя.");
          return;
        }
        closeSheet();
        opts.onSum(amount);
      },
    })
  );
  buttons.appendChild(
    button("Отменить", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);
}

/** Решётка полок: занятые с картинкой, свободные с плюсом. */
function shelfGrid(opts) {
  const grid = document.createElement("div");
  grid.className = "shelf-grid";
  grid.id = opts.id || "shelf-grid";
  for (let place = 0; place < (opts.slots || 0); place += 1) {
    const thing = opts.items[place];
    const cell = document.createElement("button");
    cell.type = "button";
    if (thing) {
      cell.className =
        "shelf-slot" + (thing.mod && thing.mod.level ? " tier" : "");
      cell.title = opts.title ? opts.title(thing) : thing.title;
      cell.appendChild(slotPicture(thing, thing.icon));
      cell.addEventListener("click", () => opts.onThing(thing));
    } else {
      cell.className = "shelf-slot free";
      cell.title = "Свободная полка";
      cell.textContent = "＋";
      cell.addEventListener("click", () => opts.onFree());
    }
    grid.appendChild(cell);
  }
  return grid;
}

/** Окно вещи с полки: что это и можно ли её забрать. */
function openThingSheet(opts) {
  const thing = opts.thing;
  openSheet(thing.title, opts.note || "", true);
  const list = el("sheet-list");
  list.appendChild(thingCard(thing, 0, false, true));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (opts.onTake) {
    buttons.appendChild(
      button(opts.label || "Забрать себе", {
        onClick: () => {
          closeSheet();
          opts.onTake(thing);
        },
      })
    );
  } else if (opts.denial) {
    const no = document.createElement("p");
    no.className = "thing-note";
    no.textContent = opts.denial;
    list.appendChild(no);
  }
  buttons.appendChild(
    button("Закрыть", { secondary: true, onClick: () => closeSheet() })
  );
  list.appendChild(buttons);
}

/** Окно «что положить»: снятое из своего рюкзака. */
function openPickSheet(opts) {
  openSheet("Что положить", opts.note || "");
  const list = el("sheet-list");
  if (!opts.bag.length) {
    const empty = document.createElement("p");
    empty.className = "sheet-note";
    empty.textContent = "Рюкзак пуст. Надетое сначала снимают в инвентаре.";
    list.appendChild(empty);
    return;
  }
  opts.bag.forEach((one) => {
    const row = thingCard(one, 0, false, true);
    const buttons = document.createElement("div");
    buttons.className = "thing-buttons";
    buttons.appendChild(
      button("Положить", {
        disabled: !opts.free,
        hint: "Свободных полок нет.",
        onClick: () => {
          closeSheet();
          opts.onPick(one);
        },
      })
    );
    row.querySelector(".thing-body").appendChild(buttons);
    list.appendChild(row);
  });
}

// ---------- имущество семьи ----------
//
// Общий карман: кладут в него все, берут Дон и приближённые. Кнопки
// страница прячет по `can_take`, но отказывает сервер — спрятанная
// кнопка обходится запросом мимо интерфейса.

/** Вкладка «Имущество»: сейф с кредитами и склад на тридцать вещей. */
function renderEstateTab(estate) {
  const body = el("mafia-estate");
  body.textContent = "";
  if (!estate.in_clan) return;
  body.appendChild(vaultBox(estate));
  // Бонусный пулл стоит сразу под сейфом: в него он и придёт, и
  // смотрят на него там же, где на общие деньги
  const bonus = (mansionData || {}).bonus;
  if (bonus && bonus.rows) body.appendChild(bonusBox(bonus));
  body.appendChild(shelfBox(estate));
  body.appendChild(
    parkingBox(
      estate.garage || {},
      mafiaAction,
      "🅿️ Парковка особняка",
      "Гараж общий: ставят все свои, уезжают все свои — на любой из "
        + "стоящих. Чин тут ничего не решает."
    )
  );
  body.appendChild(robberyBoxFamily(estate.robbery || {}));
}

/** Гараж участка: две патрульные и машина начальника.

    Карточки те же, что в таксопарке, — гараж один и тот же, — но после
    кнопки перечитываем участок: экран здесь его, а не машинный. */
function stationGarageBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  const free = (data.garage || []).reduce((sum, one) => sum + one.free, 0);
  head.textContent = "🚓 Гараж участка";
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent = "свободно " + free;
  head.appendChild(count);
  box.appendChild(head);

  const list = document.createElement("div");
  list.className = "shelf-list";
  (data.garage || []).forEach((one) => {
    list.appendChild(garageCar(one, stationCarAction));
  });
  box.appendChild(list);
  return box;
}

async function stationCarAction(payload) {
  try {
    const data = await post("api/cars", payload);
    haptic((feedback) => feedback.impactOccurred("medium"));
    if (data.card) render(data.card, true);
    if (data.said) popup("Гараж участка", data.said);
  } catch (error) {
    popup("Гараж участка", error.message);
  }
  await loadPolice();
}

/** Парковка: что стоит и что можно поставить.
 *
 * Одна вёрстка на два дома — гараж особняка и место при квартире: это
 * одна и та же полка, только для машин, и разница между ними в числе
 * мест и в том, чью машину можно увести.
 *
 * `shared` — парковка общая: в особняке уезжают на любой стоящей
 * машине, и кнопка стоит у каждой. У квартиры парковка своя, и там
 * чужой машине взяться неоткуда. */
function parkingBox(parking, act, title, note) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  const cars = parking.cars || [];
  head.textContent = title + " · " + cars.length + " из " + (parking.slots || 0);
  box.appendChild(head);

  const said = document.createElement("p");
  said.className = "thing-note";
  said.textContent = parking.locked
    ? "Квартира заперта: машина стоит на месте, но выехать с него можно "
      + "только после продления."
    : note;
  box.appendChild(said);

  const list = document.createElement("div");
  list.className = "shelf-list";
  cars.forEach((one) =>
    list.appendChild(parkedCar(one, act, parking.locked, parking.shared))
  );
  box.appendChild(list);

  if (!parking.free || parking.locked) return box;
  const keys = parking.keys || [];
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  if (!keys.length) {
    const empty = document.createElement("p");
    empty.className = "shelf-empty";
    empty.textContent = cars.length
      ? "Свободных мест: " + parking.free + ". Других машин при себе нет."
      : "Место свободно. Машину покупают или снимают в автосалоне.";
    box.appendChild(empty);
    return box;
  }
  keys.forEach((one) => {
    buttons.appendChild(
      button("Поставить · " + one.title, {
        secondary: true,
        onClick: () => act({ action: "park", car: one.code }),
      })
    );
  });
  box.appendChild(buttons);
  return box;
}

/** Машина на парковке: чья она и можно ли её забрать.
 *
 * На общей парковке кнопка у каждой машины, и у чужой она говорит
 * прямо: не «забрать», а «уехать». Подпись с хозяином остаётся — по ней
 * видно, что машина тут не ничья, просто гараж общий. */
function parkedCar(one, act, locked, shared) {
  const box = carCard(one);
  box.appendChild(
    paperRows([
      ["Поставил", one.mine ? "ты" : one.owner],
      [one.own ? "Своя" : "Снята до", one.own ? "насовсем" : one.left_text],
    ])
  );
  if (!one.mine && !shared) return box;
  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button(one.mine ? "Забрать" : "Уехать на ней", {
      secondary: !one.mine,
      disabled: Boolean(locked),
      hint: "Квартира заперта: сперва продли аренду.",
      onClick: () => act({ action: "unpark", car: one.code, id: one.id }),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Ограбления: лицензии бойца и общая недельная цель семьи. */
function robberyBoxFamily(shop) {
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Ограбления";
  box.appendChild(head);

  box.appendChild(
    paperRows([
      ["Нападений на руках", String(shop.left || 0)],
      ["В лицензии", (shop.pack || 0) + " нападений, без срока"],
      ["Цена", num(shop.price || 0) + " 💰"],
      ["Берут из кармана", (shop.share || 0) + "%"],
    ])
  );

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Лицензия — пачка прав на нападение или вмешательство: "
    + (shop.pack || 0) + " штук, и они не сгорают. "
    + "Деньги с ограбления идут тому, кто напал: помощникам ничего.";
  box.appendChild(note);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Взять лицензию · " + num(shop.price || 0) + " 💰", {
      onClick: () => mafiaAction({ action: "licence", count: 1 }),
    })
  );
  box.appendChild(buttons);

  // Цель общая: её закрывают все вместе, и премия тоже общая
  const goal = document.createElement("p");
  goal.className = "thing-note";
  goal.textContent = shop.done
    ? "Недельная цель закрыта: " + (shop.attacks || 0) + " из "
      + (shop.goal || 0) + ". Премия " + num(shop.reward || 0) + " 💰 в сейфе."
    : "Цель недели: " + (shop.attacks || 0) + " из " + (shop.goal || 0)
      + " нападений. Закроете — " + num(shop.reward || 0) + " 💰 в сейф.";
  box.appendChild(goal);
  return box;
}


/** Сейф: остаток видит вся семья, берут из него только старшие. */
function vaultBox(estate) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Сейф семьи";
  box.appendChild(head);
  box.appendChild(vaultSum(estate.vault));

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = estate.can_take
    ? "Кладут все, кто в семье. Брать можно тебе — ты из старших."
    : "Кладут все, кто в семье. Берут только Дон и приближённые.";
  box.appendChild(note);

  // Чем вносить: тот же выбор кошелька, что в лавке
  box.appendChild(walletBar(estate.purse, loadMafia));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Внести", {
      onClick: () =>
        openMoneySheet({
          title: "Внести в сейф",
          note:
            "Из того кошелька, которым ты платишь здесь: " +
            num(spendable(estate)) + " 💰",
          max: spendable(estate),
          hint: "Платить нечем.",
          onSum: (amount) => mafiaAction({ action: "deposit", amount: amount }),
        }),
    })
  );
  if (estate.can_take) {
    buttons.appendChild(
      button("Снять", {
        secondary: true,
        disabled: !estate.vault,
        hint: "Сейф пуст.",
        onClick: () =>
          openMoneySheet({
            title: "Снять из сейфа",
            note:
              "В сейфе " + num(estate.vault) +
              " 💰. Снятое ляжет наличными",
            max: estate.vault,
            hint: "Сейф пуст.",
            onSum: (amount) => mafiaAction({ action: "cash", amount: amount }),
          }),
      })
    );
  }
  box.appendChild(buttons);
  return box;
}

/** Склад семьи: все его полки, и пустые тоже видно. */
function shelfBox(estate) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent =
    "Склад семьи · " + estate.items.length + " из " + estate.slots;
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent = estate.can_take
    ? "Кладут все, кто в семье. Забрать с полки можешь и ты."
    : "Кладут все, кто в семье. Забирают только Дон и приближённые.";
  box.appendChild(note);

  box.appendChild(
    shelfGrid({
      slots: estate.slots,
      items: estate.items,
      title: (one) => one.title + (one.by ? " · положил " + one.by : ""),
      onThing: (one) =>
        openThingSheet({
          thing: one,
          note:
            (one.by ? "Положил " + one.by : "Общая вещь семьи") +
            (one.when ? " · " + one.when : ""),
          onTake: estate.can_take
            ? (thing) => mafiaAction({ action: "take", item: thing.id })
            : null,
          denial: "Забирают с полок только Дон и приближённые.",
        }),
      onFree: () =>
        openPickSheet({
          bag: estate.bag,
          free: estate.free,
          note: "Свободных полок: " + estate.free,
          onPick: (one) => mafiaAction({ action: "stash", item: one.id }),
        }),
    })
  );
  return box;
}

/** Форма создания клана: имя, знак и тысяча кредитов. */
function foundBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Завести свой клан";
  box.appendChild(head);

  const note = document.createElement("p");
  note.className = "thing-note";
  note.textContent =
    "Цена — " + data.price + " 💰. Имя от " + data.name_min + " до " +
    data.name_max + " знаков, и его потом можно сменить за " +
    data.rename_price + " 💰, но не чаще раза в " + data.rename_days +
    " дней. Знак выбирается один раз и достаётся только твоей семье. " +
    "Ты станешь Доном.";
  box.appendChild(note);

  const name = document.createElement("input");
  name.type = "text";
  name.className = "field-input";
  name.id = "clan-name";
  name.maxLength = data.name_max;
  name.placeholder = "Имя клана";
  if (signPicked) {
    const chosen = data.signs.find((one) => one.code === signPicked);
    if (chosen) name.value = nameTyped || chosen.title;
  }
  name.addEventListener("input", () => {
    nameTyped = name.value;
  });
  box.appendChild(name);

  box.appendChild(signsGrid(data));

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Завести клан · " + data.price + " 💰", {
      disabled: !data.can_pay || !data.free_signs,
      hint: data.free_signs
        ? "Кредитов не хватает."
        : "Все знаки разобраны: свободного для новой семьи нет.",
      onClick: () => foundClan(),
    })
  );
  box.appendChild(buttons);
  return box;
}

/** Решётка знаков: занятые подписаны чужим именем и не выбираются. */
function signsGrid(data) {
  const grid = document.createElement("div");
  grid.className = "sign-grid";
  grid.id = "sign-grid";
  data.signs.forEach((sign) => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className =
      "sign" + (sign.taken ? " taken" : "") +
      (sign.code === signPicked ? " picked" : "");
    cell.title = sign.taken ? sign.title + " · у клана «" + sign.owner + "»"
                            : sign.title;
    cell.disabled = Boolean(sign.taken);
    const pic = document.createElement("img");
    pic.src = sign.picture;
    pic.alt = sign.title;
    cell.appendChild(pic);
    cell.addEventListener("click", () => pickSign(sign));
    grid.appendChild(cell);
  });
  return grid;
}

/** Выбрать знак: он же подставляет имя, пока игрок не написал своё. */
function pickSign(sign) {
  haptic((feedback) => feedback.selectionChanged());
  signPicked = sign.code;
  const field = el("clan-name");
  if (field && !nameTyped) field.value = sign.title;
  if (mansionData) renderMafia(mansionData);
}

function foundClan() {
  const field = el("clan-name");
  const name = field ? field.value.trim() : "";
  if (!name) {
    popup("Особняк мафии", "У клана должно быть имя.");
    return;
  }
  if (!signPicked) {
    popup("Особняк мафии", "Выбери знак клана — он будет только твоим.");
    return;
  }
  mafiaAction({ action: "found", name: name, sign: signPicked });
}

/** Поле смены имени: его показывают Дону прямо в карточке своей семьи.
 *
 * Полем, а не всплывающим окном с вводом: такого окна в мини-аппе нет
 * вовсе, а `prompt` в телеграмовском вебвью не открывается.
 */
function renameBox(clan, data) {
  const box = document.createElement("div");
  box.className = "clan-rename";

  const field = document.createElement("input");
  field.type = "text";
  field.className = "field-input";
  field.id = "clan-rename";
  field.maxLength = data.name_max;
  field.placeholder = "Новое имя клана";
  field.disabled = !clan.can_rename;
  box.appendChild(field);

  box.appendChild(
    button("Сменить имя · " + data.rename_price + " 💰", {
      secondary: true,
      disabled: !clan.can_rename,
      hint:
        "Имя меняют не чаще раза в " + data.rename_days + " дней." +
        (clan.rename_at ? " Следующая смена — " + clan.rename_at + "." : ""),
      onClick: () => {
        const name = el("clan-rename").value.trim();
        if (!name) {
          popup("Особняк мафии", "Напиши новое имя клана.");
          return;
        }
        mafiaAction({ action: "rename", name: name });
      },
    })
  );
  return box;
}

/** Список кланов города: свой сверху, остальные по размеру.
 *
 * Строка, а не карточка с кнопками: нажатие на саму строку открывает
 * клан, а справа стоят значки дел — галочка своей семьи, крестик выхода
 * и дверь заявки. Словами они были бы вдвое длиннее строки, а дел у
 * бойца в чужом клане ровно одно.
 */
function clansBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Кланы города · " + data.clans.length;
  box.appendChild(head);

  if (!data.clans.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Ни одной семьи. Заведи первую.";
    box.appendChild(empty);
    return box;
  }
  data.clans.forEach((clan) => box.appendChild(clanCard(clan, data)));
  return box;
}

function clanCard(clan, data) {
  const row = document.createElement("div");
  row.className = "clan-item" + (clan.mine ? " me" : "");

  // Сама строка — кнопка: по ней клан и открывается
  const open = document.createElement("button");
  open.type = "button";
  open.className = "clan-open";
  open.title = "Открыть клан «" + clan.name + "»";

  const pic = document.createElement("span");
  pic.className = "clan-item-pic";
  if (clan.emblem) {
    const sign = document.createElement("img");
    sign.src = clan.emblem;
    sign.alt = clan.name;
    pic.appendChild(sign);
  } else {
    pic.textContent = "🖤";
  }
  open.appendChild(pic);

  const text = document.createElement("span");
  text.className = "clan-item-text";
  const title = document.createElement("span");
  title.className = "clan-item-name";
  // Своя семья помечена галочкой: по ней и видно, где ты
  title.textContent = (clan.mine ? "✅ " : "") + clan.name;
  const size = document.createElement("span");
  size.className = "clan-item-size";
  size.textContent =
    "В семье " + clan.members + " из " + clan.max_members +
    (clan.founded ? " · с " + clan.founded : "");
  text.append(title, size);
  open.appendChild(text);
  open.addEventListener("click", () => openClan(clan.id));
  row.appendChild(open);

  const acts = document.createElement("div");
  acts.className = "clan-acts";
  if (clan.mine) {
    acts.appendChild(
      iconButton("✕", "Выйти из клана", async () => {
        const ok = await askConfirm(
          "Выйти из клана?",
          "Вы уверены, что хотите покинуть клан «" + clan.name + "»?" +
            (data.is_don
              ? " Дон уходит последним: если в семье есть люди, сперва передай дела."
              : " Вернуться можно будет только по новой заявке."),
          "Подтвердить"
        );
        if (ok) mafiaAction({ action: "leave" });
      })
    );
  } else if (clan.asked) {
    acts.appendChild(
      iconButton("⏳", "Отозвать заявку", () =>
        mafiaAction({ action: "withdraw", clan: clan.id })
      )
    );
  } else if (!data.in_clan) {
    // Дверь со стрелкой — «постучаться». Её видит только тот, кто сам
    // ни в какой семье: из двух семей сразу не выйдет
    acts.appendChild(
      iconButton(
        "➡️🚪",
        clan.full ? "В этой семье мест нет" : "Подать заявку",
        () => mafiaAction({ action: "apply", clan: clan.id }),
        clan.full
      )
    );
  }
  if (acts.children.length) row.appendChild(acts);
  return row;
}

/** Значок-кнопка: одно дело, один символ и подпись при наведении. */
function iconButton(glyph, title, onClick, locked) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "icon-btn" + (locked ? " off" : "");
  btn.textContent = glyph;
  btn.title = title;
  btn.setAttribute("aria-label", title);
  if (locked) {
    btn.addEventListener("click", () => popup("Кланы", title + "."));
  } else {
    btn.addEventListener("click", onClick);
  }
  return btn;
}

// ---------- своя семья ----------
//
// Своего экрана у семьи нет: состав и имущество лежат в особняке, а
// быстрая правка состава открывается окном из карточки. Чин меняют на
// ходу — за этим через полгорода не ходят, — и ручка `api/clan` места
// не проверяет нарочно. Окно и вкладка «Состав» рисуются одними и теми
// же кусками: список один, и собирать его дважды незачем.

async function loadClan() {
  try {
    const response = await fetch("api/clan", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) {
      popup("Клан", "Состав не открылся.");
      return;
    }
    renderClanSheet(await response.json());
  } catch (error) {
    popup("Клан", error.message);
  }
}

/** Открыть окно состава: по имени клана в карточке. */
function openClanSheet() {
  haptic((feedback) => feedback.selectionChanged());
  clanSheetOpen = true;
  openSheet("Состав семьи", "Загружаю…");
  if (clanData) renderClanSheet(clanData);
  loadClan();
}

async function clanAction(payload) {
  if (clanBusy) return;
  clanBusy = true;
  try {
    haptic((feedback) => feedback.impactOccurred("light"));
    const response = await fetch("api/clan", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": (tg && tg.initData) || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      popup("Клан", data.error || "Не получилось.");
      return;
    }
    renderClanSheet(data);
    if (data.card) render(data.card, true);
    // Особняк смотрит на те же чины: открыт — перечитываем и его
    if (lastTab === "mafia") loadMafia();
  } catch (error) {
    popup("Клан", error.message);
  } finally {
    clanBusy = false;
  }
}

/** Окно состава семьи: чины, заявки и кнопки старших.
 *
 * Рисуется только пока окно открыто: тот же ответ приходит и после
 * кнопки в особняке, и дорисовывать поверх закрытого окна незачем.
 */
function renderClanSheet(data) {
  clanData = data;
  if (!clanSheetOpen) return;
  if (!data.in_clan) {
    closeSheet();
    return;
  }
  const clan = data.clan;
  openSheet(
    clan.name,
    data.said ||
      "В семье " + clan.members + " из " + clan.max_members +
        " · твой чин: " + clan.rank_title
  );
  const list = el("sheet-list");
  if (data.can_admin) list.appendChild(requestsBox(data));
  // Общий счёт недели — выше состава: он про то, чем семья занята, а
  // не про то, кто в ней числится
  if (data.bonus && data.bonus.rows) list.appendChild(bonusBox(data.bonus));
  data.ranks.forEach((rank) => {
    if (!rank.members.length && rank.code !== "capo") return;
    list.appendChild(rankBox(rank, data));
  });
}

/** Ряд одного чина: Дон, приближённые или мафиози. */
function rankBox(rank, data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent =
    rank.title +
    (rank.limit
      ? " · " + rank.members.length + " из " + rank.limit
      : rank.members.length > 1 ? " · " + rank.members.length : "");
  box.appendChild(head);

  if (!rank.members.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Пока никого.";
    box.appendChild(empty);
    return box;
  }
  rank.members.forEach((member) => {
    const tools = [];
    if (member.can_promote) {
      tools.push(
        button("Приблизить", {
          secondary: true,
          disabled: !data.free_capo_seats,
          hint: "Приближённых уже " + data.max_capos + ".",
          onClick: () => clanAction({ action: "promote", user: member.user_id }),
        })
      );
    }
    if (member.can_demote) {
      tools.push(
        button("Разжаловать", {
          secondary: true,
          onClick: () => clanAction({ action: "demote", user: member.user_id }),
        })
      );
    }
    if (member.can_expel) {
      tools.push(
        button("Выгнать", {
          onClick: async () => {
            const ok = await askConfirm(
              "Выгнать из семьи?",
              member.nickname + " покинет клан.",
              "Выгнать"
            );
            if (ok) clanAction({ action: "expel", user: member.user_id });
          },
        })
      );
    }
    box.appendChild(clanMemberRow(member, tools));
  });
  return box;
}

/** Передача прав: окно с выпадающим списком семьи и двумя кнопками.
 *
 * Список, а не вопрос на каждого бойца: в семье до двадцати пяти
 * человек, и кнопка «передать» у каждого из них превратила бы состав в
 * частокол. Вторым списком Дон выбирает, кем остаться сам: прежний Дон
 * из семьи не уходит, и чин его — его выбор, а не наш.
 */
function handOver(data) {
  const heirs = (data.members || []).filter((one) => !one.is_self);
  if (!heirs.length) {
    popup("Клан", "Передавать дела некому: в семье ты один.");
    return;
  }
  openSheet(
    "Передать права",
    "Новый Дон получит все дела семьи. Ты останешься в ней тем, кого выберешь.",
    true
  );
  const list = el("sheet-list");

  const heir = document.createElement("select");
  heir.className = "field-input";
  heir.id = "handover-heir";
  heirs.forEach((one) => {
    const pick = document.createElement("option");
    pick.value = String(one.user_id);
    pick.textContent = one.nickname + " · " + one.rank_title;
    heir.appendChild(pick);
  });
  list.appendChild(heir);

  const keep = document.createElement("select");
  keep.className = "field-input";
  keep.id = "handover-keep";
  [
    ["capo", "Сам остаюсь приближённым"],
    ["soldier", "Сам остаюсь мафиози"],
  ].forEach(([code, label]) => {
    const pick = document.createElement("option");
    pick.value = code;
    pick.textContent = label;
    keep.appendChild(pick);
  });
  list.appendChild(keep);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Подтвердить", {
      onClick: () => {
        const user = Number(el("handover-heir").value) || 0;
        const stay = el("handover-keep").value;
        closeSheet();
        clanAction({ action: "handover", user: user, keep: stay });
      },
    })
  );
  buttons.appendChild(
    button("Отменить", {
      secondary: true,
      // Окно одно на всю страницу: отмена возвращает тот список, поверх
      // которого эту форму открыли, а не оставляет пустой экран
      onClick: () => {
        if (clanSheetOpen && clanData) renderClanSheet(clanData);
        else closeSheet();
      },
    })
  );
  list.appendChild(buttons);
}

/** Заявки в семью: их разбирают старшие. */
function requestsBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Заявки · " + data.requests.length;
  box.appendChild(head);

  if (!data.requests.length) {
    const empty = document.createElement("p");
    empty.className = "thing-note";
    empty.textContent = "Заявок нет.";
    box.appendChild(empty);
  }
  data.requests.forEach((knock) => {
    box.appendChild(
      clanMemberRow(knock, [
        button("Принять", {
          onClick: () => clanAction({ action: "approve", user: knock.user_id }),
        }),
        button("Отклонить", {
          secondary: true,
          onClick: () => clanAction({ action: "decline", user: knock.user_id }),
        }),
      ])
    );
  });

  if (data.invites.length) {
    const sent = document.createElement("p");
    sent.className = "thing-note";
    sent.textContent =
      "Позваны и думают: " +
      data.invites.map((one) => one.nickname).join(", ") + ".";
    box.appendChild(sent);
  }
  return box;
}

/** Позвать бойца по нику: ответит он сам, окном при входе. */
function inviteBox(data) {
  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "Позвать в семью";
  box.appendChild(head);

  const field = document.createElement("input");
  field.type = "text";
  field.className = "field-input";
  field.id = "clan-invite";
  field.placeholder = "Ник бойца";
  box.appendChild(field);

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
  buttons.appendChild(
    button("Позвать", {
      onClick: () => {
        const who = el("clan-invite").value.trim();
        if (!who) {
          popup("Клан", "Напиши ник того, кого зовёшь.");
          return;
        }
        clanAction({ action: "invite", nickname: who });
      },
    })
  );
  box.appendChild(buttons);
  return box;
}

// ---------- приглашение: окно при входе ----------

/** Показать первое приглашение, которое ждёт ответа. */
function showInvites(invites) {
  const veil = el("invite-veil");
  if (!invites || !invites.length) {
    veil.classList.add("hidden");
    return;
  }
  const knock = invites[0];
  // Одно и то же приглашение не навязываем дважды за сеанс: ответить на
  // него можно и позже, а окно поверх экрана мешает играть
  if (inviteShown === String(knock.clan_id)) return;
  inviteShown = String(knock.clan_id);

  el("invite-head").textContent = "Тебя зовут в семью";
  el("invite-note").textContent =
    (knock.by ? knock.by + " зовёт тебя " : "Тебя зовут ") +
    "в клан «" + knock.clan + "»." +
    (knock.when ? " Позвали " + knock.when + "." : "");
  const buttons = el("invite-buttons");
  buttons.textContent = "";
  buttons.appendChild(
    button("Вступить", {
      onClick: () => answerInvite({ action: "accept", clan: knock.clan_id }),
    })
  );
  buttons.appendChild(
    button("Отказаться", {
      secondary: true,
      onClick: () => answerInvite({ action: "refuse", clan: knock.clan_id }),
    })
  );
  veil.classList.remove("hidden");
}

async function answerInvite(payload) {
  el("invite-veil").classList.add("hidden");
  await clanAction(payload);
  await catchUp();
}

// Своя служба: по ней зажигается подпись под местоположением
let myService = null;

/** Строка клана в карточке и знаки у ника: и то и другое есть у мафии. */
function renderClanMarks(card) {
  const clan = card.clan || {};
  // Строку зажигает своя карточка, а не та, что открыта: чужую
  // открывают из списка клуба, и чужой семьёй не распоряжаются
  if (card.is_self) myClan = clan.emblem ? clan : null;
  if (card.is_self) myService = (card.service || {}).badge ? card.service : null;
  paintClanLine();
  paintDutyLine();
  // А знаки у ника — того бойца, чья карточка перед глазами
  ["hero-", "bag-"].forEach((prefix) => {
    const box = el(prefix + "clan");
    if (!box) return;
    box.textContent = "";
    clanMarks(clan).forEach((mark) => box.appendChild(mark));
    serviceMarks(card.service).forEach((mark) => box.appendChild(mark));
  });
}

/** Имя своей семьи под местоположением: вход в её состав. */
function paintClanLine() {
  const line = el("hero-clan-line");
  if (!line) return;
  line.classList.toggle("hidden", !myClan);
  line.textContent = "";
  if (!myClan) {
    // Из семьи вышли, а окно её состава осталось открытым — закрываем
    if (clanSheetOpen) closeSheet();
    clanData = null;
    return;
  }
  clanMarks(myClan).forEach((mark) => line.appendChild(mark));
  const name = document.createElement("span");
  name.className = "clan-line-name";
  name.textContent = myClan.name;
  const rank = document.createElement("span");
  rank.className = "clan-line-rank";
  rank.textContent = myClan.rank_title || "";
  line.append(name, rank);
}

/** Служба под местоположением: значок, служба и должность.

    У мафии такая строка есть с самого начала, и служивому она нужна
    ровно затем же: должность — это не запись в анкете, а то, кем боец
    в городе ходит. */
function paintDutyLine() {
  const line = el("hero-duty-line");
  if (!line) return;
  line.classList.toggle("hidden", !myService);
  line.textContent = "";
  if (!myService) return;
  serviceMarks(myService).forEach((mark) => line.appendChild(mark));
  const name = document.createElement("span");
  name.className = "clan-line-name";
  name.textContent = myService.title;
  const post = document.createElement("span");
  post.className = "clan-line-rank";
  post.textContent = myService.rank_title || "";
  line.append(name, post);
}

const INTERIOR_SCREENS = [
  "club", "shop", "magic", "workshop", "hospital", "insurance", "gym", "bank",
  "hr", "work", "school", "police", "mafia", "flats", "post", "tavern", "study",
  "trade", "house", "prison", "arena", "cars", "dorm", "charge",
  "dojo", "barber",
];

// Виды, которые не доехали. Помнить их приходится: карточка
// перерисовывается сама по себе — по сердцебиению, после боя, при
// возврате из чата, — а браузер второй раз за упавшую картинку не
// возьмётся. Без этого списка рамка после первой же перерисовки
// возвращалась бы пустой полосой и висела так до конца сеанса
const brokenInteriors = new Set();

function paintInterior() {
  const place = myPlace || {};
  // В пути боец ни в старом доме, ни в новом: вида изнутри у него нет.
  // Оставить картинку прежнего дома значило бы показать его там, где
  // его уже нет
  const src = place.seconds_left ? "" : place.interior || "";
  const show = Boolean(src) && !brokenInteriors.has(src);
  INTERIOR_SCREENS.forEach((screen) => {
    const box = el(screen + "-interior");
    const pic = el(screen + "-pic");
    if (!box || !pic) return;
    box.classList.toggle("hidden", !show);
    if (!show) return;
    pic.alt = place.title || "";
    // Тот же адрес заново не грузим: перерисовка идёт на каждой карточке,
    // а картинка тяжёлая
    if (pic.getAttribute("src") !== src) pic.src = src;
  });
}

// Файл не доехал — убираем рамку целиком: пустая полоса под заголовком
// хуже, чем её отсутствие. Дом со своей картинкой при этом не страдает:
// в списке павших лежат адреса, а не экраны
function watchInteriors() {
  INTERIOR_SCREENS.forEach((screen) => {
    const pic = el(screen + "-pic");
    if (!pic) return;
    pic.addEventListener("error", () => {
      brokenInteriors.add(pic.getAttribute("src"));
      paintInterior();
    });
  });
}

function paintCity(card) {
  if (card.is_self) myPlace = card.place || null;
  paintInterior();
  // Вышли из рейдового дома — рейд закрывается сам: держать его
  // открытым значит показывать то, во что с улицы всё равно не пустят
  if (!inRaidHouse() && RAID_HOUSES.includes(clubHouse)) {
    clubHouse = "club";
    clubSection = "fights";
    if (lastTab === "club") pickClubSection("fights");
  }
  // Строка под куклой: город и дом, в котором боец стоит. В пути дом
  // сменяется дорогой — иначе выходит, что он одновременно и там, и там
  const place = card.place || {};
  const line = place.seconds_left
    ? "🚶 В пути до дома «" + place.going_to + "» — " + place.seconds_left + " сек"
    : card.city + (place.title ? " · 📍 " + place.title : "");
  ["city", "hero-city"].forEach((id) => {
    el(id).textContent = line;
    el(id).classList.toggle("on-road", Boolean(place.seconds_left));
  });
}

function fail(message) {
  el("loader").classList.add("hidden");
  const box = el("error");
  box.querySelector(".error-text").textContent = message;
  box.classList.remove("hidden");
}

// Куда просили открыть приложение. Объявление в чате ведёт сюда же:
// «ring» — бои, «raid» — подвал, «shop» — лавка. Номер бойца в этом же
// месте означает чужую карточку, поэтому экраны названы словами.
const SCREEN_PARAMS = {
  shop: () => showTab("shop"),
  ring: () => {
    showTab("club");
    pickClubSection("fights");
  },
  raid: () => {
    // Из чата зовут в рейд — но выйти можно только из своего дома:
    // казино или стадиона. Кто не там, попадает на карту: пусть дойдёт
    if (inRaidHouse()) openRaidHouse(myPlace && myPlace.code);
    else showTab("map");
  },
};

function wantedScreen() {
  const params = new URLSearchParams(window.location.search);
  const asked =
    params.get("view") ||
    params.get("tgWebAppStartParam") ||
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) ||
    "";
  return Object.prototype.hasOwnProperty.call(SCREEN_PARAMS, asked) ? asked : "";
}

async function load() {
  const initData = tg ? tg.initData : "";
  const params = new URLSearchParams(window.location.search);
  const target = params.get("user_id") || params.get("tgWebAppStartParam") || "";
  const url = "api/card" + (target ? "?user_id=" + encodeURIComponent(target) : "");
  try {
    const response = await fetch(url, {
      headers: { "X-Telegram-Init-Data": initData || "" },
    });
    if (response.status === 404) {
      const body = await response.json();
      fail(body.message || "Бойца не нашли.");
      return;
    }
    if (!response.ok) {
      fail("Карточка открывается только из Telegram.");
      return;
    }
    const card = await response.json();
    render(card);
    showInvites(card.invites);
    // Бюллетень приезжает с карточкой: при входе в игру во время
    // голосования лист встаёт поверх любого экрана сам
    setBallot(card.ballot);
    const screen = wantedScreen();
    if (card.is_self && screen) SCREEN_PARAMS[screen]();
  } catch (error) {
    console.error("card load failed", error);
    reportOops(error, "загрузка карточки");
    fail("Не получилось загрузить карточку. Попробуй ещё раз.");
  }
}

el("hero-avatar").addEventListener("click", () => {
  if (!el("hero-avatar").classList.contains("clickable")) return;
  haptic((feedback) => feedback.selectionChanged());
  openLooks();
});
// Бармен на картинке — кнопка: с него и начинается разговор
el("tavern-npc").addEventListener("click", () => {
  haptic((feedback) => feedback.selectionChanged());
  barTalking = true;
  if (barData) renderBar(barData);
  else loadBar();
});
el("sheet-close").addEventListener("click", closeSheet);
el("sheet-back").addEventListener("click", closeSheet);
// Имя клана под местоположением открывает состав семьи
el("hero-clan-line").addEventListener("click", openClanSheet);
// Выход на карту есть в каждом доме: боец пришёл сюда ногами и уходит
// так же. Нижняя панель ведёт в клуб, в рюкзак и в карточку — то есть
// куда угодно, кроме того места, откуда он в этот дом зашёл
[
  "house", "hospital", "trade", "insurance", "gym", "bank", "hr", "work",
  "school", "police", "mafia", "flats", "post", "tavern", "study",
  "crowd", "elections", "shop", "magic", "prison", "arena", "cars", "dorm",
  "charge", "dojo", "barber", "workshop", "club",
].forEach((screen) => {
  el(screen + "-back").addEventListener("click", () => showTab("map"));
});
// Война — исключение: в неё заходят из дома, и возвращает она туда же
el("turf-back").addEventListener("click", () => showTab(turfFrom));
// Счётчик соседей в углу карты: нажал — открылся список дома
el("map-crowd").addEventListener("click", () => showTab("crowd"));
// Плашка выборов: нажал — бюллетень открылся снова
el("vote-chip").addEventListener("click", () => openBallot());
startVoteClock();
watchInteriors();

// Кнопок на панели меньше, чем экранов: лавки открываются с карты
TABS.forEach((tab) => {
  el("tab-" + tab).addEventListener("click", () => {
    // Казино открывает только его дверь. Кнопка внизу — всегда клуб,
    // даже если боец стоит в казино и только что смотрел рейд
    if (tab === "club") clubHouse = "club";
    showTab(tab);
  });
});
el("topup-back").addEventListener("click", () => showTab(lastTab));

// Мастерская: слоты открывают выбор, кнопка запускает модификацию
el("master-item").addEventListener("click", () => {
  masterPicking = masterPicking === "item" ? "" : "item";
  if (workshopData) renderWorkshop(workshopData);
});
el("master-mod").addEventListener("click", () => {
  masterPicking = masterPicking === "mod" ? "" : "mod";
  if (workshopData) renderWorkshop(workshopData);
});
el("master-go").addEventListener("click", runMaster);

if (tg) {
  tg.ready();
  tg.expand();
  if (tg.colorScheme === "dark") document.body.classList.add("dark");
  if (tg.onEvent) {
    tg.onEvent("themeChanged", () => {
      document.body.classList.toggle("dark", tg.colorScheme === "dark");
    });
  }
}

// Вернулись в приложение из чата — сверяемся с базой: пока нас не было,
// боец мог подраться и взять уровень
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) catchUp();
});
window.addEventListener("focus", catchUp);
setInterval(catchUp, CARD_HEARTBEAT);

load();
