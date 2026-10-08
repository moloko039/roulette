"""Живой краш: база данных и логика."""

import time
import math
import sqlite3

import crash
import crash_live
import economy_config
import wallet
import xp
from core import achievements
from core.db_conn import _connect
from games.round_common import pay_and_xp, add_staked
from core.kernel import _accrue_write, _register_player, _record_best_win


def _time_to_crash_x100(crash_x100):
    if crash_x100 <= 100:
        return 0
    if crash_x100 >= crash.CAP_X100:
        return math.ceil(crash.DOUBLING_MS * math.log2(crash.CAP_X100 / 100))
    t = math.ceil(crash.DOUBLING_MS * math.log2(crash_x100 / 100.0))
    while crash.m100(t) < crash_x100:
        t += 1
    while t > 0 and crash.m100(t - 1) >= crash_x100:
        t -= 1
    return t


def _advance_room_in_tx(conn, room_key, now_ms, rng):
    last_round = conn.execute(
        "SELECT id, status, crash_ms FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1",
        (room_key,)
    ).fetchone()

    if not last_round or (last_round["status"] == "closed" and now_ms >= last_round["crash_ms"] + economy_config.CRASH_LIVE_RESULT_MS):
        seed = crash_live.new_seed(rng)
        seed_hash = crash_live.commit_of(seed)
        crash_x100 = crash_live.crash_from_seed(seed)
        
        bet_open_ms = now_ms
        if last_round:
            expected_next = last_round["crash_ms"] + economy_config.CRASH_LIVE_RESULT_MS
            if now_ms - expected_next < economy_config.CRASH_LIVE_BET_MS:
                bet_open_ms = expected_next
            else:
                bet_open_ms = now_ms

        flight_start_ms = bet_open_ms + economy_config.CRASH_LIVE_BET_MS
        crash_ms = flight_start_ms + _time_to_crash_x100(crash_x100)
        
        conn.execute(
            "INSERT INTO crash_rounds (room_key, seed_hash, seed, crash_x100, bet_open_ms, flight_start_ms, crash_ms, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
            (room_key, seed_hash, seed, crash_x100, bet_open_ms, flight_start_ms, crash_ms)
        )
        return

    round_row = conn.execute(
        "SELECT * FROM crash_rounds WHERE id = ?", (last_round["id"],)
    ).fetchone()
    
    if round_row["status"] == "closed":
        return

    phase = crash_live.phase_of(round_row, now_ms)
    
    if phase == "flight" or phase == "result":
        if phase == "result":
            current_m100 = round_row["crash_x100"]
        else:
            current_m100 = crash.m100(crash.effective_ms(now_ms, round_row["flight_start_ms"]))
            current_m100 = min(current_m100, round_row["crash_x100"])

        auto_bets = conn.execute(
            "SELECT telegram_id, bet, target_x100 FROM crash_bets WHERE round_id = ? AND status = 'open' AND target_x100 IS NOT NULL AND target_x100 <= ?",
            (round_row["id"], current_m100)
        ).fetchall()

        now_sec = now_ms // 1000
        for b in auto_bets:
            payout = crash.payout(b["bet"], b["target_x100"])
            conn.execute(
                "UPDATE crash_bets SET status = 'cashed', cashed_x100 = ?, payout = ? WHERE round_id = ? AND telegram_id = ?",
                (b["target_x100"], payout, round_row["id"], b["telegram_id"])
            )
            pay_and_xp(conn, b["telegram_id"], payout, xp.crash_xp(b["bet"], crash.xp_multiplier("auto", "win", b["target_x100"], b["target_x100"])), "crash", b["bet"], now_sec)
            _record_best_win(conn, b["telegram_id"], "crash", b["bet"], payout, now_sec)

    if phase == "result":
        now_sec = now_ms // 1000
        conn.execute("UPDATE crash_rounds SET status = 'closed', settled_at_ms = ? WHERE id = ?", (now_ms, round_row["id"]))
        
        lost_bets = conn.execute("SELECT telegram_id, bet, target_x100 FROM crash_bets WHERE round_id = ? AND status = 'open'", (round_row["id"],)).fetchall()
        for b in lost_bets:
            conn.execute("UPDATE crash_bets SET status = 'lost' WHERE round_id = ? AND telegram_id = ?", (round_row["id"], b["telegram_id"]))
            mode = "auto" if b["target_x100"] is not None else "manual"
            xp_amount = xp.crash_xp(b["bet"], crash.xp_multiplier(mode, "lose", 0, b["target_x100"]))
            pay_and_xp(conn, b["telegram_id"], 0, xp_amount, "crash", b["bet"], now_sec)
            _record_best_win(conn, b["telegram_id"], "crash", b["bet"], 0, now_sec)
            achievements.record(conn, b["telegram_id"], "crash_crash", now_sec, mult_x100=round_row["crash_x100"])

        if now_ms >= round_row["crash_ms"] + economy_config.CRASH_LIVE_RESULT_MS:
            _advance_room_in_tx(conn, room_key, now_ms, rng)


def advance_room(room_key, now_ms, db_path=None, rng=None):
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_room_in_tx(conn, room_key, now_ms, rng)
        conn.commit()
    finally:
        conn.close()


def place_bet(telegram_id, room_key, request_id, bet, target_x100, now_ms, db_path=None, rng=None):
    if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
        raise ValueError("bet out of range")
    if target_x100 is not None and (type(target_x100) is not int or not crash.MIN_TARGET_X100 <= target_x100 <= crash.CAP_X100):
        raise ValueError("target out of range")

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_room_in_tx(conn, room_key, now_ms, rng)
        
        round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (room_key,)).fetchone()
        if not round_row or crash_live.phase_of(round_row, now_ms) != "betting":
            raise crash_live.BettingClosed()
        
        existing = conn.execute("SELECT * FROM crash_bets WHERE round_id = ? AND telegram_id = ?", (round_row["id"], telegram_id)).fetchone()
        if existing:
            if existing["request_id"] == request_id:
                balance = wallet.get_balance(conn, telegram_id)
                conn.commit()
                return {"round_id": round_row["id"], "bet": existing["bet"], "target_x100": existing["target_x100"], "balance": balance, "replayed": True}
            else:
                raise crash_live.AlreadyBet()
                
        count = conn.execute("SELECT COUNT(*) FROM crash_bets WHERE round_id = ?", (round_row["id"],)).fetchone()[0]
        if count >= economy_config.CRASH_LIVE_ROOM_BETS_MAX:
            raise crash_live.RoomFull()
            
        now_sec = now_ms // 1000
        _register_player(conn, telegram_id, now_sec)
        _accrue_write(conn, telegram_id, now_sec)
        wallet.debit(conn, telegram_id, bet)
        add_staked(conn, telegram_id, bet, now_sec)
        
        conn.execute(
            "INSERT INTO crash_bets (round_id, telegram_id, bet, target_x100, status, request_id, created_at_ms) VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (round_row["id"], telegram_id, bet, target_x100, request_id, now_ms)
        )
        
        balance = wallet.get_balance(conn, telegram_id)
        conn.commit()
        return {"round_id": round_row["id"], "bet": bet, "target_x100": target_x100, "balance": balance, "replayed": False}
    finally:
        conn.close()


def cashout(telegram_id, room_key, request_id, now_ms, db_path=None, rng=None):
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_room_in_tx(conn, room_key, now_ms, rng)
        
        round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (room_key,)).fetchone()
        if not round_row:
            raise crash_live.NoBet()
            
        b = conn.execute("SELECT * FROM crash_bets WHERE round_id = ? AND telegram_id = ?", (round_row["id"], telegram_id)).fetchone()
        if not b:
            raise crash_live.NoBet()
            
        if b["request_id"] == request_id and b["status"] == "cashed":
            balance = wallet.get_balance(conn, telegram_id)
            conn.commit()
            return {"round_id": round_row["id"], "cashed_x100": b["cashed_x100"], "payout": b["payout"], "balance": balance, "replayed": True}
            
        if b["status"] != "open":
            raise crash_live.RoundOver()
            
        phase = crash_live.phase_of(round_row, now_ms)
        if phase != "flight":
            raise crash_live.RoundOver()
            
        eff_ms = crash.effective_ms(now_ms, round_row["flight_start_ms"])
        m_x100 = crash.m100(eff_ms)
        if m_x100 > round_row["crash_x100"]:
            raise crash_live.RoundOver()
            
        if m_x100 < crash.MIN_TARGET_X100:
            raise crash_live.TooEarly()
            
        payout = crash.payout(b["bet"], m_x100)
        
        now_sec = now_ms // 1000
        conn.execute("UPDATE crash_bets SET status = 'cashed', cashed_x100 = ?, payout = ?, request_id = ? WHERE round_id = ? AND telegram_id = ?", (m_x100, payout, request_id, round_row["id"], telegram_id))
        
        mode = "auto" if b["target_x100"] is not None else "manual"
        pay_and_xp(conn, telegram_id, payout, xp.crash_xp(b["bet"], crash.xp_multiplier(mode, "win", m_x100, b["target_x100"])), "crash", b["bet"], now_sec)
        _record_best_win(conn, telegram_id, "crash", b["bet"], payout, now_sec)
        
        balance = wallet.get_balance(conn, telegram_id)
        conn.commit()
        return {"round_id": round_row["id"], "cashed_x100": m_x100, "payout": payout, "balance": balance, "replayed": False}
    finally:
        conn.close()


def live_state(telegram_id, room_key, now_ms, chat_instance=None, db_path=None, rng=None):
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_room_in_tx(conn, room_key, now_ms, rng)
        
        round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (room_key,)).fetchone()
        
        history_rows = conn.execute("SELECT crash_x100, seed_hash, seed FROM crash_rounds WHERE room_key = ? AND status = 'closed' ORDER BY id DESC LIMIT ?", (room_key, economy_config.CRASH_LIVE_HISTORY)).fetchall()
        history = [{"crash_x100": r["crash_x100"], "seed_hash": r["seed_hash"], "seed": r["seed"].hex()} for r in history_rows]

        res = {
            "server_ms": now_ms,
            "history": history,
            "round": None,
            "bets": [],
            "me": None
        }

        if round_row:
            phase = crash_live.phase_of(round_row, now_ms)
            r_dict = {
                "id": round_row["id"],
                "phase": phase,
                "seed_hash": round_row["seed_hash"],
                "bet_open_ms": round_row["bet_open_ms"],
                "flight_start_ms": round_row["flight_start_ms"]
            }
            if phase == "flight":
                eff_ms = crash.effective_ms(now_ms, round_row["flight_start_ms"])
                r_dict["m100"] = crash.m100(eff_ms)
            else:
                r_dict["m100"] = None
                
            if phase == "result":
                r_dict["result"] = {
                    "crash_x100": round_row["crash_x100"],
                    "seed": round_row["seed"].hex()
                }
                r_dict["next_open_ms"] = round_row["crash_ms"] + economy_config.CRASH_LIVE_RESULT_MS
                
            res["round"] = r_dict
            
            bets_rows = conn.execute("SELECT telegram_id, bet, target_x100, status, cashed_x100, payout FROM crash_bets WHERE round_id = ? ORDER BY created_at_ms", (round_row["id"],)).fetchall()
            
            bets = []
            me = None
            if chat_instance:
                names = {r["telegram_id"]: r["first_name"] for r in conn.execute("SELECT telegram_id, first_name FROM chat_members WHERE chat_instance = ?", (chat_instance,)).fetchall()}
                for b in bets_rows:
                    if b["telegram_id"] == telegram_id:
                        me = {"bet": b["bet"], "target_x100": b["target_x100"], "status": b["status"], "cashed_x100": b["cashed_x100"], "payout": b["payout"]}
                    bets.append({
                        "name": names.get(b["telegram_id"], "Игрок"),
                        "bet": b["bet"],
                        "status": b["status"],
                        "cashed_x100": b["cashed_x100"],
                        "payout": b["payout"]
                    })
            else:
                for b in bets_rows:
                    if b["telegram_id"] == telegram_id:
                        me = {"bet": b["bet"], "target_x100": b["target_x100"], "status": b["status"], "cashed_x100": b["cashed_x100"], "payout": b["payout"]}
                        bets.append({
                            "name": "Игрок",
                            "bet": b["bet"],
                            "status": b["status"],
                            "cashed_x100": b["cashed_x100"],
                            "payout": b["payout"]
                        })
            res["bets"] = bets
            res["me"] = me
            
        conn.commit()
        return res
    finally:
        conn.close()
