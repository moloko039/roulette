// #region Рамка и значок участника
// рамка и значок самого игрока (из cosmetics.equipped в /api/me и после смены в гардеробе)
let ownEquipped = { avatar_frame: null, badge: null };
let ownPatina = {};
let ownPatinaInfo = {};      // числа для рисунка патины: chip_notches, days (с сервера, у себя)
let ratingLast = null;   // последний ответ рейтинга: перерисовывается при смене своих рамки и значка

function decorateAvatar(el, frame, stage, no) {
  if (frame) el.setAttribute('data-skin-avatar_frame', frame);
  else el.removeAttribute('data-skin-avatar_frame');
  // рамка «Патина» темнеет по стажу аккаунта: стадия 0..4 приходит с сервера (у себя в /api/me, у других в рейтинге)
  if (frame === 'frame_patina' && Number.isInteger(stage) && stage >= 0 && stage <= 4) el.setAttribute('data-patina-avatar_frame', String(stage));
  else el.removeAttribute('data-patina-avatar_frame');
  // рамка «Арка» (награда основателя): номер основателя на замковом камне берётся из data-founder-no (число с сервера, content: attr() в css)
  if (frame === 'ref_arch' && Number.isInteger(no) && no > 0) el.setAttribute('data-founder-no', String(no));
  else el.removeAttribute('data-founder-no');
}

// Значок рядом с именем; null, если значка нет. Разметка из констант клиента по проверенному коду.
function badgeEl(code, no, days, stage) {
  const svg = code ? BADGE_SVG[code] : null;
  if (!svg) return null;
  const el = document.createElement('span');
  el.className = 'rating-badge';
  el.setAttribute('data-skin-badge', code);
  el.setAttribute('aria-hidden', 'true');
  if (code === 'ref_scout' && Number.isInteger(no) && no > 0) el.setAttribute('data-founder-no', String(no));      // номер основателя на плитке «Камень» (content: attr() в css)
  if (code === 'badge_patina') {      // именной жетон: число дней в игре выбито цифрами (content: attr() в css), стадия износа темнит латунь
    if (Number.isInteger(days) && days >= 0 && days <= 99999) el.setAttribute('data-days', String(days));
    if (Number.isInteger(stage) && stage >= 0 && stage <= 4) el.setAttribute('data-patina-badge', String(stage));
  }
  el.innerHTML = svg;
  return el;
}

// Публичные слоты участника из ответа рейтинга ({слот: код}); неизвестное игнорируется
function publicOf(c) {
  const src = c && typeof c === 'object' ? c : {};
  return { avatar_frame: publicSkin('avatar_frame', src.avatar_frame), frame_stage: src.avatar_frame_stage, founder_no: src.founder_no, badge: publicSkin('badge', src.badge), badge_days: src.badge_days, badge_stage: src.badge_stage };
}

function renderOwnCosmetics() {
  decorateAvatar(profileEls.avatar, ownEquipped.avatar_frame, ownEquipped.frame_stage, ownEquipped.founder_no);
  const holder = document.getElementById('profile-badge');
  const code = ownEquipped.badge;
  holder.hidden = !(code && BADGE_SVG[code]);
  holder.textContent = '';
  if (!holder.hidden) {
    holder.setAttribute('data-skin-badge', code);
    holder.innerHTML = BADGE_SVG[code];
    const d = ownEquipped.badge_days, st = ownEquipped.badge_stage;
    if (code === 'badge_patina' && Number.isInteger(d) && d >= 0 && d <= 99999) holder.setAttribute('data-days', String(d)); else holder.removeAttribute('data-days');
    if (code === 'badge_patina' && Number.isInteger(st) && st >= 0 && st <= 4) holder.setAttribute('data-patina-badge', String(st)); else holder.removeAttribute('data-patina-badge');
  } else {
    holder.removeAttribute('data-skin-badge');
    holder.removeAttribute('data-days');
    holder.removeAttribute('data-patina-badge');
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
  if (c && c.patina_info && typeof c.patina_info === 'object') {
    ownPatinaInfo = {};
    for (const k of ['chip_notches', 'days']) if (Number.isInteger(c.patina_info[k]) && c.patina_info[k] >= 0) ownPatinaInfo[k] = c.patina_info[k];
  } else if (c && !c.patina_info) {
    ownPatinaInfo = {};
  }
  const eq = c && c.equipped && typeof c.equipped === 'object' ? c.equipped : {};
  const next = { avatar_frame: publicSkin('avatar_frame', eq.avatar_frame), frame_stage: ownPatina.avatar_frame, founder_no: c && Number.isInteger(c.founder_no) ? c.founder_no : undefined, badge: publicSkin('badge', eq.badge), badge_days: ownPatinaInfo.days, badge_stage: ownPatina.badge };
  if (next.avatar_frame === ownEquipped.avatar_frame && next.frame_stage === ownEquipped.frame_stage && next.founder_no === ownEquipped.founder_no && next.badge === ownEquipped.badge && next.badge_days === ownEquipped.badge_days && next.badge_stage === ownEquipped.badge_stage) return;
  ownEquipped = next;
  renderOwnCosmetics();
}
