import logging
import os
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import api
import db
import ratelimit
from api import create_app
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
SECRET_ID, SECRET_NAME, SECRET_BALANCE = 424242421, "СекретноеИмя", 7654321

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("WRITE_RATE_PER_SEC", "WRITE_RATE_BURST", "READ_RATE_PER_SEC", "READ_RATE_BURST", "DB_PATH",
             "BOT_TOKEN", "ALLOWED_ORIGINS", "PUBLIC_URL", "WEBHOOK_SECRET", "LOCAL_POLLING", "WEBAPP_URL",
             "OWNER_CHAT_ID", "BACKUP_PUBLIC_KEY", "BACKUP_ENABLED", "BACKUP_DIR", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


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


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


tmp = tempfile.mkdtemp()
try:
    # ================= настройки =================
    c = ratelimit.load_config({})
    check("умолчания", (c["WRITE_RATE_PER_SEC"], c["WRITE_RATE_BURST"], c["READ_RATE_PER_SEC"], c["READ_RATE_BURST"], c["invalid"]),
          (5, 20, 10, 40, []))
    c = ratelimit.load_config({"WRITE_RATE_PER_SEC": "2", "WRITE_RATE_BURST": " 7 ", "READ_RATE_PER_SEC": "3", "READ_RATE_BURST": "9"})
    check("свои значения", (c["WRITE_RATE_PER_SEC"], c["WRITE_RATE_BURST"], c["READ_RATE_PER_SEC"], c["READ_RATE_BURST"], c["invalid"]),
          (2, 7, 3, 9, []))
    for bad in ("0", "-1", "abc", "1.5", "1 2", "9999999", "٣"):
        c = ratelimit.load_config({"WRITE_RATE_BURST": bad, "READ_RATE_PER_SEC": bad})
        check("неверное %r заменено" % bad, (c["WRITE_RATE_BURST"], c["READ_RATE_PER_SEC"], sorted(c["invalid"])),
              (20, 10, ["READ_RATE_PER_SEC", "WRITE_RATE_BURST"]))
    cap.lines.clear()
    ratelimit.warn_config(ratelimit.load_config({"WRITE_RATE_BURST": "secret-bad-value", "READ_RATE_BURST": "0"}))
    warns = [l for l in cap.lines if "Неверные значения" in l]
    check("одно предупреждение", len(warns), 1)
    assert "WRITE_RATE_BURST" in warns[0] and "READ_RATE_BURST" in warns[0] and "secret-bad-value" not in warns[0], warns
    cap.lines.clear()
    ratelimit.warn_config(ratelimit.load_config({}))
    check("без ошибок предупреждения нет", [l for l in cap.lines if "Неверные" in l], [])

    # ================= токен-бакет =================
    clock = Clock()
    cfg = ratelimit.load_config({"WRITE_RATE_PER_SEC": "2", "WRITE_RATE_BURST": "4", "READ_RATE_PER_SEC": "10", "READ_RATE_BURST": "6"})
    lim = ratelimit.RateLimiter(cfg, clock=clock)
    check("в пределах пакета", [lim.check(1, "write") for _ in range(4)], [None] * 4)
    wait = lim.check(1, "write")
    assert isinstance(wait, int) and wait >= 1, wait
    check("rate 2/с: ждать 1 с", wait, 1)
    clock.t += 0.4
    assert lim.check(1, "write") is not None, "токен не успел восстановиться"
    clock.t += 0.6   # всего 1 с: 2 токена
    check("восстановление со временем", [lim.check(1, "write"), lim.check(1, "write")], [None, None])
    assert lim.check(1, "write") is not None
    clock.t += 100   # пакет не накапливается выше burst
    check("не больше burst", [lim.check(1, "write") for _ in range(5)], [None] * 4 + [1])
    # Retry-After не меньше 1 и целое даже при высокой скорости
    fast = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1000", "WRITE_RATE_BURST": "1"}), clock=clock)
    fast.check(7, "write")
    check("Retry-After не меньше 1", fast.check(7, "write"), 1)
    slow = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "1"}), clock=clock)
    slow.check(7, "write")
    clock.t += 0.25
    check("ожидание округляется вверх", slow.check(7, "write"), 1)
    # игроки и группы независимы
    clock.t += 1000
    lim = ratelimit.RateLimiter(cfg, clock=clock)
    for _ in range(4):
        lim.check(1, "write")
    assert lim.check(1, "write") is not None
    check("другой игрок не затронут", lim.check(2, "write"), None)
    check("другая группа не затронута", lim.check(1, "read"), None)
    for _ in range(5):
        lim.check(1, "read")
    assert lim.check(1, "read") is not None
    check("write не зависит от read", lim.check(2, "read"), None)

    # ================= память =================
    clock = Clock()
    lim = ratelimit.RateLimiter(cfg, clock=clock)
    for uid in range(5):
        lim.check(uid, "read")
    check("5 бакетов", len(lim.buckets), 5)
    clock.t += ratelimit.IDLE_SECONDS - 1
    lim.check(100, "read")
    check("до 10 минут бакеты живут", len(lim.buckets), 6)
    clock.t += 2
    lim.check(101, "read")
    check("старше 10 минут удалены (остались 100 и 101)", sorted(k[0] for k in lim.buckets), [100, 101])
    with mock.patch.object(ratelimit, "MAX_BUCKETS", 5):
        lim = ratelimit.RateLimiter(cfg, clock=clock)
        for uid in range(8):
            clock.t += 1
            lim.check(uid, "read")
        check("предел записей", len(lim.buckets), 5)
        check("удалены самые старые", sorted(k[0] for k in lim.buckets), [3, 4, 5, 6, 7])
        clock.t += 1
        lim.check(3, "read")   # обращение освежает запись
        lim.check(99, "read")
        assert (3, "read") in lim.buckets and (4, "read") not in lim.buckets
    check("предел по умолчанию", ratelimit.MAX_BUCKETS, 50_000)

    # ================= API =================
    path = os.path.join(tmp, "rl.db")
    db.init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked) "
                 "VALUES (?, ?, 100, ?, ?, 0)", (SECRET_ID, SECRET_BALANCE, int(time.time()), int(time.time())))
    conn.commit()
    conn.close()
    clock = Clock()
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "3",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "2"}), clock=clock)
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid, chat=True, first_name=SECRET_NAME):
        data = make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=first_name,
                              chat_type="group" if chat else None, chat_instance="room" if chat else None)
        return {"Authorization": "tma " + data}

    def spin(uid, request_id="rate-req-0001"):
        return client.post("/api/roulette/spin", headers=auth(uid),
                           json={"request_id": request_id, "bets": [{"type": "red", "value": None, "amount": 1}]})

    cap.lines.clear()
    # неподписанные запросы бакеты не создают
    for headers in ({}, {"Authorization": "tma bad"}, {"Authorization": "Bearer x"}):
        for path_ in ("/api/me", "/api/chat/top"):
            r = client.get(path_, headers=headers)
            check("401 без подписи", r.status_code, 401)
        r = client.post("/api/roulette/spin", headers=headers, json={"request_id": "x" * 10, "bets": []})
        check("401 без подписи (spin)", r.status_code, 401)
    check("бакетов нет", len(lim.buckets), 0)
    # read: burst 2
    check("read 1", client.get("/api/me", headers=auth(SECRET_ID)).status_code, 200)
    check("read 2 (chat/top тоже read)", client.get("/api/chat/top", headers=auth(SECRET_ID)).status_code, 200)
    r = client.get("/api/me", headers=auth(SECRET_ID))
    check("read превышен", r.status_code, 429)
    check("тело 429", r.json(), {"error": "too_many_requests"})
    assert r.headers["Retry-After"].isdigit() and int(r.headers["Retry-After"]) >= 1, r.headers
    check("Retry-After секунды", r.headers["Retry-After"], "1")
    check("chat/top тоже 429", client.get("/api/chat/top", headers=auth(SECRET_ID)).status_code, 429)
    # write не зависит от read; burst 3; повторы с тем же request_id тоже считаются
    check("write 1", spin(SECRET_ID).status_code, 200)
    check("write 2 (повтор того же request_id)", spin(SECRET_ID).json()["replayed"], True)
    check("write 3 (повтор)", spin(SECRET_ID).status_code, 200)
    r = spin(SECRET_ID)
    check("write превышен повтором", (r.status_code, r.json(), r.headers["Retry-After"]),
          (429, {"error": "too_many_requests"}, "1"))
    check("после отказа новая ставка тоже 429", spin(SECRET_ID, "rate-req-0002").status_code, 429)
    check("в базе один раунд", sqlite3.connect(path).execute("SELECT COUNT(*) FROM roulette_rounds").fetchone()[0], 1)
    # другой игрок не затронут
    check("другой игрок read", client.get("/api/me", headers=auth(555)).status_code, 200)
    check("другой игрок write", spin(555, "rate-req-0003").status_code, 200)
    # восстановление со временем
    clock.t += 1.1
    check("после паузы read снова работает", client.get("/api/me", headers=auth(SECRET_ID)).status_code, 200)
    check("после паузы write снова работает", spin(SECRET_ID, "rate-req-0004").status_code, 200)
    # ошибка формы тела тоже считается (бакет на пару игрок-группа, а не на успешные запросы)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "1", "WRITE_RATE_PER_SEC": "1"}), clock=clock)
    c2 = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim2))
    check("кривое тело 400", c2.post("/api/roulette/spin", headers=auth(9), json={"x": 1}).status_code, 400)
    check("следующий запрос 429", c2.post("/api/roulette/spin", headers=auth(9), json={"x": 1}).status_code, 429)
    # без ограничителя (как в остальных тестах) лимитов нет
    free = TestClient(create_app(TOKEN, [], db_path=path))
    check("без rate_limiter: 60 запросов", {free.get("/api/me", headers=auth(SECRET_ID)).status_code for _ in range(60)}, {200})

    # ================= подключение при запуске (create_app_from_env) =================
    env = {"BOT_TOKEN": TOKEN, "DB_PATH": os.path.join(tmp, "env.db"), "ALLOWED_ORIGINS": "https://example.test",
           "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "bad-value"}
    with mock.patch.dict(os.environ, env):
        cap.lines.clear()
        with mock.patch.object(api, "load_dotenv", lambda *a, **k: None):
            app = api.create_app_from_env()
        warns = [l for l in cap.lines if "Неверные значения" in l and "WRITE_RATE_BURST" in l]
        check("предупреждение при старте", len(warns), 1)
        c3 = TestClient(app)
        codes = [c3.get("/api/me", headers=auth(77)).status_code for _ in range(3)]
        check("боевая сборка ограничивает чтение", codes, [200, 200, 429])

    # ================= в логах нет id, имён, балансов и сумм ставок =================
    for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), "555"):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:80]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
