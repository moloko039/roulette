import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import shutil
import threading
import time

from fastapi.testclient import TestClient

import bot
import db
from api import create_app
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

FUT_ACCRUAL = __import__("time").time().__int__() + 10 * 86400   # старые базы в тестах миграции: метка в будущем, начисления нет

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
SECRET_NAME, SECRET_ID, SECRET_BALANCE = "СекретноеИмя", 424242421, 7654321
HOUR = 3600

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"  # без него удаление данных отключено


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


def new_path():
    counter[0] += 1
    return os.path.join(tmp, "t%d.db" % counter[0])


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def bet(kind, value=None, amount=10):
    return {"type": kind, "value": value, "amount": amount}


def staked(path, uid):
    return sql(path, "SELECT total_staked FROM players WHERE telegram_id = ?", (uid,))[0][0]


def add_player(path, uid, balance=1000, total=0):
    now = int(time.time())  # last_accrual = сейчас: начислений за время теста нет
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked) "
              "VALUES (?, ?, 100, ?, ?, ?)", (uid, balance, now, now, total))


def add_member(path, chat, uid, name, seen=None):
    now = int(time.time()) if seen is None else seen
    sql(path, "INSERT OR REPLACE INTO chat_members VALUES (?, ?, ?, ?, ?)", (chat, uid, name, now, now))


try:
    # ================= миграция =================
    old = new_path()
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("CREATE TABLE roulette_rounds (telegram_id INTEGER NOT NULL, request_id TEXT NOT NULL, "
                 "number INTEGER NOT NULL, stake_total INTEGER NOT NULL, payout_total INTEGER NOT NULL, "
                 "bets_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (telegram_id, request_id))")
    conn.execute("INSERT INTO players VALUES (1, 777, 100, %d, 1000), (2, 1500, 100, %d, 2000), "
                 "(3, 5, 100, %d, 3000)" % (FUT_ACCRUAL, FUT_ACCRUAL, FUT_ACCRUAL))
    conn.execute("INSERT INTO roulette_rounds VALUES (1, 'a', 17, 10, 360, '[]', 1500), "
                 "(1, 'b', 3, 20, 0, '[]', 1600), (3, 'c', 1, ?, 0, '[]', 1700), (3, 'd', 2, ?, 0, '[]', 1800)",
                 (MAX_SAFE_INT, MAX_SAFE_INT))
    conn.commit()
    conn.close()
    db.init_db(old)
    cols = {r[1]: (r[2], r[3], r[4]) for r in sql(old, "PRAGMA table_info(players)")}
    check("столбец добавлен", cols["total_staked"], ("INTEGER", 1, "0"))
    check("игроки целы", sql(old, "SELECT telegram_id, balance, rate, last_accrual, created_at FROM players ORDER BY 1"),
          [(1, 777, 100, FUT_ACCRUAL, 1000), (2, 1500, 100, FUT_ACCRUAL, 2000), (3, 5, 100, FUT_ACCRUAL, 3000)])
    check("начальные значения по раундам", sql(old, "SELECT telegram_id, total_staked FROM players ORDER BY 1"),
          [(1, 30), (2, 0), (3, MAX_SAFE_INT)])
    check("раунды целы", sql(old, "SELECT COUNT(*) FROM roulette_rounds")[0][0], 4)
    sql(old, "UPDATE players SET total_staked = 999 WHERE telegram_id = 2")
    db.init_db(old)
    db.init_db(old)
    check("повторный init_db ничего не меняет", sql(old, "SELECT telegram_id, total_staked FROM players ORDER BY 1"),
          [(1, 30), (2, 999), (3, MAX_SAFE_INT)])
    check("столбец один", [r[1] for r in sql(old, "PRAGMA table_info(players)")].count("total_staked"), 1)
    # база старой схемы без таблицы раундов
    old2 = new_path()
    conn = sqlite3.connect(old2)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("INSERT INTO players VALUES (1, 10, 100, 1, 1)")
    conn.commit()
    conn.close()
    db.init_db(old2)
    check("без раундов: 0", sql(old2, "SELECT total_staked FROM players")[0][0], 0)
    # новая база: столбец сразу есть, новые игроки с 0
    fresh = new_path()
    db.init_db(fresh)
    assert "total_staked" in [r[1] for r in sql(fresh, "PRAGMA table_info(players)")]
    db.get_player(5, now=1000, db_path=fresh)
    check("новый игрок: 0", staked(fresh, 5), 0)

    # ================= spin =================
    path = new_path()
    db.init_db(path)
    rng = lambda n: 5  # noqa: E731
    db.get_player(1, now=1000, db_path=path)
    r = db.spin_roulette(1, "req-00000001", [bet("number", 17, 10), bet("black", None, 20)], now=1000, db_path=path, rng=rng)
    check("счётчик = сумма ставок", staked(path, 1), 30)
    check("stake_total в ответе", r["stake_total"], 30)
    db.spin_roulette(1, "req-00000002", [bet("red", None, 5)], now=1000, db_path=path, rng=rng)
    check("растёт", staked(path, 1), 35)
    again = db.spin_roulette(1, "req-00000001", [bet("black", None, 20), bet("number", 17, 10)], now=1000, db_path=path, rng=lambda n: 0)
    check("повтор: replayed", again["replayed"], True)
    check("повтор не увеличивает", staked(path, 1), 35)
    try:
        db.spin_roulette(1, "req-00000003", [bet("red", None, 10_000)], now=1000, db_path=path, rng=rng)
        raise AssertionError("не хватило фишек, но спин прошёл")
    except InsufficientFunds:
        pass
    check("InsufficientFunds не увеличивает", staked(path, 1), 35)
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = 1", (MAX_SAFE_INT - 10,))
    try:
        db.spin_roulette(1, "req-00000004", [bet("number", 1, 100)], now=1000, db_path=path, rng=rng)
        raise AssertionError("лимит баланса не сработал")
    except BalanceLimit:
        pass
    check("BalanceLimit не увеличивает", staked(path, 1), 35)
    check("раундов записано 2", sql(path, "SELECT COUNT(*) FROM roulette_rounds")[0][0], 2)
    # потолок MAX_SAFE_INT
    sql(path, "UPDATE players SET balance = 1000, total_staked = ? WHERE telegram_id = 1", (MAX_SAFE_INT - 5,))
    db.spin_roulette(1, "req-00000005", [bet("red", None, 10)], now=1000, db_path=path, rng=rng)
    check("не выше MAX_SAFE_INT", staked(path, 1), MAX_SAFE_INT)
    db.spin_roulette(1, "req-00000006", [bet("red", None, 10)], now=1000, db_path=path, rng=rng)
    check("остаётся на потолке", staked(path, 1), MAX_SAFE_INT)

    # два параллельных потока не теряют прирост
    path = new_path()
    db.init_db(path)
    add_player(path, 7, balance=10**9)
    errors = []

    def worker(tag):
        try:
            for i in range(25):
                db.spin_roulette(7, "req-%s-%04d" % (tag, i), [bet("red", None, 3), bet("number", 1, 2)],
                                 now=1000, db_path=path, rng=rng)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("потоки без ошибок", errors, [])
    check("прирост не потерян", staked(path, 7), 2 * 25 * 5)
    check("совпадает с суммой раундов", sql(path, "SELECT SUM(stake_total) FROM roulette_rounds")[0][0], 250)

    # ================= рейтинг беседы =================
    path = new_path()
    db.init_db(path)
    client = TestClient(create_app(TOKEN, [], db_path=path))

    def auth(uid, chat_type="group", chat="chat-A", name="Игрок"):
        data = make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), chat_type=chat_type,
                              chat_instance=chat, first_name=name)
        return {"Authorization": "tma " + data}

    # 13 участников беседы A: балансы убывают с номером, ставки известны; игрок 1 без вращений
    sums = {}
    for i in range(1, 14):
        uid = 100 + i
        stake = 0 if i == 1 else i * 1000
        add_player(path, uid, balance=100000 - i * 100, total=stake)
        add_member(path, "chat-A", uid, "P%d" % i)
        sums[uid] = stake
    # участники другой беседы B не учитываются; игрок 150 состоит в обеих
    add_player(path, 150, balance=500, total=777)
    add_member(path, "chat-A", 150, "Both")
    add_member(path, "chat-B", 150, "Both")
    add_player(path, 160, balance=900, total=5555)
    add_member(path, "chat-B", 160, "OnlyB")
    expected_a = sum(sums.values()) + 777
    r = client.get("/api/chat/top", headers=auth(101, name="P1"))
    check("200", r.status_code, 200)
    body = r.json()
    check("поля ответа", sorted(body), ["chat_level", "chat_level_start_points", "chat_next_points", "chat_points", "chat_staked", "me", "scope", "set_names", "top"])
    check("chat_staked беседы A (включая не попавших в топ-10)", body["chat_staked"], expected_a)
    check("в топе 10", len(body["top"]), 10)
    check("порядок прежний", [e["name"] for e in body["top"]],
          ["P%d" % i for i in range(1, 11)])
    check("у каждого staked", [e["staked"] for e in body["top"]], [0] + [i * 1000 for i in range(2, 11)])
    check("поля записи", sorted(body["top"][0]), ["balance", "complete_sets", "cosmetics", "is_me", "level", "name", "rank", "staked"])
    check("me: staked 0 без вращений", body["me"]["staked"], 0)
    check("me.total по-прежнему число участников", body["me"]["total"], 14)
    check("поля me", sorted(body["me"]), ["balance", "level", "rank", "staked", "total"])
    r = client.get("/api/chat/top", headers=auth(113, name="P13"))
    me = r.json()["me"]
    check("me вне топа: staked", (me["rank"], me["staked"]), (13, 13000))
    check("chat_staked не зависит от вызывающего", r.json()["chat_staked"], expected_a)
    # беседа B: свои участники; игрок в двух беседах учтён в каждой
    r = client.get("/api/chat/top", headers=auth(160, chat="chat-B", name="OnlyB"))
    check("chat_staked беседы B", r.json()["chat_staked"], 777 + 5555)
    check("игрок в двух беседах: staked", [e["staked"] for e in r.json()["top"] if e["name"] == "Both"], [777])
    # scope none: новых полей нет
    for kw in ({"chat_type": "private", "chat": None}, {"chat_type": "sender", "chat": None}):
        data = make_init_data(TOKEN, user_id=101, auth_date=int(time.time()), chat_type=kw["chat_type"])
        r = client.get("/api/chat/top", headers={"Authorization": "tma " + data})
        check("scope none", r.json(), {"scope": "none"})
    # потолок chat_staked
    add_player(path, 901, balance=1, total=MAX_SAFE_INT)
    add_player(path, 902, balance=1, total=MAX_SAFE_INT)
    add_member(path, "chat-C", 901, "X")
    add_member(path, "chat-C", 902, "Y")
    r = client.get("/api/chat/top", headers=auth(901, chat="chat-C", name="X"))
    check("chat_staked не выше MAX_SAFE_INT", r.json()["chat_staked"], MAX_SAFE_INT)
    check("staked игроков не выше MAX_SAFE_INT", [e["staked"] for e in r.json()["top"]], [MAX_SAFE_INT, MAX_SAFE_INT])
    # нет вращений: у нового игрока 0, итог только из него
    r = client.get("/api/chat/top", headers=auth(555, chat="chat-new", name="Новый"))
    check("новая беседа", (r.json()["chat_staked"], r.json()["me"]["staked"]), (0, 0))
    # настоящая игра: вращение меняет staked в рейтинге
    spin = client.post("/api/roulette/spin", headers=auth(555, chat="chat-new"),
                       json={"request_id": "req-api-0001", "bets": [bet("red", None, 40), bet("number", 3, 10)]})
    check("spin 200", spin.status_code, 200)
    r = client.get("/api/chat/top", headers=auth(555, chat="chat-new", name="Новый"))
    check("после вращения staked и chat_staked", (r.json()["me"]["staked"], r.json()["chat_staked"]), (50, 50))

    # ================= /mydata и /deletemydata =================
    path = new_path()
    db.init_db(path)
    os.environ["DB_PATH"] = path
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, total=0)
    db.spin_roulette(SECRET_ID, "req-mydata-01", [bet("red", None, 70)], now=int(time.time()), db_path=path, rng=rng)
    add_member(path, "chat-D", SECRET_ID, SECRET_NAME)
    add_player(path, 801, balance=100, total=300)
    add_member(path, "chat-D", 801, "Other")
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: total_staked", export["player"]["total_staked"], 70)
    u = FakeUpdate("private", user_id=SECRET_ID)
    ctx = type("C", (), {})()
    asyncio.run(bot.mydata(u, ctx))
    doc = u.effective_message.documents[0]
    check("файл mydata.json", doc["filename"], "mydata.json")
    payload = json.loads(doc["data"].decode("utf-8"))
    check("/mydata: total_staked", payload["player"]["total_staked"], 70)
    c = TestClient(create_app(TOKEN, [], db_path=path))
    r = c.get("/api/chat/top", headers=auth(801, chat="chat-D", name="Other"))
    check("до удаления chat_staked", r.json()["chat_staked"], 70 + 300)
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удалена строка игрока", counts["players"], 1)
    check("счётчик удалён вместе с игроком", sql(path, "SELECT COUNT(*) FROM players WHERE telegram_id = ?", (SECRET_ID,))[0][0], 0)
    r = c.get("/api/chat/top", headers=auth(801, chat="chat-D", name="Other"))
    check("chat_staked уменьшился", r.json()["chat_staked"], 300)
    check("после удаления выгрузки нет", db.get_player_export(SECRET_ID, db_path=path), None)
    db.get_player(SECRET_ID, now=int(time.time()), db_path=path)
    check("новый игрок после удаления: 0", staked(path, SECRET_ID), 0)
    os.environ.pop("DB_PATH", None)

    # ================= очистка старых раундов =================
    path = new_path()
    db.init_db(path)
    add_player(path, 31, balance=1000, total=0)
    db.spin_roulette(31, "req-purge-01", [bet("red", None, 25)], now=1000, db_path=path, rng=rng)
    check("до очистки", staked(path, 31), 25)
    deleted = db.purge_old_data(now=1000 + 40 * 86400, db_path=path)
    check("раунд удалён очисткой", deleted["roulette_rounds"], 1)
    check("счётчик не изменился", staked(path, 31), 25)
    db.init_db(path)  # перезапуск после очистки: пересчёта по раундам нет
    check("после перезапуска тоже", staked(path, 31), 25)

    # ================= страница политики =================
    page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "<script" not in page.lower(), "на странице скрипт"
    assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
    assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
    assert "общая сумма ваших ставок за всё время (число; считается и хранится, пока существует ваш игровой профиль)" in page
    assert ("видят в рейтинге ваше имя в Telegram, баланс, общую сумму ваших ставок и уровень профиля "
            "(число, считается по накопленному игровому опыту), а также общую сумму ставок "
            "всех участников беседы (суммарное число без разбивки по людям)") in page
    sec2 = page[page.index("<h2>2."):page.index("<h2>3.")]
    sec4 = page[page.index("<h2>4."):page.index("<h2>5.")]
    assert "общая сумма ваших ставок" in sec2 and "общую сумму ставок всех участников" in sec4
    assert "Дата последнего обновления:" in page

    # ================= в логах нет id, имён, балансов и сумм ставок =================
    for secret in (str(SECRET_ID), SECRET_NAME, str(SECRET_BALANCE), "77777", "424242421"):
        for line in cap.lines:
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
