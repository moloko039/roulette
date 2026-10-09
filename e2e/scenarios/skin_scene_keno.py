"""Сцена скина кено «Бочонки» (skins/keno_lotto.js, skins/keno_lotto.css, коллекция «Дачный сезон»).
Проверяется: сцена монтируется в #keno-board при открытии кено с надетым скином, css скина подключён;
уход на другую вкладку и снятие скина убирают сцену и css;
розыгрыш: бочонки-частицы летят из мешка (от 1 до 12 в полёте, 0 после), при 0 совпадений мешок сникает;
reduced-motion и perf-lite выключают полёт частиц (0 частиц)."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_keno"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#keno-board .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/keno_lotto.css\"]').length"
PARTICLES = "document.querySelectorAll('.skl-p.on').length"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

DRAW_WIN = [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]
DRAW_ZERO = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'keno_lotto', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'keno_ball', 'keno_lotto')", (uid,))
    await w.reload()

    check("до открытия экрана кено сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 шариков")
    await p.wait("!!document.querySelector('.skl-bag')", 10, "мешок смонтирован")
    check("сцена смонтирована, css подключён, data-scene установлена",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('keno-board').dataset.scene")],
          [1, 1, "keno_lotto"])
    check("сцена не пуста", await p.ev("document.querySelector('#keno-board .skin-scene').children.length > 0"), True)

    for n in (1, 2, 3):
        await p.tap("button.keno-ball:nth-of-type(%d)" % n)
    check("выбрано 3 числа", await p.ev("document.querySelectorAll('.keno-ball.sel').length"), 3)

    # --- раунд 1: выигрыш (совпало 3 из 3), частицы в полёте от 1 до 12, 0 после; мешок не сникает
    w.server.script(keno=[DRAW_WIN])
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("document.querySelectorAll('.skl-p.on').length >= 1", 5, "частица бочонка в полёте")
    seen = await p.ev(PARTICLES)
    check("бочонки летят: частиц от 1 до 12", 1 <= seen <= 12, True)
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 1 завершён")
    check("после окончания розыгрыша все частицы вернулись в пул", await p.ev(PARTICLES), 0)
    check("при выигрыше мешок не сник", await p.ev("document.querySelector('.skl-bag').classList.contains('slump')"), False)
    check("3 совпадения", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 3)

    # --- раунд 2: 0 совпадений при picks > 0: мешок сникает
    w.server.script(keno=[DRAW_ZERO])
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 2 завершён")
    check("после окончания розыгрыша частиц 0", await p.ev(PARTICLES), 0)
    check("ноль совпадений: мешок сник", await p.ev("document.querySelector('.skl-bag').classList.contains('slump')"), True)

    # --- раунд 3: reduced-motion: частиц в полёте нет
    await p.send("Emulation.setEmulatedMedia", STILL)
    w.server.script(keno=[DRAW_WIN])
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 3 завершён")
    check("reduced-motion: частиц нет", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # --- раунд 4: perf-lite: частиц в полёте нет
    await p.ev("skinSetLite()")
    check("perf-lite включён", await p.ev("skinRt.lite"), True)
    w.server.script(keno=[DRAW_WIN])
    await p.tap("#keno-play")
    await p.wait("kn.busy", 5, "раунд 4 начался")
    await asyncio.sleep(0.5)
    check("perf-lite: во время розыгрыша частиц нет", await p.ev(PARTICLES), 0)
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 4 завершён")
    check("perf-lite: после окончания частиц 0", await p.ev(PARTICLES), 0)

    # --- переключение вкладок: уход убирает сцену и css, возврат восстанавливает
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята", await p.ev("document.getElementById('keno-board').dataset.scene || ''"), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "возврат в кено")
    await p.wait("!!document.querySelector('.skl-bag')", 10, "сцена вернулась")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])

    # --- снятие скина: в DOM ничего не остаётся
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'keno_ball'", (uid,))
    await w.reload()
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 15, "кено без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('keno-board').dataset.scene || ''")],
          [0, 0, ""])
