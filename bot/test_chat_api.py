import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import os
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

from api import create_app
from db import init_db
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
HOUR = 3600


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def auth(user_id, chat_type="group", chat_instance="chat-A", first_name="Игрок", username=None):
    data = make_init_data(TOKEN, user_id=user_id, auth_date=int(time.time()), chat_type=chat_type,
                          chat_instance=chat_instance, first_name=first_name, username=username)
    return {"Authorization": "tma " + data}


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def members(path, chat="chat-A"):
    return sql(path, "SELECT telegram_id, first_name, first_seen, last_seen FROM chat_members "
                     "WHERE chat_instance = ? ORDER BY telegram_id", (chat,))


class At:
    """Подмена серверного времени: реальное время плюс смещение в секундах."""

    def __init__(self, offset):
        real = time.time
        self.patch = mock.patch("time.time", lambda: real() + offset)

    def __enter__(self):
        self.patch.start()

    def __exit__(self, *a):
        self.patch.stop()


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    client = TestClient(create_app(TOKEN, [], db_path=path))
    me = lambda uid, **kw: client.get("/api/me", headers=auth(uid, **kw))  # noqa: E731
    top = lambda uid, **kw: client.get("/api/chat/top", headers=auth(uid, **kw))  # noqa: E731

    # ---------- /api/me записывает участника только из групп ----------
    r = me(1, chat_type="group", first_name="Аня")
    check("/api/me без изменений", set(r.json()), {"balance", "rate", "seconds_to_next", "level", "income_level", "storage_level", "farm", "active_game", "incoming_unseen", "transfer_limits", "cosmetics"})
    me(2, chat_type="supergroup", first_name="Боря")
    me(3, chat_type="private")
    me(4, chat_type="sender")
    me(5, chat_type="channel")
    me(6, chat_type=None, chat_instance=None)
    me(7, chat_type="group", chat_instance=None)
    me(8, chat_type="group", chat_instance="bad instance")
    check("записаны только группы", sql(path, "SELECT telegram_id FROM chat_members ORDER BY telegram_id"), [(1,), (2,)])
    check("имя", sql(path, "SELECT first_name FROM chat_members WHERE telegram_id = 1")[0][0], "Аня")

    # ---------- не чаще раза в 60 секунд ----------
    t0 = members(path)[0][3]
    me(1, chat_type="group", first_name="Новое")
    check("в течение 60 секунд не пишем", members(path)[0][1:], ("Аня", t0, t0))
    with At(30):
        me(1, chat_type="group", first_name="Новое")
    check("через 30 секунд не пишем", members(path)[0][1], "Аня")
    with At(61):
        me(1, chat_type="group", first_name="Новое")
    row = members(path)[0]
    check("через 61 секунду обновили имя", row[1], "Новое")
    assert row[3] >= t0 + 60, "last_seen не обновился"
    check("first_seen не менялся", row[2], t0)

    # ошибка записи не ломает /api/me
    with mock.patch("api.touch_chat_member", side_effect=RuntimeError("boom")):
        r = me(1, chat_type="group")
    check("ошибка записи", (r.status_code, set(r.json())), (200, {"balance", "rate", "seconds_to_next", "level", "income_level", "storage_level", "farm", "active_game", "incoming_unseen", "transfer_limits", "cosmetics"}))

    # ---------- /api/chat/top: нет беседы ----------
    for kw in [{"chat_type": "private"}, {"chat_type": "sender"}, {"chat_type": "channel"},
               {"chat_type": None, "chat_instance": None}, {"chat_type": "group", "chat_instance": None},
               {"chat_type": "group", "chat_instance": "bad instance"}]:
        r = top(50, **kw)
        check("scope none " + str(kw), (r.status_code, r.json()), (200, {"scope": "none"}))
    check("none ничего не записал", sql(path, "SELECT COUNT(*) FROM chat_members WHERE telegram_id = 50")[0][0], 0)

    # ---------- рейтинг из 3 игроков ----------
    for uid, bal in [(101, 1500), (102, 3000), (103, 2000)]:
        top(uid, chat_instance="chat-T", first_name="P%d" % uid)
        sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (bal, uid))
    r = top(103, chat_instance="chat-T", first_name="P103")
    body = r.json()
    check("scope", body["scope"], "chat")
    check("порядок", [(e["rank"], e["name"], e["balance"], e["is_me"]) for e in body["top"]],
          [(1, "P102", 3000, False), (2, "P103", 2000, True), (3, "P101", 1500, False)])
    check("me", body["me"], {"rank": 2, "balance": 2000, "total": 3, "staked": 0, "level": 1})

    # другой chat_instance не видит этих игроков
    r = top(999, chat_instance="chat-OTHER", first_name="Чужой")
    check("другая беседа", (len(r.json()["top"]), r.json()["me"]), (1, {"rank": 1, "balance": 1000, "total": 1, "staked": 0, "level": 1}))
    r = top(101, chat_instance="chat-T")
    check("первая беседа без чужого", [e["name"] for e in r.json()["top"]], ["P102", "P103", "P101"])

    # ---------- 12 игроков: top из 10, ранг вне десятки ----------
    for i in range(12):
        uid = 200 + i
        top(uid, chat_instance="chat-12", first_name="U%d" % i)
        sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (5000 - i * 100, uid))
    body = top(210, chat_instance="chat-12", first_name="U10").json()  # 11-й по балансу
    check("в top 10 записей", len(body["top"]), 10)
    check("me вне десятки", body["me"], {"rank": 11, "balance": 4000, "total": 12, "staked": 0, "level": 1})
    check("is_me нет среди первых 10", any(e["is_me"] for e in body["top"]), False)
    check("последний в top", body["top"][-1]["rank"], 10)

    # ---------- актуальный баланс с начислением, в базе не меняется ----------
    # часы сервера фиксируются на середине минуты: 3 часа = ровно 180 тиков, граница минуты посреди теста невозможна
    now = int(time.time()) // 60 * 60 + 30
    for uid, bal, hours in [(301, 1000, 3), (302, 1250, 0)]:
        top(uid, chat_instance="chat-acc", first_name="A%d" % uid)
        sql(path, "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?", (bal, now // 60 * 60 - hours * HOUR, uid))
    before = sql(path, "SELECT telegram_id, balance, last_accrual FROM players WHERE telegram_id IN (301, 302) ORDER BY 1")
    with mock.patch("time.time", return_value=float(now)):
        body = top(302, chat_instance="chat-acc", first_name="A302").json()
    check("в top баланс с начислением", [(e["name"], e["balance"]) for e in body["top"]], [("A301", 1300), ("A302", 1250)])
    check("players не изменилась", sql(path, "SELECT telegram_id, balance, last_accrual FROM players WHERE telegram_id IN (301, 302) ORDER BY 1"), before)

    # подменённое время: через 10 часов у обоих накапало
    with At(10 * HOUR):
        body = top(302, chat_instance="chat-acc", first_name="A302").json()
    # A301: 3 ч в базе + 10 ч = 13 ч → +1300; A302: 10 ч → +1000
    check("с подменённым временем", [(e["name"], e["balance"]) for e in body["top"]], [("A301", 2300), ("A302", 2250)])

    # ---------- равные балансы: first_seen, затем telegram_id ----------
    for uid in (411, 410, 412, 413):
        top(uid, chat_instance="chat-tie", first_name="T%d" % uid)
        sql(path, "UPDATE players SET balance = 2000, last_accrual = ? WHERE telegram_id = ?", (now, uid))
    sql(path, "UPDATE chat_members SET first_seen = 100 WHERE telegram_id IN (413)")
    sql(path, "UPDATE chat_members SET first_seen = 200 WHERE telegram_id IN (411, 412)")
    sql(path, "UPDATE chat_members SET first_seen = 300 WHERE telegram_id = 410")
    body = top(410, chat_instance="chat-tie", first_name="T410").json()
    check("порядок при равных балансах", [e["name"] for e in body["top"]], ["T413", "T411", "T412", "T410"])

    # ---------- очистка имён ----------
    cases = [("\x00А\x07Б\n\t", "АБ"), ("x" * 40, "x" * 32), ("   Вася   ", "Вася"), ("", "Игрок"),
             ("  \t\n ", "Игрок"), ("\x1b\x00", "Игрок"), ("A‮B", "AB"), ("я" * 31 + " ы", "я" * 31)]
    for i, (raw, want) in enumerate(cases):
        uid = 500 + i
        body = top(uid, chat_instance="chat-names", first_name=raw).json()
        got = [e["name"] for e in body["top"] if e["is_me"]][0]
        check("имя %r" % raw, got, want)

    # ---------- ответы без telegram_id, chat_instance и username ----------
    for uid in (987654321, 987654322):
        top(uid, chat_instance="chat-secret-77", first_name="Имя", username="secret_user_%d" % uid)
    r = top(987654321, chat_instance="chat-secret-77", first_name="Имя", username="secret_user_987654321")
    raw = r.text
    for secret in ["987654321", "987654322", "chat-secret-77", "secret_user", "username", "telegram_id", "chat_instance"]:
        assert secret not in raw, "в ответе есть " + secret
    body = r.json()
    check("поля записи", sorted(body["top"][0]), ["balance", "cosmetics", "is_me", "level", "member_ref", "name", "rank", "staked"])
    check("поля me", sorted(body["me"]), ["balance", "level", "rank", "staked", "total"])
    check("поля ответа", sorted(body), ["chat_staked", "me", "scope", "top"])

    # ---------- запрос без подписи ----------
    for h in [{}, {"Authorization": ""}, {"Authorization": "tma garbage"}]:
        r = client.get("/api/chat/top", headers=h)
        check("401", (r.status_code, r.json()), (401, {"detail": "Unauthorized"}))

    # ---------- ограничение 1000 участников ----------
    for i in range(7):
        uid = 700 + i
        top(uid, chat_instance="chat-cap", first_name="C%d" % i)
        sql(path, "UPDATE chat_members SET last_seen = ? WHERE telegram_id = ?", (now - 1000 + i, uid))
    with mock.patch("db.MAX_CHAT_MEMBERS", 5):
        body = top(706, chat_instance="chat-cap", first_name="C6").json()
    check("учтено не больше лимита", body["me"]["total"], 5)
    check("вне лимита самые давние", sorted(e["name"] for e in body["top"]), ["C2", "C3", "C4", "C5", "C6"])
finally:
    os.remove(path)

# ---------- миграция: база со старой схемой ----------
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("CREATE TABLE roulette_rounds (telegram_id INTEGER NOT NULL, request_id TEXT NOT NULL, "
                 "number INTEGER NOT NULL, stake_total INTEGER NOT NULL, payout_total INTEGER NOT NULL, "
                 "bets_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (telegram_id, request_id))")
    conn.execute("INSERT INTO players VALUES (1, 777, 100, %d, 1000), (2, 1500, 100, %d, 2000)" % (int(time.time()) + 10 * 86400, int(time.time()) + 10 * 86400))   # метка в будущем: миграция начисления ничего не платит
    conn.execute("INSERT INTO roulette_rounds VALUES (1, 'old-request-1', 17, 10, 360, '[]', 1500)")
    conn.commit()
    conn.close()

    init_db(path)
    init_db(path)  # повторный вызов безопасен

    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("таблицы", {"players", "roulette_rounds", "chat_members"} <= tables, True)
    check("игроки на месте", conn.execute("SELECT telegram_id, balance FROM players ORDER BY 1").fetchall(), [(1, 777), (2, 1500)])
    check("раунд на месте", conn.execute("SELECT request_id, number, payout_total FROM roulette_rounds").fetchall(),
          [("old-request-1", 17, 360)])
    check("колонки chat_members", [r[1] for r in conn.execute("PRAGMA table_info(chat_members)")],
          ["chat_instance", "telegram_id", "first_name", "first_seen", "last_seen"])
    conn.close()
finally:
    os.remove(path)

print("Все проверки прошли")
