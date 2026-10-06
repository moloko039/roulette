"""Блэкджек: раздача, действия, автозакрытие брошенных (правила в blackjack.py)."""

import json

import blackjack
import wallet
import xp

from games.round_common import (Game, CLOSE_BATCH, active_row, add_staked, close_expired, latest_row, now_or_clock, pay_and_xp,
                                player_view, read_state, run_action, settle_expired)

GAME = Game("blackjack", blackjack.RequestConflict)


def _bj_active(conn, telegram_id):
    return active_row(conn, GAME, telegram_id)


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
    pay_and_xp(conn, telegram_id, state["payout"], xp.blackjack_xp(state["wager"]), "blackjack", state["wager"], now)


def _bj_response(conn, telegram_id, row, replayed=False):
    return blackjack.view(_bj_state(row), *player_view(conn, telegram_id), auto=bool(row["auto"]), replayed=replayed)


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
    now = now_or_clock(now)

    def expired(conn):      # быстрая проверка чтением, без блокировки записи: есть ли просроченная активная раздача (как у хило и краша)
        row = _bj_active(conn, telegram_id)
        return row is not None and now - row["updated_at"] >= blackjack.BLACKJACK_IDLE_SECONDS

    return settle_expired(lambda conn: _bj_settle_expired_in(conn, telegram_id, now), db_path, precheck=expired)


BLACKJACK_CLOSE_BATCH = CLOSE_BATCH


def close_expired_blackjack(now=None, db_path=None, batch=BLACKJACK_CLOSE_BATCH):
    """Фоновое закрытие просроченных раздач всех игроков (не больше batch за проход). Возвращает число."""
    now = now_or_clock(now)
    return close_expired(
        GAME, "SELECT telegram_id FROM blackjack_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
        (now, blackjack.BLACKJACK_IDLE_SECONDS, batch), lambda owner: settle_expired_blackjack(owner, now=now, db_path=db_path),
        db_path, "Закрыто просроченных раздач блэкджека")


def _run_blackjack_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия (round_common.run_action): закрытие просроченной раздачи, повтор по request_id, начисление, тело."""
    now = now_or_clock(now)
    return run_action(GAME, telegram_id, request_id, action, params, lambda conn: body(conn, now), now, db_path,
                      presettle=lambda: settle_expired_blackjack(telegram_id, now=now, db_path=db_path))


def blackjack_start(telegram_id, request_id, bet, now=None, db_path=None, rng=None):
    """Новая раздача: нет активной, ставка списывается через wallet и сразу идёт в total_staked, колода тасуется заново.
    Блэкджек (у игрока и/или дилера) решается тут же. rng: объект с shuffle (тесты)."""
    if type(bet) is not int or not 1 <= bet <= blackjack.BLACKJACK_MAX_BET:
        raise ValueError("bet out of range")

    def body(conn, now_):
        if _bj_active(conn, telegram_id) is not None:
            raise blackjack.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        add_staked(conn, telegram_id, bet)
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
            add_staked(conn, telegram_id, state["bet"])
        blackjack.act(state, action)
        if state["status"] == "finished":
            _bj_finish(conn, telegram_id, row["id"], state, now_)
        else:
            _bj_save(conn, row["id"], state, now_)
        return _bj_response(conn, telegram_id, conn.execute("SELECT * FROM blackjack_games WHERE id = ?", (row["id"],)).fetchone())

    return _run_blackjack_action(telegram_id, request_id, action, {}, body, now, db_path)


def blackjack_state(telegram_id, now=None, db_path=None):
    """Активная раздача, иначе последняя завершённая, иначе status none (только чтение, плюс закрытие просроченной)."""
    now = now_or_clock(now)
    settle_expired_blackjack(telegram_id, now=now, db_path=db_path)

    def read(conn):   # баланс с начислением, как /api/me
        row = latest_row(conn, GAME, telegram_id)
        balance, level, xp_total = player_view(conn, telegram_id)
        if row is None:
            return blackjack.none_view(balance, level, xp_total)
        return blackjack.view(_bj_state(row), balance, level, xp_total, auto=bool(row["auto"]))

    return read_state(telegram_id, now, db_path, read)
