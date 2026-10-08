"""Патина: стадия износа приходит в /api/me (cosmetics.patina) и ставится на корень атрибутом data-patina-<слот>; надевание другого слота стадию не сбрасывает; у только что надетой патины стадия подтягивается из /api/me."""
import time

from harness import check

NAME = "patina_apply"
USERS = {"me": {"rate": 0}}


async def tap_card(p, name):
    idx = await p.ev("[...document.querySelectorAll('#wd-grid .wd-card')].findIndex(c => c.querySelector('.wd-name').textContent === %r) + 1" % name)
    assert idx > 0, "нет карточки «%s»" % name
    await p.tap("#wd-grid .wd-card:nth-child(%d)" % idx)


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    # шесть завершённых крашей выше x50: стадия 2 у фишки (пороги 1, 5, 15, 40); раундов мало: рубашка стадия 0
    for i in range(6):
        w.sql("INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, result, mult_x100, payout, auto, created_at, finished_at) "
              "VALUES (?, 10, 'manual', NULL, 6000, ?, 'finished', 'lose', 6000, 0, 0, ?, ?)", (uid, (now - 1000 + i) * 1000, now - 1000 + i, now - 900 + i))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'chip_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'chip', 'chip_patina')", (uid,))
    await w.reload()
    await p.wait("document.documentElement.getAttribute('data-skin-chip') === 'chip_patina'", 15, "надета патина фишки")
    check("фишка: скин и стадия", (await p.ev("document.documentElement.getAttribute('data-skin-chip')"), await p.ev("document.documentElement.getAttribute('data-patina-chip')")), ("chip_patina", "2"))
    check("у слотов без патины атрибута стадии нет", await p.ev("['card_back', 'mine_icons'].map(s => document.documentElement.getAttribute('data-patina-' + s))"), [None, None])

    # надеваем патину рубашки в гардеробе: стадия фишки не сбрасывается, стадия рубашки подтягивается (0: раундов мало)
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden", 10, "магазин")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    await p.tap("#wd-tabs .wd-tab:nth-child(1)")            # рубашка карт
    await tap_card(p, "Патина")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр патины рубашки")
    await p.tap("#wd-prev-act")
    await p.wait("document.documentElement.getAttribute('data-skin-card_back') === 'back_patina'", 10, "патина рубашки надета")
    check("после надевания другого слота стадия фишки сохранилась", await p.ev("document.documentElement.getAttribute('data-patina-chip')"), "2")
    await p.wait("document.documentElement.getAttribute('data-patina-card_back') !== null", 10, "стадия рубашки подтянулась из /api/me")
    check("стадия новой надетой патины рубашки пришла с сервера (0: сыграно мало раундов)", await p.ev("document.documentElement.getAttribute('data-patina-card_back')"), "0")
