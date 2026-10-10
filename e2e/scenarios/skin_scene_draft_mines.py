"""Сцена скина мин «Черновик» (skins/draft_mines.css, коллекция «Черновик»).
Проверяется: сцена монтируется в #mines-board-wrap при открытии мин с надетым скином, css скина подключён;
уход на другую вкладку и снятие скина убирают сцену и css;
открытая безопасная клетка получает анимацию появления (крестик синей ручкой);
prefers-reduced-motion и perf-lite выключают анимацию (animationName === 'none');
при забирании выигрыша крестики получают анимацию волны;
взрыв даёт анимацию лучей-штрихов на .mine.hit."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_draft_mines"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#mines-board-wrap .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/draft_mines.css\"]').length"
DATA_SCENE = "document.getElementById('mines-board-wrap').dataset.scene || ''"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

SAFE_ANIM = "getComputedStyle(document.querySelector('#mines-grid .mines-cell.safe'), '::before').animationName"
HIT_ANIM = "getComputedStyle(document.querySelector('#mines-grid .mines-cell.mine.hit'), '::before').animationName"
WAVE_ANIM = "getComputedStyle(document.querySelector('#mines-grid .mines-cell.safe'), '::before').animationName"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'draft_mines', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'mine_icons', 'draft_mines')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана мин сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта")
    check("экран открыт: css подключён, data-scene установлена, сцена смонтирована",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, 1, "draft_mines"])

    # --- Раунд 1: открываем безопасную клетку и забираем выигрыш ---
    w.server.script(mines=[[0, 1, 2], [0, 1, 2]])
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле 5x5")

    # Открытие безопасной клетки (клетка 13)
    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("document.querySelector('#mines-grid .mines-cell:nth-child(13)').classList.contains('safe')", 10, "клетка 13 открыта")
    safe_name = await p.ev(SAFE_ANIM)
    check("открытая безопасная клетка получает анимацию появления", safe_name != "none" and "cross-pop" in safe_name, True)

    # prefers-reduced-motion отключает анимацию
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("prefers-reduced-motion: getComputedStyle(...).animationName === 'none'", await p.ev(SAFE_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)
    check("prefers-reduced-motion снят: анимация активна", await p.ev(SAFE_ANIM) != "none", True)

    # perf-lite отключает анимацию
    await p.ev("skinSetLite()")
    check("perf-lite: getComputedStyle(...).animationName === 'none'", await p.ev(SAFE_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")
    check("perf-lite снят: анимация активна", await p.ev(SAFE_ANIM) != "none", True)

    # Забираем выигрыш: крестики получают анимацию волны
    await p.wait("!document.getElementById('mines-cash').disabled", 10, "кнопка «Забрать» доступна")
    await p.tap("#mines-cash")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "итог игры: выигрыш")
    check("статус итога cashed", await p.ev("document.getElementById('mines-board-wrap').dataset.status"), "cashed")
    wave_name = await p.ev(WAVE_ANIM)
    check("при забирании выигрыша крестики получают анимацию волны", wave_name != "none" and "cross-wave" in wave_name, True)

    # --- Раунд 2: наступаем на мину (взрыв) ---
    await p.tap("#mines-again")
    await p.wait("!document.getElementById('mines-begin').disabled", 10, "форма старта раунда 2")
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле раунда 2")

    # Клетка 1 — мина (взрыв)
    await p.tap("#mines-grid .mines-cell:nth-child(1)")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "итог: мина")
    check("клетка 1 получила класс hit", await p.ev("document.querySelector('#mines-grid .mines-cell:nth-child(1)').classList.contains('hit')"), True)
    hit_name = await p.ev(HIT_ANIM)
    check("взрыв даёт анимацию на .mine.hit", hit_name != "none" and "blast-pop" in hit_name, True)

    # prefers-reduced-motion и perf-lite на взрыве
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("взрыв: prefers-reduced-motion отключает анимацию", await p.ev(HIT_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    await p.ev("skinSetLite()")
    check("взрыв: perf-lite отключает анимацию", await p.ev(HIT_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")

    # --- Переключение вкладок: уход убирает сцену и css, возврат восстанавливает ---
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята", await p.ev(DATA_SCENE), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=mines]').hidden", 5, "возврат в мины")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)], [1, 1, "draft_mines"])

    # --- Снятие скина: в DOM ничего не остаётся ---
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'mine_icons'", (uid,))
    await w.reload()
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "мины без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev(DATA_SCENE)],
          [0, 0, ""])
