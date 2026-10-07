"""Краш: ставка с автовыводом и ручной вывод в любой момент. Кнопка «Забрать» есть и при автовыводе (в том же месте, что при обычной ставке), индикатор
«авто ×X» остаётся. Проверяется: (1) ручной вывод до цели: итог на экране совпадает с ответом сервера и базой, баланс, выплата ниже выплаты по цели;
(2) автовывод без нажатия срабатывает по серверному времени, множитель на экране не выше цели; (3) крах раньше цели; (4) сеть: первая отправка
вывода обрывается, повтор с тем же request_id проходит, выплата одна; (5) все попытки обрываются, раунд остаётся активным и выводится после возврата сети."""
from harness import check, open_game, set_bet, shown

NAME = "crash_auto_manual"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}
WIN = "!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('win')"
LOSE = "!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('lose')"
CASH_VISIBLE = "!document.getElementById('cr-actions').hidden && !document.getElementById('cr-cash').disabled"
OVERRIDE = """
(() => {
  const orig = window.fetch;
  window.__mode = 'pass';
  window.__cash = [];
  window.fetch = (u, o) => {
    if (String(u).includes('/api/crash/cashout')) {
      window.__cash.push([window.__mode, JSON.parse(o.body).request_id]);
      if (window.__mode === 'fail_once') { window.__mode = 'pass'; return Promise.reject(new TypeError('Failed to fetch')); }
      if (window.__mode === 'fail_all') return Promise.reject(new TypeError('Failed to fetch'));
      if (window.__mode === 'slow') { window.__mode = 'pass'; return orig(u, o).then((r) => new Promise((res) => setTimeout(() => res(r), 1500))); }   // сервер ответил сразу, до клиента ответ доходит через 1,5 с
    }
    return orig(u, o);
  };
  window.__multLog = [];
  const el = document.getElementById('cr-mult');
  new MutationObserver(() => window.__multLog.push(parseFloat(el.textContent.replace('×', '')))).observe(el, { childList: true, characterData: true, subtree: true });
  return true;
})()
"""


RECTS = """(() => { const r = (id) => { const b = document.getElementById(id).getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map((v) => Math.round(v * 10) / 10); };
  return { cash: r('cr-cash'), actions: r('cr-actions'), chart: r('cr-chart'), mult: r('cr-mult'), label: r('cr-label') }; })()"""


async def start_auto(p, target):
    await set_bet(p, "cr-bet", 100)
    await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = %r; i.dispatchEvent(new Event('input')); })()" % target)
    await p.tap("#cr-start")
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать» при автовыводе")


def row(w, n):
    return w.sql_value("SELECT mode || ' ' || COALESCE(mult_x100, '') || ' ' || COALESCE(payout, '') || ' ' || result FROM crash_games ORDER BY id LIMIT 1 OFFSET %d" % n)


async def run(w):
    p = w.page
    w.server.script(crash=[100000, 100000, 130, 100000, 100000, 100000])
    await open_game(p, "crash")
    await p.wait("!document.getElementById('cr-bets').hidden", 10, "панель ставки")
    await p.ev(OVERRIDE)

    # --- 1. автовывод ×2, ручной вывод до цели
    await start_auto(p, "2")
    check("индикатор автовывода и кнопка «Забрать» на месте", await p.ev("[document.getElementById('cr-label').textContent.trim(), document.getElementById('cr-cash').textContent.trim().startsWith('Забрать'), document.getElementById('cr-actions').hidden]"),
          ["авто ×2.00", True, False])
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.20", 20, "множитель вырос")
    await p.ev("window.__mode = 'slow'")      # итог должен быть из ответа сервера (множитель на момент запроса), а не из часов клиента (за 1,5 с он вырос бы на 19 %)
    await p.tap("#cr-cash")
    await p.wait(WIN, 20, "ручной вывод до цели выиграл")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    r = row(w, 0).split()
    mult, payout = int(r[1]), int(r[2])
    check("в базе: авто-раунд закрыт ручным выводом до цели", (r[0], r[3], 120 <= mult < 200, payout == 100 * mult // 100), ("auto", "win", True, True))
    banner = await p.ev("document.getElementById('cr-banner-title').textContent.replace(/\\s/g, ' ')")
    check("итог на экране совпадает с ответом сервера (множитель и прибыль)", banner, "Выигрыш +%d (×%d.%02d)" % (payout - 100, mult // 100, mult % 100))
    check("баланс = 100000 - ставка + выплата по ответу сервера", await shown(p, "#cr-balance"), 100000 - 100 + payout)
    check("кнопка «Забрать» пропала", await p.ev("document.getElementById('cr-actions').hidden"), True)
    bal = 100000 - 100 + payout

    # --- 2. автовывод по цели без нажатия: серверное время, множитель на экране не выше цели
    await p.ev("window.__multLog = []")
    await start_auto(p, "1.5")
    await p.wait(WIN, 20, "автовывод сработал на ×1.50")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("автовывод: выплата ровно по цели", row(w, 1), "auto 150 150 win")
    log = await p.ev("window.__multLog")
    check("множитель на экране ни разу не выше цели ×1.50", [v for v in log if v > 1.5], [])
    check("баланс после автовывода", await shown(p, "#cr-balance"), bal - 100 + 150)
    bal = bal - 100 + 150

    # --- 3. крах раньше цели (точка ×1.30, цель ×3)
    await start_auto(p, "3")
    await p.wait(LOSE, 20, "крах раньше цели")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("крах: проигрыш в базе и на экране, кнопки нет", [row(w, 2), await p.ev("document.getElementById('cr-banner-title').textContent.includes('Крах ×1.30')"), await p.ev("document.getElementById('cr-actions').hidden")],
          ["auto 0 0 lose", True, True])
    check("баланс после проигрыша", await shown(p, "#cr-balance"), bal - 100)
    bal -= 100

    # --- 4. обрыв сети при выводе: первая отправка не доходит, повтор с тем же request_id
    await start_auto(p, "5")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.10", 20, "множитель вырос")
    await p.ev("window.__mode = 'fail_once'; window.__cash = []")
    await p.tap("#cr-cash")
    await p.wait(WIN, 30, "вывод прошёл после повтора")
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    cash = await p.ev("window.__cash")
    check("две отправки вывода с одним request_id", [len(cash), len({c[1] for c in cash})], [2, 1])
    r = row(w, 3).split()
    check("выплата одна, ниже цели", (r[0], r[3], 110 <= int(r[1]) < 500), ("auto", "win", True))
    check("баланс: одна выплата", await shown(p, "#cr-balance"), bal - 100 + int(r[2]))
    bal = bal - 100 + int(r[2])

    # --- 5. все попытки вывода обрываются: раунд остаётся активным, после возврата сети выводится
    await start_auto(p, "20")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.10", 20, "множитель вырос")
    rects_auto = await p.ev(RECTS)      # к этому раунду лента истории уже на месте, как и в ручном раунде в конце
    await p.ev("window.__mode = 'fail_all'; window.__cash = []")
    await p.tap("#cr-cash")
    await p.wait("window.__cash.length >= 3 && !document.getElementById('cr-cash').disabled", 40, "три попытки вывода оборваны")
    check("раунд остался активным, кнопка снова доступна", await p.ev("[cr.view, document.getElementById('cr-actions').hidden]"), ["play", False])
    check("ставка списана один раз, выплаты нет", [w.sql_value("SELECT status FROM crash_games ORDER BY id LIMIT 1 OFFSET 4"), await shown(p, "#cr-balance")], ["active", bal - 100])
    await p.ev("window.__mode = 'pass'")
    await p.tap("#cr-cash")
    await p.wait(WIN, 20, "вывод после возврата сети")
    r = row(w, 4).split()
    check("итог: ручной вывод до цели ×20", (r[0], r[3], int(r[1]) < 2000), ("auto", "win", True))
    await p.wait("!document.getElementById('cr-start').disabled", 20, "раунд закончен")
    check("баланс после возврата сети", await shown(p, "#cr-balance"), bal - 100 + int(r[2]))

    # --- 6. обычная ручная ставка: кнопка «Забрать», панель и график на тех же местах, что при автовыводе (ни пикселя вне кнопки не сдвинулось)
    bal = await shown(p, "#cr-balance")
    await set_bet(p, "cr-bet", 100)
    await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = ''; i.dispatchEvent(new Event('input')); })()")
    await p.tap("#cr-start")
    await p.wait(CASH_VISIBLE, 15, "кнопка «Забрать» при ручной ставке")
    await p.wait("parseFloat(document.getElementById('cr-mult').textContent.replace('×', '')) >= 1.20", 20, "множитель вырос")
    rects_manual = await p.ev(RECTS)
    # множитель и подпись: тексты разные («×1.23», «авто ×2.00»), поэтому у них сравниваются вертикаль и высота, у остальных всё
    same = lambda a, b: all(a[k] == b[k] for k in ("cash", "actions", "chart")) and all((a[k][1], a[k][3]) == (b[k][1], b[k][3]) for k in ("mult", "label"))   # noqa: E731
    check("геометрия кнопки «Забрать», панели и графика одинакова при автовыводе и ручной ставке, множитель и подпись на тех же строках", [same(rects_manual, rects_auto), [rects_manual, rects_auto] if not same(rects_manual, rects_auto) else None], [True, None])
    await p.tap("#cr-cash")
    await p.wait(WIN, 20, "ручной вывод выиграл")
