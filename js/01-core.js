// #region ЯДРО: общее для всех экранов и игр (константы времени, Telegram, форматирование, сеть)

const REQUEST_TIMEOUT_MS = 10000; // таймаут запроса
const REFRESH_MIN_MS = 10000;     // обновление при открытии экрана и возврате в приложение
const REQUEST_GAP_MS = 5000;      // любые два запроса /api/me не чаще, чем раз в 5 секунд
const ERROR_RETRY_MS = 30000;     // после ошибки автоповтор не чаще, чем раз в 30 секунд
const ZERO_DELAY_MS = 1000;       // пауза после нуля таймера перед новым запросом

// Telegram Mini App: вне Telegram объекта нет, и игра работает как обычная страница
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    if (tg.isVersionAtLeast('7.7')) tg.disableVerticalSwipes(); // Bot API 7.7+
  } catch (e) {
    // сбой Telegram API не должен ронять игру
  }
  // цвет шапки, фона и нижней панели Telegram = фон приложения (--bg), чтобы не было швов
  const THEME_BG = '#050506';
  [['6.1', 'setHeaderColor'], ['6.1', 'setBackgroundColor'], ['7.10', 'setBottomBarColor']].forEach(([ver, fn]) => {
    try {
      if (typeof tg[fn] === 'function' && tg.isVersionAtLeast(ver)) tg[fn](THEME_BG);
    } catch (e) {
      // старый клиент Telegram: цвета останутся по умолчанию
    }
  });
}

// Вибрация: 'light' (касание), 'success' (выигрыш), 'error' (проигрыш или отказ). Без Telegram ничего не делает
function haptic(kind) {
  try {
    const h = tg && tg.HapticFeedback;
    if (!h) return;
    if (kind === 'light') h.impactOccurred('light');
    else h.notificationOccurred(kind);
  } catch (e) {
    // вибрация необязательна
  }
}

// Размер шрифта по длине записи: длинные числа (до 16 цифр) не должны выталкивать соседей за край экрана
function fitNumberFont(el, length) {
  el.dataset.len = length <= 9 ? 'l' : length <= 13 ? 'm' : length <= 17 ? 's' : 'xs';
}

// число баланса с обычными пробелами: очень длинное можно перенести по группам цифр, а не посреди группы
const spacedNumber = (n) => formatNumber(n).replace(/[\u00a0\u202f]/g, ' ');

const mmss = (sec) => {
  const left = Math.max(0, Math.ceil(sec));
  return String(Math.floor(left / 60)).padStart(2, '0') + ':' + String(left % 60).padStart(2, '0');
};

const isCount = (v) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;

const reducedMotion = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

const BJ_SUIT_SETS = {
  default: {
    S: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c3.2 4.2 8 6.7 8 11.2a4.4 4.4 0 0 1-7.1 3.4c.2 2.3 1 3.8 2.3 5.4H8.8c1.3-1.6 2.1-3.1 2.3-5.4A4.4 4.4 0 0 1 4 13.2C4 8.7 8.8 6.2 12 2z"/></svg>',
    H: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.5S3.5 16 3.5 9.8a4.6 4.6 0 0 1 8.5-2.4 4.6 4.6 0 0 1 8.5 2.4C20.5 16 12 21.5 12 21.5z"/></svg>',
    D: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l7 10-7 10-7-10z"/></svg>',
    C: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7.5" r="4.4"/><circle cx="6.8" cy="14.6" r="4.4"/><circle cx="17.2" cy="14.6" r="4.4"/><path d="M12 12.5l-2.8 9.5h5.6z"/></svg>'
  }
};
let BJ_SUIT_SVG = BJ_SUIT_SETS.default;      // набор мастей зависит от скина рубашки (applySkins); стартовый набор один

// Тост поверх нижней панели (общий для «Скоро», «Отправлено», «Вам перевели»)
let toastTimer = null;
function showToast(text, id = 'soon-toast') {
  const el = document.getElementById(id);
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

const ROUND_ATTEMPTS = 3;               // попыток отправки одного раунда
const ROUND_PAUSES_MS = [2000, 4000];   // паузы перед 2-й и 3-й попытками
const REQUEST_ID_RE = /^[A-Za-z0-9-]{8,64}$/;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeRequestId() {
  let id = null;
  try {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      id = window.crypto.randomUUID();
    } else if (window.crypto && window.crypto.getRandomValues) {
      id = Array.from(window.crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
    }
  } catch (e) {
    id = null;
  }
  return id && REQUEST_ID_RE.test(id) ? id : null;
}

const isInt = (v) => typeof v === 'number' && Number.isSafeInteger(v);

// Единое место для всех POST-запросов. Если сервер ответил 429 (слишком часто), это временный сбой:
// ждём Retry-After (не меньше 1 и не больше 10 секунд) и повторяем ТОТ ЖЕ запрос (с тем же request_id),
// всего не более 3 повторов на один request_id (счёт общий для всех попыток вызывающего кода; через минуту
// он сбрасывается). Остальные ответы и ошибки сети отдаются вызывающему как есть.
const RATE_LIMIT_RETRIES = 3;
const RATE_BUDGET_MS = 60000;
const rateBudget = new Map(); // request_id -> { used, at }
const retryAfterMs = (res) => {
  const sec = parseInt(res.headers.get('Retry-After'), 10);
  return Math.min(10, Math.max(1, Number.isFinite(sec) ? sec : 1)) * 1000;
};

// Что повторять. POST повторяется (с тем же request_id) только при сетевой ошибке, таймауте, 429 (это делает
// postJson) и ответе 5xx. Остальные 4xx и любой ответ 2xx не повторяются: после 2xx сервер уже выполнил действие,
// а если наш код не смог разобрать или показать ответ, это наша ошибка, а не сбой сети.
const isServerError = (res) => res.status >= 500;

async function readJsonBody(res) {
  try {
    return { ok: true, data: await res.json() };
  } catch (e) {
    return { ok: false };
  }
}

// Таймаут действует на весь запрос, в том числе на чтение тела ответа: тело читается здесь целиком, пока таймер не снят (раньше таймер снимался
// после заголовков, и сервер, отдавший заголовки и замолчавший, оставлял интерфейс в состоянии «отправка» навсегда). Обрыв или таймаут при чтении
// тела бросают исключение, как сбой сети до заголовков: вызывающий код повторяет запрос с тем же request_id (сервер повтор не выполнит дважды).
// Возвращается объект с теми же status, ok, headers и json(), text() по уже прочитанному телу.
async function postJson(path, payload) {
  for (;;) {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    let res;
    try {
      const raw = await fetch(API_URL + path, {
        method: 'POST',
        headers: { Authorization: 'tma ' + tg.initData, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        cache: 'no-store',
        signal: ctrl.signal
      });
      const text = await raw.text();
      res = { status: raw.status, ok: raw.ok, headers: raw.headers, text: async () => text, json: async () => JSON.parse(text) };
    } finally {
      clearTimeout(timeout);
    }
    const key = payload.request_id;
    if (res.status !== 429) {
      rateBudget.delete(key);
      return res;
    }
    const now = performance.now();
    let budget = rateBudget.get(key);
    if (!budget || now - budget.at > RATE_BUDGET_MS) budget = { used: 0, at: now };
    if (budget.used >= RATE_LIMIT_RETRIES) return res; // повторы исчерпаны: 429 уходит вызывающему коду
    budget.used += 1;
    rateBudget.set(key, budget);
    await sleep(retryAfterMs(res));
  }
}

// Один POST действия. { kind: 'ok', data } | { kind: 'conflict', detail } | { kind: 'fatal', text } | { kind: 'retry', code }
async function postMinesOnce(path, payload, validate) {
  try {
    const res = await postJson(path, payload);
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Неверные параметры' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      return { kind: 'conflict', detail: typeof body.detail === 'string' ? body.detail : '' };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось выполнить действие' };
    }
    const body = await readJsonBody(res);
    // ответ 2xx не повторяем: действие уже выполнено сервером; непонятный ответ = повод запросить состояние
    return body.ok && validate(body.data) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// До ROUND_ATTEMPTS попыток одного действия (один request_id): исход 'retry' повторяется после паузы, любой другой возвращается.
// post() делает один POST; после неудачных попыток результат null.
async function postWithRetries(post) {
  let result = null;
  for (let attempt = 0; attempt < ROUND_ATTEMPTS && !result; attempt++) {
    if (attempt > 0) await sleep(ROUND_PAUSES_MS[attempt - 1]);
    const r = await post();
    if (r.kind !== 'retry') result = r;
  }
  return result;
}

// Что сказать игроку, если действие не дало результата: { note, reload } (reload: запросить ли состояние с сервера).
// notes: тексты по ответу 409 (detail); значение-массив [текст, false] означает «состояние не запрашивать».
function actionFailure(result, notes) {
  let note = 'Состояние обновлено';
  let reload = true;
  if (result && result.kind === 'invalid') {
    note = 'Ответ сервера не распознан. Состояние обновлено';
  } else if (result && result.kind === 'fatal') {
    note = result.text;
    reload = false;
  } else if (result && result.kind === 'conflict' && Object.prototype.hasOwnProperty.call(notes, result.detail)) {
    const n = notes[result.detail];
    if (Array.isArray(n)) { note = n[0]; reload = n[1]; } else note = n;
  }
  return { note, reload };
}

// Состояние игры с сервера (GET, общий для мин, блэкджека, краша и хило): таймаут и единый разбор ошибок.
// validate(d) решает, верен ли ответ. Ошибка: { text, code, retry }.
async function fetchGameState(path, validate) {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + path, {
      method: 'GET',
      headers: { Authorization: 'tma ' + tg.initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      throw { text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', code: '401', retry: false };
    }
    if (res.status === 429) throw { text: 'Слишком много запросов, подождите немного', code: '429', retry: true };
    if (!res.ok) throw { text: 'Нет связи с сервером', code: String(res.status), retry: true };
    const d = await res.json();
    if (!validate(d)) throw { text: 'Нет связи с сервером', code: 'ответ', retry: true };
    return d;
  } catch (e) {
    if (e && typeof e.text === 'string') throw e;
    throw { text: 'Нет связи с сервером', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть или CORS?', retry: true };
  } finally {
    clearTimeout(timeout);
  }
}

// Загрузка состояния игры g при открытии экрана, возврате в приложение и вручную (общая для мин, блэкджека, краша и хило).
// reason: 'open' | 'visible' (не чаще раза в 10 секунд) | 'manual'. Между любыми двумя запросами не меньше 5 секунд.
// cfg: id игры; fetchState(); applyState(d); setMessage(text, code, canRetry); setNotice(text); render();
// blockWhileAnimating: не грузить, пока идёт анимация (краш); skipRecent(g): можно ли пропустить недавнее обновление (по умолчанию да).
async function loadGameState(g, cfg, reason) {
  if (g.inFlight || g.busy || (cfg.blockWhileAnimating && g.animating) || activeTab !== 'play' || currentGame !== cfg.id) return;
  const now = performance.now();
  const sinceLast = now - g.lastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason === 'manual') {
      clearTimeout(g.timer);
      g.timer = setTimeout(() => loadGameState(g, cfg, 'manual'), REQUEST_GAP_MS - sinceLast + 20);
    }
    return;
  }
  if (reason !== 'manual' && sinceLast < REFRESH_MIN_MS && (!cfg.skipRecent || cfg.skipRecent(g))) return;
  if (!(tg && tg.initData)) {
    // вне Telegram запросы не отправляются
    g.view = 'loading';
    g.error = true;
    cfg.setMessage('Откройте игру через бота в Telegram', 'нет Telegram', false);
    cfg.render();
    return;
  }
  clearTimeout(g.timer);
  g.inFlight = true;
  g.lastRequestAt = now;
  if (!g.loaded) {
    g.error = false;
    cfg.setMessage('', '', false);
    cfg.render();
  }
  try {
    cfg.applyState(await cfg.fetchState());
  } catch (e) {
    if (g.loaded) {
      cfg.setNotice(e.text);   // уже есть данные: оставляем экран, сообщаем о сбое
    } else {
      g.error = true;
      cfg.setMessage(e.text, e.code, e.retry);
      cfg.render();
    }
  } finally {
    g.inFlight = false;
  }
}

// Фишки ставки по серверному балансу игры (номиналы chipSet) и подсветка нажатой: одна на панель ставок игры.
// attr: имя data-атрибута кнопок-фишек; getBalance(), getBet(), setBet(v): доступ к балансу и полю ставки игры.
function makeChipBar({ root, attr, getBalance, getBet, setBet }) {
  let values = [];
  const render = () => {
    const next = chipSet(getBalance());
    if (next.join() === values.join()) return;
    const wasChip = values.includes(getBet());
    values = next;
    root.querySelectorAll('[data-' + attr + ']').forEach((btn, i) => {
      btn.dataset[attr] = String(next[i]);
      setChipText(btn, next[i]);
    });
    if (wasChip && !next.includes(getBet())) setBet(nearestChip(next, getBet()));
  };
  const sync = () => {
    const v = getBet();
    root.querySelectorAll('[data-' + attr + ']').forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset[attr]) === v)));
  };
  return { render, sync };
}

// Реестр игр: ядро (запрос баланса, минутное начисление) обходит его и не знает игр по именам. Игра регистрируется при объявлении
// своего состояния: busy() = идёт запрос или анимация раунда (баланс на экране менять нельзя); state и render: если у игры свой серверный
// баланс, после начисления ядро подтягивает его к серверному (keepBalance() = сейчас не трогать).
const gameRegistry = [];
const registerGame = (entry) => { gameRegistry.push(entry); };

// Скины (косметика, только внешний вид): код надетого предмета каждого слота ставится атрибутом data-skin-<слот> на корень
// (для стартового предмета атрибут тоже есть и равен стартовому коду). CSS-переменные скина переопределяются в style.css внутри
// [data-skin-<слот>="<код>"]; сами правила читают переменные. Наборы SVG (масти, иконки мин) выбираются по коду, стартовый набор один.
// Слоты avatar_frame и badge (рамка и значок) здесь не обрабатываются. Источник: cosmetics.equipped из /api/me.
const SKIN_SLOTS = ['card_back', 'chip', 'table', 'mine_icons', 'keno_ball', 'crash'];
const SKIN_CODE_RE = /^[a-z0-9_]{1,40}$/;
function applySkins(equipped) {
  const root = document.documentElement;
  const src = equipped && typeof equipped === 'object' ? equipped : {};
  const before = root.getAttribute('data-skin-table');
  SKIN_SLOTS.forEach((slot) => {
    const code = src[slot];
    if (typeof code === 'string' && SKIN_CODE_RE.test(code)) root.setAttribute('data-skin-' + slot, code);
    else root.removeAttribute('data-skin-' + slot);
  });
  const pick = (sets, code) => (typeof code === 'string' && Object.prototype.hasOwnProperty.call(sets, code) ? sets[code] : sets.default);
  const suits = pick(BJ_SUIT_SETS, src.card_back);
  const icons = pick(MINES_ICON_SETS, src.mine_icons);
  const setsChanged = suits !== BJ_SUIT_SVG || icons !== minesIcons;
  BJ_SUIT_SVG = suits;
  minesIcons = icons;
  if (root.getAttribute('data-skin-table') !== before) drawWheel();       // колесо рисуется в canvas: перерисовка при смене стола
  if (setsChanged) { renderMines(); renderBj(); renderHl(); }               // наборы SVG вставляются при отрисовке
}

// #endregion ЯДРО

