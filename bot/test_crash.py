import asyncio
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import time
from fractions import Fraction
from unittest import mock

from fastapi.testclient import TestClient

import bot
import crash
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
NOW_MS = NOW * 1000
DAY = 86400
SECRET_ID, SECRET_BET, SECRET_BALANCE = 424242421, 4242, 7654321
SECRET_CRASH = 123457          # точка краха, которая не должна нигде появиться до конца раунда

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

tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "c%d.db" % counter[0])
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


def add_player(path, uid, balance=100_000, last_accrual=NOW + 10 * DAY):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, 0, 0, 0)", (uid, balance, last_accrual, last_accrual))


def balance(path, uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def rid(n):
    return "cr-req-%05d" % n


def start(path, uid, n, bet, crash_x100, target=None, at=NOW_MS):
    with mock.patch.object(crash, "new_crash", return_value=crash_x100):
        return db.crash_start(uid, rid(n), bet, target, now_ms=at, db_path=path)


def cashout(path, uid, n, at):
    return db.crash_cashout(uid, rid(n), now_ms=at, db_path=path)


def at_eff(eff_ms):
    """Момент запроса, при котором эффективное время равно eff_ms (раунд начат в NOW_MS)."""
    return NOW_MS + crash.GRACE_MS + eff_ms


try:
    # ================= множитель по времени =================
    check("контрольные", [crash.m100(t) for t in (0, 6000, 12000)], [100, 200, 400])
    check("отрицательное время", crash.m100(-5), 100)
    check("предел достигается", (crash.m100(59794), crash.m100(59795), crash.m100(10 ** 9)), (99991, 100000, 100000))
    assert all(crash.m100(t) <= crash.m100(t + 1) for t in range(0, 70000, 7)), "множитель не убывает"
    assert max(crash.m100(t) for t in range(0, 80000, 13)) == crash.CAP_X100
    check("эффективное время", (crash.effective_ms(1000, 0), crash.effective_ms(100, 0), crash.effective_ms(0, 5000)), (850, 0, 0))
    check("текст", [crash.text(v) for v in (0, 100, 101, 12345, 100000)], ["0.00", "1.00", "1.01", "123.45", "1000.00"])
    check("константы", (crash.DOUBLING_MS, crash.CAP_X100, crash.MIN_TARGET_X100, crash.GRACE_MS, crash.M), (6000, 100000, 101, 150, 2 ** 53))

    # ================= точка краха =================
    check("u=0 даёт 1.00", crash.crash_from_u(0), 100)
    check("u=M-1 ограничено", crash.crash_from_u(crash.M - 1), 10 ** 9)
    M = crash.M
    for t in (101, 150, 200, 500, 1000, 5000, 100000):
        # вероятность crash_x100 >= t: число u, при которых 3600*M // (37*(M-u)) >= t, делённое на M
        n_u = 3600 * M // (37 * t)
        edge = M - n_u
        assert crash.crash_from_u(edge) >= t and crash.crash_from_u(edge - 1) < t, "граница u для t=%d" % t
    rtp_report = []
    for t in (101, 150, 200, 500, 1000, 5000, 100000):
        p_win = Fraction(3600 * M // (37 * t), M)
        rtp = Fraction(t, 100) * p_win
        assert rtp <= Fraction(36, 37), "возврат выше 36/37 при t=%d" % t
        assert Fraction(36, 37) - rtp <= Fraction(1, 10 ** 9), "возврат ниже 36/37 - 1e-9 при t=%d" % t
        assert p_win <= Fraction(3600, 37 * t), "P(win) выше 36/(37k), k = t/100"
        rtp_report.append("%d=%.12f" % (t, float(rtp)))
    print("возврат по цели (36/37 = %.12f): %s" % (36 / 37, ", ".join(rtp_report)))
    check("P(win) цели 2.00 около 0,4865", round(float(Fraction(3600 * M // (37 * 200), M)), 4), 0.4865)
    # выборка с фиксированным seed
    rng = random.Random(20261004)
    sample = sorted(crash.new_crash(rng) for _ in range(200_000))
    share_one = sum(1 for v in sample if v == 100) / len(sample)
    median = sample[len(sample) // 2] / 100
    print("выборка 200000: доля crash_x100 == 100 = %.4f, медиана = %.3fx, максимум = %d" % (share_one, median, sample[-1]))
    exact_one = 1 - Fraction(3600 * M // (37 * 101), M)    # P(crash_x100 == 100) = P(значение < 1.01)
    assert abs(share_one - float(exact_one)) < 0.004, (share_one, float(exact_one))
    assert 1.90 <= median <= 2.00, median
    assert sample[-1] <= 10 ** 9 and sample[0] >= 100
    assert crash.new_crash() >= 100          # SystemRandom по умолчанию
    import random as _r
    with mock.patch.object(_r, "SystemRandom", wraps=_r.SystemRandom) as sr:
        crash.new_crash()
        assert sr.called, "точка краха не через SystemRandom"

    # ================= исход, выплата, XP =================
    check("авто: цель == краху выигрывает", crash.decide_auto(200, 200), ("win", 200))
    check("авто: цель выше краха проигрывает", crash.decide_auto(201, 200), ("lose", 0))
    check("авто: ниже краха", crash.decide_auto(150, 200), ("win", 150))
    raises(ValueError, crash.decide_auto, 100, 500)
    raises(ValueError, crash.decide_auto, 100001, 500)
    check("выплата", (crash.payout(100, 150), crash.payout(7, 150), crash.payout(10 ** 9, 100000)), (150, 10, 10 ** 12))
    check("XP цели 200", crash.xp_for(10_000, 200), 10_000 * (7400 - 3600) // 7400)
    assert abs(crash.xp_for(10 ** 6, 200) / 10 ** 6 - 0.5135) < 0.0005
    assert abs(crash.xp_for(10 ** 6, 101) / 10 ** 6 - 0.03666) < 0.0005
    assert abs(crash.xp_for(10 ** 6, crash.CAP_X100) / 10 ** 6 - 0.999) < 0.0005
    check("XP не отрицателен", crash.xp_for(100, 1), 0)
    check("m для XP", (crash.xp_multiplier("auto", "win", 200, 200), crash.xp_multiplier("auto", "lose", 0, 350),
                       crash.xp_multiplier("manual", "win", 180, None), crash.xp_multiplier("manual", "lose", 0, None)),
          (200, 350, 180, crash.CAP_X100))
    check("xp.crash_xp", xp.crash_xp(250, 300), crash.xp_for(250, 300))
    # правила времени
    check("не разбился", crash.settle(500, 0, crash.GRACE_MS + 6000), None)       # m=200 <= 500
    check("разбился", crash.settle(150, 0, crash.GRACE_MS + 6000), ("lose", 0))   # m=200 > 150
    check("ровно на краху ещё жив", crash.settle(200, 0, crash.GRACE_MS + 6000), None)
    check("предел с crash >= CAP: выигрыш", crash.settle(crash.CAP_X100, 0, crash.GRACE_MS + 59795), ("win", crash.CAP_X100))
    check("предел с crash < CAP: проигрыш", crash.settle(crash.CAP_X100 - 1, 0, crash.GRACE_MS + 59795), ("lose", 0))
    check("брошенный выше предела", crash.settle(10 ** 9, 0, 80_000), ("win", crash.CAP_X100))
    check("брошенный ниже предела", crash.settle(5000, 0, 80_000), ("lose", 0))
    raises(crash.TooEarly, crash.cashout_multiplier, 0, crash.GRACE_MS + 50)
    check("вывод на 1.01", crash.cashout_multiplier(0, crash.GRACE_MS + 87), 101)

    # ================= база: ручной раунд =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    calls = []
    real_debit, real_credit = wallet.debit, wallet.credit
    with mock.patch.object(wallet, "debit", side_effect=lambda *a: (calls.append("debit"), real_debit(*a))[1]), \
            mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        r = start(path, 1, 1, 100, SECRET_CRASH)
        check("активный: форма", {k: r[k] for k in ("status", "mode", "bet", "target", "elapsed_ms", "crash_multiplier", "result", "multiplier", "payout", "balance", "auto", "replayed")},
              {"status": "active", "mode": "manual", "bet": 100, "target": None, "elapsed_ms": 0, "crash_multiplier": None, "result": None,
               "multiplier": None, "payout": None, "balance": 9_900, "auto": False, "replayed": False})
        check("константы в ответе", (r["doubling_ms"], r["cap"]), (6000, "1000.00"))
        check("списание через wallet", calls, ["debit"])
        text = json.dumps(r)
        assert str(SECRET_CRASH) not in text and "1234.57" not in text, "точка краха в ответе активного раунда"
        check("total_staked", sql(path, "SELECT total_staked, xp FROM players")[0], (100, 0))
        raises(crash.TooEarly, cashout, path, 1, 2, at_eff(50))
        check("too_early: игра активна, ничего не списано", (sql(path, "SELECT status FROM crash_games")[0][0], balance(path, 1),
                                                           sql(path, "SELECT COUNT(*) FROM crash_actions")[0][0]), ("active", 9_900, 1))
        s = db.crash_state(1, now_ms=at_eff(2500), db_path=path)
        check("state активного: elapsed по эффективному времени", (s["status"], s["elapsed_ms"], s["crash_multiplier"], s["balance"]), ("active", 2500, None, 9_900))
        assert str(SECRET_CRASH) not in json.dumps(s)
        w = cashout(path, 1, 3, at_eff(6000))
    check("вывод на 2.00: выплата 200", (w["status"], w["result"], w["multiplier"], w["payout"], w["balance"], w["elapsed_ms"], w["auto"]),
          ("finished", "win", "2.00", 200, 10_100, None, False))
    check("вывод: выплата через wallet", calls, ["debit", "credit"])
    check("точка краха открыта после конца", w["crash_multiplier"], "1234.57")
    check("XP ручного выигрыша (m=200)", sql(path, "SELECT xp FROM players")[0][0], crash.xp_for(100, 200))
    s = db.crash_state(1, now_ms=at_eff(7000), db_path=path)
    check("state после конца: последний раунд", (s["status"], s["result"], s["payout"]), ("finished", "win", 200))
    raises(crash.NoActiveGame, cashout, path, 1, 4, at_eff(8000))

    # граница: вывод ровно на множителе краха выигрывает, на следующем мс-шаге (m=201) уже поздно
    path = new_db()
    add_player(path, 1, balance=10_000)
    start(path, 1, 1, 100, 200)
    w = cashout(path, 1, 2, at_eff(6000))
    check("c == crash выигрывает", (w["result"], w["multiplier"], w["payout"]), ("win", "2.00", 200))
    add_player(path, 2, balance=10_000)
    start(path, 2, 1, 100, 200)
    l = cashout(path, 2, 2, at_eff(6044))             # m100 = 201 > 200: раунд уже разбился
    check("c > crash: итог раунда проигрыш, не ошибка", (l["status"], l["result"], l["multiplier"], l["payout"], l["auto"], l["crash_multiplier"]),
          ("finished", "lose", "0.00", 0, True, "2.00"))
    check("баланс проигравшего", balance(path, 2), 9_900)
    check("XP проигранного ручного (m=CAP)", sql(path, "SELECT xp FROM players WHERE telegram_id = 2")[0][0], crash.xp_for(100, crash.CAP_X100))
    # мгновенный крах 1.00
    add_player(path, 3, balance=10_000)
    start(path, 3, 1, 100, 100)
    raises(crash.TooEarly, cashout, path, 3, 2, at_eff(86))
    check("крах 1.00 состояние до 1.01 активно", db.crash_state(3, now_ms=at_eff(86), db_path=path)["status"], "active")
    check("после 1.01 разбился", (lambda x: (x["status"], x["result"], x["crash_multiplier"]))(db.crash_state(3, now_ms=at_eff(87), db_path=path)), ("finished", "lose", "1.00"))
    # предел
    add_player(path, 4, balance=10_000)
    start(path, 4, 1, 100, crash.CAP_X100)
    c = cashout(path, 4, 2, at_eff(59795))
    check("достиг предела при crash >= CAP: выигрыш ×1000", (c["result"], c["multiplier"], c["payout"], c["auto"]), ("win", "1000.00", 100_000, True))
    add_player(path, 5, balance=10_000)
    start(path, 5, 1, 100, crash.CAP_X100 - 1)
    check("crash < CAP у предела: проигрыш", db.crash_state(5, now_ms=at_eff(59795), db_path=path)["result"], "lose")
    # цель ровно на пределе: авто
    add_player(path, 6, balance=10_000)
    r = start(path, 6, 1, 100, crash.CAP_X100, target=crash.CAP_X100)
    check("авто на ×1000 выигрывает при crash >= CAP", (r["result"], r["payout"]), ("win", 100_000))

    # ================= режим авто =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    calls.clear()
    with mock.patch.object(wallet, "debit", side_effect=lambda *a: (calls.append("debit"), real_debit(*a))[1]), \
            mock.patch.object(wallet, "credit", side_effect=lambda *a: (calls.append("credit"), real_credit(*a))[1]):
        r = start(path, 1, 1, 100, 341, target=200)
    check("авто выигрыш", (r["status"], r["mode"], r["target"], r["result"], r["multiplier"], r["payout"], r["crash_multiplier"], r["elapsed_ms"], r["balance"]),
          ("finished", "auto", "2.00", "win", "2.00", 200, "3.41", None, 10_100))
    check("авто: списание и выплата", calls, ["debit", "credit"])
    check("XP авто (цель 200)", sql(path, "SELECT total_staked, xp FROM players")[0], (100, crash.xp_for(100, 200)))
    r = start(path, 1, 2, 100, 150, target=200)
    check("авто проигрыш", (r["result"], r["multiplier"], r["payout"], r["crash_multiplier"], r["balance"]), ("lose", "0.00", 0, "1.50", 10_000))
    check("XP авто и при проигрыше (цель)", sql(path, "SELECT xp FROM players")[0][0], 2 * crash.xp_for(100, 200))
    r = start(path, 1, 3, 100, 200, target=200)
    check("авто: цель == краху выигрывает", r["result"], "win")
    r = start(path, 1, 4, 100, 100, target=101)
    check("мгновенный крах проигрывает любую цель", r["result"], "lose")
    check("авто ничего не оставляет активным", sql(path, "SELECT COUNT(*) FROM crash_games WHERE status = 'active'")[0][0], 0)

    # ================= идемпотентность, ошибки, откат =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    first = start(path, 1, 10, 100, 5000)
    with mock.patch.object(crash, "new_crash", return_value=100):
        again = db.crash_start(1, rid(10), 100, None, now_ms=NOW_MS + 5, db_path=path)
    check("повтор старта: тот же ответ", {k: v for k, v in again.items() if k != "replayed"}, {k: v for k, v in first.items() if k != "replayed"})
    check("replayed", (first["replayed"], again["replayed"]), (False, True))
    check("повтор ничего не списал", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM crash_games")[0][0]), (9_900, 1))
    e = raises(crash.RequestConflict, db.crash_start, 1, rid(10), 101, None, now_ms=NOW_MS, db_path=path)
    check("код", e.code, "request_conflict")
    raises(crash.RequestConflict, db.crash_start, 1, rid(10), 100, 200, now_ms=NOW_MS, db_path=path)
    raises(crash.RequestConflict, cashout, path, 1, 10, at_eff(6000))
    e = raises(crash.ActiveGameExists, start, path, 1, 11, 50, 5000)
    check("код", e.code, "active_game_exists")
    check("второй старт ничего не списал", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM crash_games")[0][0]), (9_900, 1))
    c1 = cashout(path, 1, 12, at_eff(3000))
    c2 = cashout(path, 1, 12, at_eff(9000))
    check("повтор cashout: тот же ответ", ({k: v for k, v in c2.items() if k != "replayed"}, c2["replayed"]), ({k: v for k, v in c1.items() if k != "replayed"}, True))
    check("повтор не платит второй раз", balance(path, 1), 9_900 + crash.payout(100, 141))
    raises(crash.RequestConflict, db.crash_start, 1, rid(12), 100, None, now_ms=NOW_MS, db_path=path)
    add_player(path, 2, balance=500)
    o = start(path, 2, 10, 50, 5000)
    check("request_id у каждого игрока свой", (o["replayed"], o["bet"]), (False, 50))
    # нехватка, ставка на весь баланс, аргументы
    add_player(path, 3, balance=300)
    raises(InsufficientFunds, start, path, 3, 1, 301, 5000)
    check("нехватка: ничего нет", (balance(path, 3), sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = 3")[0][0]), (300, 0))
    r = start(path, 3, 2, 300, 5000, target=150)
    check("ставка на весь баланс (300 -> 450)", (r["result"], r["balance"]), ("win", 450))
    for bad in (0, -1, 10 ** 9 + 1, 1.5, "5", True, None):
        raises(ValueError, db.crash_start, 1, rid(20), bad, None, now_ms=NOW_MS, db_path=path)
    for bad in (100, 100001, 150.5, "200", True):
        raises(ValueError, db.crash_start, 1, rid(21), 10, bad, now_ms=NOW_MS, db_path=path)
    # сбой на выплате откатывает всё
    path = new_db()
    add_player(path, 1, balance=1000)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("boom")):
        raises(RuntimeError, start, path, 1, 1, 100, 500, 200)
    check("откат старта", (balance(path, 1), sql(path, "SELECT COUNT(*) FROM crash_games")[0][0], sql(path, "SELECT total_staked, xp FROM players")[0]), (1000, 0, (0, 0)))
    # начисление по часам и регистрация
    path = new_db()
    add_player(path, 1, balance=0, last_accrual=NOW - 3 * 3600)
    r = start(path, 1, 1, 300, 5000)
    check("начислено 3 часа, списана ставка", (r["balance"], sql(path, "SELECT last_accrual FROM players")[0][0]), (0, NOW))
    path = new_db()
    check("новый игрок: 1000 - 100", start(path, 77, 1, 100, 5000)["balance"], 900)

    # ================= автозакрытие и брошенные раунды =================
    path = new_db()
    for u in range(1, 7):
        add_player(path, u, balance=1000)
    start(path, 1, 1, 100, 5000)                       # брошен, crash < CAP: проигрыш
    start(path, 2, 1, 100, 10 ** 9)                    # брошен, crash >= CAP: выигрыш ×1000
    start(path, 3, 1, 100, 5000, at=NOW_MS + 60_000)   # свежий
    check("до срока ничего не закрывается", db.close_expired_crash(now_ms=NOW_MS + 5_000, db_path=path), 0)
    check("фоновая задача закрыла две брошенные", db.close_expired_crash(now_ms=NOW_MS + 71_000, db_path=path), 2)
    check("исходы брошенных", sorted(sql(path, "SELECT telegram_id, result, mult_x100, payout, auto FROM crash_games WHERE status = 'finished'")),
          [(1, "lose", 0, 0, 1), (2, "win", 100000, 100_000, 1)])
    check("балансы", (balance(path, 1), balance(path, 2)), (900, 900 + 100_000))
    check("XP брошенных (m=CAP)", (sql(path, "SELECT xp FROM players WHERE telegram_id = 1")[0][0], sql(path, "SELECT xp FROM players WHERE telegram_id = 2")[0][0]),
          (crash.xp_for(100, crash.CAP_X100),) * 2)
    check("свежий остался", sql(path, "SELECT status FROM crash_games WHERE telegram_id = 3")[0][0], "active")
    check("повторное закрытие невозможно", (db.close_expired_crash(now_ms=NOW_MS + 200_000, db_path=path), sql(path, "SELECT xp FROM players WHERE telegram_id = 2")[0][0]),
          (1, crash.xp_for(100, crash.CAP_X100)))
    # закрывается первым шагом действия: новый старт после брошенного
    path = new_db()
    add_player(path, 1, balance=1000)
    start(path, 1, 1, 100, 5000)
    r = start(path, 1, 2, 100, 5000, at=NOW_MS + 80_000)
    check("новый раунд после брошенного", (r["status"], sql(path, "SELECT COUNT(*) FROM crash_games")[0][0], sql(path, "SELECT result FROM crash_games WHERE id = 1")[0][0]), ("active", 2, "lose"))
    # пачки
    path = new_db()
    for u in range(1, 7):
        add_player(path, u, balance=1000)
        start(path, u, 1, 10, 5000)
    check("пачка 2", db.close_expired_crash(now_ms=NOW_MS + 71_000, db_path=path, batch=2), 2)
    check("остаток", db.close_expired_crash(now_ms=NOW_MS + 71_000, db_path=path, batch=200), 4)

    # ================= задержка: state и cashout используют одно и то же effective_ms =================
    path = new_db()
    rnd = random.Random(7)
    uid = [100]
    problems = []
    for trial in range(400):
        crash_x100 = rnd.choice([100, 101, 120, 150, 200, 333, 1000, 5000, 99999, 100000, 10 ** 8])
        t = rnd.choice([rnd.randint(0, 400), rnd.randint(0, 20_000), rnd.randint(0, 80_000)])
        eff = crash.effective_ms(NOW_MS + t, NOW_MS)
        c_now = crash.m100(eff)
        crashed = crash_x100 < c_now or (c_now >= crash.CAP_X100 and crash_x100 >= crash.CAP_X100) or t > crash.ABANDON_MS
        for variant in ("state", "cashout"):
            uid[0] += 1
            add_player(path, uid[0], balance=10_000)
            start(path, uid[0], 1, 100, crash_x100)
            if variant == "state":
                s = db.crash_state(uid[0], now_ms=NOW_MS + t, db_path=path)
                if s["status"] == "active":
                    if crashed or s["elapsed_ms"] != eff or s["crash_multiplier"] is not None:
                        problems.append(("state active", crash_x100, t))
                    # опросил state и тут же вывел в тот же момент: выигрыш на том же множителе
                    try:
                        w = cashout(path, uid[0], 2, NOW_MS + t)
                        if w["result"] != "win" or w["multiplier"] != crash.text(c_now) or int(round(float(w["multiplier"]) * 100)) > crash_x100:
                            problems.append(("cashout после state active", crash_x100, t, w["result"]))
                    except crash.TooEarly:
                        if c_now >= crash.MIN_TARGET_X100:
                            problems.append(("лишний too_early", crash_x100, t))
                else:
                    if not crashed:
                        problems.append(("state раскрыл крах раньше времени", crash_x100, t))
                    # «успеть вывести после краха»: любой вывод позже и в тот же момент не выигрывает с ошибкой или проигрышем
                    for later in (t, t + 1, t + 500):
                        try:
                            w = cashout(path, uid[0], 3 + later, NOW_MS + later)
                            problems.append(("вывод после краха сработал", crash_x100, t, w["result"]))
                        except crash.NoActiveGame:
                            pass
                    if s["result"] == "win" and not (crash_x100 >= crash.CAP_X100):
                        problems.append(("выигрыш после краха", crash_x100, t))
            else:
                try:
                    w = cashout(path, uid[0], 2, NOW_MS + t)
                except crash.TooEarly:
                    if c_now >= crash.MIN_TARGET_X100:
                        problems.append(("too_early при c>=1.01", crash_x100, t))
                    continue
                if w["result"] == "win":
                    won_c = int(round(float(w["multiplier"]) * 100))
                    ok = (won_c == c_now and won_c <= crash_x100) or (won_c == crash.CAP_X100 and crash_x100 >= crash.CAP_X100)
                    if not ok or crashed and crash_x100 < crash.CAP_X100:
                        problems.append(("cashout выиграл после краха", crash_x100, t, won_c))
                else:
                    if not (crashed or crash_x100 < c_now):
                        problems.append(("cashout проиграл до краха", crash_x100, t))
    assert not problems, problems[:5]

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
        return client.post("/api/crash/" + name, headers=auth(uid), json=body)

    def state(uid=SECRET_ID):
        return client.get("/api/crash/state", headers=auth(uid))

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "crash.json"), encoding="utf-8"))
    NULLABLE_TYPES = {"mode": str, "bet": int, "target": str, "elapsed_ms": int, "crash_multiplier": str, "result": str, "multiplier": str, "payout": int}

    def same_shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for k, ex in example.items():
            v = body[k]
            if k in NULLABLE_TYPES:
                assert v is None or type(v) is NULLABLE_TYPES[k], "%s.%s: %r" % (name, k, v)
            else:
                assert type(v) is type(ex), "%s.%s: %r, в примере %s" % (name, k, v, type(ex).__name__)

    check("без подписи", [client.get("/api/crash/state").status_code, client.post("/api/crash/start", json={}).status_code,
                          client.post("/api/crash/cashout", json={}).status_code], [401, 401, 401])
    s = state()
    check("state без игры", (s.status_code, s.json()["status"], s.json()["balance"]), (200, "none", SECRET_BALANCE))
    same_shape("none", s.json(), examples["none"])
    good = {"request_id": rid(100), "bet": 100}
    for body in ({}, {"request_id": rid(100)}, dict(good, extra=1), dict(good, bet=0), dict(good, bet=10 ** 9 + 1), dict(good, bet=1.5), dict(good, bet="100"),
                 dict(good, bet=True), dict(good, bet=None), dict(good, request_id="short"), dict(good, target_x100=100), dict(good, target_x100=100001),
                 dict(good, target_x100=150.5), dict(good, target_x100="200"), dict(good, target_x100=True), dict(good, target_x100=[200])):
        r = post("start", body)
        check("400 start %s" % json.dumps(body)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    for body in ({}, {"request_id": "x"}, {"request_id": rid(101), "extra": 1}, {"request_id": 5}):
        r = post("cashout", body)
        check("400 cashout %s" % json.dumps(body)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    check("при 400 ничего не списано", (balance(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM crash_games")[0][0]), (SECRET_BALANCE, 0))
    r = post("cashout", {"request_id": rid(102)})
    check("cashout без игры: 409", (r.status_code, r.json()), (409, {"detail": "no_active_game"}))
    with mock.patch.object(crash, "new_crash", return_value=SECRET_CRASH):
        st = post("start", {"request_id": rid(103), "bet": SECRET_BET})
        check("start 200", st.status_code, 200)
        sj = st.json()
        same_shape("active", sj, examples["active_manual"])
        check("активный ручной", (sj["status"], sj["mode"], sj["target"], sj["result"], sj["payout"], sj["balance"]), ("active", "manual", None, None, None, SECRET_BALANCE - SECRET_BET))
        for body_text in (json.dumps(sj), json.dumps(state().json())):
            assert str(SECRET_CRASH) not in body_text and "1234.57" not in body_text, "точка краха в ответе активного раунда"
        check("state = start (кроме времени)", {k: v for k, v in state().json().items() if k not in ("replayed", "elapsed_ms")},
              {k: v for k, v in sj.items() if k not in ("replayed", "elapsed_ms")})
        r = post("start", {"request_id": rid(104), "bet": 10})
        check("active_game_exists", (r.status_code, r.json()), (409, {"detail": "active_game_exists"}))
        r = post("start", {"request_id": rid(105), "bet": 10, "target_x100": 200})
        check("active_game_exists и в авто", (r.status_code, r.json()), (409, {"detail": "active_game_exists"}))
        rep = post("start", {"request_id": rid(103), "bet": SECRET_BET})
        check("повтор start", (rep.status_code, rep.json()["replayed"]), (200, True))
        conflict = post("start", {"request_id": rid(103), "bet": SECRET_BET + 1})
        check("request_conflict", (conflict.status_code, conflict.json()), (409, {"detail": "request_conflict"}))
        early = post("cashout", {"request_id": rid(106)})
        check("too_early сразу после старта", (early.status_code, early.json()), (409, {"detail": "too_early"}))
        check("после too_early игра активна", state().json()["status"], "active")
        time.sleep(0.45)
        t0 = time.perf_counter()
        w = post("cashout", {"request_id": rid(107)})
        print("cashout через TestClient: %.1f мс" % ((time.perf_counter() - t0) * 1000))
        check("cashout 200", w.status_code, 200)
        wj = w.json()
        same_shape("finished win", wj, examples["finished_win_auto"])
        check("выиграл на малом множителе", (wj["status"], wj["result"], wj["crash_multiplier"]), ("finished", "win", "1234.57"))
        assert 1.01 <= float(wj["multiplier"]) <= 1.5 and wj["payout"] == SECRET_BET * int(round(float(wj["multiplier"]) * 100)) // 100, wj
        rep = post("cashout", {"request_id": rid(107)})
        check("повтор cashout", (rep.json()["replayed"], rep.json()["balance"]), (True, wj["balance"]))
        gj = state().json()
        same_shape("state finished", gj, examples["finished_win_auto"])
    # авто: выигрыш, проигрыш
    with mock.patch.object(crash, "new_crash", return_value=341):
        a = post("start", {"request_id": rid(108), "bet": 100, "target_x100": 200}).json()
    same_shape("auto win", a, examples["finished_win_auto"])
    check("авто выигрыш по API", (a["mode"], a["target"], a["result"], a["payout"], a["crash_multiplier"]), ("auto", "2.00", "win", 200, "3.41"))
    with mock.patch.object(crash, "new_crash", return_value=100):
        a = post("start", {"request_id": rid(109), "bet": 100, "target_x100": 150, }).json()
        check("мгновенный крах в авто", (a["result"], a["crash_multiplier"], a["multiplier"]), ("lose", "1.00", "0.00"))
        m = post("start", {"request_id": rid(110), "bet": 100, "target_x100": None}).json()
        check("target null = ручной", (m["mode"], m["status"]), ("manual", "active"))
        db.settle_expired_crash(SECRET_ID, now_ms=int(time.time() * 1000) + 200_000, db_path=path)
    l = state().json()
    same_shape("lose manual", l, examples["finished_lose_manual"])
    check("брошенный: проигрыш, auto", (l["result"], l["auto"], l["crash_multiplier"]), ("lose", True, "1.00"))
    for k, v in examples["errors"].items():
        check("пример ошибки " + k, v, {"detail": k})
    # нехватка фишек
    add_player(path, 555, balance=50, last_accrual=now_real)
    r = post("start", {"request_id": rid(111), "bet": 100}, uid=555)
    check("insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    # /api/me закрывает брошенный раунд
    add_player(path, 600, balance=10_000, last_accrual=now_real)
    with mock.patch.object(crash, "new_crash", return_value=5000):
        post("start", {"request_id": rid(112), "bet": 10}, uid=600)
    sql(path, "UPDATE crash_games SET started_at_ms = ? WHERE telegram_id = 600", (int(time.time() * 1000) - 120_000,))
    client.get("/api/me", headers=auth(600))
    check("/api/me закрыл брошенный", sql(path, "SELECT status, result, auto FROM crash_games WHERE telegram_id = 600")[0], ("finished", "lose", 1))
    # ограничение частоты
    path2 = new_db()
    add_player(path2, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1", "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path2, rate_limiter=lim2))
    codes = [c2.post("/api/crash/start", headers=auth(SECRET_ID), json={"request_id": rid(200 + i), "bet": 1, "target_x100": 150}).status_code for i in range(5)]
    check("write 429 после лимита", [c == 429 for c in codes], [False, False, False, True, True])
    r = c2.post("/api/crash/cashout", headers=auth(SECRET_ID), json={"request_id": rid(300)})
    check("429 тело", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    check("read: третий 429", [c2.get("/api/crash/state", headers=auth(SECRET_ID)).status_code for _ in range(3)], [200, 200, 429])

    # ================= /mydata и /deletemydata =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE)
    add_player(path, 801, balance=50_000)
    start(path, 801, 1, 700, 5000, target=300)
    start(path, SECRET_ID, 2, SECRET_BET, SECRET_CRASH)                       # активный
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("активный: только факт", (export["crash_games"], export["crash_active"]), ([], True))
    cashout(path, SECRET_ID, 3, at_eff(6000))
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("завершённый в выгрузке", (export["crash_active"], export["crash_games"]),
          (False, [{"created_at": NOW, "bet": SECRET_BET, "mode": "manual", "target_x100": None, "crash_x100": SECRET_CRASH,
                    "result": "win", "payout": SECRET_BET * 2, "finished_at": NOW + 6}]))
    start(path, SECRET_ID, 4, 50, 987654)                                      # снова активный: точка краха 987654
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    text = upd.effective_message.documents[0]["data"].decode("utf-8")
    payload = json.loads(text)
    check("/mydata: краш", (len(payload["crash_games"]), payload["crash_active"]), (1, True))
    assert "987654" not in text and "9876.54" not in text, "точка краха активного раунда в выгрузке"
    for i in range(110):
        sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, result, mult_x100, payout, created_at, finished_at) "
                  "VALUES (801, 1, 'auto', 200, 150, 0, 'finished', 'lose', 0, 0, ?, ?)", (NOW + 100 + i, NOW + 100 + i))
    check("не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["crash_games"]), 100)
    others = sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = 801")[0][0]
    others_act = sql(path, "SELECT COUNT(*) FROM crash_actions WHERE telegram_id = 801")[0][0]
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалены раунды (и активный)", counts["crash_games"], 2)
    check("раундов и действий нет, чужие целы", (sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                                sql(path, "SELECT COUNT(*) FROM crash_actions WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                                sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = 801")[0][0],
                                                sql(path, "SELECT COUNT(*) FROM crash_actions WHERE telegram_id = 801")[0][0]), (0, 0, others, others_act))
    add_player(path, 900, balance=5000)
    start(path, 900, 5, 100, 5000)
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    assert "незавершённый раунд краша (вместе со ставкой)" in bot.DELETE_WARNING and "история раундов краша" in bot.DELETE_WARNING, bot.DELETE_WARNING
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "раунды краша — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)

    # ================= очистка =================
    path = new_db()
    add_player(path, 1, balance=1000)
    ins = ("INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, result, mult_x100, payout, created_at, finished_at) "
           "VALUES (1, 10, 'auto', 200, 150, 0, 'finished', 'lose', 0, 0, ?, ?)")
    sql(path, ins, (NOW - 40 * DAY, NOW - 40 * DAY))
    sql(path, ins, (NOW - 31 * DAY, NOW - 31 * DAY))
    sql(path, ins, (NOW - 5 * DAY, NOW - 5 * DAY))
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, crash_x100, started_at_ms, status, created_at) VALUES (2, 10, 'manual', 500, 0, 'active', ?)", (NOW - 90 * DAY,))
    for i, age in enumerate((40, 31, 5)):
        sql(path, "INSERT INTO crash_actions VALUES (1, ?, 'start', '{}', '{}', ?)", ("old-act-%04d" % i, NOW - age * DAY))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка: старые раунды и действия", (deleted["crash_games"], deleted["crash_actions"]), (2, 2))
    check("свежий и активный остались", sorted(r[0] for r in sql(path, "SELECT status FROM crash_games")), ["active", "finished"])
    db.purge_old_data(now=NOW + 400 * DAY, db_path=path, rounds_days=30)
    check("активный не удаляется никогда", [r[0] for r in sql(path, "SELECT status FROM crash_games")], ["active"])

    # ================= миграция, индекс, документы =================
    path = new_db()
    db.init_db(path)
    db.init_db(path)
    check("таблицы есть", sorted(r[0] for r in sql(path, "SELECT name FROM sqlite_master WHERE name LIKE 'crash_%' AND type = 'table'")), ["crash_actions", "crash_games"])
    add_player(path, 1)
    start(path, 1, 1, 10, 5000)
    try:
        sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, crash_x100, started_at_ms, status, created_at) VALUES (1, 1, 'manual', 100, 0, 'active', 1)")
        raise AssertionError("второй активный раунд записался")
    except sqlite3.IntegrityError:
        pass
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "раундов в краш" in privacy and "История раундов краша хранится 30 дней" in privacy and "закрывается автоматически как раунд без вывода" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert all(p in api_doc for p in ("/api/crash/start", "/api/crash/cashout", "/api/crash/state"))

    # ================= в логах нет id, ставок, балансов, точек краха и request_id =================
    for secret in (str(SECRET_ID), str(SECRET_BET), str(SECRET_BALANCE), str(SECRET_CRASH), "1234.57", "987654", rid(103), rid(107), "cr-req-"):
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
