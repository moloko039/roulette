import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from math import comb

from fastapi.testclient import TestClient

import bot
import db
import levels
import mines
import xp
from api import create_app
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
DAY = 86400
SECRET_ID, SECRET_BET = 424242421, 4242

# тест не зависит от окружения и bot/.env
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError("ожидали %s" % exc.__name__)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)


class FixedRng:
    def __init__(self, cells):
        self.cells = list(cells)

    def sample(self, population, k):
        return list(self.cells)[:k]


def bet(kind, value=None, amount=10):
    return {"type": kind, "value": value, "amount": amount}


# ---------- независимая реализация покрытия исходов (не использует roulette.py) ----------
REDS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def covers(b, n):
    t, v = b["type"], b["value"]
    if t == "number":
        return n == v
    if n == 0:
        return False
    if t == "red":
        return n in REDS
    if t == "black":
        return n not in REDS
    if t == "even":
        return n % 2 == 0
    if t == "odd":
        return n % 2 == 1
    if t == "dozen":
        return (n - 1) // 12 + 1 == v
    if t == "column":
        return (n - 1) % 3 + 1 == v
    raise AssertionError(t)


def reference_xp(bets):
    stake = sum(b["amount"] for b in bets)
    losing = sum(1 for n in range(37) if not any(covers(b, n) for b in bets))
    return stake * losing // 37


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "x%d.db" % counter[0])
    db.init_db(path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=1_000_000, xp_=0, staked=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, "
              "storage_level) VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW + 10 * DAY, NOW, staked, xp_))


def player_xp(path, uid):
    return sql(path, "SELECT xp FROM players WHERE telegram_id = ?", (uid,))[0][0]


def rid(n):
    return "xp-request-%06d" % n


try:
    # ================= xp.py: рулетка =================
    check("10 на красное", xp.roulette_xp(10, [bet("red")]), 5)
    check("10 на число", xp.roulette_xp(10, [bet("number", 17)]), 9)
    check("красное и чёрное по 1000", xp.roulette_xp(2000, [bet("red", None, 1000), bet("black", None, 1000)]), 54)
    check("по 1 на все 37 чисел", xp.roulette_xp(37, [bet("number", i, 1) for i in range(37)]), 0)
    check("нет ставок", xp.roulette_xp(0, []), 0)
    check("floor", xp.roulette_xp(1, [bet("red", None, 1)]), 0)
    singles = [bet(t) for t in ("red", "black", "even", "odd")] + [bet("dozen", d) for d in (1, 2, 3)] \
        + [bet("column", c) for c in (1, 2, 3)] + [bet("number", n) for n in range(37)]
    for b in singles:
        for amount in (1, 7, 100, 1000, 12345):
            b2 = dict(b, amount=amount)
            check("одна ставка %s %s на %d" % (b["type"], b["value"], amount), xp.roulette_xp(amount, [b2]), reference_xp([b2]))
    import itertools
    pairs = list(itertools.combinations(singles, 2))
    for a, c in pairs:
        pair = [dict(a, amount=100), dict(c, amount=250)]
        check("пара %s/%s" % (a["type"], c["type"]), xp.roulette_xp(350, pair), reference_xp(pair))
    mixed = [bet("red", None, 100), bet("number", 17, 50), bet("dozen", 2, 70), bet("column", 3, 20), bet("even", None, 5)]
    check("смешанные ставки", xp.roulette_xp(245, mixed), reference_xp(mixed))
    check("красное и чёрное: 0 единственный проигрыш", xp.roulette_xp(37 * 100, [bet("red", None, 1850), bet("black", None, 1850)]), 100)
    # чем больше исходов покрыто, тем меньше опыта (при той же сумме ставок)
    nested = [[bet("number", 5)], [bet("number", 5), bet("number", 6)], [bet("number", 5), bet("number", 6), bet("red")],
              [bet("number", 5), bet("number", 6), bet("red"), bet("black")]]
    values = [xp.roulette_xp(3700, [dict(b, amount=3700 // len(bs)) for b in bs]) for bs in nested]
    assert values == sorted(values, reverse=True) and values[0] > values[-1], values
    for b in singles:
        assert xp.roulette_xp(1000, [dict(b, amount=1000)]) <= 1000 * 36 // 37
    raises(ValueError, xp.roulette_xp, -1, [])
    raises(ValueError, xp.roulette_xp, 1.5, [])

    # ================= xp.py: мины =================
    check("1 мина, 1 клетка", xp.mines_xp(1000, 1, 1, False), 40)
    check("3 мины, 1 клетка", xp.mines_xp(1000, 3, 1, False), 120)
    check("24 мины, 1 клетка", xp.mines_xp(1000, 24, 1, False), 960)
    check("3 мины, поле очищено", xp.mines_xp(1000, 3, 22, False), 999)
    check("проигрыш", xp.mines_xp(1000, 3, 5, True), 1000)
    check("проигрыш при нуле открытых", xp.mines_xp(1000, 3, 0, True), 1000)
    check("возврат", xp.mines_xp(1000, 3, 0, False), 0)
    for b in (1, 10, 1000, 10 ** 9):
        for m in range(1, 25):
            row = [xp.mines_xp(b, m, k, False) for k in range(0, 26 - m)]
            assert all(0 <= v <= b for v in row), (b, m)
            assert row[0] == 0 and all(a <= c for a, c in zip(row, row[1:])), "не растёт с k (b=%d, m=%d)" % (b, m)
            assert xp.mines_xp(b, m, 1, True) == b
        for k in range(1, 25):
            col = [xp.mines_xp(b, m, k, False) for m in range(1, 26 - k)]
            assert all(a <= c for a, c in zip(col, col[1:])), "не растёт с m (b=%d, k=%d)" % (b, k)
    assert max(xp.mines_xp(10 ** 9, m, k, False) for m in range(1, 25) for k in range(0, 26 - m)) <= MAX_SAFE_INT
    for bad in ((1000, 0, 1, False), (1000, 25, 1, False), (1000, 3, 23, False), (1000, 3, -1, False), (-1, 3, 1, False),
                (1.5, 3, 1, False), (True, 3, 1, False)):
        raises(ValueError, xp.mines_xp, *bad) if not (bad[0] is True) else None
    src = open(os.path.join(HERE, "xp.py"), encoding="utf-8").read()
    code = "\n".join(l.split("#")[0] for l in re.sub(r'""".*?"""', "", src, flags=re.S).splitlines())
    assert "float" not in code and not re.search(r"\d\.\d", code) and not re.search(r"(?<!/)/(?!/)", code)

    # ================= миграция =================
    old = new_db()
    conn = sqlite3.connect(old)
    conn.execute("DROP TABLE players")
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, rate INTEGER NOT NULL, "
                 "last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL, total_staked INTEGER NOT NULL DEFAULT 0, "
                 "income_level INTEGER NOT NULL DEFAULT 0, storage_level INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked) VALUES "
                 "(1, 777, 100, %d, 1000, 5500), (2, 5, 100, %d, 2000, 0)" % (int(time.time()) + 10 * 86400, int(time.time()) + 10 * 86400))   # метка в будущем: миграция начисления ничего не платит
    conn.commit()
    conn.close()
    db.init_db(old)
    cols = {r[1]: (r[2], r[3], r[4]) for r in sql(old, "PRAGMA table_info(players)")}
    check("столбец xp", cols["xp"], ("INTEGER", 1, "0"))
    check("xp = total_staked", sql(old, "SELECT telegram_id, xp, total_staked, balance FROM players ORDER BY 1"),
          [(1, 5500, 5500, 777), (2, 0, 0, 5)])
    sql(old, "UPDATE players SET xp = 123 WHERE telegram_id = 1")
    db.init_db(old)
    db.init_db(old)
    check("повторный init_db ничего не меняет", sql(old, "SELECT xp FROM players ORDER BY telegram_id"), [(123,), (0,)])
    check("столбец один", [r[1] for r in sql(old, "PRAGMA table_info(players)")].count("xp"), 1)
    check("новый игрок начинает с 0", db.get_player(99, now=NOW, db_path=old)["xp"], 0)

    # ================= spin =================
    path = new_db()
    add_player(path, 1)
    rng = lambda n: 5  # noqa: E731
    cases = [("красное 10", [bet("red", None, 10)], 5), ("число 10", [bet("number", 17, 10)], 9),
             ("красное+чёрное", [bet("red", None, 1000), bet("black", None, 1000)], 54),
             ("37 чисел", [bet("number", i, 1) for i in range(37)], 0),
             ("дюжина 100", [bet("dozen", 2, 100)], 100 * 25 // 37),
             ("смешанные", mixed, reference_xp(mixed))]
    total = 0
    for i, (name, bets_, expected) in enumerate(cases):
        before = player_xp(path, 1)
        db.spin_roulette(1, rid(i), bets_, now=NOW, db_path=path, rng=rng)
        check("xp за раунд: " + name, player_xp(path, 1) - before, expected)
        total += expected
    # повтор с тем же request_id не добавляет
    db.spin_roulette(1, rid(0), cases[0][1], now=NOW, db_path=path, rng=lambda n: 0)
    check("повтор не добавляет", player_xp(path, 1), total)
    # ошибочные запросы не добавляют
    before = player_xp(path, 1)
    raises(InsufficientFunds, db.spin_roulette, 1, rid(50), [bet("red", None, 10 ** 12)], NOW, path)
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = 1", (MAX_SAFE_INT - 10,))
    raises(BalanceLimit, db.spin_roulette, 1, rid(51), [bet("number", 1, 100)], NOW, path)
    check("ошибки опыт не меняют", player_xp(path, 1), before)
    # параллельные запросы не теряют прирост
    path = new_db()
    add_player(path, 1)
    errors = []

    def worker(tag):
        try:
            for i in range(20):
                db.spin_roulette(1, "par-%s-%04d" % (tag, i), [bet("red", None, 10), bet("number", 5, 4)], now=NOW,
                                 db_path=path, rng=rng)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("потоки без ошибок", errors, [])
    check("прирост не потерян", player_xp(path, 1), 60 * reference_xp([bet("red", None, 10), bet("number", 5, 4)]))
    # потолок
    sql(path, "UPDATE players SET xp = ? WHERE telegram_id = 1", (MAX_SAFE_INT - 1,))
    db.spin_roulette(1, rid(60), [bet("number", 3, 100)], now=NOW, db_path=path, rng=rng)
    check("опыт не выше MAX_SAFE_INT", player_xp(path, 1), MAX_SAFE_INT)

    # ================= мины: опыт при завершении =================
    def start(path, uid, n, bet_=1000, m=3, cells=(0, 1, 2), now=NOW):
        return db.mines_start(uid, rid(n), bet_, m, now=now, db_path=path, rng=FixedRng(cells))

    path = new_db()
    add_player(path, 1)
    start(path, 1, 1)
    db.mines_reveal(1, rid(2), 10, now=NOW + 1, db_path=path)
    check("до завершения опыта нет", player_xp(path, 1), 0)
    db.mines_reveal(1, rid(3), 0, now=NOW + 2, db_path=path)           # мина
    check("проигрыш: опыт = ставка", player_xp(path, 1), 1000)
    db.mines_reveal(1, rid(3), 0, now=NOW + 3, db_path=path)           # повтор
    check("повтор открытия не добавляет", player_xp(path, 1), 1000)
    # cashout при k = 1 и k > 1
    start(path, 1, 4)
    db.mines_reveal(1, rid(5), 10, now=NOW + 5, db_path=path)
    db.mines_cashout(1, rid(6), now=NOW + 6, db_path=path)
    check("cashout k=1", player_xp(path, 1), 1000 + 120)
    db.mines_cashout(1, rid(6), now=NOW + 7, db_path=path)
    check("повтор cashout не добавляет", player_xp(path, 1), 1120)
    start(path, 1, 7)
    for i, cell in enumerate((10, 11, 12)):
        db.mines_reveal(1, rid(8 + i), cell, now=NOW + 8 + i, db_path=path)
    db.mines_cashout(1, rid(12), now=NOW + 12, db_path=path)
    check("cashout k=3", player_xp(path, 1) - 1120, xp.mines_xp(1000, 3, 3, False))
    # очистка поля
    before = player_xp(path, 1)
    start(path, 1, 13, m=24, cells=range(24))
    db.mines_reveal(1, rid(14), 24, now=NOW + 14, db_path=path)
    check("очистка поля (24 мины)", player_xp(path, 1) - before, 960)
    before = player_xp(path, 1)
    start(path, 1, 15, m=3, cells=(0, 1, 2))
    for i, cell in enumerate(range(3, 25)):
        db.mines_reveal(1, rid(20 + i), cell, now=NOW + 20 + i, db_path=path)
    check("очистка поля (3 мины, 22 клетки)", player_xp(path, 1) - before, 999)
    # возврат: без опыта
    before = player_xp(path, 1)
    start(path, 1, 50)
    db.mines_cashout(1, rid(51), now=NOW + 60, db_path=path)
    check("возврат при k = 0: опыта нет", player_xp(path, 1), before)
    # автозакрытие: три пути, один раз
    for label, closer in (("первый шаг действия", lambda p, u, t: db.mines_state(u, now=t, db_path=p)),
                          ("settle_expired_mines", lambda p, u, t: db.settle_expired_mines(u, now=t, db_path=p)),
                          ("фоновая задача", lambda p, u, t: db.close_expired_mines(now=t, db_path=p))):
        path = new_db()
        add_player(path, 1)
        add_player(path, 2)
        start(path, 1, 1)
        db.mines_reveal(1, rid(2), 10, now=NOW + 1, db_path=path)
        start(path, 2, 3)                                    # у второго игрока k = 0
        closer(path, 1, NOW + 1 + DAY)
        check("автозакрытие (%s): k >= 1 даёт опыт" % label, player_xp(path, 1), xp.mines_xp(1000, 3, 1, False))
        closer(path, 1, NOW + 5 * DAY)
        closer(path, 1, NOW + 6 * DAY)
        db.close_expired_mines(now=NOW + 6 * DAY, db_path=path)   # закрывает и игру второго игрока (k = 0)
        check("автозакрытие (%s): возврат без опыта" % label, player_xp(path, 2), 0)
        check("автозакрытие (%s): один раз" % label, player_xp(path, 1), xp.mines_xp(1000, 3, 1, False))
        check("статусы", [r[0] for r in sql(path, "SELECT status FROM mines_games ORDER BY telegram_id")], ["auto_cashed", "auto_refunded"])
    # GET /api/me закрывает просроченную игру и даёт опыт один раз
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID)
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = ?", (now_real + 10 * DAY, SECRET_ID))
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, created_at, updated_at) "
              "VALUES (?, ?, 3, 7, 3072, 'active', ?, ?)", (SECRET_ID, SECRET_BET, now_real - 2 * DAY, now_real - 2 * DAY))
    client = TestClient(create_app(TOKEN, [], db_path=path))

    def auth(uid, group=False):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок",
                                                          chat_type="group" if group else None,
                                                          chat_instance="room" if group else None)}

    client.get("/api/me", headers=auth(SECRET_ID))
    client.get("/api/me", headers=auth(SECRET_ID))
    check("/api/me: опыт за автозакрытие один раз", player_xp(path, SECRET_ID), xp.mines_xp(SECRET_BET, 3, 2, False))
    # гонка cashout и автозакрытия: опыт ровно один раз
    for attempt in range(8):
        path = new_db()
        add_player(path, 1)
        start(path, 1, 1)
        db.mines_reveal(1, rid(2), 10, now=NOW + 1, db_path=path)
        gate = threading.Barrier(2)
        outcomes = []

        def do_cashout():
            gate.wait()
            try:
                db.mines_cashout(1, rid(3), now=NOW + 1 + DAY, db_path=path)   # к этому моменту игра просрочена
                outcomes.append("cashout")
            except mines.NoActiveGame:
                outcomes.append("no_active")

        def do_close():
            gate.wait()
            db.settle_expired_mines(1, now=NOW + 1 + DAY, db_path=path)
            outcomes.append("close")

        threads = [threading.Thread(target=do_cashout), threading.Thread(target=do_close)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("гонка: опыт один раз (попытка %d)" % attempt, player_xp(path, 1), xp.mines_xp(1000, 3, 1, False))
        check("гонка: игра закрыта один раз", sql(path, "SELECT COUNT(*) FROM mines_games WHERE status != 'active'")[0][0], 1)
    # потолок
    path = new_db()
    add_player(path, 1, xp_=MAX_SAFE_INT - 5)
    start(path, 1, 1)
    db.mines_reveal(1, rid(2), 0, now=NOW + 1, db_path=path)
    check("опыт игры не выше MAX_SAFE_INT", player_xp(path, 1), MAX_SAFE_INT)
    check("total_staked при первом открытии по-прежнему", sql(path, "SELECT total_staked FROM players")[0][0], 1000)

    # ================= уровни по опыту =================
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    add_player(path, 10, xp_=1599, staked=10 ** 6)       # ставок много, опыта мало
    add_player(path, 11, xp_=1600, staked=0)             # ставок нет, опыта на границе
    add_player(path, 12, xp_=levels.threshold(3), staked=5)
    for uid in (10, 11, 12):
        client.get("/api/me", headers=auth(uid, group=True))
    me = {uid: client.get("/api/me", headers=auth(uid)).json()["level"] for uid in (10, 11, 12)}
    check("уровни /api/me по опыту", me, {10: 1, 11: 2, 12: 3})
    top = client.get("/api/chat/top", headers=auth(11, group=True)).json()
    by_level = {e["staked"]: e["level"] for e in top["top"]}
    check("уровни в рейтинге по опыту, staked не меняется", by_level, {10 ** 6: 1, 0: 2, 5: 3})
    check("me в рейтинге", (top["me"]["level"], top["me"]["staked"]), (2, 0))
    check("chat_staked не менялся", top["chat_staked"], 10 ** 6 + 5)
    f = client.get("/api/farm", headers=auth(11)).json()
    check("farm.profile по опыту, staked сохранён", f["profile"], {"level": 2, "xp": 1600, "staked": 0,
                                                                  "next_threshold": levels.threshold(3)})
    check("слоты по уровню от xp", f["slots"], {"used": 0, "total": 2})
    f = client.get("/api/farm", headers=auth(10)).json()
    check("много ставок, мало опыта", (f["profile"]["level"], f["profile"]["staked"], f["slots"]["total"]), (1, 10 ** 6, 1))
    # покупка: слоты по опыту
    r = client.post("/api/farm/buy", headers=auth(10), json={"request_id": rid(70), "kind": "storage"})
    check("первая покупка", r.status_code, 200)
    r = client.post("/api/farm/buy", headers=auth(10), json={"request_id": rid(71), "kind": "income"})
    check("второй слот закрыт уровнем от опыта", (r.status_code, r.json()), (409, {"detail": "level_locked", "required_level": 2}))
    r = client.post("/api/farm/buy", headers=auth(11), json={"request_id": rid(72), "kind": "storage"})
    r2 = client.post("/api/farm/buy", headers=auth(11), json={"request_id": rid(73), "kind": "income"})
    check("при опыте 1600 два слота", (r.status_code, r2.status_code), (200, 200))
    # опыт из игры повышает уровень
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    add_player(path, 20, xp_=1500)
    check("до игры уровень 1", client.get("/api/me", headers=auth(20)).json()["level"], 1)
    db.spin_roulette(20, rid(80), [bet("number", 5, 200)], now=int(time.time()), db_path=path, rng=lambda n: 5)
    check("опыт 1500 + 194 = 1694: уровень 2", client.get("/api/me", headers=auth(20)).json()["level"], 2)

    # ================= /mydata и /deletemydata =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, xp_=777)
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: xp", export["player"]["xp"], 777)
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    payload = json.loads(upd.effective_message.documents[0]["data"].decode("utf-8"))
    check("/mydata: xp в player", payload["player"]["xp"], 777)
    add_player(path, 801, xp_=55)
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("игрок удалён вместе с опытом", (counts["players"], sql(path, "SELECT COUNT(*) FROM players WHERE telegram_id = ?", (SECRET_ID,))[0][0]), (1, 0))
    check("чужой опыт цел", player_xp(path, 801), 55)
    check("новый игрок после удаления начинает с 0", db.get_player(SECRET_ID, now=NOW, db_path=path)["xp"], 0)
    os.environ.pop("DB_PATH", None)

    # ================= политика =================
    page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "<script" not in page.lower(), "на странице скрипт"
    assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
    assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
    assert ("накопленный игровой опыт (число; считается по вашим играм и определяет уровень профиля; хранится, "
            "пока существует ваш игровой профиль)") in page
    assert "уровень профиля (число, считается по накопленному игровому опыту)" in page
    assert "считается по общей сумме ваших ставок" not in page, "старая формулировка уровня осталась"
    sec2 = page[page.index("<h2>2."):page.index("<h2>3.")]
    sec4 = page[page.index("<h2>4."):page.index("<h2>5.")]
    assert "игровой опыт" in sec2 and "игровому опыту" in sec4
    assert "Дата последнего обновления:" in page

    # ================= в логах нет id, ставок, опыта и балансов =================
    for secret in (str(SECRET_ID), str(SECRET_BET), "xp="):
        for line in cap.lines:
            assert secret not in line, "лишнее в логе: " + line[:80]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
