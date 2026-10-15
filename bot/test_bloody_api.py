"""Bloody slot: HTTP-маршруты /api/bloody/state, /start, /spin: форма ответов, ошибки, повтор по request_id, подпись Telegram."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import bloody_config as cfg
import db
from api import create_app
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://pazrabi.github.io"
U = 919191901
NOW = int(time.time())


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["DB_PATH"] = path
db.init_db(path)
conn = sqlite3.connect(path)
conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 5000000, 100, ?, ?, 0, 0, 0, 0)",
             (U, NOW + 864000, NOW - 86400))
conn.commit()
conn.close()
client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))


def auth(uid=U):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Я")}


check("без подписи", (client.get("/api/bloody/state").status_code, client.post("/api/bloody/start", json={}).status_code), (401, 401))
r = client.get("/api/bloody/state", headers=auth())
st = r.json()
check("состояние без раундов", (r.status_code, st["status"], st["spin"]), (200, "none", None))
check("настройки для интерфейса", (st["settings"]["bets"], st["settings"]["buys"], st["settings"]["max_win_bets"]),
      (list(cfg.BET_LADDER), {k: v / 100 for k, v in cfg.BUY_COST_UNITS.items()}, 16161))
check("ключи ответа", sorted(st), ["balance", "bet", "buy", "cost", "level", "max_win_bets", "payout", "replayed", "round", "settings", "spin", "status", "xp"])
rid = "web-bl-0000001"
r = client.post("/api/bloody/start", headers=auth(), json={"request_id": rid, "bet": 100, "buy": None})
d = r.json()
check("старт: 200, ставка списана", (r.status_code, d["cost"], d["status"] in ("active", "finished", "capped")), (200, 100, True))
check("в ответе лог спина", (sorted(d["spin"])[:3], d["spin"]["mode"]), (["bar", "candle_left", "capped"], "base"))
check("повтор того же запроса", (client.post("/api/bloody/start", headers=auth(), json={"request_id": rid, "bet": 100, "buy": None}).json()["replayed"]), True)
check("тот же id, другая ставка", client.post("/api/bloody/start", headers=auth(), json={"request_id": rid, "bet": 200, "buy": None}).status_code, 409)
if d["status"] == "active":
    r2 = client.post("/api/bloody/start", headers=auth(), json={"request_id": "web-bl-0000002", "bet": 100, "buy": None})
    check("второй старт при активном раунде", (r2.status_code, r2.json()["detail"]), (409, "active_round"))
    r3 = client.post("/api/bloody/spin", headers=auth(), json={"request_id": "web-bl-0000003"})
    check("бонусный спин", (r3.status_code, r3.json()["spin"]["index"]), (200, 2))
    check("состояние = последний спин", client.get("/api/bloody/state", headers=auth()).json()["spin"]["index"], 2)
else:
    r3 = client.post("/api/bloody/spin", headers=auth(), json={"request_id": "web-bl-0000003"})
    check("спин без активного раунда", (r3.status_code, r3.json()["detail"]), (409, "no_active_round"))
# покупка и ошибки
bad = [{"request_id": "web-bl-0000010", "bet": 7, "buy": None}, {"request_id": "web-bl-0000011", "bet": 100, "buy": "zzz"}, {"request_id": "web-bl-0000012", "bet": "100", "buy": None},
       {"request_id": "x", "bet": 100, "buy": None}, {"request_id": "web-bl-0000013", "bet": 100, "buy": None, "extra": 1}]
codes = [client.post("/api/bloody/start", headers=auth(), json=b).status_code for b in bad]
check("ошибки запроса: не из лестницы 409, остальное 400", codes, [409, 400, 400, 400, 400])
db_conn = sqlite3.connect(path)
db_conn.execute("UPDATE bloody_games SET status = 'finished' WHERE status = 'active'")
db_conn.execute("UPDATE players SET balance = 10 WHERE telegram_id = ?", (U,))
db_conn.commit()
db_conn.close()
r = client.post("/api/bloody/start", headers=auth(), json={"request_id": "web-bl-0000020", "bet": 100, "buy": "gibbet"})
check("не хватает фишек на покупку", (r.status_code, r.json()["detail"]), (409, "insufficient_funds"))
os.unlink(path)
print("test_bloody_api OK")
