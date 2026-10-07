"""Лобби: появляется один раз за запуск, порядок игр, меню игр и переключение."""
from harness import check

NAME = "lobby"
ORDER = ["crash", "roulette", "keno", "mines", "hilo", "blackjack", "arcade"]


async def run(w):
    p = w.page
    visible = "[...document.querySelectorAll('.screen:not([hidden])')].map(e => e.dataset.screen).join()"
    check("при запуске виден только титульный экран", await p.ev(visible), "lobby")
    check("порядок игр на титульном экране", await p.ev("[...document.querySelectorAll('.lobby-card')].map(e => e.dataset.game)"), ORDER)
    check("баланс в лобби", await p.ev("document.getElementById('lobby-balance').textContent.replace(/\\s/g, '')"), "100000")
    check("все игры доступны (заглушек нет)", await p.ev("[...document.querySelectorAll('.lobby-card')].every(e => e.dataset.soon === 'false')"), True)

    await p.tap(".lobby-card[data-game=hilo]")
    await p.wait("document.querySelector('[data-screen=hilo]') && !document.querySelector('[data-screen=hilo]').hidden", 5, "экран хило")
    check("после выбора игры лобби скрыто", await p.ev("document.querySelector('[data-screen=lobby]').hidden"), True)

    await p.tap(".tab.main")
    await p.wait("document.getElementById('game-menu').classList.contains('open')", 5, "меню игр")
    check("порядок игр в меню", await p.ev("[...document.querySelectorAll('#game-grid .tile')].map(e => e.dataset.game)"), ORDER)
    await p.tap(".tile[data-game=keno]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "экран кено")
    check("меню закрылось, виден только кено", await p.ev(visible), "keno")
    check("лобби не возвращается", await p.ev("document.querySelector('[data-screen=lobby]').hidden"), True)

    await p.tap(".tab.main")
    await p.wait("document.getElementById('game-menu').classList.contains('open')", 5, "меню игр")
    await p.tap(".tab.main")
    await p.wait("!document.getElementById('game-menu').classList.contains('open')", 5, "меню закрыто повторным нажатием")

    await w.reload()
    check("после перезапуска лобби снова один раз", await p.ev(visible), "lobby")
