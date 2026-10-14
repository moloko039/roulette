"""Общий забег свинок на настоящем сервере: режим «Общий», таймер приёма, ставка на дорожку (лента и число ставок), забег по сценарию сервера,
итог совпадает с выплатой, баланс на экране совпадает с сервером; в соло-режиме опрос раунда не идёт."""
import time

from harness import check, open_game, set_bet

NAME = "race_live"
USERS = {"me": {"rate": 0}}


async def run(w):
    p = w.page
    await w.reload()
    await open_game(p, "race")
    check("режим по умолчанию одиночный", await p.ev("document.querySelector('[data-race-mode=solo]').getAttribute('aria-pressed')"), "true")
    check("панель раунда скрыта", await p.ev("document.getElementById('race-live').hidden"), True)
    start_balance = await p.ev("Number(document.getElementById('race-balance').textContent.replace(/\\s/g, ''))")
    await p.tap("[data-race-mode=live]")
    await p.wait("document.querySelectorAll('.race-lane').length === 4", 10, "в общем режиме четыре дорожки")
    check("панель раунда видна, выбор дорожек скрыт", await p.ev("[document.getElementById('race-live').hidden, getComputedStyle(document.querySelector('.race-lanes')).display]"), [False, "none"])
    # ждём начала приёма ставок с запасом времени: ставка не должна попасть на закрытие
    await p.wait("document.getElementById('race-live-phase').textContent === 'Приём ставок' && parseInt(document.getElementById('race-live-time').textContent) >= 9", 40, "приём ставок идёт")
    check("до выбора дорожки кнопка просит выбрать", await p.ev("document.getElementById('race-play').textContent.trim()"), "Выберите дорожку")
    await p.tap(".race-lane[data-lane='1']")
    await set_bet(p, "race-bet", 10)
    check("кнопка со ставкой", await p.ev("document.getElementById('race-play').textContent.trim()"), "Поставить · 10")
    await p.tap("#race-play")
    await p.wait("document.getElementById('race-play').textContent.startsWith('Ставка принята')", 10, "ставка принята")
    check("ставка в ленте и на дорожке", await p.ev("[document.querySelector('#race-feed li.me') !== null, document.querySelector(\".race-lane[data-lane='1'] .race-crowd\").textContent]"), [True, "1"])
    check("баланс списан сразу", await p.ev("Number(document.getElementById('race-balance').textContent.replace(/\\s/g, ''))"), start_balance - 10)
    check("вторая ставка недоступна", await p.ev("document.getElementById('race-play').disabled"), True)
    # забег: результат появляется только после анимации, победитель подсвечен, баланс = сервер
    await p.wait("document.getElementById('race-result').hidden === false", 45, "итог общего забега")
    title = await p.ev("document.getElementById('race-result-title').textContent.trim()")
    check("итог — выигрыш или проигрыш", title.startswith("Выигрыш") or title == "Проигрыш", True)
    check("подсвечена одна победившая дорожка", await p.ev("document.querySelectorAll('.race-lane.win').length"), 1)
    await p.wait("document.getElementById('race-balance').textContent.length > 0", 5)
    bal = await p.ev("Number(document.getElementById('race-balance').textContent.replace(/\\s/g, ''))")
    check("баланс: проигрыш или выплата 38", bal in (start_balance - 10, start_balance - 10 + 38), True)
    check("выплата совпадает с итогом", (bal == start_balance - 10 + 38) == title.startswith("Выигрыш"), True)
    # новый раунд: выбор снова доступен, прошлый итог убран
    await p.wait("document.getElementById('race-live-phase').textContent === 'Приём ставок' && document.getElementById('race-result').hidden === true", 40, "следующий раунд")
    check("в новом раунде ставка свободна", await p.ev("document.getElementById('race-play').textContent.trim()"), "Выберите дорожку")
    # одиночный режим: панель раунда убрана, опрос остановлен
    await p.tap("[data-race-mode=solo]")
    n = await p.ev("window.E ? E.count('/api/race/live') : -1")
    time.sleep(2.5)
    check("в одиночном режиме опрос раунда остановлен", (await p.ev("window.E ? E.count('/api/race/live') : -1")) - n <= 1, True)
    check("панель раунда скрыта снова", await p.ev("document.getElementById('race-live').hidden"), True)
