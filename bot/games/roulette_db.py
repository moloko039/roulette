"""Рулетка: один спин в одной транзакции."""

import json
import secrets
import time

import wallet
import xp
import roulette
from roulette import BalanceLimit
from roulette import MAX_SAFE_INT
from roulette import max_payout
from roulette import settle

from core.db_conn import _connect
from core.kernel import _accrue_conn, _add_xp, _record_best_win, _register_player


def _round_result(number, stake_total, payout_total, balance, replayed):
    return {
        "number": number,
        "stake_total": stake_total,
        "payout_total": payout_total,
        "net": payout_total - stake_total,
        "balance": balance,
        "replayed": replayed,
    }


def spin_roulette(telegram_id, request_id, bets, now=None, db_path=None, rng=None):
    """Один спин рулетки в одной транзакции.

    bets уже проверены validate_bets(). rng(n) возвращает число 0..n-1 (по умолчанию
    secrets.randbelow); параметр нужен, чтобы тесты подставляли числа.
    Бросает InsufficientFunds, BalanceLimit или RequestConflict (тот же request_id с другими ставками); тогда в базе ничего не меняется.
    """
    if now is None:
        now = int(time.time())
    if rng is None:
        rng = secrets.randbelow

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # (а) повторный запрос: отдаём сохранённый результат, ничего не меняем.
            # balance в ответе — текущий баланс игрока, а не баланс на момент раунда
            old = conn.execute(
                "SELECT number, stake_total, payout_total, bets_json FROM roulette_rounds "
                "WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                try:
                    same = roulette.bets_fingerprint(json.loads(old["bets_json"])) == roulette.bets_fingerprint(bets)
                except (ValueError, TypeError):
                    same = False
                if not same:
                    raise roulette.RequestConflict()
                cur = conn.execute(
                    "SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
                result = _round_result(old["number"], old["stake_total"], old["payout_total"],
                                       cur["balance"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            # (б) игрок и начисление: потратить можно и только что начисленное.
            # Минутное начисление (_accrue_conn) пишется сразу (при ошибке ниже транзакция откатится целиком)
            _register_player(conn, telegram_id, now)
            _accrue_conn(conn, telegram_id, now)

            # (в) списание ставки через кошелёк (InsufficientFunds, если фишек не хватает) и проверка,
            # не упрётся ли баланс в предел точных чисел JavaScript при самой большой выплате
            stake_total = sum(b["amount"] for b in bets)
            wallet.debit(conn, telegram_id, stake_total)
            if wallet.get_balance(conn, telegram_id) + max_payout(bets) > MAX_SAFE_INT:
                raise BalanceLimit()

            # (г) число, (д) выигрыш
            number = rng(37)
            if type(number) is not int or not 0 <= number <= 36:
                raise RuntimeError("bad random number")
            stake_total, payout_total = settle(bets, number)

            # (е) выплата через кошелёк, запись раунда и счётчик ставок (в той же транзакции)
            if payout_total > 0:
                wallet.credit(conn, telegram_id, payout_total)
                _record_best_win(conn, telegram_id, "roulette", stake_total, payout_total, now)
            new_balance = wallet.get_balance(conn, telegram_id)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (stake_total, MAX_SAFE_INT, telegram_id),
            )
            _add_xp(conn, telegram_id, xp.roulette_xp(stake_total, bets))
            conn.execute(
                "INSERT INTO roulette_rounds "
                "(telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, number, stake_total, payout_total,
                 json.dumps(bets, separators=(",", ":")), now),
            )
            # (ж)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return _round_result(number, stake_total, payout_total, new_balance, False)
