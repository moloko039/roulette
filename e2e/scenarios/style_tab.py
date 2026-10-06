"""Вкладка «Стиль» (гардероб внутри пятой вкладки нижней панели): данные берутся с сервера при открытии вкладки, а не при старте; в шапке профиля кнопки
«Гардероб» нет и пустого места на её месте нет; выбранный слот помнится на сессию (в памяти, не в localStorage) и сохраняется при уходе и возврате; каждое
открытие снова спрашивает сервер; сетка карточек не прокручивается внутри себя; в узком настольном окне (колесо, без касаний) вкладка при открытии стоит в
начале, листается до самого низа и верха, верх не обрезан. Лист предпросмотра остаётся листом поверх вкладки."""
import asyncio

from harness import check

from scenarios.desktop_scroll import desktop, metrics, phone, to_end, wheel

NAME = "style_tab"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}
LABELS = ["Рубашка карт", "Фишки", "Стол", "Иконки мин", "Шарики кено", "Краш", "Рамка аватара", "Значок"]
READY = "!document.querySelector('[data-screen=style]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0"


async def run(w):
    p = w.page
    check("при старте приложения данные гардероба не запрашиваются", await p.ev("[E.count('/api/cosmetics/catalog'), E.count('/api/cosmetics/mine')]"), [0, 0])
    keys_before = await p.ev("Object.keys(localStorage).sort()")

    # --- профиль без кнопки «Гардероб», шапка без пустого места
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль")
    check("в профиле нет кнопки и слова «Гардероб»", await p.ev("[!!document.getElementById('wardrobe-open'), document.querySelector('[data-screen=profile]').textContent.includes('Гардероб')]"), [False, False])
    head = await p.ev("(() => { const h = document.querySelector('.profile-head'); const r = h.getBoundingClientRect(); const kids = [...h.children].filter((e) => !e.hidden && e.getBoundingClientRect().width > 0);"
                      " return { kids: kids.length, last: Math.round(Math.max(...kids.map((e) => e.getBoundingClientRect().right))), right: Math.round(r.right), left: Math.round(r.left), centered: Math.abs((r.left + r.right) / 2 - innerWidth / 2) < 2 }; })()")
    check("шапка профиля: аватар и имя, по центру, пустого хвоста справа нет", [head["kids"], head["centered"], head["right"] - head["last"] <= 1], [2, True, True])
    check("при открытии профиля гардероб не запрашивался", await p.ev("E.count('/api/cosmetics/catalog')"), 0)

    # --- открытие вкладки «Стиль»: данные с сервера при открытии
    await p.tap(".tab[data-tab=style]")
    await p.wait(READY, 15, "вкладка «Стиль» открыта")
    check("после открытия по одному запросу каталога и «моего»", await p.ev("[E.count('/api/cosmetics/catalog'), E.count('/api/cosmetics/mine')]"), [1, 1])
    check("заголовок и подпись вкладки", await p.ev("[document.querySelector('[data-screen=style] h2').textContent.trim(), document.querySelector('.tab[data-tab=style]').getAttribute('aria-current')]"), ["Стиль", "page"])
    check("восемь слотов с русскими названиями", await p.ev("[...document.querySelectorAll('#wd-tabs .wd-tab')].map(b => b.textContent)"), LABELS)
    check("переключатель показа, «Условия покупки» и строка про игру и Stars на месте", await p.ev(
        "[document.getElementById('wd-vis').textContent.trim(), document.getElementById('wd-terms').textContent.trim(), document.querySelector('.wd-foot-note').textContent.trim()]"),
        ["Показывать мои рамку и значок в рейтинге", "Условия покупки", "Предметы не влияют на игру. Фишки за Stars не продаются."])
    check("сетка не прокручивается внутри себя", await p.ev("(() => { const g = document.getElementById('wd-grid'); return [getComputedStyle(g).overflowY, g.scrollHeight - g.clientHeight]; })()"), ["visible", 0])
    check("лист предпросмотра не открыт, он остаётся листом поверх вкладки", await p.ev("[document.getElementById('wd-prev-sheet').hidden, document.getElementById('wd-prev-sheet').classList.contains('transfer-sheet')]"), [True, True])
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр поверх вкладки")
    check("вкладка осталась под листом", await p.ev("!document.querySelector('[data-screen=style]').hidden"), True)
    await p.tap("#wd-prev-close")

    # --- слот помнится на сессию
    await p.tap("#wd-tabs .wd-tab:nth-child(5)")
    check("выбран слот «Шарики кено»", await p.ev("[wd.slot, document.querySelector('#wd-tabs .wd-tab[aria-selected=true]').textContent]"), ["keno_ball", "Шарики кено"])
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "рейтинг")
    await p.tap(".tab[data-tab=style]")
    await p.wait(READY, 15, "вкладка открыта снова")
    check("после ухода и возврата слот прежний", await p.ev("[wd.slot, document.querySelector('#wd-tabs .wd-tab[aria-selected=true]').textContent]"), ["keno_ball", "Шарики кено"])
    check("каждое открытие вкладки снова берёт данные с сервера", await p.ev("[E.count('/api/cosmetics/catalog'), E.count('/api/cosmetics/mine')]"), [2, 2])
    check("в localStorage ничего не появилось", await p.ev("Object.keys(localStorage).sort()"), keys_before)

    # --- узкое настольное окно: колесо, начало и конец, верх не обрезан
    await desktop(p, 320, 568)
    await w.reload()
    await p.tap(".tab[data-tab=style]")
    await p.wait(READY, 15, "вкладка открыта в узком окне")
    await asyncio.sleep(0.4)
    m = await metrics(p)
    check("открыта в начале, верх не обрезан, нет горизонтальной прокрутки", [m["top"], m["first"] >= -0.5, m["x"] <= 0, m["doc"]], [0, True, True, 0])
    check("содержимое выше окна (есть что листать)", m["max"] > 40, True)
    end = await to_end(p, 6, 190, 1)
    check("колесом доходит до самого низа, нижний край виден", [end["top"] >= end["max"] - 1, end["last"] >= -0.5], [True, True])
    check("внизу видны «Условия покупки»", await p.ev("(() => { const r = document.getElementById('wd-terms').getBoundingClientRect(); const s = document.querySelector('[data-screen=style]').getBoundingClientRect(); return r.bottom <= s.bottom + 0.5 && r.top >= s.top; })()"), True)
    top = await to_end(p, 6, 190, -1)
    check("колесом возвращается к самому верху", [top["top"], top["first"] >= -0.5], [0, True])
    # колесо над сеткой карточек тоже листает страницу (сетка не ловит его)
    box = await p.ev("(() => { const r = document.getElementById('wd-grid').getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(Math.min(Math.max(r.top + 30, 60), 500)) }; })()")
    await wheel(p, box["x"], box["y"], 300)
    check("колесо над сеткой прокручивает страницу", (await metrics(p))["top"] > 0, True)
    await phone(p, 390, 700)
