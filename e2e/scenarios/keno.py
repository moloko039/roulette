"""Кено: выбор трёх чисел, заданный розыгрыш (все три совпали), выплата по таблице сервера, баланс после анимации."""
import os
import sys

from harness import BOT, check, open_game, set_bet, shown

NAME = "keno"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках


async def run(w):
    sys.path.insert(0, BOT)
    import keno      # чистые правила (без базы): ожидаемая выплата считается теми же функциями, что на сервере
    p = w.page
    w.server.script(keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]])
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 чисел")
    for n in (1, 2, 3):
        await p.tap(".keno-ball:nth-child(%d)" % n)
    check("выбрано 3 числа", await p.ev("document.querySelectorAll('.keno-ball.sel').length"), 3)
    await set_bet(p, "keno-bet", 100)
    check("баланс до раунда", await shown(p, "#keno-balance"), 100000)
    await p.tap("#keno-play")
    await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 30, "итог раунда")
    expected = 100000 - 100 + keno.payout(100, 3, 3)
    await p.wait("!document.getElementById('keno-play').disabled", 30, "кнопка снова доступна")
    check("выплата по таблице: 3 из 3", await shown(p, "#keno-balance"), expected)
