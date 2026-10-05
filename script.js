// Адрес сервера с балансом. Менять только здесь.
const API_URL = 'https://roulette-production-4b93.up.railway.app';

// Карта файла (секции помечены // #region ... // #endregion, свёртываются в редакторе):
//   Константы и форматирование чисел
//   ЯДРО: время, Telegram, сеть, реестр игр, общие каркасы игр (загрузка состояния, повторы, фишки)
//   Ввод суммы и фишки
//   Панель ставок над клавиатурой и защита тапов
//   Рулетка
//   Рулетка: ставка через сервер
//   Баланс и профиль (/api/me)
//   Рейтинг
//   Ферма
//   Минутное начисление
//   Мины
//   Мины: отрисовка
//   Мины: запросы
//   Мины: действия
//   Кено
//   Кено: таблица выплат
//   Кено: раунд
//   Блэкджек
//   Блэкджек: проверка ответа сервера
//   Блэкджек: рисование
//   Блэкджек: запросы
//   Блэкджек: анимация
//   Краш
//   Краш: проверка ответа сервера
//   Краш: график и множитель
//   Краш: итог
//   Краш: запросы
//   Хило
//   Хило: проверка ответа сервера
//   Хило: рисование
//   Хило: запросы
//   Хило: анимация
//   Переводы
//   Выбор получателя
//   История переводов
//   Лобби, меню игр и вкладки
//   Запуск

// #region Константы и форматирование чисел
// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

// цвета секторов колеса задают CSS-переменные стола (--table-red, --table-black, --table-green в style.css); читаются при отрисовке (см. wheelPalette)
const WHEEL_SECTOR_VAR = { red: '--table-red', black: '--table-black', green: '--table-green' };

// числа с разделителем тысяч («1 300»); если Intl недоступен, без него
const formatNumber = (() => {
  try {
    const f = new Intl.NumberFormat('ru-RU');
    return (n) => f.format(n);
  } catch (e) {
    return (n) => String(n);
  }
})();

// Сокращение от миллиона: «1,2 млн», «3,4 млрд». Полное значение остаётся в title и открывается нажатием
const COMPACT_UNITS = [[1e15, 'квадр.'], [1e12, 'трлн'], [1e9, 'млрд'], [1e6, 'млн']];
function formatCompact(n) {
  if (!(n >= 1e6)) return formatNumber(n);
  for (const [unit, name] of COMPACT_UNITS) {
    if (n >= unit) return String(Math.floor((n / unit) * 10) / 10).replace('.', ',') + ' ' + name;
  }
  return formatNumber(n);
}

function setNumber(el, n) {
  const full = formatNumber(n);
  const short = formatCompact(n);
  el.textContent = short;
  el.title = full;
  el.dataset.short = short;
  el.dataset.full = full;
  el.classList.toggle('num-tap', short !== full);
}

document.addEventListener('click', (e) => {
  const el = e.target instanceof Element ? e.target.closest('.num-tap') : null;
  if (!el) return;
  el.textContent = el.textContent === el.dataset.full ? el.dataset.short : el.dataset.full;
});

// #endregion Константы и форматирование чисел

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
function showToast(text) {
  const el = document.getElementById('soon-toast');
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

async function postJson(path, payload) {
  for (;;) {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    let res;
    try {
      res = await fetch(API_URL + path, {
        method: 'POST',
        headers: { Authorization: 'tma ' + tg.initData, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        cache: 'no-store',
        signal: ctrl.signal
      });
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

// #region Ввод суммы и фишки
// ---------- динамические фишки и ввод суммы ----------
// Фишки считаются от серверного баланса: T = наибольшая степень 10, не больше баланса, но не меньше 100;
// номиналы T/10, T/2, T, 5T (баланс 305: 10/50/100/500; 25 000: 1000/5000/10 000/50 000).
const CHIP_BASE = 100;
function chipSet(balance) {
  const b = Number.isSafeInteger(balance) && balance > 0 ? balance : 0;
  let t = CHIP_BASE;
  while (t * 10 <= b) t *= 10;
  return [t / 10, t / 2, t, 5 * t];
}

const CHIP_UNITS = [[1e15, 'Q'], [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
function chipLabel(n) {
  for (const [unit, name] of CHIP_UNITS) {
    if (n >= unit) return String(n / unit) + name;   // номиналы вида 10^k и 5 * 10^k делятся нацело
  }
  return String(n);
}

function setChipText(btn, n) {
  const label = chipLabel(n);
  btn.textContent = label;
  btn.dataset.len = String(label.length);
  btn.title = formatNumber(n);
  btn.setAttribute('aria-label', formatNumber(n));
}

const nearestChip = (values, v) => values.reduce((best, x) => (Math.abs(x - v) < Math.abs(best - v) ? x : best), values[0]);

// Число из поля: пусто или нечисло = минимальная ставка 1; не больше безопасного целого
function parseBet(input) {
  const n = Number(input.value);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(Math.floor(n), Number.MAX_SAFE_INTEGER);
}

function setBetValue(input, n) {
  input.value = String(n);
  input.dispatchEvent(new Event('input', { bubbles: true })); // поле остаётся валидным: слушатели видят обычный ввод
}

const halfBet = (input) => setBetValue(input, Math.max(1, Math.floor(parseBet(input) / 2)));
const doubleBet = (input, max) => setBetValue(input, Math.max(1, Math.min(parseBet(input) * 2, max))); // не выше предела

// Предел ставки одинаков в обеих играх: не больше баланса и максимума ставки игры.
// Рулетка: поле суммы это размер ставки на один клик по клетке, предел = доступный баланс (серверный минус ставки
// на столе), как проверяет placeBet. Мины: min(баланс, 10**9).
const rouletteBetLimit = () => (srv.loaded ? Math.max(1, availableBalance()) : Number.MAX_SAFE_INTEGER);
const minesBetLimit = () => Math.max(1, Math.min(MINES_BET_MAX, mn.balance === null ? MINES_BET_MAX : mn.balance));

const betPanels = [];
const refreshBetPanels = () => betPanels.forEach((p) => p.refresh());

// Поле суммы и кнопки панели: только цифры (до 16), без ведущих нулей; число выше предела при вводе и потере
// фокуса ограничивается пределом; «Макс» ставит предел, а пока поле в фокусе та же кнопка работает как «Готово»;
// Enter тоже закрывает клавиатуру. Панель прижимается к клавиатуре (dockPanel).
function setupBetPanel({ input, maxBtn, halfBtn, doubleBtn, getLimit }) {
  const panel = input.closest('.bets-dock');
  const clampToLimit = () => {
    const limit = getLimit();
    if (input.value !== '' && Number(input.value) > limit) input.value = String(limit);
  };
  const refresh = () => {
    doubleBtn.classList.toggle('dim', input.value !== '' && parseBet(input) >= getLimit());
    doubleBtn.setAttribute('aria-disabled', String(doubleBtn.classList.contains('dim')));
  };
  input.setAttribute('inputmode', 'numeric');
  input.setAttribute('enterkeyhint', 'done');
  input.addEventListener('input', () => {
    const clean = input.value.replace(/\D/g, '').slice(0, 16).replace(/^0+(?=\d)/, '');
    if (clean !== input.value) input.value = clean;
    clampToLimit();
    refresh();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      input.blur();
    }
  });
  input.addEventListener('focus', () => {
    maxBtn.dataset.mode = 'done';
    maxBtn.textContent = 'Готово';
    dockPanel(panel);
  });
  input.addEventListener('blur', () => {
    clampToLimit();
    refresh();
    maxBtn.dataset.mode = 'max';
    maxBtn.textContent = 'Макс';
    undockPanel(panel);
  });
  maxBtn.addEventListener('pointerdown', (e) => e.preventDefault()); // фокус остаётся, пока не сработает click
  maxBtn.addEventListener('click', () => {
    if (maxBtn.dataset.mode === 'done') input.blur();
    else setBetValue(input, getLimit());
  });
  halfBtn.addEventListener('click', () => halfBet(input));
  doubleBtn.addEventListener('click', () => doubleBet(input, getLimit()));
  maxBtn.dataset.mode = 'max';
  betPanels.push({ refresh });
  refresh();
  return refresh;
}

// #endregion

// #region Панель ставок над клавиатурой и защита тапов
// ---------- панель ставок над клавиатурой ----------
// Пока поле в фокусе и клавиатура открыта, панель смещается (transform: раскладка не меняется, стол не прыгает)
// так, чтобы её низ был у верхнего края клавиатуры. Видимый низ экрана берём из visualViewport (offsetTop + height),
// запасной вариант: Telegram.WebApp.viewportHeight. Клавиатура считается открытой, если низ оболочки заметно ниже видимого.
let dockedPanel = null;
let dockFrame = 0;

function visibleBottom() {
  const vv = window.visualViewport;
  if (vv) return vv.offsetTop + vv.height;
  if (tg && typeof tg.viewportHeight === 'number' && tg.viewportHeight > 0) return tg.viewportHeight;
  return window.innerHeight;
}

function positionDock() {
  const p = dockedPanel;
  if (!p) return;
  p.style.transform = '';
  const shellBottom = document.querySelector('.shell').getBoundingClientRect().bottom;
  const bottom = visibleBottom();
  if (shellBottom - bottom <= 80) {          // клавиатуры нет
    p.classList.remove('docked');
    return;
  }
  const dy = Math.round(bottom - p.getBoundingClientRect().bottom - 4);
  p.classList.add('docked');
  if (dy) p.style.transform = `translateY(${dy}px)`;
}

function scheduleDock() {
  if (dockFrame) return;
  dockFrame = requestAnimationFrame(() => {
    dockFrame = 0;
    positionDock();
  });
}

function dockPanel(panel) {
  dockedPanel = panel;
  [0, 150, 350, 700].forEach((ms) => setTimeout(() => { if (dockedPanel === panel) scheduleDock(); }, ms)); // клавиатура выезжает не сразу
}

function undockPanel(panel) {
  if (dockedPanel !== panel) return;
  dockedPanel = null;
  panel.style.transform = '';
  panel.classList.remove('docked');
  window.scrollTo(0, 0);
}

if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', scheduleDock);
  window.visualViewport.addEventListener('scroll', scheduleDock);
}
window.addEventListener('resize', scheduleDock);
window.addEventListener('scroll', scheduleDock, true);

// Когда клавиатура уходит, видимая область и вёрстка перестраиваются (панель опускается из-под клавиатуры на место). Если
// тап по кнопке успел снять фокус с поля, то клик касанием определяется уже по новой раскладке и попадает в другой элемент
// (например, в затемнение окна, которое закрывает его). Поэтому: (1) кнопки панелей действуют сразу по нажатию, после
// закрытия клавиатуры, а следующий за нажатием клик гасится (действие ровно один раз; Enter и клавиатурный клик идут как
// обычно); (2) затемнение закрывает окно, только если нажатие и клик были именно на нём и вёрстка не менялась ~400 мс.
let lastLayoutShiftAt = 0;        // последнее изменение видимой области или потеря фокуса поля
const markLayoutShift = () => { lastLayoutShiftAt = performance.now(); };
if (window.visualViewport) window.visualViewport.addEventListener('resize', markLayoutShift);
window.addEventListener('resize', markLayoutShift);
document.addEventListener('focusout', (e) => { if (e.target && e.target.tagName === 'INPUT') markLayoutShift(); }, true);
let lastPointerDownTarget = null;
document.addEventListener('pointerdown', (e) => { lastPointerDownTarget = e.target; }, true);

const BACKDROP_QUIET_MS = 400;
function closeOnBackdropTap(backdrop, close) {
  backdrop.addEventListener('click', (e) => {
    const startedHere = lastPointerDownTarget === backdrop;
    lastPointerDownTarget = null;
    if (e.target !== backdrop || !startedHere) return;                       // нажатие началось не на затемнении (или ушло с окна)
    if (performance.now() - lastLayoutShiftAt < BACKDROP_QUIET_MS) return;   // клавиатура или вёрстка только что менялись
    close();
  });
}

// Тап вне поля и прокрутка закрывают клавиатуру; кнопка панели при открытой клавиатуре: сначала закрыть клавиатуру,
// затем выполнить действие кнопки (один раз)
(() => {
  const active = () => {
    const a = document.activeElement;
    return a && a.classList && a.classList.contains('bet-input') ? a : null;
  };
  let swallow = 0;   // до этого момента следующий клик считается хвостом уже обработанного нажатия
  document.addEventListener('pointerdown', (e) => {
    swallow = 0;
    const a = active();
    if (!a || e.target === a || !e.target.closest || e.target.closest('.bet-maxdone')) return;
    if (e.target.closest('.picker-list')) {   // строки списка: фокус остаётся (без него клавиатура не уходит и вёрстка не двигается), выбор по клику
      e.preventDefault();                      // (прокрутка списка пальцем строку не выбирает)
      return;
    }
    const btn = e.target.closest('button');
    const immediate = btn && !btn.disabled && e.isPrimary !== false && (e.button || 0) === 0 && btn.closest('.bets-dock, .transfer-panel');
    a.blur();
    if (immediate) {
      btn.click();
      swallow = performance.now() + 1000;
    }
  }, true);
  document.addEventListener('click', (e) => {
    if (!swallow) return;
    const live = performance.now() < swallow;
    swallow = 0;
    if (live) { e.stopImmediatePropagation(); e.preventDefault(); }
  }, true);
  let startY = null;
  document.addEventListener('touchstart', (e) => { startY = e.touches[0].clientY; }, { passive: true, capture: true });
  document.addEventListener('touchmove', (e) => {
    const a = active();
    if (a && startY !== null && Math.abs(e.touches[0].clientY - startY) > 8) a.blur();
  }, { passive: true, capture: true });
  document.addEventListener('wheel', () => { const a = active(); if (a) a.blur(); }, { passive: true, capture: true });
})();


// #endregion

// #region Рулетка
// ---------- рулетка: колесо, стол, ставки, вращение ----------
const SECTOR = 360 / WHEEL_ORDER.length; // угол одного сектора
const SPIN_TIME_MS = 7000;               // сколько длится вращение колеса и шарика

const canvas = document.getElementById('wheel');
const ctx = canvas.getContext('2d');
const spinBtn = document.getElementById('spin');
const numberEl = document.getElementById('result-number');

const BET_TYPES = ['number', 'red', 'black', 'even', 'odd', 'dozen', 'column'];
const DOZEN_NAME = { 1: '1–12', 2: '13–24', 3: '25–36' };

const balanceEl = document.getElementById('balance');
const amountEl = document.getElementById('amount');
const tableEl = document.getElementById('table');
const messageLineEl = document.getElementById('message-line');
const messageTextEl = document.getElementById('message-text');
const messageCodeEl = document.getElementById('message-code');
const statusLineEl = document.getElementById('status-line');
const statusTextEl = document.getElementById('status-text');
const statusCodeEl = document.getElementById('status-code');
const betsPanel = document.getElementById('bets');

let wheelAngle = 0;  // поворот колеса, градусы (по часовой от верха)
let ballRel = 0;     // положение шарика относительно колеса, градусы
let ballRadius = 0;  // расстояние шарика от центра, в долях радиуса колеса

const ballEl = document.getElementById('ball');
const wheelBox = document.querySelector('.wheel-box');
const wheelLayer = document.getElementById('wheel-layer');
const layerResultEl = document.getElementById('layer-result');
const layerWinEl = document.getElementById('layer-win');
const gameSwitchEl = document.getElementById('game-switch');
const RESULT_HOLD_MS = 1500; // сколько колесо остаётся на экране после остановки
const BALL_TRACK = 0.955;  // радиус, по которому шарик катится по ободу
const BALL_POCKET = 0.885; // радиус, на котором он лежит в ячейке
let bets = []; // { type, value, amount }
let lastBets = []; // ставки предыдущего раунда — для кнопки «Повторить»

const HISTORY_SIZE = 10;
const historyEl = document.getElementById('history-list');
const appEl = document.querySelector('.app');
const balanceBox = document.querySelector('.balance');
let spinHistory = []; // последние выпавшие числа, новое — первым

const STORAGE_KEY = 'depnaya-state';
// v2: значения колонок в lastBets — в нумерации сервера. Старые данные (без v) не берём.
// Баланс в localStorage не хранится: он только серверный. Старое поле balance игнорируется.
const STORAGE_VERSION = 2;

// Ставка в формате сервера: целые числа, value строго null у простых ставок
function isValidBet(b) {
  if (!b || typeof b !== 'object' || !BET_TYPES.includes(b.type)) return false;
  if (!Number.isSafeInteger(b.amount) || b.amount < 1) return false;
  if (b.type === 'number') return Number.isInteger(b.value) && b.value >= 0 && b.value <= 36;
  if (b.type === 'dozen' || b.type === 'column') return Number.isInteger(b.value) && b.value >= 1 && b.value <= 3;
  return b.value === null;
}

// Сохраняем после раунда: выпавшие числа и ставки последнего раунда
function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, spinHistory, lastBets }));
  } catch (e) {
    // localStorage может быть недоступен — игра просто работает без сохранения
  }
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (!saved) return;
    if (Array.isArray(saved.spinHistory)) {
      spinHistory = saved.spinHistory.filter((n) => WHEEL_ORDER.includes(n)).slice(0, HISTORY_SIZE);
    }
    if (saved.v === STORAGE_VERSION && Array.isArray(saved.lastBets)) {
      lastBets = saved.lastBets.filter(isValidBet);
    }
  } catch (e) {
    // повреждённые данные игнорируем и начинаем с начального состояния
  }
}

function getColor(n) {
  if (n === 0) return 'green';
  return RED_NUMBERS.has(n) ? 'red' : 'black';
}

// Рисуем колесо один раз. Сектор i находится по центру в углу i * SECTOR
// от верха (по часовой стрелке), поэтому 0 изначально под стрелкой.
// Палитра колеса из CSS-переменных корня (скин «стол» переопределяет их): читается при каждой отрисовке, колесо рисуется редко.
function wheelPalette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim() || 'transparent';
  return {
    sector: { red: v(WHEEL_SECTOR_VAR.red), black: v(WHEEL_SECTOR_VAR.black), green: v(WHEEL_SECTOR_VAR.green) },
    rimA: v('--wheel-rim-a'), rimB: v('--wheel-rim-b'), rimC: v('--wheel-rim-c'), line: v('--wheel-sector-line'),
    text: v('--wheel-text'), edge: v('--wheel-edge'), hub: v('--wheel-hub'), hubLine: v('--wheel-hub-line')
  };
}

function drawWheel() {
  const pal = wheelPalette();
  const size = canvas.width;
  const cx = size / 2;
  const cy = size / 2;
  const outer = size / 2;       // внешний край обода
  const radius = outer - 22;    // внешний край секторов — обод одинаковой толщины со всех сторон
  const rad = (deg) => (deg * Math.PI) / 180;

  ctx.clearRect(0, 0, size, size);

  // Обод рисуем прямо на колесе: он вращается вместе с ним и везде одинаков
  const rim = ctx.createRadialGradient(cx, cy, radius, cx, cy, outer);
  rim.addColorStop(0, pal.rimA);
  rim.addColorStop(0.5, pal.rimB);
  rim.addColorStop(1, pal.rimC);
  ctx.beginPath();
  ctx.arc(cx, cy, outer, 0, Math.PI * 2);
  ctx.fillStyle = rim;
  ctx.fill();

  WHEEL_ORDER.forEach((num, i) => {
    // в canvas угол 0 — справа, поэтому сдвигаем на -90°, чтобы считать от верха
    const start = rad(i * SECTOR - SECTOR / 2 - 90);
    const end = rad(i * SECTOR + SECTOR / 2 - 90);

    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, start, end);
    ctx.closePath();
    ctx.fillStyle = pal.sector[getColor(num)];
    ctx.fill();
    ctx.strokeStyle = pal.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    // подпись числа: поворачиваем холст к центру сектора и рисуем у края
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(i * SECTOR));
    ctx.fillStyle = pal.text;
    ctx.font = 'bold 28px "Playfair Display", Georgia, serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(num), 0, -radius + 34);
    ctx.restore();
  });

  // тонкая линия по границе секторов и обода
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = pal.edge;
  ctx.lineWidth = 3;
  ctx.stroke();

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = pal.hub;
  ctx.fill();
  ctx.strokeStyle = pal.hubLine;
  ctx.lineWidth = 4;
  ctx.stroke();
}

function renderHistory() {
  historyEl.innerHTML = '';
  if (spinHistory.length === 0) {
    const li = document.createElement('li');
    li.className = 'history-empty';
    li.textContent = 'пока пусто';
    historyEl.appendChild(li);
    return;
  }
  spinHistory.forEach((n) => {
    const li = document.createElement('li');
    li.className = getColor(n);
    li.textContent = n;
    historyEl.appendChild(li);
  });
}

// Подсветка результата раунда: зелёная при выигрыше, красная при проигрыше
function flash(net) {
  const kind = net > 0 ? 'win' : net < 0 ? 'lose' : '';
  [appEl, balanceBox].forEach((el) => el.classList.remove('win', 'lose'));
  if (!kind) return;
  void appEl.offsetWidth; // перезапускаем CSS-анимацию
  [appEl, balanceBox].forEach((el) => el.classList.add(kind));
}

// На экране доступный баланс: серверный минус разложенные ставки (вычисляется, не хранится)
const stakedTotal = () => bets.reduce((sum, b) => sum + b.amount, 0);
const availableBalance = () => srv.balance - stakedTotal();

function renderBalance() {
  stopBalanceAnimation(); // промежуточные кадры накрутки не должны перебивать актуальное значение
  const loading = !srv.loaded && !srv.error;
  balanceEl.classList.toggle('skeleton', loading);
  if (srv.loaded) balanceEl.textContent = spacedNumber(availableBalance());
  else balanceEl.textContent = srv.error ? '—' : 'Загрузка…';
  fitNumberFont(balanceEl, balanceEl.textContent.length);
  renderRouletteChips();
  refreshBetPanels();
  if (lobbyRender) lobbyRender();
}

// Фишки рулетки по серверному балансу (srv.balance меняется только по ответу сервера, после остановки колеса)
let rouletteChipValues = [];
function renderRouletteChips() {
  const values = chipSet(srv.loaded ? srv.balance : 0);
  if (values.join() === rouletteChipValues.join()) return;
  const prev = Number(amountEl.value);
  const wasChip = rouletteChipValues.includes(prev);
  rouletteChipValues = values;
  betsPanel.querySelectorAll('.chip').forEach((btn, i) => {
    btn.dataset.amount = String(values[i]);
    setChipText(btn, values[i]);
  });
  // выбранная фишка исчезла из набора: берём ближайшую
  if (wasChip && !values.includes(prev)) amountEl.value = String(nearestChip(values, prev));
  syncChips();
}

// Накрутка баланса после раунда: только отображение, значение уже серверное и итоговое
let balanceAnimFrame = 0;
function stopBalanceAnimation() {
  if (balanceAnimFrame) cancelAnimationFrame(balanceAnimFrame);
  balanceAnimFrame = 0;
}

function animateBalance(from, to) {
  stopBalanceAnimation();
  const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (calm || from === null || from === to) return;
  fitNumberFont(balanceEl, Math.max(spacedNumber(from).length, spacedNumber(to).length));
  const t0 = performance.now();
  const DURATION = 700;
  const frame = (now) => {
    const p = Math.min((now - t0) / DURATION, 1);
    const e = 1 - Math.pow(1 - p, 3);
    balanceEl.textContent = spacedNumber(Math.round(from + (to - from) * e));
    balanceAnimFrame = p < 1 ? requestAnimationFrame(frame) : 0;
  };
  balanceAnimFrame = requestAnimationFrame(frame);
}

// Подсветка плашки баланса без подсветки колеса (например, «недостаточно фишек»)
function flashBalance(kind) {
  balanceBox.classList.remove('win', 'lose');
  void balanceBox.offsetWidth;
  balanceBox.classList.add(kind);
}

// Колонка в нумерации СЕРВЕРА по числам, которые она покрывает: 1 — 1, 4, 7…34;
// 2 — 2, 5…35; 3 — 3, 6…36. Определяем по первому числу колонки, а не по ряду стола.
const serverColumnOf = (n) => ((n - 1) % 3) + 1;

// Строим вертикальный стол: 0 сверху, слева дюжины, три колонки чисел 1–36,
// под ними ставки на колонки (2 к 1), внизу внешние ставки.
function buildTable() {
  const addCell = (parent, cls, label, type, value = null, place = {}) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'cell ' + cls;
    btn.dataset.key = `${type}:${value}`;
    if (place.row) btn.style.gridRow = place.row;
    if (place.col) btn.style.gridColumn = place.col;
    const text = document.createElement('span');
    text.textContent = label;
    btn.appendChild(text);
    btn.addEventListener('click', () => placeBet(type, value));
    parent.appendChild(btn);
  };

  // Сетка: колонка 1 — дюжины, колонки 2–4 — числа (в ряду r: 3r-2, 3r-1, 3r).
  // Ряд 1: «0», ряды 2–13: числа, ряд 14: колонки, ряд 15: внешние ставки
  addCell(tableEl, 'green', '0', 'number', 0, { row: '1', col: '1 / -1' });
  for (let n = 1; n <= 36; n++) {
    addCell(tableEl, getColor(n), String(n), 'number', n, {
      row: String(2 + Math.floor((n - 1) / 3)),
      col: String(2 + ((n - 1) % 3))
    });
  }
  for (let d = 1; d <= 3; d++) {
    addCell(tableEl, 'dozen', DOZEN_NAME[d], 'dozen', d, {
      row: `${2 + (d - 1) * 4} / span 4`,
      col: '1'
    });
  }
  // ячейка под столбцом, где первое число i + 1 (1, 2 или 3)
  for (let i = 1; i <= 3; i++) {
    addCell(tableEl, 'column', '2 к 1', 'column', serverColumnOf(i), { row: '14', col: String(1 + i) });
  }
  const outsideRow = document.createElement('div');
  outsideRow.className = 'outside-row';
  outsideRow.style.gridRow = '15';
  tableEl.appendChild(outsideRow);
  [
    ['plain', 'Чёт', 'even'],
    ['red', 'Красн.', 'red'],
    ['black', 'Чёрн.', 'black'],
    ['plain', 'Нечет', 'odd']
  ].forEach(([cls, label, type]) => addCell(outsideRow, cls + ' outside', label, type));
}

// Кладём на клетки стола фишки с суммой ставки
function renderChips() {
  tableEl.querySelectorAll('.cell').forEach((cell) => {
    const old = cell.querySelector('.stack');
    if (old) old.remove();
    const bet = bets.find((b) => `${b.type}:${b.value}` === cell.dataset.key);
    if (!bet) return;
    const chip = document.createElement('i');
    chip.className = 'stack';
    chip.textContent = bet.amount >= 1000 ? Math.round(bet.amount / 100) / 10 + 'k' : bet.amount;
    cell.appendChild(chip);
  });
}

function renderBets() {
  renderChips();
  renderBalance();
  renderStatus();
  updateControls();
}

// Сообщение раунда (выигрыш, ошибка) и мелкий код ошибки под ним
function setMessage(text, kind = '', code = '') {
  messageLineEl.hidden = !text;
  messageLineEl.className = kind;
  messageLineEl.style.animation = 'none'; // тост показывается заново при каждом сообщении
  void messageLineEl.offsetWidth;
  messageLineEl.style.animation = '';
  messageTextEl.textContent = text;
  messageCodeEl.textContent = code ? 'код: ' + code : '';
}

// Постоянное состояние под сообщением: ошибка загрузки баланса или «фишки закончились»
function renderStatus() {
  let text = '';
  let code = '';
  if (!srv.loaded && srv.error) {
    text = srv.error.text;
    code = srv.error.code;
  } else if (srv.loaded && game.phase === 'idle' && srv.balance === 0) {
    text = 'Фишки закончились. Следующее начисление через ' + mmss((srv.deadline - performance.now()) / 1000);
  }
  statusLineEl.hidden = !text;
  statusTextEl.textContent = text;
  statusCodeEl.textContent = code ? 'код: ' + code : '';
}

// Состояние раунда: idle → sending (ждём ответ) → animating → idle; pending — связи не было,
// раунд мог быть засчитан, ждём «Повторить» с тем же request_id
const game = { phase: 'idle', round: null };
const gameBusy = () => game.phase !== 'idle';
registerGame({ id: 'roulette', busy: gameBusy });

// Блокирует/разблокирует ставки, кнопку «Крутить» и нижнюю панель
function updateControls() {
  const ready = !!(tg && tg.initData) && srv.loaded;
  const canBet = ready && !gameBusy();
  const canAdd = canBet && availableBalance() > 0;
  const loadRetry = !srv.loaded && srv.error && srv.error.retry;
  document.querySelectorAll('#table button').forEach((el) => { el.disabled = !canAdd; });
  document.querySelectorAll('#bets .chip, #bets input, #bets .step-btn, #bets .bet-maxdone, #repeat-bets, #clear-bets').forEach((el) => { el.disabled = !canBet; });
  const retry = game.phase === 'pending' || loadRetry;
  spinBtn.textContent = retry ? 'Повторить' : 'Крутить';
  spinBtn.classList.toggle('retry', retry);
  spinBtn.disabled = retry ? game.phase === 'sending' : !canBet;
  // пока идёт запрос, анимация или раунд не подтверждён, уйти с экрана нельзя
  navEl.querySelectorAll('.tab').forEach((el) => { el.disabled = gameBusy(); });
  gameSwitchEl.disabled = gameBusy();
}

// Ставки пока только на столе: сервер о них не знает. Лимита на размер ставки нет,
// проверка «не больше доступного» — удобство, настоящую делает сервер
function placeBet(type, value = null) {
  if (gameBusy() || !srv.loaded) return;
  const amount = Number(amountEl.value);
  if (!Number.isSafeInteger(amount) || amount < 1) {
    setMessage('Введите целую сумму ставки от 1', 'lose');
    return;
  }
  if (amount > availableBalance()) {
    setMessage('Недостаточно фишек', 'lose');
    flashBalance('lose');
    haptic('error');
    return;
  }
  const existing = bets.find((b) => b.type === type && b.value === value);
  if (existing) existing.amount += amount;
  else bets.push({ type, value, amount });
  setMessage('');
  renderBets();
  haptic('light');
  const stack = tableEl.querySelector(`.cell[data-key="${type}:${value}"] .stack`);
  if (stack) stack.classList.add('drop'); // фишка «садится» на клетку
}

// Повторяет ставки прошлого раунда (текущие ставки заменяются)
function repeatBets() {
  if (gameBusy() || !srv.loaded) return;
  if (lastBets.length === 0) {
    setMessage('Нет прошлой ставки для повтора', 'lose');
    return;
  }
  const needed = lastBets.reduce((sum, b) => sum + b.amount, 0);
  if (needed > srv.balance) {
    setMessage('Недостаточно фишек для повтора ставки', 'lose');
    flashBalance('lose');
    haptic('error');
    return;
  }
  bets = lastBets.map((b) => ({ ...b }));
  setMessage('');
  renderBets();
}

function clearBets() {
  if (gameBusy()) return;
  bets = [];
  setMessage('');
  renderBets();
}

const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

// Шарик подпрыгивает, прежде чем успокоиться в ячейке
function easeOutBounce(t) {
  const n = 7.5625;
  const d = 2.75;
  if (t < 1 / d) return n * t * t;
  if (t < 2 / d) return n * (t -= 1.5 / d) * t + 0.75;
  if (t < 2.5 / d) return n * (t -= 2.25 / d) * t + 0.9375;
  return n * (t -= 2.625 / d) * t + 0.984375;
}

// Ставит колесо и шарик в нужное положение
function renderSpin() {
  canvas.style.transform = `rotate(${wheelAngle}deg)`;
  const theta = ((wheelAngle + ballRel) * Math.PI) / 180;
  const half = wheelBox.clientWidth / 2;
  const x = Math.sin(theta) * ballRadius * half;
  const y = -Math.cos(theta) * ballRadius * half;
  ballEl.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px)`;
}

// Шарик (angle, radius) считается относительно колеса: сначала он несётся по ободу
// против вращения колеса, затем падает вниз и, попрыгав, замирает в ячейке.
// Так он всегда заканчивает в нужной ячейке и дальше едет вместе с колесом.
function animateSpin(index, onDone) {
  const finalRel = index * SECTOR;
  const wheelStart = wheelAngle;
  const wheelTravel = 720 + Math.random() * 360;
  const laps = 7 + Math.floor(Math.random() * 3);
  // стартовое положение шарика: ячейка прошлого результата, откуда он взлетает
  const back = (((ballRel - finalRel) % 360) + 360) % 360;
  const startRel = finalRel + back - laps * 360;
  const startRadius = ballRadius;
  const t0 = performance.now();

  function frame(now) {
    const p = Math.min((now - t0) / SPIN_TIME_MS, 1);
    const e = easeOutCubic(p);
    wheelAngle = wheelStart + wheelTravel * e;

    let rel = finalRel + (startRel - finalRel) * (1 - e);
    if (p < 0.04) {
      const u = p / 0.04; // взлёт на обод
      ballRadius = startRadius + (BALL_TRACK - startRadius) * (u * u * (3 - 2 * u));
    } else if (p < 0.6) {
      ballRadius = BALL_TRACK;
    } else {
      const q = (p - 0.6) / 0.4; // падение в ячейки
      ballRadius = BALL_TRACK + (BALL_POCKET - BALL_TRACK) * easeOutBounce(q);
      rel += SECTOR * 1.6 * Math.sin(q * Math.PI * 5) * Math.pow(1 - q, 2);
    }
    ballRel = rel;
    renderSpin();

    if (p < 1) {
      requestAnimationFrame(frame);
    } else {
      ballRel = finalRel;
      ballRadius = BALL_POCKET;
      renderSpin();
      onDone();
    }
  }
  requestAnimationFrame(frame);
}

// #endregion

// #region Рулетка: ставка через сервер
// ---------- ставка через сервер ----------
// Сервер сам выбирает число и считает выигрыш. Клиент шлёт только request_id и ставки.

// Один POST. Возвращает { kind: 'ok', data } | { kind: 'fatal', text, code, refresh } | { kind: 'retry', code }
async function postRound(round) {
  try {
    const res = await postJson('/api/roulette/spin', { request_id: round.id, bets: round.bets });
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', code: '401' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Ошибка ставок', code: '400' };
    if (res.status === 409) {
      let detail = '';
      try { detail = (await res.json()).detail; } catch (e) { detail = ''; }
      if (detail === 'insufficient_funds') return { kind: 'fatal', text: 'Недостаточно фишек', code: '409', refresh: true };
      if (detail === 'balance_limit') return { kind: 'fatal', text: 'Достигнут максимальный баланс', code: '409' };
      return { kind: 'fatal', text: 'Не удалось выполнить ставку', code: '409' };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось выполнить ставку', code: String(res.status) };
    }
    const body = await readJsonBody(res);
    const d = body.ok ? body.data : null;
    const valid = d && isInt(d.number) && d.number >= 0 && d.number <= 36
      && isInt(d.stake_total) && d.stake_total >= 1 && isInt(d.payout_total) && d.payout_total >= 0
      && d.net === d.payout_total - d.stake_total && isInt(d.balance) && d.balance >= 0;
    // ответ 2xx не повторяем: раунд уже обработан сервером, баланс узнаём отдельным запросом
    return valid ? { kind: 'ok', data: d }
      : { kind: 'fatal', text: 'Ответ сервера не распознан. Баланс обновлён', code: 'ответ', refresh: true };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// Отправляет раунд (до 3 попыток с ТЕМ ЖЕ request_id: повтор безопасен, сервер не спишет дважды)
async function submitRound() {
  const round = game.round;
  game.phase = 'sending';
  setMessage('Крутим…');
  updateControls();
  let last = { code: 'сеть' };
  for (let attempt = 0; attempt < ROUND_ATTEMPTS; attempt++) {
    if (attempt > 0) await sleep(ROUND_PAUSES_MS[attempt - 1]);
    const r = await postRound(round);
    if (r.kind === 'ok') {
      playRound(round, r.data);
      return;
    }
    if (r.kind === 'fatal') {
      // ставки разблокируем, чтобы игрок мог их исправить; request_id выбрасываем
      game.phase = 'idle';
      game.round = null;
      setMessage(r.text, 'lose', r.code);
      renderBets();
      if (r.refresh) loadServer('after');
      return;
    }
    last = r;
  }
  game.phase = 'pending';
  setMessage('Нет связи. Раунд мог быть засчитан. Нажмите «Повторить»', 'lose', last.code);
  updateControls();
}

function spin() {
  if (game.phase === 'pending') {
    submitRound(); // тот же request_id и те же ставки
    return;
  }
  if (!srv.loaded && srv.error && srv.error.retry) {
    loadServer('manual');
    return;
  }
  // защита от двойного нажатия: пока идёт запрос или анимация, игнорируем
  if (gameBusy() || !srv.loaded) return;
  if (bets.length === 0) {
    setMessage('Сначала сделайте ставку', 'lose');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    setMessage('Ошибка', 'lose', 'request_id');
    return;
  }
  game.round = { id, bets: bets.map((b) => ({ type: b.type, value: b.value, amount: b.amount })) };
  submitRound();
}

// Анимация к числу из ответа сервера; баланс на экране не меняется до её конца
function playRound(round, data) {
  game.phase = 'animating';
  updateControls();
  layerResultEl.className = 'layer-result';
  layerResultEl.textContent = '';
  layerWinEl.className = 'layer-win';
  layerWinEl.textContent = '';
  wheelLayer.classList.add('active');
  animateSpin(WHEEL_ORDER.indexOf(data.number), () => {
    layerResultEl.textContent = data.number;
    layerResultEl.className = 'layer-result ' + getColor(data.number);
    showResult(round, data);
    setTimeout(() => wheelLayer.classList.remove('active'), RESULT_HOLD_MS);
  });
}

function showResult(round, data) {
  const shownBefore = srv.loaded ? availableBalance() : null; // что было на экране до результата
  const n = data.number;
  numberEl.textContent = n;
  numberEl.className = 'result-number ' + getColor(n);

  spinHistory.unshift(n);
  spinHistory.length = Math.min(spinHistory.length, HISTORY_SIZE);
  renderHistory();

  // только теперь серверный баланс подставляется на экран
  srv.balance = data.balance;
  srv.loaded = true;
  bets = [];
  lastBets = round.bets.map((b) => ({ ...b }));
  game.phase = 'idle';
  game.round = null;
  saveState();
  flash(data.net);
  if (data.net > 0) setMessage(`Вы выиграли ${formatNumber(data.net)} фишек!`, 'win');
  else if (data.net < 0) setMessage(`Вы проиграли ${formatNumber(-data.net)} фишек`, 'lose');
  else setMessage('Ничья: ставки вернулись', '');
  renderAll();
  // оформление результата: накрутка баланса, итог на колесе, вибрация (данные раунда не меняются)
  animateBalance(shownBefore, srv.balance);
  if (data.net !== 0) {
    layerWinEl.textContent = (data.net > 0 ? '+' : '−') + formatNumber(Math.abs(data.net));
    layerWinEl.className = 'layer-win ' + (data.net > 0 ? 'win' : 'lose');
  }
  haptic(data.net > 0 ? 'success' : data.net < 0 ? 'error' : 'light');
  loadServer('after'); // обновит таймер (не чаще, чем раз в 5 секунд)
}

// #endregion

// #region Баланс и профиль (/api/me)
// ---------- серверное состояние: общее для экрана игры и вкладки «Профиль» ----------
// Баланс только серверный и хранится в памяти страницы, в localStorage он не пишется.

const srv = {
  loaded: false,   // получен ли хотя бы один ответ
  balance: 0,
  rate: 0,
  deadline: 0,     // performance.now(), когда таймер дойдёт до нуля
  error: null,     // { text, code, retry } последней неудачной загрузки
  incomeLevel: null,
  storageLevel: null,
  level: null,        // уровень профиля из /api/me
  transferLimits: null,   // лимиты переводов из /api/me: {min, max, daily_left, fee_percent, min_level, cooldown_seconds, min_age_hours, min_staked, unlimited}
  incomingUnseen: null,   // непросмотренные входящие переводы: {count, total}
  noChat: false,          // приложение открыто вне беседы (по рейтингу или по ответу списка участников): переводы недоступны
  farm: null,         // блок фермы из /api/me: {income_per_hour, per_minute_estimate, next_tick_in_s, hours_cap, accrued_now}
  activeGame: null    // незавершённая игра из /api/me: "mines" | "blackjack" | "crash" | "hilo" | null
};
let lobbyStartDecided = false;   // при запуске выбор «титульный экран или активная игра» делается один раз

const profileEls = {
  data: document.getElementById('profile-data'),
  balance: document.getElementById('profile-balance'),
  rate: document.getElementById('profile-rate'),
  timer: document.getElementById('profile-timer'),
  ring: document.getElementById('profile-ring'),
  skel: document.getElementById('profile-skel'),
  name: document.getElementById('profile-name'),
  upgrades: document.getElementById('profile-upgrades'),
  upgradesRow: document.getElementById('profile-upgrades-row'),
  avatar: document.getElementById('profile-avatar'),
  msg: document.getElementById('profile-msg'),
  code: document.getElementById('profile-code'),
  retry: document.getElementById('profile-retry')
};

let started = false;            // игра полностью собрана
let srvInFlight = false;
let srvLastRequestAt = -Infinity; // performance.now() последнего запроса
let srvLastFailed = false;
let srvFetchTimer = null;       // отложенный запрос (ноль таймера или автоповтор после ошибки)

// данные нужны, только пока открыт экран рулетки или «Профиль»
const srvWanted = () => activeTab === 'profile' || activeTab === 'farm'
  || (activeTab === 'play' && (currentGame === 'lobby' || currentGame === 'roulette' || currentGame === 'mines' || currentGame === 'keno' || currentGame === 'blackjack' || currentGame === 'crash' || currentGame === 'hilo'));

function renderProfile() {
  renderProfileIdentity();
  if (srv.error) {
    profileEls.skel.hidden = true;
    profileEls.data.hidden = true;
    profileEls.msg.textContent = srv.error.text;
    profileEls.code.textContent = 'код: ' + srv.error.code;
    profileEls.retry.hidden = !srv.error.retry;
  } else if (srv.loaded) {
    profileEls.balance.textContent = formatNumber(srv.balance); // серверный, без вычета ставок на столе
    fitNumberFont(profileEls.balance, profileEls.balance.textContent.length);
    profileEls.rate.textContent = formatNumber(srv.rate);
    const hasUpgrades = srv.incomeLevel !== null && srv.storageLevel !== null;
    profileEls.upgradesRow.hidden = !hasUpgrades; // без поля от сервера строки нет
    if (hasUpgrades) profileEls.upgrades.textContent = `доход ${srv.incomeLevel}, хранилище ${srv.storageLevel}`;
    profileEls.skel.hidden = true;
    profileEls.data.hidden = false;
    profileEls.msg.textContent = '';
    profileEls.code.textContent = '';
    profileEls.retry.hidden = true;
    renderProfileTimer();
  } else {
    profileEls.data.hidden = true;
    profileEls.skel.hidden = false; // скелетон вместо спиннера
    profileEls.msg.textContent = '';
    profileEls.code.textContent = '';
    profileEls.retry.hidden = true;
  }
}

// Имя и аватар из Telegram (initDataUnsafe) только для показа на этом экране: никуда не отправляются,
// в localStorage не пишутся; картинок нет, аватар — круг с первой буквой имени
const initialOf = (name) => (Array.from(String(name || '').trim())[0] || '·');

function renderProfileIdentity() {
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  const name = user && typeof user.first_name === 'string' ? user.first_name : '';
  profileEls.name.textContent = name;
  profileEls.avatar.textContent = initialOf(name);
  profileEls.name.parentElement.hidden = !name;
}

// Кольцо до следующего начисления: фишки приходят каждую минуту (доход в час делится на минутные начисления)
const ACCRUAL_PERIOD_S = 60;   // фишки приходят каждую минуту
const RING_LENGTH = 2 * Math.PI * 52;
let ringFraction = 0;

function renderProfileTimer() {
  const left = (srv.deadline - performance.now()) / 1000;
  profileEls.timer.textContent = mmss(left);
  const fraction = Math.min(1, Math.max(0, 1 - left / ACCRUAL_PERIOD_S));
  profileEls.ring.style.strokeDasharray = String(RING_LENGTH);
  // после начисления кольцо сбрасывается сразу, без обратной анимации
  profileEls.ring.style.transition = fraction < ringFraction - 0.5 ? 'none' : '';
  profileEls.ring.style.strokeDashoffset = String(RING_LENGTH * (1 - fraction));
  ringFraction = fraction;
}

// renderKeno и renderLobby появляются ниже по файлу (до их объявления остаются null)
var kenoRender = null; // eslint-disable-line no-var
var lobbyRender = null; // eslint-disable-line no-var

function renderAll() {
  renderProfile();
  renderFarmIncome();
  renderBets();
  if (kenoRender) kenoRender();
  if (lobbyRender) lobbyRender();
}

// Раз в секунду обновляем таймеры (по монотонным часам, а не по часам устройства)
setInterval(() => {
  if (!started) return;
  if (srv.loaded && !srv.error) renderProfileTimer();
  renderFarmIncome();
  renderStatus();
}, 1000);

// Запланировать запрос не раньше, чем через delay мс, и не чаще REQUEST_GAP_MS
function scheduleServerFetch(delay) {
  clearTimeout(srvFetchTimer);
  const gapLeft = REQUEST_GAP_MS - (performance.now() - srvLastRequestAt);
  // +20 мс запаса: таймер браузера может сработать чуть раньше расчётного времени
  srvFetchTimer = setTimeout(() => loadServer('timer'), Math.max(delay, gapLeft > 0 ? gapLeft + 20 : 0));
}

function failServer(text, code, retry) {
  lobbyStartDecided = true;   // нет ответа: остаёмся на титульном экране
  srvLastFailed = true;
  srv.error = { text, code, retry };
  renderAll();
  if (retry) scheduleServerFetch(ERROR_RETRY_MS);
}

// Единственная функция запроса /api/me. reason: 'open' | 'visible' (с ограничением по частоте)
// | 'timer' | 'manual' | 'after' (ручной запрос и запрос после раунда не теряются: откладываются)
// Балансы игр хранятся у каждой игры отдельно: подпись нужна, чтобы устаревший ответ /api/me не затёр свежие значения
const balanceSignature = () => [srv.balance, ...gameRegistry.filter((g) => g.state).map((g) => g.state.balance)].join('|');
// идёт запрос или анимация какой-либо игры: баланс на экране менять нельзя, данные ставятся в очередь (повтор через секунду)
const anyRoundBusy = () => gameRegistry.some((g) => g.busy());
let srvNeedsTick = false;        // вкладка была скрыта, когда пришло время минутного запроса: при возврате обновляем сразу

async function loadServer(reason) {
  if (srvInFlight || !srvWanted()) return;
  if (reason === 'timer' && document.visibilityState !== 'visible') {
    srvNeedsTick = true;    // скрытая вкладка не опрашивает сервер; при возврате в приложение обновим сразу
    return;
  }
  if (anyRoundBusy()) {
    // во время запроса раунда и анимации баланс на экране менять нельзя
    scheduleServerFetch(1000);
    return;
  }
  const now = performance.now();
  const sinceLast = now - srvLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason !== 'open' && reason !== 'visible') scheduleServerFetch(0);
    return;
  }
  if ((reason === 'open' || reason === 'visible') && !srvNeedsTick && sinceLast < (srvLastFailed ? ERROR_RETRY_MS : REFRESH_MIN_MS)) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    failServer('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(srvFetchTimer);
  srvInFlight = true;
  srvNeedsTick = false;
  srvLastRequestAt = now;
  const sigBefore = balanceSignature();
  if (!srv.loaded && !srv.error) renderAll();

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/me', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      // initData живёт ограниченное время, повторять запрос бессмысленно
      failServer('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (!res.ok) {
      failServer('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    const ok = (v, max) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= max;
    // баланс: целое от 0 до MAX_SAFE_INTEGER (серверный потолок, bot/roulette.py MAX_SAFE_INT)
    if (!d || !isCount(d.balance) || !ok(d.rate, 1e9) || !ok(d.seconds_to_next, 86400)) {
      failServer('Нет связи с сервером', 'ответ', true);
      return;
    }
    if (anyRoundBusy() || balanceSignature() !== sigBefore) {
      // пока шёл запрос, начался или закончился раунд: этот ответ уже мог устареть
      scheduleServerFetch(1000);
      return;
    }
    srvLastFailed = false;
    srv.error = null;
    const prevBalance = srv.loaded ? srv.balance : null;
    srv.loaded = true;
    srv.balance = d.balance;
    srv.rate = d.rate;
    // уровни улучшений (если сервер их прислал) нужны только для показа в профиле
    srv.incomeLevel = isCount(d.income_level) ? d.income_level : null;
    srv.storageLevel = isCount(d.storage_level) ? d.storage_level : null;
    srv.deadline = performance.now() + d.seconds_to_next * 1000;
    srv.level = isCount(d.level) ? d.level : null;
    const tl = d.transfer_limits;
    srv.transferLimits = tl && ['min', 'max', 'daily_left', 'fee_percent', 'min_level', 'cooldown_seconds', 'min_age_hours', 'min_staked'].every((k) => isCount(tl[k]))
      && typeof tl.unlimited === 'boolean' ? tl : null;
    const iu = d.incoming_unseen;
    srv.incomingUnseen = iu && isCount(iu.count) && isCount(iu.total) ? iu : null;
    srv.activeGame = ['mines', 'blackjack', 'crash', 'hilo'].includes(d.active_game) ? d.active_game : null;
    applySkins(d.cosmetics && d.cosmetics.equipped);       // внешний вид по надетому (только оформление)
    const f = d.farm;
    srv.farm = f && isCount(f.income_per_hour) && typeof f.per_minute_estimate === 'string' && /^\d+\.\d$/.test(f.per_minute_estimate)
      && isCount(f.next_tick_in_s) && isCount(f.hours_cap) && isCount(f.accrued_now) ? f : null;
    renderAll();
    // сумма для «+N»: по accrued_now этого запроса; при возврате в приложение другой запрос (например, экрана фермы) мог подтянуть
    // начисление раньше, тогда берётся прирост баланса (не больше максимума накопления)
    let gained = srv.farm ? srv.farm.accrued_now : 0;
    if (gained === 0 && reason === 'visible' && srv.farm && prevBalance !== null && d.balance > prevBalance
      && d.balance - prevBalance <= srv.farm.income_per_hour * srv.farm.hours_cap) gained = d.balance - prevBalance;
    applyAccrualTick(gained);
    notifyIncoming();
    if (!lobbyStartDecided) {
      // запуск: если у игрока есть незавершённая игра, сразу открываем её экран (он сам восстановит раунд)
      lobbyStartDecided = true;
      if (srv.activeGame && activeTab === 'play' && currentGame === 'lobby') selectGame(srv.activeGame);
    }
    scheduleServerFetch(d.seconds_to_next * 1000 + ZERO_DELAY_MS);
  } catch (e) {
    // fetch не различает сбой сети и запрет CORS, поэтому код с вопросом
    const aborted = e && e.name === 'AbortError';
    failServer('Нет связи с сервером', aborted ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    srvInFlight = false;
  }
}

profileEls.retry.addEventListener('click', () => loadServer('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadServer('visible');
});

// #endregion

// #region Рейтинг
// ---------- вкладка «Рейтинг»: рейтинг беседы с сервера (только чтение) ----------
// Какая это беседа, решает сервер по подписи initData; клиент ничего из неё не разбирает.
// Данные хранятся только в памяти страницы, в localStorage не пишутся. Автоповтора при
// ошибке нет: только кнопка «Повторить».
const ratingEls = {
  title: document.getElementById('rating-title'),
  card: document.getElementById('rating-card'),
  list: document.getElementById('rating-list'),
  me: document.getElementById('rating-me'),
  total: document.getElementById('rating-total'),
  msg: document.getElementById('rating-msg'),
  code: document.getElementById('rating-code'),
  retry: document.getElementById('rating-retry'),
  skel: document.getElementById('rating-skel')
};

let ratingInFlight = false;
let ratingLastRequestAt = -Infinity; // performance.now() последнего запроса
let ratingTimer = null;              // отложенное нажатие «Повторить»
let ratingHasData = false;

function showRatingMessage(text, code, canRetry) {
  ratingEls.skel.hidden = true;
  ratingEls.card.hidden = true;
  ratingEls.title.textContent = 'Рейтинг';
  ratingEls.msg.textContent = text;
  ratingEls.code.textContent = code ? 'код: ' + code : '';
  ratingEls.retry.hidden = !canRetry;
  ratingHasData = false;
}

function failRating(text, code, canRetry) {
  showRatingMessage(text, code, canRetry);
}

// форма ответа: scope «none» или «chat» с не более чем 10 записями и позицией игрока
function validRating(d) {
  if (!d || typeof d !== 'object' || typeof d.scope !== 'string') return false;
  if (d.scope === 'none') return true;
  if (d.scope !== 'chat') return false;
  if (!Array.isArray(d.top) || d.top.length > 10) return false;
  const rowsOk = d.top.every((e) => e && typeof e === 'object' && isCount(e.rank) && isCount(e.balance)
    && typeof e.name === 'string' && typeof e.is_me === 'boolean');
  return rowsOk && !!d.me && typeof d.me === 'object' && isCount(d.me.rank) && isCount(d.me.balance) && isCount(d.me.total);
}

// Все тексты с сервера (в том числе имена) выводятся только через textContent
function levelBadge(level) {
  const el = document.createElement('span');
  el.className = 'rating-lvl';
  el.textContent = 'Ур. ' + level;
  return el;
}

const CROWN_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/></svg>';

function showRating(d) {
  ratingEls.skel.hidden = true;
  ratingEls.msg.textContent = '';
  ratingEls.code.textContent = '';
  ratingEls.retry.hidden = true;
  ratingHasData = true;
  srv.noChat = d.scope === 'none';
  renderTransferEntry();
  if (d.scope === 'none') {
    ratingEls.card.hidden = true;
    ratingEls.title.textContent = 'Рейтинг';
    ratingEls.msg.textContent = 'Рейтинг работает в беседах. Откройте игру по ссылке из группового чата, и здесь появится рейтинг участников';
    return;
  }
  ratingEls.title.textContent = 'Рейтинг беседы';
  ratingEls.list.textContent = '';
  d.top.forEach((e) => {
    const li = document.createElement('li');
    li.className = (e.is_me ? 'me ' : '') + (e.rank >= 1 && e.rank <= 3 ? 'top' + e.rank : '');
    const rank = document.createElement('span');
    rank.className = 'rating-rank';
    if (e.rank === 1) {
      rank.innerHTML = CROWN_SVG; // постоянная разметка иконки, данных сервера в ней нет
      rank.setAttribute('aria-label', '1');
    } else {
      rank.textContent = e.rank;
    }
    const avatar = document.createElement('span');
    avatar.className = 'avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = initialOf(e.name);
    const name = document.createElement('span');
    name.className = 'rating-name';
    name.textContent = e.name;
    const who = document.createElement('span');
    who.className = 'rating-who';
    who.appendChild(name);
    if (isCount(e.level)) who.appendChild(levelBadge(e.level)); // без поля level подписи нет
    const bal = document.createElement('span');
    bal.className = 'rating-bal';
    setNumber(bal, e.balance);
    li.append(rank, avatar, who, bal);
    if (!e.is_me && typeof e.member_ref === 'string') {
      // участник беседы: нажатие открывает панель перевода (метка непрозрачная, Telegram ID клиент не знает)
      li.classList.add('tap');
      li.tabIndex = 0;
      li.setAttribute('role', 'button');
      li.setAttribute('aria-label', 'Перевести фишки: ' + e.name);
      li.addEventListener('click', () => openTransfer(e.member_ref, e.name));
      li.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openTransfer(e.member_ref, e.name); } });
    }
    // сумма ставок за всё время (поле staked); в старом ответе его нет, тогда строки нет
    if (isCount(e.staked)) {
      const staked = document.createElement('span');
      staked.className = 'rating-staked';
      staked.textContent = 'поставлено ' + formatNumber(e.staked);
      li.appendChild(staked);
    }
    ratingEls.list.appendChild(li);
  });
  // строка текущего пользователя закреплена под списком
  const me = ratingEls.me;
  me.textContent = '';
  const myRank = document.createElement('span');
  myRank.className = 'rating-rank';
  myRank.textContent = d.me.rank;
  const myAvatar = document.createElement('span');
  myAvatar.className = 'avatar';
  myAvatar.setAttribute('aria-hidden', 'true');
  const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  myAvatar.textContent = initialOf(tgUser && tgUser.first_name ? tgUser.first_name : 'Я');
  const myName = document.createElement('span');
  myName.className = 'rating-name';
  myName.textContent = 'Вы';
  const myWho = document.createElement('span');
  myWho.className = 'rating-who';
  myWho.appendChild(myName);
  if (isCount(d.me.level)) myWho.appendChild(levelBadge(d.me.level));
  const myBal = document.createElement('span');
  myBal.className = 'rating-bal';
  setNumber(myBal, d.me.balance);
  const mySub = document.createElement('span');
  mySub.className = 'rating-staked';
  mySub.textContent = `${d.me.rank}-е место из ${d.me.total}` + (isCount(d.me.staked) ? `, поставлено ${formatNumber(d.me.staked)}` : '');
  me.append(myRank, myAvatar, myWho, myBal, mySub);
  // итог по беседе (поле chat_staked); без поля строка скрыта
  if (isCount(d.chat_staked)) {
    ratingEls.total.textContent = 'Поставлено участниками беседы за всё время: ' + formatNumber(d.chat_staked);
    ratingEls.total.hidden = false;
  } else {
    ratingEls.total.textContent = '';
    ratingEls.total.hidden = true;
  }
  ratingEls.card.hidden = false;
}

// reason: 'open' | 'visible' (не чаще раза в 10 секунд) | 'manual' («Повторить»).
// Между любыми двумя запросами не меньше 5 секунд
async function loadRating(reason) {
  if (ratingInFlight || activeTab !== 'rating') return;
  const now = performance.now();
  const sinceLast = now - ratingLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason === 'manual') {
      // нажатие не теряем: запрос уйдёт, когда пройдут 5 секунд
      clearTimeout(ratingTimer);
      ratingTimer = setTimeout(() => loadRating('manual'), REQUEST_GAP_MS - sinceLast + 20);
    }
    return;
  }
  if (reason !== 'manual' && sinceLast < REFRESH_MIN_MS) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    failRating('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(ratingTimer);
  ratingInFlight = true;
  ratingLastRequestAt = now;
  if (!ratingHasData) {
    ratingEls.skel.hidden = false; // скелетон вместо спиннера
    ratingEls.msg.textContent = '';
    ratingEls.code.textContent = '';
    ratingEls.retry.hidden = true;
  }

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/chat/top', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      failRating('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (!res.ok) {
      failRating('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    if (!validRating(d)) {
      failRating('Нет связи с сервером', 'ответ', true);
      return;
    }
    showRating(d);
  } catch (e) {
    // fetch не различает сбой сети и запрет CORS, поэтому код с вопросом
    failRating('Нет связи с сервером', e && e.name === 'AbortError' ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    ratingInFlight = false;
  }
}

ratingEls.retry.addEventListener('click', () => loadRating('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadRating('visible');
});

// #endregion

// #region Ферма
// ---------- вкладка «Ферма»: улучшения дохода и хранилища за фишки ----------
// Все числа и состояния приходят с сервера (GET /api/farm), клиент ничего не считает и не хранит.
// Покупка: POST /api/farm/buy, до 3 попыток с одним request_id; 429 повторяется в postJson.
const farmEls = {
  body: document.getElementById('farm-body'),
  skel: document.getElementById('farm-skel'),
  msg: document.getElementById('farm-msg'),
  code: document.getElementById('farm-code'),
  retry: document.getElementById('farm-retry'),
  note: document.getElementById('farm-note'),
  income: document.getElementById('farm-income'),
  incHour: document.getElementById('farm-inc-hour'),
  incMin: document.getElementById('farm-inc-min'),
  incTimer: document.getElementById('farm-inc-timer'),
  incNote: document.getElementById('farm-inc-note'),
  level: document.getElementById('farm-level'),
  balance: document.getElementById('farm-balance'),
  bar: document.getElementById('farm-bar'),
  barFill: document.getElementById('farm-bar-fill'),
  progress: document.getElementById('farm-progress'),
  hintBtn: document.getElementById('farm-hint-btn'),
  hint: document.getElementById('farm-hint'),
  staked: document.getElementById('farm-staked'),
  slots: document.getElementById('farm-slots'),
  cards: document.getElementById('farm-cards')
};
const FARM_KINDS = { income: 'Доход', storage: 'Хранилище' };
const FARM_REASONS = {
  level_locked: 'Нужен уровень профиля',
  insufficient_funds: 'Не хватает фишек',
  max_level: 'Максимальный уровень'
};

let farmInFlight = false;
let farmLastRequestAt = -Infinity;
let farmTimer = null;
let farmHasData = false;
let farmBusy = false;   // идёт покупка
let farmData = null;    // последний ответ GET /api/farm

const farmCards = {};
Object.keys(FARM_KINDS).forEach((kind) => {
  const card = document.createElement('article');
  card.className = 'farm-card';
  const head = document.createElement('div');
  head.className = 'farm-card-head';
  const title = document.createElement('h3');
  title.textContent = FARM_KINDS[kind];
  const lvl = document.createElement('span');
  lvl.className = 'farm-lvl';
  head.append(title, lvl);
  const effect = document.createElement('div');
  effect.className = 'farm-effect';
  const cost = document.createElement('div');
  cost.className = 'farm-cost';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'action farm-buy';
  btn.textContent = 'Улучшить';
  btn.addEventListener('click', () => buyUpgrade(kind));
  const reason = document.createElement('small');
  reason.className = 'farm-reason';
  card.append(head, effect, cost, btn, reason);
  farmEls.cards.appendChild(card);
  farmCards[kind] = { lvl, effect, cost, btn, reason };
});

function setFarmNote(text, kind = '') {
  farmEls.note.textContent = text;
  farmEls.note.className = 'farm-note' + (kind ? ' ' + kind : '');
}

function showFarmMessage(text, code, canRetry) {
  farmEls.skel.hidden = true;
  farmEls.body.hidden = true;
  farmEls.msg.textContent = text;
  farmEls.code.textContent = code ? 'код: ' + code : '';
  farmEls.retry.hidden = !canRetry;
  farmHasData = false;
}

const isLevelPair = (o, extra) => !!o && typeof o === 'object' && isCount(o.level) && isCount(o.max)
  && extra.every((k) => isCount(o[k])) && typeof o.can_buy === 'boolean'
  && (o.reason === null || typeof o.reason === 'string');

function validFarm(d) {
  if (!d || typeof d !== 'object' || !isCount(d.balance)) return false;
  const p = d.profile;
  const s = d.slots;
  if (!p || !isCount(p.level) || !isCount(p.staked) || !(p.next_threshold === null || isCount(p.next_threshold))) return false;
  if (!s || !isCount(s.used) || !isCount(s.total)) return false;
  const nullable = (o, k) => o[k] === null || isCount(o[k]);
  return isLevelPair(d.income, ['rate']) && nullable(d.income, 'next_rate') && nullable(d.income, 'next_cost')
    && isLevelPair(d.storage, ['hours']) && nullable(d.storage, 'next_hours') && nullable(d.storage, 'next_cost');
}

function renderFarmCard(kind, o, slots) {
  const c = farmCards[kind];
  c.lvl.textContent = `${o.level} / ${o.max}`;
  if (kind === 'income') {
    c.effect.textContent = `${formatNumber(o.rate)} в час` + (o.next_rate !== null ? ` → ${formatNumber(o.next_rate)} в час` : '');
  } else {
    c.effect.textContent = `${o.hours} ч` + (o.next_hours !== null ? ` → ${o.next_hours} ч` : '');
  }
  c.cost.textContent = '';
  if (o.next_cost !== null) {
    const value = document.createElement('b');
    setNumber(value, o.next_cost);
    c.cost.append('Цена: ', value);
  }
  c.btn.disabled = !o.can_buy || farmBusy;
  let reason = o.reason ? FARM_REASONS[o.reason] || '' : '';
  if (o.reason === 'level_locked') reason += ' ' + (slots.used + 1);
  c.reason.textContent = reason;
}

// #endregion

// #region Минутное начисление
// ---------- минутное начисление: экран фермы, балансы игр, «+N» ----------
// Блок дохода на экране фермы: берётся из /api/me (srv.farm), таймер идёт по монотонным часам и обновляется раз в секунду
function renderFarmIncome() {
  const f = srv.farm;
  const show = !!f && srv.loaded && farmHasData;
  farmEls.income.hidden = !show;
  if (!show) return;
  farmEls.incHour.textContent = formatNumber(f.income_per_hour);
  farmEls.incMin.textContent = f.per_minute_estimate;
  farmEls.incTimer.textContent = mmss((srv.deadline - performance.now()) / 1000);
  farmEls.incNote.textContent = 'Пока вас нет, доход копится до ' + f.hours_cap + ' ч, дальше не начисляется.';
}

// Балансы игр хранятся у каждой игры отдельно: после минутного начисления подтягиваем их к серверному (игры без запроса и анимации;
// полёт краша и ход в процессе не трогаем: они обновятся сами по итогу)
function syncGameBalances() {
  gameRegistry.forEach(({ state: g, render, keepBalance }) => {
    if (!g) return;
    if (g.balance === null || g.balance === srv.balance || g.busy || g.animating) return;
    if (keepBalance && keepBalance()) return;
    g.balance = srv.balance;
    render();
  });
}

// «+N» над балансом: поверх экрана (вёрстку не двигает), без звука, не дольше 1,4 секунды
function showAccrualPop(amount) {
  if (document.visibilityState !== 'visible') return;
  const host = ['.screen:not([hidden]) .balance strong', '.screen:not([hidden]) #profile-balance', '.screen:not([hidden]) #farm-balance']
    .map((sel) => document.querySelector(sel)).find((el) => el && el.getBoundingClientRect().width > 0);
  if (!host) return;
  const rect = host.getBoundingClientRect();
  const pop = document.createElement('span');
  pop.className = 'accrual-pop';
  pop.textContent = '+' + formatCompact(amount);
  pop.title = '+' + formatNumber(amount);
  pop.style.left = Math.round(rect.left) + 'px';
  pop.style.top = Math.max(0, Math.round(rect.top) - 4) + 'px';
  document.body.appendChild(pop);
  setTimeout(() => pop.remove(), 1500);
}

function applyAccrualTick(accrued) {
  syncGameBalances();
  if (farmData && !farmEls.body.hidden) {
    farmData.balance = srv.balance;
    setNumber(farmEls.balance, srv.balance);
    updateFarmButtons();
  }
  renderFarmIncome();
  if (accrued > 0) showAccrualPop(accrued);
}

function renderFarm(d) {
  farmData = d;
  farmHasData = true;
  farmEls.skel.hidden = true;
  farmEls.msg.textContent = '';
  farmEls.code.textContent = '';
  farmEls.retry.hidden = true;
  farmEls.body.hidden = false;
  farmEls.level.textContent = 'Уровень ' + d.profile.level;
  setNumber(farmEls.balance, d.balance);
  // уровень растёт от опыта (profile.xp); если сервер xp не прислал, показываем сумму ставок, как раньше
  const hasXp = isCount(d.profile.xp);
  const progressValue = hasXp ? d.profile.xp : d.profile.staked;
  farmEls.progress.textContent = '';
  if (d.profile.next_threshold === null) {
    farmEls.progress.textContent = 'Максимальный уровень';
    farmEls.barFill.style.width = '100%';
    farmEls.bar.setAttribute('aria-valuenow', '100');
  } else {
    const have = document.createElement('b');
    const next = document.createElement('b');
    setNumber(have, progressValue);
    setNumber(next, d.profile.next_threshold);
    farmEls.progress.append(hasXp ? 'Опыт ' : 'Поставлено ', have, ' / ', next);
    const pct = Math.min(100, Math.floor((progressValue / d.profile.next_threshold) * 100));
    farmEls.barFill.style.width = pct + '%';
    farmEls.bar.setAttribute('aria-valuenow', String(pct));
  }
  // подсказка про опыт нужна только когда показывается опыт; сумма ставок остаётся мелкой справкой
  farmEls.hintBtn.hidden = !hasXp;
  if (!hasXp) {
    farmEls.hint.hidden = true;
    farmEls.hintBtn.setAttribute('aria-expanded', 'false');
  }
  farmEls.staked.textContent = '';
  farmEls.staked.classList.remove('num-tap');
  if (hasXp) setNumberLabel(farmEls.staked, 'Поставлено всего: ', d.profile.staked);
  farmEls.slots.textContent = `Слоты улучшений: ${d.slots.used} / ${d.slots.total}`;
  renderFarmCard('income', d.income, d.slots);
  renderFarmCard('storage', d.storage, d.slots);
}

function updateFarmButtons() {
  if (!farmData) return;
  renderFarmCard('income', farmData.income, farmData.slots);
  renderFarmCard('storage', farmData.storage, farmData.slots);
}

// reason: 'open' | 'visible' (не чаще раза в 10 секунд) | 'manual' | 'after' (после покупки; не теряется,
// а откладывается). Между любыми двумя запросами не меньше 5 секунд
async function loadFarm(reason) {
  if (farmInFlight || activeTab !== 'farm') return;
  const now = performance.now();
  const sinceLast = now - farmLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason === 'manual' || reason === 'after') {
      clearTimeout(farmTimer);
      farmTimer = setTimeout(() => loadFarm(reason), REQUEST_GAP_MS - sinceLast + 20);
    }
    return;
  }
  if (reason !== 'manual' && reason !== 'after' && sinceLast < REFRESH_MIN_MS) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    showFarmMessage('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(farmTimer);
  farmInFlight = true;
  farmLastRequestAt = now;
  if (!farmHasData) {
    farmEls.skel.hidden = false; // скелетон вместо спиннера
    farmEls.msg.textContent = '';
    farmEls.code.textContent = '';
    farmEls.retry.hidden = true;
  }

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/farm', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      showFarmMessage('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (res.status === 429) {
      showFarmMessage('Слишком много запросов, подождите немного', '429', true);
      return;
    }
    if (!res.ok) {
      showFarmMessage('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    if (!validFarm(d)) {
      showFarmMessage('Нет связи с сервером', 'ответ', true);
      return;
    }
    renderFarm(d);
  } catch (e) {
    showFarmMessage('Нет связи с сервером', e && e.name === 'AbortError' ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    farmInFlight = false;
  }
}

// Один POST покупки. Возвращает { kind: 'ok', data } | { kind: 'fatal', text, refresh } | { kind: 'retry', code }
async function postFarmBuy(id, kind) {
  try {
    const res = await postJson('/api/farm/buy', { request_id: id, kind });
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Ошибка запроса' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      if (body.detail === 'max_level') return { kind: 'fatal', text: 'Максимальный уровень', refresh: true };
      if (body.detail === 'level_locked') {
        const need = isCount(body.required_level) ? ' ' + body.required_level : '';
        return { kind: 'fatal', text: 'Нужен уровень профиля' + need, refresh: true };
      }
      if (body.detail === 'insufficient_funds') return { kind: 'fatal', text: 'Не хватает фишек', refresh: true };
      return { kind: 'fatal', text: 'Не удалось купить улучшение', refresh: true };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось купить улучшение', refresh: true };
    }
    const parsed = await readJsonBody(res);
    const d = parsed.ok ? parsed.data : null;
    const valid = d && typeof d.kind === 'string' && isCount(d.level_after) && isCount(d.cost)
      && isCount(d.balance) && typeof d.replayed === 'boolean';
    // ответ 2xx не повторяем: покупка уже обработана, состояние берём новым GET /api/farm
    return valid ? { kind: 'ok', data: d }
      : { kind: 'fatal', text: 'Ответ сервера не распознан. Данные обновлены', refresh: true };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// Покупка: кнопки блокируются, до 3 попыток с ТЕМ ЖЕ request_id (повтор безопасен, сервер не спишет дважды)
async function buyUpgrade(kind) {
  if (farmBusy || !(tg && tg.initData) || !farmData) return;
  const id = makeRequestId();
  if (!id) {
    setFarmNote('Ошибка', 'lose');
    return;
  }
  farmBusy = true;
  updateFarmButtons();
  setFarmNote('Покупка…');
  let last = { code: 'сеть' };
  let done = null;
  for (let attempt = 0; attempt < ROUND_ATTEMPTS && !done; attempt++) {
    if (attempt > 0) await sleep(ROUND_PAUSES_MS[attempt - 1]);
    const r = await postFarmBuy(id, kind);
    if (r.kind === 'retry') {
      last = r;
      continue;
    }
    done = r;
  }
  farmBusy = false;
  if (done && done.kind === 'ok') {
    setFarmNote(`Куплено: ${FARM_KINDS[done.data.kind] || FARM_KINDS[kind]}, уровень ${done.data.level_after}`, 'win');
  } else if (done) {
    setFarmNote(done.text, 'lose');
  } else if (last.code === '429') {
    setFarmNote('Слишком много запросов, попробуйте чуть позже', 'lose'); // сервер отклонил запрос до обработки
  } else {
    setFarmNote('Нет связи. Покупка могла пройти: проверьте карточки (код: ' + last.code + ')', 'lose');
  }
  updateFarmButtons();
  if (!done || done.kind === 'ok' || done.refresh) {
    loadFarm('after');   // новые карточки и баланс с сервера
    loadServer('after'); // баланс в шапке и таймер до начисления (/api/me)
  }
}

const FARM_XP_HINT = 'Опыт даётся за риск: чем выше шанс потерять всю ставку раунда, тем больше опыта. '
  + 'Ставки на красное и чёрное одновременно почти не дают опыта.';
farmEls.hintBtn.addEventListener('click', () => {
  const open = farmEls.hint.hidden;
  farmEls.hint.textContent = FARM_XP_HINT;
  farmEls.hint.hidden = !open;
  farmEls.hintBtn.setAttribute('aria-expanded', String(open));
});

farmEls.retry.addEventListener('click', () => loadFarm('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadFarm('visible');
});

// #endregion

// #region Мины
// ---------- игра «Мины» ----------
// Состояние игры только с сервера: клиент не знает раскладку мин до конца игры и не пытается её угадать.
// Ответы с mine_cells нигде не сохраняются и не пишутся в консоль. Настройки (ставка, число мин) живут в памяти.
// Каждое действие пользователя получает новый request_id; все повторы действия идут с тем же (postJson).
const MINES_BET_MAX = 1000000000;
const MINES_COUNT_MIN = 1;
const MINES_COUNT_MAX = 24;
const MINES_CELLS = 25;
const MINES_ICON_SETS = { default: { gem: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12l4 6-10 13L2 9z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/></svg>', mine: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="6"/><path d="M12 3v3M12 20v3M3 13h3M18 13h3M5.6 6.6l2.1 2.1M16.3 17.3l2.1 2.1M18.4 6.6l-2.1 2.1M7.7 17.3l-2.1 2.1"/></svg>' } };
let minesIcons = MINES_ICON_SETS.default;   // набор иконок зависит от скина «значки мин» (applySkins)

const minesEls = {
  balance: document.getElementById('mines-balance'),
  switchBtn: document.getElementById('mines-switch'),
  inplay: document.getElementById('mines-inplay'),
  meta: document.getElementById('mines-meta'),
  mults: document.getElementById('mines-mults'),
  arrow: document.getElementById('mines-arrow'),
  nextMult: document.getElementById('mines-next-mult'),
  nextPay: document.getElementById('mines-next-pay'),
  nextBox: document.getElementById('mines-next'),
  notice: document.getElementById('mines-notice'),
  skel: document.getElementById('mines-skel'),
  msg: document.getElementById('mines-msg'),
  code: document.getElementById('mines-code'),
  retry: document.getElementById('mines-retry'),
  form: document.getElementById('mines-start'),
  bet: document.getElementById('mines-bet'),
  maxBtn: document.getElementById('mines-max'),
  minus: document.getElementById('mines-minus'),
  plus: document.getElementById('mines-plus'),
  count: document.getElementById('mines-count'),
  begin: document.getElementById('mines-begin'),
  boardWrap: document.getElementById('mines-board-wrap'),
  grid: document.getElementById('mines-grid'),
  panel: document.getElementById('mines-panel'),
  mult: document.getElementById('mines-mult'),
  left: document.getElementById('mines-left'),
  cash: document.getElementById('mines-cash'),
  expiry: document.getElementById('mines-expiry'),
  result: document.getElementById('mines-result'),
  resultTitle: document.getElementById('mines-result-title'),
  resultDetail: document.getElementById('mines-result-detail'),
  again: document.getElementById('mines-again'),
  last: document.getElementById('mines-last')
};

const mn = {
  view: 'loading',      // 'loading' | 'start' | 'play' | 'result'
  loaded: false,
  error: false,
  game: null,
  last: null,
  balance: null,
  busy: false,          // идёт действие: поле и кнопки заблокированы
  pending: null,        // клетка, открываемая сейчас (для подсказки на поле)
  hit: null,            // клетка, на которой сработала мина (только в этой сессии)
  showLast: true,       // показывать блок прошлой игры под формой
  settings: { bet: 10, mines: 3 },
  seen: new Set(),      // завершённые игры, о которых пользователь уже знает (в этой сессии)
  inFlight: false,
  lastRequestAt: -Infinity,
  timer: null
};
registerGame({ id: 'mines', state: mn, render: renderMines, busy: () => mn.busy });

const minesCells = [];
for (let i = 0; i < MINES_CELLS; i++) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'mines-cell';
  btn.setAttribute('aria-label', 'Клетка ' + (i + 1));
  btn.addEventListener('click', () => minesReveal(i));
  minesEls.grid.appendChild(btn);
  minesCells.push(btn);
}

// число одним текстом: сокращение от миллиона, полное значение в title и по нажатию (не в кнопках)
function setNumberLabel(el, prefix, n, suffix = '') {
  const full = prefix + formatNumber(n) + suffix;
  const short = prefix + formatCompact(n) + suffix;
  el.textContent = short;
  el.title = full;
  el.dataset.short = short;
  el.dataset.full = full;
  el.classList.toggle('num-tap', short !== full);
}

// то же для кнопок: без нажатия-раскрытия, чтобы случайно не нажать «Забрать»
function setButtonLabel(el, prefix, n) {
  el.textContent = prefix + formatCompact(n);
  el.title = prefix + formatNumber(n);
}

const minesKey = (last) => last.status + ':' + last.finished_at;

function validMinesGame(g) {
  return !!g && typeof g === 'object' && isCount(g.bet) && isCount(g.mines) && Array.isArray(g.revealed)
    && g.revealed.every((c) => isCount(c) && c < MINES_CELLS) && isCount(g.safe_left)
    && typeof g.multiplier === 'string' && isCount(g.payout_now)
    && (g.next_multiplier === null || typeof g.next_multiplier === 'string')
    && (g.next_payout === null || isCount(g.next_payout)) && isCount(g.expires_at);
}

function validMinesLast(l) {
  return !!l && typeof l === 'object' && typeof l.status === 'string' && isCount(l.bet) && isCount(l.mines)
    && Array.isArray(l.revealed) && Array.isArray(l.mine_cells) && l.mine_cells.every((c) => isCount(c) && c < MINES_CELLS)
    && isCount(l.payout) && (l.finished_at === null || isCount(l.finished_at));
}

const validMinesState = (d) => !!d && typeof d === 'object' && isCount(d.balance)
  && (d.game === null || validMinesGame(d.game)) && (d.last === null || validMinesLast(d.last));

// #endregion

// #region Мины: отрисовка
// ---------- отрисовка ----------
function setMinesMessage(text, code, canRetry) {
  minesEls.msg.textContent = text;
  minesEls.code.textContent = code ? 'код: ' + code : '';
  minesEls.retry.hidden = !canRetry;
}

function setMinesNotice(text) {
  minesEls.notice.textContent = text;
}

function renderMinesBoard(revealed, mineCells, muted, hit, interactive) {
  const open = new Set(revealed);
  const bombs = new Set(mineCells);
  minesEls.boardWrap.classList.toggle('locked', mn.busy);
  minesCells.forEach((btn, i) => {
    let cls = 'mines-cell';
    let html = '';
    if (open.has(i)) {
      cls += ' safe';
      html = minesIcons.gem;
    } else if (bombs.has(i)) {
      cls += ' mine' + (muted ? ' muted' : '') + (i === hit ? ' hit' : '');
      html = minesIcons.mine;
    }
    if (mn.busy && i === mn.pending && !open.has(i) && !bombs.has(i)) cls += ' opening';
    btn.className = cls;
    btn.innerHTML = html; // постоянная разметка значков, данных сервера в ней нет
    btn.disabled = !interactive || mn.busy || open.has(i) || bombs.has(i);
  });
}

function renderMinesPlay() {
  const g = mn.game;
  renderMinesBoard(g.revealed, [], false, null, true);
  // множители строками с сервера как есть: текущий крупно, справа через стрелку множитель следующей безопасной клетки
  const now = '×' + g.multiplier;
  const changed = minesEls.mult.dataset.ready === '1' && minesEls.mult.textContent !== now;
  minesEls.mult.textContent = now;
  minesEls.mult.dataset.ready = '1';
  const hasNext = g.next_multiplier !== null && g.next_payout !== null;
  minesEls.arrow.toggleAttribute('hidden', !hasNext);
  minesEls.nextBox.toggleAttribute('hidden', !hasNext);
  if (hasNext) {
    minesEls.nextMult.textContent = '×' + g.next_multiplier;
    setNumberLabel(minesEls.nextPay, 'выплата ', g.next_payout);
  }
  if (changed) {                       // короткий пульс при смене множителя (при prefers-reduced-motion анимации нет, см. CSS)
    minesEls.mult.classList.remove('pulse');
    void minesEls.mult.offsetWidth;
    minesEls.mult.classList.add('pulse');
  }
  minesEls.left.textContent = 'Безопасных клеток: ' + g.safe_left;
  if (g.revealed.length === 0) {
    minesEls.cash.textContent = 'Вернуть ставку';
    minesEls.cash.title = '';
  } else {
    setButtonLabel(minesEls.cash, 'Забрать ', g.payout_now);
  }
  minesEls.cash.disabled = mn.busy;
  const hours = Math.max(1, Math.ceil((g.expires_at - Date.now() / 1000) / 3600));
  minesEls.expiry.textContent = `Игра закроется автоматически через ${hours} ч без действий`;
}

function renderMinesResult() {
  const l = mn.last;
  const lost = l.status === 'lost';
  renderMinesBoard(l.revealed, l.mine_cells, !lost, mn.hit, false);
  const refundedGame = l.status === 'refunded' || l.status === 'auto_refunded';
  minesEls.result.classList.toggle('win', !lost && !refundedGame);
  minesEls.result.classList.toggle('lose', lost);
  minesEls.resultDetail.textContent = '';
  if (lost) {
    setNumberLabel(minesEls.resultTitle, 'Мина! Потеряно ', l.bet);
  } else if (refundedGame) {
    minesEls.resultTitle.textContent = 'Ставка возвращена';
    minesEls.resultTitle.classList.remove('num-tap');
    minesEls.resultTitle.title = '';
  } else {
    setNumberLabel(minesEls.resultTitle, 'Выигрыш ', l.payout);
    const profit = l.payout - l.bet;
    if (profit > 0) setNumberLabel(minesEls.resultDetail, 'Чистая прибыль: ', profit);
    else minesEls.resultDetail.textContent = 'Чистая прибыль: 0';
  }
  minesEls.again.disabled = mn.busy;
}

function renderMinesForm() {
  minesEls.bet.value = String(mn.settings.bet);
  minesEls.count.textContent = String(mn.settings.mines);
  minesEls.form.querySelectorAll('[data-bet]').forEach((b) => {
    b.setAttribute('aria-pressed', String(Number(b.dataset.bet) === mn.settings.bet));
  });
  minesEls.form.querySelectorAll('[data-mines]').forEach((b) => {
    b.setAttribute('aria-pressed', String(Number(b.dataset.mines) === mn.settings.mines));
  });
  minesEls.form.querySelectorAll('button, input').forEach((el) => { el.disabled = mn.busy; });
  minesEls.last.hidden = true;
  const l = mn.last;
  if (mn.showLast && l) {
    minesEls.last.hidden = false;
    if (l.status === 'lost') setNumberLabel(minesEls.last, 'Прошлая игра: мина, потеряно ', l.bet);
    else if (l.status === 'refunded' || l.status === 'auto_refunded') minesEls.last.textContent = 'Прошлая игра: ставка возвращена';
    else setNumberLabel(minesEls.last, 'Прошлая игра: выигрыш ', l.payout);
  }
}

// Фишки ставки в минах по серверному балансу (те же номиналы, что в рулетке)
const minesChipBar = makeChipBar({
  root: minesEls.form,
  attr: 'bet',
  getBalance: () => (mn.balance === null ? 0 : mn.balance),
  getBet: () => mn.settings.bet,
  setBet: (v) => { mn.settings.bet = v; }
});
const renderMinesChips = minesChipBar.render;

function renderMines() {
  const loading = mn.view === 'loading';
  minesEls.skel.hidden = !(loading && !mn.error);
  minesEls.form.hidden = mn.view !== 'start';
  minesEls.boardWrap.hidden = !(mn.view === 'play' || mn.view === 'result');
  minesEls.panel.hidden = mn.view !== 'play';
  minesEls.mults.hidden = mn.view !== 'play';
  minesEls.result.hidden = mn.view !== 'result';
  minesEls.last.hidden = true;
  minesEls.switchBtn.disabled = mn.busy;
  if (mn.balance !== null) {
    minesEls.balance.classList.remove('skeleton');
    minesEls.balance.textContent = spacedNumber(mn.balance);
    fitNumberFont(minesEls.balance, minesEls.balance.textContent.length);
  }
  renderMinesChips();
  refreshBetPanels();
  const inGame = mn.game !== null && mn.view === 'play';
  minesEls.meta.hidden = !inGame;
  if (inGame) setNumberLabel(minesEls.inplay, 'В игре: ', mn.game.bet);
  if (mn.view === 'play') renderMinesPlay();
  else if (mn.view === 'result') renderMinesResult();
  else if (mn.view === 'start') renderMinesForm();
  fitMinesBoard();
}

// Поле занимает всё свободное место между блоками экрана и остаётся квадратным: сторона = меньшее из ширины и высоты доступной области
// (высота зависит от видимой области: шапка Telegram, нижняя навигация, динамическая высота экрана).
function fitMinesBoard() {
  const wrap = minesEls.boardWrap;
  if (wrap.hidden) return;
  const size = Math.floor(Math.min(wrap.clientWidth, wrap.clientHeight));
  const px = size > 0 ? size + 'px' : '';
  minesEls.grid.style.width = px;
  minesEls.grid.style.height = px;
}
if (window.ResizeObserver) new ResizeObserver(fitMinesBoard).observe(minesEls.boardWrap);
window.addEventListener('resize', fitMinesBoard);

// Применяет состояние с сервера: определяет, что показывать
function applyMinesState(d, announce) {
  mn.loaded = true;
  mn.error = false;
  mn.balance = d.balance;
  mn.game = d.game;
  mn.last = d.last;
  setMinesMessage('', '', false);
  if (d.game !== null) {
    mn.view = 'play';
  } else if (mn.view === 'result' && d.last !== null) {
    // итог только что закончившейся игры остаётся, пока игрок не нажмёт «Играть снова»
  } else {
    mn.view = 'start';
    mn.showLast = true;
  }
  // автоматически закрытая игра: сообщаем один раз за сессию
  const l = d.last;
  if (announce && d.game === null && l && (l.status === 'auto_cashed' || l.status === 'auto_refunded') && !mn.seen.has(minesKey(l))) {
    setMinesNotice(l.status === 'auto_cashed'
      ? 'Игра закрылась автоматически: выплата ' + formatCompact(l.payout)
      : 'Игра закрылась автоматически: ставка возвращена');
  }
  if (l) mn.seen.add(minesKey(l));
  renderMines();
}

// #endregion

// #region Мины: запросы
// ---------- запросы ----------
async function fetchMinesState() {
  return fetchGameState('/api/mines/state', validMinesState);
}

async function loadMines(reason) {
  return loadGameState(mn, {
    id: 'mines',
    fetchState: fetchMinesState,
    applyState: (d) => applyMinesState(d, true),
    setMessage: setMinesMessage,
    setNotice: setMinesNotice,
    render: renderMines
  }, reason);
}

const validMinesStart = (d) => !!d && validMinesGame(d.game) && isCount(d.balance);
// при result "safe" ключа last в ответе сервера нет (он есть только у закрытой игры), при "mine" и "cleared" game равен null
const validMinesReveal = (d) => !!d && ['safe', 'mine', 'cleared'].includes(d.result) && isCount(d.balance)
  && (d.result === 'safe' ? validMinesGame(d.game) : validMinesLast(d.last) && (d.game === null || d.game === undefined));
const validMinesCashout = (d) => !!d && validMinesLast(d.last) && isCount(d.balance);

// Действие пользователя: блокировка, до 3 попыток с одним request_id, разбор ответа.
// Если после повторов результата нет, ничего не угадываем: запрашиваем реальное состояние
async function minesAct(path, body, validate, onOk) {
  if (mn.busy) return;
  if (!(tg && tg.initData)) {
    mn.pending = null;
    setMinesNotice('Откройте игру через бота в Telegram');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    mn.pending = null;
    setMinesNotice('Ошибка');
    return;
  }
  mn.busy = true;
  setMinesNotice('');
  renderMines();
  const result = await postWithRetries(() => postMinesOnce(path, { request_id: id, ...body }, validate));
  let note = 'Состояние обновлено';
  if (result && result.kind === 'ok') {
    mn.balance = result.data.balance;
    try {
      onOk(result.data);
      mn.busy = false;
      mn.pending = null;
      renderMines();
      return;
    } catch (e) {
      // ошибка нашего кода после успешного ответа: POST не повторяем, один раз берём состояние с сервера
      note = 'Не удалось показать результат. Состояние обновлено';
    }
  }
  // дальше состояние известно только серверу
  let reload = true;
  if (!(result && result.kind === 'ok')) {
    ({ note, reload } = actionFailure(result, {
      insufficient_funds: 'Не хватает фишек',
      active_game_exists: 'У вас уже есть начатая игра'
    }));
  }
  if (reload) {
    try {
      applyMinesState(await fetchMinesState(), false);
      setMinesNotice(note);
    } catch (e) {
      setMinesNotice('Нет связи. Состояние игры неизвестно, обновите экран');
    }
  } else {
    setMinesNotice(note);
  }
  mn.busy = false;
  mn.pending = null;
  renderMines();
  loadServer('after');
}

// #endregion

// #region Мины: действия
// ---------- действия ----------
function minesStart() {
  const bet = Number(minesEls.bet.value);
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > MINES_BET_MAX) {
    setMinesNotice('Введите целую ставку от 1 до ' + formatNumber(MINES_BET_MAX));
    return;
  }
  if (mn.balance !== null && bet > mn.balance) {
    setMinesNotice('Не хватает фишек');
    return;
  }
  mn.settings.bet = bet;
  minesAct('/api/mines/start', { bet, mines: mn.settings.mines }, validMinesStart, (d) => {
    mn.game = d.game;
    mn.view = 'play';
    mn.hit = null;
    mn.showLast = false;
    haptic('light');
  });
}

function minesReveal(cell) {
  if (mn.view !== 'play' || mn.busy) return;
  mn.pending = cell; // нажатая клетка показывает «открывается», пока идёт запрос
  minesAct('/api/mines/reveal', { cell }, validMinesReveal, (d) => {
    if (d.result === 'safe') {
      mn.game = d.game;
      haptic('light');
      return;
    }
    mn.game = null;
    mn.last = d.last;
    mn.seen.add(minesKey(d.last));
    mn.view = 'result';
    mn.hit = d.result === 'mine' ? cell : null;
    haptic(d.result === 'mine' ? 'error' : 'success');
    loadServer('after');
  });
}

function minesCashout() {
  if (mn.view !== 'play' || mn.busy) return;
  minesAct('/api/mines/cashout', {}, validMinesCashout, (d) => {
    mn.game = null;
    mn.last = d.last;
    mn.seen.add(minesKey(d.last));
    mn.view = 'result';
    mn.hit = null;
    haptic('success');
    loadServer('after');
  });
}

function setMinesCount(n) {
  mn.settings.mines = Math.min(MINES_COUNT_MAX, Math.max(MINES_COUNT_MIN, n));
  renderMinesForm();
}

minesEls.begin.addEventListener('click', minesStart);
minesEls.cash.addEventListener('click', minesCashout);
minesEls.again.addEventListener('click', () => {
  mn.view = 'start';
  mn.showLast = false;
  mn.hit = null;
  setMinesNotice('');
  renderMines();
});
minesEls.minus.addEventListener('click', () => setMinesCount(mn.settings.mines - 1));
minesEls.plus.addEventListener('click', () => setMinesCount(mn.settings.mines + 1));
minesEls.form.querySelectorAll('[data-bet]').forEach((b) => b.addEventListener('click', () => {
  mn.settings.bet = Number(b.dataset.bet);
  renderMinesForm();
  haptic('light');
}));
minesEls.form.querySelectorAll('[data-mines]').forEach((b) => b.addEventListener('click', () => {
  setMinesCount(Number(b.dataset.mines));
  haptic('light');
}));
setupBetPanel({
  input: minesEls.bet,
  maxBtn: minesEls.maxBtn,
  halfBtn: document.getElementById('mines-half'),
  doubleBtn: document.getElementById('mines-double'),
  getLimit: minesBetLimit
});
minesEls.bet.addEventListener('input', () => {
  const v = Number(minesEls.bet.value);
  if (Number.isSafeInteger(v) && v >= 1) {
    mn.settings.bet = v;
    minesEls.form.querySelectorAll('[data-bet]').forEach((b) => {
      b.setAttribute('aria-pressed', String(Number(b.dataset.bet) === v));
    });
  }
});
minesEls.retry.addEventListener('click', () => loadMines('manual'));
minesEls.switchBtn.addEventListener('click', toggleGameMenu);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadMines('visible');
});

// #endregion

// #region Кено
// ---------- игра «Кено» ----------
// Один раунд за запрос: клиент шлёт ставку и числа, сервер вытягивает 10 чисел и считает выплату. Баланс только серверный
// (общий srv, как у рулетки) и в шапке кено обновляется после анимации розыгрыша. Выбор чисел и ставка живут только в памяти.
// Таблица выплат берётся с сервера (GET /api/keno/paytable) и кэшируется в памяти.
const KENO_BET_MAX = 1000000000;
const KENO_FIELD = 40;
const KENO_MAX_PICKS = 10;
const KENO_STEP_MS = 250;           // пауза между вытянутыми числами (10 чисел ≈ 2,5 с)
const KENO_PAY_RETRY_MS = 10000;    // таблицу выплат при неудаче повторно не чаще, чем раз в 10 секунд

const kenoEls = {
  balance: document.getElementById('keno-balance'),
  switchBtn: document.getElementById('keno-switch'),
  notice: document.getElementById('keno-notice'),
  board: document.getElementById('keno-board'),
  count: document.getElementById('keno-count'),
  auto: document.getElementById('keno-auto'),
  clear: document.getElementById('keno-clear'),
  result: document.getElementById('keno-result'),
  resultTitle: document.getElementById('keno-result-title'),
  resultDetail: document.getElementById('keno-result-detail'),
  payList: document.getElementById('keno-pay-list'),
  payNote: document.getElementById('keno-pay-note'),
  panel: document.getElementById('keno-bets'),
  bet: document.getElementById('keno-bet'),
  maxBtn: document.getElementById('keno-max'),
  play: document.getElementById('keno-play')
};

const kn = {
  picks: new Set(),     // выбранные числа
  busy: false,          // идёт раунд (запрос и анимация): поле и кнопки заблокированы
  revealed: new Set(),  // вытянутые числа, уже показанные на поле
  final: false,         // анимация закончилась, показан итог
  result: null,         // { hits: [..], picked, payout, bet } последнего раунда
  shownBalance: null,   // баланс в шапке на время раунда (обновляется после анимации)
  pay: null,            // таблица выплат { "k": { "h": "X.YY" } }
  payLoading: false,
  payFailedAt: -Infinity,
  noticeTimer: null
};
registerGame({ id: 'keno', busy: () => kn.busy });

const kenoBalls = [];
for (let n = 1; n <= KENO_FIELD; n++) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'keno-ball';
  btn.setAttribute('aria-label', 'Число ' + n);
  btn.setAttribute('aria-pressed', 'false');
  btn.innerHTML = '<span></span>';
  btn.firstChild.textContent = String(n);
  btn.addEventListener('click', () => toggleKenoPick(n));
  kenoEls.board.appendChild(btn);
  kenoBalls.push(btn);
}

const kenoBetLimit = () => Math.max(1, Math.min(KENO_BET_MAX, srv.loaded ? srv.balance : KENO_BET_MAX));

function setKenoNotice(text) {
  clearTimeout(kn.noticeTimer);
  kenoEls.notice.textContent = text;
  if (text) kn.noticeTimer = setTimeout(() => { kenoEls.notice.textContent = ''; }, 3500);
}

// Новый выбор или правка выбора сбрасывает показ прошлого итога
function resetKenoResult() {
  kn.result = null;
  kn.final = false;
  kn.revealed = new Set();
}

function toggleKenoPick(n) {
  if (kn.busy) return;
  resetKenoResult();
  if (kn.picks.has(n)) {
    kn.picks.delete(n);
  } else if (kn.picks.size >= KENO_MAX_PICKS) {
    setKenoNotice('Можно выбрать не больше ' + KENO_MAX_PICKS + ' чисел');
    haptic('error');
  } else {
    kn.picks.add(n);
    haptic('light');
  }
  renderKeno();
  loadKenoPay();
}

function kenoAuto() {
  if (kn.busy) return;
  resetKenoResult();
  const all = Array.from({ length: KENO_FIELD }, (_, i) => i + 1);
  for (let i = all.length - 1; i > 0; i--) { // перемешивание Фишера-Йетса
    const j = Math.floor(Math.random() * (i + 1));
    [all[i], all[j]] = [all[j], all[i]];
  }
  kn.picks = new Set(all.slice(0, KENO_MAX_PICKS));
  haptic('light');
  renderKeno();
  loadKenoPay();
}

function kenoClear() {
  if (kn.busy) return;
  resetKenoResult();
  kn.picks = new Set();
  renderKeno();
}

// Фишки ставки по серверному балансу (те же номиналы, что в рулетке и минах)
const kenoChipBar = makeChipBar({
  root: kenoEls.panel,
  attr: 'kbet',
  getBalance: () => (srv.loaded ? srv.balance : 0),
  getBet: () => Number(kenoEls.bet.value),
  setBet: (v) => { kenoEls.bet.value = String(v); }
});
const renderKenoChips = kenoChipBar.render;
const syncKenoChips = kenoChipBar.sync;

// одна строка текста: короткая запись на экране, полное число в подсказке
function setKenoLine(el, shortText, fullText) {
  el.textContent = shortText;
  el.title = fullText;
}

function renderKenoPay() {
  const k = kn.picks.size;
  kenoEls.payList.textContent = '';
  kenoEls.payNote.textContent = '';
  if (k === 0) {
    kenoEls.payNote.textContent = 'Выберите числа, чтобы увидеть выплаты';
    return;
  }
  const row = kn.pay && kn.pay[String(k)];
  if (!row) {
    kenoEls.payNote.textContent = kn.payLoading ? 'Загрузка таблицы выплат…' : 'Таблица выплат недоступна';
    return;
  }
  const hitCount = kn.final && kn.result ? kn.result.hits.length : -1;
  Object.keys(row).map(Number).sort((a, b) => a - b).forEach((h) => {
    const li = document.createElement('li');
    li.dataset.h = String(h);
    if (h === hitCount) li.className = 'won';
    li.innerHTML = '<span></span><b></b>';
    li.firstChild.textContent = String(h);
    li.setAttribute('aria-label', 'совпало ' + h + ' из ' + k + ', множитель ' + row[String(h)]);
    li.lastChild.textContent = '×' + row[String(h)];
    kenoEls.payList.appendChild(li);
  });
  kenoEls.payNote.textContent = 'Остальные исходы: 0';
}

function renderKenoResult() {
  const r = kn.final ? kn.result : null;
  kenoEls.result.classList.toggle('win', !!r && r.payout > 0);
  if (!r) {
    kenoEls.resultTitle.textContent = '';
    kenoEls.resultDetail.textContent = '';
    kenoEls.resultTitle.title = kenoEls.resultDetail.title = '';
    return;
  }
  kenoEls.resultTitle.textContent = 'Совпало ' + r.hits.length + ' из ' + r.picked;
  if (r.payout > 0) {
    const profit = r.payout - r.bet;
    setKenoLine(kenoEls.resultDetail,
      'Выигрыш ' + formatCompact(r.payout) + ', чистая прибыль ' + formatCompact(profit),
      'Выигрыш ' + formatNumber(r.payout) + ', чистая прибыль ' + formatNumber(profit));
  } else {
    setKenoLine(kenoEls.resultDetail, 'Не повезло', 'Не повезло');
  }
}

function renderKeno() {
  const bal = kn.busy ? kn.shownBalance : (srv.loaded ? srv.balance : null);
  if (bal !== null && bal !== undefined) {
    kenoEls.balance.classList.remove('skeleton');
    kenoEls.balance.textContent = spacedNumber(bal);
  } else {
    kenoEls.balance.textContent = srv.error ? '—' : 'Загрузка…';
  }
  fitNumberFont(kenoEls.balance, kenoEls.balance.textContent.length);
  renderKenoChips();
  syncKenoChips();
  refreshBetPanels();
  kenoBalls.forEach((btn, i) => {
    const n = i + 1;
    const sel = kn.picks.has(n);
    const drawn = kn.revealed.has(n);
    btn.classList.toggle('sel', sel);
    btn.classList.toggle('drawn', drawn);
    btn.classList.toggle('hit', sel && drawn);
    btn.classList.toggle('miss', kn.final && sel && !drawn);
    btn.setAttribute('aria-pressed', String(sel));
    btn.disabled = kn.busy;
  });
  kenoEls.board.classList.toggle('locked', kn.busy);
  kenoEls.count.textContent = 'Выбрано ' + kn.picks.size + ' из ' + KENO_MAX_PICKS;
  renderKenoPay();
  renderKenoResult();
  const ready = !!(tg && tg.initData) && srv.loaded;
  kenoEls.panel.querySelectorAll('button, input').forEach((el) => { el.disabled = kn.busy || !ready; });
  kenoEls.auto.disabled = kn.busy;
  kenoEls.clear.disabled = kn.busy || kn.picks.size === 0;
  kenoEls.play.disabled = kn.busy || !ready;
  kenoEls.play.textContent = kn.busy ? 'Играем…' : (kn.final ? 'Играть снова' : 'Играть');
  kenoEls.switchBtn.disabled = kn.busy;
  refreshBetPanels();
}
kenoRender = renderKeno;

// #endregion

// #region Кено: таблица выплат
// ---------- таблица выплат ----------
const validKenoPay = (d) => {
  if (!d || typeof d.paytable !== 'object' || d.paytable === null) return false;
  return Object.keys(d.paytable).every((k) => {
    const row = d.paytable[k];
    return /^\d+$/.test(k) && row && typeof row === 'object'
      && Object.keys(row).every((h) => /^\d+$/.test(h) && typeof row[h] === 'string' && /^\d+\.\d\d$/.test(row[h]));
  });
};

async function loadKenoPay() {
  if (kn.pay || kn.payLoading || !(tg && tg.initData)) return;
  if (performance.now() - kn.payFailedAt < KENO_PAY_RETRY_MS) return;
  kn.payLoading = true;
  renderKenoPay();
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/keno/paytable', {
      method: 'GET',
      headers: { Authorization: 'tma ' + tg.initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (!res.ok) throw new Error('status');
    const d = await res.json();
    if (!validKenoPay(d)) throw new Error('shape');
    kn.pay = d.paytable;
  } catch (e) {
    kn.payFailedAt = performance.now();
  } finally {
    clearTimeout(timeout);
    kn.payLoading = false;
    renderKenoPay();
  }
}

// #endregion

// #region Кено: раунд
// ---------- раунд ----------
// Ответ проверяется по настоящему контракту (docs/API.md): нужны только draw, hits, payout, balance
function validKenoRound(d, picks) {
  return !!d && Array.isArray(d.draw) && d.draw.length === 10
    && d.draw.every((n, i) => isInt(n) && n >= 1 && n <= KENO_FIELD && d.draw.indexOf(n) === i)
    && Array.isArray(d.hits) && d.hits.every((n) => isInt(n) && d.draw.includes(n) && picks.includes(n))
    && isCount(d.payout) && isCount(d.balance);
}

// Один POST. { kind: 'ok', data } | { kind: 'conflict', detail } | { kind: 'fatal', text } | { kind: 'retry', code } | { kind: 'invalid' }
async function postKenoOnce(payload, picks) {
  try {
    const res = await postJson('/api/keno/play', payload);
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
        : { kind: 'fatal', text: 'Не удалось выполнить раунд' };
    }
    const body = await readJsonBody(res);
    // ответ 2xx не повторяем: раунд уже обработан сервером, непонятный ответ = повод обновить баланс отдельным запросом
    return body.ok && validKenoRound(body.data, picks) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// Вытянутые числа загораются по одному (без движения: сразу все)
async function animateKenoDraw(draw, picks) {
  if (reducedMotion()) {
    kn.revealed = new Set(draw);
    renderKeno();
    return;
  }
  for (const n of draw) {
    await sleep(KENO_STEP_MS);
    kn.revealed.add(n);
    renderKeno();
    const ball = kenoBalls[n - 1];
    ball.classList.remove('pop');
    void ball.offsetWidth; // перезапуск CSS-анимации
    ball.classList.add('pop');
    if (picks.includes(n)) haptic('light');
  }
  await sleep(KENO_STEP_MS);
}

async function kenoPlay() {
  if (kn.busy) return;
  const picks = [...kn.picks].sort((a, b) => a - b);
  const bet = Number(kenoEls.bet.value);
  if (picks.length < 1) {
    setKenoNotice('Выберите от 1 до ' + KENO_MAX_PICKS + ' чисел');
    return;
  }
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > KENO_BET_MAX) {
    setKenoNotice('Введите целую ставку от 1 до ' + formatNumber(KENO_BET_MAX));
    return;
  }
  if (!srv.loaded || bet > srv.balance) {
    setKenoNotice('Не хватает фишек');
    return;
  }
  if (!(tg && tg.initData)) {
    setKenoNotice('Откройте игру через бота в Telegram');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    setKenoNotice('Ошибка');
    return;
  }
  setKenoNotice('');
  resetKenoResult();
  kn.busy = true;
  kn.shownBalance = srv.balance;
  renderKeno();
  const result = await postWithRetries(() => postKenoOnce({ request_id: id, bet, picks }, picks));
  let note = 'Баланс обновлён';
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      await animateKenoDraw(d.draw, picks);
      kn.result = { hits: d.hits, picked: picks.length, payout: d.payout, bet };
      kn.final = true;
      srv.balance = d.balance;      // баланс на экране меняется только после анимации
      srv.loaded = true;
      srv.error = null;
      kn.busy = false;
      renderBalance();
      renderKeno();
      haptic(d.payout > 0 ? 'success' : 'error');
      loadServer('after');
      return;
    } catch (e) {
      // ошибка нашего кода после успешного ответа: POST не повторяем, баланс берём отдельным запросом
      note = 'Не удалось показать результат. Баланс обновлён';
    }
  } else if (result && result.kind === 'invalid') {
    note = 'Ответ сервера не распознан. Баланс обновлён';
  } else if (result && result.kind === 'fatal') {
    note = result.text;
  } else if (result && result.kind === 'conflict') {
    if (result.detail === 'insufficient_funds') note = 'Не хватает фишек';
    else if (result.detail === 'balance_limit') note = 'Достигнут максимальный баланс';
    else note = 'Не удалось выполнить раунд';
  } else {
    note = 'Нет связи. Баланс обновлён';
  }
  resetKenoResult();
  kn.busy = false;
  setKenoNotice(note);
  renderKeno();
  loadServer('after');
}

setupBetPanel({
  input: kenoEls.bet,
  maxBtn: kenoEls.maxBtn,
  halfBtn: document.getElementById('keno-half'),
  doubleBtn: document.getElementById('keno-double'),
  getLimit: kenoBetLimit
});
kenoEls.bet.addEventListener('input', syncKenoChips);
kenoEls.panel.querySelectorAll('[data-kbet]').forEach((b) => b.addEventListener('click', () => {
  kenoEls.bet.value = b.dataset.kbet;
  syncKenoChips();
  refreshBetPanels();
  haptic('light');
}));
kenoEls.auto.addEventListener('click', kenoAuto);
kenoEls.clear.addEventListener('click', kenoClear);
kenoEls.play.addEventListener('click', kenoPlay);
kenoEls.switchBtn.addEventListener('click', toggleGameMenu);
renderKeno();

// #endregion

// #region Блэкджек
// ---------- игра «Блэкджек» ----------
// Состояние раздачи только с сервера: колоды и скрытой карты дилера клиент не знает (в ответе у неё null).
// Каждое действие получает новый request_id; повторы (сеть, таймаут, 429, 5xx) идут с тем же (postJson, postMinesOnce).
// Баланс в шапке обновляется только после анимации раздачи. Ставка живёт в памяти.
const BJ_BET_MAX = 1000000000;
const BJ_DEAL_MS = 220;        // пауза между раздаваемыми картами
const BJ_DEALER_MS = 400;      // дилер открывает карту и добирает по одной
const BJ_CARD_RE = /^(A|10|[2-9]|J|Q|K)[SHDC]$/;
const BJ_RESULTS = ['win', 'push', 'lose', 'bust', 'dealer_bust', 'blackjack'];
const BJ_ACTIONS = ['hit', 'stand', 'double'];

const bjEls = {
  balance: document.getElementById('bj-balance'),
  switchBtn: document.getElementById('bj-switch'),
  notice: document.getElementById('bj-notice'),
  skel: document.getElementById('bj-skel'),
  msg: document.getElementById('bj-msg'),
  code: document.getElementById('bj-code'),
  retry: document.getElementById('bj-retry'),
  table: document.getElementById('bj-table'),
  dealerCards: document.getElementById('bj-dealer-cards'),
  playerCards: document.getElementById('bj-player-cards'),
  dealerTotal: document.getElementById('bj-dealer-total'),
  playerTotal: document.getElementById('bj-player-total'),
  banner: document.getElementById('bj-banner'),
  bannerTitle: document.getElementById('bj-banner-title'),
  bannerDetail: document.getElementById('bj-banner-detail'),
  bets: document.getElementById('bj-bets'),
  bet: document.getElementById('bj-bet'),
  maxBtn: document.getElementById('bj-max'),
  deal: document.getElementById('bj-deal'),
  actions: document.getElementById('bj-actions'),
  stake: document.getElementById('bj-stake'),
  hit: document.getElementById('bj-hit'),
  stand: document.getElementById('bj-stand'),
  double: document.getElementById('bj-double')
};

const bj = {
  view: 'loading',      // 'loading' | 'start' | 'play' | 'result'
  loaded: false,
  error: false,
  game: null,           // последний ответ сервера (активная или завершённая раздача)
  balance: null,
  busy: false,          // идёт действие или анимация: кнопки заблокированы
  animating: false,     // карты раздаются: очки не показываются
  shown: { player: [], dealer: [] },   // карты, уже показанные на столе (dealer: null = закрытая карта)
  seen: new Set(),      // завершённые раздачи, о которых игрок уже знает (в этой сессии)
  inFlight: false,
  lastRequestAt: -Infinity,
  timer: null
};
registerGame({ id: 'blackjack', state: bj, render: renderBj, busy: () => bj.busy || bj.animating });

const bjBetLimit = () => Math.max(1, Math.min(BJ_BET_MAX, bj.balance === null ? BJ_BET_MAX : bj.balance));

function setBjNotice(text) {
  bjEls.notice.textContent = text;
}

function setBjMessage(text, code, retry) {
  bjEls.msg.textContent = text;
  bjEls.code.textContent = code ? 'код: ' + code : '';
  bjEls.retry.hidden = !retry;
}

// #endregion

// #region Блэкджек: проверка ответа сервера
// ---------- проверка ответа сервера (по реальному контракту, docs/API.md) ----------
const validBjCards = (cards, hiddenOk) => Array.isArray(cards) && cards.every((c) => (c === null ? hiddenOk : (typeof c === 'string' && BJ_CARD_RE.test(c))));
const validBjHand = (h, hiddenOk) => !!h && validBjCards(h.cards, hiddenOk) && h.cards.length >= 2 && isCount(h.total);

function validBjState(d, allowNone) {
  if (!d || !isCount(d.balance) || !Array.isArray(d.actions) || !d.actions.every((a) => BJ_ACTIONS.includes(a))) return false;
  if (d.status === 'none') return !!allowNone && d.player === null && d.dealer === null;
  if (d.status !== 'active' && d.status !== 'finished') return false;
  const active = d.status === 'active';
  if (!isCount(d.bet) || d.bet < 1 || !isCount(d.wager) || d.wager < d.bet) return false;
  if (!validBjHand(d.player, false) || typeof d.player.soft !== 'boolean' || !validBjHand(d.dealer, active)) return false;
  if (active) {
    // у активной раздачи вторая карта дилера закрыта, итога и выплаты ещё нет
    return d.dealer.cards.length === 2 && d.dealer.cards[1] === null && d.dealer.cards[0] !== null
      && d.result === null && d.payout === null;
  }
  return d.dealer.cards.every((c) => c !== null) && BJ_RESULTS.includes(d.result) && isCount(d.payout);
}
const validBjAction = (d) => validBjState(d, false);

// #endregion

// #region Блэкджек: рисование
// ---------- рисование ----------
function bjCardEl(card, isNew) {
  const el = document.createElement('div');
  if (card === null) {
    el.className = 'bj-card back';
    el.setAttribute('aria-label', 'Закрытая карта');
  } else {
    const rank = card.slice(0, -1);
    const suit = card.slice(-1);
    el.className = 'bj-card' + (suit === 'H' || suit === 'D' ? ' red' : '');
    el.innerHTML = '<span class="rank"></span>' + BJ_SUIT_SVG[suit];
    el.firstChild.textContent = rank;
    el.setAttribute('aria-label', rank + ' ' + { S: 'пик', H: 'червей', D: 'бубен', C: 'треф' }[suit]);
  }
  if (isNew) el.classList.add('new');
  return el;
}

// Карты внахлёст: если ряд не помещается, соседние карты наезжают друг на друга (до 8 и больше без прокрутки)
function bjFillCards(box, cards, newFrom) {
  box.textContent = '';
  const width = box.clientWidth || 280;
  const cardW = 46;
  const n = cards.length;
  const gap = 4;
  const free = n > 1 ? Math.min(gap, (width - 4 - cardW * n) / (n - 1)) : 0;
  cards.forEach((card, i) => {
    const el = bjCardEl(card, i >= newFrom);
    if (i > 0) el.style.marginLeft = Math.floor(free) + 'px';
    box.appendChild(el);
  });
}

let bjDrawn = { player: 0, dealer: 0 };   // сколько карт уже было нарисовано (новые получают анимацию)

function bjTotalText(hand, finished) {
  if (hand.cards.filter((c) => c !== null).length === 2 && hand.total === 21 && finished) return { text: 'Блэкджек!', cls: 'blackjack' };
  if (hand.total > 21) return { text: 'Перебор ' + hand.total, cls: 'bust' };
  return { text: (hand.soft ? 'Мягкие ' : '') + hand.total, cls: '' };
}

function renderBjTable() {
  const show = bj.view === 'play' || bj.view === 'result' || bj.busy;
  bjEls.table.hidden = !show;
  if (!show) return;
  bjFillCards(bjEls.playerCards, bj.shown.player, bjDrawn.player);
  bjFillCards(bjEls.dealerCards, bj.shown.dealer, bjDrawn.dealer);
  bjDrawn = { player: bj.shown.player.length, dealer: bj.shown.dealer.length };
  const g = bj.game;
  const settled = !bj.animating && g !== null;
  for (const [el, hand, who] of [[bjEls.playerTotal, g && g.player, 'p'], [bjEls.dealerTotal, g && g.dealer, 'd']]) {
    el.className = 'bj-total';
    if (!settled || !hand) { el.textContent = ''; continue; }
    const t = bjTotalText(who === 'p' ? hand : { cards: hand.cards, total: hand.total, soft: false }, g.status === 'finished');
    el.textContent = t.text;
    if (t.cls) el.classList.add(t.cls);
  }
}

// число одним текстом: короткая запись, полное значение в подсказке
function setBjLine(el, shortText, fullText) {
  el.textContent = shortText;
  el.title = fullText;
}

function renderBjBanner() {
  const g = bj.game;
  const done = bj.view === 'result' && !bj.animating && g !== null && g.status === 'finished';
  bjEls.banner.hidden = !(done || bj.view === 'start');
  bjEls.banner.classList.remove('win', 'lose');
  bjEls.bannerDetail.textContent = '';
  bjEls.bannerDetail.title = '';
  if (!done) {
    bjEls.bannerTitle.textContent = bj.view === 'start' ? 'Сделайте ставку' : '';
    bjEls.bannerTitle.title = '';
    return;
  }
  const profit = g.payout - g.wager;
  const money = (sign, n) => ({ short: sign + formatCompact(n), full: sign + formatNumber(n) });
  let title = '';
  let detail = '';
  if (g.result === 'blackjack') { const m = money('+', profit); title = 'Блэкджек! ' + m.short; detail = m.full; bjEls.banner.classList.add('win'); }
  else if (g.result === 'win' || g.result === 'dealer_bust') {
    const m = money('+', profit); title = 'Победа ' + m.short; detail = g.result === 'dealer_bust' ? 'Перебор у дилера' : m.full; bjEls.banner.classList.add('win');
  } else if (g.result === 'push') { title = 'Ничья'; detail = 'Ставка возвращена'; }
  else if (g.result === 'bust') { const m = money('−', g.wager); title = 'Перебор ' + m.short; detail = m.full; bjEls.banner.classList.add('lose'); }
  else { const m = money('−', g.wager); title = 'Проигрыш ' + m.short; detail = m.full; bjEls.banner.classList.add('lose'); }
  bjEls.bannerTitle.textContent = title;
  bjEls.bannerDetail.textContent = detail;
  bjEls.bannerTitle.title = detail;
}

// Фишки ставки по серверному балансу блэкджека (те же номиналы, что в других играх)
const bjChipBar = makeChipBar({
  root: bjEls.bets,
  attr: 'jbet',
  getBalance: () => bj.balance === null ? 0 : bj.balance,
  getBet: () => Number(bjEls.bet.value),
  setBet: (v) => { bjEls.bet.value = String(v); }
});
const renderBjChips = bjChipBar.render;
const syncBjChips = bjChipBar.sync;

function renderBj() {
  const loading = bj.view === 'loading';
  bjEls.skel.hidden = !(loading && !bj.error);
  if (bj.balance !== null) {
    bjEls.balance.classList.remove('skeleton');
    bjEls.balance.textContent = spacedNumber(bj.balance);
    fitNumberFont(bjEls.balance, bjEls.balance.textContent.length);
  }
  bjEls.switchBtn.disabled = bj.busy;
  renderBjChips();
  syncBjChips();
  const playing = bj.view === 'play' && bj.game !== null;
  bjEls.bets.hidden = !(bj.view === 'start' || bj.view === 'result');
  bjEls.actions.hidden = !(playing || (bj.busy && bj.view === 'play'));
  renderBjTable();
  renderBjBanner();
  if (playing) {
    setBjLine(bjEls.stake, 'Ставка ' + formatCompact(bj.game.wager), 'Ставка ' + formatNumber(bj.game.wager));
    const allowed = bj.game.actions;
    bjEls.hit.hidden = !allowed.includes('hit');
    bjEls.stand.hidden = !allowed.includes('stand');
    bjEls.double.hidden = !allowed.includes('double');
    [bjEls.hit, bjEls.stand, bjEls.double].forEach((b) => { b.disabled = bj.busy; });
  }
  bjEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = bj.busy || bj.balance === null; });
  bjEls.deal.textContent = bj.view === 'result' ? 'Новая раздача' : 'Раздать';
  refreshBetPanels();
}

// #endregion

// #region Блэкджек: запросы
// ---------- запросы ----------
async function fetchBjState() {
  return fetchGameState('/api/blackjack/state', (d) => validBjState(d, true));
}

const bjKey = (g) => g.status + ':' + g.player.cards.join('') + ':' + g.dealer.cards.join('') + ':' + g.payout;

// Применяет состояние с сервера без анимации: активная раздача восстанавливается, завершённая остаётся на столе
function applyBjState(d, announce) {
  bj.loaded = true;
  bj.error = false;
  bj.balance = d.balance;
  setBjMessage('', '', false);
  if (d.status === 'none') {
    bj.game = null;
    bj.view = 'start';
    bj.shown = { player: [], dealer: [] };
  } else {
    bj.game = d;
    bj.view = d.status === 'active' ? 'play' : 'result';
    bj.shown = { player: d.player.cards.slice(), dealer: d.dealer.cards.slice() };
    bjDrawn = { player: bj.shown.player.length, dealer: bj.shown.dealer.length };
    if (announce && d.status === 'finished' && d.auto === true && !bj.seen.has(bjKey(d))) {
      setBjNotice('Раздача закрылась автоматически: вы остановились, дилер доиграл');
    }
    if (d.status === 'finished') bj.seen.add(bjKey(d));
  }
  renderBj();
}

async function loadBj(reason) {
  return loadGameState(bj, {
    id: 'blackjack',
    fetchState: fetchBjState,
    applyState: (d) => applyBjState(d, true),
    setMessage: setBjMessage,
    setNotice: setBjNotice,
    render: renderBj
  }, reason);
}

// #endregion

// #region Блэкджек: анимация
// ---------- анимация ----------
// Карты раздаются по одной, дилер открывает вторую карту и добирает по одной. Очки показываются после анимации.
async function animateBj(prev, d, kind) {
  const target = { player: d.player.cards, dealer: d.dealer.cards };
  if (reducedMotion()) {
    bj.shown = { player: target.player.slice(), dealer: target.dealer.slice() };
    return;
  }
  bj.animating = true;
  const step = async (ms) => { renderBjTable(); await sleep(ms); };
  if (kind === 'start') {
    bj.shown = { player: [], dealer: [] };
    bjDrawn = { player: 0, dealer: 0 };
    const order = [['player', 0], ['dealer', 0], ['player', 1], ['dealer', 1]];
    for (const [who, i] of order) {
      bj.shown[who].push(who === 'dealer' && i === 1 ? null : target[who][i]);
      await step(BJ_DEAL_MS);
    }
  } else {
    for (let i = prev.player.length; i < target.player.length; i++) {   // hit и double: одна новая карта игроку
      bj.shown.player.push(target.player[i]);
      await step(BJ_DEAL_MS);
    }
  }
  if (d.status === 'finished') {
    await sleep(BJ_DEALER_MS - BJ_DEAL_MS > 0 ? BJ_DEALER_MS - BJ_DEAL_MS : 0);
    bj.shown.dealer[1] = target.dealer[1];            // дилер открывает вторую карту
    bjDrawn.dealer = 1;                                 // открытая карта перерисуется новой
    await step(BJ_DEALER_MS);
    for (let i = bj.shown.dealer.length; i < target.dealer.length; i++) {   // и добирает по одной
      bj.shown.dealer.push(target.dealer[i]);
      await step(BJ_DEALER_MS);
    }
  }
  bj.animating = false;
}

// Действие пользователя: блокировка, до 3 попыток с одним request_id, разбор ответа.
// Если результата нет, ничего не угадываем: запрашиваем реальное состояние
async function bjAct(path, body, kind) {
  if (bj.busy) return;
  if (!(tg && tg.initData)) {
    setBjNotice('Откройте игру через бота в Telegram');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    setBjNotice('Ошибка');
    return;
  }
  const prev = bj.game ? { player: bj.game.player.cards.slice(), dealer: bj.game.dealer.cards.slice() } : { player: [], dealer: [] };
  bj.busy = true;
  setBjNotice('');
  renderBj();
  const result = await postWithRetries(() => postMinesOnce(path, { request_id: id, ...body }, validBjAction));
  let note = 'Состояние обновлено';
  let reload = true;
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      bj.game = d;
      bj.view = d.status === 'active' ? 'play' : 'result';
      await animateBj(prev, d, kind);
      bj.animating = false;
      bj.shown = { player: d.player.cards.slice(), dealer: d.dealer.cards.slice() };
      bj.balance = d.balance;                 // баланс в шапке меняется только после анимации
      if (d.status === 'finished') bj.seen.add(bjKey(d));
      bj.busy = false;
      renderBj();
      haptic(d.status === 'active' ? 'light' : (d.payout > d.wager ? 'success' : (d.payout === d.wager ? 'light' : 'error')));
      loadServer('after');
      return;
    } catch (e) {
      bj.animating = false;
      note = 'Не удалось показать результат. Состояние обновлено';
    }
  } else {
    ({ note, reload } = actionFailure(result, {
      insufficient_funds: 'Не хватает фишек',
      active_game_exists: 'У вас уже есть начатая раздача',
      no_active_game: 'Раздача уже закрыта',
      invalid_action: 'Это действие сейчас недоступно'
    }));
  }
  if (reload) {
    try {
      bj.animating = false;
      applyBjState(await fetchBjState(), false);
      setBjNotice(note);
    } catch (e) {
      setBjNotice('Нет связи. Состояние раздачи неизвестно, обновите экран');
    }
  } else {
    setBjNotice(note);
  }
  bj.animating = false;
  bj.busy = false;
  renderBj();
  loadServer('after');
}

function bjDeal() {
  const bet = Number(bjEls.bet.value);
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > BJ_BET_MAX) {
    setBjNotice('Введите целую ставку от 1 до ' + formatNumber(BJ_BET_MAX));
    return;
  }
  if (bj.balance !== null && bet > bj.balance) {
    setBjNotice('Не хватает фишек');
    return;
  }
  bj.animating = true;   // прошлый итог и очки на время запроса не показываются
  bjAct('/api/blackjack/start', { bet }, 'start');
}

setupBetPanel({
  input: bjEls.bet,
  maxBtn: bjEls.maxBtn,
  halfBtn: document.getElementById('bj-half'),
  doubleBtn: document.getElementById('bj-double-bet'),
  getLimit: bjBetLimit
});
bjEls.bet.addEventListener('input', syncBjChips);
bjEls.bets.querySelectorAll('[data-jbet]').forEach((b) => b.addEventListener('click', () => {
  bjEls.bet.value = b.dataset.jbet;
  syncBjChips();
  refreshBetPanels();
  haptic('light');
}));
bjEls.deal.addEventListener('click', bjDeal);
bjEls.hit.addEventListener('click', () => bjAct('/api/blackjack/action', { action: 'hit' }, 'hit'));
bjEls.stand.addEventListener('click', () => bjAct('/api/blackjack/action', { action: 'stand' }, 'stand'));
bjEls.double.addEventListener('click', () => bjAct('/api/blackjack/action', { action: 'double' }, 'double'));
bjEls.retry.addEventListener('click', () => loadBj('manual'));
bjEls.switchBtn.addEventListener('click', toggleGameMenu);
window.addEventListener('resize', () => { if (bj.view !== 'loading') renderBjTable(); });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadBj('visible');
});

// #endregion

// #region Краш
// ---------- игра «Краш» ----------
// Время и исход только на сервере. Клиент рисует рост множителя по elapsed_ms из ответа плюс локальное время с момента ответа
// (только для показа), а во время полёта раз в 300 мс спрашивает состояние: крах и итог берутся из ответа сервера.
// Режим «авто» решается сразу при старте: клиент проигрывает короткую анимацию до цели или до точки краха из ответа.
// Баланс в шапке в авто-режиме обновляется после анимации. Ставка, цель и лента краха живут только в памяти.
const CR_BET_MAX = 1000000000;
const CR_TARGET_MIN = 101;
const CR_TARGET_MAX = 25000;     // ×250.00, как CAP_X100 на сервере
const CR_POLL_MS = 300;          // опрос состояния во время полёта
const CR_AUTO_MAX_MS = 2000;     // авто-анимация не дольше
const CR_HISTORY = 10;
const CR_MULT_RE = /^\d+\.\d\d$/;

const crEls = {
  balance: document.getElementById('cr-balance'),
  switchBtn: document.getElementById('cr-switch'),
  notice: document.getElementById('cr-notice'),
  skel: document.getElementById('cr-skel'),
  msg: document.getElementById('cr-msg'),
  code: document.getElementById('cr-code'),
  retry: document.getElementById('cr-retry'),
  chart: document.getElementById('cr-chart'),
  curve: document.getElementById('cr-curve'),
  mult: document.getElementById('cr-mult'),
  label: document.getElementById('cr-label'),
  history: document.getElementById('cr-history'),
  banner: document.getElementById('cr-banner'),
  bannerTitle: document.getElementById('cr-banner-title'),
  bannerDetail: document.getElementById('cr-banner-detail'),
  bets: document.getElementById('cr-bets'),
  bet: document.getElementById('cr-bet'),
  maxBtn: document.getElementById('cr-max'),
  target: document.getElementById('cr-target'),
  start: document.getElementById('cr-start'),
  actions: document.getElementById('cr-actions'),
  cash: document.getElementById('cr-cash')
};

const cr = {
  view: 'loading',      // 'loading' | 'start' | 'play' | 'result'
  loaded: false,
  error: false,
  game: null,           // последний ответ сервера
  balance: null,
  shownBalance: null,   // баланс в шапке на время авто-анимации
  busy: false,          // идёт запрос: кнопки заблокированы
  animating: false,     // идёт авто-анимация
  base: { elapsed: 0, at: 0 },   // elapsed_ms из последнего ответа и момент его получения
  history: [],          // последние множители краха (текст), только в памяти
  seen: new Set(),
  pressed: false,       // игрок нажал «Забрать» в этом раунде
  live: false,          // итог пришёл, пока игрок смотрел полёт (иначе раунд закончился без него)
  inFlight: false,
  lastRequestAt: -Infinity,
  timer: null,
  raf: 0,
  iv: 0,
  pollTimer: 0,
  polling: false
};
registerGame({ id: 'crash', state: cr, render: renderCrash, busy: () => cr.busy || cr.animating, keepBalance: () => cr.view === 'play' });

const crBetLimit = () => Math.max(1, Math.min(CR_BET_MAX, cr.balance === null ? CR_BET_MAX : cr.balance));
const crText = (x100) => '×' + Math.floor(x100 / 100) + '.' + String(x100 % 100).padStart(2, '0');
const crX100 = (text) => Math.round(parseFloat(text) * 100);

function setCrNotice(text) { crEls.notice.textContent = text; }
function setCrMessage(text, code, retry) {
  crEls.msg.textContent = text;
  crEls.code.textContent = code ? 'код: ' + code : '';
  crEls.retry.hidden = !retry;
}

// Цель из поля: пусто = вручную (null); число 1.01..1000 с двумя знаками; иначе undefined (ошибка)
function crParseTarget() {
  const raw = crEls.target.value.trim().replace(',', '.');
  if (raw === '') return null;
  if (!/^\d{1,3}(\.\d{0,2})?$/.test(raw)) return undefined;
  const x = Math.round(parseFloat(raw) * 100);
  return x >= CR_TARGET_MIN && x <= CR_TARGET_MAX ? x : undefined;
}

// #endregion

// #region Краш: проверка ответа сервера
// ---------- проверка ответа сервера (по реальному контракту, docs/API.md) ----------
function validCrash(d, allowNone) {
  if (!d || !isCount(d.balance) || !isInt(d.doubling_ms) || d.doubling_ms < 1 || typeof d.cap !== 'string') return false;
  if (d.status === 'none') return !!allowNone && d.mode === null && d.bet === null;
  if (d.status !== 'active' && d.status !== 'finished') return false;
  if ((d.mode !== 'auto' && d.mode !== 'manual') || !isCount(d.bet) || d.bet < 1) return false;
  if (d.mode === 'auto' ? !(typeof d.target === 'string' && CR_MULT_RE.test(d.target)) : d.target !== null) return false;
  if (d.status === 'active') {
    return d.mode === 'manual' && isCount(d.elapsed_ms) && d.crash_multiplier === null && d.result === null
      && d.multiplier === null && d.payout === null;
  }
  return typeof d.crash_multiplier === 'string' && CR_MULT_RE.test(d.crash_multiplier) && (d.result === 'win' || d.result === 'lose')
    && typeof d.multiplier === 'string' && CR_MULT_RE.test(d.multiplier) && isCount(d.payout);
}
const validCrashAction = (d) => validCrash(d, false);

// #endregion

// #region Краш: график и множитель
// ---------- график и множитель ----------
const crElapsed = () => cr.base.elapsed + (performance.now() - cr.base.at);
const crDoubling = () => (cr.game && cr.game.doubling_ms) || 6000;
const crCap = () => (cr.game && typeof cr.game.cap === 'string' ? crX100(cr.game.cap) : CR_TARGET_MAX);   // предел множителя из ответа сервера

// m в сотых по времени (только для показа; сервер считает так же, но решает именно он)
function crM100(elapsed) {
  const x = Math.max(0, elapsed) / crDoubling();
  return Math.min(crCap(), Math.floor(100 * Math.pow(2, x)));
}

function crDrawCurve(elapsedMs) {
  const dbl = crDoubling();
  const m = Math.pow(2, Math.max(0, elapsedMs) / dbl);
  const xmax = Math.max(8000, elapsedMs * 1.1);
  const ymax = Math.max(2, m * 1.15);
  const pts = [];
  const N = 36;
  for (let i = 0; i <= N; i++) {
    const t = (elapsedMs * i) / N;
    const x = (t / xmax) * 300;
    const y = 149 - ((Math.pow(2, t / dbl) - 1) / (ymax - 1)) * 148;
    pts.push((i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1));
  }
  crEls.curve.setAttribute('d', pts.join(''));
}

function crSetTone(tone) { crEls.chart.dataset.tone = tone; }

function crShowMult(x100) {
  crEls.mult.textContent = crText(x100);
}

// Отрисовка одного кадра: множитель, кривая и «Забрать N» (N = ставка × текущий множитель)
function crDraw() {
  if (cr.view !== 'play' || !cr.game) return;
  const e = crElapsed();
  const x100 = crM100(e);
  crShowMult(x100);
  crDrawCurve(e);
  if (!cr.busy) crEls.cash.textContent = 'Забрать ' + formatCompact(Math.floor(cr.game.bet * x100 / 100));
}

function crRaf() {
  cr.raf = 0;
  crDraw();
  if (cr.view === 'play') cr.raf = requestAnimationFrame(crRaf);
}

function crStartLoop() {
  crStopLoop();
  crDraw();
  if (reducedMotion()) cr.iv = setInterval(crDraw, 500);   // без плавной анимации: число реже
  else cr.raf = requestAnimationFrame(crRaf);
  crSchedulePoll();
}

function crStopLoop() {
  if (cr.raf) cancelAnimationFrame(cr.raf);
  if (cr.iv) clearInterval(cr.iv);
  cr.raf = 0;
  cr.iv = 0;
  clearTimeout(cr.pollTimer);
  cr.pollTimer = 0;
}

function crSchedulePoll() {
  clearTimeout(cr.pollTimer);
  cr.pollTimer = setTimeout(crPoll, CR_POLL_MS);
}

// Опрос состояния во время полёта: крах и итог берутся из ответа сервера
async function crPoll() {
  cr.pollTimer = 0;
  if (cr.view !== 'play' || activeTab !== 'play' || currentGame !== 'crash') return;
  if (cr.busy || cr.polling || document.visibilityState !== 'visible') { crSchedulePoll(); return; }
  cr.polling = true;
  try {
    const d = await fetchCrashState();
    if (cr.view === 'play' && !cr.busy) crApplyLive(d);
  } catch (e) {
    // сбой опроса не прерывает полёт: следующая попытка через интервал (429 тоже)
  } finally {
    cr.polling = false;
  }
  if (cr.view === 'play') crSchedulePoll();
}

// Живой ответ: активный раунд пересинхронизирует время, завершённый показывает итог (крах)
function crApplyLive(d) {
  cr.live = d.status !== 'active';
  if (d.status === 'active') {
    cr.game = d;
    cr.balance = d.balance;
    cr.base = { elapsed: d.elapsed_ms, at: performance.now() };
    renderCrashChrome();
    return;
  }
  crFinishWith(d, false);
}

// #endregion

// #region Краш: итог
// ---------- итог ----------
function crFinishWith(d, byCashout) {
  crStopLoop();
  cr.game = d;
  cr.view = d.status === 'none' ? 'start' : 'result';
  cr.animating = false;
  cr.shownBalance = null;
  cr.balance = d.balance;
  if (d.status === 'finished') {
    cr.seen.add(crKey(d));
    crPushHistory(d.crash_multiplier);
  }
  renderCrash();
  if (d.status === 'finished') haptic(d.result === 'win' ? 'success' : 'error');
  loadServer('after');
}

const crKey = (g) => g.mode + ':' + g.bet + ':' + g.crash_multiplier + ':' + g.payout + ':' + g.multiplier;

function crPushHistory(text) {
  cr.history.unshift(text);
  cr.history.length = Math.min(cr.history.length, CR_HISTORY);
}

function renderCrHistory() {
  crEls.history.hidden = cr.history.length === 0;
  crEls.history.textContent = '';
  cr.history.forEach((t) => {
    const li = document.createElement('li');
    li.textContent = '×' + t;
    if (parseFloat(t) >= 2) li.className = 'high';
    crEls.history.appendChild(li);
  });
}

function renderCrResult() {
  const g = cr.game;
  const done = cr.view === 'result' && !cr.animating && g && g.status === 'finished';
  crEls.banner.hidden = !(done || cr.view === 'start');
  crEls.banner.classList.remove('win', 'lose');
  crEls.bannerDetail.textContent = '';
  crEls.bannerDetail.title = '';
  crEls.bannerTitle.title = '';
  if (!done) {
    crEls.bannerTitle.textContent = cr.view === 'start' ? 'Сделайте ставку' : '';
    return;
  }
  const crashX = '×' + g.crash_multiplier;
  if (g.result === 'win') {
    const profit = g.payout - g.bet;
    crEls.bannerTitle.textContent = 'Выигрыш +' + formatCompact(profit) + ' (×' + g.multiplier + ')';
    crEls.bannerTitle.title = 'Выигрыш +' + formatNumber(profit);
    crEls.bannerDetail.textContent = 'Крах был бы ' + crashX;
    crEls.banner.classList.add('win');
  } else if (g.mode === 'manual' && (cr.pressed || (g.auto && !cr.live))) {
    crEls.bannerTitle.textContent = 'Не успел';
    crEls.bannerDetail.textContent = 'Крах ' + crashX + ', потеряно ' + formatCompact(g.bet);
    crEls.bannerDetail.title = 'Потеряно ' + formatNumber(g.bet);
    crEls.banner.classList.add('lose');
  } else {
    crEls.bannerTitle.textContent = 'Крах ' + crashX + ', потеряно ' + formatCompact(g.bet);
    crEls.bannerTitle.title = 'Потеряно ' + formatNumber(g.bet);
    crEls.banner.classList.add('lose');
  }
}

// Фишки ставки по серверному балансу краша (те же номиналы, что в других играх)
const crChipBar = makeChipBar({
  root: crEls.bets,
  attr: 'cbet',
  getBalance: () => cr.balance === null ? 0 : cr.balance,
  getBet: () => Number(crEls.bet.value),
  setBet: (v) => { crEls.bet.value = String(v); }
});
const renderCrChips = crChipBar.render;

function syncCrChips() {
  crChipBar.sync();
  const t = crParseTarget();
  crEls.bets.querySelectorAll('[data-ctarget]').forEach((b) => {
    b.setAttribute('aria-pressed', String(typeof t === 'number' && t === Math.round(parseFloat(b.dataset.ctarget) * 100)));
  });
}

// шапка и кнопки без перерисовки графика (вызывается и по ответам опроса)
function renderCrashChrome() {
  const bal = cr.animating && cr.shownBalance !== null ? cr.shownBalance : cr.balance;
  if (bal !== null) {
    crEls.balance.classList.remove('skeleton');
    crEls.balance.textContent = spacedNumber(bal);
    fitNumberFont(crEls.balance, crEls.balance.textContent.length);
  }
  crEls.switchBtn.disabled = cr.busy || cr.animating;
  crEls.cash.disabled = cr.busy;
  if (cr.busy && cr.view === 'play') crEls.cash.textContent = 'Фиксируем…';
}

function renderCrash() {
  crStopIfNotPlaying();
  const loading = cr.view === 'loading';
  crEls.skel.hidden = !(loading && !cr.error);
  const showChart = cr.view !== 'loading';
  crEls.chart.hidden = !showChart;
  renderCrHistory();
  crEls.bets.hidden = !(cr.view === 'start' || cr.view === 'result');
  crEls.actions.hidden = cr.view !== 'play';
  renderCrashChrome();
  renderCrChips();
  syncCrChips();
  renderCrResult();
  const g = cr.game;
  if (cr.view === 'start' && !cr.animating) {
    crSetTone('idle');
    crShowMult(100);
    crEls.label.textContent = 'Ждём старта';
    crEls.curve.setAttribute('d', 'M0 149');
  } else if (cr.view === 'result' && g && !cr.animating) {
    const win = g.result === 'win';
    crSetTone(win ? 'win' : 'crash');
    const x100 = win ? crX100(g.multiplier) : crX100(g.crash_multiplier);
    crShowMult(x100);
    crEls.label.textContent = win ? 'Выигрыш +' + formatCompact(g.payout - g.bet) : 'Крах ' + crText(crX100(g.crash_multiplier));
    crDrawCurve(crDoubling() * Math.log2(Math.max(1, x100 / 100)));
  } else if (cr.view === 'play' && g) {
    crSetTone('idle');
    crEls.label.textContent = g.target === null ? 'Нажмите «Забрать» до краха' : '';
  }
  crEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = cr.busy || cr.animating || cr.balance === null; });
  crEls.start.textContent = cr.view === 'result' ? 'Ещё раз' : 'Старт';
  if (cr.view === 'play' && !cr.raf && !cr.iv) crStartLoop();
  refreshBetPanels();
}

function crStopIfNotPlaying() {
  if (cr.view !== 'play') crStopLoop();
}

// Применяет состояние с сервера без анимации: активный раунд восстанавливается, завершённый остаётся на экране
function applyCrashState(d, announce) {
  cr.loaded = true;
  cr.error = false;
  cr.balance = d.balance;
  cr.shownBalance = null;
  cr.animating = false;
  setCrMessage('', '', false);
  if (d.status === 'none') {
    cr.game = null;
    cr.view = 'start';
  } else if (d.status === 'active') {
    cr.game = d;
    cr.view = 'play';
    cr.base = { elapsed: d.elapsed_ms, at: performance.now() };
    cr.pressed = false;
    cr.live = false;
  } else {
    cr.game = d;
    cr.view = 'result';
    cr.live = false;
    if (announce && d.auto === true && !cr.seen.has(crKey(d))) {
      setCrNotice('Раунд закончился без вас: ' + (d.result === 'win' ? 'выигрыш на ×' + d.multiplier : 'крах ×' + d.crash_multiplier));
    }
    cr.seen.add(crKey(d));
    if (!cr.history.length) crPushHistory(d.crash_multiplier);
  }
  renderCrash();
}

// #endregion

// #region Краш: запросы
// ---------- запросы ----------
async function fetchCrashState() {
  return fetchGameState('/api/crash/state', (d) => validCrash(d, true));
}

async function loadCrash(reason) {
  return loadGameState(cr, {
    id: 'crash',
    fetchState: fetchCrashState,
    applyState: (d) => applyCrashState(d, true),
    setMessage: setCrMessage,
    setNotice: setCrNotice,
    render: renderCrash,
    blockWhileAnimating: true,
    skipRecent: (g) => g.view !== 'play'
  }, reason);
}

// Авто-режим: быстрая анимация до цели (выигрыш) или до точки краха (проигрыш), потом итог и баланс
async function crAnimateAuto(d) {
  const win = d.result === 'win';
  const endX100 = win ? crX100(d.multiplier) : crX100(d.crash_multiplier);
  cr.animating = true;
  cr.view = 'result';
  cr.game = d;
  cr.shownBalance = cr.balance;
  renderCrash();
  crSetTone('idle');
  crEls.label.textContent = 'Цель ' + crText(crX100(d.target));
  if (!reducedMotion()) {
    const dur = Math.min(CR_AUTO_MAX_MS, Math.max(500, 400 * Math.log2(Math.max(2, endX100 / 100)) + 300));
    const t0 = performance.now();
    await new Promise((resolve) => {
      const step = () => {
        const f = Math.min(1, (performance.now() - t0) / dur);
        const x100 = Math.floor(100 * Math.pow(endX100 / 100, f));
        crShowMult(x100);
        crDrawCurve(crDoubling() * Math.log2(Math.max(1, x100 / 100)));
        if (f < 1) requestAnimationFrame(step);
        else resolve();
      };
      step();
    });
  }
  cr.animating = false;
}

async function crAct(path, body, onOk) {
  if (cr.busy) return;
  if (!(tg && tg.initData)) { setCrNotice('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setCrNotice('Ошибка'); return; }
  cr.busy = true;
  setCrNotice('');
  renderCrashChrome();
  crEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = true; });
  const result = await postWithRetries(() => postMinesOnce(path, { request_id: id, ...body }, validCrashAction));
  let note = 'Состояние обновлено';
  let reload = true;
  if (result && result.kind === 'ok') {
    try {
      await onOk(result.data);
      cr.busy = false;
      renderCrash();
      return;
    } catch (e) {
      cr.animating = false;
      note = 'Не удалось показать результат. Состояние обновлено';
    }
  } else {
    ({ note, reload } = actionFailure(result, {
      too_early: ['Слишком рано: вывод возможен с ×1.01', false],
      insufficient_funds: 'Не хватает фишек',
      active_game_exists: 'У вас уже есть начатый раунд',
      no_active_game: 'Раунд уже закрыт'
    }));
  }
  cr.busy = false;
  if (reload) {
    try {
      applyCrashState(await fetchCrashState(), false);
      setCrNotice(note);
    } catch (e) {
      setCrNotice('Нет связи. Состояние раунда неизвестно, обновите экран');
    }
  } else {
    setCrNotice(note);
    renderCrash();
  }
  loadServer('after');
}

function crStart() {
  const bet = Number(crEls.bet.value);
  const target = crParseTarget();
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > CR_BET_MAX) { setCrNotice('Введите целую ставку от 1 до ' + formatNumber(CR_BET_MAX)); return; }
  if (target === undefined) { setCrNotice('Авто-вывод: от 1.01 до 250 (или пусто для ручного режима)'); return; }
  if (cr.balance !== null && bet > cr.balance) { setCrNotice('Не хватает фишек'); return; }
  cr.pressed = false;
  const body = target === null ? { bet } : { bet, target_x100: target };
  crAct('/api/crash/start', body, async (d) => {
    if (d.status === 'active') {
      cr.game = d;
      cr.view = 'play';
      cr.base = { elapsed: d.elapsed_ms, at: performance.now() };
      cr.balance = d.balance;
      renderCrash();
      haptic('light');
      return;
    }
    await crAnimateAuto(d);    // баланс в шапке остаётся прежним до конца анимации
    crFinishWith(d, false);
  });
}

function crCash() {
  if (cr.view !== 'play' || cr.busy) return;
  cr.pressed = true;
  crAct('/api/crash/cashout', {}, async (d) => { crFinishWith(d, true); });
}

setupBetPanel({
  input: crEls.bet,
  maxBtn: crEls.maxBtn,
  halfBtn: document.getElementById('cr-half'),
  doubleBtn: document.getElementById('cr-double-bet'),
  getLimit: crBetLimit
});
crEls.bet.addEventListener('input', syncCrChips);
crEls.bets.querySelectorAll('[data-cbet]').forEach((b) => b.addEventListener('click', () => {
  crEls.bet.value = b.dataset.cbet;
  syncCrChips();
  refreshBetPanels();
  haptic('light');
}));
// поле цели: цифры и один разделитель, панель над клавиатурой как у ставки, «Готово» вместо «Макс» на время ввода
crEls.target.addEventListener('input', () => {
  let v = crEls.target.value.replace(',', '.').replace(/[^\d.]/g, '');
  const dot = v.indexOf('.');
  if (dot >= 0) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2);
  crEls.target.value = v.slice(0, 7);
  syncCrChips();
});
crEls.target.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); crEls.target.blur(); } });
crEls.target.addEventListener('focus', () => {
  crEls.maxBtn.dataset.mode = 'done';
  crEls.maxBtn.textContent = 'Готово';
  dockPanel(crEls.bets);
});
crEls.target.addEventListener('blur', () => {
  crEls.maxBtn.dataset.mode = 'max';
  crEls.maxBtn.textContent = 'Макс';
  undockPanel(crEls.bets);
  syncCrChips();
});
crEls.maxBtn.addEventListener('click', () => { if (document.activeElement === crEls.target) crEls.target.blur(); });
crEls.bets.querySelectorAll('[data-ctarget]').forEach((b) => b.addEventListener('click', () => {
  const same = crParseTarget() === Math.round(parseFloat(b.dataset.ctarget) * 100);
  crEls.target.value = same ? '' : b.dataset.ctarget;   // повторное нажатие возвращает ручной режим
  syncCrChips();
  haptic('light');
}));
crEls.start.addEventListener('click', crStart);
crEls.cash.addEventListener('click', crCash);
crEls.retry.addEventListener('click', () => loadCrash('manual'));
crEls.switchBtn.addEventListener('click', toggleGameMenu);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadCrash('visible');
});

// #endregion

// #region Хило
// ---------- игра «Хило» ----------
// Состояние партии только с сервера: следующей карты клиент не знает (её не существует до хода), множители, вероятности и выплаты
// считает сервер, клиент ничего не считает сам. Каждое действие получает новый request_id; повторы (сеть, таймаут, 429, 5xx) идут с тем же
// (postJson, postMinesOnce). Баланс в шапке обновляется только после анимации. Ставка живёт в памяти.
const HL_BET_MAX = 1000000000;
const HL_FLIP_MS = 420;          // переворот карты
const HL_HIT_MS = 500;           // подсветка угадал/ошибся после переворота
const HL_RANKS = ['', 'A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
const HL_STATUSES = ['active', 'lost', 'cashed', 'capped', 'refunded'];
const HL_HOWS = ['start', 'win', 'tie', 'skip', 'lose'];
const HL_MULT_RE = /^\d+\.\d\d$/;
const HL_PROB_RE = /^\d+\.\d$/;
const HL_SUIT_NAMES = { S: 'пик', H: 'червей', D: 'бубен', C: 'треф' };

const hlEls = {
  balance: document.getElementById('hl-balance'),
  switchBtn: document.getElementById('hl-switch'),
  notice: document.getElementById('hl-notice'),
  skel: document.getElementById('hl-skel'),
  msg: document.getElementById('hl-msg'),
  code: document.getElementById('hl-code'),
  retry: document.getElementById('hl-retry'),
  table: document.getElementById('hl-table'),
  history: document.getElementById('hl-history'),
  card: document.getElementById('hl-card'),
  mult: document.getElementById('hl-mult'),
  steps: document.getElementById('hl-steps'),
  now: document.getElementById('hl-now'),
  banner: document.getElementById('hl-banner'),
  bannerTitle: document.getElementById('hl-banner-title'),
  bannerDetail: document.getElementById('hl-banner-detail'),
  bets: document.getElementById('hl-bets'),
  bet: document.getElementById('hl-bet'),
  maxBtn: document.getElementById('hl-max'),
  start: document.getElementById('hl-start'),
  actions: document.getElementById('hl-actions'),
  hi: document.getElementById('hl-hi'),
  lo: document.getElementById('hl-lo'),
  hiSub: document.getElementById('hl-hi-sub'),
  loSub: document.getElementById('hl-lo-sub'),
  skip: document.getElementById('hl-skip'),
  cash: document.getElementById('hl-cash')
};

const hl = {
  view: 'loading',      // 'loading' | 'start' | 'play' | 'result'
  loaded: false,
  error: false,
  game: null,           // последний ответ сервера (активная или завершённая партия)
  balance: null,
  busy: false,          // идёт действие или анимация: кнопки заблокированы
  animating: false,     // карта переворачивается: итог и баланс ещё не показываются
  shownCard: null,      // карта на столе (на время анимации может отличаться от game.card)
  seen: new Set(),      // завершённые партии, о которых игрок уже знает (в этой сессии)
  inFlight: false,
  lastRequestAt: -Infinity,
  timer: null
};
registerGame({ id: 'hilo', state: hl, render: renderHl, busy: () => hl.busy || hl.animating });

const hlBetLimit = () => Math.max(1, Math.min(HL_BET_MAX, hl.balance === null ? HL_BET_MAX : hl.balance));

function setHlNotice(text) { hlEls.notice.textContent = text; }
function setHlMessage(text, code, retry) {
  hlEls.msg.textContent = text;
  hlEls.code.textContent = code ? 'код: ' + code : '';
  hlEls.retry.hidden = !retry;
}

// #endregion

// #region Хило: проверка ответа сервера
// ---------- проверка ответа сервера (по реальному контракту, docs/API.md) ----------
const validHlCard = (c) => !!c && typeof c === 'object' && Number.isInteger(c.rank) && c.rank >= 1 && c.rank <= 13
  && typeof c.suit === 'string' && !!HL_SUIT_NAMES[c.suit] && HL_HOWS.includes(c.how);
const validHlMove = (m) => !!m && typeof m.available === 'boolean' && typeof m.probability === 'string' && HL_PROB_RE.test(m.probability)
  && typeof m.multiplier === 'string' && HL_MULT_RE.test(m.multiplier) && isCount(m.payout);

function validHlState(d, allowNone) {
  if (!d || !isCount(d.balance) || !isCount(d.steps) || typeof d.multiplier !== 'string' || !HL_MULT_RE.test(d.multiplier)
    || !Array.isArray(d.history) || !d.history.every(validHlCard) || typeof d.can_cashout !== 'boolean') return false;
  if (d.status === 'none') return !!allowNone && d.card === null && d.moves === null;
  if (!HL_STATUSES.includes(d.status) || !isCount(d.bet) || d.bet < 1 || !validHlCard(d.card)) return false;
  if (d.status === 'active') {
    return validHlMove(d.moves && d.moves.hi) && validHlMove(d.moves && d.moves.lo) && isCount(d.payout_now) && d.payout === null;
  }
  return d.moves === null && d.payout_now === null && isCount(d.payout) && d.can_cashout === false;
}
const validHlAction = (d) => validHlState(d, false);

// #endregion

// #region Хило: рисование
// ---------- рисование ----------
const hlCardKey = (c) => (c ? c.rank + c.suit + c.how : '');
const hlRankText = (c) => HL_RANKS[c.rank];
const hlIsRed = (c) => c.suit === 'H' || c.suit === 'D';

function renderHlCard(animate, tone) {
  const c = hl.shownCard;
  const el = hlEls.card;
  el.className = 'hl-card';
  el.textContent = '';
  if (!c) { el.classList.add('back'); el.setAttribute('aria-label', 'Карта не выбрана'); return; }
  el.innerHTML = '<span class="rank"></span>' + BJ_SUIT_SVG[c.suit];
  el.firstChild.textContent = hlRankText(c);
  el.setAttribute('aria-label', hlRankText(c) + ' ' + HL_SUIT_NAMES[c.suit]);
  if (hlIsRed(c)) el.classList.add('red');
  if (hl.game && hl.game.status === 'lost' && !hl.animating) el.classList.add('lost');
  if (animate && !reducedMotion()) {
    void el.offsetWidth;
    el.classList.add(tone === 'win' ? 'hit-win' : tone === 'lose' ? 'hit-lose' : 'flip');
  }
}

function renderHlHistory() {
  hlEls.history.textContent = '';
  const items = hl.game ? hl.game.history : [];
  items.forEach((c) => {
    const li = document.createElement('li');
    li.className = 'hl-mini ' + c.how + (hlIsRed(c) ? ' red' : '');
    li.innerHTML = '<span></span>' + BJ_SUIT_SVG[c.suit];
    li.firstChild.textContent = hlRankText(c);
    li.setAttribute('aria-label', hlRankText(c) + ' ' + HL_SUIT_NAMES[c.suit]);
    hlEls.history.appendChild(li);
  });
}

function renderHlBanner() {
  const g = hl.game;
  const done = hl.view === 'result' && !hl.animating && g !== null && g.status !== 'active';
  hlEls.banner.hidden = !(done || hl.view === 'start');
  hlEls.banner.classList.remove('win', 'lose');
  hlEls.bannerDetail.textContent = '';
  hlEls.bannerDetail.title = '';
  hlEls.bannerTitle.title = '';
  if (!done) {
    hlEls.bannerTitle.textContent = hl.view === 'start' ? 'Сделайте ставку' : '';
    return;
  }
  const money = (sign, n) => ({ short: sign + formatCompact(n), full: sign + formatNumber(n) });
  let title = '';
  let detail = '';
  if (g.status === 'lost') { const m = money('−', g.bet); title = 'Не угадали ' + m.short; detail = m.full; hlEls.banner.classList.add('lose'); }
  else if (g.status === 'refunded') { title = 'Ставка возвращена'; detail = g.auto ? 'Партия закрылась автоматически' : ''; }
  else {
    const m = money('+', g.payout - g.bet);
    title = (g.status === 'capped' ? 'Потолок ×' + parseInt(g.cap, 10) + '! ' : 'Забрали ') + m.short;
    detail = g.auto ? 'Партия закрылась автоматически: выигрыш выплачен' : m.full;
    hlEls.banner.classList.add('win');
  }
  hlEls.bannerTitle.textContent = title;
  hlEls.bannerDetail.textContent = detail;
  hlEls.bannerTitle.title = detail;
}

function hlMoveText(m) {
  return m.available ? '×' + m.multiplier + ' · ' + m.probability + '%' : 'Всегда выигрыш, смысла нет';
}

// Фишки ставки по серверному балансу (те же номиналы, что в других играх)
const hlChipBar = makeChipBar({
  root: hlEls.bets,
  attr: 'hbet',
  getBalance: () => hl.balance === null ? 0 : hl.balance,
  getBet: () => Number(hlEls.bet.value),
  setBet: (v) => { hlEls.bet.value = String(v); }
});
const renderHlChips = hlChipBar.render;
const syncHlChips = hlChipBar.sync;

function renderHl() {
  const loading = hl.view === 'loading';
  hlEls.skel.hidden = !(loading && !hl.error);
  if (hl.balance !== null) {
    hlEls.balance.classList.remove('skeleton');
    hlEls.balance.textContent = spacedNumber(hl.balance);
    fitNumberFont(hlEls.balance, hlEls.balance.textContent.length);
  }
  hlEls.switchBtn.disabled = hl.busy;
  renderHlChips();
  syncHlChips();
  const g = hl.game;
  const playing = hl.view === 'play' && g !== null;
  const showTable = hl.view === 'play' || hl.view === 'result' || hl.busy;
  hlEls.table.hidden = !showTable;
  if (showTable) {
    renderHlHistory();
    hlEls.mult.textContent = g ? '×' + g.multiplier : '×1.00';
    hlEls.steps.textContent = g ? String(g.steps) : '0';
    const nowValue = g && g.status === 'active' ? g.payout_now : (g && g.payout !== null ? g.payout : 0);
    hlEls.now.textContent = formatCompact(nowValue);
    hlEls.now.title = formatNumber(nowValue);
  }
  hlEls.bets.hidden = !(hl.view === 'start' || hl.view === 'result');
  hlEls.actions.hidden = !(playing || (hl.busy && hl.view === 'play'));
  renderHlBanner();
  if (playing && g.status === 'active') {
    hlEls.hiSub.textContent = hlMoveText(g.moves.hi);
    hlEls.loSub.textContent = hlMoveText(g.moves.lo);
    hlEls.hi.disabled = hl.busy || !g.moves.hi.available;
    hlEls.lo.disabled = hl.busy || !g.moves.lo.available;
    hlEls.skip.disabled = hl.busy;
    hlEls.cash.disabled = hl.busy || !g.can_cashout;
    hlEls.cash.textContent = g.can_cashout ? 'Забрать ' + formatCompact(g.payout_now) : 'Забрать';
    hlEls.cash.title = g.can_cashout ? 'Забрать ' + formatNumber(g.payout_now) : 'Забрать можно после первого угаданного хода';
  }
  hlEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = hl.busy || hl.balance === null; });
  hlEls.start.textContent = hl.view === 'result' ? 'Новая партия' : 'Играть';
  refreshBetPanels();
}

// #endregion

// #region Хило: запросы
// ---------- запросы ----------
async function fetchHlState() {
  return fetchGameState('/api/hilo/state', (d) => validHlState(d, true));
}

const hlKey = (g) => g.status + ':' + hlCardKey(g.card) + ':' + g.steps + ':' + g.payout;

// Применяет состояние с сервера без анимации: активная партия восстанавливается, завершённая остаётся на столе
function applyHlState(d, announce) {
  hl.loaded = true;
  hl.error = false;
  hl.balance = d.balance;
  setHlMessage('', '', false);
  if (d.status === 'none') {
    hl.game = null;
    hl.view = 'start';
    hl.shownCard = null;
  } else {
    hl.game = d;
    hl.view = d.status === 'active' ? 'play' : 'result';
    hl.shownCard = d.card;
    if (announce && d.status !== 'active' && d.auto === true && !hl.seen.has(hlKey(d))) {
      setHlNotice(d.status === 'refunded' ? 'Партия закрылась автоматически: ставка возвращена' : 'Партия закрылась автоматически: выигрыш выплачен');
    }
    if (d.status !== 'active') hl.seen.add(hlKey(d));
  }
  renderHlCard(false);
  renderHl();
}

async function loadHilo(reason) {
  return loadGameState(hl, {
    id: 'hilo',
    fetchState: fetchHlState,
    applyState: (d) => applyHlState(d, true),
    setMessage: setHlMessage,
    setNotice: setHlNotice,
    render: renderHl
  }, reason);
}

// #endregion

// #region Хило: анимация
// ---------- анимация ----------
// Новая карта переворачивается; ход подсвечивается (угадал: зелёным, ошибся: красным). Баланс и итог показываются после.
async function animateHl(d, kind) {
  hl.shownCard = d.card;
  if (reducedMotion()) { renderHlCard(false); return; }
  hl.animating = true;
  const tone = kind === 'guess' && (d.card.how === 'win' || d.card.how === 'tie') ? 'win' : (kind === 'guess' && d.card.how === 'lose' ? 'lose' : 'flip');
  renderHlCard(true, tone);
  renderHlHistory();
  await sleep(HL_FLIP_MS + (tone === 'flip' ? 0 : HL_HIT_MS));
  hl.animating = false;
}

// Действие пользователя: блокировка, до 3 попыток с одним request_id, разбор ответа.
// Если результата нет, ничего не угадываем: запрашиваем реальное состояние
async function hlAct(path, body, kind) {
  if (hl.busy) return;
  if (!(tg && tg.initData)) {
    setHlNotice('Откройте игру через бота в Telegram');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    setHlNotice('Ошибка');
    return;
  }
  hl.busy = true;
  setHlNotice('');
  renderHl();
  const result = await postWithRetries(() => postMinesOnce(path, { request_id: id, ...body }, validHlAction));
  let note = 'Состояние обновлено';
  let reload = true;
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      hl.game = d;
      hl.view = d.status === 'active' ? 'play' : 'result';
      await animateHl(d, kind);
      hl.animating = false;
      hl.shownCard = d.card;
      hl.balance = d.balance;                 // баланс в шапке меняется только после анимации
      if (d.status !== 'active') hl.seen.add(hlKey(d));
      hl.busy = false;
      renderHlCard(false);
      renderHl();
      haptic(d.status === 'active' ? 'light' : (d.payout > d.bet ? 'success' : (d.payout === d.bet ? 'light' : 'error')));
      loadServer('after');
      return;
    } catch (e) {
      hl.animating = false;
      note = 'Не удалось показать результат. Состояние обновлено';
    }
  } else {
    ({ note, reload } = actionFailure(result, {
      insufficient_funds: 'Не хватает фишек',
      active_game_exists: 'У вас уже есть начатая партия',
      no_active_game: 'Партия уже закрыта',
      move_forbidden: 'Этот ход сейчас недоступен',
      nothing_to_cash_out: 'Забрать можно после первого угаданного хода',
      request_conflict: 'Запрос уже обработан, обновите экран'
    }));
  }
  if (reload) {
    try {
      hl.animating = false;
      applyHlState(await fetchHlState(), false);
      setHlNotice(note);
    } catch (e) {
      setHlNotice('Нет связи. Состояние партии неизвестно, обновите экран');
    }
  } else {
    setHlNotice(note);
  }
  hl.animating = false;
  hl.busy = false;
  renderHl();
  loadServer('after');
}

function hlStart() {
  const bet = Number(hlEls.bet.value);
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > HL_BET_MAX) {
    setHlNotice('Введите целую ставку от 1 до ' + formatNumber(HL_BET_MAX));
    return;
  }
  if (hl.balance !== null && bet > hl.balance) {
    setHlNotice('Не хватает фишек');
    return;
  }
  hlAct('/api/hilo/start', { bet }, 'start');
}

setupBetPanel({
  input: hlEls.bet,
  maxBtn: hlEls.maxBtn,
  halfBtn: document.getElementById('hl-half'),
  doubleBtn: document.getElementById('hl-double-bet'),
  getLimit: hlBetLimit
});
hlEls.bet.addEventListener('input', syncHlChips);
hlEls.bets.querySelectorAll('[data-hbet]').forEach((b) => b.addEventListener('click', () => {
  hlEls.bet.value = b.dataset.hbet;
  syncHlChips();
  refreshBetPanels();
  haptic('light');
}));
hlEls.start.addEventListener('click', hlStart);
hlEls.hi.addEventListener('click', () => hlAct('/api/hilo/guess', { choice: 'hi' }, 'guess'));
hlEls.lo.addEventListener('click', () => hlAct('/api/hilo/guess', { choice: 'lo' }, 'guess'));
hlEls.skip.addEventListener('click', () => hlAct('/api/hilo/guess', { choice: 'skip' }, 'skip'));
hlEls.cash.addEventListener('click', () => hlAct('/api/hilo/cashout', {}, 'cashout'));
hlEls.retry.addEventListener('click', () => loadHilo('manual'));
hlEls.switchBtn.addEventListener('click', toggleGameMenu);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadHilo('visible');
});

// #endregion

// #region Переводы
// ---------- переводы фишек между участниками беседы ----------
// Получатель выбирается по member_ref из рейтинга беседы (непрозрачная метка, Telegram ID клиент не знает). Лимиты, комиссия и
// минимальный уровень приходят из /api/me (srv.transferLimits), клиент констант не дублирует. Баланс обновляется по ответу сервера.
const trEls = {
  sheet: document.getElementById('transfer-sheet'),
  dim: document.getElementById('transfer-dim'),
  to: document.getElementById('transfer-to'),
  amount: document.getElementById('transfer-amount'),
  maxBtn: document.getElementById('transfer-max'),
  recv: document.getElementById('transfer-recv'),
  limits: document.getElementById('transfer-limits'),
  msg: document.getElementById('transfer-msg'),
  cancel: document.getElementById('transfer-cancel'),
  send: document.getElementById('transfer-send'),
  block: document.getElementById('profile-transfers'),
  list: document.getElementById('transfers-list'),
  empty: document.getElementById('transfers-empty')
};
const tr = { ref: null, name: '', confirm: false, busy: false };
let incomingToastShown = false;      // тост «Вам перевели» один раз за сессию
let trListAt = -Infinity;            // performance.now() последней загрузки истории
let trListBusy = false;

const trAmount = () => {
  const n = Number(trEls.amount.value);
  return Number.isSafeInteger(n) ? n : 0;
};
const trFee = (amount) => {
  const p = srv.transferLimits ? srv.transferLimits.fee_percent : 0;
  return p > 0 ? Math.max(1, Math.floor((amount * p) / 100)) : 0;
};
const trBetLimit = () => {
  const l = srv.transferLimits;
  if (!l) return 1;
  return Math.max(1, Math.min(l.max, srv.loaded ? srv.balance : l.max, l.unlimited ? l.max : l.daily_left));
};

// Ошибка ввода (текст) или пустая строка
function trInputError() {
  const l = srv.transferLimits;
  const a = trAmount();
  if (!l) return 'Переводы пока недоступны, попробуйте позже';
  if (srv.loaded && srv.balance < l.min) return 'Не хватает фишек';
  if (!l.unlimited && l.daily_left < l.min) return 'Суточный лимит исчерпан: осталось ' + formatNumber(l.daily_left);
  if (a < l.min) return 'Сумма от ' + formatNumber(l.min) + ' до ' + formatNumber(l.max);
  if (a > l.max) return 'Не больше ' + formatNumber(l.max) + ' за раз';
  if (srv.loaded && a > srv.balance) return 'Не хватает фишек';
  if (!l.unlimited && a > l.daily_left) return 'Суточный лимит: осталось ' + formatNumber(l.daily_left);
  return '';
}

function renderTransfer() {
  const l = srv.transferLimits;
  const a = trAmount();
  const fee = trFee(a);
  if (l && a >= l.min) {
    trEls.recv.textContent = fee > 0
      ? 'Получит ' + formatNumber(a - fee) + '. Комиссия ' + formatNumber(fee) + ' идёт разработчику'
      : 'Получит ' + formatNumber(a) + ' (без комиссии)';
  } else {
    trEls.recv.textContent = l ? 'Получит: введите сумму' : '';
  }
  trEls.limits.textContent = l
    ? (l.unlimited ? 'Лимит на сегодня: без ограничений' : 'Лимит на сегодня: осталось ' + formatNumber(l.daily_left))
      + '. Мин. уровень ' + l.min_level + '. Пауза ' + l.cooldown_seconds + ' с'
    : '';
  trEls.send.textContent = tr.busy ? 'Отправляем…' : (tr.confirm ? 'Подтвердить' : 'Отправить');
  trEls.send.disabled = tr.busy;
  trEls.cancel.disabled = tr.busy;
  trEls.amount.disabled = tr.busy;
  document.getElementById('transfer-change').disabled = tr.busy;
  trEls.maxBtn.disabled = tr.busy;
  refreshBetPanels();
}

function setTrMsg(text) { trEls.msg.textContent = text; }

function openTransfer(ref, name, keepAmount) {
  tr.ref = ref;
  tr.name = name;
  tr.confirm = false;
  tr.busy = false;
  trEls.to.textContent = name;
  trEls.to.title = name;
  if (!keepAmount || !trEls.amount.value) trEls.amount.value = srv.transferLimits ? String(srv.transferLimits.min) : '';
  setTrMsg(srv.transferLimits ? '' : 'Переводы пока недоступны, попробуйте позже');
  trEls.sheet.hidden = false;
  renderTransfer();
  haptic('light');
}

function closeTransfer() {
  if (tr.busy) return;
  if (document.activeElement === trEls.amount) trEls.amount.blur();
  trEls.sheet.hidden = true;
  tr.ref = null;
  tr.confirm = false;
}

// Понятные тексты ошибок сервера
function trErrorText(detail, seconds) {
  const l = srv.transferLimits;
  return ({
    insufficient_funds: 'Не хватает фишек',
    level_too_low: 'Нужен уровень ' + (l ? l.min_level : 3) + ' или выше',
    account_too_new: 'Аккаунт слишком новый: переводы откроются через ' + (l ? l.min_age_hours : 1) + ' ч после начала игры',
    not_enough_staked: 'Переводы откроются, когда вы поставите в играх не менее ' + formatNumber(l ? l.min_staked : 20000) + ' фишек',
    cooldown: 'Подождите ' + (isCount(seconds) ? seconds : 10) + ' сек. перед следующим переводом',
    daily_limit: 'Суточный лимит переводов исчерпан',
    no_chat: 'Вне беседы переводы недоступны: откройте игру из группового чата',
    not_in_chat: 'Этот игрок сейчас недоступен для перевода',
    recipient_limit: 'У получателя уже слишком много фишек',
    self_transfer: 'Нельзя перевести фишки самому себе',
    invalid_request: 'Проверьте сумму и получателя',
    request_conflict: 'Запрос уже обработан, обновите экран'
  })[detail] || 'Не удалось выполнить перевод';
}

const validTransferSend = (d) => !!d && isCount(d.amount) && isCount(d.fee) && isCount(d.received) && isCount(d.balance) && isCount(d.daily_left);

// Один POST перевода. { kind: 'ok', data } | { kind: 'conflict', detail, seconds } | { kind: 'fatal', text } | { kind: 'retry' } | { kind: 'invalid' }
async function postTransferOnce(payload) {
  try {
    const res = await postJson('/api/transfers/send', payload);
    if (res.status === 401) return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    if (res.status === 400) return { kind: 'conflict', detail: 'invalid_request' };
    if (res.status === 409) {
      const body = await readJsonBody(res);
      const data = body.ok && body.data ? body.data : {};
      return { kind: 'conflict', detail: typeof data.detail === 'string' ? data.detail : '', seconds: data.seconds };
    }
    if (!res.ok) return isServerError(res) ? { kind: 'retry' } : { kind: 'fatal', text: 'Не удалось выполнить перевод' };
    const body = await readJsonBody(res);
    // ответ 2xx не повторяем: перевод уже выполнен сервером; непонятный ответ = повод обновить баланс отдельным запросом
    return body.ok && validTransferSend(body.data) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry' };
  }
}

async function doTransfer() {
  if (tr.busy) return;
  if (!(tg && tg.initData)) { setTrMsg('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setTrMsg('Ошибка'); return; }
  const amount = trAmount();
  tr.busy = true;
  setTrMsg('');
  renderTransfer();
  // повторы с тем же request_id только при сбоях сети, 429 и 5xx
  const result = await postWithRetries(() => postTransferOnce({ request_id: id, member_ref: tr.ref, amount }));
  tr.busy = false;
  if (result && result.kind === 'ok') {
    const d = result.data;
    srv.balance = d.balance;
    srv.loaded = true;
    if (srv.transferLimits) srv.transferLimits.daily_left = d.daily_left;
    trListAt = -Infinity;   // история изменилась
    closeTransfer();
    renderAll();
    showToast('Отправлено');
    haptic('success');
    loadServer('after');
    return;
  }
  tr.confirm = false;
  if (result && result.kind === 'conflict') {
    setTrMsg(trErrorText(result.detail, result.seconds));
    if (result.detail === 'insufficient_funds') loadServer('after');
  } else if (result && result.kind === 'fatal') {
    setTrMsg(result.text);
  } else if (result && result.kind === 'invalid') {
    setTrMsg('Ответ сервера не распознан. Баланс обновлён');
    loadServer('after');
  } else {
    setTrMsg('Нет связи. Баланс обновлён, проверьте, прошёл ли перевод');
    loadServer('after');
  }
  renderTransfer();
}

function trSendClick() {
  if (tr.busy) return;
  const err = trInputError();
  if (err) { setTrMsg(err); tr.confirm = false; renderTransfer(); return; }
  if (!tr.confirm) {
    tr.confirm = true;
    setTrMsg('Отправить ' + formatNumber(trAmount()) + ' игроку ' + tr.name + '?');
    renderTransfer();
    return;
  }
  doTransfer();
}

setupBetPanel({
  input: trEls.amount,
  maxBtn: trEls.maxBtn,
  halfBtn: document.getElementById('transfer-half'),
  doubleBtn: document.getElementById('transfer-double'),
  getLimit: trBetLimit
});
trEls.amount.addEventListener('input', () => { tr.confirm = false; setTrMsg(''); renderTransfer(); });
// ÷2, ×2 и «Макс» меняют сумму: подтверждение сбрасывается
['transfer-half', 'transfer-double', 'transfer-max'].forEach((id) => document.getElementById(id).addEventListener('click', () => {
  tr.confirm = false;
  setTrMsg('');
  renderTransfer();
}));
trEls.send.addEventListener('click', trSendClick);
trEls.cancel.addEventListener('click', closeTransfer);
closeOnBackdropTap(trEls.dim, closeTransfer);


// #endregion

// #region Выбор получателя
// ---------- выбор получателя среди всех участников беседы ----------
// GET /api/chat/members?q=&offset=: по 30 имён с подгрузкой при прокрутке, поиск с дебаунсом 300 мс. В ответе только имя и метка.
const PICKER_DEBOUNCE_MS = 300;
const pkEls = {
  sheet: document.getElementById('picker-sheet'),
  dim: document.getElementById('picker-dim'),
  panel: document.getElementById('picker-panel'),
  search: document.getElementById('picker-search'),
  list: document.getElementById('picker-list'),
  msg: document.getElementById('picker-msg'),
  retry: document.getElementById('picker-retry'),
  close: document.getElementById('picker-close'),
  newBtn: document.getElementById('transfer-new'),
  newNote: document.getElementById('transfer-new-note')
};
const pk = { next: null, loading: false, gen: 0, timer: null, from: 'profile' };

const validMembers = (d) => !!d && Array.isArray(d.items) && d.items.every((i) => i && typeof i.name === 'string'
  && typeof i.member_ref === 'string' && /^[0-9a-f]{32}$/.test(i.member_ref))
  && (d.next_offset === null || isCount(d.next_offset));

// «Новый перевод» в профиле: вне беседы кнопка недоступна, причина написана
function renderTransferEntry() {
  pkEls.newBtn.disabled = srv.noChat;
  pkEls.newNote.hidden = !srv.noChat;
  pkEls.newNote.textContent = srv.noChat ? 'Вне беседы переводы недоступны: откройте игру из группового чата' : '';
}

function fitPicker() {
  // с клавиатурой панель не выше видимой области
  const vv = window.visualViewport;
  const kb = !pkEls.sheet.hidden && document.activeElement === pkEls.search && vv;
  pkEls.panel.style.setProperty('--picker-h', kb ? Math.max(200, Math.min(460, vv.height - 24)) + 'px' : '');
}
if (window.visualViewport) window.visualViewport.addEventListener('resize', fitPicker);

function setPkMsg(text, retry) {
  pkEls.msg.textContent = text;
  pkEls.retry.hidden = !retry;
}

function renderPicker(items, append) {
  if (!append) pkEls.list.textContent = '';
  items.forEach((m) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    const av = document.createElement('span');
    av.className = 'avatar';
    av.setAttribute('aria-hidden', 'true');
    av.textContent = initialOf(m.name);
    const nm = document.createElement('span');
    nm.className = 'p-name';
    nm.textContent = m.name;
    btn.append(av, nm);
    btn.addEventListener('click', () => {
      const keep = pk.from === 'transfer';
      closePicker(false);
      openTransfer(m.member_ref, m.name, keep);
    });
    li.appendChild(btn);
    pkEls.list.appendChild(li);
  });
}

async function loadMembers(reset) {
  if (!(tg && tg.initData)) { setPkMsg('Откройте игру через бота в Telegram', false); return; }
  if (reset) { pk.gen += 1; pk.next = null; pk.loading = false; }
  if (pk.loading || (!reset && pk.next === null)) return;
  const gen = pk.gen;
  const offset = reset ? 0 : pk.next;
  pk.loading = true;
  setPkMsg(reset ? 'Загрузка…' : '', false);
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/chat/members?q=' + encodeURIComponent(pkEls.search.value.trim().slice(0, 32)) + '&offset=' + offset,
      { method: 'GET', headers: { Authorization: 'tma ' + tg.initData }, cache: 'no-store', signal: ctrl.signal });
    if (gen !== pk.gen) return;   // пока шёл запрос, поиск изменился
    if (res.status === 409) {
      const body = await readJsonBody(res);
      const detail = body.ok && body.data ? body.data.detail : '';
      if (detail === 'no_chat') {
        srv.noChat = true;
        renderTransferEntry();
        pkEls.list.textContent = '';
        setPkMsg('Вне беседы переводы недоступны: откройте игру из группового чата', false);
      } else {
        pkEls.list.textContent = '';
        setPkMsg('Вас нет среди участников этой беседы: откройте игру из беседы ещё раз', false);
      }
      return;
    }
    if (!res.ok) throw new Error('status');
    const d = await res.json();
    if (gen !== pk.gen) return;
    if (!validMembers(d)) throw new Error('shape');
    srv.noChat = false;
    renderTransferEntry();
    renderPicker(d.items, !reset);
    pk.next = d.next_offset;
    setPkMsg(pkEls.list.children.length === 0 ? 'Никого не найдено' : '', false);
  } catch (e) {
    if (gen === pk.gen) setPkMsg('Не удалось загрузить список. Проверьте связь', true);
  } finally {
    clearTimeout(timeout);
    if (gen === pk.gen) pk.loading = false;
  }
}

function openPicker(from) {
  if (srv.noChat) return;
  pk.from = from;
  if (from === 'transfer') trEls.sheet.hidden = true;
  pkEls.search.value = '';
  pkEls.list.textContent = '';   // прошлый список не показывается, пока грузится новый
  pkEls.sheet.hidden = false;
  pkEls.list.scrollTop = 0;
  loadMembers(true);
}

function closePicker(restore = true) {
  if (document.activeElement === pkEls.search) pkEls.search.blur();
  pkEls.sheet.hidden = true;
  if (restore && pk.from === 'transfer' && tr.ref) trEls.sheet.hidden = false;   // отмена выбора возвращает панель перевода
  clearTimeout(pk.timer);
  pk.gen += 1;
  pk.loading = false;
  fitPicker();
}

pkEls.search.addEventListener('input', () => {
  clearTimeout(pk.timer);
  pk.timer = setTimeout(() => loadMembers(true), PICKER_DEBOUNCE_MS);
});
pkEls.search.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); pkEls.search.blur(); } });
pkEls.search.addEventListener('focus', () => { dockPanel(pkEls.panel); fitPicker(); });
pkEls.search.addEventListener('blur', () => { undockPanel(pkEls.panel); setTimeout(fitPicker, 0); });
pkEls.list.addEventListener('scroll', () => {
  if (pkEls.list.scrollTop + pkEls.list.clientHeight >= pkEls.list.scrollHeight - 80) loadMembers(false);
});
pkEls.retry.addEventListener('click', () => loadMembers(true));
pkEls.close.addEventListener('click', closePicker);
closeOnBackdropTap(pkEls.dim, closePicker);
pkEls.newBtn.addEventListener('click', () => openPicker('profile'));
document.getElementById('transfer-change').addEventListener('click', () => { if (!tr.busy) openPicker('transfer'); });
renderTransferEntry();

// #endregion

// #region История переводов
// ---------- история переводов в профиле ----------
const validTransferList = (d) => !!d && Array.isArray(d.items) && d.items.every((i) => i && (i.direction === 'in' || i.direction === 'out')
  && typeof i.name === 'string' && isCount(i.amount) && isCount(i.fee) && isCount(i.time));

function trTimeText(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getDate()) + '.' + p(d.getMonth() + 1) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function renderTransferList(items) {
  trEls.list.textContent = '';
  trEls.empty.hidden = items.length > 0;
  items.forEach((i) => {
    const li = document.createElement('li');
    const who = document.createElement('span');
    who.className = 't-who';
    who.textContent = (i.direction === 'out' ? 'Вы → ' : '') + i.name + (i.direction === 'in' ? ' → вам' : '');
    const sum = document.createElement('span');
    sum.className = 't-sum' + (i.direction === 'in' ? ' in' : '');
    const shown = i.direction === 'out' ? i.amount : i.amount - i.fee;
    sum.textContent = (i.direction === 'out' ? '−' : '+') + formatCompact(shown);
    sum.title = formatNumber(shown);
    const sub = document.createElement('span');
    sub.className = 't-sub';
    sub.textContent = trTimeText(i.time) + ', комиссия ' + formatNumber(i.fee);
    li.append(who, sum, sub);
    trEls.list.appendChild(li);
  });
  trEls.block.hidden = false;
}

// reason: 'open' (профиль, не чаще раза в 10 секунд), 'toast' (после тоста о входящем) или 'manual'. Запрос помечает входящие просмотренными
async function loadTransfers(reason) {
  if (trListBusy || !(tg && tg.initData)) return;
  if (reason === 'open' && performance.now() - trListAt < REFRESH_MIN_MS) return;
  trListBusy = true;
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/transfers', { method: 'GET', headers: { Authorization: 'tma ' + tg.initData }, cache: 'no-store', signal: ctrl.signal });
    if (!res.ok) return;
    const d = await res.json();
    if (!validTransferList(d)) return;
    trListAt = performance.now();
    renderTransferList(d.items);
    if (srv.incomingUnseen) srv.incomingUnseen = { count: 0, total: 0 };
  } catch (e) {
    // история необязательна: при сбое раздел просто не обновляется
  } finally {
    clearTimeout(timeout);
    trListBusy = false;
  }
}

// Входящие переводы при запуске: один тост за сессию, затем запрос истории (он помечает их просмотренными)
function notifyIncoming() {
  const inc = srv.incomingUnseen;
  if (incomingToastShown || !inc || inc.count < 1) return;
  incomingToastShown = true;
  showToast('Вам перевели ' + formatNumber(inc.total) + ' фишек');
  loadTransfers('toast');
}

// #endregion

// #region Лобби, меню игр и вкладки
// ---------- титульный экран ----------
// Баланс и уровень берутся из общего серверного состояния (srv), новых запросов нет. Карточки игр строятся из реестра GAMES.
const lobbyEls = {
  root: document.querySelector('.screen.lobby'),
  balance: document.getElementById('lobby-balance'),
  level: document.getElementById('lobby-level'),
  grid: document.getElementById('lobby-grid')
};

function renderLobby() {
  if (srv.loaded) {
    lobbyEls.balance.classList.remove('skeleton');
    lobbyEls.balance.textContent = spacedNumber(availableBalance());
  } else {
    lobbyEls.balance.textContent = srv.error ? '—' : 'Загрузка…';
  }
  fitNumberFont(lobbyEls.balance, lobbyEls.balance.textContent.length);
  lobbyEls.level.hidden = srv.level === null || !srv.loaded;
  if (srv.level !== null) lobbyEls.level.textContent = 'Ур. ' + srv.level;
  // пока не пришёл первый ответ, содержимое скрыто: если есть незавершённая игра, титульный экран не мелькает
  lobbyEls.root.classList.toggle('booting', !(srv.loaded || srv.error));
}
lobbyRender = renderLobby;

// Реестр игр: чтобы добавить игру, нужна запись здесь и экран с data-screen="<id>".
// Порядок записей = порядок карточек на титульном экране и пунктов меню (два столбца: слева направо, сверху вниз).
// Для ready: false игра не открывается: нажатие показывает «Скоро».
// Иконка — вложенный SVG (24×24, контур)
const GAMES = [
  { id: 'crash',     label: 'Краш',      hint: 'Забери вовремя', desc: 'Успей забрать до краха', ready: true, icon: '<path d="M3 20h18M4 16l5-5 4 3 7-8M15 6h5v5"/>' },
  { id: 'roulette',  label: 'Рулетка',   hint: 'Угадай цвет', desc: 'Классическая европейская рулетка', ready: true,  icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>' },
  { id: 'keno',      label: 'Кено',      hint: 'Угадай числа', desc: 'Выбери числа и жди розыгрыш', ready: true, icon: '<circle cx="6" cy="6" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18" cy="12" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>' },
  { id: 'mines',     label: 'Мины',      hint: 'Обойди мины', desc: 'Открывай клетки и забирай выигрыш', ready: true, icon: '<circle cx="11" cy="14" r="7"/><path d="M16 9l3-3M18 4l2 2M11 3v2M4 14H2M20 14h2"/>' },
  { id: 'hilo',      label: 'Хило',      hint: 'Выше или ниже?', desc: 'Угадай: выше или ниже карта', ready: true,  icon: '<rect x="3" y="4" width="8" height="12" rx="2"/><rect x="13" y="8" width="8" height="12" rx="2"/><path d="M7 7.5v5M5.2 9.3 7 7.5l1.8 1.8M17 11.5v5M15.2 14.7 17 16.5l1.8-1.8"/>' },
  { id: 'blackjack', label: 'Блэкджек',  hint: 'Набери 21', desc: 'Набери 21', ready: true, icon: '<rect x="4" y="6" width="11" height="15" rx="2"/><path d="M9 3h9a2 2 0 0 1 2 2v12"/>' }
];
// Пока игра не выбрана (currentGame = 'lobby'), вкладка «Играть» показывает титульный экран. После выбора он не возвращается
// до следующего запуска; выбранная игра хранится только в памяти.
let currentGame = 'lobby';

// Нижняя панель: названия и иконки меняются здесь. Иконка — вложенный SVG (24×24, контур).
// Иконка центральной кнопки: нейтральная, пока игра не выбрана; после выбора подменяется иконкой открытой игры.
const TABS = [
  { id: 'rating',  label: 'Рейтинг', icon: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3"/>' },
  { id: 'style',   label: 'Стиль',   icon: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/><path d="M19 15v4M17 17h4"/>' },
  { id: 'play',    label: 'Играть',  icon: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>', main: true },
  { id: 'farm',    label: 'Ферма',   icon: '<path d="M7 20h10"/><path d="M10 20c5.5-2.5.8-6.4 3-10"/><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z"/><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z"/>' },
  { id: 'profile', label: 'Профиль', icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>' }
];
const START_TAB = 'play';
const navEl = document.getElementById('nav');

const gameMenu = document.getElementById('game-menu');
const gamePanel = document.getElementById('game-panel');
const gameGrid = document.getElementById('game-grid');
let activeTab = START_TAB;

const iconSvg = (path) => `<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
const getGame = (id) => GAMES.find((g) => g.id === id);

// Экраны только прячутся и показываются, игровые элементы не пересоздаются.
// Вкладка «Играть» показывает экран выбранной игры.
function showTab(id) {
  activeTab = id;
  const screen = id === 'play' ? currentGame : id;
  document.querySelectorAll('[data-screen]').forEach((el) => { el.hidden = el.dataset.screen !== screen; });
  closeGameMenu();
  if (started && (screen === 'profile' || screen === 'roulette' || screen === 'lobby')) loadServer('open');
  if (started && screen === 'profile') loadTransfers('open');
  if (started && screen === 'rating') loadRating('open');
  if (started && screen === 'farm') loadFarm('open');
  if (started && screen === 'mines') loadMines('open');
  if (started && screen === 'blackjack') loadBj('open');
  if (started && screen === 'crash') loadCrash('open');
  if (started && screen === 'hilo') loadHilo('open');
  if (started && screen === 'keno') {
    loadServer('open');
    loadKenoPay();
    renderKeno();
  }
  navEl.querySelectorAll('.tab').forEach((btn) => {
    if (btn.dataset.tab === id) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
}

TABS.forEach((tab) => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'tab' + (tab.main ? ' main' : '');
  btn.dataset.tab = tab.id;
  btn.innerHTML = `${iconSvg(tab.icon)}<span>${tab.label}</span>`;
  btn.addEventListener('click', () => {
    // повторное нажатие на «Играть» открывает и закрывает меню игр (в том числе поверх титульного экрана)
    if (tab.id === 'play' && activeTab === 'play') toggleGameMenu();
    else showTab(tab.id);
  });
  navEl.appendChild(btn);
});

// Меню игр: нельзя открыть, пока колесо на экране (вращение и пауза после него)
function closeGameMenu() {
  gameMenu.classList.remove('open');
  gamePanel.classList.remove('dragging');
  gamePanel.style.transform = '';
  navEl.querySelector('.tab.main').setAttribute('aria-expanded', 'false');
}

function toggleGameMenu() {
  if (gameMenu.classList.contains('open')) {
    closeGameMenu();
    return;
  }
  if (wheelLayer.classList.contains('active') || gameBusy()) return;
  gameMenu.classList.add('open');
  navEl.querySelector('.tab.main').setAttribute('aria-expanded', 'true');
}

function selectGame(id) {
  if (!getGame(id).ready) { showSoon(); return; }
  currentGame = id;
  navEl.querySelector('.tab.main svg').outerHTML = iconSvg(getGame(id).icon);
  // кнопка смены игры есть в шапке рулетки и в шапке мин; разметка иконки постоянная, из реестра
  document.querySelectorAll('.switch-icon').forEach((el) => { el.innerHTML = iconSvg(getGame(id).icon); });
  document.querySelectorAll('.switch-name').forEach((el) => { el.textContent = getGame(id).label; });
  gamePanel.querySelectorAll('.tile').forEach((t) => {
    t.setAttribute('aria-current', String(t.dataset.game === id));
  });
  showTab('play');
}

// Короткое сообщение «Скоро» для игр-заглушек (игра не открывается)
function showSoon() {
  showToast('Скоро');
}

// Плитки меню и карточки титульного экрана строятся из реестра GAMES
GAMES.forEach((game, i) => {
  const tile = document.createElement('button');
  tile.type = 'button';
  tile.className = 'tile';
  tile.dataset.game = game.id;
  tile.style.setProperty('--i', i);
  tile.setAttribute('role', 'menuitem');
  tile.dataset.soon = String(!game.ready);
  tile.innerHTML = `${iconSvg(game.icon)}<span class="tile-name">${game.label}</span><span class="tile-hint">${game.hint || ''}</span>`;
  tile.addEventListener('click', () => selectGame(game.id));
  gameGrid.appendChild(tile);

  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'lobby-card';
  card.dataset.game = game.id;
  card.dataset.soon = String(!game.ready);
  card.setAttribute('role', 'menuitem');
  card.innerHTML = `${iconSvg(game.icon)}<span class="lobby-name"></span><span class="lobby-desc"></span>`;
  card.querySelector('.lobby-name').textContent = game.label;
  card.querySelector('.lobby-desc').textContent = game.desc || '';
  card.addEventListener('click', () => selectGame(game.id));
  lobbyEls.grid.appendChild(card);
});
renderLobby();

gameMenu.addEventListener('click', (e) => {
  if (e.target === gameMenu) closeGameMenu(); // нажатие по затемнению
});
gameSwitchEl.addEventListener('click', toggleGameMenu);

// Шторка закрывается свайпом вниз
(() => {
  let startY = null;
  let dy = 0;
  gamePanel.addEventListener('touchstart', (e) => {
    startY = e.touches[0].clientY;
    dy = 0;
  }, { passive: true });
  gamePanel.addEventListener('touchmove', (e) => {
    if (startY === null) return;
    dy = Math.max(0, e.touches[0].clientY - startY);
    gamePanel.classList.add('dragging');
    gamePanel.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  const end = () => {
    if (startY === null) return;
    const far = dy > 80;
    startY = null;
    gamePanel.classList.remove('dragging');
    gamePanel.style.transform = '';
    if (far) closeGameMenu();
  };
  gamePanel.addEventListener('touchend', end);
  gamePanel.addEventListener('touchcancel', end);
})();
// #endregion

// #region Запуск
// ---------- запуск ----------
showTab('play');   // титульный экран; если у игрока есть незавершённая игра, её откроет ответ /api/me

loadState();
drawWheel();
ballRadius = BALL_POCKET;
renderSpin();
window.addEventListener('resize', renderSpin);
buildTable();
renderHistory();
spinBtn.addEventListener('click', spin);
document.getElementById('clear-bets').addEventListener('click', clearBets);
document.getElementById('repeat-bets').addEventListener('click', repeatBets);
const syncChips = () => {
  betsPanel.querySelectorAll('.chip').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(Number(amountEl.value) === Number(btn.dataset.amount)));
  });
};
betsPanel.querySelectorAll('.chip').forEach((btn) => {
  btn.addEventListener('click', () => {
    amountEl.value = btn.dataset.amount;
    syncChips();
    haptic('light');
  });
});
setupBetPanel({
  input: amountEl,
  maxBtn: document.getElementById('amount-max'),
  halfBtn: document.getElementById('amount-half'),
  doubleBtn: document.getElementById('amount-double'),
  getLimit: rouletteBetLimit
});
amountEl.addEventListener('input', syncChips);
syncChips();

// шрифты грузятся локально: после загрузки колесо перерисовывается (числа на секторах)
if (document.fonts && document.fonts.load) {
  document.fonts.load('700 28px "Playfair Display"').then(drawWheel, () => {});
}

// всё собрано: показываем состояние и загружаем баланс с сервера
started = true;
renderAll();
loadServer('open');

// #endregion