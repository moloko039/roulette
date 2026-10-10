// #region Рулетка: состояние, запросы, связка
// ---------- рулетка: состояние, запросы, связка ----------
const spinBtn = document.getElementById('spin');
const numberEl = document.getElementById('result-number');

const balanceEl = document.getElementById('balance');
const amountEl = document.getElementById('amount');
const messageLineEl = document.getElementById('message-line');
const messageTextEl = document.getElementById('message-text');
const messageCodeEl = document.getElementById('message-code');
const statusLineEl = document.getElementById('status-line');
const statusTextEl = document.getElementById('status-text');
const statusCodeEl = document.getElementById('status-code');
const betsPanel = document.getElementById('bets');

const wheelLayer = document.getElementById('wheel-layer');
const layerResultEl = document.getElementById('layer-result');
const layerWinEl = document.getElementById('layer-win');
const gameSwitchEl = document.getElementById('game-switch');
const RESULT_HOLD_MS = 1500; // сколько колесо остаётся на экране после остановки

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
  skinEvents.emit('round:end', { game: 'roulette', net: data.net });       // конец раунда для скинов (сцена стола копит листья, ...)
  loadServer('after'); // обновит таймер (не чаще, чем раз в 5 секунд)
}

// #endregion
