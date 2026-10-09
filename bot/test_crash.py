import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
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



def legacy_game(path, uid, bet, crash_x100=5000, status="active", result=None, payout=None, created_at=NOW, finished_at=None):
    """Строка партии прежнего краша (новых партий нет, старые создаёт только этот тест прямой записью)."""
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, result, mult_x100, payout, created_at, finished_at) "
              "VALUES (?, ?, 'manual', NULL, ?, ?, ?, ?, ?, ?, ?, ?)", (uid, bet, crash_x100, created_at * 1000, status, result, 200 if result == "win" else None, payout, created_at, finished_at))



try:
    # ================= множитель по времени =================
    check("контрольные", [crash.m100(t) for t in (0, 6000, 12000)], [100, 200, 400])
    check("отрицательное время", crash.m100(-5), 100)
    check("предел достигается за 6000*log2(250) = 47794,7 мс", (crash.m100(47794), crash.m100(47795), crash.m100(10 ** 9)), (24997, 25000, 25000))
    assert all(crash.m100(t) <= crash.m100(t + 1) for t in range(0, 70000, 7)), "множитель не убывает"
    assert max(crash.m100(t) for t in range(0, 80000, 13)) == crash.CAP_X100
    check("эффективное время", (crash.effective_ms(1000, 0), crash.effective_ms(100, 0), crash.effective_ms(0, 5000)), (850, 0, 0))
    check("текст", [crash.text(v) for v in (0, 100, 101, 12345, 25000)], ["0.00", "1.00", "1.01", "123.45", "250.00"])
    check("константы", (crash.DOUBLING_MS, crash.CAP_X100, crash.MIN_TARGET_X100, crash.GRACE_MS, crash.M), (6000, 25000, 101, 150, 2 ** 53))

    # ================= точка краха =================
    check("u=0 даёт 1.00", crash.crash_from_u(0), 100)
    check("u=M-1 ограничено", crash.crash_from_u(crash.M - 1), 10 ** 9)
    M = crash.M
    for t in (101, 150, 200, 500, 1000, 5000, 25000):
        # вероятность crash_x100 >= t: число u, при которых 3600*M // (37*(M-u)) >= t, делённое на M
        n_u = 3600 * M // (37 * t)
        edge = M - n_u
        assert crash.crash_from_u(edge) >= t and crash.crash_from_u(edge - 1) < t, "граница u для t=%d" % t
    rtp_report = []
    for t in (101, 150, 200, 500, 1000, 5000, 25000):
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
    raises(ValueError, crash.decide_auto, 25001, 500)
    raises(ValueError, crash.decide_auto, 100001, 500)
    check("выплата", (crash.payout(100, 150), crash.payout(7, 150), crash.payout(10 ** 9, 25000)), (150, 10, 25 * 10 ** 10))
    check("XP цели 200", crash.xp_for(10_000, 200), 10_000 * (7400 - 3600) // 7400)
    assert abs(crash.xp_for(10 ** 6, 200) / 10 ** 6 - 0.5135) < 0.0005
    assert abs(crash.xp_for(10 ** 6, 101) / 10 ** 6 - 0.03666) < 0.0005
    assert abs(crash.xp_for(10 ** 6, crash.CAP_X100) / 10 ** 6 - 0.9961) < 0.0005
    check("XP не отрицателен", crash.xp_for(100, 1), 0)
    check("m для XP", (crash.xp_multiplier("auto", "win", 200, 200), crash.xp_multiplier("auto", "lose", 0, 350),
                       crash.xp_multiplier("manual", "win", 180, None), crash.xp_multiplier("manual", "lose", 0, None)),
          (200, 350, 180, crash.CAP_X100))
    check("xp.crash_xp", xp.crash_xp(250, 300), crash.xp_for(250, 300))
    # правила времени
    check("не разбился", crash.settle(500, 0, crash.GRACE_MS + 6000), None)       # m=200 <= 500
    check("разбился", crash.settle(150, 0, crash.GRACE_MS + 6000), ("lose", 0))   # m=200 > 150
    check("ровно на краху ещё жив", crash.settle(200, 0, crash.GRACE_MS + 6000), None)
    check("предел с crash >= CAP: выигрыш", crash.settle(crash.CAP_X100, 0, crash.GRACE_MS + 47795), ("win", crash.CAP_X100))
    check("предел с crash < CAP: проигрыш", crash.settle(crash.CAP_X100 - 1, 0, crash.GRACE_MS + 47795), ("lose", 0))
    check("брошенный выше предела", crash.settle(10 ** 9, 0, 80_000), ("win", crash.CAP_X100))
    check("брошенный ниже предела", crash.settle(5000, 0, 80_000), ("lose", 0))
    raises(crash.TooEarly, crash.cashout_multiplier, 0, crash.GRACE_MS + 50)
    check("вывод на 1.01", crash.cashout_multiplier(0, crash.GRACE_MS + 87), 101)

    # раунды с точкой краха выше потолка обрабатываются как автоматический выигрыш ×250
    high = [v for v in sample if v >= crash.CAP_X100]
    assert len(high) > 500, len(high)
    assert all(crash.settle(v, 0, crash.GRACE_MS + 47795) == ("win", crash.CAP_X100) for v in high[:5000])
    assert all(crash.settle(v, 0, crash.GRACE_MS + 47794) is None for v in high[:5000]), "до потолка раунд идёт"
    share_high = len(high) / len(sample)
    assert abs(share_high - float(Fraction(3600 * M // (37 * crash.CAP_X100), M))) < 0.0005, share_high
    print("доля раундов, дошедших до потолка ×250: %.5f (теория %.5f)" % (share_high, 36 / (37 * 250)))
    check("XP потолка", crash.xp_for(10 ** 6, crash.CAP_X100), 10 ** 6 * (37 * 25000 - 3600) // (37 * 25000))

    # ================= прежний краш закрыт: возврат ставок открытых партий =================
    path = new_db()
    for uid, bal, staked in ((1, 900, 100), (2, 1000, 50), (3, 400, 600)):
        add_player(path, uid, balance=bal)
        sql(path, "UPDATE players SET total_staked = ? WHERE telegram_id = ?", (staked, uid))
    legacy_game(path, 1, 100)                                               # открытая: ставка 100 уже списана
    legacy_game(path, 2, 50, status="finished", result="lose", payout=0, finished_at=NOW + 3)   # закрытая не трогается
    legacy_game(path, 3, 600)
    check("возврат одного игрока", db.refund_legacy_crash(1, now=NOW + 10, db_path=path), 1)
    check("баланс вернулся, ставка снята из total_staked", sql(path, "SELECT balance, total_staked FROM players WHERE telegram_id = 1")[0], (1000, 0))
    check("партия закрыта возвратом", sql(path, "SELECT status, result, payout, finished_at FROM crash_games WHERE telegram_id = 1")[0], ("finished", "refund", 100, NOW + 10))
    check("повтор ничего не возвращает", (db.refund_legacy_crash(1, now=NOW + 11, db_path=path), balance(path, 1)), (0, 1000))
    check("закрытая партия цела", (sql(path, "SELECT status, result FROM crash_games WHERE telegram_id = 2")[0], balance(path, 2)), (("finished", "lose"), 1000))
    check("фоновый проход возвращает остальные", (db.refund_legacy_crash(now=NOW + 20, db_path=path), balance(path, 3)), (1, 1000))
    check("открытых партий не осталось", sql(path, "SELECT COUNT(*) FROM crash_games WHERE status = 'active'")[0][0], 0)
    check("возврат не считается сыгранным раундом", sum(sql(path, q, (1,))[0][0] for q in __import__("features.round_counts", fromlist=["x"]).ROUND_COUNT_SQL), 0)
    check("возврат не меняет опыт и рекорды", (sql(path, "SELECT xp FROM players WHERE telegram_id = 1")[0][0], sql(path, "SELECT COUNT(*) FROM player_best_win")[0][0]), (0, 0))
    # потолок баланса: возврат не проходит, партия остаётся открытой, ошибка не уходит наружу, повтор безопасен
    add_player(path, 4, balance=wallet.MAX_SAFE_INT)
    legacy_game(path, 4, 10)
    check("потолок баланса: возврат отложен", (db.refund_legacy_crash(4, now=NOW + 30, db_path=path), balance(path, 4), sql(path, "SELECT status FROM crash_games WHERE telegram_id = 4")[0][0]),
          (0, wallet.MAX_SAFE_INT, "active"))
    # /api/me возвращает ставку открытой партии до ответа
    path = new_db()
    add_player(path, 7, balance=900)
    sql(path, "UPDATE players SET total_staked = 100 WHERE telegram_id = 7")
    legacy_game(path, 7, 100)
    client = TestClient(create_app(TOKEN, [], db_path=path))
    me = client.get("/api/me", headers={"Authorization": "tma " + make_init_data(TOKEN, user_id=7, auth_date=int(time.time()))}).json()
    check("/api/me: ставка возвращена, игры нет", (me["balance"] >= 1000, me["active_game"], sql(path, "SELECT result FROM crash_games WHERE telegram_id = 7")[0][0]), (True, None, "refund"))
    for method, url in (("post", "/api/crash/start"), ("post", "/api/crash/cashout"), ("get", "/api/crash/state")):
        r = getattr(client, method)(url, headers={"Authorization": "tma " + make_init_data(TOKEN, user_id=7, auth_date=int(time.time()))}, **({"json": {}} if method == "post" else {}))
        check("прежний адрес %s отвечает 410" % url, (r.status_code, r.json()), (410, {"detail": "gone"}))
    check("новых партий прежнего краша нет", sql(path, "SELECT COUNT(*) FROM crash_games WHERE status = 'active'")[0][0], 0)

    # ================= /mydata и /deletemydata (история прежнего краша хранится до срока) =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE)
    add_player(path, 801, balance=50_000)
    legacy_game(path, 801, 700, status="finished", result="lose", payout=0, finished_at=NOW + 1)
    legacy_game(path, SECRET_ID, SECRET_BET, SECRET_CRASH)                    # открытая
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("активная: только факт", (export["crash_games"], export["crash_active"]), ([], True))
    sql(path, "UPDATE crash_games SET status = 'finished', result = 'win', mult_x100 = 200, payout = ?, finished_at = ? WHERE telegram_id = ?", (SECRET_BET * 2, NOW + 6, SECRET_ID))
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("завершённая в выгрузке", (export["crash_active"], export["crash_games"]),
          (False, [{"created_at": NOW, "bet": SECRET_BET, "mode": "manual", "target_x100": None, "crash_x100": SECRET_CRASH,
                    "result": "win", "payout": SECRET_BET * 2, "finished_at": NOW + 6}]))
    legacy_game(path, SECRET_ID, 50, 987654)                                   # открытая: точка краха 987654 не должна попасть в выгрузку
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    text = upd.effective_message.documents[0]["data"].decode("utf-8")
    payload = json.loads(text)
    check("/mydata: краш", (len(payload["crash_games"]), payload["crash_active"]), (1, True))
    assert "987654" not in text and "9876.54" not in text, "точка краха открытой партии в выгрузке"
    for i in range(110):
        legacy_game(path, 801, 1, 150, status="finished", result="lose", payout=0, created_at=NOW + 100 + i, finished_at=NOW + 100 + i)
    check("не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["crash_games"]), 100)
    others = sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = 801")[0][0]
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалены партии игрока (и открытая)", counts["crash_games"], 2)
    check("партий игрока нет, чужие целы", (sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                            sql(path, "SELECT COUNT(*) FROM crash_games WHERE telegram_id = 801")[0][0]), (0, others))
    add_player(path, 900, balance=5000)
    legacy_game(path, 900, 100, status="finished", result="lose", payout=0, finished_at=NOW + 1)
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    assert "история раундов краша" in bot.DELETE_WARNING, bot.DELETE_WARNING
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "раунды краша — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)

    # ================= очистка =================
    path = new_db()
    add_player(path, 1, balance=1000)
    for age in (40, 31, 5):
        legacy_game(path, 1, 10, 150, status="finished", result="lose", payout=0, created_at=NOW - age * DAY, finished_at=NOW - age * DAY)
    legacy_game(path, 2, 10, created_at=NOW - 90 * DAY)
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка: старые партии", deleted["crash_games"], 2)
    check("свежая и открытая остались", sorted(r[0] for r in sql(path, "SELECT status FROM crash_games")), ["active", "finished"])
    db.purge_old_data(now=NOW + 400 * DAY, db_path=path, rounds_days=30)
    check("открытая партия не удаляется очисткой (её закрывает возврат)", [r[0] for r in sql(path, "SELECT status FROM crash_games")], ["active"])

    # ================= миграция, индекс, документы =================
    path = new_db()
    db.init_db(path)
    db.init_db(path)
    check("таблицы есть", sorted(r[0] for r in sql(path, "SELECT name FROM sqlite_master WHERE name LIKE 'crash_%' AND type = 'table'")), ["crash_actions", "crash_bets", "crash_games", "crash_rounds"])
    add_player(path, 1)
    legacy_game(path, 1, 10)
    try:
        legacy_game(path, 1, 1)
        raise AssertionError("вторая открытая партия записалась")
    except sqlite3.IntegrityError:
        pass
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "История раундов краша хранится 30 дней" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert all(p in api_doc for p in ("/api/crash/start", "/api/crash/cashout", "/api/crash/state", "410")), "API.md: прежние адреса краша и 410"

    # ================= в логах нет id, ставок, балансов и точек краха =================
    for secret in (str(SECRET_ID), str(SECRET_BET), str(SECRET_BALANCE), str(SECRET_CRASH), "987654"):
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
