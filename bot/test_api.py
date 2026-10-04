import os
import tempfile
import time

from fastapi.testclient import TestClient

from api import create_app
from db import init_db
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://moloko039.github.io"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def auth(user_id, token=TOKEN, auth_date=None):
    data = make_init_data(token, user_id=user_id, auth_date=auth_date or int(time.time()))
    return {"Authorization": "tma " + data}


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))

    # новый игрок: 1000 и 100
    r = client.get("/api/me", headers=auth(1))
    check("статус", r.status_code, 200)
    body = r.json()
    check("новый игрок", (body["balance"], body["rate"]), (1000, 100))
    assert 1 <= body["seconds_to_next"] <= 60 and body["seconds_to_next"] == body["farm"]["next_tick_in_s"], body   # до следующей минуты

    # повторный запрос ничего лишнего не начисляет
    r = client.get("/api/me", headers=auth(1))
    check("повторный запрос", r.json()["balance"], 1000)

    # два пользователя — разные записи
    other = client.get("/api/me", headers=auth(2)).json()
    check("второй игрок", other["balance"], 1000)
    from db import get_player
    get_player(2, now=int(time.time()) + 5 * 3600, db_path=path)  # второму накапало +500
    check("балансы разные",
          (client.get("/api/me", headers=auth(1)).json()["balance"],
           client.get("/api/me", headers=auth(2)).json()["balance"]), (1000, 1500))

    # id берётся только из подписи: параметр в запросе игнорируется
    r = client.get("/api/me?user_id=2", headers=auth(1))
    check("параметр user_id игнорируется", r.json()["balance"], 1000)

    # без подписи, пустой, мусор, чужой токен, устаревшие данные: 401 и одно сообщение
    bad = [
        {},
        {"Authorization": ""},
        {"Authorization": "tma "},
        {"Authorization": "tma garbage"},
        {"Authorization": "Bearer " + make_init_data(TOKEN, user_id=1)},
        auth(1, token="999:OTHER"),
        auth(1, auth_date=int(time.time()) - 25 * 3600),
    ]
    for h in bad:
        r = client.get("/api/me", headers=h)
        check("401 для " + str(list(h)), r.status_code, 401)
        check("одинаковое сообщение", r.json(), {"detail": "Unauthorized"})

    # CORS: только нужный origin, только GET и Authorization
    r = client.get("/api/me", headers={**auth(1), "Origin": ORIGIN})
    check("CORS разрешённый origin", r.headers.get("access-control-allow-origin"), ORIGIN)
    r = client.get("/api/me", headers={**auth(1), "Origin": "https://evil.example"})
    check("CORS чужой origin", r.headers.get("access-control-allow-origin"), None)
    r = client.options("/api/me", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "Authorization"})
    check("CORS DELETE запрещён", r.status_code, 400)
    r = client.options("/api/me", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization"})
    check("CORS preflight GET", (r.status_code, r.headers.get("access-control-allow-origin")), (200, ORIGIN))
finally:
    os.remove(path)

print("Все проверки прошли")
