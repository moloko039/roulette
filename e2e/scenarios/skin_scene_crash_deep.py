"""Сцены скинов (DESIGN.md, приёмка): скин краша «Глубина» (сцена skins/crash_deep.js). Проверяется: сцена и её css подгружаются при открытии экрана краша с надетым скином и
исчезают из DOM при уходе с экрана и при снятии скина; высотные зоны меняются по множителю на ×1.5, ×5, ×20 (в DOM не больше двух зон); события: вывод сбрасывает балласт,
краш проявляет трещины на иллюминаторе и разбрасывает пузыри (частиц не больше 12, потом ни одной); батискаф стоит на конце видимой линии (в начале раунда в левом верхнем углу);
глубина «×… · … м» отображается; пауза при скрытии страницы; prefers-reduced-motion и perf-lite выключают движение покоя и частицы (краш показывается сменой кадра);
эффект полного набора «Глубина» запускает светящуюся медузу; монитор частоты кадров включает perf-lite на низкой частоте."""
import asyncio
import time

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "skin_scene_crash_deep"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('.skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/crash_deep.css\"]').length"
ZONES = "[...document.querySelectorAll('.scdp-zone')].map(g => g.dataset.zone)"
PARTICLES = "document.querySelectorAll('.scdp-p.on').length"
NEEDLE = "getComputedStyle(document.querySelector('.scdp-needle')).animationName"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def at_x(w, p, fs, ms):
    await server_to(w, p, fs + ms)
    await p.wait("crServerNow() >= %d" % (fs + ms - 100), 10, "серверное время дошло")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'crash_deep', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'crash', 'crash_deep')", (uid,))
    for code in ("table_deep", "chip_pearl", "mine_urchin", "keno_bubble", "back_deep", "frame_deep", "badge_deep"):      # с «crash_deep» это все восемь частей «Глубины»: эффект полного набора
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, 'free', NULL, ?)", (uid, code, now))
    w.server.script(crash_live=[2500, 150, 150, 150])
    await w.reload()

    # настоящий монитор кадров на медленной машине сам мог бы включить perf-lite посреди сценария: его запуск отключаем, монитор проверяется ниже вручную поданными кадрами
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("до открытия краша сцены и css скина в DOM нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.wait("!!document.querySelector('.scdp-r')", 10, "сцена смонтирована")
    check("сцена одна, css скина подключён, у графика метка сцены", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('cr-chart').dataset.scene")], [1, 1, "crash_deep"])
    await p.wait("!document.getElementById('cr-chart').hidden", 10, "график виден")
    await p.wait("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scdp-sub').getBoundingClientRect(); "
                 "return Math.abs(r.left + r.width / 2 - (c.left + 8)) < 4 && Math.abs(r.top + r.height / 2 - (c.top + 8)) < 4; })()", 10, "батискаф на начале видимой линии")
    off = await p.ev("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scdp-sub').getBoundingClientRect(); "
                     "return [r.left + r.width / 2 - (c.left + 8), r.top + r.height / 2 - (c.top + 8)]; })()")
    check("батискаф в начале стоит на начале видимой линии в левом верхнем углу", [abs(off[0]) < 4, abs(off[1]) < 4], [True, True])
    check("батискаф в начале в верхней половине графика", await p.ev("(() => { const c = document.getElementById('cr-chart').getBoundingClientRect(); const r = document.querySelector('.scdp-sub').getBoundingClientRect(); return r.top + r.height / 2 < c.top + c.height / 2; })()"), True)
    check("вес сцены: узлов в DOM немного", await p.ev("document.querySelector('.skin-scene').querySelectorAll('*').length") < 260, True)

    # отметка глубины
    await p.wait("!!document.querySelector('.scdp-depth')", 5, "глубина смонтирована")
    depth_text = await p.ev("document.querySelector('.scdp-depth').textContent")
    check("глубина в начале отображается", "100 м" in depth_text and "·" in depth_text, True)

    # --- эффект полного набора: огромная светящаяся медуза при событии skin:effect {set:'deep'}
    await p.ev("skinEvents.emit('skin:effect', { set: 'deep' })")
    await p.wait("document.querySelector('.scdp-jelly').getAnimations().length > 0", 5, "медуза поплыла")
    check("медуза показана и анимирована", [await p.ev("getComputedStyle(document.querySelector('.scdp-jelly')).display"), await p.ev("document.querySelector('.scdp-jelly').getAnimations().length")], ["block", 1])
    await p.send("Emulation.setEmulatedMedia", STILL)
    await p.ev("document.querySelector('.scdp-jelly').getAnimations().forEach((a) => a.cancel())")
    await p.ev("skinEvents.emit('skin:effect', { set: 'deep' })")
    check("reduced-motion: медузы нет (событие эффекта игнорируется)", await p.ev("document.querySelector('.scdp-jelly').getAnimations().length"), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- раунд 1 (точка ×25.00): зоны по множителю на ×1.5, ×5, ×20, пауза, вывод (балласт), краш (трещины и пузыри)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 1800)
    check("×1.2: зона «Поверхность»", await p.ev(ZONES), ["0"])
    depth_flying = await p.ev("document.querySelector('.scdp-depth').textContent")
    check("глубина обновляется в полёте", "·" in depth_flying and "м" in depth_flying, True)

    await at_x(w, p, fs, 4200)
    await p.wait("!!document.querySelector('.scdp-zone.in[data-zone=\"1\"]')", 8, "пришла вторая зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scdp-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scdp-zone').length === 1", 5, "переход зон закончен")
    check("×1.5: зона «Сумерки», уходящая зона убрана", await p.ev(ZONES), ["1"])

    await at_x(w, p, fs, 14500)
    await p.wait("!!document.querySelector('.scdp-zone.in[data-zone=\"2\"]')", 8, "пришла третья зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scdp-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scdp-zone').length === 1", 5, "переход зон закончен")
    check("×5: зона «Полночь», уходящая зона убрана", await p.ev(ZONES), ["2"])

    await at_x(w, p, fs, 26500)
    await p.wait("!!document.querySelector('.scdp-zone.in[data-zone=\"3\"]')", 8, "пришла четвёртая зона")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scdp-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scdp-zone').length === 1", 5, "переход зон закончен")
    check("×20: зона «Бездна», уходящая зона убрана", await p.ev(ZONES), ["3"])

    await p.wait(CASH_VISIBLE, 10, "кнопка «Забрать»")
    await p.ev("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    check("страница скрыта: класс паузы, анимации стоят", [await p.ev("document.documentElement.classList.contains('skin-paused')"), await p.ev("getComputedStyle(document.querySelector('.scdp-needle')).animationPlayState")], [True, "paused"])
    await p.ev("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    check("страница снова видна: пауза снята", await p.ev("document.documentElement.classList.contains('skin-paused')"), False)

    await p.tap("#cr-cash")
    await p.wait("!!document.querySelector('.scdp-r.cashout')", 10, "вывод: батискаф всплывает")
    seen_cashout = await p.ev(PARTICLES)
    check("вывод: сброс балласта (2-3 частицы)", 2 <= seen_cashout <= 3, True)
    await p.wait("document.querySelectorAll('.scdp-p.on').length === 0", 4, "частицы балласта упали")

    await server_to(w, p, fs + t_crash_ms(2500) + 150)
    await p.wait("!!document.querySelector('.scdp-r.broken')", 10, "краш: стекло треснуло")
    await p.wait("getComputedStyle(document.querySelector('.scdp-cracks')).strokeDashoffset === '0px'", 5, "трещины проявились")
    check("трещины видны", await p.ev("getComputedStyle(document.querySelector('.scdp-cracks')).strokeDashoffset"), "0px")
    seen = await p.ev(PARTICLES)
    check("краш: частицы-пузыри летят (от 1 до 12)", 1 <= seen <= 12, True)
    await p.wait("document.querySelectorAll('.scdp-p.on').length === 0", 4, "все пузыри вернулись в пул")
    check("через четыре секунды частиц 0", await p.ev(PARTICLES), 0)

    # --- раунд 2 (×1.50): prefers-reduced-motion: ни покоя, ни частиц, краш сменой кадра
    await next_round(w, p, fs, 2500)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    check("reduced-motion: стрелка манометра не дрожит", await p.ev(NEEDLE), "none")
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scdp-r.broken')", 10, "краш сменой кадра")
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
    check("perf-lite: стрелка не дрожит, параллакса нет", [await p.ev(NEEDLE), await p.ev("document.querySelector('.scdp-par').style.transform")], ["none", ""])
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("!!document.querySelector('.scdp-r.broken')", 10, "краш сменой кадра")
    check("perf-lite: частиц нет", await p.ev(PARTICLES), 0)

    # --- уход с экрана краша убирает сцену и css, возврат возвращает; снятый скин не оставляет ничего
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scdp-r, .scdp-p, .scdp-zone').length")], [0, 0, 0])
    await p.tap(".tab[data-tab=play]")
    await p.wait("!!document.querySelector('.scdp-r')", 10, "сцена вернулась")
    check("после возврата сцена одна", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'crash'", (uid,))
    await w.reload()
    await open_game(p, "crash")
    await p.wait(READY, 15, "панель ставки без скина")
    check("скин снят: сцены, css и декора в DOM нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scdp-r, .scdp-p, .scdp-zone').length"), await p.ev("document.getElementById('cr-chart').dataset.scene || ''")], [0, 0, 0, ""])
