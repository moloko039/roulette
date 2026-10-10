// #region Краш
// ---------- живой краш (docs/CRASH_LIVE.md) ----------
// Один общий раунд на весь сервер: приём ставок, полёт, итог, снова приём. Время и исход только на сервере. Клиент опрашивает GET /api/crash/live
// (раз в 300 мс в приёме ставок и полёте, раз в секунду в паузе итога; повторный запрос с токеном изменений v получает маленький ответ «unchanged»),
// сверяет часы по server_ms и рисует рост множителя по времени старта полёта. Точка краха и секрет раскрываются только в фазе итога; клиент сам проверяет
// честность раунда: sha256(секрет) совпадает с хэшем, показанным до раунда, и точка краха выведена из секрета той же формулой (hmac-sha256, как на сервере).
// Лента ставок: беседа видит ставки только своей беседы (с именами), вне беседы общая анонимная лента (имена «Игрок N»).
const CR_POLL_MS = 300;          // опрос в приёме ставок и полёте
const CR_POLL_RESULT_MS = 1000;  // опрос в паузе итога
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
const crRound = () => (cr.state && cr.state.round) || null;
const crMe = () => (cr.state && cr.state.me) || null;
const crServerNow = () => cr.syncServerMs + (performance.now() - cr.syncAt);

function setCrNotice(text) { crEls.notice.textContent = text; }
function setCrMessage(text, code, retry) {
  crEls.msg.textContent = text;
  crEls.code.textContent = code ? 'код: ' + code : '';
  crEls.retry.hidden = !retry;
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

crEls.retry.addEventListener('click', () => loadCrash('manual'));
crEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadCrash('visible');
});

// #endregion
