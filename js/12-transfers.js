// #region Переводы
// ---------- переводы фишек между участниками беседы ----------
// Получатель выбирается по member_ref из рейтинга беседы (непрозрачная метка, Telegram ID клиент не знает). Лимиты, комиссия и
// минимальный уровень приходят из /api/me (srv.transferLimits), клиент констант не дублирует. Баланс обновляется по ответу сервера.
const trEls = {
  sheet: document.getElementById('transfer-sheet'),
  dim: document.getElementById('transfer-dim'),
  to: document.getElementById('transfer-to'),
  amount: document.getElementById('transfer-amount'),
  maxBtn: document.getElementById('transfer-max'),
  recv: document.getElementById('transfer-recv'),
  limits: document.getElementById('transfer-limits'),
  msg: document.getElementById('transfer-msg'),
  cancel: document.getElementById('transfer-cancel'),
  send: document.getElementById('transfer-send'),
  block: document.getElementById('profile-transfers'),
  list: document.getElementById('transfers-list'),
  empty: document.getElementById('transfers-empty')
};
const tr = { ref: null, name: '', confirm: false, busy: false };
let incomingToastShown = false;      // тост «Вам перевели» один раз за сессию
let trListAt = -Infinity;            // performance.now() последней загрузки истории
let trListBusy = false;

const trAmount = () => {
  const n = Number(trEls.amount.value);
  return Number.isSafeInteger(n) ? n : 0;
};
const trFee = (amount) => {
  const p = srv.transferLimits ? srv.transferLimits.fee_percent : 0;
  return p > 0 ? Math.max(1, Math.floor((amount * p) / 100)) : 0;
};
const trBetLimit = () => {
  const l = srv.transferLimits;
  if (!l) return 1;
  return Math.max(1, Math.min(l.max, srv.loaded ? srv.balance : l.max, l.unlimited ? l.max : l.daily_left));
};

// Ошибка ввода (текст) или пустая строка
function trInputError() {
  const l = srv.transferLimits;
  const a = trAmount();
  if (!l) return 'Переводы пока недоступны, попробуйте позже';
  if (srv.loaded && srv.balance < l.min) return 'Не хватает фишек';
  if (!l.unlimited && l.daily_left < l.min) return 'Суточный лимит исчерпан: осталось ' + formatNumber(l.daily_left);
  if (a < l.min) return 'Сумма от ' + formatNumber(l.min) + ' до ' + formatNumber(l.max);
  if (a > l.max) return 'Не больше ' + formatNumber(l.max) + ' за раз';
  if (srv.loaded && a > srv.balance) return 'Не хватает фишек';
  if (!l.unlimited && a > l.daily_left) return 'Суточный лимит: осталось ' + formatNumber(l.daily_left);
  return '';
}

function renderTransfer() {
  const l = srv.transferLimits;
  const a = trAmount();
  const fee = trFee(a);
  if (l && a >= l.min) {
    trEls.recv.textContent = fee > 0
      ? 'Получит ' + formatNumber(a - fee) + '. Комиссия ' + formatNumber(fee) + ' идёт разработчику'
      : 'Получит ' + formatNumber(a) + ' (без комиссии)';
  } else {
    trEls.recv.textContent = l ? 'Получит: введите сумму' : '';
  }
  trEls.limits.textContent = l
    ? (l.unlimited ? 'Лимит на сегодня: без ограничений' : 'Лимит на сегодня: осталось ' + formatNumber(l.daily_left))
      + '. Мин. уровень ' + l.min_level + '. Пауза ' + l.cooldown_seconds + ' с'
    : '';
  trEls.send.textContent = tr.busy ? 'Отправляем…' : (tr.confirm ? 'Подтвердить' : 'Отправить');
  trEls.send.disabled = tr.busy;
  trEls.cancel.disabled = tr.busy;
  trEls.amount.disabled = tr.busy;
  document.getElementById('transfer-change').disabled = tr.busy;
  trEls.maxBtn.disabled = tr.busy;
  refreshBetPanels();
}

function setTrMsg(text) { trEls.msg.textContent = text; }

function openTransfer(ref, name, keepAmount) {
  tr.ref = ref;
  tr.name = name;
  tr.confirm = false;
  tr.busy = false;
  trEls.to.textContent = name;
  trEls.to.title = name;
  if (!keepAmount || !trEls.amount.value) trEls.amount.value = srv.transferLimits ? String(srv.transferLimits.min) : '';
  setTrMsg(srv.transferLimits ? '' : 'Переводы пока недоступны, попробуйте позже');
  trEls.sheet.hidden = false;
  renderTransfer();
  haptic('light');
}

function closeTransfer() {
  if (tr.busy) return;
  if (document.activeElement === trEls.amount) trEls.amount.blur();
  trEls.sheet.hidden = true;
  tr.ref = null;
  tr.confirm = false;
}

// Понятные тексты ошибок сервера
function trErrorText(detail, seconds) {
  const l = srv.transferLimits;
  return ({
    insufficient_funds: 'Не хватает фишек',
    level_too_low: 'Нужен уровень ' + (l ? l.min_level : 3) + ' или выше',
    account_too_new: 'Аккаунт слишком новый: переводы откроются через ' + (l ? l.min_age_hours : 1) + ' ч после начала игры',
    not_enough_staked: 'Переводы откроются, когда вы поставите в играх не менее ' + formatNumber(l ? l.min_staked : 20000) + ' фишек',
    cooldown: 'Подождите ' + (isCount(seconds) ? seconds : 10) + ' сек. перед следующим переводом',
    daily_limit: 'Суточный лимит переводов исчерпан',
    no_chat: 'Вне беседы переводы недоступны: откройте игру из группового чата',
    not_in_chat: 'Этот игрок сейчас недоступен для перевода',
    recipient_limit: 'У получателя уже слишком много фишек',
    self_transfer: 'Нельзя перевести фишки самому себе',
    invalid_request: 'Проверьте сумму и получателя',
    request_conflict: 'Запрос уже обработан, обновите экран'
  })[detail] || 'Не удалось выполнить перевод';
}

const validTransferSend = (d) => !!d && isCount(d.amount) && isCount(d.fee) && isCount(d.received) && isCount(d.balance) && isCount(d.daily_left);

// Один POST перевода. { kind: 'ok', data } | { kind: 'conflict', detail, seconds } | { kind: 'fatal', text } | { kind: 'retry' } | { kind: 'invalid' }
async function postTransferOnce(payload) {
  try {
    const res = await postJson('/api/transfers/send', payload);
    if (res.status === 401) return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    if (res.status === 400) return { kind: 'conflict', detail: 'invalid_request' };
    if (res.status === 409) {
      const body = await readJsonBody(res);
      const data = body.ok && body.data ? body.data : {};
      return { kind: 'conflict', detail: typeof data.detail === 'string' ? data.detail : '', seconds: data.seconds };
    }
    if (!res.ok) return isServerError(res) ? { kind: 'retry' } : { kind: 'fatal', text: 'Не удалось выполнить перевод' };
    const body = await readJsonBody(res);
    // ответ 2xx не повторяем: перевод уже выполнен сервером; непонятный ответ = повод обновить баланс отдельным запросом
    return body.ok && validTransferSend(body.data) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry' };
  }
}

async function doTransfer() {
  if (tr.busy) return;
  if (!(tg && tg.initData)) { setTrMsg('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setTrMsg('Ошибка'); return; }
  const amount = trAmount();
  tr.busy = true;
  setTrMsg('');
  renderTransfer();
  // повторы с тем же request_id только при сбоях сети, 429 и 5xx
  const result = await postWithRetries(() => postTransferOnce({ request_id: id, member_ref: tr.ref, amount }));
  tr.busy = false;
  if (result && result.kind === 'ok') {
    const d = result.data;
    srv.balance = d.balance;
    srv.loaded = true;
    if (srv.transferLimits) srv.transferLimits.daily_left = d.daily_left;
    trListAt = -Infinity;   // история изменилась
    closeTransfer();
    renderAll();
    showToast('Отправлено');
    haptic('success');
    loadServer('after');
    return;
  }
  tr.confirm = false;
  if (result && result.kind === 'conflict') {
    setTrMsg(trErrorText(result.detail, result.seconds));
    if (result.detail === 'insufficient_funds') loadServer('after');
  } else if (result && result.kind === 'fatal') {
    setTrMsg(result.text);
  } else if (result && result.kind === 'invalid') {
    setTrMsg('Ответ сервера не распознан. Баланс обновлён');
    loadServer('after');
  } else {
    setTrMsg('Нет связи. Баланс обновлён, проверьте, прошёл ли перевод');
    loadServer('after');
  }
  renderTransfer();
}

function trSendClick() {
  if (tr.busy) return;
  const err = trInputError();
  if (err) { setTrMsg(err); tr.confirm = false; renderTransfer(); return; }
  if (!tr.confirm) {
    tr.confirm = true;
    setTrMsg('Отправить ' + formatNumber(trAmount()) + ' игроку ' + tr.name + '?');
    renderTransfer();
    return;
  }
  doTransfer();
}

setupBetPanel({
  input: trEls.amount,
  maxBtn: trEls.maxBtn,
  halfBtn: document.getElementById('transfer-half'),
  doubleBtn: document.getElementById('transfer-double'),
  getLimit: trBetLimit
});
trEls.amount.addEventListener('input', () => { tr.confirm = false; setTrMsg(''); renderTransfer(); });
// ÷2, ×2 и «Макс» меняют сумму: подтверждение сбрасывается
['transfer-half', 'transfer-double', 'transfer-max'].forEach((id) => document.getElementById(id).addEventListener('click', () => {
  tr.confirm = false;
  setTrMsg('');
  renderTransfer();
}));
trEls.send.addEventListener('click', trSendClick);
trEls.cancel.addEventListener('click', closeTransfer);
closeOnBackdropTap(trEls.dim, closeTransfer);


// #endregion

// #region Выбор получателя
// ---------- выбор получателя среди всех участников беседы ----------
// GET /api/chat/members?q=&offset=: по 30 имён с подгрузкой при прокрутке, поиск с дебаунсом 300 мс. В ответе только имя и метка.
const PICKER_DEBOUNCE_MS = 300;
const pkEls = {
  sheet: document.getElementById('picker-sheet'),
  dim: document.getElementById('picker-dim'),
  panel: document.getElementById('picker-panel'),
  search: document.getElementById('picker-search'),
  list: document.getElementById('picker-list'),
  msg: document.getElementById('picker-msg'),
  retry: document.getElementById('picker-retry'),
  close: document.getElementById('picker-close'),
  newBtn: document.getElementById('transfer-new'),
  newNote: document.getElementById('transfer-new-note')
};
const pk = { next: null, loading: false, gen: 0, timer: null, from: 'profile' };

const validMembers = (d) => !!d && Array.isArray(d.items) && d.items.every((i) => i && typeof i.name === 'string'
  && typeof i.member_ref === 'string' && /^[0-9a-f]{32}$/.test(i.member_ref))
  && (d.next_offset === null || isCount(d.next_offset));

// «Новый перевод» в профиле: вне беседы кнопка недоступна, причина написана
function renderTransferEntry() {
  pkEls.newBtn.disabled = srv.noChat;
  pkEls.newNote.hidden = !srv.noChat;
  pkEls.newNote.textContent = srv.noChat ? 'Вне беседы переводы недоступны: откройте игру из группового чата' : '';
}

function fitPicker() {
  // с клавиатурой панель не выше видимой области
  const vv = window.visualViewport;
  const kb = !pkEls.sheet.hidden && document.activeElement === pkEls.search && vv;
  pkEls.panel.style.setProperty('--picker-h', kb ? Math.max(200, Math.min(460, vv.height - 24)) + 'px' : '');
}
if (window.visualViewport) window.visualViewport.addEventListener('resize', fitPicker);

function setPkMsg(text, retry) {
  pkEls.msg.textContent = text;
  pkEls.retry.hidden = !retry;
}

function renderPicker(items, append) {
  if (!append) pkEls.list.textContent = '';
  items.forEach((m) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    const av = document.createElement('span');
    av.className = 'avatar';
    av.setAttribute('aria-hidden', 'true');
    av.textContent = initialOf(m.name);
    const nm = document.createElement('span');
    nm.className = 'p-name';
    nm.textContent = m.name;
    btn.append(av, nm);
    btn.addEventListener('click', () => {
      const keep = pk.from === 'transfer';
      closePicker(false);
      openTransfer(m.member_ref, m.name, keep);
    });
    li.appendChild(btn);
    pkEls.list.appendChild(li);
  });
}

async function loadMembers(reset) {
  if (!(tg && tg.initData)) { setPkMsg('Откройте игру через бота в Telegram', false); return; }
  if (reset) { pk.gen += 1; pk.next = null; pk.loading = false; }
  if (pk.loading || (!reset && pk.next === null)) return;
  const gen = pk.gen;
  const offset = reset ? 0 : pk.next;
  pk.loading = true;
  setPkMsg(reset ? 'Загрузка…' : '', false);
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/chat/members?q=' + encodeURIComponent(pkEls.search.value.trim().slice(0, 32)) + '&offset=' + offset,
      { method: 'GET', headers: { Authorization: 'tma ' + tg.initData }, cache: 'no-store', signal: ctrl.signal });
    if (gen !== pk.gen) return;   // пока шёл запрос, поиск изменился
    if (res.status === 409) {
      const body = await readJsonBody(res);
      const detail = body.ok && body.data ? body.data.detail : '';
      if (detail === 'no_chat') {
        srv.noChat = true;
        renderTransferEntry();
        pkEls.list.textContent = '';
        setPkMsg('Вне беседы переводы недоступны: откройте игру из группового чата', false);
      } else {
        pkEls.list.textContent = '';
        setPkMsg('Вас нет среди участников этой беседы: откройте игру из беседы ещё раз', false);
      }
      return;
    }
    if (!res.ok) throw new Error('status');
    const d = await res.json();
    if (gen !== pk.gen) return;
    if (!validMembers(d)) throw new Error('shape');
    srv.noChat = false;
    renderTransferEntry();
    renderPicker(d.items, !reset);
    pk.next = d.next_offset;
    setPkMsg(pkEls.list.children.length === 0 ? 'Никого не найдено' : '', false);
  } catch (e) {
    if (gen === pk.gen) setPkMsg('Не удалось загрузить список. Проверьте связь', true);
  } finally {
    clearTimeout(timeout);
    if (gen === pk.gen) pk.loading = false;
  }
}

function openPicker(from) {
  if (srv.noChat) return;
  pk.from = from;
  if (from === 'transfer') trEls.sheet.hidden = true;
  pkEls.search.value = '';
  pkEls.list.textContent = '';   // прошлый список не показывается, пока грузится новый
  pkEls.sheet.hidden = false;
  pkEls.list.scrollTop = 0;
  loadMembers(true);
}

function closePicker(restore = true) {
  if (document.activeElement === pkEls.search) pkEls.search.blur();
  pkEls.sheet.hidden = true;
  if (restore && pk.from === 'transfer' && tr.ref) trEls.sheet.hidden = false;   // отмена выбора возвращает панель перевода
  clearTimeout(pk.timer);
  pk.gen += 1;
  pk.loading = false;
  fitPicker();
}

pkEls.search.addEventListener('input', () => {
  clearTimeout(pk.timer);
  pk.timer = setTimeout(() => loadMembers(true), PICKER_DEBOUNCE_MS);
});
pkEls.search.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); pkEls.search.blur(); } });
pkEls.search.addEventListener('focus', () => { dockPanel(pkEls.panel); fitPicker(); });
pkEls.search.addEventListener('blur', () => { undockPanel(pkEls.panel); setTimeout(fitPicker, 0); });
pkEls.list.addEventListener('scroll', () => {
  if (pkEls.list.scrollTop + pkEls.list.clientHeight >= pkEls.list.scrollHeight - 80) loadMembers(false);
});
pkEls.retry.addEventListener('click', () => loadMembers(true));
pkEls.close.addEventListener('click', closePicker);
closeOnBackdropTap(pkEls.dim, closePicker);
pkEls.newBtn.addEventListener('click', () => openPicker('profile'));
document.getElementById('transfer-change').addEventListener('click', () => { if (!tr.busy) openPicker('transfer'); });
renderTransferEntry();

// #endregion

// #region История переводов
// ---------- история переводов в профиле ----------
const validTransferList = (d) => !!d && Array.isArray(d.items) && d.items.every((i) => i && (i.direction === 'in' || i.direction === 'out')
  && typeof i.name === 'string' && isCount(i.amount) && isCount(i.fee) && isCount(i.time));

function trTimeText(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getDate()) + '.' + p(d.getMonth() + 1) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function renderTransferList(items) {
  trEls.list.textContent = '';
  trEls.empty.hidden = items.length > 0;
  items.forEach((i) => {
    const li = document.createElement('li');
    const who = document.createElement('span');
    who.className = 't-who';
    who.textContent = (i.direction === 'out' ? 'Вы → ' : '') + i.name + (i.direction === 'in' ? ' → вам' : '');
    const sum = document.createElement('span');
    sum.className = 't-sum' + (i.direction === 'in' ? ' in' : '');
    const shown = i.direction === 'out' ? i.amount : i.amount - i.fee;
    sum.textContent = (i.direction === 'out' ? '−' : '+') + formatCompact(shown);
    sum.title = formatNumber(shown);
    const sub = document.createElement('span');
    sub.className = 't-sub';
    sub.textContent = trTimeText(i.time) + ', комиссия ' + formatNumber(i.fee);
    li.append(who, sum, sub);
    trEls.list.appendChild(li);
  });
  trEls.block.hidden = false;
}

// reason: 'open' (профиль, не чаще раза в 10 секунд), 'toast' (после тоста о входящем) или 'manual'. Запрос помечает входящие просмотренными
async function loadTransfers(reason) {
  if (trListBusy || !(tg && tg.initData)) return;
  if (reason === 'open' && performance.now() - trListAt < REFRESH_MIN_MS) return;
  trListBusy = true;
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/transfers', { method: 'GET', headers: { Authorization: 'tma ' + tg.initData }, cache: 'no-store', signal: ctrl.signal });
    if (!res.ok) return;
    const d = await res.json();
    if (!validTransferList(d)) return;
    trListAt = performance.now();
    renderTransferList(d.items);
    if (srv.incomingUnseen) srv.incomingUnseen = { count: 0, total: 0 };
  } catch (e) {
    // история необязательна: при сбое раздел просто не обновляется
  } finally {
    clearTimeout(timeout);
    trListBusy = false;
  }
}

// Входящие переводы при запуске: один тост за сессию, затем запрос истории (он помечает их просмотренными)
function notifyIncoming() {
  const inc = srv.incomingUnseen;
  if (incomingToastShown || !inc || inc.count < 1) return;
  incomingToastShown = true;
  showToast('Вам перевели ' + formatNumber(inc.total) + ' фишек');
  loadTransfers('toast');
}

// #endregion

