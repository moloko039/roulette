import asyncio
import collections
import logging
import os
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import backup
import db
import notify
from api import create_app
from stubs import StubApplication

# тест не зависит от окружения и bot/.env: на время теста эти переменные очищаются, в конце возвращаются
_ENV_KEYS = ("PUBLIC_URL", "BACKUP_DIR", "OWNER_CHAT_ID", "DB_PATH")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

T = 1_760_000_000
HOUR, DAY = 3600, 86400
OWNER = 777000111
SECRET_NAME, SECRET_ID, SECRET_BALANCE = "СекретноеИмя", 123456789, 7654321
Usage = collections.namedtuple("Usage", "total used free")
GB = 2**30
HEALTHY = Usage(100 * GB, 50 * GB, 50 * GB)


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)

tmp = tempfile.mkdtemp()
counter = [0]


def fresh(enabled=True):
    """Новая база с данными и каталогом копий; возвращает (путь базы, конфиг, бот-заглушка, Notifier)."""
    counter[0] += 1
    base = os.path.join(tmp, "case%d" % counter[0])
    os.makedirs(base)
    path = os.path.join(base, "players.db")
    db.init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, ?, 100, ?, ?)", (SECRET_ID, SECRET_BALANCE, T, T))
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (2, 1000, 100, ?, ?)", (T, T))
    conn.execute("INSERT INTO chat_members VALUES ('room', ?, ?, ?, ?)", (SECRET_ID, SECRET_NAME, T, T))
    conn.commit()
    conn.close()
    cfg = backup.load_config({"BACKUP_ENABLED": "1" if enabled else "0"}, path)
    bot = StubApplication().bot
    return path, cfg, bot, notify.Notifier(OWNER, lambda: bot, path, cfg)


def run(n, events, now):
    asyncio.run(n.run(events, now))


def reminder_text(public_url):
    """Текст напоминания при заданном PUBLIC_URL (None: переменной нет)."""
    path, cfg, bot, n = fresh()
    backup.create_snapshot(path, cfg["dir"], now=T)
    with mock.patch.dict(os.environ):  # прежние значения возвращаются после блока
        os.environ.pop("PUBLIC_URL", None)
        os.environ.pop("BACKUP_DIR", None)
        if public_url is not None:
            os.environ["PUBLIC_URL"] = public_url
        run(n, [], T + HOUR)
    check("напоминание отправлено", len(bot.sent), 1)
    return bot.sent[0]["text"], cfg


try:
    with mock.patch("shutil.disk_usage", return_value=HEALTHY):
        # ================= OWNER_CHAT_ID =================
        for raw in ("", "abc", "12.5", "-5", "0", "1 2", "٣٣"):
            check("неверный owner %r" % raw, notify.load_owner_id({"OWNER_CHAT_ID": raw}), None)
        check("owner не задан", notify.load_owner_id({}), None)
        check("owner верный", notify.load_owner_id({"OWNER_CHAT_ID": " 123456 "}), 123456)
        cap.lines.clear()
        notify.warn_owner(None)
        notify.warn_owner(notify.load_owner_id({"OWNER_CHAT_ID": "bad-secret-value"}))
        check("предупреждение без значения", len(cap.lines), 2)
        assert "bad-secret-value" not in " ".join(cap.lines)
        cap.lines.clear()
        notify.warn_owner(OWNER)
        check("при верном id предупреждения нет", cap.lines, [])

        # ================= сбой копирования =================
        path, cfg, bot, n = fresh()
        run(n, ["snapshot_failed"], T)
        check("тревога о сбое", len(bot.sent), 1)
        check("получатель и простой текст", (bot.sent[0]["chat_id"], sorted(bot.sent[0])), (OWNER, ["chat_id", "text"]))
        assert bot.sent[0]["text"].startswith("Тревога: резервная копия базы не создана"), bot.sent[0]["text"]
        run(n, ["snapshot_failed"], T + HOUR)
        run(n, ["snapshot_failed"], T + 23 * HOUR)
        check("повтор в течение 24 часов подавлен", len(bot.sent), 1)
        run(n, ["snapshot_failed"], T + 24 * HOUR)
        check("через 24 часа снова", len(bot.sent), 2)
        # без события сообщения нет
        run(n, [], T + 47 * HOUR)
        check("без события тишина", len(bot.sent), 2)

        # настоящий сбой create_snapshot попадает в события
        events = []
        with mock.patch.object(backup, "create_snapshot", return_value=None):
            backup.run_maintenance_once(cfg, path, T, None, events)
        check("событие сбоя", events, ["snapshot_failed"])
        events = []
        backup.run_maintenance_once(cfg, path, T, None, events)
        check("успех без события", events, [])

        # ================= нет успешной копии 48 часов =================
        path, cfg, bot, n = fresh()
        run(n, [], T)                      # начало наблюдения
        run(n, [], T + 47 * HOUR)
        check("до 48 часов тихо", len(bot.sent), 0)
        run(n, [], T + 48 * HOUR)
        check("тревога после 48 часов", len(bot.sent), 1)
        assert "больше 48 часов" in bot.sent[0]["text"] and "Последняя: нет" in bot.sent[0]["text"]
        run(n, [], T + 60 * HOUR)
        check("раз в сутки", len(bot.sent), 1)
        run(n, [], T + 72 * HOUR)
        check("через сутки снова", len(bot.sent), 2)
        # свежая копия снимает тревогу
        backup.create_snapshot(path, cfg["dir"], now=T + 73 * HOUR)
        run(n, [], T + 100 * HOUR)
        kinds = [m["text"][:10] for m in bot.sent]
        check("после свежей копии тревоги нет", sum(1 for k in kinds if k == "Тревога: н"), 2)

        # копии выключены: ни тревог, ни напоминаний
        path, cfg, bot, n = fresh(enabled=False)
        run(n, ["x"], T)
        run(n, [], T + 10 * DAY)
        check("копии выключены", len(bot.sent), 0)

        # ================= еженедельное напоминание =================
        path, cfg, bot, n = fresh()
        backup.create_snapshot(path, cfg["dir"], now=T)
        run(n, [], T + HOUR)
        check("первое напоминание", len(bot.sent), 1)
        size_kb = (os.path.getsize(os.path.join(cfg["dir"], "latest.db")) + 1023) // 1024
        want = ("Напоминание о резервной копии. Последняя копия на сервере: 2025-10-09 08:53 UTC, %d КБ, "
                "игроков: 2. Скачайте её командами:\n"
                "scp <домен сервиса>@ssh.railway.com:%s/latest.db ~/roulette-backups/latest-$(date +%%F).db\n"
                "python3 bot/verify_backup.py ~/roulette-backups/latest-$(date +%%F).db\n"
                "и удалите на компьютере копии старше 30 дней." % (size_kb, cfg["dir"]))
        check("текст напоминания", bot.sent[0]["text"], want)

        # ---- напоминание: способ скачивания (scp, хост из PUBLIC_URL) ----
        text, cfg2 = reminder_text("https://depnaya-demo.example.org/")
        assert "scp depnaya-demo.example.org@ssh.railway.com:%s/latest.db " % cfg2["dir"] in text, text
        assert "~/roulette-backups/latest-$(date +%F).db" in text
        assert "python3 bot/verify_backup.py ~/roulette-backups/latest-$(date +%F).db" in text
        assert "railway volume files" not in text and "https://" not in text, text
        assert "<домен сервиса>" not in text
        text, _ = reminder_text("https://host.example.org:8443/some/path?x=1")
        assert "scp host.example.org@ssh.railway.com:" in text, text
        for bad in (None, "", "not a url !!", "https://", "https://a b.example/"):
            text, _ = reminder_text(bad)
            assert "scp <домен сервиса>@ssh.railway.com:" in text, (bad, text)
            assert "railway volume files" not in text
        for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), "TEST-TOKEN"):
            assert secret not in text, secret
        check("public_host без схемы", notify.public_host({"PUBLIC_URL": "host.example.org"}), "host.example.org")
        # перезапуск: заново init_db на той же базе и новый Notifier
        db.init_db(path)
        n = notify.Notifier(OWNER, lambda: bot, path, cfg)
        backup.create_snapshot(path, cfg["dir"], now=T + 3 * DAY)
        run(n, [], T + 3 * DAY)
        check("после перезапуска повтора нет", len(bot.sent), 1)
        backup.create_snapshot(path, cfg["dir"], now=T + 6 * DAY)
        run(n, [], T + 6 * DAY + 23 * HOUR)
        check("до 7 дней нет", len(bot.sent), 1)
        backup.create_snapshot(path, cfg["dir"], now=T + 7 * DAY + HOUR)
        run(n, [], T + 7 * DAY + HOUR)
        check("через 7 дней отправлено", len(bot.sent), 2)
        # время хранится как unix
        check("meta unix", db.get_meta(notify.META_KEYS["reminder"], path), str(T + 7 * DAY + HOUR))
        # мусор в служебной отметке не ломает работу
        db.set_meta(notify.META_KEYS["reminder"], "мусор", path)
        backup.create_snapshot(path, cfg["dir"], now=T + 8 * DAY)
        run(n, [], T + 8 * DAY)
        check("мусор в отметке трактуется как «не отправляли»", len(bot.sent), 3)

    # ================= предупреждение о месте =================
    path, cfg, bot, n = fresh()
    backup.create_snapshot(path, cfg["dir"], now=T)
    with mock.patch("shutil.disk_usage", return_value=HEALTHY):
        run(n, [], T)
    sent_ok = len(bot.sent)  # здесь только напоминание
    check("места достаточно", [m for m in bot.sent if m["text"].startswith("Мало места")], [])
    with mock.patch("shutil.disk_usage", return_value=Usage(100 * GB, 90 * GB, 10 * GB)):
        run(n, [], T + HOUR)
        texts = [m["text"] for m in bot.sent if m["text"].startswith("Мало места")]
        check("меньше 15%", texts, ["Мало места на томе: свободно 10240 МБ из 102400 МБ (10%)."])
        run(n, [], T + 5 * HOUR)
        check("раз в сутки", len([m for m in bot.sent if m["text"].startswith("Мало места")]), 1)
        run(n, [], T + HOUR + DAY)
        check("через сутки снова", len([m for m in bot.sent if m["text"].startswith("Мало места")]), 2)
    with mock.patch("shutil.disk_usage", return_value=Usage(100 * 2**20, 60 * 2**20, 40 * 2**20)):
        path, cfg, bot, n = fresh(enabled=False)
        run(n, [], T)
        check("меньше 50 МБ (40 из 100 МБ = 40%)", len(bot.sent), 1)
    with mock.patch("shutil.disk_usage", side_effect=OSError()):
        path, cfg, bot, n = fresh(enabled=False)
        run(n, [], T)
        check("ошибка disk_usage не роняет", len(bot.sent), 0)

    # ================= потолок 3 в сутки =================
    path, cfg, bot, n = fresh()
    backup.create_snapshot(path, cfg["dir"], now=T - 3 * DAY)  # старая копия: тревога 48 ч и напоминание
    cap.lines.clear()
    with mock.patch("shutil.disk_usage", return_value=Usage(100 * GB, 90 * GB, 10 * GB)):
        run(n, ["snapshot_failed"], T)
        check("не больше 3", [m["text"][:8] for m in bot.sent], ["Тревога:", "Тревога:", "Мало мес"])
        assert any("потолок" in line for line in cap.lines), cap.lines
        run(n, [], T + HOUR)
        check("потолок держится", len(bot.sent), 3)
        run(n, [], T + DAY + HOUR)  # окно сутки прошло
        assert any(m["text"].startswith("Напоминание") for m in bot.sent), [m["text"][:12] for m in bot.sent]

    # ================= режим только API и пустой владелец =================
    path, cfg, bot, n = fresh()
    n_api = notify.Notifier(OWNER, lambda: None, path, cfg)
    run(n_api, ["snapshot_failed"], T)
    check("API: ничего не записано", db.get_meta(notify.META_KEYS["snapshot_failed"], path), None)
    n_none = notify.Notifier(None, lambda: bot, path, cfg)
    run(n_none, ["snapshot_failed"], T)
    check("без владельца ничего не отправлено", len(bot.sent), 0)

    app = create_app("tok", [], db_path=path, mode="api",
                     maintenance={"config": cfg, "db_path": path, "owner_id": None})
    with TestClient(app) as client:
        check("сервис работает без владельца", client.get("/health").json(), {"ok": True})

    # ================= сбой отправки =================
    path, cfg, bot, n = fresh()
    bot.fail_send = RuntimeError("token-123:SECRET-in-error-text")
    cap.lines.clear()
    run(n, ["snapshot_failed"], T)
    check("ошибка отправки логируется типом", [l for l in cap.lines if "не отправлено" in l],
          ["Уведомление владельцу не отправлено: RuntimeError"])
    assert "SECRET-in-error-text" not in " ".join(cap.lines)
    bot.fail_send = None
    run(n, ["snapshot_failed"], T + HOUR)
    check("повтора после сбоя нет", len(bot.sent), 0)

    class Hang:
        async def send_message(self, **kwargs):
            await asyncio.sleep(60)

    path, cfg, _, _ = fresh()
    hang_n = notify.Notifier(OWNER, lambda: Hang(), path, cfg)
    with mock.patch.object(notify, "SEND_TIMEOUT", 0.05):
        run(hang_n, ["snapshot_failed"], T)
    check("зависшая отправка прервана", any("TimeoutError" in l for l in cap.lines), True)

    # цикл переживает сбой уведомлений и продолжает работу
    class Broken:
        calls = 0

        async def run(self, events, now):
            Broken.calls += 1
            raise ValueError("boom")

    path, cfg, bot, n = fresh()

    async def loop_survives():
        task = asyncio.create_task(backup.maintenance_loop(cfg, path, first_delay=0, tick=0.01, notifier=Broken()))
        await asyncio.sleep(0.3)
        alive = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return alive

    check("цикл жив после ошибки уведомлений", asyncio.run(loop_survives()), True)
    assert Broken.calls >= 2, Broken.calls

    # ================= через жизненный цикл приложения (webhook, заглушка бота) =================
    path, cfg, _, _ = fresh()
    stub = StubApplication()
    with mock.patch.object(backup, "create_snapshot", return_value=None):
        app = create_app("tok", [], db_path=path, mode="webhook", public_url="https://x.example",
                         webhook_secret="s", application=stub,
                         maintenance={"config": cfg, "db_path": path, "owner_id": OWNER,
                                      "loop_args": {"first_delay": 0, "tick": 0.05}})
        with TestClient(app) as client:
            time.sleep(0.5)
            check("сервис работает", client.get("/health").json(), {"ok": True})
    check("уведомление ушло один раз", [m["chat_id"] for m in stub.bot.sent], [OWNER])

    path, cfg, _, _ = fresh()
    with mock.patch.object(backup, "create_snapshot", return_value=None):
        app = create_app("tok", [], db_path=path, mode="api",
                         maintenance={"config": cfg, "db_path": path, "owner_id": OWNER,
                                      "loop_args": {"first_delay": 0, "tick": 0.05}})
        with TestClient(app) as client:
            time.sleep(0.3)
    check("в режиме API ничего не записано",
          db.get_meta(notify.META_KEYS["snapshot_failed"], path), None)

    # ================= нет личных данных и значения OWNER_CHAT_ID =================
    path, cfg, bot, n = fresh()
    backup.create_snapshot(path, cfg["dir"], now=T - 3 * DAY)
    cap.lines.clear()
    with mock.patch("shutil.disk_usage", return_value=Usage(100 * GB, 90 * GB, 10 * GB)):
        run(n, ["snapshot_failed"], T)
        run(n, [], T + DAY + HOUR)
        bot.fail_send = RuntimeError("x")
        run(n, ["snapshot_failed"], T + 3 * DAY)
    blob = " ".join([m["text"] for m in bot.sent] + cap.lines)
    assert bot.sent and cap.lines
    for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), str(OWNER)):
        assert secret not in blob, "в сообщениях или логах есть %r" % secret
    for key in sqlite3.connect(path).execute("SELECT key, value FROM service_meta").fetchall():
        assert str(SECRET_ID) not in "".join(key) and SECRET_NAME not in "".join(key)

    # ================= миграция: база без service_meta =================
    path = os.path.join(tmp, "old.db")
    db.init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, ?, 100, ?, ?)", (SECRET_ID, SECRET_BALANCE, T, T))
    conn.execute("INSERT INTO roulette_rounds VALUES (?, 'r1', 17, 10, 0, '[]', ?)", (SECRET_ID, T))
    conn.execute("INSERT INTO chat_members VALUES ('room', ?, 'N', ?, ?)", (SECRET_ID, T, T))
    conn.execute("DROP TABLE service_meta")
    conn.commit()
    conn.close()
    db.init_db(path)
    db.init_db(path)  # повторный вызов безопасен
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("service_meta создана", "service_meta" in tables, True)
    check("данные сохранены",
          (conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (SECRET_ID,)).fetchone()[0],
           conn.execute("SELECT COUNT(*) FROM roulette_rounds").fetchone()[0],
           conn.execute("SELECT COUNT(*) FROM chat_members").fetchone()[0]),
          (SECRET_BALANCE, 1, 1))
    check("схема service_meta",
          [(r[1], r[2], r[5]) for r in conn.execute("PRAGMA table_info(service_meta)")],
          [("key", "TEXT", 1), ("value", "TEXT", 0)])
    conn.close()
    check("get_meta пусто", db.get_meta("нет", path), None)
    db.set_meta("k", 5, path)
    db.set_meta("k", 6, path)
    check("set_meta обновляет", db.get_meta("k", path), "6")
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v

print("Все проверки прошли")
