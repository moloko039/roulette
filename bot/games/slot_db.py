"""Western Slot: один раунд (платный спин или покупка бонуса) в одной транзакции."""

import json
import time

import slot
import wallet
import xp
from levels import profile_level
from roulette import BalanceLimit
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from core.kernel import _accrue_write, _add_xp, _register_player, _round_finished


def _slot_result(coin, bought, cost, payout, round_data, balance, xp_total, replayed):
    return {
        "coin": coin,
        "bought": bought,
        "cost": cost,
        "payout": payout,
        "round": round_data,
        "balance": balance,
        "level": profile_level(xp_total),
        "xp": xp_total,
        "replayed": replayed,
    }


def play_slot(telegram_id, request_id, coin, buy, now=None, db_path=None, rng=None):
    """Один раунд Western Slot в одной транзакции BEGIN IMMEDIATE (по образцу play_keno).

    coin из slot.COIN_VALUES, buy: покупка бонуса (списание 75 ставок) или обычный спин (одна ставка = 20 монет).
    Весь раунд (первое поле, каскады, бесплатные вращения) считается здесь и сразу: списание, затем зачисление
    всего выигрыша; клиент только проигрывает присланный раунд. rng: объект с random() (тесты).
    Повтор с тем же request_id и теми же coin и buy возвращает сохранённый раунд (balance текущий),
    с другими: slot.RequestConflict. Бросает InsufficientFunds или BalanceLimit; тогда в базе ничего не меняется.
    """
    coin = slot.validate_coin(coin)
    if type(buy) is not bool:
        raise ValueError("buy must be a bool")
    cost = slot.cost_chips(coin, buy)
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT coin, bought, cost, payout, round_json FROM slot_rounds WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["coin"] != coin or bool(old["bought"]) != buy:
                    raise slot.RequestConflict()
                cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                result = _slot_result(coin, buy, old["cost"], old["payout"], json.loads(old["round_json"]),
                                      cur["balance"] if cur else 0, cur["xp"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            wallet.debit(conn, telegram_id, cost)   # InsufficientFunds, если фишек не хватает
            if wallet.get_balance(conn, telegram_id) + slot.max_payout_chips(coin) > MAX_SAFE_INT:
                raise BalanceLimit()
            round_data = slot.play_round(slot.CONFIG, rng, buy)
            payout = round_data["totalWin"] * coin
            if payout > 0:
                wallet.credit(conn, telegram_id, payout)
            _round_finished(conn, telegram_id, "slot", cost, payout, now)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?), last_played_at = ? WHERE telegram_id = ?",
                (cost, MAX_SAFE_INT, now, telegram_id),
            )
            _add_xp(conn, telegram_id, xp.slot_xp(cost, buy))
            conn.execute(
                "INSERT INTO slot_rounds (telegram_id, request_id, coin, bought, cost, payout, round_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, coin, 1 if buy else 0, cost, payout,
                 json.dumps(round_data, separators=(",", ":")), now),
            )
            cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            result = _slot_result(coin, buy, cost, payout, round_data, cur["balance"], cur["xp"], False)
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
