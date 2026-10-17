"""Примерка скина в окне предпросмотра (UI_REVIEW, магазин): кнопка «Примерить» есть у слотов с игрой и нет у рамки и значка; примерка двигает образец
(фишка садится, карта переворачивается, клетка подсвечивается, мины открываются, шарики кено выбираются, график краша растёт через зоны),
«Стоп» возвращает образец, закрытие окна останавливает примерку, пересборка образца (смена вкладки) тоже."""
from harness import check

NAME = "wardrobe_tryon"
USERS = {"me": {"rate": 0}}
VIEWPORT = (390, 760)
CLOCK_MOD = 5

SLOTS = [  # (номер вкладки, код предмета, что должно появиться в образце за ~3.5 секунды)
    (5, "chip_ring", "document.querySelectorAll('#wd-prev-scene .stack.drop').length > 0"),
    (7, "back_midnight", "document.querySelectorAll('#wd-prev-scene .bj-card.flip').length > 0"),
    (4, "table_blue", "document.querySelectorAll('#wd-prev-scene .cell.hit').length > 0"),
    (6, "mine_star", "document.querySelectorAll('#wd-prev-scene .mines-cell.safe').length > 1"),
    (3, "keno_hex", "document.querySelectorAll('#wd-prev-scene .keno-ball.press').length > 0"),
    (1, "crash_neon", "/^×(?!1\\.00)/.test((document.querySelector('#wd-prev-scene .cr-mult') || {}).textContent || '')"),
]


async def run(w):
    p = w.page
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб открыт")
    for n, code, cond in SLOTS:
        await p.tap("#wd-tabs .wd-tab:nth-child(%d)" % n)
        await p.wait("!!document.querySelector('#wd-grid .wd-card[data-code=%s]')" % code, 5, "карточка " + code)
        await p.tap("#wd-grid .wd-card[data-code=%s]" % code)
        await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "окно предпросмотра " + code)
        check(code + ": кнопка «Примерить» есть", await p.ev("!document.getElementById('wd-prev-try').hidden && document.getElementById('wd-prev-try').textContent"), "Примерить")
        check(code + ": до примерки образец неподвижен", await p.ev(cond), False)
        await p.tap("#wd-prev-try")
        check(code + ": кнопка стала «Стоп»", await p.ev("document.getElementById('wd-prev-try').textContent"), "Стоп")
        await p.wait(cond, 8, code + ": образец двигается")
        await p.tap("#wd-prev-try")
        check(code + ": после «Стоп» образец собран заново", (await p.ev(cond), await p.ev("document.getElementById('wd-prev-try').textContent")), (False, "Примерить"))
        await p.tap("#wd-prev-try")
        await p.tap("#wd-prev-close")
        check(code + ": закрытие останавливает примерку", await p.ev("document.getElementById('wd-prev-sheet').hidden"), True)
    # у рамки и значка примерки нет
    await p.tap("#wd-tabs .wd-tab:nth-child(8)")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 5, "рамки")
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "окно рамки")
    check("у рамки примерки нет", await p.ev("document.getElementById('wd-prev-try').hidden"), True)
