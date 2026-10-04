"""Минутное начисление при подмененных часах сервера: тик приходит по таймеру, баланс растёт, «+N»; скрытая вкладка не опрашивает."""
import asyncio

from harness import check, shown

NAME = "accrual_tick"
USERS = {"me": {"rate": 1500, "balance": 100000}}
CLOCK_MOD = 56          # на сервере секунда 56: до минутной границы остаётся 4 секунды


async def run(w):
    p = w.page
    check("блок дохода в лобби: баланс до тика", await shown(p, "#lobby-balance") >= 100000, True)
    # 1) плановый тик при видимой вкладке: запрос уходит сам, баланс растёт на кратное 25, показан «+N»
    first = await p.ev("E.meCount()")
    await p.wait("E.meCount() > %d" % first, 30, "плановый минутный запрос")
    await p.wait("E.pops.length >= 1", 10, "«+N» у баланса")
    await p.wait("document.getElementById('lobby-balance').textContent.replace(/\\D/g, '') > 100000", 10, "баланс вырос")
    gained = await shown(p, "#lobby-balance") - 100000
    check("прирост кратен минутному доходу (25)", gained % 25, 0)
    check("«+N» совпадает с приростом", int((await p.ev("E.pops[0]")).replace("+", "").replace(" ", "").replace(" ", "")) <= gained, True)

    # 2) экран фермы показывает доход
    await p.tap(".tab[data-tab=farm]")
    await p.wait("!document.getElementById('farm-income').hidden", 10, "блок дохода на ферме")
    check("доход в час и в минуту", await p.ev("[document.getElementById('farm-inc-hour').textContent.replace(/\\D/g, ''), document.getElementById('farm-inc-min').textContent]"),
          ["1500", "25.0"])

    # 3) скрытая вкладка не опрашивает: страница открывается заново, до тика 4 с, вкладка скрывается до него
    await w.reload()
    await p.ev("E.setVis('hidden')")
    seen = await p.ev("E.meCount()")
    w.server.offset(int(w.server.offset_file and open(w.server.offset_file).read()) + 600)    # на сервере прошло ещё 10 минут
    await asyncio.sleep(7)       # дольше, чем до планового запроса (граница минуты + 1 с)
    check("скрытая вкладка запросов не шлёт", await p.ev("E.meCount()"), seen)
    pops = await p.ev("E.pops.length")
    await p.ev("E.setVis('visible')")
    await p.wait("E.meCount() > %d" % seen, 8, "при возврате сразу обновление")
    await p.wait("E.pops.length > %d" % pops, 8, "«+N» после возврата")
