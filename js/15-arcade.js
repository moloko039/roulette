// #region Не слоты (встроенные игры)
// Встроенные игры: отдельные статические приложения в games/<id>/, открываются в iframe во весь экран.
// Игра с полем bridge играет на фишки приложения: сама она денег не считает и к API не ходит; раунд она запрашивает у этого скрипта
// сообщением postMessage, скрипт делает обычный POST (сервер считает раунд и расплачивается, bot/slot.py) и возвращает игре итог и баланс.
// Баланс у игры только тот, что прислал сервер: wallet, лимит частоты, рекорды и /api/me общие с остальными играми.
// Чтобы добавить игру: положить её сборку в games/<id>/ (вход index.html, пути относительные) и добавить запись сюда; игре на фишках
// приложения нужен серверный эндпоинт по образцу /api/slot/spin и свой мост по образцу slot ниже.
const EMBEDDED_GAMES = [
  { id: 'western-slot', label: 'Western Slot', hint: 'Каскадный слот, 3600 способов, бонус с множителями', url: 'games/western-slot/index.html?host=depnaya', bridge: 'slot' }
];
const arcadeEls = {
  grid: document.getElementById('arcade-grid'),
  switchBtn: document.getElementById('arcade-switch'),
  view: document.getElementById('embed-view'),
  frame: document.getElementById('embed-frame'),
  title: document.getElementById('embed-title'),
  back: document.getElementById('embed-back')
};
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
  haptic('light');
}

function closeEmbedded() {
  sl.frame = null;   // ответ на запрос, ушедший до закрытия, обновит баланс приложения, игре уже не отправится
  arcadeEls.frame.textContent = '';
  arcadeEls.view.hidden = true;
  document.body.classList.remove('embed-open');
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

EMBEDDED_GAMES.forEach((game) => {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'arcade-card';
  card.dataset.embedded = game.id;
  card.setAttribute('role', 'menuitem');
  card.innerHTML = '<span class="arcade-name"></span><span class="arcade-hint"></span>';
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

