import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from fractions import Fraction
from math import comb
from unittest import mock

from fastapi.testclient import TestClient

import backup
import bot
import db
import mines
import ratelimit
import wallet
from api import create_app
from roulette import MAX_SAFE_INT, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
HOUR, DAY = 3600, 86400
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
    """Раскладка для тестов: мины в заданных клетках."""

    def __init__(self, cells):
        self.cells = list(cells)

    def sample(self, population, k):
        assert k == len(self.cells), (k, self.cells)
        return list(self.cells)


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "m%d.db" % counter[0])
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


def staked(path, uid):
    return sql(path, "SELECT total_staked FROM players WHERE telegram_id = ?", (uid,))[0][0]


def rid(n):
    return "mines-req-%06d" % n


def game_row(path, uid):
    return sql(path, "SELECT status, payout, revealed_mask, staked_counted FROM mines_games WHERE telegram_id = ? "
                     "ORDER BY id DESC LIMIT 1", (uid,))[0]


def active_count(path, uid=None):
    if uid is None:
        return sql(path, "SELECT COUNT(*) FROM mines_games WHERE status = 'active'")[0][0]
    return sql(path, "SELECT COUNT(*) FROM mines_games WHERE status = 'active' AND telegram_id = ?", (uid,))[0][0]


def no_layout(obj, label):
    """В ответе активной игры нет раскладки мин."""
    text = json.dumps(obj, ensure_ascii=False)
    assert "mine_cells" not in text and "mine_mask" not in text, label + ": раскладка в ответе"


try:
    # ================= mines.py =================
    check("контрольные значения", [mines.payout(1000, 1, 1), mines.payout(1000, 3, 1), mines.payout(1000, 24, 1),
                                   mines.payout(1, 12, 13)], [1013, 1105, 24324, 5059751])
    check("константы", (mines.MINES_MIN_COUNT, mines.MINES_MAX_COUNT, mines.MINES_MAX_BET, mines.MINES_IDLE_SECONDS),
          (1, 24, 10 ** 9, 86400))
    for bet in (1, 7, 10, 999, 1000, 10 ** 6, 10 ** 9):
        for m in range(1, 25):
            check("k = 0 возвращает ставку", mines.payout(bet, m, 0), bet)
            row = [mines.payout(bet, m, k) for k in range(0, 26 - m)]
            assert all(p >= bet for p in row[1:]), (bet, m)
            assert all(a <= b for a, b in zip(row, row[1:])), "выплата убывает с ростом k"
    for m in range(1, 25):
        row = [mines.payout(10 ** 6, m, k) for k in range(1, 26 - m)]
        assert all(a < b for a, b in zip(row, row[1:])), "для 10**6 рост не строгий (m=%d)" % m
    # честный множитель: floor(bet * 36/37 / P_выжить), без float
    for m in range(1, 25):
        for k in range(1, 26 - m):
            survive = Fraction(comb(25 - m, k), comb(25, k))
            exact = Fraction(10 ** 6 * 36, 37) / survive
            value = mines.payout(10 ** 6, m, k)
            assert exact - 1 <= value <= exact, (m, k, value, float(exact))
            hundredths = (Fraction(36, 37) / survive * 100).__floor__()
            text = mines.multiplier_text(m, k)
            assert re.fullmatch(r"\d+\.\d\d", text), text
            check("множитель %d,%d" % (m, k), text, "%d.%02d" % (hundredths // 100, hundredths % 100))
    check("множитель при k = 0", [mines.multiplier_text(m, 0) for m in (1, 12, 24)], ["1.00"] * 3)
    check("множитель 1 мина 1 клетка", mines.multiplier_text(1, 1), "1.01")
    check("множитель 24 мины 1 клетка", mines.multiplier_text(24, 1), "24.32")
    for bad in ((0, 0), (25, 0), (1, 25), (1, -1), (True, 0), (1, True), (1.5, 0), (3, 23)):
        raises(ValueError, mines.payout, 100, *bad)
        raises(ValueError, mines.multiplier_text, *bad)
    for bad in (-1, 1.5, "10", None, True):
        raises(ValueError, mines.payout, bad, 3, 1)
    # технический потолок ставки: любая выплата меньше MAX_SAFE_INT
    top = max(mines.payout(mines.MINES_MAX_BET, m, k) for m in range(1, 25) for k in range(0, 26 - m))
    assert top <= MAX_SAFE_INT, top
    check("максимум выплаты около 5,06 * 10**15", top, mines.payout(10 ** 9, 12, 13))
    # раскладка
    for m in range(1, 25):
        mask = mines.new_layout(m)
        check("ровно %d мин" % m, mines.popcount(mask), m)
        assert 0 <= mask <= mines.FULL_MASK
    check("раскладка по заданному генератору", mines.cells_of(mines.new_layout(3, FixedRng([4, 9, 20]))), [4, 9, 20])
    check("разные раскладки у разных игр", len({mines.new_layout(5) for _ in range(40)}) > 1, True)
    src = open(os.path.join(HERE, "mines.py"), encoding="utf-8").read()
    assert "SystemRandom" in src, "раскладка должна идти через криптостойкий источник"
    code = "\n".join(l.split("#")[0] for l in re.sub(r'""".*?"""', "", src, flags=re.S).splitlines())
    code = re.sub(r'"[^"\n]*"', '""', code)   # текстовые литералы (например "1.00") не в счёт
    assert "float" not in code and not re.search(r"\d\.\d", code) and not re.search(r"(?<!/)/(?!/)", code)
    raises(ValueError, mines.new_layout, 0)
    raises(ValueError, mines.new_layout, 25)

    # ================= миграция и индексы =================
    old = new_db()
    sql(old, "DROP TABLE mines_games")
    sql(old, "DROP TABLE mines_actions")
    add_player(old, 1, balance=777)
    db.init_db(old)
    db.init_db(old)
    check("таблицы созданы", sorted(r[0] for r in sql(old, "SELECT name FROM sqlite_master WHERE name IN ('mines_games','mines_actions')")),
          ["mines_actions", "mines_games"])
    check("данные целы", balance(old, 1), 777)
    check("столбцы mines_games", [r[1] for r in sql(old, "PRAGMA table_info(mines_games)")],
          ["id", "telegram_id", "bet", "mines_count", "mine_mask", "revealed_mask", "status", "payout",
           "staked_counted", "created_at", "updated_at", "finished_at"])
    check("столбцы mines_actions", [r[1] for r in sql(old, "PRAGMA table_info(mines_actions)")],
          ["telegram_id", "request_id", "action", "params", "response_json", "created_at"])
    check("индексы", sorted(r[1] for r in sql(old, "PRAGMA index_list(mines_games)") if r[1].startswith("idx_")),
          ["idx_mines_active", "idx_mines_history"])
    ins = ("INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at) "
           "VALUES (?, 10, 3, 7, ?, 1, 1)")
    sql(old, ins, (1, "active"))
    try:
        sql(old, ins, (1, "active"))
        raise AssertionError("вторая активная игра принята")
    except sqlite3.IntegrityError:
        pass
    sql(old, ins, (1, "lost"))
    sql(old, ins, (1, "cashed"))
    sql(old, ins, (2, "active"))
    check("уникален только активный статус", sql(old, "SELECT COUNT(*) FROM mines_games")[0][0], 4)

    # ================= старт, открытие, выход =================
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW - 3 * HOUR)
    cap.lines.clear()
    r = db.mines_start(1, rid(1), 100, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    check("старт: ключи", sorted(r), ["balance", "game", "replayed"])
    check("начисление 3 часа до списания: 1000 + 300 - 100", (r["balance"], balance(path, 1)), (1200, 1200))
    check("игра", r["game"], {"bet": 100, "mines": 3, "revealed": [], "safe_left": 22, "multiplier": "1.00",
                              "payout_now": 100, "next_multiplier": mines.multiplier_text(3, 1),
                              "next_payout": mines.payout(100, 3, 1), "expires_at": NOW + DAY})
    no_layout(r, "start")
    check("ставка при старте не в total_staked", staked(path, 1), 0)
    check("игра в базе", game_row(path, 1), ("active", 0, 0, 0))
    check("запись действия", sql(path, "SELECT action, params FROM mines_actions")[0], ("start", '{"bet":100,"mines":3}'))
    # ошибки старта
    raises(mines.ActiveGameExists, db.mines_start, 1, rid(2), 50, 3, NOW, path)
    check("после отказа списания нет", balance(path, 1), 1200)
    for bad in ((0, 3), (-1, 3), (True, 3), (1.5, 3), (10 ** 9 + 1, 3), (100, 0), (100, 25), (100, True), (100, "3")):
        raises(ValueError, db.mines_start, 1, rid(3), bad[0], bad[1], NOW, path)
    # открытие
    r = db.mines_reveal(1, rid(10), 5, now=NOW + 10, db_path=path)
    check("безопасная клетка", (r["result"], r["game"]["revealed"], r["game"]["safe_left"], r["replayed"]), ("safe", [5], 21, False))
    check("множитель", (r["game"]["multiplier"], r["game"]["payout_now"]), (mines.multiplier_text(3, 1), mines.payout(100, 3, 1)))
    check("expires_at сдвинут", r["game"]["expires_at"], NOW + 10 + DAY)
    no_layout(r, "reveal safe")
    check("ставка в total_staked при первом открытии", (staked(path, 1), game_row(path, 1)[3]), (100, 1))
    db.mines_reveal(1, rid(11), 6, now=NOW + 11, db_path=path)
    check("второе открытие total_staked не меняет", staked(path, 1), 100)
    raises(mines.AlreadyRevealed, db.mines_reveal, 1, rid(12), 5, NOW + 12, path)
    check("после AlreadyRevealed total_staked прежний", staked(path, 1), 100)
    for bad in (-1, 25, True, 1.5, "3", None):
        raises(ValueError, db.mines_reveal, 1, rid(13), bad, NOW, path)
    # мина
    r = db.mines_reveal(1, rid(14), 0, now=NOW + 20, db_path=path)
    check("мина", (r["result"], r["game"], r["last"]["status"], r["last"]["payout"]), ("mine", None, "lost", 0))
    check("раскладка у завершённой игры", (r["last"]["mine_cells"], r["last"]["revealed"], r["last"]["finished_at"]),
          ([0, 1, 2], [5, 6], NOW + 20))
    check("ставка потеряна", (r["balance"], balance(path, 1)), (1200, 1200))
    check("total_staked один раз", staked(path, 1), 100)
    check("активных нет", active_count(path, 1), 0)
    raises(mines.NoActiveGame, db.mines_reveal, 1, rid(15), 7, NOW + 30, path)
    raises(mines.NoActiveGame, db.mines_cashout, 1, rid(16), NOW + 30, path)

    # cashout при k = 0: возврат, в total_staked не идёт
    path = new_db()
    add_player(path, 1, balance=1000)
    db.mines_start(1, rid(1), 300, 5, now=NOW, db_path=path, rng=FixedRng([0, 1, 2, 3, 4]))
    check("ставка списана", balance(path, 1), 700)
    r = db.mines_cashout(1, rid(2), now=NOW + 5, db_path=path)
    check("возврат", (r["last"]["status"], r["last"]["payout"], r["balance"], r["replayed"]), ("refunded", 300, 1000, False))
    check("раскладка раскрыта", r["last"]["mine_cells"], [0, 1, 2, 3, 4])
    check("возврат не идёт в total_staked", staked(path, 1), 0)
    # cashout при k > 0
    db.mines_start(1, rid(3), 300, 5, now=NOW + 6, db_path=path, rng=FixedRng([0, 1, 2, 3, 4]))
    for i, cell in enumerate((10, 11, 12)):
        db.mines_reveal(1, rid(10 + i), cell, now=NOW + 7 + i, db_path=path)
    r = db.mines_cashout(1, rid(4), now=NOW + 20, db_path=path)
    owed = mines.payout(300, 5, 3)
    check("выплата за 3 клетки", (r["last"]["status"], r["last"]["payout"], r["balance"]), ("cashed", owed, 1000 - 300 + owed))
    check("total_staked ровно один раз", staked(path, 1), 300)

    # очищение поля: автовыплата
    path = new_db()
    add_player(path, 1, balance=5000)
    db.mines_start(1, rid(1), 1000, 24, now=NOW, db_path=path, rng=FixedRng(list(range(24))))
    r = db.mines_reveal(1, rid(2), 24, now=NOW + 1, db_path=path)
    owed = mines.payout(1000, 24, 1)
    check("cleared", (r["result"], r["game"], r["last"]["status"], r["last"]["payout"]), ("cleared", None, "cashed", owed))
    check("баланс после очищения", (r["balance"], balance(path, 1)), (4000 + owed, 4000 + owed))
    check("total_staked", staked(path, 1), 1000)
    check("мины в ответе завершённой игры", r["last"]["mine_cells"], list(range(24)))

    # нехватка фишек
    path = new_db()
    add_player(path, 1, balance=50)
    raises(wallet.InsufficientFunds, db.mines_start, 1, rid(1), 51, 3, NOW, path)
    raises(InsufficientFunds, db.mines_start, 1, rid(1), 51, 3, NOW, path)
    check("баланс не тронут", balance(path, 1), 50)
    check("записи о действии нет", sql(path, "SELECT COUNT(*) FROM mines_actions")[0][0], 0)
    check("игры нет", sql(path, "SELECT COUNT(*) FROM mines_games")[0][0], 0)

    # ================= повторы и конфликт request_id =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    first = db.mines_start(1, rid(1), 500, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    again = db.mines_start(1, rid(1), 500, 3, now=NOW + 5, db_path=path, rng=FixedRng([20, 21, 22]))
    check("повтор: тот же ответ", {k: v for k, v in again.items() if k != "replayed"}, {k: v for k, v in first.items() if k != "replayed"})
    check("повтор: replayed", (first["replayed"], again["replayed"]), (False, True))
    check("повтор не списывает", balance(path, 1), 9500)
    check("одна игра с прежней раскладкой", sql(path, "SELECT COUNT(*), MIN(mine_mask) FROM mines_games")[0], (1, 7))
    raises(mines.RequestConflict, db.mines_start, 1, rid(1), 600, 3, NOW, path)
    raises(mines.RequestConflict, db.mines_start, 1, rid(1), 500, 4, NOW, path)
    raises(mines.RequestConflict, db.mines_reveal, 1, rid(1), 5, NOW, path)
    raises(mines.RequestConflict, db.mines_cashout, 1, rid(1), NOW, path)
    r1 = db.mines_reveal(1, rid(2), 5, now=NOW + 1, db_path=path)
    r2 = db.mines_reveal(1, rid(2), 5, now=NOW + 2, db_path=path)
    check("повтор открытия", ({k: v for k, v in r2.items() if k != "replayed"}, r2["replayed"]),
          ({k: v for k, v in r1.items() if k != "replayed"}, True))
    raises(mines.RequestConflict, db.mines_reveal, 1, rid(2), 6, NOW, path)
    check("повтор не открыл второй раз и total_staked один раз", (game_row(path, 1)[2], staked(path, 1)), (1 << 5, 500))
    hit = db.mines_reveal(1, rid(3), 1, now=NOW + 3, db_path=path)
    hit2 = db.mines_reveal(1, rid(3), 1, now=NOW + 4, db_path=path)
    check("повтор попадания в мину", (hit2["result"], hit2["replayed"], hit2["last"]), ("mine", True, hit["last"]))
    check("total_staked по-прежнему один раз", staked(path, 1), 500)
    bal0 = balance(path, 1)
    db.mines_start(1, rid(4), 100, 3, now=NOW + 5, db_path=path, rng=FixedRng([0, 1, 2]))
    k1 = db.mines_cashout(1, rid(5), now=NOW + 6, db_path=path)
    k2 = db.mines_cashout(1, rid(5), now=NOW + 7, db_path=path)
    check("повтор cashout", (k2["replayed"], k2["last"], balance(path, 1)), (True, k1["last"], bal0))

    # ================= параллельность =================
    path = new_db()
    add_player(path, 1, balance=10_000)
    results, errors = [], []

    def runner(fn, *args):
        try:
            results.append(fn(*args))
        except mines.MinesError as exc:
            errors.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=runner, args=(db.mines_start, 1, rid(20 + i), 100, 3, NOW, path)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("одновременный старт: одна игра", (len(results), sorted(set(errors)), active_count(path, 1)), (1, ["active_game_exists"], 1))
    check("ставка списана один раз", balance(path, 1), 9900)
    results.clear()
    errors.clear()
    threads = [threading.Thread(target=runner, args=(db.mines_start, 1, rid(99), 100, 3, NOW, path)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("тот же request_id: ошибок нет или конфликт игры", sorted(set(errors)) in ([], ["active_game_exists"]), True)
    # открытие одной клетки двумя потоками
    sql(path, "UPDATE mines_games SET mine_mask = 7, revealed_mask = 0, staked_counted = 0 WHERE status = 'active'")
    results.clear()
    errors.clear()
    threads = [threading.Thread(target=runner, args=(db.mines_reveal, 1, rid(30 + i), 10, NOW + 1, path)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("одна клетка в пять потоков: одно открытие", (len(results), sorted(set(errors))), (1, ["already_revealed"]))
    check("total_staked один раз", staked(path, 1), 100)
    # два игрока параллельно: фишки не теряются
    path = new_db()
    for uid in (1, 2):
        add_player(path, uid, balance=1000)
    results.clear()
    errors.clear()
    threads = [threading.Thread(target=runner, args=(db.mines_start, uid, rid(40 + uid), 250, 3, NOW, path)) for uid in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("оба игрока стартовали", (len(results), errors, balance(path, 1), balance(path, 2)), (2, [], 750, 750))

    # ================= просрочка 24 часа =================
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)   # без начисления по часам
    db.mines_start(1, rid(1), 200, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    s = db.mines_state(1, now=NOW + DAY - 1, db_path=path)
    check("до 24 часов игра активна", (s["game"] is not None, s["last"], s["balance"]), (True, None, 800))
    s = db.mines_state(1, now=NOW + DAY, db_path=path)
    check("через 24 часа без открытых клеток: возврат", (s["game"], s["last"]["status"], s["last"]["payout"], s["balance"]),
          (None, "auto_refunded", 200, 1000))
    check("идемпотентно", db.settle_expired_mines(1, now=NOW + 2 * DAY, db_path=path), False)
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)   # без начисления: часы в будущем
    db.mines_start(1, rid(1), 200, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    db.mines_reveal(1, rid(2), 10, now=NOW + 5, db_path=path)
    db.mines_reveal(1, rid(3), 11, now=NOW + 6, db_path=path)
    check("не просрочена на границе от последнего действия", db.settle_expired_mines(1, now=NOW + 6 + DAY - 1, db_path=path), False)
    check("просрочена", db.settle_expired_mines(1, now=NOW + 6 + DAY, db_path=path), True)
    owed = mines.payout(200, 3, 2)
    check("k > 0: автовыплата", (game_row(path, 1)[:2], balance(path, 1)), (("auto_cashed", owed), 800 + owed))
    check("повторное закрытие ничего не делает", db.settle_expired_mines(1, now=NOW + 9 * DAY, db_path=path), False)
    check("баланс прежний", balance(path, 1), 800 + owed)
    check("total_staked от первого открытия остался", staked(path, 1), 200)
    # новая игра после автозакрытия
    check("новая игра возможна", db.mines_start(1, rid(4), 100, 3, now=NOW + 2 * DAY, db_path=path)["game"]["bet"], 100)
    # просроченная игра закрывается первым шагом любого действия (старт без отдельного запроса)
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    db.mines_start(1, rid(1), 200, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    r = db.mines_start(1, rid(2), 300, 3, now=NOW + DAY, db_path=path)
    check("старт после просрочки: старая закрыта возвратом, новая открыта", (r["game"]["bet"], r["balance"], active_count(path, 1)), (300, 700, 1))
    # reveal на просроченной игре: игра закрыта, действие даёт no_active_game, закрытие сохраняется
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    db.mines_start(1, rid(1), 200, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    raises(mines.NoActiveGame, db.mines_reveal, 1, rid(2), 5, NOW + DAY, path)
    check("закрытие не откатилось", (game_row(path, 1)[0], balance(path, 1)), ("auto_refunded", 1000))
    # фоновое закрытие пачками
    path = new_db()
    for uid in range(1, 7):
        add_player(path, uid, balance=1000, last_accrual=NOW + 10 * DAY)
        db.mines_start(uid, rid(uid), 100, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    add_player(path, 7, balance=1000, last_accrual=NOW + 10 * DAY)
    db.mines_start(7, rid(7), 100, 3, now=NOW + DAY - 10, db_path=path)            # свежая
    cap.lines.clear()
    check("первая пачка 2", db.close_expired_mines(now=NOW + DAY, db_path=path, batch=2), 2)
    check("вторая пачка 2", db.close_expired_mines(now=NOW + DAY, db_path=path, batch=2), 2)
    check("остаток 2", db.close_expired_mines(now=NOW + DAY, db_path=path, batch=200), 2)
    check("больше нечего закрывать", db.close_expired_mines(now=NOW + DAY, db_path=path), 0)
    check("свежая игра не затронута", (active_count(path, 7), balance(path, 7)), (1, 900))
    check("остальные вернули ставку", [balance(path, u) for u in range(1, 7)], [1000] * 6)
    closed_lines = [l for l in cap.lines if "просроченных игр в мины" in l]
    check("в логе только количество", closed_lines, ["Закрыто просроченных игр в мины: 2"] * 2 + ["Закрыто просроченных игр в мины: 2"])
    # фоновая задача обслуживания закрывает игры
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    db.mines_start(1, rid(1), 100, 3, now=NOW, db_path=path)
    cfg = backup.load_config({"BACKUP_ENABLED": "0"}, path)
    backup.run_maintenance_once(cfg, path, NOW + DAY, None)
    check("run_maintenance_once закрыл игру", (game_row(path, 1)[0], balance(path, 1)), ("auto_refunded", 1000))
    # потолок баланса при зачислении
    path = new_db()
    add_player(path, 1, balance=MAX_SAFE_INT - 5, last_accrual=NOW + 10 * DAY)
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, created_at, updated_at) "
              "VALUES (1, 1000, 3, 7, 1792, 'active', ?, ?)", (NOW, NOW))
    r = db.mines_cashout(1, rid(1), now=NOW + 5, db_path=path)
    check("зачисление не выше MAX_SAFE_INT", (r["balance"], r["last"]["payout"]), (MAX_SAFE_INT, 5))

    # ================= API =================
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    sql(path, "UPDATE players SET rate = 0 WHERE telegram_id = ?", (SECRET_ID,))   # без дохода: граница минуты в середине теста не меняет баланс
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "60",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "60"}),
                                clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    def post(name, body, uid=SECRET_ID):
        return client.post("/api/mines/" + name, headers=auth(uid), json=body)

    check("без подписи", [client.get("/api/mines/state").status_code,
                          client.post("/api/mines/start", json={}).status_code], [401, 401])
    s = client.get("/api/mines/state", headers=auth(SECRET_ID))
    check("state без игры", (s.status_code, s.json()), (200, {"game": None, "last": None, "balance": SECRET_BALANCE}))
    # 400
    bad_bodies = [
        {"request_id": rid(1), "bet": 0, "mines": 3}, {"request_id": rid(1), "bet": -5, "mines": 3},
        {"request_id": rid(1), "bet": True, "mines": 3}, {"request_id": rid(1), "bet": 1.5, "mines": 3},
        {"request_id": rid(1), "bet": "10", "mines": 3}, {"request_id": rid(1), "bet": 10 ** 9 + 1, "mines": 3},
        {"request_id": rid(1), "bet": 10, "mines": 0}, {"request_id": rid(1), "bet": 10, "mines": 25},
        {"request_id": rid(1), "bet": 10, "mines": True}, {"request_id": "short", "bet": 10, "mines": 3},
        {"request_id": rid(1), "bet": 10}, {"bet": 10, "mines": 3}, {"request_id": rid(1), "bet": 10, "mines": 3, "x": 1}, [], "text"]
    for body in bad_bodies:
        r = post("start", body)
        check("400 start %r" % (body,), (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    for body in ({"request_id": rid(1), "cell": 25}, {"request_id": rid(1), "cell": -1}, {"request_id": rid(1), "cell": True},
                 {"request_id": rid(1), "cell": "1"}, {"request_id": rid(1), "cell": 1.5}, {"request_id": rid(1)},
                 {"request_id": rid(1), "cell": 1, "x": 1}):
        check("400 reveal %r" % (body,), post("reveal", body).status_code, 400)
    check("400 cashout", post("cashout", {"request_id": rid(1), "x": 1}).status_code, 400)
    check("400 не json", client.post("/api/mines/start", headers=auth(SECRET_ID), content=b"nope").status_code, 400)
    # 409 без игры
    for name, body in (("reveal", {"request_id": rid(2), "cell": 3}), ("cashout", {"request_id": rid(3)})):
        r = post(name, body)
        check("409 no_active_game " + name, (r.status_code, r.json()), (409, {"detail": "no_active_game"}))
    r = post("start", {"request_id": rid(4), "bet": SECRET_BALANCE + 1, "mines": 3})
    check("409 insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    # старт
    r = post("start", {"request_id": rid(5), "bet": SECRET_BET, "mines": 3})
    body = r.json()
    check("start 200", (r.status_code, sorted(body), body["replayed"], body["balance"]), (200, ["balance", "game", "replayed"], False, SECRET_BALANCE - SECRET_BET))
    check("поля игры", sorted(body["game"]), ["bet", "expires_at", "mines", "multiplier", "next_multiplier", "next_payout",
                                              "payout_now", "revealed", "safe_left"])
    no_layout(body, "API start")
    mask = sql(path, "SELECT mine_mask FROM mines_games WHERE status = 'active'")[0][0]
    mine_cells = mines.cells_of(mask)
    safe_cells = [c for c in range(25) if c not in mine_cells]
    r = post("start", {"request_id": rid(6), "bet": 10, "mines": 3})
    check("409 active_game_exists", (r.status_code, r.json()), (409, {"detail": "active_game_exists"}))
    r = post("start", {"request_id": rid(5), "bet": 10, "mines": 3})
    check("409 request_conflict", (r.status_code, r.json()), (409, {"detail": "request_conflict"}))
    r = post("start", {"request_id": rid(5), "bet": SECRET_BET, "mines": 3})
    check("повтор через API", (r.status_code, r.json()["replayed"], r.json()["game"]["bet"]), (200, True, SECRET_BET))
    r = post("reveal", {"request_id": rid(7), "cell": safe_cells[0]})
    check("reveal safe", (r.status_code, r.json()["result"], r.json()["game"]["revealed"]), (200, "safe", [safe_cells[0]]))
    check("поля ответа reveal", sorted(r.json()), ["balance", "game", "replayed", "result"])
    no_layout(r.json(), "API reveal safe")
    r = post("reveal", {"request_id": rid(8), "cell": safe_cells[0]})
    check("409 already_revealed", (r.status_code, r.json()), (409, {"detail": "already_revealed"}))
    s = client.get("/api/mines/state", headers=auth(SECRET_ID))
    check("state с игрой", (s.status_code, s.json()["game"]["revealed"], s.json()["last"]), (200, [safe_cells[0]], None))
    no_layout(s.json(), "API state")
    r = post("reveal", {"request_id": rid(7), "cell": safe_cells[0]})
    check("повтор reveal через API", (r.json()["replayed"], r.json()["result"]), (True, "safe"))
    no_layout(r.json(), "API reveal replay")
    for (stored,) in sql(path, "SELECT response_json FROM mines_actions WHERE telegram_id = ?", (SECRET_ID,)):
        assert "mine_cells" not in stored and "mine_mask" not in stored, "раскладка в response_json активной игры"
    r = post("reveal", {"request_id": rid(9), "cell": mine_cells[0]})
    check("мина", (r.status_code, r.json()["result"], r.json()["game"], sorted(r.json())), (200, "mine", None, ["balance", "game", "last", "replayed", "result"]))
    check("раскладка у завершённой игры в ответе", r.json()["last"]["mine_cells"], mine_cells)
    check("поля last", sorted(r.json()["last"]), ["bet", "finished_at", "mine_cells", "mines", "payout", "revealed", "status"])
    s = client.get("/api/mines/state", headers=auth(SECRET_ID)).json()
    check("state: last с раскладкой", (s["game"], s["last"]["status"], s["last"]["mine_cells"]), (None, "lost", mine_cells))
    # cashout через API
    r = post("start", {"request_id": rid(10), "bet": 100, "mines": 3})
    check("cashout без открытых: возврат", post("cashout", {"request_id": rid(11)}).json()["last"]["status"], "refunded")
    r2 = post("cashout", {"request_id": rid(11)})
    check("повтор cashout", (r2.status_code, r2.json()["replayed"]), (200, True))
    check("поля cashout", sorted(r2.json()), ["balance", "last", "replayed"])
    # просроченная игра закрывается при GET /api/me
    old_t = int(time.time()) - 2 * DAY
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, created_at, updated_at) "
              "VALUES (?, 500, 3, 7, 0, 'active', ?, ?)", (SECRET_ID, old_t, old_t))
    before = balance(path, SECRET_ID)
    me = client.get("/api/me", headers=auth(SECRET_ID))
    check("/api/me", me.status_code, 200)
    check("/api/me закрыл просроченную игру", (game_row(path, SECRET_ID)[0], balance(path, SECRET_ID)), ("auto_refunded", before + 500))
    check("ключи /api/me прежние и active_game", sorted(me.json()), ["active_game", "balance", "cosmetics", "farm", "income_level", "level", "rate", "seconds_to_next", "storage_level"])
    check("баланс в /api/me включает возврат", me.json()["balance"], before + 500)
    st = client.get("/api/mines/state", headers=auth(SECRET_ID)).json()
    check("state: автовозврат виден клиенту как auto_refunded", (st["game"], st["last"]["status"], st["last"]["payout"]), (None, "auto_refunded", 500))

    # ================= лимит частоты: write и read =================
    path2 = new_db()
    add_player(path2, 1, balance=10_000, last_accrual=int(time.time()))
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1",
                                                        "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: 1000.0)
    c2 = TestClient(create_app(TOKEN, [], db_path=path2, rate_limiter=lim2))
    h = auth(1)
    codes = [c2.post("/api/mines/start", headers=h, json={"request_id": rid(50), "bet": 10, "mines": 3}).status_code,
             c2.post("/api/mines/reveal", headers=h, json={"request_id": rid(51), "cell": 3}).status_code,
             c2.post("/api/mines/cashout", headers=h, json={"request_id": rid(52)}).status_code]
    check("start, reveal, cashout делят группу write", codes[0] in (200,) and codes[1] in (200, 409) and codes[2] in (200, 409), True)
    r = c2.post("/api/mines/start", headers=h, json={"request_id": rid(53), "bet": 10, "mines": 3})
    check("четвёртый write: 429", (r.status_code, r.json(), r.headers["Retry-After"]), (429, {"error": "too_many_requests"}, "1"))
    check("state в группе read (write не мешает)", [c2.get("/api/mines/state", headers=h).status_code for _ in range(2)], [200, 200])
    r = c2.get("/api/mines/state", headers=h)
    check("третий read: 429", (r.status_code, r.headers["Retry-After"]), (429, "1"))
    check("read не мешает ни write-лимит, ни /api/me отдельно", c2.get("/api/me", headers=h).status_code, 429)

    # ================= /mydata и /deletemydata =================
    path = new_db()
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=NOW + 10 * DAY)
    add_player(path, 801, balance=50_000, last_accrual=NOW + 10 * DAY)
    layout = [3, 9, 14, 20, 24]
    layout_mask = sum(1 << c for c in layout)
    db.mines_start(801, rid(1), 700, 3, now=NOW, db_path=path, rng=FixedRng([0, 1, 2]))
    db.mines_reveal(801, rid(2), 10, now=NOW + 1, db_path=path)
    db.mines_start(SECRET_ID, rid(3), SECRET_BET, 5, now=NOW, db_path=path, rng=FixedRng(layout))
    db.mines_reveal(SECRET_ID, rid(4), 10, now=NOW + 1, db_path=path)
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: игры", export["mines_games"], [{"created_at": NOW, "bet": SECRET_BET, "mines": 5, "opened": 1,
                                                    "status": "active", "payout": 0, "finished_at": None}])
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    text = upd.effective_message.documents[0]["data"].decode("utf-8")
    payload = json.loads(text)
    check("/mydata: раздел mines_games", [g["status"] for g in payload["mines_games"]], ["active"])
    assert "mine_mask" not in text and "mine_cells" not in text and str(layout_mask) not in text, "раскладка в выгрузке"
    # завершённая игра тоже без раскладки
    db.mines_reveal(SECRET_ID, rid(5), layout[0], now=NOW + 2, db_path=path)
    bot.mydata_limiter.last.clear()   # /mydata не чаще раза в интервал на игрока
    upd = FakeUpdate("private", user_id=SECRET_ID)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    text = upd.effective_message.documents[0]["data"].decode("utf-8")
    assert "mine_mask" not in text and "mine_cells" not in text and str(layout_mask) not in text, "раскладка завершённой игры"
    check("завершённая игра в выгрузке", json.loads(text)["mines_games"][0]["status"], "lost")
    # игры ограничены 100
    for i in range(110):
        sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at, finished_at) "
                  "VALUES (801, 1, 3, 7, 'lost', ?, ?, ?)", (NOW + 100 + i, NOW + 100 + i, NOW + 100 + i))
    check("в выгрузке не больше 100", len(db.get_player_export(801, rounds_limit=100, db_path=path)["mines_games"]), 100)
    # удаление: незавершённая игра у второго игрока для проверки изоляции, у нашего своя
    db.mines_start(SECRET_ID, rid(6), 50, 3, now=NOW + 3, db_path=path, rng=FixedRng([0, 1, 2]))
    others_games = sql(path, "SELECT COUNT(*) FROM mines_games WHERE telegram_id = 801")[0][0]
    others_actions = sql(path, "SELECT COUNT(*) FROM mines_actions WHERE telegram_id = 801")[0][0]
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалены игры (включая незавершённую)", counts["mines_games"], 2)
    check("игры и действия игрока удалены", (sql(path, "SELECT COUNT(*) FROM mines_games WHERE telegram_id = ?", (SECRET_ID,))[0][0],
                                            sql(path, "SELECT COUNT(*) FROM mines_actions WHERE telegram_id = ?", (SECRET_ID,))[0][0]), (0, 0))
    check("чужие игры и действия целы", (sql(path, "SELECT COUNT(*) FROM mines_games WHERE telegram_id = 801")[0][0],
                                         sql(path, "SELECT COUNT(*) FROM mines_actions WHERE telegram_id = 801")[0][0]),
          (others_games, others_actions))
    check("активная игра чужого цела", active_count(path, 801), 1)
    # сообщения
    add_player(path, 900, balance=5000, last_accrual=NOW + 10 * DAY)
    db.mines_start(900, rid(7), 100, 3, now=NOW, db_path=path)
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    warn = bot.DELETE_WARNING
    assert "незавершённая игра в мины (вместе со ставкой)" in warn and "история игр в мины" in warn, warn
    q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
    asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "игры в мины — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)

    # ================= очистка =================
    path = new_db()
    add_player(path, 1, balance=1000, last_accrual=NOW + 10 * DAY)
    ins = ("INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at, finished_at) "
           "VALUES (1, 10, 3, 7, ?, ?, ?, ?)")
    sql(path, ins, ("lost", NOW - 40 * DAY, NOW - 40 * DAY, NOW - 40 * DAY))
    sql(path, ins, ("cashed", NOW - 31 * DAY, NOW - 31 * DAY, NOW - 31 * DAY))
    sql(path, ins, ("refunded", NOW - 5 * DAY, NOW - 5 * DAY, NOW - 5 * DAY))
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at) "
              "VALUES (2, 10, 3, 7, 'active', ?, ?)", (NOW - 90 * DAY, NOW - 90 * DAY))
    for i, age in enumerate((40, 31, 5)):
        sql(path, "INSERT INTO mines_actions VALUES (1, ?, 'start', '{}', '{}', ?)", ("old-action-%d" % i, NOW - age * DAY))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("удалены старые завершённые игры и действия", (deleted["mines_games"], deleted["mines_actions"]), (2, 2))
    check("свежая завершённая и активная остались", sorted(r[0] for r in sql(path, "SELECT status FROM mines_games")), ["active", "refunded"])
    deleted = db.purge_old_data(now=NOW + 400 * DAY, db_path=path, rounds_days=30)
    check("активная игра не удаляется никогда", sorted(r[0] for r in sql(path, "SELECT status FROM mines_games")), ["active"])

    # ================= политика =================
    page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "<script" not in page.lower(), "на странице скрипт"
    assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
    assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
    assert "историю ваших игр в мины (ставка, число мин, число открытых клеток, итог, выигрыш)" in page
    assert "состояние вашей незавершённой игры в мины (включая расположение мин, оно хранится только на сервере)" in page
    assert ("История игр в мины хранится 30 дней. Если вы не продолжаете начатую игру в мины 24 часа, она закрывается "
            "автоматически: выплачивается выигрыш за открытые клетки, а если клеток не открыто, возвращается ставка.") in page
    sec2 = page[page.index("<h2>2."):page.index("<h2>3.")]
    sec5 = page[page.index("<h2>5."):page.index("<h2>6.")]
    sec4 = page[page.index("<h2>4."):page.index("<h2>5.")]
    assert "игр в мины" in sec2 and "игр в мины хранится 30 дней" in sec5 and "мин" not in sec4.replace("минут", "")
    assert "Дата последнего обновления:" in page

    # ================= в логах нет id, ставок, балансов и раскладок =================
    for secret in (str(SECRET_ID), str(SECRET_BET), str(SECRET_BALANCE), str(layout_mask), "mine_mask", "mine_cells"):
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
