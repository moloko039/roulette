"""Общий забег свинок: расписание, ставки, выплаты при закрытии приёма, лента комнаты беседы, один раз на раунд, данные игрока и очистка."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import db
import race
import race_live
from api import create_app
from core.db_conn import _connect
from core.schema import init_db
from features import purge_db, race_live_db
from features.data_rights_db import delete_player_data, get_player_export
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://pazrabi.github.io"
A, B, C = 515151501, 515151502, 515151503
P = race_live.PERIOD_MS
T0 = 50_000 * P          # начало какого-то раунда, мс


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["DB_PATH"] = path
init_db(path)

# расписание
check("номер раунда", race_live.round_id(T0 + 5), 50_000)
check("фазы", [race_live.phase_of(50_000, T0 + off) for off in (0, race_live.BET_MS - 1, race_live.BET_MS, race_live.BET_MS + race_live.RACE_MS - 1, race_live.BET_MS + race_live.RACE_MS, P - 1)],
      ["bets", "bets", "race", "race", "result", "result"])

conn = sqlite3.connect(path)
for uid in (A, B, C):
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, accrual_acc, created_at, income_level, storage_level) VALUES (?, 10000, 100, ?, 0, ?, 0, 0)", (uid, T0 // 1000, T0 // 1000))
    conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('room1', ?, ?, 1, 2)", (uid, "Имя%d" % uid))
conn.commit()
conn.close()

room = race_live.room_key("room1")


def bal(uid):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT balance FROM players WHERE telegram_id = ?", (uid,)).fetchone()[0]
    finally:
        c.close()


def expect(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


# ставки: приём
r = race_live_db.place_bet(A, room, "reqa-0000-1", 2, 1000, T0 + 1000, path)
check("ставка принята", (r["lane"], r["bet"], r["replayed"], r["balance"]), (2, 1000, False, 9000))
check("повтор того же запроса", race_live_db.place_bet(A, room, "reqa-0000-1", 2, 1000, T0 + 1500, path)["replayed"], True)
check("тот же запрос с другой дорожкой", expect(race.RequestConflict, lambda: race_live_db.place_bet(A, room, "reqa-0000-1", 1, 1000, T0 + 1600, path)), True)
check("вторая ставка в раунде", expect(race_live.AlreadyBet, lambda: race_live_db.place_bet(A, room, "reqa-0000-2", 1, 500, T0 + 1700, path)), True)
race_live_db.place_bet(B, room, "reqb-0000-1", 0, 2000, T0 + 2000, path)
race_live_db.place_bet(C, race_live.PUBLIC_ROOM_KEY, "reqc-0000-1", 2, 500, T0 + 3000, path)
check("ставка после закрытия приёма", expect(race_live.BettingClosed, lambda: race_live_db.place_bet(C, room, "reqc-0000-2", 1, 500, T0 + race_live.BET_MS + 1, path)), True)
check("ставка не по правилам", [expect((ValueError, race_live.RaceLiveError, Exception), lambda: race_live_db.place_bet(C, room, "reqc-0000-3", ln, bt, T0 + 4000, path)) for ln, bt in ((4, 10), (-1, 10), (0, 0), (0, 2_000_000))], [True] * 4)

# лента: своя беседа видит имена, чужая комната не видит ставок
s = race_live_db.live_state(A, room, T0 + 5000, "room1", path)
check("приём: фаза, ставки беседы", (s["round"]["phase"], [(b["name"], b["lane"], b["bet"], b["me"]) for b in s["bets"]], s["counts"]),
      ("bets", [("Имя%d" % A, 2, 1000, True), ("Имя%d" % B, 0, 2000, False)], [1, 0, 1, 0]))
check("итога до закрытия нет", (s["result"], s["me"]["payout"]), (None, None))
s = race_live_db.live_state(C, race_live.PUBLIC_ROOM_KEY, T0 + 5000, None, path)
check("общая комната анонимна и не видит беседу", [(b["name"], b["bet"]) for b in s["bets"]], [("Игрок", 500)])

# закрытие: первый же запрос подводит итог и платит; победитель известен только после закрытия
orig = race_live_db.secrets.randbelow
race_live_db.secrets.randbelow = lambda n: 2
try:
    s = race_live_db.live_state(A, room, T0 + race_live.BET_MS + 100, "room1", path)
finally:
    race_live_db.secrets.randbelow = orig
check("победитель и сценарий", (s["round"]["phase"], s["result"]["winner"], len(s["result"]["script"]), s["result"]["script"][-1][2]), ("race", 2, 40, 1000))
win = race.payout(1000, 4, True)
check("выплаты", (bal(A), bal(B), bal(C)), (9000 + win, 8000, 9500 + race.payout(500, 4, True)))
check("моя ставка с выплатой", s["me"], {"lane": 2, "bet": 1000, "payout": win})
check("повторный запрос ничего не платит", (race_live_db.live_state(B, room, T0 + race_live.BET_MS + 200, "room1", path)["result"]["winner"], bal(A)), (2, 9000 + win))
c = sqlite3.connect(path)
check("опыт и учёт ставок", (c.execute("SELECT total_staked FROM players WHERE telegram_id = ?", (A,)).fetchone()[0], c.execute("SELECT COUNT(*) FROM race_live_rounds").fetchone()[0]), (1000, 1))
c.close()

# следующий раунд: снова можно, прошлый показан в last
r2 = race_live_db.place_bet(A, room, "reqa-0000-3", 1, 100, T0 + P + 10, path)
check("новый раунд", r2["round_id"], 50_001)
s = race_live_db.live_state(A, room, T0 + P + 20, "room1", path)
check("прошлый раунд в last", (s["last"]["round_id"], s["last"]["winner"], s["last"]["payout"]), (50_000, 2, win))

# раунд без ставок тоже получает итог (зрители видят забег), раунд со ставкой без запросов подводится позже
s = race_live_db.live_state(B, room, T0 + 3 * P + race_live.BET_MS + 50, "room1", path)
check("пустой раунд имеет итог", s["result"] is not None, True)
c = sqlite3.connect(path)
check("ставка раунда 50001 выплачена позже", c.execute("SELECT payout IS NOT NULL FROM race_live_bets WHERE round_id = 50001").fetchone()[0], 1)
c.close()

# маршруты
client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))


def auth(uid, chat=None):
    p = {"user_id": uid, "auth_date": int(time.time()), "first_name": "Имя%d" % uid}
    if chat:
        p.update(chat_instance=chat, chat_type="group")
    return {"Authorization": "tma " + make_init_data(TOKEN, **p)}


r = client.get("/api/race/live", headers=auth(A, "room1"))
check("GET /api/race/live", (r.status_code, sorted(r.json())), (200, ["balance", "bets", "counts", "lanes", "last", "me", "result", "round", "server_ms"]))
check("тело POST: лишнее поле", client.post("/api/race/live/bet", headers=auth(A), json={"request_id": "x-req-0001", "lane": 0, "bet": 5, "z": 1}).status_code, 400)
check("тело POST: дорожка 4", client.post("/api/race/live/bet", headers=auth(A), json={"request_id": "x-req-0002", "lane": 4, "bet": 5}).status_code, 400)
check("без подписи", client.get("/api/race/live").status_code, 401)

# данные игрока и очистка
exp = get_player_export(A, db_path=path)
check("выгрузка: ставки общего забега", [(b["round_id"], b["lane"], b["bet"]) for b in exp["race_live"]][-1:], [(50_000, 2, 1000)])
purge_db.purge_old_data(now=int(time.time()) + 40 * 86400, db_path=path)
c = sqlite3.connect(path)
check("очистка: старые ставки и итоги", (c.execute("SELECT COUNT(*) FROM race_live_bets").fetchone()[0], c.execute("SELECT COUNT(*) FROM race_live_rounds").fetchone()[0]), (0, 0))
c.close()
delete_player_data(B, db_path=path)
c = sqlite3.connect(path)
check("ставки удалены с профилем", c.execute("SELECT COUNT(*) FROM race_live_bets WHERE telegram_id = ?", (B,)).fetchone()[0], 0)
c.close()
os.unlink(path)
print("test_race_live OK")
