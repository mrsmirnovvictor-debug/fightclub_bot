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

function renderSlots(container, slots) {
  container.textContent = "";
  slots.forEach((slot) => {
    const box = document.createElement("div");
    box.className = "slot" + (slot.item ? "" : " empty");
    box.textContent = slot.item ? slot.item.icon : slot.placeholder;
    box.addEventListener("click", () => {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
      if (slot.item) {
        const bonus = slot.item.bonus ? "\n" + slot.item.bonus : "";
        popup(slot.item.title, slot.title + bonus);
      } else {
        popup("Слот пуст", "Сюда надевается: " + slot.title + ".");
      }
    });
    container.appendChild(box);
  });
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
  renderSlots(el("slots-left"), card.slots.left);
  renderSlots(el("slots-right"), card.slots.right);
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
