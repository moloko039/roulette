import logging

import httpx
from fastapi.testclient import TestClient

import api  # при импорте выставляет уровни логгеров httpx и httpcore
from api import create_app
from stubs import StubApplication

TOKEN = "123456:TEST-TOKEN-not-real"
SECRET = "test-secret_123"
PUBLIC = "https://example.up.railway.app"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


capture = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)  # самый подробный уровень: если что-то пишется, мы это увидим
root.addHandler(capture)
try:
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING, f"{name} не на WARNING"

    # запрос к адресу Telegram с токеном (транспорт-заглушка, сети нет)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
    client.get(f"https://api.telegram.org/bot{TOKEN}/getMe")
    client.close()

    # webhook, polling и ошибка регистрации webhook с токеном в тексте исключения
    for mode, kwargs, stub in [
        ("webhook", {"public_url": PUBLIC, "webhook_secret": SECRET}, StubApplication()),
        ("polling", {}, StubApplication()),
        ("webhook", {"public_url": PUBLIC, "webhook_secret": SECRET},
         StubApplication(fail_with=ConnectionError(f"https://api.telegram.org/bot{TOKEN}/setWebhook"))),
    ]:
        app = create_app(TOKEN, [], mode=mode, application=stub, **kwargs)
        try:
            with TestClient(app) as c:
                c.post("/telegram/webhook", json={"update_id": 1},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        except RuntimeError:
            pass
finally:
    root.removeHandler(capture)
    root.setLevel(old_level)

assert capture.lines, "логи не перехвачены, проверка бессмысленна"
for line in capture.lines:
    assert TOKEN not in line, "токен попал в лог"
    assert SECRET not in line, "секрет webhook попал в лог"
    assert "api.telegram.org" not in line, "адрес запроса к Telegram попал в лог"

print("Все проверки прошли")
