"""Применение скинов: сервер отдаёт в /api/me cosmetics.equipped (предметы кладутся в базу e2e-сервера напрямую, боевой код не меняется);
атрибуты data-skin-<слот> выставляются на корень, при смене надетого применяются без перезагрузки; слоты рамки и значка не обрабатываются."""
import os
import sqlite3
import time

from harness import check

NAME = "skin_apply"
USERS = {"me": {"rate": 0}}
SLOTS = ["card_back", "chip", "table", "mine_icons", "keno_ball", "crash"]
STARTERS = {"card_back": "back_classic", "chip": "chip_plain", "table": "table_green", "mine_icons": "mine_classic", "keno_ball": "keno_round", "crash": "crash_line"}
WORN = {"card_back": "back_midnight", "chip": "chip_ring", "table": "table_blue", "mine_icons": "mine_star", "keno_ball": "keno_hex", "crash": "crash_neon"}

ATTRS = "(() => { const r = {}; for (const s of %s) r[s] = document.documentElement.getAttribute('data-skin-' + s); return r; })()" % SLOTS


def db(w, query, params=()):
    conn = sqlite3.connect(os.path.join(w.tmp, "e2e.db"))
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


async def run(w):
    p = w.page
    uid = w.users["me"].id
    check("после первого /api/me стоят стартовые скины", await p.ev(ATTRS), STARTERS)
    check("рамка и значок не обрабатываются", await p.ev("[document.documentElement.getAttribute('data-skin-avatar_frame'), document.documentElement.getAttribute('data-skin-badge')]"), [None, None])
    now = int(time.time())
    for slot, code in dict(WORN, avatar_frame="frame_thin", badge="badge_spade").items():
        db(w, "INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))
        db(w, "INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
    await w.reload()
    check("после перезагрузки применены надетые скины", await p.ev(ATTRS), WORN)
    check("рамка и значок по-прежнему не обрабатываются", await p.ev("[document.documentElement.getAttribute('data-skin-avatar_frame'), document.documentElement.getAttribute('data-skin-badge')]"), [None, None])
    # смена в течение сессии: без перезагрузки страницы
    await p.ev("window.__marker = 'same-page'")
    db(w, "DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot IN ('chip', 'table')", (uid,))
    db(w, "UPDATE cosmetic_equipped SET item_code = 'keno_round' WHERE telegram_id = ? AND slot = 'keno_ball'", (uid,))
    await p.tap(".tab[data-tab=profile]")
    await p.ev("E.sleep(5600)")       # между запросами /api/me не меньше 5 секунд
    await p.ev("loadServer('manual')")
    await p.wait("document.documentElement.getAttribute('data-skin-chip') === 'chip_plain'", 10, "скин применён без перезагрузки")
    got = await p.ev(ATTRS)
    check("изменённые слоты применены, остальные прежние", got, dict(WORN, chip="chip_plain", table="table_green", keno_ball="keno_round"))
    check("страница не перезагружалась", await p.ev("window.__marker"), "same-page")
    # неверный ответ сервера не ломает: applySkins отбрасывает странные значения
    check("applySkins: странные значения убираются", await p.ev(
        "(() => { applySkins({ card_back: 'Bad Code!', chip: 5, table: null, mine_icons: 'ok_code' }); const r = {}; for (const s of %s) r[s] = document.documentElement.getAttribute('data-skin-' + s); return r; })()" % SLOTS),
        {"card_back": None, "chip": None, "table": None, "mine_icons": "ok_code", "keno_ball": None, "crash": None})
    await p.ev("applySkins(undefined)")
    check("applySkins без данных снимает все атрибуты", await p.ev(ATTRS), {s: None for s in SLOTS})
    # набор иконок мин по коду: звезда вместо самоцвета, мина остаётся колючим кругом; сброс возвращает стартовый набор
    check("иконки: стартовый набор по умолчанию", await p.ev("minesIcons === MINES_ICON_SETS.default"), True)
    check("иконки: mine_star даёт звезду вместо самоцвета", await p.ev(
        "(() => { applySkins({ mine_icons: 'mine_star' }); return [minesIcons === MINES_ICON_SETS.mine_star, minesIcons.gem !== MINES_ICON_SETS.default.gem, minesIcons.gem.includes('2.6 5.6'), minesIcons.mine.includes('<circle')]; })()"), [True, True, True, True])
    await p.ev("applySkins(undefined)")
    check("иконки: после сброса стартовый набор", await p.ev("minesIcons === MINES_ICON_SETS.default"), True)
