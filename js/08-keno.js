// #region Кено
// ---------- игра «Кено» ----------
// Один раунд за запрос: клиент шлёт ставку и числа, сервер вытягивает 10 чисел и считает выплату. Баланс только серверный
// (общий srv, как у рулетки) и в шапке кено обновляется после анимации розыгрыша. Выбор чисел и ставка живут только в памяти.
// Таблица выплат берётся с сервера (GET /api/keno/paytable) и кэшируется в памяти.
const KENO_BET_MAX = 1000000000;
const KENO_FIELD = 40;
const KENO_MAX_PICKS = 10;
const KENO_STEP_MS = 250;           // пауза между вытянутыми числами (10 чисел ≈ 2,5 с)
const KENO_PAY_RETRY_MS = 10000;    // таблицу выплат при неудаче повторно не чаще, чем раз в 10 секунд

const kenoEls = {
  balance: document.getElementById('keno-balance'),
  switchBtn: document.getElementById('keno-switch'),
  notice: document.getElementById('keno-notice'),
  board: document.getElementById('keno-board'),
  count: document.getElementById('keno-count'),
  auto: document.getElementById('keno-auto'),
  clear: document.getElementById('keno-clear'),
  result: document.getElementById('keno-result'),
  resultTitle: document.getElementById('keno-result-title'),
  resultDetail: document.getElementById('keno-result-detail'),
  payList: document.getElementById('keno-pay-list'),
  payNote: document.getElementById('keno-pay-note'),
  panel: document.getElementById('keno-bets'),
  bet: document.getElementById('keno-bet'),
  maxBtn: document.getElementById('keno-max'),
  play: document.getElementById('keno-play')
};

const kn = {
  picks: new Set(),     // выбранные числа
  busy: false,          // идёт раунд (запрос и анимация): поле и кнопки заблокированы
  revealed: new Set(),  // вытянутые числа, уже показанные на поле
  final: false,         // анимация закончилась, показан итог
  result: null,         // { hits: [..], picked, payout, bet } последнего раунда
  shownBalance: null,   // баланс в шапке на время раунда (обновляется после анимации)
  pay: null,            // таблица выплат { "k": { "h": "X.YY" } }
  payLoading: false,
  payFailedAt: -Infinity,
  noticeTimer: null
};
registerGame({ id: 'keno', busy: () => kn.busy });

const kenoBalls = [];
for (let n = 1; n <= KENO_FIELD; n++) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'keno-ball';
  btn.setAttribute('aria-label', 'Число ' + n);
  btn.setAttribute('aria-pressed', 'false');
  btn.innerHTML = '<span></span>';
  btn.firstChild.textContent = String(n);
  btn.addEventListener('click', () => toggleKenoPick(n));
  kenoEls.board.appendChild(btn);
  kenoBalls.push(btn);
}

const kenoBetLimit = () => Math.max(1, Math.min(KENO_BET_MAX, srv.loaded ? srv.balance : KENO_BET_MAX));

function setKenoNotice(text) {
  clearTimeout(kn.noticeTimer);
  kenoEls.notice.textContent = text;
  if (text) kn.noticeTimer = setTimeout(() => { kenoEls.notice.textContent = ''; }, 3500);
}

// Новый выбор или правка выбора сбрасывает показ прошлого итога
function resetKenoResult() {
  kn.result = null;
  kn.final = false;
  kn.revealed = new Set();
}

function toggleKenoPick(n) {
  if (kn.busy) return;
  resetKenoResult();
  if (kn.picks.has(n)) {
    kn.picks.delete(n);
  } else if (kn.picks.size >= KENO_MAX_PICKS) {
    setKenoNotice('Можно выбрать не больше ' + KENO_MAX_PICKS + ' чисел');
    haptic('error');
  } else {
    kn.picks.add(n);
    haptic('light');
  }
  renderKeno();
  loadKenoPay();
}

function kenoAuto() {
  if (kn.busy) return;
  resetKenoResult();
  const all = Array.from({ length: KENO_FIELD }, (_, i) => i + 1);
  for (let i = all.length - 1; i > 0; i--) { // перемешивание Фишера-Йетса
    const j = Math.floor(Math.random() * (i + 1));
    [all[i], all[j]] = [all[j], all[i]];
  }
  kn.picks = new Set(all.slice(0, KENO_MAX_PICKS));
  haptic('light');
  renderKeno();
  loadKenoPay();
}

function kenoClear() {
  if (kn.busy) return;
  resetKenoResult();
  kn.picks = new Set();
  renderKeno();
}

// Фишки ставки по серверному балансу (те же номиналы, что в рулетке и минах)
const kenoChipBar = makeChipBar({
  root: kenoEls.panel,
  attr: 'kbet',
  getBalance: () => (srv.loaded ? srv.balance : 0),
  getBet: () => Number(kenoEls.bet.value),
  setBet: (v) => { kenoEls.bet.value = String(v); }
});
const renderKenoChips = kenoChipBar.render;
const syncKenoChips = kenoChipBar.sync;

// одна строка текста: короткая запись на экране, полное число в подсказке
function setKenoLine(el, shortText, fullText) {
  el.textContent = shortText;
  el.title = fullText;
}

function renderKenoPay() {
  const k = kn.picks.size;
  kenoEls.payList.textContent = '';
  kenoEls.payNote.textContent = '';
  if (k === 0) {
    kenoEls.payNote.textContent = 'Выберите числа, чтобы увидеть выплаты';
    return;
  }
  const row = kn.pay && kn.pay[String(k)];
  if (!row) {
    kenoEls.payNote.textContent = kn.payLoading ? 'Загрузка таблицы выплат…' : 'Таблица выплат недоступна';
    return;
  }
  const hitCount = kn.final && kn.result ? kn.result.hits.length : -1;
  Object.keys(row).map(Number).sort((a, b) => a - b).forEach((h) => {
    const li = document.createElement('li');
    li.dataset.h = String(h);
    if (h === hitCount) li.className = 'won';
    li.innerHTML = '<span></span><b></b>';
    li.firstChild.textContent = String(h);
    li.setAttribute('aria-label', 'совпало ' + h + ' из ' + k + ', множитель ' + row[String(h)]);
    li.lastChild.textContent = '×' + row[String(h)];
    kenoEls.payList.appendChild(li);
  });
  kenoEls.payNote.textContent = 'Остальные исходы: 0';
}

function renderKenoResult() {
  const r = kn.final ? kn.result : null;
  kenoEls.result.classList.toggle('win', !!r && r.payout > 0);
  if (!r) {
    kenoEls.resultTitle.textContent = '';
    kenoEls.resultDetail.textContent = '';
    kenoEls.resultTitle.title = kenoEls.resultDetail.title = '';
    return;
  }
  kenoEls.resultTitle.textContent = 'Совпало ' + r.hits.length + ' из ' + r.picked;
  if (r.payout > 0) {
    const profit = r.payout - r.bet;
    setKenoLine(kenoEls.resultDetail,
      'Выигрыш ' + formatCompact(r.payout) + ', чистая прибыль ' + formatCompact(profit),
      'Выигрыш ' + formatNumber(r.payout) + ', чистая прибыль ' + formatNumber(profit));
  } else {
    setKenoLine(kenoEls.resultDetail, 'Не повезло', 'Не повезло');
  }
}

function renderKeno() {
  const bal = kn.busy ? kn.shownBalance : (srv.loaded ? srv.balance : null);
  if (bal !== null && bal !== undefined) {
    kenoEls.balance.classList.remove('skeleton');
    kenoEls.balance.textContent = spacedNumber(bal);
  } else {
    kenoEls.balance.textContent = srv.error ? '—' : 'Загрузка…';
  }
  fitNumberFont(kenoEls.balance, kenoEls.balance.textContent.length);
  renderKenoChips();
  syncKenoChips();
  refreshBetPanels();
  kenoBalls.forEach((btn, i) => {
    const n = i + 1;
    const sel = kn.picks.has(n);
    const drawn = kn.revealed.has(n);
    btn.classList.toggle('sel', sel);
    btn.classList.toggle('drawn', drawn);
    btn.classList.toggle('hit', sel && drawn);
    btn.classList.toggle('miss', kn.final && sel && !drawn);
    btn.setAttribute('aria-pressed', String(sel));
    btn.disabled = kn.busy;
  });
  kenoEls.board.classList.toggle('locked', kn.busy);
  kenoEls.count.textContent = 'Выбрано ' + kn.picks.size + ' из ' + KENO_MAX_PICKS;
  renderKenoPay();
  renderKenoResult();
  const ready = !!(tg && tg.initData) && srv.loaded;
  kenoEls.panel.querySelectorAll('button, input').forEach((el) => { el.disabled = kn.busy || !ready; });
  kenoEls.auto.disabled = kn.busy;
  kenoEls.clear.disabled = kn.busy || kn.picks.size === 0;
  kenoEls.play.disabled = kn.busy || !ready;
  kenoEls.play.textContent = kn.busy ? 'Играем…' : (kn.final ? 'Играть снова' : 'Играть');
  kenoEls.switchBtn.disabled = kn.busy;
  refreshBetPanels();
}
kenoRender = renderKeno;

// #endregion

// #region Кено: таблица выплат
// ---------- таблица выплат ----------
const validKenoPay = (d) => {
  if (!d || typeof d.paytable !== 'object' || d.paytable === null) return false;
  return Object.keys(d.paytable).every((k) => {
    const row = d.paytable[k];
    return /^\d+$/.test(k) && row && typeof row === 'object'
      && Object.keys(row).every((h) => /^\d+$/.test(h) && typeof row[h] === 'string' && /^\d+\.\d\d$/.test(row[h]));
  });
};

async function loadKenoPay() {
  if (kn.pay || kn.payLoading || !(tg && tg.initData)) return;
  if (performance.now() - kn.payFailedAt < KENO_PAY_RETRY_MS) return;
  kn.payLoading = true;
  renderKenoPay();
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/keno/paytable', {
      method: 'GET',
      headers: { Authorization: 'tma ' + tg.initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (!res.ok) throw new Error('status');
    const d = await res.json();
    if (!validKenoPay(d)) throw new Error('shape');
    kn.pay = d.paytable;
  } catch (e) {
    kn.payFailedAt = performance.now();
  } finally {
    clearTimeout(timeout);
    kn.payLoading = false;
    renderKenoPay();
  }
}

// #endregion

// #region Кено: раунд
// ---------- раунд ----------
// Ответ проверяется по настоящему контракту (docs/API.md): нужны только draw, hits, payout, balance
function validKenoRound(d, picks) {
  return !!d && Array.isArray(d.draw) && d.draw.length === 10
    && d.draw.every((n, i) => isInt(n) && n >= 1 && n <= KENO_FIELD && d.draw.indexOf(n) === i)
    && Array.isArray(d.hits) && d.hits.every((n) => isInt(n) && d.draw.includes(n) && picks.includes(n))
    && isCount(d.payout) && isCount(d.balance);
}

// Один POST. { kind: 'ok', data } | { kind: 'conflict', detail } | { kind: 'fatal', text } | { kind: 'retry', code } | { kind: 'invalid' }
async function postKenoOnce(payload, picks) {
  try {
    const res = await postJson('/api/keno/play', payload);
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Неверные параметры' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      return { kind: 'conflict', detail: typeof body.detail === 'string' ? body.detail : '' };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось выполнить раунд' };
    }
    const body = await readJsonBody(res);
    // ответ 2xx не повторяем: раунд уже обработан сервером, непонятный ответ = повод обновить баланс отдельным запросом
    return body.ok && validKenoRound(body.data, picks) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// Вытянутые числа загораются по одному (без движения: сразу все)
async function animateKenoDraw(draw, picks) {
  if (reducedMotion()) {
    kn.revealed = new Set(draw);
    renderKeno();
    return;
  }
  skinEvents.emit('keno:start', { picks: picks.length });
  for (const n of draw) {
    await sleep(KENO_STEP_MS);
    kn.revealed.add(n);
    skinEvents.emit('keno:draw', { n, hit: picks.includes(n) });
    renderKeno();
    const ball = kenoBalls[n - 1];
    ball.classList.remove('pop');
    void ball.offsetWidth; // перезапуск CSS-анимации
    ball.classList.add('pop');
    if (picks.includes(n)) haptic('light');
  }
  await sleep(KENO_STEP_MS);
  skinEvents.emit('keno:end', { hits: picks.filter((n) => draw.includes(n)).length, picks: picks.length });
  skinEvents.emit('round:end', { game: 'keno' });
}

skinSetHost('keno_ball', () => document.getElementById('keno-board'));

async function kenoPlay() {
  if (kn.busy) return;
  const picks = [...kn.picks].sort((a, b) => a - b);
  const bet = Number(kenoEls.bet.value);
  if (picks.length < 1) {
    setKenoNotice('Выберите от 1 до ' + KENO_MAX_PICKS + ' чисел');
    return;
  }
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > KENO_BET_MAX) {
    setKenoNotice('Введите целую ставку от 1 до ' + formatNumber(KENO_BET_MAX));
    return;
  }
  if (!srv.loaded || bet > srv.balance) {
    setKenoNotice('Не хватает фишек');
    return;
  }
  if (!(tg && tg.initData)) {
    setKenoNotice('Откройте игру через бота в Telegram');
    return;
  }
  const id = makeRequestId();
  if (!id) {
    setKenoNotice('Ошибка');
    return;
  }
  setKenoNotice('');
  resetKenoResult();
  kn.busy = true;
  kn.shownBalance = srv.balance;
  renderKeno();
  const result = await postWithRetries(() => postKenoOnce({ request_id: id, bet, picks }, picks));
  let note = 'Баланс обновлён';
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      await animateKenoDraw(d.draw, picks);
      kn.result = { hits: d.hits, picked: picks.length, payout: d.payout, bet };
      kn.final = true;
      srv.balance = d.balance;      // баланс на экране меняется только после анимации
      srv.loaded = true;
      srv.error = null;
      kn.busy = false;
      renderBalance();
      renderKeno();
      haptic(d.payout > 0 ? 'success' : 'error');
      loadServer('after');
      return;
    } catch (e) {
      // ошибка нашего кода после успешного ответа: POST не повторяем, баланс берём отдельным запросом
      note = 'Не удалось показать результат. Баланс обновлён';
    }
  } else if (result && result.kind === 'invalid') {
    note = 'Ответ сервера не распознан. Баланс обновлён';
  } else if (result && result.kind === 'fatal') {
    note = result.text;
  } else if (result && result.kind === 'conflict') {
    if (result.detail === 'insufficient_funds') note = 'Не хватает фишек';
    else if (result.detail === 'balance_limit') note = 'Достигнут максимальный баланс';
    else note = 'Не удалось выполнить раунд';
  } else {
    note = 'Нет связи. Баланс обновлён';
  }
  resetKenoResult();
  kn.busy = false;
  setKenoNotice(note);
  renderKeno();
  loadServer('after');
}

setupBetPanel({
  input: kenoEls.bet,
  maxBtn: kenoEls.maxBtn,
  halfBtn: document.getElementById('keno-half'),
  doubleBtn: document.getElementById('keno-double'),
  getLimit: kenoBetLimit
});
kenoEls.bet.addEventListener('input', syncKenoChips);
kenoEls.panel.querySelectorAll('[data-kbet]').forEach((b) => b.addEventListener('click', () => {
  kenoEls.bet.value = b.dataset.kbet;
  syncKenoChips();
  refreshBetPanels();
  haptic('light');
}));
kenoEls.auto.addEventListener('click', kenoAuto);
kenoEls.clear.addEventListener('click', kenoClear);
kenoEls.play.addEventListener('click', kenoPlay);
kenoEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
renderKeno();

// #endregion

