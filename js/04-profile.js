// #region Баланс и профиль (/api/me)
// ---------- серверное состояние: общее для экрана игры и вкладки «Профиль» ----------
// Баланс только серверный и хранится в памяти страницы, в localStorage он не пишется.

const srv = {
  loaded: false,   // получен ли хотя бы один ответ
  balance: 0,
  rate: 0,
  deadline: 0,     // performance.now(), когда таймер дойдёт до нуля
  error: null,     // { text, code, retry } последней неудачной загрузки
  incomeLevel: null,
  storageLevel: null,
  level: null,        // уровень профиля из /api/me
  farm: null,         // блок фермы из /api/me: {income_per_hour, per_minute_estimate, next_tick_in_s, hours_cap, accrued_now}
  activeGame: null,   // незавершённая игра из /api/me: "mines" | "blackjack" | "crash" | "hilo" | null
  chat: null          // атрибуция и бонусы беседы из /api/me: {in_chat, bonus_pct, active_today, boost_until, boost_gems} | null
};
let lobbyStartDecided = false;   // при запуске выбор «титульный экран или активная игра» делается один раз

const profileEls = {
  data: document.getElementById('profile-data'),
  rate: document.getElementById('profile-rate'),
  stats: document.getElementById('profile-stats'),
  statGames: document.getElementById('stat-games'),
  statNote: document.getElementById('stat-note'),
  best: document.getElementById('profile-best'),
  bestValue: document.getElementById('profile-best-value'),
  skel: document.getElementById('profile-skel'),
  name: document.getElementById('profile-name'),
  avatar: document.getElementById('profile-avatar'),
  msg: document.getElementById('profile-msg'),
  code: document.getElementById('profile-code'),
  retry: document.getElementById('profile-retry')
};

let started = false;            // игра полностью собрана
let srvInFlight = false;
let srvLastRequestAt = -Infinity; // performance.now() последнего запроса
let srvLastFailed = false;
let srvFetchTimer = null;       // отложенный запрос (ноль таймера или автоповтор после ошибки)

// данные нужны, только пока открыт экран рулетки или «Профиль»
const srvWanted = () => activeTab === 'profile' || activeTab === 'farm'
  || (activeTab === 'play' && (currentGame === 'lobby' || currentGame === 'roulette' || currentGame === 'mines' || currentGame === 'keno' || currentGame === 'blackjack' || currentGame === 'crash' || currentGame === 'hilo'));

// «Статистика»: счётчики по играм за всё время (раундов и доля раундов с выигрышем); без раундов блока нет
const statPlural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many));

function renderProfileStats() {
  const st = srv.stats;
  const games = st ? st.games.filter((g) => isBestGame(g.game)) : [];
  profileEls.stats.hidden = !games.length;
  profileEls.statGames.textContent = '';
  if (!games.length) return;
  const row = (name, rounds, wins) => {
    const li = document.createElement('li');
    const a = document.createElement('span');
    a.textContent = name;
    const b = document.createElement('strong');
    b.textContent = formatCompact(rounds) + ' ' + statPlural(rounds, 'раунд', 'раунда', 'раундов') + ' · ' + Math.round((wins * 100) / Math.max(1, rounds)) + '% побед';
    li.append(a, b);
    profileEls.statGames.appendChild(li);
  };
  const total = games.reduce((acc, g) => ({ rounds: acc.rounds + g.rounds, wins: acc.wins + g.wins }), { rounds: 0, wins: 0 });
  row('Всего', total.rounds, total.wins);
  games.forEach((g) => row(BEST_GAMES[g.game], g.rounds, g.wins));
  profileEls.statNote.textContent = isCount(st.since) ? 'Учёт ведётся с ' + bestDayText(st.since) : '';
}

function renderProfile() {
  renderProfileIdentity();
  if (srv.error) {
    profileEls.skel.hidden = true;
    profileEls.data.hidden = true;
    profileEls.msg.textContent = srv.error.text;
    profileEls.code.textContent = 'код: ' + srv.error.code;
    profileEls.retry.hidden = !srv.error.retry;
  } else if (srv.loaded) {
    profileEls.rate.textContent = formatCompact(srv.rate);       // экономика (баланс, улучшения, таймер) живёт на вкладке «Ферма»
    renderProfileStats();
    const bw = srv.bestWin;      // рекорд: сумма, игра и множитель; без выигрышей строки нет
    profileEls.best.hidden = !bw || typeof isBestGame !== 'function' || !isBestGame(bw.game);
    if (bw && !profileEls.best.hidden) profileEls.bestValue.textContent = '+' + formatCompact(bw.net_amount) + ' · ' + BEST_GAMES[bw.game] + (isCount(bw.mult_x100) && bw.mult_x100 > 0 ? ' · ×' + bestMultText(bw.mult_x100) : '');
    profileEls.skel.hidden = true;
    profileEls.data.hidden = false;
    profileEls.msg.textContent = '';
    profileEls.code.textContent = '';
    profileEls.retry.hidden = true;
  } else {
    profileEls.data.hidden = true;
    profileEls.skel.hidden = false; // скелетон вместо спиннера
    profileEls.msg.textContent = '';
    profileEls.code.textContent = '';
    profileEls.retry.hidden = true;
  }
}

// Имя и аватар из Telegram (initDataUnsafe) только для показа на этом экране: никуда не отправляются,
// в localStorage не пишутся; картинок нет, аватар — круг с первой буквой имени
const initialOf = (name) => (Array.from(String(name || '').trim())[0] || '·');

function renderProfileIdentity() {
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  const name = user && typeof user.first_name === 'string' ? user.first_name : '';
  profileEls.name.textContent = name;
  profileEls.avatar.textContent = initialOf(name);
  profileEls.name.parentElement.hidden = !name;
}

// renderKeno и renderLobby появляются ниже по файлу (до их объявления остаются null)
var kenoRender = null; // eslint-disable-line no-var
var raceRender = null; // eslint-disable-line no-var
var lobbyRender = null; // eslint-disable-line no-var

function renderAll() {
  renderProfile();
  renderFarmIncome();
  renderBets();
  if (kenoRender) kenoRender();
  if (raceRender) raceRender();
  if (lobbyRender) lobbyRender();
}

// Раз в секунду обновляем таймеры (по монотонным часам, а не по часам устройства)
setInterval(() => {
  if (!started) return;
  renderFarmIncome();
  renderStatus();
}, 1000);

// Запланировать запрос не раньше, чем через delay мс, и не чаще REQUEST_GAP_MS
function scheduleServerFetch(delay) {
  clearTimeout(srvFetchTimer);
  const gapLeft = REQUEST_GAP_MS - (performance.now() - srvLastRequestAt);
  // +20 мс запаса: таймер браузера может сработать чуть раньше расчётного времени
  srvFetchTimer = setTimeout(() => loadServer('timer'), Math.max(delay, gapLeft > 0 ? gapLeft + 20 : 0));
}

function failServer(text, code, retry) {
  lobbyStartDecided = true;   // нет ответа: остаёмся на титульном экране
  srvLastFailed = true;
  srv.error = { text, code, retry };
  renderAll();
  if (retry) scheduleServerFetch(ERROR_RETRY_MS);
}

// Единственная функция запроса /api/me. reason: 'open' | 'visible' (с ограничением по частоте)
// | 'timer' | 'manual' | 'after' (ручной запрос и запрос после раунда не теряются: откладываются)
// Балансы игр хранятся у каждой игры отдельно: подпись нужна, чтобы устаревший ответ /api/me не затёр свежие значения
const balanceSignature = () => [srv.balance, ...gameRegistry.filter((g) => g.state).map((g) => g.state.balance)].join('|');
// идёт запрос или анимация какой-либо игры: баланс на экране менять нельзя, данные ставятся в очередь (повтор через секунду)
const anyRoundBusy = () => gameRegistry.some((g) => g.busy());
let srvNeedsTick = false;        // вкладка была скрыта, когда пришло время минутного запроса: при возврате обновляем сразу

async function loadServer(reason) {
  if (srvInFlight || !srvWanted()) return;
  if (reason === 'timer' && document.visibilityState !== 'visible') {
    srvNeedsTick = true;    // скрытая вкладка не опрашивает сервер; при возврате в приложение обновим сразу
    return;
  }
  if (anyRoundBusy()) {
    // во время запроса раунда и анимации баланс на экране менять нельзя
    scheduleServerFetch(1000);
    return;
  }
  const now = performance.now();
  const sinceLast = now - srvLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason !== 'open' && reason !== 'visible') scheduleServerFetch(0);
    return;
  }
  if ((reason === 'open' || reason === 'visible') && !srvNeedsTick && sinceLast < (srvLastFailed ? ERROR_RETRY_MS : REFRESH_MIN_MS)) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    failServer('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(srvFetchTimer);
  srvInFlight = true;
  srvNeedsTick = false;
  srvLastRequestAt = now;
  const sigBefore = balanceSignature();
  if (!srv.loaded && !srv.error) renderAll();

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/me', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      // initData живёт ограниченное время, повторять запрос бессмысленно
      failServer('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (!res.ok) {
      failServer('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    const ok = (v, max) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= max;
    // баланс: целое от 0 до MAX_SAFE_INTEGER (серверный потолок, bot/roulette.py MAX_SAFE_INT)
    if (!d || !isCount(d.balance) || !ok(d.rate, 1e9) || !ok(d.seconds_to_next, 86400)) {
      failServer('Нет связи с сервером', 'ответ', true);
      return;
    }
    if (anyRoundBusy() || balanceSignature() !== sigBefore) {
      // пока шёл запрос, начался или закончился раунд: этот ответ уже мог устареть
      scheduleServerFetch(1000);
      return;
    }
    srvLastFailed = false;
    srv.error = null;
    const prevBalance = srv.loaded ? srv.balance : null;
    srv.loaded = true;
    srv.balance = d.balance;
    srv.rate = d.rate;
    // уровни улучшений (если сервер их прислал) нужны только для показа в профиле
    srv.incomeLevel = isCount(d.income_level) ? d.income_level : null;
    srv.storageLevel = isCount(d.storage_level) ? d.storage_level : null;
    srv.deadline = performance.now() + d.seconds_to_next * 1000;
    srv.level = isCount(d.level) ? d.level : null;
    srv.activeGame = ['mines', 'blackjack', 'crash', 'hilo'].includes(d.active_game) ? d.active_game : null;
    let patinaObj = undefined;
    if (d.cosmetics && typeof d.cosmetics.patina === 'object' && d.cosmetics.patina !== null) {
      patinaObj = {};
      for (const [k, v] of Object.entries(d.cosmetics.patina)) {
        if (Number.isInteger(v) && v >= 0 && v <= 4) patinaObj[k] = v;
      }
    }
    applySkins(d.cosmetics && d.cosmetics.equipped, patinaObj, d.cosmetics && d.cosmetics.patina_info);       // внешний вид по надетому (только оформление)
    skinSetOwnSets(d.cosmetics && d.cosmetics.complete_sets);       // собранные коллекции: эффекты полного набора в сценах
    setOwnCosmetics(d.cosmetics);                           // рамка и значок у себя (профиль, рейтинг)
    const f = d.farm;
    srv.farm = f && isCount(f.income_per_hour) && typeof f.per_minute_estimate === 'string' && /^\d+\.\d$/.test(f.per_minute_estimate)
      && isCount(f.next_tick_in_s) && isCount(f.hours_cap) && isCount(f.accrued_now) ? f : null;
    const bw = d.best_win;
    srv.bestWin = bw && typeof bw === 'object' && typeof bw.game === 'string' && isCount(bw.net_amount) && bw.net_amount > 0 ? bw : null;       // личный рекорд для профиля
    const gs = d.stats;
    srv.stats = gs && typeof gs === 'object' && Array.isArray(gs.games)
      && gs.games.every((g) => g && typeof g.game === 'string' && isCount(g.rounds) && isCount(g.wins) && g.wins <= g.rounds) && (gs.since === null || isCount(gs.since)) ? gs : null;
    const c = d.chat;
    srv.chat = c && typeof c === 'object' && typeof c.in_chat === 'boolean'
      && isCount(c.bonus_pct) && c.bonus_pct <= 45 && isCount(c.active_today)
      && (c.boost_until === null || isCount(c.boost_until)) && isCount(c.boost_gems) ? c : null;
    renderAll();
    // сумма для «+N»: по accrued_now этого запроса; при возврате в приложение другой запрос (например, экрана фермы) мог подтянуть
    // начисление раньше, тогда берётся прирост баланса (не больше максимума накопления)
    let gained = srv.farm ? srv.farm.accrued_now : 0;
    if (gained === 0 && reason === 'visible' && srv.farm && prevBalance !== null && d.balance > prevBalance
      && d.balance - prevBalance <= srv.farm.income_per_hour * srv.farm.hours_cap) gained = d.balance - prevBalance;
    applyAccrualTick(gained);
    if (!lobbyStartDecided) {
      // запуск: если у игрока есть незавершённая игра, сразу открываем её экран (он сам восстановит раунд)
      lobbyStartDecided = true;
      if (srv.activeGame && activeTab === 'play' && currentGame === 'lobby') selectGame(srv.activeGame);
    }
    scheduleServerFetch(d.seconds_to_next * 1000 + ZERO_DELAY_MS);
  } catch (e) {
    // fetch не различает сбой сети и запрет CORS, поэтому код с вопросом
    const aborted = e && e.name === 'AbortError';
    failServer('Нет связи с сервером', aborted ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    srvInFlight = false;
  }
}

profileEls.retry.addEventListener('click', () => loadServer('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadServer('visible');
});

// #endregion

// #region Referral
const refEls = {
  card: document.getElementById('referral-card'),
  text: document.getElementById('referral-text'),
  invited: document.getElementById('referral-invited'),
  qualified: document.getElementById('referral-qualified'),
  commission: document.getElementById('referral-commission'),
  lead: document.getElementById('referral-lead'),
  steps: document.getElementById('ref-steps'),
  none: document.getElementById('referral-none'),
  stats: document.getElementById('referral-stats'),
  rulesBtn: document.getElementById('referral-rules-btn'),
  send: document.getElementById('referral-send'),
  copy: document.getElementById('referral-copy'),
  msg: document.getElementById('referral-msg'),
  linkText: document.getElementById('referral-link-text'),
  actions: document.getElementById('referral-actions')
};

let refLastRequest = -Infinity;    // performance.now() последнего запроса; -Infinity: ещё не запрашивали (иначе первые 60 с после старта карточка не грузилась бы)
let refData = null;
let refError = false;

const REF_MILESTONE_NAMES = { ref_scout: 'значок «Камень»', ref_beacon: 'рамка «Колонна»', ref_arch: 'рамка «Арка»' };
const REF_MILESTONE_SHORT = { ref_scout: ['Камень', 'значок'], ref_beacon: ['Колонна', 'рамка'], ref_arch: ['Арка', 'рамка'] };

function validReferral(d) {
  if (!d || typeof d !== 'object') return false;
  if (!Number.isInteger(d.invited) || d.invited < 0) return false;
  if (!Number.isInteger(d.qualified) || d.qualified < 0) return false;
  if (!Number.isInteger(d.commission_earned) || d.commission_earned < 0) return false;
  if (d.link !== null && (typeof d.link !== 'string' || !d.link.startsWith('https://t.me/') || d.link.length > 400)) return false;
  if (!Array.isArray(d.milestones) || d.milestones.length > 10) return false;
  for (const m of d.milestones) {
    if (!m || !Number.isInteger(m.count) || m.count <= 0 || typeof m.item !== 'string' || typeof m.reached !== 'boolean') return false;
  }
  const r = d.rules;
  if (!r || typeof r !== 'object') return false;
  const keys = ['invitee_chips', 'inviter_chips', 'inviter_gems', 'qualify_hours', 'qualify_level', 'qualify_rounds', 'commission_pct', 'commission_days', 'commission_cap', 'founder_chips', 'founder_gems', 'founder_players', 'founder_level'];
  for (const k of keys) {
    if (!Number.isInteger(r[k]) || r[k] <= 0) return false;
  }
  return true;
}

// Три шага на линии прогресса: 3 «Камень» → 10 «Колонна» → 30 «Арка»; линия заполняется по числу друзей, дошедших до награды
function renderRefSteps(d) {
  refEls.steps.textContent = '';
  const ms = d.milestones.slice().sort((a, b) => a.count - b.count);
  if (!ms.length) return;
  const n = ms.length;
  const pos = (i) => (i + 0.5) / n;                              // центр i-го узла по ширине
  let p = 0;
  let prevCount = 0;
  let prevPos = 0;
  for (let i = 0; i < n; i++) {
    if (d.qualified >= ms[i].count) { p = pos(i); prevCount = ms[i].count; prevPos = pos(i); continue; }
    p = prevPos + ((d.qualified - prevCount) / (ms[i].count - prevCount)) * (pos(i) - prevPos);
    break;
  }
  const line = document.createElement('li');
  line.className = 'ref-line';
  line.setAttribute('aria-hidden', 'true');
  const fill = document.createElement('i');
  fill.style.transform = 'scaleX(' + p.toFixed(3) + ')';
  line.appendChild(fill);
  refEls.steps.appendChild(line);
  ms.forEach((m) => {
    const li = document.createElement('li');
    li.className = 'ref-step' + (m.reached ? ' reached' : '');
    const short = REF_MILESTONE_SHORT[m.item] || ['Награда', ''];
    const num = document.createElement('b');
    num.textContent = m.reached ? '✓' : String(m.count);
    const name = document.createElement('span');
    name.textContent = short[0];
    const kind = document.createElement('small');
    kind.textContent = (m.reached ? '' : m.count + ' · ') + short[1];
    li.append(num, name, kind);
    refEls.steps.appendChild(li);
  });
}

function renderReferral() {
  if (refError) {
    refEls.card.hidden = false;
    refEls.text.textContent = 'Ошибка загрузки реферальных данных.';
    refEls.actions.hidden = true;
    refEls.linkText.hidden = true;
    refEls.msg.hidden = false;
    refEls.msg.textContent = '';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'action';
    btn.textContent = 'Повторить';
    btn.addEventListener('click', () => { refLastRequest = -Infinity; loadReferral(); });
    refEls.msg.appendChild(btn);
    return;
  }
  if (!refData) {
    refEls.card.hidden = true;
    return;
  }
  refEls.card.hidden = false;
  refEls.msg.hidden = true;
  refEls.actions.hidden = false;
  const r = refData.rules;
  refEls.text.textContent = `Друг получит ${r.invitee_chips} фишек при первом входе. Когда он проживёт ${r.qualify_hours} ч, дойдёт до ${r.qualify_level} уровня и сыграет ${r.qualify_rounds} раундов, ты получишь ${r.inviter_chips} фишек и ${r.inviter_gems} 💎, а ещё ${r.commission_days} дней ${r.commission_pct}% от того, что казино выиграет у друга (до ${r.commission_cap} фишек с одного друга). Добавь бота в беседу: когда в ней начнут играть ${r.founder_players} человек не ниже ${r.founder_level} уровня, получишь ${r.founder_chips} фишек и ${r.founder_gems} 💎.`;
  refEls.invited.textContent = refData.invited;
  refEls.qualified.textContent = refData.qualified;
  refEls.commission.textContent = refData.commission_earned;
  refEls.lead.textContent = `За каждого друга, дошедшего до награды: ${r.inviter_chips} фишек и ${r.inviter_gems} 💎.`;
  refEls.none.hidden = refData.invited > 0;                    // нулей не показываем, пока нет данных: одна строка «Пока никого»
  refEls.stats.hidden = refData.invited === 0;
  renderRefSteps(refData);

  if (refData.link === null) {
    refEls.actions.hidden = true;
    refEls.linkText.hidden = false;
    refEls.linkText.textContent = 'Ссылка приглашения пока недоступна';
  } else {
    refEls.actions.hidden = false;
    refEls.linkText.hidden = true;
    refEls.linkText.textContent = refData.link; // for fallback
  }
}

async function loadReferral() {
  if (!tg || !tg.initData) return;
  const now = performance.now();
  if (now - refLastRequest < 60000) return;
  refLastRequest = now;
  
  try {
    const res = await fetch(API_URL + '/api/referral', {
      headers: { Authorization: 'tma ' + tg.initData },
      cache: 'no-store'
    });
    if (!res.ok) throw new Error('status ' + res.status);
    const d = await res.json();
    if (!validReferral(d)) throw new Error('invalid format');
    refData = d;
    refError = false;
  } catch (e) {
    refError = true;
  }
  renderReferral();
}

new MutationObserver((mutations) => {
  for (const m of mutations) {
    if (m.type === 'attributes' && m.attributeName === 'hidden') {
      if (!profileEls.data.parentElement.hidden) loadReferral();
    }
  }
}).observe(profileEls.data.parentElement, { attributes: true });

refEls.rulesBtn.addEventListener('click', () => {
  refEls.text.hidden = !refEls.text.hidden;
  refEls.rulesBtn.setAttribute('aria-expanded', String(!refEls.text.hidden));
});

refEls.send.addEventListener('click', () => {
  if (!refData || !refData.link) return;
  const url = 'https://t.me/share/url?url=' + encodeURIComponent(refData.link) + '&text=' + encodeURIComponent('Заходи в Депную');
  if (tg.openTelegramLink) {
    tg.openTelegramLink(url);
  } else if (tg.openLink) {
    tg.openLink(url);
  } else {
    refEls.msg.hidden = false;
    refEls.msg.textContent = 'Отправка работает только внутри Telegram';
  }
});

function showRefMsg(text) {
  refEls.msg.hidden = false;
  refEls.msg.textContent = text;
  setTimeout(() => { refEls.msg.hidden = true; }, 2000);
}

refEls.copy.addEventListener('click', async () => {
  if (!refData || !refData.link) return;
  try {
    await navigator.clipboard.writeText(refData.link);
    showRefMsg('Скопировано');
  } catch (e) {
    refEls.linkText.hidden = false;
  }
});
// #endregion
