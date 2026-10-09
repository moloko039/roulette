// #region Мины
// ---------- игра «Мины» ----------
// Состояние игры только с сервера: клиент не знает раскладку мин до конца игры и не пытается её угадать.
// Ответы с mine_cells нигде не сохраняются и не пишутся в консоль. Настройки (ставка, число мин) живут в памяти.
// Каждое действие пользователя получает новый request_id; все повторы действия идут с тем же (postJson).
const MINES_BET_MAX = 1000000000;
const MINES_COUNT_MIN = 1;
const MINES_COUNT_MAX = 24;
const MINES_CELLS = 25;
const MINES_ICON_SETS = {
  default: {
    gem: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12l4 6-10 13L2 9z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/></svg>',
    mine: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="6"/><path d="M12 3v3M12 20v3M3 13h3M18 13h3M5.6 6.6l2.1 2.1M16.3 17.3l2.1 2.1M18.4 6.6l-2.1 2.1M7.7 17.3l-2.1 2.1"/></svg>'
  },
  mine_acorn: {
    gem: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19C5 9 11 5 19 5c0 8-4 14-14 14z"/><path d="M5 19 14 10"/></svg>',
    mine: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 11c0-4 3-6 6-6s6 2 6 6z"/><path d="M12 3v2"/><path d="M7 11c0 5 2 9 5 10 3-1 5-5 5-10"/></svg>'
  },
  mine_star: {
    gem: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6.1L12 16.6 6.6 19.6l1.2-6.1-4.5-4.2 6.1-.7z"/></svg>',
    mine: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="6"/><path d="M12 3v3M12 20v3M3 13h3M18 13h3M5.6 6.6l2.1 2.1M16.3 17.3l2.1 2.1M18.4 6.6l-2.1 2.1M7.7 17.3l-2.1 2.1"/></svg>'
  }
};
let minesIcons = MINES_ICON_SETS.default;   // набор иконок зависит от скина «значки мин» (applySkins)

skinSetHost('mine_icons', () => document.getElementById('mines-board-wrap'));
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
    btn.style.setProperty('--ci', String(i));       // номер клетки для сдвига анимаций «волной» в скинах
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
    skinEvents.emit('bet:placed', { game: 'mines' });
    haptic('light');
  });
}

function minesReveal(cell) {
  if (mn.view !== 'play' || mn.busy) return;
  mn.pending = cell; // нажатая клетка показывает «открывается», пока идёт запрос
  minesAct('/api/mines/reveal', { cell }, validMinesReveal, (d) => {
    skinEvents.emit('mines:reveal', { cell, result: d.result });             // 'safe' | 'mine' | 'cleared': скин рисует выкапывание, взрыв
    if (d.result !== 'safe') skinEvents.emit('mines:end', { status: d.result === 'mine' ? 'lost' : 'cashed', cell });
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
    skinEvents.emit('mines:end', { status: d.last.status, cell: null });
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
minesEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadMines('visible');
});

// #endregion

