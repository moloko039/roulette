"""Сцена скина стола «Глубина» (skins/table_deep.css, коллекция «Глубина»).
Проверяется: при открытии экрана рулетки с надетым скином link на skins/table_deep.css подключён,
у #table установлен data-scene="table_deep";
у стола применён декор (песчаное дно с рябью, каустики, водоросли);
каустики и водоросли анимированы в покое (animationName не 'none');
при prefers-reduced-motion и perf-lite animationName === 'none';
клетки кликабельны под декором;
на экране другой вкладки (рейтинг) их нет;
снятие скина не оставляет следов в DOM."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_table_deep"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#table .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/table_deep.css\"]').length"
DATA_SCENE = "document.getElementById('table') ? document.getElementById('table').dataset.scene || '' : ''"
TABLE_BG = "getComputedStyle(document.getElementById('table')).backgroundImage"
CAUSTICS_BG = "getComputedStyle(document.getElementById('table'), '::before').backgroundImage"
CAUSTICS_ANIM = "getComputedStyle(document.getElementById('table'), '::before').animationName"
SEAWEED_BG = "getComputedStyle(document.getElementById('table'), '::after').backgroundImage"
SEAWEED_ANIM = "getComputedStyle(document.getElementById('table'), '::after').animationName"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'table_deep', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'table', 'table_deep')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана рулетки link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "стол рулетки загружен")
    await p.wait("!!document.querySelector('#table[data-scene=\"table_deep\"]')", 10, "сцена стола смонтирована")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("экран рулетки открыт: css подключён, у #table data-scene='table_deep'",
          [await p.ev(LINK), await p.ev(DATA_SCENE), await p.ev(SCENE)],
          [1, "table_deep", 1])

    # Проверка применения фона стола (песок с рябью)
    table_bg = await p.ev(TABLE_BG)
    check("у стола фон применён (backgroundImage содержит gradient)", "gradient" in table_bg, True)

    # Проверка псевдоэлементов ::before (каустики) и ::after (водоросли)
    caustics_bg = await p.ev(CAUSTICS_BG)
    check("у ::before фон применён (каустики url)", "url" in caustics_bg, True)

    seaweed_bg = await p.ev(SEAWEED_BG)
    check("у ::after фон применён (водоросли url)", "url" in seaweed_bg, True)

    # Каустики анимированы в покое (animationName не 'none')
    caustics_anim = await p.ev(CAUSTICS_ANIM)
    check("у стола каустики анимированы в покое (animationName не none)",
          caustics_anim != "none" and "table-deep-caustics" in caustics_anim, True)

    # Водоросли покачиваются в покое (animationName не 'none')
    seaweed_anim = await p.ev(SEAWEED_ANIM)
    check("у стола водоросли анимированы в покое (animationName не none)",
          seaweed_anim != "none" and "table-deep-seaweed" in seaweed_anim, True)

    # prefers-reduced-motion отключает анимацию
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("prefers-reduced-motion: animationName === 'none'", await p.ev(CAUSTICS_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)
    check("prefers-reduced-motion снят: каустики снова анимированы", await p.ev(CAUSTICS_ANIM) != "none", True)

    # perf-lite отключает анимацию
    await p.ev("skinSetLite()")
    check("perf-lite: animationName === 'none'", await p.ev(CAUSTICS_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")
    check("perf-lite снят: каустики снова анимированы", await p.ev(CAUSTICS_ANIM) != "none", True)

    # Клетки кликабельны: ставим ставку на число 17
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    await p.wait("document.querySelectorAll('#table .stack').length === 1", 5, "ставка поставлена на клетку стола")
    check("клетка кликабельна под декором стола", await p.ev("document.querySelectorAll('#table .stack').length"), 1)

    # Переключение вкладок: на экране рейтинга link и data-scene отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране другой вкладки (рейтинг) link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    # Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "возврат в рулетку")
    await p.wait("!!document.querySelector('#table[data-scene=\"table_deep\"]')", 10, "сцена стола вернулась")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "table_deep"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'table'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "рулетка без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
