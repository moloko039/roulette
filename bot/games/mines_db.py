"""Мины: игры, открытие клеток, кэшаут, автозакрытие брошенных (правила в mines.py)."""

import mines
import wallet
import xp

from core.kernel import _add_xp, _credit_capped
from games.round_common import (Game, CLOSE_BATCH, active_row, add_staked, close_expired, now_or_clock, read_state, run_action,
                                settle_expired)

GAME = Game("mines", mines.RequestConflict)


# Все изменения баланса идут через wallet. Раскладка мин активной игры не попадает ни в ответы, ни в лог,
# ни в response_json, ни в выгрузку данных. Баланс игрока с активной игрой не включает ставку, лежащую в игре
# (она возвращается при завершении), в рейтинге беседы это так же.
def _active_game(conn, telegram_id):
    return active_row(conn, GAME, telegram_id)


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
    now = now_or_clock(now)
    return settle_expired(lambda conn: _settle_expired_in(conn, telegram_id, now), db_path)


MINES_CLOSE_BATCH = CLOSE_BATCH


def close_expired_mines(now=None, db_path=None, batch=MINES_CLOSE_BATCH):
    """Фоновое закрытие просроченных активных игр всех игроков (не больше batch за проход). Возвращает число."""
    now = now_or_clock(now)
    return close_expired(
        GAME, "SELECT telegram_id FROM mines_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
        (now, mines.MINES_IDLE_SECONDS, batch), lambda owner: settle_expired_mines(owner, now=now, db_path=db_path), db_path,
        "Закрыто просроченных игр в мины")


def _run_mines_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия (round_common.run_action): закрытие просроченной игры, повтор по request_id, начисление, тело;
    к ответу добавляется баланс (до поля replayed)."""
    now = now_or_clock(now)
    return run_action(GAME, telegram_id, request_id, action, params, lambda conn: body(conn, now), now, db_path,
                      presettle=lambda: settle_expired_mines(telegram_id, now=now, db_path=db_path),
                      finalize=lambda conn, response: response.update(balance=wallet.get_balance(conn, telegram_id)))


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
            add_staked(conn, telegram_id, game["bet"])
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
    now = now_or_clock(now)
    settle_expired_mines(telegram_id, now=now, db_path=db_path)

    def read(conn):
        active = _active_game(conn, telegram_id)
        last = conn.execute(
            "SELECT * FROM mines_games WHERE telegram_id = ? AND status != 'active' "
            "ORDER BY finished_at DESC, id DESC LIMIT 1", (telegram_id,),
        ).fetchone()
        return {
            "game": _game_view(active) if active is not None else None,
            "last": _last_view(last) if last is not None else None,
            "balance": wallet.get_balance(conn, telegram_id),
        }

    return read_state(telegram_id, now, db_path, read)
