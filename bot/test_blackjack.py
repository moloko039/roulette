import asyncio
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import blackjack as bj
import bot
import db
import ratelimit
import wallet
import xp
from api import create_app
from roulette import InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
DAY = 86400
SECRET_ID, SECRET_BET, SECRET_BALANCE = 424242421, 4242, 7654321

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)


class Stack:
    """Колода для тестов: заданные карты идут первыми (игрок, дилер, игрок, дилер, дальше добор), остальное как есть."""

    def __init__(self, cards):
        self.cards = list(cards)

    def shuffle(self, shoe):
        for c in reversed(self.cards):
            shoe.remove(c)
            shoe.insert(0, c)


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "b%d.db" % counter[0])
    db.init_db(path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=100_000, total=0, last_accrual=NOW + 10 * DAY):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, 0, 0)", (uid, balance, last_accrual, last_accrual, total))


def balance(path, uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def rid(n):
    return "bj-req-%05d" % n


def start(path, uid, n, bet, stack, now=NOW):
    return db.blackjack_start(uid, rid(n), bet, now=now, db_path=path, rng=Stack(stack))


def act(path, uid, n, action, now=NOW):
    return db.blackjack_action(uid, rid(n), action, now=now, db_path=path)


try:
    # ================= очки =================
    check("жёсткие", [bj.hand_value(c) for c in (["10S", "7H"], ["9S", "9H", "3C"], ["KS", "QH", "2C"], ["5S", "6H"])],
          [(17, False), (21, False), (22, False), (11, False)])
    check("мягкие", [bj.hand_value(c) for c in (["AS", "6H"], ["AS", "AH"], ["AS", "6H", "5C"], ["AS", "KH"], ["AS", "6H", "KC"])],
          [(17, True), (12, True), (12, False), (21, True), (17, False)])
    check("три туза", bj.hand_value(["AS", "AH", "AD"]), (13, True))
    check("туз 11 или 1 как выгоднее", bj.hand_value(["AS", "AH", "9D"]), (21, True))
    check("четыре туза и 7", bj.hand_value(["AS", "AH", "AD", "AC", "7S"]), (21, True))
    check("блэкджек только с двух карт", [bj.is_blackjack(c) for c in (["AS", "KH"], ["AS", "10H"], ["5S", "6H", "KC"], ["KS", "QH"])],
          [True, True, False, False])
    check("значения карт", [bj.card_value(c) for c in ("AS", "2H", "9D", "10C", "JS", "QH", "KD")], [11, 2, 9, 10, 10, 10, 10])
    shoe = bj.new_shoe()
    check("6 колод", (len(shoe), len(set(shoe))), (312, 52))
    assert all(shoe.count(c) == 6 for c in set(shoe))
    import random as _r
    with mock.patch.object(_r, "SystemRandom", wraps=_r.SystemRandom) as sr:
        bj.new_shoe()
        assert sr.called, "тасовка не через SystemRandom"
    assert bj.new_shoe() != bj.new_shoe(), "колоды одинаковые"

    # ================= движок: блэкджек, дилер, выплаты =================
    def play(bet, stack, actions=()):
        s = bj.start(bet, bj.new_shoe(Stack(stack)))
        for a in actions:
            bj.act(s, a)
        return s

    s = play(101, ["AS", "9C", "KD", "7H"])
    check("блэкджек игрока 3:2 вниз", (s["status"], s["result"], s["payout"]), ("finished", "blackjack", 101 + 101 * 3 // 2))
    check("3:2 при 100", play(100, ["AS", "9C", "KD", "7H"])["payout"], 250)
    check("блэкджек дилера (туз)", (lambda x: (x["status"], x["result"], x["payout"]))(play(100, ["9S", "AC", "7D", "KH"])), ("finished", "lose", 0))
    check("блэкджек дилера (десятка)", (lambda x: (x["status"], x["result"], x["payout"]))(play(100, ["9S", "KC", "7D", "AH"])), ("finished", "lose", 0))
    check("у обоих: ничья", (lambda x: (x["result"], x["payout"]))(play(100, ["AS", "AC", "KD", "KH"])), ("push", 100))
    check("21 из трёх карт не блэкджек", play(100, ["5S", "9C", "6D", "7H"], ["hit"])["status"] in ("active", "finished"), True)
    s = play(100, ["5S", "9C", "6D", "7H", "KS"], ["hit"])    # 5+6+10 = 21, не блэкджек; дилер 16 (ещё не ходил)
    check("21 тремя картами: игра продолжается", (s["status"], bj.hand_value(s["player"])), ("active", (21, False)))
    s = bj.act(s, "stand")
    check("и платит как обычная победа (дилер 16 + добор)", s["result"] in ("win", "dealer_bust", "push"), True)
    # игра дилера
    s = play(100, ["10S", "AC", "8D", "6H"], ["stand"])       # у дилера мягкие 17: стоит
    check("дилер стоит на мягких 17", (len(s["dealer"]), bj.hand_value(s["dealer"]), s["result"], s["payout"]), (2, (17, True), "win", 200))
    s = play(100, ["10S", "10C", "9D", "6H", "5S"], ["stand"])  # дилер 16 берёт 5 = 21
    check("дилер берёт до 17", (s["dealer"], s["result"], s["payout"]), (["10C", "6H", "5S"], "lose", 0))
    s = play(100, ["10S", "10C", "9D", "6H", "KS"], ["stand"])
    check("перебор дилера", (s["result"], s["payout"]), ("dealer_bust", 200))
    s = play(100, ["10S", "10C", "8D", "7H"], ["stand"])      # 18 против 17
    check("победа 2:1 ставки", (s["result"], s["payout"]), ("win", 200))
    s = play(100, ["10S", "10C", "7D", "7H"], ["stand"])
    check("ничья возвращает ставку", (s["result"], s["payout"]), ("push", 100))
    s = play(100, ["10S", "10C", "6D", "7H"], ["stand"])
    check("проигрыш", (s["result"], s["payout"]), ("lose", 0))
    s = play(100, ["10S", "6C", "6D", "9H", "KD"], ["hit"])
    check("перебор игрока", (s["status"], s["result"], s["payout"], s["dealer"]), ("finished", "bust", 0, ["6C", "9H"]))
    # удвоение
    s = play(100, ["5S", "6C", "6D", "10H", "10D", "7C"], ["double"])   # 11 + 10 = 21; дилер 16 + 7 = 23
    check("удвоение: wager и выплата", (s["wager"], s["result"], s["payout"], len(s["player"])), (200, "dealer_bust", 400, 3))
    s = play(100, ["5S", "6C", "6D", "10H", "10D", "7C"])
    check("double только на двух картах", (bj.can_double(s), bj.legal_actions(s, 1000), bj.legal_actions(s, 99)),
          (True, ["hit", "stand", "double"], ["hit", "stand"]))
    bj.act(s, "hit")
    check("после hit double нельзя", (bj.can_double(s), bj.legal_actions(s, 1000)), (False, ["hit", "stand"]))
    raises(bj.InvalidAction, bj.act, s, "double")
    raises(bj.InvalidAction, bj.act, s, "split")
    s = play(100, ["10S", "6C", "6D", "10H", "KD"], ["double"])   # 16 + король
    check("удвоение с перебором", (s["result"], s["payout"], s["wager"]), ("bust", 0, 200))
    s = play(100, ["5S", "6C", "6D", "10H", "10D", "10C"], ["double"])  # 21 против 16 + 10
    check("удвоение выигрыш", (s["result"], s["payout"]), ("dealer_bust", 400))
    s = play(100, ["10S", "10C", "9D", "9H"], ["stand"])
    raises(bj.NoActiveGame, bj.act, s, "hit")
    check("XP", [bj.xp_for(w) for w in (0, 1, 100, 200, 10 ** 9)], [0, 0, 48, 96, 480_000_000])
    check("xp.blackjack_xp", xp.blackjack_xp(250), 250 * 12 // 25)
    raises(ValueError, bj.xp_for, -1)
    raises(ValueError, bj.start, 0, bj.new_shoe())
    raises(ValueError, bj.start, 10 ** 9 + 1, bj.new_shoe())
    # скрытая карта в ответе движка
    s = play(100, ["10S", "10C", "9D", "6H"])
    v = bj.view(s, 500, 1, 0)
    check("активная: вторая карта дилера null, очки по открытой", (v["dealer"], v["actions"], v["result"], v["payout"]),
          ({"cards": ["10C", None], "total": 10}, ["hit", "stand", "double"], None, None))
    assert "shoe" not in json.dumps(v) and "deck" not in json.dumps(v)
    check("туз открыт: 11", bj.view(play(100, ["10S", "AC", "9D", "6H"]), 500, 1, 0)["dealer"]["total"], 11)

    # ================= база: раздача, кошелёк, статистика =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    calls = []
    real_debit, real_credit = wallet.debit, wallet.credit
    with mock.patch.object(wallet, "debit", side_effect=lambda *a: (calls.append("debit"), real_debit(*a))[1]), \
            mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        r = start(path, 1, 1, 100, ["10S", "10C", "9D", "9H"])
    check("активная раздача: баланс списан, выплат нет", (r["status"], balance(path, 1), calls), ("active", 9_900, ["debit"]))
    check("ответ активной", (r["bet"], r["wager"], r["player"], r["dealer"], r["actions"], r["result"], r["payout"], r["balance"], r["auto"], r["replayed"]),
          (100, 100, {"cards": ["10S", "9D"], "total": 19, "soft": False}, {"cards": ["10C", None], "total": 10}, ["hit", "stand", "double"],
           None, None, 9_900, False, False))
    check("total_staked при старте", sql(path, "SELECT total_staked, xp FROM players")[0], (100, 0))
    deck = sql(path, "SELECT deck_json, deck_pos FROM blackjack_games")[0]
    assert len(json.loads(deck[0])) == 312 and deck[1] == 4, "колода хранится у активной раздачи"
    # одна активная
    e = raises(bj.ActiveGameExists, start, path, 1, 2, 50, ["10S", "10C", "9D", "9H"])
    check("код", e.code, "active_game_exists")
    check("второй старт ничего не списал", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0]), (9_900, 1))
    # состояние
    st = db.blackjack_state(1, now=NOW, db_path=path)
    check("state = активная раздача", {k: v for k, v in st.items() if k != "replayed"}, {k: v for k, v in r.items() if k != "replayed"})
    # stand: дилер 10C + 9H = 19 против 19 -> ничья
    calls.clear()
    with mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        f = act(path, 1, 3, "stand")
    check("ничья: возврат ставки", (f["status"], f["result"], f["payout"], f["balance"], f["actions"], calls), ("finished", "push", 100, 10_000, [], ["credit"]))
    check("колода очищена после конца", sql(path, "SELECT deck_json FROM blackjack_games")[0][0], "")
    check("опыт за раздачу и finished_at", (sql(path, "SELECT xp FROM players")[0][0], sql(path, "SELECT finished_at FROM blackjack_games")[0][0]), (48, NOW))
    check("финиш: колоды в ответе нет", "shoe" in json.dumps(f) or "deck" in json.dumps(f), False)
    st = db.blackjack_state(1, now=NOW, db_path=path)
    check("state после конца: последняя раздача", (st["status"], st["result"], st["dealer"]["cards"]), ("finished", "push", ["10C", "9H"]))
    raises(bj.NoActiveGame, act, path, 1, 4, "hit")
    raises(bj.NoActiveGame, act, path, 1, 5, "double")
    # победа, перебор, удвоение с балансом
    path = new_db()
    add_player(path, 1, balance=1_000)
    start(path, 1, 1, 100, ["5S", "6C", "6D", "10H", "10D", "7C"])
    r = act(path, 1, 2, "double")
    check("удвоение: списана вторая ставка, выплата 4x", (r["wager"], r["result"], r["payout"], r["balance"]), (200, "dealer_bust", 400, 1_000 - 200 + 400))
    check("total_staked: ставка и удвоение", sql(path, "SELECT total_staked FROM players")[0][0], 200)
    check("xp от общей ставки", sql(path, "SELECT xp FROM players")[0][0], 200 * 12 // 25)
    # блэкджек у игрока при старте
    path = new_db()
    add_player(path, 1, balance=1_000)
    r = start(path, 1, 1, 101, ["AS", "9C", "KD", "7H"])
    check("блэкджек: сразу выплата 3:2", (r["status"], r["result"], r["payout"], r["balance"], r["actions"]), ("finished", "blackjack", 252, 1_000 - 101 + 252, []))
    check("колода не хранится", sql(path, "SELECT deck_json FROM blackjack_games")[0][0], "")
    # блэкджек у дилера: проигрыш, удвоения нет
    path = new_db()
    add_player(path, 1, balance=1_000)
    r = start(path, 1, 1, 100, ["9S", "AC", "7D", "KH"])
    check("у дилера блэкджек", (r["status"], r["result"], r["payout"], r["balance"], r["dealer"]["cards"], r["dealer"]["total"]), ("finished", "lose", 0, 900, ["AC", "KH"], 21))
    check("ставка не удваивалась", (r["wager"], sql(path, "SELECT total_staked, xp FROM players")[0]), (100, (100, 48)))
    # у обоих: ничья
    r = start(path, 1, 2, 100, ["AS", "AC", "KD", "KH"])
    check("у обоих: возврат", (r["result"], r["payout"], r["balance"]), ("push", 100, 900))
    # удвоение не хватает баланса
    path = new_db()
    add_player(path, 1, balance=150)
    start(path, 1, 1, 100, ["5S", "6C", "6D", "10H", "10D", "7C"])
    st = db.blackjack_state(1, now=NOW, db_path=path)
    check("actions без double при нехватке баланса", st["actions"], ["hit", "stand"])
    raises(InsufficientFunds, act, path, 1, 2, "double")
    check("нехватка: всё как было", (balance(path, 1), sql(path, "SELECT wager, status FROM blackjack_games")[0], sql(path, "SELECT total_staked FROM players")[0][0],
                                     sql(path, "SELECT COUNT(*) FROM blackjack_actions")[0][0]), (50, (100, "active"), 100, 1))
    # double после hit: invalid_action, ничего не списано
    path = new_db()
    add_player(path, 1, balance=1_000)
    start(path, 1, 1, 100, ["2S", "6C", "3D", "10H", "2D", "9C"])
    act(path, 1, 2, "hit")
    e = raises(bj.InvalidAction, act, path, 1, 3, "double")
    check("код", e.code, "invalid_action")
    check("invalid_action ничего не списал", (balance(path, 1), sql(path, "SELECT wager FROM blackjack_games")[0][0]), (900, 100))
    # ставка на весь баланс
    path = new_db()
    add_player(path, 1, balance=500)
    r = start(path, 1, 1, 500, ["10S", "10C", "9D", "9H"])
    check("ставка на весь баланс", (r["balance"], r["actions"]), (0, ["hit", "stand"]))
    raises(InsufficientFunds, start, path, 2, 2, 1100, ["10S", "10C", "9D", "9H"])
    # ошибки аргументов
    for bad in (0, -5, 10 ** 9 + 1, 1.5, "10", True, None):
        raises(ValueError, db.blackjack_start, 1, rid(9), bad, now=NOW, db_path=path)
    raises(ValueError, db.blackjack_action, 1, rid(9), "split", now=NOW, db_path=path)
    # нехватка на старте
    path = new_db()
    add_player(path, 1, balance=50)
    raises(InsufficientFunds, start, path, 1, 1, 100, ["10S", "10C", "9D", "9H"])
    check("нехватка на старте: ничего нет", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0], sql(path, "SELECT COUNT(*) FROM blackjack_actions")[0][0]), (50, 0, 0))
    # сбой на выплате откатывает всё
    path = new_db()
    add_player(path, 1, balance=1_000)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("boom")):
        raises(RuntimeError, start, path, 1, 1, 100, ["AS", "9C", "KD", "7H"])
    check("откат старта", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0], sql(path, "SELECT total_staked, xp FROM players")[0]), (1_000, 0, (0, 0)))
    # начисление по часам и регистрация
    path = new_db()
    add_player(path, 1, balance=0, last_accrual=NOW - 3 * 3600)
    r = start(path, 1, 1, 300, ["10S", "10C", "9D", "9H"])
    check("начислено 3 часа, списана ставка", (r["balance"], sql(path, "SELECT last_accrual FROM players")[0][0]), (0, NOW // 60 * 60))   # метка на границе минуты
    path = new_db()
    r = start(path, 77, 1, 100, ["10S", "10C", "9D", "9H"])
    check("новый игрок: 1000 - 100", r["balance"], 900)

    # ================= идемпотентность =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    first = start(path, 1, 10, 100, ["5S", "6C", "6D", "10H", "10D", "7C"])
    again = db.blackjack_start(1, rid(10), 100, now=NOW + 5, db_path=path, rng=Stack(["KS", "KC", "KD", "KH"]))
    check("повтор старта: тот же ответ", {k: v for k, v in again.items() if k != "replayed"}, {k: v for k, v in first.items() if k != "replayed"})
    check("replayed", (first["replayed"], again["replayed"]), (False, True))
    check("повтор ничего не списал", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0], sql(path, "SELECT total_staked FROM players")[0][0]), (9_900, 1, 100))
    e = raises(bj.RequestConflict, db.blackjack_start, 1, rid(10), 101, now=NOW, db_path=path)
    check("код", e.code, "request_conflict")
    raises(bj.RequestConflict, act, path, 1, 10, "hit")        # тот же request_id, другое действие
    h1 = act(path, 1, 11, "hit")
    h2 = act(path, 1, 11, "hit")
    check("повтор действия: тот же ответ, карта одна", ({k: v for k, v in h2.items() if k != "replayed"}, h2["replayed"], len(h2["player"]["cards"])),
          ({k: v for k, v in h1.items() if k != "replayed"}, True, 3))
    raises(bj.RequestConflict, act, path, 1, 11, "stand")
    check("в базе три карты игрока", len(json.loads(sql(path, "SELECT player_json FROM blackjack_games")[0][0])), 3)
    add_player(path, 2, balance=500)
    o = start(path, 2, 10, 50, ["10S", "10C", "9D", "9H"])
    check("request_id у каждого игрока свой", (o["replayed"], o["bet"]), (False, 50))

    # ================= автозакрытие =================
    path = new_db()
    add_player(path, 1, balance=1_000)
    add_player(path, 2, balance=1_000)
    start(path, 1, 1, 100, ["10S", "10C", "8D", "9H"], now=NOW)           # игрок 18, дилер 19: auto stand -> проигрыш
    start(path, 2, 1, 100, ["10S", "10C", "9D", "7H", "KS"], now=NOW)     # игрок 19, дилер 17: auto stand -> победа
    check("до срока ничего не закрывается", (db.settle_expired_blackjack(1, now=NOW + DAY - 1, db_path=path),
                                              db.close_expired_blackjack(now=NOW + DAY - 1, db_path=path)), (False, 0))
    st = db.blackjack_state(1, now=NOW + DAY, db_path=path)
    check("state закрыл просроченную", (st["status"], st["result"], st["auto"], st["payout"], st["balance"], st["actions"]), ("finished", "lose", True, 0, 900, []))
    check("колода очищена", sql(path, "SELECT deck_json FROM blackjack_games WHERE telegram_id = 1")[0][0], "")
    check("фоновая задача закрыла вторую", db.close_expired_blackjack(now=NOW + DAY, db_path=path), 1)
    r2 = db.blackjack_state(2, now=NOW + DAY, db_path=path)
    check("авто-победа", (r2["result"], r2["payout"], r2["balance"], r2["auto"]), ("win", 200, 1_100, True))
    check("повторное закрытие невозможно", (db.close_expired_blackjack(now=NOW + 2 * DAY, db_path=path), sql(path, "SELECT xp FROM players WHERE telegram_id = 2")[0][0]), (0, 48))
    # первым шагом действия: просроченная раздача закрывается, новое действие получает no_active_game
    path = new_db()
    add_player(path, 1, balance=1_000)
    start(path, 1, 1, 100, ["10S", "10C", "8D", "9H"], now=NOW)
    raises(bj.NoActiveGame, act, path, 1, 2, "hit", now=NOW + DAY + 5)
    check("закрыта первым шагом действия", sql(path, "SELECT status, result, auto FROM blackjack_games")[0], ("finished", "lose", 1))
    # старт после просрочки закрывает старую и начинает новую
    path = new_db()
    add_player(path, 1, balance=1_000)
    start(path, 1, 1, 100, ["10S", "10C", "8D", "9H"], now=NOW)
    r = start(path, 1, 2, 100, ["10S", "10C", "9D", "9H"], now=NOW + DAY + 5)
    check("новая раздача после автозакрытия", (r["status"], sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0]), ("active", 2))
    # действие обновляет срок
    path = new_db()
    add_player(path, 1, balance=1_000)
    start(path, 1, 1, 100, ["2S", "10C", "3D", "9H", "2D", "2C"], now=NOW)
    act(path, 1, 2, "hit", now=NOW + DAY - 10)
    check("после действия срок отсчитывается заново", db.settle_expired_blackjack(1, now=NOW + DAY + 100, db_path=path), False)
    check("а через сутки от действия закрывается", db.settle_expired_blackjack(1, now=NOW + 2 * DAY, db_path=path), True)
    # пачки
    path = new_db()
    for u in range(1, 7):
        add_player(path, u, balance=1_000)
        start(path, u, 1, 10, ["10S", "10C", "8D", "9H"], now=NOW)
    check("пачка 2", db.close_expired_blackjack(now=NOW + DAY, db_path=path, batch=2), 2)
    check("остаток", db.close_expired_blackjack(now=NOW + DAY, db_path=path, batch=200), 4)

    # ================= API =================
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "100",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "100"}), clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    def post(name, body, uid=SECRET_ID):
        return client.post("/api/blackjack/" + name, headers=auth(uid), json=body)

    def state(uid=SECRET_ID):
        return client.get("/api/blackjack/state", headers=auth(uid))

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "blackjack.json"), encoding="utf-8"))

    def types_of(value):
        if isinstance(value, dict):
            return {k: types_of(v) for k, v in value.items()}
        if isinstance(value, list):
            return [{type(x) for x in value} or {int}]
        return type(value)

    def same_shape(name, body, example, path_="$"):
        if isinstance(example, dict):
            assert isinstance(body, dict) and set(body) == set(example), "%s %s: поля %s, в примере %s" % (
                name, path_, sorted(body) if isinstance(body, dict) else body, sorted(example))
            for k in example:
                same_shape(name, body[k], example[k], path_ + "." + k)
        elif isinstance(example, list):
            assert isinstance(body, list), (name, path_, body)
            ok = {type(x) for x in example}
            for x in body:
                assert type(x) in ok, "%s %s: элемент %r, в примере типы %s" % (name, path_, x, ok)
        else:
            assert type(body) is type(example), "%s %s: %r, в примере %s" % (name, path_, body, type(example).__name__)

    check("без подписи", [client.get("/api/blackjack/state").status_code, client.post("/api/blackjack/start", json={}).status_code,
                          client.post("/api/blackjack/action", json={}).status_code], [401, 401, 401])
    s = state()
    check("state без игры", (s.status_code, s.json()["status"]), (200, "none"))
    same_shape("none", s.json(), examples["none"])
    check("none: баланс игрока", s.json()["balance"], SECRET_BALANCE)
    # 400
    good = {"request_id": rid(100), "bet": 100}
    for body in ({}, {"request_id": rid(100)}, dict(good, extra=1), dict(good, bet=0), dict(good, bet=10 ** 9 + 1), dict(good, bet=1.5),
                 dict(good, bet="100"), dict(good, bet=True), dict(good, bet=None), dict(good, request_id="short"), dict(good, request_id=5)):
        r = post("start", body)
        check("400 start %s" % json.dumps(body)[:50], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    for body in ({}, {"request_id": rid(101)}, {"request_id": rid(101), "action": "split"}, {"request_id": rid(101), "action": 5},
                 {"request_id": rid(101), "action": "hit", "extra": 1}, {"request_id": "x", "action": "hit"}, {"request_id": rid(101), "action": None}):
        r = post("action", body)
        check("400 action %s" % json.dumps(body)[:50], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/blackjack/start", headers=dict(auth(SECRET_ID), **{"content-type": "application/json"}), content=b"nope")
    check("не JSON", r.status_code, 400)
    check("при 400 ничего не списано", (balance(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM blackjack_games")[0][0]), (SECRET_BALANCE, 0))
    r = post("action", {"request_id": rid(102), "action": "hit"})
    check("action без игры: 409 no_active_game", (r.status_code, r.json()), (409, {"detail": "no_active_game"}))
    # подставляем колоду: игрок 5S+6D, дилер 10C + 7H; double -> 10D
    with mock.patch("blackjack.new_shoe", side_effect=lambda rng=None: (lambda sh: (Stack(["5S", "10C", "6D", "7H", "10D"]).shuffle(sh), sh)[1])(bj.DECKS and [r_ + s_ for _ in range(6) for s_ in bj.SUITS for r_ in bj.RANKS])):
        st = post("start", {"request_id": rid(103), "bet": SECRET_BET})
        check("start 200", st.status_code, 200)
        sj = st.json()
        same_shape("active", sj, examples["active"])
        check("активная раздача", (sj["status"], sj["player"]["cards"], sj["dealer"], sj["actions"], sj["result"], sj["payout"], sj["balance"], sj["replayed"]),
              ("active", ["5S", "6D"], {"cards": ["10C", None], "total": 10}, ["hit", "stand", "double"], None, None, SECRET_BALANCE - SECRET_BET, False))
        text = json.dumps(sj)
        assert "shoe" not in text and "deck" not in text and "7H" not in text, "скрытая карта или колода в ответе"
        # state во время игры: тоже без скрытой карты
        g = state()
        gj = g.json()
        same_shape("state active", gj, examples["active"])
        assert "7H" not in json.dumps(gj) and gj["dealer"]["cards"][1] is None
        check("state = start", {k: v for k, v in gj.items() if k != "replayed"}, {k: v for k, v in sj.items() if k != "replayed"})
        # active_game_exists
        r = post("start", {"request_id": rid(104), "bet": 10})
        check("active_game_exists", (r.status_code, r.json()), (409, {"detail": "active_game_exists"}))
        # повтор старта по сети
        rep = post("start", {"request_id": rid(103), "bet": SECRET_BET})
        check("повтор start", (rep.status_code, rep.json()["replayed"], {k: v for k, v in rep.json().items() if k != "replayed"}),
              (200, True, {k: v for k, v in sj.items() if k != "replayed"}))
        conflict = post("start", {"request_id": rid(103), "bet": SECRET_BET + 1})
        check("request_conflict", (conflict.status_code, conflict.json()), (409, {"detail": "request_conflict"}))
        # double: 5+6+10 = 21, дилер 17 -> победа
        d = post("action", {"request_id": rid(105), "action": "double"})
        dj = d.json()
        check("double 200", d.status_code, 200)
        same_shape("finished", dj, examples["finished_win"])
        check("итог удвоения", (dj["status"], dj["wager"], dj["result"], dj["payout"], dj["dealer"]["cards"], dj["actions"], dj["balance"]),
              ("finished", 2 * SECRET_BET, "win", 4 * SECRET_BET, ["10C", "7H"], [], SECRET_BALANCE - 2 * SECRET_BET + 4 * SECRET_BET))
        check("опыт за раздачу", dj["xp"], 2 * SECRET_BET * 12 // 25)
        gj = state().json()
        same_shape("state finished", gj, examples["finished_win"])
        check("state после конца", (gj["status"], gj["result"], gj["auto"]), ("finished", "win", False))
        rep = post("action", {"request_id": rid(105), "action": "double"})
        check("повтор action", (rep.json()["replayed"], rep.json()["balance"]), (True, dj["balance"]))
        check("request_conflict по действию", post("action", {"request_id": rid(105), "action": "hit"}).json(), {"detail": "request_conflict"})
    # блэкджек сразу: форма
    with mock.patch("blackjack.new_shoe", side_effect=lambda rng=None: (lambda sh: (Stack(["AS", "9C", "KD", "7H"]).shuffle(sh), sh)[1])([r_ + s_ for _ in range(6) for s_ in bj.SUITS for r_ in bj.RANKS])):
        b = post("start", {"request_id": rid(106), "bet": 100}).json()
    same_shape("blackjack", b, examples["finished_blackjack"])
    check("блэкджек", (b["result"], b["payout"], b["player"]["soft"]), ("blackjack", 250, True))
    # invalid_action и insufficient_funds по API
    with mock.patch("blackjack.new_shoe", side_effect=lambda rng=None: (lambda sh: (Stack(["2S", "10C", "3D", "7H", "2D", "9C"]).shuffle(sh), sh)[1])([r_ + s_ for _ in range(6) for s_ in bj.SUITS for r_ in bj.RANKS])):
        post("start", {"request_id": rid(107), "bet": 100})
        h = post("action", {"request_id": rid(108), "action": "hit"})
        check("hit", (h.status_code, h.json()["player"]["cards"]), (200, ["2S", "3D", "2D"]))
        r = post("action", {"request_id": rid(109), "action": "double"})
        check("invalid_action", (r.status_code, r.json()), (409, {"detail": "invalid_action"}))
        post("action", {"request_id": rid(110), "action": "stand"})
    add_player(path, 555, balance=150, last_accrual=now_real)
    with mock.patch("blackjack.new_shoe", side_effect=lambda rng=None: (lambda sh: (Stack(["5S", "10C", "6D", "7H"]).shuffle(sh), sh)[1])([r_ + s_ for _ in range(6) for s_ in bj.SUITS for r_ in bj.RANKS])):
        post("start", {"request_id": rid(111), "bet": 100}, uid=555)
        check("double без баланса не предлагается", state(555).json()["actions"], ["hit", "stand"])
        r = post("action", {"request_id": rid(112), "action": "double"}, uid=555)
        check("insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
        r = post("start", {"request_id": rid(113), "bet": 100}, uid=555)
        check("active_game_exists у бедного", r.json(), {"detail": "active_game_exists"})
    add_player(path, 556, balance=10, last_accrual=now_real)
    r = post("start", {"request_id": rid(114), "bet": 100}, uid=556)
    check("start insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    for k, v in examples["errors"].items():
        check("пример ошибки " + k, v, {"detail": k})
    # реальная раздача без подмены колоды: форма
    add_player(path, 600, balance=10_000, last_accrual=now_real)
    real = post("start", {"request_id": rid(115), "bet": 10}, uid=600).json()
    same_shape("real start", real, examples["active"] if real["status"] == "active" else examples["finished_blackjack"])
    # /api/me закрывает просроченную раздачу
    sql(path, "UPDATE blackjack_games SET updated_at = ? WHERE telegram_id = 600 AND status = 'active'", (int(time.time()) - 2 * DAY,))
    if real["status"] == "active":
        client.get("/api/me", headers=auth(600))
        check("/api/me закрыл просроченную", sql(path, "SELECT status, auto FROM blackjack_games WHERE telegram_id = 600")[0], ("finished", 1))
    # ограничение частоты: write и read
    path2 = new_db()
    add_player(path2, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1", "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}),
                                 clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path2, rate_limiter=lim2))
    codes = [c2.post("/api/blackjack/start", headers=auth(SECRET_ID), json={"request_id": rid(200 + i), "bet": 1}).status_code for i in range(5)]
    check("write 429 после лимита", [c == 429 for c in codes], [False, False, False, True, True])
    r = c2.post("/api/blackjack/action", headers=auth(SECRET_ID), json={"request_id": rid(300), "action": "hit"})
    check("429 тело", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    reads = [c2.get("/api/blackjack/state", headers=auth(SECRET_ID)).status_code for _ in range(3)]
    check("read: третий 429", reads, [200, 200, 429])

    # ================= /mydata и /deletemydata =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE)
    add_player(path, 801, balance=50_000)
    start(path, 801, 1, 700, ["10S", "10C", "9D", "9H"])
    start(path, SECRET_ID, 2, SECRET_BET, ["10S", "10C", "9D", "9H"])        # активная
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("активная раздача: только факт, без карт", (export["blackjack_games"], export["blackjack_active"]), ([], True))
    act(path, SECRET_ID, 3, "stand")
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("завершённая в выгрузке", (export["blackjack_active"], export["blackjack_games"]),
          (False, [{"created_at": NOW, "bet": SECRET_BET, "wager": SECRET_BET, "player_cards": ["10S", "9D"], "dealer_cards": ["10C", "9H"],
                    "result": "push", "payout": SECRET_BET, "finished_at": NOW}]))
    start(path, SECRET_ID, 4, 50, ["8S", "KC", "9D", "9H"])                      # снова активная: скрытая карта 9H
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    text = upd.effective_message.documents[0]["data"].decode("utf-8")
    payload = json.loads(text)
    check("/mydata: блэкджек", (len(payload["blackjack_games"]), payload["blackjack_active"]), (1, True))
    assert "shoe" not in text and "deck" not in text and "8S" not in text, "карты активной раздачи в выгрузке"
    for i in range(110):
        sql(path, "INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, status, result, payout, created_at, updated_at, finished_at) "
                  "VALUES (801, 1, 1, '', 4, '[\"2S\",\"3S\"]', '[\"2D\",\"3D\"]', 'finished', 'lose', 0, ?, ?, ?)", (NOW + 100 + i,) * 3)
    check("не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["blackjack_games"]), 100)
    others = sql(path, "SELECT COUNT(*) FROM blackjack_games WHERE telegram_id = 801")[0][0]
    others_act = sql(path, "SELECT COUNT(*) FROM blackjack_actions WHERE telegram_id = 801")[0][0]
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалены раздачи (и активная)", counts["blackjack_games"], 2)
    check("раздач и действий игрока нет, чужие целы", (sql(path, "SELECT COUNT(*) FROM blackjack_games WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                                      sql(path, "SELECT COUNT(*) FROM blackjack_actions WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                                      sql(path, "SELECT COUNT(*) FROM blackjack_games WHERE telegram_id = 801")[0][0],
                                                      sql(path, "SELECT COUNT(*) FROM blackjack_actions WHERE telegram_id = 801")[0][0]), (0, 0, others, others_act))
    add_player(path, 900, balance=5000)
    start(path, 900, 5, 100, ["10S", "10C", "9D", "9H"])
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    assert "незавершённая раздача блэкджека (вместе со ставкой)" in bot.DELETE_WARNING and "история раздач блэкджека" in bot.DELETE_WARNING, bot.DELETE_WARNING
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "раздачи блэкджека — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)

    # ================= очистка =================
    path = new_db()
    add_player(path, 1, balance=1000)
    ins = ("INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, status, result, payout, created_at, updated_at, finished_at) "
           "VALUES (1, 10, 10, '', 4, '[]', '[]', ?, ?, 0, ?, ?, ?)")
    sql(path, ins, ("finished", "lose", NOW - 40 * DAY, NOW - 40 * DAY, NOW - 40 * DAY))
    sql(path, ins, ("finished", "lose", NOW - 31 * DAY, NOW - 31 * DAY, NOW - 31 * DAY))
    sql(path, ins, ("finished", "lose", NOW - 5 * DAY, NOW - 5 * DAY, NOW - 5 * DAY))
    sql(path, "INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, status, created_at, updated_at) "
              "VALUES (2, 10, 10, '[]', 4, '[]', '[]', 'active', ?, ?)", (NOW - 90 * DAY, NOW - 90 * DAY))
    for i, age in enumerate((40, 31, 5)):
        sql(path, "INSERT INTO blackjack_actions VALUES (1, ?, 'start', '{}', '{}', ?)", ("old-act-%04d" % i, NOW - age * DAY))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка: старые раздачи и действия", (deleted["blackjack_games"], deleted["blackjack_actions"]), (2, 2))
    check("свежая и активная остались", sorted(r[0] for r in sql(path, "SELECT status FROM blackjack_games")), ["active", "finished"])
    db.purge_old_data(now=NOW + 400 * DAY, db_path=path, rounds_days=30)
    check("активная не удаляется никогда", [r[0] for r in sql(path, "SELECT status FROM blackjack_games")], ["active"])

    # ================= миграция и политика =================
    path = new_db()
    db.init_db(path)
    db.init_db(path)
    check("таблицы есть", sorted(r[0] for r in sql(path, "SELECT name FROM sqlite_master WHERE name LIKE 'blackjack_%' AND type = 'table'")), ["blackjack_actions", "blackjack_games"])
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "раздач в блэкджек" in privacy and "История раздач блэкджека хранится 30 дней" in privacy and "закрывается автоматически: вы останавливаетесь" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert all(p in api_doc for p in ("/api/blackjack/start", "/api/blackjack/action", "/api/blackjack/state"))
    # единственный уникальный индекс: вторая активная невозможна на уровне базы
    path = new_db()
    add_player(path, 1)
    start(path, 1, 1, 10, ["10S", "10C", "9D", "9H"])
    try:
        sql(path, "INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, status, created_at, updated_at) VALUES (1, 1, 1, '', 0, '[]', '[]', 'active', 1, 1)")
        raise AssertionError("вторая активная раздача записалась")
    except sqlite3.IntegrityError:
        pass

    # ================= симуляция: базовая стратегия, фиксированный seed =================
    def basic_strategy(state):
        """hit/stand/double без сплита; на двух картах удвоение по таблице 6 колод и стоянием дилера на мягких 17."""
        total, soft = bj.hand_value(state["player"])
        up = bj.card_value(state["dealer"][0])
        first_two = len(state["player"]) == 2
        if soft:
            if total >= 20:
                return "stand"
            if total == 19:
                return "double" if (first_two and up == 6) else "stand"    # «D/S» против 6, иначе стоит
            if total == 18:
                if first_two and 3 <= up <= 6:
                    return "double"
                return "stand" if up in (2, 7, 8) else ("hit" if up in (9, 10, 11) else "stand")
            if total == 17:
                return "double" if (first_two and 3 <= up <= 6) else "hit"
            if total in (15, 16):
                return "double" if (first_two and 4 <= up <= 6) else "hit"
            return "double" if (first_two and 5 <= up <= 6) else "hit"      # мягкие 13, 14
        if total >= 17:
            return "stand"
        if total >= 13:
            return "stand" if up <= 6 else "hit"
        if total == 12:
            return "stand" if 4 <= up <= 6 else "hit"
        if total == 11:
            return "double" if first_two else "hit"
        if total == 10:
            return "double" if (first_two and up <= 9) else "hit"
        if total == 9:
            return "double" if (first_two and 3 <= up <= 6) else "hit"
        return "hit"

    HANDS = 300_000
    BET = 100   # чётная ставка: 3:2 без потери на округлении
    rng = random.Random(20261004)
    staked = returned = full_loss = 0
    outcomes = {r: 0 for r in bj.RESULTS}
    for _ in range(HANDS):
        st = bj.start(BET, bj.new_shoe(rng))
        while st["status"] == "active":
            action = basic_strategy(st)
            if action == "double" and not bj.can_double(st):
                action = "hit"
            bj.act(st, action)
        staked += BET
        returned += st["payout"] - st["wager"]
        outcomes[st["result"]] += 1
        if st["payout"] == 0:
            full_loss += 1
    edge = returned / staked
    loss_share = full_loss / HANDS
    print("симуляция %d раздач: средний итог на ставку %.4f, доля полной потери %.4f, исходы %s" % (HANDS, edge, loss_share, outcomes))
    assert -0.014 <= edge <= -0.002, "средний итог на ставку вне -1,4%% ... -0,2%%: %.4f" % edge
    assert 0.46 <= loss_share <= 0.50, "доля полных потерь не около 0,48: %.4f" % loss_share

    # ================= в логах нет id, ставок, балансов, карт и request_id =================
    for secret in (str(SECRET_ID), str(SECRET_BET), str(SECRET_BALANCE), rid(103), rid(105), "bj-req-", "7H", "shoe"):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:80]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
