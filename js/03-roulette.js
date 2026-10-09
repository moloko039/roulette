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
skinSetHost('table', () => document.getElementById('table'));

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

