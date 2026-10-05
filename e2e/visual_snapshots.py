"""Визуальный эталон клиента: скриншоты ключевых экранов и состояний на ширинах 320/360/390/430 (высота 700) с детерминированными данными
(числа игр и часы подменены в e2e/server_boot.py, как в обычных сценариях).

  python e2e/visual_snapshots.py --out ПАПКА                    записать снимки (ВНЕ репозитория)
  python e2e/visual_snapshots.py --out НОВАЯ --compare СТАРАЯ   записать и сравнить с прежними попиксельно (допуск 0), код 1 при расхождении
  python e2e/visual_snapshots.py --out ПАПКА --only 390         одна ширина

Перед снимком на миг останавливаются анимации и переходы (иначе бесконечные анимации дают разные кадры), после снимка возвращаются.
Снимки в репозиторий не попадают."""
import asyncio
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WIDTHS = [320, 360, 390, 430]
HEIGHT = 700
FREEZE = "(() => { const s = document.createElement('style'); s.id = 'e2e-freeze'; s.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; scrollbar-width: none !important; } ::-webkit-scrollbar { display: none !important; } #farm-inc-timer, #profile-timer, .ring-fill { visibility: hidden !important; }'; document.head.appendChild(s); })()"
UNFREEZE = "(() => { const s = document.getElementById('e2e-freeze'); if (s) s.remove(); })()"
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}    # без дохода у всех: балансы в рейтинге не зависят от времени


class Shooter:
    def __init__(self, page, folder):
        self.page, self.folder, self.n = page, folder, 0

    async def shot(self, name):
        await self.page.settle(4)
        await asyncio.sleep(1.0)          # докрутка плавных переходов
        await self.page.ev(FREEZE)
        await self.page.settle(3)
        self.n += 1
        path = os.path.join(self.folder, "%02d-%s.png" % (self.n, name))
        await self.page.screenshot(path)
        if os.environ.get("VIS_SELF"):    # отладка устойчивости: второй снимок подряд и снимок после «разморозки»
            await self.page.screenshot(path + ".2")
            if open(path, "rb").read() != open(path + ".2", "rb").read():
                print("ДВА ПОДРЯД РАЗНЫЕ:", name, diff_area(path, path + ".2"))
            os.remove(path + ".2")
            await self.page.ev(UNFREEZE)
            await asyncio.sleep(1.3)
            await self.page.ev(FREEZE)
            await self.page.settle(3)
            await self.page.screenshot(path + ".3")
            if open(path, "rb").read() != open(path + ".3", "rb").read():
                print("ПОСЛЕ РАЗМОРОЗКИ РАЗНЫЕ:", name, diff_area(path, path + ".3"))
            os.remove(path + ".3")
        await self.page.ev(UNFREEZE)


def tab_js(name):
    return ".tab[data-tab=%s]" % name


async def scenario(w, shooter):
    from harness import open_game, set_bet
    p = w.page
    shot = shooter.shot
    w.server.script(spin=[17], keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]], mines=[[0, 1, 2], [0, 1, 2]],
                    hilo=[[7, "H"], [7, "S"], [3, "C"], [2, "D"]],
                    shoe=[["10S", "9H", "10D", "8C"], ["10S", "10H", "6D", "9C"]], crash=[5000, 150])

    await shot("lobby")
    # рулетка
    await open_game(p, "roulette")
    await shot("roulette-start")
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    await shot("roulette-bet")
    await p.tap("#spin")
    await p.wait("document.querySelector('.wheel-layer.active')", 10, "колесо")
    await p.wait("!document.querySelector('.wheel-layer.active') && !document.getElementById('spin').disabled", 40, "вращение закончилось")
    await shot("roulette-result")
    # меню игр
    await p.tap(".tab.main")
    await p.wait("document.getElementById('game-menu').classList.contains('open')", 5, "меню игр")
    await shot("game-menu")
    await p.tap(".tile[data-game=keno]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "кено")
    # кено
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле кено")
    await shot("keno-start")
    for n in (1, 2, 3):
        await p.tap(".keno-ball:nth-child(%d)" % n)
    await set_bet(p, "keno-bet", 100)
    await shot("keno-picked")
    await p.tap("#keno-play")
    await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 30, "итог кено")
    await p.wait("!document.getElementById('keno-play').disabled", 30, "кено готово")
    await shot("keno-result")
    # мины
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма мин")
    await shot("mines-start")
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле мин")
    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("!document.getElementById('mines-cash').disabled", 10, "«Забрать»")
    await shot("mines-play")
    await p.tap("#mines-cash")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "итог мин")
    await shot("mines-win")
    await p.tap("#mines-again")
    await p.wait("!document.getElementById('mines-begin').disabled", 10, "форма мин")
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле мин")
    await p.tap("#mines-grid .mines-cell:nth-child(1)")
    await p.wait("!document.getElementById('mines-result').hidden", 10, "мина")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell.mine').length > 0", 10, "мины показаны")
    await shot("mines-lose")
    # хило
    await open_game(p, "hilo")
    await p.wait("!document.getElementById('hl-bets').hidden", 10, "панель хило")
    await shot("hilo-start")
    await set_bet(p, "hl-bet", 100)
    await p.tap("#hl-start")
    await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия хило")
    await shot("hilo-play")
    await p.tap("#hl-hi")
    await p.wait("E.count('/api/hilo/') >= 2", 10, "ход хило")
    await p.wait("!document.getElementById('hl-skip').disabled && !document.getElementById('hl-actions').hidden", 15, "анимация хило")
    await p.tap("#hl-cash")
    await p.wait("!document.getElementById('hl-banner').hidden && document.getElementById('hl-banner-title').textContent.length > 0", 15, "итог хило")
    await shot("hilo-win")
    await set_bet(p, "hl-bet", 100)
    await p.tap("#hl-start")
    await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия хило")
    await p.tap("#hl-hi")
    await p.wait("!document.getElementById('hl-banner').hidden && document.getElementById('hl-banner-title').textContent.length > 0", 15, "проигрыш хило")
    await shot("hilo-lose")
    # блэкджек
    await open_game(p, "blackjack")
    await shot("blackjack-start")
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled", 15, "панель действий")
    await shot("blackjack-play")
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden && document.getElementById('bj-banner').classList.contains('win')", 20, "победа")
    await shot("blackjack-win")
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled", 15, "панель действий")
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden && !document.getElementById('bj-banner').classList.contains('win')", 20, "проигрыш")
    await shot("blackjack-lose")
    # краш (в полёте картинка зависит от времени: только до старта и итоги авто-режима)
    await open_game(p, "crash")
    await p.wait("!document.getElementById('cr-bets').hidden", 10, "панель краша")
    await shot("crash-start")
    for target, banner, name in (("2", "win", "crash-win"), ("2", "lose", "crash-lose")):
        await set_bet(p, "cr-bet", 100)
        await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = %r; i.dispatchEvent(new Event('input')); })()" % target)
        await p.tap("#cr-start")
        await p.wait("!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('%s')" % banner, 20, name)
        await p.wait("!document.getElementById('cr-bets').hidden && !document.getElementById('cr-start').disabled", 20, "раунд закончен")
        await shot(name)
    # вкладки и переводы
    for tab in ("farm", "rating", "profile"):
        await p.tap(tab_js(tab))
        await p.wait("!document.querySelector('.screen:not([hidden]) .skeleton-card:not([hidden])')", 15, "экран загружен: " + tab)
        await shot(tab)
    await p.tap(tab_js("rating"))
    await p.wait("document.querySelectorAll('#rating-list li').length >= 2", 10, "рейтинг")
    await p.ev("[...document.querySelectorAll('#rating-list li')].find(l => l.textContent.includes('bob')).id = 'e2e-bob'")
    await p.tap("#e2e-bob")
    await p.wait("!document.getElementById('transfer-sheet').hidden", 5, "окно перевода")
    await shot("transfer")
    await p.tap("#transfer-change")
    await p.wait("!document.getElementById('picker-sheet').hidden && document.querySelectorAll('#picker-list li').length >= 1", 10, "выбор получателя")
    await p.ev("(() => { const i = document.getElementById('picker-search'); i.value = 'bob'; i.dispatchEvent(new Event('input')); })()")
    await p.wait("document.querySelectorAll('#picker-list li').length === 1", 10, "поиск по имени")
    await shot("picker")


async def run_width(harness, chrome, width, folder):
    os.makedirs(folder, exist_ok=True)
    world = harness.World(chrome, users=USERS, viewport=(width, HEIGHT), clock_mod=30)
    world.owner.rate = 0
    try:
        await world.start()
        await scenario(world, Shooter(world.page, folder))
        if world.page.problems:
            raise harness.E2EError("консоль: " + " | ".join(world.page.problems[:3]))
    finally:
        await world.stop()


def png_rows(path):
    """Пиксели PNG без внешних библиотек (8 бит, RGB или RGBA, без чересстрочности): (ширина, высота, список строк-bytes)."""
    import struct
    import zlib
    data = open(path, "rb").read()
    pos, idat, width, height, ctype = 8, b"", 0, 0, 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype in (2, 6) and inter == 0, "формат PNG не поддержан"
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    bpp = 4 if ctype == 6 else 3
    raw, stride, rows, prev = zlib.decompress(idat), width * bpp, [], bytearray(width * bpp)
    for y in range(height):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return width, height, rows, bpp


def diff_area(pa, pb):
    """Описание расхождения двух снимков: число отличающихся пикселей и рамка вокруг них."""
    wa, ha, ra, bpp = png_rows(pa)
    wb, hb, rb, _ = png_rows(pb)
    if (wa, ha) != (wb, hb):
        return "размеры %dx%d и %dx%d" % (wa, ha, wb, hb)
    count, x0, y0, x1, y1 = 0, wa, ha, -1, -1
    for y in range(ha):
        if ra[y] == rb[y]:
            continue
        for x in range(wa):
            if ra[y][x * bpp:(x + 1) * bpp] != rb[y][x * bpp:(x + 1) * bpp]:
                count += 1
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    return "%d пикселей, рамка x %d..%d, y %d..%d" % (count, x0, x1, y0, y1)


def compare(new_dir, old_dir):
    """Побайтовое сравнение PNG (одинаковые пиксели дают одинаковый файл); при расхождении считается число отличающихся пикселей и рамка (свой разбор PNG, без Pillow)."""
    bad = []
    names = sorted(set(os.listdir(old_dir)) | set(os.listdir(new_dir)))
    for width_dir in names:
        a, b = os.path.join(old_dir, width_dir), os.path.join(new_dir, width_dir)
        if not (os.path.isdir(a) and os.path.isdir(b)):
            if os.path.isdir(a) or os.path.isdir(b):
                bad.append("%s: нет в одной из папок" % width_dir)
            continue
        for f in sorted(set(os.listdir(a)) | set(os.listdir(b))):
            pa, pb = os.path.join(a, f), os.path.join(b, f)
            if not (os.path.exists(pa) and os.path.exists(pb)):
                bad.append("%s/%s: нет в одной из папок" % (width_dir, f))
            elif open(pa, "rb").read() != open(pb, "rb").read():
                bad.append("%s/%s: отличается (%s)" % (width_dir, f, diff_area(pa, pb)))
    return bad


async def main(argv):
    out = argv[argv.index("--out") + 1]
    old = argv[argv.index("--compare") + 1] if "--compare" in argv else None
    only = [int(argv[argv.index("--only") + 1])] if "--only" in argv else WIDTHS
    import harness
    if "--client-dir" in argv:
        harness.CLIENT_ROOT = os.path.abspath(argv[argv.index("--client-dir") + 1])
    chrome_path = harness.find_chrome()
    if chrome_path is None or harness.websockets is None:
        print("Нужны Chrome и websockets")
        return 1
    tmp = tempfile.mkdtemp(prefix="e2e-vis-")
    chrome = harness.Chrome(chrome_path, tmp)
    try:
        for width in only:
            await run_width(harness, chrome, width, os.path.join(out, str(width)))
            print("ширина %d: %d снимков" % (width, len(os.listdir(os.path.join(out, str(width))))), flush=True)
    finally:
        chrome.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    if old:
        bad = compare(out, old)
        for line in bad:
            print("РАСХОЖДЕНИЕ", line)
        print("Сравнение с %s: %s" % (old, "расхождений нет" if not bad else "расхождений %d" % len(bad)))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
