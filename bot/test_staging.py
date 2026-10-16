"""Тесты тестового сервера (staging): раздача клиента, подмена API_URL и CSP,
белый список, защита от обхода путей, блокировка оплаты Stars, /health в обоих режимах."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import db
import staging
from api import create_app
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
USER_A = 424242421

_ENV_KEYS = ("DB_PATH", "APP_ENV", "CLIENT_DIR", "STAGING_ALLOW_PAYMENTS")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


class FakeBot:
    def __init__(self):
        self.sent, self.refunds, self.links = [], [], []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        self.refunds.append((user_id, telegram_payment_charge_id))
        return True

    async def create_invoice_link(self, **kw):
        self.links.append(kw)
        return "https://t.me/$fake-gems-%d" % len(self.links)


def auth_h(uid=USER_A):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}


tmp_dir = tempfile.mkdtemp()
db_counter = [0]


def new_db():
    db_counter[0] += 1
    p = os.path.join(tmp_dir, f"test_{db_counter[0]}.db")
    db.init_db(p)
    return p


try:
    client_dir = staging.find_client_dir({})
    check("папка клиента существует", client_dir.is_dir(), True)
    check("index.html в папке клиента", (client_dir / "index.html").is_file(), True)

    # ================= 1. is_staging и find_client_dir =================
    check("is_staging со staging", staging.is_staging({"APP_ENV": "staging"}), True)
    check("is_staging без APP_ENV", staging.is_staging({}), False)
    check("is_staging с пустым APP_ENV", staging.is_staging({"APP_ENV": ""}), False)
    check("is_staging с production (предупреждение в лог)", staging.is_staging({"APP_ENV": "production"}), False)
    check("is_staging с другим значением", staging.is_staging({"APP_ENV": "dev"}), False)

    empty_tmp = tempfile.mkdtemp(dir=tmp_dir)
    raises(SystemExit, staging.find_client_dir, {"CLIENT_DIR": empty_tmp})
    custom_client = tempfile.mkdtemp(dir=tmp_dir)
    (Path(custom_client) / "index.html").write_text("<!DOCTYPE html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"connect-src 'none';\"></head><body></body></html>", encoding="utf-8")
    check("find_client_dir с CLIENT_DIR", staging.find_client_dir({"CLIENT_DIR": custom_client}), Path(custom_client).resolve())

    # ================= 2. /health в обоих режимах =================
    path_db = new_db()
    app_prod = create_app(TOKEN, [], db_path=path_db)
    client_prod = TestClient(app_prod)
    r = client_prod.get("/health")
    check("/health в бою: ровно {'ok': true}", (r.status_code, r.json()), (200, {"ok": True}))

    app_staging = create_app(TOKEN, [], db_path=path_db, client_dir=client_dir)
    client_staging = TestClient(app_staging)
    r = client_staging.get("/health")
    check("/health в staging: {'ok': true, 'env': 'staging'}", (r.status_code, r.json()), (200, {"ok": True, "env": "staging"}))

    # ================= 3. Боевой режим: клиент не раздаётся (404) =================
    for p in ("/", "/index.html", "/js/00-config.js", "/privacy.html", "/terms.html", "/robots.txt", "/_staging/ribbon.css"):
        check(f"в бою {p} даёт 404", client_prod.get(p).status_code, 404)

    # ================= 4. Раздача в staging: GET / и /index.html с подменой =================
    r_root = client_staging.get("/")
    check("GET / 200", r_root.status_code, 200)
    check("GET / Cache-Control", r_root.headers.get("cache-control"), "no-cache")
    check("GET / X-Robots-Tag", r_root.headers.get("x-robots-tag"), "noindex")
    check("GET / Content-Type", r_root.headers.get("content-type"), "text/html; charset=utf-8")
    assert "connect-src 'self' " in r_root.text or "connect-src 'self';" in r_root.text, "в connect-src должен добавиться 'self'"
    assert '<link rel="stylesheet" href="/_staging/ribbon.css">' in r_root.text, "стиль ленты должен быть перед </head>"
    assert r_root.text.find('<link rel="stylesheet" href="/_staging/ribbon.css">') < r_root.text.find("</head>"), "стиль ленты перед </head>"
    assert '<div class="staging-ribbon" role="note">ТЕСТОВЫЙ СЕРВЕР</div>' in r_root.text, "лента перед </body>"
    assert r_root.text.find('<div class="staging-ribbon" role="note">ТЕСТОВЫЙ СЕРВЕР</div>') < r_root.text.find("</body>"), "лента перед </body>"

    # HEAD-запрос
    r_head = client_staging.head("/")
    check("HEAD / 200", r_head.status_code, 200)
    check("HEAD / тело пустое", len(r_head.content), 0)
    check("HEAD / Content-Type", r_head.headers.get("content-type"), "text/html; charset=utf-8")
    check("HEAD / Cache-Control", r_head.headers.get("cache-control"), "no-cache")

    r_index = client_staging.get("/index.html")
    check("GET /index.html совпадает с GET /", r_index.text, r_root.text)
    check("HEAD /index.html 200", client_staging.head("/index.html").status_code, 200)

    # ================= 5. Подмена в js/00-config.js =================
    r_cfg = client_staging.get("/js/00-config.js")
    check("GET /js/00-config.js 200", r_cfg.status_code, 200)
    check("GET /js/00-config.js Content-Type", r_cfg.headers.get("content-type"), "text/javascript; charset=utf-8")
    check("GET /js/00-config.js Cache-Control", r_cfg.headers.get("cache-control"), "no-cache")
    check("GET /js/00-config.js X-Robots-Tag", r_cfg.headers.get("x-robots-tag"), "noindex")
    assert "const API_URL = '';" in r_cfg.text, "API_URL должен быть пустой строкой"
    assert "railway.app" not in r_cfg.text, "старый API_URL не должен остаться"

    # Проверка, что файлы на диске НЕ изменились
    disk_cfg = (client_dir / "js" / "00-config.js").read_text(encoding="utf-8")
    assert "const API_URL = '';" not in disk_cfg, "файл js/00-config.js на диске не должен меняться"
    disk_index = (client_dir / "index.html").read_text(encoding="utf-8")
    assert "staging-ribbon" not in disk_index, "файл index.html на диске не должен меняться"

    # ================= 6. Служебные файлы раздачи: ribbon.css, robots.txt, privacy, terms =================
    r_ribbon = client_staging.get("/_staging/ribbon.css")
    check("GET /_staging/ribbon.css 200", r_ribbon.status_code, 200)
    check("ribbon.css Content-Type", r_ribbon.headers.get("content-type"), "text/css; charset=utf-8")
    check("ribbon.css Cache-Control", r_ribbon.headers.get("cache-control"), "no-cache")
    check("ribbon.css X-Robots-Tag", r_ribbon.headers.get("x-robots-tag"), "noindex")
    for prop in (".staging-ribbon", "position: fixed", "pointer-events: none", "z-index: 99999", "font-size: 10px", "rgba(220, 38, 38"):
        assert prop in r_ribbon.text, f"В ribbon.css ожидалось свойство {prop}"

    r_robots = client_staging.get("/robots.txt")
    check("GET /robots.txt 200", r_robots.status_code, 200)
    check("robots.txt Content-Type", r_robots.headers.get("content-type"), "text/plain; charset=utf-8")
    check("robots.txt Cache-Control", r_robots.headers.get("cache-control"), "no-cache")
    check("robots.txt X-Robots-Tag", r_robots.headers.get("x-robots-tag"), "noindex")
    check("robots.txt тело", r_robots.text.strip(), "User-agent: *\nDisallow: /")

    r_priv = client_staging.get("/privacy.html")
    check("GET /privacy.html 200", r_priv.status_code, 200)
    check("privacy.html Content-Type", r_priv.headers.get("content-type"), "text/html; charset=utf-8")

    r_terms = client_staging.get("/terms.html")
    check("GET /terms.html 200", r_terms.status_code, 200)
    check("terms.html Content-Type", r_terms.headers.get("content-type"), "text/html; charset=utf-8")

    # ================= 7. Файлы внутри разрешённых папок (css, js, fonts, games, skins) =================
    static_ok = [
        ("/css/00-fonts-tokens.css", "text/css; charset=utf-8"),
        ("/js/01-core.js", "text/javascript; charset=utf-8"),
        ("/fonts/inter-latin-wght-normal.woff2", "font/woff2"),
        ("/games/western-slot/index.html", "text/html; charset=utf-8"),
        ("/games/bloody-slot/img/bg.webp", "image/webp"),
        ("/skins/crash_barrel.js", "text/javascript; charset=utf-8"),
        ("/skins/draft_mines.css", "text/css; charset=utf-8"),
    ]
    for url, mime in static_ok:
        res = client_staging.get(url)
        check(f"{url} status", res.status_code, 200)
        check(f"{url} content-type", res.headers.get("content-type"), mime)
        check(f"{url} cache-control", res.headers.get("cache-control"), "no-cache")
        check(f"{url} x-robots-tag", res.headers.get("x-robots-tag"), "noindex")

    # ================= 8. Белый список и защита от выхода из папок (404) =================
    # Файлы вне белого списка
    bad_targets = [
        "/bot/.env",
        "/bot/players.db",
        "/.git/config",
        "/.env",
        "/docs/API.md",
        "/api.py",
        "/bot.py",
        "/tools/README.md" if os.path.exists("tools/README.md") else "/tools/",
        "/e2e/test.js" if os.path.exists("e2e") else "/e2e/",
        "/fonts/LICENSE-Inter-OFL.txt",  # расширение .txt в fonts/ запрещено
        "/fonts/LICENSE-PlayfairDisplay-OFL.txt",
        "/css/",  # папка целиком
        "/games/",
        "/css/not_found_test_123.css",
    ]
    for bad in bad_targets:
        check(f"запрещённый путь {bad}", client_staging.get(bad).status_code, 404)

    # Варианты обхода: .., %2e%2e, //, обратный слэш, нулевой байт
    traversal_paths = [
        "/css/../bot/.env",
        "/%2e%2e/bot/.env",
        "/%2E%2E/bot/.env",
        "/%2e%2e",
        "/%2e%2e/",
        "/css/%2e%2e/bot/.env",
        "/css/%2e%2e/bot/players.db",
        "/css/%2e%2e/.git/config",
        "http://testserver//index.html",
        "/css//00-fonts-tokens.css",
        r"/css\00-fonts-tokens.css",
        "/css%5c00-fonts-tokens.css",
        "/css/00-fonts-tokens.css%00.js",
        "/css/test%00.css",
        "/css/.hidden.css",
    ]
    for bad in traversal_paths:
        check(f"обход пути {bad}", client_staging.get(bad).status_code, 404)

    # Проверка сырого пути /.. и /../ и нулевого байта (httpx в TestClient нормализует /.. в / и отвергает \x00 на клиенте)
    def raw_get(app, path, raw=None):
        events = []
        async def send(msg):
            events.append(msg)
        async def receive():
            return {"type": "http.request"}
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": raw if raw is not None else path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        asyncio.run(app(scope, receive, send))
        return next(m["status"] for m in events if m["type"] == "http.response.start")

    check("сырой запрос /.. дает 404", raw_get(app_staging, "/..", b"/.."), 404)
    check("сырой запрос /../ дает 404", raw_get(app_staging, "/../", b"/../"), 404)
    check("сырой запрос с нулевым байтом дает 404", raw_get(app_staging, "/css/test\x00.css", b"/css/test\x00.css"), 404)

    # Символьные ссылки наружу на временной копии клиента
    symlink_dir = tempfile.mkdtemp(dir=tmp_dir)
    try:
        shutil.copy(client_dir / "index.html", Path(symlink_dir) / "index.html")
        shutil.copytree(client_dir / "js", Path(symlink_dir) / "js")
        os.makedirs(Path(symlink_dir) / "css", exist_ok=True)
        # Символьная ссылка на файл наружу (/etc/passwd или bot/.env)
        secret_file = Path(symlink_dir) / "outside_secret.txt"
        secret_file.write_text("SUPER_SECRET")
        os.symlink(secret_file, Path(symlink_dir) / "css" / "leak.css")
        # Символьная ссылка на файл внутри клиента, но не из css/ (например index.html)
        os.symlink(Path(symlink_dir) / "index.html", Path(symlink_dir) / "css" / "internal_leak.html")

        client_sym = TestClient(create_app(TOKEN, [], db_path=path_db, client_dir=symlink_dir))
        check("символьная ссылка наружу дает 404", client_sym.get("/css/leak.css").status_code, 404)
        check("символьная ссылка на чужой каталог внутри дает 404", client_sym.get("/css/internal_leak.html").status_code, 404)
    finally:
        shutil.rmtree(symlink_dir, ignore_errors=True)

    # ================= 9. Оплата в staging (403 и STAGING_ALLOW_PAYMENTS=1) =================
    fake_bot = FakeBot()
    app_staging.state.application = SimpleNamespace(bot=fake_bot)
    invoice_payload = {"request_id": "inv-req-stg-01", "pack_code": "gems_250"}

    # Без STAGING_ALLOW_PAYMENTS: 403 {"detail": "staging_payments_disabled"}
    os.environ.pop("STAGING_ALLOW_PAYMENTS", None)
    r_inv_disabled = client_staging.post("/api/gems/invoice", headers=auth_h(), json=invoice_payload)
    check("staging оплата выключена: статус 403", r_inv_disabled.status_code, 403)
    check("staging оплата выключена: тело", r_inv_disabled.json(), {"detail": "staging_payments_disabled"})
    check("ссылка не создавалась бота не звали", len(fake_bot.links), 0)

    # С STAGING_ALLOW_PAYMENTS=1: ссылка создаётся, 200
    os.environ["STAGING_ALLOW_PAYMENTS"] = "1"
    r_inv_enabled = client_staging.post("/api/gems/invoice", headers=auth_h(), json=dict(invoice_payload, request_id="inv-req-stg-02"))
    check("staging оплата разрешена: статус 200", r_inv_enabled.status_code, 200)
    check("staging оплата: инвойс получен", r_inv_enabled.json()["invoice_url"].startswith("https://t.me/"), True)
    check("бот вызывался один раз", len(fake_bot.links), 1)

    # Боевой режим: оплата создаётся без 403 даже без STAGING_ALLOW_PAYMENTS
    os.environ.pop("STAGING_ALLOW_PAYMENTS", None)
    app_prod.state.application = SimpleNamespace(bot=fake_bot)
    r_inv_prod = client_prod.post("/api/gems/invoice", headers=auth_h(), json=dict(invoice_payload, request_id="inv-req-stg-03"))
    check("боевой режим: статус 200", r_inv_prod.status_code, 200)
    check("бот вызывался второй раз", len(fake_bot.links), 2)

    # ================= 10. Громкие ошибки при старте (SystemExit) =================
    # Копия клиента с повреждённым js/00-config.js (нет API_URL)
    broken_dir_1 = tempfile.mkdtemp(dir=tmp_dir)
    try:
        shutil.copy(client_dir / "index.html", Path(broken_dir_1) / "index.html")
        os.makedirs(Path(broken_dir_1) / "js", exist_ok=True)
        (Path(broken_dir_1) / "js" / "00-config.js").write_text("// config without API_URL\nconst FOO = 'bar';", encoding="utf-8")
        raises(SystemExit, create_app, TOKEN, [], db_path=path_db, client_dir=broken_dir_1)
    finally:
        shutil.rmtree(broken_dir_1, ignore_errors=True)

    # Копия клиента с дублирующейся строкой API_URL
    broken_dir_2 = tempfile.mkdtemp(dir=tmp_dir)
    try:
        shutil.copy(client_dir / "index.html", Path(broken_dir_2) / "index.html")
        os.makedirs(Path(broken_dir_2) / "js", exist_ok=True)
        (Path(broken_dir_2) / "js" / "00-config.js").write_text("const API_URL = 'http://a';\nconst API_URL = 'http://b';", encoding="utf-8")
        raises(SystemExit, create_app, TOKEN, [], db_path=path_db, client_dir=broken_dir_2)
    finally:
        shutil.rmtree(broken_dir_2, ignore_errors=True)

    # Копия клиента с index.html без connect-src в CSP
    broken_dir_3 = tempfile.mkdtemp(dir=tmp_dir)
    try:
        (Path(broken_dir_3) / "index.html").write_text("<!DOCTYPE html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none';\"></head><body></body></html>", encoding="utf-8")
        os.makedirs(Path(broken_dir_3) / "js", exist_ok=True)
        shutil.copy(client_dir / "js" / "00-config.js", Path(broken_dir_3) / "js" / "00-config.js")
        raises(SystemExit, create_app, TOKEN, [], db_path=path_db, client_dir=broken_dir_3)
    finally:
        shutil.rmtree(broken_dir_3, ignore_errors=True)

finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("test_staging OK")
