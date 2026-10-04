import asyncio
import io
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from unittest import mock

from fastapi.testclient import TestClient

import backup
import db
import verify_backup
from api import create_app

T = 1_760_000_000  # 2025-10-09, произвольный момент
DAY = 86400
SECRET_NAME, SECRET_ID, SECRET_BALANCE = "СекретноеИмя", 123456789, 7654321


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
try:
    main_db = os.path.join(tmp, "players.db")
    bdir = os.path.join(tmp, "backups")
    db.init_db(main_db)

    def sql(query, params=(), path=main_db):
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(query, params).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def seed():
        conn = sqlite3.connect(main_db)
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, ?, 100, ?, ?)", (SECRET_ID, SECRET_BALANCE, T, T))
        conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (2, 1000, 100, ?, ?)", (T, T))
        conn.executemany("INSERT INTO roulette_rounds VALUES (?, ?, 17, 10, 0, '[]', ?)",
                         [(SECRET_ID, "req-%06d" % i, T - i) for i in range(300)])
        conn.execute("INSERT INTO chat_members VALUES ('room', ?, ?, ?, ?)", (SECRET_ID, SECRET_NAME, T, T))
        conn.execute("INSERT INTO deletion_tombstones VALUES ('hash-secret-value', ?)", (T,))
        conn.commit()
        conn.close()

    seed()
    want_counts = {"players": 2, "roulette_rounds": 300, "chat_members": 1, "deletion_tombstones": 1}

    # ================= снимок =================
    path = backup.create_snapshot(main_db, bdir, now=T)
    assert path and os.path.isfile(path), "копия не создана"
    check("имя", os.path.basename(path), "players-20251009T085320Z.db")
    check("integrity_check и строки", backup.inspect_database(path), want_counts)
    check("права файла", stat.S_IMODE(os.stat(path).st_mode), 0o600)
    check("временных файлов нет", [n for n in os.listdir(bdir) if n.endswith(".tmp")], [])
    latest = os.path.join(bdir, "latest.db")
    check("latest.db", (backup.inspect_database(latest), stat.S_IMODE(os.stat(latest).st_mode)), (want_counts, 0o600))
    # latest обновляется
    sql("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (3, 1000, 100, ?, ?)", (T, T))
    path2 = backup.create_snapshot(main_db, bdir, now=T + 100)
    check("latest обновлён", backup.inspect_database(latest)["players"], 3)
    check("старая копия не тронута", backup.inspect_database(path)["players"], 2)
    check("основная база осталась", sql("SELECT COUNT(*) FROM players")[0][0], 3)
    sql("DELETE FROM players WHERE telegram_id = 3")

    # ================= копия во время параллельной записи =================
    big = os.path.join(tmp, "big.db")
    db.init_db(big)
    conn = sqlite3.connect(big)
    conn.executemany("INSERT INTO roulette_rounds VALUES (?, ?, 17, 10, 0, ?, ?)",
                     [(1, "big-%07d" % i, "x" * 200, T) for i in range(30000)])
    conn.commit()
    conn.close()
    errors, written = [], []
    stop = threading.Event()

    def writer():
        c = sqlite3.connect(big, timeout=30)
        i = 0
        try:
            while not stop.is_set():
                c.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 1000, 100, ?, ?)", (10_000 + i, T, T))
                c.commit()
                written.append(i)
                i += 1
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__ + ": " + str(exc))

    th = threading.Thread(target=writer)
    th.start()
    time.sleep(0.05)
    snap = backup.create_snapshot(big, os.path.join(tmp, "bbig"), now=T)
    stop.set()
    th.join()
    check("запись шла без ошибок", errors, [])
    assert snap, "копия при параллельной записи не создана"
    counts = backup.inspect_database(snap)
    check("копия целая", counts["roulette_rounds"], 30000)
    assert 0 < len(written) and counts["players"] <= len(written), (counts, len(written))
    check("основная база цела", sql("PRAGMA integrity_check", path=big), [("ok",)])

    # постоянная запись с перезапусками копирования: лимит времени, а не зависание
    stop2 = threading.Event()

    def hammer():
        c = sqlite3.connect(big, timeout=30)
        n = 0
        while not stop2.is_set():
            c.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 1, 1, 1, 1)", (900_000 + n, ))
            c.commit()
            n += 1
            time.sleep(0.001)

    th2 = threading.Thread(target=hammer)
    th2.start()
    try:
        with mock.patch.object(backup, "MAX_COPY_SECONDS", 1), mock.patch.object(backup, "PAGES_PER_STEP", 50):
            started = time.monotonic()
            snap2 = backup.create_snapshot(big, os.path.join(tmp, "bbig2"), now=T + 5)
            if sql("PRAGMA journal_mode", path=big)[0][0] == "wal":
                # в режиме WAL запись не блокирует чтение, копирование может успеть: тогда копия должна быть целой
                assert snap2 is None or backup.inspect_database(snap2)["roulette_rounds"] == 30000, "копия из WAL повреждена"
            else:
                check("постоянная запись: копия не сделана", snap2, None)
            assert time.monotonic() - started < 20, "копирование зависло"
    finally:
        stop2.set()
        th2.join()
    check("после срыва нет временных файлов", [n for n in os.listdir(os.path.join(tmp, "bbig2")) if n.endswith(".tmp")], [])

    # ================= сбои =================
    blocker = os.path.join(tmp, "not-a-dir")
    open(blocker, "w").close()
    cap.lines.clear()
    check("недоступный каталог", backup.create_snapshot(main_db, os.path.join(blocker, "sub"), now=T), None)
    assert any("Резервная копия не создана" in l for l in cap.lines)
    check("нет такой базы", backup.create_snapshot(os.path.join(tmp, "missing.db"), os.path.join(tmp, "b2"), now=T), None)
    check("после сбоя нет tmp", [n for n in os.listdir(os.path.join(tmp, "b2")) if n.endswith(".tmp")], [])
    # сбой проверки копии: итоговый файл не появляется, latest не меняется
    b3 = os.path.join(tmp, "b3")
    with mock.patch.object(backup, "inspect_database", side_effect=RuntimeError("секрет в тексте")):
        cap.lines.clear()
        check("сбой проверки", backup.create_snapshot(main_db, b3, now=T), None)
    check("итоговых и временных файлов нет", os.listdir(b3), [])
    check("в логе только тип", [l for l in cap.lines if "не создана" in l], ["Резервная копия не создана: RuntimeError"])
    # мало места
    b4 = os.path.join(tmp, "b4")
    with mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=10)):
        cap.lines.clear()
        check("мало места", backup.create_snapshot(main_db, b4, now=T), None)
    assert any("мало свободного места" in l for l in cap.lines)
    check("файлов нет", os.listdir(b4), [])

    # ================= ротация =================
    b5 = os.path.join(tmp, "b5")
    os.makedirs(b5)
    for i in range(10):
        open(os.path.join(b5, backup._utc_name(T + i * DAY)), "w").write("x")
    for foreign in ("notes.txt", "players.db", "players-bad.db", "latest.db", "players-20250101T000000Z.db.tmp"):
        open(os.path.join(b5, foreign), "w").write("keep")
    os.makedirs(os.path.join(b5, "players-20240101T000000Z.db.d"))
    outside = os.path.join(tmp, "outside-target.db")
    open(outside, "w").write("outside")
    os.symlink(outside, os.path.join(b5, "players-20230101T000000Z.db"))  # ссылка наружу, самая старая
    backup.rotate_backups(b5, 7, os.path.join(tmp, "unrelated.db"))
    left = backup.list_backups(b5)
    check("осталось 7 новейших + ссылка пропущена", len(left), 8)
    check("новейшие на месте", left[-7:], [backup._utc_name(T + i * DAY) for i in range(3, 10)])
    for foreign in ("notes.txt", "players.db", "players-bad.db", "latest.db", "players-20250101T000000Z.db.tmp"):
        assert os.path.exists(os.path.join(b5, foreign)), "удалён посторонний файл " + foreign
    assert os.path.exists(outside), "удалён файл за пределами каталога"
    assert os.path.islink(os.path.join(b5, "players-20230101T000000Z.db")), "ссылка удалена"
    # основная база внутри каталога не удаляется, даже если подходит под шаблон
    b6 = os.path.join(tmp, "b6")
    os.makedirs(b6)
    inside_main = os.path.join(b6, backup._utc_name(T))
    open(inside_main, "w").write("main")
    for i in range(1, 5):
        open(os.path.join(b6, backup._utc_name(T + i * DAY)), "w").write("x")
    backup.rotate_backups(b6, 2, inside_main)
    assert os.path.exists(inside_main), "основная база удалена"
    # ротация при создании копий
    b7 = os.path.join(tmp, "b7")
    for i in range(6):
        backup.create_snapshot(main_db, b7, now=T + i * 10, keep=3)
    check("keep=3 после 6 копий", len(backup.list_backups(b7)), 3)

    # ================= расписание =================
    b8 = os.path.join(tmp, "b8")
    backup.create_snapshot(main_db, b8, now=T)
    check("свежая копия: пропуск", backup.snapshot_due(b8, 24, T + 3600), False)
    check("через 24 часа пора", backup.snapshot_due(b8, 24, T + 24 * 3600), True)
    check("нет копий: пора", backup.snapshot_due(os.path.join(tmp, "empty"), 24, T), True)
    cfg = {"enabled": True, "dir": b8, "interval_hours": 24, "keep": 7, "rounds_days": 30, "member_days": 90}
    backup.run_maintenance_once(cfg, main_db, T + 3600, None)
    check("свежая есть: новая не создана", len(backup.list_backups(b8)), 1)
    backup.run_maintenance_once(cfg, main_db, T + 25 * 3600, None)
    check("старая: создана новая", len(backup.list_backups(b8)), 2)
    # отключение
    b9 = os.path.join(tmp, "b9")
    off = dict(cfg, enabled=False, dir=b9)
    asyncio.run(backup.maintenance_loop(off, main_db, first_delay=0, once=True))
    check("BACKUP_ENABLED=0: копий нет", os.path.exists(b9), False)
    on = dict(cfg, dir=b9)
    asyncio.run(backup.maintenance_loop(on, main_db, first_delay=0, once=True, clock=lambda: T + 5))
    check("включено: копия сделана", len(backup.list_backups(b9)), 1)

    # ================= первая копия после старта сервиса (настройки по умолчанию) =================
    b10 = os.path.join(tmp, "b10", "backups")
    dflt = backup.load_config({}, os.path.join(tmp, "b10", "players.db"))
    dflt = dict(dflt, dir=b10)
    cap.lines.clear()

    async def first_start():
        task = asyncio.create_task(backup.maintenance_loop(dflt, main_db, first_delay=0.05, tick=3600))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if backup.list_backups(b10):
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(first_start())
    check("после старта есть файл копии", len(backup.list_backups(b10)), 1)
    assert any("Резервная копия создана" in l and "результат=ok" in l for l in cap.lines), cap.lines
    assert any("Фоновая задача запущена" in l for l in cap.lines), cap.lines
    # пропуск (свежая копия есть, как после перезапуска сервиса) оставляет строку с причиной
    cap.lines.clear()
    backup.run_maintenance_once(dflt, main_db, int(time.time()) + 3600, None)
    skip = [l for l in cap.lines if "пропущена" in l]
    check("одна строка о пропуске", len(skip), 1)
    assert "свежая уже есть" in skip[0] and "players-" in skip[0], skip
    cap.lines.clear()
    backup.run_maintenance_once(dict(dflt, enabled=False), main_db, T, None)
    skip = [l for l in cap.lines if "пропущена" in l]
    check("отключена: одна строка", len(skip), 1)
    assert "BACKUP_ENABLED" in skip[0], skip
    cap.lines.clear()
    backup.run_maintenance_once(dflt, main_db, int(time.time()) + 3600, 1)  # не первый проход: тишина
    check("не первый проход: без строки", [l for l in cap.lines if "пропущена" in l], [])


    # ошибка внутри цикла: логируется только тип, цикл продолжает работу
    calls = []

    def flaky(config, db_path, now, last_purge):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("секретный текст ошибки")
        return now

    async def run_flaky():
        task = asyncio.create_task(backup.maintenance_loop(cfg, main_db, first_delay=0, tick=0.01))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "ended"

    cap.lines.clear()
    with mock.patch.object(backup, "run_maintenance_once", flaky):
        check("цикл отменяется", asyncio.run(run_flaky()), "cancelled")
    assert len(calls) >= 2, "после ошибки цикл остановился"
    check("в логе только тип", [l for l in cap.lines if "фоновой" in l][:1], ["Ошибка фоновой задачи: RuntimeError"])
    assert not any("секретный" in l for l in cap.lines)

    # задача в lifespan: запускается только при maintenance и отменяется при остановке
    events = []

    async def fake_loop(config, db_path, **kw):
        events.append("start")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    with mock.patch.object(backup, "maintenance_loop", fake_loop):
        with TestClient(create_app("123456:TEST-TOKEN-not-real", [], db_path=main_db)):
            pass
        check("по умолчанию задача не запускается", events, [])
        with TestClient(create_app("123456:TEST-TOKEN-not-real", [], db_path=main_db,
                                   maintenance={"config": cfg, "db_path": main_db})):
            time.sleep(0.2)
            check("задача запущена", events, ["start"])
        check("задача отменена при остановке", events, ["start", "cancelled"])

    # настройки: неверные значения заменяются умолчаниями, одно предупреждение
    c = backup.load_config({}, main_db)
    check("умолчания", (c["enabled"], c["interval_hours"], c["keep"], c["rounds_days"], c["member_days"], c["invalid"]),
          (True, 24, 7, 30, 90, []))
    check("каталог по умолчанию", c["dir"], os.path.join(tmp, "backups"))
    bad = {"BACKUP_ENABLED": "maybe", "BACKUP_INTERVAL_HOURS": "0", "BACKUP_KEEP": "-1",
           "ROUNDS_RETENTION_DAYS": "1", "CHAT_MEMBER_RETENTION_DAYS": "3", "BACKUP_DIR": "a\x00b"}
    c = backup.load_config(bad, main_db)
    check("неверные значения", (c["enabled"], c["interval_hours"], c["keep"], c["rounds_days"], c["member_days"]), (True, 24, 7, 30, 90))
    check("список неверных", sorted(c["invalid"]), sorted(bad))
    check("каталог заменён", c["dir"], os.path.join(tmp, "backups"))
    c = backup.load_config({"BACKUP_INTERVAL_HOURS": "abc"}, main_db)
    check("нечисловое", (c["interval_hours"], c["invalid"]), (24, ["BACKUP_INTERVAL_HOURS"]))
    c = backup.load_config({"BACKUP_ENABLED": "0", "BACKUP_DIR": "/x/y", "BACKUP_INTERVAL_HOURS": "1", "BACKUP_KEEP": "3",
                            "ROUNDS_RETENTION_DAYS": "2", "CHAT_MEMBER_RETENTION_DAYS": "7"}, main_db)
    check("допустимые значения", (c["enabled"], c["dir"], c["interval_hours"], c["keep"], c["rounds_days"], c["member_days"], c["invalid"]),
          (False, "/x/y", 1, 3, 2, 7, []))
    cap.lines.clear()
    backup.warn_config(backup.load_config(bad, main_db))
    warns = [l for l in cap.lines if "Неверные значения" in l]
    check("одно предупреждение", len(warns), 1)
    for name in bad:
        assert name in warns[0]
    cap.lines.clear()
    backup.warn_config(backup.load_config({}, main_db))
    check("без предупреждения", cap.lines, [])

    # ================= очистка старых данных =================
    pdb = os.path.join(tmp, "purge.db")
    db.init_db(pdb)
    NOW = T + 200 * DAY
    conn = sqlite3.connect(pdb)
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (1, 1000, 100, 1, 1), (2, 5, 100, 1, 1)")
    rounds = [("old", NOW - 40 * DAY), ("mid", NOW - 20 * DAY), ("new", NOW - DAY), ("h36", NOW - 36 * 3600), ("d3", NOW - 3 * DAY)]
    conn.executemany("INSERT INTO roulette_rounds VALUES (1, ?, 1, 1, 0, '[]', ?)", rounds)
    members = [("a", NOW - 100 * DAY), ("b", NOW - 50 * DAY), ("c", NOW - 5 * DAY), ("d", NOW - 8 * DAY)]
    conn.executemany("INSERT INTO chat_members VALUES ('r', ?, 'n', 1, ?)", [(i, t) for i, (_, t) in enumerate(members)])
    conn.execute("INSERT INTO deletion_tombstones VALUES ('old-t', ?), ('new-t', ?)", (NOW - 31 * DAY, NOW - 5 * DAY))
    conn.commit()
    conn.close()
    res = db.purge_old_data(now=NOW, db_path=pdb)
    check("удалено", res, {"roulette_rounds": 1, "farm_purchases": 0, "mines_games": 0, "mines_actions": 0, "keno_rounds": 0, "blackjack_games": 0, "blackjack_actions": 0, "chat_members": 1, "deletion_tombstones": 1})
    check("раунды остались", sorted(r[0] for r in sql("SELECT request_id FROM roulette_rounds", path=pdb)), ["d3", "h36", "mid", "new"])
    check("участники остались", sorted(r[0] for r in sql("SELECT telegram_id FROM chat_members", path=pdb)), [1, 2, 3])
    check("tombstone", [r[0] for r in sql("SELECT key_hash FROM deletion_tombstones", path=pdb)], ["new-t"])
    check("players не тронута", sql("SELECT COUNT(*) FROM players", path=pdb)[0][0], 2)
    # нижние границы: 1 сутки -> 2, 3 дня -> 7
    res = db.purge_old_data(now=NOW, db_path=pdb, rounds_days=1, member_days=3)
    check("границы", res, {"roulette_rounds": 2, "farm_purchases": 0, "mines_games": 0, "mines_actions": 0, "keno_rounds": 0, "blackjack_games": 0, "blackjack_actions": 0, "chat_members": 2, "deletion_tombstones": 0})  # раунды mid и d3; участники старше 7 суток
    check("36 часов остаётся", "h36" in [r[0] for r in sql("SELECT request_id FROM roulette_rounds", path=pdb)], True)
    check("участник 5 суток остаётся", 2 in [r[0] for r in sql("SELECT telegram_id FROM chat_members", path=pdb)], True)
    # пачки
    sql("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (9, 1, 1, 1, 1)", path=pdb)
    conn = sqlite3.connect(pdb)
    conn.executemany("INSERT INTO roulette_rounds VALUES (9, ?, 1, 1, 0, '[]', ?)", [("batch-%03d" % i, NOW - 60 * DAY) for i in range(35)])
    conn.commit()
    conn.close()
    with mock.patch.object(db.time, "sleep") as sl:
        res = db.purge_old_data(now=NOW, db_path=pdb, batch=10)
    check("пачками: всё удалено", res["roulette_rounds"], 35)
    check("пачки: 3 паузы между 4 транзакциями", sl.call_count, 3)
    check("players цела", sql("SELECT COUNT(*) FROM players", path=pdb)[0][0], 3)
    # старая схема без таблицы надгробий
    old = os.path.join(tmp, "old.db")
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE roulette_rounds (telegram_id INTEGER NOT NULL, request_id TEXT NOT NULL, number INTEGER NOT NULL, stake_total INTEGER NOT NULL, payout_total INTEGER NOT NULL, bets_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (telegram_id, request_id))")
    c.commit()
    c.close()
    check("без таблицы надгробий", db.purge_old_data(now=NOW, db_path=old)["deletion_tombstones"], 0)

    # ================= verify_backup.py =================
    out = io.StringIO()
    with redirect_stdout(out):
        rc = verify_backup.main(["verify_backup.py", path])
    text = out.getvalue()
    check("исправная копия: код", rc, 0)
    for expect in ("integrity_check: ok", "players: 2 строк", "roulette_rounds: 300 строк", "chat_members: 1 строк",
                   "deletion_tombstones: 1 строк", "Самая свежая запись: 2025-10-09T08:53:20Z", "исправна"):
        assert expect in text, (expect, text)
    for private in (SECRET_NAME, str(SECRET_ID), str(SECRET_BALANCE), "hash-secret-value", "req-000"):
        assert private not in text, "verify печатает личные данные: " + private
    before = (os.path.getsize(path), os.path.getmtime(path))
    proc = subprocess.run([sys.executable, "verify_backup.py", path], capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.abspath(__file__)))
    check("запуск как скрипта", proc.returncode, 0)
    check("файл не изменён", (os.path.getsize(path), os.path.getmtime(path)), before)
    os.chmod(path, 0o400)  # только чтение
    check("файл только для чтения", verify_backup.verify(path)[0], True)
    os.chmod(path, 0o600)
    # повреждённые файлы
    garbage = os.path.join(tmp, "garbage.db")
    open(garbage, "wb").write("это не база данных".encode("utf-8") * 100)
    proc = subprocess.run([sys.executable, "verify_backup.py", garbage], capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.abspath(__file__)))
    check("мусор: код 1", proc.returncode, 1)
    assert "ПРОБЛЕМА" in proc.stdout
    truncated = os.path.join(tmp, "truncated.db")
    data = open(path, "rb").read()
    open(truncated, "wb").write(data[: len(data) // 3])
    check("обрезанный файл", verify_backup.verify(truncated)[0], False)
    check("нет файла", verify_backup.verify(os.path.join(tmp, "nope.db"))[0], False)
    no_tables = os.path.join(tmp, "empty.db")
    sqlite3.connect(no_tables).close()
    ok, lines = verify_backup.verify(no_tables)
    check("нет таблиц", (ok, any("players" in l for l in lines)), (False, True))
    check("без аргумента", verify_backup.main(["verify_backup.py"]), 1)

    # ================= логи =================
    assert cap.lines, "логи не перехвачены"
    for line in cap.lines:
        for secret in (SECRET_NAME, str(SECRET_ID), str(SECRET_BALANCE), "hash-secret-value", "секретный", "req-000"):
            assert secret not in line, f"в логе есть {secret!r}: {line}"
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
