"""Усиление безопасности (аудит 2026-10-04): запуск uvicorn без access-лога и заголовка server, лимит тела запроса,
заголовки ответов, повтор рулетки с другими ставками, ответ 503 при занятой базе."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from fastapi.testclient import TestClient

import db
from api import MAX_BODY_BYTES, create_app
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://example.invalid"
SECRET_NAME = "СекретноеИмя"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def auth(user_id):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=user_id, auth_date=int(time.time()))}


def start_command():
    text = open(os.path.join(HERE, "railway.toml"), encoding="utf-8").read()
    return re.search(r'^startCommand\s*=\s*"([^"]+)"', text, re.M).group(1)


tmp = tempfile.mkdtemp()
try:
    # ================= 2. запуск: нет access-лога и заголовка server =================
    cmd = start_command()
    check("startCommand: флаги", ("--no-access-log" in cmd, "--no-server-header" in cmd), (True, True))

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def run_server(extra_args, drop_flags):
        """Настоящие аргументы из railway.toml (хост 127.0.0.1, свободный порт); .env не читается."""
        port = free_port()
        args = [a for a in shlex.split(cmd.replace("$PORT", str(port)).replace("0.0.0.0", "127.0.0.1"))[1:] if a not in drop_flags]
        env = {k: v for k, v in os.environ.items() if k not in ("BOT_TOKEN", "DB_PATH", "ALLOWED_ORIGINS", "PUBLIC_URL", "LOCAL_POLLING")}
        env.update({"BOT_TOKEN": TOKEN, "DB_PATH": os.path.join(tmp, "live.db"), "ALLOWED_ORIGINS": ORIGIN,
                    "PYTHONPATH": HERE, "PYTHONUNBUFFERED": "1"})
        # python-dotenv не должен подтянуть bot/.env: подменяем загрузку, остальное запускается как на Railway
        boot = "import dotenv, sys; dotenv.load_dotenv = lambda *a, **k: False; import uvicorn; sys.argv = ['uvicorn'] + sys.argv[1:]; uvicorn.main()"
        proc = subprocess.Popen([sys.executable, "-c", boot] + args + extra_args, cwd=tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1).read()
                break
            except Exception:
                time.sleep(0.1)
        else:
            proc.kill()
            raise AssertionError("сервер не поднялся: " + proc.communicate()[0][-300:])
        return proc, port

    def probe(port):
        url = "http://127.0.0.1:%d/api/chat/top?q=%s" % (port, urllib.parse.quote(SECRET_NAME))
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "tma bad"}), timeout=3)
        except urllib.error.HTTPError as err:
            return err.code, err.headers

    def stop(proc):
        proc.terminate()
        try:
            return proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.communicate()[0]

    proc, port = run_server([], [])
    code, headers = probe(port)
    out = stop(proc)
    check("запрос принят и отклонён", code, 401)
    check("в выводе сервера нет имени из строки запроса", (SECRET_NAME in out, urllib.parse.quote(SECRET_NAME) in out, "?q=" in out), (False, False, False))
    check("заголовка server нет", headers.get("server"), None)
    # контроль чувствительности: без флагов то же имя попадает в лог (иначе проверка выше ничего не доказывала)
    proc, port = run_server([], ("--no-access-log", "--no-server-header"))
    code, headers = probe(port)
    out = stop(proc)
    check("без флагов имя видно в логе", urllib.parse.quote(SECRET_NAME) in out, True)
    check("без флагов заголовок server есть", (headers.get("server") or "").lower(), "uvicorn")

    # ================= общие приложения для проверок через TestClient =================
    path = os.path.join(tmp, "t.db")
    db.init_db(path)
    app = create_app(TOKEN, [ORIGIN], db_path=path)
    client = TestClient(app, raise_server_exceptions=False)

    # ================= 12. заголовки безопасности =================
    r = client.get("/api/me", headers=auth(1))
    check("/api/me: заголовки", (r.status_code, r.headers.get("x-content-type-options"), r.headers.get("referrer-policy"),
                                 r.headers.get("cache-control")), (200, "nosniff", "no-referrer", "no-store"))
    for label, resp in (("401", client.get("/api/me")), ("404", client.get("/api/nope")),
                        ("POST 400", client.post("/api/roulette/spin", json={}, headers=auth(1))),
                        ("CORS preflight", client.options("/api/me", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"}))):
        check("заголовки при " + label, (resp.headers.get("x-content-type-options"), resp.headers.get("referrer-policy")), ("nosniff", "no-referrer"))
        if label != "CORS preflight":
            check("no-store при " + label + (" (только /api/*)" if label != "404" else ""), resp.headers.get("cache-control"), "no-store")
    r = client.get("/health")
    check("/health: без no-store, но nosniff", (r.headers.get("cache-control"), r.headers.get("x-content-type-options")), (None, "nosniff"))
    check("CORS по-прежнему работает", client.get("/api/me", headers=dict(auth(1), Origin=ORIGIN)).headers.get("access-control-allow-origin"), ORIGIN)
    check("CSP не добавляется", "content-security-policy" in client.get("/api/me", headers=auth(1)).headers, False)

    # ================= переводы удалены (E1): старые адреса отвечают 410, подпись не нужна =================
    for method, url in (("post", "/api/transfers/send"), ("get", "/api/transfers"), ("get", "/api/chat/members")):
        r = getattr(client, method)(url, headers=auth(2))
        check("410 Gone: " + method.upper() + " " + url, (r.status_code, r.json()), (410, {"detail": "gone"}))

    # ================= 4. лимит тела запроса: 413 до чтения =================
    posts = [("/api/roulette/spin", {}), ("/api/farm/buy", {}), ("/api/mines/start", {}), ("/api/keno/play", {})]
    for url, _ in posts:
        r = client.post(url, content=b"x" * (MAX_BODY_BYTES + 1), headers=auth(2))
        check("413 по Content-Length: " + url, (r.status_code, r.json()), (413, {"detail": "payload_too_large"}))

        def chunks():   # без Content-Length: клиент шлёт кусками, сервер обрывает чтение на пределе
            for _i in range(MAX_BODY_BYTES // 1024 + 4):
                yield b"y" * 1024
        r = client.post(url, content=chunks(), headers=auth(2))
        check("413 при chunked-теле: " + url, (r.status_code, r.json()), (413, {"detail": "payload_too_large"}))
        r = client.post(url, content=b"x" * MAX_BODY_BYTES, headers=auth(2))
        check("ровно предел: обычный 400, не 413: " + url, r.status_code, 400)
        r = client.post(url, content=b"x" * (MAX_BODY_BYTES + 1), headers={"Authorization": "tma bad"})
        check("413 только после проверки подписи (401 раньше): " + url, r.status_code, 401)
    # сервер не читает тело, если заявленный размер больше предела
    from starlette.requests import Request
    import api as api_module

    async def never_read():
        raise AssertionError("тело читалось")
    scope = {"type": "http", "headers": [(b"content-length", str(MAX_BODY_BYTES + 1).encode())]}
    import asyncio
    try:
        asyncio.run(api_module.read_body_limited(Request(scope, receive=never_read)))
        raise AssertionError("предел не сработал")
    except api_module.BodyTooLarge:
        pass

    # ================= 7. рулетка: тот же request_id с другими ставками =================
    def spin(user, rid, bets):
        return client.post("/api/roulette/spin", json={"request_id": rid, "bets": bets}, headers=auth(user))

    b1 = [{"type": "red", "value": None, "amount": 10}, {"type": "number", "value": 7, "amount": 5}]
    first = spin(3, "sec-spin-0001", b1)
    check("первый спин", first.status_code, 200)
    bal = client.get("/api/me", headers=auth(3)).json()["balance"]
    r = spin(3, "sec-spin-0001", [{"type": "red", "value": None, "amount": 99}])
    check("другие ставки: 409 request_conflict", (r.status_code, r.json()), (409, {"detail": "request_conflict"}))
    r = spin(3, "sec-spin-0001", list(reversed(b1)))
    check("те же ставки в другом порядке: повтор", (r.status_code, r.json()["replayed"], r.json()["number"]), (200, True, first.json()["number"]))
    check("баланс после конфликта и повтора не менялся", client.get("/api/me", headers=auth(3)).json()["balance"], bal)

    # ================= 8. база занята: 503 с Retry-After, один повтор BEGIN =================
    spin(4, "sec-busy-0000", b1)    # игрок создан
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with mock.patch.object(db, "BUSY_TIMEOUT_SECONDS", 0.05), mock.patch.object(db, "BUSY_RETRY_DELAY", 0.05):
            t0 = time.perf_counter()
            r = spin(4, "sec-busy-0001", b1)
            waited = time.perf_counter() - t0
        check("занятая база: 503, Retry-After", (r.status_code, r.json(), r.headers.get("retry-after")), (503, {"detail": "busy"}, "1"))
        assert waited < 2, "ответ 503 слишком долгий: %.2f с" % waited
        check("503: заголовки безопасности и no-store есть", (r.headers.get("x-content-type-options"), r.headers.get("cache-control")), ("nosniff", "no-store"))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    r = spin(4, "sec-busy-0001", b1)
    check("после снятия блокировки тот же request_id проходит (503 ничего не сохранил)", (r.status_code, r.json()["replayed"]), (200, False))

    # блокировка снимается во время паузы перед повтором: запрос проходит без 503
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")

    def release():
        time.sleep(0.15)
        holder.execute("ROLLBACK")
    th = threading.Thread(target=release)
    th.start()
    with mock.patch.object(db, "BUSY_TIMEOUT_SECONDS", 0.05), mock.patch.object(db, "BUSY_RETRY_DELAY", 0.3):
        r = spin(4, "sec-busy-0002", b1)
    th.join()
    holder.close()
    check("повтор BEGIN после паузы успешен", r.status_code, 200)
    check("таймаут соединения по умолчанию 10 с", db.BUSY_TIMEOUT_SECONDS, 10)
    check("не «занято» остаётся 500", db.is_busy_error(sqlite3.OperationalError("no such table: x")), False)
    check("«locked» и «busy» распознаются", (db.is_busy_error(sqlite3.OperationalError("database is locked")),
                                           db.is_busy_error(sqlite3.OperationalError("database table is locked"))), (True, True))
    # прочие ошибки SQLite не превращаются в 503
    with mock.patch("web.routes_account.get_player", side_effect=sqlite3.OperationalError("no such table: players")):
        r = client.get("/api/me", headers=auth(5))
    check("прочая ошибка SQLite: 500, не 503", r.status_code, 500)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
