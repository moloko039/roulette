"""Блок «Коллекции» в профиле: лента карточек, переключение на сетку «все →», открытие шторки с сеткой частей, закрытие по крестику, открытие шторки по openCollection из магазина."""
from harness import check

NAME = "collections_profile"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5
SP = "(s) => s.replace(/\\s/g, ' ')"


async def run(w):
    p = w.page
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('collections-card').hidden", 10, "блок коллекций в профиле")

    check("блок коллекций виден", await p.ev("!document.getElementById('collections-card').hidden"), True)

    # три карточки в порядке сервера: Листопад, Дачный сезон, Основатель
    card_names = await p.ev("[...document.querySelectorAll('#collections-feed .collection-card .col-card-name')].map(e => e.textContent)")
    check("карточки коллекций в порядке сервера", card_names, ["Листопад", "Дачный сезон", "Основатель"])

    # сегменты по числу частей
    segments = await p.ev("[...document.querySelectorAll('#collections-feed .collection-card')].map(c => c.querySelectorAll('.col-segment').length)")
    check("сегменты по числу частей (Листопад: 8, Дачный: 8, Основатель: 3)", segments, [8, 8, 3])

    # переключение сетки «все →»
    check("вид по умолчанию не расширен", await p.ev("document.getElementById('collections-all-btn').getAttribute('aria-expanded')"), "false")
    check("сетка не активна изначально", await p.ev("document.getElementById('collections-card').classList.contains('grid-view')"), False)
    await p.tap("#collections-all-btn")
    check("после тапа «все →» aria-expanded true", await p.ev("document.getElementById('collections-all-btn').getAttribute('aria-expanded')"), "true")
    check("сетка активна", await p.ev("document.getElementById('collections-card').classList.contains('grid-view')"), True)
    await p.tap("#collections-all-btn")
    check("повторный тап возвращает ленту", await p.ev("document.getElementById('collections-all-btn').getAttribute('aria-expanded')"), "false")

    # тап по карточке открывает шторку с сеткой частей и подписью «Как получить»
    await p.tap("#collections-feed .collection-card:nth-child(1)")
    await p.wait("!document.getElementById('collection-sheet').hidden", 5, "шторка открыта")
    check("заголовок шторки — Листопад", await p.ev("document.getElementById('collection-title').textContent"), "Листопад")
    check("строка прогресса", await p.ev("document.getElementById('collection-progress').textContent"), "Собрано 0 из 8")

    part_count = await p.ev("document.querySelectorAll('#collection-grid .collection-part').length")
    check("в шторке 8 частей", part_count, 8)

    check("у неполученных частей подпись «Как получить»", await p.ev("document.querySelector('#collection-grid .collection-part.unowned').textContent.includes('Как получить')"), True)

    # закрытие по крестику
    await p.tap("#collection-close")
    await p.wait("document.getElementById('collection-sheet').hidden", 5, "шторка закрыта")
    check("шторка закрылась", await p.ev("document.getElementById('collection-sheet').hidden"), True)

    # openCollection('leaves') из магазина открывает шторку в профиле
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden", 5, "магазин")
    await p.ev("openCollection('leaves')")
    await p.wait("!document.querySelector('[data-screen=profile]').hidden && !document.getElementById('collection-sheet').hidden", 5, "шторка Листопада открыта из магазина")
    check("openCollection переключил на профиль и открыл шторку", await p.ev("[!document.querySelector('[data-screen=profile]').hidden, !document.getElementById('collection-sheet').hidden, document.getElementById('collection-title').textContent]"), [True, True, "Листопад"])

    await p.tap("#collection-close")
    await p.wait("document.getElementById('collection-sheet').hidden", 5, "шторка закрыта")
