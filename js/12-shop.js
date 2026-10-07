// #region Магазин
// Вкладка «Магазин» (в нижней панели первая): три страницы по порядку. «Оформление» (открыта по умолчанию): гардероб (js/13-wardrobe.js). «Фишки»: покупка фишек за кристаллы (появится в E6;
// продать фишки или вывести их нельзя, это сказано прямо). «Кристаллы»: покупка пакетов за Telegram Stars (E2). Баланс кристаллов и список пакетов приходят с сервера
// (GET /api/gems/packs), цены берутся только оттуда. Оплата: счёт /api/gems/invoice, Telegram.WebApp.openInvoice, затем опрос баланса.
// Файл стоит перед js/13-wardrobe.js: функции гардероба (wdBuyErrorText, wdSafeInvoiceUrl, openWardrobe) вызываются уже после загрузки всех файлов.
const SHOP_PAGES = ['look', 'chips', 'gems'];
const GEM_POLL_MS = 1500;
const GEM_POLL_TOTAL_MS = 20000;
const GEM_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12l4 6-10 12L2 9l4-6z"/><path d="M2 9h20M9 3l3 6 3-6M9 9l3 12 3-12"/></svg>';

const shopEls = {
  pages: document.getElementById('shop-pages'), gems: document.getElementById('shop-gems'), packs: document.getElementById('gem-packs'),
  msg: document.getElementById('gem-msg'), terms: document.getElementById('gem-terms'), chipsTerms: document.getElementById('chips-terms')
};
const shop = { open: false, page: 'look', gems: null, packs: null, loading: false, busy: false, paying: false, payCode: null, pollGen: 0, msg: '' };

// Ответ GET /api/gems/packs: пакеты с кодом gems_*, звёзды и кристаллы целые и разумные; всё остальное отбрасывается
function validGemPacks(d) {
  return !!d && Array.isArray(d.packs) && Number.isSafeInteger(d.gems) && d.gems >= 0 && d.gems <= 1e9;
}

function normalizeGemPacks(d) {
  const out = [];
  d.packs.forEach((p) => {
    if (!p || typeof p.code !== 'string' || !/^gems_[a-z0-9_]{1,30}$/.test(p.code)) return;
    if (!Number.isSafeInteger(p.stars) || p.stars < 1 || p.stars > 1e6 || !Number.isSafeInteger(p.gems) || p.gems < 1 || p.gems > 1e9) return;
    out.push({ code: p.code, stars: p.stars, gems: p.gems });
  });
  return out;
}

function setShopMsg(text) {
  shop.msg = text;
  shopEls.msg.textContent = text;
}

function renderShop() {
  shopEls.pages.querySelectorAll('[data-page]').forEach((btn) => {
    const on = btn.dataset.page === shop.page;
    btn.setAttribute('aria-selected', String(on));
    btn.tabIndex = on ? 0 : -1;
  });
  SHOP_PAGES.forEach((name) => { document.getElementById('shop-page-' + name).hidden = name !== shop.page; });
  shopEls.gems.textContent = shop.gems === null ? '…' : formatNumber(shop.gems);
  shopEls.packs.textContent = '';
  (shop.packs || []).forEach((p) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'gem-pack';
    btn.dataset.code = p.code;
    btn.disabled = shop.busy || shop.paying;
    const icon = document.createElement('span');
    icon.className = 'gem-pack-icon';
    icon.innerHTML = GEM_SVG;       // постоянная разметка значка, данных сервера в ней нет
    const body = document.createElement('span');
    body.className = 'gem-pack-body';
    const name = document.createElement('strong');
    name.textContent = formatNumber(p.gems) + ' кристаллов';
    body.appendChild(name);
    if (p.gems > p.stars) {
      const bonus = document.createElement('small');
      bonus.textContent = '+' + formatNumber(p.gems - p.stars) + ' в подарок';
      body.appendChild(bonus);
    }
    const price = document.createElement('span');
    price.className = 'gem-pack-price';
    price.textContent = formatNumber(p.stars) + ' Stars';
    btn.append(icon, body, price);
    btn.addEventListener('click', () => buyGems(p));
    li.appendChild(btn);
    shopEls.packs.appendChild(li);
  });
  if (!shop.packs && !shop.loading && !shop.msg) shopEls.packs.textContent = '';
}

function showShopPage(name) {
  if (!SHOP_PAGES.includes(name)) return;
  shop.page = name;
  renderShop();
}

async function loadGems() {
  if (shop.loading) return;
  if (!(tg && tg.initData)) { setShopMsg('Откройте игру через бота в Telegram'); return; }
  shop.loading = true;
  setShopMsg('Загрузка…');
  renderShop();
  try {
    const d = await fetchGameState('/api/gems/packs', validGemPacks);
    shop.packs = normalizeGemPacks(d);
    shop.gems = d.gems;
    setShopMsg('');
  } catch (e) {
    setShopMsg(e && e.text ? e.text : 'Нет связи с сервером');
  } finally {
    shop.loading = false;
    renderShop();
  }
}

function gemErrorText(r, status) {
  if (r.kind === 'conflict') return r.detail === 'unknown_pack' || r.detail === 'unknown_item' ? 'Такого пакета нет' : 'Не удалось выполнить покупку';
  if (r.kind === 'retry') {
    if (status === 502) return 'Не удалось создать счёт. Попробуйте позже';
    if (status === 503) return 'Покупки сейчас недоступны. Попробуйте позже';
  }
  return wdErrorText(r);
}

async function buyGems(pack) {
  if (shop.busy || shop.paying) return;
  if (!(tg && typeof tg.openInvoice === 'function')) { setShopMsg('Оплата Stars работает только внутри Telegram. Откройте игру через бота'); return; }
  if (!(tg && tg.initData)) { setShopMsg('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setShopMsg('Ошибка'); return; }
  shop.busy = true;
  setShopMsg('');
  renderShop();
  let lastStatus = null;
  const result = await postWithRetries(async () => {
    const r = await wdPostOnce('/api/gems/invoice', { request_id: id, pack_code: pack.code }, wdValidInvoice);
    if (r.kind === 'retry') lastStatus = r.status;
    return r;
  });
  shop.busy = false;
  if (!result || result.kind !== 'ok') { setShopMsg(gemErrorText(result || { kind: 'retry' }, lastStatus)); renderShop(); return; }
  const url = wdSafeInvoiceUrl(result.data.invoice_url);
  if (!url) { setShopMsg('Некорректная ссылка на оплату. Попробуйте позже'); renderShop(); return; }
  const gen = ++shop.pollGen;
  const before = shop.gems === null ? 0 : shop.gems;
  shop.paying = true;
  shop.payCode = pack.code;
  setShopMsg('Ожидаем оплату в Telegram…');
  renderShop();
  const done = (text) => { if (gen === shop.pollGen) { shop.paying = false; setShopMsg(text); renderShop(); } };
  try {
    tg.openInvoice(url, (status) => {
      if (gen !== shop.pollGen) return;
      if (status === 'paid' || status === 'pending') gemPollAfterPayment(before, gen);
      else if (status === 'cancelled') done('');
      else done('Оплата не прошла');
    });
  } catch (e) {
    done('Не удалось открыть оплату. Попробуйте позже');
  }
}

// После оплаты кристаллы появляются не мгновенно: опрос баланса раз в 1,5 с до 20 с
async function gemPollAfterPayment(before, gen) {
  setShopMsg('Оплата обрабатывается…');
  const started = performance.now();
  while (gen === shop.pollGen) {
    try {
      const d = await fetchGameState('/api/gems/packs', validGemPacks);
      if (gen !== shop.pollGen) return;
      if (d.gems > before) {
        shop.gems = d.gems;
        shop.packs = normalizeGemPacks(d);
        shop.paying = false;
        setShopMsg('Кристаллы начислены: +' + formatNumber(d.gems - before));
        haptic('success');
        renderShop();
        return;
      }
    } catch (e) {
      // временный сбой опроса: пробуем дальше до конца срока
    }
    if (performance.now() - started >= GEM_POLL_TOTAL_MS) break;
    await sleep(GEM_POLL_MS);
  }
  if (gen !== shop.pollGen) return;
  shop.paying = false;
  setShopMsg('Платёж получен, кристаллы скоро появятся. Если нет, напишите в /paysupport в боте');
  renderShop();
}

// Вкладка открыта: данные берутся с сервера при каждом открытии (и кристаллы, и гардероб), не при старте приложения
function openShop() {
  shop.open = true;
  renderShop();
  openWardrobe();
  loadGems();
}

function closeShop() {
  if (!shop.open) return;
  shop.open = false;
  shop.pollGen += 1;          // устаревшие опросы и ответы openInvoice больше ничего не меняют
  shop.paying = false;
  setShopMsg('');
  closeWardrobe();
}

shopEls.pages.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-page]');
  if (btn) showShopPage(btn.dataset.page);
});
shopEls.terms.addEventListener('click', () => openTerms());
shopEls.chipsTerms.addEventListener('click', () => openTerms());
document.getElementById('shop-gems-icon').innerHTML = GEM_SVG;   // постоянная разметка значка
renderShop();
// #endregion
