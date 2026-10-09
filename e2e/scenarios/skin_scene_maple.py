"""Сцены скинов (DESIGN.md, приёмка): скин краша «Клён» (сцена skins/crash_maple.js). Проверяется: сцена и её css подгружаются при открытии экрана краша с надетым скином и
исчезают из DOM при уходе с экрана и при снятии скина; высотные зоны меняются по множителю на ×1.5, ×5, ×20 (в DOM не больше двух зон); события: вывод показывает руку в перчатке,
краш запускает падение листа (частиц не больше 12, потом ни одной); пауза при скрытии страницы; prefers-reduced-motion и perf-lite выключают движение покоя и частицы (краш показывается сменой кадра);
эффект полного набора «Листопад» запускает порыв ветра (частиц от 1 до 12); монитор частоты кадров включает perf-lite на низкой частоте."""
import asyncio
import time

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "skin_scene_maple"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('.skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/crash_maple.css\"]').length"
ZONES = "[...document.querySelectorAll('.scm-zone')].map(g => g.dataset.zone)"
PARTICLES = "document.querySelectorAll('.scm-p.on').length"
FLUTTER = "getComputedStyle(document.querySelector('.scm-flutter')).animationName"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def at_x(w, p, fs, ms):
    await server_to(w, p, fs + ms)
    await p.wait("crServerNow() >= %d" % (fs + ms - 100), 10, "серверное время дошло")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'crash_maple', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'crash', 'crash_maple')", (uid,))
    for code in ("back_leaves", "table_autumn", "mine_acorn", "chip_leaf", "keno_apple", "frame_wreath", "badge_pumpkin"):      # с «Клёном» это все восемь частей «Листопада»: эффект полного набора
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, 'free', NULL, ?)", (uid, code, now))
    w.server.script(crash_live=[2500, 150, 150, 150])
    await w.reload()
    # настоящий монитор кадров на медленной машине сам мог бы включить perf-lite посреди сценария: его запуск отключаем, монитор проверяется ниже вручную поданными кадрами
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("до открытия краша сцены и css скина в DOM нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.wait("!!document.querySelector('.scm-r')", 10, "сцена смонтирована")
    check("сцена одна, css скина подключён, у графика метка сцены", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('cr-chart').dataset.scene")], [1, 1, "crash_maple"])
    await p.wait("!document.getElementById('cr-chart').hidden", 10, "график виден")
    off = await p.ev("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scm-leaf').getBoundingClientRect(); "
                     "return [r.left + r.width / 2 - (c.left + 8), r.top + r.height / 2 - (c.top + 8 + 149 / 150 * (c.height - 16))]; })()")
    check("лист в начале стоит на начале линии (сцена монтируется при скрытом графике, размер берётся по факту)", [abs(off[0]) < 4, abs(off[1]) < 4], [True, True])
    check("вес сцены: узлов в DOM немного", await p.ev("document.querySelector('.skin-scene').querySelectorAll('*').length") < 260, True)

    # --- эффект полного набора: порыв ветра при событии skin:effect {set:'leaves'}
    check("собран полный набор «Листопад»", await p.ev("skinHasSet('leaves')"), True)
    await p.ev("skinEvents.emit('skin:effect', { set: 'leaves' })")
    await p.wait("document.querySelectorAll('.scm-p.on').length > 0", 5, "порыв ветра")
    seen = await p.ev(PARTICLES)
    check("эффект: порыв листьев (частиц от 1 до 12)", 1 <= seen <= 12, True)
    await asyncio.sleep(2.0)
    check("порыв завершён: частиц 0", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await p.ev("skinEvents.emit('skin:effect', { set: 'leaves' })")
    check("reduced-motion: порыва нет (событие эффекта игнорируется)", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- раунд 1 (точка ×25.00): зоны по множителю на ×1.5, ×5, ×20, пауза, вывод (рука), краш (падение листа)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 1800)
    check("×1.2: зона «Аллея»", await p.ev(ZONES), ["0"])
    await at_x(w, p, fs, 4200)
    await p.wait("!!document.querySelector('.scm-zone.in[data-zone=\"1\"]')", 8, "пришла вторая зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scm-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scm-zone').length === 1", 5, "переход зон закончен")
    check("×1.5: зона «Кроны», уходящая зона убрана", await p.ev(ZONES), ["1"])

    await at_x(w, p, fs, 14500)
    await p.wait("!!document.querySelector('.scm-zone.in[data-zone=\"2\"]')", 8, "пришла третья зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scm-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scm-zone').length === 1", 5, "переход зон закончен")
    check("×5: зона «Над парком», уходящая зона убрана", await p.ev(ZONES), ["2"])

    await at_x(w, p, fs, 26500)
    await p.wait("!!document.querySelector('.scm-zone.in[data-zone=\"3\"]')", 8, "пришла четвёртая зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scm-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scm-zone').length === 1", 5, "переход зон закончен")
    check("×20: зона «Тучи», уходящая зона убрана", await p.ev(ZONES), ["3"])

    await p.wait(CASH_VISIBLE, 10, "кнопка «Забрать»")
    await p.ev("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    check("страница скрыта: класс паузы, анимации стоят", [await p.ev("document.documentElement.classList.contains('skin-paused')"), await p.ev("getComputedStyle(document.querySelector('.scm-flutter')).animationPlayState")], [True, "paused"])
    await p.ev("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    check("страница снова видна: пауза снята", await p.ev("document.documentElement.classList.contains('skin-paused')"), False)

    await p.tap("#cr-cash")
    await p.wait("!!document.querySelector('.scm-r.caught')", 10, "вывод показывает руку")
    await p.wait("getComputedStyle(document.querySelector('.scm-glove')).opacity === '1'", 5, "рука в перчатке видна")
    check("рука в перчатке поймала лист", await p.ev("getComputedStyle(document.querySelector('.scm-glove')).opacity"), "1")
    check("в полёте покачивание листа анимировано", await p.ev(FLUTTER) != "none", True)

    await server_to(w, p, fs + t_crash_ms(2500) + 150)
    await p.wait("!!document.querySelector('.scm-r.gone')", 10, "краш: лист сорвался")
    seen = await p.ev(PARTICLES)
    check("краш: частицы летят (частиц от 1 до 12)", 1 <= seen <= 12, True)
    await asyncio.sleep(1.8)
    check("через две секунды все частицы вернулись в пул", await p.ev(PARTICLES), 0)

    # --- раунд 2 (×1.50): prefers-reduced-motion: ни покоя, ни частиц, краш сменой кадра
    await next_round(w, p, fs, 2500)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    check("reduced-motion: лист не покачивается", await p.ev(FLUTTER), "none")
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scm-r.broken')", 10, "краш сменой кадра")
    check("reduced-motion: частиц нет", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- монитор частоты кадров: низкая частота включает perf-lite, нормальная нет
    await p.ev("skinFpsStop()")
    await p.ev("(() => { for (let t = 1000; t <= 6000; t += 16) skinFpsTick(t); skinFpsStop(); })()")
    check("60 кадров в секунду: perf-lite не включается", [await p.ev("skinRt.lite"), await p.ev("document.documentElement.classList.contains('perf-lite')")], [False, False])
    await p.ev("(() => { skinFps.start = 0; skinFps.lowSince = 0; for (let t = 10000; t <= 15000; t += 100) skinFpsTick(t); skinFpsStop(); })()")
    check("10 кадров в секунду дольше 3 с: perf-lite включился", [await p.ev("skinRt.lite"), await p.ev("document.documentElement.classList.contains('perf-lite')")], [True, True])

    # --- раунд 3 (×1.50) в perf-lite: движения покоя и частиц нет, краш сменой кадра, зоны меняются без параллакса
    await next_round(w, p, fs, 150)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    check("perf-lite: лист не покачивается, параллакса нет", [await p.ev(FLUTTER), await p.ev("document.querySelector('.scm-par').style.transform")], ["none", ""])
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scm-r.broken')", 10, "краш сменой кадра")
    check("perf-lite: частиц нет", await p.ev(PARTICLES), 0)

    # --- уход с экрана краша убирает сцену и css, возврат возвращает; снятый скин не оставляет ничего
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scm-r, .scm-p, .scm-zone').length")], [0, 0, 0])
    await p.tap(".tab[data-tab=play]")
    await p.wait("!!document.querySelector('.scm-r')", 10, "сцена вернулась")
    check("после возврата сцена одна", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'crash'", (uid,))
    await w.reload()
    await open_game(p, "crash")
    await p.wait(READY, 15, "панель ставки без скина")
    check("скин снят: сцены, css и декора в DOM нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scm-r, .scm-p, .scm-zone').length"), await p.ev("document.getElementById('cr-chart').dataset.scene || ''")], [0, 0, 0, ""])
