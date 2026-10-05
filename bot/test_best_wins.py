"""Рекорды выигрыша: личный рекорд (лучший чистый выигрыш за один раунд по всем играм) и рейтинг беседы /api/chat/best-wins.

Запись идёт в одном месте (core.kernel._record_best_win) в той же транзакции, что выплата, для всех шести игр. Рекорд растёт только
строго; проигрыш, ничья и возврат ставки (чистый выигрыш не больше нуля) ничего не пишут; повтор по request_id рекорд не меняет.
Тест не зависит от окружения оболочки и bot/.env."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["MEMBER_REF_SECRET"] = "test-ref-secret-not-real"

import backup  # noqa: E402
import crash  # noqa: E402
import db  # noqa: E402
import hilo  # noqa: E402
import keno  # noqa: E402
import mines  # noqa: E402
import ratelimit  # noqa: E402
import roulette  # noqa: E402
from api import create_app  # noqa: E402
from tg_testutil import make_init_data  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
DAY = 86400
IDS = [424242421 + i for i in range(12)]


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Scripted:
    """Подаёт заданные значения играм: randrange (карты хило, число краша), sample (мины, кено), shuffle (колода блэкджека)."""

    def __init__(self, ints=(), samples=(), shoes=()):
        self.ints, self.samples, self.shoes = list(ints), list(samples), list(shoes)

    def randrange(self, n):
        v = self.ints.pop(0)
        assert 0 <= v < n
        return v

    def sample(self, population, k):
        cells = self.samples.pop(0)
        assert len(cells) == k
        return list(cells)

    def shuffle(self, items):
        head = self.shoes.pop(0)
        rest = list(items)
        for card in head:
            rest.remove(card)
        items[:] = list(head) + rest


def cards(*pairs):
    out = []
    for rank, suit in pairs:
        out += [rank - 1, hilo.SUITS.index(suit)]
    return Scripted(ints=out)


def crash_rng(x100):
    u = crash.M - 3600 * crash.M // (37 * x100)
    return Scripted(ints=[min(u, crash.M - 1)])


def _raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("ожидали исключение")


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


_tmp = tempfile.mkdtemp()
_n = [0]
_rid = [0]


def new_db():
    _n[0] += 1
    path = os.path.join(_tmp, "bw%d.db" % _n[0])
    db.init_db(path)
    return path


def rid():
    _rid[0] += 1
    return "request-bw-%08d" % _rid[0]


def player(path, uid, balance=10 ** 9):
    # доход выключен (last_accrual в будущем): баланс меняют только раунды
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, 0, 0, 0, 0)", (uid, balance, NOW + 10 * DAY, NOW - DAY))


def best(path, uid):
    rows = sql(path, "SELECT game, net_amount, achieved_at FROM player_best_win WHERE telegram_id = ?", (uid,))
    return rows[0] if rows else None


def bets(*items):
    return roulette.validate_bets([{"type": t, "value": v, "amount": a} for t, v, a in items])


def balance(path, uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def auth(user_id, chat_type="group", chat_instance="chat-A", first_name="Игрок"):
    data = make_init_data(TOKEN, user_id=user_id, auth_date=int(time.time()), chat_type=chat_type, chat_instance=chat_instance, first_name=first_name)
    return {"Authorization": "tma " + data}


try:
    # ================= запись: все шесть игр =================
    path = new_db()
    A = IDS[0]
    player(path, A)

    # --- рулетка: полная ставка раунда (две ставки), чистый выигрыш = выплата - все ставки
    b0 = balance(path, A)
    res = db.spin_roulette(A, rid(), bets(("number", 17, 100), ("red", None, 100)), now=NOW, db_path=path, rng=lambda n: 17)
    check("рулетка: выплата 3600 при ставках 200", (res["payout_total"], res["stake_total"]), (3600, 200))
    check("рулетка: рекорд = 3600 - 200", best(path, A), ("roulette", 3400, NOW))
    check("рулетка: баланс изменился ровно по правилам игры", balance(path, A), b0 - 200 + 3600)
    # меньший выигрыш рекорд не трогает; проигрыш и ничья (возврат ставки) тоже
    db.spin_roulette(A, rid(), bets(("number", 17, 10)), now=NOW + 5, db_path=path, rng=lambda n: 17)
    db.spin_roulette(A, rid(), bets(("red", None, 500)), now=NOW + 6, db_path=path, rng=lambda n: 17)       # 17 чёрное: проигрыш
    db.spin_roulette(A, rid(), bets(("red", None, 100), ("black", None, 100)), now=NOW + 7, db_path=path, rng=lambda n: 17)   # выплата 200 при ставках 200
    check("рулетка: меньший выигрыш, проигрыш и ничья рекорд не меняют", best(path, A), ("roulette", 3400, NOW))
    # равный выигрыш (строго больше): остаётся более ранний
    db.spin_roulette(A, rid(), bets(("number", 17, 100), ("red", None, 100)), now=NOW + 8, db_path=path, rng=lambda n: 17)
    check("рулетка: равный выигрыш не перезаписывает (остаётся время первого)", best(path, A), ("roulette", 3400, NOW))
    # повтор по request_id: ничего не меняется
    same = rid()
    db.spin_roulette(A, same, bets(("number", 5, 1000)), now=NOW + 9, db_path=path, rng=lambda n: 5)
    after_first = best(path, A)
    check("рулетка: новый рекорд записан", after_first, ("roulette", 35000, NOW + 9))
    r2 = db.spin_roulette(A, same, bets(("number", 5, 1000)), now=NOW + 99, db_path=path, rng=lambda n: 5)
    check("повтор по request_id: replayed и рекорд прежний", (r2["replayed"], best(path, A)), (True, after_first))

    # --- кено
    K = IDS[1]
    player(path, K)
    check("кено: возврат 0 — нет рекорда до игры", best(path, K), None)
    db.play_keno(K, rid(), 100, [30, 31, 32], now=NOW, db_path=path, rng=Scripted(samples=[list(range(1, 11))]))
    check("кено: проигрыш не пишет", best(path, K), None)
    res = db.play_keno(K, rid(), 100, [1, 2, 3], now=NOW + 1, db_path=path, rng=Scripted(samples=[list(range(1, 11))]))
    check("кено: рекорд = выплата - ставка", best(path, K), ("keno", keno.payout(100, 3, 3) - 100, NOW + 1))
    check("кено: ответ совпадает с таблицей", res["payout"], keno.payout(100, 3, 3))
    kid = rid()
    db.play_keno(K, kid, 100, [1, 2], now=NOW + 2, db_path=path, rng=Scripted(samples=[list(range(1, 11))]))
    k_after = best(path, K)
    db.play_keno(K, kid, 100, [1, 2], now=NOW + 50, db_path=path, rng=Scripted(samples=[list(range(1, 11))]))
    check("кено: повтор по request_id рекорд не меняет", best(path, K), k_after)

    # --- мины: вывод, зачистка, проигрыш, возврат, автовывод
    M = IDS[2]
    player(path, M)
    db.mines_start(M, rid(), 1000, 3, now=NOW, db_path=path, rng=Scripted(samples=[[0, 1, 2]]))
    db.mines_reveal(M, rid(), 10, now=NOW + 1, db_path=path)
    db.mines_reveal(M, rid(), 11, now=NOW + 2, db_path=path)
    cid = rid()
    out = db.mines_cashout(M, cid, now=NOW + 3, db_path=path)
    want = mines.payout(1000, 3, 2) - 1000
    check("мины: вывод — чистый выигрыш (выплата при выводе минус ставка)", best(path, M), ("mines", want, NOW + 3))
    check("мины: выплата в ответе", out["last"]["payout"] - 1000, want)
    db.mines_cashout(M, cid, now=NOW + 40, db_path=path)
    check("мины: повтор вывода рекорд не меняет", best(path, M), ("mines", want, NOW + 3))
    # проигрыш и возврат
    M2 = IDS[3]
    player(path, M2)
    db.mines_start(M2, rid(), 1000, 3, now=NOW, db_path=path, rng=Scripted(samples=[[0, 1, 2]]))
    db.mines_reveal(M2, rid(), 0, now=NOW + 1, db_path=path)
    db.mines_start(M2, rid(), 1000, 3, now=NOW + 2, db_path=path, rng=Scripted(samples=[[0, 1, 2]]))
    db.mines_cashout(M2, rid(), now=NOW + 3, db_path=path)      # ноль открытых: возврат ставки
    check("мины: проигрыш и возврат ставки не пишут", best(path, M2), None)
    # зачистка поля (24 мины - одна безопасная клетка)
    db.mines_start(M2, rid(), 1000, 24, now=NOW + 4, db_path=path, rng=Scripted(samples=[list(range(1, 25))]))
    db.mines_reveal(M2, rid(), 0, now=NOW + 5, db_path=path)
    check("мины: зачистка поля пишет рекорд", best(path, M2), ("mines", mines.payout(1000, 24, 1) - 1000, NOW + 5))
    # автовывод по бездействию (фоновая задача закрывает партию)
    M3 = IDS[4]
    player(path, M3)
    db.mines_start(M3, rid(), 1000, 3, now=NOW, db_path=path, rng=Scripted(samples=[[0, 1, 2]]))
    db.mines_reveal(M3, rid(), 10, now=NOW + 1, db_path=path)
    check("мины: автовывод закрыл партию", db.settle_expired_mines(M3, now=NOW + 2 + mines.MINES_IDLE_SECONDS, db_path=path), True)
    check("мины: автовывод пишет рекорд", best(path, M3), ("mines", mines.payout(1000, 3, 1) - 1000, NOW + 2 + mines.MINES_IDLE_SECONDS))

    # --- блэкджек: с удвоением вся поставленная сумма (2000), естественный блэкджек 3:2, проигрыш и ничья не пишут
    B = IDS[5]
    player(path, B)
    db.blackjack_start(B, rid(), 1000, now=NOW, db_path=path, rng=Scripted(shoes=[("5S", "9H", "6D", "7C", "10H", "10S")]))
    out = db.blackjack_action(B, rid(), "double", now=NOW + 1, db_path=path)
    check("блэкджек: удвоение выиграно: wager 2000, выплата 4000", (out["wager"], out["payout"]), (2000, 4000))
    check("блэкджек: рекорд = 4000 - 2000 (обе поставленные суммы)", best(path, B), ("blackjack", 2000, NOW + 1))
    db.blackjack_start(B, rid(), 1000, now=NOW + 2, db_path=path, rng=Scripted(shoes=[("5S", "9H", "6D", "10C", "2H", "KS")]))
    db.blackjack_action(B, rid(), "double", now=NOW + 3, db_path=path)      # удвоение проиграно
    db.blackjack_start(B, rid(), 1000, now=NOW + 4, db_path=path, rng=Scripted(shoes=[("5S", "10H", "6D", "8C", "7H", "KS")]))
    db.blackjack_action(B, rid(), "double", now=NOW + 5, db_path=path)      # ничья на удвоенной ставке
    check("блэкджек: проигрыш и ничья рекорд не меняют", best(path, B), ("blackjack", 2000, NOW + 1))
    B2 = IDS[6]
    player(path, B2)
    out = db.blackjack_start(B2, rid(), 1000, now=NOW, db_path=path, rng=Scripted(shoes=[("AS", "5H", "KD", "9C")]))
    check("блэкджек: блэкджек 3:2 → выплата 2500, рекорд 1500", (out["payout"], best(path, B2)), (2500, ("blackjack", 1500, NOW)))
    sid = rid()
    B3 = IDS[7]
    player(path, B3)
    db.blackjack_start(B3, rid(), 1000, now=NOW, db_path=path, rng=Scripted(shoes=[("10S", "6H", "9D", "10C", "5S", "KD")]))
    stand = rid()
    out = db.blackjack_action(B3, stand, "stand", now=NOW + 1, db_path=path)
    first = best(path, B3)
    db.blackjack_action(B3, stand, "stand", now=NOW + 60, db_path=path)
    check("блэкджек: повтор stand рекорд не меняет", best(path, B3), first)

    # --- краш: авто, ручной вывод, проигрыш
    C = IDS[8]
    player(path, C)
    T = NOW * 1000
    db.crash_start(C, rid(), 1000, 200, now_ms=T, db_path=path, rng=crash_rng(110))        # цель 2.00x, краш на 1.10x: проигрыш
    check("краш: проигрыш не пишет", best(path, C), None)
    out = db.crash_start(C, rid(), 1000, 200, now_ms=T + 1000, db_path=path, rng=crash_rng(500))
    check("краш: авто-выигрыш: рекорд = выплата - ставка", (best(path, C), out["payout"]), (("crash", crash.payout(1000, 200) - 1000, NOW + 1), crash.payout(1000, 200)))
    C2 = IDS[9]
    player(path, C2)
    db.crash_start(C2, rid(), 1000, None, now_ms=T, db_path=path, rng=crash_rng(100000))
    cid = rid()
    out = db.crash_cashout(C2, cid, now_ms=T + 3000, db_path=path)
    check("краш: ручной вывод: рекорд = выплата при выводе - ставка", (best(path, C2), out["payout"] - 1000), (("crash", out["payout"] - 1000, NOW + 3), out["payout"] - 1000))
    assert out["payout"] > 1000
    db.crash_cashout(C2, cid, now_ms=T + 9000, db_path=path)
    check("краш: повтор вывода рекорд не меняет", best(path, C2)[2], NOW + 3)

    # --- хило: вывод, проигрыш, возврат
    H = IDS[10]
    player(path, H)
    db.hilo_start(H, rid(), 1000, now=NOW, db_path=path, rng=cards((7, "S")))
    db.hilo_guess(H, rid(), "lo", now=NOW + 1, db_path=path, rng=cards((3, "C")))
    out = db.hilo_cashout(H, rid(), now=NOW + 2, db_path=path)
    check("хило: вывод: рекорд = выплата - ставка", (best(path, H), out["payout"] > 1000), (("hilo", out["payout"] - 1000, NOW + 2), True))
    H2 = IDS[11]
    player(path, H2)
    db.hilo_start(H2, rid(), 1000, now=NOW, db_path=path, rng=cards((7, "S")))
    db.hilo_guess(H2, rid(), "hi", now=NOW + 1, db_path=path, rng=cards((2, "D")))         # проигрыш
    db.hilo_start(H2, rid(), 1000, now=NOW + 2, db_path=path, rng=cards((7, "S")))
    check("хило: без угаданных ходов вывод запрещён", type(_raises(db.hilo_cashout, H2, rid(), now=NOW + 3, db_path=path)).__name__, "NothingToCashOut")
    check("хило: бездействие без ходов закрыло партию возвратом", db.settle_expired_hilo(H2, now=NOW + 4 + hilo.HILO_IDLE_SECONDS, db_path=path), True)
    check("хило: проигрыш и возврат ставки не пишут", best(path, H2), None)

    # --- единая точка записи: только kernel, игры таблицу не знают
    import re
    sources = {}
    for folder in ("", "core", "features", "games"):
        for n in os.listdir(os.path.join(HERE, folder)):
            if n.endswith(".py") and not n.startswith("test_"):
                sources[os.path.join(folder, n)] = open(os.path.join(HERE, folder, n), encoding="utf-8").read()
    writers = sorted(f for f, s in sources.items() if re.search(r"(INSERT|UPDATE|REPLACE)\b[^\"']*player_best_win", s))
    check("запись в player_best_win только в core/kernel.py", writers, [os.path.join("core", "kernel.py")])
    check("игры таблицу рекордов не читают и не пишут", [f for f in sources if f.startswith("games") and "player_best_win" in sources[f]], [])
    check("рекорды не влияют на деньги: ни wallet, ни economy, ни xp, ни levels их не знают",
          [f for f in ("wallet.py", "economy.py", "xp.py", "levels.py", "farm.py", "transfers.py") if "best_win" in sources[f]], [])

    # --- 20 параллельных раундов одного игрока: запись не ломается, остаётся максимум
    P = IDS[0]
    path2 = new_db()
    player(path2, P)
    gate = threading.Barrier(20)

    def race(i):
        gate.wait()
        amount = 10 * (i + 1)
        return db.spin_roulette(P, rid(), bets(("number", 17, amount)), now=NOW + i, db_path=path2, rng=lambda n: 17)["payout_total"]
    with ThreadPoolExecutor(20) as pool:
        payouts = list(pool.map(race, range(20)))
    check("параллельно: все 20 раундов прошли", len(payouts), 20)
    check("параллельно: остаётся максимум 35 * 200 = 7000, одна строка", (best(path2, P)[:2], sql(path2, "SELECT COUNT(*) FROM player_best_win")[0][0]), (("roulette", 7000), 1))

    # ================= рейтинг беседы =================
    path3 = new_db()
    ids = IDS[:9]
    for u in ids:
        player(path3, u)
    now = int(time.time())
    for i, (chat, u) in enumerate([("chat-A", ids[0]), ("chat-A", ids[1]), ("chat-A", ids[2]), ("chat-A", ids[3]), ("chat-A", ids[4]),
                                   ("chat-B", ids[5]), ("chat-A", ids[6]), ("chat-A", ids[7])]):
        sql(path3, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
            (chat, u, "Имя%d" % i, now - 100, now))
    # ids[8]: в players, но не участник ни одной беседы
    recs = {ids[0]: ("roulette", 5000, 300), ids[1]: ("keno", 9000, 200), ids[2]: ("mines", 5000, 100), ids[3]: ("crash", 700, 50),
            ids[5]: ("hilo", 99999, 10), ids[6]: ("blackjack", 5000, 100), ids[8]: ("roulette", 123456, 1)}     # ids[4], ids[7]: без рекорда
    for u, (g, n, t) in recs.items():
        sql(path3, "INSERT INTO player_best_win (telegram_id, game, net_amount, achieved_at) VALUES (?, ?, ?, ?)", (u, g, n, t))
    top = db.chat_best_wins("chat-A", ids[0], db_path=path3)
    check("порядок: сумма по убыванию, при равенстве раньше достигший, затем id", [(e["name"], e["net_amount"]) for e in top["top"]],
          [("Имя1", 9000), ("Имя2", 5000), ("Имя6", 5000), ("Имя0", 5000), ("Имя3", 700)])
    check("ничья 5000: у ids[2] и ids[6] время 100, раньше по id; ids[0] время 300 последний", [e["rank"] for e in top["top"]], [1, 2, 3, 4, 5])
    check("фильтр по беседе: чужая беседа и не-участники не видны", (top["total"], [e["game"] for e in top["top"]]), (5, ["keno", "mines", "blackjack", "roulette", "crash"]))
    check("я: место среди участников с рекордом, is_me, member_ref None у себя", (top["me"], [e["is_me"] for e in top["top"]], [e["member_ref"] is None for e in top["top"]]),
          ({"rank": 4, "net_amount": 5000, "game": "roulette", "total": 5}, [False, False, False, True, False], [False, False, False, True, False]))
    check("поля записи рейтинга", set(top["top"][0]), {"rank", "name", "net_amount", "game", "is_me", "cosmetics", "member_ref"})
    text = json.dumps(top)
    check("в ответе нет telegram_id и времени достижения", [str(u) in text for u in IDS], [False] * 12)
    other = db.chat_best_wins("chat-B", ids[5], db_path=path3)
    check("другая беседа: только свои", ([e["name"] for e in other["top"]], other["me"]["rank"]), (["Имя5"], 1))
    nobody = db.chat_best_wins("chat-A", ids[4], db_path=path3)
    check("у кого нет рекорда: me None, список тот же", (nobody["me"], nobody["total"]), (None, 5))
    check("пустая беседа", db.chat_best_wins("chat-Z", ids[0], db_path=path3), {"scope": "chat", "top": [], "me": None, "total": 0})
    # топ-10 из 12 участников
    path4 = new_db()
    for i in range(12):
        u = 500 + i
        player(path4, u)
        sql(path4, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('chat-A', ?, ?, ?, ?)", (u, "Игрок%d" % i, now - 100, now))
        sql(path4, "INSERT INTO player_best_win (telegram_id, game, net_amount, achieved_at) VALUES (?, 'keno', ?, ?)", (u, 1000 + i, NOW))
    t12 = db.chat_best_wins("chat-A", 500, db_path=path4)
    check("топ-10 из 12: десять строк, я (самый слабый) вне списка, но с местом", (len(t12["top"]), t12["me"]["rank"], t12["total"]), (10, 12, 12))
    # рамка и значок по правилам видимости косметики (публичные слоты, не скрыто)
    sql(path3, "INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, 'frame_thin', 'owner_gift', 1)", (ids[1],))
    sql(path3, "INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'avatar_frame', 'frame_thin')", (ids[1],))
    sql(path3, "INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'chip', 'chip_ring')", (ids[1],))
    check("рамка видна, непубличный слот нет", db.chat_best_wins("chat-A", ids[0], db_path=path3)["top"][0]["cosmetics"], {"avatar_frame": "frame_thin"})
    sql(path3, "INSERT INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 0)", (ids[1],))
    check("скрыто игроком: косметики нет", db.chat_best_wins("chat-A", ids[0], db_path=path3)["top"][0]["cosmetics"], {})

    # ================= API =================
    client = TestClient(create_app(TOKEN, [], db_path=path3))
    r = client.get("/api/chat/best-wins")
    check("без initData: 401", r.status_code, 401)
    r = client.get("/api/chat/best-wins", headers={"Authorization": "Bearer x"})
    check("не tma: 401", r.status_code, 401)
    r = client.get("/api/chat/best-wins", headers=auth(ids[0], chat_type="private", chat_instance="chat-A"))
    check("личка: scope none", r.json(), {"scope": "none"})
    r = client.get("/api/chat/best-wins", headers=auth(ids[0], chat_instance=None))
    check("нет беседы: scope none", r.json(), {"scope": "none"})
    r = client.get("/api/chat/best-wins", headers=auth(ids[0]))
    check("API: 200 и тот же ответ, что у функции", (r.status_code, r.json()), (200, db.chat_best_wins("chat-A", ids[0], db_path=path3)))
    check("API: no-store", r.headers.get("cache-control"), "no-store")
    example = json.load(open(os.path.join(ROOT, "docs", "examples", "best_wins.json"), encoding="utf-8"))
    body = r.json()
    check("форма ответа совпадает с docs/examples/best_wins.json", (set(body), set(body["top"][0]), set(body["me"])),
          (set(example["chat"]), set(example["chat"]["top"][0]), set(example["chat"]["me"])))
    check("пример: пустой и none", (db.chat_best_wins("chat-Z", ids[0], db_path=path3), client.get("/api/chat/best-wins", headers=auth(ids[0], chat_type="private")).json()),
          (example["empty"], example["none"]))
    check("API: нет telegram_id", [str(u) in r.text for u in IDS], [False] * 12)
    check("API: чтение не регистрирует участника беседы", sql(path3, "SELECT COUNT(*) FROM chat_members WHERE chat_instance = 'chat-A'")[0][0], 7)

    # лимит запросов как у чтения: при малом запасе третий запрос подряд получает 429
    tight = TestClient(create_app(TOKEN, [], db_path=path3, rate_limiter=ratelimit.RateLimiter(ratelimit.load_config(
        {"READ_RATE_PER_SEC": "0.01", "READ_RATE_BURST": "2"}))))
    codes = [tight.get("/api/chat/best-wins", headers=auth(ids[0])).status_code for _ in range(3)]
    check("лимит чтения: 200, 200, 429", codes, [200, 200, 429])

    # ================= данные игрока: экспорт, удаление, бэкап =================
    ex = db.get_player_export(ids[1], db_path=path3)
    check("/mydata: рекорд игрока (игра, сумма, дата)", ex["best_win"], {"game": "keno", "net_amount": 9000, "achieved_at": 200})
    check("/mydata без рекорда: поле None", db.get_player_export(ids[4], db_path=path3)["best_win"], None)
    sql(path3, "DELETE FROM players WHERE telegram_id = ?", (ids[8],))
    sql(path3, "DELETE FROM chat_members WHERE telegram_id = ?", (ids[8],))
    check("выгрузка есть даже если осталась только строка рекорда", db.get_player_export(ids[8], db_path=path3)["best_win"]["net_amount"], 123456)
    snap = backup.create_snapshot(path3, os.path.join(_tmp, "bk"), now=NOW)
    check("копия базы содержит таблицу и строки рекордов", sql(snap, "SELECT COUNT(*) FROM player_best_win")[0][0], 7)
    check("копия проходит проверку целостности", backup.inspect_database(snap)["players"], 8)
    deleted = db.delete_player_data(ids[1], db_path=path3, now=NOW)
    check("/deletemydata: строка рекорда удалена и посчитана", (deleted["player_best_win"], best(path3, ids[1])), (1, None))
    check("после удаления игрока в рейтинге его нет", [e["name"] for e in db.chat_best_wins("chat-A", ids[0], db_path=path3)["top"]], ["Имя2", "Имя6", "Имя0", "Имя3"])
    check("повторное удаление ничего не удаляет", db.delete_player_data(ids[1], db_path=path3, now=NOW + 1)["player_best_win"], 0)
    check("чужие рекорды целы", sql(path3, "SELECT COUNT(*) FROM player_best_win")[0][0], 6)

    # ================= миграция с предыдущей версии (до таблицы рекордов) =================
    old_dir = os.path.join(_tmp, "oldcode")
    os.makedirs(old_dir)
    try:
        subprocess.run("git -C %s archive 981d2c0 bot | tar -x -C %s" % (ROOT, old_dir), shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        have_old = os.path.exists(os.path.join(old_dir, "bot", "cosmetics.py"))
    except Exception:
        have_old = False
    if have_old:
        old_db = os.path.join(_tmp, "prev.db")
        code = ("import sys, os; sys.path.insert(0, %r); os.chdir(%r)\n"
                "for k in ('DB_PATH','TOMBSTONE_SECRET','OWNER_CHAT_ID'): os.environ.pop(k, None)\n"
                "import db; db.init_db(%r); db.get_player(777, now=1760000000, db_path=%r)\n") % (os.path.join(old_dir, "bot"), os.path.join(old_dir, "bot"), old_db, old_db)
        subprocess.run([sys.executable, "-c", code], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        before = {r[0] for r in sql(old_db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "player_best_win" not in before
        players_before = sql(old_db, "SELECT * FROM players ORDER BY telegram_id")
        db.init_db(old_db)
        after = {r[0] for r in sql(old_db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
        check("миграция добавила только player_best_win", after - before, {"player_best_win"})
        check("существующее не тронуто", sql(old_db, "SELECT * FROM players ORDER BY telegram_id"), players_before)
        db.init_db(old_db)
        check("повторная миграция ничего не ломает", {r[0] for r in sql(old_db, "SELECT name FROM sqlite_master WHERE type = 'table'")}, after)
        sql(old_db, "UPDATE players SET balance = 10000, last_accrual = ? WHERE telegram_id = 777", (NOW + 10 * DAY,))
        db.spin_roulette(777, rid(), bets(("number", 17, 100)), now=NOW, db_path=old_db, rng=lambda n: 17)
        check("на мигрированной базе рекорд пишется", best(old_db, 777), ("roulette", 3500, NOW))
        print("миграция с предыдущей версии (коммит 981d2c0) проверена")
    else:
        print("git-архив предыдущей версии недоступен: проверка миграции пропущена")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print("Все проверки прошли")
