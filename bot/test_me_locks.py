import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
"""GET /api/me: проверка просроченных игр делает быстрое чтение и берёт блокировку записи (BEGIN IMMEDIATE) только если игра есть и просрочена
(как у хило и краша). Раньше у мин и блэкджека блокировка бралась на каждый запрос, даже без активных игр. Ответы и поведение те же: просроченные
игры закрываются как раньше (мины: автовывод или возврат, блэкджек: stand, хило: автовывод или возврат, краш: раунд решён)."""
import os
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import api as api_module
import web.routes_account as me_routes  # имена settle_expired_* ищутся в модуле маршрута /api/me
import blackjack
import crash
import db
import hilo
import mines
from core import db_conn
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
DAY = 86400
A, B = 424242421, 424242422


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Stack:
    def __init__(self, cards=(), cells=(), ints=()):
        self.cards, self.cells, self.ints = list(cards), list(cells), list(ints)

    def shuffle(self, shoe):
        for card in reversed(self.cards):
            shoe.remove(card)
            shoe.insert(0, card)

    def sample(self, population, k):
        return list(self.cells)

    def randrange(self, n):
        return self.ints.pop(0)


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


locks = [0]
_orig_execute = db_conn.TimedConnection.execute


def counting_execute(self, query, *args):
    if isinstance(query, str) and query.lstrip().upper().startswith("BEGIN IMMEDIATE"):
        locks[0] += 1
    return _orig_execute(self, query, *args)


tmp = tempfile.mkdtemp(prefix="me locks ")
try:
    path = os.path.join(tmp, "me.db")
    db.init_db(path)
    now = int(time.time())
    for uid in (A, B):
        sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
                  "VALUES (?, 1000000, 100, ?, ?, 0, 0, 0, 0)", (uid, now + 10 * DAY, now - DAY))

    def count(fn, *args, **kwargs):
        locks[0] = 0
        with mock.patch.object(db_conn.TimedConnection, "execute", counting_execute):
            result = fn(*args, **kwargs)
        return locks[0], result

    settlers = {"мины": lambda: db.settle_expired_mines(A, now=now, db_path=path), "блэкджек": lambda: db.settle_expired_blackjack(A, now=now, db_path=path),
                "краш": lambda: db.refund_legacy_crash(A, now=now, db_path=path), "хило": lambda: db.settle_expired_hilo(A, now=now, db_path=path)}

    # ---- нет активных игр: ни одной блокировки записи ни у одной из четырёх проверок
    for name, fn in settlers.items():
        n, res = count(fn)
        check("%s: нет игры: блокировки записи нет, ничего не закрыто" % name, (n, res), (0, False))

    # ---- активные, но не просроченные игры: тоже без блокировки
    db.mines_start(A, "me-lock-mines-0001", 100, 3, now=now, db_path=path, rng=Stack(cells=[0, 1, 2]))
    db.blackjack_start(B, "me-lock-bj-0000001", 100, now=now, db_path=path, rng=Stack(cards=["10S", "6C", "6D", "9H"]))
    db.hilo_start(A, "me-lock-hilo-0001", 100, now=now, db_path=path, rng=Stack(ints=[6, 0]))
    for name, fn in (("мины", lambda: db.settle_expired_mines(A, now=now + 60, db_path=path)), ("блэкджек", lambda: db.settle_expired_blackjack(B, now=now + 60, db_path=path)),
                     ("хило", lambda: db.settle_expired_hilo(A, now=now + 60, db_path=path))):
        n, res = count(fn)
        check("%s: игра идёт, не просрочена: блокировки записи нет" % name, (n, res), (0, False))
    check("игры целы", [sql(path, "SELECT status FROM mines_games")[0][0], sql(path, "SELECT status FROM blackjack_games")[0][0], sql(path, "SELECT status FROM hilo_games")[0][0]], ["active"] * 3)

    # ---- просроченные игры закрываются как раньше (одна блокировка на закрытие, повтор ничего не делает и блокировки не берёт)
    late = now + mines.MINES_IDLE_SECONDS + 5
    n, res = count(lambda: db.settle_expired_mines(A, now=late, db_path=path))
    check("мины: просрочена, нет открытых клеток: возврат ставки", (n, res, sql(path, "SELECT status, payout FROM mines_games")[0], sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (A,))[0][0]),
          (1, True, ("auto_refunded", 100), 999_900))   # у A ещё идёт хило со ставкой 100
    n, res = count(lambda: db.settle_expired_mines(A, now=late, db_path=path))
    check("мины: повтор: блокировки нет", (n, res), (0, False))
    n, res = count(lambda: db.settle_expired_blackjack(B, now=late, db_path=path))
    check("блэкджек: просрочена: автоматический stand, раздача закончена", (n, res, sql(path, "SELECT status, auto FROM blackjack_games")[0]), (1, True, ("finished", 1)))
    n, res = count(lambda: db.settle_expired_blackjack(B, now=late, db_path=path))
    check("блэкджек: повтор: блокировки нет", (n, res), (0, False))
    n, res = count(lambda: db.settle_expired_hilo(A, now=late + DAY, db_path=path))
    check("хило: просрочена без ходов: возврат", (n, res, sql(path, "SELECT status FROM hilo_games")[0][0]), (1, True, "refunded"))
    # просроченная мина с открытой клеткой: автовывод
    db.mines_start(A, "me-lock-mines-0002", 100, 3, now=late, db_path=path, rng=Stack(cells=[0, 1, 2]))
    db.mines_reveal(A, "me-lock-mines-0003", 10, now=late + 1, db_path=path)
    n, res = count(lambda: db.settle_expired_mines(A, now=late + 2 + mines.MINES_IDLE_SECONDS, db_path=path))
    check("мины: просрочена с открытой клеткой: автовывод", (n, res, sql(path, "SELECT status FROM mines_games ORDER BY id DESC")[0][0]), (1, True, "auto_cashed"))

    # ---- /api/me: четыре проверки без игр не берут блокировку; просроченные игры закрываются при запросе как раньше
    client = TestClient(api_module.create_app(TOKEN, [], db_path=path))
    seen = {}
    wrappers = {}
    for fname in ("settle_expired_mines", "settle_expired_blackjack", "refund_legacy_crash", "settle_expired_hilo"):
        real = getattr(me_routes, fname)

        def wrap(*a, _real=real, _name=fname, **k):
            before = locks[0]
            out = _real(*a, **k)
            seen[_name] = locks[0] - before
            return out
        wrappers[fname] = wrap

    def me(uid):
        return client.get("/api/me", headers={"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()))})

    sql(path, "DELETE FROM mines_games"); sql(path, "DELETE FROM blackjack_games"); sql(path, "DELETE FROM hilo_games")   # noqa: E702
    with mock.patch.object(db_conn.TimedConnection, "execute", counting_execute), \
            mock.patch.multiple(me_routes, **wrappers):
        r = me(A)
    check("/api/me без игр: 200, active_game null", (r.status_code, r.json()["active_game"]), (200, None))
    check("/api/me без игр: ни одной блокировки записи при проверке просроченных игр", seen, {"settle_expired_mines": 0, "settle_expired_blackjack": 0, "refund_legacy_crash": 0, "settle_expired_hilo": 0})
    # просроченные: игры, созданные давно по реальному времени, закрываются запросом /api/me
    old = int(time.time()) - mines.MINES_IDLE_SECONDS - 100
    db.mines_start(A, "me-lock-mines-0010", 100, 3, now=old, db_path=path, rng=Stack(cells=[0, 1, 2]))
    db.blackjack_start(A, "me-lock-bj-0000010", 100, now=old, db_path=path, rng=Stack(cards=["10S", "6C", "6D", "9H"]))
    check("до запроса обе игры активны", [sql(path, "SELECT status FROM mines_games")[0][0], sql(path, "SELECT status FROM blackjack_games")[0][0]], ["active", "active"])
    seen.clear()
    with mock.patch.object(db_conn.TimedConnection, "execute", counting_execute), mock.patch.multiple(me_routes, **wrappers):
        r = me(A)
    check("/api/me закрыл просроченные игры, active_game null", (r.status_code, r.json()["active_game"],
                                                              sql(path, "SELECT status FROM mines_games")[0][0], sql(path, "SELECT status, auto FROM blackjack_games")[0]),
          (200, None, "auto_refunded", ("finished", 1)))
    check("закрытие взяло по одной блокировке", [seen["settle_expired_mines"], seen["settle_expired_blackjack"]], [1, 1])
    # активная непросроченная игра видна в active_game, блокировка при этом не берётся
    db.mines_start(A, "me-lock-mines-0011", 100, 3, now=int(time.time()), db_path=path, rng=Stack(cells=[0, 1, 2]))
    seen.clear()
    with mock.patch.object(db_conn.TimedConnection, "execute", counting_execute), mock.patch.multiple(me_routes, **wrappers):
        r = me(A)
    check("активная игра: active_game mines, проверка без блокировки", (r.json()["active_game"], seen["settle_expired_mines"], seen["settle_expired_blackjack"]), ("mines", 0, 0))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
