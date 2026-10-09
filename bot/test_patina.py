"""Тест патины (docs/COLLECTIONS.md, №3): счётчики, /api/me и выдача."""
import testenv  # noqa: F401
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import db
import economy_config

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = int(time.time())
A, B, C = 424242421, 424242422, 424242423

def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"

def new_db():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    return path

def sql(path, query, params=()):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()

def add_player(path, uid):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, xp, income_level, storage_level) VALUES (?, 1000, 100, 1, 1, 0, 0, 0)", (uid,))

try:
    path = new_db()
    add_player(path, A)
    add_player(path, B)
    add_player(path, C)

    # Счетчики
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at, finished_at) VALUES (?, 10, 'manual', 200, ?, 1, 'cashed', 1, 2)", (A, 5000))
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at, finished_at) VALUES (?, 10, 'manual', 200, ?, 1, 'cashed', 1, 2)", (A, 4999))
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at, finished_at) VALUES (?, 10, 'manual', 200, ?, 1, 'lost', 1, 2)", (A, 5001)) # lost but >= 5000
    sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at, finished_at) VALUES (?, 10, 'manual', 200, ?, 1, 'active', 1, NULL)", (A, 5000))

    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, payout, staked_counted, created_at, finished_at, updated_at) VALUES (?, 10, 3, 1, 0, 'lost', 0, 1, 1, 2, 2)", (A,))
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, payout, staked_counted, created_at, finished_at, updated_at) VALUES (?, 10, 3, 1, 0, 'cashed', 0, 1, 1, 2, 2)", (A,))
    sql(path, "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, payout, staked_counted, created_at, finished_at, updated_at) VALUES (?, 10, 3, 1, 0, 'lost', 0, 1, 1, NULL, 2)", (A,))

    for i in range(200):
        sql(path, f"INSERT INTO roulette_rounds (telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) VALUES (?, 'req-{i}', 0, 10, 0, '[]', 1)", (A,))
    
    # живой краш: закрытая ставка в раунде с крахом >= порога считается крахом и раундом; открытая ставка и раунд ниже порога нет
    for rid_, cx in ((1, 5000), (2, 4999), (3, 6000)):
        sql(path, "INSERT INTO crash_rounds (id, room_key, seed_hash, seed, crash_x100, bet_open_ms, flight_start_ms, crash_ms, status) VALUES (?, 'g', 'h', x'00', ?, 1, 2, 3, 'closed')", (rid_, cx))
    sql(path, "INSERT INTO crash_bets (round_id, telegram_id, bet, status, request_id, created_at_ms) VALUES (1, ?, 10, 'lost', 'r1', 1)", (A,))
    sql(path, "INSERT INTO crash_bets (round_id, telegram_id, bet, status, request_id, created_at_ms) VALUES (2, ?, 10, 'lost', 'r2', 1)", (A,))
    sql(path, "INSERT INTO crash_bets (round_id, telegram_id, bet, status, request_id, created_at_ms) VALUES (3, ?, 10, 'open', 'r3', 1)", (A,))
    # 3 crashes >= 5000 (два прежних и один живой), 1 explosion, 202 раунда (прежние закрытые 3 + 200 рулетки + 1 мина... + 2 закрытые живые ставки)
    # thresholds: chip(1,5,15,40) => 2 crashes => stage 1
    # card_back(200,1000,3000,8000) => 200 rounds => stage 1
    # mine_icons(10,40,120,300) => 1 explosion => stage 0

    import sqlite3
    conn = sqlite3.connect(path)
    from features.patina_db import patina_counters, patina_stages
    c = patina_counters(conn, A)
    check("счётчики", c, {"big_crashes": 3, "explosions": 1, "rounds": 207})

    s = patina_stages(conn, A, {"chip": "chip_patina", "card_back": "back_patina", "mine_icons": "mine_patina"})
    check("стадии патины", s, {"chip": 1, "card_back": 1, "mine_icons": 0})

    s2 = patina_stages(conn, A, {"chip": "chip_patina"}) # only chip
    check("патины без других слотов", s2, {"chip": 1})

    from features.cosmetics_db import grant_patina_items
    granted = grant_patina_items(B, db_path=path)
    check("выдано 3 предмета", granted, 3)
    check("повтор не выдает", grant_patina_items(B, db_path=path), 0)

    # 12 потоков
    granted_C = [None] * 12
    def worker(i):
        granted_C[i] = grant_patina_items(C, db_path=path)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for th in threads: th.start()
    for th in threads: th.join()
    check("параллельная выдача", sum(granted_C), 3)

    # API Contract /api/me
    from api import create_app
    from fastapi.testclient import TestClient
    from tg_testutil import make_init_data
    client = TestClient(create_app("123", [], db_path=path))
    def auth(uid):
        return {"Authorization": "tma " + make_init_data("123", user_id=uid, auth_date=NOW, first_name="Имя")}

    r = client.get("/api/me", headers=auth(A))
    check("у игрока без надетой патины поля нет", "patina" in r.json()["cosmetics"], False)

    grant_patina_items(A, db_path=path)
    db.equip_item(A, "req-1", "chip", "chip_patina", now=NOW, db_path=path)
    r = client.get("/api/me", headers=auth(A))
    check("поле есть с одной патиной", r.json()["cosmetics"]["patina"], {"chip": 1})

    # ответ на надевание и снятие тоже отдаёт стадии надетых вещей: клиент применяет износ сразу, без запроса /api/me
    eq = db.equip_item(A, "req-2", "card_back", "back_patina", now=NOW + 100000, db_path=path)
    check("надевание: ответ содержит стадии надетой патины (фишка и рубашка)", eq["patina"], {"chip": 1, "card_back": 1})
    uneq = db.unequip_item(A, "req-3", "chip", now=NOW + 200000, db_path=path)
    check("снятие: стадия снятой патины пропала, у оставшейся есть", uneq["patina"], {"card_back": 1})
    uneq = db.unequip_item(A, "req-4", "card_back", now=NOW + 300000, db_path=path)
    check("когда патины не осталось, поля patina в ответе нет", "patina" in uneq, False)
    db.equip_item(A, "req-5", "chip", "chip_patina", now=NOW + 400000, db_path=path)      # вернуть как было для проверок ниже

    # Ленивая выдача при просмотре гардероба
    add_player(path, 424242424)
    r = client.get("/api/cosmetics/mine", headers=auth(424242424))
    owned = r.json()["owned"]
    patina_items = [i for i in owned if i.get("code") in ("chip_patina", "back_patina", "mine_patina")]
    check("ленивая выдача 3 предметов", len(patina_items), 3)

    conn.close()
    print("Все проверки прошли")
finally:
    try:
        os.remove(path)
    except:
        pass
