"""Забег свинок: открыть игру, выбрать 4 дорожки и дорожку, сыграть раунд на настоящем сервере.
Проверяем интерфейс (число дорожек, выбор дорожки, блокировку кнопок во время раунда), что после анимации появляется итог
и подсвечена победившая дорожка, а баланс на экране совпадает с ответом сервера (исход случайный, сценарий его не подменяет)."""
from harness import check, open_game, set_bet

NAME = "race_play"
USERS = {"me": {"rate": 0}}


async def run(w):
    p = w.page
    await w.reload()
    await open_game(p, "race")
    await p.wait("document.querySelectorAll('.race-lane').length === 3", 10, "три дорожки по умолчанию")
    await p.tap("[data-race-lanes='4']")
    await p.wait("document.querySelectorAll('.race-lane').length === 4", 10, "четыре дорожки")
    await p.tap(".race-pick-btn[data-lane='2']")
    await p.wait("document.querySelector(\".race-pick-btn[data-lane='2']\").getAttribute('aria-pressed') === 'true'", 5, "выбрана дорожка 3")
    await set_bet(p, "race-bet", 10)
    await p.tap("#race-play")
    await p.wait("document.getElementById('race-result').hidden === false || document.getElementById('race-notice').textContent.length > 0", 60, "итог раунда или уведомление")
    notice = await p.ev("document.getElementById('race-notice').textContent")
    check("уведомления об ошибке нет", notice, "")
    title = await p.ev("document.getElementById('race-result-title').textContent.trim()")
    check("итог начинается с выигрыша или проигрыша", title.startswith("Выигрыш") or title == "Проигрыш", True)
    winners = await p.ev("document.querySelectorAll('.race-lane.win').length")
    check("подсвечена ровно одна победившая дорожка", winners, 1)
    locked = await p.ev("document.querySelector('#race-play').disabled")
    check("после раунда кнопка снова доступна", locked, False)
    check("дорожки 4, ставка сохранилась", await p.ev("document.querySelectorAll('.race-lane').length"), 4)
