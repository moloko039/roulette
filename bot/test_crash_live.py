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
check("verify fail commit", crash_live.verify(seed_hex, ("0" if commit_hex[0] != "0" else "1") + commit_hex[1:], crash_x100), False)
check("verify fail crash", crash_live.verify(seed_hex, commit_hex, crash_x100 + 1), False)
check("verify fail seed", crash_live.verify(("0" if seed_hex[0] != "0" else "1") + seed_hex[1:], commit_hex, crash_x100), False)

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
        db.cashout(1, "req5", now, db_path=db_path, rng=my_rng)
        assert False, "Too early"
    except crash_live.TooEarly:
        pass
    
    now_101 = now + crash.GRACE_MS + 100 
    res101 = db.cashout(1, "req6", now_101, db_path=db_path, rng=my_rng)
    check("cashed out 101", res101["cashed_x100"] >= 101, True)

    now_crash = state_flight["round"]["flight_start_ms"] + time_to_crash + crash.GRACE_MS + 10      # раунд закрывается через запас сети после краха
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
            db.cashout(1, f"race_cash_{i}", now_race_flight, db_path=db_path, rng=my_rng)
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


    # ---------- границы закрытия с запасом сети и прочие правила (ведущий) ----------
    T0 = 5_000_000_000_000                 # окно сценария A; раунд теперь один на весь сервер, поэтому сценарии разнесены по времени
    TB = T0 + 10_000_000
    TC = T0 + 20_000_000
    TD = T0 + 90_000_000

    def seed_with(pred, start=1_000_000):
        for i in range(start, start + 200_000):
            sd = i.to_bytes(32, "big")
            if pred(crash_live.crash_from_seed(sd)):
                return sd
        raise AssertionError("seed not found")

    def flight_time(c):
        t = math.ceil(crash.DOUBLING_MS * math.log2(c / 100.0))
        while crash.m100(t) < c:
            t += 1
        while t > 0 and crash.m100(t - 1) >= c:
            t -= 1
        return t

    def add_players(ids, balance=1_000_000, at=None):
        at = T0 if at is None else at
        cn = sqlite3.connect(db_path)
        for i in ids:
            cn.execute("INSERT OR IGNORE INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, ?, 100, ?, ?)", (i, balance, at // 1000 + 3600, at // 1000))
        cn.commit()
        cn.close()

    def add_member(chat, uid, name):
        cn = sqlite3.connect(db_path)
        cn.execute("INSERT OR REPLACE INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, 0, 0)", (chat, uid, name))
        cn.commit()
        cn.close()

    def balance_of(uid):
        cn = sqlite3.connect(db_path)
        v = cn.execute("SELECT balance FROM players WHERE telegram_id = ?", (uid,)).fetchone()[0]
        cn.close()
        return v

    # граница закрытия: за 1 мс до crash_ms + GRACE вывод ещё выигрывает, ровно в этот момент уже поздно (запас сети)
    s1 = seed_with(lambda c: 300 <= c <= 800)
    c1 = crash_live.crash_from_seed(s1)
    tc1 = flight_time(c1)
    add_players([101, 102])
    roomA = crash_live.room_key(chat_instance="gA")
    rngA = CustomRng(s1)
    db.live_state(101, roomA, T0, chat_instance="gA", db_path=db_path, rng=rngA)
    db.place_bet(101, roomA, "bA1", 100, None, T0, db_path=db_path, rng=rngA)
    db.place_bet(102, roomA, "bA2", 100, None, T0, db_path=db_path, rng=rngA)
    fsA = T0 + economy_config.CRASH_LIVE_BET_MS
    endA = fsA + tc1 + crash.GRACE_MS
    won = db.cashout(101, "cA1", endA - 1, db_path=db_path, rng=rngA)
    check("за 1 мс до закрытия вывод выигрывает и множитель ниже точки краха", (won["cashed_x100"] < c1, won["cashed_x100"] >= 101), (True, True))
    check("выплата по множителю", won["payout"], 100 * won["cashed_x100"] // 100)
    try:
        db.cashout(102, "cA2", endA, db_path=db_path, rng=rngA)
        assert False, "вывод в момент закрытия должен быть поздним"
    except crash_live.RoundOver:
        pass
    stA = db.live_state(102, roomA, endA, chat_instance="gA", db_path=db_path, rng=rngA)
    check("опоздавший проиграл, баланс уменьшился на ставку", (stA["me"]["status"], balance_of(102)), ("lost", 1_000_000 - 100))
    check("выигравший получил ставку обратно с выигрышем", balance_of(101), 1_000_000 - 100 + won["payout"])
    bal_before_replay = balance_of(101)
    again = db.cashout(101, "cA1", endA, db_path=db_path, rng=rngA)
    check("повтор вывода по тому же request_id (потерян ответ): тот же результат, replayed, баланс не меняется",
          (again["replayed"], again["cashed_x100"], again["payout"], balance_of(101)), (True, won["cashed_x100"], won["payout"], bal_before_replay))
    check("в фазе итога раскрыты точка и секрет и они сходятся с хэшем", crash_live.verify(stA["round"]["result"]["seed"], stA["round"]["seed_hash"], stA["round"]["result"]["crash_x100"]), True)
    # цель выше точки краха проигрывает, цель не выше краха выплачивается по цели (не по множителю краха)
    s2 = seed_with(lambda c: 150 <= c <= 400, start=2_000_000)
    c2 = crash_live.crash_from_seed(s2)
    add_players([111, 112], at=TB)
    roomB = crash_live.room_key(chat_instance="gB")
    rngB = CustomRng(s2)
    db.live_state(111, roomB, TB, chat_instance="gB", db_path=db_path, rng=rngB)
    db.place_bet(111, roomB, "bB1", 100, 20000, TB, db_path=db_path, rng=rngB)       # цель x200 выше краха
    db.place_bet(112, roomB, "bB2", 100, 101, TB, db_path=db_path, rng=rngB)         # цель x1.01 не выше краха
    endB = TB + economy_config.CRASH_LIVE_BET_MS + flight_time(c2) + crash.GRACE_MS + 1
    stB = db.live_state(111, roomB, endB, chat_instance="gB", db_path=db_path, rng=rngB)
    check("цель выше краха: проиграла", (stB["me"]["status"], balance_of(111)), ("lost", 1_000_000 - 100))
    check("цель ниже краха: выплата ровно по цели", (balance_of(112), [b["cashed_x100"] for b in stB["bets"] if b["status"] == "cashed"]), (1_000_000 - 100 + 100 * 101 // 100, [101]))
    check("цель чужой ставки в ленте не показывается", any("target_x100" in b for b in stB["bets"]), False)

    # простой: после долгой паузы невыведенные ставки проиграны, автоцели выплачены, открывается ОДИН новый раунд, пропущенные не доигрываются
    add_players([121, 122], at=TC)
    roomC = crash_live.room_key(chat_instance="gC")
    rngC = CustomRng(s2)
    db.live_state(121, roomC, TC, chat_instance="gC", db_path=db_path, rng=rngC)
    db.place_bet(121, roomC, "bC1", 100, None, TC, db_path=db_path, rng=rngC)
    db.place_bet(122, roomC, "bC2", 100, 101, TC, db_path=db_path, rng=rngC)
    later = TC + 50_000_000
    stC = db.live_state(121, roomC, later, chat_instance="gC", db_path=db_path, rng=CustomRng(high_seed))
    cn = sqlite3.connect(db_path)
    rounds_c = cn.execute("SELECT COUNT(*), SUM(status = 'closed') FROM crash_rounds WHERE bet_open_ms >= ? AND bet_open_ms < ?", (TC, TC + 60_000_000)).fetchone()
    bets_c = dict(cn.execute("SELECT telegram_id, status FROM crash_bets WHERE round_id = (SELECT MIN(id) FROM crash_rounds WHERE bet_open_ms >= ? AND bet_open_ms < ?)", (TC, TC + 60_000_000)).fetchall())
    cn.close()
    check("после простоя: два раунда (закрытый и один новый), приём ставок идёт", (rounds_c[0], rounds_c[1], stC["round"]["phase"]), (2, 1, "betting"))
    check("после простоя: ручная проиграна, автоцель выплачена", (bets_c[121], bets_c[122], balance_of(121), balance_of(122)), ("lost", "cashed", 1_000_000 - 100, 1_000_000 - 100 + 101))

    # один общий раунд на весь сервер, лента ставок по беседам: беседы D и E и личная комната играют в одном раунде, но видят только свои ставки
    add_players([131, 132, 141, 142, 143], at=TD)
    add_member("gD", 131, "Анна")
    add_member("gE", 132, "Борис")
    add_member("gE", 131, "Чужое")          # тот же игрок в другой беседе под другим именем: в ленте D имя берётся из D
    roomD = crash_live.room_key(chat_instance="gD")
    roomE = crash_live.room_key(chat_instance="gE")
    sD = seed_with(lambda c: c > 200, start=3_000_000)          # свой секрет у раунда D: в общей истории прошлых раундов его нет
    rngD = CustomRng(sD)
    db.live_state(131, roomD, TD, chat_instance="gD", db_path=db_path, rng=rngD)
    db.place_bet(131, roomD, "bD1", 100, None, TD, db_path=db_path, rng=rngD)
    db.place_bet(132, roomE, "bE1", 200, None, TD, db_path=db_path, rng=rngD)
    pub = crash_live.room_key()           # общая анонимная комната игроков вне бесед
    check("ключ общей комнаты постоянный и не равен ключу беседы", (pub == crash_live.PUBLIC_ROOM_KEY, pub != roomD), (True, True))
    db.place_bet(141, pub, "bS1", 300, None, TD, db_path=db_path, rng=rngD)
    db.place_bet(142, pub, "bS2", 400, None, TD + 1, db_path=db_path, rng=rngD)
    feedD = db.live_state(131, roomD, TD + 1, chat_instance="gD", db_path=db_path, rng=rngD)
    feedE = db.live_state(132, roomE, TD + 1, chat_instance="gE", db_path=db_path, rng=rngD)
    feedS = db.live_state(141, pub, TD + 2, db_path=db_path, rng=rngD)
    feedS2 = db.live_state(143, pub, TD + 2, db_path=db_path, rng=rngD)
    check("беседа D видит только свои ставки, имя из беседы D", [(b["name"], b["bet"]) for b in feedD["bets"]], [("Анна", 100)])
    check("беседа E видит только свои ставки", [(b["name"], b["bet"]) for b in feedE["bets"]], [("Борис", 200)])
    check("вне бесед: игроки видят ставки друг друга, но анонимно (Игрок N по порядку подачи), ставки бесед в общую ленту не попадают",
          ([(b["name"], b["bet"]) for b in feedS["bets"]], [(b["name"], b["bet"]) for b in feedS2["bets"]]),
          ([("Игрок 1", 300), ("Игрок 2", 400)], [("Игрок 1", 300), ("Игрок 2", 400)]))
    check("в общей ленте нет настоящих имён и идентификаторов, у наблюдателя без ставки me пуст", ("141" in json.dumps(feedS2["bets"]), "Анна" in json.dumps(feedS2), feedS2["me"]), (False, False, None))
    check("раунд ОДИН на всех: одинаковые номер, хэш и времена во всех комнатах", (feedD["round"]["id"] == feedE["round"]["id"] == feedS["round"]["id"],
          feedD["round"]["seed_hash"] == feedE["round"]["seed_hash"] == feedS["round"]["seed_hash"], feedD["round"]["flight_start_ms"] == feedE["round"]["flight_start_ms"]), (True, True, True))
    check("в раунде ровно четыре ставки на весь сервер (две беседы и две вне бесед)", sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM crash_bets WHERE round_id = ?", (feedD["round"]["id"],)).fetchone()[0], 4)
    # вывод не зависит от комнаты: игрок из E выводит в общем полёте и выигрывает, ставки других комнат не затронуты
    wonE = db.cashout(132, "cE1", TD + economy_config.CRASH_LIVE_BET_MS + crash.GRACE_MS + 1500, db_path=db_path, rng=rngD)
    check("вывод игрока беседы E в общем раунде: выплата по множителю", wonE["payout"], 200 * wonE["cashed_x100"] // 100)
    feedD2 = db.live_state(131, roomD, TD + economy_config.CRASH_LIVE_BET_MS + crash.GRACE_MS + 1600, chat_instance="gD", db_path=db_path, rng=rngD)
    check("чужой вывод в беседе D не виден (в D по-прежнему одна ставка Анны, открытая)", [(b["name"], b["status"]) for b in feedD2["bets"]], [("Анна", "open")])

    # в полёте точка краха и секрет текущего раунда не утекают ни ключом, ни значением (история закрытых раундов раскрыта по замыслу)
    stF = db.live_state(131, roomD, TD + economy_config.CRASH_LIVE_BET_MS + 100, chat_instance="gD", db_path=db_path, rng=rngD)
    current = json.dumps({"round": stF["round"], "bets": stF["bets"], "me": stF["me"]})
    check("в полёте: фаза flight, нет result, нет crash_x100 в раунде, ленте и «мне»", (stF["round"]["phase"], "result" in stF["round"] and stF["round"]["result"] is not None, "crash_x100" in current), ("flight", False, False))
    check("в полёте секрет текущего раунда не встречается нигде в ответе, даже в истории", sD.hex() in json.dumps(stF), False)
    # лента общей комнаты усечена до последних CRASH_LIVE_FEED_MAX ставок, нумерация сквозная; своя старая ставка отдаётся отдельно (me)
    TE = T0 + 130_000_000
    add_players([151, 152, 153, 154, 155], at=TE)
    sE = seed_with(lambda c: c > 200, start=4_000_000)
    rngE = CustomRng(sE)
    db.live_state(151, pub, TE, db_path=db_path, rng=rngE)
    for k, uid in enumerate([151, 152, 153, 154, 155]):
        db.place_bet(uid, pub, "bF%d" % uid, 100 + k, None, TE + k, db_path=db_path, rng=rngE)
    old_feed = economy_config.CRASH_LIVE_FEED_MAX
    economy_config.CRASH_LIVE_FEED_MAX = 3
    try:
        feedF = db.live_state(151, pub, TE + 10, db_path=db_path, rng=rngE)
    finally:
        economy_config.CRASH_LIVE_FEED_MAX = old_feed
    check("усечённая лента: последние три ставки, нумерация сквозная (Игрок 3..5)", [(b["name"], b["bet"]) for b in feedF["bets"]], [("Игрок 3", 102), ("Игрок 4", 103), ("Игрок 5", 104)])
    check("своя ставка старше ленты всё равно отдаётся в me", feedF["me"]["bet"], 100)

    print("Все проверки прошли")
finally:
    shutil.rmtree(tmp)
