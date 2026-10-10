"""Сцена скина стола «Черновик» (skins/draft_table.css, коллекция «Черновик»).
Проверяется: при открытии экрана рулетки с надетым скином link на skins/draft_table.css подключён,
у #table установлен data-scene="draft_table";
у стола применён фон (миллиметровка, кофе, поля), у ::before скрепка, у ::after загнутый уголок;
на экране другой вкладки (рейтинг) их нет;
снятие скина не оставляет следов в DOM;
клетки кликабельны под скином."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_draft_table"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#table .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/draft_table.css\"]').length"
DATA_SCENE = "document.getElementById('table') ? document.getElementById('table').dataset.scene || '' : ''"
TABLE_BG = "getComputedStyle(document.getElementById('table')).backgroundImage"
BEFORE_BG = "getComputedStyle(document.getElementById('table'), '::before').backgroundImage"
AFTER_BG = "getComputedStyle(document.getElementById('table'), '::after').backgroundImage"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'draft_table', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'table', 'draft_table')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана рулетки link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "стол рулетки загружен")
    await p.wait("!!document.querySelector('#table[data-scene=\"draft_table\"]')", 10, "сцена стола смонтирована")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("экран рулетки открыт: css подключён, у #table data-scene='draft_table'",
          [await p.ev(LINK), await p.ev(DATA_SCENE), await p.ev(SCENE)],
          [1, "draft_table", 1])

    # Проверка применения фона стола (миллиметровка, кофе, поля)
    table_bg = await p.ev(TABLE_BG)
    check("у стола фон применён (backgroundImage содержит url или gradient)", "url" in table_bg or "gradient" in table_bg, True)

    # Проверка псевдоэлементов ::before (скрепка) и ::after (уголок)
    before_bg = await p.ev(BEFORE_BG)
    check("у ::before фон применён (скрепка url)", "url" in before_bg, True)

    after_bg = await p.ev(AFTER_BG)
    check("у ::after фон применён (загнутый уголок gradient)", "gradient" in after_bg, True)

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
    await p.wait("!!document.querySelector('#table[data-scene=\"draft_table\"]')", 10, "сцена стола вернулась")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "draft_table"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'table'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "рулетка без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
