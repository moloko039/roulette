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
  card_back: ['back_classic', 'back_midnight', 'back_ember', 'back_leaves', 'back_rug', 'back_patina', 'draft_back'], chip: ['chip_plain', 'chip_ring', 'chip_gold', 'void_chip', 'chip_cork', 'chip_patina', 'chip_pearl', 'chip_leaf', 'draft_chip'],
  table: ['table_green', 'table_blue', 'table_violet', 'table_autumn', 'draft_table', 'void_table', 'table_oilcloth', 'table_deep'], mine_icons: ['mine_classic', 'mine_star', 'mine_gem', 'mine_acorn', 'draft_mines', 'mine_beetle', 'mine_patina', 'mine_urchin'],
  keno_ball: ['keno_round', 'keno_hex', 'keno_lotto', 'keno_bubble', 'keno_apple', 'draft_keno'], crash: ['crash_line', 'crash_neon', 'draft_crash', 'crash_barrel', 'ref_comet', 'crash_deep', 'crash_maple'],
  avatar_frame: ['frame_plain', 'frame_thin', 'frame_double', 'frame_crown', 'ref_beacon', 'frame_patina', 'frame_dacha', 'frame_wreath', 'draft_frame'], badge: ['badge_none', 'badge_spade', 'badge_flame', 'draft_badge', 'void_badge', 'ref_scout', 'badge_dacha', 'badge_pumpkin']
};
const skinKnown = (slot, code) => typeof code === 'string' && Object.prototype.hasOwnProperty.call(SKIN_CODES, slot) && SKIN_CODES[slot].includes(code);
const skinStarter = (slot) => SKIN_CODES[slot][0];
// публичные слоты: только нестартовые известные коды (стартовые «ничего не рисуют»)
const publicSkin = (slot, code) => ((slot === 'avatar_frame' || slot === 'badge') && skinKnown(slot, code) && code !== skinStarter(slot) ? code : null);
const BADGE_SVG = {
  badge_spade: BJ_SUIT_SETS.default.S,
  badge_flame: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c1 4 5 6 5 11a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-4-1-6 1-10z"/></svg>',
  draft_badge: '<svg viewBox="0 0 24 24" aria-hidden="true"><defs><mask id="dbm"><path d="M0 0h24v24H0z" fill="#fff"/><circle cx="6" cy="11" r="1.8" opacity=".6"/><circle cx="12" cy="9" r="1.5" opacity=".5"/><circle cx="17" cy="14" r="1.6" opacity=".6"/><circle cx="19" cy="10" r="1.3" opacity=".5"/></mask></defs><g transform="rotate(-6 12 12)" mask="url(#dbm)" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="6" width="21" height="12" rx="1" stroke-width="1.4"/><rect x="3" y="7.5" width="18" height="9" rx=".5" stroke-width=".7"/><path d="M4.3 10.5l.3 1.5.3-1.5v3M5.8 10.5h1m-.5 0v3M7.4 10.5v3h.8m0-1.5H7.4m.8 0h-.8M9 10.5v3h.8m-.8-1.5h.6m-.8-1.5h.8M10.6 10.5v3m0-3h.8v1.5h-.8M12.2 10.5l.6 3m0-3l-.6 3m-.2-1.5h1M13.8 13.5h1.2m-.1 0v-3h-.8v3M15.6 10.5v3h.8m-.8-1.5h.6m-.8-1.5h.8M17.2 10.5v3m.8-3v3m-.8-1.5h.8M18.8 10.5v3h.9v-3z" stroke-width=".75"/></g></svg>',
  badge_dacha: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="2" width="11" height="2" rx=".6" fill="currentColor"/><path d="M4.5 4.5h15l.8 2.6c-1.4.5-2.8-.2-4.1.4s-2.8-.2-4.1.4s-2.8-.2-4.1.4L4.5 4.5z" fill="#F3E7CF"/><line x1="5" y1="4.8" x2="19" y2="4.8" stroke="#8A5A35"/><path d="M5.5 7.2h13v12a2.8 2.8 0 0 1-2.8 2.8h-7.4A2.8 2.8 0 0 1 5.5 19.2z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M8 11c-1.2 2-1 4.8.2 6.8 1 1.2 2.6.8 2.6-.6.3-2.4 0-4.8-1.2-6.5-.4-.6-1.2-.4-1.6.3zM16 11.5c1.2 2 1 4.8-.2 6.8-1 1.2-2.6.8-2.6-.6-.3-2.4 0-4.8 1.2-6.5.4-.6 1.2-.4 1.6.3z" fill="currentColor"/><circle cx="9.5" cy="14" r=".7" fill="#F3E7CF"/><circle cx="14.5" cy="14.5" r=".7" fill="#F3E7CF"/><circle cx="12" cy="11" r=".9" fill="#F3E7CF"/><circle cx="12" cy="15.5" r=".7" fill="#F3E7CF"/></svg>',
  badge_pumpkin: '<svg viewBox="0 0 24 24" aria-hidden="true"><defs><radialGradient id="pg" cx="50%" cy="60%" r="45%"><stop offset="0%" stop-color="#FFF9C4"/><stop offset="100%" stop-color="#E65100"/></radialGradient></defs><path d="M12 5c0-1.8.8-3 2.2-3.6-.3.8-.1 1.6.3 2.1-.8.4-1.6.8-2.5 1.5z" fill="#4B6E2C"/><path d="M12 6c-1.4-.7-4.2-.7-6.5.8C3.2 8.3 2 11 2 14c0 4.2 3.2 7.5 7 7.8 1 .1 2-.4 3-.8 1 .4 2 .9 3 .8 3.8-.3 7-3.6 7-7.8 0-3-1.2-5.7-3.5-7.2-2.3-1.5-5.1-1.5-6.5-.8z" fill="currentColor"/><path d="M8 7c-1.5 2-2 4.5-2 7s.5 5 2 7M16 7c1.5 2 2 4.5 2 7s-.5 5-2 7" fill="none" stroke="#7A2800" stroke-width="0.8" opacity="0.4"/><path d="M7 11.5l2.2-2v2.5zM17 11.5l-2.2-2v2.5zM12 12l1 1.6h-2zM6 15.2c1.3 1.5 3.3 2.8 6 2.8s4.7-1.3 6-2.8c-.8.7-2 1.2-3 1.2v-1h-1.5v1h-3v-1H9v1c-1 0-2.2-.5-3-1.2z" fill="url(#pg)" stroke="#260C02" stroke-width="0.5" stroke-linejoin="round"/></svg>',
  ref_scout: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11l18-8-8 18-2-8z"/></svg>',
  void_badge: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="6" y1="12" x2="18" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
};
const CR_SAMPLE_SVG = '<svg class="cr-svg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true"><path class="cr-axis" d="M0 149H300M1 0V150"/><path class="cr-curve" d="M0 149L60 140L120 118L180 80L240 30L300 2"/></svg>';

// рамка и значок самого игрока (из cosmetics.equipped в /api/me и после смены в гардеробе)
let ownEquipped = { avatar_frame: null, badge: null };
let ownPatina = {};
let ratingLast = null;   // последний ответ рейтинга: перерисовывается при смене своих рамки и значка

function decorateAvatar(el, frame, stage) {
  if (frame) el.setAttribute('data-skin-avatar_frame', frame);
  else el.removeAttribute('data-skin-avatar_frame');
  // рамка «Патина» темнеет по стажу аккаунта: стадия 0..4 приходит с сервера (у себя в /api/me, у других в рейтинге)
  if (frame === 'frame_patina' && Number.isInteger(stage) && stage >= 0 && stage <= 4) el.setAttribute('data-patina-avatar_frame', String(stage));
  else el.removeAttribute('data-patina-avatar_frame');
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
  return { avatar_frame: publicSkin('avatar_frame', src.avatar_frame), frame_stage: src.avatar_frame_stage, badge: publicSkin('badge', src.badge) };
}

function renderOwnCosmetics() {
  decorateAvatar(profileEls.avatar, ownEquipped.avatar_frame, ownEquipped.frame_stage);
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
  if (c && c.patina && typeof c.patina === 'object') {
    ownPatina = {};
    for (const [k, v] of Object.entries(c.patina)) {
      if (Number.isInteger(v) && v >= 0 && v <= 4) ownPatina[k] = v;
    }
  } else if (c && (!c.patina || typeof c.patina !== 'object')) {
    ownPatina = {};
  }
  const eq = c && c.equipped && typeof c.equipped === 'object' ? c.equipped : {};
  const next = { avatar_frame: publicSkin('avatar_frame', eq.avatar_frame), frame_stage: ownPatina.avatar_frame, badge: publicSkin('badge', eq.badge) };
  if (next.avatar_frame === ownEquipped.avatar_frame && next.frame_stage === ownEquipped.frame_stage && next.badge === ownEquipped.badge) return;
  ownEquipped = next;
  renderOwnCosmetics();
}

// ----- раздел «Оформление» магазина (гардероб) -----
const wdEls = {
  tabs: document.getElementById('wd-tabs'), grid: document.getElementById('wd-grid'), vis: document.getElementById('wd-vis'),
  msg: document.getElementById('wd-msg'), collections: document.getElementById('wd-collections'),
  pSheet: document.getElementById('wd-prev-sheet'), pDim: document.getElementById('wd-prev-dim'), pTitle: document.getElementById('wd-prev-title'),
  pStatus: document.getElementById('wd-prev-status'), pScene: document.getElementById('wd-prev-scene'), pDesc: document.getElementById('wd-prev-desc'),
  pMsg: document.getElementById('wd-prev-msg'), pNote: document.getElementById('wd-prev-note'), pClose: document.getElementById('wd-prev-close'), pAct: document.getElementById('wd-prev-act'),
  terms: document.getElementById('wd-terms'),
  setBox: document.getElementById('wd-set-box'), setDesc: document.getElementById('wd-set-desc'), setAct: document.getElementById('wd-set-act')
};
const wd = { open: false, catalog: null, sets: null, mine: null, slot: 'card_back', busy: false, loading: false, preview: null, confirm: false, prevMsg: '', setConfirm: false, setMsg: '' };

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

function wdNormalizeSets(d) {
  const sets = [];
  if (!Array.isArray(d.sets)) return sets;
  d.sets.forEach((s) => {
    if (!s || typeof s !== 'object' || typeof s.code !== 'string' || typeof s.name !== 'string') return;
    if (!Number.isSafeInteger(s.price_gems) || s.price_gems <= 0) return;
    if (!Array.isArray(s.parts) || s.parts.some(p => typeof p !== 'string')) return;
    sets.push({
      code: s.code, name: s.name.slice(0, 40), price_gems: s.price_gems, parts: s.parts
    });
  });
  return sets;
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
  return { owned, equipped: wdNormalizeEquipped(d.equipped), showInRating: d.show_in_rating, collections: wdNormalizeCollections(d.collections) };
}

// Коллекции (docs/COLLECTIONS.md): только известные поля, тексты ограничены по длине (выводятся через textContent); части это коды предметов своих слотов
function wdNormalizeCollections(list) {
  const out = [];
  (Array.isArray(list) ? list : []).forEach((c) => {
    if (!c || typeof c.code !== 'string' || typeof c.name !== 'string' || !Array.isArray(c.parts)) return;
    if (!Number.isSafeInteger(c.owned) || !Number.isSafeInteger(c.total) || c.total < 1 || c.owned < 0 || c.owned > c.total) return;
    out.push({ code: c.code.slice(0, 30), name: c.name.slice(0, 40), how: typeof c.how === 'string' ? c.how.slice(0, 160) : '',
      parts: c.parts.filter((x) => typeof x === 'string').slice(0, 20), owned: c.owned, total: c.total, complete: c.owned === c.total });
  });
  return out;
}

// Коллекция, к которой относится предмет (только пока он не получен он помечается как часть коллекции)
const wdCollectionOf = (item) => (wd.mine ? wd.mine.collections.find((c) => c.parts.includes(item.code)) || null : null);

const wdOwns = (item) => item.starter || (wd.mine && wd.mine.owned.has(item.code));
const wdWorn = (item) => !!wd.mine && wd.mine.equipped[item.slot] === item.code;
const wdCanEquip = (item) => item.available && wdOwns(item);
const wdBuyable = (item) => item.available && !wdOwns(item) && !!item.price;
function wdStatus(item) {
  if (wdWorn(item)) return 'Надето';
  if (!item.available) return 'Скоро';
  if (wdOwns(item)) return 'Есть';
  if (wdCollectionOf(item)) return 'Коллекция';
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
  if (code === 'chip_patina' || code === 'back_patina' || code === 'mine_patina' || code === 'frame_patina') {
    const stage = ownPatina[slot];
    if (stage !== undefined) box.setAttribute('data-patina-' + slot, String(stage));
  }
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

// Полоса коллекций над слотами: название, сколько частей собрано, как получить; собранная коллекция помечена
function renderWdCollections() {
  wdEls.collections.textContent = '';
  const list = wd.mine ? wd.mine.collections : [];
  list.forEach((c) => {
    const box = document.createElement('div');
    box.className = 'wd-collection' + (c.complete ? ' complete' : '');
    box.dataset.code = c.code;
    const head = document.createElement('strong');
    head.textContent = c.name + ': ' + c.owned + ' из ' + c.total + (c.complete ? ' (собрана)' : '');
    const how = document.createElement('small');
    how.textContent = c.how;
    box.append(head, how);
    wdEls.collections.appendChild(box);
  });
  renderSetEffect();
}

// Эффект полного набора: все части собранной коллекции надеты, у корня появляется атрибут data-set-complete (лёгкое мерцание названия, только визуально, без бонусов)
function renderSetEffect() {
  const done = wd.mine ? wd.mine.collections.find((c) => c.complete && c.parts.every((code) => Object.values(wd.mine.equipped).includes(code))) : null;
  if (done) document.documentElement.setAttribute('data-set-complete', done.code);
  else document.documentElement.removeAttribute('data-set-complete');
}

function renderWardrobe() {
  renderWdCollections();
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
  resetGiftBox();
  wd.preview = item;
  wd.confirm = false;
  wd.prevMsg = '';
  wdEls.pMsg.textContent = '';
  wdEls.pSheet.hidden = false;
  renderWdPreview();
}

function closeWdPreview() {
  if (wd.busy) return;
  resetGiftBox();
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
  else if (!act) hint = !item.available ? 'Этот предмет появится позже' : (wdCollectionOf(item) ? 'Часть коллекции «' + wdCollectionOf(item).name + '»: ' + wdCollectionOf(item).how : 'Этого предмета у вас пока нет');
  wdEls.pMsg.textContent = wd.prevMsg || hint;
  wdEls.pNote.hidden = kind !== 'buy';
  wdEls.pAct.hidden = !act;
  wdEls.pAct.textContent = act;
  wdEls.pAct.disabled = wd.busy;
  renderWdSetBox();
  wdEls.pClose.textContent = confirming || wd.setConfirm ? 'Отмена' : 'Закрыть';
  wdEls.pClose.disabled = wd.busy;
  renderGiftControls();
}

function renderWdSetBox() {
  const item = wd.preview;
  if (!item || !wd.sets || !wd.catalog || !wd.mine) {
    wdEls.setBox.hidden = true;
    return;
  }
  if (!item.price || item.price.currency !== 'gems' || wd.mine.owned.has(item.code)) {
    wdEls.setBox.hidden = true;
    return;
  }
  const set = wd.sets.find((s) => s.parts.includes(item.code));
  if (!set) {
    wdEls.setBox.hidden = true;
    return;
  }
  const hasAnyPart = set.parts.some((p) => wd.mine.owned.has(p));
  if (hasAnyPart) {
    wdEls.setBox.hidden = true;
    return;
  }
  let sum = 0;
  for (const partCode of set.parts) {
    const partItem = wd.catalog.find((i) => i.code === partCode);
    if (partItem && partItem.price && partItem.price.currency === 'gems') {
      sum += partItem.price.amount;
    }
  }
  wdEls.setBox.hidden = false;
  wdEls.setDesc.textContent = `Весь набор «${set.name}»: ${set.price_gems} 💎 (вместо ${sum} 💎 за все части)`;
  if (wd.setConfirm) {
    wdEls.setAct.textContent = 'Потратить';
    if (!wd.prevMsg) wdEls.pMsg.textContent = `Потратить ${set.price_gems} 💎? Вернуть набор нельзя`;
  } else {
    wdEls.setAct.textContent = 'Купить набор';
  }
  wdEls.setAct.disabled = wd.busy;
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

function wdApplyEquipped(equipped, patina) {
  wd.mine.equipped = wdNormalizeEquipped(equipped);
  if (patina && typeof patina === 'object') {          // стадии износа надетых вещей приходят в ответе на надевание (как cosmetics.patina в /api/me)
    ownPatina = {};
    for (const [k, v] of Object.entries(patina)) {
      if (Number.isInteger(v) && v >= 0 && v <= 4) ownPatina[k] = v;
    }
  } else {
    ownPatina = {};
  }
  applySkins(wd.mine.equipped, ownPatina);
  setOwnCosmetics({ equipped: wd.mine.equipped, patina: ownPatina });
}

function wdEquip(item) {
  wdAct('/api/cosmetics/equip', { slot: item.slot, code: item.code }, validWdEquip, (d) => wdApplyEquipped(d.equipped, d.patina));
}

function wdUnequip(item) {
  wdAct('/api/cosmetics/unequip', { slot: item.slot }, validWdEquip, (d) => wdApplyEquipped(d.equipped, d.patina));
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
  if (!wd.confirm) { wd.confirm = true; wd.setConfirm = false; wd.prevMsg = ''; renderWdPreview(); return; }
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

const wdValidSetBuy = (d) => !!d && typeof d.set_code === 'string' && Array.isArray(d.items) && isCount(d.balance);

function wdBuySetErrorText(r, set, status) {
  if (r.kind === 'conflict') {
    if (r.detail === 'already_owned') return 'У вас уже есть часть этого набора';
    if (r.detail === 'insufficient_gems') {
      const lack = shop.gems !== null ? set.price_gems - shop.gems : 0;
      return (lack > 0 ? 'Не хватает ' + formatNumber(lack) + ' кристаллов' : 'Не хватает кристаллов') + '. Их можно купить на странице «Кристаллы»';
    }
    return ({
      request_conflict: 'Запрос уже обработан, обновите экран', unknown_set: 'Такого набора нет'
    })[r.detail] || 'Не удалось выполнить покупку';
  }
  if (r.kind === 'retry' && status === 503) return 'Покупки сейчас недоступны. Попробуйте позже';
  return wdErrorText(r);
}

async function wdBuySetRequest(path, set, validate) {
  if (!(tg && tg.initData)) { wd.prevMsg = 'Откройте игру через бота в Telegram'; renderWardrobe(); return null; }
  const id = makeRequestId();
  if (!id) { wd.prevMsg = 'Ошибка'; renderWardrobe(); return null; }
  wd.busy = true;
  wd.prevMsg = '';
  renderWardrobe();
  let lastStatus = null;
  const result = await postWithRetries(async () => {
    const r = await wdPostOnce(path, { request_id: id, set_code: set.code }, validate);
    if (r.kind === 'retry') lastStatus = r.status;
    return r;
  });
  wd.busy = false;
  return { result: result || { kind: 'retry' }, status: lastStatus };
}

async function wdBuySetDirect(set) {
  if (wd.busy) return;
  const sent = await wdBuySetRequest('/api/cosmetics/buy-set', set, wdValidSetBuy);
  wd.setConfirm = false;
  if (!sent) return;
  if (sent.result.kind === 'ok') {
    sent.result.data.items.forEach((c) => wd.mine.owned.add(c));
    wdApplyBalance(sent.result.data.balance);
    if (Number.isSafeInteger(sent.result.data.gems)) { shop.gems = sent.result.data.gems; renderShop(); }
    wd.prevMsg = 'Набор «' + set.name + '» куплен';
    haptic('success');
  } else {
    wd.prevMsg = wdBuySetErrorText(sent.result, set, sent.status);
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
    wd.sets = wdNormalizeSets(cat);
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
  if (wd.confirm || wd.setConfirm) { wd.confirm = false; wd.setConfirm = false; renderWdPreview(); return; }      // «Отмена» в подтверждении покупки
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
wdEls.setAct.addEventListener('click', () => {
  const item = wd.preview;
  if (!item || wd.busy) return;
  const set = wd.sets && wd.sets.find((s) => s.parts.includes(item.code));
  if (!set) return;
  if (!wd.setConfirm) { wd.setConfirm = true; wd.confirm = false; wd.prevMsg = ''; renderWdPreview(); return; }
  wdBuySetDirect(set);
});
wdEls.terms.addEventListener('click', openTerms);
closeOnBackdropTap(wdEls.pDim, closeWdPreview);

// #endregion

