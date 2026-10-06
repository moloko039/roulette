import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import base64
import glob
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import collections
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from nacl.public import PrivateKey

import backup
import backup_crypto
import backup_send
import bot
import db
import notify
import verify_backup
from api import create_app
from stubs import FakeUpdate, StubApplication

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
T = 1_760_000_000
HOUR, DAY = 3600, 86400
OWNER = 777000111
SECRET_NAME, SECRET_ID, SECRET_BALANCE = "СекретноеИмя", 123456789, 7654321
Usage = collections.namedtuple("Usage", "total used free")
HEALTHY = Usage(100 * 2**30, 50 * 2**30, 50 * 2**30)

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("BACKUP_PUBLIC_KEY", "BACKUP_SEND_INTERVAL_DAYS", "BACKUP_SEND_MAX_MB", "OWNER_CHAT_ID",
             "PUBLIC_URL", "BACKUP_DIR", "BACKUP_ENABLED", "DB_PATH")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}


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


def new_keys():
    key = PrivateKey.generate()
    return base64.b64encode(bytes(key)).decode(), base64.b64encode(bytes(key.public_key)).decode()


PRIV, PUB = new_keys()
OTHER_PRIV, OTHER_PUB = new_keys()

tmp = tempfile.mkdtemp()
counter = [0]


def fresh(send=True, owner=OWNER, extra_env=None):
    """Новая база и каталог копий; возвращает (путь базы, конфиг копий, конфиг отправки, бот-заглушка, sender)."""
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
    cfg = backup.load_config({}, path)
    env = {"BACKUP_PUBLIC_KEY": PUB} if send else {}
    env.update(extra_env or {})
    sc = backup_send.load_config(env, owner)
    bot_stub = StubApplication().bot
    return path, cfg, sc, bot_stub, backup_send.EncryptedSender(owner, lambda: bot_stub, path, cfg, sc)


def run(coro):
    return asyncio.run(coro)


def meta(path, key):
    return db.get_meta(key, path)


def check_encrypted(doc):
    payload = doc["document"]
    assert payload.startswith(backup_crypto.MAGIC), "нет метки формата"
    assert b"SQLite format 3" not in payload, "в отправленных байтах открытый заголовок SQLite"
    assert "parse_mode" not in doc, "подпись с parse_mode"


try:
    with mock.patch("shutil.disk_usage", return_value=HEALTHY):
        # ================= шифрование =================
        data = b"SQLite format 3\x00" + os.urandom(500)
        enc = backup_crypto.encrypt_bytes(data, PUB)
        assert enc.startswith(backup_crypto.MAGIC) and backup_crypto.MAGIC == b"RBK1\x00\x00\x00\x00"
        assert b"SQLite format 3" not in enc
        check("туда и обратно", backup_crypto.decrypt_bytes(enc, PRIV), data)
        for bad in (OTHER_PRIV,):
            try:
                backup_crypto.decrypt_bytes(enc, bad)
                raise AssertionError("чужой ключ расшифровал")
            except ValueError:
                pass
        for bad in (b"XXXX" + enc[4:], enc[:20] + bytes([enc[20] ^ 1]) + enc[21:], b"", enc[:8]):
            try:
                backup_crypto.decrypt_bytes(bad, PRIV)
                raise AssertionError("повреждённые данные расшифрованы")
            except ValueError:
                pass
        check("разные шифртексты (запечатанный ящик)", backup_crypto.encrypt_bytes(data, PUB) != enc, True)
        for bad_key in ("", "не base64!!", base64.b64encode(b"x" * 31).decode(), base64.b64encode(b"x" * 33).decode(), "AAAA"):
            try:
                backup_crypto.parse_public_key(bad_key)
                raise AssertionError("неверный ключ принят: %r" % bad_key)
            except ValueError as exc:
                assert str(exc)
        check("ключ с пробелами", backup_crypto.parse_public_key("  " + PUB + "\n"), base64.b64decode(PUB))

        # ================= настройки и предупреждения =================
        for name, env, owner in [("нет ключа", {}, OWNER), ("неверный ключ", {"BACKUP_PUBLIC_KEY": "abc"}, OWNER),
                                 ("нет владельца", {"BACKUP_PUBLIC_KEY": PUB}, None)]:
            sc = backup_send.load_config(env, owner)
            check(name + ": отключена", sc["enabled"], False)
            cap.lines.clear()
            backup_send.warn_config(sc)
            warns = [l for l in cap.lines if "Отправка зашифрованных копий отключена" in l]

            check(name + ": одно предупреждение", len(warns), 1)
            assert PUB not in warns[0], "значение ключа в предупреждении"
            path, cfg, _, bot_stub, _ = fresh(send=False)
            sender = backup_send.EncryptedSender(owner, lambda: bot_stub, path, cfg, sc)
            check(name + ": события", run(sender.run_scheduled(T)), [])
            check(name + ": send_now", run(sender.send_now(bot_stub, T)), "disabled")
            check(name + ": ничего не отправлено", (bot_stub.documents, bot_stub.sent), ([], []))
            check(name + ": копий нет", backup.list_backups(cfg["dir"]), [])
        sc = backup_send.load_config({"BACKUP_PUBLIC_KEY": PUB}, OWNER)
        check("включена", (sc["enabled"], sc["interval_days"], sc["max_mb"], sc["invalid"]), (True, 7, 20, []))
        sc = backup_send.load_config({"BACKUP_PUBLIC_KEY": PUB, "BACKUP_SEND_INTERVAL_DAYS": "0",
                                      "BACKUP_SEND_MAX_MB": "x"}, OWNER)
        check("неверные числа заменены", (sc["interval_days"], sc["max_mb"], sorted(sc["invalid"])),
              (7, 20, ["BACKUP_SEND_INTERVAL_DAYS", "BACKUP_SEND_MAX_MB"]))
        sc = backup_send.load_config({"BACKUP_PUBLIC_KEY": PUB, "BACKUP_SEND_INTERVAL_DAYS": "1",
                                      "BACKUP_SEND_MAX_MB": "1"}, OWNER)
        check("допустимые числа", (sc["interval_days"], sc["max_mb"]), (1, 1))
        cap.lines.clear()
        backup_send.warn_config(backup_send.load_config({"BACKUP_PUBLIC_KEY": PUB, "BACKUP_SEND_MAX_MB": "q"}, OWNER))
        check("предупреждение про числа", len([l for l in cap.lines if "Неверные значения" in l]), 1)

        # ================= первая отправка при старте, содержимое =================
        path, cfg, sc, bot_stub, sender = fresh()
        n = notify.Notifier(OWNER, lambda: bot_stub, path, cfg, encrypted_send=True)
        cap.lines.clear()
        run(backup.maintenance_loop(cfg, path, first_delay=0, once=True, clock=lambda: T, notifier=n, sender=sender))
        check("первая отправка", len(bot_stub.documents), 1)
        doc = bot_stub.documents[0]
        check_encrypted(doc)
        check("получатель", doc["chat_id"], OWNER)
        names = backup.list_backups(cfg["dir"])
        check("копия создана и использована", len(names), 1)
        check("имя файла", doc["filename"], names[0] + ".enc")
        assert doc["filename"].startswith("players-") and doc["filename"].endswith(".db.enc")
        caption = doc["caption"]
        assert "Расшифровка: python3 bot/decrypt_backup.py ФАЙЛ ПРИВАТНЫЙ_КЛЮЧ ВЫХОД.db" in caption, caption
        assert "scp <домен сервиса>@ssh.railway.com:%s/latest.db" % cfg["dir"] in caption, caption
        assert "30 дней" in caption and "игроков: 2" in caption and "КБ" in caption and "UTC" in caption, caption
        assert len(caption) <= 1024
        check("backup_sent_at", meta(path, "backup_sent_at"), str(T))
        plain = backup_crypto.decrypt_bytes(doc["document"], PRIV)
        with open(os.path.join(cfg["dir"], names[0]), "rb") as f:
            check("расшифровано = копия", plain, f.read())
        out = os.path.join(tmp, "check.db")
        with open(out, "wb") as f:
            f.write(plain)
        check("расшифрованная копия цела", verify_backup.verify(out)[0], True)
        try:
            backup_crypto.decrypt_bytes(doc["document"], OTHER_PRIV)
            raise AssertionError("чужой ключ расшифровал отправленный файл")
        except ValueError:
            pass
        check("напоминания нет (его заменяет подпись)", [t for t in bot_stub.sent if "Напоминание" in t["text"]], [])
        sender_logs = [l for l in cap.lines if "Зашифрованная копия отправлена" in l]
        check("строка в логе об успехе", len(sender_logs), 1)

        # ================= расписание =================
        run(sender.run_scheduled(T + HOUR))
        run(sender.run_scheduled(T + 6 * DAY))
        check("до интервала повтора нет", len(bot_stub.documents), 1)
        sender2 = backup_send.EncryptedSender(OWNER, lambda: bot_stub, path, cfg, sc)   # перезапуск
        run(sender2.run_scheduled(T + 6 * DAY + 23 * HOUR))
        check("после перезапуска повтора нет", len(bot_stub.documents), 1)
        check("через интервал отправлено", run(sender2.run_scheduled(T + 7 * DAY)), [])
        check("повтор через 7 дней", len(bot_stub.documents), 2)
        check("backup_sent_at обновлён", meta(path, "backup_sent_at"), str(T + 7 * DAY))
        check_encrypted(bot_stub.documents[1])
        # свой интервал
        path1, cfg1, sc1, bot1, sender1 = fresh(extra_env={"BACKUP_SEND_INTERVAL_DAYS": "2"})
        run(sender1.run_scheduled(T))
        run(sender1.run_scheduled(T + DAY))
        check("интервал 2 дня: рано", len(bot1.documents), 1)
        run(sender1.run_scheduled(T + 2 * DAY))
        check("интервал 2 дня: пора", len(bot1.documents), 2)
        # бот не запущен: ничего не происходит, отметки не меняются
        path_n, cfg_n, sc_n, _, _ = fresh()
        none_sender = backup_send.EncryptedSender(OWNER, lambda: None, path_n, cfg_n, sc_n)
        check("бота нет", run(none_sender.run_scheduled(T)), [])
        check("отметки нет", meta(path_n, "backup_sent_at"), None)

        # ================= свежая и старая копии =================
        path, cfg, sc, bot_stub, sender = fresh()
        backup.create_snapshot(path, cfg["dir"], now=T - 3600)
        run(sender.run_scheduled(T))
        check("свежая копия использована, новая не создана", len(backup.list_backups(cfg["dir"])), 1)
        path, cfg, sc, bot_stub, sender = fresh()
        backup.create_snapshot(path, cfg["dir"], now=T - 3 * DAY)
        run(sender.run_scheduled(T))
        check("копия старше 48 часов: создана новая", len(backup.list_backups(cfg["dir"])), 2)
        check("отправлена новая", bot_stub.documents[0]["filename"], backup.list_backups(cfg["dir"])[-1] + ".enc")
        path, cfg, sc, bot_stub, sender = fresh()
        backup.create_snapshot(path, cfg["dir"], now=T - 3600)
        with open(os.path.join(cfg["dir"], backup.list_backups(cfg["dir"])[0]), "wb") as f:
            f.write(b"garbage")
        run(sender.run_scheduled(T))
        check("повреждённая свежая копия заменена новой", len(bot_stub.documents), 1)
        assert backup_crypto.decrypt_bytes(bot_stub.documents[0]["document"], PRIV).startswith(b"SQLite format 3")

        # ================= лимит размера =================
        path, cfg, sc, bot_stub, sender = fresh(extra_env={"BACKUP_SEND_MAX_MB": "1"})
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE pad (x BLOB)")
        conn.execute("INSERT INTO pad VALUES (zeroblob(?))", (2 * 1024 * 1024,))
        conn.commit()
        conn.close()
        n = notify.Notifier(OWNER, lambda: bot_stub, path, cfg, encrypted_send=True)
        cap.lines.clear()
        run(backup.maintenance_loop(cfg, path, first_delay=0, once=True, clock=lambda: T, notifier=n, sender=sender))
        check("большая копия не отправлена", bot_stub.documents, [])
        alarms = [t["text"] for t in bot_stub.sent if "больше лимита" in t["text"]]
        check("одна тревога", len(alarms), 1)
        assert any("больше лимита BACKUP_SEND_MAX_MB" in l for l in cap.lines)
        check("сбой отмечен", meta(path, "backup_send_failed_at"), str(T))
        check("успешной отправки нет", meta(path, "backup_sent_at"), None)
        run(backup.maintenance_loop(cfg, path, first_delay=0, once=True, clock=lambda: T + HOUR, notifier=n, sender=sender))
        check("повторная тревога в тот же день не отправлена", len([t for t in bot_stub.sent if "больше лимита" in t["text"]]), 1)

        # ================= сбой отправки: повтор и тревога не чаще раза в сутки =================
        path, cfg, sc, bot_stub, sender = fresh()
        n = notify.Notifier(OWNER, lambda: bot_stub, path, cfg, encrypted_send=True)
        bot_stub.fail_document = RuntimeError("секретный текст ошибки %s" % PUB)
        cap.lines.clear()

        def loop_at(now):
            run(backup.maintenance_loop(cfg, path, first_delay=0, once=True, clock=lambda: now, notifier=n,
                                        sender=sender))

        calls = []
        real_send = bot_stub.send_document

        async def counting(**kwargs):
            calls.append(1)
            return await real_send(**kwargs)

        bot_stub.send_document = counting
        loop_at(T)
        check("попытка сделана", len(calls), 1)
        check("сбой отмечен", meta(path, "backup_send_failed_at"), str(T))
        check("успеха нет", meta(path, "backup_sent_at"), None)
        alarms = [t["text"] for t in bot_stub.sent if "не отправлена в Telegram" in t["text"]]
        check("одна тревога", len(alarms), 1)
        assert "Последняя успешная отправка: нет" in alarms[0]
        loop_at(T + 2 * HOUR)
        loop_at(T + 23 * HOUR)
        check("повтор не чаще раза в 24 часа", len(calls), 1)
        check("тревога не чаще раза в 24 часа", len([t for t in bot_stub.sent if "не отправлена в Telegram" in t["text"]]), 1)
        loop_at(T + DAY + HOUR)
        check("повтор через 24 часа", len(calls), 2)
        check("вторая тревога через сутки", len([t for t in bot_stub.sent if "не отправлена в Telegram" in t["text"]]), 2)
        err = [l for l in cap.lines if "не отправлена" in l and "Зашифрованная" in l]
        assert err and all(l.endswith("RuntimeError") for l in err), err
        assert not any("секретный" in l for l in cap.lines)
        bot_stub.fail_document = None
        loop_at(T + 2 * DAY + 2 * HOUR)
        check("после починки отправлено", len(bot_stub.documents), 1)
        check("backup_sent_at после успеха", meta(path, "backup_sent_at"), str(T + 2 * DAY + 2 * HOUR))

        # ================= недельное напоминание =================
        for flag, expected in ((True, 0), (False, 1)):
            path, cfg, sc, bot_stub, _ = fresh()
            backup.create_snapshot(path, cfg["dir"], now=T)
            nn = notify.Notifier(OWNER, lambda: bot_stub, path, cfg, encrypted_send=flag)
            run(nn.run([], T + HOUR))
            check("напоминание при encrypted_send=%s" % flag,
                  len([t for t in bot_stub.sent if "Напоминание о резервной копии" in t["text"]]), expected)
        # файл не входит в потолок 3 текстовых сообщений
        path, cfg, sc, bot_stub, sender = fresh()
        n = notify.Notifier(OWNER, lambda: bot_stub, path, cfg, encrypted_send=True)
        db.set_meta(notify.SENT_LOG_KEY, ",".join(str(T - i) for i in range(1, 4)), path)
        run(backup.maintenance_loop(cfg, path, first_delay=0, once=True, clock=lambda: T, notifier=n, sender=sender))
        check("при исчерпанном потолке файл всё равно отправлен", len(bot_stub.documents), 1)

        # ================= подключение в приложении =================
        path, cfg, sc, _, _ = fresh()
        stub = StubApplication()
        loop_args = {"first_delay": 3600}
        with TestClient(create_app("123456:TEST-TOKEN-not-real", [], db_path=path, mode="polling", application=stub,
                                   maintenance={"config": cfg, "db_path": path, "owner_id": OWNER,
                                                "send_config": sc, "loop_args": loop_args})) as client:
            assert isinstance(stub.bot_data.get("backup_sender"), backup_send.EncryptedSender)
        stub = StubApplication()
        off = backup_send.load_config({}, OWNER)
        with TestClient(create_app("123456:TEST-TOKEN-not-real", [], db_path=path, mode="polling", application=stub,
                                   maintenance={"config": cfg, "db_path": path, "owner_id": OWNER,
                                                "send_config": off, "loop_args": loop_args})) as client:
            check("без ключа sender нет", stub.bot_data.get("backup_sender"), None)

        # ================= /backupnow =================
        def ctx_for(sender_obj, bot_obj):
            return SimpleNamespace(bot=bot_obj, application=SimpleNamespace(bot_data={"backup_sender": sender_obj}))

        def call(update, sender_obj, bot_obj):
            asyncio.run(bot.backupnow(update, ctx_for(sender_obj, bot_obj)))
            return update

        path, cfg, sc, bot_stub, sender = fresh()
        with mock.patch.dict(os.environ, {"OWNER_CHAT_ID": str(OWNER)}):
            bot.backupnow_limiter.last.clear()
            clock = [1000.0]
            with mock.patch.object(bot, "_clock", lambda: clock[0]):
                u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
                check("владелец: ответ", [r["text"] for r in u.replies], ["Отправляю"])
                check("владелец: файл отправлен", len(bot_stub.documents), 1)
                check_encrypted(bot_stub.documents[0])
                check("backup_sent_at не меняется", meta(path, "backup_sent_at"), None)
                check("метка сбоя не появляется", meta(path, "backup_send_failed_at"), None)
                u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
                assert u.replies and "Слишком часто" in u.replies[0]["text"], u.replies
                check("лимит: нового файла нет", len(bot_stub.documents), 1)
                clock[0] += 599
                u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
                check("лимит 10 минут ещё действует", len(bot_stub.documents), 1)
                clock[0] += 2
                u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
                check("через 10 минут можно", len(bot_stub.documents), 2)
                # чужие: ни ответа, ни записей в лог
                bot.backupnow_limiter.last.clear()
                before = list(cap.lines)
                stranger = call(FakeUpdate("private", user_id=OWNER + 1), sender, bot_stub)
                check("чужой: ответа нет", (stranger.replies, stranger.sent if hasattr(stranger, "sent") else []), ([], []))
                check("чужой: файлов нет", len(bot_stub.documents), 2)
                group = call(FakeUpdate("supergroup", user_id=OWNER, chat_id=-100500), sender, bot_stub)
                check("группа: ответа нет", group.replies, [])
                check("группа: файлов нет", len(bot_stub.documents), 2)
                nouser = FakeUpdate("private", user_id=OWNER)
                nouser.effective_user = None
                call(nouser, sender, bot_stub)
                check("нет пользователя: ответа нет", nouser.replies, [])
                noise = "Using selector"  # служебные строки asyncio при DEBUG
                check("в лог про чужих ничего не записано",
                      [l for l in cap.lines if not l.startswith(noise)], [l for l in before if not l.startswith(noise)])
                check("лимит не потрачен чужими", bot.backupnow_limiter.last, {})
                # отключено: владельцу причина
                u = call(FakeUpdate("private", user_id=OWNER), None, bot_stub)
                assert u.replies and "Отключено" in u.replies[0]["text"], u.replies
                # сбой при отправке: короткое сообщение, подробностей нет
                bot.backupnow_limiter.last.clear()
                bot_stub.fail_document = RuntimeError("секрет")
                u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
                check("сбой: два ответа", [r["text"] for r in u.replies],
                      ["Отправляю", "Не удалось отправить, подробности в логах сервиса"])
                bot_stub.fail_document = None
        with mock.patch.dict(os.environ):
            os.environ.pop("OWNER_CHAT_ID", None)
            bot.backupnow_limiter.last.clear()
            u = call(FakeUpdate("private", user_id=OWNER), sender, bot_stub)
            check("OWNER_CHAT_ID не задан: молчание", u.replies, [])
        # не в меню и не в /help, но обработчик зарегистрирован
        menu = [c.command for c in bot.PRIVATE_COMMANDS + bot.GROUP_COMMANDS]
        assert "backupnow" not in menu, menu
        assert "backupnow" not in bot.PRIVATE_HELP and "backupnow" not in bot.GROUP_HELP
        asyncio.set_event_loop(asyncio.new_event_loop())
        real = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
        assert [h for h in real.handlers[0] if getattr(h, "commands", None) and "backupnow" in h.commands]

    # ================= в серверном коде нет закрытого ключа и открытой отправки =================
    server_files = [p for p in glob.glob(os.path.join(HERE, "*.py"))
                    if not os.path.basename(p).startswith("test_")
                    and os.path.basename(p) not in ("backup_keys.py", "decrypt_backup.py", "backup_crypto.py",
                                                    "stubs.py", "tg_testutil.py", "run_tests.py")]
    assert server_files
    for p in server_files:
        text = open(p, encoding="utf-8").read()
        for token in ("PrivateKey", "private_key", "PRIVATE_KEY", "decrypt_bytes", "parse_private_key",
                      "BACKUP_PRIVATE", "import decrypt_backup", "import backup_keys", "from decrypt_backup",
                      "from backup_keys"):
            assert token not in text, "%s: найдено %s" % (os.path.basename(p), token)
        if os.path.basename(p) != "backup_send.py":
            assert "send_document(" not in text, "отправка файла вне backup_send.py: " + os.path.basename(p)
    send_src = open(os.path.join(HERE, "backup_send.py"), encoding="utf-8").read()
    check("один вызов send_document", send_src.count("send_document("), 1)
    assert "document=payload" in send_src and "encrypt_bytes(" in send_src
    assert "latest.db" not in send_src.replace("/latest.db ~/roulette-backups", ""), "отправка latest.db"

    # ================= скрипты владельца =================
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["HOME"] = home

    def script(name, *args, **kw):
        r = subprocess.run([sys.executable, os.path.join(HERE, name)] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, env=kw.get("env", env))
        return r.returncode, r.stdout

    keyfile = os.path.join(tmp, "keys", "owner.key")
    os.makedirs(os.path.dirname(keyfile))
    code, out = script("backup_keys.py", "generate", "--out", keyfile)
    check("generate: код 0", code, 0)
    check("права 0600", oct(os.stat(keyfile).st_mode & 0o777), "0o600")
    priv_text = open(keyfile).read().strip()
    check("закрытый ключ 32 байта", len(base64.b64decode(priv_text)), 32)
    assert priv_text not in out, "закрытый ключ напечатан"
    pub_line = [l for l in out.splitlines() if "Открытый ключ" in l][0].split(": ")[-1].strip()
    assert keyfile in out
    code, out2 = script("backup_keys.py", "public", "--key", keyfile)
    check("public: код 0", (code, out2.strip()), (0, pub_line))
    assert priv_text not in out2
    before = open(keyfile).read()
    code, out3 = script("backup_keys.py", "generate", "--out", keyfile)
    check("не перезаписывает: код 1", code, 1)
    check("файл не изменён", open(keyfile).read(), before)
    inside = os.path.join(ROOT, "zz-test-private.key")
    code, out4 = script("backup_keys.py", "generate", "--out", inside)
    check("внутри репозитория: отказ", (code, os.path.exists(inside)), (1, False))
    if os.path.exists(inside):
        os.remove(inside)
    other_repo = os.path.join(tmp, "otherrepo")
    os.makedirs(other_repo)
    subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
    code, _ = script("backup_keys.py", "generate", "--out", os.path.join(other_repo, "k.key"))
    check("в другом git-репозитории: отказ", (code, os.path.exists(os.path.join(other_repo, "k.key"))), (1, False))
    code, out5 = script("backup_keys.py", "generate")  # путь по умолчанию в домашней папке
    check("по умолчанию: код 0", code, 0)
    default_key = os.path.join(home, "roulette-backup-private.key")
    check("по умолчанию: файл и права", oct(os.stat(default_key).st_mode & 0o777), "0o600")
    code, _ = script("backup_keys.py", "public", "--key", os.path.join(tmp, "нет"))
    check("public без файла: код 1", code, 1)
    check("без подкоманды: код 1", script("backup_keys.py")[0], 1)

    # расшифровка
    path, cfg, sc, _, _ = fresh()
    snap = backup.create_snapshot(path, cfg["dir"], now=T)
    with open(snap, "rb") as f:
        plain = f.read()
    enc_path = os.path.join(tmp, "copy.db.enc")
    with open(enc_path, "wb") as f:
        f.write(backup_crypto.encrypt_bytes(plain, pub_line))
    out_db = os.path.join(tmp, "restored.db")
    code, out6 = script("decrypt_backup.py", enc_path, keyfile, out_db)
    check("decrypt: код 0", code, 0)
    assert "integrity_check: ok" in out6 and "players: 2 строк" in out6 and "Traceback" not in out6, out6
    check("decrypt: права 0600", oct(os.stat(out_db).st_mode & 0o777), "0o600")
    with open(out_db, "rb") as f:
        check("decrypt: содержимое", f.read(), plain)
    assert str(SECRET_ID) not in out6 and SECRET_NAME not in out6
    code, out7 = script("decrypt_backup.py", enc_path, keyfile, out_db)
    check("не перезаписывает: код 1", code, 1)
    wrong = os.path.join(tmp, "keys", "wrong.key")
    code, _ = script("backup_keys.py", "generate", "--out", wrong)
    out_wrong = os.path.join(tmp, "wrong.db")
    code, out8 = script("decrypt_backup.py", enc_path, wrong, out_wrong)
    check("неверный ключ: код 1", (code, os.path.exists(out_wrong)), (1, False))
    assert "Traceback" not in out8 and "неверный ключ" in out8, out8
    blob = bytearray(open(enc_path, "rb").read())
    blob[30] ^= 0xFF
    bad_path = os.path.join(tmp, "bad.enc")
    open(bad_path, "wb").write(bytes(blob))
    out_bad = os.path.join(tmp, "bad.db")
    code, out9 = script("decrypt_backup.py", bad_path, keyfile, out_bad)
    check("повреждённый файл: код 1", (code, os.path.exists(out_bad)), (1, False))
    assert "Traceback" not in out9
    nomagic = os.path.join(tmp, "nomagic.enc")
    open(nomagic, "wb").write(b"not an encrypted backup")
    code, out10 = script("decrypt_backup.py", nomagic, keyfile, os.path.join(tmp, "nm.db"))
    check("нет метки: код 1", code, 1)
    assert "Traceback" not in out10 and "RBK1" in out10
    check("мало аргументов: код 1", script("decrypt_backup.py", enc_path)[0], 1)
    code, out11 = script("decrypt_backup.py", os.path.join(tmp, "нет.enc"), keyfile, os.path.join(tmp, "x.db"))
    check("нет файла: код 1", code, 1)
    assert "Traceback" not in out11

    # ================= политика конфиденциальности =================
    page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "<script" not in page.lower(), "на странице скрипт"
    assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
    assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
    assert "Раз в неделю зашифрованная копия отправляется мне в личный чат Telegram: файл шифруется до отправки, расшифровать его могу только я." in page
    assert "Копии, отправленные мне в Telegram, я удаляю не позднее чем через 30 дней." in page
    assert "Дата последнего обновления:" in page

    # ================= в логах и текстах нет ключей, id, имён, балансов =================
    texts = list(cap.lines)
    for secret in (PRIV, PUB, OTHER_PRIV, OTHER_PUB, priv_text, str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE)):
        for line in texts:
            assert secret not in line, "секрет в логе: " + line[:80]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
