"""Сцена скина фишки «Жемчуг» (skins/chip_pearl.css, коллекция «Глубина»).
Проверяется: при открытии экрана рулетки с надетым скином link на skins/chip_pearl.css подключён,
у body установлен data-scene="chip_pearl";
на экране другой вкладки (рейтинг) их нет;
снятие скина не оставляет следов в DOM;
у .chip применён стиль скина (getComputedStyle(...).backgroundImage содержит url или gradient);
при поставленной ставке у .stack.drop активна анимация pearl-drop (не 'none');
при prefers-reduced-motion и perf-lite animationName === 'none'."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_chip_pearl"
USERS = {"me": {"rate": 0}}
LINK = "document.querySelectorAll('link[href*=\"skins/chip_pearl.css\"]').length"
DATA_SCENE = "document.body.dataset.scene || ''"
CHIP_BG = "getComputedStyle(document.querySelector('.chip')).backgroundImage"
STACK_ANIM = "getComputedStyle(document.querySelector('#table .stack.drop')).animationName"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'chip_pearl', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'chip', 'chip_pearl')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана рулетки link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "roulette")
    await p.wait("document.getElementById('spin') && !document.getElementById('spin').disabled", 10, "экран рулетки")
    check("экран рулетки открыт: css подключён, у body data-scene='chip_pearl'",
          [await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, "chip_pearl"])

    # Проверка применения стиля скина к .chip (фоновое изображение содержит url или gradient)
    chip_bg = await p.ev(CHIP_BG)
    check("у .chip применён стиль скина (backgroundImage содержит url или gradient)",
          "url" in chip_bg or "gradient" in chip_bg, True)

    # Ставка на столе рулетки (число 17, ставка 10)
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    await p.wait("document.querySelectorAll('#table .stack.drop').length === 1", 5, "ставка поставлена со стопкой .stack.drop")

    # Анимация .stack.drop активна и равна 'pearl-drop'
    drop_anim = await p.ev(STACK_ANIM)
    check("у .stack.drop анимация не 'none' и равна pearl-drop",
          drop_anim != "none" and "pearl-drop" in drop_anim, True)

    # prefers-reduced-motion отключает анимацию
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("prefers-reduced-motion: animationName === 'none'", await p.ev(STACK_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)
    check("prefers-reduced-motion снят: анимация активна", await p.ev(STACK_ANIM) != "none", True)

    # perf-lite отключает анимацию
    await p.ev("skinSetLite()")
    check("perf-lite: animationName === 'none'", await p.ev(STACK_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")
    check("perf-lite снят: анимация активна", await p.ev(STACK_ANIM) != "none", True)

    # Переключение вкладок: на экране рейтинга link и data-scene отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране другой вкладки (рейтинг) link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    # Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "возврат в рулетку")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "chip_pearl"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'chip'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.getElementById('spin') && !document.getElementById('spin').disabled", 10, "рулетка без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
