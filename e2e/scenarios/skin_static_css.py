"""Рамки и значки видны в рейтинге и профиле на любом экране, поэтому их css не привязан к сцене игры и подключается всегда.
Раньше skins/badge_patina.css и другие грузились только по мета-тегу при открытии игры со сценой: у слотов avatar_frame и badge сцен нет,
значок «Патина» и рамки не рисовались в рейтинге, а сценарии скрывали это, подключая css вручную. Проверяем настоящую загрузку: по одной
статической ссылке на файл и файл реально попал в document.styleSheets."""
from harness import check

NAME = "skin_static_css"
USERS = {"me": {"rate": 0}}
FILES = ("frame_patina", "badge_patina", "void_frame", "void_badge")


async def run(w):
    p = w.page
    await w.reload()
    for name in FILES:
        links = await p.ev("document.querySelectorAll('link[rel=stylesheet][href*=\"skins/%s.css\"]').length" % name)
        check("%s: статическая ссылка на css в index.html (одна)" % name, links, 1)
        loaded = await p.ev("[...document.styleSheets].some((s) => s.href && s.href.includes('skins/%s.css'))" % name)
        check("%s: css загружен без открытия игры" % name, loaded, True)
