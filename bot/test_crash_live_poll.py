import testenv
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from unittest import mock

import db as core_db
import crash_live
from features import crash_live_db
import wallet
import economy_config

db_path = "test_crash_live_poll.sqlite"
db = crash_live_db

def check(name, got, want):
    if got != want:
        raise AssertionError(f"{name}: ожидалось {want!r}, получено {got!r}")

def balance_of(telegram_id):
    c = sqlite3.connect(db_path)
    res = wallet.get_balance(c, telegram_id)
    c.close()
    return res

class CustomRng:
    def __init__(self, start_bytes):
        self.b = start_bytes
    def randrange(self, n):
        return 0

try:
    if os.path.exists(db_path):
        os.remove(db_path)
    core_db.init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    
    # Initialize some players
    for uid in [1, 2, 3, 4, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21]:
        db._register_player(c, uid, 1000)
        wallet.credit(c, uid, 10_000_000)
    c.commit()
    c.close()

    # 1. needs_advance conditions
    # We will verify needs_advance instead, as it queries the DB directly
    T0 = 1_000_000
    
    # No round
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    check("no round needs advance", db.needs_advance(c, T0), True)
    
    # Advance to betting
    db.live_state(1, crash_live.GLOBAL_ROOM, T0, db_path=db_path, rng=CustomRng(b"1"*32))
    r = c.execute("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1").fetchone()
    check("phase betting", crash_live.phase_of(r, T0), "betting")
    
    check("betting needs advance (end_ms-1)", db.needs_advance(c, crash_live.end_ms(r) - 1), False)
    check("betting needs advance (end_ms)", db.needs_advance(c, crash_live.end_ms(r)), True)
    
    # Place bets during betting phase
    db.place_bet(1, crash_live.GLOBAL_ROOM, "req1", 100, 200, T0, db_path=db_path)
    db.place_bet(2, crash_live.GLOBAL_ROOM, "req2", 100, 101, T0, db_path=db_path)

    # Advance to flight
    db.live_state(1, crash_live.GLOBAL_ROOM, r["flight_start_ms"] + 10, db_path=db_path, rng=CustomRng(b"1"*32))
    r = c.execute("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1").fetchone()
    check("phase flight", crash_live.phase_of(r, r["flight_start_ms"] + 10), "flight")
    
    # Check flight auto payout needs advance
    # flight_start_ms is when m100 starts at 100
    # effective_ms = 0 -> m100 = 100
    # effective_ms = 1000 -> m100 = crash.m100(1000) > 101
    
    T_flight = r["flight_start_ms"] + 10
    check("flight needs advance (auto not reached)", db.needs_advance(c, T_flight), False)
    
    T_flight_later = r["flight_start_ms"] + 2000
    check("flight needs advance (auto reached)", db.needs_advance(c, T_flight_later), True)
    
    # Advance to result
    db.live_state(1, crash_live.GLOBAL_ROOM, r["crash_ms"] + crash_live.crash.GRACE_MS, db_path=db_path, rng=CustomRng(b"1"*32))
    r = c.execute("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1").fetchone()
    check("phase result", crash_live.phase_of(r, r["crash_ms"] + crash_live.crash.GRACE_MS), "result")
    
    check("result needs advance (next_open-1)", db.needs_advance(c, crash_live.next_open_ms(r) - 1), False)
    check("result needs advance (next_open)", db.needs_advance(c, crash_live.next_open_ms(r)), True)
    c.close()

    # 2. Fast path read lock check
    T_idle = r["flight_start_ms"] + 1000 # Wait, in result phase. We need a point where NO advance is needed.
    T_no_advance = crash_live.next_open_ms(r) - 1000
    
    # Hold lock in another connection
    c_lock = sqlite3.connect(db_path, timeout=0.1)
    c_lock.execute("BEGIN IMMEDIATE")
    
    start_time = time.time()
    st = db.live_state(1, crash_live.GLOBAL_ROOM, T_no_advance, db_path=db_path)
    elapsed = time.time() - start_time
    if elapsed > 0.5:
        raise AssertionError(f"fast path blocked! took {elapsed}s")
    
    c_lock.rollback()
    c_lock.close()

    # 3. Token checks and unchanged
    T_new_betting = crash_live.next_open_ms(r)
    # Advance to next round betting phase
    db.live_state(1, "room1", T_new_betting, db_path=db_path, rng=CustomRng(b"2"*32))
    
    v_st1 = db.live_state(1, "room1", T_new_betting, db_path=db_path)
    v_token = v_st1["v"]
    
    # Same v -> unchanged
    v_unchanged = db.live_state(1, "room1", T_new_betting, db_path=db_path, client_v=v_token)
    check("unchanged true", v_unchanged.get("unchanged"), True)
    check("unchanged phase", v_unchanged.get("phase"), "betting")
    check("unchanged v", v_unchanged.get("v"), v_token)
    check("unchanged no bets", "bets" in v_unchanged, False)
    
    # Different v -> full
    v_full = db.live_state(1, "room1", T_new_betting, db_path=db_path, client_v="invalid")
    check("full response", "bets" in v_full, True)

    # Bet changes token
    db.place_bet(3, "room1", "req3", 50, None, T_new_betting, db_path=db_path)
    v_st2 = db.live_state(1, "room1", T_new_betting, db_path=db_path)
    check("token changed on bet", v_st1["v"] != v_st2["v"], True)
    
    # Bet in room2 doesn't change room1 token
    db.place_bet(4, "room2", "req4", 50, None, T_new_betting, db_path=db_path)
    v_st3 = db.live_state(1, "room1", T_new_betting, db_path=db_path)
    check("token unchanged on other room bet", v_st2["v"] == v_st3["v"], True)
    
    # My bet changes my_state part of token, but others have same room part
    db.place_bet(1, "room1", "req1_b", 50, None, T_new_betting, db_path=db_path)
    v_st1_with_bet = db.live_state(1, "room1", T_new_betting, db_path=db_path)
    v_st_other = db.live_state(2, "room1", T_new_betting, db_path=db_path)
    check("room part of token is same", v_st1_with_bet["v"].rsplit(".", 1)[0] == v_st_other["v"].rsplit(".", 1)[0], True)
    check("my part is different", v_st1_with_bet["v"].rsplit(".", 1)[1] != v_st_other["v"].rsplit(".", 1)[1], True)

    # Cashout changes token
    # Advance to flight
    c2 = sqlite3.connect(db_path)
    c2.row_factory = sqlite3.Row
    r_new = c2.execute("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1").fetchone()
    c2.close()
    T_flight = r_new["flight_start_ms"] + 1000
    db.live_state(1, "room1", T_flight, db_path=db_path, rng=CustomRng(b"3"*32))
    v_st_before_cashout = db.live_state(1, "room1", T_flight, db_path=db_path)
    db.cashout(1, "req1_c", T_flight, db_path=db_path)
    v_st_after_cashout = db.live_state(1, "room1", T_flight, db_path=db_path)
    check("token changed on cashout", v_st_before_cashout["v"] != v_st_after_cashout["v"], True)

    # 4. Concurrency test
    c = sqlite3.connect(db_path)
    c.execute("UPDATE crash_rounds SET bet_open_ms = ?, flight_start_ms = ?, crash_ms = ?, status = 'closed'", (0, 0, 0))
    c.commit()
    c.close()
    
    running = True
    errors = []
    
    def poller(uid, room):
        try:
            while running:
                st = db.live_state(uid, room, crash_live.now_ms(), db_path=db_path)
                time.sleep(0.01)
        except Exception as e:
            errors.append(e)
            
    def player(uid, room):
        try:
            for i in range(5):
                t = crash_live.now_ms()
                db.live_state(uid, room, t, db_path=db_path)
                try:
                    db.place_bet(uid, room, f"r_{uid}_{i}", 10, None, t, db_path=db_path)
                except Exception:
                    pass
                time.sleep(0.05)
                try:
                    db.cashout(uid, f"c_{uid}_{i}", crash_live.now_ms(), db_path=db_path)
                except Exception:
                    pass
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(8):
        t = threading.Thread(target=poller, args=(10+i, "room_c"))
        threads.append(t)
        t.start()
        
    for i in range(2):
        t = threading.Thread(target=player, args=(20+i, "room_c"))
        threads.append(t)
        t.start()
        
    time.sleep(1)
    running = False
    for t in threads:
        t.join()
        
    if errors:
        raise AssertionError(f"Errors in threads: {errors}")

    print("Все проверки прошли")
finally:
    if os.path.exists(db_path):
        os.remove(db_path)
