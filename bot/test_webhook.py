import os
import shutil
import sys
import tempfile

from fastapi.testclient import TestClient

from api import create_app, load_settings
from db import init_db
from stubs import StubApplication

TOKEN = "123456:TEST-TOKEN-not-real"
SECRET = "test-secret_123"
PUBLIC = "https://example.up.railway.app"
HEADER = "X-Telegram-Bot-Api-Secret-Token"
UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1, "date": 1_700_000_000,
        "chat": {"id": 5, "type": "private"},
        "from": {"id": 5, "is_bot": False, "first_name": "Тест"},
        "text": "/balance",
    },
}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises_exit(env):
    try:
        load_settings(env)
    except SystemExit:
        return True
    return False


# ---------- режим «только API»: без PUBLIC_URL и LOCAL_POLLING бот не создаётся ----------
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    s = load_settings({"BOT_TOKEN": TOKEN})
    check("режим", s["mode"], "api")
    app = create_app(TOKEN, [], db_path=path, mode=s["mode"])
    with TestClient(app) as client:
        check("/health", (client.get("/health").status_code, client.get("/health").json()), (200, {"ok": True}))
        check("бот не создан", app.state.application, None)
        assert "bot" not in sys.modules, "модуль бота импортирован в режиме только API"
        # webhook без включённого режима webhook всегда закрыт
        r = client.post("/telegram/webhook", json=UPDATE, headers={HEADER: SECRET})
        check("webhook в режиме API", r.status_code, 403)

    # ---------- режим webhook ----------
    stub = StubApplication()
    app = create_app(TOKEN, [], db_path=path, mode="webhook", public_url=PUBLIC,
                     webhook_secret=SECRET, application=stub)
    with TestClient(app) as client:
        # при старте webhook зарегистрирован с адресом и секретом
        call = stub.bot.webhook_calls[0]
        check("адрес webhook", call["url"], PUBLIC + "/telegram/webhook")
        check("secret_token", call["secret_token"], SECRET)

        r = client.post("/telegram/webhook", json=UPDATE)
        check("без заголовка", (r.status_code, r.json()), (403, {"detail": "Forbidden"}))
        r = client.post("/telegram/webhook", json=UPDATE, headers={HEADER: "wrong"})
        check("неверный секрет", (r.status_code, r.json()), (403, {"detail": "Forbidden"}))
        r = client.post("/telegram/webhook", json=UPDATE, headers={HEADER: ""})
        check("пустой секрет", r.status_code, 403)
        check("очередь пуста после отказов", stub.update_queue.items, [])

        r = client.post("/telegram/webhook", json=UPDATE, headers={HEADER: SECRET})
        check("верный секрет", r.status_code, 200)
        check("в очереди одно обновление", len(stub.update_queue.items), 1)
        queued = stub.update_queue.items[0]
        check("обновление разобрано", (queued.update_id, queued.message.text), (1, "/balance"))

        r = client.post("/telegram/webhook", content=b"not json", headers={HEADER: SECRET})
        check("мусор вместо JSON", r.status_code, 400)
        check("мусор не попал в очередь", len(stub.update_queue.items), 1)
    check("запуск и остановка бота", stub.events, ["initialize", "start", "stop", "shutdown"])

    # регистрация webhook не удалась: приложение не стартует, бот останавливается
    stub = StubApplication(fail_with=ConnectionError("https://api.telegram.org/bot" + TOKEN))
    app = create_app(TOKEN, [], db_path=path, mode="webhook", public_url=PUBLIC,
                     webhook_secret=SECRET, application=stub)
    try:
        with TestClient(app):
            raise AssertionError("приложение стартовало при ошибке регистрации webhook")
    except RuntimeError as exc:
        assert TOKEN not in str(exc) and TOKEN not in repr(exc.__cause__)
    check("остановка после ошибки", stub.events, ["initialize", "start", "stop", "shutdown"])

    # ---------- режим polling ----------
    stub = StubApplication()
    app = create_app(TOKEN, [], db_path=path, mode="polling", application=stub)
    with TestClient(app) as client:
        check("polling запущен", stub.updater.events, ["start_polling"])
        check("webhook не регистрируется", stub.bot.webhook_calls, [])
        r = client.post("/telegram/webhook", json=UPDATE, headers={HEADER: SECRET})
        check("webhook закрыт при polling", r.status_code, 403)
    check("polling остановлен", stub.updater.events, ["start_polling", "updater_stop"])
finally:
    os.remove(path)

# ---------- настройки окружения ----------
base = {"BOT_TOKEN": TOKEN, "WEBAPP_URL": "https://example.test"}
check("polling", load_settings({**base, "LOCAL_POLLING": "1"})["mode"], "polling")
check("webhook", load_settings({**base, "PUBLIC_URL": PUBLIC + "/", "WEBHOOK_SECRET": SECRET})["public_url"], PUBLIC)
check("webhook важнее polling", load_settings({**base, "PUBLIC_URL": PUBLIC, "WEBHOOK_SECRET": SECRET, "LOCAL_POLLING": "1"})["mode"], "webhook")
assert raises_exit({}), "нет BOT_TOKEN"
assert raises_exit({**base, "PUBLIC_URL": PUBLIC}), "webhook без WEBHOOK_SECRET"
assert raises_exit({**base, "PUBLIC_URL": "http://example.test", "WEBHOOK_SECRET": SECRET}), "PUBLIC_URL без https"
assert raises_exit({**base, "PUBLIC_URL": PUBLIC, "WEBHOOK_SECRET": "плохой секрет"}), "недопустимый секрет"
assert raises_exit({"BOT_TOKEN": TOKEN, "LOCAL_POLLING": "1"}), "бот без WEBAPP_URL"
assert raises_exit({**base, "ALLOWED_ORIGINS": "*"}), "CORS *"

# ---------- реальное приложение бота собирается без сети ----------
from bot import build_application

a = build_application(TOKEN, use_updater=False)
check("webhook: без updater", a.updater, None)
a = build_application(TOKEN)
assert a.updater is not None
# 8 команд (start, play, balance, help, privacy, developer_info, mydata, deletemydata) и кнопки удаления
check("обработчики", len(a.handlers[0]), 9)

# ---------- DB_PATH: папка создаётся ----------
tmp = tempfile.mkdtemp()
old = os.environ.get("DB_PATH")
try:
    target = os.path.join(tmp, "нет", "такой", "папки", "players.db")
    os.environ["DB_PATH"] = target
    init_db()
    assert os.path.isdir(os.path.dirname(target)), "папка не создана"
    assert os.path.isfile(target), "файл базы не создан"
finally:
    if old is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = old
    shutil.rmtree(tmp)

print("Все проверки прошли")
