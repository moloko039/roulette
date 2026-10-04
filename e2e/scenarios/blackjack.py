"""Блэкджек: колода задана (у игрока 20, у дилера 17), «Стоп», победа 1:1, баланс после анимации."""
from harness import check, open_game, set_bet, shown

NAME = "blackjack"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках


async def run(w):
    p = w.page
    w.server.script(shoe=[["10S", "9H", "10D", "8C"]])    # порядок раздачи: игрок, дилер, игрок, дилер
    await open_game(p, "blackjack")
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled", 15, "панель действий")
    check("у игрока 20, скрытая карта дилера закрыта", await p.ev("[document.getElementById('bj-player-total').textContent.trim(), "
                                                                  "document.querySelectorAll('#bj-dealer-cards .bj-card.back').length]"), ["20", 1])
    check("баланс после ставки", await shown(p, "#bj-balance"), 99900)
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden && document.getElementById('bj-banner').classList.contains('win')", 20, "победа")
    check("дилер остановился на 17", await p.ev("document.getElementById('bj-dealer-total').textContent.trim()"), "17")
    check("выплата 2 x ставка: баланс 100100", await shown(p, "#bj-balance"), 100100)
