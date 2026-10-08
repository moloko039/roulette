"""Краш: раунды, серверное время, автозакрытие брошенных (правила в crash.py)."""

import time

import crash
import wallet
import xp

from core.db_conn import _connect
from games.round_common import (Game, CLOSE_BATCH, active_row, add_staked, close_expired, latest_row, pay_and_xp, player_view,
                                read_state, run_action, settle_expired)

GAME = Game("crash", crash.RequestConflict)


def _now_ms():
    return int(time.time() * 1000)


def _crash_active(conn, telegram_id):
    return active_row(conn, GAME, telegram_id)


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
    return _crash_view(row, now_ms, *player_view(conn, telegram_id), replayed=replayed)


def _crash_finish(conn, telegram_id, row, result, mult_x100, now, auto):
    """Единственное место окончания раунда: выплата через wallet, XP, отметка времени. Раунд закрывается один раз
    (условие status = 'active'), значит выплата и опыт тоже один раз."""
    paid = crash.payout(row["bet"], mult_x100) if result == "win" else 0
    changed = conn.execute(
        "UPDATE crash_games SET status = 'finished', result = ?, mult_x100 = ?, payout = ?, auto = ?, finished_at = ? "
        "WHERE id = ? AND status = 'active'", (result, mult_x100, paid, 1 if auto else 0, now, row["id"])).rowcount
    if changed == 0:
        return
    pay_and_xp(conn, telegram_id, paid,
               xp.crash_xp(row["bet"], crash.xp_multiplier(row["mode"], result, mult_x100, row["target_x100"])),
               "crash", row["bet"], now)


def _crash_settle_in(conn, telegram_id, now_ms):
    """Закрывает активный раунд (ручной или с автовыводом), если по серверному времени он разбился, достиг цели или предела либо брошен. True, если закрыла."""
    row = _crash_active(conn, telegram_id)
    if row is None:
        return False
    verdict = crash.settle(row["crash_x100"], row["started_at_ms"], now_ms, row["target_x100"])
    if verdict is None:
        return False
    _crash_finish(conn, telegram_id, row, verdict[0], verdict[1], now_ms // 1000, True)
    return True


def settle_expired_crash(telegram_id, now_ms=None, db_path=None):
    """Закрывает раунд игрока отдельной транзакцией (идемпотентно). Без изменений только читает. True, если закрыла."""
    if now_ms is None:
        now_ms = _now_ms()

    def due(conn):
        row = _crash_active(conn, telegram_id)
        return not (row is None or crash.settle(row["crash_x100"], row["started_at_ms"], now_ms, row["target_x100"]) is None)

    return settle_expired(lambda conn: _crash_settle_in(conn, telegram_id, now_ms), db_path, precheck=due)


CRASH_CLOSE_BATCH = CLOSE_BATCH


def close_expired_crash(now_ms=None, db_path=None, batch=CRASH_CLOSE_BATCH):
    """Фоновое закрытие брошенных раундов всех игроков (не больше batch за проход). Возвращает число."""
    if now_ms is None:
        now_ms = _now_ms()
    return close_expired(
        GAME, "SELECT telegram_id FROM crash_games WHERE status = 'active' AND started_at_ms <= ? LIMIT ?",
        (now_ms - crash.ABANDON_MS, batch), lambda owner: settle_expired_crash(owner, now_ms=now_ms, db_path=db_path), db_path,
        "Закрыто брошенных раундов краша")


def _run_crash_action(telegram_id, request_id, action, params, body, now_ms, db_path, presettle=True):
    """Общий порядок действия (round_common.run_action): закрытие просроченного раунда (кроме cashout: он закрывает сам),
    повтор по request_id, начисление, тело body(conn, now_ms, now)."""
    if now_ms is None:
        now_ms = _now_ms()
    now = now_ms // 1000
    return run_action(GAME, telegram_id, request_id, action, params, lambda conn: body(conn, now_ms, now), now, db_path,
                      presettle=(lambda: settle_expired_crash(telegram_id, now_ms=now_ms, db_path=db_path)) if presettle else None)


def crash_start(telegram_id, request_id, bet, target_x100=None, now_ms=None, db_path=None, rng=None):
    """Новый раунд: нет активного, ставка списывается через wallet и идёт в total_staked, точка краха выбирается и прячется.
    Режим авто (задан target_x100) и ручной ведут себя одинаково: раунд активен, множитель растёт по времени сервера; с целью раунд сам закроется
    на цели или на крахе, а игрок может вывести вручную в любой момент (выплата не выше цели)."""
    if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
        raise ValueError("bet out of range")
    if target_x100 is not None and (type(target_x100) is not int
                                    or not crash.MIN_TARGET_X100 <= target_x100 <= crash.CAP_X100):
        raise ValueError("target out of range")

    def body(conn, now_ms_, now):
        if _crash_active(conn, telegram_id) is not None:
            raise crash.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        add_staked(conn, telegram_id, bet, now)
        crash_x100 = crash.new_crash(rng)
        mode = "manual" if target_x100 is None else "auto"
        cur = conn.execute(
            "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
            (telegram_id, bet, mode, target_x100, crash_x100, now_ms_, now))
        game_id = cur.lastrowid
        return _crash_response(conn, telegram_id, game_id, now_ms_)

    return _run_crash_action(telegram_id, request_id, "start", {"bet": bet, "target_x100": target_x100}, body, now_ms, db_path)


def crash_cashout(telegram_id, request_id, now_ms=None, db_path=None):
    """Вывод на текущем множителе (и в ручном раунде, и в раунде с автовыводом). Если раунд к этому моменту уже разбился, достиг цели (выплата по
    цели, не выше) или предела, отвечает итогом раунда. TooEarly (множитель меньше 1.01): раунд остаётся активным."""
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
            return _crash_view(row, now_ms, *player_view(conn, telegram_id))
    finally:
        conn.close()

    def read(conn):   # баланс с начислением, как /api/me
        row = latest_row(conn, GAME, telegram_id)
        balance, level, xp_total = player_view(conn, telegram_id)
        return (_crash_none_view(balance, level, xp_total) if row is None
                else _crash_view(row, now_ms, balance, level, xp_total))

    return read_state(telegram_id, now_ms // 1000, db_path, read)
