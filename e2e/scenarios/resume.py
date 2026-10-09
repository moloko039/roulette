"""Возобновление: активная партия (мины, блэкджек, хило, краш) открывается сама после перезагрузки страницы, лобби пропускается."""
from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, server_to
from harness import check, open_game, set_bet

NAME = "resume"


async def gone_to(p, game):
    await p.wait("!document.querySelector('[data-screen=%s]').hidden" % game, 15, "после перезагрузки открылась игра " + game)
    check("титульный экран пропущен (" + game + ")", await p.ev("document.querySelector('[data-screen=lobby]').hidden"), True)


async def run(w):
    p = w.page
    w.server.script(mines=[[0, 1, 2]], shoe=[["10S", "9H", "10D", "8C"]], hilo=[[7, "H"]], crash_live=[25000])
    # у каждой следующей игры своя секунда на сервере: «последняя по действию» определяется временем
    await open_game(p, "mines")
    await p.wait("!document.getElementById('mines-begin').disabled", 10, "форма мин")
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25 && E.count('/api/mines/start') === 1", 10, "партия в мины")
    await w.reload()
    await gone_to(p, "mines")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25 && document.getElementById('mines-start').hidden", 10, "партия восстановлена")

    w.server.offset(10)
    await open_game(p, "blackjack")
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled", 15, "раздача")
    await w.reload()
    await gone_to(p, "blackjack")
    await p.wait("!document.getElementById('bj-actions').hidden && document.getElementById('bj-player-total').textContent.trim() === '20'", 10, "раздача восстановлена")

    w.server.offset(20)
    await open_game(p, "hilo")
    await p.wait("!document.getElementById('hl-bets').hidden", 10, "панель хило")
    await set_bet(p, "hl-bet", 100)
    await p.tap("#hl-start")
    await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия в хило")
    await w.reload()
    await gone_to(p, "hilo")
    await p.wait("!document.getElementById('hl-actions').hidden && document.getElementById('hl-card').getAttribute('aria-label') === '7 червей'", 10, "партия восстановлена")

    await open_game(p, "crash")
    await p.wait(READY, 10, "панель краша")
    await bet(p, 100, "")
    await server_to(w, p, await flight_start(p) + 1500)
    await p.wait(CASH_VISIBLE, 15, "полёт идёт")
    await w.reload()
    await gone_to(p, "crash")
    await p.wait(CASH_VISIBLE, 15, "раунд краша продолжается")
