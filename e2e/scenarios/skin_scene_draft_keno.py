"""Сцена скина кено «Черновик» (skins/draft_keno.css, коллекция «Черновик»).
Проверяется: сцена монтируется в #keno-board при открытии кено с надетым скином, css скина подключён;
уход на другую вкладку и снятие скина убирают сцену и css;
выбранный шарик получает анимацию обводки овала ручкой;
совпадение (.hit) получает анимацию жёлтой полосы маркера;
prefers-reduced-motion и perf-lite выключают анимации (animationName === 'none');
промахи получают класс .miss."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_draft_keno"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#keno-board .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/draft_keno.css\"]').length"
DATA_SCENE = "document.getElementById('keno-board').dataset.scene || ''"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

SEL_ANIM = "getComputedStyle(document.querySelector('button.keno-ball.sel span'), '::before').animationName"
HIT_ANIM = "getComputedStyle(document.querySelector('button.keno-ball.hit span'), '::after').animationName"

DRAW_WIN = [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]
DRAW_ZERO = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'draft_keno', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'keno_ball', 'draft_keno')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана кено сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 шариков")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("сцена смонтирована, css подключён, data-scene установлена",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, 1, "draft_keno"])

    # Выбор 3 чисел
    for n in (1, 2, 3):
        await p.tap("button.keno-ball:nth-of-type(%d)" % n)
    check("выбрано 3 числа", await p.ev("document.querySelectorAll('.keno-ball.sel').length"), 3)

    # Анимация обводки ручкой на выбранном числе
    sel_anim = await p.ev(SEL_ANIM)
    check("выбранный шарик получает анимацию обводки ручкой", sel_anim != "none" and "sel-circle" in sel_anim, True)

    # prefers-reduced-motion отключает анимацию
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("prefers-reduced-motion: getComputedStyle(...).animationName === 'none'", await p.ev(SEL_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)
    check("prefers-reduced-motion снят: анимация активна", await p.ev(SEL_ANIM) != "none", True)

    # perf-lite отключает анимацию
    await p.ev("skinSetLite()")
    check("perf-lite: getComputedStyle(...).animationName === 'none'", await p.ev(SEL_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")
    check("perf-lite снят: анимация активна", await p.ev(SEL_ANIM) != "none", True)

    # --- Раунд 1: выигрыш (3 совпадения) ---
    w.server.script(keno=[DRAW_WIN])
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 1 завершён")
    check("3 совпадения на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 3)

    # Совпадение получает анимацию жёлтой полосы маркера
    hit_anim = await p.ev(HIT_ANIM)
    check("совпадение получает анимацию полосы маркера", hit_anim != "none" and "marker-swipe" in hit_anim, True)

    # prefers-reduced-motion и perf-lite на совпадении
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("hit: prefers-reduced-motion отключает анимацию", await p.ev(HIT_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    await p.ev("skinSetLite()")
    check("hit: perf-lite отключает анимацию", await p.ev(HIT_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")

    # --- Раунд 2: 0 совпадений (промахи .miss) ---
    w.server.script(keno=[DRAW_ZERO])
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 2 завершён")
    check("0 совпадений на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 0)
    check("3 промаха .miss на поле", await p.ev("document.querySelectorAll('.keno-ball.miss').length"), 3)

    # --- Переключение вкладок: уход убирает сцену и css, возврат восстанавливает ---
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята", await p.ev(DATA_SCENE), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "возврат в кено")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)], [1, 1, "draft_keno"])

    # --- Снятие скина: в DOM ничего не остаётся ---
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'keno_ball'", (uid,))
    await w.reload()
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 15, "кено без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [0, 0, ""])
