"""Живой краш: база данных и логика. Раунд один на весь сервер (комната crash_live.GLOBAL_ROOM); комната беседы (room_key) определяет только ленту ставок, которую видит игрок."""

import math
import threading
import time

import crash
import crash_live
import economy_config
import wallet
import xp
from core import achievements
from core.db_conn import _connect
from games.round_common import pay_and_xp, add_staked
from core.kernel import _accrue_write, _register_player

# Записи живого краша (ставка, вывод, продвижение раунда) идут в процессе по одной под общим замком. Без него десятки потоков в момент события (открытие раунда,
# пересечение автоцели, закрытие) разом берут BEGIN IMMEDIATE, а SQLite разводит их сном с нарастающей паузой: задержки растут, хотя сама запись стоит около 0,6 мс
# (tools/crash_load.py). Замок процессный: сервер один; между процессами защищает BEGIN IMMEDIATE, как раньше.
_WRITE_LOCK = threading.Lock()


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


def _advance_round_in_tx(conn, now_ms, rng):
    room_key = crash_live.GLOBAL_ROOM
    last_round = conn.execute(
        "SELECT id, status, crash_ms FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1",
        (room_key,)
    ).fetchone()

    if not last_round or (last_round["status"] == "closed" and now_ms >= crash_live.next_open_ms(last_round)):
        seed = crash_live.new_seed(rng)
        seed_hash = crash_live.commit_of(seed)
        crash_x100 = crash_live.crash_from_seed(seed)
        
        bet_open_ms = now_ms
        if last_round:
            expected_next = crash_live.next_open_ms(last_round)
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

    if phase == "result":
        now_sec = now_ms // 1000
        conn.execute("UPDATE crash_rounds SET status = 'closed', settled_at_ms = ? WHERE id = ?", (now_ms, round_row["id"]))
        
        lost_bets = conn.execute("SELECT telegram_id, bet, target_x100 FROM crash_bets WHERE round_id = ? AND status = 'open'", (round_row["id"],)).fetchall()
        for b in lost_bets:
            conn.execute("UPDATE crash_bets SET status = 'lost' WHERE round_id = ? AND telegram_id = ?", (round_row["id"], b["telegram_id"]))
            mode = "auto" if b["target_x100"] is not None else "manual"
            xp_amount = xp.crash_xp(b["bet"], crash.xp_multiplier(mode, "lose", 0, b["target_x100"]))
            pay_and_xp(conn, b["telegram_id"], 0, xp_amount, "crash", b["bet"], now_sec)
            achievements.record(conn, b["telegram_id"], "crash_crash", now_sec, mult_x100=round_row["crash_x100"])

        if now_ms >= crash_live.next_open_ms(round_row):
            _advance_round_in_tx(conn, now_ms, rng)


def advance_round(now_ms, db_path=None, rng=None):
    with _WRITE_LOCK:
        _advance_round_locked(now_ms, db_path, rng)


def _advance_round_locked(now_ms, db_path, rng):
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_round_in_tx(conn, now_ms, rng)
        conn.commit()
    finally:
        conn.close()


def place_bet(telegram_id, room_key, request_id, bet, target_x100, now_ms, db_path=None, rng=None):
    with _WRITE_LOCK:
        return _place_bet_locked(telegram_id, room_key, request_id, bet, target_x100, now_ms, db_path, rng)


def _place_bet_locked(telegram_id, room_key, request_id, bet, target_x100, now_ms, db_path, rng):
    if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
        raise ValueError("bet out of range")
    if target_x100 is not None and (type(target_x100) is not int or not crash.MIN_TARGET_X100 <= target_x100 <= crash.CAP_X100):
        raise ValueError("target out of range")

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_round_in_tx(conn, now_ms, rng)
        
        round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (crash_live.GLOBAL_ROOM,)).fetchone()
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
                
        room_count = conn.execute("SELECT COUNT(*) FROM crash_bets WHERE round_id = ? AND room_key = ?", (round_row["id"], room_key)).fetchone()[0]
        round_count = conn.execute("SELECT COUNT(*) FROM crash_bets WHERE round_id = ?", (round_row["id"],)).fetchone()[0]
        room_cap = economy_config.CRASH_LIVE_ROUND_BETS_MAX if room_key == crash_live.PUBLIC_ROOM_KEY else economy_config.CRASH_LIVE_ROOM_BETS_MAX
        if room_count >= room_cap or round_count >= economy_config.CRASH_LIVE_ROUND_BETS_MAX:
            raise crash_live.RoomFull()
            
        now_sec = now_ms // 1000
        _register_player(conn, telegram_id, now_sec)
        _accrue_write(conn, telegram_id, now_sec)
        wallet.debit(conn, telegram_id, bet)
        add_staked(conn, telegram_id, bet, now_sec)
        
        conn.execute(
            "INSERT INTO crash_bets (round_id, telegram_id, bet, target_x100, status, request_id, created_at_ms, room_key) VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (round_row["id"], telegram_id, bet, target_x100, request_id, now_ms, room_key)
        )
        
        balance = wallet.get_balance(conn, telegram_id)
        conn.commit()
        return {"round_id": round_row["id"], "bet": bet, "target_x100": target_x100, "balance": balance, "replayed": False}
    finally:
        conn.close()


def cashout(telegram_id, request_id, now_ms, db_path=None, rng=None):
    with _WRITE_LOCK:
        return _cashout_locked(telegram_id, request_id, now_ms, db_path, rng)


def _cashout_locked(telegram_id, request_id, now_ms, db_path, rng):
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _advance_round_in_tx(conn, now_ms, rng)
        
        round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (crash_live.GLOBAL_ROOM,)).fetchone()
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
        
        balance = wallet.get_balance(conn, telegram_id)
        conn.commit()
        return {"round_id": round_row["id"], "cashed_x100": m_x100, "payout": payout, "balance": balance, "replayed": False}
    finally:
        conn.close()


def needs_advance(conn, now_ms):
    """Чистая функция (только чтение БД без блокировок), проверяющая нужно ли продвигать время."""
    round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (crash_live.GLOBAL_ROOM,)).fetchone()
    if not round_row:
        return True
    if round_row["status"] == "closed" and now_ms >= crash_live.next_open_ms(round_row):
        return True
    if round_row["status"] == "open" and now_ms >= crash_live.end_ms(round_row):
        return True
    if round_row["status"] == "open" and crash_live.phase_of(round_row, now_ms) == "flight":
        eff_ms = crash.effective_ms(now_ms, round_row["flight_start_ms"])
        curr_m100 = min(crash.m100(eff_ms), round_row["crash_x100"])
        # Check if there's any open bet with auto cashout <= curr_m100
        has_auto = conn.execute("SELECT 1 FROM crash_bets WHERE round_id = ? AND status = 'open' AND target_x100 IS NOT NULL AND target_x100 <= ? LIMIT 1", (round_row["id"], curr_m100)).fetchone()
        if has_auto:
            return True
    return False


def _build_live_state(conn, telegram_id, room_key, now_ms, chat_instance, client_v=None):
    round_row = conn.execute("SELECT * FROM crash_rounds WHERE room_key = ? ORDER BY id DESC LIMIT 1", (crash_live.GLOBAL_ROOM,)).fetchone()
    
    history_rows = conn.execute("SELECT crash_x100, seed_hash, seed FROM crash_rounds WHERE room_key = ? AND status = 'closed' ORDER BY id DESC LIMIT ?", (crash_live.GLOBAL_ROOM, economy_config.CRASH_LIVE_HISTORY)).fetchall()
    history = [{"crash_x100": r["crash_x100"], "seed_hash": r["seed_hash"], "seed": r["seed"].hex()} for r in history_rows]

    res = {
        "server_ms": now_ms,
        "history": history,
        "round": None,
        "bets": [],
        "me": None
    }

    if not round_row:
        v_token = f"0.none.0.0.none"
        if client_v == v_token:
            return {"unchanged": True, "v": v_token, "server_ms": now_ms, "phase": "none", "m100": None}
        res["v"] = v_token
        return res

    phase = crash_live.phase_of(round_row, now_ms)
    r_dict = {
        "id": round_row["id"],
        "phase": phase,
        "seed_hash": round_row["seed_hash"],
        "bet_open_ms": round_row["bet_open_ms"],
        "flight_start_ms": round_row["flight_start_ms"]
    }
    
    m100_val = None
    if phase == "flight":
        eff_ms = crash.effective_ms(now_ms, round_row["flight_start_ms"])
        m100_val = crash.m100(eff_ms)
    r_dict["m100"] = m100_val
        
    if phase == "result":
        r_dict["result"] = {
            "crash_x100": round_row["crash_x100"],
            "seed": round_row["seed"].hex()
        }
        r_dict["next_open_ms"] = crash_live.next_open_ms(round_row)
        
    res["round"] = r_dict
    
    feed_rows = conn.execute("SELECT telegram_id, bet, status, cashed_x100, payout, created_at_ms FROM crash_bets WHERE round_id = ? AND room_key = ? "
                             "ORDER BY created_at_ms DESC, telegram_id DESC LIMIT ?", (round_row["id"], room_key, economy_config.CRASH_LIVE_FEED_MAX)).fetchall()
    feed_rows = list(reversed(feed_rows))
    my_row = conn.execute("SELECT bet, target_x100, status, cashed_x100, payout FROM crash_bets WHERE round_id = ? AND telegram_id = ?", (round_row["id"], telegram_id)).fetchone()
    me = None
    my_state = "none"
    if my_row is not None:
        me = {"bet": my_row["bet"], "target_x100": my_row["target_x100"], "status": my_row["status"], "cashed_x100": my_row["cashed_x100"], "payout": my_row["payout"]}
        my_state = f"{my_row['status']}_{my_row['payout'] or 0}"
        
    n_bets = len(feed_rows)
    n_done = sum(1 for b in feed_rows if b["status"] != "open")
    
    # Check if feed rows are truncated to accurately get total bets for the token if needed. Wait, token rules say "число ставок в ленте комнаты, число ставок комнаты в статусе не open (cashed или lost)"
    # Better to count them precisely in db to have a stable token even if feed limit applies.
    # Actually, if we just use the entire room's count, it's easier and perfectly valid.
    counts = conn.execute("SELECT COUNT(*), SUM(status != 'open') FROM crash_bets WHERE round_id = ? AND room_key = ?", (round_row["id"], room_key)).fetchone()
    total_bets = counts[0]
    total_done = counts[1] or 0
    
    v_token = f"{round_row['id']}.{phase}.{total_bets}.{total_done}.{my_state}"
    if client_v == v_token:
        return {"unchanged": True, "v": v_token, "server_ms": now_ms, "phase": phase, "m100": m100_val}

    names = {}
    if chat_instance and room_key != crash_live.PUBLIC_ROOM_KEY:
        names = {r["telegram_id"]: r["first_name"] for r in conn.execute("SELECT telegram_id, first_name FROM chat_members WHERE chat_instance = ?", (chat_instance,)).fetchall()}
    anonymous = room_key == crash_live.PUBLIC_ROOM_KEY
    ordinal_base = 0
    if anonymous and len(feed_rows) == economy_config.CRASH_LIVE_FEED_MAX:
        ordinal_base = conn.execute("SELECT COUNT(*) FROM crash_bets WHERE round_id = ? AND room_key = ? AND (created_at_ms, telegram_id) < (?, ?)",
                                    (round_row["id"], room_key, feed_rows[0]["created_at_ms"], feed_rows[0]["telegram_id"])).fetchone()[0]
    bets = []
    for n, b in enumerate(feed_rows, start=ordinal_base + 1):
        label = ("Игрок %d" % n) if anonymous else (names.get(b["telegram_id"]) or "Игрок")
        bets.append({"name": label, "bet": b["bet"], "status": b["status"], "cashed_x100": b["cashed_x100"], "payout": b["payout"]})
    res["bets"] = bets
    res["me"] = me
    res["v"] = v_token
    return res


def live_state_read(telegram_id, room_key, now_ms, chat_instance=None, db_path=None, client_v=None):
    """Быстрый читающий путь: проверка и сборка ответа в ОДНОЙ читающей транзакции (общий снимок базы, без блокировки записи, около 0,4 мс).
    None, если раунд пора продвигать (открыть, закрыть, выплатить автовывод): тогда нужен live_state (запись). Обработчик вызывает это прямо в цикле событий:
    так опрос не платит за передачу в пул потоков и гонку за GIL (на замере tools/crash_load.py это съедало больше, чем сама работа)."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        try:
            if not needs_advance(conn, now_ms):
                return _build_live_state(conn, telegram_id, room_key, now_ms, chat_instance, client_v)
            return None
        finally:
            conn.execute("ROLLBACK")
    finally:
        conn.close()


def live_state(telegram_id, room_key, now_ms, chat_instance=None, db_path=None, rng=None, client_v=None):
    res = live_state_read(telegram_id, room_key, now_ms, chat_instance, db_path, client_v)
    if res is not None:
        return res
    conn = _connect(db_path)
    try:
        # Медленный путь: пора продвигать раунд (открыть, закрыть, выплатить автовывод): запись под BEGIN IMMEDIATE, по одной под замком процесса
        with _WRITE_LOCK:
            # пока ждали замок, раунд мог продвинуть другой поток: тогда запись не нужна, отвечаем читающим путём
            conn.execute("BEGIN")
            try:
                if not needs_advance(conn, now_ms):
                    return _build_live_state(conn, telegram_id, room_key, now_ms, chat_instance, client_v)
            finally:
                conn.execute("ROLLBACK")
            conn.execute("BEGIN IMMEDIATE")
            try:
                _advance_round_in_tx(conn, now_ms, rng)
                res = _build_live_state(conn, telegram_id, room_key, now_ms, chat_instance, client_v)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return res
    finally:
        conn.close()
