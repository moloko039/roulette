"""Bloody slot: партия в базе (правила в bloody_round.py, числа в bloody_config.py). Один раунд = стартовый спин и бонусные спины; ставка списывается при старте,
выигрыш накапливается в состоянии раунда и выплачивается один раз при его конце (или через 7 дней без действий: автозакрытие с выплатой накопленного).
Состояние раунда и последний спин хранятся в bloody_games: после перезагрузки Mini App игрок продолжает с того же места."""

import json

import bloody_config as cfg
import bloody_round as br
import wallet
import xp
from roulette import MAX_SAFE_INT, BalanceLimit

from games.round_common import (Game, CLOSE_BATCH, active_row, add_staked, close_expired, latest_row, now_or_clock, pay_and_xp, player_view, read_state,
                                run_action, settle_expired)

GAME = Game("bloody", br.RequestConflict)


def _active(conn, telegram_id):
    return active_row(conn, GAME, telegram_id)


def validate_bet(bet):
    if type(bet) is not int or bet not in cfg.BET_LADDER:
        raise br.BadBet()
    return bet


def _view(row, balance, level, xp_total, replayed=False):
    """Ответ API (одна форма). Состояние раунда и последний спин (для показа конечного положения после перезагрузки)."""
    rnd = br.Round.from_json(row["state_json"])
    active = row["status"] == "active"
    return {
        "status": row["status"],
        "bet": row["bet"],
        "cost": row["cost"],
        "buy": row["buy"],
        "round": {"spins": rnd.spins, "units": rnd.units, "win": br.payout_chips(rnd.units, row["bet"]), "next": rnd.next_mode(), "candle_left": rnd.candle_left,
                  "reign_left": rnd.reign_left, "bar": rnd.bar.to_dict(), "sticky": rnd.sticky, "capped": rnd.capped,
                  "expires_at": row["updated_at"] + cfg.IDLE_SECONDS if active else None},
        "spin": json.loads(row["last_json"]) if row["last_json"] else None,
        "payout": None if active else row["payout"],
        "max_win_bets": cfg.MAX_WIN_BETS,
        "balance": balance, "level": level, "xp": xp_total, "replayed": replayed,
    }


def _none_view(balance, level, xp_total):
    return {"status": "none", "bet": None, "cost": None, "buy": None, "round": None, "spin": None, "payout": None, "max_win_bets": cfg.MAX_WIN_BETS,
            "balance": balance, "level": level, "xp": xp_total, "replayed": False}


def _response(conn, telegram_id, game_id):
    row = conn.execute("SELECT * FROM bloody_games WHERE id = ?", (game_id,)).fetchone()
    return _view(row, *player_view(conn, telegram_id))


def _finish(conn, row, rnd, status, now):
    """Единственное место окончания раунда: выплата накопленного через wallet, опыт, рекорд. Закрывается один раз (условие status = 'active')."""
    paid = br.payout_chips(rnd.units, row["bet"])
    changed = conn.execute("UPDATE bloody_games SET status = ?, payout = ?, state_json = ?, updated_at = ?, finished_at = ? WHERE id = ? AND status = 'active'",
                           (status, paid, rnd.to_json(), now, now, row["id"])).rowcount
    if changed == 0:
        return
    pay_and_xp(conn, row["telegram_id"], paid, xp.bloody_xp(row["cost"]), "bloody", row["cost"], now)


def _settle_in(conn, telegram_id, now):
    """Закрывает брошенный раунд (внутри открытой транзакции): выплата накопленного. True, если закрыл."""
    row = _active(conn, telegram_id)
    if row is None or now - row["updated_at"] < cfg.IDLE_SECONDS:
        return False
    _finish(conn, row, br.Round.from_json(row["state_json"]), "expired", now)
    return True


def settle_expired_bloody(telegram_id, now=None, db_path=None):
    now = now_or_clock(now)

    def not_expired(conn):
        row = _active(conn, telegram_id)
        return not (row is None or now - row["updated_at"] < cfg.IDLE_SECONDS)

    return settle_expired(lambda conn: _settle_in(conn, telegram_id, now), db_path, precheck=not_expired)


def close_expired_bloody(now=None, db_path=None, batch=CLOSE_BATCH):
    """Фоновое закрытие брошенных раундов всех игроков (не больше batch за проход). Возвращает число."""
    now = now_or_clock(now)
    return close_expired(
        GAME, "SELECT telegram_id FROM bloody_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?", (now, cfg.IDLE_SECONDS, batch),
        lambda owner: settle_expired_bloody(owner, now=now, db_path=db_path), db_path, "Закрыто брошенных раундов Bloody slot")


def _run(telegram_id, request_id, action, params, body, now, db_path):
    now = now_or_clock(now)
    return run_action(GAME, telegram_id, request_id, action, params, lambda conn: body(conn, now), now, db_path,
                      presettle=lambda: settle_expired_bloody(telegram_id, now=now, db_path=db_path))


def _play(conn, row, rnd, rng, now):
    """Один спин раунда, запись состояния; если раунд закончился, выплата. Возвращает ответ."""
    log = br.play_spin(rnd, rng)
    conn.execute("UPDATE bloody_games SET state_json = ?, last_json = ?, units = ?, spins = ?, updated_at = ? WHERE id = ? AND status = 'active'",
                 (rnd.to_json(), json.dumps(log, separators=(",", ":")), rnd.units, rnd.spins, now, row["id"]))
    if rnd.finished:
        _finish(conn, conn.execute("SELECT * FROM bloody_games WHERE id = ?", (row["id"],)).fetchone(), rnd, "capped" if rnd.capped else "finished", now)
    return _response(conn, row["telegram_id"], row["id"])


def bloody_start(telegram_id, request_id, bet, now=None, db_path=None, rng=None):
    """Старт раунда: нет активного, ставка списывается через wallet, играется стартовый спин (бонусные спины игрок запускает сам: bloody_spin)."""
    validate_bet(bet)
    cost = bet

    def body(conn, now_):
        if _active(conn, telegram_id) is not None:
            raise br.ActiveRoundExists()
        wallet.debit(conn, telegram_id, cost)   # InsufficientFunds, если фишек не хватает
        if wallet.get_balance(conn, telegram_id) + br.payout_chips(br.CAP_UNITS, bet) > MAX_SAFE_INT:
            raise BalanceLimit()
        add_staked(conn, telegram_id, cost, now_)
        rnd = br.Round()
        cur = conn.execute("INSERT INTO bloody_games (telegram_id, bet, cost, buy, state_json, last_json, units, spins, status, payout, created_at, updated_at) "
                           "VALUES (?, ?, ?, NULL, ?, NULL, 0, 0, 'active', 0, ?, ?)", (telegram_id, bet, cost, rnd.to_json(), now_, now_))
        row = conn.execute("SELECT * FROM bloody_games WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _play(conn, row, rnd, rng if rng is not None else _system_rng(), now_)

    return _run(telegram_id, request_id, "start", {"bet": bet}, body, now, db_path)


def bloody_spin(telegram_id, request_id, now=None, db_path=None, rng=None):
    """Следующий бонусный спин активного раунда (Candle Spins или Reign of Terror Spins)."""
    def body(conn, now_):
        row = _active(conn, telegram_id)
        if row is None:
            raise br.NoActiveRound()
        rnd = br.Round.from_json(row["state_json"])
        return _play(conn, row, rnd, rng if rng is not None else _system_rng(), now_)

    return _run(telegram_id, request_id, "spin", {}, body, now, db_path)


def bloody_state(telegram_id, now=None, db_path=None):
    """Активный раунд, иначе последний завершённый, иначе status none; баланс с начислением, как /api/me."""
    now = now_or_clock(now)
    settle_expired_bloody(telegram_id, now=now, db_path=db_path)

    def read(conn):
        row = latest_row(conn, GAME, telegram_id)
        balance, level, xp_total = player_view(conn, telegram_id)
        return _none_view(balance, level, xp_total) if row is None else _view(row, balance, level, xp_total)

    return read_state(telegram_id, now, db_path, read)


_SYSTEM_RNG = None


def _system_rng():
    global _SYSTEM_RNG
    if _SYSTEM_RNG is None:
        import random
        _SYSTEM_RNG = random.SystemRandom()
    return _SYSTEM_RNG
