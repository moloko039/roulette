// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

const COLOR_HEX = { red: '#c62828', black: '#111111', green: '#1b8f3a' };

const SECTOR = 360 / WHEEL_ORDER.length; // угол одного сектора
const SPIN_TIME_MS = 7000;               // сколько длится вращение колеса и шарика

const canvas = document.getElementById('wheel');
const ctx = canvas.getContext('2d');
const spinBtn = document.getElementById('spin');
const numberEl = document.getElementById('result-number');

const START_BALANCE = 1000;
const PAYOUT = { red: 1, black: 1, even: 1, odd: 1, dozen: 2, column: 2, number: 35 }; // выплата «N к 1»
const DOZEN_NAME = { 1: '1–12', 2: '13–24', 3: '25–36' };
// ряды стола: 1 — верхний (3, 6, 9…), 2 — средний (2, 5, 8…), 3 — нижний (1, 4, 7…)
const rowOf = (n) => 3 - ((n - 1) % 3);

const balanceEl = document.getElementById('balance');
const amountEl = document.getElementById('amount');
const tableEl = document.getElementById('table');
const messageEl = document.getElementById('message');
const loanOverlay = document.getElementById('loan');
const loanBtn = document.getElementById('loan-btn');
const LOAN_AMOUNT = 1000;
const betsPanel = document.getElementById('bets');

let wheelAngle = 0;  // поворот колеса, градусы (по часовой от верха)
let ballRel = 0;     // положение шарика относительно колеса, градусы
let ballRadius = 0;  // расстояние шарика от центра, в долях радиуса колеса

const ballEl = document.getElementById('ball');
const wheelBox = document.querySelector('.wheel-box');
const wheelLayer = document.getElementById('wheel-layer');
const layerResultEl = document.getElementById('layer-result');
const RESULT_HOLD_MS = 1500; // сколько колесо остаётся на экране после остановки
const BALL_TRACK = 0.955;  // радиус, по которому шарик катится по ободу
const BALL_POCKET = 0.885; // радиус, на котором он лежит в ячейке
let balance = START_BALANCE;
let bets = []; // { type, value, amount }
let lastBets = []; // ставки предыдущего раунда — для кнопки «Повторить»

const HISTORY_SIZE = 10;
const historyEl = document.getElementById('history-list');
const appEl = document.querySelector('.app');
const balanceBox = document.querySelector('.balance');
let spinHistory = []; // последние выпавшие числа, новое — первым

const STORAGE_KEY = 'depnaya-state';

// Сохраняем состояние после раунда: баланс, выпавшие числа и ставки последнего раунда
function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ balance, spinHistory, lastBets }));
  } catch (e) {
    // localStorage может быть недоступен — игра просто работает без сохранения
  }
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (!saved) return;
    if (Number.isInteger(saved.balance) && saved.balance >= 0) balance = saved.balance;
    if (Array.isArray(saved.spinHistory)) {
      spinHistory = saved.spinHistory.filter((n) => WHEEL_ORDER.includes(n)).slice(0, HISTORY_SIZE);
    }
    if (Array.isArray(saved.lastBets)) {
      lastBets = saved.lastBets.filter(
        (b) => b && b.type in PAYOUT && Number.isInteger(b.amount) && b.amount > 0
      );
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
  rim.addColorStop(0, '#1c7f99');
  rim.addColorStop(0.5, '#a8f0ff');
  rim.addColorStop(1, '#3fd0e8');
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
    ctx.strokeStyle = '#3fd0e8';
    ctx.lineWidth = 2;
    ctx.stroke();

    // подпись числа: поворачиваем холст к центру сектора и рисуем у края
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(i * SECTOR));
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 28px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(num), 0, -radius + 34);
    ctx.restore();
  });

  // тонкая линия по границе секторов и обода
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = '#0b121c';
  ctx.lineWidth = 3;
  ctx.stroke();

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = '#0b121c';
  ctx.fill();
  ctx.strokeStyle = '#3fd0e8';
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

function renderBalance() {
  balanceEl.textContent = balance;
}

// Строим вертикальный стол: 0 сверху, слева дюжины, три колонки чисел 1–36,
// под ними место под ставки на колонки, внизу внешние ставки.
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
  // Ряд 1: «0», ряды 2–13: числа, ряд 14: колонки (пока без ставок), ряд 15: внешние ставки
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
  for (let i = 0; i < 3; i++) {
    const slot = document.createElement('div');
    slot.className = 'slot';
    slot.textContent = '2 к 1';
    slot.style.gridRow = '14';
    slot.style.gridColumn = String(2 + i);
    tableEl.appendChild(slot);
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
}

function setMessage(text, kind = '') {
  messageEl.textContent = text;
  messageEl.className = 'message ' + kind;
}

// Блокирует/разблокирует все кнопки и поля ставок
function setBettingEnabled(enabled) {
  document.querySelectorAll('#bets button, #bets input, #table button').forEach((el) => {
    el.disabled = !enabled;
  });
  spinBtn.disabled = !enabled;
}

// Ставка списывается с баланса сразу; одинаковые ставки складываются
function placeBet(type, value = null) {
  const amount = Number(amountEl.value);
  if (!Number.isInteger(amount) || amount < 1) {
    setMessage('Введите целую сумму ставки от 1', 'lose');
    return;
  }
  if (amount > balance) {
    setMessage('Недостаточно фишек', 'lose');
    return;
  }
  if (type === 'number' && (!Number.isInteger(value) || value < 0 || value > 36)) {
    setMessage('Число должно быть от 0 до 36', 'lose');
    return;
  }
  balance -= amount;
  const existing = bets.find((b) => b.type === type && b.value === value);
  if (existing) existing.amount += amount;
  else bets.push({ type, value, amount });
  setMessage('');
  renderBalance();
  renderBets();
}

// Повторяет ставки прошлого раунда (текущие ставки заменяются)
function repeatBets() {
  if (lastBets.length === 0) {
    setMessage('Нет прошлой ставки для повтора', 'lose');
    return;
  }
  const current = bets.reduce((sum, b) => sum + b.amount, 0);
  const needed = lastBets.reduce((sum, b) => sum + b.amount, 0);
  if (needed > balance + current) {
    setMessage('Недостаточно фишек для повтора ставки', 'lose');
    return;
  }
  balance += current - needed;
  bets = lastBets.map((b) => ({ ...b }));
  setMessage('');
  renderBalance();
  renderBets();
}

function clearBets() {
  balance += bets.reduce((sum, b) => sum + b.amount, 0);
  bets = [];
  setMessage('');
  renderBalance();
  renderBets();
}

// Выигрывает ли ставка при выпавшем числе n (у нуля нет цвета и чётности)
function isWinning(b, n) {
  switch (b.type) {
    case 'red':    return getColor(n) === 'red';
    case 'black':  return getColor(n) === 'black';
    case 'even':   return n !== 0 && n % 2 === 0;
    case 'odd':    return n % 2 === 1;
    case 'dozen':  return n !== 0 && Math.ceil(n / 12) === b.value;
    case 'column': return n !== 0 && rowOf(n) === b.value;
    case 'number': return n === b.value;
  }
}

// Возвращает чистый результат раунда (выигрыш минус все ставки)
function settleBets(n) {
  let returned = 0;
  let staked = 0;
  bets.forEach((b) => {
    staked += b.amount;
    if (isWinning(b, n)) returned += b.amount * (PAYOUT[b.type] + 1); // ставка + выигрыш
  });
  balance += returned;
  bets = [];
  return returned - staked;
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

function spin() {
  if (bets.length === 0) {
    setMessage('Сначала сделайте ставку', 'lose');
    return;
  }
  lastBets = bets.map((b) => ({ ...b }));
  setBettingEnabled(false);
  setMessage('');

  // 1. Выбираем случайный индекс сектора (а значит и число)
  const index = Math.floor(Math.random() * WHEEL_ORDER.length);
  const winner = WHEEL_ORDER[index];

  // 2. Крутим колесо и пускаем шарик; он упадёт именно в ячейку выбранного числа
  layerResultEl.className = 'layer-result';
  layerResultEl.textContent = '';
  wheelLayer.classList.add('active');
  animateSpin(index, () => {
    layerResultEl.textContent = winner;
    layerResultEl.className = 'layer-result ' + getColor(winner);
    showResult(winner);
    setTimeout(() => wheelLayer.classList.remove('active'), RESULT_HOLD_MS);
  });
}

function showResult(n) {
  const color = getColor(n);
  numberEl.textContent = n;
  numberEl.className = 'result-number ' + color;

  spinHistory.unshift(n);
  spinHistory.length = Math.min(spinHistory.length, HISTORY_SIZE);
  renderHistory();

  const net = settleBets(n);
  renderBalance();
  renderBets();
  saveState();
  flash(net);
  if (net > 0) setMessage(`Вы выиграли ${net} фишек!`, 'win');
  else if (net < 0) setMessage(`Вы проиграли ${-net} фишек`, 'lose');
  else setMessage('Ничья: ставки вернулись', '');

  if (balance === 0) {
    // фишки закончились — фон размывается, остаётся только кнопка микрозайма
    loanOverlay.hidden = false;
  } else {
    setBettingEnabled(true);
  }
}

function takeLoan() {
  balance += LOAN_AMOUNT;
  loanOverlay.hidden = true;
  setBettingEnabled(true);
  setMessage('');
  renderBalance();
  saveState();
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
}

loadState();
drawWheel();
ballRadius = BALL_POCKET;
renderSpin();
window.addEventListener('resize', renderSpin);
buildTable();
renderBalance();
renderBets();
renderHistory();
if (balance === 0) {
  loanOverlay.hidden = false;
  setBettingEnabled(false);
}
spinBtn.addEventListener('click', spin);
loanBtn.addEventListener('click', takeLoan);
document.getElementById('clear-bets').addEventListener('click', clearBets);
document.getElementById('repeat-bets').addEventListener('click', repeatBets);
betsPanel.querySelectorAll('.chip').forEach((btn) => {
  btn.addEventListener('click', () => {
    amountEl.value = btn.dataset.amount;
  });
});
