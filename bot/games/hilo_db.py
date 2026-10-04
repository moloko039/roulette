"""Хило: партии, ходы, кэшаут, автозакрытие брошенных (правила в hilo.py)."""

import json
import time

import hilo
import wallet
import xp
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _add_xp, _credit_capped, _register_player


def _hilo_active(conn, telegram_id):
    return conn.execute("SELECT * FROM hilo_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)).fetchone()


HOW_TEXT = {"s": "start", "w": "win", "t": "tie", "k": "skip", "l": "lose"}


def _hilo_cards(row):
    """(текущая карта, прошлые карты) из hist_json; у каждой карты how: start, win, tie, skip, lose."""
    hist = [{"rank": c[0], "suit": c[1], "how": HOW_TEXT[c[2]]} for c in json.loads(row["hist_json"])]
    return hist[-1], hist[:-1][-hilo.HISTORY_SHOWN:]


def _hilo_moves(row, m):
    """Ходы hi и lo активной партии: доступность, вероятность (проценты), множитель и выплата после угадывания. Считает сервер."""
    moves = {}
    for choice in ("hi", "lo"):
        k = hilo.ways(choice, row["card_rank"])
        after = m * hilo.step_multiplier(k)
        moves[choice] = {"available": k < hilo.RANKS, "probability": hilo.probability_text(k),
                         "multiplier": hilo.multiplier_text(min(after, hilo.Fraction(hilo.HILO_MAX_X))),   # не выше потолка
                         "payout": hilo.payout(row["bet"], after)}
    return moves


def _hilo_view(row, balance, level, xp_total, replayed=False):
    """Ответ API (одна форма). Следующей карты в ответе нет: её ещё не существует."""
    active = row["status"] == "active"
    m = hilo.frac(row["mult_num"], row["mult_den"])
    card, history = _hilo_cards(row)
    return {
        "status": row["status"],
        "bet": row["bet"],
        "card": card,
        "history": history,
        "steps": row["steps"],
        "multiplier": hilo.multiplier_text(m),
        "payout_now": hilo.payout(row["bet"], m) if active else None,
        "can_cashout": active and row["steps"] >= 1,
        "moves": _hilo_moves(row, m) if active else None,
        "cap": hilo.multiplier_text(hilo.Fraction(hilo.HILO_MAX_X)),
        "payout": None if active else row["payout"],
        "balance": balance,
        "level": level,
        "xp": xp_total,
        "auto": bool(row["auto"]),
        "replayed": replayed,
    }


def _hilo_none_view(balance, level, xp_total):
    return {"status": "none", "bet": None, "card": None, "history": [], "steps": 0, "multiplier": "1.00", "payout_now": None,
            "can_cashout": False, "moves": None, "cap": hilo.multiplier_text(hilo.Fraction(hilo.HILO_MAX_X)), "payout": None,
            "balance": balance, "level": level, "xp": xp_total, "auto": False, "replayed": False}


def _hilo_response(conn, telegram_id, game_id):
    row = conn.execute("SELECT * FROM hilo_games WHERE id = ?", (game_id,)).fetchone()
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return _hilo_view(row, pl["balance"], profile_level(pl["xp"]), pl["xp"])


def _hilo_finish(conn, row, status, paid, xp_m, now, auto, card=None, how=None):
    """Единственное место окончания партии: выплата через wallet, XP, отметка времени. Закрывается один раз (условие
    status = 'active'), значит выплата и опыт тоже один раз. xp_m: множитель цели для опыта (None: опыта нет).
    card и how: карта, которой закончилась партия (проигравшая), добавляется в историю."""
    hist = json.loads(row["hist_json"])
    if card is not None:
        hist.append([card[0], card[1], how])
    rank, suit = (card if card is not None else (row["card_rank"], row["card_suit"]))
    changed = conn.execute(
        "UPDATE hilo_games SET status = ?, payout = ?, auto = ?, card_rank = ?, card_suit = ?, hist_json = ?, "
        "finished_at = ?, updated_at = ? WHERE id = ? AND status = 'active'",
        (status, paid, 1 if auto else 0, rank, suit, json.dumps(hist[-(hilo.HISTORY_SHOWN + 1):], separators=(",", ":")),
         now, now, row["id"])).rowcount
    if changed == 0:
        return
    if paid > 0:
        _credit_capped(conn, row["telegram_id"], paid)
    if xp_m is not None:
        _add_xp(conn, row["telegram_id"], xp.hilo_xp(row["bet"], xp_m))


def _hilo_settle_in(conn, telegram_id, now):
    """Закрывает просроченную партию (внутри открытой транзакции), как у мин: без угаданных ходов возврат ставки, иначе
    автоматический cashout по текущему множителю. True, если закрыла."""
    row = _hilo_active(conn, telegram_id)
    if row is None or now - row["updated_at"] < hilo.HILO_IDLE_SECONDS:
        return False
    if row["steps"] == 0:
        _hilo_finish(conn, row, "refunded", row["bet"], None, now, True)
    else:
        m = hilo.frac(row["mult_num"], row["mult_den"])
        _hilo_finish(conn, row, "cashed", hilo.payout(row["bet"], m), m, now, True)
    return True


def settle_expired_hilo(telegram_id, now=None, db_path=None):
    """Закрывает просроченную партию игрока отдельной транзакцией (идемпотентно). Ничего не меняет, пока срок не вышел."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        row = _hilo_active(conn, telegram_id)
        if row is None or now - row["updated_at"] < hilo.HILO_IDLE_SECONDS:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _hilo_settle_in(conn, telegram_id, now)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


HILO_CLOSE_BATCH = 200


def close_expired_hilo(now=None, db_path=None, batch=HILO_CLOSE_BATCH):
    """Фоновое закрытие просроченных партий всех игроков (не больше batch за проход). Возвращает число."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'hilo_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM hilo_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
            (now, hilo.HILO_IDLE_SECONDS, batch))]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_hilo(owner, now=now, db_path=db_path))
    if closed:
        logger.info("Закрыто просроченных партий в хило: %d", closed)   # только количество
    return closed


def _run_hilo_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия: закрытие просроченной партии, повтор по request_id (другое действие или параметры:
    RequestConflict), минутное начисление, тело действия, запись ответа."""
    if now is None:
        now = int(time.time())
    settle_expired_hilo(telegram_id, now=now, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM hilo_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id)).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise hilo.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO hilo_actions (telegram_id, request_id, action, params, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now))
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def hilo_start(telegram_id, request_id, bet, now=None, db_path=None, rng=None):
    """Старт: нет активной партии, ставка списывается через wallet, выбирается первая карта. В total_staked ставка идёт
    при первом ходе hi/lo (как у мин), не на старте."""
    if type(bet) is not int or not 1 <= bet <= hilo.HILO_MAX_BET:
        raise ValueError("bet out of range")

    def body(conn, now_):
        if _hilo_active(conn, telegram_id) is not None:
            raise hilo.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        rank, suit = hilo.draw_card(rng)
        cur = conn.execute(
            "INSERT INTO hilo_games (telegram_id, bet, card_rank, card_suit, steps, mult_num, mult_den, hist_json, status, "
            "payout, staked_counted, auto, created_at, updated_at) VALUES (?, ?, ?, ?, 0, '1', '1', ?, 'active', 0, 0, 0, ?, ?)",
            (telegram_id, bet, rank, suit, json.dumps([[rank, suit, "s"]], separators=(",", ":")), now_, now_))
        return _hilo_response(conn, telegram_id, cur.lastrowid)

    return _run_hilo_action(telegram_id, request_id, "start", {"bet": bet}, body, now, db_path)


def hilo_guess(telegram_id, request_id, choice, now=None, db_path=None, rng=None):
    """Ход hi, lo или skip. Следующая карта выбирается здесь и до этого нигде не существует. Равенство достоинств выигрывает.
    Ход с k = 13 запрещён (MoveForbidden). Потолок: M >= 1000 закрывает партию выплатой 1000 * ставка."""
    if choice not in hilo.CHOICES:
        raise ValueError("choice")

    def body(conn, now_):
        row = _hilo_active(conn, telegram_id)
        if row is None:
            raise hilo.NoActiveGame()
        m = hilo.frac(row["mult_num"], row["mult_den"])
        hist = json.loads(row["hist_json"])
        rank = row["card_rank"]
        if choice == "skip":
            new_rank, new_suit = hilo.draw_card(rng)
            hist.append([new_rank, new_suit, "k"])
            conn.execute("UPDATE hilo_games SET card_rank = ?, card_suit = ?, hist_json = ?, updated_at = ? WHERE id = ?",
                         (new_rank, new_suit, json.dumps(hist[-(hilo.HISTORY_SHOWN + 1):], separators=(",", ":")), now_, row["id"]))
            return _hilo_response(conn, telegram_id, row["id"])
        k = hilo.ways(choice, rank)
        if k >= hilo.RANKS:
            raise hilo.MoveForbidden()
        if not row["staked_counted"]:
            conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                         (row["bet"], MAX_SAFE_INT, telegram_id))
            conn.execute("UPDATE hilo_games SET staked_counted = 1 WHERE id = ?", (row["id"],))
        new_rank, new_suit = hilo.draw_card(rng)
        after = m * hilo.step_multiplier(k)
        if not hilo.is_win(choice, rank, new_rank):
            _hilo_finish(conn, row, "lost", 0, after, now_, False, card=(new_rank, new_suit), how="l")
            return _hilo_response(conn, telegram_id, row["id"])
        hist.append([new_rank, new_suit, "t" if new_rank == rank else "w"])
        if hilo.reached_cap(after):
            _hilo_finish(conn, row, "capped", row["bet"] * hilo.HILO_MAX_X, hilo.Fraction(hilo.HILO_MAX_X), now_, False,
                         card=(new_rank, new_suit), how=hist[-1][2])
            return _hilo_response(conn, telegram_id, row["id"])
        conn.execute(
            "UPDATE hilo_games SET card_rank = ?, card_suit = ?, steps = steps + 1, mult_num = ?, mult_den = ?, hist_json = ?, "
            "updated_at = ? WHERE id = ?",
            (new_rank, new_suit, str(after.numerator), str(after.denominator),
             json.dumps(hist[-(hilo.HISTORY_SHOWN + 1):], separators=(",", ":")), now_, row["id"]))
        return _hilo_response(conn, telegram_id, row["id"])

    return _run_hilo_action(telegram_id, request_id, "guess", {"choice": choice}, body, now, db_path)


def hilo_cashout(telegram_id, request_id, now=None, db_path=None):
    """Забрать выигрыш: только после хотя бы одного угаданного хода (NothingToCashOut, партия остаётся)."""
    def body(conn, now_):
        row = _hilo_active(conn, telegram_id)
        if row is None:
            raise hilo.NoActiveGame()
        if row["steps"] < 1:
            raise hilo.NothingToCashOut()
        m = hilo.frac(row["mult_num"], row["mult_den"])
        _hilo_finish(conn, row, "cashed", hilo.payout(row["bet"], m), m, now_, False)
        return _hilo_response(conn, telegram_id, row["id"])

    return _run_hilo_action(telegram_id, request_id, "cashout", {}, body, now, db_path)


def hilo_state(telegram_id, now=None, db_path=None):
    """Активная партия, иначе последняя завершённая, иначе status none; баланс с начислением, как /api/me."""
    if now is None:
        now = int(time.time())
    settle_expired_hilo(telegram_id, now=now, db_path=db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            row = conn.execute(
                "SELECT * FROM hilo_games WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1",
                (telegram_id,)).fetchone()
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            level = profile_level(pl["xp"])
            result = _hilo_none_view(pl["balance"], level, pl["xp"]) if row is None else _hilo_view(
                row, pl["balance"], level, pl["xp"])
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
