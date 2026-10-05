"""Кено с надетым keno_hex (баг на iPhone в Telegram: приложение мигало и показывало чёрный экран). Причина: на каждом из 40 шариков стояли filter: drop-shadow
и clip-path, в WebKit это десятки слоёв с размытием на каждое изменение класса. Теперь шестиугольник нарисован фоном-SVG. Сценарий (CPU замедлен в 6 раз):
надеть keno_hex, открыть кено, сыграть три раунда, подождать. Страница не перезагружалась (метка в window и одна навигация), консоль чиста (проверяет
харнесс), на экране кено нет элементов с filter, backdrop-filter и clip-path, перерисовки доски (записи MutationObserver) не растут от раунда к раунду
и в покое их нет. Настоящий WebKit этот сценарий не заменяет: в Chrome долгих кадров не видно (измерение в WebKit: вне репозитория, см. журнал)."""
import time

from harness import check, open_game, set_bet, shown

NAME = "keno_hex_play"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}
DRAW = [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]

OBSERVE = """
(() => {
  window.__moves = 0;
  const board = document.querySelector('.keno-board');
  window.__obs = new MutationObserver((list) => { window.__moves += list.length; });
  window.__obs.observe(board, { subtree: true, childList: true, attributes: true, characterData: true });
  return true;
})()
"""

HEAVY = """
(() => {
  const els = [...document.querySelectorAll('.screen[data-screen=keno], .screen[data-screen=keno] *')];
  const cs = (e) => getComputedStyle(e);
  return { total: els.length, filter: els.filter((e) => cs(e).filter !== 'none').length,
           backdrop: els.filter((e) => (cs(e).backdropFilter || cs(e).webkitBackdropFilter || 'none') !== 'none').length,
           clip: els.filter((e) => cs(e).clipPath !== 'none').length,
           hexImages: [...document.querySelectorAll('.keno-ball span')].filter((e) => cs(e).backgroundImage.startsWith('url(')).length,
           skin: document.documentElement.getAttribute('data-skin-keno_ball') };
})()
"""


async def run(w):
    p = w.page
    uid = w.users["me"].id
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, 'keno_hex', 'owner_gift', ?)", (uid, int(time.time())))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'keno_ball', 'keno_hex')", (uid,))
    w.server.script(keno=[DRAW] * 3)
    await w.reload()
    await p.ev("window.__marker = 'same-page'")
    await p.send("Emulation.setCPUThrottlingRate", {"rate": 6})
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 чисел")
    check("надет keno_hex", (await p.ev(HEAVY))["skin"], "keno_hex")
    for n in (1, 2, 3):
        await p.tap(".keno-ball:nth-child(%d)" % n)
    await set_bet(p, "keno-bet", 100)
    await p.ev(OBSERVE)
    moves = []
    for i in range(3):
        before = await p.ev("window.__moves")
        await p.tap("#keno-play")
        await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 60, "итог раунда %d" % (i + 1))
        await p.wait("!document.getElementById('keno-play').disabled", 60, "кнопка снова доступна")
        moves.append(await p.ev("window.__moves") - before)
        h = await p.ev(HEAVY)
        check("раунд %d: нет элементов с filter, backdrop-filter и clip-path на экране кено" % (i + 1), [h["filter"], h["backdrop"], h["clip"]], [0, 0, 0])
        check("раунд %d: все 40 шариков нарисованы фоном-SVG" % (i + 1), h["hexImages"], 40)
    check("перерисовки доски не растут от раунда к раунду (%s)" % moves, moves[2] <= moves[0] * 1.25 + 40, True)
    check("перерисовка одного раунда ограничена (не больше 2000 записей)", max(moves) <= 2000, True)
    # после раунда сервер ещё раз синхронизирует состояние (одна перерисовка, при замедленном CPU время плавает), потом доска стоит:
    # среди шести окон по 3 секунды должно найтись окно без перерисовок (периодический цикл перерисовок такого окна не даст)
    windows = []
    for _ in range(6):
        before = await p.ev("window.__moves")
        await p.ev("E.sleep(3000)")
        windows.append(await p.ev("window.__moves") - before)
        if windows[-1] == 0:
            break
    check("в покое доска не перерисовывается (окна по 3 с: %s)" % windows, windows[-1], 0)
    check("страница не перезагружалась", [await p.ev("window.__marker"), await p.ev("performance.getEntriesByType('navigation').length")], ["same-page", 1])
    await p.send("Emulation.setCPUThrottlingRate", {"rate": 1})
