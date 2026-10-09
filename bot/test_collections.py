"""Коллекции косметики (docs/COLLECTIONS.md): реестр, прогресс, выдача частей «Листопада» за серию входов только в сезон и только бесплатно, нельзя купить и подарить, повторы, порядок, собранный набор."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sqlite3
import tempfile

import cosmetic_sets
import cosmetics
import db
import economy_config
from features import gifts_db

A, B = 424242422, 424242423
MSK = economy_config.STREAK_UTC_OFFSET_HOURS * 3600
LEAVES = cosmetic_sets.COLLECTIONS["leaves"]["parts"]      # все части «Листопада» (8: старые три и пять новых по DESIGN.md)
N = len(LEAVES)

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
    path = os.path.join(tmp, "col%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    for uid in (A, B):
        sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 1000, 100, ?, ?)", (uid, 4_000_000_000, 1))
        sql(path, "INSERT INTO chat_members VALUES ('room', ?, ?, ?, ?)", (uid, "Игрок", 1, 2))
    return path


def at(date_text, hour=12):
    """Момент в середине московского дня указанной даты (YYYY-MM-DD)."""
    return cosmetic_sets._day(date_text) * 86400 - MSK + (hour - 3) * 3600 + MSK


def moscow_noon(day_offset, start="2026-10-10"):
    return (cosmetic_sets._day(start) + day_offset) * 86400 - MSK + 12 * 3600


try:
    # ---- реестр
    check("коллекция «Листопад»: три части, источник серия входов, сезон октябрь 2026", (cosmetic_sets.COLLECTIONS["leaves"]["parts"], cosmetic_sets.COLLECTIONS["leaves"]["source"], cosmetic_sets.COLLECTIONS["leaves"]["season"]),
          (LEAVES, "streak", ("2026-10-01", "2026-10-31")))
    check("все части есть в каталоге, в своих слотах, без цены, доступны, не стартовые", [(cosmetics.item(c)["slot"], cosmetics.item(c)["price"], cosmetics.item(c)["available"], cosmetics.item(c)["starter"]) for c in LEAVES],
          [(slot, None, True, False) for slot in ("card_back", "table", "mine_icons", "chip", "keno_ball", "crash", "avatar_frame", "badge")])
    check("части не продаются ни за что: нет в ценах, нет в старых ценах Stars", [c in cosmetics.PRICES or c in cosmetics.LEGACY_STARS_PRICES for c in LEAVES], [False] * N)
    check("коллекция по коду части", [cosmetic_sets.collection_of(c) for c in LEAVES + ("table_blue", "nope")], ["leaves"] * N + [None, None])
    check("сезон: 30 сентября нет, 1 октября да, 31 октября да, 1 ноября нет", [cosmetic_sets.season_active("leaves", cosmetic_sets._day(d)) for d in ("2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01")], [False, True, True, False])
    check("прогресс: ничего, одна часть, все", [(p["owned"], p["total"], p["complete"]) for p in (cosmetic_sets.progress([])[0:1] + [x for x in cosmetic_sets.progress(["back_leaves"]) if x["code"] == "leaves"] + [x for x in cosmetic_sets.progress(list(LEAVES)) if x["code"] == "leaves"])],
          [(0, N, False), (1, N, False), (N, N, True)])
    day = cosmetic_sets._day("2026-10-10")
    check("часть выдаётся на 3, 5 и 7 день серии, по порядку недостающих", [cosmetic_sets.streak_part_to_grant(o, d, day) for o, d in (([], 3), (["back_leaves"], 5), (["back_leaves", "table_autumn"], 7), (list(LEAVES), 7), ([], 1), ([], 2), ([], 4), ([], 6))],
          [("leaves", "back_leaves"), ("leaves", "table_autumn"), ("leaves", "mine_acorn"), None, None, None, None, None])
    check("вне сезона части не выдаются", cosmetic_sets.streak_part_to_grant([], 3, cosmetic_sets._day("2026-11-05")), None)

    # ---- выдача за серию входов в сезон
    path = new_db()
    got = []
    for d in range(0, 7):
        r = db.claim_streak(A, now=moscow_noon(d), db_path=path)
        got.append((r["streak_day"], (r["collection_part"] or {}).get("part")))
    check("за неделю в сезон: части на днях 3, 5 и 7 по порядку", got, [(1, None), (2, None), (3, "back_leaves"), (4, None), (5, "table_autumn"), (6, None), (7, "mine_acorn")])
    check("описание выданной части: коллекция и названия", db.claim_streak(B, now=moscow_noon(0), db_path=path)["collection_part"], None)
    owned = sql(path, "SELECT item_code, source FROM cosmetic_items WHERE telegram_id = ? ORDER BY item_code", (A,))
    check("предметы у игрока с источником collection", owned, [("back_leaves", "collection"), ("mine_acorn", "collection"), ("table_autumn", "collection")])
    mine = db.cosmetics_mine(A, db_path=path)
    check("в /mine прогресс: за неделю три части из всех", [(c["code"], c["owned"], c["total"], c["complete"]) for c in mine["collections"]], [("leaves", 3, N, False), ("dacha", 0, 8, False)])
    r = db.claim_streak(A, now=moscow_noon(6) + 60, db_path=path)
    check("повтор сбора в тот же день: части заново нет", (r["replayed"], r["collection_part"]), (True, None))
    # описание части в ответе
    path2 = new_db()
    r = None
    for d in range(0, 3):
        r = db.claim_streak(A, now=moscow_noon(d), db_path=path2)
    check("ответ сбора на 3-й день: часть с названиями", r["collection_part"], {"collection": "leaves", "collection_name": "Листопад", "part": "back_leaves", "name": "Листопад"})
    # уже есть часть из другого источника: выдаётся следующая недостающая
    path3 = new_db()
    db.grant_item(A, "back_leaves", "owner_gift", db_path=path3)
    r = None
    for d in range(0, 3):
        r = db.claim_streak(A, now=moscow_noon(d), db_path=path3)
    check("первая часть уже есть: на 3-й день выдана следующая", r["collection_part"]["part"], "table_autumn")
    # после сезона
    path4 = new_db()
    for d in range(0, 7):
        r = db.claim_streak(A, now=moscow_noon(d, "2026-11-03"), db_path=path4)
    check("после сезона частей нет, фишки и кристаллы по-прежнему идут", (sql(path4, "SELECT COUNT(*) FROM cosmetic_items")[0][0], r["gems"]), (0, 5))
    # граница сезона: 31 октября ещё выдаёт, 1 ноября уже нет
    path5 = new_db()
    for d, date in enumerate(("2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02")):
        r = db.claim_streak(A, now=moscow_noon(0, date), db_path=path5)
    check("3-й день пришёлся на 31 октября: часть выдана, на 5-й день (2 ноября) нет", sql(path5, "SELECT item_code FROM cosmetic_items"), [("back_leaves",)])

    # ---- нельзя купить и подарить
    path = new_db()
    db.owner_grant_gems(A, 1000, "seed", now=1, db_path=path)
    raises(cosmetics.ItemUnavailable, db.buy_item, A, "col-req-000001", "back_leaves", now=1, db_path=path)
    raises(cosmetics.ItemUnavailable, db.buy_with_gems, A, "col-req-000002", "table_autumn", now=1, db_path=path)
    raises(cosmetics.ItemUnavailable, db.buy_with_chips, A, "col-req-000003", "mine_acorn", now=1, db_path=path)
    ref = gifts_db.member_ref("room", B)
    raises(cosmetics.ItemUnavailable, db.send_gift, A, "room", "col-req-000004", ref, "back_leaves", "Игрок", now=1, db_path=path)
    check("ни одной записи покупки или подарка, кристаллы целы", (sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], sql(path, "SELECT COUNT(*) FROM gifts")[0][0], db.gems_state(A, path)["gems"]), (0, 0, 1000))
    check("счёт за Stars на часть не выставляется (нет старой цены)", cosmetics.stars_price("back_leaves"), None)
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
