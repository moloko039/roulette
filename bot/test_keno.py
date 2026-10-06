import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from fractions import Fraction
from math import comb
from unittest import mock

from fastapi.testclient import TestClient

import bot
import db
import keno
import levels
import ratelimit
import wallet
import xp
from api import create_app
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds
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


class FixedRng:
    """Розыгрыш для тестов: заданные 10 чисел."""

    def __init__(self, numbers):
        self.numbers = list(numbers)

    def sample(self, population, k):
        assert k == 10 and set(self.numbers) <= set(population), (k, self.numbers)
        return list(self.numbers)


tmp = tempfile.mkdtemp()
counter = [0]
SECRET_PICKS = [11, 22, 33]
DRAW = [2, 3, 7, 9, 15, 18, 22, 27, 31, 40]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "k%d.db" % counter[0])
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


def add_player(path, uid, balance=100_000, total=0, last_accrual=NOW):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, 0, 0)",
        (uid, balance, last_accrual, last_accrual, total))


def balance(path, uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def rid(n):
    return "keno-req-%04d" % n


try:
    # ================= таблица множителей =================
    check("таблица ровно как в постановке", keno.PAYTABLE, {
        1: {1: 389},
        2: {2: 1686},
        3: {2: 213, 3: 5607},
        4: {3: 988, 4: 25405},
        5: {3: 367, 4: 4064, 5: 76217},
        6: {3: 213, 4: 1656, 5: 12869, 6: 100000},
        7: {4: 1249, 5: 5385, 6: 23205, 7: 100000},
        8: {4: 711, 5: 2450, 6: 8436, 7: 29045, 8: 100000},
        9: {4: 450, 5: 1326, 6: 3907, 7: 11515, 8: 33934, 9: 100000},
        10: {5: 1589, 6: 3639, 7: 8332, 8: 19077, 9: 43677, 10: 100000},
    })
    total = comb(40, 10)
    rtps = {}
    for k, row in keno.PAYTABLE.items():
        rtp = sum(Fraction(comb(k, h) * comb(40 - k, 10 - h), total) * Fraction(m, 100) for h, m in row.items())
        rtps[k] = rtp
        assert rtp <= Fraction(36, 37), "возврат выше 36/37 при k=%d: %s" % (k, float(rtp))
        assert rtp >= Fraction(97, 100), "возврат ниже 0,97 при k=%d: %s" % (k, float(rtp))
        assert all(m >= 100 for m in row.values()), "платный множитель ниже 1.00 при k=%d" % k
        values = [row[h] for h in sorted(row)]
        assert all(a < b for a, b in zip(values, values[1:])), "множители не растут с h при k=%d" % k
        assert max(row.values()) <= 100000, "множитель выше 1000"
        assert all(1 <= h <= k for h in row), "h вне 1..k при k=%d" % k
    check("число k", sorted(keno.PAYTABLE), list(range(1, 11)))
    print("возврат по k: " + ", ".join("%d=%.4f" % (k, float(r)) for k, r in sorted(rtps.items())))
    check("наибольший множитель", keno.MAX_MULT_X100, 100000)
    # наибольшая выплата намного ниже MAX_SAFE_INT
    assert keno.KENO_MAX_BET * keno.MAX_MULT_X100 // 100 == 10 ** 12 < MAX_SAFE_INT // 1000

    # ================= правила =================
    check("текст множителя", [keno.multiplier_text(m) for m in (0, 100, 389, 1686, 100000)], ["0.00", "1.00", "3.89", "16.86", "1000.00"])
    check("paytable_text", keno.paytable_text()["3"], {"2": "2.13", "3": "56.07"})
    check("paytable_text: все k", sorted(keno.paytable_text(), key=int), [str(k) for k in range(1, 11)])
    check("выплата", (keno.payout(100, 3, 2), keno.payout(100, 3, 1), keno.payout(7, 3, 2), keno.payout(1, 1, 1),
                      keno.payout(10 ** 9, 10, 10)), (213, 0, 14, 3, 10 ** 12))
    check("множитель вне таблицы", keno.multiplier_x100(5, 2), 0)
    raises(ValueError, keno.payout, 10, 3, 4)
    raises(ValueError, keno.payout, 10, 0, 0)
    raises(ValueError, keno.payout, -1, 3, 1)
    # выбор чисел
    check("выбор сортируется", keno.validate_picks([5, 1, 40]), [1, 5, 40])
    for bad in ([], list(range(1, 12)), [0], [41], [1, 1], [True], [1.0], ["1"], None, "1", {"a": 1}, [None], [-1], [1, 2, 2]):
        raises(keno.InvalidPicks, keno.validate_picks, bad)
    check("10 чисел можно", keno.validate_picks(list(range(1, 11))), list(range(1, 11)))
    # розыгрыш
    for _ in range(200):
        d = keno.draw_numbers()
        assert len(d) == 10 and len(set(d)) == 10 and d == sorted(d) and all(1 <= n <= 40 for n in d), d
    check("розыгрыш из rng", keno.draw_numbers(FixedRng(reversed(DRAW))), DRAW)
    import random
    with mock.patch.object(random, "SystemRandom", wraps=random.SystemRandom) as sr:
        keno.draw_numbers()
        assert sr.called, "розыгрыш не через SystemRandom"
    check("совпадения", keno.play([22, 3, 5], DRAW), [3, 22])
    # проверка частот: розыгрыш равномерный (число 1..40 встречается примерно в четверти раундов)
    freq = [0] * 41
    for _ in range(4000):
        for n in keno.draw_numbers():
            freq[n] += 1
    assert all(800 < freq[n] < 1200 for n in range(1, 41)), freq[1:]

    # ================= опыт =================
    for k, ref in ((1, 0.75), (2, 0.9423), (4, 0.9583), (6, 0.8472), (10, 0.9502)):
        assert abs(keno.lose_combinations(k) / total - ref) < 0.0001, (k, keno.lose_combinations(k) / total)
    for k in range(1, 11):
        lose = sum(comb(k, h) * comb(40 - k, 10 - h) for h in range(k + 1) if h not in keno.PAYTABLE[k])
        check("L при k=%d" % k, keno.lose_combinations(k), lose)
        for bet in (1, 100, 12345, 10 ** 9):
            check("xp k=%d bet=%d" % (k, bet), xp.keno_xp(bet, k), bet * lose // total)
            assert 0 <= xp.keno_xp(bet, k) <= bet
    check("xp: k=1", xp.keno_xp(1_000_000, 1), 750_000)
    raises(ValueError, xp.keno_xp, -1, 3)
    raises(ValueError, xp.keno_xp, 10, 11)

    # ================= раунд: выигрыш, проигрыш, кошелёк =================
    path = new_db()
    add_player(path, 1, balance=10_000, last_accrual=NOW + 10 * DAY)
    calls = []
    real_debit, real_credit = wallet.debit, wallet.credit
    with mock.patch.object(wallet, "debit", side_effect=lambda *a: (calls.append("debit"), real_debit(*a))[1]), \
            mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        win = db.play_keno(1, rid(1), 100, [7, 3, 12], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("через wallet: сначала списание, потом выплата", calls, ["debit", "credit"])
    check("ответ выигрыша", win, {
        "bet": 100, "picks": [3, 7, 12], "draw": DRAW, "hits": [3, 7], "hit_count": 2, "multiplier": "2.13", "payout": 213,
        "balance": 10_000 - 100 + 213, "level": levels.profile_level(xp.keno_xp(100, 3)), "xp": xp.keno_xp(100, 3), "replayed": False})
    check("баланс в базе", balance(path, 1), 10_113)
    check("total_staked и xp", sql(path, "SELECT total_staked, xp FROM players WHERE telegram_id = 1")[0], (100, xp.keno_xp(100, 3)))
    check("запись раунда", sql(path, "SELECT telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at FROM keno_rounds")[0],
          (1, rid(1), 100, "[3,7,12]", json.dumps(DRAW, separators=(",", ":")), 2, 213, NOW))
    calls.clear()
    with mock.patch.object(wallet, "debit", side_effect=lambda *a: (calls.append("debit"), real_debit(*a))[1]), \
            mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        lose = db.play_keno(1, rid(2), 100, [1, 5, 12], now=NOW + 1, db_path=path, rng=FixedRng(DRAW))
    check("проигрыш: только списание", calls, ["debit"])
    check("ответ проигрыша", (lose["hits"], lose["hit_count"], lose["multiplier"], lose["payout"], lose["balance"], lose["replayed"]),
          ([], 0, "0.00", 0, 10_013, False))
    check("xp суммируется", sql(path, "SELECT total_staked, xp FROM players WHERE telegram_id = 1")[0], (200, 2 * xp.keno_xp(100, 3)))
    # k = 1 и k = 10
    one = db.play_keno(1, rid(3), 10, [22], now=NOW + 2, db_path=path, rng=FixedRng(DRAW))
    check("k=1 выигрыш", (one["multiplier"], one["payout"], one["hit_count"]), ("3.89", 38, 1))
    ten = db.play_keno(1, rid(4), 100, [2, 3, 7, 9, 15, 18, 22, 27, 31, 40], now=NOW + 3, db_path=path, rng=FixedRng(DRAW))
    check("k=10 джекпот", (ten["multiplier"], ten["payout"], ten["hit_count"]), ("1000.00", 100_000, 10))
    # ставка и выплата целые, округление вниз
    r = db.play_keno(1, rid(5), 7, [3, 7, 12], now=NOW + 4, db_path=path, rng=FixedRng(DRAW))
    check("округление вниз", r["payout"], 14)

    # ================= идемпотентность =================
    path = new_db()
    add_player(path, 1, balance=10_000, last_accrual=NOW + 10 * DAY)
    first = db.play_keno(1, rid(10), 100, [3, 7, 12], now=NOW, db_path=path, rng=FixedRng(DRAW))
    bal_after = balance(path, 1)
    again = db.play_keno(1, rid(10), 100, [12, 3, 7], now=NOW + 5, db_path=path, rng=FixedRng([1, 2, 4, 5, 6, 8, 10, 11, 13, 14]))
    check("повтор: тот же розыгрыш и выплата", {k: v for k, v in again.items() if k != "replayed"},
          {k: v for k, v in first.items() if k != "replayed"})
    check("повтор: replayed", (first["replayed"], again["replayed"]), (False, True))
    check("повтор ничего не списал", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0],
                                      sql(path, "SELECT total_staked FROM players")[0][0]), (bal_after, 1, 100))
    raises(keno.RequestConflict, db.play_keno, 1, rid(10), 101, [3, 7, 12], now=NOW, db_path=path)
    raises(keno.RequestConflict, db.play_keno, 1, rid(10), 100, [3, 7, 13], now=NOW, db_path=path)
    check("после конфликта всё цело", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0]), (bal_after, 1))
    # другой игрок с тем же request_id: отдельный раунд
    add_player(path, 2, balance=500, last_accrual=NOW + 10 * DAY)
    other = db.play_keno(2, rid(10), 50, [1], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("request_id у каждого игрока свой", (other["replayed"], other["bet"]), (False, 50))

    # ================= ошибки и откат =================
    path = new_db()
    add_player(path, 1, balance=500, last_accrual=NOW + 10 * DAY)
    raises(InsufficientFunds, db.play_keno, 1, rid(20), 501, [1], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("нехватка: ничего не изменилось", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0],
                                             sql(path, "SELECT total_staked, xp FROM players")[0]), (500, 0, (0, 0)))
    # ставка на весь баланс
    allin = db.play_keno(1, rid(21), 500, [1, 2], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("ставка на весь баланс: 500 -> 0 (проигрыш)", (allin["balance"], allin["payout"]), (0, 0))
    raises(InsufficientFunds, db.play_keno, 1, rid(22), 1, [1], now=NOW, db_path=path, rng=FixedRng(DRAW))
    # плохие аргументы
    for bad_bet in (0, -5, 10 ** 9 + 1, 1.5, "10", True, None):
        raises(ValueError, db.play_keno, 1, rid(23), bad_bet, [1], now=NOW, db_path=path)
    raises(ValueError, db.play_keno, 1, rid(23), 10, [], now=NOW, db_path=path)
    # сбой на выплате откатывает всё (списание тоже)
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("boom")):
        raises(RuntimeError, db.play_keno, 1, rid(24), 100, [3, 7, 12], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("сбой откатывает списание", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0],
                                       sql(path, "SELECT total_staked, xp FROM players")[0]), (1000, 0, (0, 0)))
    # предел точных чисел: баланс рядом с MAX_SAFE_INT
    path = new_db()
    add_player(path, 1, balance=MAX_SAFE_INT - 10, last_accrual=NOW + 10 * DAY)
    raises(BalanceLimit, db.play_keno, 1, rid(25), 100, [3, 7, 12], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("balance_limit: ничего не изменилось", balance(path, 1), MAX_SAFE_INT - 10)
    # самая большая ставка и самая большая выплата
    path = new_db()
    add_player(path, 1, balance=10 ** 9, last_accrual=NOW + 10 * DAY)
    big = db.play_keno(1, rid(26), 10 ** 9, DRAW, now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("максимальная выплата 10**12", (big["payout"], big["balance"]), (10 ** 12, 10 ** 12))
    # начисление по часам тем же способом, что в рулетке: потратить можно и только что начисленное
    path = new_db()
    add_player(path, 1, balance=0, last_accrual=NOW - 3 * 3600)
    r = db.play_keno(1, rid(27), 300, [1], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("начислено 3 часа и списана ставка", (r["balance"], sql(path, "SELECT last_accrual FROM players")[0][0]), (0, NOW // 60 * 60))   # метка на границе минуты
    # новый игрок регистрируется (стартовые 1000)
    path = new_db()
    r = db.play_keno(77, rid(28), 100, [22], now=NOW, db_path=path, rng=FixedRng(DRAW))
    check("новый игрок: 1000 - 100 + 389", r["balance"], 1000 - 100 + 389)

    # ================= API =================
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "60",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "60"}),
                                clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    def play(body, uid=SECRET_ID):
        return client.post("/api/keno/play", headers=auth(uid), json=body)

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "keno.json"), encoding="utf-8"))

    def spec_of(value):
        """Форма из примера: объект с теми же ключами, типы значений те же."""
        if isinstance(value, dict):
            return {k: spec_of(v) for k, v in value.items()}
        if isinstance(value, list):
            return [spec_of(value[0]) if value else int]
        return type(value)

    def same_shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for key, spec in spec_of(example).items():
            v = body[key]
            if isinstance(spec, list):
                assert type(v) is list and all(type(x) is int for x in v), (name, key, v)
            else:
                assert type(v) is spec, "%s.%s: %r, ожидали %s" % (name, key, v, spec.__name__)

    check("без подписи", [client.get("/api/keno/paytable").status_code, client.post("/api/keno/play", json={}).status_code], [401, 401])
    p = client.get("/api/keno/paytable", headers=auth(SECRET_ID))
    check("paytable", (p.status_code, p.json()), (200, {"paytable": keno.paytable_text()}))
    check("paytable: пример совпадает с ответом", examples["paytable"], p.json())
    # 400
    good = {"request_id": rid(100), "bet": 100, "picks": [1, 2, 3]}
    bad_bodies = [
        {}, {"request_id": rid(100), "bet": 100}, dict(good, extra=1),
        dict(good, bet=0), dict(good, bet=10 ** 9 + 1), dict(good, bet=1.5), dict(good, bet="100"), dict(good, bet=True), dict(good, bet=None),
        dict(good, picks=[]), dict(good, picks=list(range(1, 12))), dict(good, picks=[0]), dict(good, picks=[41]),
        dict(good, picks=[1, 1]), dict(good, picks=[1.0]), dict(good, picks=["1"]), dict(good, picks=None), dict(good, picks=True),
        dict(good, request_id="short"), dict(good, request_id=5), dict(good, request_id="bad id with spaces!!"),
    ]
    for body in bad_bodies:
        r = play(body)
        check("400 %s" % json.dumps(body)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/keno/play", headers=dict(auth(SECRET_ID), **{"content-type": "application/json"}), content=b"not json")
    check("не JSON", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/keno/play", headers=auth(SECRET_ID), content=b"[" + b"1," * 40000 + b"1]")
    check("слишком большое тело", r.status_code, 413)
    check("при 400 ничего не списано", (balance(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0]), (SECRET_BALANCE, 0))
    # 200: выигрыш и проигрыш с подставленным розыгрышем
    with mock.patch.object(keno, "draw_numbers", return_value=DRAW):
        w = play({"request_id": rid(101), "bet": SECRET_BET, "picks": [12, 7, 3]})
        check("выигрыш 200", w.status_code, 200)
        wj = w.json()
        same_shape("play_win", wj, examples["play_win"])
        check("выигрыш: значения", (wj["picks"], wj["draw"], wj["hits"], wj["hit_count"], wj["multiplier"], wj["payout"], wj["replayed"]),
              ([3, 7, 12], DRAW, [3, 7], 2, "2.13", SECRET_BET * 213 // 100, False))
        check("выигрыш: баланс", wj["balance"], SECRET_BALANCE - SECRET_BET + SECRET_BET * 213 // 100)
        l = play({"request_id": rid(102), "bet": 10, "picks": [1, 5, 12]})
        lj = l.json()
        same_shape("play_loss", lj, examples["play_loss"])
        check("проигрыш: значения", (lj["hits"], lj["hit_count"], lj["multiplier"], lj["payout"]), ([], 0, "0.00", 0))
        # повтор по сети: тот же ответ, баланс не меняется
        rep = play({"request_id": rid(101), "bet": SECRET_BET, "picks": [3, 7, 12]})
        rj = rep.json()
        check("повтор: replayed", (rep.status_code, rj["replayed"]), (200, True))
        cur = ("replayed", "balance", "level", "xp")   # в повторе эти три значения текущие
        check("повтор: тот же раунд", {k: v for k, v in rj.items() if k not in cur}, {k: v for k, v in wj.items() if k not in cur})
        check("повтор: баланс текущий и не изменился", rj["balance"], lj["balance"])
        conflict = play({"request_id": rid(101), "bet": SECRET_BET + 1, "picks": [3, 7, 12]})
        check("request_conflict", (conflict.status_code, conflict.json()), (409, {"detail": "request_conflict"}))
        conflict = play({"request_id": rid(101), "bet": SECRET_BET, "picks": [3, 7, 13]})
        check("request_conflict по числам", (conflict.status_code, conflict.json()), (409, {"detail": "request_conflict"}))
        # нехватка фишек
        poor = play({"request_id": rid(103), "bet": 10 ** 9, "picks": [1]})
        check("insufficient_funds", (poor.status_code, poor.json()), (409, {"detail": "insufficient_funds"}))
        # ставка на весь баланс
        bal_now = lj["balance"]
        allin = play({"request_id": rid(104), "bet": bal_now, "picks": [1]})
        check("весь баланс", (allin.status_code, allin.json()["balance"]), (200, 0))
        zero = play({"request_id": rid(105), "bet": 1, "picks": [1]})
        check("после всего баланса 409", (zero.status_code, zero.json()), (409, {"detail": "insufficient_funds"}))
    # настоящий розыгрыш без подмены
    add_player(path, 555, balance=10_000, last_accrual=now_real)
    real = play({"request_id": rid(106), "bet": 10, "picks": [5, 6]}, uid=555).json()
    same_shape("play_real", real, examples["play_win"])
    assert len(real["draw"]) == 10 and real["draw"] == sorted(real["draw"]) and len(set(real["draw"])) == 10
    check("hits = пересечение", real["hits"], [n for n in [5, 6] if n in real["draw"]])
    for k in ("invalid_request", "insufficient_funds", "request_conflict", "balance_limit"):
        check("пример ошибки " + k, examples["errors"][k], {"detail": k})
    # ограничение частоты: группа write
    path2 = new_db()
    add_player(path2, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1",
                                                        "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path2, rate_limiter=lim2))
    codes = [c2.post("/api/keno/play", headers=auth(SECRET_ID), json={"request_id": rid(200 + i), "bet": 1, "picks": [1]}).status_code
             for i in range(5)]
    check("write: после лимита 429", codes[:3] + [c != 200 for c in codes[3:]], [200, 200, 200, True, True])
    r = c2.post("/api/keno/play", headers=auth(SECRET_ID), json={"request_id": rid(300), "bet": 1, "picks": [1]})
    check("429 тело и заголовок", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    reads = [c2.get("/api/keno/paytable", headers=auth(SECRET_ID)).status_code for _ in range(3)]
    check("read: свой счётчик (paytable), третий 429", reads, [200, 200, 429])
    check("429 не создал раундов сверх разрешённых", sql(path2, "SELECT COUNT(*) FROM keno_rounds")[0][0], 3)

    # ================= /mydata и /deletemydata =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=NOW + 10 * DAY)
    add_player(path, 801, balance=50_000, last_accrual=NOW + 10 * DAY)
    db.play_keno(801, rid(400), 700, [3, 7], now=NOW, db_path=path, rng=FixedRng(DRAW))
    db.play_keno(SECRET_ID, rid(401), SECRET_BET, SECRET_PICKS, now=NOW, db_path=path, rng=FixedRng(DRAW))
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: кено", export["keno_rounds"], [{"time": NOW, "bet": SECRET_BET, "picks": SECRET_PICKS, "draw": DRAW,
                                                    "hits": 1, "payout": keno.payout(SECRET_BET, 3, 1)}])
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    payload = json.loads(upd.effective_message.documents[0]["data"].decode("utf-8"))
    check("/mydata: раздел keno_rounds", len(payload["keno_rounds"]), 1)
    check("/mydata: чужих раундов нет", all(r["bet"] == SECRET_BET for r in payload["keno_rounds"]), True)
    for i in range(110):
        sql(path, "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
                  "VALUES (801, ?, 1, '[1]', '[1,2,3,4,5,6,7,8,9,10]', 1, 3, ?)", ("bulk-req-%04d" % i, NOW + 100 + i))
    check("в выгрузке не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["keno_rounds"]), 100)
    check("самые свежие первыми", db.get_player_export(801, rounds_limit=100, db_path=path)["keno_rounds"][0]["time"], NOW + 100 + 109)
    # игрок, у которого есть только раунды кено (нет строки players), всё равно получает выгрузку
    sql(path, "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
              "VALUES (950, 'lonely-req-1', 5, '[1]', '[1,2,3,4,5,6,7,8,9,10]', 1, 19, ?)", (NOW,))
    assert db.get_player_export(950, db_path=path) is not None
    others = sql(path, "SELECT COUNT(*) FROM keno_rounds WHERE telegram_id = 801")[0][0]
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалены раунды кено", counts["keno_rounds"], 1)
    check("раундов игрока нет, чужие целы", (sql(path, "SELECT COUNT(*) FROM keno_rounds WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                            sql(path, "SELECT COUNT(*) FROM keno_rounds WHERE telegram_id = 801")[0][0]), (0, others))
    # сообщения
    add_player(path, 900, balance=5000, last_accrual=NOW + 10 * DAY)
    db.play_keno(900, rid(402), 100, [3], now=NOW, db_path=path, rng=FixedRng(DRAW))
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    assert "история раундов кено" in bot.DELETE_WARNING, bot.DELETE_WARNING
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "раунды кено — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    # человек, у которого были только раунды кено, не получает «нечего удалять»
    sql(path, "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
              "VALUES (951, 'lonely-req-2', 5, '[1]', '[1,2,3,4,5,6,7,8,9,10]', 1, 19, ?)", (NOW,))
    check("только кено: удаляется", db.delete_player_data(951, db_path=path)["keno_rounds"], 1)
    os.environ.pop("DB_PATH", None)

    # ================= очистка =================
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    ins = ("INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
           "VALUES (1, ?, 10, '[1]', '[1,2,3,4,5,6,7,8,9,10]', 1, 38, ?)")
    sql(path, ins, ("old-req-0001", NOW - 40 * DAY))
    sql(path, ins, ("old-req-0002", NOW - 31 * DAY))
    sql(path, ins, ("old-req-0003", NOW - 29 * DAY))
    sql(path, ins, ("old-req-0004", NOW - 5 * DAY))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка: старше 30 дней", (deleted["keno_rounds"], sql(path, "SELECT COUNT(*) FROM keno_rounds")[0][0]), (2, 2))
    check("очистка: игрок цел", sql(path, "SELECT COUNT(*) FROM players")[0][0], 1)
    # срок защиты request_id не меньше 2 суток
    sql(path, ins, ("old-req-0005", NOW - 1 * DAY))
    db.purge_old_data(now=NOW, db_path=path, rounds_days=0)
    check("срок не короче 2 суток: раунд суточной давности остаётся", sql(path, "SELECT request_id FROM keno_rounds"), [("old-req-0005",)])

    # ================= миграция идемпотентна =================
    path = new_db()
    db.init_db(path)
    db.init_db(path)
    check("таблица есть", sql(path, "SELECT name FROM sqlite_master WHERE name = 'keno_rounds'"), [("keno_rounds",)])

    # ================= политика и документы =================
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "раундов в кено" in privacy and "История раундов кено хранится 30 дней" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert "/api/keno/play" in api_doc and "/api/keno/paytable" in api_doc

    # ================= в логах нет id, ставок, балансов, чисел и request_id =================
    secrets_in_log = [str(SECRET_ID), str(SECRET_BET), str(SECRET_BALANCE), rid(101), rid(401), "keno-req-"]
    for secret in secrets_in_log:
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
