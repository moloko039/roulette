"""Покупка фишек за кристаллы (план экономики, этап E6): пакеты в часах фермы, одна транзакция, идемпотентность, суточный лимит, потолок баланса, параллельные запросы,
API, /mydata, /deletemydata, очистка, охранник кошелька. Опыт, total_staked и уровень не меняются."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi.testclient import TestClient

import balance_guard
import cosmetics
import db
import economy_config
import wallet
from api import create_app
from roulette import MAX_SAFE_INT, BalanceLimit, RequestConflict
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
A, B = 424242422, 424242423

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID")
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


tmp = tempfile.mkdtemp()
counter = [0]


def new_db(balance=10_000, rate=100, gems=0, xp=3000, total=7777):
    counter[0] += 1
    path = os.path.join(tmp, "c%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (A, balance, rate, NOW + 10 * 86400, NOW - 86400, total, xp))
    if gems:
        db.owner_grant_gems(A, gems, "seed", now=NOW, db_path=path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def me(path):
    return sql(path, "SELECT balance, xp, total_staked, income_level, storage_level, rate FROM players WHERE telegram_id = ?", (A,))[0]


def ledger_ok(path):
    rows = dict(sql(path, "SELECT telegram_id, SUM(delta) FROM gems_ledger GROUP BY telegram_id"))
    bal = dict(sql(path, "SELECT telegram_id, gems FROM gem_balances"))
    return all(bal.get(u, 0) == s for u, s in rows.items())


try:
    # ================= конфигурация и расчёт =================
    check("пакеты владельца: 30/6, 100/24, 250/72", economy_config.CHIP_PACKS, {"chips_6h": (30, 6), "chips_24h": (100, 24), "chips_72h": (250, 72)})
    check("фишек в пакете: часы * доход игрока", [db.chips_in_pack(6, 100), db.chips_in_pack(24, 448), db.chips_in_pack(72, 40427)], [600, 10752, 2_910_744])
    check("новичок с доходом ниже нижней оценки: считается по нижней оценке", db.chips_in_pack(24, 10), 2400)
    check("потолок пакета", db.chips_in_pack(72, 10 ** 9), economy_config.CHIP_PACK_MAX_CHIPS)
    check("причина chip_purchase есть и это сток", economy_config.GEM_REASONS["chip_purchase"], "sink")

    # ================= покупка =================
    path = new_db(balance=10_000, rate=448, gems=300)
    before = me(path)
    r = db.buy_chip_pack(A, "chip-req-000001", "chips_24h", now=NOW, db_path=path)
    check("ответ: фишки, кристаллы, баланс", (r["chips"], r["gems_spent"], r["balance"], r["gems"], r["daily_left"], r["replayed"]), (10_752, 100, 20_752, 200, 4, False))
    after = me(path)
    check("фишки зачислены, а опыт, ставки, уровни и доход прежние", (after[0], after[1:]), (20_752, before[1:]))
    check("журнал кристаллов: списание с причиной и ref", sql(path, "SELECT delta, reason, ref FROM gems_ledger ORDER BY id DESC LIMIT 1"), [(-100, "chip_purchase", "chip-req-000001")])
    check("запись покупки", sql(path, "SELECT pack_code, gems, chips FROM chip_purchases"), [("chips_24h", 100, 10_752)])
    check("повтор того же request_id: тот же ответ, списания нет", (lambda x: (x["replayed"], x["chips"], x["gems"], me(path)[0]))(db.buy_chip_pack(A, "chip-req-000001", "chips_24h", now=NOW + 1, db_path=path)), (True, 10_752, 200, 20_752))
    raises(RequestConflict, db.buy_chip_pack, A, "chip-req-000001", "chips_6h", now=NOW + 2, db_path=path)
    raises(db.UnknownChipPack, db.buy_chip_pack, A, "chip-req-000002", "chips_1h", now=NOW + 2, db_path=path)
    raises(db.UnknownChipPack, db.buy_chip_pack, A, "chip-req-000003", 5, now=NOW + 2, db_path=path)
    check("отказы ничего не изменили", (me(path)[0], db.gems_state(A, path)["gems"], ledger_ok(path)), (20_752, 200, True))

    # доход подтягивается до зачисления (как при любой покупке)
    path = new_db(balance=0, rate=3600, gems=100)
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = ?", (NOW - 600, A))
    r = db.buy_chip_pack(A, "chip-req-000010", "chips_6h", now=NOW, db_path=path)
    check("баланс = накопленное за 10 минут + пакет", r["balance"], 600 + 6 * 3600)

    # ================= нехватка и потолки =================
    path = new_db(balance=0, rate=100, gems=29)
    raises(wallet.InsufficientGems, db.buy_chip_pack, A, "chip-req-000020", "chips_6h", now=NOW, db_path=path)
    check("нехватка кристаллов: ничего не списано, записи нет", (db.gems_state(A, path)["gems"], me(path)[0], sql(path, "SELECT COUNT(*) FROM chip_purchases")[0][0]), (29, 0, 0))
    path = new_db(balance=MAX_SAFE_INT - 100, rate=100, gems=100)
    raises(BalanceLimit, db.buy_chip_pack, A, "chip-req-000021", "chips_6h", now=NOW, db_path=path)
    check("потолок баланса: кристаллы не списаны", (db.gems_state(A, path)["gems"], me(path)[0]), (100, MAX_SAFE_INT - 100))
    path = new_db(balance=1, rate=100, gems=100)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("сбой зачисления")):
        raises(RuntimeError, db.buy_chip_pack, A, "chip-req-000022", "chips_6h", now=NOW, db_path=path)
    check("сбой после списания кристаллов: всё откатилось", (db.gems_state(A, path)["gems"], me(path)[0], sql(path, "SELECT COUNT(*) FROM chip_purchases")[0][0], ledger_ok(path)), (100, 1, 0, True))

    # ================= суточный лимит =================
    path = new_db(balance=0, rate=100, gems=1000)
    for i in range(economy_config.CHIP_PACK_DAILY_LIMIT):
        r = db.buy_chip_pack(A, "chip-lim-%06d" % i, "chips_6h", now=NOW + i, db_path=path)
    check("после лимита осталось 0", r["daily_left"], 0)
    raises(db.DailyLimit, db.buy_chip_pack, A, "chip-lim-000099", "chips_6h", now=NOW + 10, db_path=path)
    check("лимит: ничего не списано сверх", (db.gems_state(A, path)["gems"], me(path)[0]), (1000 - 5 * 30, 5 * 600))
    check("повтор прежнего request_id при исчерпанном лимите всё равно отвечает", db.buy_chip_pack(A, "chip-lim-000001", "chips_6h", now=NOW + 11, db_path=path)["replayed"], True)
    check("через 24 часа лимит снова доступен", db.buy_chip_pack(A, "chip-lim-000098", "chips_6h", now=NOW + 86400 + 10, db_path=path)["daily_left"], 4)

    # ================= параллельные запросы =================
    path = new_db(balance=0, rate=100, gems=100)
    gate = threading.Barrier(20)

    def same(i):
        gate.wait()
        return db.buy_chip_pack(A, "chip-race-same", "chips_6h", now=NOW, db_path=path)
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(same, range(20)))
    check("20 одинаковых request_id: одно действие и одно списание", (sum(1 for x in res if not x["replayed"]), db.gems_state(A, path)["gems"], me(path)[0]), (1, 70, 600))
    path = new_db(balance=0, rate=100, gems=100)
    gate2 = threading.Barrier(20)

    def diff(i):
        gate2.wait()
        try:
            return db.buy_chip_pack(A, "chip-race-%05d" % i, "chips_6h", now=NOW, db_path=path)["gems"]
        except (db.ChipsError, wallet.InsufficientGems) as exc:
            return getattr(exc, "code", "insufficient_gems")
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(diff, range(20)))
    ok = sum(1 for x in res if isinstance(x, int))
    check("20 разных запросов при 100 кристаллах и лимите 5: купили не больше трёх (30 из 100), баланс не отрицательный", (ok, db.gems_state(A, path)["gems"] >= 0, ledger_ok(path), me(path)[0] == ok * 600), (3, True, True, True))

    # ================= охранник =================
    check("статически чисто: кристаллы и фишки пишет только wallet", balance_guard.violations(), [])

    # ================= API =================
    path = new_db(balance=5000, rate=448, gems=130)
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = ?", (int(time.time()), A))      # в API время настоящее: накопленного дохода ещё нет
    client = TestClient(create_app(TOKEN, [], db_path=path))

    def auth(uid=A):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}
    check("пакеты без подписи: 401", client.get("/api/chips/packs").status_code, 401)
    r = client.get("/api/chips/packs", headers=auth())
    check("пакеты: фишки по доходу игрока, кристаллы, баланс, остаток на сегодня", (r.status_code, r.json()), (200, {"packs": [
        {"code": "chips_6h", "gems": 30, "hours": 6, "chips": 2688}, {"code": "chips_24h", "gems": 100, "hours": 24, "chips": 10752},
        {"code": "chips_72h", "gems": 250, "hours": 72, "chips": 32256}], "gems": 130, "balance": 5000, "daily_left": 5}))
    body = {"request_id": "chip-api-000001", "pack_code": "chips_24h"}
    r = client.post("/api/chips/buy", headers=auth(), json=body)
    check("покупка через API", (r.status_code, r.json()), (200, {"pack_code": "chips_24h", "gems_spent": 100, "chips": 10752, "balance": 15752, "gems": 30, "daily_left": 4, "replayed": False}))
    check("повтор", client.post("/api/chips/buy", headers=auth(), json=body).json()["replayed"], True)
    check("нехватка кристаллов", (lambda x: (x.status_code, x.json()))(client.post("/api/chips/buy", headers=auth(), json={"request_id": "chip-api-000002", "pack_code": "chips_72h"})), (409, {"detail": "insufficient_gems"}))
    check("неизвестный пакет", (lambda x: (x.status_code, x.json()))(client.post("/api/chips/buy", headers=auth(), json={"request_id": "chip-api-000003", "pack_code": "nope"})), (404, {"detail": "unknown_pack"}))
    check("конфликт request_id", (lambda x: (x.status_code, x.json()))(client.post("/api/chips/buy", headers=auth(), json=dict(body, pack_code="chips_6h"))), (409, {"detail": "request_conflict"}))
    for bad in ({}, {"request_id": "chip-api-000004"}, dict(body, extra=1), {"request_id": "x", "pack_code": "chips_6h"}, {"request_id": "chip-api-000005", "pack_code": 5}):
        check("400 на %s" % json.dumps(bad)[:40], client.post("/api/chips/buy", headers=auth(), json=bad).status_code, 400)
    check("без подписи: 401", client.post("/api/chips/buy", json=body).status_code, 401)
    check("игрок без профиля: пакеты и покупка ведут себя ровно", client.get("/api/chips/packs", headers=auth(B)).json()["gems"], 0)
    check("покупка без кристаллов у нового игрока: нехватка", client.post("/api/chips/buy", headers=auth(B), json={"request_id": "chip-api-000006", "pack_code": "chips_6h"}).json(), {"detail": "insufficient_gems"})

    # ================= /mydata, /deletemydata, очистка =================
    export = db.get_player_export(A, db_path=path)
    check("выгрузка: покупки фишек без идентификаторов", (export["gems"]["chip_purchases"], "chip-api" in json.dumps(export)), ([{"pack": "chips_24h", "gems": 100, "chips": 10752, "time": export["gems"]["chip_purchases"][0]["time"]}], False))
    db.delete_player_data(A, db_path=path)
    check("удаление: записи о покупках фишек удалены", sql(path, "SELECT COUNT(*) FROM chip_purchases")[0][0], 0)
    path = new_db(balance=0, rate=100, gems=100)
    db.buy_chip_pack(A, "chip-old-000001", "chips_6h", now=NOW, db_path=path)
    db.purge_old_data(now=NOW + (cosmetics.PURCHASE_RETENTION_DAYS + 1) * 86400, db_path=path)
    check("очистка: покупка старше срока хранения удалена", sql(path, "SELECT COUNT(*) FROM chip_purchases")[0][0], 0)

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
