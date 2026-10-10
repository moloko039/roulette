"""Сцена скина фишки «Патина» (skins/chip_patina.css, коллекция «Патина», DESIGN.md раздел 8).
Проверяется:
1. При открытии экрана рулетки с надетым скином link на skins/chip_patina.css подключён,
   у body установлен data-scene="chip_patina";
2. До открытия игры и на другой вкладке (рейтинг) link и data-scene отсутствуют;
3. Возврат на экран игры восстанавливает link и data-scene;
4. Снятие скина не оставляет следов в DOM;
5. У .chip применён стиль скина (гравировка в backgroundImage содержит url или gradient);
6. При 5 крашах >= x50: patina_info.chip_notches=5 ставит --patina-notches="5",
   стадия износа data-patina-chip="2", маска штрихов на ребре (.chip::after) охватывает 75deg;
7. Стадия 0 без патины: при 0 крашей data-patina-chip="0", --patina-notches="0",
   маска засечек даёт 0 штрихов (0deg)."""
import asyncio
import time

from harness import check, open_game

NAME = "skin_scene_chip_patina"
USERS = {"me": {"rate": 0}}
LINK = "document.querySelectorAll('link[href*=\"skins/chip_patina.css\"]').length"
DATA_SCENE = "document.body.dataset.scene || ''"
CHIP_BG = "getComputedStyle(document.querySelector('.chip')).backgroundImage"
CHIP_NOTCHES_VAR = "getComputedStyle(document.documentElement).getPropertyValue('--patina-notches').trim()"
CHIP_MASK = "(() => { const el = document.querySelector('.chip'); const cs = getComputedStyle(el, '::after'); return cs.maskImage || cs.webkitMaskImage || ''; })()"
CHIP_AFTER_BG = "getComputedStyle(document.querySelector('.chip'), '::after').backgroundImage"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # Пять завершённых крашей выше x50: 5 засечек, стадия износа 2 (пороги: 1, 5, 15, 40)
    for i in range(5):
        w.sql(
            "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, result, mult_x100, payout, auto, created_at, finished_at) "
            "VALUES (?, 10, 'manual', NULL, 6000, ?, 'finished', 'lose', 6000, 0, 0, ?, ?)",
            (uid, (now - 1000 + i) * 1000, now - 1000 + i, now - 900 + i),
        )
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'chip_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'chip', 'chip_patina')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана рулетки link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "roulette")
    await p.wait("document.getElementById('spin') && !document.getElementById('spin').disabled", 10, "экран рулетки")
    check("экран рулетки открыт: css подключён, у body data-scene='chip_patina'",
          [await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, "chip_patina"])

    check("на корне выставлен атрибут data-skin-chip='chip_patina'",
          await p.ev("document.documentElement.getAttribute('data-skin-chip')"),
          "chip_patina")

    # Проверка переменных патины и стадии износа при 5 крашах
    check("5 крашей выше x50 дают стадию data-patina-chip='2'",
          await p.ev("document.documentElement.getAttribute('data-patina-chip')"),
          "2")
    check("5 крашей выше x50 выставляют переменную --patina-notches='5'",
          await p.ev(CHIP_NOTCHES_VAR),
          "5")

    # Проверка применения стиля скина к .chip (гравировка в backgroundImage)
    chip_bg = await p.ev(CHIP_BG)
    check("у .chip применён стиль скина (гравировка и блик в backgroundImage)",
          "url" in chip_bg or "gradient" in chip_bg,
          True)

    # Проверка засечек на ребре (.chip::after): штрихи повторяющегося градиента и маска 75deg (5 * 15deg)
    chip_after_bg = await p.ev(CHIP_AFTER_BG)
    check("у .chip::after штрихи засечек (conic-gradient)",
          "conic-gradient" in chip_after_bg,
          True)
    mask5 = await p.ev(CHIP_MASK)
    check("при --patina-notches=5 маска засечек охватывает 75deg (5 штрихов)",
          "75deg" in mask5,
          True)

    # Проверка стадии 0 без патины: 0 крашей дают 0 засечек
    w.sql("DELETE FROM crash_games WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.getElementById('spin') && !document.getElementById('spin').disabled", 10, "экран рулетки (стадия 0)")

    check("стадия 0: data-patina-chip равен '0'",
          await p.ev("document.documentElement.getAttribute('data-patina-chip')"),
          "0")
    check("стадия 0: --patina-notches равен '0'",
          await p.ev(CHIP_NOTCHES_VAR),
          "0")
    mask0 = await p.ev(CHIP_MASK)
    check("стадия 0: маска даёт 0 штрихов (0deg вместо 75deg)",
          "0deg" in mask0 and "75deg" not in mask0,
          True)

    # Переключение вкладок: на экране рейтинга link и data-scene отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране другой вкладки (рейтинг) link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    # Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "возврат в рулетку")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "chip_patina"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'chip'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.getElementById('spin') && !document.getElementById('spin').disabled", 10, "рулетка без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
