// #region Ферма
// ---------- вкладка «Ферма»: улучшения дохода и хранилища за фишки ----------
// Все числа и состояния приходят с сервера (GET /api/farm), клиент ничего не считает и не хранит.
// Покупка: POST /api/farm/buy, до 3 попыток с одним request_id; 429 повторяется в postJson.
const farmEls = {
  body: document.getElementById('farm-body'),
  scene: document.getElementById('farm-scene'),
  goal: document.getElementById('farm-goal'),
  goalText: document.getElementById('farm-goal-text'),
  goalFill: document.getElementById('farm-goal-fill'),
  skel: document.getElementById('farm-skel'),
  msg: document.getElementById('farm-msg'),
  code: document.getElementById('farm-code'),
  retry: document.getElementById('farm-retry'),
  note: document.getElementById('farm-note'),
  income: document.getElementById('farm-income'),
  incHour: document.getElementById('farm-inc-hour'),
  incMin: document.getElementById('farm-inc-min'),
  incTimer: document.getElementById('farm-inc-timer'),
  incNote: document.getElementById('farm-inc-note'),
  level: document.getElementById('farm-level'),
  balance: document.getElementById('farm-balance'),
  bar: document.getElementById('farm-bar'),
  barFill: document.getElementById('farm-bar-fill'),
  progress: document.getElementById('farm-progress'),
  hintBtn: document.getElementById('farm-hint-btn'),
  hint: document.getElementById('farm-hint'),
  staked: document.getElementById('farm-staked'),
  slots: document.getElementById('farm-slots'),
  cards: document.getElementById('farm-cards'),
  chatBonus: document.getElementById('farm-chat-bonus'),
  chatBonusLabel: document.getElementById('farm-chat-bonus-label'),
  chatBonusVal: document.getElementById('farm-chat-bonus-val'),
  chatActiveRow: document.getElementById('farm-chat-active-row'),
  chatActiveVal: document.getElementById('farm-chat-active-val'),
  chatBoostUntil: document.getElementById('farm-chat-boost-until'),
  chatNote: document.getElementById('farm-chat-note'),
  chatBoostBtn: document.getElementById('farm-chat-boost-btn')
};
const FARM_KINDS = { income: 'Доход', storage: 'Хранилище' };
const FARM_REASONS = {
  level_locked: 'Нужен уровень профиля',
  insufficient_funds: 'Не хватает фишек',
  max_level: 'Максимальный уровень'
};

let farmInFlight = false;
let farmLastRequestAt = -Infinity;
let farmTimer = null;
let farmHasData = false;
let farmBusy = false;   // идёт покупка
let farmData = null;    // последний ответ GET /api/farm

const farmCards = {};
Object.keys(FARM_KINDS).forEach((kind) => {
  const card = document.createElement('article');
  card.className = 'farm-card';
  const head = document.createElement('div');
  head.className = 'farm-card-head';
  const title = document.createElement('h3');
  title.textContent = FARM_KINDS[kind];
  const lvl = document.createElement('span');
  lvl.className = 'farm-lvl';
  head.append(title, lvl);
  const effect = document.createElement('div');
  effect.className = 'farm-effect';
  const cost = document.createElement('div');
  cost.className = 'farm-cost';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'action farm-buy';
  btn.textContent = 'Улучшить';
  btn.addEventListener('click', () => buyUpgrade(kind));
  const reason = document.createElement('small');
  reason.className = 'farm-reason';
  card.append(head, effect, cost, btn, reason);
  farmEls.cards.appendChild(card);
  farmCards[kind] = { card, lvl, effect, cost, btn, reason };
});

function setFarmNote(text, kind = '') {
  farmEls.note.textContent = text;
  farmEls.note.className = 'farm-note' + (kind ? ' ' + kind : '');
}

function showFarmMessage(text, code, canRetry) {
  farmEls.skel.hidden = true;
  farmEls.body.hidden = true;
  farmEls.msg.textContent = text;
  farmEls.code.textContent = code ? 'код: ' + code : '';
  farmEls.retry.hidden = !canRetry;
  farmHasData = false;
}

const isLevelPair = (o, extra) => !!o && typeof o === 'object' && isCount(o.level) && isCount(o.max)
  && extra.every((k) => isCount(o[k])) && typeof o.can_buy === 'boolean'
  && (o.reason === null || typeof o.reason === 'string');

function validFarm(d) {
  if (!d || typeof d !== 'object' || !isCount(d.balance)) return false;
  const p = d.profile;
  const s = d.slots;
  if (!p || !isCount(p.level) || !isCount(p.staked) || !(p.next_threshold === null || isCount(p.next_threshold))) return false;
  if (!s || !isCount(s.used) || !isCount(s.total)) return false;
  const nullable = (o, k) => o[k] === null || isCount(o[k]);
  return isLevelPair(d.income, ['rate']) && nullable(d.income, 'next_rate') && nullable(d.income, 'next_cost')
    && isLevelPair(d.storage, ['hours']) && nullable(d.storage, 'next_hours') && nullable(d.storage, 'next_cost');
}

// Картинка места в духе скина краша «Бочка» (UI_REVIEW, ферма): дача под вечерним небом, яблони растут и краснеют вместе с доходом,
// бочка у дома наполняется вместе с хранилищем. Только рисунок по данным сервера
function renderFarmScene(d) {
  const inc = Math.max(0, Math.min(1, d.income.level / Math.max(1, d.income.max)));
  const sto = Math.max(0, Math.min(1, d.storage.level / Math.max(1, d.storage.max)));
  const trees = 2 + Math.round(inc * 4);                     // 2..6 яблонь
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 300 120');
  svg.setAttribute('class', 'farm-svg');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Дача: яблони и бочка');
  const add = (tag, attrs, parent = svg) => { const e = document.createElementNS(NS, tag); Object.keys(attrs).forEach((k) => e.setAttribute(k, attrs[k])); parent.appendChild(e); return e; };
  const defs = add('defs', {});
  const sky = add('linearGradient', { id: 'farm-sky', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
  add('stop', { offset: 0, 'stop-color': '#7FB2D9' }, sky);
  add('stop', { offset: 1, 'stop-color': '#F7C27A' }, sky);
  const clip = add('clipPath', { id: 'farm-barrel-clip' }, defs);
  add('rect', { x: 255, y: 62, width: 28, height: 34, rx: 7 }, clip);
  add('rect', { x: 0, y: 0, width: 300, height: 120, fill: 'url(#farm-sky)' });
  add('circle', { cx: 246, cy: 30, r: 15, fill: '#FFE3A3', opacity: 0.9 });
  add('path', { d: 'M0 86Q80 74 150 86T300 84V120H0z', fill: '#5C8A3A' });
  for (let i = 0; i < trees; i++) {                          // яблони: ствол и крона, крона растёт и краснеет с уровнем дохода
    const x = 26 + i * (150 / Math.max(1, trees - 1));
    const r = 11 + inc * 9 + (i % 2) * 2;
    const base = 92 - (i % 2) * 3;
    const tree = add('g', { transform: 'translate(' + x.toFixed(1) + ' ' + base + ')' });
    add('rect', { x: -3, y: -(r + 8), width: 6, height: r + 8, fill: '#8A5A35' }, tree);
    const crown = add('g', { class: 'farm-plant' }, tree);
    crown.style.setProperty('--d', (i * 0.37).toFixed(2) + 's');       // через CSSOM: атрибут style закрыт CSP
    add('circle', { cx: 0, cy: -(r + 8), r, fill: '#4E7A31' }, crown);
    const apples = inc > 0.15 ? 1 + Math.round(inc * 3) : 0;
    for (let k = 0; k < apples; k++) add('circle', { cx: ((k % 2 ? 1 : -1) * (r * 0.45)).toFixed(1), cy: (-(r + 8) + (k - 1) * r * 0.4).toFixed(1), r: 2.3, fill: '#B8323A' }, crown);
  }
  // дача: стены, крыша, окно; бочка справа, уровень зерна поднимается с хранилищем
  add('rect', { x: 196, y: 62, width: 50, height: 30, fill: '#8A5A35' });
  add('path', { d: 'M190 62l31-20 31 20z', fill: '#3B2A1E' });
  add('rect', { x: 212, y: 72, width: 12, height: 12, fill: '#FFE3A3' });
  add('rect', { x: 255, y: 62, width: 28, height: 34, rx: 7, fill: '#6B4528' });
  const level = 34 * sto;
  const grain = add('rect', { x: 255, y: 96 - level, width: 28, height: Math.max(0.01, level), fill: '#E8B04B', 'clip-path': 'url(#farm-barrel-clip)' });
  grain.setAttribute('data-level', String(d.storage.level));
  add('rect', { x: 255, y: 70, width: 28, height: 3, fill: '#3B3F45' });
  add('rect', { x: 255, y: 86, width: 28, height: 3, fill: '#3B3F45' });
  add('path', { d: 'M0 120V99l3-4 3 4v21zM18 120V99l3-4 3 4v21zM36 120V99l3-4 3 4v21zM54 120V99l3-4 3 4v21zM72 120V99l3-4 3 4v21zM90 120V99l3-4 3 4v21zM108 120V99l3-4 3 4v21zM126 120V99l3-4 3 4v21zM144 120V99l3-4 3 4v21zM162 120V99l3-4 3 4v21zM180 120V99l3-4 3 4v21zM198 120V99l3-4 3 4v21zM216 120V99l3-4 3 4v21zM234 120V99l3-4 3 4v21zM252 120V99l3-4 3 4v21zM270 120V99l3-4 3 4v21zM288 120V99l3-4 3 4v21z', fill: '#E9DCC0' });
  add('rect', { y: 104, width: 300, height: 2, fill: '#8A5A35' });
  add('rect', { y: 114, width: 300, height: 2, fill: '#8A5A35' });
  farmEls.scene.textContent = '';
  farmEls.scene.appendChild(svg);
}

// Следующая цель: ближайшее улучшение и сколько до него не хватает; если улучшать нечего, цель следующий уровень профиля
function renderFarmGoal(d) {
  const options = [['доход', d.income], ['хранилище', d.storage]].filter(([, o]) => o.next_cost !== null && o.level < o.max);
  let text = '';
  let ratio = 0;
  if (options.length) {
    const [name, o] = options.sort((a, b) => a[1].next_cost - b[1].next_cost)[0];
    ratio = Math.min(1, d.balance / o.next_cost);
    text = 'Цель: ' + name + ' ' + (o.level + 1) + ' уровня, ' + formatCompact(o.next_cost) + ' фишек' + (d.balance >= o.next_cost ? ' (хватает)' : ' (ещё ' + formatCompact(o.next_cost - d.balance) + ')');
  } else if (d.profile.next_threshold !== null && isCount(d.profile.xp)) {
    ratio = Math.min(1, d.profile.xp / d.profile.next_threshold);
    text = 'Цель: уровень ' + (d.profile.level + 1) + ', ещё ' + formatCompact(d.profile.next_threshold - d.profile.xp) + ' опыта';
  }
  farmEls.goal.hidden = !text;
  farmEls.goalText.textContent = text;
  farmEls.goalFill.style.transform = 'scaleX(' + ratio.toFixed(3) + ')';
}

function renderFarmCard(kind, o, slots) {
  const c = farmCards[kind];
  c.card.classList.toggle('maxed', o.level >= o.max);        // максимум: одна компактная строка (UI_REVIEW, ферма)
  c.lvl.textContent = o.level >= o.max ? 'максимум' : `${o.level} / ${o.max}`;
  if (kind === 'income') {
    c.effect.textContent = `${formatCompact(o.rate)} в час` + (o.next_rate !== null ? ` → ${formatCompact(o.next_rate)} в час` : '');
  } else {
    c.effect.textContent = `${o.hours} ч` + (o.next_hours !== null ? ` → ${o.next_hours} ч` : '');
  }
  c.cost.textContent = '';
  if (o.next_cost !== null) {
    const value = document.createElement('b');
    setNumber(value, o.next_cost);
    c.cost.append('Цена: ', value);
  }
  c.btn.disabled = !o.can_buy || farmBusy;
  let reason = o.reason ? FARM_REASONS[o.reason] || '' : '';
  if (o.reason === 'level_locked') reason += ' ' + (slots.used + 1);
  c.reason.textContent = reason;
}

// #endregion

// #region Минутное начисление
// ---------- минутное начисление: экран фермы, балансы игр, «+N» ----------
// Блок дохода на экране фермы: берётся из /api/me (srv.farm), таймер идёт по монотонным часам и обновляется раз в секунду
function renderFarmIncome() {
  const f = srv.farm;
  const show = !!f && srv.loaded && farmHasData;
  farmEls.income.hidden = !show;
  renderFarmChatBonus();
  if (!show) return;
  farmEls.incHour.textContent = formatCompact(f.income_per_hour);
  farmEls.incMin.textContent = f.per_minute_estimate;
  farmEls.incTimer.textContent = mmss((srv.deadline - performance.now()) / 1000);
  farmEls.incNote.textContent = 'Пока вас нет, доход копится до ' + f.hours_cap + ' ч, дальше не начисляется.';
}

let boostInFlight = false;

function renderFarmChatBonus() {
  const c = srv.chat;
  const show = !!c && srv.loaded && farmHasData;
  farmEls.chatBonus.hidden = !show;
  if (!show) return;

  const nowSec = Math.floor(Date.now() / 1000);
  if (!c.in_chat) {
    farmEls.chatBonusLabel.textContent = '';
    farmEls.chatBonusVal.textContent = '';
    farmEls.chatActiveRow.hidden = true;
    farmEls.chatBoostUntil.hidden = true;
    farmEls.chatNote.textContent = 'Бонусы беседы работают только в групповом чате';
    farmEls.chatNote.hidden = false;
    farmEls.chatBoostBtn.hidden = true;
    farmEls.chatBoostBtn.disabled = true;
  } else {
    farmEls.chatBonusLabel.textContent = 'Бонус беседы:';
    farmEls.chatBonusVal.textContent = '+' + c.bonus_pct + ' %';
    farmEls.chatActiveRow.hidden = false;
    farmEls.chatActiveVal.textContent = formatNumber(c.active_today);
    farmEls.chatNote.hidden = true;

    if (c.boost_until && c.boost_until > nowSec) {
      const dt = new Date(c.boost_until * 1000);
      const hh = String(dt.getHours()).padStart(2, '0');
      const mm = String(dt.getMinutes()).padStart(2, '0');
      farmEls.chatBoostUntil.textContent = 'Буст беседы до ' + hh + ':' + mm;
      farmEls.chatBoostUntil.hidden = false;
    } else {
      farmEls.chatBoostUntil.hidden = true;
    }

    const cost = c.boost_gems || 50;
    farmEls.chatBoostBtn.textContent = 'Бустить беседу за ' + cost + ' кристаллов';
    farmEls.chatBoostBtn.hidden = false;
    farmEls.chatBoostBtn.disabled = boostInFlight;
  }
}

// Балансы игр хранятся у каждой игры отдельно: после минутного начисления подтягиваем их к серверному (игры без запроса и анимации;
// полёт краша и ход в процессе не трогаем: они обновятся сами по итогу)
function syncGameBalances() {
  gameRegistry.forEach(({ state: g, render, keepBalance }) => {
    if (!g) return;
    if (g.balance === null || g.balance === srv.balance || g.busy || g.animating) return;
    if (keepBalance && keepBalance()) return;
    g.balance = srv.balance;
    render();
  });
}

// «+N» над балансом: поверх экрана (вёрстку не двигает), без звука, не дольше 1,4 секунды
function showAccrualPop(amount) {
  if (document.visibilityState !== 'visible') return;
  const host = ['.screen:not([hidden]) .balance strong', '.screen:not([hidden]) #profile-balance', '.screen:not([hidden]) #farm-balance']
    .map((sel) => document.querySelector(sel)).find((el) => el && el.getBoundingClientRect().width > 0);
  if (!host) return;
  host.classList.remove('bump');            // сам баланс «подпрыгивает» при начислении
  void host.offsetWidth;
  host.classList.add('bump');
  const rect = host.getBoundingClientRect();
  const pop = document.createElement('span');
  pop.className = 'accrual-pop';
  pop.textContent = '+' + formatCompact(amount);
  pop.title = '+' + formatNumber(amount);
  pop.style.left = Math.round(rect.left) + 'px';
  pop.style.top = Math.max(0, Math.round(rect.top) - 4) + 'px';
  document.body.appendChild(pop);
  setTimeout(() => pop.remove(), 1500);
}

function applyAccrualTick(accrued) {
  syncGameBalances();
  if (farmData && !farmEls.body.hidden) {
    farmData.balance = srv.balance;
    setNumber(farmEls.balance, srv.balance);
    updateFarmButtons();
  }
  renderFarmIncome();
  if (accrued > 0) showAccrualPop(accrued);
}

function renderFarm(d) {
  farmData = d;
  farmHasData = true;
  farmEls.skel.hidden = true;
  farmEls.msg.textContent = '';
  farmEls.code.textContent = '';
  farmEls.retry.hidden = true;
  farmEls.body.hidden = false;
  farmEls.level.textContent = 'Уровень ' + d.profile.level;
  setNumber(farmEls.balance, d.balance);
  // уровень растёт от опыта (profile.xp); если сервер xp не прислал, показываем сумму ставок, как раньше
  const hasXp = isCount(d.profile.xp);
  const progressValue = hasXp ? d.profile.xp : d.profile.staked;
  farmEls.progress.textContent = '';
  if (d.profile.next_threshold === null) {
    farmEls.progress.textContent = 'Максимальный уровень';
    farmEls.barFill.style.width = '100%';
    farmEls.bar.setAttribute('aria-valuenow', '100');
  } else {
    const have = document.createElement('b');
    const next = document.createElement('b');
    setNumber(have, progressValue);
    setNumber(next, d.profile.next_threshold);
    farmEls.progress.append(hasXp ? 'Опыт ' : 'Поставлено ', have, ' / ', next);
    const pct = Math.min(100, Math.floor((progressValue / d.profile.next_threshold) * 100));
    farmEls.barFill.style.width = pct + '%';
    farmEls.bar.setAttribute('aria-valuenow', String(pct));
  }
  // подсказка про опыт нужна только когда показывается опыт; сумма ставок остаётся мелкой справкой
  farmEls.hintBtn.hidden = !hasXp;
  if (!hasXp) {
    farmEls.hint.hidden = true;
    farmEls.hintBtn.setAttribute('aria-expanded', 'false');
  }
  farmEls.staked.textContent = '';
  farmEls.staked.classList.remove('num-tap');
  if (hasXp) setNumberLabel(farmEls.staked, 'Поставлено всего: ', d.profile.staked);
  farmEls.slots.textContent = `Слоты улучшений: ${d.slots.used} / ${d.slots.total}`;
  renderFarmCard('income', d.income, d.slots);
  renderFarmCard('storage', d.storage, d.slots);
  renderFarmScene(d);
  renderFarmGoal(d);
}

function updateFarmButtons() {
  if (!farmData) return;
  renderFarmCard('income', farmData.income, farmData.slots);
  renderFarmCard('storage', farmData.storage, farmData.slots);
}

// reason: 'open' | 'visible' (не чаще раза в 10 секунд) | 'manual' | 'after' (после покупки; не теряется,
// а откладывается). Между любыми двумя запросами не меньше 5 секунд
async function loadFarm(reason) {
  if (farmInFlight || activeTab !== 'farm') return;
  const now = performance.now();
  const sinceLast = now - farmLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason === 'manual' || reason === 'after') {
      clearTimeout(farmTimer);
      farmTimer = setTimeout(() => loadFarm(reason), REQUEST_GAP_MS - sinceLast + 20);
    }
    return;
  }
  if (reason !== 'manual' && reason !== 'after' && sinceLast < REFRESH_MIN_MS) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    showFarmMessage('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(farmTimer);
  farmInFlight = true;
  farmLastRequestAt = now;
  if (!farmHasData) {
    farmEls.skel.hidden = false; // скелетон вместо спиннера
    farmEls.msg.textContent = '';
    farmEls.code.textContent = '';
    farmEls.retry.hidden = true;
  }

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/farm', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      showFarmMessage('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (res.status === 429) {
      showFarmMessage('Слишком много запросов, подождите немного', '429', true);
      return;
    }
    if (!res.ok) {
      showFarmMessage('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    if (!validFarm(d)) {
      showFarmMessage('Нет связи с сервером', 'ответ', true);
      return;
    }
    renderFarm(d);
  } catch (e) {
    showFarmMessage('Нет связи с сервером', e && e.name === 'AbortError' ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    farmInFlight = false;
  }
}

// Один POST покупки. Возвращает { kind: 'ok', data } | { kind: 'fatal', text, refresh } | { kind: 'retry', code }
async function postFarmBuy(id, kind) {
  try {
    const res = await postJson('/api/farm/buy', { request_id: id, kind });
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Ошибка запроса' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      if (body.detail === 'max_level') return { kind: 'fatal', text: 'Максимальный уровень', refresh: true };
      if (body.detail === 'level_locked') {
        const need = isCount(body.required_level) ? ' ' + body.required_level : '';
        return { kind: 'fatal', text: 'Нужен уровень профиля' + need, refresh: true };
      }
      if (body.detail === 'insufficient_funds') return { kind: 'fatal', text: 'Не хватает фишек', refresh: true };
      return { kind: 'fatal', text: 'Не удалось купить улучшение', refresh: true };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось купить улучшение', refresh: true };
    }
    const parsed = await readJsonBody(res);
    const d = parsed.ok ? parsed.data : null;
    const valid = d && typeof d.kind === 'string' && isCount(d.level_after) && isCount(d.cost)
      && isCount(d.balance) && typeof d.replayed === 'boolean';
    // ответ 2xx не повторяем: покупка уже обработана, состояние берём новым GET /api/farm
    return valid ? { kind: 'ok', data: d }
      : { kind: 'fatal', text: 'Ответ сервера не распознан. Данные обновлены', refresh: true };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

// Покупка: кнопки блокируются, до 3 попыток с ТЕМ ЖЕ request_id (повтор безопасен, сервер не спишет дважды)
async function buyUpgrade(kind) {
  if (farmBusy || !(tg && tg.initData) || !farmData) return;
  const id = makeRequestId();
  if (!id) {
    setFarmNote('Ошибка', 'lose');
    return;
  }
  farmBusy = true;
  updateFarmButtons();
  setFarmNote('Покупка…');
  let last = { code: 'сеть' };
  let done = null;
  for (let attempt = 0; attempt < ROUND_ATTEMPTS && !done; attempt++) {
    if (attempt > 0) await sleep(ROUND_PAUSES_MS[attempt - 1]);
    const r = await postFarmBuy(id, kind);
    if (r.kind === 'retry') {
      last = r;
      continue;
    }
    done = r;
  }
  farmBusy = false;
  if (done && done.kind === 'ok') {
    setFarmNote(`Куплено: ${FARM_KINDS[done.data.kind] || FARM_KINDS[kind]}, уровень ${done.data.level_after}`, 'win');
  } else if (done) {
    setFarmNote(done.text, 'lose');
  } else if (last.code === '429') {
    setFarmNote('Слишком много запросов, попробуйте чуть позже', 'lose'); // сервер отклонил запрос до обработки
  } else {
    setFarmNote('Нет связи. Покупка могла пройти: проверьте карточки (код: ' + last.code + ')', 'lose');
  }
  updateFarmButtons();
  if (!done || done.kind === 'ok' || done.refresh) {
    loadFarm('after');   // новые карточки и баланс с сервера
    loadServer('after'); // баланс в шапке и таймер до начисления (/api/me)
  }
}

const FARM_XP_HINT = 'Опыт даётся за риск: чем выше шанс потерять всю ставку раунда, тем больше опыта. '
  + 'Ставки на красное и чёрное одновременно почти не дают опыта.';
farmEls.hintBtn.addEventListener('click', () => {
  const open = farmEls.hint.hidden;
  farmEls.hint.textContent = FARM_XP_HINT;
  farmEls.hint.hidden = !open;
  farmEls.hintBtn.setAttribute('aria-expanded', String(open));
});

function validChatBoost(d) {
  return !!d && typeof d === 'object' && isCount(d.bonus_pct) && d.bonus_pct <= 45
    && isCount(d.boost_until) && isCount(d.gems);
}

async function buyChatBoost() {
  if (boostInFlight || !(tg && tg.initData) || !srv.chat || !srv.chat.in_chat) return;
  const id = makeRequestId();
  if (!id) {
    setFarmNote('Ошибка', 'lose');
    return;
  }
  boostInFlight = true;
  renderFarmChatBonus();
  setFarmNote('Буст беседы…');

  const result = await postWithRetries(() => postMinesOnce('/api/chat/boost', { request_id: id }, validChatBoost));
  boostInFlight = false;

  if (result && result.kind === 'ok') {
    const d = result.data;
    if (srv.chat) {
      srv.chat.bonus_pct = d.bonus_pct;
      srv.chat.boost_until = d.boost_until;
    }
    if (typeof shop !== 'undefined' && shop && typeof shop.gems !== 'undefined') {
      shop.gems = d.gems;
      if (typeof renderShop === 'function') renderShop();
    }
    const dt = new Date(d.boost_until * 1000);
    const hh = String(dt.getHours()).padStart(2, '0');
    const mm = String(dt.getMinutes()).padStart(2, '0');
    const untilText = 'Буст беседы до ' + hh + ':' + mm;
    farmEls.chatBoostUntil.textContent = untilText;
    farmEls.chatBoostUntil.hidden = false;
    setFarmNote(untilText, 'win');
    haptic('success');
    renderFarmChatBonus();
    loadServer('after');
    return;
  }

  const { note } = actionFailure(result, {
    no_chat: ['Бусты работают только в групповом чате', false],
    not_attributed: ['Бустить может игрок этой беседы: откройте игру из неё', false],
    insufficient_gems: ['Не хватает кристаллов. Их можно купить на странице «Кристаллы»', false],
    boost_cap_reached: ['Бонус беседы уже максимальный, новый буст не нужен', false],
    request_conflict: ['Запрос уже обработан, обновите экран', false]
  });

  setFarmNote(note, 'lose');
  renderFarmChatBonus();
  loadServer('after');
}

farmEls.chatBoostBtn.addEventListener('click', buyChatBoost);

farmEls.retry.addEventListener('click', () => loadFarm('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadFarm('visible');
});

// #endregion


