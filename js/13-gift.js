// #region Подарки косметикой
// В листе предпросмотра предмета за кристаллы появляется «Подарить участнику беседы»: список участников той же беседы (имена как в рейтинге, получатель задаётся непрозрачной меткой с сервера),
// подтверждение вторым нажатием, один запрос POST /api/gifts/send. Только предметы за кристаллы; подаренное нельзя передарить (ECONOMY_ADDITIONS.md, п. 2).
// Кнопка видна, только если приложение открыто из беседы (сервер отвечает 409 no_chat иначе). Файл стоит перед js/13-wardrobe.js: функции гардероба вызываются после загрузки всех файлов.
const GIFT_CONFIRM_MS = 5000;
const giftState = { available: false, recipients: [], dailyLeft: 0, open: false, busy: false, confirmRef: null, timer: 0 };
const giftEls = { open: document.getElementById('wd-gift-open'), box: document.getElementById('wd-gift-box'), list: document.getElementById('wd-gift-list') };

function validGiftRecipients(d) {
  return !!d && Array.isArray(d.recipients) && Number.isSafeInteger(d.daily_left) && d.daily_left >= 0 && Number.isSafeInteger(d.gems) && d.gems >= 0;
}

const validGiftSend = (d) => !!d && typeof d.item_code === 'string' && Number.isSafeInteger(d.gems) && d.gems >= 0 && Number.isSafeInteger(d.daily_left) && d.daily_left >= 0 && typeof d.recipient === 'string';

// Предмет можно подарить: он продаётся за кристаллы (цена из каталога сервера)
const giftEligible = (item) => !!item && item.available && !item.starter && !!item.price && item.price.currency === 'gems';

async function loadGiftRecipients() {
  giftState.available = false;
  if (!(tg && tg.initData)) return;
  try {
    const d = await fetchGameState('/api/gifts/recipients', validGiftRecipients);
    giftState.recipients = d.recipients.filter((r) => r && typeof r.name === 'string' && typeof r.ref === 'string' && /^[0-9a-f]{32}$/.test(r.ref)).map((r) => ({ name: r.name.slice(0, 40), ref: r.ref }));
    giftState.dailyLeft = d.daily_left;
    giftState.available = true;
  } catch (e) {
    // вне беседы (409) и при сбое подарков нет: приложение работает без них
    giftState.recipients = [];
  }
  renderGiftControls();
}

function resetGiftBox() {
  clearTimeout(giftState.timer);
  giftState.open = false;
  giftState.confirmRef = null;
}

function renderGiftControls() {
  const item = wd.preview;
  const show = !!item && !wdEls.pSheet.hidden && giftState.available && giftEligible(item);
  giftEls.open.hidden = !show || giftState.open;
  giftEls.open.disabled = giftState.busy || giftState.dailyLeft <= 0 || giftState.recipients.length === 0;
  giftEls.open.textContent = giftState.dailyLeft <= 0 ? 'Подарки на сегодня закончились' : (giftState.recipients.length === 0 ? 'В беседе пока некому дарить' : 'Подарить участнику беседы');
  giftEls.box.hidden = !(show && giftState.open);
  giftEls.list.textContent = '';
  if (giftEls.box.hidden) return;
  giftState.recipients.forEach((r) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'gem-pack wd-gift-to';
    btn.dataset.ref = r.ref;
    btn.disabled = giftState.busy;
    const body = document.createElement('span');
    body.className = 'gem-pack-body';
    const name = document.createElement('strong');
    name.textContent = r.name;
    body.appendChild(name);
    const price = document.createElement('span');
    price.className = 'gem-pack-price';
    price.textContent = giftState.confirmRef === r.ref ? 'Нажмите ещё раз' : formatNumber(item.price.amount) + ' кристаллов';
    btn.append(body, price);
    btn.addEventListener('click', () => giftTo(item, r));
    li.appendChild(btn);
    giftEls.list.appendChild(li);
  });
}

function giftErrorText(r) {
  if (r.kind === 'conflict') {
    return ({
      already_owned: 'У этого участника такой предмет уже есть', insufficient_gems: 'Не хватает кристаллов. Их можно купить на странице «Кристаллы»', daily_limit: 'Подарки на сегодня закончились, попробуйте завтра',
      self_gift: 'Себе подарить нельзя', not_for_gems: 'Этот предмет за кристаллы не продаётся', item_unavailable: 'Этот предмет пока недоступен', no_chat: 'Подарки доступны, когда приложение открыто из беседы',
      not_in_chat: 'Подарки доступны только участникам беседы', request_conflict: 'Запрос уже обработан, обновите экран', unknown_item: 'Участник или предмет не найден'
    })[r.detail] || 'Не удалось подарить';
  }
  return wdErrorText(r);
}

// Подарок: первое нажатие на имя просит подтвердить (5 секунд), второе отправляет один запрос с одним request_id
async function giftTo(item, recipient) {
  if (giftState.busy) return;
  if (giftState.confirmRef !== recipient.ref) {
    giftState.confirmRef = recipient.ref;
    clearTimeout(giftState.timer);
    giftState.timer = setTimeout(() => { giftState.confirmRef = null; wd.prevMsg = ''; renderWdPreview(); }, GIFT_CONFIRM_MS);
    wd.prevMsg = 'Подарить «' + item.name + '» участнику ' + recipient.name + ' за ' + formatNumber(item.price.amount) + ' кристаллов? Вернуть нельзя';
    renderWdPreview();
    return;
  }
  clearTimeout(giftState.timer);
  giftState.confirmRef = null;
  if (!(tg && tg.initData)) { wd.prevMsg = 'Откройте игру через бота в Telegram'; renderWdPreview(); return; }
  const id = makeRequestId();
  if (!id) { wd.prevMsg = 'Ошибка'; renderWdPreview(); return; }
  giftState.busy = true;
  wd.prevMsg = '';
  renderWdPreview();
  const result = await postWithRetries(() => wdPostOnce('/api/gifts/send', { request_id: id, ref: recipient.ref, item_code: item.code }, validGiftSend));
  giftState.busy = false;
  if (result && result.kind === 'ok') {
    const d = result.data;
    shop.gems = d.gems;
    giftState.dailyLeft = d.daily_left;
    giftState.open = false;
    wd.prevMsg = 'Подарок отправлен: «' + item.name + '» для ' + d.recipient;
    haptic('success');
    renderShop();
  } else {
    wd.prevMsg = giftErrorText(result || { kind: 'retry' });
  }
  renderWdPreview();
}

giftEls.open.addEventListener('click', () => { giftState.open = true; giftState.confirmRef = null; wd.prevMsg = ''; renderWdPreview(); });
// #endregion
