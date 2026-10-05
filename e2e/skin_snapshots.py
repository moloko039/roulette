"""Снимки восьми скинов косметики для просмотра глазами: на каждый скин нужный экран с надетым предметом на ширинах 360 и 390.

  python e2e/skin_snapshots.py --out ~/roulette-skin-shots [--only back_midnight] [--client-dir КОПИЯ]

Снимки кладутся ВНЕ репозитория (репозиторий публичный, PNG в него не попадают). Данные детерминированы (подмена чисел игр и часов в e2e/server_boot.py);
перед снимком анимации и переходы замораживаются, как в visual_snapshots.py."""
import asyncio
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WIDTHS = [360, 390]
HEIGHT = 700
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}
SLOT_OF = {"back_midnight": "card_back", "chip_ring": "chip", "table_blue": "table", "mine_star": "mine_icons", "keno_hex": "keno_ball",
           "crash_neon": "crash", "frame_thin": "avatar_frame", "badge_spade": "badge"}


async def steps(w, skin, shot):
    from harness import open_game, set_bet
    p = w.page
    if skin == "table_blue":
        w.server.script(spin=[17])
        await open_game(p, "roulette")
        await shot("roulette-table")
        await set_bet(p, "amount", 10)
        await p.ev("document.querySelector(\"#table .cell[data-key='number:17']\").scrollIntoView({block: 'center'})")
        await p.settle()
        await p.tap("#table .cell[data-key='number:17']")
        await shot("roulette-bet")
        await p.ev("document.getElementById('wheel-layer').classList.add('active')")
        await shot("wheel")
        await p.ev("document.getElementById('wheel-layer').classList.remove('active')")
    elif skin == "chip_ring":
        await open_game(p, "roulette")
        await p.ev("document.getElementById('bets').scrollIntoView({block: 'end'})")
        await shot("roulette-chips")
        await open_game(p, "mines")
        await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма мин")
        await shot("mines-chips")
    elif skin == "back_midnight":
        w.server.script(shoe=[["10S", "9H", "10D", "8C"]], hilo=[[7, "H"], [7, "S"]])
        await open_game(p, "blackjack")
        await set_bet(p, "bj-bet", 100)
        await p.tap("#bj-deal")
        await p.wait("!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled", 15, "раздача")
        await shot("blackjack-back")
        await open_game(p, "hilo")
        await p.wait("!document.getElementById('hl-bets').hidden", 10, "хило")
        await set_bet(p, "hl-bet", 100)
        await p.tap("#hl-start")
        await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия")
        await p.ev("document.getElementById('hl-card').classList.add('back')")
        await shot("hilo-back")
    elif skin == "mine_star":
        w.server.script(mines=[[0, 1, 2], [0, 1, 2]])
        await open_game(p, "mines")
        await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма мин")
        await set_bet(p, "mines-bet", 100)
        await p.tap("#mines-begin")
        await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле")
        for n in (13, 14, 18):
            await p.tap("#mines-grid .mines-cell:nth-child(%d)" % n)
            await p.wait("!document.getElementById('mines-cash').disabled", 10, "ход")
        await shot("mines-play")
        await p.tap("#mines-grid .mines-cell:nth-child(1)")
        await p.wait("!document.getElementById('mines-result').hidden", 10, "итог")
        await p.wait("document.querySelectorAll('#mines-grid .mines-cell.mine').length > 0", 10, "мины")
        await shot("mines-lose")
    elif skin == "keno_hex":
        w.server.script(keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]])
        await open_game(p, "keno")
        await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле кено")
        for n in (1, 2, 3, 4, 5):
            await p.tap(".keno-ball:nth-child(%d)" % n)
        await shot("keno-picked")
        await set_bet(p, "keno-bet", 100)
        await p.tap("#keno-play")
        await p.wait("document.getElementById('keno-result-title').textContent.trim().length > 0", 30, "итог")
        await p.wait("!document.getElementById('keno-play').disabled", 30, "готово")
        await shot("keno-result")
    elif skin == "crash_neon":
        w.server.script(crash=[5000, 150])
        await open_game(p, "crash")
        await p.wait("!document.getElementById('cr-bets').hidden", 10, "краш")
        await shot("crash-start")
        for target, banner in (("2", "win"), ("2", "lose")):
            await set_bet(p, "cr-bet", 100)
            await p.ev("(() => { const i = document.getElementById('cr-target'); i.value = %r; i.dispatchEvent(new Event('input')); })()" % target)
            await p.tap("#cr-start")
            await p.wait("!document.getElementById('cr-banner').hidden && document.getElementById('cr-banner').classList.contains('%s')" % banner, 20, banner)
            await p.wait("!document.getElementById('cr-bets').hidden && !document.getElementById('cr-start').disabled", 20, "раунд закончен")
            await shot("crash-" + banner)
    else:                                                    # frame_thin, badge_spade
        await p.tap(".tab[data-tab=rating]")
        await p.wait("document.querySelectorAll('#rating-list li').length >= 2 && document.querySelector('#rating-list .rating-badge, #rating-list .avatar[data-skin-avatar_frame]')", 15, "рейтинг")
        await shot("rating")
        await p.tap(".tab[data-tab=profile]")
        await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль")
        await shot("profile")


async def run_one(harness, chrome, skin, width, folder):
    from visual_snapshots import Shooter
    os.makedirs(folder, exist_ok=True)
    w = harness.World(chrome, users=USERS, viewport=(width, HEIGHT), clock_mod=30)
    w.owner.rate = 0
    try:
        await w.start()
        now = int(time.time())
        rows = [("me", SLOT_OF[skin], skin)]
        if skin in ("frame_thin", "badge_spade"):
            rows += [("bob", "avatar_frame", "frame_thin"), ("bob", "badge", "badge_spade")]
            rows += [("me", "avatar_frame", "frame_thin"), ("me", "badge", "badge_spade")]
        seen = set()
        for who, slot, code in rows:
            if (who, slot) in seen:
                continue
            seen.add((who, slot))
            uid = w.users[who].id
            w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))
            w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, code))
        await w.reload()
        await steps(w, skin, Shooter(w.page, folder).shot)
        if w.page.problems:
            raise harness.E2EError("консоль: " + " | ".join(w.page.problems[:3]))
    finally:
        await w.stop()


async def main(argv):
    out = os.path.expanduser(argv[argv.index("--out") + 1])
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else list(SLOT_OF)
    import harness
    if "--client-dir" in argv:
        harness.CLIENT_ROOT = os.path.abspath(argv[argv.index("--client-dir") + 1])
    tmp = tempfile.mkdtemp(prefix="e2e-skin-")
    chrome = harness.Chrome(harness.find_chrome(), tmp)
    try:
        for skin in only:
            for width in WIDTHS:
                await run_one(harness, chrome, skin, width, os.path.join(out, skin, str(width)))
                print("%s %d: готово" % (skin, width), flush=True)
    finally:
        chrome.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
