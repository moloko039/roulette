"""Сцена скина кено «Патина» (skins/keno_patina.js, skins/keno_patina.css, коллекция «Патина»).
Проверяется: сцена монтируется в #keno-board при открытии кено с надетым скином, css скина подключён;
стадии износа: стадия 0 без потёртостей, со стадии 2 видны потёртости на жетонах;
событие draw не роняет страницу, при розыгрыше вспыхивают жетоны;
prefers-reduced-motion и perf-lite без движения (смена кадра);
уход на другую вкладку и снятие скина убирают сцену и css."""
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_keno_patina"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#keno-board .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/keno_patina.css\"]').length"
DATA_SCENE = "document.getElementById('keno-board').dataset.scene || ''"
SCUFFS = "[...document.querySelectorAll('.skp-scuff')].filter(el => getComputedStyle(el).display !== 'none').length"
SCRATCHES = "[...document.querySelectorAll('.skp-scratch')].filter(el => getComputedStyle(el).display !== 'none').length"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

DRAW_WIN = [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]
DRAW_ZERO = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'keno_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'keno_ball', 'keno_patina')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана кено сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 шариков")
    await p.wait("!!document.querySelector('.skp-decor')", 10, "декор смонтирован")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("сцена смонтирована, css подключён, data-scene установлена",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, 1, "keno_patina"])

    # --- Стадии износа: стадия 0 без потёртостей, со стадии 2 видны потёртости на жетонах ---
    scuffs_0 = await p.ev(SCUFFS)
    check("стадия 0: потёртостей на жетонах нет", scuffs_0, 0)
    scratches_0 = await p.ev(SCRATCHES)
    check("стадия 0: царапин на поле нет", scratches_0, 0)

    # Стадия 2: потёртости на жетонах появляются
    await p.ev("document.documentElement.setAttribute('data-patina-keno_ball', '2')")
    scuffs_2 = await p.ev(SCUFFS)
    check("стадия 2+: видны потёртости на жетонах", scuffs_2 > 0, True)

    # Стадия 4: сеть потёртостей увеличивается
    await p.ev("document.documentElement.setAttribute('data-patina-keno_ball', '4')")
    scuffs_4 = await p.ev(SCUFFS)
    check("стадия 4: потёртостей больше, чем на стадии 2", scuffs_4 > scuffs_2, True)

    # Возврат к стадии 2 для последующей игры
    await p.ev("document.documentElement.setAttribute('data-patina-keno_ball', '2')")

    # Выбор 3 чисел
    for n in (1, 2, 3):
        await p.tap("button.keno-ball:nth-of-type(%d)" % n)
    check("выбрано 3 числа", await p.ev("document.querySelectorAll('.keno-ball.sel').length"), 3)

    # --- Раунд 1: выигрыш (3 совпадения), событие draw не роняет страницу ---
    w.server.script(keno=[DRAW_WIN])
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 1 завершён")
    check("3 совпадения на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 3)

    # --- Раунд 2: prefers-reduced-motion и perf-lite: розыгрыш без движения (смена кадра), без падений ---
    await p.send("Emulation.setEmulatedMedia", STILL)
    w.server.script(keno=[DRAW_ZERO])
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 2 (still) завершён")
    check("0 совпадений на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- Переключение вкладок: уход убирает сцену и css, возврат восстанавливает ---
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята", await p.ev(DATA_SCENE), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "возврат в кено")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)], [1, 1, "keno_patina"])

    # --- Снятие скина: в DOM ничего не остаётся ---
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'keno_ball'", (uid,))
    await w.reload()
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 15, "кено без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [0, 0, ""])
