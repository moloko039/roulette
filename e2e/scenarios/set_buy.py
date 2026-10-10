"""Покупка набора косметики целиком: блок набора в листе предпросмотра части (цена набора и сумма частей из каталога), подтверждение вторым нажатием, выдача всех частей одной покупкой, снятие кристаллов; блока нет, если у игрока уже есть часть набора."""
import time

from harness import check

NAME = "set_buy"
USERS = {"me": {"rate": 0}}
NBSP = "(s) => s.replace(/\\s/g, ' ')"


async def tap_card(p, name):
    """Нажимает карточку текущего слота по названию (у карточек нет кода в разметке)."""
    idx = await p.ev("[...document.querySelectorAll('#wd-grid .wd-card')].findIndex(c => c.querySelector('.wd-name').textContent === %r) + 1" % name)
    assert idx > 0, "нет карточки «%s»" % name
    await p.tap("#wd-grid .wd-card:nth-child(%d)" % idx)


async def open_wardrobe(p):
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden", 10, "магазин")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 1000, 'owner_grant', 'e2e-grant-1', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 1000)", (uid,))
    await open_wardrobe(p)
    await p.wait("document.getElementById('shop-gems').textContent.replace(/\\s/g, '') === '1000'", 10, "1000 кристаллов в шапке магазина")

    # ---- «Черновик»: блок набора в предпросмотре части, покупка с подтверждением
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол
    await tap_card(p, "Черновик")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    await p.wait("!document.getElementById('wd-set-box').hidden", 5, "блок набора виден")
    check("блок набора «Черновик»: цена набора и сумма частей из каталога", await p.ev("(%s)(document.getElementById('wd-set-desc').textContent)" % NBSP),
          "Весь набор «Черновик»: 200 💎 (вместо 800 💎 за все части)")
    check("кнопка набора до подтверждения", await p.ev("document.getElementById('wd-set-act').textContent"), "Купить набор")
    base = await p.ev("E.count('/api/cosmetics/buy-set')")
    await p.tap("#wd-set-act")
    check("после первого нажатия запрос не ушёл, просит подтверждение", await p.ev(
        "[E.count('/api/cosmetics/buy-set'), (%s)(document.getElementById('wd-prev-msg').textContent), document.getElementById('wd-set-act').textContent]" % NBSP),
        [base, "Потратить 200 💎? Вернуть набор нельзя", "Потратить"])
    await p.tap("#wd-set-act")
    await p.wait("document.getElementById('wd-prev-msg').textContent === 'Набор «Черновик» куплен'", 10, "набор куплен")
    check("ушёл ровно один запрос покупки набора", await p.ev("E.count('/api/cosmetics/buy-set')") - base, 1)
    check("кристаллов стало 800, в базе все восемь частей набора", [
        w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ? AND item_code LIKE 'draft_%'", (uid,))], [800, 8])
    check("после покупки блока набора нет", await p.ev("document.getElementById('wd-set-box').hidden"), True)
    check("в шапке магазина баланс кристаллов обновился", await p.ev("document.getElementById('shop-gems').textContent.replace(/\\s/g, '')"), "800")
    await p.tap("#wd-prev-close")

    # ---- «Пустота»: пока частей нет, блок виден; когда одна часть куплена отдельно, блока нет у другой части
    await p.tap("#wd-tabs .wd-tab:nth-child(2)")           # фишки
    await tap_card(p, "Пустота")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр void_chip")
    await p.wait("!document.getElementById('wd-set-box').hidden", 5, "блок набора «Пустота» виден")
    check("блок набора «Пустота»: цена набора и сумма частей", await p.ev("(%s)(document.getElementById('wd-set-desc').textContent)" % NBSP),
          "Весь набор «Пустота»: 2000 💎 (вместо 2400 💎 за все части)")
    await p.tap("#wd-prev-close")
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'void_table', 'gems', NULL, ?)", (uid, now))
    await p.tap(".tab[data-tab=play]")
    await open_wardrobe(p)
    await p.tap("#wd-tabs .wd-tab:nth-child(2)")           # фишки
    await tap_card(p, "Пустота")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр void_chip после покупки части")
    check("часть набора уже есть у игрока: блока набора у другой части нет", await p.ev("document.getElementById('wd-set-box').hidden"), True)
    check("и обычная покупка отдельной части по-прежнему доступна", await p.ev("document.getElementById('wd-prev-act').hidden"), False)
