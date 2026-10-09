"""Гардероб (раздел «Оформление» магазина): вход из нижней панели, слоты, карточки и статусы, предпросмотр (скин только на самом превью), надеть и снять, защита от повторного
нажатия, ошибки (не получено, недоступно, 429, сеть, 503), переключатель показа в рейтинге, уход со вкладки и возврат, закрытие предпросмотра по тапу вне."""
import time

from harness import check

NAME = "wardrobe_flow"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): сценарий идёт около 40 с, лишний /api/me по таймеру не вклинивается
ALLOW_CONSOLE = (r"status of 409",)    # отказы сервера (not_owned, item_unavailable) здесь ожидаемы: проверяются тексты ошибок
LABELS = ["Рубашка карт", "Фишки", "Стол", "Иконки мин", "Шарики кено", "Краш", "Рамка аватара", "Значок"]
ROOT = "document.documentElement"

FETCH_OVERRIDE = """
(() => {
  const orig = window.__origFetch || (window.__origFetch = window.fetch);
  window.fetch = async (u, o) => {
    const mode = window.__wdMode;
    const isPost = o && o.method === 'POST' && String(u).includes('/api/cosmetics/');
    if (isPost && mode === 'slow') { await new Promise((r) => setTimeout(r, 900)); }
    if (isPost && mode === '429') return new Response('{"error":"too_many_requests"}', { status: 429, headers: { 'Retry-After': '1', 'Content-Type': 'application/json' } });
    if (isPost && mode === '503') return new Response('{"detail":"busy"}', { status: 503, headers: { 'Content-Type': 'application/json' } });
    if (isPost && mode === 'net') throw new TypeError('Failed to fetch');
    return orig(u, o);
  };
  return true;
})()
"""


async def toast_text(p):
    return await p.ev("[document.getElementById('wd-toast').textContent, document.getElementById('wd-prev-toast').textContent].join('|')")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    for code in ("back_midnight", "chip_ring", "back_ember"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))
    await w.reload()
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")      # гардероб на странице «Оформление»
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб открыт")
    check("восемь слотов с русскими названиями", await p.ev("[...document.querySelectorAll('#wd-tabs .wd-tab')].map(b => b.textContent)"), LABELS)
    cards = "[...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.querySelector('.wd-name').textContent, c.querySelector('.wd-status').textContent, c.classList.contains('dim')])"
    check("слот «Рубашка карт»: надето, есть, скоро", await p.ev(cards), [["Классика", "Надето", False], ["Полночь", "Есть", False], ["Уголь", "Скоро", True], ["Листопад", "Коллекция", True], ["Ковёр", "Коллекция", True], ["Патина", "400 кристаллов", True], ["Черновик", "100 кристаллов", True]])
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")
    check("слот «Стол»: стартовый надет, у недостающего цена из каталога, у «Скоро» цены нет", await p.ev(
        "[...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.querySelector('.wd-name').textContent, c.querySelector('.wd-status').textContent.replace(/\\s/g, ' '), c.classList.contains('dim')])"),
        [["Сукно", "Надето", False], ["Лагуна", "150 кристаллов", True], ["Сумерки", "Скоро", True], ["Октябрь", "Коллекция", True], ["Черновик", "100 кристаллов", True], ["Пустота", "150 кристаллов", True], ["Клеёнка", "Коллекция", True], ["Дно", "150 кристаллов", True]])
    check("в сетке карточек нет кнопок покупки (покупка только в листе предпросмотра)", await p.ev("/Купить/.test(document.getElementById('wd-grid').textContent)"), False)
    check("мини-превью несут скин на самом элементе", await p.ev("[...document.querySelectorAll('#wd-grid .wd-mini')].map(m => m.getAttribute('data-skin-table'))"), ["table_green", "table_blue", "table_violet", "table_autumn", "draft_table", "void_table", "table_oilcloth", "table_deep"])
    # предпросмотр: не получено / скоро — без кнопки «Надеть»
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("не получено, но есть цена: кнопка «Купить за …» (покупка проверяется в wardrobe_buy_*)", await p.ev("[document.getElementById('wd-prev-act').hidden, document.getElementById('wd-prev-act').textContent, document.getElementById('wd-prev-status').textContent.replace(/\\s/g, ' ')]"),
          [False, "Купить за 150 кристаллов", "150 кристаллов"])
    await p.tap("#wd-prev-close")
    await p.wait("document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр закрыт")
    await p.tap("#wd-grid .wd-card:nth-child(3)")
    check("скоро: пояснение", await p.ev("[document.getElementById('wd-prev-act').hidden, document.getElementById('wd-prev-msg').textContent]"), [True, "Этот предмет появится позже"])
    await p.tap("#wd-prev-close")
    # предпросмотр доступного предмета: скин на самом превью, корень не меняется
    await p.tap("#wd-tabs .wd-tab:nth-child(1)")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("предпросмотр: образец с кодом на самом элементе, корень прежний", await p.ev(
        "[document.getElementById('wd-prev-scene').getAttribute('data-skin-card_back'), %s.getAttribute('data-skin-card_back'), document.getElementById('wd-prev-act').textContent, document.getElementById('wd-prev-title').textContent]" % ROOT),
        ["back_midnight", "back_classic", "Надеть", "Полночь"])
    check("рубашка в образце окрашена скином, а вне образца нет", await p.ev(
        "(() => { const a = getComputedStyle(document.querySelector('#wd-prev-scene .bj-card.back')).borderTopColor; const probe = document.createElement('div'); probe.className = 'bj-card back'; document.body.appendChild(probe);"
        " const b = getComputedStyle(probe).borderTopColor; probe.remove(); return a !== b; })()"), True)
    # надеть
    posts = await p.ev("E.count('/api/cosmetics/equip')")
    await p.tap("#wd-prev-act")
    await p.wait("%s.getAttribute('data-skin-card_back') === 'back_midnight'" % ROOT, 10, "надето")
    check("после ответа сервера: статус и кнопка «Снять»", await p.ev("[document.getElementById('wd-prev-status').textContent, document.getElementById('wd-prev-act').textContent]"), ["Надето", "Снять"])
    check("ушёл один запрос с request_id", await p.ev("E.count('/api/cosmetics/equip')"), posts + 1)
    check("при надетом скине образцы показывают каждый свой вид (стартовый не наследует надетое)", await p.ev(
        "(() => { const c = [...document.querySelectorAll('#wd-grid .wd-card .bj-card.back')].map(e => getComputedStyle(e).borderTopColor); return [c.length >= 2, c[0] !== c[1]]; })()"), [True, True])
    await p.ev("E.sleep(1100)")      # не чаще одной смены в секунду на игрока
    await p.tap("#wd-prev-act")
    await p.wait("%s.getAttribute('data-skin-card_back') === 'back_classic'" % ROOT, 10, "снято")
    check("после снятия: «Надеть»", await p.ev("document.getElementById('wd-prev-act').textContent"), "Надеть")
    # защита от повторного нажатия: запрос задержан, второе нажатие игнорируется
    await p.ev("E.sleep(1100)")
    await p.ev(FETCH_OVERRIDE)
    await p.ev("window.__wdMode = 'slow'")
    base = await p.ev("E.count('/api/cosmetics/equip')")
    await p.tap("#wd-prev-act")
    check("во время запроса кнопка недоступна", await p.ev("document.getElementById('wd-prev-act').disabled"), True)
    await p.ev("wdEquip(wd.preview)")      # прямой повторный вызов
    await p.ev("document.getElementById('wd-prev-act').click()")
    await p.wait("%s.getAttribute('data-skin-card_back') === 'back_midnight'" % ROOT, 10, "надето после задержки")
    check("повторные нажатия не отправили лишних запросов", await p.ev("E.count('/api/cosmetics/equip')"), base + 1)
    await p.ev("window.__wdMode = ''")
    # ошибки сервера: не получено, недоступно
    await p.ev("E.sleep(1100)")
    await p.ev("wdEquip({ slot: 'table', code: 'table_blue' })")
    await p.wait("(document.getElementById('wd-prev-toast').textContent + document.getElementById('wd-toast').textContent).length > 0", 10, "тост об ошибке")
    check("не получено", (await toast_text(p)).strip("|"), "Этого предмета у вас нет")
    await p.ev("E.sleep(2500)")
    await p.ev("E.sleep(1100); wdEquip({ slot: 'card_back', code: 'back_ember' })")
    await p.wait("(document.getElementById('wd-prev-toast').textContent + document.getElementById('wd-toast').textContent).includes('недоступен')", 10, "тост недоступности")
    check("недоступно", (await toast_text(p)).strip("|"), "Этот предмет пока недоступен")
    check("экран цел: слот и скин прежние", await p.ev("[wd.busy, %s.getAttribute('data-skin-card_back')]" % ROOT), [False, "back_midnight"])
    # 429, сеть, 503 (ответы подменены в странице)
    for mode, text in (("429", "Слишком часто: подождите секунду и повторите"), ("503", "Нет связи с сервером. Попробуйте ещё раз"), ("net", "Нет связи с сервером. Попробуйте ещё раз")):
        await p.ev("E.sleep(2500)")
        await p.ev("window.__wdMode = %r; document.getElementById('wd-toast').textContent = ''; document.getElementById('wd-prev-toast').textContent = ''" % mode)
        await p.ev("wdUnequip({ slot: 'card_back' })")
        await p.wait("!wd.busy", 30, "запрос завершён (%s)" % mode)
        check("тост при ответе %s" % mode, (await toast_text(p)).strip("|"), text)
        check("экран не сломан после %s" % mode, await p.ev("[document.querySelector('[data-screen=shop]').hidden, document.querySelectorAll('#wd-grid .wd-card').length > 0, %s.getAttribute('data-skin-card_back')]" % ROOT),
              [False, True, "back_midnight"])
    await p.ev("window.__wdMode = ''")
    # переключатель показа в рейтинге
    await p.tap("#wd-prev-close")
    check("показ включён по умолчанию", await p.ev("document.getElementById('wd-vis').getAttribute('aria-checked')"), "true")
    await p.ev("E.sleep(1100)")
    await p.tap("#wd-vis")
    await p.wait("document.getElementById('wd-vis').getAttribute('aria-checked') === 'false'", 10, "показ выключен")
    check("в базе показ выключен", w.sql_value("SELECT show_in_rating FROM cosmetic_prefs WHERE telegram_id = ?", (uid,)), 0)
    await p.ev("E.sleep(1100)")
    await p.tap("#wd-vis")
    await p.wait("document.getElementById('wd-vis').getAttribute('aria-checked') === 'true'", 10, "показ включён")
    # уход со вкладки и возврат, тап вне листа предпросмотра
    await p.tap(".tab[data-tab=profile]")
    await p.wait("document.querySelector('[data-screen=shop]').hidden && !document.querySelector('[data-screen=profile]').hidden", 5, "раздел «Оформление» магазина закрыта уходом на профиль")
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")      # гардероб на странице «Оформление»
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 5, "вкладка открыта снова")
    await p.ev("E.sleep(500)")
    await p.tap("#wd-grid .wd-card:nth-child(1)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.ev("E.sleep(500)")
    await p.tap({"x": 195, "y": 30})
    await p.wait("document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр закрыт тапом вне")
    check("раздел «Оформление» магазина осталась открытой (тап не прошёл сквозь)", await p.ev("document.querySelector('[data-screen=shop]').hidden"), False)
    await p.tap("#wd-grid .wd-card:nth-child(1)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр снова")
    await p.ev("showTab('profile')")      # уход со вкладки программно: лист поверх навигации нажатием не обойти
    check("уход со вкладки с открытым предпросмотром закрывает его и сбрасывает состояние", await p.ev("[document.getElementById('wd-prev-sheet').hidden, wd.preview, wd.open, wd.confirm]"), [True, None, False, False])
