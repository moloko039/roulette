"""Нет горизонтального скролла на ключевых экранах на ширинах 320/360/390/430: лобби, игры, профиль, ферма, рейтинг, окно перевода."""
from harness import check, open_game

NAME = "layout"
SIZES = [(320, 600), (360, 640), (390, 700), (430, 800)]
GAMES = ["crash", "roulette", "keno", "mines", "hilo", "blackjack"]
NO_SKELETON = "!document.querySelector('.screen:not([hidden]) .skeleton-card:not([hidden])')"


async def measure(p, label):
    await p.wait(NO_SKELETON, 15, "экран загружен: " + label)
    await p.settle(3)
    check("нет горизонтального скролла: " + label, await p.ev("[E.hs(), E.overflow()]"), [0, []])


async def run(w):
    p = w.page
    for width, height in SIZES:
        await p.viewport(width, height)
        await p.settle(3)
        await measure(p, "лобби %dx%d" % (width, height))
    for width, height in SIZES:
        await p.viewport(width, height)
        for game in GAMES:
            await open_game(p, game)
            await measure(p, "%s %dx%d" % (game, width, height))
        for tab in ("profile", "farm", "rating"):
            await p.tap(".tab[data-tab=%s]" % tab)
            await measure(p, "%s %dx%d" % (tab, width, height))
        await p.tap(".tab[data-tab=play]")
