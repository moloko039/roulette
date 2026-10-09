"""Сцены скинов (DESIGN.md, приёмка): скин краша «Бочка» (сцена skins/crash_barrel.js). Проверяется: сцена и её css подгружаются при открытии экрана краша с надетым скином и
исчезают из DOM при уходе с экрана и при снятии скина; высотные зоны меняются по множителю (в DOM не больше двух зон); события: вывод открывает парашют, краш разбрасывает обломки
(частиц не больше 12, потом ни одной); пауза при скрытии страницы; prefers-reduced-motion и perf-lite выключают движение покоя и частицы (краш показывается сменой кадра);
монитор частоты кадров включает perf-lite на низкой частоте (и не включает на нормальной)."""
import asyncio
import time

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "skin_scene"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('.skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/crash_barrel.css\"]').length"
ZONES = "[...document.querySelectorAll('.scb-zone')].map(g => g.dataset.zone)"
PARTICLES = "document.querySelectorAll('.scb-p.on').length"
FLAME = "getComputedStyle(document.querySelector('.scb-flame')).animationName"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def at_x(w, p, fs, ms):
    await server_to(w, p, fs + ms)
    await p.wait("crServerNow() >= %d" % (fs + ms - 100), 10, "серверное время дошло")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'crash_barrel', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'crash', 'crash_barrel')", (uid,))
    w.server.script(crash_live=[400, 150, 150, 150])
    await w.reload()
    check("до открытия краша сцены и css скина в DOM нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.wait("!!document.querySelector('.scb-r')", 10, "сцена смонтирована")
    check("сцена одна, css скина подключён, у графика метка сцены", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('cr-chart').dataset.scene")], [1, 1, "crash_barrel"])
    await p.wait("!document.getElementById('cr-chart').hidden", 10, "график виден")
    off = await p.ev("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scb-rocket').getBoundingClientRect(); "
                     "return [r.left + r.width / 2 - (c.left + 8), r.top + r.height / 2 - (c.top + 8 + 149 / 150 * (c.height - 16))]; })()")
    check("бочка в начале стоит на начале линии (сцена монтируется при скрытом графике, размер берётся по факту)", [abs(off[0]) < 4, abs(off[1]) < 4], [True, True])
    check("вес сцены: узлов в DOM немного", await p.ev("document.querySelector('.skin-scene').querySelectorAll('*').length") < 260, True)

    # --- раунд 1 (точка ×4.00): зоны по множителю, пауза, вывод (парашют), краш (обломки)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 1800)
    check("×1.2: зона «Огород»", await p.ev(ZONES), ["0"])
    await at_x(w, p, fs, 6300)
    await p.wait("!!document.querySelector('.scb-zone.in[data-zone=\"1\"]')", 8, "пришла вторая зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scb-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scb-zone').length === 1", 5, "переход зон закончен")
    check("×2: зона «Над крышами», уходящая зона убрана", await p.ev(ZONES), ["1"])
    await p.wait(CASH_VISIBLE, 10, "кнопка «Забрать»")
    await p.ev("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    check("страница скрыта: класс паузы, анимации стоят", [await p.ev("document.documentElement.classList.contains('skin-paused')"), await p.ev("getComputedStyle(document.querySelector('.scb-flame')).animationPlayState")], [True, "paused"])
    await p.ev("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    check("страница снова видна: пауза снята", await p.ev("document.documentElement.classList.contains('skin-paused')"), False)
    await p.tap("#cr-cash")
    await p.wait("!!document.querySelector('.scb-r.open')", 10, "вывод раскрыл парашют")
    check("в покое пламя бочки анимировано", await p.ev(FLAME) != "none", True)
    await server_to(w, p, fs + t_crash_ms(400) + 150)
    await p.wait("!!document.querySelector('.scb-r.gone')", 10, "краш: бочка разлетелась")
    seen = await p.ev(PARTICLES)
    check("краш: обломки летят (частиц от 1 до 12)", 1 <= seen <= 12, True)
    await asyncio.sleep(1.8)
    check("через две секунды все частицы вернулись в пул", await p.ev(PARTICLES), 0)

    # --- раунд 2 (×1.50): prefers-reduced-motion: ни покоя, ни частиц, краш сменой кадра
    await next_round(w, p, fs, 400)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    check("reduced-motion: пламя не анимируется", await p.ev(FLAME), "none")
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scb-r.broken')", 10, "краш сменой кадра")
    check("reduced-motion: частиц нет", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- монитор частоты кадров: низкая частота включает perf-lite, нормальная нет (кадры подаются вручную с заданными метками времени)
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
    check("perf-lite: пламя не анимируется, параллакса нет", [await p.ev(FLAME), await p.ev("document.querySelector('.scb-par').style.transform")], ["none", ""])
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scb-r.broken')", 10, "краш сменой кадра")
    check("perf-lite: частиц нет", await p.ev(PARTICLES), 0)

    # --- уход с экрана краша убирает сцену и css, возврат возвращает; снятый скин не оставляет ничего
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scb-r, .scb-p, .scb-zone').length")], [0, 0, 0])
    await p.tap(".tab[data-tab=play]")
    await p.wait("!!document.querySelector('.scb-r')", 10, "сцена вернулась")
    check("после возврата сцена одна", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'crash'", (uid,))
    await w.reload()
    await open_game(p, "crash")
    await p.wait(READY, 15, "панель ставки без скина")
    check("скин снят: сцены, css и обломков в DOM нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scb-r, .scb-p, .scb-zone').length"), await p.ev("document.getElementById('cr-chart').dataset.scene || ''")], [0, 0, 0, ""])
