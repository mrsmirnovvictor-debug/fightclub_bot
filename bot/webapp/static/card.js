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

function slotPicture(item, placeholder) {
  if (item && item.image) {
    const img = document.createElement("img");
    img.src = item.image;
    img.alt = item.title;
    img.addEventListener("error", () => {
      img.replaceWith(document.createTextNode(item.icon));
    });
    return img;
  }
  return document.createTextNode(item ? item.icon : placeholder);
}

function renderSlots(container, slots, own) {
  container.textContent = "";
  slots.forEach((slot) => {
    const box = document.createElement("div");
    box.className = "slot" + (slot.item ? "" : " empty");
    box.title = slot.title;
    box.appendChild(slotPicture(slot.item, slot.placeholder));
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
        popup(slot.item.title, slot.title + bonus);
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

  if (!shop) {
    const wear = document.createElement("div");
    const left = item.max_wear - item.wear;
    wear.className = "thing-wear" + (left <= 1 ? " dying" : item.wear ? " worn" : "");
    wear.textContent = "🔧 Износ: " + item.wear_text;
    if (left <= 1) wear.textContent += " — ещё один бой, и рассыплется";
    body.appendChild(wear);
  }

  if (shop) {
    const price = document.createElement("div");
    price.className = "thing-price";
    price.textContent = item.price + " 💰";
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
      owned.textContent = item.owned > 1
        ? "✔ уже есть, штук: " + item.owned
        : "✔ уже есть";
      body.appendChild(owned);
    }
  }

  const reqLabel = document.createElement("div");
  reqLabel.className = "thing-label";
  reqLabel.textContent = "Требования";
  body.append(reqLabel, requirementList(item));

  if (item.bonuses.length) {
    const gainLabel = document.createElement("div");
    gainLabel.className = "thing-label";
    gainLabel.textContent = "Даёт надетой";
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
        button(item.affordable ? "Купить · " + item.price + " 💰" : "Не хватает кредитов", {
          disabled: !item.affordable,
          onClick: () => purchase(item),
        })
      );
      body.appendChild(buy);
    }
    box.appendChild(body);
    return box;
  }

  const buttons = document.createElement("div");
  buttons.className = "thing-buttons";
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
  const bag = el("bag");
  if (!card.is_self) {
    bag.classList.add("hidden");
    return;
  }
  bag.classList.remove("hidden");
  el("bag-count").textContent = card.inventory.length
    ? "· " + card.inventory.length
    : "";
  el("bag-empty").classList.toggle("hidden", card.inventory.length > 0);

  const list = el("bag-list");
  list.textContent = "";
  card.inventory.forEach((item) => {
    list.appendChild(thingCard(item, card.record.credits));
  });
}

// ---------- магазин ----------

// Что показывать на прилавке: тип вещи и уровень партии.
// Фильтры живут на клиенте — витрина приходит целиком, одним запросом.
const filters = { slot: "all", level: "all" };

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

function pickLevel(value) {
  filters.level = value;
  renderShop(shopData);
}

function shownItems(section) {
  if (filters.level === "open") return section.items.filter((item) => item.unlocked);
  if (filters.level === "all") return section.items.filter((item) => item.unlocked);
  return section.items.filter((item) => item.level_required === filters.level);
}

function hiddenItems(section) {
  // Закрытое прячем, только пока не выбран конкретный уровень
  if (filters.level !== "all") return [];
  return section.items.filter((item) => !item.unlocked);
}

function renderFilters(data) {
  const types = el("filter-type");
  types.textContent = "";
  types.appendChild(chip("Все", filters.slot === "all", () => pickSlot("all")));
  data.sections.forEach((section) => {
    types.appendChild(
      chip(
        section.emoji + " " + section.title,
        filters.slot === section.slot,
        () => pickSlot(section.slot)
      )
    );
  });

  const levels = el("filter-level");
  levels.textContent = "";
  levels.appendChild(chip("Все", filters.level === "all", () => pickLevel("all")));
  levels.appendChild(
    chip("Доступные", filters.level === "open", () => pickLevel("open"))
  );
  const numbers = [
    ...new Set(
      data.sections.flatMap((section) =>
        section.items.map((item) => item.level_required)
      )
    ),
  ].sort((a, b) => a - b);
  numbers.forEach((level) => {
    levels.appendChild(
      chip(
        (level > data.level ? "🔒 " : "") + level + " ур.",
        filters.level === level,
        () => pickLevel(level),
        level > data.level ? "locked" : ""
      )
    );
  });
}

function shelf(section) {
  const shown = shownItems(section);
  const locked = hiddenItems(section);
  if (!shown.length && !locked.length) return null;

  const box = document.createElement("section");
  box.className = "shelf";

  const head = document.createElement("h2");
  head.className = "shelf-head";
  head.textContent = section.emoji + " " + section.title;
  const count = document.createElement("span");
  count.className = "shelf-count";
  count.textContent =
    filters.level === "all" || filters.level === "open"
      ? "открыто " + section.open + " из " + section.items.length
      : "товаров: " + shown.length;
  head.appendChild(count);
  box.appendChild(head);

  const list = document.createElement("div");
  list.className = "shelf-list";
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
  if (typeof filters.level === "number") {
    const open = filters.level <= data.level;
    return open
      ? "Партия " + filters.level + " уровня — она уже открыта."
      : "Партия " + filters.level + " уровня откроется, когда дорастёшь: "
        + "сейчас у тебя " + data.level + ".";
  }
  if (filters.level === "open") return "Только то, что уже можно купить.";
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

const SCREENS = ["card", "shop", "topup"];
let lastTab = "card";

function showTab(name) {
  SCREENS.forEach((screen) => {
    el(screen).classList.toggle("hidden", screen !== name);
  });
  if (name !== "topup") lastTab = name;
  el("tab-card").classList.toggle("active", name === "card");
  el("tab-shop").classList.toggle("active", name === "shop");
  if (name === "shop" && !shopData) loadShop();
  if (name === "topup") loadTopUp();
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
    showTab("topup");
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
    popup(
      "🛍 " + data.bought.title,
      data.bought.can_equip
        ? "Куплено за " + data.bought.price + " 💰. Вещь ждёт в инвентаре — "
          + "надеть можно на вкладке «Боец»."
        : "Куплено за " + data.bought.price + " 💰. Надеть пока нечем: "
          + "требования не выполнены — вещь полежит в инвентаре."
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
    render(await post(url, body), true);
    shopData = null;  // «уже есть» на витрине могло измениться
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
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

function renderAvatar(card) {
  const box = el("avatar");
  box.textContent = "";
  box.classList.toggle("clickable", Boolean(card.is_self));
  if (card.avatar.url) {
    const img = document.createElement("img");
    img.src = card.avatar.url;
    img.alt = card.name;
    img.addEventListener("error", () => {
      box.textContent = "";
      const emoji = document.createElement("div");
      emoji.className = "emoji";
      emoji.textContent = card.avatar.emoji || "🥊";
      box.appendChild(emoji);
    });
    box.appendChild(img);
  } else {
    const emoji = document.createElement("div");
    emoji.className = "emoji";
    emoji.textContent = card.avatar.emoji || "🥊";
    box.appendChild(emoji);
  }
}

// Здоровье затягивается на глазах: тикаем локально от той же скорости,
// по которой его считает сервер.
let health = null;
let ticker = null;

function paintHealth() {
  if (!health) return;
  const percent = health.max ? Math.min(100, (health.current / health.max) * 100) : 0;
  const bar = el("hp");
  bar.classList.remove("green", "yellow", "red");
  bar.classList.add(percent < 20 ? "red" : percent < 80 ? "yellow" : "green");
  el("hp-fill").style.width = percent.toFixed(1) + "%";
  el("hp-text").textContent = num(Math.floor(health.current)) + " / " + num(health.max);

  const note = el("hp-note");
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

function startHealthTicker(hp) {
  health = {
    current: hp.current,
    max: hp.max,
    rate: hp.max / (hp.regen_seconds || 600),
  };
  paintHealth();
  if (ticker) clearInterval(ticker);
  ticker = setInterval(() => {
    if (health.current >= health.max) return;
    health.current = Math.min(health.max, health.current + health.rate);
    paintHealth();
  }, 1000);
}

function render(card, keepTab) {
  el("class-emoji").textContent = card.fclass.emoji;
  el("name").textContent = card.name;
  el("level").textContent = "[" + card.level + "]";
  document.title = card.name + " [" + card.level + "]";

  startHealthTicker(card.hp);
  renderAvatar(card);
  renderSlots(el("slots-left"), card.slots.left, card.is_self);
  renderSlots(el("slots-right"), card.slots.right, card.is_self);
  renderBag(card);
  el("city").textContent = card.city;

  const stats = el("stats");
  stats.textContent = "";
  card.stats.forEach((stat) => {
    stats.appendChild(row(stat.emoji + " " + stat.title, statValue(stat)));
  });

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
      c.weapon_damage.map((w) => " + " + w.min + "–" + w.max).join("")
    : c.damage_min + "–" + c.damage_max;
  combat.appendChild(row("👊 Урон", hit));
  combat.appendChild(row("💥 Крит", c.crit_chance + "% ×" + c.crit_power));
  combat.appendChild(row("🚫 Антикрит", c.anticrit + "%"));
  combat.appendChild(row("🌀 Уворот", c.dodge_chance + "%"));
  combat.appendChild(row("🎯 Точность", c.accuracy + "%"));
  combat.appendChild(row("🔄 Контрудар", c.counter_chance + "%"));
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
  if (card.is_self) el("tabs").classList.remove("hidden");
  if (!keepTab) showTab("card");
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

el("avatar").addEventListener("click", () => {
  if (!el("avatar").classList.contains("clickable")) return;
  if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
  openLooks();
});
el("sheet-close").addEventListener("click", closeSheet);
el("sheet-back").addEventListener("click", closeSheet);

el("tab-card").addEventListener("click", () => showTab("card"));
el("tab-shop").addEventListener("click", () => showTab("shop"));
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
