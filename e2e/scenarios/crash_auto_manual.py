"""Живой краш: ставка с автовыводом и ручной вывод в любой момент. Кнопка «Забрать» есть и при автовыводе, подпись «авто ×X» остаётся.
Проверяется: (1) ручной вывод до цели: итог на экране совпадает с ответом сервера и базой, баланс; (2) автовывод без нажатия срабатывает по серверному времени
и платит ровно по цели; (3) крах раньше цели: проигрыш; (4) обрыв сети при выводе: первая отправка не доходит, повтор с тем же request_id, выплата одна;
(5) все попытки обрываются: ставка остаётся открытой, после возврата сети выводится. Серверные часы двигаются смещением относительно раунда."""
from crash_helpers import CASH_VISIBLE, TITLE, bet, flight_start, next_round, server_to
from harness import check, open_game, shown

NAME = "crash_auto_manual"
USERS = {"me": {"rate": 0}}
OVERRIDE = """
(() => {
  const orig = window.fetch;
  window.__mode = 'pass';
  window.__cash = [];
  window.fetch = (u, o) => {
    if (String(u).includes('/api/crash/live/cashout')) {
      window.__cash.push([window.__mode, JSON.parse(o.body).request_id]);
      if (window.__mode === 'fail_once') { window.__mode = 'pass'; return Promise.reject(new TypeError('Failed to fetch')); }
      if (window.__mode === 'fail_all') return Promise.reject(new TypeError('Failed to fetch'));
      if (window.__mode === 'slow') { window.__mode = 'pass'; return orig(u, o).then((r) => new Promise((res) => setTimeout(() => res(r), 1500))); }   // сервер ответил сразу, до клиента ответ доходит поздно
    }
    return orig(u, o);
  };
  return true;
})()
"""


def row(w, n):
    return w.sql_value("SELECT status || ' ' || COALESCE(cashed_x100, '') || ' ' || payout FROM crash_bets ORDER BY round_id LIMIT 1 OFFSET %d" % n)


async def run(w):
    p = w.page
    w.server.script(crash_live=[100000, 100000, 130, 100000, 100000])      # точки краха пяти раундов: ×1000 (вне досягаемости теста), ×1000, ×1.30, ×1000, ×1000
    await open_game(p, "crash")
    await p.wait("!document.getElementById('cr-bets').hidden && !document.getElementById('cr-start').disabled", 10, "панель ставки")
    await p.ev(OVERRIDE)
    bal = 100000

    # --- 1. автовывод ×2, ручной вывод до цели; ответ сервера приходит поздно: итог берётся из ответа, а не из часов клиента
    await bet(p, 100, "2")
    fs = await flight_start(p)
    await server_to(w, p, fs + 3000)
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать» при автовыводе")
    check("индикатор автовывода и кнопка «Забрать» на месте", await p.ev("[document.getElementById('cr-label').textContent.trim(), document.getElementById('cr-cash').textContent.startsWith('Забрать')]"), ["авто ×2.00", True])
    await p.ev("window.__mode = 'slow'")
    await p.tap("#cr-cash")
    await p.wait("%s.startsWith('Выведено ×')" % TITLE, 20, "ручной вывод до цели выиграл")
    r = row(w, 0).split()
    mult, payout = int(r[1]), int(r[2])
    check("в базе: ставка выведена вручную до цели ×2", (r[0], 120 <= mult < 200, payout == 100 * mult // 100), ("cashed", True, True))
    check("итог на экране совпадает с ответом сервера (множитель и прибыль)", await p.ev(TITLE), "Выведено ×%d.%02d: +%d" % (mult // 100, mult % 100, payout - 100))
    await p.wait("document.getElementById('cr-balance').textContent.replace(/\\s/g, '') === '%d'" % (bal - 100 + payout), 20, "баланс = 100000 - ставка + выплата по ответу сервера")
    check("кнопка «Забрать» пропала после вывода", await p.ev("document.getElementById('cr-actions').hidden"), True)
    bal = bal - 100 + payout

    # --- 2. автовывод по цели без нажатия: серверное время, выплата ровно по цели
    await next_round(w, p, fs, 100000)
    await bet(p, 100, "1.5")
    fs = await flight_start(p)
    await server_to(w, p, fs + 4200)       # цель ×1.50 достигается через 3,5 с полёта
    await p.wait("%s.startsWith('Выведено ×1.50')" % TITLE, 20, "автовывод сработал на ×1.50")
    check("автовывод: выплата ровно по цели", row(w, 1), "cashed 150 150")
    await p.wait("document.getElementById('cr-balance').textContent.replace(/\\s/g, '') === '%d'" % (bal - 100 + 150), 20, "баланс после автовывода")
    bal = bal - 100 + 150

    # --- 3. крах раньше цели (точка ×1.30, цель ×3)
    await next_round(w, p, fs, 100000)
    await bet(p, 100, "3")
    fs = await flight_start(p)
    await server_to(w, p, fs + 3000)
    await p.wait("%s.startsWith('Крах ×1.30, потеряно 100')" % TITLE, 20, "крах раньше цели: проигрыш")
    check("крах: проигрыш в базе, баннер lose, кнопки «Забрать» нет", [row(w, 2), await p.ev("document.getElementById('cr-banner').classList.contains('lose')"), await p.ev("document.getElementById('cr-actions').hidden")],
          ["lost  0", True, True])
    await p.wait("document.getElementById('cr-balance').textContent.replace(/\\s/g, '') === '%d'" % (bal - 100), 20, "баланс после проигрыша")
    bal -= 100

    # --- 4. обрыв сети при выводе: первая отправка не доходит, повтор с тем же request_id
    await next_round(w, p, fs, 130)
    await bet(p, 100, "5")
    fs = await flight_start(p)
    await server_to(w, p, fs + 2500)
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать»")
    await p.ev("window.__mode = 'fail_once'; window.__cash = []")
    await p.tap("#cr-cash")
    await p.wait("%s.startsWith('Выведено ×')" % TITLE, 30, "вывод прошёл после повтора")
    cash = await p.ev("window.__cash")
    check("две отправки вывода с одним request_id", [len(cash), len({c[1] for c in cash})], [2, 1])
    r = row(w, 3).split()
    check("выплата одна, ниже цели ×5", (r[0], 110 <= int(r[1]) < 500), ("cashed", True))
    await p.wait("document.getElementById('cr-balance').textContent.replace(/\\s/g, '') === '%d'" % (bal - 100 + int(r[2])), 20, "баланс: одна выплата")
    bal = bal - 100 + int(r[2])

    # --- 5. все попытки вывода обрываются: ставка остаётся открытой, после возврата сети выводится
    await next_round(w, p, fs, 100000)
    await bet(p, 100, "20")
    fs = await flight_start(p)
    await server_to(w, p, fs + 2500)
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать»")
    await p.ev("window.__mode = 'fail_all'; window.__cash = []")
    await p.tap("#cr-cash")
    await p.wait("window.__cash.length >= 3 && !document.getElementById('cr-cash').disabled", 40, "три попытки вывода оборваны")
    check("ставка осталась открытой, кнопка снова доступна", [row(w, 4), await p.ev("document.getElementById('cr-actions').hidden")], ["open  0", False])
    check("ставка списана один раз, выплаты нет", await shown(p, "#cr-balance"), bal - 100)
    await p.ev("window.__mode = 'pass'")
    await p.tap("#cr-cash")
    await p.wait("%s.startsWith('Выведено ×')" % TITLE, 20, "вывод после возврата сети")
    r = row(w, 4).split()
    check("итог: ручной вывод до цели ×20", (r[0], int(r[1]) < 2000), ("cashed", True))
