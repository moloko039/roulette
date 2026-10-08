"""Покупка за фишки в гардеробе: цена на карточке (из каталога), двухшаговое подтверждение, один запрос при двойном тапе, повтор с тем же request_id
при сбое сети, обновление баланса, «Надеть» после покупки, ошибки (нехватка с разницей, уже есть, недоступно, не за фишки, конфликт, 429, 503, сеть)."""
import time

from harness import check

NAME = "wardrobe_buy_chips"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 2      # минутная граница начисления далеко: лишний /api/me по таймеру не вклинивается в сетевой эталон
ALLOW_CONSOLE = (r"status of 409",)
NBSP = "(s) => s.replace(/\\s/g, ' ')"
NOTE = "Предметы не влияют на игру."

OVERRIDE = """
(() => {
  const orig = window.__origFetch || (window.__origFetch = window.fetch);
  window.__bodies = [];
  window.fetch = async (u, o) => {
    const isBuy = o && o.method === 'POST' && String(u).includes('/api/cosmetics/buy');
    if (isBuy) window.__bodies.push(JSON.parse(o.body));
    const mode = window.__buyMode;
    if (isBuy && mode === 'failonce') { window.__buyMode = ''; throw new TypeError('Failed to fetch'); }
    if (isBuy && mode === 'net') throw new TypeError('Failed to fetch');
    if (isBuy && mode === '429') return new Response('{"error":"too_many_requests"}', { status: 429, headers: { 'Retry-After': '1', 'Content-Type': 'application/json' } });
    if (isBuy && mode === '503') return new Response('{"detail":"busy"}', { status: 503, headers: { 'Content-Type': 'application/json' } });
    if (isBuy && mode === 'conflict') return new Response('{"detail":"request_conflict"}', { status: 409, headers: { 'Content-Type': 'application/json' } });
    if (isBuy && mode === 'slow') await new Promise((r) => setTimeout(r, 800));
    return orig(u, o);
  };
  return true;
})()
"""


async def msg(p):
    return await p.ev("document.getElementById('wd-prev-msg').textContent")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")      # гардероб на странице «Оформление»
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.ev(OVERRIDE)
    n = NBSP
    cards = "[...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.querySelector('.wd-name').textContent, (%s)(c.querySelector('.wd-status').textContent), c.getBoundingClientRect().height])" % n
    await p.tap("#wd-tabs .wd-tab:nth-child(8)")
    got = await p.ev(cards)
    check("слот «Значок»: цена из каталога вместо «Не получено», «Скоро» без цены", [[c[0], c[1]] for c in got], [["Без значка", "Надето"], ["Пика", "20 000 фишек"], ["Пламя", "Скоро"], ["Черновик", "100 кристаллов"], ["Пустота", "150 кристаллов"]])
    check("размеры карточек не зависят от статуса", len({c[2] for c in got}), 1)
    check("в клиенте нет цен: подписи берутся из ответа каталога", await p.ev("typeof WD_PRICES === 'undefined' && !/20000|40000|60000|100000/.test(String(wdNormalizeCatalog) + String(wdPriceText))"), True)
    # подтверждение
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("кнопка «Купить за …» и строка про игру", await p.ev("[(%s)(document.getElementById('wd-prev-act').textContent), document.getElementById('wd-prev-note').hidden, document.getElementById('wd-prev-note').textContent]" % n),
          ["Купить за 20 000 фишек", False, NOTE])
    base = await p.ev("E.count('/api/cosmetics/buy')")
    await p.tap("#wd-prev-act")
    check("подтверждение: текст и кнопки", await p.ev("[(%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-prev-act').textContent, document.getElementById('wd-prev-close').textContent]" % n),
          ["Потратить 20 000 фишек? Вернуть предмет нельзя", "Потратить", "Отмена"])
    await p.tap("#wd-prev-close")
    check("«Отмена» возвращает к покупке без запроса, лист остался", await p.ev("[document.getElementById('wd-prev-act').textContent.startsWith('Купить'), document.getElementById('wd-prev-sheet').hidden, E.count('/api/cosmetics/buy')]"), [True, False, base])
    # двойной тап = один запрос
    await p.ev("window.__buyMode = 'slow'")
    await p.tap("#wd-prev-act")
    await p.ev("(() => { const b = document.getElementById('wd-prev-act'); b.click(); b.click(); })()")
    check("во время запроса кнопка заблокирована", await p.ev("document.getElementById('wd-prev-act').disabled"), True)
    await p.wait("document.getElementById('wd-prev-act').textContent === 'Надеть'", 10, "куплено")
    await p.ev("window.__buyMode = ''")
    check("двойной тап: один запрос с request_id", await p.ev("[E.count('/api/cosmetics/buy') - %d, window.__bodies.length, typeof window.__bodies[0].request_id]" % base), [1, 1, "string"])
    check("предмет «Есть» → после покупки «Надеть», сообщение", await p.ev("[document.getElementById('wd-prev-status').textContent, (%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-prev-note').hidden]" % n),
          ["Есть", "Предмет куплен: 20 000 фишек", True])
    check("в базе: баланс списан, источник chips", [w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)), w.sql_value("SELECT source FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'badge_spade'", (uid,))], [80000, "chips"])
    check("баланс приложения обновлён из ответа", await p.ev("srv.balance"), 80000)
    check("карточка в сетке обновилась (без перезагрузки)", await p.ev("(%s)(document.querySelector('#wd-grid .wd-card:nth-child(2) .wd-status').textContent)" % n), "Есть")
    # надеть купленное
    await p.ev("E.sleep(1100)")
    await p.tap("#wd-prev-act")
    await p.wait("!document.getElementById('profile-badge').hidden", 10, "значок надет")
    check("после покупки предмет надевается", await p.ev("document.getElementById('wd-prev-act').textContent"), "Снять")
    await p.tap("#wd-prev-close")
    # повтор при сбое сети с тем же request_id: фишки списаны один раз
    await p.tap("#wd-tabs .wd-tab:nth-child(2)")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.ev("window.__bodies = []; window.__buyMode = 'failonce'")
    await p.tap("#wd-prev-act")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-act').textContent === 'Надеть'", 20, "куплено после повтора")
    check("повтор после сбоя: два запроса с одним request_id, одно списание", await p.ev("[window.__bodies.length, window.__bodies[0].request_id === window.__bodies[1].request_id]"), [2, True])
    check("баланс 80000 - 40000", [w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)), await p.ev("srv.balance")], [40000, 40000])
    await p.tap("#wd-prev-close")
    # нехватка с разницей
    await p.tap("#wd-tabs .wd-tab:nth-child(4)")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.tap("#wd-prev-act")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.startsWith('Не хватает')", 10, "нехватка")
    check("нехватка: сколько не хватает", (await msg(p)).replace(" ", " "), "Не хватает 20 000 фишек")
    check("кнопка снова доступна, баланс прежний", await p.ev("[!document.getElementById('wd-prev-act').disabled, srv.balance]"), [True, 40000])
    await p.tap("#wd-prev-close")
    # уже есть (клиент не знает): предмет выдан в базе
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, 'frame_thin', 'owner_gift', ?)", (uid, int(time.time())))
    await p.tap("#wd-tabs .wd-tab:nth-child(7)")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.tap("#wd-prev-act")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.length > 0 && !document.getElementById('wd-prev-msg').textContent.startsWith('Потратить')", 10, "ответ")
    check("уже есть", await msg(p), "Этот предмет уже у вас")
    # остальные ошибки (прямой вызов запросов, текст в листе)
    async def direct(item, mode=None):
        await p.ev("window.__buyMode = %r" % (mode or ""))
        await p.ev("wdBuyDirect(%s)" % item)
        await p.wait("!wd.busy", 40, "запрос завершён")
        await p.ev("window.__buyMode = ''")
        return await msg(p)
    check("предмет за кристаллы без кристаллов: сервер берёт кристаллы, подсказка", await direct("{ code: 'table_blue', slot: 'table', price: { currency: 'chips', amount: 5 } }"), "Не хватает 5 кристаллов. Их можно купить на странице «Кристаллы»")
    check("недоступен", await direct("{ code: 'back_ember', slot: 'card_back', price: { currency: 'chips', amount: 5 } }"), "Этот предмет пока недоступен")
    item = "{ code: 'mine_star', slot: 'mine_icons', price: { currency: 'chips', amount: 60000 } }"
    check("конфликт запроса", await direct(item, "conflict"), "Запрос уже обработан, обновите экран")
    check("429", await direct(item, "429"), "Слишком часто: подождите секунду и повторите")
    check("503 после повторов", await direct(item, "503"), "Покупки сейчас недоступны. Попробуйте позже")
    check("нет сети после повторов", await direct(item, "net"), "Нет связи с сервером. Попробуйте ещё раз")
    check("экран цел, баланс прежний", await p.ev("[document.querySelector('[data-screen=shop]').hidden, srv.balance, wd.busy]"), [False, 40000, False])
