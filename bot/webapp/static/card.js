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
  return box;
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
  if (item.bonus) parts.push(item.bonus);
  if (item.in_hands) parts.push(item.in_hands);
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

function renderSlots(container, slots, own) {
  container.textContent = "";
  slots.forEach((slot) => {
    // Картинкой показываем верхнюю вещь; если её нет, а нижняя есть — нижнюю.
    // Пустой клетка считается, только когда в ней нет ни одной.
    const shown = slot.item || slot.under;
    const box = document.createElement("div");
    box.className = "slot" + (shown ? "" : " empty");
    box.title = slotHint(slot);
    box.appendChild(
      shown ? slotPicture(shown, slot.placeholder) : emptySlotPicture(slot, box)
    );
    box.addEventListener("click", () => {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
      if (!slot.item && slot.under) {
        // В клетке только нижняя вещь — снимаем её
        if (own) {
          confirmAction(
            "Вы уверены, что хотите снять предмет?\n" + slot.under.title
          ).then((ok) => {
            if (ok) act("api/unequip", { slot: slot.under.slot });
          });
        } else {
          popup(slot.under.title, slotHint(slot));
        }
        return;
      }
      if (slot.item && own) {
        // Клик по надетой вещи возвращает её в инвентарь, но не молча:
        // промахнуться по слоту легко, а вещь при этом слетает.
        confirmAction(
          "Вы уверены, что хотите снять предмет?\n" + slot.item.title
        ).then((ok) => {
          if (ok) act("api/unequip", { slot: slot.slot });
        });
      } else if (slot.item) {
        popup(slot.item.title, slotHint(slot));
      } else {
        popup("Слот пуст", "Сюда надевается: " + slot.cell_title + ".");
      }
    });
    container.appendChild(box);
  });
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
      btn.addEventListener("click", () => popup("Нельзя надеть", options.hint));
    }
  } else if (options && options.onClick) {
    btn.addEventListener("click", options.onClick);
  }
  return btn;
}

function thingCard(item, credits, shop) {
  const box = document.createElement("div");
  box.className = "thing" + (shop && !item.unlocked ? " locked" : "");

  const pic = document.createElement("div");
  pic.className = "thing-pic";
  pic.appendChild(slotPicture(item, item.icon));
  box.appendChild(pic);

  const body = document.createElement("div");
  body.className = "thing-body";

  const title = document.createElement("div");
  title.className = "thing-title";
  title.textContent = item.title;
  body.appendChild(title);

  const kind = document.createElement("div");
  kind.className = "thing-kind";
  kind.textContent = item.slot_title;
  body.appendChild(kind);

  if (!shop && item.consumable) {
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

  if (!shop && !item.consumable) {
    const wear = document.createElement("div");
    const left = item.max_wear - item.wear;
    wear.className = "thing-wear" + (left <= 1 ? " dying" : item.wear ? " worn" : "");
    wear.textContent = "🔧 Износ: " + item.wear_text;
    if (left <= 1) wear.textContent += " — ещё один бой, и рассыплется";
    body.appendChild(wear);
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
          : button(
              item.affordable
                ? "Купить · " + item.price + " 💰"
                : "Не хватает кредитов",
              { disabled: !item.affordable, onClick: () => purchase(item) }
            )
      );
      body.appendChild(buy);
    }
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

  if (item.wear > 0) {
    const affordable = Math.min(item.wear, credits);
    const full = item.repair_price <= credits;
    const label = full
      ? "Чинить · " + item.repair_price + " 💰"
      : affordable > 0
        ? "Чинить на " + affordable + " 💰"
        : "Чинить · " + item.repair_price + " 💰";
    buttons.appendChild(
      button(label, {
        secondary: true,
        disabled: affordable <= 0,
        onClick: () => repair(item, full ? null : affordable),
      })
    );
  }

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

function requirementText(item) {
  return item.requirements
    .filter((need) => !need.ok)
    .map((need) => need.title.toLowerCase() + " " + need.need)
    .join(", ");
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
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
  ["effects", "hero-effects"].forEach((id) => {
    const box = el(id);
    box.textContent = "";
    box.classList.toggle("hidden", live.length === 0);
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
}

function startEffects(list) {
  effects = list || [];
  effectsAt = Date.now();
  paintEffects();
}

// ---------- магазин ----------

// Что показывать на прилавке. Фильтр один — тип вещи; уровень не фильтруем:
// закрытое и так свёрнуто в конце каждой полки.
const filters = { slot: "all" };

function chip(label, active, onClick, extraClass) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chip" + (active ? " on" : "") + (extraClass ? " " + extraClass : "");
  btn.textContent = label;
  btn.addEventListener("click", () => {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
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
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  try {
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

function renderMarket(data) {
  marketData = data;
  el("shop-purse").textContent = "";
  el("shop-purse").appendChild(purse(data.credits));
  const count = data.sections.reduce((all, row) => all + row.items.length, 0);
  el("market-note").textContent = count
    ? "Вещи игроков клуба. Клуб берёт " + data.fee + "% с каждой продажи."
    : "На комиссии пусто. Выставь своё — заберут.";

  const body = el("market-body");
  body.textContent = "";
  body.appendChild(sellBox(data));
  data.sections.forEach((section) => body.appendChild(marketShelf(section)));
}

function sellBox(data) {
  // Что можно выставить: всё, что лежит в рюкзаке и не надето
  const box = document.createElement("section");
  box.className = "shelf";
  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = "🤝 Выставить своё";
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

  const pic = document.createElement("div");
  pic.className = "thing-pic";
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
  const wear = document.createElement("div");
  wear.className = "thing-wear" + (row.wear ? " worn" : "");
  wear.textContent = "🔧 Износ: " + row.wear_text;
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

  const pic = document.createElement("div");
  pic.className = "thing-pic";
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
  const wear = document.createElement("div");
  wear.className = "thing-wear" + (lot.wear ? " worn" : "");
  wear.textContent = "🔧 Износ: " + lot.wear_text;
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
      : button(
          lot.affordable ? "Купить · " + lot.price + " 💰" : "Не хватает кредитов",
          {
            disabled: !lot.affordable,
            onClick: () => buyLot(lot),
          }
        )
  );
  body.appendChild(buttons);

  box.appendChild(body);
  return box;
}

const SCREENS = ["club", "map", "shop", "magic", "bag", "hero"];
// Вкладок меньше, чем экранов: лавки открываются с карты, а не с панели.
// Пока в них стоишь, горит «Карта» — оттуда в них и пришли
const TABS = ["club", "map", "bag", "hero"];
const OPENED_FROM = { shop: "map", magic: "map" };
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
  if (name === "map") loadMap();
  if (name === "club" && !clubData) loadClub();
  if (name === "magic" && !magicData) loadMagic();
  // Ринг опрашиваем, только пока на него смотрят: ушли со вкладки — молчим
  if (name === "club") startWatchingFights();
  else stopWatchingFights();
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
let roadTimer = null;

async function loadMap() {
  try {
    const response = await fetch("api/map", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" },
    });
    if (!response.ok) throw new Error("Карта не открылась.");
    renderMap(await response.json());
  } catch (error) {
    el("map-note").textContent = error.message;
  }
}

function renderMap(data) {
  mapData = data;
  // Пришли на карту — показываем тот район, где стоим. Дальше человек
  // листает сам, и его выбор не сбрасывается каждым обновлением
  if (!mapShown || !data.districts.some((one) => one.code === mapShown)) {
    mapShown = data.district || data.districts[0].code;
  }
  el("map-here").textContent = data.here_title;
  renderDistricts();
  paintDistrict();
  paintRoad();
}

function renderDistricts() {
  const box = el("map-districts");
  box.textContent = "";
  mapData.districts.forEach((district) => {
    const label = district.here ? district.title + " ·" : district.title;
    box.appendChild(
      chip(label, district.code === mapShown, () => {
        mapShown = district.code;
        renderDistricts();
        paintDistrict();
      })
    );
  });
}

function shownDistrict() {
  return mapData.districts.find((one) => one.code === mapShown);
}

function paintDistrict() {
  const district = shownDistrict();
  if (!district) return;
  const pic = el("map-pic");
  if (pic.getAttribute("src") !== district.image) pic.src = district.image;
  pic.alt = "Район: " + district.title;
  el("map-note").textContent = district.here
    ? "Ты в этом районе. Нажми на дом, чтобы зайти."
    : "Другой район. Нажми на дом — боец пойдёт туда.";
  placeZones();
}

/** Куда картинка легла внутри рамки: без полей по краям и с ними. */
function drawnBox(pic) {
  const width = pic.clientWidth;
  const height = pic.clientHeight;
  const natural = pic.naturalWidth && pic.naturalHeight
    ? pic.naturalWidth / pic.naturalHeight
    : MAP_RATIO;
  if (!width || !height) return null;
  // Картинка вписывается целиком: по ширине, если рамка выше, и по
  // высоте, если рамка шире. Остаток по краям — те самые поля
  const drawnWidth = Math.min(width, height * natural);
  const drawnHeight = drawnWidth / natural;
  return {
    left: (width - drawnWidth) / 2,
    top: (height - drawnHeight) / 2,
    width: drawnWidth,
    height: drawnHeight,
  };
}

// Соотношение сторон нарисованных карт: 941×1672. Нужно, только пока
// картинка не загрузилась и своего размера ещё не назвала
const MAP_RATIO = 941 / 1672;

function placeZones() {
  const district = shownDistrict();
  const box = el("map-zones");
  box.textContent = "";
  if (!district) return;
  const drawn = drawnBox(el("map-pic"));
  if (!drawn) return;

  const put = (zone, node) => {
    node.style.left = drawn.left + zone.x * drawn.width + "px";
    node.style.top = drawn.top + zone.y * drawn.height + "px";
    node.style.width = zone.w * drawn.width + "px";
    node.style.height = zone.h * drawn.height + "px";
    box.appendChild(node);
  };

  district.places.forEach((place) => put(place.zone, houseButton(place)));
  put(mapData.exit_zone, exitButton());
}

function houseButton(place) {
  const node = document.createElement("button");
  node.type = "button";
  node.className =
    "zone-house" + (place.here ? " here" : "") + (place.works ? "" : " soon");
  node.dataset.code = place.code;
  node.setAttribute("aria-label", place.title);

  const sign = document.createElement("span");
  sign.className = "zone-sign";
  sign.textContent = place.here ? "📍 " + place.title : place.title;
  node.appendChild(sign);

  node.addEventListener("click", () => enterHouse(place));
  return node;
}

function exitButton() {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "zone-exit";
  node.id = "map-exit";
  node.setAttribute("aria-label", "Выбрать район");
  node.textContent = "⬆ Районы";
  node.addEventListener("click", () => {
    el("map-districts").scrollIntoView({ block: "center" });
  });
  return node;
}

/** Что открывает дом, если боец уже в нём. */
const HOUSE_SCREENS = {
  fight: () => {
    showTab("club");
    pickClubSection("fights");
  },
  raid: () => {
    showTab("club");
    pickClubSection("raid");
  },
  weapons: () => openShop(),
  clothes: () => openShop(),
  potions: () => openShop(),
  premium: () => showTab("magic"),
  market: () => {
    pickShopSection("market");
    showTab("shop");
  },
  repair: () => {
    showTab("bag");
    popup("Мастерская", "Чинят вещи в рюкзаке: у каждой своя кнопка починки.");
  },
};

async function enterHouse(place) {
  if (place.here) {
    if (!place.works) {
      popup(place.title, "Скоро здесь появится новая услуга: " + place.soon + ".");
      return;
    }
    const open = HOUSE_SCREENS[place.services[0]];
    if (open) open();
    return;
  }
  if (mapData.road.going) {
    popup("Ты в пути", mapData.road.text);
    return;
  }
  // До дома без услуги дойти можно: город не должен выглядеть
  // наполовину нарисованным. Что он пока пуст, скажем уже на месте
  // Спрашиваем до выхода: дорога занимает время, и уходить молча нечестно
  const go = await askConfirm(
    "Идём?",
    "Дойти до дома «" + place.title + "» — " + walkText(place)
  );
  if (go) await travelTo(place.code);
}

function walkText(place) {
  // Сколько идти, считает сервер: у него же и решение, пускать ли
  return place.walk + " сек пути.";
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

function paintRoad() {
  const road = mapData.road;
  const line = el("map-road");
  line.classList.toggle("hidden", !road.going);
  if (!road.going) {
    if (roadTimer) clearInterval(roadTimer);
    roadTimer = null;
    return;
  }
  let left = road.seconds_left;
  const tick = () => {
    line.textContent = left > 0
      ? "🚶 В пути до дома «" + road.to_title + "» — " + left + " сек"
      : "🚶 Пришли.";
    if (left <= 0) {
      clearInterval(roadTimer);
      roadTimer = null;
      loadMap();
      catchUp();
      return;
    }
    left -= 1;
  };
  tick();
  if (roadTimer) clearInterval(roadTimer);
  roadTimer = setInterval(tick, 1000);
}

// Окно меняет размер — картинка ложится иначе, и дома едут вместе с ней
window.addEventListener("resize", () => {
  if (mapData && !el("map").classList.contains("hidden")) placeZones();
});
el("map-pic").addEventListener("load", placeZones);

// ---------- лавка мага ----------

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

  const note = document.createElement("div");
  note.className = "thing-note";
  note.textContent = pro.note;
  body.appendChild(note);

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
    "Товар мага берут за звёзды Telegram — кредиты тут не в ходу. "
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    const got = data.pro;
    const extras = [];
    if (got.blade) extras.push("клинок ассасина — в инвентаре");
    if (got.look) extras.push("образ ассасина — в гардеробе");
    popup(
      "💎 " + pro.title,
      (got.renewed ? "Подписка продлена на " : "Подписка на ") + got.days + " дней."
        + (extras.length ? "\n" + extras.join("\n") : "")
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
    if (!response.ok) throw new Error("Лавка мага закрыта.");
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
  name.textContent = fighter.nickname;

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

  box.append(face, name, level, info);
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

  // Здоровье — первое, что хотят знать о чужом бойце: цел он или отлёживается
  if (card.hp) {
    box.appendChild(sheetHealth(card.hp));
    const note = document.createElement("p");
    note.className = "hp-note sheet-hp-note";
    note.textContent = card.hp.state_title
      ? card.hp.state_title.charAt(0).toUpperCase() + card.hp.state_title.slice(1)
      : "";
    if (card.hp.ready_in_text) note.textContent += " · в строю через " + card.hp.ready_in_text;
    box.appendChild(note);
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
  clubSection = name;
  ["fights", "battle", "raid", "players", "stats"].forEach((section) => {
    el("club-" + section).classList.toggle("hidden", section !== name);
  });
  renderClubSections();
  if (name === "players" && !clubData) loadClub();
  if (name === "stats" && !statsData) loadHistory(statsWho);
  // Рейд живёт волнами: пока раздел открыт, спрашиваем состояние
  if (name === "raid") startWatchingRaid();
  else stopWatchingRaid();
  // Групповой бой тоже идёт раундами и без тебя — следим так же
  if (name === "battle") startWatchingBattle();
  else stopWatchingBattle();
}

function renderClubSections() {
  const box = el("club-sections");
  box.textContent = "";
  // Рейда среди пузырей нет: в подвал спускаются из казино на карте.
  // Раздел жив и открывается оттуда — но зайти в него мимо казино нельзя
  [
    ["fights", "Бои"],
    ["battle", "Отряд"],
    ["players", "Игроки"],
    ["stats", "Статистика"],
  ].forEach(([code, label]) => {
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
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  try {
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

function turnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";

  // Столбцов ударов столько, сколько рук с оружием, а блок бывает шире:
  // со щитом он держит три зоны вместо двух
  const hands = data.duel.hands || [{ hand: 0, icon: "👊", title: "Кулаки" }];
  const blocks = data.duel.blocks || data.blocks;

  box.appendChild(
    zoneColumns(
      hands, data.attacks, blocks, () => turnDraft, "turn", paintDraft
    )
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

function judgeLines(turn, into) {
  (turn.lines || []).forEach((said, index) => {
    into.appendChild(judgeLine(said, (turn.strikes || [])[index]));
  });
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
let raidWasOver = false;
// Развёрнута ли карточка босса под заголовком
let bossOpen = false;
let raidBusy = false;
let raidClock = null;
// Что нарисовано на экране: по этой метке видно, изменилось ли хоть что-то
let raidPainted = null;
let raidDraft = { attacks: {}, block: null };
let raidWave = null;

function startWatchingRaid() {
  if (raidTimer) return;
  loadRaid();
  raidTimer = setInterval(loadRaid, 2000);
  // Часы тикают чаще, чем ходит опрос: секунда на экране должна быть секундой
  if (!raidClock) raidClock = setInterval(paintClocks, 1000);
}

function stopWatchingRaid() {
  if (!raidTimer) return;
  clearInterval(raidTimer);
  raidTimer = null;
  if (raidClock) clearInterval(raidClock);
  raidClock = null;
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
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  try {
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
      raid.boss.hp, raid.log.length,
      raid.party.map((one) => [one.user_id, one.hp, one.alive, one.acted]),
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

  // Ничего не поменялось — не трогаем экран. Опрос идёт каждые две секунды,
  // и перерисовка схлопывала бы под пальцем открытый список, гасила фокус и
  // сбрасывала прокрутку. Часы в объявлениях тикают отдельно, сами.
  const shape = raidShape(data);
  const body = el("raid-body");
  if (shape === raidPainted && body.firstChild) return;
  raidPainted = shape;
  body.textContent = "";
  if (data.boss) {
    body.appendChild(raidHead(data.boss));
    if (bossOpen) body.appendChild(bossStats(data.boss));
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

function raidHead(boss) {
  // «Ограбление Босса подпольного казино (i)» — заголовок и всё о нём
  const box = document.createElement("div");
  box.className = "raid-head";
  const title = document.createElement("h2");
  title.className = "shelf-head";
  title.textContent = boss.raid_name || "Рейд против " + bossGenitive(boss.title);
  box.appendChild(title);

  const info = document.createElement("button");
  info.type = "button";
  info.className = "info-btn";
  info.id = "boss-info";
  info.textContent = "i";
  info.title = "Характеристики босса";
  info.addEventListener("click", () => {
    bossOpen = !bossOpen;
    renderRaid(raidData);
  });
  box.appendChild(info);
  return box;
}

function bossGenitive(title) {
  // «Босс Подвала» → «Босса Подвала»: склоняем только то, что знаем сами.
  // Незнакомое имя оставляем как есть — лучше косо, чем неверно.
  return title.startsWith("Босс ") ? "Босса " + title.slice(5) : title;
}

function bossStats(boss) {
  const box = document.createElement("div");
  box.className = "boss-stats";

  // Кукла босса — тем же кодом, что и карточка бойца: аватар в середине,
  // слоты по бокам, под пустыми — подложки
  if (boss.slots) box.appendChild(sheetDoll(boss));
  if (boss.tagline) {
    const line = document.createElement("p");
    line.className = "screen-note";
    line.textContent = boss.tagline;
    box.appendChild(line);
  }

  const note = document.createElement("p");
  note.className = "screen-note";
  note.textContent = boss.live
    ? "Это босс идущего рейда."
    : "Так он выйдет на твой уровень: он всегда на " + boss.levels_above +
      " уровня выше отряда, а здоровья набирает с каждым бойцом.";
  box.appendChild(note);

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
  const box = document.createElement("div");
  box.className = "fight-open";

  const line = document.createElement("p");
  line.className = "fight-line";
  line.textContent = gate.open
    ? "Подвал открыт " + gate.window + "."
    : "Подвал закрыт. Босса бьют " + gate.schedule + ".";
  box.appendChild(line);

  const note = document.createElement("p");
  note.className = "fight-row-note";
  note.textContent = gate.won
    ? "Босс повержен: в это окно ты своё взял. Следующее — " +
      gate.next_window + "."
    : gate.spent
      ? "Пропуск за это окно отдан — заходи хоть до самого конца."
      : gate.open
        ? "Вход по пропуску. В инвентаре: " + (gate.passes || 0) + " шт."
        : "Ближайшее окно " + gate.next_window + ".";
  box.appendChild(note);

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

  const head = document.createElement("p");
  head.className = "fight-round";
  head.textContent = raid.finished
    ? "🔔 Рейд окончен"
    : raid.resting
      ? "😮‍💨 Передышка"
      : "🔔 Волна " + raid.wave;
  box.appendChild(head);
  box.appendChild(bossCard(raid.boss));
  const versus = document.createElement("p");
  versus.className = "versus";
  versus.textContent = "VS";
  box.appendChild(versus);
  box.appendChild(partyBoard(raid.party));

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
  } else if (raid.acted) {
    const wait = document.createElement("p");
    wait.className = "fight-line";
    wait.textContent = "Удар засчитан. Ждём остальных.";
    box.appendChild(wait);
  } else {
    box.appendChild(raidTurnForm(data));
  }

  if (raid.log.length) box.appendChild(raidLog(raid));
  return box;
}

function bossCard(boss) {
  const box = document.createElement("div");
  box.className = "boss-card";
  if (boss.image) {
    const img = document.createElement("img");
    img.className = "boss-face";
    img.src = boss.image;
    img.alt = boss.title;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => img.remove());
    box.appendChild(img);
  }
  const side = document.createElement("div");
  side.className = "boss-side";
  const name = document.createElement("p");
  name.className = "fight-name";
  name.textContent = boss.emoji + " " + boss.title + " [" + boss.level + "]";
  const hp = document.createElement("p");
  hp.className = "fight-hp";
  hp.textContent = boss.hp + "/" + boss.max_hp;
  side.appendChild(name);
  side.appendChild(hp);
  side.appendChild(fightBar(boss));
  box.appendChild(side);
  return box;
}

function partyBoard(party) {
  const box = document.createElement("div");
  box.className = "raid-party";
  party.forEach((member) => {
    const row = document.createElement("div");
    row.className = "raid-member" + (member.alive ? "" : " down");
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
    row.appendChild(name);
    row.appendChild(hp);
    row.appendChild(fightBar(member));
    box.appendChild(row);
  });
  return box;
}

function raidHands(data) {
  return (data.raid && data.raid.hands) || [{ hand: 0, icon: "👊", title: "Кулаки" }];
}

function raidReady(hands) {
  return Boolean(raidDraft.block) && hands.every((row) => raidDraft.attacks[row.hand]);
}

function raidTurnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";
  const hands = raidHands(data);
  const blocks = (data.raid && data.raid.blocks) || data.blocks;

  box.appendChild(
    zoneColumns(
      hands, data.attacks, blocks, () => raidDraft, "raid", paintRaidDraft
    )
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
  raid.log.slice().reverse().forEach((turn) => judgeLines(turn, box));
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
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  try {
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

function battleOpenForm(data) {
  const box = document.createElement("div");
  box.className = "fight-open";
  const line = document.createElement("p");
  line.className = "fight-line";
  line.textContent =
    "Командный бой — сторона на сторону, королевская битва — каждый сам за " +
    "себя. Уровни подбираются по твоему.";
  box.appendChild(line);

  data.kinds.forEach((kind) => {
    const row = document.createElement("div");
    row.className = "raid-sizes";
    [kind.min, kind.max].forEach((size) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn wide";
      btn.textContent =
        kind.emoji + " " + kind.title + " на " + size;
      btn.disabled = !data.can_fight;
      btn.addEventListener("click", () =>
        battleAction({ action: "open", kind: kind.code, size: size })
      );
      row.appendChild(btn);
    });
    box.appendChild(row);
  });
  return box;
}

function battleLobby(lobby, mine) {
  const box = document.createElement("div");
  box.className = "fight-card";
  const head = document.createElement("p");
  head.className = "fight-line";
  head.textContent =
    lobby.emoji + " " + lobby.kind_title + " — " +
    lobby.total + "/" + lobby.capacity +
    " · уровни " + lobby.min_level + "–" + lobby.max_level;
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
  } else {
    box.appendChild(battleTurnForm(data));
  }

  if (battle.log.length) box.appendChild(battleLog(battle));
  return box;
}

function battleBoard(battle) {
  const box = document.createElement("div");
  box.className = "raid-party";
  battle.party.forEach((member) => {
    const row = document.createElement("div");
    row.className = "raid-member" + (member.alive ? "" : " down");
    const name = document.createElement("p");
    name.className = "fight-name";
    name.textContent =
      (member.alive ? (member.ready ? "✅ " : "⏳ ") : "💀 ") +
      member.emoji + " " + member.name + " [" + member.level + "]" +
      (battle.kind === "team" ? " · " + member.team_title : "") +
      (member.you ? " — ты" : "");
    const hp = document.createElement("p");
    hp.className = "fight-hp";
    hp.textContent =
      member.hp + "/" + member.max_hp + " · урона " + member.damage_dealt +
      (member.rival ? " · против " + member.rival : " · без пары");
    row.appendChild(name);
    row.appendChild(hp);
    row.appendChild(fightBar(member));
    box.appendChild(row);
  });
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
        if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
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
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
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

async function repair(item, points) {
  if (busy) return;
  busy = true;
  try {
    const data = await post("api/repair", { item_id: item.id, points: points });
    render(data.card, true);
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
}

function openSheet(title, note) {
  el("sheet-title").textContent = title;
  el("sheet-note").textContent = note || "";
  el("sheet-list").textContent = "";
  el("sheet").classList.remove("hidden");
}

function lookTile(look) {
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
  box.addEventListener("click", () => pickLook(look));
  return box;
}

function renderLooks(looks) {
  const list = el("sheet-list");
  list.textContent = "";
  [
    ["male", "Мужские"],
    ["female", "Женские"],
  ].forEach(([gender, title]) => {
    const head = document.createElement("div");
    head.className = "look-group";
    head.textContent = title;
    const grid = document.createElement("div");
    grid.className = "look-grid";
    looks
      .filter((look) => look.gender === gender)
      .forEach((look) => grid.appendChild(lookTile(look)));
    list.append(head, grid);
  });
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
      "Шесть образов открыты всем, остальные покупаются раз и навсегда. "
      + "На бой образ не влияет.";
    renderLooks(data.looks);
  } catch (error) {
    el("sheet-note").textContent = error.message;
  }
}

async function pickLook(look) {
  if (busy || look.current) return;
  if (!look.owned) {
    const ok = await confirmAction(
      "Купить образ «" + look.title + "» за " + num(look.price) + " кредитов?"
    );
    if (!ok) return;
  }
  busy = true;
  try {
    const data = await post("api/look", { code: look.code });
    render(data.card, true);
    renderLooks(data.looks);
    shopData = null;  // кредитов могло стать меньше
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    if (data.chosen.bought) {
      popup(
        "Образ куплен",
        "«" + data.chosen.title + "» теперь твой навсегда. "
          + "На счету осталось " + num(data.chosen.credits) + " 💰."
      );
    }
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
    return;
  }
  const img = document.createElement("img");
  img.src = card.avatar.url;
  img.alt = card.name;
  img.addEventListener("error", () => {
    box.textContent = "";
    box.appendChild(emoji());
  });
  box.appendChild(img);
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
  // Полоска стоит и в инвентаре, и на карточке персонажа: тикают обе
  if (!health) return;
  paintOneBar("");
  paintOneBar("hero-");
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

function render(card, keepTab) {
  renderHead("bag-", card);
  renderHead("hero-", card);
  document.title = card.name + " [" + card.level + "]";

  startHealthTicker(card.hp);
  startEffects(card.effects);
  renderAvatar(card);
  renderSlots(el("slots-left"), card.slots.left, card.is_self);
  renderSlots(el("slots-right"), card.slots.right, card.is_self);
  renderSlots(el("hero-slots-left"), card.slots.left, card.is_self);
  renderSlots(el("hero-slots-right"), card.slots.right, card.is_self);
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

function paintCity(card) {
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
    showTab("club");
    pickClubSection("raid");
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
  if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
  openLooks();
});
el("sheet-close").addEventListener("click", closeSheet);
el("sheet-back").addEventListener("click", closeSheet);

// Кнопок на панели меньше, чем экранов: лавки открываются с карты
TABS.forEach((tab) => {
  el("tab-" + tab).addEventListener("click", () => showTab(tab));
});
el("topup-back").addEventListener("click", () => showTab(lastTab));

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
