"""Блэкджек: раздача, действия, автозакрытие брошенных (правила в blackjack.py)."""

import json
import time

import blackjack
import wallet
import xp
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _add_xp, _credit_capped, _register_player


def _bj_active(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM blackjack_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _bj_state(row):
    """Состояние движка из строки базы (у завершённой игры колоды нет: она очищена)."""
    return {
        "bet": row["bet"], "wager": row["wager"],
        "shoe": json.loads(row["deck_json"]) if row["deck_json"] else [], "pos": row["deck_pos"],
        "player": json.loads(row["player_json"]), "dealer": json.loads(row["dealer_json"]),
        "status": row["status"], "result": row["result"], "payout": row["payout"],
    }


def _bj_save(conn, game_id, state, now):
    """Записывает состояние. После конца раздачи колода очищается и больше не хранится."""
    finished = state["status"] == "finished"
    conn.execute(
        "UPDATE blackjack_games SET wager = ?, deck_json = ?, deck_pos = ?, player_json = ?, dealer_json = ?, "
        "status = ?, result = ?, payout = ?, updated_at = ? WHERE id = ?",
        (state["wager"], "" if finished else json.dumps(state["shoe"], separators=(",", ":")), state["pos"],
         json.dumps(state["player"], separators=(",", ":")), json.dumps(state["dealer"], separators=(",", ":")),
         state["status"], state["result"], state["payout"], now, game_id),
    )


def _bj_finish(conn, telegram_id, game_id, state, now, auto=False):
    """Единственное место окончания раздачи: выплата через wallet, XP за раздачу, отметка времени. Вызывается один раз
    (раздача уже не active, повторно её никто не закроет)."""
    _bj_save(conn, game_id, state, now)
    conn.execute("UPDATE blackjack_games SET finished_at = ?, auto = ? WHERE id = ?", (now, 1 if auto else 0, game_id))
    if state["payout"] > 0:
        _credit_capped(conn, telegram_id, state["payout"])
    _add_xp(conn, telegram_id, xp.blackjack_xp(state["wager"]))


def _bj_response(conn, telegram_id, row, replayed=False):
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return blackjack.view(_bj_state(row), pl["balance"], profile_level(pl["xp"]), pl["xp"],
                          auto=bool(row["auto"]), replayed=replayed)


def _bj_settle_expired_in(conn, telegram_id, now):
    """Просроченная активная раздача (24 часа без действий) закрывается автоматическим stand. True, если закрыла."""
    row = _bj_active(conn, telegram_id)
    if row is None or now - row["updated_at"] < blackjack.BLACKJACK_IDLE_SECONDS:
        return False
    state = blackjack.act(_bj_state(row), "stand")
    _bj_finish(conn, telegram_id, row["id"], state, now, auto=True)
    return True


def settle_expired_blackjack(telegram_id, now=None, db_path=None):
    """Закрывает просроченную раздачу игрока отдельной транзакцией (идемпотентно). True, если закрыла."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _bj_settle_expired_in(conn, telegram_id, now)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


BLACKJACK_CLOSE_BATCH = 200


def close_expired_blackjack(now=None, db_path=None, batch=BLACKJACK_CLOSE_BATCH):
    """Фоновое закрытие просроченных раздач всех игроков (не больше batch за проход). Возвращает число."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'blackjack_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM blackjack_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
            (now, blackjack.BLACKJACK_IDLE_SECONDS, batch),
        )]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_blackjack(owner, now=now, db_path=db_path))
    if closed:
        logger.info("Закрыто просроченных раздач блэкджека: %d", closed)  # только количество
    return closed


def _run_blackjack_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия: закрытие просроченной раздачи, повтор по request_id, минутное начисление, тело
    действия, запись ответа. Один request_id с другим действием или параметрами даёт RequestConflict."""
    if now is None:
        now = int(time.time())
    settle_expired_blackjack(telegram_id, now=now, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM blackjack_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise blackjack.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO blackjack_actions (telegram_id, request_id, action, params, response_json, created_at) "
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


def blackjack_start(telegram_id, request_id, bet, now=None, db_path=None, rng=None):
    """Новая раздача: нет активной, ставка списывается через wallet и сразу идёт в total_staked, колода тасуется заново.
    Блэкджек (у игрока и/или дилера) решается тут же. rng: объект с shuffle (тесты)."""
    if type(bet) is not int or not 1 <= bet <= blackjack.BLACKJACK_MAX_BET:
        raise ValueError("bet out of range")

    def body(conn, now_):
        if _bj_active(conn, telegram_id) is not None:
            raise blackjack.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                     (bet, MAX_SAFE_INT, telegram_id))
        state = blackjack.start(bet, blackjack.new_shoe(rng))
        cur = conn.execute(
            "INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, "
            "status, created_at, updated_at) VALUES (?, ?, ?, '', 0, '[]', '[]', 'active', ?, ?)",
            (telegram_id, bet, bet, now_, now_),
        )
        game_id = cur.lastrowid
        if state["status"] == "finished":
            _bj_finish(conn, telegram_id, game_id, state, now_)
        else:
            _bj_save(conn, game_id, state, now_)
        return _bj_response(conn, telegram_id, conn.execute("SELECT * FROM blackjack_games WHERE id = ?", (game_id,)).fetchone())

    return _run_blackjack_action(telegram_id, request_id, "start", {"bet": bet}, body, now, db_path)


def blackjack_action(telegram_id, request_id, action, now=None, db_path=None):
    """hit, stand или double. double: только на первых двух картах (InvalidAction), списывается вторая ставка
    (InsufficientFunds), она тоже идёт в total_staked."""
    if action not in blackjack.ACTIONS:
        raise ValueError("bad action")

    def body(conn, now_):
        row = _bj_active(conn, telegram_id)
        if row is None:
            raise blackjack.NoActiveGame()
        state = _bj_state(row)
        if action == "double":
            if not blackjack.can_double(state):
                raise blackjack.InvalidAction()
            wallet.debit(conn, telegram_id, state["bet"])
            conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                         (state["bet"], MAX_SAFE_INT, telegram_id))
        blackjack.act(state, action)
        if state["status"] == "finished":
            _bj_finish(conn, telegram_id, row["id"], state, now_)
        else:
            _bj_save(conn, row["id"], state, now_)
        return _bj_response(conn, telegram_id, conn.execute("SELECT * FROM blackjack_games WHERE id = ?", (row["id"],)).fetchone())

    return _run_blackjack_action(telegram_id, request_id, action, {}, body, now, db_path)


def blackjack_state(telegram_id, now=None, db_path=None):
    """Активная раздача, иначе последняя завершённая, иначе status none (только чтение, плюс закрытие просроченной)."""
    if now is None:
        now = int(time.time())
    settle_expired_blackjack(telegram_id, now=now, db_path=db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)   # баланс с начислением, как /api/me
            row = conn.execute(
                "SELECT * FROM blackjack_games WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1",
                (telegram_id,),
            ).fetchone()
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            level = profile_level(pl["xp"])
            if row is None:
                result = blackjack.none_view(pl["balance"], level, pl["xp"])
            else:
                result = blackjack.view(_bj_state(row), pl["balance"], level, pl["xp"], auto=bool(row["auto"]))
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
