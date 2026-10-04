import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from unittest import mock

from fastapi.testclient import TestClient

import api
import db
from api import create_app
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
SECRET_ID, SECRET_NAME, SECRET_BALANCE, SECRET_BET = 424242421, "СекретноеИмя", 7654321, 4321
REQUEST_ID = "secret-request-id-0001"

# тест не зависит от окружения и bot/.env
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


http_log = logging.getLogger("depnaya.http")
cap = Capture()
old_level = http_log.level
http_log.setLevel(logging.DEBUG)
http_log.addHandler(cap)

tmp = tempfile.mkdtemp()
try:
    path = os.path.join(tmp, "t.db")
    db.init_db(path)
    conn = sqlite3.connect(path)
    now = int(time.time())
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
                 "income_level, storage_level) VALUES (?, ?, 100, ?, ?, 0, 0, 0)", (SECRET_ID, SECRET_BALANCE, now, now))
    conn.commit()
    conn.close()
    client = TestClient(create_app(TOKEN, [], db_path=path))

    def auth():
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=SECRET_ID, auth_date=int(time.time()),
                                                           first_name=SECRET_NAME)}

    LINE = re.compile(r"^(GET|POST|OTHER) (\S+) (\d{3}) (\d+)ms( db_ms=begin:\d+,commit:\d+)?$")

    # ================= обычные запросы: одна строка INFO =================
    cap.records.clear()
    r = client.get("/api/me?token=secret-query&x=1", headers=auth())
    check("/api/me", r.status_code, 200)
    check("одна строка", len(cap.records), 1)
    level, line = cap.records[0]
    m = LINE.match(line)
    assert m, line
    check("метод, шаблон, код", (m.group(1), m.group(2), m.group(3)), ("GET", "/api/me", "200"))
    check("уровень INFO", level, logging.INFO)
    assert m.group(5), "у запроса с транзакциями есть db_ms: " + line
    assert "secret-query" not in line, "параметры запроса в логе"

    cap.records.clear()
    r = client.post("/api/roulette/spin", headers=auth(),
                    json={"request_id": REQUEST_ID, "bets": [{"type": "red", "value": None, "amount": SECRET_BET}]})
    check("spin", r.status_code, 200)
    check("одна строка", len(cap.records), 1)
    m = LINE.match(cap.records[0][1])
    assert m, cap.records[0][1]
    check("POST spin", (m.group(1), m.group(2), m.group(3)), ("POST", "/api/roulette/spin", "200"))
    assert m.group(5), "POST /api/* всегда с db_ms"
    # ошибки и другие POST
    cap.records.clear()
    client.post("/api/roulette/spin", headers=auth(), json={"bad": 1})
    client.post("/api/mines/reveal", headers=auth(), json={"request_id": REQUEST_ID, "cell": 3})
    client.post("/api/farm/buy", headers=auth(), json={"request_id": REQUEST_ID, "kind": "income"})
    client.get("/api/chat/top", headers=auth())
    client.get("/api/farm", headers=auth())
    client.get("/api/mines/state", headers=auth())
    check("по строке на запрос", [LINE.match(l).group(2) for _, l in cap.records],
          ["/api/roulette/spin", "/api/mines/reveal", "/api/farm/buy", "/api/chat/top", "/api/farm", "/api/mines/state"])
    check("коды ответов", [LINE.match(l).group(3) for _, l in cap.records], ["400", "409", "200", "200", "200", "200"])
    assert LINE.match(cap.records[0][1]).group(5), "и у запроса с ошибкой формы есть db_ms (POST /api/*)"
    # без подписи: 401 тоже записывается
    cap.records.clear()
    client.get("/api/me")
    check("401", LINE.match(cap.records[0][1]).group(3), "401")
    # ненайденный маршрут: путь не попадает в лог
    cap.records.clear()
    client.get("/api/secret-%d-path" % SECRET_ID)
    client.request("PROPFIND", "/x")
    check("(unmatched)", [LINE.match(l).group(2) for _, l in cap.records], ["(unmatched)", "(unmatched)"])
    check("странный метод", LINE.match(cap.records[1][1]).group(1), "OTHER")

    # ================= медленный запрос: WARNING =================
    clock = {"t": 0.0}

    def fake_perf():
        clock["t"] += 0.6   # между началом и концом запроса проходит 0,6 секунды
        return clock["t"]

    cap.records.clear()
    with mock.patch.object(api, "_perf", fake_perf):
        client.get("/api/me", headers=auth())
    level, line = cap.records[0]
    check("медленный: WARNING", level, logging.WARNING)
    assert re.match(r"^GET /api/me 200 \d{3,}ms", line) and " 6" in line, line
    ticks = iter([0.0, 0.1])
    cap.records.clear()
    with mock.patch.object(api, "_perf", lambda: next(ticks)):
        client.get("/api/me", headers=auth())
    check("быстрый (100 мс): INFO", (cap.records[0][0], LINE.match(cap.records[0][1]).group(4)), (logging.INFO, "100"))
    ticks = iter([0.0, 0.5])
    cap.records.clear()
    with mock.patch.object(api, "_perf", lambda: next(ticks)):
        client.get("/api/me", headers=auth())
    check("ровно 500 мс: ещё INFO", cap.records[0][0], logging.INFO)

    # ================= /health и вебхук не логируются =================
    cap.records.clear()
    check("/health", client.get("/health").status_code, 200)
    client.post("/telegram/webhook", json={"update_id": 1})
    check("в логе пусто", cap.records, [])

    # ================= в логе нет id, имён, балансов, значений тела =================
    cap.records.clear()
    client.get("/api/me", headers=auth())
    client.post("/api/roulette/spin", headers=auth(),
                json={"request_id": REQUEST_ID, "bets": [{"type": "red", "value": None, "amount": SECRET_BET}]})
    client.post("/api/mines/start", headers=auth(), json={"request_id": REQUEST_ID + "b", "bet": SECRET_BET, "mines": 3})
    text = "\n".join(l for _, l in cap.records)
    for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), str(SECRET_BET), REQUEST_ID, "tma ", "Authorization", "red"):
        assert secret not in text, "лишнее в логе: " + secret

    # ================= замер BEGIN и COMMIT =================
    holder = {"begin": 0.0, "commit": 0.0}
    token = db.request_timing.set(holder)
    try:
        ready = threading.Event()

        def hold_lock():
            other = sqlite3.connect(path)
            other.isolation_level = None
            other.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(0.4)
            other.execute("COMMIT")
            other.close()

        t = threading.Thread(target=hold_lock)
        t.start()
        ready.wait()
        db.get_player(SECRET_ID, now=int(time.time()), db_path=path)   # ждёт блокировку записи
        t.join()
    finally:
        db.request_timing.reset(token)
    assert holder["begin"] >= 0.25, "ожидание блокировки должно попасть в begin: %r" % holder
    assert holder["commit"] >= 0.0
    # вне запроса замеров нет и соединение работает как обычное
    check("вне запроса значения нет", db.request_timing.get(), None)
    check("get_player вне запроса работает", db.get_player(SECRET_ID, now=int(time.time()), db_path=path)["balance"] >= 0, True)
    conn2 = db._connect(path)
    check("соединение с замерами", isinstance(conn2, sqlite3.Connection), True)
    conn2.close()
finally:
    http_log.removeHandler(cap)
    http_log.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
