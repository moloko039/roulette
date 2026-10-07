"""Магазин, страница «Фишки» (настоящий сервер): три пакета в часах фермы с числом фишек и ценой в кристаллах из ответа сервера, правило «только купить, продать нельзя» на странице,
подтверждение вторым нажатием (без запроса до него, сбрасывается через 5 секунд), один запрос на покупку, зачисление фишек и списание кристаллов в базе, баланс на экране и в магазине,
нехватка кристаллов: понятный текст, ничего не списано."""
import time

from harness import check

NAME = "shop_chips"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = (r"status of 409",)

SP = "(s) => s.replace(/\\s/g, ' ')"
PACK = "#chip-packs .gem-pack[data-code=%s]"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 130, 'owner_grant', 'e2e-grant-chips', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 130)", (uid,))
    chips_before = w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,))
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#chip-packs .gem-pack').length === 3", 10, "пакеты фишек")
    await p.tap("#shop-pages [data-page=chips]")
    rows = await p.ev("[...document.querySelectorAll('#chip-packs .gem-pack')].map(b => [(%s)(b.querySelector('strong').textContent), (%s)(b.querySelector('small').textContent), (%s)(b.querySelector('.gem-pack-price').textContent)])" % (SP, SP, SP))
    check("пакеты: часы фермы, число фишек для этого игрока, цена в кристаллах", rows,
          [["6 часов фермы", "600 фишек", "30 кристаллов"], ["24 часа фермы", "2 400 фишек", "100 кристаллов"], ["72 часа фермы", "7 200 фишек", "250 кристаллов"]])
    check("правило на странице сказано прямо", await p.ev("(%s)(document.getElementById('chip-note').textContent)" % SP), "Фишки можно только купить. Продать их или обменять на реальные деньги и Stars нельзя. Условия покупки")
    check("на странице нет обещаний выигрыша", await p.ev("/выигр|удач/i.test(document.getElementById('shop-page-chips').textContent)"), False)
    base = await p.ev("E.count('/api/chips/buy')")
    # подтверждение вторым нажатием
    await p.tap(PACK % "chips_24h")
    check("первое нажатие: просьба подтвердить, запроса нет", await p.ev("[(%s)(document.getElementById('chip-msg').textContent), (%s)(document.querySelector('#chip-packs [data-code=chips_24h] .gem-pack-price').textContent), E.count('/api/chips/buy')]" % (SP, SP)),
          ["Потратить 100 кристаллов на 2 400 фишек? Вернуть фишки нельзя", "Нажмите ещё раз", base])
    await p.tap(PACK % "chips_24h")
    await p.wait("document.getElementById('chip-msg').textContent.includes('Куплено')", 10, "куплено")
    check("один запрос, сообщение", await p.ev("[E.count('/api/chips/buy') - %d, (%s)(document.getElementById('chip-msg').textContent)]" % (base, SP)), [1, "Куплено: 2 400 фишек за 100 кристаллов"])
    check("в базе: фишки +2400, кристаллов 30, запись и строка журнала", [
        w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)) - chips_before,
        w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT COUNT(*) FROM chip_purchases WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT delta || ':' || reason FROM gems_ledger WHERE telegram_id = ? AND delta < 0", (uid,))], [2400, 30, 1, "-100:chip_purchase"])
    check("баланс кристаллов в магазине и фишек в приложении обновились", await p.ev("[document.getElementById('shop-gems').textContent.replace(/\\s/g, ''), srv.balance - %d]" % chips_before), ["30", 2400])
    # нехватка
    await p.tap(PACK % "chips_72h")
    await p.tap(PACK % "chips_72h")
    await p.wait("document.getElementById('chip-msg').textContent.includes('Не хватает')", 10, "нехватка")
    check("нехватка: текст с подсказкой, ничего не списано", await p.ev("[(%s)(document.getElementById('chip-msg').textContent), E.count('/api/chips/buy')]" % SP) + [w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,))],
          ["Не хватает кристаллов. Их можно купить на странице «Кристаллы»", base + 2, 30])
    # подтверждение сбрасывается само
    await p.tap(PACK % "chips_6h")
    await p.wait("document.querySelector('#chip-packs [data-code=chips_6h] .gem-pack-price').textContent.includes('Нажмите')", 5, "просьба подтвердить")
    await p.wait("!document.querySelector('#chip-packs [data-code=chips_6h] .gem-pack-price').textContent.includes('Нажмите')", 10, "подтверждение сбросилось")
    check("после сброса запроса не было", await p.ev("E.count('/api/chips/buy')"), base + 2)
    # последняя покупка на оставшиеся кристаллы
    await p.tap(PACK % "chips_6h")
    await p.tap(PACK % "chips_6h")
    await p.wait("document.getElementById('shop-gems').textContent.replace(/\\s/g, '') === '0'", 10, "кристаллы потрачены")
    check("итог: кристаллов 0, фишки +3000", [w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)), w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)) - chips_before], [0, 3000])
