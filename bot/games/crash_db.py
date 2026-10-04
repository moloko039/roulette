"""Краш: раунды, серверное время, автозакрытие брошенных (правила в crash.py)."""

import json
import time

import crash
import wallet
import xp
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _add_xp, _credit_capped, _register_player


def _now_ms():
    return int(time.time() * 1000)


def _crash_active(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM crash_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _crash_view(row, now_ms, balance, level, xp_total, replayed=False):
    """Ответ API (одна форма). Пока раунд идёт, точки краха в ответе нет; elapsed_ms по эффективному времени."""
    active = row["status"] == "active"
    return {
        "status": row["status"],
        "mode": row["mode"],
        "bet": row["bet"],
        "target": crash.text(row["target_x100"]) if row["target_x100"] is not None else None,
        "elapsed_ms": crash.effective_ms(now_ms, row["started_at_ms"]) if active else None,
        "doubling_ms": crash.DOUBLING_MS,
        "cap": crash.text(crash.CAP_X100),
        "crash_multiplier": None if active else crash.text(row["crash_x100"]),
        "result": row["result"],
        "multiplier": None if active else crash.text(row["mult_x100"]),
        "payout": row["payout"],
        "balance": balance,
        "level": level,
        "xp": xp_total,
        "auto": bool(row["auto"]),
        "replayed": replayed,
    }


def _crash_none_view(balance, level, xp_total):
    return {"status": "none", "mode": None, "bet": None, "target": None, "elapsed_ms": None, "doubling_ms": crash.DOUBLING_MS,
            "cap": crash.text(crash.CAP_X100), "crash_multiplier": None, "result": None, "multiplier": None, "payout": None,
            "balance": balance, "level": level, "xp": xp_total, "auto": False, "replayed": False}


def _crash_response(conn, telegram_id, game_id, now_ms, replayed=False):
    row = conn.execute("SELECT * FROM crash_games WHERE id = ?", (game_id,)).fetchone()
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return _crash_view(row, now_ms, pl["balance"], profile_level(pl["xp"]), pl["xp"], replayed)


def _crash_finish(conn, telegram_id, row, result, mult_x100, now, auto):
    """Единственное место окончания раунда: выплата через wallet, XP, отметка времени. Раунд закрывается один раз
    (условие status = 'active'), значит выплата и опыт тоже один раз."""
    paid = crash.payout(row["bet"], mult_x100) if result == "win" else 0
    changed = conn.execute(
        "UPDATE crash_games SET status = 'finished', result = ?, mult_x100 = ?, payout = ?, auto = ?, finished_at = ? "
        "WHERE id = ? AND status = 'active'", (result, mult_x100, paid, 1 if auto else 0, now, row["id"])).rowcount
    if changed == 0:
        return
    if paid > 0:
        _credit_capped(conn, telegram_id, paid)
    _add_xp(conn, telegram_id, xp.crash_xp(row["bet"], crash.xp_multiplier(row["mode"], result, mult_x100, row["target_x100"])))


def _crash_settle_in(conn, telegram_id, now_ms):
    """Закрывает активный ручной раунд, если он разбился по времени, достиг предела или брошен. Возвращает True, если закрыла."""
    row = _crash_active(conn, telegram_id)
    if row is None:
        return False
    verdict = crash.settle(row["crash_x100"], row["started_at_ms"], now_ms)
    if verdict is None:
        return False
    _crash_finish(conn, telegram_id, row, verdict[0], verdict[1], now_ms // 1000, True)
    return True


def settle_expired_crash(telegram_id, now_ms=None, db_path=None):
    """Закрывает раунд игрока отдельной транзакцией (идемпотентно). Без изменений только читает. True, если закрыла."""
    if now_ms is None:
        now_ms = _now_ms()
    conn = _connect(db_path)
    try:
        row = _crash_active(conn, telegram_id)
        if row is None or crash.settle(row["crash_x100"], row["started_at_ms"], now_ms) is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _crash_settle_in(conn, telegram_id, now_ms)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


CRASH_CLOSE_BATCH = 200


def close_expired_crash(now_ms=None, db_path=None, batch=CRASH_CLOSE_BATCH):
    """Фоновое закрытие брошенных раундов всех игроков (не больше batch за проход). Возвращает число."""
    if now_ms is None:
        now_ms = _now_ms()
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'crash_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM crash_games WHERE status = 'active' AND started_at_ms <= ? LIMIT ?",
            (now_ms - crash.ABANDON_MS, batch))]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_crash(owner, now_ms=now_ms, db_path=db_path))
    if closed:
        logger.info("Закрыто брошенных раундов краша: %d", closed)  # только количество
    return closed


def _run_crash_action(telegram_id, request_id, action, params, body, now_ms, db_path, presettle=True):
    """Общий порядок действия: закрытие просроченного раунда (кроме cashout: он закрывает сам), повтор по request_id,
    минутное начисление, тело действия, запись ответа. Один request_id с другими параметрами: RequestConflict."""
    if now_ms is None:
        now_ms = _now_ms()
    now = now_ms // 1000
    if presettle:
        settle_expired_crash(telegram_id, now_ms=now_ms, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM crash_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise crash.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now_ms, now)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO crash_actions (telegram_id, request_id, action, params, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now),
            )
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def crash_start(telegram_id, request_id, bet, target_x100=None, now_ms=None, db_path=None, rng=None):
    """Новый раунд: нет активного, ставка списывается через wallet и идёт в total_staked, точка краха выбирается и прячется.
    Режим авто (задан target_x100): раунд решается сразу. Ручной: раунд активен, множитель растёт по времени сервера."""
    if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
        raise ValueError("bet out of range")
    if target_x100 is not None and (type(target_x100) is not int
                                    or not crash.MIN_TARGET_X100 <= target_x100 <= crash.CAP_X100):
        raise ValueError("target out of range")

    def body(conn, now_ms_, now):
        if _crash_active(conn, telegram_id) is not None:
            raise crash.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                     (bet, MAX_SAFE_INT, telegram_id))
        crash_x100 = crash.new_crash(rng)
        mode = "manual" if target_x100 is None else "auto"
        cur = conn.execute(
            "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
            (telegram_id, bet, mode, target_x100, crash_x100, now_ms_, now))
        game_id = cur.lastrowid
        if mode == "auto":
            result, mult = crash.decide_auto(target_x100, crash_x100)
            _crash_finish(conn, telegram_id, conn.execute("SELECT * FROM crash_games WHERE id = ?", (game_id,)).fetchone(),
                          result, mult, now, False)
        return _crash_response(conn, telegram_id, game_id, now_ms_)

    return _run_crash_action(telegram_id, request_id, "start", {"bet": bet, "target_x100": target_x100}, body, now_ms, db_path)


def crash_cashout(telegram_id, request_id, now_ms=None, db_path=None):
    """Вывод на текущем множителе. Если раунд к этому моменту уже разбился (или достиг предела), отвечает итогом раунда.
    TooEarly (множитель меньше 1.01): раунд остаётся активным."""
    def body(conn, now_ms_, now):
        row = _crash_active(conn, telegram_id)
        if row is None:
            raise crash.NoActiveGame()
        if _crash_settle_in(conn, telegram_id, now_ms_):
            return _crash_response(conn, telegram_id, row["id"], now_ms_)
        c = crash.cashout_multiplier(row["started_at_ms"], now_ms_)   # TooEarly: ничего не меняется (откат)
        _crash_finish(conn, telegram_id, row, "win", c, now, False)
        return _crash_response(conn, telegram_id, row["id"], now_ms_)

    return _run_crash_action(telegram_id, request_id, "cashout", {}, body, now_ms, db_path, presettle=False)


def crash_state(telegram_id, now_ms=None, db_path=None):
    """Активный раунд, иначе последний завершённый, иначе status none. Во время полёта только читает (без начисления)."""
    if now_ms is None:
        now_ms = _now_ms()
    settle_expired_crash(telegram_id, now_ms=now_ms, db_path=db_path)
    conn = _connect(db_path)
    try:
        row = _crash_active(conn, telegram_id)
        if row is not None:
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return _crash_view(row, now_ms, pl["balance"], profile_level(pl["xp"]), pl["xp"])
    finally:
        conn.close()
    now = now_ms // 1000
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)   # баланс с начислением, как /api/me
            row = conn.execute(
                "SELECT * FROM crash_games WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1",
                (telegram_id,)).fetchone()
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            level = profile_level(pl["xp"])
            result = _crash_none_view(pl["balance"], level, pl["xp"]) if row is None else _crash_view(
                row, now_ms, pl["balance"], level, pl["xp"])
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
