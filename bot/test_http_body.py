"""Тесты общего помощника разбора тела запроса read_json_object (bot/web/http.py).

Проверяет:
1. Изолированные проверки read_json_object:
   - успешный разбор с валидацией request_id
   - не dict (список, число, строка, boolean) -> 400 {"detail": "invalid_request"}
   - лишний ключ в теле -> 400 {"detail": "invalid_request"}
   - недостающий обязательный ключ -> 400 {"detail": "invalid_request"}
   - неверный request_id (короткий, спецсимволы, не строка, None) -> 400 {"detail": "invalid_request"}
   - слишком большое тело (Content-Length и chunked) -> 413 {"detail": "payload_too_large"}
   - кривой JSON -> 400 {"detail": "invalid_request"}
   - optional-параметр: допустимые опциональные ключи разрешены, остальные запрещены
   - request_id=False: разбор без request_id
   - keys=(), request_id=False (streak): пустое тело b"" и пустой dict {} разрешены, лишние ключи запрещены
2. Сквозные вызовы через настоящее приложение на переписанных маршрутах:
   - /api/chips/buy
   - /api/gems/invoice
   - /api/cosmetics/equip
   - /api/bloody/start
   - /api/farm/buy
   - /api/streak/claim
   - /api/chat/boost
   - /api/crash/live/cashout
   - /api/crash/live/bet
   - /api/race/live/bet
   - /api/race/play
   - /api/gifts/send
"""
import testenv  # noqa: F401 (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import os
import sqlite3
import tempfile
import time

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

import db
from api import MAX_BODY_BYTES, create_app
from tg_testutil import make_init_data
from web.http import read_json_object

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://example.invalid"
USER_ID = 424242422
VALID_RID = "req-test-12345678"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def make_request(body: bytes, content_length: bool = True, chunked: bool = False):
    """Создаёт тестовый starlette Request с заданным телом."""
    if chunked:
        chunks = [body[i:i + 1024] for i in range(0, len(body), 1024)] or [b""]
        queue = list(chunks)

        async def receive():
            if queue:
                chunk = queue.pop(0)
                return {"type": "http.request", "body": chunk, "more_body": bool(queue)}
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {"type": "http", "headers": []}
    else:
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        headers = [(b"content-length", str(len(body)).encode())] if content_length else []
        scope = {"type": "http", "headers": headers}
    return Request(scope, receive)


# =====================================================================
# 1. Изолированные проверки помощника read_json_object
# =====================================================================

# 1.1 Успешный разбор с точным набором ключей и валидным request_id
body = json.dumps({"request_id": VALID_RID, "kind": "income"}).encode()
res = asyncio.run(read_json_object(make_request(body), {"kind"}))
check("успешный разбор dict", res, {"request_id": VALID_RID, "kind": "income"})

# keys передан явно включая request_id
res = asyncio.run(read_json_object(make_request(body), {"request_id", "kind"}))
check("успешный разбор dict (явный request_id в keys)", res, {"request_id": VALID_RID, "kind": "income"})

# 1.2 Не dict (список, строка, число, boolean)
for not_dict_body in (b"[1, 2, 3]", b'"string"', b"12345", b"true"):
    res = asyncio.run(read_json_object(make_request(not_dict_body), {"kind"}))
    check("не dict: тип JSONResponse", isinstance(res, JSONResponse), True)
    check("не dict: status 400", res.status_code, 400)
    check("не dict: body invalid_request", json.loads(res.body), {"detail": "invalid_request"})

# 1.3 Лишний ключ
body_extra = json.dumps({"request_id": VALID_RID, "kind": "income", "extra": 123}).encode()
res = asyncio.run(read_json_object(make_request(body_extra), {"kind"}))
check("лишний ключ: статус", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.4 Недостающий обязательный ключ
body_missing = json.dumps({"request_id": VALID_RID}).encode()
res = asyncio.run(read_json_object(make_request(body_missing), {"kind"}))
check("недостающий ключ: статус", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# отсутствует request_id при request_id=True
body_no_rid = json.dumps({"kind": "income"}).encode()
res = asyncio.run(read_json_object(make_request(body_no_rid), {"kind"}))
check("нет request_id: статус", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.5 Неверный request_id
for bad_rid in ("short", "bad@char!", 12345678, None, ""):
    body_bad_rid = json.dumps({"request_id": bad_rid, "kind": "income"}).encode()
    res = asyncio.run(read_json_object(make_request(body_bad_rid), {"kind"}))
    check("неверный request_id: " + str(bad_rid), (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.6 Слишком большое тело (> MAX_BODY_BYTES)
large_body = b"x" * (MAX_BODY_BYTES + 1)
# по Content-Length
res = asyncio.run(read_json_object(make_request(large_body, content_length=True), {"kind"}))
check("слишком большое тело (Content-Length): 413", (res.status_code, json.loads(res.body)), (413, {"detail": "payload_too_large"}))
# chunked stream
res = asyncio.run(read_json_object(make_request(large_body, chunked=True), {"kind"}))
check("слишком большое тело (chunked): 413", (res.status_code, json.loads(res.body)), (413, {"detail": "payload_too_large"}))

# 1.7 Кривой JSON
res = asyncio.run(read_json_object(make_request(b"{not-valid-json"), {"kind"}))
check("кривой JSON: 400", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.8 Пустое тело при ожидании JSON
res = asyncio.run(read_json_object(make_request(b""), {"kind"}))
check("пустое тело при обязательных ключах: 400", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.9 Вариация: optional-ключи
# без опционального ключа
b_no_opt = json.dumps({"request_id": VALID_RID, "bet": 100}).encode()
res = asyncio.run(read_json_object(make_request(b_no_opt), {"bet"}, optional={"buy"}))
check("optional: без опционального", res, {"request_id": VALID_RID, "bet": 100})
# с опциональным ключом
b_with_opt = json.dumps({"request_id": VALID_RID, "bet": 100, "buy": "bonus"}).encode()
res = asyncio.run(read_json_object(make_request(b_with_opt), {"bet"}, optional={"buy"}))
check("optional: с опциональным", res, {"request_id": VALID_RID, "bet": 100, "buy": "bonus"})
# с непредусмотренным третьим ключом
b_bad_opt = json.dumps({"request_id": VALID_RID, "bet": 100, "buy": "bonus", "extra": 1}).encode()
res = asyncio.run(read_json_object(make_request(b_bad_opt), {"bet"}, optional={"buy"}))
check("optional: с лишним ключом", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))

# 1.10 Вариация: request_id=False
b_no_rid_needed = json.dumps({"foo": "bar"}).encode()
res = asyncio.run(read_json_object(make_request(b_no_rid_needed), {"foo"}, request_id=False))
check("request_id=False: успешно без request_id", res, {"foo": "bar"})

# 1.11 Вариация streak: keys=(), request_id=False
# пустое тело b""
res = asyncio.run(read_json_object(make_request(b""), (), request_id=False))
check("streak: пустое тело b'' -> {}", res, {})
# пустой объект b"{}"
res = asyncio.run(read_json_object(make_request(b"{}"), (), request_id=False))
check("streak: пустой объект b'{}' -> {}", res, {})
# непустой объект при keys=() -> 400
res = asyncio.run(read_json_object(make_request(b'{"extra": 1}'), (), request_id=False))
check("streak: непустой объект -> 400", (res.status_code, json.loads(res.body)), (400, {"detail": "invalid_request"}))


# =====================================================================
# 2. Сквозные вызовы через настоящее приложение TestClient
# =====================================================================

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "test_app.db")
db.init_db(db_path)

conn = sqlite3.connect(db_path)
conn.execute(
    "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
    "VALUES (?, 100000, 100, 1760000000, 1760000000, 0, 1000, 0, 0)",
    (USER_ID,)
)
conn.commit()
conn.close()

app = create_app(TOKEN, [ORIGIN], db_path=db_path)
client = TestClient(app, raise_server_exceptions=False)


def auth():
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=USER_ID, auth_date=int(time.time()))}


# Набор тестируемых переписанных маршрутов и их минимально валидные тела
TEST_ROUTES = [
    ("/api/chips/buy", {"request_id": VALID_RID, "pack_code": "p1"}),
    ("/api/gems/invoice", {"request_id": VALID_RID, "pack_code": "pack_100"}),
    ("/api/cosmetics/equip", {"request_id": VALID_RID, "slot": "avatar", "code": "classic"}),
    ("/api/bloody/start", {"request_id": VALID_RID, "bet": 100}),
    ("/api/farm/buy", {"request_id": VALID_RID, "kind": "income"}),
    ("/api/chat/boost", {"request_id": VALID_RID}),
    ("/api/crash/live/cashout", {"request_id": VALID_RID}),
    ("/api/crash/live/bet", {"request_id": VALID_RID, "bet": 100}),
    ("/api/race/live/bet", {"request_id": VALID_RID, "lane": 0, "bet": 100}),
    ("/api/race/play", {"request_id": VALID_RID, "lanes": 3, "lane": 0, "bet": 100}),
    ("/api/gifts/send", {"request_id": VALID_RID, "ref": "user123", "item_code": "classic"}),
]

for url, valid_body in TEST_ROUTES:
    # 2.1 Тело больше лимита: 413 {"detail": "payload_too_large"}
    r = client.post(url, headers=auth(), content=b"x" * (MAX_BODY_BYTES + 1))
    check(f"{url}: 413 при Content-Length > MAX", (r.status_code, r.json()), (413, {"detail": "payload_too_large"}))

    # 2.2 Кривой JSON: 400 {"detail": "invalid_request"}
    r = client.post(url, headers=auth(), content=b"{broken-json")
    check(f"{url}: 400 при кривом JSON", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 2.3 Не dict (JSON список): 400 {"detail": "invalid_request"}
    r = client.post(url, headers=auth(), json=[1, 2, 3])
    check(f"{url}: 400 при теле-списке", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 2.4 Лишний ключ: 400 {"detail": "invalid_request"}
    body_with_extra = dict(valid_body, unexpected_extra_key="boom")
    r = client.post(url, headers=auth(), json=body_with_extra)
    check(f"{url}: 400 при лишнем ключе", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 2.5 Неверный request_id: 400 {"detail": "invalid_request"}
    body_short_rid = dict(valid_body, request_id="short")
    r = client.post(url, headers=auth(), json=body_short_rid)
    check(f"{url}: 400 при коротком request_id", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

    # 2.6 Недостающий ключ request_id: 400 {"detail": "invalid_request"}
    body_no_rid = {k: v for k, v in valid_body.items() if k != "request_id"}
    r = client.post(url, headers=auth(), json=body_no_rid)
    check(f"{url}: 400 при отсутствии request_id", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

# Дополнительно: /api/streak/claim (без request_id, ожидает пустое тело / {})
r = client.post("/api/streak/claim", headers=auth(), content=b"x" * (MAX_BODY_BYTES + 1))
check("/api/streak/claim: 413", (r.status_code, r.json()), (413, {"detail": "payload_too_large"}))

r = client.post("/api/streak/claim", headers=auth(), content=b"{broken")
check("/api/streak/claim: 400 кривой JSON", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

r = client.post("/api/streak/claim", headers=auth(), json={"unexpected": 1})
check("/api/streak/claim: 400 лишний ключ", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))

# Пустое тело b"" на streak принимается
r = client.post("/api/streak/claim", headers=auth(), content=b"")
check("/api/streak/claim: пустое тело статус", r.status_code, 200)

print("test_http_body OK")
