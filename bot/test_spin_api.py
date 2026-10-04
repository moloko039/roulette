import json
import os
import sqlite3
import tempfile
import threading
import time
from unittest import mock

from fastapi.testclient import TestClient

from api import create_app
from db import init_db
from roulette import MAX_SAFE_INT
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://moloko039.github.io"
URL = "/api/roulette/spin"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def auth(user_id, token=TOKEN):
    return {"Authorization": "tma " + make_init_data(token, user_id=user_id, auth_date=int(time.time()))}


def bet(t, v=None, a=10):
    return {"type": t, "value": v, "amount": a}


def spin(client, user, request_id, bets, fixed=None):
    body = {"request_id": request_id, "bets": bets}
    if fixed is None:
        return client.post(URL, json=body, headers=auth(user))
    with mock.patch("secrets.randbelow", lambda n: fixed):
        return client.post(URL, json=body, headers=auth(user))


def balance(client, user):
    return client.get("/api/me", headers=auth(user)).json()["balance"]


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    app = create_app(TOKEN, [ORIGIN], db_path=path)
    client = TestClient(app)

    # успех со стартового баланса 1000 (число подставлено: 17, чёрное)
    r = spin(client, 1, "request-aaaa-0001", [bet("number", 17, 10), bet("black", None, 20)], fixed=17)
    check("статус", r.status_code, 200)
    body = r.json()
    check("ответ", body, {"number": 17, "stake_total": 30, "payout_total": 400, "net": 370,
                          "balance": 1370, "replayed": False})
    check("баланс = 1000 + net", balance(client, 1), 1000 + body["net"])

    # настоящая случайность: число 0-36 и баланс сходится
    r = client.post(URL, json={"request_id": "request-aaaa-0002", "bets": [bet("red", None, 100)]}, headers=auth(1))
    body = r.json()
    assert 0 <= body["number"] <= 36
    check("баланс после случайного раунда", balance(client, 1), 1370 + body["net"])

    # ставка ровно на весь баланс проходит, на 1 больше — 409 и баланс не меняется
    cur = balance(client, 1)
    r = spin(client, 1, "request-aaaa-0003", [bet("red", None, cur + 1)])
    check("на 1 больше", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    check("баланс не изменился", balance(client, 1), cur)
    r = spin(client, 1, "request-aaaa-0004", [bet("number", 5, cur)], fixed=5)
    check("весь баланс", (r.status_code, r.json()["balance"]), (200, cur * 36))

    # повтор того же request_id: баланс не меняется, replayed=true, то же число
    first = spin(client, 2, "request-bbbb-0001", [bet("red", None, 100)], fixed=1).json()
    bal = balance(client, 2)
    again = spin(client, 2, "request-bbbb-0001", [bet("red", None, 100)], fixed=2).json()
    check("повтор: replayed", again["replayed"], True)
    check("повтор: то же число", again["number"], first["number"])
    check("повтор: тот же итог", (again["stake_total"], again["payout_total"], again["net"]),
          (first["stake_total"], first["payout_total"], first["net"]))
    check("повтор: баланс", (again["balance"], balance(client, 2)), (bal, bal))
    check("раунд один", sql(path, "SELECT COUNT(*) FROM roulette_rounds WHERE telegram_id = 2")[0][0], 1)

    # тот же request_id у ДРУГОГО игрока — новый раунд
    other = spin(client, 3, "request-bbbb-0001", [bet("red", None, 100)], fixed=1).json()
    check("другой игрок", (other["replayed"], other["balance"]), (False, 1100))

    # невалидные ставки: 400, тело без подробностей, баланс не меняется
    bal = balance(client, 4)
    bad_bodies = [
        {"request_id": "request-cccc-0001", "bets": []},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, True)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, 1.5)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, "10")]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, -1)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, 0)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5), bet("number", 5)]},
        {"request_id": "request-cccc-0001", "bets": [bet("red", 1)]},
        {"request_id": "request-cccc-0001", "bets": [bet("split", 1)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", 5, MAX_SAFE_INT + 1)]},
        {"request_id": "request-cccc-0001", "bets": [bet("number", n % 37, 1) for n in range(48)]},
        {"request_id": "короткий", "bets": [bet("red", None, 1)]},
        {"request_id": "x", "bets": [bet("red", None, 1)]},
        {"request_id": 12345678, "bets": [bet("red", None, 1)]},
        {"bets": [bet("red", None, 1)]},
        {"request_id": "request-cccc-0001"},
        {"request_id": "request-cccc-0001", "bets": [bet("red", None, 1)], "number": 7},
        {"request_id": "request-cccc-0001", "bets": [bet("red", None, 1)], "user_id": 5},
        {"request_id": "request-cccc-0001", "bets": "red"},
        [1, 2, 3],
        "строка",
        None,
    ]
    for b in bad_bodies:
        r = client.post(URL, json=b, headers=auth(4))
        check("400 для " + json.dumps(b, ensure_ascii=False)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_bets"}))
    for raw in [b"", b"not json", b"{", b'{"request_id": "request-cccc-0001", "bets": [{"type": "number", "value": 5, "amount": 1e3}]}']:
        r = client.post(URL, content=raw, headers=auth(4))
        check("400 для сырого тела", (r.status_code, r.json()), (400, {"detail": "invalid_bets"}))
    r = client.post(URL, content=b"x" * 70000, headers=auth(4))
    check("слишком большое тело", (r.status_code, r.json()), (413, {"detail": "payload_too_large"}))
    check("баланс не менялся", balance(client, 4), bal)

    # без подписи и с чужой подписью: 401 (раньше проверки тела)
    good = {"request_id": "request-dddd-0001", "bets": [bet("red", None, 1)]}
    for h in [{}, {"Authorization": ""}, {"Authorization": "tma garbage"}, auth(1, token="999:OTHER")]:
        r = client.post(URL, json=good, headers=h)
        check("401", (r.status_code, r.json()), (401, {"detail": "Unauthorized"}))
    r = client.post(URL, content=b"not json")
    check("401 раньше 400", r.status_code, 401)

    # клиент не может подсунуть число, баланс или id игрока
    r = client.post(URL, json={**good, "number": 0, "balance": 99999, "telegram_id": 1}, headers=auth(5))
    check("лишние поля", r.status_code, 400)
    check("id только из подписи", sql(path, "SELECT COUNT(*) FROM roulette_rounds WHERE request_id = 'request-dddd-0001'")[0][0], 0)

    # потраченное с учётом начисления: в базе 1000, но прошло 5 часов (+500)
    balance(client, 6)  # регистрация
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = 6", (int(time.time()) - 5 * 3600 - 10,))
    r = spin(client, 6, "request-eeee-0001", [bet("red", None, 1500)], fixed=0)
    check("начисленное потрачено", (r.status_code, r.json()["stake_total"], r.json()["balance"]), (200, 1500, 0))
    r = spin(client, 6, "request-eeee-0002", [bet("red", None, 1)], fixed=0)
    check("больше нечего ставить", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))

    # balance_limit: баланс около MAX_SAFE_INT и ставка на число
    balance(client, 7)
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = 7", (MAX_SAFE_INT - 10,))
    r = spin(client, 7, "request-ffff-0001", [bet("number", 5, 1)], fixed=5)
    check("balance_limit", (r.status_code, r.json()), (409, {"detail": "balance_limit"}))
    check("баланс при balance_limit", sql(path, "SELECT balance FROM players WHERE telegram_id = 7")[0][0], MAX_SAFE_INT - 10)
    check("раунд не записан", sql(path, "SELECT COUNT(*) FROM roulette_rounds WHERE telegram_id = 7")[0][0], 0)
    r = spin(client, 7, "request-ffff-0002", [bet("red", None, 1)], fixed=0)
    check("простая ставка рядом с пределом", r.status_code, 200)
    assert 0 <= r.json()["balance"] <= MAX_SAFE_INT

    # CORS: POST и Content-Type разрешены только для нашего origin
    pre = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"}
    r = client.options(URL, headers=pre)
    check("CORS preflight POST", (r.status_code, r.headers.get("access-control-allow-origin")), (200, ORIGIN))
    r = client.options(URL, headers={**pre, "Origin": "https://evil.example"})
    check("CORS чужой origin", r.headers.get("access-control-allow-origin"), None)
    r = client.options(URL, headers={**pre, "Access-Control-Request-Method": "PUT"})
    check("CORS PUT запрещён", r.status_code, 400)

    # ---------- параллельность ----------
    def parallel(requests):
        """requests — список (user, request_id, bets); запускает одновременно, возвращает ответы."""
        results = [None] * len(requests)
        barrier = threading.Barrier(len(requests))

        def worker(i, user, rid, bets):
            c = TestClient(app)
            barrier.wait()
            results[i] = c.post(URL, json={"request_id": rid, "bets": bets}, headers=auth(user))

        threads = [threading.Thread(target=worker, args=(i, *req)) for i, req in enumerate(requests)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    # два запроса по 600 на баланс 1000: ровно один проходит
    for attempt in range(5):
        user = 100 + attempt
        # выпадает всегда 0: обе ставки проигрышные, иначе выигрыш первой ставки позволил бы вторую
        with mock.patch("secrets.randbelow", lambda n: 0):
            res = parallel([(user, "request-par1-000%d" % attempt, [bet("red", None, 600)]),
                            (user, "request-par2-000%d" % attempt, [bet("black", None, 600)])])
        codes = sorted(r.status_code for r in res)
        check("один успех и один 409", codes, [200, 409])
        loser = [r for r in res if r.status_code == 409][0]
        check("409 insufficient_funds", loser.json(), {"detail": "insufficient_funds"})
        check("раундов один", sql(path, "SELECT COUNT(*) FROM roulette_rounds WHERE telegram_id = ?", (user,))[0][0], 1)
        win = [r for r in res if r.status_code == 200][0].json()
        check("баланс сходится", balance(client, user), 1000 + win["net"])

    # два запроса с одним request_id: один раунд, оба ответа с одним числом
    for attempt in range(5):
        user = 200 + attempt
        rid = "request-same-000%d" % attempt
        res = parallel([(user, rid, [bet("red", None, 100)]), (user, rid, [bet("red", None, 100)])])
        check("оба 200", [r.status_code for r in res], [200, 200])
        a, b = res[0].json(), res[1].json()
        check("одно число", a["number"], b["number"])
        check("replayed: один раз", sorted([a["replayed"], b["replayed"]]), [False, True])
        check("раунд один", sql(path, "SELECT COUNT(*) FROM roulette_rounds WHERE telegram_id = ?", (user,))[0][0], 1)
        check("списано один раз", balance(client, user), 1000 + a["net"])
finally:
    os.remove(path)

print("Все проверки прошли")
