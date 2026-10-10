"""Косметика: каталог, выдача, надевание, API, рейтинг (публичные слоты), независимость от экономики, /giveitem, экспорт, удаление,
очистка, бэкап, миграция со старой базой, контракт (docs/examples/cosmetics.json)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import glob
import json
import logging
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

import backup
import bot
import cosmetics
import db
import hilo
import ratelimit
from api import create_app
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
OWNER, A, B, C = 424242421, 424242422, 424242423, 424242424

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
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


def add_player(path, uid, balance=100_000, xp=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, 0, ?, 0, 0)", (uid, balance, NOW + 10 * 86400, NOW - 86400, xp))


rid_n = [0]


def rid():
    rid_n[0] += 1
    return "cosm-req-%06d" % rid_n[0]


try:
    # ================= каталог =================
    codes = [i["code"] for i in cosmetics.CATALOG]
    check("коды уникальны", len(codes), len(set(codes)))
    check("слоты каталога допустимы", {i["slot"] for i in cosmetics.CATALOG} - set(cosmetics.SLOTS), set())
    check("все 8 слотов представлены", {i["slot"] for i in cosmetics.CATALOG}, set(cosmetics.SLOTS))
    check("редкости допустимы", {i["rarity"] for i in cosmetics.CATALOG} - set(cosmetics.RARITIES), set())
    for slot in cosmetics.SLOTS:
        starters = [i for i in cosmetics.CATALOG if i["slot"] == slot and i["starter"]]
        check("ровно один стартовый в слоте %s" % slot, len(starters), 1)
        check("стартовый бесплатный и доступный (%s)" % slot, (starters[0]["price"], starters[0]["available"], starters[0]["rarity"]), (None, True, "starter"))
    check("стартовый только у редкости starter", all(i["starter"] == (i["rarity"] == "starter") for i in cosmetics.CATALOG), True)
    check("69 предметов (23, 8 частей Листопад, 4 достижения, 8 черновик, 3 пустота, 8 дача, 3+1 патина, 3 вехи приглашений, 5 глубина)", len(cosmetics.CATALOG), 69)
    check("доступны нестартовые", sorted(i["code"] for i in cosmetics.CATALOG if i["available"] and not i["starter"]),
          sorted(["back_midnight", "chip_ring", "table_blue", "mine_star", "keno_hex", "crash_neon", "frame_thin", "badge_spade", "back_leaves", "table_autumn", "mine_acorn", "chip_leaf", "keno_apple", "crash_maple", "frame_wreath", "badge_pumpkin", "achv_nearly", "achv_sapper", "achv_bust", "achv_keno",
                  "draft_crash", "draft_mines", "draft_table", "draft_badge", "draft_chip", "draft_keno", "draft_back", "draft_frame", "void_table", "void_chip", "void_badge",
                  "back_rug", "chip_cork", "table_oilcloth", "mine_beetle", "keno_lotto", "crash_barrel", "frame_dacha", "badge_dacha", "chip_patina", "back_patina", "mine_patina", "frame_patina", "table_deep", "chip_pearl", "mine_urchin", "keno_bubble", "crash_deep", "back_deep", "frame_deep", "badge_deep", "ref_scout", "ref_beacon", "ref_comet"]))
    check("цена есть у платных", sorted(i["code"] for i in cosmetics.CATALOG if i["price"] is not None),
          sorted(c for c, _ in cosmetics.PRICES.items()))
    check("цены в одном месте: валюта и целая положительная сумма", all(v[0] in ("gems", "chips") and type(v[1]) is int and v[1] > 0 for v in cosmetics.PRICES.values()), True)
    check("цены из задания", {k: tuple(v) for k, v in cosmetics.PRICES.items()}, {"table_blue": ("gems", 150), "crash_neon": ("gems", 100), "back_midnight": ("gems", 100), "keno_hex": ("gems", 75),
                                                                           "badge_spade": ("chips", 20000), "chip_ring": ("chips", 40000), "mine_star": ("chips", 60000), "frame_thin": ("chips", 100000),
                                                                           "draft_crash": ("gems", 100), "draft_mines": ("gems", 100), "draft_table": ("gems", 100), "draft_badge": ("gems", 100), "draft_chip": ("gems", 100), "draft_keno": ("gems", 100), "draft_back": ("gems", 100), "draft_frame": ("gems", 100),
                                                                           "void_table": ("gems", 150), "void_chip": ("gems", 150), "void_badge": ("gems", 150),
                                                                           "chip_patina": ("gems", 400), "back_patina": ("gems", 400), "mine_patina": ("gems", 400), "frame_patina": ("gems", 400),
                                                                           "table_deep": ("gems", 150), "chip_pearl": ("gems", 150), "mine_urchin": ("gems", 150), "keno_bubble": ("gems", 150), "crash_deep": ("gems", 150), "back_deep": ("gems", 150), "frame_deep": ("gems", 150), "badge_deep": ("gems", 150),
                                                                           })
    check("недоступные и стартовые без цены", all(i["price"] is None for i in cosmetics.CATALOG if i["starter"] or not i["available"]), True)
    check("скрытого тестового предмета нет в каталоге для клиента", ("test_1star" in codes, cosmetics.item("test_1star"), cosmetics.sellable("test_1star")["price"]), (False, None, {"currency": "stars", "amount": 1}))
    check("публичные слоты", cosmetics.PUBLIC_SLOTS, ("avatar_frame", "badge"))
    check("источники", cosmetics.SOURCES, ("free", "owner_gift", "stars", "chips", "gems", "gift", "collection", "achievement", "referral"))

    # ================= статически: экономика и игры косметику не читают =================
    guarded = (glob.glob(os.path.join(HERE, "games", "*.py")) + glob.glob(os.path.join(HERE, "core", "kernel.py")) +
               [os.path.join(HERE, n) for n in ("wallet.py", "economy.py", "farm.py", "levels.py", "xp.py", "roulette.py", "keno.py", "mines.py",
                                                "blackjack.py", "crash.py", "hilo.py", "ratelimit.py")] +
               [os.path.join(HERE, "features", n) for n in ("farm_db.py", "give_db.py", "grants_db.py")])
    assert len(guarded) >= 20, guarded
    for f in guarded:
        text = open(f, encoding="utf-8").read().lower()
        assert "cosmetic" not in text, "%s упоминает косметику: игры, wallet, economy и переводы её не читают" % os.path.relpath(f, HERE)

    # ================= grant_item =================
    path = new_db()
    add_player(path, A)
    check("выдача", db.grant_item(A, "back_midnight", "free", now=NOW, db_path=path), True)
    check("повтор: не создаёт дубль и не падает", db.grant_item(A, "back_midnight", "owner_gift", now=NOW + 5, db_path=path), False)
    check("одна строка, источник первой выдачи", sql(path, "SELECT item_code, source, payment_ref, acquired_at FROM cosmetic_items"), [("back_midnight", "free", None, NOW)])
    raises(ValueError, db.grant_item, A, "nope", "free", db_path=path)
    raises(ValueError, db.grant_item, A, 5, "free", db_path=path)
    raises(ValueError, db.grant_item, A, "chip_ring", "hacked", db_path=path)
    raises(ValueError, db.grant_item, A, "chip_ring", None, db_path=path)
    raises(ValueError, db.grant_item, A, "chip_plain", "free", db_path=path)       # стартовые не выдаются
    raises(ValueError, db.grant_item, A, "back_classic", "owner_gift", db_path=path)
    raises(cosmetics.NoSuchPlayer, db.grant_item, 999, "chip_ring", "free", db_path=path)
    check("недоступный в каталоге предмет выдать можно (владелец)", db.grant_item(A, "frame_crown", "owner_gift", now=NOW, db_path=path), True)
    check("платёжная ссылка пока хранится как есть", db.grant_item(A, "badge_flame", "stars", "ref-1", now=NOW, db_path=path), True)
    check("payment_ref сохранён", sql(path, "SELECT payment_ref FROM cosmetic_items WHERE item_code = 'badge_flame'"), [("ref-1",)])
    check("ничего лишнего: три строки", sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], 3)

    # ================= equip / unequip =================
    path = new_db()
    add_player(path, A)
    add_player(path, B)
    t = [NOW]

    def tick():
        t[0] += 5
        return t[0]

    st = db.cosmetics_state(A, db_path=path)
    check("по умолчанию надеты стартовые", st["equipped"], cosmetics.STARTERS)
    check("показ в рейтинге по умолчанию включён", st["show_in_rating"], True)
    e = raises(cosmetics.NotOwned, db.equip_item, A, rid(), "card_back", "back_midnight", now=tick(), db_path=path)
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    r = db.equip_item(A, "req-equip-0001", "card_back", "back_midnight", now=tick(), db_path=path)
    check("надето своё", (r["slot"], r["code"], r["equipped"]["card_back"], r["replayed"]), ("card_back", "back_midnight", "back_midnight", False))
    check("у другого игрока не изменилось", db.cosmetics_state(B, db_path=path)["equipped"], cosmetics.STARTERS)
    check("повтор: прежний ответ", db.equip_item(A, "req-equip-0001", "card_back", "back_midnight", now=tick(), db_path=path),
          dict(r, replayed=True))
    raises(cosmetics.RequestConflict, db.equip_item, A, "req-equip-0001", "card_back", "back_classic", now=tick(), db_path=path)
    raises(cosmetics.RequestConflict, db.unequip_item, A, "req-equip-0001", "card_back", now=tick(), db_path=path)
    raises(cosmetics.NotOwned, db.equip_item, B, rid(), "card_back", "back_midnight", now=tick(), db_path=path)    # чужое
    raises(cosmetics.UnknownItem, db.equip_item, A, rid(), "card_back", "nope", now=tick(), db_path=path)
    raises(cosmetics.SlotMismatch, db.equip_item, A, rid(), "chip", "back_midnight", now=tick(), db_path=path)
    raises(cosmetics.SlotMismatch, db.equip_item, A, rid(), "card_back", "chip_plain", now=tick(), db_path=path)
    db.grant_item(A, "back_ember", "owner_gift", now=NOW, db_path=path)
    raises(cosmetics.ItemUnavailable, db.equip_item, A, rid(), "card_back", "back_ember", now=tick(), db_path=path)   # есть, но надеть нельзя
    raises(cosmetics.ItemUnavailable, db.equip_item, B, rid(), "card_back", "back_ember", now=tick(), db_path=path)
    for bad_slot in ("nope", None, 5):
        raises(ValueError, db.equip_item, A, rid(), bad_slot, "back_midnight", now=tick(), db_path=path)
        raises(ValueError, db.unequip_item, A, rid(), bad_slot, now=tick(), db_path=path)
    raises(ValueError, db.equip_item, A, rid(), "card_back", 5, now=tick(), db_path=path)
    check("отказы ничего не записали в журнал", sql(path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0], 1)
    r = db.equip_item(A, rid(), "card_back", "back_classic", now=tick(), db_path=path)          # стартовый = снять
    check("стартовый надевается без владения и снимает", (r["equipped"]["card_back"], sql(path, "SELECT COUNT(*) FROM cosmetic_equipped")[0][0]), ("back_classic", 0))
    db.equip_item(A, rid(), "card_back", "back_midnight", now=tick(), db_path=path)
    r = db.unequip_item(A, rid(), "card_back", now=tick(), db_path=path)
    check("снято", (r["code"], r["equipped"]["card_back"], sql(path, "SELECT COUNT(*) FROM cosmetic_equipped")[0][0]), ("back_classic", "back_classic", 0))
    check("снять пустой слот не ошибка", db.unequip_item(A, rid(), "chip", now=tick(), db_path=path)["code"], "chip_plain")
    check("предмет остался у игрока после снятия", sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE item_code = 'back_midnight'")[0][0], 1)
    r = db.set_visibility(A, "req-vis-0001", False, now=tick(), db_path=path)
    check("скрыть показ", (r["show_in_rating"], db.cosmetics_state(A, db_path=path)["show_in_rating"]), (False, False))
    check("повтор visibility", db.set_visibility(A, "req-vis-0001", False, now=tick(), db_path=path), dict(r, replayed=True))
    raises(cosmetics.RequestConflict, db.set_visibility, A, "req-vis-0001", True, now=tick(), db_path=path)
    for bad in (1, 0, "true", None):
        raises(ValueError, db.set_visibility, A, rid(), bad, now=tick(), db_path=path)
    db.set_visibility(A, rid(), True, now=tick(), db_path=path)
    check("настройка в одной строке", sql(path, "SELECT show_in_rating FROM cosmetic_prefs WHERE telegram_id = ?", (A,)), [(1,)])
    # не чаще одной смены в секунду на игрока
    base = tick()
    db.unequip_item(A, rid(), "chip", now=base, db_path=path)
    raises(cosmetics.TooFast, db.unequip_item, A, rid(), "chip", now=base, db_path=path)
    check("повтор прежнего request_id не считается сменой", db.unequip_item(A, "req-fast-0001", "chip", now=base + 1, db_path=path)["replayed"], False)
    check("повтор в ту же секунду возвращает ответ", db.unequip_item(A, "req-fast-0001", "chip", now=base + 1, db_path=path)["replayed"], True)
    db.unequip_item(A, rid(), "chip", now=base + 2, db_path=path)
    check("другой игрок не ограничен чужой сменой", db.unequip_item(B, rid(), "chip", now=base + 2, db_path=path)["code"], "chip_plain")

    # ================= 20 потоков на один слот =================
    path = new_db()
    add_player(path, A)
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    gate = threading.Barrier(20)

    def work(i):
        gate.wait()
        try:
            code = "back_midnight" if i % 2 == 0 else "back_classic"
            return db.equip_item(A, "thr-req-%04d" % i, "card_back", code, now=NOW + 10 * (i + 1), db_path=path)["code"]
        except cosmetics.TooFast:
            return "fast"
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(work, range(20)))
    rows = sql(path, "SELECT item_code FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'card_back'", (A,))
    assert len(rows) <= 1 and set(res) <= {"back_midnight", "back_classic", "fast"}, (res, rows)
    check("итог: один предмет в слоте", db.cosmetics_state(A, db_path=path)["equipped"]["card_back"] in ("back_midnight", "back_classic"), True)
    gate2 = threading.Barrier(20)

    def same(i):
        gate2.wait()
        return db.equip_item(A, "thr-same-0001", "card_back", "back_midnight", now=NOW + 1000, db_path=path)
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(same, range(20)))
    check("20 одинаковых request_id: одно действие", (sum(1 for x in res if not x["replayed"]), len({json.dumps(dict(x, replayed=0), sort_keys=True) for x in res})), (1, 1))

    # ================= API =================
    path = new_db()
    for uid in (OWNER, A, B, C):
        add_player(path, uid)
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "100", "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "100"}),
                                clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid, name="Игрок", group=True, chat="room"):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=name,
                                                         chat_type="group" if group else None, chat_instance=chat if group else None)}

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "cosmetics.json"), encoding="utf-8"))

    def shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for k, v in example.items():
            if isinstance(v, dict) and v and k not in ("equipped", "cosmetics"):
                shape(name + "." + k, body[k], v)
            elif isinstance(v, bool):
                assert type(body[k]) is bool, (name, k)
            elif isinstance(v, int):
                assert type(body[k]) is int, (name, k)
            elif isinstance(v, str):
                assert type(body[k]) is str, (name, k)

    def post(uid, route, body, **kw):
        return client.post("/api/cosmetics/" + route, headers=auth(uid, **kw), json=body)

    # без подписи и с чужой подписью
    for route in ("catalog", "mine"):
        check("без подписи %s" % route, client.get("/api/cosmetics/" + route).status_code, 401)
    check("POST без подписи", client.post("/api/cosmetics/equip", json={"request_id": rid(), "slot": "chip", "code": "chip_plain"}).status_code, 401)
    r = client.get("/api/cosmetics/catalog", headers=auth(A))
    check("каталог", r.status_code, 200)
    body = r.json()
    check("в каталоге все предметы и слоты", (len(body["items"]), len(body["slots"])), (69, 8))
    shape("catalog.item", body["items"][0], examples["catalog"]["items"][0])
    shape("catalog.slot", body["slots"][0], examples["catalog"]["slots"][0])
    check("каталог одинаков для всех игроков (принадлежность не раскрывается)", client.get("/api/cosmetics/catalog", headers=auth(B)).json(), body)
    r = client.get("/api/cosmetics/mine", headers=auth(A))
    check("mine пустой (патину больше не выдаём)", (r.status_code, [x["code"] for x in r.json()["owned"]], r.json()["show_in_rating"]), (200, [], True))
    shape("mine", r.json(), examples["mine"])
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "frame_thin", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "badge_spade", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "chip_ring", "owner_gift", now=NOW, db_path=path)
    r = client.get("/api/cosmetics/mine", headers=auth(A)).json()
    check("mine: предметы без платёжных полей", sorted(r["owned"][0]), ["acquired_at", "code", "source"])
    check("mine: предметы", sorted(o["code"] for o in r["owned"]), sorted(["back_midnight", "badge_spade", "chip_ring", "frame_thin"]))
    check("у B ничего", [x["code"] for x in client.get("/api/cosmetics/mine", headers=auth(B)).json()["owned"]], [])
    r = post(A, "equip", {"request_id": "api-eq-000001", "slot": "card_back", "code": "back_midnight"})
    check("equip", r.status_code, 200)
    shape("equip", r.json(), examples["equip"])
    check("equip: надето", r.json()["equipped"]["card_back"], "back_midnight")
    clock[0] += 1
    check("повтор того же request_id", (post(A, "equip", {"request_id": "api-eq-000001", "slot": "card_back", "code": "back_midnight"}).json()["replayed"]), True)
    check("другие параметры при том же request_id", (lambda x: (x.status_code, x.json()))(post(A, "equip", {"request_id": "api-eq-000001", "slot": "card_back", "code": "back_classic"})),
          (409, {"detail": "request_conflict"}))
    errs = examples["errors"]
    check("неизвестный предмет", (lambda x: (x.status_code, x.json()))(post(A, "equip", {"request_id": rid(), "slot": "chip", "code": "nope"})), (404, errs["unknown_item"]))
    check("чужой предмет", (lambda x: (x.status_code, x.json()))(post(B, "equip", {"request_id": rid(), "slot": "card_back", "code": "back_midnight"})), (409, errs["not_owned"]))
    check("неверный слот", (lambda x: (x.status_code, x.json()))(post(A, "equip", {"request_id": rid(), "slot": "chip", "code": "back_midnight"})), (409, errs["slot_mismatch"]))
    db.grant_item(A, "back_ember", "owner_gift", now=NOW, db_path=path)
    check("недоступный предмет", (lambda x: (x.status_code, x.json()))(post(A, "equip", {"request_id": rid(), "slot": "card_back", "code": "back_ember"})), (409, errs["item_unavailable"]))
    for body in ({}, {"request_id": rid()}, {"request_id": rid(), "slot": "chip"}, {"request_id": rid(), "slot": "nope", "code": "chip_plain"},
                 {"request_id": rid(), "slot": "chip", "code": 5}, {"request_id": rid(), "slot": "chip", "code": "chip_plain", "extra": 1},
                 {"request_id": "x", "slot": "chip", "code": "chip_plain"}, {"request_id": rid(), "slot": ["chip"], "code": "chip_plain"}):
        r = post(A, "equip", body)
        check("400 %s" % json.dumps(body)[:50], (r.status_code, r.json()), (400, errs["invalid_request"]))
    check("не JSON", client.post("/api/cosmetics/equip", headers=auth(A), content=b"not json").status_code, 400)
    check("слишком большое тело", client.post("/api/cosmetics/equip", headers=auth(A), content=b"x" * 70_000).status_code, 413)
    check("GET на POST-маршрут", client.get("/api/cosmetics/equip", headers=auth(A)).status_code, 405)
    # не чаще раза в секунду (по серверному времени): две смены подряд в одну секунду
    now_real = int(time.time())
    p2 = post(A, "unequip", {"request_id": "api-un-000001", "slot": "card_back"})
    p3 = post(A, "unequip", {"request_id": "api-un-000002", "slot": "chip"})
    assert p2.status_code in (200, 429) and p3.status_code in (200, 429), (p2.status_code, p3.status_code)
    if p2.status_code == 200 and p3.status_code == 429:
        check("429 с Retry-After", (p3.json(), p3.headers.get("Retry-After")), ({"error": "too_many_requests"}, "1"))
    time.sleep(1.1)
    r = post(A, "unequip", {"request_id": "api-un-000003", "slot": "card_back"})
    check("unequip", r.status_code, 200)
    shape("unequip", r.json(), examples["unequip"])
    time.sleep(1.1)
    r = post(A, "visibility", {"request_id": "api-vis-00001", "show_in_rating": False})
    check("visibility", (r.status_code, r.json()["show_in_rating"]), (200, False))
    shape("visibility", r.json(), examples["visibility"])
    check("visibility: не булево", post(A, "visibility", {"request_id": rid(), "show_in_rating": 1}).status_code, 400)
    time.sleep(1.1)
    post(A, "visibility", {"request_id": rid(), "show_in_rating": True})

    # /api/me
    me = client.get("/api/me", headers=auth(A)).json()
    check("/api/me: косметика", sorted(me["cosmetics"]), ["complete_sets", "equipped", "show_in_rating"])
    check("/api/me: надетое по слотам", sorted(me["cosmetics"]["equipped"]), sorted(cosmetics.SLOTS))
    check("/api/me: новый игрок со стартовыми", client.get("/api/me", headers=auth(C)).json()["cosmetics"], {"equipped": cosmetics.STARTERS, "show_in_rating": True, "complete_sets": []})

    # ================= рейтинг: публичные слоты =================
    for uid, name in ((A, "Аня"), (B, "Борис"), (C, "Вера")):
        sql(path, "INSERT OR REPLACE INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('room', ?, ?, ?, ?)",
            (uid, name, int(time.time()) - 100, int(time.time())))
    db.grant_item(B, "frame_thin", "owner_gift", db_path=path)
    db.grant_item(B, "badge_spade", "owner_gift", db_path=path)
    db.grant_item(B, "chip_ring", "owner_gift", db_path=path)
    db.grant_item(B, "table_blue", "owner_gift", db_path=path)
    t0 = int(time.time()) + 1000
    for i, (slot, code) in enumerate((("avatar_frame", "frame_thin"), ("badge", "badge_spade"), ("chip", "chip_ring"), ("table", "table_blue"))):
        db.equip_item(B, rid(), slot, code, now=t0 + 5 * i, db_path=path)

    def top_for(viewer):
        r = client.get("/api/chat/top", headers=auth(viewer, name="Зритель"))
        assert r.status_code == 200, r.text
        return {e["name"]: e for e in r.json()["top"]}

    clock[0] += 5
    top = top_for(A)
    check("публичные слоты B видны другим", top["Борис"]["cosmetics"], {"avatar_frame": "frame_thin", "badge": "badge_spade"})
    check("приватные слоты (фишки, стол) других не отдаются никогда", set(top["Борис"]["cosmetics"]) <= set(cosmetics.PUBLIC_SLOTS), True)
    check("у Веры ничего не надето: пусто", top["Вера"]["cosmetics"], {})
    check("в ответе нет приватных кодов", "chip_ring" in json.dumps(client.get("/api/chat/top", headers=auth(A)).json()) or "table_blue" in json.dumps(client.get("/api/chat/top", headers=auth(A)).json()), False)
    shape("rating.entry", top["Борис"], examples["rating_entry"])
    sql(path, "INSERT OR REPLACE INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 0)", (B,))
    clock[0] += 5
    check("show_in_rating=0: публичные слоты скрыты", top_for(A)["Борис"]["cosmetics"], {})
    sql(path, "INSERT OR REPLACE INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 1)", (B,))
    sql(path, "DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'badge'", (B,))
    clock[0] += 5
    check("значок снят: остаётся только рамка", top_for(A)["Борис"]["cosmetics"], {"avatar_frame": "frame_thin"})
    sql(path, "INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'avatar_frame', 'no_such_code')", (B,))
    clock[0] += 5
    check("неизвестный код в базе не отдаётся", top_for(A)["Борис"]["cosmetics"], {})
    sql(path, "INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'avatar_frame', 'badge_spade')", (B,))
    clock[0] += 5
    check("код чужого слота не отдаётся", top_for(A)["Борис"]["cosmetics"], {})

    # ================= экономика не зависит от косметики =================
    def play(path_):
        out = []
        out.append(db.spin_roulette(A, "econ-spin-0001", [{"type": "number", "value": 7, "amount": 100}, {"type": "red", "value": None, "amount": 50}],
                                    now=NOW, rng=lambda n: 7, db_path=path_))
        out.append(db.mines_start(A, "econ-mine-0001", 100, 3, now=NOW + 1, rng=random.Random(5), db_path=path_))
        g = db.mines_state(A, now=NOW + 2, db_path=path_)["game"]
        safe = [c for c in range(25) if c not in g["revealed"]][:1]
        out.append(db.mines_reveal(A, "econ-mine-0002", safe[0], now=NOW + 3, db_path=path_))
        out.append(db.mines_cashout(A, "econ-mine-0003", now=NOW + 4, db_path=path_))
        out.append(db.hilo_start(A, "econ-hilo-0001", 100, now=NOW + 5, rng=random.Random(7), db_path=path_))
        out.append(db.hilo_guess(A, "econ-hilo-0002", "skip", now=NOW + 6, rng=random.Random(8), db_path=path_))
        out.append(db.play_keno(A, "econ-keno-0001", 100, [1, 2, 3], now=NOW + 7, rng=random.Random(9), db_path=path_))
        row = sql(path_, "SELECT balance, xp, total_staked, rate, income_level, storage_level FROM players WHERE telegram_id = ?", (A,))[0]
        return json.dumps(out, sort_keys=True, default=str), row
    p1, p2 = new_db(), new_db()
    add_player(p1, A, 1_000_000)
    add_player(p2, A, 1_000_000)
    for code, slot in (("back_midnight", "card_back"), ("chip_ring", "chip"), ("table_blue", "table"), ("mine_star", "mine_icons"),
                       ("keno_hex", "keno_ball"), ("crash_neon", "crash"), ("frame_thin", "avatar_frame"), ("badge_spade", "badge")):
        db.grant_item(A, code, "owner_gift", now=NOW, db_path=p2)
    tt = NOW - 100
    for code, slot in (("back_midnight", "card_back"), ("chip_ring", "chip"), ("table_blue", "table"), ("mine_star", "mine_icons"),
                       ("keno_hex", "keno_ball"), ("crash_neon", "crash"), ("frame_thin", "avatar_frame"), ("badge_spade", "badge")):
        tt += 5
        db.equip_item(A, rid(), slot, code, now=tt, db_path=p2)
    check("надето восемь предметов", len(sql(p2, "SELECT * FROM cosmetic_equipped")), 8)
    r1, r2 = play(p1), play(p2)
    check("ответы игр, баланс, XP, ставки и ферма одинаковы с предметами и без", r1, r2)

    # ================= /giveitem =================
    path = new_db()
    for uid in (OWNER, A):
        add_player(path, uid)
    os.environ["DB_PATH"] = path
    os.environ["OWNER_CHAT_ID"] = str(OWNER)

    def say(args, uid=OWNER, chat="private"):
        update = FakeUpdate(chat, user_id=uid)
        asyncio.run(bot.giveitem(update, SimpleNamespace(args=args, application=SimpleNamespace(bot_data={}))))
        return [r["text"] for r in update.replies]

    check("чужой: нет ответа", say(["chip_ring"], uid=B), [])
    check("владелец в группе: нет ответа", say(["chip_ring"], chat="supergroup"), [])
    check("канал: нет ответа", say(["chip_ring"], chat="channel"), [])
    os.environ.pop("OWNER_CHAT_ID")
    check("без OWNER_CHAT_ID: нет ответа", say(["chip_ring"]), [])
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    check("ничего не выдано", sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], 0)
    for args in ([], ["a", "b", "c"], ["chip_ring", "abc"], ["chip_ring", "-5"], ["chip_ring", "1" * 20]):
        out = say(args)
        assert len(out) == 1 and out[0].startswith("Формат"), (args, out)
    check("неизвестный код", say(["nope"]), ["Такого предмета нет в каталоге"])
    check("стартовый", say(["chip_plain"])[0].startswith("Стартовые предметы"), True)
    cap.lines.clear()
    check("себе", say(["chip_ring"]), ["Выдано: Кольцо"])
    check("запись source=owner_gift", sql(path, "SELECT telegram_id, item_code, source, payment_ref FROM cosmetic_items"), [(OWNER, "chip_ring", "owner_gift", None)])
    check("повтор: уже есть", say(["chip_ring"]), ["Уже есть: Кольцо"])
    check("дубля нет", sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], 1)
    check("игроку по id", say(["badge_flame", str(A)]), ["Выдано: Пламя"])
    check("игроку: запись", sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ?", (A,)), [("badge_flame",)])
    check("игрока нет в базе", say(["chip_ring", "999"]), ["Игрока нет в базе: он должен хотя бы раз открыть игру"])
    log_text = "\n".join(cap.lines)
    for needle in (str(OWNER), str(A), "chip_ring", "badge_flame", "Кольцо", "Пламя"):
        assert needle not in log_text, "в логах есть %r: %s" % (needle, log_text)
    menu = [c.command for c in bot.PRIVATE_COMMANDS + bot.GROUP_COMMANDS]
    assert "giveitem" not in menu and "/giveitem" not in bot.PRIVATE_HELP and "/giveitem" not in bot.GROUP_HELP
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
    names = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert "giveitem" in names, names

    # ================= экспорт, удаление, очистка =================
    path = new_db()
    add_player(path, A)
    add_player(path, B)
    db.grant_item(A, "back_midnight", "owner_gift", payment_ref="secret-ref", now=NOW, db_path=path)
    db.equip_item(A, rid(), "card_back", "back_midnight", now=NOW + 5, db_path=path)
    db.set_visibility(A, rid(), False, now=NOW + 10, db_path=path)
    db.grant_item(B, "chip_ring", "free", now=NOW, db_path=path)
    ex = db.get_player_export(A, db_path=path)
    check("экспорт: косметика", ex["cosmetics"], {"items": [{"code": "back_midnight", "source": "owner_gift", "acquired_at": NOW}],
                                                  "equipped": {"card_back": "back_midnight"}, "show_in_rating": False, "purchases": []})
    assert "secret-ref" not in json.dumps(ex), "payment_ref не должен попадать в выгрузку"
    only = new_db()
    add_player(only, C)
    sql(only, "DELETE FROM players")
    sql(only, "INSERT INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, 0)", (C,))
    check("только настройка: выгрузка есть", db.get_player_export(C, db_path=only) is not None, True)
    counts = db.delete_player_data(A, db_path=path, now=NOW + 100)
    for table in ("cosmetic_items", "cosmetic_equipped", "cosmetic_prefs", "cosmetic_actions"):
        check("удалено из %s" % table, sql(path, "SELECT COUNT(*) FROM %s WHERE telegram_id = ?" % table, (A,))[0][0], 0)
    check("чужие предметы остались", sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ?", (B,))[0][0], 1)
    check("tombstone записан как раньше", sql(path, "SELECT COUNT(*) FROM deletion_tombstones")[0][0], 1)
    check("players удалён", counts["players"], 1)
    # очистка: cosmetic_actions по сроку 30 дней, предметы и надетое не чистятся
    path = new_db()
    add_player(path, A)
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    db.equip_item(A, "old-act-0001", "card_back", "back_midnight", now=NOW + 5, db_path=path)
    db.set_visibility(A, "new-act-0001", False, now=NOW + 40 * 86400, db_path=path)
    db.purge_old_data(now=NOW + 41 * 86400, db_path=path)
    check("очистка: старое действие удалено, свежее осталось", sql(path, "SELECT request_id FROM cosmetic_actions"), [("new-act-0001",)])
    check("предметы, надетое и настройки не чистятся", (sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], sql(path, "SELECT COUNT(*) FROM cosmetic_equipped")[0][0],
                                                    sql(path, "SELECT COUNT(*) FROM cosmetic_prefs")[0][0]), (1, 1, 1))

    # ================= бэкап и восстановление =================
    bdir = os.path.join(tmp, "bk")
    os.makedirs(bdir)
    snap = backup.create_snapshot(path, bdir, now=NOW + 50 * 86400)
    snap_path = snap if isinstance(snap, str) and os.path.exists(snap) else (sorted(glob.glob(os.path.join(bdir, "*")))[0])
    check("в копии новые таблицы с данными", (sql(snap_path, "SELECT COUNT(*) FROM cosmetic_items")[0][0], sql(snap_path, "SELECT COUNT(*) FROM cosmetic_equipped")[0][0],
                                              sql(snap_path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0]), (1, 1, 1))
    restored = os.path.join(tmp, "restored.db")
    sql(snap_path, "SELECT 1")
    import shutil
    shutil.copy(snap_path, restored)
    db.init_db(restored)    # восстановленная база открывается кодом без потерь
    check("восстановление: надетое на месте", db.cosmetics_state(A, db_path=restored)["equipped"]["card_back"], "back_midnight")

    # Миграции со старых версий базы проверяет test_legacy_migration.py на фикстурах схемы (bot/testdata/legacy), без git.

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
