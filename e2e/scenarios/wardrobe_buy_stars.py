"""Покупка за Stars в гардеробе: инвойс, проверка ссылки, openInvoice (подмена Telegram в харнессе со статусом paid, cancelled, failed, pending), опрос списка предметов
до 20 с, сообщения, блокировка кнопки, закрытие листа во время ожидания, ошибки (нет openInvoice, чужая ссылка, 429, 502, 503, уже есть, не за Stars)."""
import time

from harness import check

NAME = "wardrobe_buy_stars"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = (r"status of 409", r"status of 502")
NOTE = "Предметы не влияют на игру. Фишки за Stars не продаются."

OVERRIDE = """
(() => {
  const orig = window.__origFetch || (window.__origFetch = window.fetch);
  const json = (status, obj, extra) => new Response(JSON.stringify(obj), { status, headers: Object.assign({ 'Content-Type': 'application/json' }, extra || {}) });
  window.fetch = async (u, o) => {
    const isInv = o && o.method === 'POST' && String(u).includes('/api/cosmetics/invoice');
    const mode = window.__invMode;
    if (isInv && mode) {
      if (mode === '429') return json(429, { error: 'too_many_requests' }, { 'Retry-After': '1' });
      if (mode === '503') return json(503, { detail: 'payments_unavailable' });
      if (mode === 'ok') return json(200, { invoice_url: 'https://t.me/$e2e-override', replayed: false });
      return json(200, { invoice_url: mode, replayed: false });      // любая другая строка: ссылка как есть (проверка отказа)
    }
    return orig(u, o);
  };
  return true;
})()
"""


async def msg(p):
    return await p.ev("document.getElementById('wd-prev-msg').textContent")


async def open_item(p, tab, index):
    await p.tap("#wd-tabs .wd-tab:nth-child(%d)" % tab)
    await p.tap("#wd-grid .wd-card:nth-child(%d)" % index)
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    await p.tap(".tab[data-tab=style]")
    await p.wait("!document.querySelector('[data-screen=style]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.ev(OVERRIDE)
    clean = "(s) => s.replace(/\\s/g, ' ')"
    # цена и кнопка из каталога
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")
    check("стол: цена на карточке «Лагуна»", await p.ev("(%s)(document.querySelector('#wd-grid .wd-card:nth-child(2) .wd-status').textContent)" % clean), "150 Stars")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("кнопка и строка про игру и Stars", await p.ev("[document.getElementById('wd-prev-act').textContent, document.getElementById('wd-prev-note').textContent, document.getElementById('wd-prev-note').hidden]"), ["Купить за 150 Stars", NOTE, False])
    # paid: опрос и появление предмета
    await p.ev("window.__invoiceStatus = 'paid'; window.__invoices = []")
    await p.tap("#wd-prev-act")
    check("во время ожидания кнопка заблокирована", await p.ev("document.getElementById('wd-prev-act').disabled"), True)
    await p.wait("window.__invoices.length === 1", 10, "openInvoice вызван")
    check("ссылка из ответа сервера (t.me)", await p.ev("window.__invoices[0]"), "https://t.me/$e2e-invoice")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Оплата обрабатывается…'", 5, "обработка")
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'table_blue', 'stars', 'e2e-charge', ?)", (uid, now))
    await p.wait("document.getElementById('wd-prev-act').textContent === 'Надеть'", 10, "предмет появился")
    check("успех: сообщение, статус, кнопка разблокирована, сетка обновилась", await p.ev(
        "[document.getElementById('wd-prev-msg').textContent, document.getElementById('wd-prev-status').textContent, !document.getElementById('wd-prev-act').disabled, wd.paying, document.querySelector('#wd-grid .wd-card:nth-child(2) .wd-status').textContent]"),
        ["Предмет добавлен в гардероб", "Есть", True, False, "Есть"])
    await p.ev("E.sleep(1100)")
    await p.tap("#wd-prev-act")
    await p.wait("document.documentElement.getAttribute('data-skin-table') === 'table_blue'", 10, "куплено и надето")
    await p.tap("#wd-prev-close")
    # paid, но предмет не появился за 20 с
    await open_item(p, 6, 1 + 1)           # слот «Краш»: вторая карточка (crash_neon) уже без реального платежа
    await p.tap("#wd-prev-close")
    await open_item(p, 5, 2)               # слот «Шарики кено»: keno_hex
    await p.ev("window.__invoiceStatus = 'paid'")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Оплата обрабатывается…'", 10, "обработка")
    polls = await p.ev("E.count('/api/cosmetics/mine')")
    await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Платёж получен')", 30, "сообщение через 20 с")
    check("через 20 с: сообщение про /paysupport, кнопка разблокирована", await p.ev("[document.getElementById('wd-prev-msg').textContent, !document.getElementById('wd-prev-act').disabled, wd.paying]"),
          ["Платёж получен, предмет скоро появится. Если нет, напишите в /paysupport в боте", True, False])
    after = await p.ev("E.count('/api/cosmetics/mine')")
    await p.ev("E.sleep(3500)")
    check("опрос остановлен", await p.ev("E.count('/api/cosmetics/mine')"), after)
    check("опрос шёл примерно раз в 1,5 с", 8 <= after - polls <= 16, True)
    await p.tap("#wd-prev-close")
    # статусы через подмену ответа invoice (ссылка всегда t.me): cancelled, failed, pending
    await open_item(p, 6, 2)
    await p.ev("window.__invMode = 'ok'; window.__invoiceStatus = 'cancelled'; window.__invoices = []")
    await p.tap("#wd-prev-act")
    await p.wait("window.__invoices.length === 1", 10, "openInvoice")
    await p.ev("E.sleep(300)")
    check("cancelled: без ошибки, лист остался, кнопка доступна", await p.ev("[document.getElementById('wd-prev-msg').textContent, !document.getElementById('wd-prev-sheet').hidden, !document.getElementById('wd-prev-act').disabled, wd.paying]"), ["", True, True, False])
    await p.ev("window.__invoiceStatus = 'failed'")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Оплата не прошла'", 10, "failed")
    check("failed: кнопка доступна", await p.ev("!document.getElementById('wd-prev-act').disabled"), True)
    await p.ev("window.__invoiceStatus = 'pending'")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Оплата обрабатывается…'", 10, "pending как «обрабатывается»")
    # закрытие листа во время опроса: опрос останавливается, ничего не ломается
    mine = await p.ev("E.count('/api/cosmetics/mine')")
    await p.tap("#wd-prev-close")
    await p.ev("E.sleep(3500)")
    check("закрытие во время ожидания: опрос остановлен, экран цел", await p.ev("[E.count('/api/cosmetics/mine') - %d, wd.paying, document.getElementById('wd-prev-sheet').hidden, document.querySelector('[data-screen=style]').hidden]" % mine), [await p.ev("E.count('/api/cosmetics/mine') - %d" % mine), False, True, False])
    # нет openInvoice (вне Telegram)
    await open_item(p, 6, 2)
    await p.ev("window.Telegram.WebApp.__oi = window.Telegram.WebApp.openInvoice; delete window.Telegram.WebApp.openInvoice; window.__invoices = []")
    posts = await p.ev("E.count('/api/cosmetics/invoice')")
    await p.tap("#wd-prev-act")
    check("нет openInvoice: сообщение, запрос не отправлен", await p.ev("[document.getElementById('wd-prev-msg').textContent, E.count('/api/cosmetics/invoice')]"),
          ["Оплата Stars работает только внутри Telegram. Откройте игру через бота", posts])
    await p.ev("window.Telegram.WebApp.openInvoice = window.Telegram.WebApp.__oi")
    # чужие ссылки отвергаются
    for bad in ("https://evil.example/pay", "http://t.me/x", "https://t.me.evil.example/x", "https://t.me@evil.example/x", "javascript:alert(1)", "https://t.me:8443/x"):
        await p.ev("window.__invMode = %r; window.__invoices = []" % bad)
        await p.tap("#wd-prev-act")
        await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Некорректная ссылка')", 10, "отказ для " + bad)
        check("ссылка %s отвергнута, openInvoice не вызван" % bad, await p.ev("window.__invoices.length"), 0)
    # ошибки инвойса
    await p.ev("window.__invMode = '429'")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Слишком часто')", 20, "429")
    await p.ev("window.__invMode = '503'")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Покупки сейчас недоступны')", 30, "503")
    await p.ev("window.__invMode = ''")
    await p.tap("#wd-prev-close")
    await open_item(p, 2, 3)                  # слот «Фишки»: chip_gold недоступен; для 502 берём back_midnight
    await p.tap("#wd-prev-close")
    await open_item(p, 1, 2)
    w.server.script(invoice=["ERR", "ERR", "ERR"])
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Не удалось создать счёт')", 30, "502")
    check("502: сообщение, кнопка доступна, ссылка не открывалась", await p.ev("[document.getElementById('wd-prev-msg').textContent, !document.getElementById('wd-prev-act').disabled, window.__invoices.length]"),
          ["Не удалось создать счёт. Попробуйте позже", True, 0])
    # остальные ответы сервера (прямой вызов): уже есть, за фишки, недоступен
    async def direct(item):
        await p.ev("wdBuyStars(%s)" % item)
        await p.wait("!wd.busy", 20, "запрос завершён")
        return await msg(p)
    check("уже есть", await direct("{ code: 'table_blue', slot: 'table', price: { currency: 'stars', amount: 150 } }"), "Этот предмет уже у вас")
    check("не за Stars", await direct("{ code: 'chip_ring', slot: 'chip', price: { currency: 'stars', amount: 5 } }"), "Этот предмет продаётся за фишки")
    check("недоступен", await direct("{ code: 'back_ember', slot: 'card_back', price: { currency: 'stars', amount: 5 } }"), "Этот предмет пока недоступен")
    check("цен нет в коде клиента", await p.ev("!/150|\\b75\\b/.test(String(wdPriceText)) && typeof WD_PRICES === 'undefined'"), True)
