import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import db
import sqlite3
def _connect(p):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c
from api import create_app
from tg_testutil import make_init_data
from economy_config import (
    CHAT_BONUS_PER_PLAYER_PCT,
    CHAT_BONUS_MAX_PCT,
    CHAT_BOOST_PCT,
    CHAT_BOOST_MAX_PCT,
    CHAT_BOOST_GEMS,
    CHAT_BOOST_HOURS,
    STREAK_UTC_OFFSET_HOURS,
)
from features.chat_bonus import get_chat_bonus, buy_chat_boost, NoChat, NotAttributed

NOW = int(time.time())
TOKEN = "123456:TEST-TOKEN-not-real"
_tmp = tempfile.mkdtemp()
_n = [0]
_rid = [0]

def new_db():
    _n[0] += 1
    path = os.path.join(_tmp, "chatbonus%d.db" % _n[0])
    db.init_db(path)
    return path

def rid():
    _rid[0] += 1
    return "req-bonus-%08d" % _rid[0]

def auth(user_id, chat_type="group", chat_instance="chat-A", first_name="Игрок"):
    data = make_init_data(TOKEN, user_id=user_id, auth_date=int(time.time()), chat_type=chat_type, chat_instance=chat_instance, first_name=first_name)
    return {"Authorization": "tma " + data}

def player(path, uid, balance=10**9, gems=1000):
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level, last_played_at) "
                     "VALUES (?, ?, 100, ?, ?, 0, 0, 0, 0, 0)", (uid, balance, NOW - 3600, NOW))
        conn.execute("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, ?)", (uid, gems))
        conn.commit()
    finally:
        conn.close()

def add_chat(path, uid, chat_instance, seen, played_at=0):
    conn = _connect(path)
    try:
        conn.execute("INSERT OR REPLACE INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, 'Name', 0, ?)",
                     (chat_instance, uid, seen))
        if played_at:
            conn.execute("UPDATE players SET last_played_at = ? WHERE telegram_id = ?", (played_at, uid))
        conn.commit()
    finally:
        conn.close()

def test_attribution_and_active():
    path = new_db()
    
    # 1. Атрибуция по последнему визиту
    player(path, 1)
    add_chat(path, 1, "chat-B", NOW - 100)
    add_chat(path, 1, "chat-A", NOW - 10)
    
    conn = _connect(path)
    bonus = get_chat_bonus(conn, 1, NOW)
    assert bonus["in_chat"] is True
    
    
    # 2. Активен сегодня (московское время)
    # Сегодня
    add_chat(path, 1, "chat-A", NOW, played_at=NOW)
    bonus = get_chat_bonus(conn, 1, NOW)
    assert bonus["active_today"] == 1
    
    # Вчера (более 24 часов назад)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW - 86400 * 2)
    bonus = get_chat_bonus(conn, 1, NOW)
    assert bonus["active_today"] == 0
    
    # Другой игрок активен, но атрибутирован к chat-B
    player(path, 2)
    add_chat(path, 2, "chat-A", NOW - 100, played_at=NOW)
    add_chat(path, 2, "chat-B", NOW - 10) # last seen here
    
    bonus = get_chat_bonus(conn, 1, NOW)
    assert bonus["active_today"] == 0 # player 1 not active, player 2 in chat-B
    
    conn.close()

def test_bonus_limits():
    path = new_db()
    conn = _connect(path)
    
    # 15 игроков активны в chat-A
    for i in range(15):
        player(path, i+1)
        add_chat(path, i+1, "chat-A", NOW, played_at=NOW)
        
    bonus = get_chat_bonus(conn, 1, NOW)
    # Макс 20%
    assert bonus["active_today"] == 15
    assert bonus["bonus_pct"] == CHAT_BONUS_MAX_PCT # 20
    
    # Добавим бусты (6 бустов)
    for i in range(6):
        conn.execute("INSERT INTO chat_boosts (chat_instance, telegram_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     ("chat-A", 1, NOW, NOW + 3600))
        
    bonus = get_chat_bonus(conn, 1, NOW)
    # Макс буст 25% + Макс участники 20% = 45%
    assert bonus["bonus_pct"] == CHAT_BONUS_MAX_PCT + CHAT_BOOST_MAX_PCT # 45
    
    conn.close()

def test_buy_boost():
    path = new_db()
    player(path, 1, gems=100)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW)
    
    req_id = rid()
    res = buy_chat_boost(1, req_id, "chat-A", now=NOW, db_path=path)
    assert res["bonus_pct"] == CHAT_BONUS_PER_PLAYER_PCT + CHAT_BOOST_PCT
    assert res["gems"] == 50
    assert res["boost_until"] == NOW + CHAT_BOOST_HOURS * 3600
    
    # Повтор (идемпотентность)
    res2 = buy_chat_boost(1, req_id, "chat-A", now=NOW, db_path=path)
    assert res2["gems"] == 50
    
    # Отказ без атрибуции
    add_chat(path, 1, "chat-B", NOW + 10)
    try:
        buy_chat_boost(1, rid(), "chat-A", now=NOW, db_path=path)
        assert False, "Should raise NotAttributed"
    except NotAttributed:
        pass
    
    conn = _connect(path)
    gems = conn.execute("SELECT gems FROM gem_balances WHERE telegram_id = 1").fetchone()["gems"]
    assert gems == 50 # Не списано

def test_accrual_with_bonus():
    path = new_db()
    player(path, 1)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW) # 2% bonus
    
    # buy a boost -> +5% = 7% total
    buy_chat_boost(1, rid(), "chat-A", now=NOW, db_path=path)
    
    conn = _connect(path)
    conn.execute("BEGIN")
    db._accrue_conn(conn, 1, NOW + 3600) # 1 hour passed
    conn.commit()
    
    # Rate is 100. Effective rate = 100 * 1.07 = 107
    p = conn.execute("SELECT balance FROM players WHERE telegram_id = 1").fetchone()
    # 10^9 + 107

def test_api():
    path = new_db()
    player(path, 1)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW)
    
    app = create_app(TOKEN, [], db_path=path)
    client = TestClient(app)
    
    # POST /api/chat/boost
    r = client.post("/api/chat/boost", headers=auth(1), json={"request_id": rid()})
    assert r.status_code == 200
    assert r.json()["gems"] == 950
    
    # Личка
    r = client.post("/api/chat/boost", headers=auth(1, chat_type="private", chat_instance=None), json={"request_id": rid()})
    assert r.status_code == 409
    assert r.json()["detail"] == "no_chat"
    
    # /api/me
    r = client.get("/api/me", headers=auth(1))
    assert r.json()["chat"]["in_chat"] is True
    assert r.json()["chat"]["bonus_pct"] == 7
    
    r = client.get("/api/me", headers=auth(1, chat_type="private", chat_instance=None))
    assert r.json()["chat"]["in_chat"] is False
    assert r.json()["chat"]["bonus_pct"] == 0

def test_concurrency():
    path = new_db()
    player(path, 1, gems=50 * 20)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW)
    
    gate = threading.Barrier(20)
    def race(i):
        gate.wait()
        return client.post("/api/chat/boost", headers=auth(1), json={"request_id": rid()}).status_code
        
    app = create_app(TOKEN, [], db_path=path)
    client = TestClient(app)
    
    with ThreadPoolExecutor(20) as pool:
        codes = list(pool.map(race, range(20)))
        
    assert codes.count(200) == 20
    
    conn = _connect(path)
    boosts = conn.execute("SELECT count(*) as c FROM chat_boosts").fetchone()["c"]
    assert boosts == 20

def test_data_rights():
    path = new_db()
    player(path, 1)
    add_chat(path, 1, "chat-A", NOW, played_at=NOW)
    buy_chat_boost(1, rid(), "chat-A", now=NOW, db_path=path)
    
    # /mydata
    ex = db.get_player_export(1, db_path=path)
    assert len(ex["chat_boosts"]) == 1
    assert ex["chat_boosts"][0]["gems"] == 50
    
    # /deletemydata
    os.environ["TOMBSTONE_SECRET"] = "test"
    db.delete_player_data(1, db_path=path, now=NOW)
    
    conn = _connect(path)
    boosts = conn.execute("SELECT count(*) as c FROM chat_boosts").fetchone()["c"]
    assert boosts == 0

if __name__ == "__main__":
    test_attribution_and_active()
    test_bonus_limits()
    test_buy_boost()
    test_accrual_with_bonus()
    test_api()
    test_concurrency()
    test_data_rights()
    print("Все проверки прошли")
