// Адрес сервера с балансом. Менять только здесь.
const API_URL = 'https://roulette-production-4b93.up.railway.app';

// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

// цвета секторов колеса: те же, что у токенов --red, --surface-2, --green в style.css
const COLOR_HEX = { red: '#B3262B', black: '#17171B', green: '#1E9E4A' };

// числа с разделителем тысяч («1 300»); если Intl недоступен, без него
const formatNumber = (() => {
  try {
    const f = new Intl.NumberFormat('ru-RU');
    return (n) => f.format(n);
  } catch (e) {
    return (n) => String(n);
  }
})();

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
function drawWheel() {
  const size = canvas.width;
  const cx = size / 2;
  const cy = size / 2;
  const outer = size / 2;       // внешний край обода
  const radius = outer - 22;    // внешний край секторов — обод одинаковой толщины со всех сторон
  const rad = (deg) => (deg * Math.PI) / 180;

  ctx.clearRect(0, 0, size, size);

  // Обод рисуем прямо на колесе: он вращается вместе с ним и везде одинаков
  const rim = ctx.createRadialGradient(cx, cy, radius, cx, cy, outer);
  rim.addColorStop(0, '#55555B');
  rim.addColorStop(0.5, '#F5F5F2');
  rim.addColorStop(1, '#8A8A90');
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
    ctx.fillStyle = COLOR_HEX[getColor(num)];
    ctx.fill();
    ctx.strokeStyle = 'rgba(245, 245, 242, 0.35)';
    ctx.lineWidth = 2;
    ctx.stroke();

    // подпись числа: поворачиваем холст к центру сектора и рисуем у края
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(i * SECTOR));
    ctx.fillStyle = '#F5F5F2';
    ctx.font = 'bold 28px "Playfair Display", Georgia, serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(num), 0, -radius + 34);
    ctx.restore();
  });

  // тонкая линия по границе секторов и обода
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = '#050506';
  ctx.lineWidth = 3;
  ctx.stroke();

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = '#0F0F12';
  ctx.fill();
  ctx.strokeStyle = 'rgba(245, 245, 242, 0.5)';
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

// Размер шрифта по длине записи: длинные числа (до 16 цифр) не должны выталкивать соседей за край экрана
function fitNumberFont(el, length) {
  el.dataset.len = length <= 9 ? 'l' : length <= 13 ? 'm' : length <= 17 ? 's' : 'xs';
}

// число баланса с обычными пробелами: очень длинное можно перенести по группам цифр, а не посреди группы
const spacedNumber = (n) => formatNumber(n).replace(/[\u00a0\u202f]/g, ' ');

function renderBalance() {
  stopBalanceAnimation(); // промежуточные кадры накрутки не должны перебивать актуальное значение
  const loading = !srv.loaded && !srv.error;
  balanceEl.classList.toggle('skeleton', loading);
  if (srv.loaded) balanceEl.textContent = spacedNumber(availableBalance());
  else balanceEl.textContent = srv.error ? '—' : 'Загрузка…';
  fitNumberFont(balanceEl, balanceEl.textContent.length);
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

const mmss = (sec) => {
  const left = Math.max(0, Math.ceil(sec));
  return String(Math.floor(left / 60)).padStart(2, '0') + ':' + String(left % 60).padStart(2, '0');
};

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

// Блокирует/разблокирует ставки, кнопку «Крутить» и нижнюю панель
function updateControls() {
  const ready = !!(tg && tg.initData) && srv.loaded;
  const canBet = ready && !gameBusy();
  const canAdd = canBet && availableBalance() > 0;
  const loadRetry = !srv.loaded && srv.error && srv.error.retry;
  document.querySelectorAll('#table button').forEach((el) => { el.disabled = !canAdd; });
  document.querySelectorAll('#bets .chip, #bets input, #repeat-bets, #clear-bets').forEach((el) => { el.disabled = !canBet; });
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

// ---------- ставка через сервер ----------
// Сервер сам выбирает число и считает выигрыш. Клиент шлёт только request_id и ставки.
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

// Один POST. Возвращает { kind: 'ok', data } | { kind: 'fatal', text, code, refresh } | { kind: 'retry', code }
async function postRound(round) {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/roulette/spin', {
      method: 'POST',
      headers: { Authorization: 'tma ' + tg.initData, 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: round.id, bets: round.bets }),
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', code: '401' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Ошибка ставок', code: '400' };
    if (res.status === 409) {
      let detail = '';
      try { detail = (await res.json()).detail; } catch (e) { detail = ''; }
      if (detail === 'insufficient_funds') return { kind: 'fatal', text: 'Недостаточно фишек', code: '409', refresh: true };
      if (detail === 'balance_limit') return { kind: 'fatal', text: 'Достигнут максимальный баланс', code: '409' };
      return { kind: 'retry', code: '409' };
    }
    if (!res.ok) return { kind: 'retry', code: String(res.status) };
    const d = await res.json();
    const valid = d && isInt(d.number) && d.number >= 0 && d.number <= 36
      && isInt(d.stake_total) && d.stake_total >= 1 && isInt(d.payout_total) && d.payout_total >= 0
      && d.net === d.payout_total - d.stake_total && isInt(d.balance) && d.balance >= 0;
    return valid ? { kind: 'ok', data: d } : { kind: 'retry', code: 'ответ' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  } finally {
    clearTimeout(timeout);
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

// ---------- серверное состояние: общее для экрана игры и вкладки «Профиль» ----------
// Баланс только серверный и хранится в памяти страницы, в localStorage он не пишется.
const REQUEST_TIMEOUT_MS = 10000; // таймаут запроса
const REFRESH_MIN_MS = 10000;     // обновление при открытии экрана и возврате в приложение
const REQUEST_GAP_MS = 5000;      // любые два запроса /api/me не чаще, чем раз в 5 секунд
const ERROR_RETRY_MS = 30000;     // после ошибки автоповтор не чаще, чем раз в 30 секунд
const ZERO_DELAY_MS = 1000;       // пауза после нуля таймера перед новым запросом

const srv = {
  loaded: false,   // получен ли хотя бы один ответ
  balance: 0,
  rate: 0,
  deadline: 0,     // performance.now(), когда таймер дойдёт до нуля
  error: null      // { text, code, retry } последней неудачной загрузки
};

const profileEls = {
  data: document.getElementById('profile-data'),
  balance: document.getElementById('profile-balance'),
  rate: document.getElementById('profile-rate'),
  timer: document.getElementById('profile-timer'),
  ring: document.getElementById('profile-ring'),
  skel: document.getElementById('profile-skel'),
  name: document.getElementById('profile-name'),
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
const srvWanted = () => activeTab === 'profile' || (activeTab === 'play' && currentGame === 'roulette');

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

// Кольцо до следующего начисления: фишки приходят раз в час
const ACCRUAL_PERIOD_S = 3600;
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

function renderAll() {
  renderProfile();
  renderBets();
}

// Раз в секунду обновляем таймеры (по монотонным часам, а не по часам устройства)
setInterval(() => {
  if (!started) return;
  if (srv.loaded && !srv.error) renderProfileTimer();
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
  srvLastFailed = true;
  srv.error = { text, code, retry };
  renderAll();
  if (retry) scheduleServerFetch(ERROR_RETRY_MS);
}

// Единственная функция запроса /api/me. reason: 'open' | 'visible' (с ограничением по частоте)
// | 'timer' | 'manual' | 'after' (ручной запрос и запрос после раунда не теряются: откладываются)
async function loadServer(reason) {
  if (srvInFlight || !srvWanted()) return;
  if (gameBusy()) {
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
  if ((reason === 'open' || reason === 'visible') && sinceLast < (srvLastFailed ? ERROR_RETRY_MS : REFRESH_MIN_MS)) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    failServer('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(srvFetchTimer);
  srvInFlight = true;
  srvLastRequestAt = now;
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
    if (!d || !ok(d.balance, 1e12) || !ok(d.rate, 1e9) || !ok(d.seconds_to_next, 86400)) {
      failServer('Нет связи с сервером', 'ответ', true);
      return;
    }
    if (gameBusy()) {
      // пока шёл запрос, начался раунд: этот ответ уже мог устареть
      scheduleServerFetch(1000);
      return;
    }
    srvLastFailed = false;
    srv.error = null;
    srv.loaded = true;
    srv.balance = d.balance;
    srv.rate = d.rate;
    srv.deadline = performance.now() + d.seconds_to_next * 1000;
    renderAll();
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

const isCount = (v) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;

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
const CROWN_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/></svg>';

function showRating(d) {
  ratingEls.skel.hidden = true;
  ratingEls.msg.textContent = '';
  ratingEls.code.textContent = '';
  ratingEls.retry.hidden = true;
  ratingHasData = true;
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
    const bal = document.createElement('span');
    bal.className = 'rating-bal';
    bal.textContent = formatNumber(e.balance);
    li.append(rank, avatar, name, bal);
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
  const myBal = document.createElement('span');
  myBal.className = 'rating-bal';
  myBal.textContent = formatNumber(d.me.balance);
  const mySub = document.createElement('span');
  mySub.className = 'rating-staked';
  mySub.textContent = `${d.me.rank}-е место из ${d.me.total}` + (isCount(d.me.staked) ? `, поставлено ${formatNumber(d.me.staked)}` : '');
  me.append(myRank, myAvatar, myName, myBal, mySub);
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

// Реестр игр: чтобы добавить игру, нужна запись здесь и экран с data-screen="<id>".
// Для ready: false экран-заглушка «Скоро» создаётся автоматически.
// Иконка — вложенный SVG (24×24, контур)
const GAMES = [
  { id: 'roulette',  label: 'Рулетка',   hint: 'Угадай цвет', ready: true,  icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>' },
  { id: 'crash',     label: 'Краш',      hint: 'Забери вовремя', ready: false, icon: '<path d="M3 20h18M4 16l5-5 4 3 7-8M15 6h5v5"/>' },
  { id: 'blackjack', label: 'Блэкджек',  hint: 'Набери 21', ready: false, icon: '<rect x="4" y="6" width="11" height="15" rx="2"/><path d="M9 3h9a2 2 0 0 1 2 2v12"/>' },
  { id: 'mines',     label: 'Мины',      hint: 'Обойди мины', ready: false, icon: '<circle cx="11" cy="14" r="7"/><path d="M16 9l3-3M18 4l2 2M11 3v2M4 14H2M20 14h2"/>' },
  { id: 'keno',      label: 'Кено',      hint: 'Угадай числа', ready: false, icon: '<circle cx="6" cy="6" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18" cy="12" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>' }
];
const START_GAME = 'roulette';
let currentGame = START_GAME;

// Нижняя панель: названия и иконки меняются здесь. Иконка — вложенный SVG (24×24, контур).
// Иконка центральной кнопки подменяется иконкой открытой игры.
const TABS = [
  { id: 'rating',  label: 'Рейтинг', icon: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3"/>' },
  { id: 'play',    label: 'Играть',  icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>', main: true },
  { id: 'profile', label: 'Профиль', icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>' }
];
const START_TAB = 'play';
const navEl = document.getElementById('nav');

const shellEl = document.querySelector('.shell');
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
  if (started && (screen === 'profile' || screen === 'roulette')) loadServer('open');
  if (started && screen === 'rating') loadRating('open');
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
    // повторное нажатие на «Играть» открывает и закрывает меню игр
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
  currentGame = id;
  navEl.querySelector('.tab.main svg').outerHTML = iconSvg(getGame(id).icon);
  document.getElementById('game-switch-icon').innerHTML = iconSvg(getGame(id).icon); // постоянная разметка из реестра
  document.getElementById('game-switch-name').textContent = getGame(id).label;
  gamePanel.querySelectorAll('.tile').forEach((t) => {
    t.setAttribute('aria-current', String(t.dataset.game === id));
  });
  showTab('play');
}

// Плитки меню и экраны-заглушки строятся из реестра GAMES
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

  if (!game.ready) {
    const stub = document.createElement('section');
    stub.className = 'screen stub';
    stub.dataset.screen = game.id;
    stub.hidden = true;
    stub.innerHTML = `<h2>${game.label}</h2><span>Скоро</span>`;
    shellEl.insertBefore(stub, wheelLayer);
  }
});
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
selectGame(START_GAME);

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
