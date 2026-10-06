"""POST: таймаут покрывает чтение тела ответа, обрыв связи не оставляет интерфейс в состоянии «отправка». Сервер отдаёт заголовки и (а) замолкает, не закрывая тело,
(б) обрывает тело. Подмена fetch в странице строит Response с потоком тела так же, как браузер при сбое сети: поток завершается ошибкой, а при таймауте
(AbortController клиента) ошибкой AbortError. Боевой код не меняется. Проверяется на игре «Кено».
(а) первая попытка: заголовки 200 и тишина; клиент по таймауту (10 с) повторяет с тем же request_id, вторая попытка доходит до настоящего сервера:
    раунд сыгран один раз (одно списание), экран показывает итог.
(б) все три попытки: заголовки 200 и оборванное тело: после повторов интерфейс выходит из «отправки» с понятной ошибкой, кнопка снова доступна, баланс прежний."""
import sys

from harness import BOT, check, open_game, set_bet, shown

NAME = "post_body_abort"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}

OVERRIDE = """
(() => {
  const orig = window.fetch;
  window.__mode = 'pass';
  window.__calls = [];
  window.fetch = (u, o) => {
    const url = String(u);
    if (!url.includes('/api/keno/play') || window.__mode === 'pass') return orig(u, o);
    const id = JSON.parse(o.body).request_id;
    window.__calls.push([window.__mode, id]);
    if (window.__mode === 'stall_once') {
      window.__mode = 'pass';                       // следующая попытка уйдёт на настоящий сервер
      const stream = new ReadableStream({ start(c) {
        c.enqueue(new TextEncoder().encode('{"draw":'));
        o.signal.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError')));   // как браузер: таймаут клиента обрывает чтение тела
      } });
      return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    const stream = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{"draw":[1,2')); c.error(new TypeError('network error')); } });
    return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'application/json' } }));
  };
  return true;
})()
"""


async def pick_and_play(p):
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 чисел")
    for n in (1, 2, 3):
        if not await p.ev("document.querySelector('.keno-ball:nth-child(%d)').classList.contains('sel')" % n):
            await p.tap(".keno-ball:nth-child(%d)" % n)
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")


async def run(w):
    p = w.page
    w.server.script(keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]])
    await open_game(p, "keno")
    await p.ev(OVERRIDE)
    check("баланс до раундов", await shown(p, "#keno-balance"), 100000)

    # (а) тишина после заголовков: таймаут клиента и повтор
    await p.ev("window.__mode = 'stall_once'")
    await pick_and_play(p)
    check("во время ожидания интерфейс в состоянии «отправка»", await p.ev("document.getElementById('keno-play').disabled"), True)
    await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 40, "итог раунда после таймаута тела и повтора")
    await p.wait("!document.getElementById('keno-play').disabled", 30, "кнопка снова доступна")
    calls = await p.ev("window.__calls")
    check("первая попытка зависла, вторая прошла на настоящий сервер", len(calls), 1)
    sys.path.insert(0, BOT)
    import keno
    check("раунд сыгран один раз: одно списание и выплата по таблице", await shown(p, "#keno-balance"), 100000 - 100 + keno.payout(100, 3, 3))
    check("на сервере один раунд кено", w.sql_value("SELECT COUNT(*) FROM keno_rounds"), 1)

    # (б) оборванное тело на всех попытках: понятная ошибка, выход из «отправки», баланс прежний
    balance = await shown(p, "#keno-balance")
    await p.ev("window.__mode = 'truncate'; window.__calls = []")
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled && document.getElementById('keno-notice').textContent.trim().length > 0", 40, "выход из «отправки» с ошибкой")
    check("текст ошибки понятный", await p.ev("document.getElementById('keno-notice').textContent.trim()"), "Нет связи. Баланс обновлён")
    calls = await p.ev("window.__calls")
    check("три попытки с одним request_id", [len(calls), len({c[1] for c in calls})], [3, 1])
    check("баланс прежний, на сервере всё ещё один раунд", [await shown(p, "#keno-balance"), w.sql_value("SELECT COUNT(*) FROM keno_rounds")], [balance, 1])
