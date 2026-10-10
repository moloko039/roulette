"""Сцена скина кено «Яблоки» (skins/keno_apple.js, skins/keno_apple.css, коллекция «Листопад»).
Проверяется: сцена монтируется в #keno-board при открытии кено с надетым скином, css скина подключён;
уход на другую вкладку и снятие скина убирают сцену и css;
розыгрыш: яблоки-частицы летят с ветки (от 1 до 12 в полёте, 0 после), совпадения наполняют корзину;
reduced-motion и perf-lite выключают полёт частиц (0 частиц)."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_keno_apple"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#keno-board .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/keno_apple.css\"]').length"
PARTICLES = "document.querySelectorAll('.ska-p.on').length"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

DRAW_WIN = [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]
DRAW_ZERO = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'keno_apple', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'keno_ball', 'keno_apple')", (uid,))
    await w.reload()

    check("до открытия экрана кено сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле из 40 шариков")
    await p.wait("!!document.querySelector('.ska-branch')", 10, "ветка смонтирована")
    await p.wait("!!document.querySelector('.ska-basket')", 10, "корзина смонтирована")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("сцена смонтирована, css подключён, data-scene установлена",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('keno-board').dataset.scene")],
          [1, 1, "keno_apple"])
    check("сцена не пуста", await p.ev("document.querySelector('#keno-board .skin-scene').children.length > 0"), True)
    check("декор корзины виден на экране кено", await p.ev("""(() => {
        const b = document.querySelector('.ska-basket');
        const r = b ? b.getBoundingClientRect() : null;
        return !!(r && r.width > 0 && r.height > 0 && getComputedStyle(b).display !== 'none' && getComputedStyle(b).visibility !== 'hidden');
    })()"""), True)
    check("изоляция экрана кено и z-index декора не выше 2", await p.ev("""(() => {
        const screen = document.querySelector('[data-screen="keno"]');
        const basket = document.querySelector('.ska-basket');
        const branch = document.querySelector('.ska-branch');
        const fx = document.querySelector('.ska-fx');
        const iso = getComputedStyle(screen).isolation;
        const bZ = parseInt(getComputedStyle(basket).zIndex, 10);
        const brZ = parseInt(getComputedStyle(branch).zIndex, 10);
        const fxZ = parseInt(getComputedStyle(fx).zIndex, 10);
        return iso === 'isolate' && bZ <= 2 && brZ <= 2 && fxZ <= 2;
    })()"""), True)

    # --- меню выбора игр: декор под панелью, elementFromPoint возвращает элемент панели, а не корзину
    await p.tap(".tab.main")
    await p.wait("document.getElementById('game-menu').classList.contains('open')", 5, "меню выбора игр открыто")
    await asyncio.sleep(0.3)
    check("при открытом меню выбора игр декор не поверх листа: elementFromPoint в центре панели возвращает элемент панели, а не декор корзины",
          await p.ev("""(() => {
              const panel = document.getElementById('game-panel');
              const r = panel.getBoundingClientRect();
              const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return !!(el && panel.contains(el) && !el.closest('.ska-basket'));
          })()"""), True)
    await p.tap(".tab.main")
    await p.wait("!document.getElementById('game-menu').classList.contains('open')", 5, "меню выбора игр закрыто")
    await asyncio.sleep(0.3)
    check("после закрытия меню выбора игр декор виден на экране кено", await p.ev("""(() => {
        const b = document.querySelector('.ska-basket');
        const r = b ? b.getBoundingClientRect() : null;
        return !!(r && r.width > 0 && r.height > 0 && getComputedStyle(b).display !== 'none' && getComputedStyle(b).visibility !== 'hidden');
    })()"""), True)

    for n in (1, 2, 3):
        await p.tap("button.keno-ball:nth-of-type(%d)" % n)
    check("выбрано 3 числа", await p.ev("document.querySelectorAll('.keno-ball.sel').length"), 3)

    # --- раунд 1: выигрыш (совпало 3 из 3), частицы в полёте от 1 до 12, 0 после; корзина наполняется
    w.server.script(keno=[DRAW_WIN])
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("document.querySelectorAll('.ska-p.on').length >= 1", 5, "частица яблока в полёте")
    seen = await p.ev(PARTICLES)
    check("яблоки летят: частиц от 1 до 12", 1 <= seen <= 12, True)
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 1 завершён")
    await p.wait(PARTICLES + " === 0", 4, "все частицы вернулись в пул")
    check("после окончания розыгрыша все частицы вернулись в пул", await p.ev(PARTICLES), 0)
    check("3 совпадения на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 3)
    check("в корзине 3 яблока", await p.ev("document.querySelectorAll('.ska-b-apple.on').length"), 3)

    # --- раунд 2: 0 совпадений при picks > 0: корзина пустеет
    w.server.script(keno=[DRAW_ZERO])
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд 2 завершён")
    await p.wait(PARTICLES + " === 0", 4, "последнее яблоко долетело (допустимо до 4 с после конца раунда на медленной машине)")
    check("после окончания розыгрыша частиц 0", await p.ev(PARTICLES), 0)
    check("0 совпадений на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 0)
    check("ноль совпадений: корзина пуста", await p.ev("document.querySelectorAll('.ska-b-apple.on').length"), 0)

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
    await p.wait(PARTICLES + " === 0", 4, "частицы убраны")
    check("perf-lite: после окончания частиц 0", await p.ev(PARTICLES), 0)
    check("perf-lite: в корзине 3 яблока без анимации", await p.ev("document.querySelectorAll('.ska-b-apple.on').length"), 3)

    # --- переключение вкладок: уход убирает сцену и css, возврат восстанавливает
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята", await p.ev("document.getElementById('keno-board').dataset.scene || ''"), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "возврат в кено")
    await p.wait("!!document.querySelector('.ska-branch')", 10, "сцена вернулась")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])

    # --- снятие скина: в DOM ничего не остаётся
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'keno_ball'", (uid,))
    await w.reload()
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 15, "кено без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('keno-board').dataset.scene || ''")],
          [0, 0, ""])
