// #region Стол рулетки и ставки
// ---------- рулетка: стол и ставки ----------
const BET_TYPES = ['number', 'red', 'black', 'even', 'odd', 'dozen', 'column'];
const DOZEN_NAME = { 1: '1–12', 2: '13–24', 3: '25–36' };

const tableEl = document.getElementById('table');
let bets = []; // { type, value, amount }
let lastBets = []; // ставки предыдущего раунда — для кнопки «Повторить»

// Колонка в нумерации СЕРВЕРА по числам, которые она покрывает: 1 — 1, 4, 7…34;
// 2 — 2, 5…35; 3 — 3, 6…36. Определяем по первому числу колонки, а не по ряду стола.
const serverColumnOf = (n) => ((n - 1) % 3) + 1;

// Строим вертикальный стол: 0 сверху, слева дюжины, три колонки чисел 1–36,
// под ними ставки на колонки (2 к 1); внешние ставки (чёт, красное, чёрное, нечет) компактным рядом НАД сеткой (UI_REVIEW, рулетка).
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
  // Ряд 1: внешние ставки, ряд 2: «0», ряды 3–14: числа, ряд 15: колонки
  const outsideRow = document.createElement('div');
  outsideRow.className = 'outside-row';
  outsideRow.style.gridRow = '1';
  tableEl.appendChild(outsideRow);
  [
    ['plain', 'Чёт', 'even'],
    ['red', 'Красн.', 'red'],
    ['black', 'Чёрн.', 'black'],
    ['plain', 'Нечет', 'odd']
  ].forEach(([cls, label, type]) => addCell(outsideRow, cls + ' outside', label, type));
  addCell(tableEl, 'green', '0', 'number', 0, { row: '2', col: '1 / -1' });
  for (let n = 1; n <= 36; n++) {
    addCell(tableEl, getColor(n), String(n), 'number', n, {
      row: String(3 + Math.floor((n - 1) / 3)),
      col: String(2 + ((n - 1) % 3))
    });
  }
  for (let d = 1; d <= 3; d++) {
    addCell(tableEl, 'dozen', DOZEN_NAME[d], 'dozen', d, {
      row: `${3 + (d - 1) * 4} / span 4`,
      col: '1'
    });
  }
  // ячейка под столбцом, где первое число i + 1 (1, 2 или 3)
  for (let i = 1; i <= 3; i++) {
    addCell(tableEl, 'column', '2 к 1', 'column', serverColumnOf(i), { row: '15', col: String(1 + i) });
  }
}

// Кладём на клетки стола фишки с суммой ставки
function renderChips() {
  const summary = document.getElementById('table-summary');
  if (summary) summary.textContent = 'Ставок: ' + bets.length + ' · Сумма: ' + formatCompact(bets.reduce((sum, b) => sum + b.amount, 0));   // заполняется по ходу (вместо «пока пусто»)
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

// Ставки пока только на столе: сервер о них не знает. Лимита на размер ставки нет,
// проверка «не больше доступного» — удобство, настоящую делает сервер
skinSetHost('table', () => document.getElementById('table'));

function placeBet(type, value = null) {
  if (gameBusy() || !srv.loaded) return;
  clearHitCell();
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
  skinEvents.emit('bet:placed', { game: 'roulette', type, value });      // скин фишки: фишка садится на клетку
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

// #endregion
