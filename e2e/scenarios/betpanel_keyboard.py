"""Тапы при открытой «клавиатуре» (подмена visualViewport): «Отправить» в переводе, «×2»/«÷2» в рулетке, затемнение окна.
Баг, который чинили: тап по кнопке снимал фокус, раскладка менялась и клик попадал в затемнение, окно закрывалось."""
import asyncio

from harness import check

NAME = "betpanel_keyboard"
SHEET = "!document.getElementById('transfer-sheet').hidden"
SHEET_HIDDEN = "document.getElementById('transfer-sheet').hidden"


async def open_transfer(p):
    """Окно перевода игроку bob: открыть рейтинг и нажать на строку (настоящим касанием)."""
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 2", 10, "рейтинг беседы")
    await p.ev("[...document.querySelectorAll('#rating-list li')].find(l => l.textContent.includes('bob')).id = 'e2e-bob'")
    await p.tap("#e2e-bob")
    await p.wait(SHEET, 5, "окно перевода")


async def type_amount(p, value):
    await p.ev("(() => { const i = document.getElementById('transfer-amount'); i.value = '%s'; i.dispatchEvent(new Event('input')); i.focus(); })()" % value)
    await p.wait("E.kbOpen && E.kbHeight > 0 && document.querySelector('.transfer-panel.bets-dock.docked')", 5, "панель над клавиатурой")


async def run(w):
    p = w.page
    vw = w.viewport[0]
    top = {"x": vw / 2, "y": 50}     # затемнение над окном
    # --- перевод: «Отправить» при открытой клавиатуре
    await open_transfer(p)
    await type_amount(p, "500")
    await p.tap("#transfer-send")
    await p.wait("document.getElementById('transfer-msg').textContent.includes('Отправить 500')", 5, "подтверждение")
    check("окно не закрылось, клавиатура ушла, POST ещё нет", await p.ev("[%s, E.kbOpen, E.count('/api/transfers/send')]" % SHEET), [True, False, 0])
    await p.tap("#transfer-send")
    await p.wait("E.count('/api/transfers/send') === 1 && %s" % SHEET_HIDDEN, 10, "перевод отправлен, окно закрыто")
    check("перевод ушёл ровно один раз", await p.ev("E.count('/api/transfers/send')"), 1)

    # --- затемнение: тап при закрытой клавиатуре закрывает
    await open_transfer(p)
    await p.tap(top)
    await p.wait(SHEET_HIDDEN, 5, "тап по затемнению закрыл окно")
    # при открытой клавиатуре первый тап убирает только клавиатуру
    await open_transfer(p)
    await type_amount(p, "500")
    await p.tap(top)
    check("первый тап по затемнению: клавиатура ушла, окно осталось", await p.ev("[%s, E.kbOpen]" % SHEET), [True, False])
    await asyncio.sleep(0.6)     # правило «не раньше ~400 мс после смены вёрстки»
    await p.tap(top)
    await p.wait(SHEET_HIDDEN, 5, "второй тап закрыл окно")
    # перетаскивание с затемнения на окно и обратно не закрывает
    await open_transfer(p)
    panel = await p.ev("JSON.stringify(E.rect('.transfer-panel.bets-dock'))")
    import json
    r = json.loads(panel)
    inside = {"x": vw / 2, "y": (r["t"] + r["b"]) / 2}
    await p.drag(top, inside)
    check("нажатие на затемнении, отпускание на окне: окно осталось", await p.ev(SHEET), True)
    await p.drag({"x": vw / 2, "y": r["t"] + 8}, top)
    check("нажатие на окне, отпускание на затемнении: окно осталось", await p.ev(SHEET), True)
    await p.tap(top)
    await p.wait(SHEET_HIDDEN, 5, "обычный тап закрыл окно")

    # --- рулетка: «×2» и «÷2» при клавиатуре действуют один раз и не ставят красное
    await p.tap(".tab[data-tab=play]")
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
