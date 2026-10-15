// #region Не слоты (встроенные игры)
// Встроенные игры: отдельные статические приложения в games/<id>/, открываются в iframe во весь экран.
// Игра с полем bridge играет на фишки приложения: сама она денег не считает и к API не ходит; раунд она запрашивает у этого скрипта
// сообщением postMessage, скрипт делает обычный POST (сервер считает раунд и расплачивается, bot/slot.py) и возвращает игре итог и баланс.
// Баланс у игры только тот, что прислал сервер: wallet, лимит частоты, рекорды и /api/me общие с остальными играми.
// Чтобы добавить игру: положить её сборку в games/<id>/ (вход index.html, пути относительные) и добавить запись сюда; игре на фишках
// приложения нужен серверный эндпоинт по образцу /api/slot/spin и свой мост по образцу slot ниже.
const EMBEDDED_GAMES = [
  { id: 'western-slot', label: 'Western Slot', hint: 'Каскадный слот, 3600 способов, бонус с множителями', url: 'games/western-slot/index.html?host=depnaya', bridge: 'slot' },
  { id: 'bloody-slot', label: 'Bloody Slot', hint: 'Готический каскадный слот, 1024 способа, ритуальная шкала и бонусы', url: 'games/bloody-slot/index.html?host=depnaya', bridge: 'bloody',
    preview: ['H1', 'H2', 'Wild', 'H3', 'H4'] }
];
const arcadeEls = {
  grid: document.getElementById('arcade-grid'),
  balance: document.getElementById('arcade-balance'),
  info: document.getElementById('arcade-info'),
  note: document.getElementById('arcade-note'),
  switchBtn: document.getElementById('arcade-switch'),
  view: document.getElementById('embed-view'),
  frame: document.getElementById('embed-frame'),
  title: document.getElementById('embed-title'),
  back: document.getElementById('embed-back')
};
// Шапка как у остальных игр: баланс слева (UI_REVIEW 1.5). Баланс один на приложение, серверный
const ac = { balance: null };
function renderArcade() {
  if (srv.loaded) {
    ac.balance = availableBalance();
    arcadeEls.balance.classList.remove('skeleton');
    arcadeEls.balance.textContent = spacedNumber(ac.balance);
  } else {
    arcadeEls.balance.textContent = srv.error ? '—' : 'Загрузка…';
  }
  fitNumberFont(arcadeEls.balance, arcadeEls.balance.textContent.length);
}
registerGame({ id: 'arcade', state: ac, render: renderArcade, busy: () => false });

// Пояснение «баланс один на все игры»: при первом открытии раздела показано, дальше только по значку ⓘ
function showArcadeNote(on) {
  arcadeEls.note.hidden = !on;
  arcadeEls.info.setAttribute('aria-expanded', String(on));
}
function arcadeScreenOpened() {
  renderArcade();
  let seen = false;
  try { seen = localStorage.getItem('arcade_note_seen') === '1'; } catch (e) { /* без хранилища показываем каждый раз */ }
  if (!seen) {
    showArcadeNote(true);
    try { localStorage.setItem('arcade_note_seen', '1'); } catch (e) { /* не страшно */ }
  } else {
    showArcadeNote(false);
  }
}
arcadeEls.info.addEventListener('click', () => showArcadeNote(arcadeEls.note.hidden));
const getEmbedded = (id) => EMBEDDED_GAMES.find((g) => g.id === id);

// Открытая игра: один iframe, создаётся при открытии и удаляется при закрытии (звук и анимации останавливаются)
function openEmbedded(id) {
  const game = getEmbedded(id);
  if (!game) return;
  closeEmbedded();
  const frame = document.createElement('iframe');
  // index.html игры запрашивается свежим при каждом открытии: страница крошечная, а её кэш (Pages 10 минут, вебвью Telegram
  // дольше) иначе показывал бы прошлую сборку; тяжёлые файлы сборки имеют хэш в имени и кэшируются как обычно
  frame.src = game.url + (game.url.includes('?') ? '&' : '?') + 'ts=' + Date.now();
  frame.title = game.label;
  frame.setAttribute('allow', 'autoplay');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  arcadeEls.frame.appendChild(frame);
  arcadeEls.title.textContent = game.label;
  arcadeEls.view.hidden = false;
  document.body.classList.add('embed-open');
  if (game.bridge === 'slot') {
    sl.frame = frame;
    if (!srv.loaded) loadServer('open');   // баланс для игры: без него она не даст крутить
  }
  if (game.bridge === 'bloody') {
    bl.frame = frame;
    if (!srv.loaded) loadServer('open');
  }
  haptic('light');
}

function closeEmbedded() {
  sl.frame = null;   // ответ на запрос, ушедший до закрытия, обновит баланс приложения, игре уже не отправится
  bl.frame = null;
  arcadeEls.frame.textContent = '';
  arcadeEls.view.hidden = true;
  document.body.classList.remove('embed-open');
  renderArcade();
}

// ---------- мост Western Slot ----------
// Сообщения игры (iframe → приложение): slot:hello; slot:spin {id, coin, buy}; slot:haptic {kind}.
// Ответы (приложение → iframe): slot:ready {balance}; slot:balance {balance} (после начислений, через реестр игр);
// slot:result {id, round, cost, payout, balance}; slot:error {id, code, text, balance}. Принимаются только сообщения из открытого iframe.
// Форма ответа сервера: docs/API.md (POST /api/slot/spin); игра сама проверяет раунд перед показом.
const SLOT_COINS = [1, 2, 5, 10, 25, 50, 100, 250, 500, 2500, 10000, 25000, 50000];
const SLOT_HAPTICS = ['light', 'medium', 'heavy', 'success', 'error'];
const SLOT_NOTES = {
  insufficient_funds: 'Не хватает фишек',
  balance_limit: 'Достигнут потолок баланса',
  request_conflict: 'Повтор запроса с другими параметрами'
};
const sl = { balance: null, busy: false, frame: null };
// render: реестр игр подтягивает баланс игры к серверному после начисления; игре он уходит сообщением
registerGame({ id: 'slot', state: sl, render: () => slotPost({ type: 'slot:balance', balance: srv.balance }), busy: () => sl.busy });

function slotPost(msg) {
  const win = sl.frame && sl.frame.contentWindow;
  if (!win) return;
  try {
    win.postMessage(msg, window.location.origin && window.location.origin !== 'null' ? window.location.origin : '*');
  } catch (e) { /* iframe уже закрыт */ }
}

// Ответ сервера: нужны деньги и каркас раунда; поля и шаги игра проверяет сама и при ошибке показывает только итог
function validSlotRound(d) {
  return !!d && isCount(d.balance) && isCount(d.cost) && isCount(d.payout) && typeof d.bought === 'boolean'
    && SLOT_COINS.includes(d.coin) && !!d.round && typeof d.round === 'object' && !!d.round.base
    && Array.isArray(d.round.base.steps) && Array.isArray(d.round.freeSpins) && isCount(d.round.totalWin)
    && d.payout === d.round.totalWin * d.coin;
}

function slotFailure(result) {
  if (!result) return { code: 'network', text: 'Нет связи с сервером', reload: true };
  if (result.kind === 'conflict') {
    const known = Object.prototype.hasOwnProperty.call(SLOT_NOTES, result.detail);
    return { code: result.detail || 'conflict', text: known ? SLOT_NOTES[result.detail] : 'Сервер отклонил спин', reload: true };
  }
  if (result.kind === 'fatal') return { code: 'fatal', text: result.text, reload: false };
  return { code: result.kind, text: 'Ответ сервера не распознан', reload: true };
}

async function slotSpin(msg) {
  const id = typeof msg.id === 'string' ? msg.id.slice(0, 64) : '';
  const coin = msg.coin;
  const buy = msg.buy === true;
  if (!id || !SLOT_COINS.includes(coin) || sl.busy) {
    slotPost({ type: 'slot:error', id, code: 'bad_request', text: 'Неверные параметры', balance: srv.balance });
    return;
  }
  const requestId = makeRequestId();
  if (!requestId) {
    slotPost({ type: 'slot:error', id, code: 'no_request_id', text: 'Не удалось создать запрос', balance: srv.balance });
    return;
  }
  sl.busy = true;
  let result = null;
  try {
    result = await postWithRetries(() => postMinesOnce('/api/slot/spin', { request_id: requestId, coin, buy }, validSlotRound));
  } finally {
    sl.busy = false;
  }
  if (result && result.kind === 'ok') {
    const d = result.data;
    srv.balance = d.balance;   // баланс приложения сразу: игра показывает свой ход раунда, остальные экраны закрыты iframe
    srv.loaded = true;
    srv.error = null;
    sl.balance = d.balance;
    renderBalance();
    slotPost({ type: 'slot:result', id, round: d.round, cost: d.cost, payout: d.payout, balance: d.balance });
    return;
  }
  const failure = slotFailure(result);
  slotPost({ type: 'slot:error', id, code: failure.code, text: failure.text, balance: srv.loaded ? srv.balance : null });
  if (failure.reload) loadServer('after');
}

window.addEventListener('message', (e) => {
  const win = sl.frame && sl.frame.contentWindow;
  if (!win || e.source !== win) return;   // только из открытого iframe игры
  const msg = e.data;
  if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return;
  if (msg.type === 'slot:hello') {
    sl.balance = srv.loaded ? srv.balance : null;
    slotPost({ type: 'slot:ready', balance: sl.balance });
  } else if (msg.type === 'slot:spin') {
    slotSpin(msg);
  } else if (msg.type === 'slot:haptic') {
    haptic(SLOT_HAPTICS.includes(msg.kind) ? msg.kind : 'light');
  }
});

// ---------- мост Bloody Slot ----------
// Игра (iframe) денег не считает и к API не ходит. Сообщения игры: bloody:hello; bloody:call {id, action: state | start | spin, bet, buy}; bloody:haptic {kind}.
// Ответы приложения: bloody:ready; bloody:reply {id, ok, data} или {id, ok: false, code, text}. Форма ответов сервера: docs/API.md (/api/bloody/*).
// Принимаются только сообщения из открытого iframe; игра сама проверяет лог спина перед показом.
const bl = { frame: null, busy: false };
registerGame({ id: 'bloody', state: bl, render: () => {}, busy: () => bl.busy });
const BLOODY_NOTES = { insufficient_funds: 'Не хватает фишек', balance_limit: 'Достигнут потолок баланса', active_round: 'Раунд уже идёт', no_active_round: 'Нет активного раунда', bad_bet: 'Такой ставки нет', request_conflict: 'Повтор запроса с другими параметрами' };

function blPost(msg) {
  const win = bl.frame && bl.frame.contentWindow;
  if (!win) return;
  try { win.postMessage(msg, window.location.origin && window.location.origin !== 'null' ? window.location.origin : '*'); } catch (e) { /* iframe закрыт */ }
}

function validBloody(d) {
  return !!d && typeof d === 'object' && typeof d.status === 'string' && isCount(d.balance) && (d.round === null || (!!d.round && isCount(d.round.units)))
    && (d.spin === null || (!!d.spin && Array.isArray(d.spin.steps) && !!d.spin.start && Array.isArray(d.spin.start.grid)));
}

async function blCall(msg) {
  const id = typeof msg.id === 'string' ? msg.id.slice(0, 32) : '';
  const fail = (code, text) => blPost({ type: 'bloody:reply', id, ok: false, code, text });
  if (!id || bl.busy) { fail('bad_request', 'Неверные параметры'); return; }
  bl.busy = true;
  let path = '';
  let payload = null;
  try {
    if (msg.action === 'state') {
      try {
        const d = await fetchGameState('/api/bloody/state', validBloody);
        if (isCount(d.balance)) { srv.balance = d.balance; srv.loaded = true; srv.error = null; renderBalance(); }
        blPost({ type: 'bloody:reply', id, ok: true, data: d });
      } catch (e) { fail('network', e && e.text ? e.text : 'Нет связи с сервером'); }
      return;
    }
    const requestId = makeRequestId();
    if (!requestId) { fail('no_request_id', 'Не удалось создать запрос'); return; }
    if (msg.action === 'start') {
      if (!Number.isInteger(msg.bet) || !(msg.buy === null || typeof msg.buy === 'string')) { fail('bad_request', 'Неверные параметры'); return; }
      path = '/api/bloody/start';
      payload = { request_id: requestId, bet: msg.bet, buy: msg.buy };
    } else if (msg.action === 'spin') {
      path = '/api/bloody/spin';
      payload = { request_id: requestId };
    } else { fail('bad_request', 'Неверные параметры'); return; }
    const result = await postWithRetries(() => postMinesOnce(path, payload, validBloody));
    if (result && result.kind === 'ok') {
      const d = result.data;
      srv.balance = d.balance;
      srv.loaded = true;
      srv.error = null;
      renderBalance();
      blPost({ type: 'bloody:reply', id, ok: true, data: d });
      return;
    }
    if (result && result.kind === 'conflict') { fail(result.detail || 'conflict', BLOODY_NOTES[result.detail] || 'Сервер отклонил действие'); loadServer('after'); return; }
    if (result && result.kind === 'fatal') { fail('fatal', result.text); return; }
    fail(result ? result.kind : 'network', result ? 'Ответ сервера не распознан' : 'Нет связи с сервером');
    loadServer('after');
  } finally {
    bl.busy = false;
  }
}

window.addEventListener('message', (e) => {
  const win = bl.frame && bl.frame.contentWindow;
  if (!win || e.source !== win) return;
  const msg = e.data;
  if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return;
  if (msg.type === 'bloody:hello') blPost({ type: 'bloody:ready', balance: srv.loaded ? srv.balance : null });
  else if (msg.type === 'bloody:call') blCall(msg);
  else if (msg.type === 'bloody:haptic') haptic(SLOT_HAPTICS.includes(msg.kind) ? msg.kind : 'light');
});

// Превью карточки слота: поле 6×5 символов статичным кадром (рисунок, не кадр игры)
const ARCADE_PREVIEW_SVG = (() => {
  const cols = 6;
  const rows = 4;
  const symbols = [
    '<circle cx="{x}" cy="{y}" r="7"/>',
    '<rect x="{x0}" y="{y0}" width="13" height="13" rx="3"/>',
    '<path d="M{x} {y1}L{x1} {y}L{x} {y2}L{x2} {y}Z"/>'
  ];
  let out = '<svg viewBox="0 0 252 96" preserveAspectRatio="xMidYMid meet" class="arcade-reels">';
  for (let c = 0; c < cols; c++) {
    out += '<rect class="reel" x="' + (c * 42 + 2) + '" y="2" width="38" height="92" rx="6"/>';
    for (let r = 0; r < rows; r++) {
      const x = c * 42 + 21;
      const y = r * 22 + 13;
      const s = symbols[(c * 3 + r * 2 + (c % 2)) % symbols.length];
      out += '<g class="sym s' + ((c + r) % 3) + '">' + s.replace(/\{x0\}/g, x - 6.5).replace(/\{y0\}/g, y - 6.5).replace(/\{x1\}/g, x + 7).replace(/\{x2\}/g, x - 7).replace(/\{y1\}/g, y - 8).replace(/\{y2\}/g, y + 8).replace(/\{x\}/g, x).replace(/\{y\}/g, y) + '</g>';
    }
  }
  return out + '</svg>';
})();

EMBEDDED_GAMES.forEach((game) => {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'arcade-card';
  card.dataset.embedded = game.id;
  card.setAttribute('role', 'menuitem');
  const previewHtml = game.preview ? '<span class="arcade-pics">' + game.preview.map((n) => '<img src="games/bloody-slot/img/' + n + '.webp" alt="" width="48" height="48" loading="lazy">').join('') + '</span>' : ARCADE_PREVIEW_SVG;
  card.innerHTML = '<span class="arcade-preview" aria-hidden="true">' + previewHtml + '</span><span class="arcade-name"></span><span class="arcade-hint"></span>';
  card.querySelector('.arcade-name').textContent = game.label;
  card.querySelector('.arcade-hint').textContent = game.hint || '';
  card.addEventListener('click', () => openEmbedded(game.id));
  arcadeEls.grid.appendChild(card);
});
arcadeEls.back.addEventListener('click', closeEmbedded);
arcadeEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js: при загрузке этого файла её ещё нет

// Шторка закрывается свайпом вниз
(() => {
  let startY = null;
  let dy = 0;
  gamePanel.addEventListener('touchstart', (e) => {
    startY = e.touches[0].clientY;
    dy = 0;
  }, { passive: true });
  gamePanel.addEventListener('touchmove', (e) => {
    if (startY === null) return;
    dy = Math.max(0, e.touches[0].clientY - startY);
    gamePanel.classList.add('dragging');
    gamePanel.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  const end = () => {
    if (startY === null) return;
    const far = dy > 80;
    startY = null;
    gamePanel.classList.remove('dragging');
    gamePanel.style.transform = '';
    if (far) closeGameMenu();
  };
  gamePanel.addEventListener('touchend', end);
  gamePanel.addEventListener('touchcancel', end);
})();
// #endregion

