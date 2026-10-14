"""Рекорд в профиле: строка «Рекорд» с суммой, игрой и множителем появляется, только когда у игрока есть выигрыш; у прежнего рекорда без множителя множителя нет."""
import time

from harness import check

NAME = "profile_best"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5


async def run(w):
    p = w.page
    uid = w.users["me"].id
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "профиль загружен")
    check("без выигрышей строки нет", await p.ev("document.getElementById('profile-best').hidden"), True)
    w.sql("INSERT OR REPLACE INTO player_best_win (telegram_id, game, net_amount, achieved_at, mult_x100) VALUES (?, 'keno', 12500, ?, 25000)", (uid, int(time.time())))
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-best').hidden", 10, "строка рекорда")
    check("рекорд: сумма, игра, множитель", await p.ev("document.getElementById('profile-best-value').textContent.trim()"), "+12,5 тыс. · Кено · ×250")
    w.sql("UPDATE player_best_win SET mult_x100 = NULL WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-best').hidden", 10, "строка рекорда без множителя")
    check("прежний рекорд без множителя", await p.ev("document.getElementById('profile-best-value').textContent.trim()"), "+12,5 тыс. · Кено")
