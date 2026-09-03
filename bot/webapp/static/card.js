"use strict";

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

const el = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat("ru-RU");
const num = (value) => numberFormat.format(value);

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

function renderSlots(container, slots, own) {
  container.textContent = "";
  slots.forEach((slot) => {
    const box = document.createElement("div");
    box.className = "slot" + (slot.item ? "" : " empty");
    box.title = slot.title;
    box.appendChild(
      slot.item
        ? slotPicture(slot.item, slot.placeholder)
        : emptySlotPicture(slot, box)
    );
    box.addEventListener("click", () => {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
      if (slot.item && own) {
        // Клик по надетой вещи возвращает её в инвентарь, но не молча:
        // промахнуться по слоту легко, а вещь при этом слетает.
        confirmAction(
          "Вы уверены, что хотите снять предмет?\n" + slot.item.title
        ).then((ok) => {
          if (ok) act("api/unequip", { slot: slot.slot });
        });
      } else if (slot.item) {
        const bonus = slot.item.bonus ? "\n" + slot.item.bonus : "";
        const hands = slot.item.in_hands ? "\n" + slot.item.in_hands : "";
        popup(slot.item.title, slot.title + bonus + hands);
      } else {
        popup("Слот пуст", "Сюда надевается: " + slot.title + ".");
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
  head.textContent = section.emoji + " " + section.title;
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

function renderShop(data) {
  shopData = data;
  el("shop-purse").textContent = "";
  el("shop-purse").appendChild(purse(data.credits));
  el("shop-note").textContent = shopNote(data);
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

const SCREENS = ["club", "shop", "magic", "bag", "hero"];
let lastTab = "hero";

function showTab(name) {
  SCREENS.forEach((screen) => {
    el(screen).classList.toggle("hidden", screen !== name);
  });
  SCREENS.forEach((screen) => {
    el("tab-" + screen).classList.toggle("active", screen === name);
  });
  el("topup").classList.add("hidden");
  lastTab = name;
  window.scrollTo(0, 0);
  if (name === "shop" && !shopData) loadShop();
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

  const info = document.createElement("button");
  info.type = "button";
  info.className = "fighter-info";
  info.textContent = "i";
  info.title = "Карточка бойца";
  info.setAttribute("aria-label", "Карточка бойца " + fighter.nickname);
  info.addEventListener("click", () => showFighter(fighter));

  const stats = document.createElement("button");
  stats.type = "button";
  // Своим классом, а не «fighter-info»: две одинаковые кнопки в строке —
  // это неоднозначность и для теста, и для читалки экрана
  stats.className = "fighter-stats";
  stats.textContent = "📊";
  stats.title = "Статистика боёв";
  stats.setAttribute("aria-label", "Статистика боёв " + fighter.nickname);
  stats.addEventListener("click", () => {
    pickClubSection("stats");
    loadHistory(fighter.is_self ? null : fighter.user_id);
  });

  box.append(face, name, level, stats, info);
  return box;
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

function fighterCard(card) {
  const box = document.createDocumentFragment();
  box.appendChild(sheetDoll(card));

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
  ["fights", "players", "stats"].forEach((section) => {
    el("club-" + section).classList.toggle("hidden", section !== name);
  });
  renderClubSections();
  if (name === "players" && !clubData) loadClub();
  if (name === "stats" && !statsData) loadHistory(statsWho);
}

function renderClubSections() {
  const box = el("club-sections");
  box.textContent = "";
  [
    ["fights", "Бои"],
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
    turnDraft = { attack: null, block: null };
  }
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
let turnDraft = { attack: null, block: null };

function zoneColumn(title, rows, field) {
  const box = document.createElement("div");
  box.className = "zone-column";
  const head = document.createElement("p");
  head.className = "zone-head";
  head.textContent = title;
  box.appendChild(head);
  rows.forEach((row) => {
    const label = document.createElement("label");
    label.className = "zone" + (turnDraft[field] === row.zone ? " on" : "");
    const dot = document.createElement("input");
    dot.type = "radio";
    dot.name = "turn-" + field;
    dot.value = row.zone;
    dot.checked = turnDraft[field] === row.zone;
    dot.addEventListener("change", () => {
      turnDraft[field] = row.zone;
      paintDraft();
    });
    const text = document.createElement("span");
    text.textContent = row.title;
    label.appendChild(dot);
    label.appendChild(text);
    box.appendChild(label);
  });
  return box;
}

function paintDraft() {
  // Подсветка выбранного и кнопка отправки: пока не выбрано и то, и другое,
  // отправлять нечего
  document.querySelectorAll(".zone").forEach((label) => {
    const dot = label.querySelector("input");
    label.classList.toggle("on", Boolean(dot && dot.checked));
  });
  const go = el("turn-go");
  if (go) go.disabled = !(turnDraft.attack && turnDraft.block);
}

function turnForm(data) {
  const box = document.createElement("div");
  box.className = "turn-form";

  const columns = document.createElement("div");
  columns.className = "zone-columns";
  columns.appendChild(zoneColumn("Атака", data.attacks, "attack"));
  columns.appendChild(zoneColumn("Защита", data.blocks, "block"));
  box.appendChild(columns);

  const go = document.createElement("button");
  go.type = "button";
  go.id = "turn-go";
  go.className = "btn wide";
  go.textContent = "Вперёд!";
  go.disabled = !(turnDraft.attack && turnDraft.block);
  go.addEventListener("click", () => {
    const move = { action: "turn", attack: turnDraft.attack, block: turnDraft.block };
    turnDraft = { attack: null, block: null };
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
  el("stats-note").textContent = data.total
    ? data.name + ": " + data.total + " " +
      plural(data.total, "бой", "боя", "боёв") + " — " +
      counts.win + " побед, " + counts.loss + " поражений, " +
      counts.draw + " ничьих"
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
  note.textContent =
    fight.mode.emoji + " " + fight.mode.title + ", раундов " + fight.rounds;
  row.appendChild(line);
  row.appendChild(note);
  row.addEventListener("click", () => openFightLog(fight.id));
  return row;
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
    ? "Все, кто завёл бойца. Кнопка «i» открывает карточку."
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
  el("city").textContent = card.city;
  el("hero-city").textContent = card.city;

  const stats = el("stats");
  stats.textContent = "";
  card.stats.forEach((stat) => {
    stats.appendChild(row(stat.emoji + " " + stat.title, statValue(stat)));
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
        "До апа",
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
  const hit = c.weapon_damage.length
    ? c.damage_min + "–" + c.damage_max +
      c.weapon_damage
        .map((w) => " + " + (w.icon || "") + w.min + "–" + w.max)
        .join("")
    : c.damage_min + "–" + c.damage_max;
  combat.appendChild(row("👊 Урон", hit));
  // Откуда взялась прибавка от оружия: у меча 7–15, а класс проворачивает
  // его на 6–14. Без этой строки два числа на одном экране противоречат
  // друг другу, хотя оба верны.
  c.weapon_damage.forEach((w) => {
    const same = w.base === w.min + "–" + w.max;
    combat.appendChild(
      row(
        (w.icon || "") + " " + (w.title || "Оружие"),
        same ? w.base : w.base + " → " + w.min + "–" + w.max,
        "weapon"
      )
    );
  });
  combat.appendChild(row("💥 Крит", c.crit_chance + "% ×" + c.crit_power));
  combat.appendChild(row("🚫 Антикрит", c.anticrit + "%"));
  combat.appendChild(row("🌀 Уворот", c.dodge_chance + "%"));
  combat.appendChild(row("🎯 Точность", c.accuracy + "%"));
  combat.appendChild(row("🔄 Контрудар", c.counter_chance + "%"));
  // Насколько крепко держится блок, когда в него упирается крит: то, что
  // не удержалось, проходит половиной максимального урона
  combat.appendChild(row("🛡🩸 Держит блок", c.block_hold + "%"));
  combat.appendChild(row("🪨 Сопротивление", c.resist + "%"));
  combat.appendChild(row("🪚 Пробивание", c.penetration + "%"));
  const armor = card.armor.filter((zone) => zone.max > 0);
  if (armor.length) {
    combat.appendChild(
      row(
        "🛡 Броня",
        armor.map((z) => z.emoji + " " + z.min + "–" + z.max).join("  ")
      )
    );
  }

  const record = el("record");
  record.textContent = "";
  record.appendChild(row("Побед", num(card.record.wins)));
  record.appendChild(row("Поражений", num(card.record.losses)));
  record.appendChild(row("Ничьих", num(card.record.draws)));
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

function fail(message) {
  el("loader").classList.add("hidden");
  const box = el("error");
  box.querySelector(".error-text").textContent = message;
  box.classList.remove("hidden");
}

function wantsShop() {
  const params = new URLSearchParams(window.location.search);
  return (
    params.get("view") === "shop" ||
    params.get("tgWebAppStartParam") === "shop" ||
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param === "shop")
  );
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
    if (card.is_self && wantsShop()) showTab("shop");
  } catch (error) {
    console.error("card load failed", error);
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

SCREENS.forEach((screen) => {
  el("tab-" + screen).addEventListener("click", () => showTab(screen));
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

load();
