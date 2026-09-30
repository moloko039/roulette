// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

const COLOR_HEX = { red: '#c62828', black: '#111111', green: '#1b8f3a' };
const COLOR_NAME = { red: 'красное', black: 'чёрное', green: 'зелёное (зеро)' };

const SECTOR = 360 / WHEEL_ORDER.length; // угол одного сектора
const SPIN_TIME_MS = 5000;               // должно совпадать с transition в style.css

const canvas = document.getElementById('wheel');
const ctx = canvas.getContext('2d');
const spinBtn = document.getElementById('spin');
const numberEl = document.getElementById('result-number');
const colorEl = document.getElementById('result-color');

const START_BALANCE = 1000;
const PAYOUT = { red: 1, black: 1, even: 1, odd: 1, dozen: 2, number: 35 }; // выплата «N к 1»
const DOZEN_NAME = { 1: '1–12', 2: '13–24', 3: '25–36' };

const balanceEl = document.getElementById('balance');
const amountEl = document.getElementById('amount');
const tableEl = document.getElementById('table');
const messageEl = document.getElementById('message');
const restartBtn = document.getElementById('restart');
const betsPanel = document.getElementById('bets');

let rotation = 0; // сколько градусов колесо прокрутили всего
let balance = START_BALANCE;
let bets = []; // { type, value, amount }
let lastBets = []; // ставки предыдущего раунда — для кнопки «Повторить»

const HISTORY_SIZE = 10;
const historyEl = document.getElementById('history-list');
const appEl = document.querySelector('.app');
const balanceBox = document.querySelector('.balance');
let spinHistory = []; // последние выпавшие числа, новое — первым

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
  const radius = size / 2 - 4;
  const rad = (deg) => (deg * Math.PI) / 180;

  ctx.clearRect(0, 0, size, size);

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
    ctx.strokeStyle = '#d9b84a';
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

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = '#2b1d0e';
  ctx.fill();
  ctx.strokeStyle = '#d9b84a';
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

// Строим стол: 0, числа 1–36 по три в ряд и внешние ставки
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

  // Сетка 4 колонки: слева сектора-дюжины, справа числа по три в ряд, сверху 0
  const numbers = document.createElement('div');
  numbers.className = 'numbers';
  addCell(numbers, 'zero green', '0', 'number', 0, { row: '1', col: '2 / 5' });
  for (let n = 1; n <= 36; n++) {
    addCell(numbers, getColor(n), String(n), 'number', n, {
      row: String(Math.ceil(n / 3) + 1),
      col: String(((n - 1) % 3) + 2)
    });
  }
  for (let d = 1; d <= 3; d++) {
    addCell(numbers, 'dozen', DOZEN_NAME[d], 'dozen', d, {
      row: `${2 + (d - 1) * 4} / span 4`,
      col: '1'
    });
  }
  tableEl.appendChild(numbers);

  const outside = document.createElement('div');
  outside.className = 'outside';
  addCell(outside, 'red', 'Красное', 'red');
  addCell(outside, 'black', 'Чёрное', 'black');
  addCell(outside, 'plain', 'Чёт', 'even');
  addCell(outside, 'plain', 'Нечет', 'odd');
  tableEl.appendChild(outside);
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

  // 2. Считаем, на какой угол надо повернуть колесо, чтобы этот сектор
  //    оказался под стрелкой. Небольшой случайный сдвиг внутри сектора
  //    делает остановку более «живой».
  const jitter = (Math.random() - 0.5) * SECTOR * 0.7;
  const targetMod = (360 - index * SECTOR + jitter + 360) % 360;
  const currentMod = ((rotation % 360) + 360) % 360;
  const delta = (targetMod - currentMod + 360) % 360;
  const fullTurns = 5 + Math.floor(Math.random() * 3); // 5–7 полных оборотов

  rotation += fullTurns * 360 + delta;
  canvas.style.transform = `rotate(${rotation}deg)`;

  // 3. Когда анимация закончилась — показываем результат
  setTimeout(() => showResult(winner), SPIN_TIME_MS + 100);
}

function showResult(n) {
  const color = getColor(n);
  numberEl.textContent = n;
  numberEl.className = 'result-number ' + color;
  colorEl.textContent = COLOR_NAME[color];

  spinHistory.unshift(n);
  spinHistory.length = Math.min(spinHistory.length, HISTORY_SIZE);
  renderHistory();

  const net = settleBets(n);
  renderBalance();
  renderBets();
  flash(net);
  if (net > 0) setMessage(`Вы выиграли ${net} фишек!`, 'win');
  else if (net < 0) setMessage(`Вы проиграли ${-net} фишек`, 'lose');
  else setMessage('Ничья: ставки вернулись', '');

  if (balance === 0) {
    // фишки закончились — прячем ставки и предлагаем начать заново
    document.querySelectorAll('#bets .row, #table').forEach((el) => (el.hidden = true));
    spinBtn.hidden = true;
    restartBtn.hidden = false;
    setMessage('Фишки закончились. Игра окончена.', 'lose');
  } else {
    setBettingEnabled(true);
  }
}

function restart() {
  balance = START_BALANCE;
  bets = [];
  lastBets = [];
  numberEl.textContent = '—';
  numberEl.className = 'result-number';
  colorEl.textContent = '';
  spinHistory = [];
  renderHistory();
  document.querySelectorAll('#bets .row, #table').forEach((el) => (el.hidden = false));
  restartBtn.hidden = true;
  spinBtn.hidden = false;
  setBettingEnabled(true);
  setMessage('');
  renderBalance();
  renderBets();
}

drawWheel();
buildTable();
renderBalance();
renderBets();
renderHistory();
spinBtn.addEventListener('click', spin);
restartBtn.addEventListener('click', restart);
document.getElementById('clear-bets').addEventListener('click', clearBets);
document.getElementById('repeat-bets').addEventListener('click', repeatBets);
betsPanel.querySelectorAll('.chip').forEach((btn) => {
  btn.addEventListener('click', () => {
    amountEl.value = btn.dataset.amount;
  });
});
