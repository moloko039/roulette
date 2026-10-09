"""Общее ядро клиента: реестр игр (ядро видит занятую игру и откладывает запрос баланса), защита от повторной загрузки и от двойного действия.
Проверки идут через сами функции страницы (anyRoundBusy, loadHilo, hlAct) и счётчик запросов."""
from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, server_to
from harness import check, open_game, set_bet

NAME = "shared_core"
USERS = {"me": {"rate": 0}}


async def run(w):
    p = w.page
    w.server.script(spin=[17], crash_live=[25000], hilo=[[7, "H"], [7, "S"], [3, "C"]])

    # 1. рулетка: пока колесо крутится, ядро считает игру занятой и запрос баланса откладывается
    await open_game(p, "roulette")
    check("до раунда игры не заняты", await p.ev("anyRoundBusy()"), False)
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    await p.tap("#spin")
    await p.wait("document.querySelector('.wheel-layer.active')", 10, "колесо крутится")
    check("во время вращения ядро видит занятую игру", await p.ev("anyRoundBusy()"), True)
    await p.ev("E.sleep(5500)")          # между запросами /api/me должно пройти не меньше 5 секунд: иначе запрос не уходит и без проверки занятости
    await p.tap(".tab[data-tab=profile]")
    before = await p.ev("E.count('/api/me')")
    await p.ev("loadServer('manual')")
    await p.ev("E.sleep(700)")
    check("запрос баланса во время вращения отложен", await p.ev("E.count('/api/me')"), before)
    await p.wait("!anyRoundBusy()", 15, "вращение закончилось")
    await p.wait("E.count('/api/me') > %d" % before, 10, "отложенный запрос баланса ушёл после раунда")

    # 2. краш: ставка с автовыводом летит до цели в общем раунде, ручной вывод идёт запросом (ответ задержан): пока запрос в пути, игра занята
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await bet(p, 100, "50")
    fs = await flight_start(p)
    await server_to(w, p, fs + 2500)
    await p.wait(CASH_VISIBLE, 15, "раунд идёт, кнопка «Забрать»")
    check("краш: во время полёта без запроса игра не занята", await p.ev("anyRoundBusy()"), False)
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.05", 20, "множитель вырос")
    await p.ev("window.fetch = ((orig) => (u, o) => String(u).includes('/api/crash/live/cashout') ? orig(u, o).then((r) => new Promise((res) => setTimeout(() => res(r), 1500))) : orig(u, o))(window.fetch)")
    await p.tap("#cr-cash")
    check("краш: пока запрос вывода в пути, игра считается занятой", await p.ev("anyRoundBusy()"), True)
    await p.wait("!anyRoundBusy()", 30, "запрос вывода завершён")

    # 3. хило: защита от повторной загрузки и двойного действия, пока ход не завершён
    await open_game(p, "hilo")
    await p.wait("!document.getElementById('hl-bets').hidden", 10, "панель ставки")
    await set_bet(p, "hl-bet", 100)
    await p.tap("#hl-start")
    await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия началась")
    check("между ходами загрузка состояния планируется (контроль)", await p.ev(
        "(() => { const t = hl.timer; loadHilo('manual'); const changed = hl.timer !== t; clearTimeout(hl.timer); hl.timer = t; return changed; })()"), True)
    guesses = await p.ev("E.count('/api/hilo/guess')")
    await p.tap("#hl-hi")
    check("ход идёт: игра занята", await p.ev("hl.busy"), True)
    check("во время хода состояние не загружается", await p.ev("(() => { const t = hl.timer; loadHilo('manual'); return hl.timer === t; })()"), True)
    await p.ev("hlAct('/api/hilo/guess', { choice: 'hi' }, 'hi')")      # повторное действие во время хода
    await p.ev("E.sleep(400)")
    check("повторное действие во время хода не отправлено", await p.ev("E.count('/api/hilo/guess')"), guesses + 1)
    await p.wait("!hl.busy", 15, "ход завершён")
