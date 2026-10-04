"""Мины: весь экран (форма старта, партия, итог) без прокрутки на ширинах 320/360/390/430 и высотах 568/640/700/740/800;
«Забрать» целиком видна и не перекрыта выступающей центральной кнопкой навигации; все клетки нажимаемы; множители «текущий → следующий»."""
import json

from harness import check, open_game, set_bet

NAME = "mines_layout"
USERS = {"me": {"rate": 0}}
WIDTHS = [320, 360, 390, 430]
HEIGHTS = [568, 640, 700, 740, 800]
MINES = [0, 1, 2]
SCREEN = "document.querySelector('[data-screen=mines]')"

# сводка по экрану: прокрутка, горизонтальный скролл, перекрытия
STATE = """(() => {
  const s = %s, d = document.documentElement;
  return {scroll: [s.scrollHeight - s.clientHeight, d.scrollHeight - d.clientHeight], hs: E.hs(), overflow: E.overflow()};
})()""" % SCREEN

# верхний элемент в нескольких точках кнопки и в центре каждой клетки
HITS = """(() => {
  const vw = innerWidth, vh = innerHeight, out = {cash: [], cells: 0, cellsTotal: 0};
  const cash = document.getElementById('mines-cash'), r = cash.getBoundingClientRect();
  out.cashInView = r.left >= 0 && r.right <= vw && r.top >= 0 && r.bottom <= vh;
  for (const fx of [0.05, 0.5, 0.95]) for (const fy of [0.1, 0.5, 0.9]) {
    const el = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
    out.cash.push(!!el && (el === cash || cash.contains(el)));
  }
  const main = document.querySelector('.tab.main').getBoundingClientRect();
  out.gap = Math.round(main.top - r.bottom);
  for (const c of document.querySelectorAll('#mines-grid .mines-cell')) {
    const b = c.getBoundingClientRect(), el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    out.cellsTotal++; if (el === c || c.contains(el)) out.cells++;
  }
  const g = document.getElementById('mines-grid').getBoundingClientRect();
  out.cell = Math.round(g.width / 5);
  return out;
})()"""

MULTS = """(() => { const t = (id) => document.getElementById(id), vis = (e) => !e.hidden && getComputedStyle(e).display !== 'none';
  return {now: t('mines-mult').textContent.trim(), next: vis(t('mines-next')) ? t('mines-next-mult').textContent.trim() : null,
          arrow: vis(t('mines-arrow')), font: parseFloat(getComputedStyle(t('mines-mult')).fontSize),
          server: [mn.game ? mn.game.multiplier : null, mn.game ? mn.game.next_multiplier : null]}; })()"""


async def no_scroll(p, label):
    await p.settle(3)
    st = await p.ev(STATE)
    check("без вертикальной прокрутки (экран, документ): " + label, st["scroll"], [0, 0])
    check("без горизонтальной прокрутки: " + label, [st["hs"], st["overflow"]], [0, []])


async def fits(p, label):
    await no_scroll(p, label)
    h = await p.ev(HITS)
    check("«Забрать» целиком в видимой области: " + label, h["cashInView"], True)
    check("«Забрать» не перекрыта: " + label, h["cash"], [True] * 9)
    check("все клетки нажимаемы: " + label, [h["cells"], h["cellsTotal"]], [25, 25])
    if h["gap"] < 0:
        raise AssertionError("«Забрать» заходит под центральную кнопку навигации (%d px): %s" % (h["gap"], label))
    return h


async def run(w):
    p = w.page
    w.server.script(mines=[MINES] * 60)
    sizes = {}
    first = True
    for width in WIDTHS:
        for height in HEIGHTS:
            label = "%dx%d" % (width, height)
            await p.viewport(width, height)
            if first:
                await open_game(p, "mines")
                first = False
            await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта " + label)
            await no_scroll(p, "до старта " + label)

            await set_bet(p, "mines-bet", 100)
            await p.tap("#mines-begin")
            await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25 && !document.getElementById('mines-cash').disabled", 10, "поле " + label)
            await p.settle(3)
            h = await fits(p, "партия, до открытия " + label)
            sizes[label] = h["cell"]
            m = await p.ev(MULTS)
            check("до открытия: текущий ×1.00 и следующий с сервера " + label, [m["now"], m["next"], m["arrow"]], ["×1.00", "×" + str(m["server"][1]), True])
            check("до открытия: текущий с сервера " + label, m["now"], "×" + str(m["server"][0]))
            if width == 390:
                if m["font"] < 20:
                    raise AssertionError("множитель мельче 20 px: %r" % m["font"])
            before = m["next"]

            await p.tap("#mines-grid .mines-cell:nth-child(13)")
            await p.wait("document.getElementById('mines-mult').textContent.trim() !== '×1.00'", 10, "множитель сдвинулся " + label)
            await p.wait("!document.getElementById('mines-cash').disabled", 10, "«Забрать» доступна " + label)
            await fits(p, "партия, после открытия " + label)
            m = await p.ev(MULTS)
            check("после открытия: текущий = прежний следующий " + label, m["now"], before)
            check("после открытия: следующий с сервера " + label, m["next"], "×" + str(m["server"][1]))

            await p.tap("#mines-cash")
            await p.wait("!document.getElementById('mines-result').hidden", 10, "итог " + label)
            await no_scroll(p, "после выплаты " + label)
            check("после выплаты множители скрыты " + label, await p.ev("document.getElementById('mines-mults').hidden"), True)
            check("«Играть снова» видна и не перекрыта " + label, await p.ev(
                "(() => { const b = document.getElementById('mines-again'), r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
                " return r.bottom <= innerHeight && (e === b || b.contains(e)); })()"), True)

            await p.tap("#mines-again")
            await p.wait("!document.getElementById('mines-begin').disabled", 10, "форма старта " + label)
            await set_bet(p, "mines-bet", 100)
            await p.tap("#mines-begin")
            await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25 && !document.getElementById('mines-cash').disabled", 10, "поле " + label)
            await p.tap("#mines-grid .mines-cell:nth-child(1)")
            await p.wait("!document.getElementById('mines-result').hidden", 10, "итог: мина " + label)
            await p.wait("document.querySelectorAll('#mines-grid .mines-cell.mine').length > 0", 10, "мины показаны " + label)
            await no_scroll(p, "после проигрыша " + label)
            await p.tap("#mines-again")
    print("размер ячейки, px:", json.dumps(sizes))
