"""Рулетка: «Пропустить» сразу показывает результат (колесо уже знает число), баланс меняется только после остановки, выпавшее число
подсвечено на столе; внешние ставки собраны компактным рядом над сеткой, сводка ставок заполняется по ходу."""
from harness import check, open_game, set_bet, shown

NAME = "roulette_skip"
USERS = {"me": {"rate": 0}}


async def run(w):
    p = w.page
    w.server.script(spin=[17])
    await open_game(p, "roulette")
    check("сводка ставок до ставки", await p.ev("document.getElementById('table-summary').textContent"), "Ставок: 0 · Сумма: 0")
    check("внешние ставки выше нуля и чисел", await p.ev("(() => { const o = document.querySelector('.outside-row').getBoundingClientRect(); const z = document.querySelector(\"#table .cell[data-key='number:0']\").getBoundingClientRect(); const n = document.querySelector(\"#table .cell[data-key='number:1']\").getBoundingClientRect(); return o.bottom <= z.top + 1 && z.bottom <= n.top + 1; })()"), True)
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    check("сводка после ставки", await p.ev("document.getElementById('table-summary').textContent"), "Ставок: 1 · Сумма: 10")
    await p.tap("#spin")
    await p.wait("document.querySelector('.wheel-layer.active') && !document.getElementById('wheel-skip').hidden", 10, "колесо крутится, есть «Пропустить»")
    await p.tap("#wheel-skip")
    await p.wait("document.getElementById('layer-result').textContent.trim() === '17'", 3, "после «Пропустить» результат показан сразу")
    check("кнопка «Пропустить» скрыта после остановки", await p.ev("document.getElementById('wheel-skip').hidden"), True)
    await p.wait("!document.querySelector('.wheel-layer.active') && !document.getElementById('spin').disabled", 10, "колесо убрано")
    check("число 17 подсвечено на столе", await p.ev("document.querySelectorAll('#table .cell.hit').length + ':' + (document.querySelector('#table .cell.hit') || {dataset: {}}).dataset.key"), "1:number:17")
    check("выигрыш выплачен", await shown(p, "#balance"), 100350)
    await p.tap("#table .cell[data-key='number:5']")
    check("подсветка снята новой ставкой", await p.ev("document.querySelectorAll('#table .cell.hit').length"), 0)
