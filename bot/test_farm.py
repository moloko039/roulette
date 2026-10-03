import asyncio
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from fractions import Fraction
from unittest import mock

from fastapi.testclient import TestClient

import bot
import db
import farm
import levels
import ratelimit
import wallet
from api import create_app
from economy import HOUR, MAX_HOURS, accrue
from roulette import MAX_SAFE_INT, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
SECRET_ID, SECRET_NAME, SECRET_BALANCE = 424242421, "СекретноеИмя", 7654321
NOW = 1_760_000_000

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


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

tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "f%d.db" % counter[0])
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


def add_player(path, uid, balance=100_000, rate=100, total=0, income=0, storage=0, last_accrual=NOW):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
              "income_level, storage_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, balance, rate, last_accrual, last_accrual, total, income, storage))


def player(path, uid):
    return sql(path, "SELECT balance, rate, last_accrual, total_staked, income_level, storage_level "
                     "FROM players WHERE telegram_id = ?", (uid,))[0]


def rid(n):
    return "farm-req-%06d" % n


try:
    # ================= farm.py: значения =================
    check("доход: ставка", [farm.income_rate(n) for n in (0, 1, 2, 19, 20)], [100, 135, 182, 29946, 40427])
    check("доход: цена", [farm.income_cost(n) for n in (0, 1, 2, 19, 20)], [1000, 1800, 3240, 70823534, None])
    check("хранилище: часы", [farm.storage_hours(n) for n in range(9)], [30, 36, 42, 48, 54, 60, 66, 72, 78])
    check("хранилище: цена", [farm.storage_cost(n) for n in range(9)],
          [1000, 2000, 4000, 8000, 16000, 32000, 64000, 128000, None])
    check("максимумы", (farm.INCOME_MAX_LEVEL, farm.STORAGE_MAX_LEVEL), (20, 8))
    for n in range(21):  # те же числа точной дробной арифметикой, без float
        check("ставка %d" % n, farm.income_rate(n), (100 * Fraction(27, 20) ** n).__floor__())
        if n < 20:
            check("цена дохода %d" % n, farm.income_cost(n), (1000 * Fraction(9, 5) ** n).__floor__())
    rates = [farm.income_rate(n) for n in range(21)]
    costs = [farm.income_cost(n) for n in range(20)]
    assert all(a < b for a, b in zip(rates, rates[1:])), "ставка растёт не строго"
    assert all(a < b for a, b in zip(costs, costs[1:])), "цена дохода растёт не строго"
    scosts = [farm.storage_cost(n) for n in range(8)]
    assert all(a < b for a, b in zip(scosts, scosts[1:])), "цена хранилища растёт не строго"
    for bad in (-1, 21, 1.5, "1", None, True):
        raises(ValueError, farm.income_rate, bad)
        raises(ValueError, farm.income_cost, bad)
    for bad in (-1, 9, 2.0, None):
        raises(ValueError, farm.storage_hours, bad)
        raises(ValueError, farm.storage_cost, bad)
    # причины блокировки в порядке проверок покупки
    check("причина: нет", farm.block_reason("income", 0, 0, 0, 1000), None)
    check("причина: мало фишек", farm.block_reason("income", 0, 0, 0, 999), "insufficient_funds")
    check("причина: слоты заняты", farm.block_reason("income", 1, 0, 0, 10 ** 9), "level_locked")
    check("причина: максимум раньше слотов", farm.block_reason("income", 20, 0, 0, 0), "max_level")
    check("слоты ровно на границе", farm.block_reason("storage", 1, 0, 1600, 10 ** 9), None)
    check("слоты заняты при уровне 2", farm.block_reason("storage", 1, 1, 1600, 10 ** 9), "level_locked")
    raises(ValueError, farm.block_reason, "other", 0, 0, 0, 0)
    # без float в исходнике
    src = open(os.path.join(HERE, "farm.py"), encoding="utf-8").read()
    import re
    code = "\n".join(l.split("#")[0] for l in re.sub(r'""".*?"""', "", src, flags=re.S).splitlines())
    assert "float" not in code and not re.search(r"\d\.\d", code) and not re.search(r"(?<!/)/(?!/)", code)

    # ================= старые вызовы accrue =================
    check("accrue без max_hours: меньше часа", accrue(1000, 1000 + 3599, 100), (0, 1000))
    check("accrue без max_hours: 5 часов", accrue(0, 5 * HOUR + 10, 100), (500, 5 * HOUR))
    check("accrue без max_hours: потолок", accrue(0, 50 * HOUR, 100), (MAX_HOURS * 100, 50 * HOUR))
    check("accrue с max_hours: больше потолка", accrue(0, 50 * HOUR, 100, max_hours=48), (4800, 50 * HOUR))
    check("accrue с max_hours: меньше потолка", accrue(0, 40 * HOUR, 100, max_hours=48), (4000, 40 * HOUR))

    # ================= миграция =================
    old = new_db()
    os.remove(old)
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL, "
                 "total_staked INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO players VALUES (1, 777, 100, 1000, 1000, 55), (2, 5, 100, 2000, 2000, 0)")
    conn.commit()
    conn.close()
    db.init_db(old)
    cols = {r[1]: (r[2], r[3], r[4]) for r in sql(old, "PRAGMA table_info(players)")}
    check("income_level", cols["income_level"], ("INTEGER", 1, "0"))
    check("storage_level", cols["storage_level"], ("INTEGER", 1, "0"))
    check("данные целы", sql(old, "SELECT telegram_id, balance, rate, last_accrual, created_at, total_staked FROM players ORDER BY 1"),
          [(1, 777, 100, 1000, 1000, 55), (2, 5, 100, 2000, 2000, 0)])
    check("rate у существующих игроков 100", [r[0] for r in sql(old, "SELECT rate FROM players")], [100, 100])
    check("уровни 0", sql(old, "SELECT income_level, storage_level FROM players"), [(0, 0), (0, 0)])
    check("таблица покупок", [r[1] for r in sql(old, "PRAGMA table_info(farm_purchases)")],
          ["telegram_id", "request_id", "kind", "level_after", "cost", "created_at"])
    sql(old, "UPDATE players SET income_level = 3, storage_level = 2 WHERE telegram_id = 1")
    db.init_db(old)
    db.init_db(old)
    check("повторный init_db ничего не меняет", sql(old, "SELECT income_level, storage_level FROM players ORDER BY telegram_id"),
          [(3, 2), (0, 0)])
    check("столбцы по одному", [r[1] for r in sql(old, "PRAGMA table_info(players)")].count("income_level"), 1)

    # ================= покупка: успех =================
    path = new_db()
    add_player(path, 1, balance=10_000, total=1600)          # уровень профиля 2: два слота
    r = db.buy_upgrade(1, rid(1), "income", now=NOW, db_path=path)
    check("доход куплен", r, {"kind": "income", "level_after": 1, "cost": 1000, "balance": 9000, "replayed": False})
    check("уровень, rate и баланс", player(path, 1)[:2] + player(path, 1)[4:], (9000, 135, 1, 0))
    check("запись покупки", sql(path, "SELECT kind, level_after, cost, created_at FROM farm_purchases"),
          [("income", 1, 1000, NOW)])
    r = db.buy_upgrade(1, rid(2), "storage", now=NOW, db_path=path)
    check("хранилище куплено", r, {"kind": "storage", "level_after": 1, "cost": 1000, "balance": 8000, "replayed": False})
    check("уровни", player(path, 1)[4:], (1, 1))
    check("rate хранилище не меняет", player(path, 1)[1], 135)
    # слоты заняты (1 + 1 = уровень профиля 2)
    e = raises(farm.LevelLocked, db.buy_upgrade, 1, rid(3), "income", NOW, path)
    check("нужный уровень профиля", e.required_level, 3)
    check("после отказа без изменений", (player(path, 1)[0], player(path, 1)[4:]), (8000, (1, 1)))
    check("записей две", sql(path, "SELECT COUNT(*) FROM farm_purchases")[0][0], 2)

    # граница лимита: ровно used == уровень профиля - 1 разрешено
    path = new_db()
    add_player(path, 1, balance=10_000, total=1599)          # уровень 1: один слот
    db.buy_upgrade(1, rid(1), "storage", now=NOW, db_path=path)
    raises(farm.LevelLocked, db.buy_upgrade, 1, rid(2), "storage", NOW, path)
    sql(path, "UPDATE players SET total_staked = 1600")      # уровень стал 2
    check("после роста уровня профиля покупка разрешена", db.buy_upgrade(1, rid(3), "storage", NOW, path)["level_after"], 2)
    check("цена второго уровня хранилища", sql(path, "SELECT cost FROM farm_purchases ORDER BY rowid")[-1][0], 2000)

    # максимальные уровни (раньше проверки слотов)
    path = new_db()
    add_player(path, 1, balance=10 ** 9, total=0, income=20)
    raises(farm.MaxLevel, db.buy_upgrade, 1, rid(1), "income", NOW, path)
    add_player(path, 2, balance=10 ** 9, total=0, storage=8)
    raises(farm.MaxLevel, db.buy_upgrade, 2, rid(2), "storage", NOW, path)
    add_player(path, 3, balance=10 ** 9, total=levels.threshold(60), income=19, storage=8)
    check("последний уровень дохода куплен", db.buy_upgrade(3, rid(3), "income", NOW, path)["level_after"], 20)
    check("rate на максимуме", player(path, 3)[1], 40427)
    raises(farm.MaxLevel, db.buy_upgrade, 3, rid(4), "income", NOW, path)

    # нехватка фишек, откат (в том числе начисления)
    path = new_db()
    add_player(path, 1, balance=500, total=1600, last_accrual=NOW - 3 * HOUR)
    raises(wallet.InsufficientFunds, db.buy_upgrade, 1, rid(1), "income", NOW, path)
    raises(InsufficientFunds, db.buy_upgrade, 1, rid(2), "income", NOW, path)
    check("ничего не записано, начисление откатилось", player(path, 1), (500, 100, NOW - 3 * HOUR, 1600, 0, 0))
    check("покупок нет", sql(path, "SELECT COUNT(*) FROM farm_purchases")[0][0], 0)
    # ошибка после списания откатывает всё
    add_player(path, 2, balance=5000, total=1600)
    with mock.patch.object(farm, "income_rate", side_effect=RuntimeError("секрет")):
        raises(RuntimeError, db.buy_upgrade, 2, rid(3), "income", NOW, path)
    check("откат после ошибки", (player(path, 2)[0], player(path, 2)[1], player(path, 2)[4:]), (5000, 100, (0, 0)))
    check("записи нет", sql(path, "SELECT COUNT(*) FROM farm_purchases WHERE telegram_id = 2")[0][0], 0)
    raises(ValueError, db.buy_upgrade, 2, rid(4), "other", NOW, path)

    # повтор с тем же request_id
    path = new_db()
    add_player(path, 1, balance=10_000, total=1600)
    first = db.buy_upgrade(1, rid(1), "income", NOW, path)
    again = db.buy_upgrade(1, rid(1), "income", NOW + 5, path)
    check("повтор: тот же ответ", {k: v for k, v in again.items() if k != "replayed"},
          {k: v for k, v in first.items() if k != "replayed"})
    check("повтор: replayed", (first["replayed"], again["replayed"]), (False, True))
    check("повтор не списывает", player(path, 1)[0], 9000)
    check("одна запись", sql(path, "SELECT COUNT(*) FROM farm_purchases")[0][0], 1)
    # повтор после того, как слоты закончились и без денег: всё равно тот же ответ
    db.buy_upgrade(1, rid(2), "storage", NOW, path)
    again = db.buy_upgrade(1, rid(1), "income", NOW + 9, path)
    check("повтор при заполненных слотах", (again["replayed"], again["level_after"]), (True, 1))

    # параллельные запросы: один свободный слот
    path = new_db()
    add_player(path, 1, balance=10_000, total=1599)
    results, errors = [], []

    def buyer(request_id, kind):
        try:
            results.append(db.buy_upgrade(1, request_id, kind, NOW, path))
        except farm.LevelLocked:
            errors.append("locked")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=buyer, args=(rid(10 + i), "income" if i % 2 else "storage")) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("ровно одна покупка", (len(results), errors.count("locked"), len(errors)), (1, 5, 5))
    check("списано один раз", player(path, 1)[0], 9000)
    check("слоты не превышены", sum(player(path, 1)[4:]), 1)
    # один и тот же request_id параллельно: списание один раз
    path = new_db()
    add_player(path, 1, balance=10_000, total=10_000)
    results.clear()
    errors.clear()
    threads = [threading.Thread(target=buyer, args=(rid(99), "income")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("без ошибок", errors, [])
    check("одно списание", (player(path, 1)[0], sorted(r["replayed"] for r in results)), (9000, [False, True, True, True, True]))
    check("одна запись", sql(path, "SELECT COUNT(*) FROM farm_purchases")[0][0], 1)

    # ================= начисление и покупка =================
    # накопленное до покупки по старой ставке; новая ставка действует только дальше
    path = new_db()
    add_player(path, 1, balance=2000, total=1600, last_accrual=NOW - 5 * HOUR)
    r = db.buy_upgrade(1, rid(1), "income", NOW, path)
    check("5 часов по старой ставке, затем списание: 2000 + 500 - 1000", r["balance"], 1500)
    check("last_accrual сдвинут на целые часы", player(path, 1)[2], NOW)
    p = db.get_player(1, now=NOW + 2 * HOUR, db_path=path)
    check("после покупки 2 часа по новой ставке", p["balance"], 1500 + 2 * 135)
    # остаток минут сохраняется
    path = new_db()
    add_player(path, 1, balance=2000, total=1600, last_accrual=NOW - 5 * HOUR - 1800)
    db.buy_upgrade(1, rid(1), "income", NOW, path)
    check("остаток 30 минут сохранён", player(path, 1)[2], NOW - 1800)
    check("баланс: 2000 + 500 - 1000", player(path, 1)[0], 1500)
    p = db.get_player(1, now=NOW - 1800 + HOUR, db_path=path)
    check("следующий час по новой ставке", p["balance"], 1500 + 135)
    # потолок хранилища: накопленное сверх старого потолка не воскресает
    path = new_db()
    add_player(path, 1, balance=5000, total=1600, last_accrual=NOW - 40 * HOUR)
    r = db.buy_upgrade(1, rid(1), "storage", NOW, path)
    check("при покупке платим по старому потолку (30 ч): 5000 + 3000 - 1000", r["balance"], 7000)
    check("лишние часы сгорели", player(path, 1)[2], NOW)
    p = db.get_player(1, now=NOW + HOUR, db_path=path)
    check("1 час после покупки", p["balance"], 7100)
    p = db.get_player(1, now=NOW + 100 * HOUR, db_path=path)
    check("новый потолок 36 часов действует после покупки: 7100 + 36*100", p["balance"], 7100 + 3600)
    # хранилище 3: не больше 48 часов дохода
    path = new_db()
    add_player(path, 1, balance=0, storage=3, last_accrual=NOW - 100 * HOUR)
    p = db.get_player(1, now=NOW, db_path=path)
    check("100 часов простоя при хранилище 3: 48 часов", p["balance"], 48 * 100)
    add_player(path, 2, balance=0, storage=0, last_accrual=NOW - 100 * HOUR)
    check("без хранилища 30 часов", db.get_player(2, now=NOW, db_path=path)["balance"], 30 * 100)
    add_player(path, 3, balance=0, storage=8, income=2, rate=182, last_accrual=NOW - 100 * HOUR)
    check("хранилище 8 и ставка 182: 78 часов", db.get_player(3, now=NOW, db_path=path)["balance"], 78 * 182)
    # spin использует потолок и ставку игрока
    add_player(path, 4, balance=0, storage=3, last_accrual=NOW - 100 * HOUR)
    res = db.spin_roulette(4, "spin-farm-001", [{"type": "red", "value": None, "amount": 1}], now=NOW, db_path=path,
                           rng=lambda n: 1)
    check("spin: баланс с потолком игрока", res["balance"], 4800 - 1 + 2)
    # chat_top считает баланс с учётом ставки и потолка каждого игрока (без записи)
    path = new_db()
    add_player(path, 1, balance=1000, rate=135, income=1, storage=0, last_accrual=NOW - 40 * HOUR)
    add_player(path, 2, balance=1000, rate=100, storage=3, last_accrual=NOW - 40 * HOUR)
    add_player(path, 3, balance=1000, rate=100, storage=0, last_accrual=NOW - 40 * HOUR)
    for uid, name in ((1, "A"), (2, "B"), (3, "C")):
        sql(path, "INSERT INTO chat_members VALUES ('room', ?, ?, ?, ?)", (uid, name, NOW, NOW))
    before = sql(path, "SELECT telegram_id, balance, last_accrual FROM players ORDER BY 1")
    top = db.chat_top("room", 1, "A", now=NOW, db_path=path)["top"]
    check("баланс в рейтинге", {e["name"]: e["balance"] for e in top},
          {"A": 1000 + 30 * 135, "B": 1000 + 40 * 100, "C": 1000 + 30 * 100})
    check("порядок по балансу", [e["name"] for e in top], ["A", "B", "C"])
    check("chat_top ничего не записал", sql(path, "SELECT telegram_id, balance, last_accrual FROM players ORDER BY 1"), before)

    # ================= API =================
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, total=0, last_accrual=now_real)
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "20",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "50"}),
                                clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid, first_name=SECRET_NAME):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=first_name)}

    def buy(uid, body):
        return client.post("/api/farm/buy", headers=auth(uid), json=body)

    cap.lines.clear()
    # без подписи
    check("farm без подписи", client.get("/api/farm").status_code, 401)
    check("buy без подписи", client.post("/api/farm/buy", json={"request_id": rid(1), "kind": "income"}).status_code, 401)
    # /api/me
    r = client.get("/api/me", headers=auth(SECRET_ID)).json()
    check("/api/me: уровни", (r["income_level"], r["storage_level"]), (0, 0))
    # GET /api/farm: структура
    f = client.get("/api/farm", headers=auth(SECRET_ID)).json()
    check("ключи верхнего уровня", sorted(f), ["balance", "income", "profile", "slots", "storage"])
    check("balance", f["balance"], SECRET_BALANCE)
    check("profile", f["profile"], {"level": 1, "staked": 0, "next_threshold": 1600})
    check("slots", f["slots"], {"used": 0, "total": 1})
    check("income", f["income"], {"level": 0, "max": 20, "rate": 100, "next_rate": 135, "next_cost": 1000,
                                  "can_buy": True, "reason": None})
    check("storage", f["storage"], {"level": 0, "max": 8, "hours": 30, "next_hours": 36, "next_cost": 1000,
                                    "can_buy": True, "reason": None})
    # покупки через API
    r = buy(SECRET_ID, {"request_id": rid(1), "kind": "income"})
    check("buy 200", r.status_code, 200)
    check("buy ответ", r.json(), {"kind": "income", "level_after": 1, "cost": 1000, "balance": SECRET_BALANCE - 1000,
                                  "replayed": False})
    r = buy(SECRET_ID, {"request_id": rid(1), "kind": "income"})
    check("повтор через API", (r.status_code, r.json()["replayed"], r.json()["balance"]), (200, True, SECRET_BALANCE - 1000))
    r = buy(SECRET_ID, {"request_id": rid(2), "kind": "storage"})
    check("слоты заняты: 409", (r.status_code, r.json()), (409, {"detail": "level_locked", "required_level": 2}))
    f = client.get("/api/farm", headers=auth(SECRET_ID)).json()
    check("после покупки: слоты и причина", (f["slots"], f["income"]["level"], f["income"]["rate"], f["storage"]["reason"]),
          ({"used": 1, "total": 1}, 1, 135, "level_locked"))
    check("и доход заблокирован слотами", f["income"]["reason"], "level_locked")
    # неверные запросы: 400 одним текстом
    for body in ({"request_id": rid(3), "kind": "other"}, {"request_id": "short", "kind": "income"},
                 {"request_id": rid(3)}, {"kind": "income"}, {"request_id": rid(3), "kind": "income", "x": 1},
                 {"request_id": rid(3), "kind": 5}, [], "text", {"request_id": 5, "kind": "income"}):
        r = buy(SECRET_ID, body)
        check("400 для %r" % (body,), (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/farm/buy", headers=auth(SECRET_ID), content=b"not json")
    check("400 без json", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    # max_level и insufficient_funds через API
    sql(path, "UPDATE players SET income_level = 20, storage_level = 0, total_staked = ?, balance = 10 WHERE telegram_id = ?",
        (levels.threshold(60), SECRET_ID))
    r = buy(SECRET_ID, {"request_id": rid(4), "kind": "income"})
    check("max_level", (r.status_code, r.json()), (409, {"detail": "max_level"}))
    r = buy(SECRET_ID, {"request_id": rid(5), "kind": "storage"})
    check("insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    f = client.get("/api/farm", headers=auth(SECRET_ID)).json()
    check("reason max_level / insufficient_funds", (f["income"]["reason"], f["income"]["next_cost"], f["income"]["next_rate"],
                                                    f["storage"]["reason"]), ("max_level", None, None, "insufficient_funds"))
    check("can_buy false", (f["income"]["can_buy"], f["storage"]["can_buy"]), (False, False))
    check("профиль на максимуме", f["profile"]["next_threshold"], None)
    # группа write: покупки тоже ограничены (burst 6 уже частично израсходован)
    codes = [buy(SECRET_ID, {"request_id": rid(20 + i), "kind": "storage"}).status_code for i in range(8)]
    assert 429 in codes, codes
    r = buy(SECRET_ID, {"request_id": rid(40), "kind": "storage"})
    check("429 для покупок", (r.status_code, r.json(), r.headers["Retry-After"]), (429, {"error": "too_many_requests"}, "1"))

    # ================= /mydata, /deletemydata, очистка =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, total=2000)
    add_player(path, 801, balance=100_000, total=5000)
    db.buy_upgrade(SECRET_ID, rid(1), "income", NOW, path)
    db.buy_upgrade(SECRET_ID, rid(2), "storage", NOW + 10, path)
    db.buy_upgrade(801, rid(3), "income", NOW, path)
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: уровни", (export["player"]["income_level"], export["player"]["storage_level"]), (1, 1))
    check("выгрузка: покупки (новые первыми)", export["farm_purchases"],
          [{"time": NOW + 10, "kind": "storage", "level": 1, "cost": 1000},
           {"time": NOW, "kind": "income", "level": 1, "cost": 1000}])
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    payload = json.loads(upd.effective_message.documents[0]["data"].decode("utf-8"))
    check("/mydata: farm_purchases", [p["kind"] for p in payload["farm_purchases"]], ["storage", "income"])
    check("/mydata: уровни в player", (payload["player"]["income_level"], payload["player"]["storage_level"]), (1, 1))
    # последние 100
    for i in range(110):
        sql(path, "INSERT INTO farm_purchases VALUES (801, ?, 'storage', 1, 1, ?)", ("bulk-%05d" % i, NOW + i))
    check("в выгрузке не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["farm_purchases"]), 100)
    # очистка старых покупок (срок как у раундов), уровни остаются
    sql(path, "INSERT INTO farm_purchases VALUES (801, 'old-request-1', 'income', 1, 1000, ?)", (NOW - 40 * 86400,))
    sql(path, "INSERT INTO farm_purchases VALUES (801, 'new-request-1', 'income', 1, 1000, ?)", (NOW - 10 * 86400,))
    levels_before = player(path, 801)[4:]
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30, batch=50)
    check("удалена только старая (110 свежих и две записи остались)", deleted["farm_purchases"], 1)
    check("новая осталась", sql(path, "SELECT COUNT(*) FROM farm_purchases WHERE request_id = 'new-request-1'")[0][0], 1)
    check("уровни не изменились", player(path, 801)[4:], levels_before)
    deleted = db.purge_old_data(now=NOW + 40 * 86400, db_path=path, rounds_days=30, batch=50)
    check("через срок удалены все (пачками)", sql(path, "SELECT COUNT(*) FROM farm_purchases")[0][0], 0)
    # возвращаем покупки для проверки удаления
    sql(path, "UPDATE players SET total_staked = 20000 WHERE telegram_id IN (?, 802)", (SECRET_ID,))
    db.buy_upgrade(SECRET_ID, rid(11), "storage", NOW, path)
    add_player(path, 802, balance=100_000, total=2000)
    db.buy_upgrade(802, rid(12), "income", NOW, path)
    others = sql(path, "SELECT telegram_id, request_id FROM farm_purchases WHERE telegram_id != ?", (SECRET_ID,))
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалено покупок", counts["farm_purchases"], 1)
    check("покупки игрока удалены", sql(path, "SELECT COUNT(*) FROM farm_purchases WHERE telegram_id = ?", (SECRET_ID,))[0][0], 0)
    check("чужие целы", sql(path, "SELECT telegram_id, request_id FROM farm_purchases WHERE telegram_id != ?", (SECRET_ID,)), others)
    check("чужие игроки целы", sorted(r[0] for r in sql(path, "SELECT telegram_id FROM players")), [801, 802])
    check("чужие уровни целы", player(path, 802)[4:], (1, 0))
    check("после удаления выгрузки нет", db.get_player_export(SECRET_ID, db_path=path), None)
    # сообщение об удалении
    add_player(path, 900, balance=5000, total=2000)
    db.buy_upgrade(900, rid(13), "income", NOW, path)
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "покупки улучшений — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)

    # ================= политика =================
    page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "<script" not in page.lower(), "на странице скрипт"
    assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
    assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
    assert "уровни ваших игровых улучшений (доход, хранилище)" in page
    assert "историю ваших покупок улучшений за фишки (вид, уровень, цена, время)" in page
    assert "История покупок улучшений хранится 30 дней." in page
    sec2 = page[page.index("<h2>2."):page.index("<h2>3.")]
    sec5 = page[page.index("<h2>5."):page.index("<h2>6.")]
    assert "уровни ваших игровых улучшений" in sec2 and "покупок улучшений за фишки" in sec2
    assert "История раундов хранится 30 дней" in sec5 and "История покупок улучшений хранится 30 дней" in sec5
    sec4 = page[page.index("<h2>4."):page.index("<h2>5.")]
    assert "улучшений" not in sec4, "уровни улучшений другим участникам не показываются"
    assert "Дата последнего обновления:" in page

    # ================= в логах нет id, имён, балансов, цен и уровней =================
    for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), "70823534"):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:80]
    for line in cap.lines:
        assert "farm-req" not in line and "уровень" not in line.lower() or "Очистка" in line
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
