"""Магазин: вкладка вторая в нижней панели и называется «Магазин»; три страницы (Кристаллы, Фишки, Оформление) переключаются; страница кристаллов показывает баланс и
пакеты с ценами из ответа сервера (бонус виден), покупка пакета: счёт, openInvoice (подмена Telegram в харнессе: paid, cancelled, failed), опрос баланса и сообщение
о начислении; страница фишек прямо говорит, что фишки можно только купить, продать нельзя."""
import time

from harness import check

NAME = "shop_gems"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = ()

PACK = "#gem-packs .gem-pack[data-code=gems_250]"           # отмена
PACK_FAIL = "#gem-packs .gem-pack[data-code=gems_50]"        # отказ оплаты
PACK_PAID = "#gem-packs .gem-pack[data-code=gems_1000]"      # оплата (у сервера лимит: один счёт на пакет раз в 10 секунд, поэтому у каждого сценария свой пакет)


async def run(w):
    p = w.page
    uid = w.users["me"].id
    check("порядок вкладок: Магазин, Рейтинг, Играть, Ферма, Профиль", await p.ev("[...document.querySelectorAll('#nav .tab')].map(t => t.textContent.trim())"),
          ["Магазин", "Рейтинг", "Играть", "Ферма", "Профиль"])
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#gem-packs .gem-pack').length === 3", 10, "пакеты кристаллов")
    check("заголовок и открытая страница по умолчанию: кристаллы", await p.ev(
        "[document.querySelector('[data-screen=shop] h2').textContent.trim(), document.getElementById('shop-page-gems').hidden, document.getElementById('shop-page-chips').hidden, document.getElementById('shop-page-look').hidden]"),
        ["Магазин", False, True, True])
    check("баланс кристаллов 0", await p.ev("document.getElementById('shop-gems').textContent"), "0")
    rows = await p.ev("[...document.querySelectorAll('#gem-packs .gem-pack')].map(b => [b.querySelector('strong').textContent.replace(/\\s/g, ' '), "
                      "(b.querySelector('small') || {textContent: ''}).textContent.replace(/\\s/g, ' '), b.querySelector('.gem-pack-price').textContent.replace(/\\s/g, ' ')])")
    check("пакеты: кристаллы, бонус и цена в Stars из ответа сервера", rows, [["50 кристаллов", "", "50 Stars"], ["275 кристаллов", "+25 в подарок", "250 Stars"], ["1 200 кристаллов", "+200 в подарок", "1 000 Stars"]])
    check("в данных страницы нет цен в фишках и обещаний выигрыша", await p.ev("/выигр|удач|деньги/i.test(document.getElementById('shop-page-gems').textContent)"), False)
    check("в /api/me приходит gems, при открытии запросы пакетов и гардероба по разу", await p.ev("[E.count('/api/gems/packs'), E.count('/api/cosmetics/catalog')]"), [1, 1])

    # страницы
    await p.tap("#shop-pages [data-page=chips]")
    check("фишки: правило покупки и продажи сказано прямо", await p.ev(
        "[document.getElementById('shop-page-chips').hidden, document.getElementById('shop-page-chips').textContent.replace(/\\s+/g, ' ').includes('Фишки можно только купить. Продать их или обменять на реальные деньги и Stars нельзя.')]"), [False, True])
    await p.tap("#shop-pages [data-page=look]")
    check("оформление: гардероб на этой странице", await p.ev("[document.getElementById('shop-page-look').hidden, document.querySelectorAll('#wd-grid .wd-card').length > 0]"), [False, True])
    check("выбранная страница отмечена aria-selected", await p.ev("[...document.querySelectorAll('#shop-pages [data-page]')].map(b => b.getAttribute('aria-selected'))"), ["false", "false", "true"])
    await p.tap("#shop-pages [data-page=gems]")

    # отмена и отказ оплаты: ничего не начислено
    await p.ev("window.__invoiceStatus = 'cancelled'; window.__invoices = []")
    await p.tap(PACK)
    await p.wait("window.__invoices.length === 1", 10, "счёт открыт")
    await p.wait("!document.getElementById('gem-packs').querySelector('.gem-pack').disabled && document.getElementById('gem-msg').textContent === ''", 5, "отмена: сообщение пусто")
    check("отмена: счёт был один, баланс прежний", await p.ev("[window.__invoices.length, document.getElementById('shop-gems').textContent]"), [1, "0"])
    await p.ev("window.__invoiceStatus = 'failed'")
    await p.tap(PACK_FAIL)
    await p.wait("document.getElementById('gem-msg').textContent === 'Оплата не прошла'", 10, "оплата не прошла")
    await p.ev("window.__invoiceStatus = 'paid'")
    # оплата: бот начисляет кристаллы позже, клиент опрашивает баланс
    await p.wait("!document.querySelector('#gem-packs .gem-pack').disabled", 5, "кнопки снова доступны")
    await p.ev("window.__invoices = []")
    await p.tap(PACK_PAID)
    await p.wait("window.__invoices.length === 1 && document.getElementById('gem-msg').textContent.includes('Оплата обрабатывается')", 15, "оплата принята, ждём начисления")
    check("пока идёт оплата кнопки пакетов заблокированы", await p.ev("[...document.querySelectorAll('#gem-packs .gem-pack')].every(b => b.disabled)"), True)
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 1200, 'purchase', 'e2e-charge-1', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 1200) ON CONFLICT(telegram_id) DO UPDATE SET gems = 1200", (uid,))
    await p.wait("document.getElementById('gem-msg').textContent.includes('Кристаллы начислены')", 20, "начисление")
    check("после начисления: сообщение, баланс, кнопки доступны", await p.ev(
        "[document.getElementById('gem-msg').textContent.replace(/\\s/g, ' '), document.getElementById('shop-gems').textContent.replace(/\\s/g, ' '), [...document.querySelectorAll('#gem-packs .gem-pack')].some(b => b.disabled)]"),
        ["Кристаллы начислены: +1 200", "1 200", False])
    # баланс кристаллов виден и в /api/me
    check("баланс кристаллов в /api/me (запросом из страницы)", await p.ev("fetch(API_URL + '/api/me', {headers: {Authorization: 'tma ' + tg.initData}}).then(r => r.json()).then(d => d.gems)"), 1200)
