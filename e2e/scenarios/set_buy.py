"""Покупка набора косметики целиком: отображение скидки на набор, подтверждение покупки, начисление всех предметов, снятие кристаллов."""
import time

from harness import check

NAME = "set_buy"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = ()

NBSP = "(s) => s.replace(/\\s/g, ' ')"

async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 1000, 'owner_grant', 'e2e-grant-1', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 1000)", (uid,))
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.getElementById('shop-gems').textContent.replace(/\\s/g, '') === '1 000'", 10, "магазин, 1000 кристаллов")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол
    
    # Черновик (draft_table)
    await p.tap("#wd-grid .wd-card[data-code='draft_table']")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.wait("!document.getElementById('wd-set-box').hidden", 5, "блок набора виден")
    
    check("блок набора Черновик", await p.ev("(%s)(document.getElementById('wd-set-desc').textContent)" % NBSP), "Весь набор «Черновик»: 200 💎 (вместо 400 💎 за все части)")
    check("кнопка набора", await p.ev("document.getElementById('wd-set-act').textContent"), "Купить набор")
    
    base = await p.ev("E.count('/api/cosmetics/buy-set')")
    await p.tap("#wd-set-act")
    check("подтверждение набора", await p.ev("[(%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-set-act').textContent]" % NBSP),
          ["Потратить 200 💎? Вернуть набор нельзя", "Потратить"])
    
    await p.tap("#wd-set-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Набор «Черновик» куплен'", 10, "набор куплен")
    check("запрос ушёл", await p.ev("E.count('/api/cosmetics/buy-set') - %d" % base), 1)
    
    check("кристаллов стало 800", [w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,))], [800])
    parts_count = w.sql_value("SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ? AND item_code LIKE 'draft_%'", (uid,))
    check("в базе все четыре части", [parts_count], [4])
    
    check("блока набора больше нет", await p.ev("document.getElementById('wd-set-box').hidden"), True)
    
    await p.tap("#wd-prev-close")
    
    # Пустота (void_table) - имитируем наличие одной части (купим отдельно)
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, created_at) VALUES (?, 'void_table', 'gems', ?)", (uid, now))
    # Переоткрываем, чтобы подтянулся mine?
    # Нужно обновить mine. Лучше просто закрыть вкладку и открыть заново?
    # В приложении update можно сделать закрыв/открыв вкладку магазина.
    await p.tap(".tab[data-tab=lobby]")
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол
    
    await p.tap("#wd-grid .wd-card[data-code='void_table']")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр void_table")
    
    check("если есть часть, блока набора нет", await p.ev("document.getElementById('wd-set-box').hidden"), True)
