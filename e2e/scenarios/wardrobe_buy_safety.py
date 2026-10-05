"""Безопасность покупок: теги и неизвестные коды в ответах сервера не создают разметки; цены с ненормальными значениями не показываются как покупаемые
(нет кнопки «Купить»); чужие и опасные ссылки на оплату отвергаются; текст ошибки сервера не выводится как есть. Ответы подменяются в странице."""
import json

from harness import check

NAME = "wardrobe_buy_safety"
USERS = {"me": {"rate": 0}}
EVIL = '<img src=x onerror="window.__pwn=1">'

OVERRIDE = """
(() => {
  const orig = window.fetch;
  const json = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });
  const evil = %s;
  const item = (code, slot, price, extra) => Object.assign({ code, slot, name: code, description: evil, rarity: 'common', price, starter: false, available: true }, extra || {});
  window.fetch = async (u, o) => {
    const url = String(u);
    if (url.includes('/api/cosmetics/catalog')) {
      return json(200, { slots: [], items: [
        item('badge_none', 'badge', { currency: 'chips', amount: 5 }, { starter: true, name: 'Старт' }),
        item('badge_spade', 'badge', { currency: 'stars', amount: 150 }, { name: 'Пика' }),
        item('badge_flame', 'badge', { currency: 'stars', amount: 75 }, { name: 'Пламя', available: false }),
        item('evil_code', 'badge', { currency: 'stars', amount: 10 }) ] });
    }
    if (url.includes('/api/cosmetics/mine')) return json(200, { owned: [], equipped: {}, show_in_rating: true });
    if (o && o.method === 'POST' && url.includes('/api/cosmetics/buy')) return json(409, { detail: evil });
    if (o && o.method === 'POST' && url.includes('/api/cosmetics/invoice')) return json(200, { invoice_url: window.__url });
    return orig(u, o);
  };
  return true;
})()
"""
# цены, которые не должны делать предмет покупаемым (подставляются в карточку «Пика» по очереди)
BAD_PRICES = [{"currency": "stars", "amount": -5}, {"currency": "stars", "amount": 0}, {"currency": "stars", "amount": 1.5}, {"currency": "stars", "amount": "150"},
              {"currency": "stars", "amount": 1e15}, {"currency": EVIL, "amount": 5}, {"currency": "rub", "amount": 100}, {"amount": 100}, "150", None, [], {"currency": "chips"}]


async def run(w):
    p = w.page
    await p.ev(OVERRIDE % json.dumps(EVIL))
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль")
    await p.tap("#wardrobe-open")
    await p.wait("!document.getElementById('wd-sheet').hidden && wd.catalog !== null && wd.mine !== null", 10, "гардероб")
    await p.tap("#wd-tabs .wd-tab:nth-child(8)")
    cards = "[...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.dataset.code, c.querySelector('.wd-status').textContent.replace(/\\s/g, ' ')])"
    check("карточки: у стартового и «Скоро» цены нет, у «Пики» цена, неизвестный код пропущен", await p.ev(cards),
          [["badge_none", "Надето"], ["badge_spade", "150 Stars"], ["badge_flame", "Скоро"]])
    check("в карточках нет разметки из ответа", await p.ev("[document.querySelectorAll('#wd-grid img, #wd-grid b').length, window.__pwn || 0]"), [0, 0])
    # ненормальные цены: предмет не покупаемый
    for bad in BAD_PRICES:
        norm = await p.ev("wdNormalizeCatalog({ items: [{ code: 'badge_spade', slot: 'badge', name: 'Пика', description: '', available: true, price: %s }] })[0].price" % json.dumps(bad))
        check("цена %s не принимается" % json.dumps(bad)[:40], norm, None)
    check("нормальная цена принимается как есть", await p.ev("wdNormalizeCatalog({ items: [{ code: 'badge_spade', slot: 'badge', name: 'Пика', description: '', available: true, price: { currency: 'chips', amount: 20000 } }] })[0].price"), {"currency": "chips", "amount": 20000})
    check("без цены кнопки покупки нет, подпись «Не получено»", await p.ev(
        "(() => { wd.catalog = wdNormalizeCatalog({ items: [{ code: 'badge_spade', slot: 'badge', name: 'Пика', description: '', available: true, price: { currency: 'stars', amount: -1 } }] }); renderWardrobe();"
        " openWdPreview(wd.catalog[0]); return [document.querySelector('#wd-grid .wd-status').textContent, document.getElementById('wd-prev-act').hidden]; })()"), ["Не получено", True])
    await p.tap("#wd-prev-close")
    # ответ покупки с тегом в тексте ошибки: показывается свой текст
    await p.ev("loadWardrobe()")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length === 3", 10, "каталог")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.ev("wdBuyChips({ code: 'badge_spade', slot: 'badge', price: { currency: 'chips', amount: 5 } })")
    await p.wait("!wd.busy", 20, "запрос")
    check("текст ошибки сервера не выводится как есть, разметки нет", await p.ev("[document.getElementById('wd-prev-msg').textContent, document.querySelectorAll('#wd-prev-sheet img').length, window.__pwn || 0]"),
          ["Не удалось выполнить покупку", 0, 0])
    # опасные ссылки на оплату
    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "https://evil.example/pay", "https://t.me@evil.example/x", "http://t.me/x", "//t.me/x", "https://t.me.evil.example/x", ""):
        await p.ev("window.__url = %s; window.__invoices = []" % json.dumps(bad))
        await p.ev("wdBuyStars({ code: 'badge_spade', slot: 'badge', price: { currency: 'stars', amount: 150 } })")
        await p.wait("!wd.busy", 20, "запрос")
        check("ссылка %r отвергнута" % bad[:30], await p.ev("[window.__invoices.length, document.getElementById('wd-prev-msg').textContent, wd.paying]"), [0, "Некорректная ссылка на оплату. Попробуйте позже", False])
    await p.ev("window.__url = 'https://t.me/$ok'; window.__invoiceStatus = 'cancelled'; window.__invoices = []")
    await p.ev("wdBuyStars({ code: 'badge_spade', slot: 'badge', price: { currency: 'stars', amount: 150 } })")
    await p.wait("window.__invoices.length === 1", 10, "корректная ссылка открывается")
    check("корректная ссылка открывается (контроль)", await p.ev("window.__invoices[0]"), "https://t.me/$ok")
    check("страница цела", await p.ev("typeof window.__pwn"), "undefined")
