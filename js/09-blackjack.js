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
  // мягкая рука: туз сейчас считается как 11 (при переборе станет 1); подпись объясняет это словами, а не термином
  return { text: hand.soft ? 'Мягкая ' + hand.total + ' (туз = 11)' : String(hand.total), cls: '' };
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
bjEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
window.addEventListener('resize', () => { if (bj.view !== 'loading') renderBjTable(); });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadBj('visible');
});

// #endregion

