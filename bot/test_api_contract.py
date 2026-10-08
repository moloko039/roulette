"""Контракт ответов API: точный набор ключей и типы значений для каждого эндпоинта и сценария.

Тест падает при добавлении, удалении или изменении поля: так изменение формы становится осознанным.
Меняя форму ответа, обновите этот тест, docs/API.md и клиент (script.js) вместе.
Тесты идут на настоящем приложении (FastAPI), а не на моке; значения в тесте заглушки.
"""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import shutil
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import db
import mines
import ratelimit
from api import create_app
from roulette import MAX_SAFE_INT
from tg_testutil import make_init_data

HERE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = int(time.time())
OPT = lambda t: (t, type(None))  # noqa: E731  значение этого типа или null

# тест не зависит от окружения и bot/.env
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}


def type_ok(expected, value):
    if expected is None or expected is type(None):
        return value is None
    if expected in (int, bool):          # bool не считается int и наоборот
        return type(value) is expected
    return isinstance(value, expected)


def shape_errors(spec, value, path="$"):
    """Список расхождений значения со спецификацией (пусто = совпало)."""
    if isinstance(spec, tuple):          # один из вариантов (например, объект или null)
        if any(not shape_errors(option, value, path) for option in spec):
            return []
        return ["%s: не подошёл ни один вариант, получили %r" % (path, type(value).__name__)]
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return ["%s: ожидался объект, получили %r" % (path, type(value).__name__)]
        errors = []
        for key in sorted(set(spec) - set(value)):
            errors.append("%s: нет поля %s" % (path, key))
        for key in sorted(set(value) - set(spec)):
            errors.append("%s: лишнее поле %s" % (path, key))
        for key in spec:
            if key in value:
                errors += shape_errors(spec[key], value[key], path + "." + key)
        return errors
    if isinstance(spec, list):
        if not isinstance(value, list):
            return ["%s: ожидался список" % path]
        errors = []
        for i, item in enumerate(value):
            errors += shape_errors(spec[0], item, "%s[%d]" % (path, i))
        return errors
    return [] if type_ok(spec, value) else ["%s: ожидался %s, получили %r" % (path, spec, value)]


def contract(name, body, spec):
    errors = shape_errors(spec, body)
    assert not errors, "контракт «%s» нарушен: %s" % (name, "; ".join(errors))


# ---------- формы ответов (то же описано в docs/API.md) ----------
ERR_DETAIL = {"detail": str}
GAME = {"bet": int, "mines": int, "revealed": [int], "safe_left": int, "multiplier": str, "payout_now": int,
        "next_multiplier": OPT(str), "next_payout": OPT(int), "expires_at": int}
LAST = {"status": str, "bet": int, "mines": int, "revealed": [int], "mine_cells": [int], "payout": int,
        "finished_at": int}
ME = {"balance": int, "rate": int, "seconds_to_next": int, "level": int, "income_level": int, "storage_level": int,
      "farm": {"income_per_hour": int, "per_minute_estimate": str, "next_tick_in_s": int, "hours_cap": int, "accrued_now": int},
      "active_game": OPT(str), "gems": int,
      "cosmetics": {"equipped": {"card_back": str, "chip": str, "table": str, "mine_icons": str, "keno_ball": str, "crash": str,
                                 "avatar_frame": str, "badge": str}, "show_in_rating": bool},
      "chat": {"in_chat": bool, "bonus_pct": int, "active_today": int, "boost_until": OPT(int), "boost_gems": int}}
SPIN = {"number": int, "stake_total": int, "payout_total": int, "net": int, "balance": int, "replayed": bool}
TOP_ITEM = {"rank": int, "name": str, "balance": int, "is_me": bool, "staked": int, "level": int,
            "cosmetics": dict,     # публичные слоты {слот: код}; пусто, если ничего не надето или игрок скрыл показ
            "complete_sets": [str]}
TOP_ME = {"rank": int, "balance": int, "total": int, "staked": int, "level": int}
CHAT_TOP = {"scope": str, "top": [TOP_ITEM], "me": TOP_ME, "chat_staked": int, "chat_level": int, "chat_points": int, "chat_level_start_points": int, "chat_next_points": OPT(int), "set_names": dict}
BEST_ITEM = {"rank": int, "name": str, "net_amount": int, "game": str, "is_me": bool, "cosmetics": dict}
BEST_WINS = {"scope": str, "top": [BEST_ITEM], "me": OPT({"rank": int, "net_amount": int, "game": str, "total": int}), "total": int}
FARM_PART = {"level": int, "max": int, "can_buy": bool, "reason": OPT(str), "next_cost": OPT(int)}
FARM = {
    "balance": int,
    "profile": {"level": int, "xp": int, "staked": int, "next_threshold": OPT(int)},
    "slots": {"used": int, "total": int},
    "income": dict(FARM_PART, rate=int, next_rate=OPT(int)),
    "storage": dict(FARM_PART, hours=int, next_hours=OPT(int)),
}
FARM_BUY = {"kind": str, "level_after": int, "cost": int, "balance": int, "replayed": bool}
M_START = {"game": GAME, "balance": int, "replayed": bool}
M_REVEAL_SAFE = {"result": str, "game": GAME, "balance": int, "replayed": bool}          # ключа last нет
M_REVEAL_END = {"result": str, "game": type(None), "last": LAST, "balance": int, "replayed": bool}
M_CASHOUT = {"last": LAST, "balance": int, "replayed": bool}
M_STATE = {"game": OPT(GAME), "last": OPT(LAST), "balance": int}
SLOT_STEP = {"board": str, "wins": [{"sym": str, "length": int, "ways": int, "pay": int}], "multiplier": int, "stepWin": int,
             "exploded": [{"reel": int, "row": int}], "toWild": [{"reel": int, "row": int}], "refilled": [{"reel": int, "cells": str}], "boardAfter": str}
SLOT_SPIN = {"mode": str, "steps": [SLOT_STEP], "scatters": int, "freeSpinsAwarded": int, "totalWin": int, "capped": bool}
SLOT_ROUND = {"base": SLOT_SPIN, "freeSpins": [SLOT_SPIN], "freeSpinsLeftAfter": [int], "totalWin": int, "bonusWin": int, "capped": bool, "bought": bool}
SLOT = {"coin": int, "bought": bool, "cost": int, "payout": int, "round": SLOT_ROUND, "balance": int, "level": int, "xp": int, "replayed": bool}
REFERRAL_RULES = {"invitee_chips": int, "inviter_chips": int, "inviter_gems": int, "qualify_hours": int, "qualify_level": int, "qualify_rounds": int}
REFERRAL = {"link": OPT(str), "invited": int, "qualified": int, "rules": REFERRAL_RULES}

class FixedRng:
    def __init__(self, cells):
        self.cells = list(cells)

    def sample(self, population, k):
        return list(self.cells)[:k]


tmp = tempfile.mkdtemp()
counter = [0]


def new_app(limiter=None):
    counter[0] += 1
    path = os.path.join(tmp, "c%d.db" % counter[0])
    db.init_db(path)
    return path, TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=limiter))


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=100_000, total=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW + 10 * 86400, NOW, total, total))


def auth(uid, group=False, name="Игрок"):
    data = make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=name,
                          chat_type="group" if group else None, chat_instance="room" if group else None)
    return {"Authorization": "tma " + data}


def rid(n):
    return "contract-req-%06d" % n


def bet(kind, value=None, amount=10):
    return {"type": kind, "value": value, "amount": amount}


try:
    path, client = new_app()
    U = 1001
    add_player(path, U)

    # ================= GET /api/me =================
    r = client.get("/api/me", headers=auth(U))
    assert r.status_code == 200
    contract("GET /api/me 200", r.json(), ME)
    r = client.get("/api/me")
    assert r.status_code == 401
    contract("GET /api/me 401", r.json(), ERR_DETAIL)
    assert r.json() == {"detail": "Unauthorized"}

    # ================= POST /api/roulette/spin =================
    body = {"request_id": rid(1), "bets": [bet("red", None, 10), bet("number", 17, 5)]}
    r = client.post("/api/roulette/spin", headers=auth(U), json=body)
    assert r.status_code == 200, r.text
    contract("spin 200", r.json(), SPIN)
    assert r.json()["replayed"] is False
    r = client.post("/api/roulette/spin", headers=auth(U), json=body)
    contract("spin replayed", r.json(), SPIN)
    assert r.json()["replayed"] is True
    r = client.post("/api/roulette/spin", headers=auth(U), json={"bad": 1})
    assert r.status_code == 400
    contract("spin 400", r.json(), ERR_DETAIL)
    assert r.json() == {"detail": "invalid_bets"}
    r = client.post("/api/roulette/spin", headers=auth(U), json={"request_id": rid(2), "bets": [bet("red", None, 10 ** 9)]})
    assert r.status_code == 409
    assert r.json() == {"detail": "insufficient_funds"}
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 10, U))
    r = client.post("/api/roulette/spin", headers=auth(U), json={"request_id": rid(3), "bets": [bet("number", 1, 100)]})
    assert r.status_code == 409
    assert r.json() == {"detail": "balance_limit"}
    sql(path, "UPDATE players SET balance = 100000 WHERE telegram_id = ?", (U,))
    r = client.post("/api/roulette/spin", json=body)
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= GET /api/chat/top =================
    r = client.get("/api/chat/top", headers=auth(U, group=False))
    assert r.status_code == 200
    assert r.json() == {"scope": "none"}, "scope none: только поле scope"
    for uid in (2001, 2002, 2003):
        add_player(path, uid, balance=1000 * uid % 7000 + 100)
    for uid in (2001, 2002, 2003):
        client.get("/api/me", headers=auth(uid, group=True))
    r = client.get("/api/chat/top", headers=auth(2001, group=True))
    assert r.status_code == 200
    contract("chat/top chat", r.json(), CHAT_TOP)
    assert r.json()["scope"] == "chat" and 1 <= len(r.json()["top"]) <= 10
    assert r.json()["me"] is not None

    # игрок с полной «Листопад» (back_leaves, table_autumn, mine_acorn) видит complete_sets ["leaves"]
    for item_code in ("back_leaves", "table_autumn", "mine_acorn"):
        sql(path, "INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (2001, ?, 'collection', ?)", (item_code, NOW))
    r = client.get("/api/chat/top", headers=auth(2001, group=True))
    assert r.status_code == 200
    contract("chat/top с собранной коллекцией", r.json(), CHAT_TOP)
    top_2001 = [e for e in r.json()["top"] if e["is_me"]][0]
    assert top_2001["complete_sets"] == ["leaves"], "ожидали ['leaves'], получили %r" % top_2001["complete_sets"]

    # игрок со скрытым показом (show_in_rating = 0) видит []
    sql(path, "INSERT OR REPLACE INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (2001, 0)")
    r = client.get("/api/chat/top", headers=auth(2001, group=True))
    assert r.status_code == 200
    contract("chat/top со скрытым показом", r.json(), CHAT_TOP)
    top_2001_hidden = [e for e in r.json()["top"] if e["is_me"]][0]
    assert top_2001_hidden["complete_sets"] == [], "ожидали [], получили %r" % top_2001_hidden["complete_sets"]

    r = client.get("/api/chat/top")
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= GET /api/chat/best-wins =================
    r = client.get("/api/chat/best-wins", headers=auth(U, group=False))
    assert (r.status_code, r.json()) == (200, {"scope": "none"}), "scope none: только поле scope"
    r = client.get("/api/chat/best-wins", headers=auth(2001, group=True))
    assert r.status_code == 200
    contract("chat/best-wins без рекордов", r.json(), BEST_WINS)
    assert r.json() == {"scope": "chat", "top": [], "me": None, "total": 0}
    client.post("/api/roulette/spin", headers=auth(2001, group=True), json={"request_id": rid(60), "bets": [bet("red", None, 10)]})
    sqlite3.connect(path).execute("INSERT OR REPLACE INTO player_best_win (telegram_id, game, net_amount, achieved_at) VALUES (2001, 'keno', 777, 1)").connection.commit()
    r = client.get("/api/chat/best-wins", headers=auth(2001, group=True))
    contract("chat/best-wins с рекордом", r.json(), BEST_WINS)
    assert r.json()["me"] == {"rank": 1, "net_amount": 777, "game": "keno", "total": 1} and "member_ref" not in r.json()["top"][0]
    r = client.get("/api/chat/best-wins")
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= GET /api/farm и POST /api/farm/buy =================
    add_player(path, 3001, balance=50_000, total=5000)   # уровень профиля >= 2
    r = client.get("/api/farm", headers=auth(3001))
    assert r.status_code == 200
    contract("farm", r.json(), FARM)
    assert r.json()["income"]["reason"] is None and r.json()["income"]["can_buy"] is True
    r = client.post("/api/farm/buy", headers=auth(3001), json={"request_id": rid(10), "kind": "income"})
    assert r.status_code == 200, r.text
    contract("farm/buy 200", r.json(), FARM_BUY)
    assert r.json()["replayed"] is False
    r = client.post("/api/farm/buy", headers=auth(3001), json={"request_id": rid(10), "kind": "income"})
    contract("farm/buy replayed", r.json(), FARM_BUY)
    assert r.json()["replayed"] is True
    r = client.get("/api/farm", headers=auth(3001))
    contract("farm после покупки", r.json(), FARM)
    r = client.post("/api/farm/buy", headers=auth(3001), json={"request_id": rid(11), "kind": "other"})
    assert (r.status_code, r.json()) == (400, {"detail": "invalid_request"})
    # причины 409
    add_player(path, 3002, balance=50_000, total=0)       # уровень 1: один слот
    client.post("/api/farm/buy", headers=auth(3002), json={"request_id": rid(12), "kind": "storage"})
    r = client.post("/api/farm/buy", headers=auth(3002), json={"request_id": rid(13), "kind": "income"})
    assert r.status_code == 409
    contract("farm/buy level_locked", r.json(), {"detail": str, "required_level": int})
    assert r.json()["detail"] == "level_locked"
    add_player(path, 3003, balance=10, total=0)
    r = client.post("/api/farm/buy", headers=auth(3003), json={"request_id": rid(14), "kind": "income"})
    assert (r.status_code, r.json()) == (409, {"detail": "insufficient_funds"})
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, income_level, "
              "storage_level) VALUES (3004, 10, 100, ?, ?, 0, 20, 0)", (NOW + 10 * 86400, NOW))
    r = client.post("/api/farm/buy", headers=auth(3004), json={"request_id": rid(15), "kind": "income"})
    assert (r.status_code, r.json()) == (409, {"detail": "max_level"})
    r = client.get("/api/farm", headers=auth(3004))
    contract("farm на максимуме", r.json(), FARM)
    assert r.json()["income"]["next_cost"] is None and r.json()["income"]["next_rate"] is None
    assert r.json()["income"]["reason"] == "max_level"
    r = client.get("/api/farm")
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})
    r = client.post("/api/farm/buy", json={"request_id": rid(16), "kind": "income"})
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= мины =================
    M = 4001
    add_player(path, M, balance=100_000)
    r = client.get("/api/mines/state", headers=auth(M))
    assert r.status_code == 200
    contract("state без игры", r.json(), M_STATE)
    assert r.json()["game"] is None and r.json()["last"] is None, "оба поля есть и равны null"
    # старт (раскладка известна тесту через БД-функцию; ответы идут через API)
    db.mines_start(M, rid(100), 100, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))   # создаём игру в БД
    r = client.get("/api/mines/state", headers=auth(M))
    contract("state с игрой", r.json(), M_STATE)
    assert r.json()["game"] is not None and r.json()["last"] is None
    r = client.post("/api/mines/cashout", headers=auth(M), json={"request_id": rid(101)})
    contract("cashout k=0", r.json(), M_CASHOUT)
    assert r.json()["last"]["status"] == "refunded" and r.json()["replayed"] is False
    r = client.post("/api/mines/cashout", headers=auth(M), json={"request_id": rid(101)})
    contract("cashout replayed", r.json(), M_CASHOUT)
    assert r.json()["replayed"] is True
    r = client.get("/api/mines/state", headers=auth(M))
    contract("state после игры", r.json(), M_STATE)
    assert r.json()["game"] is None and r.json()["last"] is not None

    r = client.post("/api/mines/start", headers=auth(M), json={"request_id": rid(102), "bet": 100, "mines": 3})
    assert r.status_code == 200, r.text
    contract("start", r.json(), M_START)
    assert r.json()["replayed"] is False and r.json()["game"]["revealed"] == []
    r = client.post("/api/mines/start", headers=auth(M), json={"request_id": rid(102), "bet": 100, "mines": 3})
    contract("start replayed", r.json(), M_START)
    assert r.json()["replayed"] is True
    # подменяем раскладку на известную (поле mine_mask хранится только в БД)
    sql(path, "UPDATE mines_games SET mine_mask = 7 WHERE status = 'active'")
    r = client.post("/api/mines/reveal", headers=auth(M), json={"request_id": rid(103), "cell": 10})
    assert r.status_code == 200, r.text
    contract("reveal safe", r.json(), M_REVEAL_SAFE)
    assert r.json()["result"] == "safe" and "last" not in r.json(), "у безопасной клетки ключа last нет"
    r = client.post("/api/mines/reveal", headers=auth(M), json={"request_id": rid(103), "cell": 10})
    contract("reveal safe replayed", r.json(), M_REVEAL_SAFE)
    assert r.json()["replayed"] is True and "last" not in r.json()
    r = client.get("/api/mines/state", headers=auth(M))
    contract("state активная игра", r.json(), M_STATE)
    assert "mine_cells" not in str(r.json()["game"]) and "mine_mask" not in r.text, "раскладка активной игры в ответе"
    assert r.json()["last"] is not None, "state отдаёт и последнюю завершённую игру при активной новой"
    r = client.post("/api/mines/cashout", headers=auth(M), json={"request_id": rid(104)})
    contract("cashout k>0", r.json(), M_CASHOUT)
    assert r.json()["last"]["status"] == "cashed"
    # мина
    client.post("/api/mines/start", headers=auth(M), json={"request_id": rid(105), "bet": 100, "mines": 3})
    sql(path, "UPDATE mines_games SET mine_mask = 7 WHERE status = 'active'")
    r = client.post("/api/mines/reveal", headers=auth(M), json={"request_id": rid(106), "cell": 1})
    contract("reveal mine", r.json(), M_REVEAL_END)
    assert r.json()["result"] == "mine" and r.json()["game"] is None and r.json()["last"]["status"] == "lost"
    r = client.post("/api/mines/reveal", headers=auth(M), json={"request_id": rid(106), "cell": 1})
    contract("reveal mine replayed", r.json(), M_REVEAL_END)
    assert r.json()["replayed"] is True
    # очищение поля: 24 мины, одна безопасная клетка (24)
    client.post("/api/mines/start", headers=auth(M), json={"request_id": rid(107), "bet": 100, "mines": 24})
    sql(path, "UPDATE mines_games SET mine_mask = ? WHERE status = 'active'", (mines.FULL_MASK ^ (1 << 24),))
    r = client.post("/api/mines/reveal", headers=auth(M), json={"request_id": rid(108), "cell": 24})
    contract("reveal cleared", r.json(), M_REVEAL_END)
    assert r.json()["result"] == "cleared" and r.json()["last"]["status"] == "cashed"
    r = client.get("/api/mines/state", headers=auth(M))
    contract("state после очищения", r.json(), M_STATE)
    # автозакрытие: статусы auto_refunded и auto_cashed
    for n, cells, status in ((1, [], "auto_refunded"), (2, [10], "auto_cashed")):
        client.post("/api/mines/start", headers=auth(M), json={"request_id": rid(120 + n), "bet": 100, "mines": 3})
        sql(path, "UPDATE mines_games SET mine_mask = 7, updated_at = ? WHERE status = 'active'", (NOW - 2 * 86400,))
        if cells:
            sql(path, "UPDATE mines_games SET revealed_mask = ? WHERE status = 'active'", (1 << cells[0],))
        r = client.get("/api/mines/state", headers=auth(M))
        contract("state " + status, r.json(), M_STATE)
        assert r.json()["game"] is None and r.json()["last"]["status"] == status, r.json()
    # ошибки мин
    N = 4002
    add_player(path, N, balance=500)
    bad = [("start", {"request_id": rid(130), "bet": 0, "mines": 3}), ("start", {"request_id": rid(130), "bet": 10, "mines": 25}),
           ("start", {"request_id": "short", "bet": 10, "mines": 3}), ("reveal", {"request_id": rid(130), "cell": 25}),
           ("cashout", {"request_id": rid(130), "x": 1}), ("start", {"request_id": rid(130), "bet": 10})]
    for name, body in bad:
        r = client.post("/api/mines/" + name, headers=auth(N), json=body)
        assert (r.status_code, r.json()) == (400, {"detail": "invalid_request"}), (name, body, r.text)
    for name, body in (("reveal", {"request_id": rid(131), "cell": 3}), ("cashout", {"request_id": rid(132)})):
        r = client.post("/api/mines/" + name, headers=auth(N), json=body)
        assert (r.status_code, r.json()) == (409, {"detail": "no_active_game"}), (name, r.text)
    r = client.post("/api/mines/start", headers=auth(N), json={"request_id": rid(133), "bet": 501, "mines": 3})
    assert (r.status_code, r.json()) == (409, {"detail": "insufficient_funds"})
    client.post("/api/mines/start", headers=auth(N), json={"request_id": rid(134), "bet": 100, "mines": 3})
    r = client.post("/api/mines/start", headers=auth(N), json={"request_id": rid(135), "bet": 100, "mines": 3})
    assert (r.status_code, r.json()) == (409, {"detail": "active_game_exists"})
    r = client.post("/api/mines/start", headers=auth(N), json={"request_id": rid(134), "bet": 200, "mines": 3})
    assert (r.status_code, r.json()) == (409, {"detail": "request_conflict"})
    sql(path, "UPDATE mines_games SET mine_mask = 7 WHERE status = 'active'")
    client.post("/api/mines/reveal", headers=auth(N), json={"request_id": rid(136), "cell": 10})
    r = client.post("/api/mines/reveal", headers=auth(N), json={"request_id": rid(137), "cell": 10})
    assert (r.status_code, r.json()) == (409, {"detail": "already_revealed"})
    for path_ in ("/api/mines/state",):
        r = client.get(path_)
        assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})
    for name in ("start", "reveal", "cashout"):
        r = client.post("/api/mines/" + name, json={"request_id": rid(140)})
        assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= POST /api/slot/spin =================
    body = {"request_id": rid(60), "coin": 10, "buy": False}
    r = client.post("/api/slot/spin", headers=auth(U), json=body)
    assert r.status_code == 200, r.text
    contract("slot spin 200", r.json(), SLOT)
    assert r.json()["replayed"] is False and r.json()["bought"] is False and r.json()["cost"] == 200
    r = client.post("/api/slot/spin", headers=auth(U), json=body)
    contract("slot spin replayed", r.json(), SLOT)
    assert r.json()["replayed"] is True
    r = client.post("/api/slot/spin", headers=auth(U), json={"request_id": rid(61), "coin": 1, "buy": True})
    assert r.status_code == 200, r.text
    contract("slot buy 200", r.json(), SLOT)
    assert r.json()["bought"] is True and r.json()["cost"] == 1500 and r.json()["round"]["bought"] is True
    r = client.post("/api/slot/spin", headers=auth(U), json={"request_id": rid(60), "coin": 25, "buy": False})
    assert (r.status_code, r.json()) == (409, {"detail": "request_conflict"})
    r = client.post("/api/slot/spin", headers=auth(U), json={"request_id": rid(62), "coin": 3, "buy": False})
    assert (r.status_code, r.json()) == (400, {"detail": "invalid_request"})
    r = client.post("/api/slot/spin", headers=auth(U), json={"request_id": rid(63), "coin": 50000, "buy": True})
    assert (r.status_code, r.json()) == (409, {"detail": "insufficient_funds"})
    r = client.post("/api/slot/spin", json=body)
    assert (r.status_code, r.json()) == (401, {"detail": "Unauthorized"})

    # ================= 429: общий формат =================
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "1", "WRITE_RATE_PER_SEC": "1",
                                                       "READ_RATE_BURST": "1", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    path2, limited = new_app(lim)
    add_player(path2, 1)
    for method, url, extra in (("get", "/api/me", {}), ("get", "/api/farm", {}), ("get", "/api/chat/top", {}), ("get", "/api/chat/best-wins", {}),
                               ("get", "/api/mines/state", {}),
                               ("post", "/api/roulette/spin", {"json": {"request_id": rid(150), "bets": [bet("red")]}}),
                               ("post", "/api/farm/buy", {"json": {"request_id": rid(151), "kind": "income"}}),
                               ("post", "/api/mines/start", {"json": {"request_id": rid(152), "bet": 10, "mines": 3}})):
        clock[0] += 1000.0
        limiter_user = 1
        getattr(limited, method)(url, headers=auth(limiter_user), **extra)
        r = getattr(limited, method)(url, headers=auth(limiter_user), **extra)
        assert r.status_code == 429, (url, r.status_code)
        contract("429 " + url, r.json(), {"error": str})
        assert r.json() == {"error": "too_many_requests"}
        assert r.headers["Retry-After"].isdigit() and int(r.headers["Retry-After"]) >= 1
    # ---------- /api/me: active_game (незавершённая игра, чтобы клиент открыл её при запуске) ----------
    import blackjack as _bj
    import crash as _cr
    from unittest import mock as _mock
    pa, ca = new_app()
    for uid in (5001, 5002, 5003, 5004, 5005):
        add_player(pa, uid)
    r = ca.get("/api/me", headers=auth(5001))
    contract("GET /api/me без игры", r.json(), ME)
    assert r.json()["active_game"] is None, r.json()
    ca.post("/api/mines/start", headers=auth(5002), json={"request_id": rid(5002), "bet": 10, "mines": 3})
    ca.post("/api/blackjack/start", headers=auth(5003), json={"request_id": rid(5003), "bet": 10})
    with _mock.patch.object(_cr, "new_crash", return_value=5000):
        ca.post("/api/crash/start", headers=auth(5004), json={"request_id": rid(5004), "bet": 10})
    with _mock.patch.object(_cr, "new_crash", return_value=5000):
        ca.post("/api/crash/start", headers=auth(5005), json={"request_id": rid(5005), "bet": 10, "target_x100": 200})   # авто-раунд активен до цели, краха или ручного вывода
    import json as _json
    me_examples = _json.load(open(os.path.join(os.path.dirname(HERE_DIR), "docs", "examples", "me.json"), encoding="utf-8"))
    for name, example in me_examples.items():
        if name.startswith("_"):
            continue
        assert not shape_errors(ME, example), (name, "пример /api/me не совпадает с контрактом", shape_errors(ME, example))
        assert example["active_game"] in (None, "mines", "blackjack", "crash", "hilo")
    for uid, expected in ((5002, "mines"), (5004, "crash"), (5005, "crash")):
        r = ca.get("/api/me", headers=auth(uid))
        contract("GET /api/me %s" % expected, r.json(), ME)
        assert r.json()["active_game"] == expected, (uid, r.json())
    r3 = ca.get("/api/me", headers=auth(5003)).json()
    state3 = ca.get("/api/blackjack/state", headers=auth(5003)).json()
    assert r3["active_game"] == ("blackjack" if state3["status"] == "active" else None), (r3, state3["status"])   # блэкджек мог решиться сразу
    # просроченная игра закрывается до ответа: active_game уже null
    sql(pa, "UPDATE mines_games SET updated_at = ? WHERE status = 'active'", (NOW - 3 * 86400,))
    assert ca.get("/api/me", headers=auth(5002)).json()["active_game"] is None
    # несколько активных: берётся та, где действие позже
    add_player(pa, 5006)
    ca.post("/api/mines/start", headers=auth(5006), json={"request_id": rid(5006), "bet": 10, "mines": 3})
    sql(pa, "UPDATE mines_games SET updated_at = ? WHERE telegram_id = 5006", (int(time.time()) - 100,))
    with _mock.patch.object(_cr, "new_crash", return_value=5000):
        ca.post("/api/crash/start", headers=auth(5006), json={"request_id": rid(5007), "bet": 10})
    assert ca.get("/api/me", headers=auth(5006)).json()["active_game"] == "crash"
    # хило: незавершённая партия видна в /api/me (возобновление после перезапуска)
    add_player(pa, 5008)
    import hilo as _hl
    with _mock.patch.object(_hl, "draw_card", return_value=(7, "H")):
        ca.post("/api/hilo/start", headers=auth(5008), json={"request_id": rid(5008), "bet": 10})
    r = ca.get("/api/me", headers=auth(5008))
    contract("GET /api/me хило", r.json(), ME)
    assert r.json()["active_game"] == "hilo", r.json()
finally:
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

# сам проверяющий код: расхождения находятся (защита от пустого теста)
assert shape_errors({"a": int}, {"a": 1, "b": 2}) == ["$: лишнее поле b"]
assert shape_errors({"a": int, "b": str}, {"a": 1}) == ["$: нет поля b"]
assert shape_errors({"a": int}, {"a": True}) and shape_errors({"a": bool}, {"a": 1})
assert shape_errors({"a": OPT(int)}, {"a": None}) == [] and shape_errors({"a": int}, {"a": None})
assert shape_errors({"a": [int]}, {"a": [1, "x"]}) and shape_errors({"a": [int]}, {"a": []}) == []

print("Все проверки прошли")
