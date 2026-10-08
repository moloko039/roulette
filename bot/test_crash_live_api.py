"""Тестирование маршрутов живого краша, лимитов, данных игрока и очистки."""
import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import db
import crash
import crash_live
import economy_config
from api import create_app
from features import purge_db
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
A, B, C = 1001, 1002, 1003

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"

def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"

def rid(u, seq):
    return f"req-{u}-{seq}"

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "db.sqlite3")
os.environ["DB_PATH"] = path

try:
    db.init_db()
    
    # Даем баланс и чаты
    conn = sqlite3.connect(path)
    now = int(time.time())
    for uid in (A, B, C):
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, accrual_acc, created_at, income_level, storage_level) VALUES (?, 1000, 100, ?, 0, ?, 1, 1)", (uid, now, now))
        conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, 'Name', ?, ?)", (f"chat_{uid}", uid, now, now))
    conn.commit()
    conn.close()

    import ratelimit
    rate_config = ratelimit.load_config(os.environ)
    app = create_app(TOKEN, ["*"], rate_limiter=ratelimit.RateLimiter(rate_config))
    ca = TestClient(app)

    def auth(uid, chat_instance=None):
        params = {"user_id": uid, "auth_date": int(time.time()), "first_name": "Игрок" if uid == A else "Name"}
        if chat_instance:
            params["chat_instance"] = chat_instance
            params["chat_type"] = "group"
        return {"Authorization": "tma " + make_init_data(TOKEN, **params)}

    # Без подписи 401
    check("GET 401", ca.get("/api/crash/live").status_code, 401)
    check("POST bet 401", ca.post("/api/crash/live/bet", json={"request_id": "r1", "bet": 10}).status_code, 401)
    check("POST cashout 401", ca.post("/api/crash/live/cashout", json={"request_id": "r1"}).status_code, 401)

    T = 1000000000000
    with mock.patch("crash_live.now_ms", return_value=T), mock.patch("crash_live.crash_from_seed", return_value=10000):
        # GET даёт ключи состояния и фазу betting
        r = ca.get("/api/crash/live", headers=auth(A))
        check("GET status", r.status_code, 200)
        d = r.json()
        assert "server_ms" in d and "history" in d and "round" in d and "bets" in d and "me" in d
        assert d["round"]["phase"] == "betting"
        assert "telegram_id" not in str(d) and "chat_instance" not in str(d) and "room_key" not in str(d)
        assert "seed" not in d["round"] and "crash_x100" not in d["round"]

        # Полный цикл
        bal_before = db.get_player(A)["balance"]
        
        # Неверные тела 400
        check("bet: лишние поля", ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0001", "bet": 10, "extra": 1}).status_code, 400)
        check("bet: нет req_id", ca.post("/api/crash/live/bet", headers=auth(A), json={"bet": 10}).status_code, 400)
        check("bet: строкой", ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0001", "bet": "10"}).status_code, 400)
        check("bet: bool", ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0001", "bet": True}).status_code, 400)
        check("bet: вне диапазона", ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0001", "bet": -10}).status_code, 400)
        check("target: вне диапазона", ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0001", "bet": 10, "target_x100": 1}).status_code, 400)
        
        # Слишком большое тело
        big_body = {"request_id": "req-0001", "bet": 10, "t": "x" * 66000}
        check("payload_too_large", ca.post("/api/crash/live/bet", headers=auth(A), json=big_body).status_code, 413)

        # Вывод без ставки 409 no_bet
        r = ca.post("/api/crash/live/cashout", headers=auth(A), json={"request_id": "req-0002"})
        check("no_bet code", r.status_code, 409)
        check("no_bet msg", r.json()["detail"], "no_bet")

        # Ставка
        r = ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0003", "bet": 10})
        check("bet ok", r.status_code, 200)
        check("bal уменьшился", r.json()["balance"], bal_before - 10)

        # Вторая ставка
        r = ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0004", "bet": 10})
        check("already_bet", r.status_code, 409)
        check("already_bet msg", r.json()["detail"], "already_bet")

        # Мало фишек
        r = ca.post("/api/crash/live/bet", headers=auth(B), json={"request_id": "req-0005", "bet": 2000})
        check("insufficient_funds", r.status_code, 409)
        check("insufficient msg", r.json()["detail"], "insufficient_funds")

        # Лента: разные chat_instance в общем раунде
        ca.post("/api/crash/live/bet", headers=auth(C, f"chat_{C}"), json={"request_id": "req-0006", "bet": 5})
        ca.post("/api/crash/live/bet", headers=auth(B, f"chat_{B}"), json={"request_id": "req-0007", "bet": 20})
        
        st_C = ca.get("/api/crash/live", headers=auth(C, f"chat_{C}")).json()
        st_B = ca.get("/api/crash/live", headers=auth(B, f"chat_{B}")).json()
        st_A = ca.get("/api/crash/live", headers=auth(A)).json()
        
        check("A видит свою", len(st_A["bets"]), 1)
        check("C видит свою", len(st_C["bets"]), 1)
        check("B видит свою", len(st_B["bets"]), 1)
        
        check("Имена в личной", st_A["bets"][0]["name"], "Игрок")
        check("Имена в группе C", st_C["bets"][0]["name"], "Name")
        
        check("Одинаковый раунд ID", st_C["round"]["id"], st_B["round"]["id"])
        check("Одинаковый хэш", st_C["round"]["seed_hash"], st_B["round"]["seed_hash"])

    # Полёт
    T2 = T + economy_config.CRASH_LIVE_BET_MS + 2000
    with mock.patch("crash_live.now_ms", return_value=T2):
        # Ставка вне приёма
        r = ca.post("/api/crash/live/bet", headers=auth(A), json={"request_id": "req-0008", "bet": 10})
        check("betting_closed", r.status_code, 409)
        check("betting_closed msg", r.json()["detail"], "betting_closed")
        
        with mock.patch("crash_live.now_ms", return_value=T + economy_config.CRASH_LIVE_BET_MS + 1):
            r = ca.post("/api/crash/live/cashout", headers=auth(A), json={"request_id": "req-0009"})
            check("too_early", r.status_code, 409)
            check("too_early msg", r.json()["detail"], "too_early")

        # Вывод в полёте
        r = ca.post("/api/crash/live/cashout", headers=auth(A), json={"request_id": "req-0010"})
        check("cashout ok", r.status_code, 200)
        assert r.json()["payout"] > 0
        assert r.json()["balance"] == bal_before - 10 + r.json()["payout"]

    # Лимиты
    with mock.patch("crash_live.now_ms", return_value=T2):
        for _ in range(12):
            ca.get("/api/crash/live", headers=auth(A))
        check("ratelimit live", ca.get("/api/crash/live", headers=auth(A)).status_code, 429)

    # Очистка и права
    exp = db.get_player_export(A)
    assert len(exp["crash_live"]) == 1

    counts = db.delete_player_data(A)
    assert counts["crash_bets"] == 1
    
    conn = sqlite3.connect(path)
    conn.execute("UPDATE crash_rounds SET bet_open_ms = ?, flight_start_ms = ?, crash_ms = ?, status = 'closed'", (0, 0, 0))
    conn.execute("UPDATE crash_bets SET created_at_ms = 0")
    conn.commit()
    conn.close()

    deleted = db.purge_old_data(rounds_days=30, member_days=30, now=100 * 86400, db_path=path)
    check("purge bets", deleted["crash_bets"], 2)
    check("purge rounds", deleted["crash_rounds"], 1)

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if os.path.exists(path):
        os.remove(path)
    os.rmdir(tmp)
