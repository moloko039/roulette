"""Коллекция «Листопад» (настоящий сервер): на третий день серии входов награда дня даёт первую часть, сообщение называет коллекцию; в «Оформлении» видна полоса коллекции «1 из 3» с подписью, как получить;
часть в слоте без подписи (не «Не получено» и не «Коллекция»), её нельзя купить; надев все три части, при полном наборе у корня появляется атрибут эффекта."""
import time

from harness import check

NAME = "collection_leaves"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5
SP = "(s) => s.replace(/\\s/g, ' ')"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    today = (int(time.time()) + 3 * 3600) // 86400
    now = int(time.time())
    # серия уже идёт два дня: сегодняшний сбор это день 3 цикла 1
    for d, sd in ((today - 2, 1), (today - 1, 2)):
        w.sql("INSERT INTO streak_claims (telegram_id, day, streak_day, cycle, chips, gems, created_at) VALUES (?, ?, ?, 1, 300, 0, ?)", (uid, d, sd, now - 86400 * (today - d)))
    await w.reload()
    await p.wait("!document.getElementById('streak-card').hidden", 10, "карточка награды дня")
    check("карточка обещает день 3", await p.ev("(%s)(document.getElementById('streak-sub').textContent)" % SP), "День 3 из 7: +500 фишек")
    await p.tap("#streak-card")
    await p.wait("document.getElementById('streak-msg').textContent.includes('Награда получена')", 10, "сбор")
    check("сообщение называет часть коллекции", await p.ev("(%s)(document.getElementById('streak-msg').textContent)" % SP), "Награда получена: +500 фишек. Часть коллекции «Листопад»: Листопад")
    check("в базе: рубашка выдана с источником collection", w.sql_value("SELECT source FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'back_leaves'", (uid,)), "collection")
    # оформление
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "магазин")
    check("в магазине нет плашек #wd-collections и .wd-collection", await p.ev("[!!document.getElementById('wd-collections'), document.querySelectorAll('.wd-collection').length]"), [False, 0])
    check("сервер отдаёт у Листопада status, days_left и источник (значения зависят от даты, проверяем только тип)", await p.ev("(() => { const c = wd.mine.collections.find(c => c.code === 'leaves'); return [typeof c.status, c.days_left === null || Number.isInteger(c.days_left), c.source]; })()"), ["string", True, "streak"])
    check("строка сезона скрыта, если нет status и days_left", await p.ev("(() => { wd.mine.collections.forEach(c => { delete c.status; delete c.days_left; }); renderWardrobe(); return document.getElementById('wd-season').hidden; })()"), True)
    check("при активном сезоне строка показывает дни со склонением", await p.ev("(() => { const c = wd.mine.collections.find(c => c.code === 'leaves'); c.status = 'active'; c.days_left = 5; renderWardrobe(); return [document.getElementById('wd-season').hidden, document.querySelector('.wd-season-btn').textContent]; })()"), [False, "Листопад: осталось 5 дней →"])
    check("склонение 1 день", await p.ev("(() => { wd.mine.collections.find(c => c.code === 'leaves').days_left = 1; renderWardrobe(); return document.querySelector('.wd-season-btn').textContent; })()"), "Листопад: осталось 1 день →")
    check("склонение 2 дня", await p.ev("(() => { wd.mine.collections.find(c => c.code === 'leaves').days_left = 2; renderWardrobe(); return document.querySelector('.wd-season-btn').textContent; })()"), "Листопад: осталось 2 дня →")
    await p.tap(".wd-season-btn")
    await p.wait("!document.getElementById('collection-sheet').hidden", 5, "шторка коллекции Листопад")
    check("тап по сезону открывает шторку в профиле", await p.ev("[!document.querySelector('[data-screen=profile]').hidden, !document.getElementById('collection-sheet').hidden, document.getElementById('collection-title').textContent]"), [True, True, "Листопад"])
    await p.tap("#collection-close")
    await p.wait("document.getElementById('collection-sheet').hidden", 5, "шторка закрыта")
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden", 5, "возврат в магазин")
    await p.ev("(() => { delete wd.mine.collections.find(c => c.code === 'leaves').status; renderWardrobe(); })()")
    await p.tap("#wd-tabs .wd-tab:nth-child(7)")           # рубашка карт
    statuses = await p.ev("Object.fromEntries([...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.querySelector('.wd-name').textContent, c.querySelector('.wd-status').textContent]))")
    check("рубашки: Классика надета, Листопад есть, Уголь скоро", [statuses["Классика"], statuses["Листопад"], statuses["Уголь"]], ["Надето", "Есть", "Скоро"])
    await p.tap("#wd-tabs .wd-tab:nth-child(4)")           # стол
    check("стол Октябрь: часть коллекции, подпись «Из коллекции «Листопад»»", await p.ev("[...document.querySelectorAll('#wd-grid .wd-card')].filter(c => c.dataset.code === 'table_autumn').map(c => c.querySelector('.wd-status').textContent)"), ["Из коллекции «Листопад»"])
    await p.tap("#wd-grid .wd-card[data-code=table_autumn]")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("предпросмотр части: нет кнопки покупки, кнопка «Открыть коллекцию»", await p.ev("[document.getElementById('wd-prev-act').hidden, (%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-gift-open').hidden]" % SP),
          [True, "Открыть коллекцию", True])
    await p.tap("#wd-prev-msg button")
    await p.wait("document.getElementById('wd-prev-sheet').hidden && !document.getElementById('collection-sheet').hidden", 5, "шторка Листопада из предпросмотра")
    check("кнопка закрывает предпросмотр и открывает шторку в профиле", await p.ev("[document.getElementById('wd-prev-sheet').hidden, !document.querySelector('[data-screen=profile]').hidden, !document.getElementById('collection-sheet').hidden, document.getElementById('collection-title').textContent]"), [True, True, True, "Листопад"])
    await p.tap("#collection-close")
    await p.wait("document.getElementById('collection-sheet').hidden", 5, "шторка закрыта")
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden", 5, "возврат в магазин")
    # полный набор: выдаём остальные части и надеваем все восемь
    for code in ("table_autumn", "mine_acorn", "chip_leaf", "keno_apple", "crash_maple", "frame_wreath", "badge_pumpkin"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'collection', ?)", (uid, code, now))
    for slot, code in (("card_back", "back_leaves"), ("table", "table_autumn"), ("mine_icons", "mine_acorn"), ("chip", "chip_leaf"), ("keno_ball", "keno_apple"), ("crash", "crash_maple"), ("avatar_frame", "frame_wreath"), ("badge", "badge_pumpkin")):
        w.sql("INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
    await w.reload()
    await p.tap(".tab[data-tab=shop]")
    await p.wait("document.documentElement.getAttribute('data-set-complete') === 'leaves'", 10, "эффект полного набора")
    check("полная коллекция: у корня эффект", await p.ev("document.documentElement.getAttribute('data-set-complete')"), "leaves")
