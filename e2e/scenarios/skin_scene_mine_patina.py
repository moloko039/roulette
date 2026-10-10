"""Сцена скина значков мин «Патина» (skins/mine_patina.css, коллекция «Патина», DESIGN.md раздел 8).
Проверяется:
1. При открытии экрана мин с надетым скином link на skins/mine_patina.css подключён,
   у контейнера поля установлен data-scene="mine_patina";
2. До открытия игры и на другой вкладке (рейтинг) link и data-scene отсутствуют;
3. Возврат на экран игры восстанавливает link и data-scene;
4. Снятие скина не оставляет следов в DOM;
5. У клетки применён стиль скина (латунная фактура в ::before содержит gradient или url);
6. На стадии 3 есть трещины (SVG data-URI и linear-gradient);
7. Стадия 0 без патины: клетка без трещин;
8. Контраст элементов клетки не ниже 3:1."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_mine_patina"
USERS = {"me": {"rate": 0}}
LINK = "document.querySelectorAll('link[href*=\"skins/mine_patina.css\"]').length"
DATA_SCENE = "(document.getElementById('mines-board-wrap') && document.getElementById('mines-board-wrap').dataset.scene) || ''"
CELL_BRASS_BG = "getComputedStyle(document.querySelector('.mines-cell'), '::before').backgroundImage"
CELL_WEAR_BG = "getComputedStyle(document.querySelector('.mines-cell'), '::after').backgroundImage"

CONTRAST_JS = """(() => {
  const parse = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const L = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const cell = document.querySelector('.mines-cell');
  const safeCell = document.querySelector('.mines-cell.safe') || cell;
  const safeBorder = parse(getComputedStyle(safeCell).borderTopColor);
  const cellBg = parse(getComputedStyle(cell).backgroundColor);
  const safeText = parse(getComputedStyle(safeCell).color);
  const safeBg = parse(getComputedStyle(safeCell).backgroundColor);
  return Math.min(ratio(safeBorder, cellBg), ratio(safeText, safeBg));
})()"""


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 120 взрывов дают стадию износа 3 для мин (пороги: 10, 40, 120, 300)
    w.sql(
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<=120) "
        "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at, finished_at) "
        "SELECT ?, 10, 3, 7, 'lost', ?, ?, ? FROM cnt",
        (uid, now, now, now),
    )
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'mine_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'mine_icons', 'mine_patina')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана мин link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта")
    check("экран мин открыт: css подключён, у mines-board-wrap data-scene='mine_patina'",
          [await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, "mine_patina"])

    check("на корне выставлен атрибут data-skin-mine_icons='mine_patina'",
          await p.ev("document.documentElement.getAttribute('data-skin-mine_icons')"),
          "mine_patina")

    # Проверка стадии износа при 120 взрывах
    check("120 взрывов дают стадию data-patina-mine_icons='3'",
          await p.ev("document.documentElement.getAttribute('data-patina-mine_icons')"),
          "3")

    # Старт раунда в минах
    w.server.script(mines=[[0, 1, 2], [0, 1, 2]])
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле 5x5")

    # Открываем безопасную клетку (клетка 13)
    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("document.querySelector('#mines-grid .mines-cell:nth-child(13)').classList.contains('safe')", 10, "клетка 13 открыта")

    # Проверка применения стиля скина к клеткам (латунная фактура в ::before)
    cell_brass = await p.ev(CELL_BRASS_BG)
    check("у клетки применён стиль скина (латунная фактура в backgroundImage ::before)",
          "gradient" in cell_brass or "url" in cell_brass,
          True)

    # Проверка наличия трещин на стадии 3
    after_bg_3 = await p.ev(CELL_WEAR_BG)
    check("на стадии 3 есть трещины (SVG data-URI)",
          "url" in after_bg_3 or "linear-gradient" in after_bg_3,
          True)

    # Контраст не падает ниже 3:1
    contrast_3 = await p.ev(CONTRAST_JS)
    check("на стадии 3 контраст элементов не ниже 3:1",
          contrast_3 >= 3.0,
          True)

    # Забираем выигрыш (Забрать)
    await p.wait("!document.getElementById('mines-cash').disabled", 10, "кнопка «Забрать» доступна")
    await p.tap("#mines-cash")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "раунд завершён")

    # Проверка стадии 0: 0 взрывов дают стадию 0 без трещин
    w.sql("DELETE FROM mines_games WHERE telegram_id = ?", (uid,))
    w.sql("DELETE FROM mines_actions WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта (стадия 0)")

    check("стадия 0: data-patina-mine_icons равен '0'",
          await p.ev("document.documentElement.getAttribute('data-patina-mine_icons')"),
          "0")

    w.server.script(mines=[[0, 1, 2], [0, 1, 2]])
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле 5x5 (стадия 0)")

    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("document.querySelector('#mines-grid .mines-cell:nth-child(13)').classList.contains('safe')", 10, "клетка 13 открыта (стадия 0)")

    after_bg_0 = await p.ev(CELL_WEAR_BG)
    check("стадия 0: клетка без трещин (url и linear-gradient отсутствуют)",
          "url" not in after_bg_0 and "linear-gradient" not in after_bg_0,
          True)

    contrast_0 = await p.ev(CONTRAST_JS)
    check("на стадии 0 контраст элементов не ниже 3:1",
          contrast_0 >= 3.0,
          True)

    # Завершаем раунд стадии 0
    await p.wait("!document.getElementById('mines-cash').disabled", 10, "кнопка «Забрать» доступна (стадия 0)")
    await p.tap("#mines-cash")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "раунд стадии 0 завершён")

    # Переключение вкладок: на экране рейтинга link и data-scene отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране другой вкладки (рейтинг) link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    # Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=mines]').hidden", 5, "возврат в мины")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "mine_patina"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'mine_icons'", (uid,))
    w.sql("DELETE FROM mines_games WHERE telegram_id = ?", (uid,))
    w.sql("DELETE FROM mines_actions WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "мины без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
