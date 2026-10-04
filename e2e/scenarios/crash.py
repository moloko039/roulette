"""Краш: авто-вывод на ×2 при точке ×50 (выигрыш), при точке ×1.50 (проигрыш), ручной вывод до краха."""
from harness import check, open_game, set_bet, shown

NAME = "crash"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках
BANNER = "!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('%s')"


async def start(p, target):
    await set_bet(p, "cr-bet", 100)
    await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = %r; i.dispatchEvent(new Event('input')); })()" % target)
    await p.tap("#cr-start")


async def run(w):
    p = w.page
    w.server.script(crash=[5000, 150, 100000])
    await open_game(p, "crash")
    await p.wait("!document.getElementById('cr-bets').hidden", 10, "панель ставки")
    await start(p, "2")
    await p.wait(BANNER % "win", 20, "авто-вывод на ×2 выиграл")
    await p.wait("!document.getElementById('cr-bets').hidden && !document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("выигрыш ×2: баланс 100100", await shown(p, "#cr-balance"), 100100)
    await start(p, "2")
    await p.wait(BANNER % "lose", 20, "точка ×1.50 ниже цели: проигрыш")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("проигрыш: баланс 100000", await shown(p, "#cr-balance"), 100000)
    await start(p, "")      # ручной режим, точка краха ×1000: раунд не обрывается
    await p.wait("!document.getElementById('cr-actions').hidden && !document.getElementById('cr-cash').disabled", 15, "кнопка «Забрать»")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.05", 20, "множитель вырос")
    await p.tap("#cr-cash")
    await p.wait(BANNER % "win", 20, "ручной вывод выиграл")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("ручной вывод: баланс вырос", await shown(p, "#cr-balance") > 100000, True)
