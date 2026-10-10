"""Общие помощники сценариев живого краша (общий раунд на сервере): часы сервера двигаются смещением относительно раунда, ставка, ожидание панели."""
import math

from harness import set_bet

TITLE = "document.getElementById('cr-banner-title').textContent.replace(/\\s/g, ' ')"
READY = "!document.getElementById('cr-bets').hidden && !document.getElementById('cr-start').disabled"
CASH_VISIBLE = "!document.getElementById('cr-actions').hidden && !document.getElementById('cr-cash').disabled"
CAP_X100 = 100000


def t_crash_ms(x100):
    """Время полёта до закрытия раунда с точкой краха x100 (мс): наименьшее t, при котором m100(t) >= x100 (как на сервере; не выше предела ×1000),
    плюс запас сети 150 мс."""
    x100 = min(x100, CAP_X100)
    t = math.ceil(6000 * math.log2(x100 / 100.0))
    while min(CAP_X100, math.floor(100 * 2 ** (t / 6000.0))) < x100:
        t += 1
    return t + 150


async def server_to(w, p, target_ms):
    """Серверные часы вперёд до target_ms (по часам клиента, сверенным с сервером); назад не идут. К реальному времени смещение прибавляется,
    поэтому цель задаётся относительно раунда (flight_start_ms), а не накопительно."""
    now = await p.ev("crServerNow()")
    if target_ms > now:
        current = float(open(w.server.offset_file, encoding="utf-8").read().strip() or 0)      # смещение, которое уже выставил сценарий или помощник
        w.server.offset(current + (target_ms - now) / 1000.0)


async def flight_start(p):
    return await p.ev("crRound().flight_start_ms")


async def bet(p, amount, target):
    """Ставка amount с целью автовывода target (пусто: вручную); ждёт баннер «Ставка принята»."""
    await set_bet(p, "cr-bet", amount)
    await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = %r; i.dispatchEvent(new Event('input')); })()" % target)
    await p.tap("#cr-start")
    await p.wait("%s.startsWith('Ставка принята')" % TITLE, 10, "ставка принята")


async def next_round(w, p, fs, crash_x100):
    """Пауза итога прошла: новый раунд открывается при ближайшем опросе; ждёт панель ставки."""
    await server_to(w, p, fs + t_crash_ms(crash_x100) + 4000 + 300)
    await p.wait(READY, 20, "новый раунд: приём ставок")
