"""Кено: один раунд в одной транзакции."""

import json
import time

import keno
import wallet
import xp
from levels import profile_level
from roulette import BalanceLimit
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from core.kernel import _accrue_write, _add_xp, _record_best_win, _register_player


def _keno_result(bet, picks, draw, payout, balance, xp_total, replayed):
    hits = keno.play(picks, draw)
    return {
        "bet": bet,
        "picks": list(picks),
        "draw": list(draw),
        "hits": hits,
        "hit_count": len(hits),
        "multiplier": keno.multiplier_text(keno.multiplier_x100(len(picks), len(hits))),
        "payout": payout,
        "balance": balance,
        "level": profile_level(xp_total),
        "xp": xp_total,
        "replayed": replayed,
    }


def play_keno(telegram_id, request_id, bet, picks, now=None, db_path=None, rng=None):
    """Один раунд кено в одной транзакции BEGIN IMMEDIATE (по образцу spin_roulette).

    bet и picks уже проверены (keno.validate_picks, 1 <= bet <= KENO_MAX_BET). rng: объект с sample (тесты).
    Повтор с тем же request_id и теми же bet и picks возвращает сохранённый раунд (balance текущий),
    с другими: keno.RequestConflict. Бросает InsufficientFunds или BalanceLimit; тогда в базе ничего не меняется.
    """
    if type(bet) is not int or not 1 <= bet <= keno.KENO_MAX_BET:
        raise ValueError("bet out of range")
    picks = keno.validate_picks(picks)
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT bet, picks_json, draw_json, payout FROM keno_rounds WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["bet"] != bet or json.loads(old["picks_json"]) != picks:
                    raise keno.RequestConflict()
                cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                result = _keno_result(bet, picks, json.loads(old["draw_json"]), old["payout"],
                                      cur["balance"] if cur else 0, cur["xp"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
            if wallet.get_balance(conn, telegram_id) + bet * keno.MAX_MULT_X100 // 100 > MAX_SAFE_INT:
                raise BalanceLimit()
            draw = keno.draw_numbers(rng)
            hits = keno.play(picks, draw)
            payout = keno.payout(bet, len(picks), len(hits))
            if payout > 0:
                wallet.credit(conn, telegram_id, payout)
                _record_best_win(conn, telegram_id, "keno", bet, payout, now)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (bet, MAX_SAFE_INT, telegram_id),
            )
            _add_xp(conn, telegram_id, xp.keno_xp(bet, len(picks)))
            conn.execute(
                "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, bet, json.dumps(picks, separators=(",", ":")),
                 json.dumps(draw, separators=(",", ":")), len(hits), payout, now),
            )
            cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            result = _keno_result(bet, picks, draw, payout, cur["balance"], cur["xp"], False)
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
