"""Серия входов (ECONOMY_ADDITIONS.md, п. 3): награда раз в «день» (московский), рост по дням и циклам, откат при пропуске, седьмой день с кристаллами и месячным потолком бесплатных,
идемпотентность, параллельные запросы, API, /mydata, /deletemydata, очистка. Опыт и ставки не меняются."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi.testclient import TestClient

import balance_guard
import db
import economy_config
from api import create_app
from features import streak_db
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
A, B = 424242422, 424242423
MSK = economy_config.STREAK_UTC_OFFSET_HOURS * 3600
DAY0 = 20_000 * 86400 - MSK + 3600      # час ночи по московскому времени одного дня (20000-й день с 1970-01-01 по Москве)

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "k%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 1000, 100, ?, ?, 777, 300, 0, 0)",
        (A, DAY0 + 10 * 86400, DAY0 - 86400))
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def at(day, hour=12):
    """Момент в середине московского дня номер day от DAY0 (день 0 это DAY0)."""
    return DAY0 + day * 86400 + (hour - 1) * 3600


try:
    # ---- расчёт и границы дня
    check("день по Москве: час ночи и 23:59 одного дня, полночь следующего другого", (streak_db.day_index(DAY0), streak_db.day_index(DAY0 + 23 * 3600 - 1), streak_db.day_index(DAY0 + 23 * 3600)), (20000, 20000, 20001))
    check("секунд до следующего дня в полдень", streak_db.seconds_to_next_day(at(0, 12)), 12 * 3600)
    check("награды первого цикла: фишки по нарастающей, седьмой день кристаллы", [streak_db.reward_for(d, 1) for d in range(1, 8)], [(300, 0), (400, 0), (500, 0), (600, 0), (800, 0), (1000, 0), (1000, 5)])
    check("второй цикл щедрее на 25%, кристаллов 6", (streak_db.reward_for(1, 2), streak_db.reward_for(7, 2)), ((375, 0), (1250, 6)))
    check("потолок цикла x2 и кристаллы не больше 8", (streak_db.reward_for(1, 10), streak_db.reward_for(7, 10)), ((600, 0), (2000, 8)))
    check("месяц начинается в полночь по Москве первого числа", streak_db.month_start(at(0)) <= at(0) < streak_db.month_start(at(0)) + 32 * 86400, True)

    # ---- сбор
    path = new_db()
    st = db.streak_status(A, now=at(0), db_path=path)
    check("до первого сбора: день 1, цикл 1, награда 300, не собрано", (st["claimed_today"], st["streak_day"], st["cycle"], st["reward"]), (False, 1, 1, {"chips": 300, "gems": 0}))
    check("лента недели: семь дней", [d["day"] for d in st["week"]], list(range(1, 8)))
    before = sql(path, "SELECT xp, total_staked, income_level FROM players WHERE telegram_id = ?", (A,))[0]
    r = db.claim_streak(A, now=at(0), db_path=path)
    check("первый сбор: 300 фишек, баланс, не повтор", (r["chips"], r["gems"], r["balance"], r["streak_day"], r["cycle"], r["replayed"]), (300, 0, 1300, 1, 1, False))
    check("опыт и ставки не изменились", sql(path, "SELECT xp, total_staked, income_level FROM players WHERE telegram_id = ?", (A,))[0], before)
    r2 = db.claim_streak(A, now=at(0, 20), db_path=path)
    check("повтор в тот же день: то же, второго начисления нет", (r2["replayed"], r2["chips"], sql(path, "SELECT balance FROM players")[0][0]), (True, 300, 1300))
    st = db.streak_status(A, now=at(0, 20), db_path=path)
    check("после сбора: собрано, на завтра день 2 с наградой 400", (st["claimed_today"], st["streak_day"], st["reward"]["chips"]), (True, 2, 400))
    # неделя подряд
    for d in range(1, 7):
        r = db.claim_streak(A, now=at(d), db_path=path)
    check("день 7 подряд: 1000 фишек и 5 кристаллов", (r["streak_day"], r["chips"], r["gems"], r["gems_balance"]), (7, 1000, 5, 5))
    check("кристаллы в журнале причиной streak_gems", sql(path, "SELECT delta, reason, ref FROM gems_ledger"), [(5, "streak_gems", "day-%d" % (20000 + 6))])
    r = db.claim_streak(A, now=at(7), db_path=path)
    check("после седьмого дня новый цикл: день 1, 375", (r["streak_day"], r["cycle"], r["chips"]), (1, 2, 375))
    # пропуск дня: откат на начало текущей недели
    db.claim_streak(A, now=at(8), db_path=path)                     # день 2 цикла 2
    db.claim_streak(A, now=at(9), db_path=path)                     # день 3 цикла 2
    r = db.claim_streak(A, now=at(12), db_path=path)                # пропустили три дня
    check("пропуск: серия откатилась на день 1 текущего цикла (цикл 2, 375), не обнулилась", (r["streak_day"], r["cycle"], r["chips"]), (1, 2, 375))
    # пропуск после законченной недели: следующий цикл
    path = new_db()
    for d in range(0, 7):
        db.claim_streak(A, now=at(d), db_path=path)
    r = db.claim_streak(A, now=at(10), db_path=path)
    check("пропуск после законченной недели: цикл 2, день 1", (r["streak_day"], r["cycle"]), (1, 2))

    # ---- потолок бесплатных кристаллов
    path = new_db()
    with mock.patch.object(economy_config, "FREE_GEMS_MONTHLY_CAP", 7):
        for d in range(0, 7):
            r = db.claim_streak(A, now=at(d), db_path=path)
        check("потолок 7: 5 кристаллов дошли, не урезано", (r["gems"], r["gems_capped"]), (5, False))
        for d in range(7, 14):
            r = db.claim_streak(A, now=at(d), db_path=path)
        check("второй седьмой день в тот же месяц: урезано до остатка 2", (r["gems"], r["gems_capped"], db.gems_state(A, path)["gems"]), (2, True, 7))
        check("фишки при урезании кристаллов начислены полностью", r["chips"], 1250)

    # ---- гонки
    path = new_db()
    gate = threading.Barrier(20)

    def race(i):
        gate.wait()
        return db.claim_streak(A, now=at(0), db_path=path)
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(race, range(20)))
    check("20 параллельных сборов в один день: одно начисление", (sum(1 for x in res if not x["replayed"]), sql(path, "SELECT balance FROM players")[0][0], sql(path, "SELECT COUNT(*) FROM streak_claims")[0][0]), (1, 1300, 1))
    check("статически чисто: фишки и кристаллы пишет только wallet", balance_guard.violations(), [])

    # ---- API
    path = new_db()
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = ?", (int(time.time()), A))
    client = TestClient(create_app(TOKEN, [], db_path=path))

    def auth(uid=A):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}
    check("без подписи: 401", (client.get("/api/streak").status_code, client.post("/api/streak/claim", json={}).status_code), (401, 401))
    r = client.get("/api/streak", headers=auth())
    check("карточка: ключи", (r.status_code, sorted(r.json())), (200, ["claimed_today", "cycle", "reward", "seconds_to_next_day", "streak_day", "week"]))
    r = client.post("/api/streak/claim", headers=auth(), json={})
    check("сбор через API", (r.status_code, sorted(r.json()), r.json()["chips"], r.json()["replayed"]), (200, ["balance", "chips", "cycle", "gems", "gems_balance", "gems_capped", "replayed", "streak_day"], 300, False))
    check("повтор: replayed", client.post("/api/streak/claim", headers=auth(), json={}).json()["replayed"], True)
    check("лишние поля в теле: 400", client.post("/api/streak/claim", headers=auth(), json={"x": 1}).status_code, 400)
    check("новый игрок без профиля: сбор создаёт профиль и платит", client.post("/api/streak/claim", headers=auth(B), json={}).json()["chips"], 300)

    # ---- /mydata, /deletemydata, очистка
    export = db.get_player_export(A, db_path=path)
    check("выгрузка: сборы серии", (len(export["streak"]), sorted(export["streak"][0])), (1, ["chips", "cycle", "day", "gems", "streak_day"]))
    db.delete_player_data(A, db_path=path)
    check("удаление: записи серии удалены", sql(path, "SELECT COUNT(*) FROM streak_claims WHERE telegram_id = ?", (A,))[0][0], 0)
    path = new_db()
    for d in range(0, 3):
        db.claim_streak(A, now=at(d), db_path=path)
    db.purge_old_data(now=at(0) + 400 * 86400, db_path=path)
    check("очистка: старые записи удалены, но последняя остаётся (состояние серии)", sql(path, "SELECT day FROM streak_claims"), [(20002,)])
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
