"""Мины: раскладка задана сервером в сценарии. Раунд 1: открыть безопасную клетку и забрать; раунд 2: наступить на мину."""
from harness import check, open_game, set_bet, shown

NAME = "mines"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках
MINES = [0, 1, 2]


async def run(w):
    p = w.page
    w.server.script(mines=[MINES, MINES])
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта")
    await set_bet(p, "mines-bet", 100)
    check("мин по умолчанию 3", await p.ev("document.getElementById('mines-count').textContent.trim()"), "3")
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле 5x5")
    check("баланс после ставки (после анимации)", await shown(p, "#mines-balance"), 99900)
    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("!document.getElementById('mines-cash').disabled", 10, "кнопка «Забрать» доступна")
    await p.tap("#mines-cash")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "итог игры")
    check("выплата с одной клетки при 3 минах: 110", await shown(p, "#mines-balance"), 100010)

    await p.tap("#mines-again")
    await p.wait("!document.getElementById('mines-begin').disabled", 10, "форма старта")
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле 5x5")
    await p.tap("#mines-grid .mines-cell:nth-child(1)")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "итог: мина")
    check("проигрыш: ставка потеряна", await shown(p, "#mines-balance"), 99910)
