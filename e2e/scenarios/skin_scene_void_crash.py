"""Сцены скинов (DESIGN.md раздел 5): скин краша «Пустота» (сцена skins/void_crash.js).
Проверяется:
1. Сцена и её css подгружаются при открытии экрана краша с надетым скином и исчезают из DOM при уходе с экрана и при снятии скина;
2. Линия графика 1 px, белая, без декора; множитель огромный тонким начертанием;
3. Зоны по множителю: на ×2 линия 1.5 px, на ×10 — 2 px, на ×50 — фон еле заметно светлеет к графиту (#111111);
   во время переходов в DOM не больше двух зон;
4. Событие «cashout» (забрать): сумма выигрыша появляется тёплым белым #F5EBDD и задерживается;
5. Событие «crash» (crash:crash): линия обрывается, число исчезает мгновенно, на месте обрыва остаётся точка, которая тихо гаснет за 1 с;
6. prefers-reduced-motion и perf-lite без движения (смена кадра)."""
import time

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "skin_scene_void_crash"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('.skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/void_crash.css\"]').length"
META_JS = "document.querySelectorAll('meta[name=\"skin-js\"][data-code=\"void_crash\"]').length"
META_CSS = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_crash\"]').length"
ZONES = "[...document.querySelectorAll('.scvc-zone')].map(g => g.dataset.zone)"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def at_x(w, p, fs, ms):
    await server_to(w, p, fs + ms)
    await p.wait("crServerNow() >= %d" % (fs + ms - 100), 10, "серверное время дошло")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 1. Проверяем наличие мета-тегов в index.html
    check("в index.html есть meta skin-js data-code=void_crash", await p.ev(META_JS), 1)
    check("в index.html есть meta skin-css data-code=void_crash", await p.ev(META_CSS), 1)

    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'void_crash', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'crash', 'void_crash')", (uid,))
    w.server.script(crash_live=[6000, 150, 150, 150])
    await w.reload()

    # Отключаем монитор кадров для детерминированности сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("до открытия краша сцены и css скина в DOM нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.wait("!!document.querySelector('.scvc-svg')", 10, "сцена смонтирована")
    check("сцена одна, css скина подключён, у графика метка сцены", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('cr-chart').dataset.scene")], [1, 1, "void_crash"])
    await p.wait("!document.getElementById('cr-chart').hidden", 10, "график виден")

    # Проверка начального состояния: линия 1 px белая, множитель тонким начертанием
    stroke_init = await p.ev("getComputedStyle(document.getElementById('cr-curve')).strokeWidth")
    check("линия графика в начале 1 px", stroke_init, "1px")
    mult_weight = await p.ev("parseInt(getComputedStyle(document.getElementById('cr-mult')).fontWeight, 10)")
    check("множитель тонким начертанием (<= 300)", mult_weight <= 300, True)

    # --- раунд 1 (точка ×60.00): зоны по множителю, вывод, краш
    await bet(p, 100, "")
    fs = await flight_start(p)

    # Зона 0 (< ×2): линия 1 px
    await at_x(w, p, fs, 1800)
    check("×1.2: зона 0", await p.ev(ZONES), ["0"])
    check("зона 0: линия 1 px", await p.ev("getComputedStyle(document.getElementById('cr-curve')).strokeWidth"), "1px")

    # Зона 1 (×2..×10): линия 1.5 px
    await at_x(w, p, fs, 6200)
    await p.wait("getComputedStyle(document.getElementById('cr-curve')).strokeWidth === '1.5px'", 8, "пришла зона 1, линия 1.5 px")
    check("во время перехода в DOM не больше двух зон", await p.ev("document.querySelectorAll('.scvc-zone').length") <= 2, True)
    await p.wait("document.querySelectorAll('.scvc-zone').length === 1", 5, "переход зон закончен")
    check("×2: зона 1, линия 1.5 px", [await p.ev(ZONES), await p.ev("getComputedStyle(document.getElementById('cr-curve')).strokeWidth")], [["1"], "1.5px"])

    # Зона 2 (×10..×50): линия 2 px
    await at_x(w, p, fs, 20500)
    await p.wait("getComputedStyle(document.getElementById('cr-curve')).strokeWidth === '2px'", 8, "пришла зона 2, линия 2 px")
    await p.wait("document.querySelectorAll('.scvc-zone').length === 1", 5, "переход зон 2 закончен")
    check("×10: зона 2, линия 2 px", [await p.ev(ZONES), await p.ev("getComputedStyle(document.getElementById('cr-curve')).strokeWidth")], [["2"], "2px"])

    # Зона 3 (×50+): линия 2 px, фон еле заметно светлеет к графиту #111111
    await at_x(w, p, fs, 34500)
    await p.wait("getComputedStyle(document.getElementById('cr-chart')).backgroundColor === 'rgb(17, 17, 17)'", 8, "фон графит #111111")
    await p.wait("document.querySelectorAll('.scvc-zone').length === 1", 5, "переход зон 3 закончен")
    check("×50: зона 3, фон посветлел к графиту", [await p.ev(ZONES), await p.ev("getComputedStyle(document.getElementById('cr-chart')).backgroundColor")], [["3"], "rgb(17, 17, 17)"])

    # Событие «cashout» (забрать): сумма выигрыша появляется тёплым белым #F5EBDD и задерживается
    await p.wait(CASH_VISIBLE, 10, "кнопка «Забрать»")
    await p.tap("#cr-cash")
    await p.wait("!!document.querySelector('.scvc-cash-mark')", 10, "вывод: точка отмечена на графике")
    await p.wait("document.querySelector('.scvc-win').classList.contains('show')", 10, "выигрыш отображён")
    win_color = await p.ev("getComputedStyle(document.querySelector('.scvc-win-text')).color")
    check("выигрыш тёплым белым #F5EBDD", win_color, "rgb(245, 235, 221)")

    # Пауза при скрытии страницы
    await p.ev("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    check("страница скрыта: класс паузы", await p.ev("document.documentElement.classList.contains('skin-paused')"), True)
    await p.ev("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    check("страница снова видна: пауза снята", await p.ev("document.documentElement.classList.contains('skin-paused')"), False)

    # Событие «crash» (crash:crash): линия обрывается, число исчезает мгновенно, точка гаснет за 1 с
    await server_to(w, p, fs + t_crash_ms(6000) + 150)
    await p.wait("document.getElementById('cr-chart').classList.contains('cr-void-crashed')", 10, "краш зафиксирован")
    check("число исчезает мгновенно", await p.ev("getComputedStyle(document.getElementById('cr-mult')).opacity"), "0")
    check("линия обрывается", await p.ev("getComputedStyle(document.getElementById('cr-curve')).opacity"), "0")
    await p.wait("document.querySelectorAll('.scvc-dot').length === 1", 5, "на месте обрыва остаётся точка")
    check("точка на месте обрыва белая", await p.ev("document.querySelector('.scvc-dot').getAttribute('fill')"), "#FFFFFF")
    await p.wait("document.querySelectorAll('.scvc-dot').length === 0", 4, "точка тихо гаснет за 1 с")
    check("точка погасла через 1 с", await p.ev("document.querySelectorAll('.scvc-dot').length"), 0)

    # --- раунд 2 (×1.50): prefers-reduced-motion: краш сменой кадра, без зависших точек
    await next_round(w, p, fs, 6000)
    await p.send("Emulation.setEmulatedMedia", STILL)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await at_x(w, p, fs, 2500)
    await server_to(w, p, fs + t_crash_ms(150) + 150)
    await p.wait("document.getElementById('cr-chart').classList.contains('cr-void-crashed')", 10, "краш сменой кадра")
    check("reduced-motion: точки после краха нет", await p.ev("document.querySelectorAll('.scvc-dot').length"), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- переключение вкладок убирает сцену и css, возврат восстанавливает
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scvc-svg, .scvc-win').length")], [0, 0, 0])
    await p.tap(".tab[data-tab=play]")
    await p.wait("!!document.querySelector('.scvc-svg')", 10, "сцена вернулась")
    check("после возврата сцена одна", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])

    # --- снятие скина очищает DOM
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'crash'", (uid,))
    await w.reload()
    await open_game(p, "crash")
    await p.wait(READY, 15, "панель ставки без скина")
    check("скин снят: сцены, css и декора в DOM нет", [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.querySelectorAll('.scvc-svg, .scvc-win').length"), await p.ev("document.getElementById('cr-chart').dataset.scene || ''")], [0, 0, 0, ""])
