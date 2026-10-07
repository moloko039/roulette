"""Прокрутка вкладок в настольном режиме (Telegram Desktop): окно без касаний, колесо мыши и клавиши через CDP (Input.dispatchMouseEvent mouseWheel,
Input.dispatchKeyEvent), окна 320x568, 380x640 и 450x750. Для каждой из пяти вкладок: при открытии scrollTop = 0 и верх содержимого не обрезан (раньше
у «Профиля» центрирование flex при переполнении уводило шапку выше начала прокрутки, до неё нельзя было долистать), первое колёсико вниз и вверх
работает, колёсиком, клавишами End и Home можно дойти до самого низа и верха, нижний край содержимого виден. «Рейтинг»: внутренний список 340 px
не ловит колесо (прокрутка передаётся странице). Затем тач-проверка: пальцем (synthesizeScrollGesture touch) на телефонном окне те же вкладки прокручиваются."""
import asyncio

from harness import check

NAME = "desktop_scroll"
CLOCK_MOD = 1      # минутная граница начисления далеко (59 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = dict({"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}, **{"игрок %d" % i: {"balance": 1000 + i, "rate": 0} for i in range(12)})
SIZES = [(320, 568), (380, 640), (450, 750)]
TABS = ["rating", "style", "play", "farm", "profile"]
READY = {
    "rating": "document.querySelectorAll('#rating-list li').length >= 10 && !document.getElementById('best-card').hidden",
    "style": "!document.querySelector('[data-screen=style]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0",
    "play": "!document.querySelector('.lobby').classList.contains('booting') && !document.querySelector('[data-screen=lobby]').hidden",
    "farm": "!document.getElementById('farm-body').hidden",
    "profile": "!document.getElementById('profile-data').hidden",
}

# метрики видимого экрана: его прокрутка, видны ли первый и последний видимые потомки (не обрезаны выше начала и ниже конца содержимого)
METRICS = """(() => {
  const s = document.querySelector('.screen:not([hidden])');
  const kids = [...s.children].filter((e) => getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0);
  const r = s.getBoundingClientRect();
  const cs = getComputedStyle(s);
  const padTop = parseFloat(cs.paddingTop), padBottom = parseFloat(cs.paddingBottom);
  return { name: s.dataset.screen, top: s.scrollTop, max: s.scrollHeight - s.clientHeight, h: s.clientHeight, sh: s.scrollHeight,
           first: kids.length ? kids[0].getBoundingClientRect().top - (r.top + padTop) : 0,
           last: kids.length ? (r.bottom - padBottom) - kids[kids.length - 1].getBoundingClientRect().bottom : 0,
           doc: document.scrollingElement.scrollTop, x: s.scrollWidth - s.clientWidth };
})()"""


async def desktop(p, w, h):
    await p.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
    await p.send("Emulation.setTouchEmulationEnabled", {"enabled": False})


async def phone(p, w, h):
    await p.viewport(w, h)


async def click(p, sel):
    """Клик мышью (без касаний) по центру элемента."""
    c = await p.center(sel)
    await p.mouse("mouseMoved", c)
    await p.mouse("mousePressed", c)
    await p.mouse("mouseReleased", c)
    await p.settle()


async def wheel(p, x, y, dy):
    await p.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy})
    await asyncio.sleep(0.08)        # плавная прокрутка Chrome доезжает за несколько кадров
    await p.settle(2)


async def metrics(p):
    return await p.ev(METRICS)


async def to_end(p, x, y, direction, limit=80):
    """Крутит колесо в одну сторону, пока scrollTop не перестанет меняться (три замера подряд); возвращает метрики."""
    last, still = None, 0
    for _ in range(limit):
        await wheel(p, x, y, 600 * direction)
        top = (await metrics(p))["top"]
        still = still + 1 if top == last else 0
        last = top
        if still >= 3:
            break
    return await metrics(p)


async def open_tab(w, tab, mouse=True):
    p = w.page
    if mouse:
        await click(p, ".tab[data-tab=%s]" % tab)
    else:
        await p.tap(".tab[data-tab=%s]" % tab)
    await p.wait(READY[tab], 20, "вкладка загружена: " + tab)
    await p.settle(3)
    await asyncio.sleep(0.4)


async def check_tab(w, tab, size):
    p = w.page
    label = "%s %dx%d" % (tab, size[0], size[1])
    await open_tab(w, tab)
    m = await metrics(p)
    scrollable = m["max"] > 1
    check("%s: открыта в начале (scrollTop 0, страница не прокручена, нет горизонтальной прокрутки)" % label, [m["top"], m["doc"], m["x"] <= 0], [0, 0, True])
    check("%s: верх содержимого не обрезан (первый блок на %d px выше начала при scrollTop 0)" % (label, round(m["first"])), m["first"] >= -0.5, True)
    x, y = 6, size[1] // 3          # мышь над полем экрана (отступ 16 px): вложенных прокруток под ней нет
    if not scrollable:
        check("%s: не прокручивается, нижний край виден" % label, m["last"] >= -0.5, True)
        return m
    await wheel(p, x, y, 120)
    first = await metrics(p)
    check("%s: первое колёсико вниз прокручивает" % label, first["top"] > 0, True)
    await wheel(p, x, y, -120)
    check("%s: первое колёсико вверх возвращает к началу" % label, (await metrics(p))["top"], 0)
    end = await to_end(p, x, y, 1)
    check("%s: колёсиком доходит до самого низа (scrollTop = максимум), нижний край виден" % label, [end["top"] >= end["max"] - 1, end["last"] >= -0.5], [True, True])
    top = await to_end(p, x, y, -1)
    check("%s: колёсиком возвращается к самому верху, верх не обрезан" % label, [top["top"], top["first"] >= -0.5], [0, True])
    # клавиши: клик по полю экрана (не по полосе прокрутки справа) даёт ему фокус прокрутки
    await p.mouse("mouseMoved", {"x": 6, "y": size[1] // 2})
    await p.mouse("mousePressed", {"x": 6, "y": size[1] // 2})
    await p.mouse("mouseReleased", {"x": 6, "y": size[1] // 2})
    await p.key("End", "End", 35)
    await asyncio.sleep(0.4)
    k_end = await metrics(p)
    check("%s: клавиша End доводит до низа" % label, k_end["top"] >= k_end["max"] - 1, True)
    await p.key("Home", "Home", 36)
    await asyncio.sleep(0.4)
    check("%s: клавиша Home возвращает к верху" % label, (await metrics(p))["top"], 0)
    await p.key("PageDown", "PageDown", 34)
    await asyncio.sleep(0.5)
    check("%s: PageDown прокручивает" % label, (await metrics(p))["top"] > 0, True)
    await p.key("Home", "Home", 36)
    await asyncio.sleep(0.4)
    return m


async def run(w):
    p = w.page
    for size in SIZES:
        await desktop(p, *size)
        await w.reload()
        for tab in TABS:
            await check_tab(w, tab, size)
    # вложенная прокрутка «Рейтинга»: колесо над внутренним списком сначала листает его, затем передаёт странице
    size = (380, 640)
    await desktop(p, *size)
    await w.reload()
    await open_tab(w, "rating")
    box = await p.ev("(() => { const r = document.getElementById('rating-list').getBoundingClientRect(); const l = document.getElementById('rating-list'); "
                     "return { x: Math.round(r.left + r.width / 2), y: Math.round(Math.max(r.top, 0) + 30), inner: l.scrollHeight - l.clientHeight }; })()")
    check("список основного рейтинга листается внутри (выше 340 px)", box["inner"] > 0, True)
    for _ in range(25):
        await wheel(p, box["x"], box["y"], 600)
    m = await metrics(p)
    check("колесо над внутренним списком дошло до низа страницы (не застряло в списке)", [m["top"] >= m["max"] - 1, m["last"] >= -0.5], [True, True])
    inner = await p.ev("(() => { const l = document.getElementById('rating-list'); return l.scrollTop >= l.scrollHeight - l.clientHeight - 1; })()")
    check("и внутренний список долистан до конца", inner, True)

    # тач-проверка на телефонном окне: палец прокручивает те же вкладки
    await phone(p, 390, 568)
    await w.reload()
    for tab in ("rating", "farm", "profile"):
        await open_tab(w, tab, mouse=False)
        m = await metrics(p)
        check("тач: %s открыта в начале" % tab, [m["top"], m["first"] >= -0.5], [0, True])
        if m["max"] <= 1:
            continue
        await p.send("Input.synthesizeScrollGesture", {"x": 8, "y": 300, "yDistance": -400, "gestureSourceType": "touch", "speed": 1200})
        moved = await metrics(p)
        for _ in range(20):                # жест на медленной машине (CI) доходит позже: ждём до 4 секунд, а не фиксированные 0,4
            if moved["top"] > 0:
                break
            await asyncio.sleep(0.2)
            moved = await metrics(p)
        check("тач: %s прокручивается пальцем вверх" % tab, moved["top"] > 0, True)
        await p.send("Input.synthesizeScrollGesture", {"x": 8, "y": 300, "yDistance": 1200, "gestureSourceType": "touch", "speed": 1200})
        back = await metrics(p)
        for _ in range(20):
            if back["top"] == 0:
                break
            await asyncio.sleep(0.2)
            back = await metrics(p)
        check("тач: %s возвращается к началу пальцем вниз" % tab, back["top"], 0)
