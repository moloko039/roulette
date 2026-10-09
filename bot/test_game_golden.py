"""Золотые эталоны партийных игр (мины, блэкджек, краш, хило): детерминированные сценарии на временной базе сравниваются с
записью в testdata/game_golden.json. Для каждой операции сверяются: ответ (хэш полного JSON или имя и аргументы ошибки), состояние
таблиц (хэш дампа каждой затронутой таблицы), число SQL-операций по видам и хэш всей последовательности SQL с параметрами
(порядок денежных операций тоже зафиксирован). Эталон записан на коде ДО рефакторинга шаблона партии.

Запись заново (только осознанно, при намеренном изменении поведения): python test_game_golden.py --record
Точечное обновление перечисленных операций целиком (намеренное изменение правил игры): python test_game_golden.py --update-ops "сценарий#номер,...";
отказывается писать, если разошлась хоть одна не перечисленная операция.
Точечное обновление журнала SQL (намеренное изменение запросов без изменения ответов, например быстрая проверка чтением вместо блокировки записи):
python test_game_golden.py --update-sql. Меняет в эталоне ТОЛЬКО поля sql и seq у операций, у которых ответ (res) и состояние таблиц (tbl) совпали;
если у какой-то операции разошлось что-то ещё, обновление отменяется (эталон не трогается)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import blackjack
import db
import hilo
import mines
from core import db_conn

NOW = 1_760_000_000
DAY = 86400
A, B, C = 424242421, 424242422, 424242423
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata", "game_golden.json")

# тест не зависит от окружения и bot/.env
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "SQLITE_JOURNAL_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"

# ---------- запись SQL: счётчик по видам и хэш последовательности (с параметрами) ----------
SQL_LOG = [None]
_orig_execute = db_conn.TimedConnection.execute


def _logged_execute(self, sql, *args):
    log = SQL_LOG[0]
    if log is not None and "player_best_win" not in sql:     # запись личного рекорда (core.kernel._record_best_win) в эталон денежных операций не входит: её проверяет test_best_wins.py
        log.append((re.sub(r"\s+", " ", sql.strip()), repr(args[0]) if args else ""))
    return _orig_execute(self, sql, *args)


db_conn.TimedConnection.execute = _logged_execute


def sql_summary(log):
    kinds = {}
    for text, _ in log:
        kind = text.split(" ", 1)[0].upper()
        kinds[kind] = kinds.get(kind, 0) + 1
    seq = hashlib.sha256(json.dumps(log, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    return "".join("%s%d" % (k[0] + k[1:3].lower() if k not in ("BEGIN", "COMMIT") else k[:2], v) for k, v in sorted(kinds.items())), seq


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:10]


# ---------- подмена случайности ----------
class Scripted:
    """Подаёт заранее заданные значения: randrange (карты хило, число краша), sample (раскладка мин), shuffle (колода)."""

    def __init__(self, ints=(), samples=(), shoes=()):
        self.ints, self.samples, self.shoes = list(ints), list(samples), list(shoes)

    def randrange(self, n):
        v = self.ints.pop(0)
        assert 0 <= v < n
        return v

    def sample(self, population, k):
        cells = self.samples.pop(0)
        assert len(cells) == k
        return list(cells)

    def shuffle(self, items):
        head = self.shoes.pop(0)
        rest = list(items)
        for card in head:
            rest.remove(card)
        items[:] = list(head) + rest


def cards(*pairs):
    out = []
    for rank, suit in pairs:
        out += [rank - 1, hilo.SUITS.index(suit)]
    return Scripted(ints=out)


# ---------- запись сценария ----------
TABLES = {
    "players": "SELECT telegram_id, balance, xp, total_staked, last_accrual, accrual_acc, rate FROM players ORDER BY telegram_id",
    "hilo_games": "SELECT * FROM hilo_games ORDER BY id", "hilo_actions": "SELECT * FROM hilo_actions ORDER BY telegram_id, request_id",
    "crash_games": "SELECT * FROM crash_games ORDER BY id", "crash_actions": "SELECT * FROM crash_actions ORDER BY telegram_id, request_id",
    "blackjack_games": "SELECT * FROM blackjack_games ORDER BY id", "blackjack_actions": "SELECT * FROM blackjack_actions ORDER BY telegram_id, request_id",
    "mines_games": "SELECT * FROM mines_games ORDER BY id", "mines_actions": "SELECT * FROM mines_actions ORDER BY telegram_id, request_id",
}
_tmp = tempfile.mkdtemp()
_counter = [0]


def outcome(fn, *a, **k):
    try:
        return "ok", fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        return "err", "%s%r" % (type(exc).__name__, exc.args)


class Run:
    def __init__(self, name):
        _counter[0] += 1
        self.name = name
        self.path = os.path.join(_tmp, "g%d.db" % _counter[0])
        db.init_db(self.path)
        self.ops = []
        self.t = NOW
        self.last = {}
        self.anon = False       # после параллельных запросов request_id победителя случаен: в actions он не сравнивается

    def player(self, uid, balance=1_000_000, xp=0, total=0, accrual=NOW + 10 * DAY, rate=100):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)", (uid, balance, rate, accrual, NOW - DAY, total, xp))
        conn.commit()
        conn.close()

    def raw(self, query, params=()):
        conn = sqlite3.connect(self.path)
        conn.execute(query, params)
        conn.commit()
        conn.close()

    def dump(self):
        conn = sqlite3.connect(self.path)
        try:
            out = {}
            for name, query in TABLES.items():
                if self.anon and name.endswith("_actions"):
                    query = "SELECT action, params, response_json, created_at FROM %s ORDER BY 1, 2, 3, 4" % name
                out[name] = [list(r) for r in conn.execute(query)]
            return out
        finally:
            conn.close()

    def op(self, label, fn, *a, **k):
        """Одна операция: ответ, SQL-статистика и хэши таблиц (только изменившихся относительно прошлой операции)."""
        SQL_LOG[0] = []
        kind, value = outcome(fn, *a, **k)
        log, SQL_LOG[0] = SQL_LOG[0], None
        self.record(label, kind, value, log)
        return value if kind == "ok" else None

    def record(self, label, kind, value, log):
        dump = self.dump()
        changed = {n: digest(rows) for n, rows in dump.items() if digest(rows) != self.last.get(n)}
        self.last = {n: digest(rows) for n, rows in dump.items()}
        entry = {"op": label, "res": kind[0] + ":" + (digest(value) if kind == "ok" else str(value)),
                 "tbl": changed}
        if log is not None:
            entry["sql"], entry["seq"] = sql_summary(log)
        self.ops.append(entry)

    def parallel(self, label, fn, args_list):
        """Параллельные запросы (20 потоков): записывается сводка исходов (число успехов и видов ошибок), а не порядок."""
        gate = threading.Barrier(len(args_list))
        self.anon = True

        def call(args):
            gate.wait()
            kind, value = outcome(fn, *args)
            if kind == "ok":
                return "ok:" + str(value.get("replayed")) + ":" + str(value.get("status", value.get("result", "")))
            return value

        with ThreadPoolExecutor(len(args_list)) as pool:
            results = sorted(pool.map(call, args_list))
        summary = {}
        for r in results:
            summary[r] = summary.get(r, 0) + 1
        self.record(label, "ok", summary, None)

    def tick(self, seconds=5):
        self.t += seconds
        return self.t


def rid(run, tag):
    return "%s-%s" % (run.name[:6], tag)


# ---------- хило ----------
def hilo_scenarios():
    out = []
    r = Run("hilo_main")
    r.player(A, 5_000, accrual=NOW - 3600, rate=6000)       # с начислением и малым балансом
    r.player(B, 10 ** 12)
    out.append(r)
    r.op("state пустой", db.hilo_state, A, now=r.tick(), db_path=r.path)
    r.op("cashout без партии", db.hilo_cashout, A, rid(r, "c0"), now=r.tick(), db_path=r.path)
    r.op("guess без партии", db.hilo_guess, A, rid(r, "g0"), "hi", now=r.tick(), db_path=r.path, rng=cards((5, "S")))
    for bad in (0, -1, True, 1.5, "10", hilo.HILO_MAX_BET + 1):
        r.op("ставка %r" % (bad,), db.hilo_start, A, rid(r, "bad"), bad, now=r.tick(), db_path=r.path, rng=cards((5, "S")))
    r.op("плохой выбор", db.hilo_guess, A, rid(r, "badc"), "up", now=r.tick(), db_path=r.path)
    r.op("не хватает фишек", db.hilo_start, A, rid(r, "poor"), 10 ** 9, now=r.tick(), db_path=r.path, rng=cards((5, "S")))
    r.op("старт", db.hilo_start, A, rid(r, "s1"), 1000, now=r.tick(), db_path=r.path, rng=cards((7, "S")))
    r.op("повтор старта", db.hilo_start, A, rid(r, "s1"), 1000, now=r.tick(), db_path=r.path, rng=cards((2, "S")))
    r.op("конфликт старта (ставка)", db.hilo_start, A, rid(r, "s1"), 2000, now=r.tick(), db_path=r.path, rng=cards((2, "S")))
    r.op("вторая партия при активной", db.hilo_start, A, rid(r, "s2"), 1000, now=r.tick(), db_path=r.path, rng=cards((2, "S")))
    r.op("active_game", db.active_game_of, A, db_path=r.path)
    r.op("cashout без ходов", db.hilo_cashout, A, rid(r, "c1"), now=r.tick(), db_path=r.path)
    r.op("skip", db.hilo_guess, A, rid(r, "k1"), "skip", now=r.tick(), db_path=r.path, rng=cards((13, "D")))
    r.op("hi на короле запрещён? (13: hi k=1)", db.hilo_guess, A, rid(r, "h0"), "hi", now=r.tick(), db_path=r.path, rng=cards((13, "H")))
    r.op("повтор хода", db.hilo_guess, A, rid(r, "h0"), "hi", now=r.tick(), db_path=r.path, rng=cards((1, "H")))
    r.op("конфликт хода", db.hilo_guess, A, rid(r, "h0"), "lo", now=r.tick(), db_path=r.path, rng=cards((1, "H")))
    r.op("lo угадан", db.hilo_guess, A, rid(r, "l1"), "lo", now=r.tick(), db_path=r.path, rng=cards((3, "C")))
    r.op("state активной", db.hilo_state, A, now=r.tick(), db_path=r.path)
    r.op("hi проигран", db.hilo_guess, A, rid(r, "h2"), "hi", now=r.tick(), db_path=r.path, rng=cards((2, "D")))
    r.op("guess после конца", db.hilo_guess, A, rid(r, "h3"), "hi", now=r.tick(), db_path=r.path, rng=cards((2, "D")))
    r.op("state после конца", db.hilo_state, A, now=r.tick(), db_path=r.path)
    r.op("active_game нет", db.active_game_of, A, db_path=r.path)
    # ход, запрещённый при k = 13 (hi на тузе), cashout после хода, ничья
    r.op("старт 2", db.hilo_start, B, rid(r, "b1"), 1000, now=r.tick(), db_path=r.path, rng=cards((1, "S")))
    r.op("hi на тузе запрещён", db.hilo_guess, B, rid(r, "b2"), "hi", now=r.tick(), db_path=r.path, rng=cards((5, "S")))
    r.op("lo на тузе (ничья)", db.hilo_guess, B, rid(r, "b3"), "lo", now=r.tick(), db_path=r.path, rng=cards((1, "H")))
    r.op("cashout", db.hilo_cashout, B, rid(r, "b4"), now=r.tick(), db_path=r.path)
    r.op("повтор cashout", db.hilo_cashout, B, rid(r, "b4"), now=r.tick(), db_path=r.path)
    r.op("конфликт cashout/guess", db.hilo_guess, B, rid(r, "b4"), "hi", now=r.tick(), db_path=r.path, rng=cards((1, "H")))
    # потолок ×1000
    r.op("старт для потолка", db.hilo_start, B, rid(r, "p1"), 1000, now=r.tick(), db_path=r.path, rng=cards((1, "S")))
    for i, card in enumerate(((1, "H"), (1, "D"), (1, "C"))):
        r.op("потолок шаг %d" % i, db.hilo_guess, B, rid(r, "p%d" % (i + 2)), "lo", now=r.tick(), db_path=r.path, rng=cards(card))
    r.op("максимальная ставка: старт", db.hilo_start, B, rid(r, "m1"), hilo.HILO_MAX_BET, now=r.tick(), db_path=r.path, rng=cards((1, "S")))
    for i, card in enumerate(((1, "H"), (1, "D"), (1, "C"))):
        r.op("максимум шаг %d" % i, db.hilo_guess, B, rid(r, "m%d" % (i + 2)), "lo", now=r.tick(), db_path=r.path, rng=cards(card))

    # автозакрытие
    r = Run("hilo_expire")
    r.player(A)
    r.player(B)
    r.player(C)
    out.append(r)
    t0 = r.tick()
    r.op("A старт", db.hilo_start, A, rid(r, "a1"), 500, now=t0, db_path=r.path, rng=cards((7, "S")))
    r.op("B старт", db.hilo_start, B, rid(r, "b1"), 700, now=t0, db_path=r.path, rng=cards((7, "S")))
    r.op("B ход", db.hilo_guess, B, rid(r, "b2"), "hi", now=t0 + 1, db_path=r.path, rng=cards((9, "S")))
    r.op("C старт", db.hilo_start, C, rid(r, "c1"), 900, now=t0 + 2, db_path=r.path, rng=cards((7, "S")))
    r.op("settle рано", db.settle_expired_hilo, A, now=t0 + hilo.HILO_IDLE_SECONDS - 5, db_path=r.path)
    r.op("close рано", db.close_expired_hilo, now=t0 + hilo.HILO_IDLE_SECONDS - 5, db_path=r.path)
    r.op("settle A (возврат)", db.settle_expired_hilo, A, now=t0 + hilo.HILO_IDLE_SECONDS, db_path=r.path)
    r.op("settle A повтор", db.settle_expired_hilo, A, now=t0 + hilo.HILO_IDLE_SECONDS + 1, db_path=r.path)
    r.op("close (B: автовывод)", db.close_expired_hilo, now=t0 + hilo.HILO_IDLE_SECONDS + 1, db_path=r.path, batch=1)
    r.op("close остальные", db.close_expired_hilo, now=t0 + hilo.HILO_IDLE_SECONDS + 5, db_path=r.path)
    r.op("state C после закрытия", db.hilo_state, C, now=t0 + hilo.HILO_IDLE_SECONDS + 6, db_path=r.path)
    r.op("новый старт после истечения", db.hilo_start, A, rid(r, "a2"), 100, now=t0 + 2 * hilo.HILO_IDLE_SECONDS, db_path=r.path, rng=cards((7, "S")))
    r.op("старт при просроченной (закрывается самим действием)", db.hilo_start, A, rid(r, "a3"), 100, now=t0 + 4 * hilo.HILO_IDLE_SECONDS, db_path=r.path, rng=cards((8, "S")))

    # параллельные запросы
    r = Run("hilo_parallel")
    r.player(A)
    r.player(B)
    out.append(r)
    r.op("старт", db.hilo_start, A, rid(r, "a1"), 1000, now=r.tick(), db_path=r.path, rng=cards((7, "S")))
    r.op("ход", db.hilo_guess, A, rid(r, "a2"), "hi", now=r.tick(), db_path=r.path, rng=cards((9, "S")))
    t = r.tick()
    r.parallel("20 cashout с разными request_id", lambda i: db.hilo_cashout(A, rid(r, "pc%d" % i), now=t, db_path=r.path), [(i,) for i in range(20)])
    r.parallel("20 стартов с разными request_id", lambda i: db.hilo_start(B, rid(r, "ps%d" % i), 100, now=t, db_path=r.path, rng=cards((7, "S"))), [(i,) for i in range(20)])
    r.parallel("20 одинаковых request_id", lambda i: db.hilo_start(A, rid(r, "same"), 100, now=t + 1, db_path=r.path, rng=cards((7, "S"))), [(i,) for i in range(20)])
    r.parallel("20 ходов hi одной партии", lambda i: db.hilo_guess(B, rid(r, "pg%d" % i), "skip", now=t + 2, db_path=r.path, rng=cards((7, "S"))), [(i,) for i in range(20)])
    return out


# ---------- блэкджек ----------
def shoe(*codes):
    return [c for c in codes]


def blackjack_scenarios():
    out = []
    r = Run("bj_main")
    r.player(A, 5_000, accrual=NOW - 3600, rate=6000)
    r.player(B, 10 ** 12)
    out.append(r)

    def start(user, tag, bet, *deck, **k):
        return r.op("старт " + tag, db.blackjack_start, user, rid(r, tag), bet, now=r.tick(), db_path=r.path, rng=Scripted(shoes=[deck]), **k)

    def act(user, tag, action, label=None):
        return r.op(label or (action + " " + tag), db.blackjack_action, user, rid(r, tag), action, now=r.tick(), db_path=r.path)

    r.op("state пустой", db.blackjack_state, A, now=r.tick(), db_path=r.path)
    act(A, "x0", "hit", "hit без раздачи")
    act(A, "x1", "stand", "stand без раздачи")
    for bad in (0, -1, True, 1.5, blackjack.BLACKJACK_MAX_BET + 1):
        r.op("ставка %r" % (bad,), db.blackjack_start, A, rid(r, "bad"), bad, now=r.tick(), db_path=r.path)
    r.op("плохое действие", db.blackjack_action, A, rid(r, "ba"), "split", now=r.tick(), db_path=r.path)
    r.op("не хватает фишек", db.blackjack_start, A, rid(r, "poor"), 10 ** 9, now=r.tick(), db_path=r.path)
    start(A, "s1", 1000, "10S", "5H", "6D", "9C", "3S", "KD", "2H")
    r.op("повтор старта", db.blackjack_start, A, rid(r, "s1"), 1000, now=r.tick(), db_path=r.path, rng=Scripted(shoes=[("AS",)]))
    r.op("конфликт старта", db.blackjack_start, A, rid(r, "s1"), 2000, now=r.tick(), db_path=r.path)
    r.op("вторая при активной", db.blackjack_start, A, rid(r, "s2"), 1000, now=r.tick(), db_path=r.path)
    r.op("active_game", db.active_game_of, A, db_path=r.path)
    r.op("state активной", db.blackjack_state, A, now=r.tick(), db_path=r.path)
    act(A, "h1", "hit")
    act(A, "h1", "hit", "повтор hit")
    act(A, "h1", "stand", "конфликт hit/stand")
    act(A, "d0", "double", "double после hit запрещён")
    act(A, "st", "stand")
    act(A, "st2", "stand", "stand после конца")
    r.op("state после", db.blackjack_state, A, now=r.tick(), db_path=r.path)
    # блэкджек игрока, ничья блэкджеков, блэкджек дилера
    start(A, "bj1", 1000, "AS", "5H", "KD", "9C")
    start(A, "bj2", 1000, "AS", "AH", "KD", "QC")
    start(A, "bj3", 1000, "5S", "AH", "6D", "KC")
    # double: выигрыш, проигрыш, ничья, нехватка фишек
    start(A, "dw", 1000, "5S", "9H", "6D", "7C", "10H", "10S")
    act(A, "dwd", "double")
    start(A, "dl", 1000, "5S", "9H", "6D", "10C", "2H", "KS")
    act(A, "dld", "double")
    start(A, "dp", 1000, "5S", "10H", "6D", "8C", "7H", "KS")
    act(A, "dpd", "double")
    # перебор, дилер берёт карты, дилер перебирает, ничья
    start(A, "bu", 1000, "10S", "5H", "6D", "9C", "KH", "2S")
    act(A, "bu1", "hit")
    start(A, "dd", 1000, "10S", "6H", "9D", "10C", "5S", "KD")
    act(A, "dd1", "stand")
    start(A, "pu", 1000, "10S", "10H", "8D", "8C")
    act(A, "pu1", "stand")
    # недостаточно для double
    r.raw("UPDATE players SET balance = 1500 WHERE telegram_id = ?", (A,))
    start(A, "po", 1000, "5S", "9H", "6D", "7C", "10H", "10S")
    act(A, "po1", "double", "double не хватает фишек")
    act(A, "po2", "stand")
    # максимальная ставка
    start(B, "mx", blackjack.BLACKJACK_MAX_BET, "AS", "5H", "KD", "9C")

    r = Run("bj_expire")
    for uid in (A, B, C):
        r.player(uid)
    out.append(r)
    t0 = r.tick()
    r.op("A старт", db.blackjack_start, A, rid(r, "a1"), 500, now=t0, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")]))
    r.op("B старт", db.blackjack_start, B, rid(r, "b1"), 700, now=t0, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "10D", "10C")]))
    r.op("C старт", db.blackjack_start, C, rid(r, "c1"), 900, now=t0 + 2, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")]))
    r.op("settle рано", db.settle_expired_blackjack, A, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS - 5, db_path=r.path)
    r.op("close рано", db.close_expired_blackjack, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS - 5, db_path=r.path)
    r.op("settle A", db.settle_expired_blackjack, A, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS, db_path=r.path)
    r.op("settle A повтор", db.settle_expired_blackjack, A, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS + 1, db_path=r.path)
    r.op("close партия", db.close_expired_blackjack, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS + 2, db_path=r.path, batch=1)
    r.op("close остальные", db.close_expired_blackjack, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS + 5, db_path=r.path)
    r.op("state после", db.blackjack_state, C, now=t0 + blackjack.BLACKJACK_IDLE_SECONDS + 6, db_path=r.path)
    r.op("старт после", db.blackjack_start, A, rid(r, "a2"), 100, now=t0 + 2 * blackjack.BLACKJACK_IDLE_SECONDS, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")]))
    r.op("действие просроченной (закрывается само)", db.blackjack_action, A, rid(r, "a3"), "hit", now=t0 + 4 * blackjack.BLACKJACK_IDLE_SECONDS, db_path=r.path)

    r = Run("bj_parallel")
    r.player(A)
    r.player(B)
    out.append(r)
    t = r.tick()
    r.op("старт", db.blackjack_start, A, rid(r, "a1"), 1000, now=t, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")]))
    r.parallel("20 stand", lambda i: db.blackjack_action(A, rid(r, "ps%d" % i), "stand", now=t + 1, db_path=r.path), [(i,) for i in range(20)])
    r.parallel("20 стартов", lambda i: db.blackjack_start(B, rid(r, "pt%d" % i), 100, now=t + 2, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")])), [(i,) for i in range(20)])
    r.parallel("20 одинаковых", lambda i: db.blackjack_start(A, rid(r, "same"), 100, now=t + 3, db_path=r.path, rng=Scripted(shoes=[("10S", "5H", "6D", "9C", "KD")])), [(i,) for i in range(20)])
    return out


# ---------- мины ----------
def mines_scenarios():
    out = []
    r = Run("mines_main")
    r.player(A, 5_000, accrual=NOW - 3600, rate=6000)
    r.player(B, 10 ** 12)
    out.append(r)

    def start(user, tag, bet, count, cells):
        return r.op("старт " + tag, db.mines_start, user, rid(r, tag), bet, count, now=r.tick(), db_path=r.path, rng=Scripted(samples=[cells]))

    def reveal(user, tag, cell, label=None):
        return r.op(label or "reveal %s %d" % (tag, cell), db.mines_reveal, user, rid(r, tag), cell, now=r.tick(), db_path=r.path)

    r.op("state пустой", db.mines_state, A, now=r.tick(), db_path=r.path)
    r.op("cashout без игры", db.mines_cashout, A, rid(r, "c0"), now=r.tick(), db_path=r.path)
    reveal(A, "r0", 3, "reveal без игры")
    for bad in (0, -1, True, 1.5, mines.MINES_MAX_BET + 1):
        r.op("ставка %r" % (bad,), db.mines_start, A, rid(r, "bad"), bad, 3, now=r.tick(), db_path=r.path)
    for bad in (0, 25, True, 2.5):
        r.op("мин %r" % (bad,), db.mines_start, A, rid(r, "badm"), 100, bad, now=r.tick(), db_path=r.path)
    for bad in (-1, 25, True, 1.5):
        r.op("клетка %r" % (bad,), db.mines_reveal, A, rid(r, "badc"), bad, now=r.tick(), db_path=r.path)
    r.op("не хватает фишек", db.mines_start, A, rid(r, "poor"), 10 ** 9, 3, now=r.tick(), db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    start(A, "s1", 1000, 3, (0, 1, 2))
    r.op("повтор старта", db.mines_start, A, rid(r, "s1"), 1000, 3, now=r.tick(), db_path=r.path, rng=Scripted(samples=[(5, 6, 7)]))
    r.op("конфликт старта", db.mines_start, A, rid(r, "s1"), 1000, 5, now=r.tick(), db_path=r.path)
    r.op("вторая при активной", db.mines_start, A, rid(r, "s2"), 1000, 3, now=r.tick(), db_path=r.path)
    r.op("active_game", db.active_game_of, A, db_path=r.path)
    r.op("state активной", db.mines_state, A, now=r.tick(), db_path=r.path)
    reveal(A, "r1", 10)
    reveal(A, "r1", 10, "повтор reveal")
    reveal(A, "r1", 11, "конфликт reveal")
    reveal(A, "r2", 10, "клетка уже открыта")
    reveal(A, "r3", 11)
    r.op("cashout", db.mines_cashout, A, rid(r, "c1"), now=r.tick(), db_path=r.path)
    r.op("повтор cashout", db.mines_cashout, A, rid(r, "c1"), now=r.tick(), db_path=r.path)
    r.op("state после", db.mines_state, A, now=r.tick(), db_path=r.path)
    # мина, возврат при нуле открытых, зачистка поля
    start(A, "s3", 1000, 3, (0, 1, 2))
    reveal(A, "m1", 4)
    reveal(A, "m2", 1, "наступил на мину")
    start(A, "s4", 1000, 3, (0, 1, 2))
    r.op("cashout при нуле (возврат)", db.mines_cashout, A, rid(r, "c4"), now=r.tick(), db_path=r.path)
    start(B, "s5", 1000, 24, tuple(range(1, 25)))
    reveal(B, "z1", 0, "зачистка поля (24 мины)")
    start(B, "s6", 1000, 1, (24,))
    for cell in range(24):
        reveal(B, "w%d" % cell, cell, "поле 1 мина, клетка %d" % cell)
    start(B, "mx", mines.MINES_MAX_BET, 24, tuple(range(1, 25)))
    reveal(B, "mx1", 0, "максимальная ставка, зачистка")

    r = Run("mines_expire")
    for uid in (A, B, C):
        r.player(uid)
    out.append(r)
    t0 = r.tick()
    r.op("A старт", db.mines_start, A, rid(r, "a1"), 500, 3, now=t0, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    r.op("B старт", db.mines_start, B, rid(r, "b1"), 700, 3, now=t0, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    r.op("B открыл", db.mines_reveal, B, rid(r, "b2"), 10, now=t0 + 1, db_path=r.path)
    r.op("C старт", db.mines_start, C, rid(r, "c1"), 900, 3, now=t0 + 2, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    r.op("settle рано", db.settle_expired_mines, A, now=t0 + mines.MINES_IDLE_SECONDS - 5, db_path=r.path)
    r.op("close рано", db.close_expired_mines, now=t0 + mines.MINES_IDLE_SECONDS - 5, db_path=r.path)
    r.op("settle A (возврат)", db.settle_expired_mines, A, now=t0 + mines.MINES_IDLE_SECONDS, db_path=r.path)
    r.op("settle A повтор", db.settle_expired_mines, A, now=t0 + mines.MINES_IDLE_SECONDS + 1, db_path=r.path)
    r.op("close партия", db.close_expired_mines, now=t0 + mines.MINES_IDLE_SECONDS + 2, db_path=r.path, batch=1)
    r.op("close остальные", db.close_expired_mines, now=t0 + mines.MINES_IDLE_SECONDS + 5, db_path=r.path)
    r.op("state после", db.mines_state, C, now=t0 + mines.MINES_IDLE_SECONDS + 6, db_path=r.path)
    r.op("старт после", db.mines_start, A, rid(r, "a2"), 100, 3, now=t0 + 2 * mines.MINES_IDLE_SECONDS, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    r.op("старт при просроченной", db.mines_start, A, rid(r, "a3"), 100, 3, now=t0 + 4 * mines.MINES_IDLE_SECONDS, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))

    r = Run("mines_parallel")
    r.player(A)
    r.player(B)
    out.append(r)
    t = r.tick()
    r.op("старт", db.mines_start, A, rid(r, "a1"), 1000, 3, now=t, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)]))
    r.parallel("20 reveal одной клетки", lambda i: db.mines_reveal(A, rid(r, "pr%d" % i), 10, now=t + 1, db_path=r.path), [(i,) for i in range(20)])
    r.parallel("20 cashout", lambda i: db.mines_cashout(A, rid(r, "pc%d" % i), now=t + 2, db_path=r.path), [(i,) for i in range(20)])
    r.parallel("20 стартов", lambda i: db.mines_start(B, rid(r, "ps%d" % i), 100, 3, now=t + 3, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)])), [(i,) for i in range(20)])
    r.parallel("20 одинаковых", lambda i: db.mines_start(A, rid(r, "same"), 100, 3, now=t + 4, db_path=r.path, rng=Scripted(samples=[(0, 1, 2)])), [(i,) for i in range(20)])
    return out


def all_scenarios():
    runs = hilo_scenarios() + blackjack_scenarios() + mines_scenarios()
    return {r.name: r.ops for r in runs}


def main():
    result = all_scenarios()
    if "--record" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=None, separators=(",", ":"))
        size = os.path.getsize(GOLDEN)
        print("Эталон записан: %d сценариев, %d операций, %d байт" % (len(result), sum(len(v) for v in result.values()), size))
        return
    with open(GOLDEN, encoding="utf-8") as f:
        golden = json.load(f)
    if "--update-ops" in sys.argv:
        # Точечное обновление перечисленных операций целиком: python test_game_golden.py --update-ops "crash_main#24,crash_main#25". Все остальные операции
        # обязаны совпасть с эталоном, иначе обновление отменяется (эталон не тронут).
        wanted = sys.argv[sys.argv.index("--update-ops") + 1].split(",")
        want_set = {(w.split("#")[0], int(w.split("#")[1])) for w in wanted}
        assert set(golden) == set(result), "наборы сценариев разошлись"
        stray = []
        for name in sorted(result):
            assert len(golden[name]) == len(result[name]), "%s: число операций изменилось" % name
            for i, (got, want) in enumerate(zip(result[name], golden[name])):
                if got != want and (name, i) not in want_set:
                    stray.append("%s #%d «%s»" % (name, i, want["op"]))
        assert not stray, "разошлись не перечисленные операции (обновление отменено, эталон не тронут): %s" % stray
        for name, i in sorted(want_set):
            print("обновлена %s #%d «%s»: %s" % (name, i, golden[name][i]["op"], [k for k in sorted(result[name][i]) if result[name][i][k] != golden[name][i].get(k)]))
            golden[name][i] = result[name][i]
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, sort_keys=True, indent=None, separators=(",", ":"))
        return
    if "--update-sql" in sys.argv:
        assert set(golden) == set(result), "наборы сценариев разошлись"
        changed = {}
        for name in sorted(result):
            assert len(golden[name]) == len(result[name]), "%s: число операций изменилось" % name
            for i, (got, want) in enumerate(zip(result[name], golden[name])):
                other = [k for k in sorted(set(got) | set(want)) if k not in ("sql", "seq") and got.get(k) != want.get(k)]
                assert not other, "%s #%d «%s»: разошлось не только SQL: %s (обновление отменено, эталон не тронут)" % (name, i, want["op"], other)
                if got != want:
                    want["sql"], want["seq"] = got["sql"], got["seq"]
                    changed[name] = changed.get(name, 0) + 1
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, sort_keys=True, indent=None, separators=(",", ":"))
        print("Журнал SQL обновлён у %d операций (ответы и состояние таблиц не менялись): %s" % (sum(changed.values()), dict(sorted(changed.items()))))
        return
    assert set(golden) == set(result), "наборы сценариев разошлись: %s" % (set(golden) ^ set(result))
    bad = []
    for name in sorted(result):
        assert len(golden[name]) == len(result[name]), "%s: число операций %d, ожидали %d" % (name, len(result[name]), len(golden[name]))
        for i, (got, want) in enumerate(zip(result[name], golden[name])):
            if got != want:
                diff = [k for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)]
                bad.append("%s #%d «%s»: расходятся %s (получили %s, ожидали %s)" % (
                    name, i, want["op"], diff, {k: got.get(k) for k in diff}, {k: want.get(k) for k in diff}))
    assert not bad, "эталон разошёлся (%d):\n%s" % (len(bad), "\n".join(bad[:25]))
    print("Операций сверено: %d" % sum(len(v) for v in result.values()))
    print("Все проверки прошли")


try:
    main()
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
