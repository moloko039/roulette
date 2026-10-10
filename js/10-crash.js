// #region Краш
// ---------- живой краш (docs/CRASH_LIVE.md) ----------
// Один общий раунд на весь сервер: приём ставок, полёт, итог, снова приём. Время и исход только на сервере. Клиент опрашивает GET /api/crash/live
// (раз в 300 мс в приёме ставок и полёте, раз в секунду в паузе итога; повторный запрос с токеном изменений v получает маленький ответ «unchanged»),
// сверяет часы по server_ms и рисует рост множителя по времени старта полёта. Точка краха и секрет раскрываются только в фазе итога; клиент сам проверяет
// честность раунда: sha256(секрет) совпадает с хэшем, показанным до раунда, и точка краха выведена из секрета той же формулой (hmac-sha256, как на сервере).
// Лента ставок: беседа видит ставки только своей беседы (с именами), вне беседы общая анонимная лента (имена «Игрок N»).
const CR_BET_MAX = 1000000000;
const CR_TARGET_MIN = 101;
const CR_TARGET_MAX = 100000;    // ×1000.00, как CAP_X100 на сервере
const CR_DOUBLING_MS = 6000;     // как crash.DOUBLING_MS на сервере (только для показа; решает сервер)
const CR_POLL_MS = 300;          // опрос в приёме ставок и полёте
const CR_POLL_RESULT_MS = 1000;  // опрос в паузе итога
const CR_HISTORY = 10;
const CR_TOKEN_RE = /^[A-Za-z0-9._-]{1,64}$/;
const CR_HEX_RE = /^[0-9a-f]{64}$/;
const CR_STATUSES = ['open', 'cashed', 'lost'];

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
  smoke: document.getElementById('cr-smoke'),
  smoke: document.getElementById('cr-smoke'),
  mult: document.getElementById('cr-mult'),
  label: document.getElementById('cr-label'),
  history: document.getElementById('cr-history'),
  banner: document.getElementById('cr-banner'),
  bannerTitle: document.getElementById('cr-banner-title'),
  bannerDetail: document.getElementById('cr-banner-detail'),
  proof: document.getElementById('cr-proof'),
  feedTitle: document.getElementById('cr-feed-title'),
  feed: document.getElementById('cr-feed'),
  bets: document.getElementById('cr-bets'),
  bet: document.getElementById('cr-bet'),
  maxBtn: document.getElementById('cr-max'),
  target: document.getElementById('cr-target'),
  start: document.getElementById('cr-start'),
  actions: document.getElementById('cr-actions'),
  cash: document.getElementById('cr-cash')
};

const cr = {
  phaseShown: '',       // показанная фаза и раунд: для события crash:phase скинов
  phaseRound: 0,
  flightSeen: 0,        // раунд, который эта сессия видела в полёте (итог без полёта сцена показывает без анимации краха)
  crashSeen: new Set(),
  loaded: false,
  error: false,
  state: null,          // последний ПОЛНЫЙ ответ GET /api/crash/live
  token: '',            // токен изменений v из него
  balance: null,
  busy: false,          // идёт действие (ставка, вывод): кнопки заблокированы
  inFlight: false,      // нужно ядру: loadGameState не используется, поле на случай общего кода
  lastRequestAt: -Infinity,
  timer: null,
  syncServerMs: 0,      // server_ms из последнего ответа и момент его получения
  syncAt: 0,
  raf: 0,
  iv: 0,
  pollTimer: 0,
  polling: false,
  resync: false,        // после возврата на экран ждём ответ сервера: кадры не рисуются
  verified: new Map(),  // номер раунда -> 'ok' | 'bad' | 'unknown' (проверка честности)
  settled: new Set(),   // раунды, по итогу которых уже обновили баланс (проигрыш)
  cashSeen: new Set()   // раунды, где мой вывод (ручной или автовывод) уже показан и баланс обновлён
};
registerGame({ id: 'crash', state: cr, render: renderCrash, busy: () => cr.busy, keepBalance: () => false });

const crText = (x100) => '×' + Math.floor(x100 / 100) + '.' + String(x100 % 100).padStart(2, '0');
const crBetLimit = () => Math.max(1, Math.min(CR_BET_MAX, cr.balance === null ? CR_BET_MAX : cr.balance));
const crRound = () => (cr.state && cr.state.round) || null;
const crMe = () => (cr.state && cr.state.me) || null;
const crServerNow = () => cr.syncServerMs + (performance.now() - cr.syncAt);

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
  if (!/^\d{1,4}(\.\d{0,2})?$/.test(raw)) return undefined;
  const x = Math.round(parseFloat(raw) * 100);
  return x >= CR_TARGET_MIN && x <= CR_TARGET_MAX ? x : undefined;
}

// #endregion

// #region Краш: проверка ответа сервера
// ---------- проверка ответа сервера (по реальному контракту, docs/API.md) ----------
const crOptCount = (v) => v === null || isCount(v);

function validCrRound(r) {
  if (!r || typeof r !== 'object' || !isCount(r.id) || !['betting', 'flight', 'result', 'idle'].includes(r.phase)) return false;
  if (typeof r.seed_hash !== 'string' || !CR_HEX_RE.test(r.seed_hash) || !isCount(r.bet_open_ms) || !isCount(r.flight_start_ms)) return false;
  if (r.m100 !== undefined && !crOptCount(r.m100)) return false;
  if (r.result !== undefined && r.result !== null) {
    if (!isCount(r.result.crash_x100) || typeof r.result.seed !== 'string' || !CR_HEX_RE.test(r.result.seed)) return false;
  }
  return r.next_open_ms === undefined || isCount(r.next_open_ms);
}

function validCrBet(b) {
  return !!b && typeof b.name === 'string' && b.name.length <= 80 && isCount(b.bet) && CR_STATUSES.includes(b.status)
    && crOptCount(b.cashed_x100) && isCount(b.payout);
}

function validCrMe(m) {
  return m === null || (!!m && isCount(m.bet) && crOptCount(m.target_x100) && CR_STATUSES.includes(m.status) && crOptCount(m.cashed_x100) && isCount(m.payout));
}

function validCrLive(d) {
  if (!d || typeof d !== 'object' || !isCount(d.server_ms)) return false;
  if (d.unchanged === true) {
    return typeof d.v === 'string' && CR_TOKEN_RE.test(d.v) && typeof d.phase === 'string' && (d.m100 === null || d.m100 === undefined || isCount(d.m100));
  }
  if (!Array.isArray(d.history) || !Array.isArray(d.bets) || d.bets.length > 500) return false;
  if (d.round !== null && !validCrRound(d.round)) return false;
  if (!d.bets.every(validCrBet) || !validCrMe(d.me === undefined ? null : d.me)) return false;
  if (d.v !== undefined && !(typeof d.v === 'string' && CR_TOKEN_RE.test(d.v))) return false;
  return d.history.every((h) => !!h && isCount(h.crash_x100) && typeof h.seed_hash === 'string' && CR_HEX_RE.test(h.seed_hash) && typeof h.seed === 'string' && CR_HEX_RE.test(h.seed));
}

const validCrBetDone = (d) => !!d && isCount(d.round_id) && isCount(d.bet) && isCount(d.balance) && typeof d.replayed === 'boolean';
const validCrCashDone = (d) => !!d && isCount(d.round_id) && isCount(d.cashed_x100) && isCount(d.payout) && isCount(d.balance) && typeof d.replayed === 'boolean';

// #endregion

// #region Краш: проверка честности раунда
// ---------- честность: хэш секрета и точка краха выводятся из секрета на устройстве игрока ----------
const crHex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
const crBytes = (hex) => Uint8Array.from(hex.match(/../g), (h) => parseInt(h, 16));

// точка краха из секрета: u = первые 7 байт hmac-sha256(секрет, "crash") по модулю 2**53, затем max(100, 3600*M // (37*(M-u))), не больше 10**9 (как crash_from_u на сервере)
async function crVerifyRound(round) {
  if (!(window.crypto && crypto.subtle && typeof BigInt === 'function') || !round.result) return 'unknown';
  try {
    const seed = crBytes(round.result.seed);
    if (crHex(await crypto.subtle.digest('SHA-256', seed)) !== round.seed_hash) return 'bad';
    const key = await crypto.subtle.importKey('raw', seed, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode('crash')));
    let u = 0n;
    for (let i = 0; i < 7; i++) u = (u << 8n) | BigInt(mac[i]);
    const M = 1n << 53n;
    u %= M;
    let c = (3600n * M) / (37n * (M - u));
    if (c < 100n) c = 100n;
    if (c > 1000000000n) c = 1000000000n;
    return Number(c) === round.result.crash_x100 ? 'ok' : 'bad';
  } catch (e) {
    return 'unknown';
  }
}

function crCheckProof(round) {
  if (!round || !round.result || cr.verified.has(round.id)) return;
  cr.verified.set(round.id, 'pending');
  crVerifyRound(round).then((res) => {
    cr.verified.set(round.id, res);
    if (cr.verified.size > 30) cr.verified.delete(cr.verified.keys().next().value);
    renderCrProof();
  });
}

function renderCrProof() {
  const r = crRound();
  if (!r) { crEls.proof.hidden = true; return; }
  crEls.proof.hidden = false;
  const short = r.seed_hash.slice(0, 12) + '…';
  const v = cr.verified.get(r.id);
  if (r.result && v === 'ok') crEls.proof.textContent = 'Честность раунда проверена: секрет раскрыт, хэш ' + short + ' и точка краха сошлись';
  else if (r.result && v === 'bad') crEls.proof.textContent = 'Проверка раунда не сошлась (хэш ' + short + '): сообщите владельцу';
  else if (r.result && v === 'unknown') crEls.proof.textContent = 'Секрет раскрыт (хэш ' + short + '), автоматическая проверка недоступна на этом устройстве';
  else crEls.proof.textContent = 'Хэш раунда ' + short + ': секрет откроется после краха, и точку можно будет проверить';
}

// #endregion

// #region Краш: график и множитель
// ---------- график и множитель ----------
// m в сотях по времени от старта полёта (только для показа; сервер считает так же, но решает именно он)
function crM100(elapsed) {
  const x = Math.max(0, elapsed) / CR_DOUBLING_MS;
  return Math.min(CR_TARGET_MAX, Math.floor(100 * Math.pow(2, x)));
}

function crDrawCurve(elapsedMs) {
  const m = Math.pow(2, Math.max(0, elapsedMs) / CR_DOUBLING_MS);
  const xmax = Math.max(8000, elapsedMs * 1.1);
  const ymax = Math.max(2, m * 1.15);
  const pts = [];
  const N = 36;
  let px = 0, py = 149, x = 0, y = 149;
  for (let i = 0; i <= N; i++) {
    const t = (elapsedMs * i) / N;
    px = x; py = y;
    x = (t / xmax) * 300;
    y = 149 - ((Math.pow(2, t / CR_DOUBLING_MS) - 1) / (ymax - 1)) * 148;
    pts.push((i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1));
  }
  crTip.x = x; crTip.y = y; crTip.px = px; crTip.py = py;      // конец линии и предыдущий узел
  const d = pts.join('');
  crEls.curve.setAttribute('d', d);
  if (crEls.smoke) crEls.smoke.setAttribute('d', d);       // толстый дымный след (виден только в скинах, которым он нужен)
}

// Конец линии в координатах графика 300×150 (x, y) и предыдущий узел (px, py): сцены скинов ставят ракету по ним
const crTip = { x: 0, y: 149, px: 0, py: 149 };

// События для сцен скинов (js/01a-skin-runtime.js): смена показанной фазы и раунда, кадр (только если на него кто-то подписан). Объект кадра один и тот же, сцена его не хранит.
const crFrame = { phase: '', roundId: 0, x100: 100, elapsed: 0, x: 0, y: 149, px: 0, py: 149 };
function crEmitSkin(phase, r, x100, elapsed) {
  if (cr.phaseShown !== phase || cr.phaseRound !== r.id) {
    cr.phaseShown = phase;
    cr.phaseRound = r.id;
    if (phase === 'flight') cr.flightSeen = r.id;
    skinEvents.emit('crash:phase', { phase, roundId: r.id, x100 });
  }
  if (skinEvents.has('crash:frame')) {
    crFrame.phase = phase; crFrame.roundId = r.id; crFrame.x100 = x100; crFrame.elapsed = elapsed;
    crFrame.x = crTip.x; crFrame.y = crTip.y; crFrame.px = crTip.px; crFrame.py = crTip.py;
    skinEvents.emit('crash:frame', crFrame);
  }
}

function crSetTone(tone) { crEls.chart.dataset.tone = tone; }
function crShowMult(x100) { crEls.mult.textContent = crText(x100); }

// Показанная фаза по локальным часам: приём ставок сменяется полётом в момент flight_start_ms, не дожидаясь ответа (график не запаздывает на интервал опроса);
// действия (ставка, вывод) разрешает только фаза сервера
function crDisplayPhase(r) {
  if (r.phase === 'betting' && crServerNow() >= r.flight_start_ms) return 'flight';
  return r.phase;
}

const crSeconds = (ms) => Math.max(0, Math.ceil(ms / 1000));

// Один кадр: множитель, кривая, подпись с обратным отсчётом, «Забрать N»
function crDraw() {
  const r = crRound();
  if (!r || cr.resync) return;
  const phase = crDisplayPhase(r);
  const me = crMe();
  if (phase === 'flight') {
    const elapsed = Math.max(0, crServerNow() - r.flight_start_ms);
    const x100 = crM100(elapsed);
    crSetTone('idle');
    crShowMult(x100);
    crDrawCurve(elapsed);
    crEmitSkin('flight', r, x100, elapsed);
    crEls.label.textContent = me && me.status === 'open' ? (me.target_x100 === null ? 'Нажмите «Забрать» до краха' : 'авто ' + crText(me.target_x100)) : 'Раунд идёт';
    if (me && me.status === 'open' && !cr.busy) crEls.cash.textContent = 'Забрать ' + formatCompact(Math.floor(me.bet * x100 / 100));
  } else if (phase === 'betting') {
    crSetTone('idle');
    crShowMult(100);
    crDrawCurve(0);
    crEmitSkin('betting', r, 100, 0);
    crEls.label.textContent = 'Приём ставок: ' + crSeconds(r.flight_start_ms - crServerNow()) + ' с';
  } else if (r.result) {
    crSetTone('crash');
    crShowMult(r.result.crash_x100);
    crDrawCurve(CR_DOUBLING_MS * Math.log2(Math.max(1, r.result.crash_x100 / 100)));
    crEmitSkin('result', r, r.result.crash_x100, CR_DOUBLING_MS * Math.log2(Math.max(1, r.result.crash_x100 / 100)));
    const next = typeof r.next_open_ms === 'number' ? ' · следующий раунд через ' + crSeconds(r.next_open_ms - crServerNow()) + ' с' : '';
    crEls.label.textContent = 'Крах ' + crText(r.result.crash_x100) + next;
  }
}

function crRaf() {
  cr.raf = 0;
  crDraw();
  if (crOnScreen()) cr.raf = requestAnimationFrame(crRaf);
}

const crOnScreen = () => activeTab === 'play' && currentGame === 'crash';

function crStartLoop() {
  crStopLoop();
  crDraw();
  if (reducedMotion()) cr.iv = setInterval(crDraw, 500);   // без плавной анимации: число реже
  else cr.raf = requestAnimationFrame(crRaf);
}

function crStopLoop() {
  if (cr.raf) cancelAnimationFrame(cr.raf);
  if (cr.iv) clearInterval(cr.iv);
  cr.raf = 0;
  cr.iv = 0;
}

// #endregion

// #region Краш: опрос
// ---------- опрос ----------
function crSchedulePoll(delay) {
  clearTimeout(cr.pollTimer);
  const r = crRound();
  cr.pollTimer = setTimeout(crPoll, delay !== undefined ? delay : (r && r.phase === 'result' ? CR_POLL_RESULT_MS : CR_POLL_MS));
}

async function crPoll() {
  cr.pollTimer = 0;
  if (!crOnScreen()) { crPause(); return; }              // ушли с экрана: ни кадров, ни опроса, пока не вернёмся (crResume)
  if (cr.busy || cr.polling || document.visibilityState !== 'visible') { crSchedulePoll(); return; }
  cr.polling = true;
  try {
    const q = cr.token ? '?v=' + encodeURIComponent(cr.token) : '';
    crApply(await fetchGameState('/api/crash/live' + q, validCrLive));
  } catch (e) {
    if (!cr.loaded) {                                     // первый ответ не пришёл: экран ошибки с кнопкой «Повторить»
      cr.error = true;
      setCrMessage(e.text || 'Нет связи с сервером', e.code, true);
      renderCrash();
    }                                                      // дальше сбой опроса не прерывает игру: следующая попытка через интервал (429 тоже)
  } finally {
    cr.polling = false;
    cr.resync = false;
  }
  if (crOnScreen()) crSchedulePoll();
}

function crPause() {
  skinSetActive('crash', false);
  crStopLoop();
  clearTimeout(cr.pollTimer);
  cr.pollTimer = 0;
}

skinSetHost('crash', () => crEls.chart);

function crResume() {
  if (!crOnScreen()) return;
  skinSetActive('crash', true);
  cr.resync = true;
  crStartLoop();
  clearTimeout(cr.pollTimer);
  cr.pollTimer = setTimeout(crPoll, 0);
}

// Запрос состояния сразу (после ставки или вывода и при открытии экрана): токен сбрасывается, чтобы пришёл полный ответ
function crPollNow() {
  cr.token = '';
  clearTimeout(cr.pollTimer);
  cr.pollTimer = setTimeout(crPoll, 0);
}

function loadCrash(reason) {
  if (!crOnScreen()) return Promise.resolve();
  if (srv.balance === null) loadServer('open');      // баланс для панели ставки приходит из общего ответа /api/me
  if (reason === 'manual') {
    cr.error = false;
    setCrMessage('', '', false);
    renderCrash();
  }
  crResume();
  return Promise.resolve();
}

// Применить ответ сервера: часы сверяются всегда, полный ответ заменяет состояние, «unchanged» меняет только фазу и множитель
function crApply(d) {
  cr.loaded = true;
  cr.error = false;
  cr.syncServerMs = d.server_ms;
  cr.syncAt = performance.now();
  if (d.unchanged === true) {
    const r = crRound();
    if (r && ['betting', 'flight', 'result', 'idle'].includes(d.phase)) {
      r.phase = d.phase;
      r.m100 = typeof d.m100 === 'number' ? d.m100 : null;
    }
    return;
  }
  cr.state = d;
  cr.token = typeof d.v === 'string' ? d.v : '';
  if (d.me === undefined) d.me = null;
  setCrMessage('', '', false);
  const r = d.round;
  if (r && r.result) crCheckProof(r);
  crSettle(r, d.me);
  if (r && r.result && !cr.crashSeen.has(r.id)) {
    if (cr.crashSeen.size > 30) cr.crashSeen.clear();
    cr.crashSeen.add(r.id);
    if (d.me) skinEvents.emit('round:end', { game: 'crash' });
    skinEvents.emit('crash:crash', { roundId: r.id, x100: r.result.crash_x100, fresh: cr.flightSeen === r.id, mine: d.me ? d.me.status : null });
  }
  renderCrash();
}

// Вывод (в том числе автовывод в полёте) и итог раунда с моей ставкой: один раз за раунд подтягиваем баланс и опыт с сервера и даём отклик
function crSettle(r, me) {
  if (!r || !me) return;
  if (me.status === 'cashed' && !cr.cashSeen.has(r.id)) {
    cr.cashSeen.add(r.id);
    skinEvents.emit('crash:cashout', { roundId: r.id, x100: me.cashed_x100, fresh: cr.flightSeen === r.id });
    haptic('success');
    loadServer('after');
  } else if (me.status === 'lost' && r.result && !cr.settled.has(r.id)) {
    cr.settled.add(r.id);
    haptic('error');
    loadServer('after');
  }
}

// #endregion

// #region Краш: экран
// ---------- отрисовка экрана (не кадр графика) ----------
function renderCrHistory() {
  const hist = cr.state ? cr.state.history.slice(0, CR_HISTORY) : [];
  crEls.history.hidden = hist.length === 0;
  crEls.history.textContent = '';
  hist.forEach((h) => {
    const li = document.createElement('li');
    li.textContent = crText(h.crash_x100);
    if (h.crash_x100 >= 200) li.className = 'high';
    crEls.history.appendChild(li);
  });
}

function renderCrFeed() {
  const bets = cr.state ? cr.state.bets : [];
  const r = crRound();
  crEls.feedTitle.textContent = bets.length ? 'Ставки в раунде: ' + bets.length : (cr.loaded ? 'Ставок пока нет' : '');
  crEls.feed.textContent = '';
  bets.forEach((b) => {
    const li = document.createElement('li');
    li.dataset.status = b.status;
    const name = document.createElement('span');
    name.className = 'cr-feed-name';
    name.textContent = b.name;
    const amount = document.createElement('span');
    amount.className = 'cr-feed-bet';
    amount.textContent = formatCompact(b.bet);
    amount.title = formatNumber(b.bet);
    const res = document.createElement('span');
    res.className = 'cr-feed-res';
    if (b.status === 'cashed') res.textContent = crText(b.cashed_x100) + ' +' + formatCompact(b.payout - b.bet);
    else if (b.status === 'lost') res.textContent = r && r.result ? '−' + formatCompact(b.bet) : '…';
    else res.textContent = '…';
    li.append(name, amount, res);
    crEls.feed.appendChild(li);
  });
}

function renderCrBanner() {
  const r = crRound();
  const me = crMe();
  crEls.banner.hidden = !r;
  crEls.banner.classList.remove('win', 'lose');
  crEls.bannerDetail.textContent = '';
  crEls.bannerDetail.title = '';
  crEls.bannerTitle.title = '';
  if (!r) return;
  const phase = r.phase;
  if (phase === 'betting') {
    if (me) {
      crEls.bannerTitle.textContent = 'Ставка принята: ' + formatCompact(me.bet);
      crEls.bannerDetail.textContent = me.target_x100 === null ? 'Забирайте вручную в полёте' : 'Автовывод на ' + crText(me.target_x100);
    } else {
      crEls.bannerTitle.textContent = 'Сделайте ставку';
      crEls.bannerDetail.textContent = 'Раунд общий: точка краха одна на всех';
    }
  } else if (phase === 'flight') {
    if (me && me.status === 'cashed') {
      crEls.bannerTitle.textContent = 'Выведено ' + crText(me.cashed_x100) + ': +' + formatCompact(me.payout - me.bet);
      crEls.bannerTitle.title = 'Выигрыш +' + formatNumber(me.payout - me.bet);
      crEls.banner.classList.add('win');
    } else if (me) {
      crEls.bannerTitle.textContent = 'Ваша ставка в игре: ' + formatCompact(me.bet);
    } else {
      crEls.bannerTitle.textContent = 'Раунд идёт';
      crEls.bannerDetail.textContent = 'Ставки следующего раунда откроются после краха';
    }
  } else if (r.result) {
    const crashX = crText(r.result.crash_x100);
    if (me && me.status === 'cashed') {
      const profit = me.payout - me.bet;
      crEls.bannerTitle.textContent = 'Выигрыш +' + formatCompact(profit) + ' (' + crText(me.cashed_x100) + ')';
      crEls.bannerTitle.title = 'Выигрыш +' + formatNumber(profit);
      crEls.bannerDetail.textContent = 'Крах был ' + crashX;
      crEls.banner.classList.add('win');
    } else if (me) {
      crEls.bannerTitle.textContent = 'Крах ' + crashX + ', потеряно ' + formatCompact(me.bet);
      crEls.bannerTitle.title = 'Потеряно ' + formatNumber(me.bet);
      crEls.banner.classList.add('lose');
    } else {
      crEls.bannerTitle.textContent = 'Крах ' + crashX;
      crEls.bannerDetail.textContent = 'Вы в этом раунде не играли';
    }
  } else {
    crEls.bannerTitle.textContent = 'Ждём следующий раунд';
  }
}

// Фишки ставки по серверному балансу (те же номиналы, что в других играх)
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

// шапка и кнопки без перерисовки графика
function renderCrashChrome() {
  if (cr.balance !== null) {
    crEls.balance.classList.remove('skeleton');
    crEls.balance.textContent = spacedNumber(cr.balance);
    fitNumberFont(crEls.balance, crEls.balance.textContent.length);
  }
  crEls.switchBtn.disabled = cr.busy;
  crEls.cash.disabled = cr.busy;
  if (cr.busy && crMe() && crMe().status === 'open') crEls.cash.textContent = 'Фиксируем…';
}

function renderCrash() {
  if (cr.balance === null && srv.balance !== null) cr.balance = srv.balance;      // у живого состояния своего баланса нет: стартовый берётся из общего (/api/me)
  const loading = !cr.loaded;
  crEls.skel.hidden = !(loading && !cr.error);
  crEls.chart.hidden = loading;
  const r = crRound();
  const me = crMe();
  const betting = !!r && r.phase === 'betting';
  const cashing = !!r && r.phase === 'flight' && !!me && me.status === 'open';
  crEls.bets.hidden = loading || cashing;
  crEls.actions.hidden = !cashing;
  renderCrashChrome();
  renderCrHistory();
  renderCrFeed();
  renderCrBanner();
  renderCrProof();
  renderCrChips();
  syncCrChips();
  const canBet = betting && !me && !cr.busy && cr.balance !== null;
  crEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = !canBet; });
  crEls.start.disabled = !canBet;
  crEls.start.textContent = me && betting ? 'Ставка принята' : (betting ? 'Поставить' : 'Ждём приёма ставок');
  if (cr.loaded && crOnScreen() && !cr.raf && !cr.iv) crStartLoop();
  crDraw();
  refreshBetPanels();
}

// #endregion

// #region Краш: запросы
// ---------- действия ----------
async function crAct(path, body, validate, onOk, failNotes) {
  if (cr.busy) return;
  if (!(tg && tg.initData)) { setCrNotice('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setCrNotice('Ошибка'); return; }
  cr.busy = true;
  setCrNotice('');
  renderCrash();
  const result = await postWithRetries(() => postMinesOnce(path, { request_id: id, ...body }, validate));
  cr.busy = false;
  if (result && result.kind === 'ok') {
    try {
      onOk(result.data);
    } catch (e) {
      setCrNotice('Не удалось показать результат. Состояние обновлено');
    }
  } else {
    setCrNotice(actionFailure(result, failNotes).note);
  }
  crPollNow();
  loadServer('after');
}

function crBet() {
  const bet = Number(crEls.bet.value);
  const target = crParseTarget();
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > CR_BET_MAX) { setCrNotice('Введите целую ставку от 1 до ' + formatNumber(CR_BET_MAX)); return; }
  if (target === undefined) { setCrNotice('Авто-вывод: от 1.01 до 1000 (или пусто для ручного режима)'); return; }
  if (cr.balance !== null && bet > cr.balance) { setCrNotice('Не хватает фишек'); return; }
  const body = target === null ? { bet } : { bet, target_x100: target };
  crAct('/api/crash/live/bet', body, validCrBetDone, (d) => {
    cr.balance = d.balance;
    skinEvents.emit('crash:bet', { roundId: d.round_id });
    skinEvents.emit('bet:placed', { game: 'crash' });
    haptic('light');
  }, {
    betting_closed: 'Приём ставок закрыт: дождитесь следующего раунда',
    already_bet: 'Вы уже поставили в этом раунде',
    room_full: 'В ленте слишком много ставок: повторите в следующем раунде',
    insufficient_funds: 'Не хватает фишек',
    balance_limit: 'Баланс слишком велик для такой ставки'
  });
}

function crCash() {
  const me = crMe();
  if (!me || me.status !== 'open' || cr.busy) return;
  crAct('/api/crash/live/cashout', {}, validCrCashDone, (d) => {
    cr.balance = d.balance;
    haptic('success');
  }, {
    too_early: 'Слишком рано: вывод возможен с ×1.01',
    round_over: 'Раунд уже закончился',
    no_bet: 'У вас нет ставки в этом раунде'
  });
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
crEls.start.addEventListener('click', crBet);
crEls.cash.addEventListener('click', crCash);
crEls.retry.addEventListener('click', () => loadCrash('manual'));
crEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadCrash('visible');
});

// #endregion
