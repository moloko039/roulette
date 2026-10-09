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
  if (animate) skinEvents.emit('cards:deal', { game: 'hilo', count: 1 });
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
      if (d.status !== 'active') skinEvents.emit('round:end', { game: 'hilo', net: d.payout - d.bet });
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
  skinEvents.emit('bet:placed', { game: 'hilo' });
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
hlEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadHilo('visible');
});

// #endregion

