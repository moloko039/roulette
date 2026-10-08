import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import logging
import os
import re
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import antiabuse
import bot
from api import create_app
from db import (chat_top, delete_player_data, get_player, init_db, spin_roulette, touch_chat_member)
from roulette import InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

# тест не зависит от окружения и bot/.env: на время теста эти переменные очищаются
_ENV_KEYS = ("GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

TOKEN = "123456:TEST-TOKEN-not-real"
SECRET = "test-secret-AAA-not-real"
DAY = 86400
T = 1_700_000_000


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def counts(path, uid):
    return tuple(sql(path, "SELECT COUNT(*) FROM %s WHERE telegram_id = ?" % t, (uid,))[0][0]
                 for t in ("players", "roulette_rounds", "chat_members"))


def tombstones(path):
    return sql(path, "SELECT key_hash, deleted_at FROM deletion_tombstones")


class At:
    def __init__(self, offset):
        real = time.time
        self.patch = mock.patch("time.time", lambda: real() + offset)

    def __enter__(self):
        self.patch.start()

    def __exit__(self, *a):
        self.patch.stop()


def auth(uid, chat=False):
    kw = dict(chat_type="group", chat_instance="room-1") if chat else {}
    data = make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Имя", **kw)
    return {"Authorization": "tma " + data}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


bet = lambda: [{"type": "red", "value": None, "amount": 1}]  # noqa: E731
old_secret = os.environ.get("TOMBSTONE_SECRET")
old_db = os.environ.get("DB_PATH")
cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    os.environ["TOMBSTONE_SECRET"] = SECRET
    os.environ["DB_PATH"] = path
    init_db(path)
    check("срок защиты задан в одном месте", (antiabuse.REGISTRATION_COOLDOWN_DAYS, antiabuse.COOLDOWN_SECONDS), (30, 30 * DAY))
    check("в коде нет второй константы срока", [], [f for f in ("db.py", "bot.py", "api.py")
                                                  if re.search(r"\b30\s*\*\s*86400|\bCOOLDOWN_DAYS\s*=", open(f, encoding="utf-8").read())])

    # ---------- удаление создаёт tombstone ----------
    UID = 987654321
    get_player(UID, now=T, db_path=path)
    spin_roulette(UID, "request-aaaa-0001", bet(), now=T, db_path=path, rng=lambda n: 0)
    touch_chat_member("room-1", UID, "Имя", now=T, db_path=path)
    OTHER = 555000111
    get_player(OTHER, now=T, db_path=path)
    other_before = counts(path, OTHER)
    res = delete_player_data(UID, db_path=path, now=T + 5)
    check("счётчики прежнего вида (и покупки фермы)", res, {"players": 1, "roulette_rounds": 1, "chat_members": 1, "farm_purchases": 0, "mines_games": 0, "keno_rounds": 0, "slot_rounds": 0, "blackjack_games": 0, "crash_games": 0, "hilo_games": 0, "transfers": 0, "transfers_anonymized": 0, "player_best_win": 0, "achievement_progress": 0, "referrals_as_invitee": 0, "referrals_as_referrer": 0, "referral_codes": 0})
    rows = tombstones(path)
    check("одна запись", len(rows), 1)
    h, deleted_at = rows[0]
    check("хэш по алгоритму", h, antiabuse.key_hash(UID, SECRET.encode()))
    assert h != str(UID) and str(UID) not in h and len(h) == 64 and re.fullmatch(r"[0-9a-f]{64}", h), h
    check("время удаления", deleted_at, T + 5)
    check("в таблице нет столбца с id", [r[1] for r in sql(path, "PRAGMA table_info(deletion_tombstones)")], ["key_hash", "deleted_at"])
    assert str(UID) not in repr(sql(path, "SELECT * FROM deletion_tombstones")), "id попал в запись"
    check("данные игрока удалены", counts(path, UID), (0, 0, 0))
    check("чужой игрок цел", counts(path, OTHER), other_before)
    # другой секрет даёт другой хэш (по хэшу без секрета игрока не найти)
    assert antiabuse.key_hash(UID, b"another") != h

    # без существующего игрока tombstone не создаётся
    check("нет игрока", delete_player_data(424242, db_path=path, now=T + 6), {"players": 0, "roulette_rounds": 0, "chat_members": 0, "farm_purchases": 0, "mines_games": 0, "keno_rounds": 0, "slot_rounds": 0, "blackjack_games": 0, "crash_games": 0, "hilo_games": 0, "transfers": 0, "transfers_anonymized": 0, "player_best_win": 0, "achievement_progress": 0, "referrals_as_invitee": 0, "referrals_as_referrer": 0, "referral_codes": 0})
    check("tombstone не создан", len(tombstones(path)), 1)
    # повторное удаление сразу ничего не меняет и не добавляет записей
    check("повторное удаление", delete_player_data(UID, db_path=path, now=T + 7), {"players": 0, "roulette_rounds": 0, "chat_members": 0, "farm_purchases": 0, "mines_games": 0, "keno_rounds": 0, "slot_rounds": 0, "blackjack_games": 0, "crash_games": 0, "hilo_games": 0, "transfers": 0, "transfers_anonymized": 0, "player_best_win": 0, "achievement_progress": 0, "referrals_as_invitee": 0, "referrals_as_referrer": 0, "referral_codes": 0})
    check("запись прежняя", tombstones(path), [(h, T + 5)])

    # ---------- регистрация в период защиты: баланс 0 и скорость 100 ----------
    p = get_player(UID, now=T + 100, db_path=path)
    check("get_player в период защиты", (p["balance"], p["rate"], p["last_accrual"]), (0, 100, T + 100))
    # начисление по часам идёт как у всех
    p = get_player(UID, now=T + 100 + 2 * 3600, db_path=path)
    check("начисление идёт", p["balance"], 200)
    # игрок без tombstone получает 1000
    check("обычный новый игрок", get_player(777001, now=T + 100, db_path=path)["balance"], 1000)

    # по истечении 30 дней: стартовые 1000 (подмена времени)
    delete_player_data(UID, db_path=path, now=T + 200)  # снова: окно считается заново от T + 200
    check("граница: ровно 30 дней ещё защита? нет, окно закрыто", get_player(UID, now=T + 200 + 30 * DAY, db_path=path)["balance"], 1000)
    delete_player_data(UID, db_path=path, now=T + 300)
    check("без одной секунды 30 дней: защита", get_player(UID, now=T + 300 + 30 * DAY - 1, db_path=path)["balance"], 0)
    delete_player_data(UID, db_path=path, now=T + 400)
    check("через 31 день", get_player(UID, now=T + 400 + 31 * DAY, db_path=path)["balance"], 1000)

    # ---------- повторное удаление после повторной регистрации обновляет tombstone ----------
    V = 6600001
    get_player(V, now=T, db_path=path)
    delete_player_data(V, db_path=path, now=T)
    check("через 10 дней: 0", get_player(V, now=T + 10 * DAY, db_path=path)["balance"], 0)
    delete_player_data(V, db_path=path, now=T + 10 * DAY)
    key = antiabuse.key_hash(V, SECRET.encode())
    check("tombstone обновлён", sql(path, "SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?", (key,)), [(T + 10 * DAY,)])
    check("через 35 дней от первого удаления всё ещё защита", get_player(V, now=T + 35 * DAY, db_path=path)["balance"], 0)
    delete_player_data(V, db_path=path, now=T + 35 * DAY)
    check("через 41 день от первого, 6 от последнего: защита", get_player(V, now=T + 41 * DAY, db_path=path)["balance"], 0)
    delete_player_data(V, db_path=path, now=T + 41 * DAY)
    check("через 72 дня: 1000", get_player(V, now=T + 72 * DAY, db_path=path)["balance"], 1000)

    # ---------- все пути создания игрока ----------
    client = TestClient(create_app(TOKEN, [], db_path=path))
    now0 = int(time.time())

    def tomb(uid):
        get_player(uid, now=now0, db_path=path)
        delete_player_data(uid, db_path=path, now=now0)

    # /api/me
    tomb(910001)
    r = client.get("/api/me", headers=auth(910001)).json()
    check("/api/me в период защиты", (r["balance"], r["rate"]), (0, 100))
    # первый спин нового игрока: баланса нет, раунд не проходит, строка не создаётся
    tomb(910002)
    try:
        spin_roulette(910002, "request-bbbb-0001", bet(), now=now0, db_path=path, rng=lambda n: 0)
        raise AssertionError("спин нового игрока в период защиты прошёл")
    except InsufficientFunds:
        pass
    r = client.post("/api/roulette/spin", json={"request_id": "request-bbbb-0002", "bets": bet()}, headers=auth(910002))
    check("API спин: 409", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    # спин игрока, который уже зарегистрирован в период защиты (0 фишек)
    client.get("/api/me", headers=auth(910002))
    r = client.post("/api/roulette/spin", json={"request_id": "request-bbbb-0003", "bets": bet()}, headers=auth(910002))
    check("сценарий абьюза: заходит, получает 0, ставить нечем", (r.status_code, r.json()["detail"]), (409, "insufficient_funds"))
    check("баланс 0", client.get("/api/me", headers=auth(910002)).json()["balance"], 0)
    # /api/chat/top
    tomb(910003)
    body = client.get("/api/chat/top", headers=auth(910003, chat=True)).json()
    check("/api/chat/top в период защиты", (body["me"]["balance"], body["top"][0]["balance"]), (0, 0))
    # прямой вызов chat_top
    tomb(910004)
    check("chat_top напрямую", chat_top("room-9", 910004, "Имя", now=now0, db_path=path)["me"]["balance"], 0)
    # через 30+ дней всё выдаётся заново по каждому пути (подмена времени)
    for uid in (910011, 910012, 910013):
        tomb(uid)
    with At(31 * DAY):
        check("/api/me через 31 день", client.get("/api/me", headers=auth(910011)).json()["balance"], 1000)
        r = client.post("/api/roulette/spin", json={"request_id": "request-cccc-0001", "bets": bet()}, headers=auth(910012))
        check("спин через 31 день", r.status_code, 200)
        body = client.get("/api/chat/top", headers=auth(910013, chat=True)).json()
        check("/api/chat/top через 31 день", body["me"]["balance"], 1000)

    # ---------- просроченные tombstone удаляются ----------
    real_now = int(time.time())
    sql(path, "INSERT OR REPLACE INTO deletion_tombstones VALUES ('old-hash', ?)", (real_now - 31 * DAY,))
    sql(path, "INSERT OR REPLACE INTO deletion_tombstones VALUES ('fresh-hash', ?)", (real_now - 5 * DAY,))
    init_db(path)
    keys = {r[0] for r in tombstones(path)}
    assert "old-hash" not in keys and "fresh-hash" in keys, keys
    sql(path, "INSERT OR REPLACE INTO deletion_tombstones VALUES ('old-hash-2', ?)", (real_now - 40 * DAY,))
    get_player(930001, now=real_now, db_path=path)
    delete_player_data(930001, db_path=path, now=real_now)
    keys = {r[0] for r in tombstones(path)}
    assert "old-hash-2" not in keys and "fresh-hash" in keys, keys
    sql(path, "DELETE FROM deletion_tombstones WHERE key_hash = 'fresh-hash'")

    # ---------- одна транзакция: ошибка откатывает всё ----------
    W = 7700001
    get_player(W, now=T, db_path=path)
    spin_roulette(W, "request-dddd-0001", bet(), now=T, db_path=path, rng=lambda n: 0)
    touch_chat_member("room-2", W, "Имя", now=T, db_path=path)
    before = counts(path, W)
    tomb_before = tombstones(path)
    sql(path, "CREATE TRIGGER block_tomb BEFORE INSERT ON deletion_tombstones BEGIN SELECT RAISE(ABORT, 'нельзя'); END")
    try:
        delete_player_data(W, db_path=path, now=T)
        raise AssertionError("ошибка не пробросилась")
    except sqlite3.DatabaseError:
        pass
    sql(path, "DROP TRIGGER block_tomb")
    check("откат: данные на месте", counts(path, W), before)
    check("откат: tombstone не добавлен", tombstones(path), tomb_before)

    # ---------- без TOMBSTONE_SECRET ----------
    X, Y = 7700002, 7700003
    get_player(X, now=T, db_path=path)
    get_player(Y, now=T, db_path=path)
    before = (counts(path, X), tombstones(path))
    os.environ.pop("TOMBSTONE_SECRET")
    try:
        delete_player_data(X, db_path=path, now=T)
        raise AssertionError("удаление без секрета прошло")
    except antiabuse.TombstoneUnavailable:
        pass
    check("без секрета ничего не удалено", (counts(path, X), tombstones(path)), before)
    # tombstone есть, но секрета нет: регистрация как раньше
    sql(path, "INSERT OR REPLACE INTO deletion_tombstones VALUES (?, ?)", (antiabuse.key_hash(8800001, SECRET.encode()), int(time.time())))
    check("без секрета регистрация как раньше", get_player(8800001, now=int(time.time()), db_path=path)["balance"], 1000)
    sql(path, "DELETE FROM players WHERE telegram_id = 8800001")
    sql(path, "DELETE FROM deletion_tombstones WHERE key_hash = ?", (antiabuse.key_hash(8800001, SECRET.encode()),))

    # бот без секрета: /deletemydata и кнопка ничего не удаляют
    def run(handler, update):
        asyncio.run(handler(update, None))
        return update

    for lim in (bot.delete_limiter,):
        lim.last.clear()
    with mock.patch.dict(os.environ, {"DEVELOPER_CONTACT": "dev@example.test"}):
        u = run(bot.deletemydata, FakeUpdate("private", user_id=X))
        check("отказ с контактом", (u.replies[0]["text"], "reply_markup" in u.replies[0]),
              ("Автоматическое удаление сейчас недоступно. Напишите разработчику: dev@example.test", False))
        q = FakeUpdate("private", user_id=X, chat_id=X, query_data="del:yes:%d" % int(time.time()))
        run(bot.delete_callback, q)
        check("кнопка без секрета: ничего не удалено", (counts(path, X), q.callback_query.answers, tombstones(path)), (before[0], 1, before[1]))
        check("кнопка: отказ", q.callback_query.edits[0]["text"], "Автоматическое удаление сейчас недоступно. Напишите разработчику: dev@example.test")
    saved_contact = os.environ.pop("DEVELOPER_CONTACT", None)
    try:
        bot.delete_limiter.last.clear()
        u = run(bot.deletemydata, FakeUpdate("private", user_id=X))
        check("отказ без контакта", u.replies[0]["text"], "Автоматическое удаление сейчас недоступно.")
    finally:
        if saved_contact is not None:
            os.environ["DEVELOPER_CONTACT"] = saved_contact

    # с секретом: предупреждение с новым текстом и кнопками
    os.environ["TOMBSTONE_SECRET"] = SECRET
    bot.delete_limiter.last.clear()
    u = run(bot.deletemydata, FakeUpdate("private", user_id=X))
    t = u.replies[0]["text"]
    assert ("Если вы снова откроете игру в ближайшие 30 дней, стартовые 1000 фишек не выдаются: фишки будут "
            "начисляться по 100 в час. Для защиты от злоупотреблений на это время сохраняется обезличенный "
            "идентификатор, через 30 дней он удаляется.") in t, t
    assert "будет создан новый игрок с 1000 фишек" not in t
    assert t.startswith("Будут удалены ваш баланс, уровни улучшений, история раундов и покупок, история игр в мины, история раундов кено, история раздач блэкджека, история раундов краша, история отправленных вами переводов (записи о полученных вами переводах не удаляются, а обезличиваются: ваш идентификатор заменяется, они остаются у отправителей до конца срока хранения), незавершённая игра в мины (вместе со ставкой), незавершённая раздача блэкджека (вместе со ставкой), незавершённый раунд краша (вместе со ставкой), а также участие в рейтингах. Это нельзя отменить. Данные на вашем устройстве")
    check("кнопки на месте", [b.text for b in u.replies[0]["reply_markup"].inline_keyboard[0]], ["Удалить всё", "Отмена"])
    # подтверждение с секретом удаляет и создаёт tombstone только у нажавшего
    q = FakeUpdate("private", user_id=X, chat_id=X, query_data="del:yes:%d" % int(time.time()))
    run(bot.delete_callback, q)
    check("удалён нажавший", counts(path, X), (0, 0, 0))
    check("чужой цел", counts(path, Y)[0], 1)
    assert antiabuse.key_hash(X, SECRET.encode()) in {r[0] for r in tombstones(path)}
    assert antiabuse.key_hash(Y, SECRET.encode()) not in {r[0] for r in tombstones(path)}
finally:
    pass

# ---------- миграция: база без новой таблицы ----------
fd2, path2 = tempfile.mkstemp(suffix=".db")
os.close(fd2)
try:
    conn = sqlite3.connect(path2)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("CREATE TABLE roulette_rounds (telegram_id INTEGER NOT NULL, request_id TEXT NOT NULL, "
                 "number INTEGER NOT NULL, stake_total INTEGER NOT NULL, payout_total INTEGER NOT NULL, "
                 "bets_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (telegram_id, request_id))")
    conn.execute("CREATE TABLE chat_members (chat_instance TEXT NOT NULL, telegram_id INTEGER NOT NULL, "
                 "first_name TEXT NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, "
                 "PRIMARY KEY (chat_instance, telegram_id))")
    conn.execute("INSERT INTO players VALUES (1, 777, 100, %d, 1000)" % (int(time.time()) + 10 * 86400))   # метка в будущем: начисления нет
    conn.execute("INSERT INTO roulette_rounds VALUES (1, 'old-request-1', 17, 10, 360, '[]', 1500)")
    conn.execute("INSERT INTO chat_members VALUES ('room', 1, 'Имя', 1, 2)")
    conn.commit()
    conn.close()
    init_db(path2)
    init_db(path2)
    conn = sqlite3.connect(path2)
    check("таблица появилась", [r[1] for r in conn.execute("PRAGMA table_info(deletion_tombstones)")], ["key_hash", "deleted_at"])
    check("данные на месте", (conn.execute("SELECT balance FROM players").fetchall(), conn.execute("SELECT COUNT(*) FROM roulette_rounds").fetchone()[0],
                              conn.execute("SELECT COUNT(*) FROM chat_members").fetchone()[0]), ([(777,)], 1, 1))
    conn.close()
finally:
    os.remove(path2)

# ---------- privacy.html ----------
page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "privacy.html"), encoding="utf-8").read()
assert "<script" not in page.lower(), "на странице есть script"
assert not re.search(r"https?://|//cdn|src=|<link|@import|url\(", page, re.I), "на странице есть внешние ресурсы"
for label in ("[КОНТАКТ]", "[РЕГИОН]"):
    assert label not in page, "метка вернулась: " + label
assert "crowdad234@gmail.com" in page
for phrase in (
    "после удаления ваших данных на 30 дней сохраняется обезличенный (хэшированный) идентификатор и дата удаления: он нужен "
    "только для того, чтобы нельзя было сразу получить стартовые фишки заново",
    "Обезличенный идентификатор из раздела 2 автоматически удаляется через 30 дней после удаления данных.",
    "После удаления вы можете начать заново: будет создана новая запись, но в течение 30 дней стартовые 1000 фишек не выдаются, "
    "фишки начисляются по 100 в час.",
):
    assert phrase in page, "нет фразы: " + phrase[:50]
assert "и для вас будет создана новая запись" not in page, "старая фраза осталась"
assert "Дата последнего обновления: 8 октября 2026 г." in page

# ---------- логи ----------
root.removeHandler(cap)
root.setLevel(old_level)
assert cap.lines is not None
for line in cap.lines:
    for secret in (SECRET, "987654321", "555000111", antiabuse.key_hash(987654321, SECRET.encode()), "request-aaaa"):
        assert secret not in line, f"в логе есть {secret!r}: {line}"

if old_secret is None:
    os.environ.pop("TOMBSTONE_SECRET", None)
else:
    os.environ["TOMBSTONE_SECRET"] = old_secret
if old_db is None:
    os.environ.pop("DB_PATH", None)
else:
    os.environ["DB_PATH"] = old_db
os.remove(path)

for _k, _v in _saved_env.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

print("Все проверки прошли")
