"""Рулетка: ставка на число, вращение с заданным числом, баланс на экране меняется только после анимации."""
from harness import check, open_game, set_bet, shown

NAME = "roulette"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках


async def run(w):
    p = w.page
    w.server.script(spin=[17])
    await open_game(p, "roulette")
    check("баланс до ставки", await shown(p, "#balance"), 100000)
    await set_bet(p, "amount", 10)     # фишка по умолчанию зависит от баланса, берём ставку 10
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    check("ставка 10 на число 17 на столе", await p.ev("document.querySelectorAll('#table .stack').length"), 1)
    check("доступный баланс уменьшился на ставку", await shown(p, "#balance"), 99990)
    await p.tap("#spin")
    await p.wait("document.querySelector('.wheel-layer.active')", 10, "колесо крутится")
    check("во время вращения баланс не меняется", await shown(p, "#balance"), 99990)
    await p.wait("!document.querySelector('.wheel-layer.active') && !document.getElementById('spin').disabled", 40, "вращение закончилось")
    check("выпало заданное число", await p.ev("document.getElementById('result-number').textContent.trim()"), "17")
    check("выигрыш 35:1 + ставка: 10 -> 360", await shown(p, "#balance"), 100350)
    check("ставки со стола убраны", await p.ev("document.querySelectorAll('#table .stack').length"), 0)
