"""Тесты кнопки «Надеть набор» (equip_collection, POST /api/cosmetics/equip-set).
Проверяет частичную экипировку, выбор последней части в слоте («Основатель»), откат при
отсутствии частей, 404/409, идемпотентность, ограничение частоты смен, недоступные предметы и валидацию HTTP.
"""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import os
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

from api import create_app
import cosmetic_sets
import cosmetics
import db
import ratelimit
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
A, B = 424242421, 424242422

tmp = tempfile.mkdtemp()
counter = [0]


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


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "equip_set_%d.db" % counter[0])
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


def add_player(path, uid, balance=100_000):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, 0, 0, 0, 0)", (uid, balance, NOW + 10 * 86400, NOW - 86400))


rid_n = [0]


def rid():
    rid_n[0] += 1
    return "eq-set-req-%06d" % rid_n[0]


try:
    # ================= а) части не всех слотов: надеваются только полученные, чужие слоты не затронуты =================
    path = new_db()
    add_player(path, A)
    # Игрок заранее надел лагунный стол и звёздные мины
    db.grant_item(A, "table_blue", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "mine_star", "owner_gift", now=NOW, db_path=path)
    db.equip_item(A, rid(), "table", "table_blue", now=NOW + 1, db_path=path)
    db.equip_item(A, rid(), "mine_icons", "mine_star", now=NOW + 2, db_path=path)

    # Игрок получил только две части «Листопада» (рубашка и фишки)
    db.grant_item(A, "back_leaves", "collection", now=NOW, db_path=path)
    db.grant_item(A, "chip_leaf", "collection", now=NOW, db_path=path)

    r = db.equip_collection(A, rid(), "leaves", now=NOW + 10, db_path=path)
    check("а) код коллекции в ответе", r["collection"], "leaves")
    check("а) надетые части в порядке parts", r["equipped_parts"], ["back_leaves", "chip_leaf"])
    check("а) рубашка надета", r["equipped"]["card_back"], "back_leaves")
    check("а) фишка надета", r["equipped"]["chip"], "chip_leaf")
    check("а) стол остался нетронутым", r["equipped"]["table"], "table_blue")
    check("а) мины остались нетронутыми", r["equipped"]["mine_icons"], "mine_star")
    check("а) остальные слоты стартовые", r["equipped"]["avatar_frame"], "frame_plain")
    check("а) не повтор", r["replayed"], False)

    # ================= б) «Основатель»: выбор последней части в слоте =================
    path = new_db()
    add_player(path, A)
    # Получены только Камень (badge) и Колонна (avatar_frame)
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)
    db.grant_item(A, "ref_beacon", "collection", now=NOW, db_path=path)

    r1 = db.equip_collection(A, rid(), "founder", now=NOW + 5, db_path=path)
    check("б1) части: Камень и Колонна", r1["equipped_parts"], ["ref_scout", "ref_beacon"])
    check("б1) значок Камень", r1["equipped"]["badge"], "ref_scout")
    check("б1) рамка Колонна", r1["equipped"]["avatar_frame"], "ref_beacon")

    # Теперь получена также Арка (avatar_frame)
    db.grant_item(A, "ref_arch", "collection", now=NOW + 6, db_path=path)
    r2 = db.equip_collection(A, rid(), "founder", now=NOW + 10, db_path=path)
    check("б2) части: Камень и Арка", r2["equipped_parts"], ["ref_scout", "ref_arch"])
    check("б2) значок Камень", r2["equipped"]["badge"], "ref_scout")
    check("б2) рамка Арка, а не Колонна", r2["equipped"]["avatar_frame"], "ref_arch")

    # ================= в) ни одной части: 409 nothing_to_equip и откат =================
    path = new_db()
    add_player(path, A)
    # Игрок надел рубашку «Полночь»
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    db.equip_item(A, rid(), "card_back", "back_midnight", now=NOW + 1, db_path=path)
    before_equipped = sql(path, "SELECT slot, item_code FROM cosmetic_equipped WHERE telegram_id = ?", (A,))
    before_actions = sql(path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0]

    # У игрока нет ни одной части коллекции «Дачный сезон»
    raises(cosmetics.NothingToEquip, db.equip_collection, A, rid(), "dacha", now=NOW + 5, db_path=path)

    # Состояние базы абсолютно не изменилось
    after_equipped = sql(path, "SELECT slot, item_code FROM cosmetic_equipped WHERE telegram_id = ?", (A,))
    after_actions = sql(path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0]
    check("в) надетое не изменилось", after_equipped, before_equipped)
    check("в) действий не добавилось", after_actions, before_actions)

    # ================= г) неизвестная коллекция 404 unknown_collection =================
    path = new_db()
    add_player(path, A)
    raises(cosmetics.UnknownCollection, db.equip_collection, A, rid(), "unknown_col", now=NOW, db_path=path)
    check("г) в cosmetic_actions ничего не записано", sql(path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0], 0)

    # ================= д) повтор с тем же request_id отдаёт тот же ответ без второй записи =================
    path = new_db()
    add_player(path, A)
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)
    req_id = "req-idempotent-001"
    first = db.equip_collection(A, req_id, "founder", now=NOW + 1, db_path=path)
    check("д) первая попытка не replayed", first["replayed"], False)
    check("д) ровно 1 запись в журнале", sql(path, "SELECT COUNT(*) FROM cosmetic_actions WHERE request_id = ?", (req_id,))[0][0], 1)

    # Повтор с тем же request_id
    second = db.equip_collection(A, req_id, "founder", now=NOW + 1, db_path=path)
    check("д) повтор replayed=True", second["replayed"], True)
    check("д) одинаковый состав ответа", dict(second, replayed=False), first)
    check("д) записей всё ещё 1", sql(path, "SELECT COUNT(*) FROM cosmetic_actions WHERE request_id = ?", (req_id,))[0][0], 1)

    # Тот же request_id, но другая коллекция: RequestConflict
    raises(cosmetics.RequestConflict, db.equip_collection, A, req_id, "leaves", now=NOW + 2, db_path=path)

    # ================= е) вторая смена в ту же секунду: TooFast как у equip =================
    path = new_db()
    add_player(path, A)
    add_player(path, B)
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)
    db.grant_item(A, "back_midnight", "owner_gift", now=NOW, db_path=path)
    db.grant_item(B, "ref_scout", "collection", now=NOW, db_path=path)

    t_base = NOW + 100
    db.equip_collection(A, rid(), "founder", now=t_base, db_path=path)
    # Попытка снова сменить набор в ту же секунду
    raises(cosmetics.TooFast, db.equip_collection, A, rid(), "founder", now=t_base, db_path=path)
    # Попытка обычной смены equip_item в ту же секунду тоже блокируется
    raises(cosmetics.TooFast, db.equip_item, A, rid(), "card_back", "back_midnight", now=t_base, db_path=path)
    # Другой игрок в ту же секунду не блокируется
    db.equip_collection(B, rid(), "founder", now=t_base, db_path=path)

    # Через 1 секунду сменить можно
    db.equip_collection(A, rid(), "founder", now=t_base + 1, db_path=path)

    # Обратная связь: equip_item блокирует equip_collection в ту же секунду
    t_base2 = t_base + 10
    db.equip_item(A, rid(), "card_back", "back_midnight", now=t_base2, db_path=path)
    raises(cosmetics.TooFast, db.equip_collection, A, rid(), "founder", now=t_base2, db_path=path)

    # ================= ж) недоступный предмет (available false) не надевается =================
    path = new_db()
    add_player(path, A)
    check("ref_comet действительно available=False", cosmetics.item("ref_comet")["available"], False)
    db.grant_item(A, "ref_comet", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)

    mock_colls = dict(cosmetic_sets.COLLECTIONS)
    mock_colls["with_comet"] = {
        "name": "Тест с кометой",
        "parts": ("ref_comet", "ref_scout"),
        "source": "referral",
        "season": None,
        "how": "тест",
    }
    mock_colls["only_comet"] = {
        "name": "Только комета",
        "parts": ("ref_comet",),
        "source": "referral",
        "season": None,
        "how": "тест",
    }
    with mock.patch.object(cosmetic_sets, "COLLECTIONS", mock_colls):
        # Если в коллекции только недоступный ref_comet, надевать нечего
        raises(cosmetics.NothingToEquip, db.equip_collection, A, rid(), "only_comet", now=NOW + 5, db_path=path)

        # Если в коллекции есть и доступный ref_scout, надевается только ref_scout
        res_mixed = db.equip_collection(A, rid(), "with_comet", now=NOW + 10, db_path=path)
        check("ж) наделся только доступный ref_scout", res_mixed["equipped_parts"], ["ref_scout"])
        check("ж) значок ref_scout", res_mixed["equipped"]["badge"], "ref_scout")
        check("ж) краш остался стартовым (не ref_comet)", res_mixed["equipped"]["crash"], "crash_line")

    # ================= з) весь набор через HTTP-маршрут и неверное тело 400 =================
    path = new_db()
    add_player(path, A)
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)
    db.grant_item(A, "ref_arch", "collection", now=NOW, db_path=path)

    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({
        "WRITE_RATE_PER_SEC": "100", "WRITE_RATE_BURST": "100",
        "READ_RATE_PER_SEC": "100", "READ_RATE_BURST": "100",
    }), clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth_h(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    # Валидация HTTP-тела: 400 invalid_request
    # 1. Не JSON
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), content=b"not json")
    check("з) не json", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 2. Пустой словарь
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={})
    check("з) пустое тело", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 3. Нет collection
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": rid()})
    check("з) нет collection", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 4. Нет request_id
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"collection": "founder"})
    check("з) нет request_id", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 5. Лишнее поле
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": rid(), "collection": "founder", "extra": 1})
    check("з) лишнее поле", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 6. collection не строка (число, null, список)
    for bad_c in (123, None, ["founder"], {}):
        r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": rid(), "collection": bad_c})
        check("з) плохой тип collection", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 7. Неверный формат request_id
    for bad_rid in ("", "bad id with spaces", "x" * 100):
        r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": bad_rid, "collection": "founder"})
        check("з) плохой request_id", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 8. Без авторизации 401
    r = client.post("/api/cosmetics/equip-set", json={"request_id": rid(), "collection": "founder"})
    check("з) без авторизации", (r.status_code, r.json()), (401, {"detail": "Unauthorized"}))

    # 9. Успешный HTTP POST /api/cosmetics/equip-set
    http_rid = rid()
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": http_rid, "collection": "founder"})
    check("з) успешный вызов HTTP 200", r.status_code, 200)
    data = r.json()
    check("з) HTTP collection", data["collection"], "founder")
    check("з) HTTP parts", data["equipped_parts"], ["ref_scout", "ref_arch"])
    check("з) HTTP badge", data["equipped"]["badge"], "ref_scout")
    check("з) HTTP frame", data["equipped"]["avatar_frame"], "ref_arch")
    check("з) HTTP replayed", data["replayed"], False)

    # 10. HTTP повтор
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": http_rid, "collection": "founder"})
    check("з) HTTP повтор 200", r.status_code, 200)
    check("з) HTTP повтор replayed", r.json()["replayed"], True)

    # 11. HTTP 404 unknown_collection
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": rid(), "collection": "no_such_col"})
    check("з) HTTP 404", (r.status_code, r.json()), (404, {"detail": "unknown_collection"}))

    # 12. HTTP 409 nothing_to_equip
    r = client.post("/api/cosmetics/equip-set", headers=auth_h(A), json={"request_id": rid(), "collection": "leaves"})
    check("з) HTTP 409", (r.status_code, r.json()), (409, {"detail": "nothing_to_equip"}))

    # ================= и) форма ответа equip-set и патина =================
    path = new_db()
    # Игрок зарегистрирован 100 дней назад
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, 100000, 100, ?, ?, 0, 0, 0, 0)", (A, NOW, NOW - 100 * 86400))
    # Наденем вещи с патиной: chip_patina и frame_patina
    db.grant_item(A, "chip_patina", "owner_gift", now=NOW, db_path=path)
    db.grant_item(A, "frame_patina", "owner_gift", now=NOW, db_path=path)
    db.equip_item(A, rid(), "chip", "chip_patina", now=NOW + 1, db_path=path)
    db.equip_item(A, rid(), "avatar_frame", "frame_patina", now=NOW + 2, db_path=path)

    # Получим значок «Основателя» (badge) и наденем набор founder
    # frame_patina останется надетой в avatar_frame, chip_patina в chip
    db.grant_item(A, "ref_scout", "collection", now=NOW, db_path=path)
    r_pat = db.equip_collection(A, rid(), "founder", now=NOW + 10, db_path=path)
    check("и) форма: collection", r_pat["collection"], "founder")
    check("и) форма: equipped_parts", r_pat["equipped_parts"], ["ref_scout"])
    check("и) форма: equipped", set(r_pat["equipped"].keys()), set(cosmetics.SLOTS))
    check("и) форма: replayed", r_pat["replayed"], False)
    check("и) тип collection", type(r_pat["collection"]), str)
    check("и) тип equipped_parts", type(r_pat["equipped_parts"]), list)
    check("и) тип equipped", type(r_pat["equipped"]), dict)
    check("и) тип replayed", type(r_pat["replayed"]), bool)
    check("и) тип patina", type(r_pat["patina"]), dict)
    check("и) тип patina_info", type(r_pat["patina_info"]), dict)
    check("и) стадия рамки патины", r_pat["patina"]["avatar_frame"], 4)
    check("и) засечки жетона патины", r_pat["patina_info"]["chip_notches"], 0)

    print("test_equip_set OK")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
