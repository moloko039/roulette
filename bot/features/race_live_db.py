"""Общий забег свинок: база и деньги. Раунд один на весь сервер (номер = окно времени, см. race_live.py), комната беседы определяет только ленту ставок.

Ставка списывается при подаче (BEGIN IMMEDIATE, повтор по (игрок, request_id)), победитель выбирается при закрытии приёма: первым же запросом после закрытия
(secrets.randbelow, без возможности подсмотреть до закрытия), тогда же выплачиваются все ставки раунда одной транзакцией."""
import json
import secrets

import economy_config
import features.race_db  # noqa: F401  (регистрирует игру race в рекордах выигрыша)
import race
import race_live
import wallet
from core.db_conn import _connect
from core.kernel import _accrue_write, _add_xp, _register_player, _round_finished
from roulette import MAX_SAFE_INT, BalanceLimit, validate_request_id


def _settle_round(conn, rid, now_ms, winner=None, seed=None):
    """Записывает итог раунда rid и выплачивает его ставки (внутри открытой транзакции). Повтор ничего не делает."""
    if conn.execute("SELECT 1 FROM race_live_rounds WHERE id = ?", (rid,)).fetchone() is not None:
        return
    if winner is None:
        winner = secrets.randbelow(race_live.LANES)
    if seed is None:
        seed = secrets.randbits(63)
    conn.execute("INSERT INTO race_live_rounds (id, winner, seed, settled_at_ms) VALUES (?, ?, ?, ?)", (rid, winner, seed, now_ms))
    now = now_ms // 1000
    for b in conn.execute("SELECT telegram_id, lane, bet FROM race_live_bets WHERE round_id = ? ORDER BY created_at_ms, telegram_id", (rid,)).fetchall():
        tid = b["telegram_id"]
        pay = race.payout(b["bet"], race_live.LANES, b["lane"] == winner)
        paid = 0
        if pay > 0:
            room = MAX_SAFE_INT - wallet.get_balance(conn, tid)
            paid = min(pay, max(0, room))
            if paid > 0:
                wallet.credit(conn, tid, paid)
        conn.execute("UPDATE race_live_bets SET payout = ? WHERE round_id = ? AND telegram_id = ?", (paid, rid, tid))
        _round_finished(conn, tid, "race", b["bet"], paid, now)
        conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?), last_played_at = ? WHERE telegram_id = ?", (b["bet"], MAX_SAFE_INT, now, tid))
        _add_xp(conn, tid, race.race_xp(b["bet"], race_live.LANES))


def _unsettled(conn, now_ms):
    """Раунды, которым пора подвести итог: текущий после закрытия приёма и прежние со ставками без выплаты."""
    out = set()
    rid = race_live.round_id(now_ms)
    if race_live.phase_of(rid, now_ms) != "bets" and conn.execute("SELECT 1 FROM race_live_rounds WHERE id = ?", (rid,)).fetchone() is None:
        out.add(rid)
    for r in conn.execute("SELECT DISTINCT round_id FROM race_live_bets WHERE payout IS NULL").fetchall():
        if now_ms >= race_live.close_ms(r["round_id"]):
            out.add(r["round_id"])
    return sorted(out)


def place_bet(telegram_id, room_key, request_id, lane, bet, now_ms, db_path=None):
    """Ставка на дорожку в текущем раунде (только пока идёт приём). Ошибки: BettingClosed, AlreadyBet, RoundFull, RequestConflict (тот же request_id
    с другими lane или bet), InsufficientFunds, BalanceLimit. Повтор того же запроса отдаёт сохранённую ставку."""
    request_id = validate_request_id(request_id)
    race.validate_lane(lane, race_live.LANES)
    race.validate_bet(bet)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for rid in _unsettled(conn, now_ms):
                _settle_round(conn, rid, now_ms)
            old = conn.execute("SELECT round_id, lane, bet FROM race_live_bets WHERE telegram_id = ? AND request_id = ?", (telegram_id, request_id)).fetchone()
            if old is not None:
                if old["lane"] != lane or old["bet"] != bet:
                    raise race.RequestConflict()
                res = {"round_id": old["round_id"], "lane": lane, "bet": bet, "balance": wallet.get_balance(conn, telegram_id), "replayed": True}
                conn.execute("COMMIT")
                return res
            rid = race_live.round_id(now_ms)
            if race_live.phase_of(rid, now_ms) != "bets":
                raise race_live.BettingClosed()
            if conn.execute("SELECT 1 FROM race_live_bets WHERE round_id = ? AND telegram_id = ?", (rid, telegram_id)).fetchone() is not None:
                raise race_live.AlreadyBet()
            if conn.execute("SELECT COUNT(*) FROM race_live_bets WHERE round_id = ?", (rid,)).fetchone()[0] >= economy_config.RACE_LIVE_ROUND_BETS_MAX:
                raise race_live.RoundFull()
            _register_player(conn, telegram_id, now_ms // 1000)
            _accrue_write(conn, telegram_id, now_ms // 1000)
            wallet.debit(conn, telegram_id, bet)
            if wallet.get_balance(conn, telegram_id) + race.max_payout(bet, race_live.LANES) > MAX_SAFE_INT:
                raise BalanceLimit()
            conn.execute("INSERT INTO race_live_bets (round_id, telegram_id, room_key, lane, bet, payout, request_id, created_at_ms) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                         (rid, telegram_id, room_key, lane, bet, request_id, now_ms))
            res = {"round_id": rid, "lane": lane, "bet": bet, "balance": wallet.get_balance(conn, telegram_id), "replayed": False}
            conn.execute("COMMIT")
            return res
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _state(conn, telegram_id, room_key, now_ms, chat_instance):
    rid = race_live.round_id(now_ms)
    phase = race_live.phase_of(rid, now_ms)
    res = {"server_ms": now_ms, "lanes": race_live.LANES,
           "round": {"id": rid, "phase": phase, "start_ms": race_live.start_ms(rid), "close_ms": race_live.close_ms(rid),
                     "race_end_ms": race_live.close_ms(rid) + race_live.RACE_MS, "next_ms": race_live.start_ms(rid + 1)},
           "bets": [], "counts": [0] * race_live.LANES, "me": None, "result": None, "last": None}
    anonymous = room_key == race_live.PUBLIC_ROOM_KEY
    names = {}
    if chat_instance and not anonymous:
        names = {r["telegram_id"]: r["first_name"] for r in conn.execute("SELECT telegram_id, first_name FROM chat_members WHERE chat_instance = ?", (chat_instance,)).fetchall()}
    for r in conn.execute("SELECT lane, COUNT(*) AS n FROM race_live_bets WHERE round_id = ? AND room_key = ? GROUP BY lane", (rid, room_key)):
        res["counts"][r["lane"]] = r["n"]
    feed = conn.execute("SELECT telegram_id, lane, bet, payout FROM race_live_bets WHERE round_id = ? AND room_key = ? ORDER BY created_at_ms DESC, telegram_id DESC LIMIT ?",
                        (rid, room_key, economy_config.RACE_LIVE_FEED_MAX)).fetchall()
    for b in reversed(feed):
        res["bets"].append({"name": "Игрок" if anonymous else (names.get(b["telegram_id"]) or "Игрок"), "lane": b["lane"], "bet": b["bet"], "payout": b["payout"], "me": b["telegram_id"] == telegram_id})
    mine = conn.execute("SELECT lane, bet, payout FROM race_live_bets WHERE round_id = ? AND telegram_id = ?", (rid, telegram_id)).fetchone()
    if mine is not None:
        res["me"] = {"lane": mine["lane"], "bet": mine["bet"], "payout": mine["payout"]}
    done = conn.execute("SELECT winner, seed FROM race_live_rounds WHERE id = ?", (rid,)).fetchone()
    if done is not None and phase != "bets":
        res["result"] = {"winner": done["winner"], "script": race.generate_script(done["seed"], done["winner"], race_live.LANES)}
    # прошлый раунд: чтобы вернувшийся в игру увидел, чем закончилась его ставка
    prev = conn.execute("SELECT b.round_id, b.lane, b.bet, b.payout, r.winner FROM race_live_bets b JOIN race_live_rounds r ON r.id = b.round_id "
                        "WHERE b.telegram_id = ? AND b.round_id < ? ORDER BY b.round_id DESC LIMIT 1", (telegram_id, rid)).fetchone()
    if prev is not None and prev["round_id"] >= rid - 2:
        res["last"] = {"round_id": prev["round_id"], "lane": prev["lane"], "bet": prev["bet"], "payout": prev["payout"], "winner": prev["winner"]}
    res["balance"] = wallet.get_balance(conn, telegram_id) if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() else None
    return res


def live_state(telegram_id, room_key, now_ms, chat_instance=None, db_path=None):
    """Состояние общего забега для игрока. Итоги раундов подводятся здесь же (первым запросом после закрытия приёма)."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        try:
            need = bool(_unsettled(conn, now_ms))
            if not need:
                res = _state(conn, telegram_id, room_key, now_ms, chat_instance)
                conn.execute("COMMIT")
                return res
            conn.execute("ROLLBACK")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("BEGIN IMMEDIATE")
        try:
            for rid in _unsettled(conn, now_ms):
                _settle_round(conn, rid, now_ms)
            res = _state(conn, telegram_id, room_key, now_ms, chat_instance)
            conn.execute("COMMIT")
            return res
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
