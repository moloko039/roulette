"""Живой краш (общий раунд): автовывод на ×2 при точке ×50 (выигрыш), цель выше точки ×1.50 (проигрыш), ручной вывод в полёте, фазы и обратный отсчёт.
Серверные часы сценарий двигает смещением (w.server.offset): приём ставок 8 с, полёт, итог 4 с проходят без ожидания."""
from crash_helpers import READY, TITLE, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game, shown

NAME = "crash"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках


async def run(w):
    p = w.page
    w.server.script(crash_live=[5000, 150, 100000])      # точки краха первых трёх раундов: ×50, ×1.50, ×1000
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки, приём ставок")
    await p.wait("/^Приём ставок: \\d+ с$/.test(document.getElementById('cr-label').textContent)", 5, "обратный отсчёт")
    check("в приёме ставок: множитель ×1.00 и обратный отсчёт", await p.ev("[document.getElementById('cr-mult').textContent, /^Приём ставок: \\d+ с$/.test(document.getElementById('cr-label').textContent)]"), ["×1.00", True])
    check("хэш раунда показан до старта", await p.ev("/^Хэш раунда [0-9a-f]{12}…/.test(document.getElementById('cr-proof').textContent)"), True)

    # --- раунд 1: автовывод ×2, точка ×50: выигрыш на цели
    await bet(p, 100, "2")
    fs = await flight_start(p)
    await server_to(w, p, fs + 6600)      # полёт идёт 6,6 с: цель ×2.00 (через 6 с) достигнута, точка ×50 (через 33,9 с) ещё нет
    await p.wait("%s.startsWith('Выведено ×2.00')" % TITLE, 20, "автовывод на ×2.00 в полёте")
    await p.wait("document.getElementById('cr-banner').classList.contains('win')", 5, "баннер выигрыша")
    await p.wait("document.getElementById('cr-balance').textContent.replace(/\\s/g, '') === '100100'", 15, "баланс обновился сразу после автовывода (до краха)")
    await server_to(w, p, fs + t_crash_ms(5000) + 300)      # крах ×50 и итог
    await p.wait("%s.startsWith('Выигрыш +100 (×2.00)')" % TITLE, 20, "итог раунда 1: выигрыш")
    check("итог: крах ×50.00 и раскрытый секрет (точка подменена сценарием, поэтому проверка честности честно «не сошлась»)",
          await p.ev("[document.getElementById('cr-mult').textContent, /Крах ×50\\.00/.test(document.getElementById('cr-label').textContent), document.getElementById('cr-proof').textContent.startsWith('Проверка раунда не сошлась')]"),
          ["×50.00", True, True])
    check("баланс 100100", await shown(p, "#cr-balance"), 100100)

    # --- раунд 2: цель ×2 выше точки ×1.50: проигрыш
    await next_round(w, p, fs, 5000)       # пауза итога прошла: новый раунд открывается при ближайшем опросе
    check("новый раунд: история с последним крахом ×50.00", await p.ev("document.getElementById('cr-history').children[0].textContent"), "×50.00")
    await bet(p, 100, "2")
    fs = await flight_start(p)
    await server_to(w, p, fs + t_crash_ms(150) + 300)     # полёт ×1.50 короткий, цель ×2 не достигнута: крах
    await p.wait("%s.startsWith('Крах ×1.50, потеряно 100')" % TITLE, 20, "итог раунда 2: проигрыш")
    check("проигрыш: баннер lose и баланс 100000", [await p.ev("document.getElementById('cr-banner').classList.contains('lose')"), await shown(p, "#cr-balance")], [True, 100000])

    # --- раунд 3: ручной вывод в полёте, точка ×1000
    await next_round(w, p, fs, 150)
    await bet(p, 100, "")
    fs = await flight_start(p)
    await server_to(w, p, fs + 3000)      # полёт 3 с: множитель около ×1.41
    await p.wait("!document.getElementById('cr-actions').hidden && !document.getElementById('cr-cash').disabled", 15, "кнопка «Забрать»")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.05", 20, "множитель вырос")
    await p.tap("#cr-cash")
    await p.wait("%s.startsWith('Выведено ×')" % TITLE, 20, "ручной вывод выиграл")
    check("кнопка «Забрать» пропала, баланс вырос", [await p.ev("document.getElementById('cr-actions').hidden"), await shown(p, "#cr-balance") > 100000], [True, True])
