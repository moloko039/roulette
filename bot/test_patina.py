"""Тест патины (docs/COLLECTIONS.md, №3): счётчики, /api/me и выдача."""
import testenv  # noqa: F401
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import cosmetic_sets
import db
import economy_config

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = int(time.time())
A, B, C = 424242421, 424242422, 424242423

def patina_age_stage_for(days):
    from features.patina_db import account_age_stage
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE players (telegram_id INTEGER, created_at INTEGER)")
    conn.execute("INSERT INTO players VALUES (1, ?)", (NOW - days * 86400,))
    return account_age_stage(conn, 1, NOW)


def cosmetics_price(code):
    import cosmetics
    return cosmetics.item(code)["price"]["amount"]


def _public_cosmetics_for(path, uid):
    from features.chat_db import _public_cosmetics
    sql(path, "INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'badge', 'badge_patina')", (uid,))
    return _public_cosmetics(path, [uid])[uid]


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

    def give(uid):
        for code in ("chip_patina", "back_patina", "mine_patina"):
            db.grant_item(uid, code, "free", db_path=path)

    # API Contract /api/me
    from api import create_app
    from fastapi.testclient import TestClient
    from tg_testutil import make_init_data
    client = TestClient(create_app("123", [], db_path=path))
    def auth(uid):
        return {"Authorization": "tma " + make_init_data("123", user_id=uid, auth_date=NOW, first_name="Имя")}

    r = client.get("/api/me", headers=auth(A))
    check("у игрока без надетой патины поля нет", "patina" in r.json()["cosmetics"], False)

    give(A)
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

    # открытие гардероба патину больше не выдаёт (с 2026-10-09 это набор за 1000 кристаллов)
    add_player(path, 424242424)
    r = client.get("/api/cosmetics/mine", headers=auth(424242424))
    check("гардероб патину не выдаёт", [i for i in r.json()["owned"] if "patina" in i.get("code", "")], [])

    # --- рамка «Патина»: стадия по стажу аккаунта (30 / 90 / 180 / 365 дней), у себя и у других участников беседы
    check("стадии рамки по дням", [economy_config.PATINA_FRAME_DAYS, [patina_age_stage_for(d) for d in (0, 29, 30, 89, 90, 179, 180, 364, 365, 2000)]],
          [(30, 90, 180, 365), [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]])
    F = 424242430
    add_player(path, F)
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 100 * 86400, F))
    db.grant_item(F, "frame_patina", "free", db_path=path)
    eq = db.equip_item(F, "req-f1", "avatar_frame", "frame_patina", now=NOW + 1_000_000, db_path=path)
    check("рамка на 100-й день: стадия 2", eq["patina"], {"avatar_frame": 2})
    r = client.get("/api/me", headers=auth(F))
    check("/api/me отдаёт стадию рамки", r.json()["cosmetics"]["patina"], {"avatar_frame": 2})
    sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('rating-chat', ?, 'Рамочник', ?, ?)", (F, NOW, NOW))
    sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('rating-chat', ?, 'Я', ?, ?)", (A, NOW, NOW))
    from features.chat_db import _public_cosmetics
    shown = _public_cosmetics(path, [F, A])
    check("другим участникам видна рамка и её стадия", shown[F], {"avatar_frame": "frame_patina", "avatar_frame_stage": 2})
    db.set_visibility(F, "req-f2", False, now=NOW + 1_001_000, db_path=path)
    check("скрывший показ ничего не отдаёт", F in _public_cosmetics(path, [F]), False)

    # --- набор «Патина» продаётся за 1000 кристаллов; нельзя купить, если уже есть хотя бы одна часть (добрать рамку можно отдельно за 400)
    P = 424242431
    add_player(path, P)
    import wallet
    conn2 = db._connect(path)
    conn2.execute("BEGIN IMMEDIATE")
    wallet.gems_credit(conn2, P, 1500, "owner_grant", "test", NOW)
    conn2.execute("COMMIT")
    conn2.close()
    from features.cosmetics_db import buy_set
    bought = buy_set(P, "req-set-1", "patina", now=NOW, db_path=path)
    check("набор за 1000: все семь частей, остаток 500", (sorted(bought["items"]), bought["price_gems"], bought["gems"]), (sorted(cosmetic_sets.SETS["patina"]["parts"]), 1000, 500))
    check("части с источником gems", sorted(r[0] for r in sql(path, "SELECT DISTINCT source FROM cosmetic_items WHERE telegram_id = ?", (P,))), ["gems"])
    check("часть дороже набора по отдельности (7 x 400 > 1000)", sum(cosmetics_price(c) for c in cosmetic_sets.SETS["patina"]["parts"]) > 1000, True)

    # --- новые части (DESIGN.md раздел 8): кено по розыгрышам, краш по раундам, именной жетон по дням; засечки жетона не больше 24
    from features.patina_db import patina_info
    K = 424242440
    add_player(path, K)
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 400 * 86400, K))
    for i in range(110):
        sql(path, "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) VALUES (?, ?, 1, '[]', '[]', 0, 0, ?)", (K, "k%d" % i, NOW))
    for i in range(30):
        sql(path, "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at, finished_at) VALUES (?, 10, 'manual', NULL, 6000, 1, 'finished', 1, 2)", (K,))
    eq = {"chip": "chip_patina", "keno_ball": "keno_patina", "crash": "crash_patina", "badge": "badge_patina"}
    import sqlite3 as _sq
    rconn = _sq.connect(path)
    rconn.row_factory = _sq.Row
    st = patina_stages(rconn, K, eq, now=NOW)
    check("стадии новых частей: кено 110 розыгрышей = 2, краш 30 раундов = 1, жетон 400 дней = 4, фишка 30 крахов выше x50 = 3", (st["keno_ball"], st["crash"], st["badge"], st["chip"]), (2, 1, 4, 3))
    check("числа для рисунка: засечек не больше 24, дней в игре", patina_info(rconn, K, eq, now=NOW), {"chip_notches": 24, "days": 400})
    check("без надетой патины чисел нет", patina_info(rconn, K, {"chip": "chip_plain"}, now=NOW), {})
    shown = _public_cosmetics_for(path, K)
    check("другим участникам виден именной жетон: дни и стадия", (shown["badge"], shown["badge_days"], shown["badge_stage"]), ("badge_patina", 400, 4))

    conn.close()
    print("Все проверки прошли")
finally:
    try:
        os.remove(path)
    except:
        pass
