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
        // Клик по надетой вещи возвращает её в инвентарь
        act("api/unequip", { slot: slot.slot });
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
    li.textContent = gain.emoji + " " + gain.title + " +" + gain.value;
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

function thingCard(item, credits) {
  const box = document.createElement("div");
  box.className = "thing";

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

  const wear = document.createElement("div");
  const left = item.max_wear - item.wear;
  wear.className = "thing-wear" + (left <= 1 ? " dying" : item.wear ? " worn" : "");
  wear.textContent = "🔧 Износ: " + item.wear_text;
  if (left <= 1) wear.textContent += " — ещё один бой, и рассыплется";
  body.appendChild(wear);

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

// ---------- действия ----------

let busy = false;

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
    render(await post(url, body));
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
    render(data.card);
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

function renderAvatar(card) {
  const box = el("avatar");
  box.textContent = "";
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

function render(card) {
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

  const record = el("record");
  record.textContent = "";
  record.appendChild(row("Побед", num(card.record.wins)));
  record.appendChild(row("Поражений", num(card.record.losses)));
  record.appendChild(row("Ничьих", num(card.record.draws)));
  record.appendChild(row("Рейтинг", num(card.record.rating)));
  if (card.is_self) {
    record.appendChild(row("Кредиты", num(card.record.credits)));
  }
  record.appendChild(row("Место рождения", card.birthplace));
  record.appendChild(row("День рождения персонажа", card.birthday));

  el("foot").textContent =
    card.fclass.emoji + " " + card.fclass.title + " — " + card.fclass.tagline;

  el("loader").classList.add("hidden");
  el("card").classList.remove("hidden");
}

function fail(message) {
  el("loader").classList.add("hidden");
  const box = el("error");
  box.querySelector(".error-text").textContent = message;
  box.classList.remove("hidden");
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
    render(await response.json());
  } catch (error) {
    console.error("card load failed", error);
    fail("Не получилось загрузить карточку. Попробуй ещё раз.");
  }
}

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
