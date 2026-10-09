"""Живой краш: уход с экрана или скрытие приложения во время раунда и возврат. На уходе кадры и опрос стоят (ни одного запроса состояния, пока экран закрыт),
на возврате состояние спрашивается сразу, пока ответ не пришёл кадры не рисуются (resync), множитель после возврата ни разу не выше точки краха и не бежит после
итога; повторные возвраты ничего не ломают. «Скрытие приложения» имитируется подменой document.visibilityState и событием visibilitychange (настоящее скрытие
страницы в headless Chrome недоступно). Серверные часы двигаются смещением относительно раунда: раунд с точкой ×2.00 заканчивается через 6,15 с полёта."""
import asyncio

from crash_helpers import CASH_VISIBLE, READY, bet, flight_start, next_round, server_to, t_crash_ms
from harness import check, open_game

NAME = "crash_visibility"
USERS = {"me": {"rate": 0}}
BANNER_LOSE = "!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('lose')"
STATE = "[cr.raf, cr.pollTimer, cr.polling, cr.resync]"
HIDE = "(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); })()"
SHOW = "(() => { delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange')); })()"
WATCH = """(() => {
  window.__multLog = [];
  const el = document.getElementById('cr-mult');
  window.__multObs = new MutationObserver(() => window.__multLog.push(parseFloat(el.textContent.replace('×', ''))));
  window.__multObs.observe(el, { childList: true, characterData: true, subtree: true });
  window.__polls = 0;
  const orig = window.fetch;
  window.fetch = (u, o) => { if (String(u).includes('/api/crash/live') && !String(u).includes('/live/')) window.__polls += 1; return orig(u, o); };
  return true;
})()"""


async def start_manual(p):
    await bet(p, 100, "")
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать»")


async def run(w):
    p = w.page
    w.server.script(crash_live=[200, 200, 200])         # точка краха ×2.00: полёт 6 с
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")
    await p.ev(WATCH)

    # --- 1. уход на другую вкладку в полёте: ни кадров, ни опроса; раунд заканчивается на сервере; возврат
    await start_manual(p)
    fs = await flight_start(p)
    await server_to(w, p, fs + 2000)
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.10", 20, "множитель вырос")
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинга")
    away = await p.ev(STATE)
    check("на уходе с экрана цикл кадров и опрос остановлены", [away[0], away[1]], [0, 0])
    polls = await p.ev("window.__polls")
    await asyncio.sleep(2.5)
    check("пока экран краша закрыт, запросов состояния нет", await p.ev("window.__polls"), polls)
    await server_to(w, p, fs + t_crash_ms(200) + 400)      # раунд закончился на сервере, пока экран закрыт
    await p.ev("window.__multLog = []")
    await p.tap(".tab[data-tab=play]")
    await p.wait("document.getElementById('cr-banner-title').textContent.startsWith('Крах ×2.00, потеряно 100')", 10, "после возврата показан итог раунда")
    await p.wait(BANNER_LOSE, 5, "баннер проигрыша")
    await asyncio.sleep(1.0)
    log = await p.ev("window.__multLog")
    check("после возврата множитель ни разу не выше точки краха ×2.00", [v for v in log if v > 2.0], [])
    check("итог: ×2.00, ответ получен (resync снят)", [await p.ev("document.getElementById('cr-mult').textContent.trim()"), await p.ev("cr.resync")], ["×2.00", False])
    shown_mult = await p.ev("document.getElementById('cr-mult').textContent")
    await asyncio.sleep(1.2)
    check("множитель в итоге не бежит", await p.ev("document.getElementById('cr-mult').textContent"), shown_mult)

    # --- 2. повторные возвраты в полёте следующего раунда (вкладки и скрытие приложения), раунд идёт и доходит до конца
    await next_round(w, p, fs, 200)
    await start_manual(p)
    fs = await flight_start(p)
    await server_to(w, p, fs + 1500)
    for _ in range(3):
        await p.tap(".tab[data-tab=farm]")
        await p.wait("!document.querySelector('[data-screen=farm]').hidden", 5, "вкладка фермы")
        await p.tap(".tab[data-tab=play]")
        await p.wait("!document.querySelector('[data-screen=crash]').hidden", 5, "экран краша")
        await p.ev(HIDE)
        await asyncio.sleep(0.15)
        await p.ev(SHOW)
        await asyncio.sleep(0.15)
    mid = await p.ev(STATE)
    check("раунд идёт, цикл кадров и опрос работают после повторных возвратов", mid[0] != 0 or mid[1] != 0, True)
    await p.ev("window.__multLog = []")
    await server_to(w, p, fs + t_crash_ms(200) + 400)
    await p.wait(BANNER_LOSE, 15, "раунд дошёл до краха и показан итог")
    await asyncio.sleep(1.0)
    log = await p.ev("window.__multLog")
    # крах клиент заранее не знает: до очередного опроса (300 мс) множитель штатно чуть выше точки; бегущего множителя после итога быть не должно
    check("множитель выше ×2.00 не больше, чем на один интервал опроса, итог ×2.00", [[v for v in log if v > 2.15], await p.ev("document.getElementById('cr-mult').textContent.trim()")], [[], "×2.00"])

    # --- 3. скрытие приложения: опроса нет, раунд заканчивается, возврат (остаёмся на экране краша)
    await next_round(w, p, fs, 200)
    await start_manual(p)
    fs = await flight_start(p)
    await server_to(w, p, fs + 1500)
    await p.ev(HIDE)
    await asyncio.sleep(0.5)
    polls = await p.ev("window.__polls")
    await asyncio.sleep(2.0)
    check("пока приложение скрыто, запросов состояния нет", await p.ev("window.__polls"), polls)
    await server_to(w, p, fs + t_crash_ms(200) + 400)
    await p.ev("window.__multLog = []; " + SHOW)      # сброс журнала и возврат одним вызовом: при имитации скрытия кадры ещё идут и до возврата
    await p.wait("document.getElementById('cr-banner-title').textContent.startsWith('Крах ×2.00, потеряно 100')", 10, "после возврата в приложение показан итог")
    await asyncio.sleep(1.0)
    log = await p.ev("window.__multLog")
    check("скрытие приложения: после возврата множитель не выше ×2.00", [v for v in log if v > 2.0], [])
    check("скрытие приложения: итог без бегущего множителя", await p.ev("document.getElementById('cr-mult').textContent.trim()"), "×2.00")
    total = await p.ev("window.__polls")
    check("запросов состояния краша не больше разумного (%d)" % total, total < 150, True)
