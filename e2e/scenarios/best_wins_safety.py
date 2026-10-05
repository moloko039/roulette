"""Безопасность блока «Рекорды выигрыша»: теги в именах и неизвестные коды игр не становятся разметкой; неизвестные игры пропускаются, прототипные
имена не проходят; неверная форма ответа не ломает рейтинг; публичные слоты и числа проверяются. Ответы подменяются в странице (боевой код не меняется)."""
from harness import check

NAME = "best_wins_safety"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}
EVIL = '<img src=x onerror="window.__pwn=1"><b>жирный</b>'

OVERRIDE = """
(() => {
  const orig = window.fetch;
  const json = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { 'Content-Type': 'application/json' } });
  const evil = %s;
  window.__bestMode = 'ok';
  window.fetch = async (u, o) => {
    const url = String(u);
    if (url.includes('/api/chat/best-wins')) {
      const row = (rank, name, game, me, cos, net) => ({ rank, name, net_amount: net === undefined ? 1000 - rank : net, game, is_me: me, cosmetics: cos, member_ref: me ? null : '0'.repeat(32) });
      if (window.__bestMode === 'ok') return json({ scope: 'chat', total: 6, me: { rank: 1, net_amount: 999, game: '__proto__', total: 6 }, top: [
        row(1, evil, 'keno', false, { avatar_frame: '<script>window.__pwn=3</script>', badge: 'x" onmouseover="window.__pwn=4' }),
        row(2, 'bob', 'evil<img>', false, {}),
        row(3, 'bob', '__proto__', false, {}),
        row(4, 'bob', 'constructor', false, {}),
        row(5, 'bob', 'toString', false, {}),
        row(6, 'carol', 'hilo', false, { avatar_frame: 'frame_unknown', badge: '__proto__', chip: 'chip_ring', table: 'table_blue' }) ] });
      if (window.__bestMode === '500') return new Response('{}', { status: 500 });
      if (window.__bestMode === 'net') throw new TypeError('Failed to fetch');
      if (window.__bestMode === 'none') return json({ scope: 'none' });
    }
    return orig(u, o);
  };
  return true;
})()
"""

LIST = "[...document.querySelectorAll('#best-list li')].map((li) => li.querySelector('.rating-name').textContent)"


async def reopen(p, mode):
    """Подменяет режим ответа и заново открывает рейтинг (между запросами рейтинга не меньше 5 секунд: перезагрузка страницы)."""
    await p.ev("window.__bestMode = %r" % mode)
    await p.ev("loadRating('manual')")
    await p.ev("E.sleep(900)")


async def run(w):
    import json
    p = w.page
    await p.ev(OVERRIDE % json.dumps(EVIL))
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 2 && !document.getElementById('best-card').hidden", 15, "рейтинг и блок")
    check("в блоке нет разметки из имён и публичных слотов", await p.ev("[document.querySelectorAll('#best-card img, #best-card b, #best-card script').length, window.__pwn || 0]"), [0, 0])
    check("имя выведено текстом", await p.ev("document.querySelector('#best-list li .rating-name').textContent"), EVIL)
    check("неизвестные и прототипные коды игр пропущены, остались keno и hilo", await p.ev(LIST), [EVIL, "carol"])
    check("игры показаны русскими названиями", await p.ev("[...document.querySelectorAll('#best-list li .rating-staked')].map((e) => e.textContent)"), ["Кено", "Хило"])
    check("неизвестные рамки и значки игнорируются, атрибутов нет", await p.ev(
        "[...document.querySelectorAll('#best-list li')].map(li => [li.querySelector('.avatar').getAttribute('data-skin-avatar_frame'), !!li.querySelector('.rating-badge'), li.outerHTML.includes('onmouseover')])"),
        [[None, False, False], [None, False, False]])
    check("своя строка с неизвестной игрой не показана", await p.ev("document.getElementById('best-me').children.length"), 0)
    check("приватные слоты других не применены", await p.ev("document.documentElement.outerHTML.includes('chip_ring') || document.documentElement.outerHTML.includes('table_blue')"), False)
    check("страница цела", await p.ev("typeof window.__pwn"), "undefined")
    # неверная форма ответа отвергается проверкой формы (чистая функция, без запросов)
    forms = {
        "ok": ({"scope": "chat", "total": 1, "me": None, "top": [{"rank": 1, "name": "x", "net_amount": 5, "game": "keno", "is_me": False}]}, True),
        "отрицательная сумма": ({"scope": "chat", "total": 1, "me": None, "top": [{"rank": 1, "name": "x", "net_amount": -5, "game": "keno", "is_me": False}]}, False),
        "дробная сумма": ({"scope": "chat", "total": 1, "me": None, "top": [{"rank": 1, "name": "x", "net_amount": 1.5, "game": "keno", "is_me": False}]}, False),
        "сумма выше безопасного целого": ({"scope": "chat", "total": 1, "me": None, "top": [{"rank": 1, "name": "x", "net_amount": 1e21, "game": "keno", "is_me": False}]}, False),
        "11 записей": ({"scope": "chat", "total": 11, "me": None, "top": [{"rank": i + 1, "name": "x", "net_amount": 5, "game": "keno", "is_me": False} for i in range(11)]}, False),
        "нет me": ({"scope": "chat", "total": 1, "top": []}, False),
        "me не объект": ({"scope": "chat", "total": 1, "me": 5, "top": []}, False),
        "scope не строка": ({"scope": 5}, False),
        "имя не строка": ({"scope": "chat", "total": 1, "me": None, "top": [{"rank": 1, "name": 5, "net_amount": 5, "game": "keno", "is_me": False}]}, False),
        "нет total": ({"scope": "chat", "me": None, "top": []}, False),
        "null": (None, False),
    }
    for name, (payload, want) in forms.items():
        check("форма «%s»: %s" % (name, "принята" if want else "отвергнута"), await p.ev("validBestWins(%s)" % json.dumps(payload)), want)
    # сбой сервера и сети: блок остаётся прежним, рейтинг цел
    before = await p.ev(LIST)
    for mode in ("500", "net"):
        await p.ev("E.sleep(5200)")           # интервал между запросами рейтинга
        await reopen(p, mode)
        check("ответ «%s»: блок прежний, рейтинг на месте" % mode, await p.ev("[%s, document.querySelectorAll('#rating-list li').length >= 2, window.__pwn || 0]" % LIST), [before, True, 0])
    # scope none от сервера: блок скрыт
    await p.ev("E.sleep(5200)")
    await reopen(p, "none")
    check("scope none: блок скрыт", await p.ev("document.getElementById('best-card').hidden"), True)
