"""Счётчики игрока по играм за всё время (player_game_stats): пишет только _round_finished в транзакции выплаты; раунд, победа (выплата больше ставки),
поставлено, выплачено; возврат ставки считается раундом без победы; /api/me отдаёт stats; выгрузка /mydata и удаление с профилем."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import db
import roulette
from api import create_app
from features.data_rights_db import delete_player_data, get_player_export
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://pazrabi.github.io"
A, B = 616161601, 616161602
NOW = 1_760_000_000


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["DB_PATH"] = path
db.init_db(path)
for uid in (A, B):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 10000000, 100, ?, ?, 0, 0, 0, 0)",
        (uid, NOW + 864000, NOW - 86400))


def bets(*items):
    return roulette.validate_bets([{"type": t, "value": v, "amount": a} for t, v, a in items])


def stats(uid):
    return {r[0]: r[1:] for r in sql(path, "SELECT game, rounds, wins, staked, paid FROM player_game_stats WHERE telegram_id = ? ORDER BY game", (uid,))}


n = [0]


def spin(uid, items, number, now):
    n[0] += 1
    return db.spin_roulette(uid, "request-gs-%08d" % n[0], bets(*items), now=now, db_path=path, rng=lambda k: number)


check("до игры счётчиков нет", stats(A), {})
spin(A, [("number", 17, 100)], 17, NOW)                                  # победа: выплата 3600
check("победа", stats(A), {"roulette": (1, 1, 100, 3600)})
spin(A, [("red", None, 500)], 17, NOW + 1)                               # 17 чёрное: проигрыш
check("проигрыш: раунд без победы", stats(A), {"roulette": (2, 1, 600, 3600)})
spin(A, [("red", None, 100), ("black", None, 100)], 17, NOW + 2)         # выплата 200 при ставках 200: возврат
check("возврат ставки не победа", stats(A), {"roulette": (3, 1, 800, 3800)})
check("игрок B не затронут", stats(B), {})
first = sql(path, "SELECT first_at FROM player_game_stats WHERE telegram_id = ?", (A,))[0][0]
check("начало учёта = время первого раунда", first, NOW)

# /api/me и выгрузка
client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))
r = client.get("/api/me", headers={"Authorization": "tma " + make_init_data(TOKEN, user_id=A, auth_date=int(time.time()), first_name="Я")})
body = r.json()
check("/api/me: stats", (r.status_code, body["stats"]["since"], body["stats"]["games"]),
      (200, NOW // 86400 * 86400, [{"game": "roulette", "rounds": 3, "wins": 1, "staked": 800, "paid": 3800}]))
exp = get_player_export(A, db_path=path)
check("/mydata: game_stats", [(g["game"], g["rounds"], g["wins"], g["staked"], g["paid"]) for g in exp["game_stats"]], [("roulette", 3, 1, 800, 3800)])
# потолок чисел
sql(path, "UPDATE player_game_stats SET staked = ?, rounds = ? WHERE telegram_id = ?", (roulette.MAX_SAFE_INT - 5, roulette.MAX_SAFE_INT, A))
spin(A, [("number", 17, 100)], 17, NOW + 10)
check("числа не выше MAX_SAFE_INT", stats(A)["roulette"][0] <= roulette.MAX_SAFE_INT and stats(A)["roulette"][2] <= roulette.MAX_SAFE_INT, True)
# удаление с профилем
counts = delete_player_data(A, db_path=path)
check("удалено с профилем", (counts["player_game_stats"], stats(A)), (1, {}))
os.unlink(path)
print("test_game_stats OK")
