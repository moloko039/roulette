"""Поминутное ленивое начисление: чистая функция, база, гонки, миграция, API."""
import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from unittest import mock

from fastapi.testclient import TestClient

import db
import economy
import farm
from api import create_app
from roulette import MAX_SAFE_INT
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
T0 = 1_760_000_040          # граница минуты (кратна 60)
assert T0 % 60 == 0

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


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


def add_player(path, uid, balance=0, rate=100, last=T0, acc=0, storage=0, income=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, accrual_acc, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)", (uid, balance, rate, last, acc, T0, income, storage))


def row(path, uid=1):
    return sql(path, "SELECT balance, last_accrual, accrual_acc FROM players WHERE telegram_id = ?", (uid,))[0]


def steps(rate, minutes, acc=0, max_hours=30):
    """Начисления по минутам: список кредитов и конечный остаток (по одной минуте за вызов)."""
    credits, last = [], T0
    for k in range(1, minutes + 1):
        c, last, acc = economy.accrue_minutes(last, acc, T0 + 60 * k, rate, max_hours)
        credits.append(c)
    return credits, acc


try:
    # ================= округление: R=1500, 100, 61 (таблица примеров) =================
    credits, acc = steps(1500, 60)
    check("R=1500: ровно 25 каждую минуту", (set(credits), sum(credits), acc), ({25}, 1500, 0))
    credits, acc = steps(100, 12)
    check("R=100: 2, 2, 1 по кругу (5 фишек за 3 минуты)", credits, [2, 2, 1] * 4)
    credits, acc = steps(100, 60)
    check("R=100: за час ровно 100", (sum(credits), acc), (100, 0))
    credits, acc = steps(61, 60)
    check("R=61: за час ровно 61", (sum(credits), acc), (61, 0))
    check("R=61: первые минуты 2, 1, 1", steps(61, 5)[0], [2, 1, 1, 1, 1])

    # ================= инварианты для набора R =================
    for rate in (0, 1, 7, 59, 60, 61, 100, 1001, 1500, 12345, 10 ** 9 + 7, 10 ** 15 + 3):
        credits, acc = steps(rate, 1440, max_hours=10 ** 6)
        for hour in range(24):
            check("R=%d: час %d ровно R" % (rate, hour), sum(credits[hour * 60:(hour + 1) * 60]), rate)
        check("R=%d: за 1440 минут ровно 24R" % rate, sum(credits), 24 * rate)
        total = 0
        for k, c in enumerate(credits, 1):
            total += c
            deviation = total - Fraction(k * rate, 60)
            assert 0 <= deviation < 1, "R=%d, префикс %d: отклонение %s" % (rate, k, deviation)
            assert -59 <= 0
        # пакетно за один вызов = пошагово
        batch, last, acc_b = economy.accrue_minutes(T0, 0, T0 + 1440 * 60, rate, 10 ** 6)
        check("R=%d: пакетно = пошагово" % rate, (batch, acc_b), (sum(credits), acc))
    # случайные сдвиги: пакет из n минут равен n шагам из любого допустимого остатка
    rnd = random.Random(7)
    for _ in range(2000):
        rate, n, acc0 = rnd.randrange(0, 200_000), rnd.randrange(1, 3000), -rnd.randrange(0, 60)
        c1, l1, a1 = economy.accrue_minutes(T0, acc0, T0 + 60 * n, rate, 10 ** 6)
        c2, a2, last = 0, acc0, T0
        for k in range(1, n + 1):
            c, last, a2 = economy.accrue_minutes(last, a2, T0 + 60 * k, rate, 10 ** 6)
            c2 += c
        assert (c1, a1) == (c2, a2), (rate, n, acc0)
        assert -59 <= a1 <= 0

    # ================= смена R посреди часа: потерь и лишнего нет =================
    for seed in range(50):
        rnd = random.Random(seed)
        acc, last, paid, exact, now = 0, T0, 0, Fraction(0), T0
        for _ in range(40):
            rate = rnd.choice([100, 135, 182, 1500, 61, 7, 12345])
            minutes = rnd.randrange(1, 130)
            now += 60 * minutes
            c, last, acc = economy.accrue_minutes(last, acc, now, rate, 10 ** 6)
            paid += c
            exact += Fraction(minutes * rate, 60)
            assert 0 <= paid - exact < 1, (seed, float(paid - exact))
            assert -59 <= acc <= 0

    # ================= меньше минуты ничего, ровно минута один тик =================
    check("меньше минуты: ничего", economy.accrue_minutes(T0, 0, T0 + 59, 100, 30), (0, T0, 0))
    check("ровно минута: один тик", economy.accrue_minutes(T0, 0, T0 + 60, 1500, 30), (25, T0 + 60, 0))
    check("внутри минуты метка не двигается", economy.accrue_minutes(T0 + 60, 0, T0 + 119, 1500, 30), (0, T0 + 60, 0))
    check("до следующего тика", [economy.next_tick_in(T0 + s) for s in (0, 1, 59)], [60, 59, 1])
    check("показ «в минуту»", [economy.per_minute_estimate(r) for r in (100, 1500, 61, 1, 0, 40000)], ["1.7", "25.0", "1.0", "0.0", "0.0", "666.7"])

    # ================= в базе: быстрый путь без записи, один тик, потолок =================
    path = new_db()
    add_player(path, 1, balance=1000, rate=1500, last=T0)
    observer = sqlite3.connect(path)
    version = lambda: observer.execute("PRAGMA data_version").fetchone()[0]
    v0 = version()
    for sec in (0, 1, 30, 59):
        p = db.get_player(1, now=T0 + sec, db_path=path)
        check("внутри минуты: ничего не начислено", (p["accrued"], p["balance"]), (0, 1000))
    check("внутри минуты в базу ничего не пишется (data_version)", version(), v0)
    p = db.get_player(1, now=T0 + 60, db_path=path)
    check("ровно минута: один тик 25", (p["accrued"], p["balance"], row(path)[1]), (25, 1025, T0 + 60))
    assert version() != v0
    v1 = version()
    db.get_player(1, now=T0 + 90, db_path=path)
    check("повторный запрос в ту же минуту: без записи", version(), v1)
    # потолок офлайн-накопления (30 часов), лишнее сгорает, метка переносится на текущую минуту
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0)
    far = T0 + 50 * 3600 + 17
    p = db.get_player(1, now=far, db_path=path)
    check("50 часов отсутствия: платят 30 часов", (p["balance"], row(path)[1], row(path)[2]), (3000, far // 60 * 60, 0))
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0, storage=3)
    check("хранилище 3: потолок 48 часов", db.get_player(1, now=T0 + 100 * 3600, db_path=path)["balance"], 4800)
    path = new_db()
    add_player(path, 1, balance=0, rate=182, last=T0, storage=8, income=2)
    check("хранилище 8 и ставка 182: 78 часов", db.get_player(1, now=T0 + 200 * 3600, db_path=path)["balance"], 78 * 182)
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0)
    check("ровно потолок (30 часов) без сгорания", db.get_player(1, now=T0 + 30 * 3600, db_path=path)["balance"], 3000)
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0)
    check("потолок + 1 минута отсутствия: всё равно 30 часов", db.get_player(1, now=T0 + 30 * 3600 + 60, db_path=path)["balance"], 3000)

    # ================= 20 параллельных запросов: ровно одно начисление =================
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0)
    barrier = threading.Barrier(20)

    def hit(_):
        barrier.wait()
        return db.get_player(1, now=T0 + 3 * 3600, db_path=path)["accrued"]
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(hit, range(20)))
    check("20 параллельных: сумма начисленного как для одного подтягивания", (sum(results), sorted(results)[-1], row(path)[0]), (300, 300, 300))

    # ================= часы назад, будущее, мусор =================
    path = new_db()
    add_player(path, 1, balance=500, rate=100, last=T0 + 3600)    # метка на час в будущем
    v0 = sqlite3.connect(path).execute("PRAGMA data_version").fetchone()[0]
    p = db.get_player(1, now=T0, db_path=path)
    check("метка в будущем: ничего не начислено, метка цела", (p["balance"], row(path)[1]), (500, T0 + 3600))
    p = db.get_player(1, now=T0 + 3600 + 60, db_path=path)
    check("когда время догнало: только новая минута, без двойного начисления", (p["balance"], row(path)[1]), (502, T0 + 3600 + 60))
    path = new_db()
    add_player(path, 1, balance=500, rate=100, last=T0 + 400 * 86400)   # метка дальше года: испорчена
    p = db.get_player(1, now=T0, db_path=path)
    check("испорченная метка исправлена без начисления", (p["balance"], row(path)[1]), (500, T0))
    check("после исправления начисление идёт", db.get_player(1, now=T0 + 600, db_path=path)["balance"], 500 + 17)
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0, acc=5000)    # положительный остаток: бесплатных фишек быть не должно
    check("положительный остаток приведён к нулю: за час ровно 100", db.get_player(1, now=T0 + 3600, db_path=path)["balance"], 100)
    path = new_db()
    add_player(path, 1, balance=0, rate=100, last=T0, acc=-5000)    # слишком отрицательный: приводится к -59
    p = db.get_player(1, now=T0 + 3600, db_path=path)
    assert 99 <= p["balance"] <= 100, p
    path = new_db()
    add_player(path, 1, balance=10, rate=-5, last=T0)               # отрицательная ставка не списывает
    check("отрицательная ставка: ничего", db.get_player(1, now=T0 + 3600, db_path=path)["balance"], 10)

    # ================= упор в MAX_SAFE_INT =================
    path = new_db()
    add_player(path, 1, balance=MAX_SAFE_INT - 10, rate=10 ** 12, last=T0)
    p = db.get_player(1, now=T0 + 3600, db_path=path)
    check("баланс не выше потолка", p["balance"], MAX_SAFE_INT)
    check("остаток не растёт", -59 <= row(path)[2] <= 0, True)
    p = db.get_player(1, now=T0 + 7200, db_path=path)
    check("у потолка начислять нечего", (p["balance"], p["accrued"]), (MAX_SAFE_INT, 0))
    check("остаток по-прежнему в [-59, 0], метка идёт вперёд", (-59 <= row(path)[2] <= 0, row(path)[1]), (True, T0 + 7200))

    # ================= покупка улучшения: сначала по старой ставке, потом по новой =================
    path = new_db()
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, accrual_acc, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (1, 5000, 100, ?, 0, ?, 1600, 1600, 0, 0)", (T0, T0))
    r = db.buy_upgrade(1, "buy-req-0001", "income", T0 + 3600, path)      # час по старой ставке: +100, цена 1000
    check("покупка: подтянуто по старой ставке, потом списание", r["balance"], 5000 + 100 - 1000)
    p = db.get_player(1, now=T0 + 7200, db_path=path)
    check("следующий час по новой ставке (135)", (p["balance"], p["rate"]), (4100 + 135, 135))

    # ================= игрок без фермы, удалённые (tombstone) =================
    path = new_db()
    p = db.get_player(77, now=T0 + 5, db_path=path)
    check("новый игрок: метка на границе минуты, остаток 0", (p["balance"], row(path, 77)[1:], p["accrued"]), (1000, (T0, 0), 0))
    p = db.get_player(77, now=T0 + 600, db_path=path)
    check("новый игрок тоже получает доход поминутно", (p["balance"], p["accrued"]), (1017, 17))
    db.delete_player_data(77, db_path=path, now=T0 + 600)
    check("после удаления строки нет", sql(path, "SELECT COUNT(*) FROM players WHERE telegram_id = 77")[0][0], 0)
    p = db.get_player(77, now=T0 + 660, db_path=path)
    check("повторная регистрация в период защиты: баланс 0, доход идёт обычно", (p["balance"], p["accrued"]), (0, 0))
    p = db.get_player(77, now=T0 + 660 + 3600, db_path=path)
    check("и через час ровно 100", p["balance"], 100)

    # ================= рейтинг беседы: виртуальное начисление не пишет в базу и совпадает =================
    path = new_db()
    add_player(path, 1, balance=1000, rate=100, last=T0)
    add_player(path, 2, balance=1000, rate=1500, last=T0)
    for uid in (1, 2):
        sql(path, "INSERT INTO chat_members VALUES ('room', ?, ?, ?, ?)", (uid, "P%d" % uid, T0, T0))
    before = sql(path, "SELECT telegram_id, balance, last_accrual, accrual_acc FROM players ORDER BY 1")
    top = db.chat_top("room", 1, "P1", now=T0 + 3600, db_path=path)["top"]
    check("рейтинг с учётом тиков", {e["name"]: e["balance"] for e in top}, {"P1": 1100, "P2": 2500})
    check("рейтинг ничего не записал", sql(path, "SELECT telegram_id, balance, last_accrual, accrual_acc FROM players ORDER BY 1"), before)

    # ================= миграция существующих игроков =================
    NOW = T0 + 1000 * 3600 + 25 * 60 + 13       # «момент миграции», не на границе минуты

    def old_db(players):
        counter[0] += 1
        p = os.path.join(tmp, "old%d.db" % counter[0])
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, rate INTEGER NOT NULL, "
                     "last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL, total_staked INTEGER NOT NULL DEFAULT 0, "
                     "xp INTEGER NOT NULL DEFAULT 0, income_level INTEGER NOT NULL DEFAULT 0, storage_level INTEGER NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE service_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, storage_level) VALUES (?, ?, ?, ?, 0, ?)", players)
        conn.commit()
        conn.close()
        return p

    # (id, баланс, rate, last_accrual, уровень хранилища)
    players = [
        (1, 1000, 100, NOW,                            0),   # только что начислено: ничего накопленного
        (2, 1000, 100, NOW - 3 * 3600 - 20 * 60 - 7,   0),   # 3 ч 20 мин: накоплено < потолка, неполный час
        (3, 1000, 100, NOW - 50 * 3600,                0),   # накоплено сверх потолка (30 ч)
        (4, 1000, 182, NOW - 60 * 3600,                3),   # хранилище 3: потолок 48 ч, ставка 182
        (5, 7, 135, NOW - 29 * 3600 - 59 * 60,          0),   # почти потолок
        (6, MAX_SAFE_INT - 5, 100, NOW - 10 * 3600,    0),   # упор в потолок баланса
        (7, 1000, 100, NOW + 3 * 3600,                 0),   # метка в будущем
    ]
    mpath = old_db(players)
    copy = mpath + ".copy"
    shutil.copyfile(mpath, copy)                          # проверка на копии базы, оригинал не трогаем до конца
    conn = sqlite3.connect(copy)
    conn.row_factory = sqlite3.Row
    db._migrate_minute_accrual(conn, now=NOW)
    after = {r["telegram_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
    conn.close()

    def old_logic(balance, rate, last, storage):
        """Что платила старая часовая логика к моменту миграции: целые часы с потолком; остаток минут оставался накопленным."""
        earned, new_last = economy.accrue(last, NOW, rate, max_hours=farm.storage_hours(storage))
        return balance + earned, new_last

    for uid, balance, rate, last, storage in players:
        a = after[uid]
        check("игрок %d: метка на границе минуты или будущая" % uid, a["last_accrual"] % 60 == 0 or last > NOW, True)
        assert -59 <= a["accrual_acc"] <= 0
        if uid == 7:
            check("метка в будущем: ничего не начислено, метка цела", (a["balance"], a["last_accrual"], a["accrual_acc"]), (balance, last, 0))
            continue
        if uid == 6:
            check("упор в потолок: баланс не выше MAX_SAFE_INT", a["balance"], MAX_SAFE_INT)
            continue
        old_balance, old_last = old_logic(balance, rate, last, storage)
        # доход за неполный час (после old_last) старая логика ещё не платила: он сохранён точно (в долях через acc)
        pending_minutes = NOW // 60 - old_last // 60
        exact_pending = pending_minutes * rate            # в долях фишки * 60
        paid_pending = a["balance"] - old_balance
        check("игрок %d: итог равен старой логике + неполный час без потерь" % uid, paid_pending * 60 + a["accrual_acc"], exact_pending)
        check("игрок %d: метка = граница минуты «сейчас»" % uid, a["last_accrual"], NOW // 60 * 60)
    check("игрок 1: пустой, ничего лишнего", (after[1]["balance"], after[1]["accrual_acc"]), (1000, 0))
    check("игрок 3: потолок 30 часов (3000), лишнее сгорело", after[3]["balance"], 1000 + 3000)
    check("игрок 4: хранилище 3, 48 часов по 182", after[4]["balance"], 1000 + 48 * 182)
    check("игрок 2: 3 часа 300 + неполный час поминутно", after[2]["balance"] - 1000 >= 300, True)
    # повторный запуск ничего не меняет (идемпотентность), и столбец один
    conn = sqlite3.connect(copy)
    conn.row_factory = sqlite3.Row
    db._migrate_minute_accrual(conn, now=NOW + 7200)
    again = {r["telegram_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
    check("повторная миграция не начисляет", again, after)
    check("столбец один", [r[1] for r in conn.execute("PRAGMA table_info(players)")].count("accrual_acc"), 1)
    conn.close()
    # то же через init_db на самой базе: сервис при запуске
    with mock.patch("time.time", return_value=float(NOW)):
        db.init_db(mpath)
    mig = {r[0]: r[1:] for r in sql(mpath, "SELECT telegram_id, balance, last_accrual, accrual_acc FROM players")}
    check("init_db даёт тот же результат, что миграция на копии", {u: (v[0], v[1], v[2]) for u, v in mig.items()}, {u: (a["balance"], a["last_accrual"], a["accrual_acc"]) for u, a in after.items()})
    db.init_db(mpath)
    db.init_db(mpath)
    check("повторный init_db ничего не меняет", {r[0]: r[1:] for r in sql(mpath, "SELECT telegram_id, balance, last_accrual, accrual_acc FROM players")}, mig)
    # пустая база и новая база
    empty = old_db([])
    db.init_db(empty)
    check("пустая база мигрирует", [r[1] for r in sql(empty, "PRAGMA table_info(players)")].count("accrual_acc"), 1)
    fresh = new_db()
    check("новая база сразу со столбцом", [r[1] for r in sql(fresh, "PRAGMA table_info(players)")].count("accrual_acc"), 1)
    # откат при сбое: ни столбца, ни частичных начислений
    broken = old_db(players[:2])
    conn = sqlite3.connect(broken)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    with mock.patch.object(db.economy, "accrue_minutes", side_effect=RuntimeError("сбой")):
        try:
            db._migrate_minute_accrual(conn, now=NOW)
            raise AssertionError("сбой не дошёл")
        except RuntimeError:
            pass
    check("при сбое миграция откатилась целиком", ([r[1] for r in conn.execute("PRAGMA table_info(players)")].count("accrual_acc"),
                                                   [r[0] for r in conn.execute("SELECT balance FROM players ORDER BY 1")]), (0, [1000, 1000]))
    conn.close()

    # ================= API: блок фермы, accrued_now, без записи внутри минуты =================
    path = new_db()
    real = int(time.time())
    base = real // 60 * 60 - 3600
    add_player(path, 5, balance=1000, rate=1500, last=base)
    app = create_app(TOKEN, ["https://example.invalid"], db_path=path)
    client = TestClient(app)
    auth = lambda: {"Authorization": "tma " + make_init_data(TOKEN, user_id=5, auth_date=real)}
    mid = base + 3600 + 30
    with mock.patch("time.time", return_value=float(mid)):
        me = client.get("/api/me", headers=auth()).json()
        check("ферма в /api/me: 60 тиков по 25 за этим запросом", me["farm"], {"income_per_hour": 1500, "per_minute_estimate": "25.0",
                                                                                "next_tick_in_s": 30, "hours_cap": 30, "accrued_now": 1500})
        check("старые поля контракта на месте", (me["balance"], me["rate"], me["seconds_to_next"]), (2500, 1500, 30))
        obs = sqlite3.connect(path)
        v = obs.execute("PRAGMA data_version").fetchone()[0]
        me2 = client.get("/api/me", headers=auth()).json()
        check("повтор в той же минуте: ничего не начислено", (me2["farm"]["accrued_now"], me2["balance"]), (0, 2500))
        check("GET /api/me внутри минуты не пишет в базу", obs.execute("PRAGMA data_version").fetchone()[0], v)
    with mock.patch("time.time", return_value=float(mid + 60)):
        me3 = client.get("/api/me", headers=auth()).json()
        check("следующая минута: +25", (me3["farm"]["accrued_now"], me3["balance"], me3["farm"]["next_tick_in_s"]), (25, 2525, 30))
    # ================= документы =================
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "технический остаток дробной части дохода" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert all(w in api_doc for w in ("accrued_now", "per_minute_estimate", "next_tick_in_s", "hours_cap", "income_per_hour", "accrual_acc"))
    me_examples = __import__("json").load(open(os.path.join(ROOT, "docs", "examples", "me.json"), encoding="utf-8"))
    assert all("farm" in v and set(v["farm"]) == {"income_per_hour", "per_minute_estimate", "next_tick_in_s", "hours_cap", "accrued_now"}
               for k, v in me_examples.items() if not k.startswith("_"))
finally:
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
