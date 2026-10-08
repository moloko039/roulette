"""Подарки косметикой (ECONOMY_ADDITIONS.md, п. 2): получатель только из своей беседы по непрозрачной метке, только предметы за кристаллы, проверки до списания, суточный лимит, идемпотентность,
гонки, уведомление, «подарок от …», невозможность передарить, /mydata, /deletemydata, очистка, охранник кошелька."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

import balance_guard
import cosmetics
import db
import economy_config
import wallet
from api import create_app
from features import gifts_db
from roulette import RequestConflict
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
A, B, C, D = 424242422, 424242423, 424242424, 424242425
CHAT = "chat-gifts-1"

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


def new_db(gems_a=1000):
    counter[0] += 1
    path = os.path.join(tmp, "g%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    for uid, name in ((A, "Алиса"), (B, "Боб"), (C, "Вера")):
        sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp) VALUES (?, 5000, 100, ?, ?, 777, 300)", (uid, NOW + 10 * 86400, NOW - 86400))
        sql(path, "INSERT INTO chat_members VALUES (?, ?, ?, ?, ?)", (CHAT, uid, name, NOW - 100, NOW - 10 * (uid - A)))
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 100, 100, ?, ?)", (D, NOW + 10 * 86400, NOW - 86400))
    sql(path, "INSERT INTO chat_members VALUES ('other-chat', ?, 'Дима', ?, ?)", (D, NOW - 100, NOW - 1))
    if gems_a:
        db.owner_grant_gems(A, gems_a, "seed", now=NOW, db_path=path)
    return path


def ref(uid, chat=CHAT):
    return gifts_db.member_ref(chat, uid)


def gems_ledger_ok(path):
    rows = dict(sql(path, "SELECT telegram_id, SUM(delta) FROM gems_ledger GROUP BY telegram_id"))
    bal = dict(sql(path, "SELECT telegram_id, gems FROM gem_balances"))
    return all(bal.get(u, 0) == s for u, s in rows.items())


try:
    # ---- метка
    check("метка: 32 hex, без id и беседы, стабильна, разная для разных участников и бесед", (len(ref(B)), ref(B) == ref(B), ref(B) != ref(C), ref(B) != ref(B, "x"), str(B) in ref(B), CHAT in ref(B)), (32, True, True, True, False, False))
    check("причина gift_purchase это сток, источник подарка в списке", (economy_config.GEM_REASONS["gift_purchase"], "gift" in cosmetics.SOURCES), ("sink", True))

    # ---- получатели
    path = new_db()
    r = db.gift_recipients(A, CHAT, now=NOW, db_path=path)
    check("получатели: Боб и Вера, без самого игрока и без чужой беседы, свежие первыми", ([x["name"] for x in r["recipients"]], r["daily_left"], r["gems"]), (["Боб", "Вера"], 5, 1000))
    check("в ответе нет Telegram id", str(B) in json.dumps(r), False)
    raises(gifts_db.NoChat, db.gift_recipients, A, None, db_path=path)
    raises(gifts_db.NotInChat, db.gift_recipients, 999, CHAT, db_path=path)
    raises(gifts_db.NotInChat, db.gift_recipients, D, CHAT, db_path=path)          # из другой беседы

    # ---- отправка
    before = sql(path, "SELECT balance, xp, total_staked FROM players WHERE telegram_id = ?", (A,))[0]
    res, notice = db.send_gift(A, CHAT, "gift-req-000001", ref(B), "table_blue", "Алиса", now=NOW, db_path=path)
    check("подарок: ответ", (res["item_code"], res["price"], res["recipient"], res["gems"], res["daily_left"], res["replayed"]), ("table_blue", {"currency": "gems", "amount": 150}, "Боб", 850, 4, False))
    check("уведомление для бота", notice, {"to_user": B, "from_name": "Алиса", "item_name": "Лагуна"})
    check("предмет у получателя с источником gift, у отправителя его нет", (sql(path, "SELECT source FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'table_blue'", (B,)), sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ?", (A,))[0][0]), ([("gift",)], 0))
    check("журнал кристаллов: списание gift_purchase", sql(path, "SELECT delta, reason, ref FROM gems_ledger WHERE delta < 0"), [(-150, "gift_purchase", "gift:gift-req-000001")])
    check("фишки, опыт и ставки отправителя не тронуты", sql(path, "SELECT balance, xp, total_staked FROM players WHERE telegram_id = ?", (A,))[0], before)
    check("запись подарка", sql(path, "SELECT from_user, to_user, from_name, item_code, gems FROM gifts"), [(A, B, "Алиса", "table_blue", 150)])
    mine = db.cosmetics_mine(B, db_path=path)
    check("у получателя в списке «подарок от»", [(o["code"], o["source"], o.get("gift_from")) for o in mine["owned"] if not o["code"].endswith("_patina")], [("table_blue", "gift", "Алиса")])
    check("у обычной покупки поля gift_from нет", "gift_from" in db.cosmetics_mine(A, db_path=path)["owned"], False)
    res2, notice2 = db.send_gift(A, CHAT, "gift-req-000001", ref(B), "table_blue", "Алиса", now=NOW + 1, db_path=path)
    check("повтор того же request_id: тот же ответ, без нового списания и без уведомления", (res2["replayed"], notice2, db.gems_state(A, path)["gems"]), (True, None, 850))
    raises(RequestConflict, db.send_gift, A, CHAT, "gift-req-000001", ref(C), "table_blue", "Алиса", now=NOW + 2, db_path=path)
    raises(RequestConflict, db.send_gift, A, CHAT, "gift-req-000001", ref(B), "crash_neon", "Алиса", now=NOW + 2, db_path=path)

    # ---- отказы до списания
    g0 = db.gems_state(A, path)["gems"]
    raises(cosmetics.AlreadyOwned, db.send_gift, A, CHAT, "gift-req-000002", ref(B), "table_blue", "Алиса", now=NOW + 3, db_path=path)
    raises(gifts_db.SelfGift, db.send_gift, A, CHAT, "gift-req-000003", ref(A), "crash_neon", "Алиса", now=NOW + 3, db_path=path)
    raises(gifts_db.UnknownRecipient, db.send_gift, A, CHAT, "gift-req-000004", "0" * 32, "crash_neon", "Алиса", now=NOW + 3, db_path=path)
    raises(gifts_db.UnknownRecipient, db.send_gift, A, CHAT, "gift-req-000005", ref(D, "other-chat"), "crash_neon", "Алиса", now=NOW + 3, db_path=path)   # метка чужой беседы
    raises(gifts_db.NoChat, db.send_gift, A, None, "gift-req-000006", ref(B), "crash_neon", "Алиса", now=NOW + 3, db_path=path)
    raises(gifts_db.NotInChat, db.send_gift, D, CHAT, "gift-req-000007", ref(B), "crash_neon", "Дима", now=NOW + 3, db_path=path)
    raises(cosmetics.NotForGems, db.send_gift, A, CHAT, "gift-req-000008", ref(C), "chip_ring", "Алиса", now=NOW + 3, db_path=path)      # за фишки: дарить нельзя
    raises(cosmetics.ItemUnavailable, db.send_gift, A, CHAT, "gift-req-000009", ref(C), "back_ember", "Алиса", now=NOW + 3, db_path=path)
    raises(cosmetics.ItemUnavailable, db.send_gift, A, CHAT, "gift-req-000010", ref(C), "chip_plain", "Алиса", now=NOW + 3, db_path=path)   # стартовый
    raises(cosmetics.UnknownItem, db.send_gift, A, CHAT, "gift-req-000011", ref(C), "nope", "Алиса", now=NOW + 3, db_path=path)
    raises(cosmetics.UnknownItem, db.send_gift, A, CHAT, "gift-req-000012", ref(C), "test_1star", "Алиса", now=NOW + 3, db_path=path)
    raises(ValueError, db.send_gift, A, CHAT, "gift-req-000013", 5, "crash_neon", "Алиса", now=NOW + 3, db_path=path)
    check("отказы ничего не списали и не создали", (db.gems_state(A, path)["gems"], sql(path, "SELECT COUNT(*) FROM gifts")[0][0], sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE item_code NOT LIKE '%_patina'")[0][0], gems_ledger_ok(path)), (g0, 1, 1, True))
    # нехватка кристаллов
    path2 = new_db(gems_a=100)
    raises(wallet.InsufficientGems, db.send_gift, A, CHAT, "gift-req-000020", ref(B), "table_blue", "Алиса", now=NOW, db_path=path2)
    check("нехватка: ничего не изменилось", (db.gems_state(A, path2)["gems"], sql(path2, "SELECT COUNT(*) FROM gifts")[0][0]), (100, 0))
    # получатель не может передарить подаренное: подарить можно только предмет из магазина, но не «тот же экземпляр»; у получателя нет пути передачи
    check("у получателя подарок есть, но передачи нет (нет такого API и записи у отправителя)", (db.cosmetics_mine(B, db_path=path)["owned"][0]["code"], sql(path, "SELECT COUNT(*) FROM gifts WHERE from_user = ?", (B,))[0][0]), ("table_blue", 0))

    # ---- суточный лимит
    path = new_db(gems_a=2000)
    for i, code in enumerate(("table_blue", "crash_neon", "back_midnight", "keno_hex")):
        db.send_gift(A, CHAT, "gift-lim-%06d" % i, ref(B), code, "Алиса", now=NOW + i, db_path=path)
    db.send_gift(A, CHAT, "gift-lim-000010", ref(C), "table_blue", "Алиса", now=NOW + 10, db_path=path)
    check("лимит: пять подарков за сутки исчерпаны", db.gift_recipients(A, CHAT, now=NOW + 20, db_path=path)["daily_left"], 0)
    raises(gifts_db.GiftDailyLimit, db.send_gift, A, CHAT, "gift-lim-000011", ref(C), "crash_neon", "Алиса", now=NOW + 20, db_path=path)
    check("через сутки лимит снова доступен", db.gift_recipients(A, CHAT, now=NOW + 86400 + 100, db_path=path)["daily_left"], 5)

    # ---- гонки
    path = new_db(gems_a=1000)
    gate = threading.Barrier(20)

    def race(i):
        gate.wait()
        try:
            return db.send_gift(A, CHAT, "gift-race-%05d" % i, ref(B), "table_blue", "Алиса", now=NOW, db_path=path)[0]["gems"]
        except cosmetics.CosmeticsError as exc:
            return exc.code
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(race, range(20)))
    check("20 параллельных подарков одного предмета: один успешный, остальные «уже есть»", (sum(1 for x in res if isinstance(x, int)), sorted({x for x in res if not isinstance(x, int)}), db.gems_state(A, path)["gems"]), (1, ["already_owned"], 850))
    check("инвариант журнала кристаллов", gems_ledger_ok(path), True)
    check("статически чисто: кристаллы пишет только wallet", balance_guard.violations(), [])

    # ---- API
    path = new_db(gems_a=1000)
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))
    client = TestClient(create_app(TOKEN, [], db_path=path))
    client.app.state.application = SimpleNamespace(bot=FakeBot())

    def auth(uid=A, chat=CHAT, name="Алиса"):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=name, chat_instance=chat, chat_type="group")}
    check("без подписи: 401", (client.get("/api/gifts/recipients").status_code, client.post("/api/gifts/send", json={}).status_code), (401, 401))
    r = client.get("/api/gifts/recipients", headers=auth())
    check("получатели через API", (r.status_code, [x["name"] for x in r.json()["recipients"]], r.json()["gems"]), (200, ["Боб", "Вера"], 1000))
    refs = {x["name"]: x["ref"] for x in r.json()["recipients"]}
    body = {"request_id": "gift-api-000001", "ref": refs["Боб"], "item_code": "crash_neon"}
    r = client.post("/api/gifts/send", headers=auth(), json=body)
    check("подарок через API", (r.status_code, sorted(r.json()), r.json()["gems"], r.json()["recipient"]), (200, ["daily_left", "gems", "item_code", "price", "recipient", "replayed"], 900, "Боб"))
    check("получатель уведомлён ботом", (len(sent), sent[0][0], "Алиса" in sent[0][1], "Неон" in sent[0][1] or "краш" in sent[0][1].lower() or "«" in sent[0][1]), (1, B, True, True))
    check("повтор: replayed и второго уведомления нет", (client.post("/api/gifts/send", headers=auth(), json=body).json()["replayed"], len(sent)), (True, 1))
    check("уже есть: 409", (lambda x: (x.status_code, x.json()))(client.post("/api/gifts/send", headers=auth(), json=dict(body, request_id="gift-api-000002"))), (409, {"detail": "already_owned"}))
    check("метка чужой беседы: 404 unknown_recipient", (lambda x: (x.status_code, x.json()))(client.post("/api/gifts/send", headers=auth(), json={"request_id": "gift-api-000003", "ref": "f" * 32, "item_code": "keno_hex"})), (404, {"detail": "unknown_recipient"}))
    check("самоподарок: 409 self_gift", (lambda x: (x.status_code, x.json()))(client.post("/api/gifts/send", headers=auth(), json={"request_id": "gift-api-000004", "ref": gifts_db.member_ref(CHAT, A), "item_code": "keno_hex"})), (409, {"detail": "self_gift"}))
    check("предмет за фишки: 409 not_for_gems", (lambda x: (x.status_code, x.json()))(client.post("/api/gifts/send", headers=auth(), json={"request_id": "gift-api-000005", "ref": refs["Вера"], "item_code": "chip_ring"})), (409, {"detail": "not_for_gems"}))
    check("неизвестный предмет: 404", client.post("/api/gifts/send", headers=auth(), json={"request_id": "gift-api-000006", "ref": refs["Вера"], "item_code": "nope"}).status_code, 404)
    check("не из беседы (личный запуск): 409 no_chat", (lambda x: (x.status_code, x.json()))(client.post("/api/gifts/send", headers={"Authorization": "tma " + make_init_data(TOKEN, user_id=A, auth_date=int(time.time()), first_name="Алиса")}, json={"request_id": "gift-api-000007", "ref": refs["Вера"], "item_code": "keno_hex"})), (409, {"detail": "no_chat"}))
    for bad in ({}, {"request_id": "gift-api-000008"}, dict(body, extra=1), dict(body, request_id="x"), dict(body, ref=5), dict(body, item_code=5)):
        check("400 на %s" % json.dumps(bad)[:40], client.post("/api/gifts/send", headers=auth(), json=bad).status_code, 400)
    nogems = client.post("/api/gifts/send", headers=auth(B, name="Боб"), json={"request_id": "gift-api-000009", "ref": gifts_db.member_ref(CHAT, C), "item_code": "keno_hex"})
    check("у отправителя нет кристаллов: 409", (nogems.status_code, nogems.json()), (409, {"detail": "insufficient_gems"}))

    # ---- /mydata, /deletemydata, очистка
    export_b = db.get_player_export(B, db_path=path)
    check("выгрузка получателя: полученный подарок с именем дарителя, без id", (export_b["gifts"]["received"][0]["from"], export_b["gifts"]["received"][0]["item"], str(A) in json.dumps(export_b["gifts"])), ("Алиса", "crash_neon", False))
    export_a = db.get_player_export(A, db_path=path)
    check("выгрузка отправителя: отправленный подарок без получателя", (export_a["gifts"]["sent"][0]["item"], "Боб" in json.dumps(export_a["gifts"]), str(B) in json.dumps(export_a["gifts"])), ("crash_neon", False, False))
    db.delete_player_data(A, db_path=path)
    check("удаление отправителя: запись остаётся без его id и имени, предмет у получателя на месте", (sql(path, "SELECT from_user, from_name FROM gifts"), sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ?", (B,))[0][0]), ([(0, "")], 1))
    owned_B = [i for i in db.cosmetics_mine(B, db_path=path)["owned"] if not i["code"].endswith("_patina")]
    check("после этого у получателя «подарок от» пуст, но предмет есть", owned_B[0].get("gift_from"), "")
    db.delete_player_data(B, db_path=path)
    check("удаление получателя: запись подарка и предметы удалены", (sql(path, "SELECT COUNT(*) FROM gifts")[0][0], sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ?", (B,))[0][0]), (0, 0))
    path = new_db(gems_a=500)
    db.send_gift(A, CHAT, "gift-old-000001", ref(B), "keno_hex", "Алиса", now=NOW, db_path=path)
    db.purge_old_data(now=NOW + (cosmetics.PURCHASE_RETENTION_DAYS + 1) * 86400, db_path=path)
    check("очистка: подарок старше срока хранения удалён, предмет остаётся", (sql(path, "SELECT COUNT(*) FROM gifts")[0][0], sql(path, "SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ?", (B,))[0][0]), (0, 1))
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
