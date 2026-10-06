"""Краш: уход с экрана или скрытие приложения во время раунда и возврат. Раньше при уходе с экрана цепочка опроса обрывалась, а кадры шли дальше по часам:
после возврата множитель бежал сверх краха, хотя раунд уже закончился. Теперь: на уходе кадры и опрос стоят, на возврате сервер спрашивается сразу, экран
показывает итог, множитель после возврата ни разу не выше точки краха и не бежит; повторные возвраты ничего не ломают. «Скрытие приложения»
имитируется подменой document.visibilityState и событием visibilitychange (настоящее скрытие страницы в headless Chrome недоступно)."""
import asyncio

from harness import check, open_game, set_bet

NAME = "crash_visibility"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}
BANNER_LOSE = "!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('lose')"
STATE = "[cr.view, cr.raf, cr.pollTimer, cr.polling, cr.resync]"
HIDE = "(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); })()"
SHOW = "(() => { delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange')); })()"
WATCH = """(() => {
  window.__multLog = [];
  const el = document.getElementById('cr-mult');
  const rec = () => window.__multLog.push(parseFloat(el.textContent.replace('×', '')));
  window.__multObs = new MutationObserver(rec);
  window.__multObs.observe(el, { childList: true, characterData: true, subtree: true });
  window.__polls = 0;
  const orig = window.fetch;
  window.fetch = (u, o) => { if (String(u).includes('/api/crash/state')) window.__polls += 1; return orig(u, o); };
  return true;
})()"""


async def start_manual(p):
    await set_bet(p, "cr-bet", 100)
    await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = ''; i.dispatchEvent(new Event('input')); })()")
    await p.tap("#cr-start")
    await p.wait("!document.getElementById('cr-actions').hidden && !document.getElementById('cr-cash').disabled", 15, "кнопка «Забрать»")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.05", 20, "множитель вырос")


async def run(w):
    p = w.page
    w.server.script(crash=[200, 200, 200])         # точка краха ×2.00: раунд длится около 6 с
    await open_game(p, "crash")
    await p.wait("!document.getElementById('cr-bets').hidden", 10, "панель ставки")
    await p.ev(WATCH)

    # --- 1. уход на другую вкладку в середине раунда, раунд заканчивается на сервере, возврат
    await start_manual(p)
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинга")
    away = await p.ev(STATE)
    check("на уходе с экрана цикл кадров и опрос остановлены", [away[0], away[1], away[2]], ["play", 0, 0])
    await asyncio.sleep(7.5)                       # раунд (около 6 с) закончился на сервере, пока экран краша закрыт
    await p.ev("window.__multLog = []")
    await p.tap(".tab[data-tab=play]")
    await p.wait(BANNER_LOSE, 10, "после возврата показан итог раунда")
    await asyncio.sleep(1.5)
    log = await p.ev("window.__multLog")
    check("после возврата множитель ни разу не выше точки краха ×2.00", [v for v in log if v > 2.0], [])
    check("итог: ×2.00 и цикл остановлен", [await p.ev("document.getElementById('cr-mult').textContent.trim()"), await p.ev("[cr.view, cr.raf, cr.pollTimer, cr.resync]")],
          ["×2.00", ["result", 0, 0, False]])
    shown = await p.ev("document.getElementById('cr-mult').textContent")
    await asyncio.sleep(1.2)
    check("множитель не бежит", await p.ev("document.getElementById('cr-mult').textContent"), shown)
    await p.wait("!document.getElementById('cr-start').disabled", 10, "раунд закончен")

    # --- 2. повторные возвраты в середине следующего раунда (вкладки и скрытие приложения), раунд доходит до конца
    await start_manual(p)
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
    check("раунд ещё идёт, цикл и опрос работают после повторных возвратов", [mid[0], mid[1] != 0 or mid[2] != 0], ["play", True])
    await p.ev("window.__multLog = []")
    await p.wait(BANNER_LOSE, 15, "раунд дошёл до краха и показан итог")
    await asyncio.sleep(1.0)
    log = await p.ev("window.__multLog")
    # раунд идёт на экране, точки краха клиент не знает: до очередного опроса (300 мс) множитель штатно чуть выше; бегущего множителя быть не должно
    check("множитель выше ×2.00 не больше, чем на один интервал опроса, итог ×2.00", [[v for v in log if v > 2.15], await p.ev("document.getElementById('cr-mult').textContent.trim()")], [[], "×2.00"])
    check("после итога ни кадров, ни опроса", await p.ev("[cr.raf, cr.pollTimer, cr.polling]"), [0, 0, False])

    # --- 3. скрытие приложения, раунд заканчивается, возврат (остаёмся на экране краша)
    await start_manual(p)
    await p.ev(HIDE)
    await asyncio.sleep(7.0)
    await p.ev("window.__multLog = []; " + SHOW)      # сброс журнала и возврат одним вызовом: при имитации скрытия кадры ещё идут и до возврата
    await p.wait(BANNER_LOSE, 10, "после возврата в приложение показан итог")
    await asyncio.sleep(1.2)
    log = await p.ev("window.__multLog")
    check("скрытие приложения: после возврата множитель не выше ×2.00", [v for v in log if v > 2.0], [])
    check("скрытие приложения: итог без бегущего множителя", await p.ev("[document.getElementById('cr-mult').textContent.trim(), cr.raf, cr.pollTimer]"), ["×2.00", 0, 0])
    polls = await p.ev("window.__polls")
    check("запросов состояния краша не больше разумного (%d)" % polls, polls < 90, True)
