// #region Гардероб
// Оформление (косметика, только внешний вид): рамка и значок у себя и в рейтинге, раздел «Оформление» магазина (гардероб) с предпросмотром.
// Коды с сервера проверяются по белому списку SKIN_CODES: неизвестные игнорируются; тексты каталога выводятся только через textContent,
// разметка (SVG) берётся из констант клиента по проверенному коду. Покупок и цен нет: предметы выдаёт владелец.
const WD_SLOTS = [
  { slot: 'card_back', label: 'Рубашка карт' }, { slot: 'chip', label: 'Фишки' }, { slot: 'table', label: 'Стол' },
  { slot: 'mine_icons', label: 'Иконки мин' }, { slot: 'keno_ball', label: 'Шарики кено' }, { slot: 'crash', label: 'Краш' },
  { slot: 'avatar_frame', label: 'Рамка аватара' }, { slot: 'badge', label: 'Значок' }
];
// первый код каждого слота стартовый (ничего не рисует / вид по умолчанию)
const SKIN_CODES = {
  card_back: ['back_classic', 'back_midnight', 'back_ember'], chip: ['chip_plain', 'chip_ring', 'chip_gold'],
  table: ['table_green', 'table_blue', 'table_violet'], mine_icons: ['mine_classic', 'mine_star', 'mine_gem'],
  keno_ball: ['keno_round', 'keno_hex'], crash: ['crash_line', 'crash_neon'],
  avatar_frame: ['frame_plain', 'frame_thin', 'frame_double', 'frame_crown'], badge: ['badge_none', 'badge_spade', 'badge_flame']
};
const skinKnown = (slot, code) => typeof code === 'string' && Object.prototype.hasOwnProperty.call(SKIN_CODES, slot) && SKIN_CODES[slot].includes(code);
const skinStarter = (slot) => SKIN_CODES[slot][0];
// публичные слоты: только нестартовые известные коды (стартовые «ничего не рисуют»)
const publicSkin = (slot, code) => ((slot === 'avatar_frame' || slot === 'badge') && skinKnown(slot, code) && code !== skinStarter(slot) ? code : null);
const BADGE_SVG = {
  badge_spade: BJ_SUIT_SETS.default.S,
  badge_flame: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c1 4 5 6 5 11a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-4-1-6 1-10z"/></svg>'
};
const CR_SAMPLE_SVG = '<svg class="cr-svg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true"><path class="cr-axis" d="M0 149H300M1 0V150"/><path class="cr-curve" d="M0 149L60 140L120 118L180 80L240 30L300 2"/></svg>';

// рамка и значок самого игрока (из cosmetics.equipped в /api/me и после смены в гардеробе)
let ownEquipped = { avatar_frame: null, badge: null };
let ratingLast = null;   // последний ответ рейтинга: перерисовывается при смене своих рамки и значка

function decorateAvatar(el, frame) {
  if (frame) el.setAttribute('data-skin-avatar_frame', frame);
  else el.removeAttribute('data-skin-avatar_frame');
}

// Значок рядом с именем; null, если значка нет. Разметка из констант клиента по проверенному коду.
function badgeEl(code) {
  const svg = code ? BADGE_SVG[code] : null;
  if (!svg) return null;
  const el = document.createElement('span');
  el.className = 'rating-badge';
  el.setAttribute('data-skin-badge', code);
  el.setAttribute('aria-hidden', 'true');
  el.innerHTML = svg;
  return el;
}

// Публичные слоты участника из ответа рейтинга ({слот: код}); неизвестное игнорируется
function publicOf(c) {
  const src = c && typeof c === 'object' ? c : {};
  return { avatar_frame: publicSkin('avatar_frame', src.avatar_frame), badge: publicSkin('badge', src.badge) };
}

function renderOwnCosmetics() {
  decorateAvatar(profileEls.avatar, ownEquipped.avatar_frame);
  const holder = document.getElementById('profile-badge');
  const code = ownEquipped.badge;
  holder.hidden = !(code && BADGE_SVG[code]);
  holder.textContent = '';
  if (!holder.hidden) {
    holder.setAttribute('data-skin-badge', code);
    holder.innerHTML = BADGE_SVG[code];
  } else {
    holder.removeAttribute('data-skin-badge');
  }
  if (ratingLast && ratingLast.scope === 'chat') showRating(ratingLast);
  if (bestLast && bestLast.scope === 'chat') showBestWins(bestLast);
}

function setOwnCosmetics(c) {
  const eq = c && c.equipped && typeof c.equipped === 'object' ? c.equipped : {};
  const next = { avatar_frame: publicSkin('avatar_frame', eq.avatar_frame), badge: publicSkin('badge', eq.badge) };
  if (next.avatar_frame === ownEquipped.avatar_frame && next.badge === ownEquipped.badge) return;
  ownEquipped = next;
  renderOwnCosmetics();
}

// ----- раздел «Оформление» магазина (гардероб) -----
const wdEls = {
  tabs: document.getElementById('wd-tabs'), grid: document.getElementById('wd-grid'), vis: document.getElementById('wd-vis'),
  msg: document.getElementById('wd-msg'),
  pSheet: document.getElementById('wd-prev-sheet'), pDim: document.getElementById('wd-prev-dim'), pTitle: document.getElementById('wd-prev-title'),
  pStatus: document.getElementById('wd-prev-status'), pScene: document.getElementById('wd-prev-scene'), pDesc: document.getElementById('wd-prev-desc'),
  pMsg: document.getElementById('wd-prev-msg'), pNote: document.getElementById('wd-prev-note'), pClose: document.getElementById('wd-prev-close'), pAct: document.getElementById('wd-prev-act'),
  terms: document.getElementById('wd-terms')
};
const wd = { open: false, catalog: null, mine: null, slot: 'card_back', busy: false, loading: false, preview: null, confirm: false, prevMsg: '' };

const validWdCatalog = (d) => !!d && Array.isArray(d.items) && Array.isArray(d.slots);
const validWdMine = (d) => !!d && Array.isArray(d.owned) && !!d.equipped && typeof d.equipped === 'object' && typeof d.show_in_rating === 'boolean';
const validWdEquip = (d) => !!d && typeof d.slot === 'string' && !!d.equipped && typeof d.equipped === 'object';
const validWdVisibility = (d) => !!d && typeof d.show_in_rating === 'boolean';

// Каталог: только известные коды своего слота; тексты ограничены по длине (выводятся через textContent)
function wdNormalizeCatalog(d) {
  const items = [];
  d.items.forEach((i) => {
    if (!i || typeof i !== 'object' || !skinKnown(i.slot, i.code) || typeof i.name !== 'string') return;
    items.push({
      code: i.code, slot: i.slot, name: i.name.slice(0, 40), description: typeof i.description === 'string' ? i.description.slice(0, 140) : '',
      starter: i.code === skinStarter(i.slot), available: i.available === true, price: null
    });
    const last = items[items.length - 1];
    if (last.available && !last.starter) last.price = wdPrice(i.price);       // цена только из каталога сервера; ненормальное значение = нет цены
  });
  return items;
}

// Цена из каталога: валюта gems или chips и разумная целая сумма, иначе null (предмет не продаётся)
function wdPrice(p) {
  if (!p || typeof p !== 'object' || (p.currency !== 'gems' && p.currency !== 'chips')) return null;
  if (!Number.isSafeInteger(p.amount) || p.amount < 1 || p.amount > 1e12) return null;
  return { currency: p.currency, amount: p.amount };
}
const wdPriceText = (p) => formatNumber(p.amount) + (p.currency === 'gems' ? ' кристаллов' : ' фишек');

function wdNormalizeEquipped(eq) {
  const out = {};
  WD_SLOTS.forEach(({ slot }) => { out[slot] = skinKnown(slot, eq[slot]) ? eq[slot] : skinStarter(slot); });
  return out;
}

function wdNormalizeMine(d) {
  const owned = new Set();
  d.owned.forEach((o) => { if (o && typeof o.code === 'string') owned.add(o.code); });
  return { owned, equipped: wdNormalizeEquipped(d.equipped), showInRating: d.show_in_rating };
}

const wdOwns = (item) => item.starter || (wd.mine && wd.mine.owned.has(item.code));
const wdWorn = (item) => !!wd.mine && wd.mine.equipped[item.slot] === item.code;
const wdCanEquip = (item) => item.available && wdOwns(item);
const wdBuyable = (item) => item.available && !wdOwns(item) && !!item.price;
function wdStatus(item) {
  if (wdWorn(item)) return 'Надето';
  if (!item.available) return 'Скоро';
  if (wdOwns(item)) return 'Есть';
  return item.price ? wdPriceText(item.price) : 'Не получено';
}
// что делает главная кнопка листа: надеть, снять, купить или ничего
function wdActKind(item) {
  if (wdWorn(item)) return item.starter ? '' : 'unequip';
  if (wdCanEquip(item)) return 'equip';
  return wdBuyable(item) ? 'buy' : '';
}

// Образец слота: код скина стоит на САМОМ элементе (не на корне), поэтому предпросмотр не меняет остальное приложение
function wdScene(slot, code, mini) {
  const box = document.createElement('div');
  box.className = mini ? 'wd-mini' : 'wd-scene';
  box.setAttribute('data-skin-' + slot, code);
  const add = (cls, text, tag) => {
    const e = document.createElement(tag || 'div');
    e.className = cls;
    if (text) e.textContent = text;
    box.appendChild(e);
    return e;
  };
  if (slot === 'chip') {
    (mini ? ['100'] : ['10', '100']).forEach((t) => add('chip', t, 'span').setAttribute('data-amount', t));
    if (!mini) add('chip', '500', 'span').setAttribute('aria-pressed', 'true');
  } else if (slot === 'card_back') {
    add('bj-card back');
    if (!mini) {
      const face = add('bj-card red');
      face.innerHTML = '<span class="rank">A</span>' + BJ_SUIT_SVG.H;
    }
  } else if (slot === 'table') {
    add('cell red', '7', 'span'); add('cell black', '8', 'span');
    if (!mini) add('cell green', '0', 'span');
  } else if (slot === 'mine_icons') {
    const icons = Object.prototype.hasOwnProperty.call(MINES_ICON_SETS, code) ? MINES_ICON_SETS[code] : MINES_ICON_SETS.default;   // набор предмета, а не надетый
    if (!mini) add('mines-cell', '', 'span');
    add('mines-cell safe', '', 'span').innerHTML = icons.gem;
    if (!mini) add('mines-cell mine', '', 'span').innerHTML = icons.mine;
  } else if (slot === 'keno_ball') {
    if (!mini) { add('keno-ball sel', '', 'span').appendChild(document.createElement('span')).textContent = '7'; add('keno-ball drawn', '', 'span').appendChild(document.createElement('span')).textContent = '12'; }
    add('keno-ball hit', '', 'span').appendChild(document.createElement('span')).textContent = '21';
  } else if (slot === 'crash') {
    const chart = add('cr-chart');
    chart.setAttribute('data-tone', 'idle');
    chart.innerHTML = CR_SAMPLE_SVG;
  } else if (slot === 'avatar_frame') {
    add('avatar', 'И', 'span');
  } else if (slot === 'badge') {
    const who = add('wd-demo-name', '', 'span');
    if (!mini) who.appendChild(document.createTextNode('Игрок'));
    const b = badgeEl(BADGE_SVG[code] ? code : null);
    if (b) who.appendChild(b);
    else who.appendChild(document.createTextNode('без значка'));
  }
  return box;
}

function setWdMsg(text) { wdEls.msg.textContent = text; }
const wdToast = (text) => showToast(text, wdEls.pSheet.hidden ? 'wd-toast' : 'wd-prev-toast');

function renderWardrobe() {
  const tabsLeft = wdEls.tabs.scrollLeft;
  const gridTop = wdEls.grid.scrollTop;
  wdEls.tabs.textContent = '';
  WD_SLOTS.forEach(({ slot, label }) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'wd-tab';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(slot === wd.slot));
    b.textContent = label;
    b.addEventListener('click', () => { if (wd.slot !== slot) { wd.slot = slot; renderWardrobe(); } });
    wdEls.tabs.appendChild(b);
  });
  wdEls.grid.textContent = '';
  if (!wd.catalog || !wd.mine) {
    if (!wd.loading) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'action';
      retry.textContent = 'Повторить';
      retry.addEventListener('click', loadWardrobe);
      wdEls.grid.appendChild(retry);
    }
  } else {
    wd.catalog.filter((i) => i.slot === wd.slot).forEach((item) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'wd-card' + (wdWorn(item) ? ' on' : '') + (wdOwns(item) && item.available ? '' : ' dim');
      card.dataset.code = item.code;
      const name = document.createElement('span');
      name.className = 'wd-name';
      name.textContent = item.name;
      const status = document.createElement('span');
      status.className = 'wd-status';
      status.textContent = wdStatus(item);
      if (wdBuyable(item)) status.classList.add('price');
      card.append(wdScene(item.slot, item.code, true), name, status);
      card.addEventListener('click', () => openWdPreview(item));
      wdEls.grid.appendChild(card);
    });
  }
  wdEls.tabs.scrollLeft = tabsLeft;
  wdEls.grid.scrollTop = gridTop;
  const on = !!wd.mine && wd.mine.showInRating;
  wdEls.vis.setAttribute('aria-checked', String(on));
  wdEls.vis.disabled = wd.busy || !wd.mine;
  renderWdPreview();
}

function openWdPreview(item) {
  wd.preview = item;
  wd.confirm = false;
  wd.prevMsg = '';
  wdEls.pMsg.textContent = '';
  wdEls.pSheet.hidden = false;
  renderWdPreview();
}

function closeWdPreview() {
  if (wd.busy) return;
  wdEls.pSheet.hidden = true;
  wd.preview = null;
  wd.confirm = false;
  wd.prevMsg = '';
}

function renderWdPreview() {
  const item = wd.preview;
  if (!item || wdEls.pSheet.hidden) return;
  wdEls.pTitle.textContent = item.name;
  wdEls.pStatus.textContent = wdStatus(item);
  wdEls.pScene.replaceWith(wdScene(item.slot, item.code, false));
  wdEls.pScene = wdEls.pSheet.querySelector('.wd-scene');
  wdEls.pScene.id = 'wd-prev-scene';
  wdEls.pDesc.textContent = item.description;
  const kind = wdActKind(item);
  const confirming = wd.confirm && kind === 'buy';
  let act = '';
  if (kind === 'unequip') act = 'Снять';
  else if (kind === 'equip') act = 'Надеть';
  else if (kind === 'buy') act = confirming ? 'Потратить' : 'Купить за ' + wdPriceText(item.price);
  let hint = '';
  if (confirming) hint = 'Потратить ' + wdPriceText(item.price) + '? Вернуть предмет нельзя';
  else if (!act) hint = !item.available ? 'Этот предмет появится позже' : 'Этого предмета у вас пока нет';
  wdEls.pMsg.textContent = wd.prevMsg || hint;
  wdEls.pNote.hidden = kind !== 'buy';
  wdEls.pAct.hidden = !act;
  wdEls.pAct.textContent = act;
  wdEls.pAct.disabled = wd.busy;
  wdEls.pClose.textContent = confirming ? 'Отмена' : 'Закрыть';
  wdEls.pClose.disabled = wd.busy;
}

// тексты ошибок гардероба
function wdErrorText(r) {
  if (r.kind === 'fatal') return r.text;
  if (r.kind === 'invalid') return 'Ответ сервера не распознан. Обновите экран';
  const byDetail = {
    not_owned: 'Этого предмета у вас нет', item_unavailable: 'Этот предмет пока недоступен', slot_mismatch: 'Предмет не подходит к этому слоту',
    unknown_item: 'Такого предмета нет', request_conflict: 'Запрос уже обработан, обновите экран'
  };
  if (r.kind === 'conflict') return byDetail[r.detail] || 'Не удалось выполнить действие';
  return 'Нет связи с сервером. Попробуйте ещё раз';
}

// Один POST гардероба. { kind: 'ok', data } | { kind: 'conflict', detail } | { kind: 'fatal', text } | { kind: 'retry' } | { kind: 'invalid' }
async function wdPostOnce(path, payload, validate) {
  try {
    const res = await postJson(path, payload);
    if (res.status === 401) return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    if (res.status === 429) return { kind: 'fatal', text: 'Слишком часто: подождите секунду и повторите' };
    if (res.status === 404) return { kind: 'conflict', detail: 'unknown_item' };
    if (res.status === 400) return { kind: 'fatal', text: 'Неверные параметры' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      return { kind: 'conflict', detail: typeof body.detail === 'string' ? body.detail : '' };
    }
    if (!res.ok) return isServerError(res) ? { kind: 'retry', status: res.status } : { kind: 'fatal', text: 'Не удалось выполнить действие' };
    const body = await readJsonBody(res);
    return body.ok && validate(body.data) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', status: 0 };
  }
}

// Действие гардероба: защита от повторного нажатия, один request_id на действие, до 3 попыток при сбоях сети и 5xx
async function wdAct(path, body, validate, onOk) {
  if (wd.busy) return;
  wd.prevMsg = '';
  if (!(tg && tg.initData)) { wdToast('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { wdToast('Ошибка'); return; }
  wd.busy = true;
  renderWardrobe();
  const result = await postWithRetries(() => wdPostOnce(path, { request_id: id, ...body }, validate));
  wd.busy = false;
  if (result && result.kind === 'ok') {
    try { onOk(result.data); haptic('light'); } catch (e) { wdToast('Не удалось показать результат. Обновите экран'); }
  } else {
    wdToast(wdErrorText(result || { kind: 'retry' }));
  }
  renderWardrobe();
}

function wdApplyEquipped(equipped) {
  wd.mine.equipped = wdNormalizeEquipped(equipped);
  applySkins(wd.mine.equipped);
  setOwnCosmetics({ equipped: wd.mine.equipped });
}

function wdEquip(item) {
  wdAct('/api/cosmetics/equip', { slot: item.slot, code: item.code }, validWdEquip, (d) => wdApplyEquipped(d.equipped));
}

function wdUnequip(item) {
  wdAct('/api/cosmetics/unequip', { slot: item.slot }, validWdEquip, (d) => wdApplyEquipped(d.equipped));
}

function wdToggleVisibility() {
  if (!wd.mine) return;
  wdAct('/api/cosmetics/visibility', { show_in_rating: !wd.mine.showInRating }, validWdVisibility, (d) => { wd.mine.showInRating = d.show_in_rating; });
}

// ----- покупка -----
const wdValidBuy = (d) => !!d && typeof d.item_code === 'string' && isCount(d.balance);
const wdValidInvoice = (d) => !!d && typeof d.invoice_url === 'string';

// Ссылка на оплату принимается только вида https://t.me/... (схема https, хост t.me, без учётных данных)
function wdSafeInvoiceUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' && u.hostname === 't.me' && !u.username && !u.password && !u.port ? u.href : null;
  } catch (e) {
    return null;
  }
}

function wdBuyErrorText(r, item, status) {
  if (r.kind === 'conflict') {
    if (r.detail === 'insufficient_chips') {
      const lack = item.price && srv.loaded ? item.price.amount - srv.balance : 0;
      return lack > 0 ? 'Не хватает ' + formatNumber(lack) + ' фишек' : 'Не хватает фишек';
    }
    if (r.detail === 'insufficient_gems') {
      const lack = item.price && shop.gems !== null ? item.price.amount - shop.gems : 0;
      return (lack > 0 ? 'Не хватает ' + formatNumber(lack) + ' кристаллов' : 'Не хватает кристаллов') + '. Их можно купить на странице «Кристаллы»';
    }
    return ({
      already_owned: 'Этот предмет уже у вас', item_unavailable: 'Этот предмет пока недоступен', not_for_chips: 'Этот предмет продаётся за кристаллы',
      not_for_gems: 'Этот предмет продаётся за фишки', request_conflict: 'Запрос уже обработан, обновите экран', unknown_item: 'Такого предмета нет'
    })[r.detail] || 'Не удалось выполнить покупку';
  }
  if (r.kind === 'retry' && status === 503) return 'Покупки сейчас недоступны. Попробуйте позже';
  return wdErrorText(r);
}

// Запрос покупки с одним request_id, до 3 попыток при сбое сети и 5xx (как у «Надеть»); null, если запрос не ушёл
async function wdBuyRequest(path, item, validate) {
  if (!(tg && tg.initData)) { wd.prevMsg = 'Откройте игру через бота в Telegram'; renderWardrobe(); return null; }
  const id = makeRequestId();
  if (!id) { wd.prevMsg = 'Ошибка'; renderWardrobe(); return null; }
  wd.busy = true;
  wd.prevMsg = '';
  renderWardrobe();
  let lastStatus = null;
  const result = await postWithRetries(async () => {
    const r = await wdPostOnce(path, { request_id: id, item_code: item.code }, validate);
    if (r.kind === 'retry') lastStatus = r.status;
    return r;
  });
  wd.busy = false;
  return { result: result || { kind: 'retry' }, status: lastStatus };
}

// Баланс из ответа сервера: существующим механизмом (srv.balance, общий показ, балансы игр), если сейчас нет раунда или анимации
function wdApplyBalance(balance) {
  if (anyRoundBusy()) { loadServer('after'); return; }
  srv.balance = balance;
  renderAll();
  applyAccrualTick(0);
}

function wdBuy(item) {
  if (!wdBuyable(item)) return;
  if (!wd.confirm) { wd.confirm = true; wd.prevMsg = ''; renderWdPreview(); return; }
  wdBuyDirect(item);
}

async function wdBuyDirect(item) {
  if (wd.busy) return;
  const sent = await wdBuyRequest('/api/cosmetics/buy', item, wdValidBuy);
  wd.confirm = false;
  if (!sent) return;
  if (sent.result.kind === 'ok') {
    wd.mine.owned.add(item.code);
    wdApplyBalance(sent.result.data.balance);
    if (Number.isSafeInteger(sent.result.data.gems)) { shop.gems = sent.result.data.gems; renderShop(); }      // кристаллы после покупки за кристаллы
    wd.prevMsg = 'Предмет куплен: ' + wdPriceText(item.price);
    haptic('success');
  } else {
    wd.prevMsg = wdBuyErrorText(sent.result, item, sent.status);
  }
  renderWardrobe();
}

// Условия покупки: страница рядом с приложением (тот же каталог GitHub Pages), открывается способом Telegram для внешних ссылок
function openTerms() {
  const url = new URL('terms.html', window.location.href).href;
  if (tg && typeof tg.openLink === 'function') tg.openLink(url);
  else window.open(url, '_blank', 'noopener');
}

async function loadWardrobe() {
  if (wd.loading) return;
  if (!(tg && tg.initData)) { setWdMsg('Откройте игру через бота в Telegram'); return; }
  wd.loading = true;
  setWdMsg('Загрузка…');
  renderWardrobe();
  try {
    const [cat, mine] = await Promise.all([
      fetchGameState('/api/cosmetics/catalog', validWdCatalog), fetchGameState('/api/cosmetics/mine', validWdMine)
    ]);
    wd.catalog = wdNormalizeCatalog(cat);
    wd.mine = wdNormalizeMine(mine);
    setWdMsg('');
  } catch (e) {
    setWdMsg(e && e.text ? e.text : 'Нет связи с сервером');
  } finally {
    wd.loading = false;
    renderWardrobe();
  }
}

// Вкладка «Стиль»: данные гардероба берутся с сервера при каждом открытии вкладки (при старте приложения их нет). Выбранный слот (wd.slot) живёт
// в памяти страницы всю сессию и не пишется в localStorage. Лист предпросмотра остаётся листом поверх вкладки.
function openWardrobe() {
  wd.open = true;
  wdEls.pSheet.hidden = true;
  renderWardrobe();
  loadWardrobe();
}

// Уход с вкладки: опрос оплаты и открытый предпросмотр закрываются (раньше это делала кнопка «Назад» листа)
function closeWardrobe() {
  if (!wd.open) return;
  wd.open = false;
  wd.confirm = false;
  wd.prevMsg = '';
  wdEls.pSheet.hidden = true;
  wd.preview = null;
}

wdEls.vis.addEventListener('click', wdToggleVisibility);
wdEls.pClose.addEventListener('click', () => {
  if (wd.confirm) { wd.confirm = false; renderWdPreview(); return; }      // «Отмена» в подтверждении покупки
  closeWdPreview();
});
wdEls.pAct.addEventListener('click', () => {
  const item = wd.preview;
  if (!item || wd.busy) return;
  const kind = wdActKind(item);
  if (kind === 'unequip') wdUnequip(item);
  else if (kind === 'equip') wdEquip(item);
  else if (kind === 'buy') wdBuy(item);
});
wdEls.terms.addEventListener('click', openTerms);
closeOnBackdropTap(wdEls.pDim, closeWdPreview);

// #endregion

