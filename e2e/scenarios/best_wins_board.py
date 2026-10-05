"""Блок «Рекорды выигрыша» под основным рейтингом: виден под ним, пустое состояние «Пока нет рекордов», порядок по сумме, сумма «+1 250 000»
и название игры, длинное имя усечено, рамка и значок как в рейтинге, высота строк одинакова и не плывёт, нет горизонтальной прокрутки на 320 и 390,
рекорд игрока появляется после выигрыша в игре (кено) и совпадает с записью в базе."""
import os
import sys
import time

from harness import BOT, check, open_game, set_bet

NAME = "best_wins_board"
LONG = "Очень длинное имя участника беседы для проверки усечения"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}, "carol": {"balance": 4000, "rate": 0}, LONG: {"balance": 3000, "rate": 0}}

ROWS = """(() => [...document.querySelectorAll('#best-list li')].map((li) => {
  const n = li.querySelector('.rating-name'), r = li.getBoundingClientRect(), a = li.querySelector('.avatar');
  return { name: n.textContent, h: Math.round(r.height * 100) / 100, rank: li.querySelector('.rating-rank').textContent.trim() || (li.querySelector('.rating-rank svg') ? 'crown' : ''),
           amount: li.querySelector('.best-amount').textContent.replace(/\\s/g, ' '), game: li.querySelector('.rating-staked').textContent, me: li.classList.contains('me'),
           cut: n.scrollWidth > n.clientWidth, frame: a.getAttribute('data-skin-avatar_frame'), badge: li.querySelector('.rating-badge') ? li.querySelector('.rating-badge').dataset.skinBadge : null,
           avatar: [a.getBoundingClientRect().width, a.getBoundingClientRect().height] };
}))()"""
LAYOUT = """(() => {
  const best = document.getElementById('best-card').getBoundingClientRect(), main = document.getElementById('rating-card').getBoundingClientRect();
  const scr = document.querySelector('.screen[data-screen=rating]');
  return { below: best.top >= main.bottom - 0.5, inside: best.left >= 0 && best.right <= window.innerWidth, hscroll: document.documentElement.scrollWidth > window.innerWidth || scr.scrollWidth > scr.clientWidth,
           title: document.querySelector('#best-card .best-title').textContent, hidden: document.getElementById('best-card').hidden,
           mainRowH: Math.round(document.querySelector('#rating-list li').getBoundingClientRect().height * 100) / 100 };
})()"""


def give(w, uid, items, show=True):
    now = int(time.time())
    for slot, code in items:
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))
        w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
    if not show:
        w.sql("INSERT INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 0)", (uid,))


async def open_rating(p):
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 4", 15, "рейтинг")
    await p.wait("!document.getElementById('best-card').hidden", 15, "блок рекордов")
    await p.settle(3)


async def run(w):
    sys.path.insert(0, BOT)
    import keno
    p = w.page
    ids = {n: u.id for n, u in w.users.items()}
    # --- пустое состояние
    await open_rating(p)
    lay = await p.ev(LAYOUT)
    check("блок виден под основным рейтингом, заголовок", (lay["below"], lay["hidden"], lay["title"]), (True, False, "Рекорды выигрыша"))
    check("пустое состояние: текст виден, строк нет, своей строки нет", await p.ev(
        "[document.getElementById('best-empty').hidden, document.getElementById('best-empty').textContent, document.querySelectorAll('#best-list li').length, getComputedStyle(document.getElementById('best-me')).display]"),
        [False, "Пока нет рекордов", 0, "none"])
    # --- рекорды других игроков (записи в базе напрямую; сумма и игра приходят с сервера)
    now = int(time.time())
    for who, game, net, t in (("bob", "keno", 1250000, now - 300), (LONG, "roulette", 5000, now - 200), ("carol", "mines", 700, now - 100)):
        w.sql("INSERT INTO player_best_win (telegram_id, game, net_amount, achieved_at) VALUES (?, ?, ?, ?)", (ids[who], game, net, t))
    give(w, ids["bob"], [("avatar_frame", "frame_thin"), ("badge", "badge_spade")])
    give(w, ids["carol"], [("avatar_frame", "frame_thin"), ("badge", "badge_spade")], show=False)
    await w.reload()
    await open_rating(p)
    rows = await p.ev(ROWS)
    check("порядок по сумме, место", [(r["name"], r["rank"]) for r in rows], [("bob", "crown"), (LONG, "2"), ("carol", "3")])
    check("сумма «+1 250 000» и русское название игры", [(r["amount"], r["game"]) for r in rows], [("+1 250 000", "Кено"), ("+5 000", "Рулетка"), ("+700", "Мины")])
    check("длинное имя усечено, остальные нет", [r["cut"] for r in rows], [False, True, False])
    check("рамка и значок: у bob видны, у скрывшей carol нет", [(r["frame"], r["badge"]) for r in rows], [("frame_thin", "badge_spade"), (None, None), (None, None)])
    check("высоты строк одинаковы, аватары 36x36", [(r["h"], r["avatar"]) for r in rows], [(rows[0]["h"], [36, 36])] * 3)
    lay = await p.ev(LAYOUT)
    check("высота строки как в основном рейтинге", rows[0]["h"], lay["mainRowH"])
    check("нет горизонтальной прокрутки, блок в пределах экрана", (lay["hscroll"], lay["inside"], lay["below"]), (False, True, True))
    check("у меня нет рекорда: закреплённой строки нет, пустого состояния нет", await p.ev(
        "[getComputedStyle(document.getElementById('best-me')).display, document.getElementById('best-empty').hidden]"), ["none", True])
    # --- 320 px: то же без горизонтальной прокрутки, высота строк не плывёт
    await p.viewport(320, 700)
    await p.settle(3)
    lay = await p.ev(LAYOUT)
    rows320 = await p.ev(ROWS)
    check("320: нет горизонтальной прокрутки", (lay["hscroll"], lay["inside"]), (False, True))
    check("320: высоты строк прежние, длинное имя усечено", ([r["h"] for r in rows320], rows320[1]["cut"]), ([rows[0]["h"]] * 3, True))
    await p.viewport(390, 700)
    await p.settle(3)
    # --- выигрыш в игре: рекорд появляется (сервер пишет его в той же транзакции, что выплату)
    w.server.script(keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]])
    await w.reload()           # титульный экран (лобби) снова виден: из него открывается игра
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 чисел")
    for n in (1, 2, 3):
        await p.tap(".keno-ball:nth-child(%d)" % n)
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 30, "итог раунда")
    await p.wait("!document.getElementById('keno-play').disabled", 30, "кнопка снова доступна")
    want = keno.payout(100, 3, 3) - 100
    text = "+" + "{:,}".format(want).replace(",", " ")          # как formatNumber: разряды через пробел
    place = 1 + sum(1 for other in (1250000, 5000, 700) if other > want)
    check("в базе рекорд игрока = выплата - ставка", w.sql_value("SELECT net_amount || ' ' || game FROM player_best_win WHERE telegram_id = ?", (ids["me"],)), "%d keno" % want)
    await w.reload()
    await open_rating(p)
    await p.wait("document.querySelectorAll('#best-list li').length === 4", 15, "четыре рекорда")
    rows = await p.ev(ROWS)
    mine = [r for r in rows if r["me"]]
    check("моя строка в списке: сумма и игра с сервера", [(r["amount"], r["game"], r["rank"]) for r in mine], [(text, "Кено", str(place))])
    pinned = await p.ev("(() => { const m = document.getElementById('best-me'); return [m.querySelector('.rating-name').textContent, m.querySelector('.best-amount').textContent.replace(/\\s/g, ' '), m.querySelector('.rating-staked').textContent]; })()")
    check("закреплённая строка: «Вы», сумма, игра и место", pinned, ["Вы", text, "Кено, %d-е место из 4" % place])
    check("высоты строк по-прежнему одинаковы", len({r["h"] for r in rows}), 1)
