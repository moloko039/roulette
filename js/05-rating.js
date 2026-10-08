// #region Рейтинг
// ---------- вкладка «Рейтинг»: рейтинг беседы с сервера (только чтение) ----------
// Какая это беседа, решает сервер по подписи initData; клиент ничего из неё не разбирает.
// Данные хранятся только в памяти страницы, в localStorage не пишутся. Автоповтора при
// ошибке нет: только кнопка «Повторить».
const ratingEls = {
  title: document.getElementById('rating-title'),
  card: document.getElementById('rating-card'),
  list: document.getElementById('rating-list'),
  me: document.getElementById('rating-me'),
  total: document.getElementById('rating-total'),
  msg: document.getElementById('rating-msg'),
  code: document.getElementById('rating-code'),
  retry: document.getElementById('rating-retry'),
  skel: document.getElementById('rating-skel')
};

// блок «Рекорды выигрыша» под рейтингом: лучший чистый выигрыш игрока за один раунд (GET /api/chat/best-wins, только чтение).
// Клиент ничего не считает: сумма, игра и место приходят с сервера; коды игр по белому списку (неизвестный код: запись пропускается).
const bestEls = {
  card: document.getElementById('best-card'),
  list: document.getElementById('best-list'),
  empty: document.getElementById('best-empty'),
  me: document.getElementById('best-me')
};
const BEST_GAMES = { roulette: 'Рулетка', mines: 'Мины', keno: 'Кено', blackjack: 'Блэкджек', crash: 'Краш', hilo: 'Хило', slot: 'Western Slot' };
let bestLast = null;          // последний ответ (перерисовывается при смене своих рамки и значка)
let bestInFlight = false;

let ratingInFlight = false;
let ratingLastRequestAt = -Infinity; // performance.now() последнего запроса
let ratingTimer = null;              // отложенное нажатие «Повторить»
let ratingHasData = false;

function showRatingMessage(text, code, canRetry) {
  ratingEls.skel.hidden = true;
  ratingEls.card.hidden = true;
  bestEls.card.hidden = true;
  ratingEls.title.textContent = 'Рейтинг';
  ratingEls.msg.textContent = text;
  ratingEls.code.textContent = code ? 'код: ' + code : '';
  ratingEls.retry.hidden = !canRetry;
  ratingHasData = false;
}

function failRating(text, code, canRetry) {
  showRatingMessage(text, code, canRetry);
}

// форма ответа: scope «none» или «chat» с не более чем 10 записями и позицией игрока
function validRating(d) {
  if (!d || typeof d !== 'object' || typeof d.scope !== 'string') return false;
  if (d.scope === 'none') return true;
  if (d.scope !== 'chat') return false;
  if (!Array.isArray(d.top) || d.top.length > 10) return false;
  const rowsOk = d.top.every((e) => e && typeof e === 'object' && isCount(e.rank) && isCount(e.balance)
    && typeof e.name === 'string' && typeof e.is_me === 'boolean');
  return rowsOk && !!d.me && typeof d.me === 'object' && isCount(d.me.rank) && isCount(d.me.balance) && isCount(d.me.total);
}

const isBestGame = (code) => typeof code === 'string' && Object.prototype.hasOwnProperty.call(BEST_GAMES, code);

// форма ответа рекордов: scope «none» или «chat» с не более чем 10 записями; me может быть null (своего рекорда нет)
function validBestWins(d) {
  if (!d || typeof d !== 'object' || typeof d.scope !== 'string') return false;
  if (d.scope === 'none') return true;
  if (d.scope !== 'chat' || !Array.isArray(d.top) || d.top.length > 10 || !isCount(d.total)) return false;
  const rowsOk = d.top.every((e) => e && typeof e === 'object' && isCount(e.rank) && isCount(e.net_amount)
    && typeof e.name === 'string' && typeof e.is_me === 'boolean' && typeof e.game === 'string');
  const m = d.me;
  return rowsOk && (m === null || (!!m && typeof m === 'object' && isCount(m.rank) && isCount(m.net_amount) && typeof m.game === 'string' && isCount(m.total)));
}

// сумма «+1 250 000»; от миллиарда сокращённо, полное значение по нажатию (как в рейтинге)
function setBestAmount(el, n) {
  if (n >= 1e9) { setNumberLabel(el, '+', n); return; }
  el.textContent = '+' + formatNumber(n);
}

function bestRankEl(rank) {
  const el = document.createElement('span');
  el.className = 'rating-rank';
  if (rank === 1) {
    el.innerHTML = CROWN_SVG; // постоянная разметка иконки, данных сервера в ней нет
    el.setAttribute('aria-label', '1');
  } else {
    el.textContent = rank;
  }
  return el;
}

function bestAvatarEl(name, frame) {
  const el = document.createElement('span');
  el.className = 'avatar';
  el.setAttribute('aria-hidden', 'true');
  el.textContent = initialOf(name);
  decorateAvatar(el, frame);
  return el;
}

function showBestWins(d) {
  bestLast = d;
  if (d.scope !== 'chat') { bestEls.card.hidden = true; return; }
  bestEls.list.textContent = '';
  d.top.filter((e) => isBestGame(e.game)).forEach((e) => {
    const li = document.createElement('li');
    li.className = (e.is_me ? 'me ' : '') + (e.rank >= 1 && e.rank <= 3 ? 'top' + e.rank : '');
    const pub = e.is_me ? ownEquipped : publicOf(e.cosmetics);   // как в рейтинге: у себя своё надетое, у других публичное
    const name = document.createElement('span');
    name.className = 'rating-name';
    name.textContent = e.name;
    const who = document.createElement('span');
    who.className = 'rating-who';
    who.appendChild(name);
    const badge = badgeEl(pub.badge);
    if (badge) who.appendChild(badge);
    const amount = document.createElement('span');
    amount.className = 'rating-bal best-amount';
    setBestAmount(amount, e.net_amount);
    const game = document.createElement('span');
    game.className = 'rating-staked';
    game.textContent = BEST_GAMES[e.game];
    li.append(bestRankEl(e.rank), bestAvatarEl(e.name, pub.avatar_frame), who, amount, game);
    bestEls.list.appendChild(li);
  });
  const empty = bestEls.list.children.length === 0;
  bestEls.empty.hidden = !empty;
  // своя строка под списком: место среди участников с рекордом (если своего рекорда нет, строки нет)
  const me = bestEls.me;
  me.textContent = '';
  if (d.me && isBestGame(d.me.game)) {
    const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
    const myName = document.createElement('span');
    myName.className = 'rating-name';
    myName.textContent = 'Вы';
    const myWho = document.createElement('span');
    myWho.className = 'rating-who';
    myWho.appendChild(myName);
    const myBadge = badgeEl(ownEquipped.badge);
    if (myBadge) myWho.appendChild(myBadge);
    const myAmount = document.createElement('span');
    myAmount.className = 'rating-bal best-amount';
    setBestAmount(myAmount, d.me.net_amount);
    const mySub = document.createElement('span');
    mySub.className = 'rating-staked';
    mySub.textContent = `${BEST_GAMES[d.me.game]}, ${d.me.rank}-е место из ${d.me.total}`;
    me.append(bestRankEl(d.me.rank), bestAvatarEl(tgUser && tgUser.first_name ? tgUser.first_name : 'Я', ownEquipped.avatar_frame), myWho, myAmount, mySub);
  }
  bestEls.card.hidden = false;
}

// Загрузка после успешного рейтинга (те же интервалы запросов). Сбой не ломает рейтинг: прежние данные остаются, а без них блок скрыт
async function loadBestWins() {
  const initData = tg && tg.initData;
  if (bestInFlight || !initData) return;
  bestInFlight = true;
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/chat/best-wins', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (!res.ok) return;
    const d = await res.json();
    if (validBestWins(d)) showBestWins(d);
  } catch (e) {
    // нет связи: блок остаётся прежним
  } finally {
    clearTimeout(timeout);
    bestInFlight = false;
  }
}

const SET_NAMES = { leaves: 'Листопад' };

function ratingSetBadge(sets) {
  if (!Array.isArray(sets) || sets.length === 0) return null;
  const el = document.createElement('span');
  el.className = 'rating-set-badge';
  el.textContent = 'коллекция';
  const title = sets.map((c) => SET_NAMES[c] || c).join(', ');
  if (title) el.title = title;
  return el;
}

// Все тексты с сервера (в том числе имена) выводятся только через textContent
function levelBadge(level) {
  const el = document.createElement('span');
  el.className = 'rating-lvl';
  el.textContent = 'Ур. ' + level;
  return el;
}

const CROWN_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/></svg>';

function showRating(d) {
  ratingEls.skel.hidden = true;
  ratingEls.msg.textContent = '';
  ratingEls.code.textContent = '';
  ratingEls.retry.hidden = true;
  ratingHasData = true;
  ratingLast = d;
  if (d.scope === 'none') {
    ratingEls.card.hidden = true;
    bestEls.card.hidden = true;
    ratingEls.title.textContent = 'Рейтинг';
    ratingEls.msg.textContent = 'Рейтинг работает в беседах. Откройте игру по ссылке из группового чата, и здесь появится рейтинг участников. Бусты беседы и бонус беседы к ферме работают только когда игра открыта из группового чата: из личного чата они не действуют.';
    return;
  }
  ratingEls.title.textContent = 'Рейтинг беседы';
  ratingEls.list.textContent = '';
  d.top.forEach((e) => {
    const li = document.createElement('li');
    li.className = (e.is_me ? 'me ' : '') + (e.rank >= 1 && e.rank <= 3 ? 'top' + e.rank : '');
    const rank = document.createElement('span');
    rank.className = 'rating-rank';
    if (e.rank === 1) {
      rank.innerHTML = CROWN_SVG; // постоянная разметка иконки, данных сервера в ней нет
      rank.setAttribute('aria-label', '1');
    } else {
      rank.textContent = e.rank;
    }
    const avatar = document.createElement('span');
    avatar.className = 'avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = initialOf(e.name);
    const pub = e.is_me ? ownEquipped : publicOf(e.cosmetics);   // у себя своё надетое, у других только публичное из рейтинга
    decorateAvatar(avatar, pub.avatar_frame);
    const name = document.createElement('span');
    name.className = 'rating-name';
    name.textContent = e.name;
    const who = document.createElement('span');
    who.className = 'rating-who';
    who.appendChild(name);
    const badge = badgeEl(pub.badge);
    if (badge) who.appendChild(badge);
    const setBadge = ratingSetBadge(e.complete_sets);
    if (setBadge) who.appendChild(setBadge);
    if (isCount(e.level)) who.appendChild(levelBadge(e.level)); // без поля level подписи нет
    const bal = document.createElement('span');
    bal.className = 'rating-bal';
    setNumber(bal, e.balance);
    li.append(rank, avatar, who, bal);
    // сумма ставок за всё время (поле staked); в старом ответе его нет, тогда строки нет
    if (isCount(e.staked)) {
      const staked = document.createElement('span');
      staked.className = 'rating-staked';
      staked.textContent = 'поставлено ' + formatNumber(e.staked);
      li.appendChild(staked);
    }
    ratingEls.list.appendChild(li);
  });
  // строка текущего пользователя закреплена под списком
  const me = ratingEls.me;
  me.textContent = '';
  const myRank = document.createElement('span');
  myRank.className = 'rating-rank';
  myRank.textContent = d.me.rank;
  const myAvatar = document.createElement('span');
  myAvatar.className = 'avatar';
  myAvatar.setAttribute('aria-hidden', 'true');
  const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  myAvatar.textContent = initialOf(tgUser && tgUser.first_name ? tgUser.first_name : 'Я');
  decorateAvatar(myAvatar, ownEquipped.avatar_frame);
  const myName = document.createElement('span');
  myName.className = 'rating-name';
  myName.textContent = 'Вы';
  const myWho = document.createElement('span');
  myWho.className = 'rating-who';
  myWho.appendChild(myName);
  const myBadge = badgeEl(ownEquipped.badge);
  if (myBadge) myWho.appendChild(myBadge);
  if (isCount(d.me.level)) myWho.appendChild(levelBadge(d.me.level));
  const myBal = document.createElement('span');
  myBal.className = 'rating-bal';
  setNumber(myBal, d.me.balance);
  const mySub = document.createElement('span');
  mySub.className = 'rating-staked';
  mySub.textContent = `${d.me.rank}-е место из ${d.me.total}` + (isCount(d.me.staked) ? `, поставлено ${formatNumber(d.me.staked)}` : '');
  me.append(myRank, myAvatar, myWho, myBal, mySub);
  // итог по беседе (поле chat_staked); без поля строка скрыта
  if (isCount(d.chat_staked)) {
    ratingEls.total.textContent = 'Поставлено участниками беседы за всё время: ' + formatNumber(d.chat_staked);
    ratingEls.total.hidden = false;
  } else {
    ratingEls.total.textContent = '';
    ratingEls.total.hidden = true;
  }
  ratingEls.card.hidden = false;
}

// reason: 'open' | 'visible' (не чаще раза в 10 секунд) | 'manual' («Повторить»).
// Между любыми двумя запросами не меньше 5 секунд
async function loadRating(reason) {
  if (ratingInFlight || activeTab !== 'rating') return;
  const now = performance.now();
  const sinceLast = now - ratingLastRequestAt;
  if (sinceLast < REQUEST_GAP_MS) {
    if (reason === 'manual') {
      // нажатие не теряем: запрос уйдёт, когда пройдут 5 секунд
      clearTimeout(ratingTimer);
      ratingTimer = setTimeout(() => loadRating('manual'), REQUEST_GAP_MS - sinceLast + 20);
    }
    return;
  }
  if (reason !== 'manual' && sinceLast < REFRESH_MIN_MS) return;

  // вне Telegram запрос не отправляем
  const initData = tg && tg.initData;
  if (!initData) {
    failRating('Откройте игру через бота в Telegram', 'нет Telegram', false);
    return;
  }

  clearTimeout(ratingTimer);
  ratingInFlight = true;
  ratingLastRequestAt = now;
  if (!ratingHasData) {
    ratingEls.skel.hidden = false; // скелетон вместо спиннера
    ratingEls.msg.textContent = '';
    ratingEls.code.textContent = '';
    ratingEls.retry.hidden = true;
  }

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(API_URL + '/api/chat/top', {
      method: 'GET',
      headers: { Authorization: 'tma ' + initData },
      cache: 'no-store',
      signal: ctrl.signal
    });
    if (res.status === 401) {
      failRating('Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота', '401', false);
      return;
    }
    if (!res.ok) {
      failRating('Нет связи с сервером', String(res.status), true);
      return;
    }
    const d = await res.json();
    if (!validRating(d)) {
      failRating('Нет связи с сервером', 'ответ', true);
      return;
    }
    showRating(d);
    if (d.scope === 'chat') loadBestWins();
  } catch (e) {
    // fetch не различает сбой сети и запрет CORS, поэтому код с вопросом
    failRating('Нет связи с сервером', e && e.name === 'AbortError' ? 'таймаут' : 'сеть или CORS?', true);
  } finally {
    clearTimeout(timeout);
    ratingInFlight = false;
  }
}

ratingEls.retry.addEventListener('click', () => loadRating('manual'));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') loadRating('visible');
});

// #endregion

