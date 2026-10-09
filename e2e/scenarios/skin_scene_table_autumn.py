"""Сцена скина стола «Октябрь» (skins/table_autumn.js, skins/table_autumn.css, коллекция «Листопад»).
Проверяется: сцена монтируется в #table при открытии рулетки с надетым скином, css скина подключён;
уход на другую вкладку и снятие скина убирают сцену и css;
исходное число листьев 6-8;
события round:end добавляют по листу в кучу до 10 (одиннадцатый не добавляется);
после ухода с экрана и возврата куча сохраняется;
reduced-motion и perf-lite: листья появляются без анимации, падающих нет, порыва нет;
skin:effect {set: 'leaves'} даёт порыв ветра (частиц от 1 до 12, затем 0)."""
import asyncio
import time

from harness import check, open_game

NAME = "skin_scene_table_autumn"
USERS = {"me": {"rate": 0}}
SCENE = "document.querySelectorAll('#table .skin-scene').length"
LINK = "document.querySelectorAll('link[href*=\"skins/table_autumn.css\"]').length"
BASE_LEAVES = "document.querySelectorAll('#table .sta-edge-leaf').length"
PILE_LEAVES = "document.querySelectorAll('#table .sta-pile-leaf').length"
PARTICLES = "document.querySelectorAll('#table .sta-p.on').length"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'table_autumn', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'table', 'table_autumn')", (uid,))
    for code in ("back_leaves", "mine_acorn", "chip_leaf", "keno_apple", "crash_maple", "frame_wreath", "badge_pumpkin"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, 'free', NULL, ?)", (uid, code, now))
    await w.reload()

    check("до открытия экрана рулетки сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])

    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "стол рулетки загружен")
    await p.wait("!!document.querySelector('#table .sta-edge-leaves')", 10, "сцена стола смонтирована")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("сцена смонтирована, css подключён, data-scene установлена",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('table').dataset.scene")],
          [1, 1, "table_autumn"])
    check("сцена не пуста", await p.ev("document.querySelector('#table .skin-scene').children.length > 0"), True)

    # 1. Исходное число листьев по краям стола: 6-8, куча в начале пуста
    base_count = await p.ev(BASE_LEAVES)
    check("исходное число листьев 6-8", 6 <= base_count <= 8, True)
    check("в начале куча пуста (0 листьев)", await p.ev(PILE_LEAVES), 0)

    # 2. События round:end добавляют по листу в кучу до 10 (одиннадцатый не добавляется)
    for r in range(1, 9):
        await p.ev("skinEvents.emit('round:end', { game: 'roulette' })")
        check(f"после раунда {r} в куче {r} листьев", await p.ev(PILE_LEAVES), r)

    # Раунд 9 при reduced-motion: лист появляется без анимации
    await p.send("Emulation.setEmulatedMedia", STILL)
    await p.ev("skinEvents.emit('round:end', { game: 'roulette' })")
    check("после раунда 9 в куче 9 листьев", await p.ev(PILE_LEAVES), 9)
    check("reduced-motion: лист появился без анимации", await p.ev("document.querySelectorAll('#table .sta-pile-leaf')[8].getAnimations().length"), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # Раунд 10 и 11
    await p.ev("skinEvents.emit('round:end', { game: 'roulette' })")
    check("после раунда 10 в куче 10 листьев", await p.ev(PILE_LEAVES), 10)

    await p.ev("skinEvents.emit('round:end', { game: 'roulette' })")
    check("одиннадцатый не добавляется: в куче по-прежнему 10 листьев", await p.ev(PILE_LEAVES), 10)

    # 3. Переключение вкладок: уход с экрана и возврат — куча та же
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на другой вкладке сцены и css скина нет", [await p.ev(SCENE), await p.ev(LINK)], [0, 0])
    check("data-scene снята со стола", await p.ev("document.getElementById('table').dataset.scene || ''"), "")

    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "возврат в рулетку")
    await p.wait("!!document.querySelector('#table .sta-edge-leaves')", 10, "сцена стола вернулась")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата сцена одна и css подключён", [await p.ev(SCENE), await p.ev(LINK)], [1, 1])
    check("после возврата в куче те же 10 листьев", await p.ev(PILE_LEAVES), 10)
    check("число листьев по краям сохранилось (6-8)", 6 <= await p.ev(BASE_LEAVES) <= 8, True)

    # 4. Эффект полного набора в обычном режиме: порыв ветра (частиц от 1 до 12, потом 0 за <= 4 с)
    check("собран полный набор «Листопад»", await p.ev("skinHasSet('leaves')"), True)
    await p.ev("skinEvents.emit('skin:effect', { set: 'leaves' })")
    await p.wait("document.querySelectorAll('#table .sta-p.on').length >= 1", 5, "порыв ветра начался")
    gust_count = await p.ev(PARTICLES)
    check("порыв ветра: частиц от 1 до 12", 1 <= gust_count <= 12, True)
    await p.wait(PARTICLES + " === 0", 4, "все частицы порыва завершили полёт")
    check("после порыва все частицы вернулись в пул", await p.ev(PARTICLES), 0)

    # 5. reduced-motion: падающих листьев нет, skin:effect не даёт порыва
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("reduced-motion: падающих листьев нет", await p.ev(PARTICLES), 0)
    await p.ev("skinEvents.emit('skin:effect', { set: 'leaves' })")
    check("reduced-motion: порыва ветра нет (0 частиц)", await p.ev(PARTICLES), 0)
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    # 6. perf-lite: падающих листьев нет, порыва нет
    await p.ev("skinSetLite()")
    check("perf-lite включён", await p.ev("skinRt.lite"), True)
    check("perf-lite: падающих листьев нет", await p.ev(PARTICLES), 0)
    await p.ev("skinEvents.emit('skin:effect', { set: 'leaves' })")
    check("perf-lite: порыва ветра нет", await p.ev(PARTICLES), 0)

    # 7. Снятие скина: в DOM ничего не остаётся
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'table'", (uid,))
    await w.reload()
    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 15, "рулетка без скина")
    check("скин снят: сцены, css и меток нет",
          [await p.ev(SCENE), await p.ev(LINK), await p.ev("document.getElementById('table').dataset.scene || ''")],
          [0, 0, ""])
