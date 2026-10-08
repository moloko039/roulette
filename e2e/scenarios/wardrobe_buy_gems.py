"""Покупка предмета за кристаллы на странице «Оформление» магазина (настоящий сервер): цена из каталога, подтверждение, один запрос, списание кристаллов в базе и в журнале,
баланс кристаллов на странице магазина обновляется, нехватка кристаллов: понятный текст и подсказка, фишки не тронуты."""
import time

from harness import check

NAME = "wardrobe_buy_gems"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = (r"status of 409",)

NBSP = "(s) => s.replace(/\\s/g, ' ')"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 200, 'owner_grant', 'e2e-grant-1', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 200)", (uid,))
    chips_before = w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,))
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.getElementById('shop-gems').textContent.replace(/\\s/g, '') === '200'", 10, "магазин, 200 кристаллов")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол
    check("карточка «Лагуна»: цена в кристаллах из каталога", await p.ev("(%s)(document.querySelector('#wd-grid .wd-card:nth-child(2) .wd-status').textContent)" % NBSP), "150 кристаллов")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("кнопка «Купить за …» и строка про игру", await p.ev("[(%s)(document.getElementById('wd-prev-act').textContent), document.getElementById('wd-prev-note').textContent]" % NBSP),
          ["Купить за 150 кристаллов", "Предметы не влияют на игру."])
    base = await p.ev("E.count('/api/cosmetics/buy')")
    await p.tap("#wd-prev-act")
    check("подтверждение", await p.ev("[(%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-prev-act').textContent]" % NBSP),
          ["Потратить 150 кристаллов? Вернуть предмет нельзя", "Потратить"])
    check("до подтверждения запроса покупки нет", await p.ev("E.count('/api/cosmetics/buy')"), base)
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-act').textContent === 'Надеть'", 10, "куплено")
    check("один запрос, статус «Есть», сообщение", await p.ev("[E.count('/api/cosmetics/buy') - %d, document.getElementById('wd-prev-status').textContent, (%s)(document.getElementById('wd-prev-msg').textContent)]" % (base, NBSP)),
          [1, "Есть", "Предмет куплен: 150 кристаллов"])
    check("в базе: 50 кристаллов, предмет с источником gems, строка списания, фишки не тронуты", [
        w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT source FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'table_blue'", (uid,)),
        w.sql_value("SELECT delta || ':' || reason || ':' || ref FROM gems_ledger WHERE telegram_id = ? AND delta < 0", (uid,)),
        w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)) == chips_before],
        [50, "gems", "-150:cosmetic_purchase:table_blue", True])
    await p.tap("#wd-prev-close")
    check("на странице магазина баланс кристаллов обновился без перезагрузки", await p.ev("document.getElementById('shop-gems').textContent.replace(/\\s/g, '')"), "50")
    # нехватка: «Сумерки» недоступны, берём предмет из другого слота за 100 кристаллов (неоновый краш), у игрока осталось 50
    await p.tap("#wd-tabs .wd-tab:nth-child(6)")           # краш
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("цена 100 кристаллов", await p.ev("(%s)(document.getElementById('wd-prev-act').textContent)" % NBSP), "Купить за 100 кристаллов")
    await p.tap("#wd-prev-act")
    await p.tap("#wd-prev-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent.includes('Не хватает')", 10, "нехватка")
    check("нехватка: понятный текст со сколько не хватает и подсказкой", await p.ev("(%s)(document.getElementById('wd-prev-msg').textContent)" % NBSP),
          "Не хватает 50 кристаллов. Их можно купить на странице «Кристаллы»")
    check("нехватка: в базе ничего не изменилось", [w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)),
                                                    w.sql_value("SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina'", (uid,))], [50, 1])      # три бесплатные патины выдаются при открытии гардероба: они не покупка
