"""Рамка и значок в рейтинге: у других видны только когда игрок разрешил показ и надел предмет; при выключенном показе не видны; у себя видны
всегда; высота строки и размер аватара не меняются; длинное имя усекается многоточием."""
import time

from harness import check

NAME = "wardrobe_rating"
LONG = "Очень длинное имя участника беседы для проверки усечения"
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}, "carol": {"balance": 4000, "rate": 0}, LONG: {"balance": 3000, "rate": 0}}

ROWS = """(() => [...document.querySelectorAll('#rating-list li')].map((li) => ({
  name: li.querySelector('.rating-name').textContent, h: Math.round(li.getBoundingClientRect().height * 100) / 100,
  av: [li.querySelector('.avatar').getBoundingClientRect().width, li.querySelector('.avatar').getBoundingClientRect().height],
  frame: li.querySelector('.avatar').getAttribute('data-skin-avatar_frame'), badge: (li.querySelector('.rating-badge') || {}).dataset ? li.querySelector('.rating-badge').dataset.skinBadge : null,
  cut: li.querySelector('.rating-name').scrollWidth > li.querySelector('.rating-name').clientWidth })))()"""


def give(w, uid, items, show=True):
    now = int(time.time())
    for slot, code in items:
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))
        w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
    if not show:
        w.sql("INSERT INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 0)", (uid,))


async def open_rating(w):
    p = w.page
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 4", 15, "рейтинг")
    await p.settle(3)
    return {r["name"]: r for r in await p.ev(ROWS)}


async def run(w):
    p = w.page
    ids = {n: u.id for n, u in w.users.items()}
    base = await open_rating(w)
    check("до надевания рамок и значков нет", {(r["frame"], r["badge"]) for r in base.values()}, {(None, None)})
    check("длинное имя усечено многоточием", base[LONG]["cut"], True)
    both = [("avatar_frame", "frame_thin"), ("badge", "badge_spade")]
    give(w, ids["bob"], both)                                   # разрешил показ (по умолчанию)
    give(w, ids["carol"], both, show=False)                     # скрыл показ
    give(w, ids[LONG], both)
    give(w, ids["me"], both)
    # приватные слоты других в ответ не попадают и в DOM их нет
    give(w, ids["bob"], [("chip", "chip_ring"), ("table", "table_blue")])
    await w.reload()
    rows = await open_rating(w)
    check("bob: рамка и значок видны", (rows["bob"]["frame"], rows["bob"]["badge"]), ("frame_thin", "badge_spade"))
    check("carol скрыла показ: ничего не видно", (rows["carol"]["frame"], rows["carol"]["badge"]), (None, None))
    check("у себя видны (строка в списке)", (rows["me"]["frame"], rows["me"]["badge"]), ("frame_thin", "badge_spade"))
    mine = await p.ev("(() => { const m = document.getElementById('rating-me'); return [m.querySelector('.avatar').getAttribute('data-skin-avatar_frame'), !!m.querySelector('.rating-badge')]; })()")
    check("у себя видны (закреплённая строка)", mine, ["frame_thin", True])
    check("приватные слоты других не попадают в страницу", await p.ev("document.documentElement.outerHTML.includes('chip_ring') || document.documentElement.outerHTML.includes('table_blue')"), False)
    check("высота строк и размер аватара не изменились", [(r["h"], r["av"]) for n, r in sorted(rows.items())], [(r["h"], r["av"]) for n, r in sorted(base.items())])
    check("длинное имя по-прежнему усечено, значок виден", (rows[LONG]["cut"], rows[LONG]["badge"]), (True, "badge_spade"))
    # свой показ в профиле
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль")
    check("рамка и значок в профиле", await p.ev("[document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame'), !document.getElementById('profile-badge').hidden]"), ["frame_thin", True])
    check("размер аватара в профиле прежний", await p.ev("[document.getElementById('profile-avatar').getBoundingClientRect().width, document.getElementById('profile-avatar').getBoundingClientRect().height]"), [44, 44])

    # 4.4 Значок собранных коллекций в рейтинге («✦ N») и профиле
    now = int(time.time())
    for part in ("ref_scout", "ref_beacon", "ref_arch"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'referral', ?)", (ids["bob"], part, now))
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'referral', ?)", (ids["me"], part, now))
    await w.reload()
    await open_rating(w)
    bob_badge_text = await p.ev("(() => { const li = [...document.querySelectorAll('#rating-list li')].find(li => li.querySelector('.rating-name').textContent === 'bob'); return li && li.querySelector('.rating-set-badge') ? li.querySelector('.rating-set-badge').textContent : null; })()")
    check("bob: значок числа коллекций в рейтинге ✦ 1", bob_badge_text, "✦ 1")
    bob_badge_aria = await p.ev("(() => { const li = [...document.querySelectorAll('#rating-list li')].find(li => li.querySelector('.rating-name').textContent === 'bob'); return li && li.querySelector('.rating-set-badge') ? li.querySelector('.rating-set-badge').getAttribute('aria-label') : null; })()")
    check("bob: aria-label значка коллекций", bob_badge_aria, "Собрано коллекций: 1")

    # нажатие по чужому значку в рейтинге: тост «Собрана коллекция: Название»
    await p.ev("(() => { const li = [...document.querySelectorAll('#rating-list li')].find(li => li.querySelector('.rating-name').textContent === 'bob'); if (li) li.querySelector('.rating-set-badge').click(); })()")
    check("тост по чужому значку", await p.ev("document.getElementById('soon-toast').textContent"), "Собрана коллекция: Основатель")

    # значок у своей строки в рейтинге
    my_badge_text = await p.ev("(() => { const li = document.querySelector('#rating-list li.me'); return li && li.querySelector('.rating-set-badge') ? li.querySelector('.rating-set-badge').textContent : null; })()")
    check("у себя: значок числа коллекций в рейтинге ✦ 1", my_badge_text, "✦ 1")

    # нажатие у своей строки открывает профиль
    await p.ev("document.querySelector('#rating-list li.me .rating-set-badge').click()")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "профиль по нажатию на свой значок")
    check("свой значок переключил на профиль", await p.ev("!document.querySelector('[data-screen=profile]').hidden"), True)

    # значок в шапке профиля рядом с именем
    check("значок в профиле виден", await p.ev("!document.getElementById('profile-set-badge').hidden"), True)
    check("текст значка в профиле ✦ 1", await p.ev("document.getElementById('profile-set-badge').textContent"), "✦ 1")
    check("aria-label значка в профиле", await p.ev("document.getElementById('profile-set-badge').getAttribute('aria-label')"), "Собрано коллекций: 1")
