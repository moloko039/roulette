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
  polling: false,
  resync: false         // после возврата на экран ждём ответ сервера: кадры не рисуются (иначе мелькнёт множитель сверх краха)
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
    return isCount(d.elapsed_ms) && d.crash_multiplier === null && d.result === null && d.multiplier === null && d.payout === null;   // и ручной, и с автовыводом
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
// Раунд с автовыводом: на экране множитель не выше цели (сервер закроет раунд на цели при ближайшем опросе, показ не должен её перескакивать)
const crTarget100 = () => (cr.game && cr.game.mode === 'auto' && typeof cr.game.target === 'string' ? crX100(cr.game.target) : null);

function crDraw() {
  if (cr.view !== 'play' || !cr.game || cr.resync) return;
  const tc = crTarget100();
  const eT = tc === null ? Infinity : crDoubling() * Math.log2(tc / 100);
  const e = Math.min(crElapsed(), eT);
  const x100 = crElapsed() >= eT ? tc : crM100(e);
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
  if (cr.view !== 'play') return;
  if (activeTab !== 'play' || currentGame !== 'crash') { crPause(); return; }   // ушли с экрана: ни кадров, ни опроса, пока не вернёмся (crResume)
  if (cr.busy || cr.polling || document.visibilityState !== 'visible') { crSchedulePoll(); return; }
  cr.polling = true;
  try {
    const d = await fetchCrashState();
    if (cr.view === 'play' && !cr.busy) crApplyLive(d);
  } catch (e) {
    // сбой опроса не прерывает полёт: следующая попытка через интервал (429 тоже)
  } finally {
    cr.polling = false;
    cr.resync = false;
  }
  if (cr.view === 'play') crSchedulePoll();
}

// Уход с экрана краша (другая вкладка или игра) или скрытие приложения во время полёта: раньше цепочка опроса обрывалась, а кадры продолжали идти
// по часам, и после возврата множитель бежал, хотя раунд уже закончился. Теперь на уходе кадры и опрос останавливаются, на возврате (crResume) сервер
// спрашивается сразу, минуя интервал между запросами; до ответа новые кадры не рисуются.
function crPause() {
  crStopLoop();
}

function crResume() {
  if (cr.view !== 'play' || activeTab !== 'play' || currentGame !== 'crash') return;
  cr.resync = true;
  if (!cr.raf && !cr.iv) crStartLoop();
  clearTimeout(cr.pollTimer);
  cr.pollTimer = setTimeout(crPoll, 0);
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
  } else if ((g.mode === 'manual' && (cr.pressed || (g.auto && !cr.live))) || (g.mode === 'auto' && cr.pressed)) {
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
    crEls.label.textContent = g.target === null ? 'Нажмите «Забрать» до краха' : 'авто ×' + g.target;
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
    crFinishWith(d, false);     // сервер всегда отвечает активным раундом; итог сразу (не должно случаться) показывается как есть
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
crEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    crResume();
    loadCrash('visible');
  }
});

// #endregion

