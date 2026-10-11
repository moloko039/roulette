"""Забег свинок: расчёт раунда и запись в транзакции BEGIN IMMEDIATE."""
import json
import secrets
import time

import core.kernel as kernel
# Регистрируем race в списке кодов игр в рекордах kernel
if "race" not in kernel.BEST_WIN_GAMES:
    kernel.BEST_WIN_GAMES = kernel.BEST_WIN_GAMES + ("race",)

from core.db_conn import _connect
from core.kernel import _accrue_write, _add_xp, _register_player, _round_finished
import race
from roulette import BalanceLimit, InsufficientFunds, MAX_SAFE_INT, validate_request_id
import wallet


def play_race(telegram_id, request_id, lanes, lane, bet, now=None, db_path=None, winner=None, seed=None):
    """Один раунд «Забег свинок» в одной транзакции BEGIN IMMEDIATE (по образцу play_slot).

    lanes: 3 или 4, lane: выбранная дорожка (0..lanes-1), bet: ставка от 1 до 1 000 000 фишек.
    request_id: идентификатор запроса для защиты от повторов и обеспечения идемпотентности.
    winner: победившая дорожка (если None, выбирается случайно через secrets.randbelow).
    seed: сид для скрипта анимации (если None, генерируется случайно через secrets.randbits).
    Повтор с тем же request_id и теми же параметрами возвращает сохранённый раунд (с актуальным балансом).
    Повтор с другими параметрами вызывает race.RequestConflict (409).
    """
    request_id = validate_request_id(request_id)
    lanes = race.validate_lanes(lanes)
    lane = race.validate_lane(lane, lanes)
    bet = race.validate_bet(bet)

    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT id, lanes, lane, bet, winner, payout, script_json FROM race_rounds "
                "WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["lanes"] != lanes or old["lane"] != lane or old["bet"] != bet:
                    raise race.RequestConflict()
                cur = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                balance = cur["balance"] if cur else 0
                conn.execute("COMMIT")
                return {
                    "round_id": old["id"],
                    "lanes": old["lanes"],
                    "lane": old["lane"],
                    "winner": old["winner"],
                    "payout": old["payout"],
                    "balance": balance,
                    "script": json.loads(old["script_json"]),
                }

            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            wallet.debit(conn, telegram_id, bet)  # InsufficientFunds, если фишек не хватает

            if wallet.get_balance(conn, telegram_id) + race.max_payout(bet, lanes) > MAX_SAFE_INT:
                raise BalanceLimit()

            if winner is None:
                winner = secrets.randbelow(lanes)
            else:
                winner = race.validate_lane(winner, lanes)

            if seed is None:
                seed = secrets.randbits(63)

            script = race.generate_script(seed, winner, lanes)
            payout = race.payout(bet, lanes, lane == winner)
            if payout > 0:
                wallet.credit(conn, telegram_id, payout)

            _round_finished(conn, telegram_id, "race", bet, payout, now)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?), last_played_at = ? WHERE telegram_id = ?",
                (bet, MAX_SAFE_INT, now, telegram_id),
            )
            _add_xp(conn, telegram_id, race.race_xp(bet, lanes))

            cur = conn.execute(
                "INSERT INTO race_rounds (telegram_id, request_id, lanes, lane, bet, winner, payout, script_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, lanes, lane, bet, winner, payout,
                 json.dumps(script, separators=(",", ":")), now),
            )
            round_id = cur.lastrowid

            cur_bal = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            balance = cur_bal["balance"] if cur_bal else 0
            conn.execute("COMMIT")
            return {
                "round_id": round_id,
                "lanes": lanes,
                "lane": lane,
                "winner": winner,
                "payout": payout,
                "balance": balance,
                "script": script,
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
