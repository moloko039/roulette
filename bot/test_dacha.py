"""Тест серверной части коллекции "Дачный сезон" (за улучшения фермы)."""
import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cosmetic_sets
import cosmetics
import db
import economy_config
from fastapi.testclient import TestClient

from api import create_app
from features import gifts_db
from features.cosmetics_db import grant_dacha_parts
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
A, B = 424242422, 424242423
NOW = int(time.time())
DACHA_PARTS = ("back_rug", "chip_cork", "table_oilcloth", "mine_beetle", "keno_lotto", "crash_barrel")

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"

def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)

tmp = tempfile.mkdtemp()
counter = [0]

def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()

def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "dacha%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    return path

def add_player(path, uid, balance=2000000000, xp=1000000000, income_level=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, xp, income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, ?, 0)",
        (uid, balance, NOW - 86400, NOW - 86400, xp, income_level))
    sql(path, "INSERT INTO chat_members VALUES ('room', ?, 'Player', 1, 2)", (uid,))

def buy_income(client, uid, n):
    """Настоящая покупка улучшения дохода фермы через API (выдачу частей делает маршрут после покупки)."""
    headers = {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}
    r = client.post("/api/farm/buy", headers=headers, json={"request_id": "dacha-req-%06d-%d" % (n, uid % 1000), "kind": "income"})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


def owned_codes(path, uid):
    return [r[0] for r in sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' AND item_code NOT LIKE '%_patina' ORDER BY acquired_at, item_code", (uid,))]


try:
    path = new_db()
    add_player(path, A)
    add_player(path, B, income_level=9)
    client = TestClient(create_app(TOKEN, [], db_path=path))

    # 1. Границы порогов и выдача при реальной покупке
    # Уровень 1 - нет частей
    buy_income(client, A, 1)
    owned = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' ORDER BY acquired_at", (A,))
    check("Уровень 1: нет частей", [r[0] for r in owned], [])

    # Уровень 2 - back_rug
    buy_income(client, A, 2)
    owned = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' ORDER BY acquired_at", (A,))
    check("Уровень 2: back_rug", [r[0] for r in owned], ["back_rug"])

    # Уровень 3 - ничего нового
    buy_income(client, A, 3)
    owned = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' ORDER BY acquired_at", (A,))
    check("Уровень 3: без изменений", [r[0] for r in owned], ["back_rug"])

    # Уровень 4 - chip_cork
    buy_income(client, A, 4)
    owned = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' ORDER BY acquired_at", (A,))
    check("Уровень 4: +chip_cork", [r[0] for r in owned], ["back_rug", "chip_cork"])

    # Доходим до 16
    for i in range(5, 17):
        buy_income(client, A, i)
    owned = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina' ORDER BY acquired_at", (A,))
    check("Уровень 16: все 6", set(r[0] for r in owned), set(DACHA_PARTS))

    # 2. Идемпотентность повторных и параллельных вызовов
    sql(path, "UPDATE players SET income_level = 16 WHERE telegram_id = ?", (B,))     # игроку B положены все шесть частей
    gate = threading.Barrier(12)

    def worker(i):
        gate.wait()
        return grant_dacha_parts(B, now=NOW, db_path=path)     # исключения не глотаем: упадёт тест

    with ThreadPoolExecutor(12) as pool:
        granted = list(pool.map(worker, range(12)))
    check("параллельная выдача: все шесть частей выданы ровно один раз (остальные потоки ничего не добавили)", sum(granted), 6)

    owned_b = sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina'", (B,))
    check("Параллельная выдача: без дублей, все 6 частей", len(owned_b), 6)
    check("Параллельная выдача: все правильные", set(r[0] for r in owned_b), set(DACHA_PARTS))

    # 3. Ленивая выдача через cosmetics_mine
    path2 = new_db()
    add_player(path2, A, income_level=9)
    add_player(path2, B)
    # 4 части: level 2, 4, 6, 9
    mine = db.cosmetics_mine(A, db_path=path2)
    owned_codes = [c["code"] for c in mine["owned"] if not c["code"].endswith("_patina")]
    check("Ленивая выдача (9 уровень): 4 части", set(owned_codes), {"back_rug", "chip_cork", "table_oilcloth", "mine_beetle"})
    
    # Ленивая выдача: вторая проверка (ничего не добавится)
    db.cosmetics_mine(A, db_path=path2)
    owned_after = sql(path2, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code NOT LIKE '%_patina'", (A,))
    check("Ленивая выдача 2: без изменений", len(owned_after), 4)

    # 4. Части не покупаются и не дарятся
    db.owner_grant_gems(A, 1000, "seed", now=1, db_path=path2)
    raises(cosmetics.ItemUnavailable, db.buy_item, A, "buy-1", "back_rug", now=1, db_path=path2)
    raises(cosmetics.ItemUnavailable, db.buy_with_gems, A, "buy-2", "chip_cork", now=1, db_path=path2)
    raises(cosmetics.ItemUnavailable, db.buy_with_chips, A, "buy-3", "table_oilcloth", now=1, db_path=path2)
    ref = gifts_db.member_ref("room", B)
    raises(cosmetics.ItemUnavailable, db.send_gift, A, "room", "gift-1", ref, "mine_beetle", "A", now=1, db_path=path2)

    # 5. Прогресс коллекции (season null)
    dacha_prog = [c for c in mine["collections"] if c["code"] == "dacha"][0]
    check("Прогресс коллекции (9 уровень)", (dacha_prog["owned"], dacha_prog["total"], dacha_prog["complete"], dacha_prog["season"]), (4, 6, False, None))
    
    # 6. В рейтинге беседы: полная коллекция
    sql(path2, "UPDATE players SET income_level = 16 WHERE telegram_id = ?", (A,))
    db.cosmetics_mine(A, db_path=path2)  # ленивая выдача остальных
    top = db.chat_top("room", A, "PlayerA", db_path=path2)
    my_top = [e for e in top["top"] if e["is_me"]][0]
    check("Рейтинг: полная коллекция", my_top["complete_sets"], ["dacha"])
    check("Рейтинг: set_names содержит dacha", "dacha" in top["set_names"], True)

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
