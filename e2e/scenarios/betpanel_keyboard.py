"""Тапы при открытой «клавиатуре» (подмена visualViewport): «×2»/«÷2» в рулетке действуют один раз и не ставят красное.
Баг, который чинили: тап по кнопке снимал фокус, раскладка менялась и клик попадал в другой элемент.
(Прежние проверки окна перевода удалены вместе с переводами, этап E1.)"""
from harness import check

NAME = "betpanel_keyboard"


async def run(w):
    p = w.page
    # --- рулетка: «×2» и «÷2» при клавиатуре действуют один раз и не ставят красное
    await p.wait("!document.querySelector('[data-screen=lobby]').hidden", 5, "лобби")
    await p.tap(".lobby-card[data-game=roulette]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "рулетка")
    start = int(await p.ev("document.getElementById('amount').value"))
    await p.ev("document.getElementById('amount').focus()")
    await p.wait("E.kbOpen && E.kbHeight > 0 && document.querySelector('#bets.docked')", 5, "панель рулетки над клавиатурой")
    await p.tap("#amount-double")
    check("×2: значение удвоено один раз", int(await p.ev("document.getElementById('amount').value")), start * 2)
    check("×2: ставок на столе нет, клавиатура закрыта", await p.ev("[document.querySelectorAll('#table .stack').length, E.kbOpen]"), [0, False])
    await p.ev("document.getElementById('amount').focus()")
    await p.wait("E.kbOpen && E.kbHeight > 0 && document.querySelector('#bets.docked')", 5, "панель рулетки над клавиатурой")
    await p.tap("#amount-half")
    check("÷2: значение вернулось", int(await p.ev("document.getElementById('amount').value")), start)
    check("÷2: ставок на столе нет", await p.ev("document.querySelectorAll('#table .stack').length"), 0)
