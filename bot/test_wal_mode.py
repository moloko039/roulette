import base64
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from unittest import mock

from nacl.public import PrivateKey

import backup
import backup_crypto
import db
import decrypt_backup
import verify_backup

SAFE = (3, 51, 3)
NOW = 1_760_000_000

# тест сам задаёт режим журнала в каждом случае и не зависит от оболочки (общий прогон с WAL тоже допустим)
_saved_env = {k: os.environ.pop(k, None) for k in ("SQLITE_JOURNAL_MODE", "DB_PATH")}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


log = logging.getLogger("depnaya.db")
cap = Capture()
old_level = log.level
log.setLevel(logging.DEBUG)
log.addHandler(cap)

tmp = tempfile.mkdtemp()
counter = [0]


def new_path():
    counter[0] += 1
    return os.path.join(tmp, "w%d.db" % counter[0])


def init(path, flag=None, version=SAFE):
    """init_db с заданным флагом и версией SQLite (версию подменяем, локальная может быть небезопасной)."""
    env = {"SQLITE_JOURNAL_MODE": flag} if flag is not None else {}
    with mock.patch.dict(os.environ, env):
        if flag is None:
            os.environ.pop("SQLITE_JOURNAL_MODE", None)
        with mock.patch.object(sqlite3, "sqlite_version_info", version):
            db._journal_warned.clear()
            db.init_db(path)


def journal(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def sync_of(path):
    conn = db._connect(path)
    try:
        return conn.execute("PRAGMA synchronous").fetchone()[0]
    finally:
        conn.close()


def players(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    finally:
        conn.close()


def warnings():
    return [m for lvl, m in cap.records if lvl == logging.WARNING]


def add(path, uid):
    db.get_player(uid, now=NOW, db_path=path)


try:
    # ================= границы безопасных версий =================
    safe = {(3, 44, 5): False, (3, 44, 6): True, (3, 44, 99): True, (3, 45, 0): False, (3, 43, 0): False,
            (3, 50, 6): False, (3, 50, 7): True, (3, 50, 99): True, (3, 51, 0): False, (3, 51, 2): False,
            (3, 51, 3): True, (3, 52, 0): True, (4, 0, 0): True, (3, 37, 2): False}
    for version, expected in safe.items():
        check("wal_version_safe%s" % (version,), db.wal_version_safe(version), expected)
    check("версия из 4 чисел", db.wal_version_safe((3, 51, 3, 0)), True)

    # ================= флаг не задан: ничего не переключается =================
    p = new_path()
    cap.records.clear()
    init(p)
    check("не задан: журнал прежний", journal(p), "delete")
    check("не задан: synchronous FULL", sync_of(p), 2)
    check("не задан: предупреждений нет", warnings(), [])
    add(p, 1)
    check("данные пишутся", players(p), 1)
    check("файла -wal нет", os.path.exists(p + "-wal"), False)

    # ================= flag wal =================
    p = new_path()
    cap.records.clear()
    init(p, "wal")
    check("wal: журнал", journal(p), "wal")
    check("wal: synchronous NORMAL в каждом соединении", sync_of(p), 1)
    check("wal: предупреждений нет", warnings(), [])
    add(p, 1)
    init(p, "wal")           # повторный init_db ничего не ломает
    init(p, "wal")
    add(p, 2)
    check("wal: данные целы после повторного init_db", players(p), 2)
    check("wal: журнал остался", journal(p), "wal")
    # флаг не задан на базе, уже переведённой в WAL: режим не трогаем, synchronous NORMAL по режиму базы
    init(p)
    check("не задан поверх WAL: режим не меняется", journal(p), "wal")
    check("не задан поверх WAL: synchronous NORMAL", sync_of(p), 1)
    # delete возвращает базу из WAL
    init(p, "delete")
    check("delete: журнал вернулся", journal(p), "delete")
    check("delete: synchronous FULL", sync_of(p), 2)
    check("delete: данные целы", players(p), 2)
    check("delete: файла -wal нет", os.path.exists(p + "-wal"), False)   # -shm в основной базе может остаться, он безвреден
    # туда и обратно без потерь
    for round_no in range(3):
        init(p, "wal")
        add(p, 10 + round_no)
        init(p, "delete")
        add(p, 20 + round_no)
    check("данные не потеряны при переключениях", players(p), 2 + 6)

    # ================= небезопасная версия и сбой включения =================
    p = new_path()
    cap.records.clear()
    init(p, "wal", version=(3, 51, 0))
    check("небезопасная версия: WAL не включён", journal(p), "delete")
    check("и synchronous прежний", sync_of(p), 2)
    w = warnings()
    check("одно предупреждение", len(w), 1)
    assert "3." in w[0] and "WAL-reset" in w[0], w
    add(p, 1)
    check("работа продолжается", players(p), 1)

    class FakeCursor:
        def fetchone(self):
            return ("delete",)

    real_execute = db.TimedConnection.execute

    def failing_execute(self, sql, *args):
        if sql.strip().upper() == "PRAGMA JOURNAL_MODE=WAL":
            return FakeCursor()          # SQLite «не включил» режим
        return real_execute(self, sql, *args)

    p = new_path()
    cap.records.clear()
    with mock.patch.object(db.TimedConnection, "execute", failing_execute):
        init(p, "wal")
    check("сбой включения: журнал прежний", journal(p), "delete")
    w = warnings()
    check("сбой включения: одно предупреждение", len(w), 1)
    assert "не включился" in w[0], w
    add(p, 1)
    check("сбой включения: работа продолжается", players(p), 1)

    def raising_execute(self, sql, *args):
        if sql.strip().upper() == "PRAGMA JOURNAL_MODE=WAL":
            raise sqlite3.OperationalError("database is locked")
        return real_execute(self, sql, *args)

    p = new_path()
    cap.records.clear()
    with mock.patch.object(db.TimedConnection, "execute", raising_execute):
        init(p, "wal")
    check("исключение при переключении: журнал прежний, предупреждение одно", (journal(p), len(warnings())), ("delete", 1))
    add(p, 1)

    # ================= неверное значение флага =================
    p = new_path()
    cap.records.clear()
    with mock.patch.dict(os.environ, {"SQLITE_JOURNAL_MODE": "bogus-value"}):
        db._journal_warned.clear()
        db.init_db(p)
        db.init_db(p)
    check("неверный флаг: журнал прежний", journal(p), "delete")
    w = warnings()
    check("одно предупреждение без значения", len(w), 1)
    assert "SQLITE_JOURNAL_MODE" in w[0] and "bogus-value" not in w[0], w

    # ================= строка при старте =================
    p = new_path()
    init(p, "wal")
    cap.records.clear()
    with mock.patch.dict(os.environ, {"SQLITE_JOURNAL_MODE": "wal"}):
        db.log_sqlite_mode(p)
    infos = [m for lvl, m in cap.records if lvl == logging.INFO]
    check("одна строка INFO", len(infos), 1)
    assert "SQLite " in infos[0] and "журнал=wal" in infos[0] and "synchronous=normal" in infos[0] and "wal" in infos[0].split("=")[-1], infos
    assert tmp not in infos[0] and "w" + str(counter[0]) not in infos[0], "путь в строке"

    # ================= копия базы в режиме WAL =================
    p = new_path()
    init(p, "wal")
    for uid in range(1, 6):
        add(p, uid)
    hold = db._connect(p)                   # открытое соединение: данные остаются в -wal, не переносятся в файл
    try:
        add(p, 100)
        wal_size = os.path.getsize(p + "-wal") if os.path.exists(p + "-wal") else 0
        assert wal_size > 0, "для проверки данные должны лежать в -wal"
        bdir = os.path.join(tmp, "backups")
        final = backup.create_snapshot(p, bdir, now=NOW)
        assert final is not None
        names = sorted(os.listdir(bdir))
        check("в каталоге копий нет -wal и -shm", [n for n in names if n.endswith("-wal") or n.endswith("-shm")], [])
        check("файлы копии", sorted(n for n in names if not n.endswith(".tmp")), ["latest.db", os.path.basename(final)])
        for copy in (final, os.path.join(bdir, "latest.db")):
            conn = sqlite3.connect(copy)
            check("копия: журнал delete", conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            check("копия содержит ещё не перенесённые данные", conn.execute("SELECT COUNT(*) FROM players").fetchone()[0], 6)
            conn.close()
            ok, lines = verify_backup.verify(copy)
            check("verify_backup принял копию", ok, True)
        # шифрование и расшифровка копии
        key = PrivateKey.generate()
        pub = base64.b64encode(bytes(key.public_key)).decode()
        key_file = os.path.join(tmp, "k.key")
        with open(key_file, "w") as f:
            f.write(base64.b64encode(bytes(key)).decode() + "\n")
        os.chmod(key_file, 0o600)
        with open(os.path.join(bdir, "latest.db"), "rb") as f:
            plain = f.read()
        enc = os.path.join(tmp, "copy.db.enc")
        with open(enc, "wb") as f:
            f.write(backup_crypto.encrypt_bytes(plain, pub))
        out = os.path.join(tmp, "restored.db")
        check("decrypt_backup принял копию из WAL", decrypt_backup.main(["decrypt_backup.py", enc, key_file, out]), 0)
        with open(out, "rb") as f:
            check("расшифровано без изменений", f.read(), plain)
        check("рядом с расшифрованной нет -wal и -shm", (os.path.exists(out + "-wal"), os.path.exists(out + "-shm")), (False, False))
    finally:
        hold.close()

    # копия во время параллельной записи из потоков
    p = new_path()
    init(p, "wal")
    for uid in range(1, 21):
        add(p, uid)
    stop = threading.Event()
    errors = []

    def writer(base):
        n = 0
        try:
            while not stop.is_set():
                add(p, base + n)
                n += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(10_000 * (i + 1),)) for i in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    results = []
    for i in range(3):
        bdir = os.path.join(tmp, "par%d" % i)
        results.append(backup.create_snapshot(p, bdir, now=NOW + i * 10))
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join()
    check("писатели без ошибок", errors, [])
    for final in results:
        assert final is not None, "копия во время записи не создана"
        ok, lines = verify_backup.verify(final)
        check("копия при параллельной записи цела", ok, True)
        conn = sqlite3.connect(final)
        assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] >= 20, "потеряны зафиксированные ранее данные"
        conn.close()
        check("и без -wal/-shm", [n for n in os.listdir(os.path.dirname(final)) if n.endswith(("-wal", "-shm"))], [])
finally:
    log.removeHandler(cap)
    log.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
