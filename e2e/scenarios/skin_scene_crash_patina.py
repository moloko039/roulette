"""Сцены скинов (DESIGN.md раздел 8): скин краша «Патина» (сцена skins/crash_patina.js).
Проверяется: сцена и её css подгружаются при открытии экрана краша с надетым скином и исчезают из DOM при уходе с экрана и при снятии скина;
приборный циферблат-шкала вместо фона, бумажная лента самописца с координатной сеткой;
стадии износа: бумажная лента желтеет (0..4), стекло циферблата царапается (штрихи со стадии 2+, из seed);
высотные зоны шкалы меняются по множителю (в DOM не больше двух зон);
события: вывод отмечает точку, краш роняет стрелку к нулю с дребезгом (частиц до 12, потом ни одной);
пауза при скрытии страницы; prefers-reduced-motion и perf-lite без частиц и без движения (смена кадра)."""
import time

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "skin_scene_crash_patina"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('.skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/crash_patina.css\"]').length"
ZONES = "[...document.querySelectorAll('.scpt-zone')].map(g => g.dataset.zone)"
PARTICLES = "document.querySelectorAll('.scpt-p.on').length"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def at_x(w, p, fs, ms):
    await server_to(w, p, fs + ms)
    await p.wait("crServerNow() >= %d" % (fs + ms - 100), 10, "серверное время дошло")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'crash_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'crash', 'crash_patina')", (uid,))
    w.server.script(crash_live=[600, 150, 150, 150])
    await w.reload()

    # Монитор кадров на медленной машине сам мог бы включить perf-lite посреди сценария:
    # его запуск отключаем, монитор проверяется ниже вручную поданными кадрами
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("до открытия краша сцены и css скина в DOM нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.wait("!!document.querySelector('.scpt-r')", 10, "сцена смонтирована")
    check("сцена одна, css скина подключён, у графика метка сцены", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('cr-chart').dataset.scene")], [1, 1, "crash_patina"])
    await p.wait("!document.getElementById('cr-chart').hidden", 10, "график виден")

    await p.wait("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scpt-pen').getBoundingClientRect(); "
                 "return Math.abs(r.left + r.width / 2 - (c.left + 8)) < 4 && Math.abs(r.top + r.height / 2 - (c.top + 8 + 149 / 150 * (c.height - 16))) < 4; })()", 10, "перо на начале линии")
    check("перо в начале стоит на начале линии внизу графика", True, True)
    check("вес сцены: узлов в DOM немного", await p.ev("document.querySelector('.skin-scene').querySelectorAll('*').length") < 260, True)

    # --- стадия износа: бумажная лента желтеет, царапины на стекле циферблата со стадии 2+
    await p.wait("!!document.querySelector('.scpt-tape')", 5, "бумажная лента смонтирована")
    scratches_0 = await p.ev("[...document.querySelectorAll('.scpt-scratch')].filter(el => getComputedStyle(el).display !== 'none').length")
    check("стадия 0: царапин на стекле циферблата нет", scratches_0, 0)

    await p.ev("document.documentElement.setAttribute('data-patina-crash', '2')")
    scratches_2 = await p.ev("[...document.querySelectorAll('.scpt-scratch')].filter(el => getComputedStyle(el).display !== 'none').length")
    check("стадия 2: царапины на стекле появились", scratches_2 > 0, True)

    await p.ev("document.documentElement.setAttribute('data-patina-crash', '4')")
    scratches_4 = await p.ev("[...document.querySelectorAll('.scpt-scratch')].filter(el => getComputedStyle(el).display !== 'none').length")
    check("стадия 4: сеть царапин увеличилась", scratches_4 > scratches_2, True)

    # возвращаем стадию 0
    await p.ev("document.documentElement.setAttribute('data-patina-crash', '0')")

    # --- раунд 1 (точка ×6.00): зоны по множителю на ×1.5, пауза, вывод, краш
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 1800)
    check("×1.2: шкала циферблата (зона 0)", await p.ev(ZONES), ["0"])

    await at_x(w, p, fs, 4200)
    await p.wait("!!document.querySelector('.scpt-zone.in[data-zone=\"1\"]')", 8, "пришла зона 1 шкалы")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scpt-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scpt-zone').length === 1", 5, "переход зон закончен")
    check("×1.5: зона 1, уходящая зона убрана", await p.ev(ZONES), ["1"])

    await p.wait(CASH_VISIBLE, 10, "кнопка «Забрать»")
    await p.ev("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    check("страница скрыта: класс паузы", await p.ev("document.documentElement.classList.contains('skin-paused')"), True)
    await p.ev("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    check("страница снова видна: пауза снята", await p.ev("document.documentElement.classList.contains('skin-paused')"), False)

    await p.tap("#cr-cash")
    await p.wait("!!document.querySelector('.scpt-r.cashout')", 10, "вывод: перо отмечает точку")
    check("вывод отмечен на пере", await p.ev("!!document.querySelector('.scpt-r.cashout')"), True)

    await server_to(w, p, fs + t_crash_ms(600) + 150)
    await p.wait("!!document.querySelector('.scpt-r.broken')", 10, "краш: стрелка упала к нулю с дребезгом")
    seen = await p.ev(PARTICLES)
    check("краш: частицы-брызги летят (до 12)", 0 <= seen <= 12, True)
    await p.wait("document.querySelectorAll('.scpt-p.on').length === 0", 4, "все брызги вернулись в пул")
    check("через четыре секунды всех частиц 0", await p.ev(PARTICLES), 0)

    # --- раунд 2 (×1.50): prefers-reduced-motion: ни движения, ни частиц, краш сменой кадра
    await next_round(w, p, fs, 600)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scpt-r.broken')", 10, "краш сменой кадра")
    check("reduced-motion: частиц нет", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- монитор частоты кадров: низкая частота включает perf-lite, нормальная нет
    await p.ev("skinFpsStop()")
    await p.ev("(() => { for (let t = 1000; t <= 6000; t += 16) skinFpsTick(t); skinFpsStop(); })()")
    check("60 кадров в секунду: perf-lite не включается", [await p.ev("skinRt.lite"), await p.ev("document.documentElement.classList.contains('perf-lite')")], [False, False])
    await p.ev("(() => { skinFps.start = 0; skinFps.lowSince = 0; for (let t = 10000; t <= 15000; t += 100) skinFpsTick(t); skinFpsStop(); })()")
    check("10 кадров в секунду дольше 3 с: perf-lite включился", [await p.ev("skinRt.lite"), await p.ev("document.documentElement.classList.contains('perf-lite')")], [True, True])

    # --- уход с экрана краша убирает сцену и css, возврат возвращает; снятый скин не оставляет ничего
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scpt-r, .scpt-p, .scpt-zone').length")], [0, 0, 0])
    await p.tap(".tab[data-tab=play]")
    await p.wait("!!document.querySelector('.scpt-r')", 10, "сцена вернулась")
    check("после возврата сцена одна", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])

    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'crash'", (uid,))
    await w.reload()
    await open_game(p, "crash")
    await p.wait(READY, 15, "панель ставки без скина")
    check("скин снят: сцены, css и декора в DOM нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scpt-r, .scpt-p, .scpt-zone').length"), await p.ev("document.getElementById('cr-chart').dataset.scene || ''")], [0, 0, 0, ""])
