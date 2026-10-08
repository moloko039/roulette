import testenv
import crash_live
import db
import crash
import economy_config
import os
import time
import math
import tempfile
import threading
import sqlite3
import random
import json

def check(name, got, expected):
    if type(got) is float and type(expected) is float:
        assert math.isclose(got, expected, rel_tol=0.01) or abs(got - expected) < 1e-4, f"{name}: {got} != {expected}"
    else:
        assert got == expected, f"{name}: {got} != {expected}"

# 1. честность
seed = crash_live.new_seed()
seed_hex = seed.hex()
commit_hex = crash_live.commit_of(seed)
crash_x100 = crash_live.crash_from_seed(seed)
check("verify ok", crash_live.verify(seed_hex, commit_hex, crash_x100), True)
check("verify fail commit", crash_live.verify(seed_hex, commit_hex.replace('0','1').replace('1','0'), crash_x100), False)
check("verify fail crash", crash_live.verify(seed_hex, commit_hex, crash_x100 + 1), False)
check("verify fail seed", crash_live.verify(seed_hex.replace('0','1').replace('1','0'), commit_hex, crash_x100), False)

# 2. распределение
rng = random.Random(42)
crashes = [crash_live.crash_from_seed(crash_live.new_seed(rng)) for _ in range(200_000)]
for x in (2, 10, 50):
    expected_prob = 36 / (37 * x)
    actual_prob = sum(1 for c in crashes if c >= x * 100) / len(crashes)
    sigma = math.sqrt(expected_prob * (1 - expected_prob) / len(crashes))
    assert abs(actual_prob - expected_prob) < 4 * sigma, f"prob for {x}: {actual_prob} vs {expected_prob}"
returns = sum(200 for c in crashes if c >= 200)
expected_return = len(crashes) * 100 * 36 / 37
assert abs(returns - expected_return) < 4 * math.sqrt(len(crashes) * 100 * 100 * 0.5), f"return: {returns} vs {expected_return}"

import shutil
tmp = tempfile.mkdtemp()
try:
    db_path = os.path.join(tmp, "test.db")
    db.init_db(db_path)
    NOW = 1_700_000_000_000
    conn = sqlite3.connect(db_path)
    for i in range(1, 25):
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 1000000, 100, ?, ?)", (i, NOW // 1000 + 3600, NOW // 1000))
        conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('room1', ?, ?, 0, 0)", (i, f'Player{i}'))
    conn.commit()
    conn.close()

    high_seed = b'\x00' * 32
    for i in range(10000):
        s = i.to_bytes(32, "big")
        if crash_live.crash_from_seed(s) > 200:
            high_seed = s
            break
            
    cx100 = crash_live.crash_from_seed(high_seed)
    t = math.ceil(crash.DOUBLING_MS * math.log2(cx100 / 100.0))
    while crash.m100(t) < cx100: t += 1
    while t > 0 and crash.m100(t - 1) >= cx100: t -= 1
    time_to_crash = t

    class CustomRng:
        def __init__(self, seed_bytes):
            self.bytes = seed_bytes
            self.idx = 0
        def randrange(self, mx):
            val = self.bytes[self.idx % 32]
            self.idx += 1
            return val

    my_rng = CustomRng(high_seed)

    room = crash_live.room_key(chat_instance="room1")
    state = db.live_state(1, room, NOW, chat_instance="room1", db_path=db_path, rng=my_rng)
    
    check("initial round opens", state["round"]["phase"], "betting")
    
    bet1 = db.place_bet(1, room, "req1", 100, None, NOW, db_path=db_path, rng=my_rng)
    check("bet1 ok", bet1["bet"], 100)
    
    try:
        db.place_bet(1, room, "req2", 100, None, NOW, db_path=db_path, rng=my_rng)
        assert False, "Double bet allowed"
    except crash_live.AlreadyBet:
        pass

    bet1_rep = db.place_bet(1, room, "req1", 100, None, NOW, db_path=db_path, rng=my_rng)
    check("bet1 replayed", bet1_rep["replayed"], True)

    for i in range(2, 21): 
        db.place_bet(i, room, f"req_cap_{i}", 100, 150, NOW, db_path=db_path, rng=my_rng)

    old_cap = economy_config.CRASH_LIVE_ROOM_BETS_MAX
    economy_config.CRASH_LIVE_ROOM_BETS_MAX = 20
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (999, 1000000, 100, ?, ?)", (NOW // 1000 + 3600, NOW // 1000))
        conn.commit(); conn.close()
        db.place_bet(999, room, "req_cap_999", 100, None, NOW, db_path=db_path, rng=my_rng)
        assert False, "Room limit ignored"
    except crash_live.RoomFull:
        pass
    finally:
        economy_config.CRASH_LIVE_ROOM_BETS_MAX = old_cap

    state = db.live_state(1, room, NOW, chat_instance="room1", db_path=db_path, rng=my_rng)
    json_state = json.dumps(state)
    check("leak check betting seed", high_seed.hex() in json_state, False)
    check("leak check betting crash", "crash_x100" in state["round"], False)

    now = state["round"]["flight_start_ms"]
    state_flight = db.live_state(1, room, now, chat_instance="room1", db_path=db_path, rng=my_rng)
    check("phase flight", state_flight["round"]["phase"], "flight")
    
    try:
        db.place_bet(999, room, "req4", 100, None, now, db_path=db_path, rng=my_rng)
        assert False, "Bet out of phase"
    except crash_live.BettingClosed:
        pass

    try:
        db.cashout(1, room, "req5", now, db_path=db_path, rng=my_rng)
        assert False, "Too early"
    except crash_live.TooEarly:
        pass
    
    now_101 = now + crash.GRACE_MS + 100 
    res101 = db.cashout(1, room, "req6", now_101, db_path=db_path, rng=my_rng)
    check("cashed out 101", res101["cashed_x100"] >= 101, True)

    now_crash = state_flight["round"]["flight_start_ms"] + time_to_crash + 10
    state_crash = db.live_state(1, room, now_crash, chat_instance="room1", db_path=db_path, rng=my_rng)
    check("phase result", state_crash["round"]["phase"], "result")
    check("seed disclosed", high_seed.hex() in json.dumps(state_crash), True)
    
    conn = sqlite3.connect(db_path)
    bals = conn.execute("SELECT sum(balance) FROM players WHERE telegram_id <= 20").fetchone()[0]
    expected_bals = 20 * 1000000 - (100 * 20) + (100 * res101["cashed_x100"] // 100) + 19 * 150 
    check("money conserved", bals, expected_bals)
    
    now_next = state_crash["round"]["next_open_ms"]
    state_next = db.live_state(1, room, now_next, chat_instance="room1", db_path=db_path, rng=my_rng)
    
    errors = []
    def do_bet(i):
        try:
            db.place_bet(1, room, f"race_req_{i}", 100, None, now_next, db_path=db_path, rng=my_rng)
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=do_bet, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    assert len(errors) == 11, f"Expected 11 errors, got {len(errors)}: {errors}"
    assert all(isinstance(e, crash_live.AlreadyBet) for e in errors)
    
    now_race_flight = state_next["round"]["flight_start_ms"] + crash.GRACE_MS + 200
    db.live_state(1, room, now_race_flight, chat_instance="room1", db_path=db_path, rng=my_rng)
    
    errors = []
    def do_cashout(i):
        try:
            db.cashout(1, room, f"race_cash_{i}", now_race_flight, db_path=db_path, rng=my_rng)
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=do_cashout, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    assert len(errors) == 11, f"Expected 11 errors, got {len(errors)}: {errors}"
    assert all(isinstance(e, crash_live.RoundOver) or isinstance(e, crash_live.NoBet) for e in errors)
    
    now_skip = now_race_flight + 10_000_000
    state_skip = db.live_state(1, room, now_skip, chat_instance="room1", db_path=db_path, rng=my_rng)
    check("missed rounds skipped", state_skip["round"]["phase"], "betting")

    print("Все проверки прошли")
finally:
    shutil.rmtree(tmp)
