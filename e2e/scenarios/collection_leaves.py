"""Коллекция «Листопад» (настоящий сервер): на третий день серии входов награда дня даёт первую часть, сообщение называет коллекцию; в «Оформлении» видна полоса коллекции «1 из 3» с подписью, как получить;
часть в слоте помечена «Коллекция» (не «Не получено»), её нельзя купить; надев все три части, при полном наборе у корня появляется атрибут эффекта."""
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
    check("полоса коллекции: название, 1 из 3, как получить", await p.ev("[...document.querySelectorAll('#wd-collections .wd-collection')].map(c => [(%s)(c.querySelector('strong').textContent), (%s)(c.querySelector('small').textContent)])" % (SP, SP)),
          [["Листопад: 1 из 3", "Награда дня на 3-й, 5-й и 7-й день серии входов, только в октябре"], ["Дачный сезон: 0 из 8", "За улучшения дохода фермы"]])
    statuses = await p.ev("Object.fromEntries([...document.querySelectorAll('#wd-grid .wd-card')].map(c => [c.querySelector('.wd-name').textContent, c.querySelector('.wd-status').textContent]))")
    check("рубашки: Классика надета, Листопад есть, Уголь скоро", [statuses["Классика"], statuses["Листопад"], statuses["Уголь"]], ["Надето", "Есть", "Скоро"])
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол
    check("стол Октябрь: часть коллекции, не «Не получено»", await p.ev("[...document.querySelectorAll('#wd-grid .wd-card')].filter(c => c.dataset.code === 'table_autumn').map(c => c.querySelector('.wd-status').textContent)"), ["Коллекция"])
    await p.tap("#wd-grid .wd-card[data-code=table_autumn]")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("предпросмотр части: нет кнопки покупки, подсказка про коллекцию", await p.ev("[document.getElementById('wd-prev-act').hidden, (%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-gift-open').hidden]" % SP),
          [True, "Часть коллекции «Листопад»: Награда дня на 3-й, 5-й и 7-й день серии входов, только в октябре", True])
    await p.tap("#wd-prev-close")
    # полный набор: выдаём остальные части и надеваем все три
    for code in ("table_autumn", "mine_acorn"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'collection', ?)", (uid, code, now))
    for slot, code in (("card_back", "back_leaves"), ("table", "table_autumn"), ("mine_icons", "mine_acorn")):
        w.sql("INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
    await w.reload()
    await p.tap(".tab[data-tab=shop]")
    await p.wait("document.documentElement.getAttribute('data-set-complete') === 'leaves'", 10, "эффект полного набора")
    check("полная коллекция: «3 из 3 (собрана)», у корня эффект", await p.ev("[(%s)(document.querySelector('#wd-collections strong').textContent), document.documentElement.getAttribute('data-set-complete')]" % SP), ["Листопад: 3 из 3 (собрана)", "leaves"])
