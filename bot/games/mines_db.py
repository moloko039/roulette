"""Мины: игры, открытие клеток, кэшаут, автозакрытие брошенных (правила в mines.py)."""

import json
import time

import mines
import wallet
import xp
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _add_xp, _credit_capped, _register_player


# Все изменения баланса идут через wallet. Раскладка мин активной игры не попадает ни в ответы, ни в лог,
# ни в response_json, ни в выгрузку данных. Баланс игрока с активной игрой не включает ставку, лежащую в игре
# (она возвращается при завершении), в рейтинге беседы это так же.
def _active_game(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM mines_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _game_view(row):
    """Активная игра для клиента. Раскладки мин здесь нет."""
    m = row["mines_count"]
    k = mines.popcount(row["revealed_mask"])
    left = mines.FIELD_CELLS - m - k
    return {
        "bet": row["bet"],
        "mines": m,
        "revealed": mines.cells_of(row["revealed_mask"]),
        "safe_left": left,
        "multiplier": mines.multiplier_text(m, k),
        "payout_now": mines.payout(row["bet"], m, k),
        "next_multiplier": mines.multiplier_text(m, k + 1) if left > 0 else None,
        "next_payout": mines.payout(row["bet"], m, k + 1) if left > 0 else None,
        "expires_at": row["updated_at"] + mines.MINES_IDLE_SECONDS,
    }


def _last_view(row):
    """Завершённая игра: раскладка мин раскрывается только здесь."""
    return {
        "status": row["status"],
        "bet": row["bet"],
        "mines": row["mines_count"],
        "revealed": mines.cells_of(row["revealed_mask"]),
        "mine_cells": mines.cells_of(row["mine_mask"]),
        "payout": row["payout"],
        "finished_at": row["finished_at"],
    }


def _finish_game(conn, game_id, status, payout, now):
    """Закрывает активную игру и начисляет опыт: единственное место для всех путей закрытия (мина, очистка поля,
    cashout, автозакрытие). Игра закрывается один раз (условие status = 'active'), значит и опыт один раз.
    Возврат ставки (refunded, auto_refunded) опыта не даёт."""
    game = conn.execute(
        "SELECT telegram_id, bet, mines_count, revealed_mask FROM mines_games WHERE id = ? AND status = 'active'",
        (game_id,),
    ).fetchone()
    if game is None:
        return
    conn.execute(
        "UPDATE mines_games SET status = ?, payout = ?, finished_at = ?, updated_at = ? WHERE id = ?",
        (status, payout, now, now, game_id),
    )
    if status in ("lost", "cashed", "auto_cashed"):
        _add_xp(conn, game["telegram_id"],
                xp.mines_xp(game["bet"], game["mines_count"], mines.popcount(game["revealed_mask"]), status == "lost"))


def _settle_expired_in(conn, telegram_id, now):
    """Закрывает просроченную активную игру игрока (внутри открытой транзакции). True, если закрыла."""
    game = _active_game(conn, telegram_id)
    if game is None or now - game["updated_at"] < mines.MINES_IDLE_SECONDS:
        return False
    k = mines.popcount(game["revealed_mask"])
    if k == 0:
        # автоматический возврат отличается от ручного (cashout при k = 0 остаётся «refunded»)
        _finish_game(conn, game["id"], "auto_refunded", _credit_capped(conn, telegram_id, game["bet"]), now)
    else:
        owed = mines.payout(game["bet"], game["mines_count"], k)
        _finish_game(conn, game["id"], "auto_cashed", _credit_capped(conn, telegram_id, owed), now)
    return True


def settle_expired_mines(telegram_id, now=None, db_path=None):
    """Закрывает просроченную игру игрока отдельной транзакцией (идемпотентно). True, если закрыла."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _settle_expired_in(conn, telegram_id, now)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


MINES_CLOSE_BATCH = 200


def close_expired_mines(now=None, db_path=None, batch=MINES_CLOSE_BATCH):
    """Фоновое закрытие просроченных активных игр всех игроков (не больше batch за проход). Возвращает число."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mines_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM mines_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
            (now, mines.MINES_IDLE_SECONDS, batch),
        )]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_mines(owner, now=now, db_path=db_path))
    if closed:
        logger.info("Закрыто просроченных игр в мины: %d", closed)  # только количество
    return closed


def _run_mines_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия: закрытие просроченной игры, повтор по request_id, минутное начисление, тело
    действия, запись ответа. Один request_id с другим действием или параметрами даёт RequestConflict."""
    if now is None:
        now = int(time.time())
    settle_expired_mines(telegram_id, now=now, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM mines_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise mines.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now)
            response["balance"] = wallet.get_balance(conn, telegram_id)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO mines_actions (telegram_id, request_id, action, params, response_json, created_at) "
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


def mines_start(telegram_id, request_id, bet, mines_count, now=None, db_path=None, rng=None):
    """Старт игры: нет активной игры, списание ставки через wallet, раскладка мин. Ставка в total_staked
    на этом шаге не засчитывается."""
    if type(bet) is not int or not 1 <= bet <= mines.MINES_MAX_BET:
        raise ValueError("bet out of range")
    if type(mines_count) is not int or not mines.MINES_MIN_COUNT <= mines_count <= mines.MINES_MAX_COUNT:
        raise ValueError("mines out of range")

    def body(conn, now_):
        if _active_game(conn, telegram_id) is not None:
            raise mines.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # wallet.InsufficientFunds, если фишек не хватает
        conn.execute(
            "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, payout, "
            "staked_counted, created_at, updated_at) VALUES (?, ?, ?, ?, 0, 'active', 0, 0, ?, ?)",
            (telegram_id, bet, mines_count, mines.new_layout(mines_count, rng), now_, now_),
        )
        return {"game": _game_view(_active_game(conn, telegram_id))}

    return _run_mines_action(telegram_id, request_id, "start", {"bet": bet, "mines": mines_count}, body, now, db_path)


def mines_reveal(telegram_id, request_id, cell, now=None, db_path=None):
    """Открытие клетки. При первом открытии ставка добавляется к total_staked (один раз за игру)."""
    if type(cell) is not int or not 0 <= cell < mines.FIELD_CELLS:
        raise ValueError("cell out of range")

    def body(conn, now_):
        game = _active_game(conn, telegram_id)
        if game is None:
            raise mines.NoActiveGame()
        bit = 1 << cell
        if game["revealed_mask"] & bit:
            raise mines.AlreadyRevealed()
        if not game["staked_counted"]:
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (game["bet"], MAX_SAFE_INT, telegram_id),
            )
            conn.execute("UPDATE mines_games SET staked_counted = 1 WHERE id = ?", (game["id"],))
        if game["mine_mask"] & bit:
            _finish_game(conn, game["id"], "lost", 0, now_)
            last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
            return {"result": "mine", "game": None, "last": _last_view(last)}
        revealed = game["revealed_mask"] | bit
        k = mines.popcount(revealed)
        conn.execute("UPDATE mines_games SET revealed_mask = ?, updated_at = ? WHERE id = ?", (revealed, now_, game["id"]))
        if k == mines.FIELD_CELLS - game["mines_count"]:
            owed = mines.payout(game["bet"], game["mines_count"], k)
            _finish_game(conn, game["id"], "cashed", _credit_capped(conn, telegram_id, owed), now_)
            last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
            return {"result": "cleared", "game": None, "last": _last_view(last)}
        return {"result": "safe", "game": _game_view(_active_game(conn, telegram_id))}

    return _run_mines_action(telegram_id, request_id, "reveal", {"cell": cell}, body, now, db_path)


def mines_cashout(telegram_id, request_id, now=None, db_path=None):
    """Забрать выигрыш: при нуле открытых клеток возвращается ставка (refunded, в total_staked не идёт)."""
    def body(conn, now_):
        game = _active_game(conn, telegram_id)
        if game is None:
            raise mines.NoActiveGame()
        k = mines.popcount(game["revealed_mask"])
        if k == 0:
            _finish_game(conn, game["id"], "refunded", _credit_capped(conn, telegram_id, game["bet"]), now_)
        else:
            owed = mines.payout(game["bet"], game["mines_count"], k)
            _finish_game(conn, game["id"], "cashed", _credit_capped(conn, telegram_id, owed), now_)
        last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
        return {"last": _last_view(last)}

    return _run_mines_action(telegram_id, request_id, "cashout", {}, body, now, db_path)


def mines_state(telegram_id, now=None, db_path=None):
    """Активная игра или None, последняя завершённая или None, баланс (с начислением, как /api/me)."""
    if now is None:
        now = int(time.time())
    settle_expired_mines(telegram_id, now=now, db_path=db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            active = _active_game(conn, telegram_id)
            last = conn.execute(
                "SELECT * FROM mines_games WHERE telegram_id = ? AND status != 'active' "
                "ORDER BY finished_at DESC, id DESC LIMIT 1", (telegram_id,),
            ).fetchone()
            result = {
                "game": _game_view(active) if active is not None else None,
                "last": _last_view(last) if last is not None else None,
                "balance": wallet.get_balance(conn, telegram_id),
            }
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
