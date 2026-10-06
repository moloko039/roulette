import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import bot
import db
import levels
import ratelimit
import transfers
import wallet
from api import create_app
from roulette import MAX_SAFE_INT, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

NOW = 1_760_000_000
FUT_ACCRUAL = NOW + 10 * 86400   # старые базы в тестах миграции: метка в будущем, начисления нет

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
DAY = 86400
A, B, C, OWNER, D = 424242421, 424242422, 424242423, 424242499, 424242424
CHAT, OTHER_CHAT = "chat-room-1", "chat-room-2"
XP3 = levels.threshold(3)      # опыт, с которого начинается уровень 3
STAKED = transfers.MIN_STAKED_TO_SEND

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "GAME_LINK", "PRIVACY_URL",
             "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["MEMBER_REF_SECRET"] = "test-ref-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


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


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "t%d.db" % counter[0])
    db.init_db(path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=100_000, xp=XP3 + 100, created=NOW - 5 * DAY, chat=CHAT, name=None, total=STAKED):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW + 10 * DAY, created, total, xp))
    if chat:
        sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
            (chat, uid, name or "Игрок%d" % (uid % 100), NOW - 4 * DAY, NOW))


def row(path, uid):
    return sql(path, "SELECT balance, total_staked, xp FROM players WHERE telegram_id = ?", (uid,))[0]


def ref(uid, chat=CHAT):
    return transfers.member_ref(chat, uid)


def send(path, sender, to, amount, n=1, chat=CHAT, owner=OWNER, now=NOW, name="Имя"):
    return db.transfer_send(sender, chat, name, ref(to) if isinstance(to, int) else to, amount, "tr-req-%05d" % n, owner_id=owner, now=now, db_path=path)


def world(balance=100_000, owner_balance=1000):
    path = new_db()
    add_player(path, A, balance)
    add_player(path, B, 5000)
    add_player(path, C, 5000)
    if owner_balance is not None:
        add_player(path, OWNER, owner_balance)
    return path


def total_chips(path):
    return sql(path, "SELECT SUM(balance) FROM players")[0][0]


try:
    # ================= комиссия =================
    check("комиссия 5 %", [transfers.fee_for(a) for a in (100, 101, 1000, 1999, 50000)], [5, 5, 50, 99, 2500])
    check("минимум 1", [transfers.fee_for(a) for a in (1, 10, 19, 20, 39, 40)], [1, 1, 1, 1, 1, 2])
    check("0 отключает комиссию", [transfers.fee_for(a, 0) for a in (1, 100, 50000)], [0, 0, 0])
    check("константы", (transfers.TRANSFER_MIN, transfers.TRANSFER_MAX, transfers.SEND_DAILY_LIMIT, transfers.COOLDOWN_SECONDS,
                        transfers.SENDER_MIN_LEVEL, transfers.MIN_ACCOUNT_AGE_HOURS, transfers.MIN_STAKED_TO_SEND, transfers.FEE_PERCENT),
          (100, 500000, 500000, 10, 3, 1, 20000, 5))
    check("лимита на получение нет", hasattr(transfers, "RECEIVE_DAILY_LIMIT"), False)
    assert all(transfers.valid_amount(a) for a in (100, 499999, 500000)) and not any(transfers.valid_amount(a) for a in (99, 500001, 0, -5, 1.5, "100", True, None))

    # ================= метки участников =================
    r1 = transfers.member_ref(CHAT, A)
    check("32 hex", bool(re.fullmatch(r"[0-9a-f]{32}", r1)), True)
    check("стабильна", transfers.member_ref(CHAT, A), r1)
    assert transfers.member_ref(CHAT, B) != r1 and transfers.member_ref(OTHER_CHAT, A) != r1
    assert str(A) not in r1 and str(A) not in transfers.member_ref(OTHER_CHAT, A)
    refs = {transfers.member_ref(CHAT, uid) for uid in range(1000, 1500)}
    check("без коллизий в беседе", len(refs), 500)
    with mock.patch.dict(os.environ, {"MEMBER_REF_SECRET": "another-secret"}):
        assert transfers.member_ref(CHAT, A) != r1, "метка не зависит от секрета"
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MEMBER_REF_SECRET")
        from_tomb = transfers.member_ref(CHAT, A)
        check("без MEMBER_REF_SECRET ключ выводится из TOMBSTONE_SECRET", (from_tomb != r1, transfers.member_ref(CHAT, A)), (True, from_tomb))
        import hashlib
        import hmac as _h
        assert from_tomb != _h.new(b"test-secret-not-real", ("%s\0%d" % (CHAT, A)).encode(), hashlib.sha256).hexdigest()[:32], "метка совпала с прямым HMAC TOMBSTONE_SECRET"
        os.environ.pop("TOMBSTONE_SECRET")
        ephemeral = transfers.member_ref(CHAT, A)
        check("без секретов не падает: случайный ключ процесса, метка стабильна", (bool(re.fullmatch(r"[0-9a-f]{32}", ephemeral)), transfers.member_ref(CHAT, A)), (True, ephemeral))
        os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
    os.environ["MEMBER_REF_SECRET"] = "test-ref-secret-not-real"
    check("метка вернулась", transfers.member_ref(CHAT, A), r1)

    # ================= успешный перевод и комиссия =================
    path = world()
    before = {u: row(path, u) for u in (A, B, C, OWNER)}
    total0 = total_chips(path)
    res = send(path, A, B, 1000)
    check("ответ", res, {"amount": 1000, "fee": 50, "received": 950, "balance": 99_000, "level": levels.profile_level(XP3 + 100),
                         "daily_left": 499_000, "replayed": False})
    check("у отправителя ровно amount", row(path, A)[0], 100_000 - 1000)
    check("получатель получил amount - fee", row(path, B)[0], 5000 + 950)
    check("владелец получил fee", row(path, OWNER)[0], 1000 + 50)
    check("посторонний не тронут", row(path, C), before[C])
    check("сумма сходится", total_chips(path), total0)
    for u in (A, B, C, OWNER):
        check("total_staked и XP не меняются (игрок %d)" % u, row(path, u)[1:], before[u][1:])
    check("запись перевода", sql(path, "SELECT sender, recipient, amount, fee, created_at, request_id FROM transfers"), [(A, B, 1000, 50, NOW, "tr-req-00001")])
    check("в таблице нет получателя комиссии", [r[1] for r in sql(path, "PRAGMA table_info(transfers)")], ["id", "sender", "recipient", "amount", "fee", "created_at", "request_id"])
    # границы суммы
    send(path, A, B, 100, n=2, now=NOW + 20)
    send(path, A, B, 50000, n=3, now=NOW + 40)
    for bad in (99, 500_001, 0, -1, 1.5, "100", True, None):
        raises(ValueError, send, path, A, B, bad, n=9, now=NOW + 60)
    for bad_ref in ("", "xyz", "0" * 31, "0" * 33, "G" * 32, None, [], "A" * 32):
        raises(ValueError, send, path, A, bad_ref, 100, n=9, now=NOW + 60)
    # минимум 1, округление, FEE_PERCENT = 0
    path = world()
    with mock.patch.object(transfers, "TRANSFER_MIN", 1):
        r = send(path, A, B, 10)
        check("fee минимум 1", (r["fee"], r["received"], row(path, B)[0], row(path, OWNER)[0]), (1, 9, 5009, 1001))
        r = send(path, A, B, 199, n=2, now=NOW + 20)
        check("округление вниз", (r["fee"], r["received"]), (9, 190))
    path = world()
    with mock.patch.object(transfers, "FEE_PERCENT", 0):
        r = send(path, A, B, 1000)
        check("FEE_PERCENT=0: комиссии нет", (r["fee"], r["received"], row(path, B)[0], row(path, OWNER)[0], row(path, A)[0]), (0, 1000, 6000, 1000, 99_000))
        check("fee_percent в лимитах", db.transfer_status(A, owner_id=OWNER, now=NOW, db_path=path)[0]["fee_percent"], 0)

    # ================= владелец =================
    path = world()
    r = send(path, OWNER, B, 1000, owner=OWNER)
    check("владелец-отправитель: комиссии нет", (r["fee"], r["received"], row(path, OWNER)[0], row(path, B)[0]), (0, 1000, 0, 6000))
    path = world()
    r = send(path, A, OWNER, 1000)
    check("владелец-получатель получает amount", (r["fee"], r["received"], row(path, OWNER)[0], row(path, A)[0]), (50, 950, 2000, 99_000))
    check("в записи fee посчитана", sql(path, "SELECT fee FROM transfers")[0][0], 50)
    check("лимиты владельца: fee_percent 0", (db.transfer_status(OWNER, owner_id=OWNER, now=NOW, db_path=path)[0]["fee_percent"],
                                             db.transfer_status(A, owner_id=OWNER, now=NOW, db_path=path)[0]["fee_percent"]), (0, 5))
    # комиссия сгорает: нет игрока владельца, не задан OWNER_CHAT_ID, потолок
    cap.lines.clear()
    for label, owner_arg, owner_balance in (("нет игрока у владельца", OWNER, None), ("owner_id не задан", None, 1000), ("упёрся в потолок", OWNER, MAX_SAFE_INT - 10)):
        path = world(owner_balance=owner_balance)
        total0 = total_chips(path)
        r = send(path, A, B, 1000, owner=owner_arg)
        check("%s: перевод прошёл, получатель amount - fee" % label, (r["fee"], r["received"], row(path, B)[0], row(path, A)[0]), (50, 950, 5950, 99_000))
        check("%s: комиссия сгорела (сумма уменьшилась на fee)" % label, total_chips(path), total0 - 50)
        if owner_balance is not None:
            check("%s: баланс владельца не тронут" % label, row(path, OWNER)[0], owner_balance)
        assert len(sql(path, "SELECT * FROM players WHERE telegram_id = ?", (OWNER,))) == (0 if owner_balance is None else 1)
    check("лог без начисления: нейтральные строки", sum(1 for l in cap.lines if l == "Комиссия за перевод не начислена (владелец недоступен)"), 3)
    assert not any("сгорела" in l or "начислена (" in l and "не" not in l for l in cap.lines)
    cap.lines.clear()
    send(world(), A, B, 1000)
    check("лог при начислении владельцу", [l for l in cap.lines if "Комиссия" in l], ["Комиссия за перевод начислена"])

    # ================= ошибки =================
    path = world()
    e = raises(transfers.TransferError, send, path, A, B, 500, chat=None)
    check("no_chat", e.code, "no_chat")
    e = raises(transfers.TransferError, send, path, A, A, 500)
    check("self_transfer", e.code, "self_transfer")
    e = raises(transfers.TransferError, send, path, A, ref(B, OTHER_CHAT), 500)
    check("метка другой беседы: not_in_chat", e.code, "not_in_chat")
    e = raises(transfers.TransferError, send, path, A, "a" * 32, 500)
    check("неизвестная метка: not_in_chat", e.code, "not_in_chat")
    add_player(path, D, 5000, chat=OTHER_CHAT)
    e = raises(transfers.TransferError, send, path, A, ref(D), 500)
    check("получатель из другой беседы: not_in_chat", e.code, "not_in_chat")
    e = raises(transfers.TransferError, send, path, A, ref(D, OTHER_CHAT), 500)
    check("метка чужой беседы в своей: not_in_chat", e.code, "not_in_chat")
    # удалённый получатель
    add_player(path, 777, 5000)
    ref777 = ref(777)
    db.delete_player_data(777, db_path=path, now=NOW)
    e = raises(transfers.TransferError, send, path, A, ref777, 500)
    check("удалил данные: not_in_chat", e.code, "not_in_chat")
    sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, 888, 'Призрак', ?, ?)", (CHAT, NOW, NOW))
    e = raises(transfers.TransferError, send, path, A, ref(888), 500)
    check("в беседе есть, игрока в базе нет: not_in_chat", e.code, "not_in_chat")
    check("ошибки ничего не изменили", (row(path, A)[0], row(path, B)[0], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (100_000, 5000, 0))
    # уровень и возраст (границы)
    path = world()
    sql(path, "UPDATE players SET xp = ? WHERE telegram_id = ?", (XP3 - 1, A))
    check("уровень 2", raises(transfers.TransferError, send, path, A, B, 500).code, "level_too_low")
    sql(path, "UPDATE players SET xp = ? WHERE telegram_id = ?", (XP3, A))
    send(path, A, B, 500)
    path = world()
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 3600 + 1, A))
    check("возраст 3599 с", raises(transfers.TransferError, send, path, A, B, 500).code, "account_too_new")
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 3600, A))
    send(path, A, B, 500)
    path = world()
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 600, A))
    check("аккаунту 10 минут: account_too_new", raises(transfers.TransferError, send, path, A, B, 500).code, "account_too_new")
    sql(path, "UPDATE players SET created_at = ? WHERE telegram_id = ?", (NOW - 2 * 3600, A))
    check("аккаунту 2 часа и уровень 3: отправляет", send(path, A, B, 500)["amount"], 500)
    # накопленные ставки отправителя (владельца условие не касается)
    path = world()
    sql(path, "UPDATE players SET total_staked = ? WHERE telegram_id = ?", (STAKED - 1, A))
    e = raises(transfers.TransferError, send, path, A, B, 500)
    check("ставок 19999: not_enough_staked", e.code, "not_enough_staked")
    check("ничего не списано", (row(path, A)[0], row(path, B)[0], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (100_000, 5000, 0))
    sql(path, "UPDATE players SET total_staked = ? WHERE telegram_id = ?", (STAKED, A))
    check("ставок 20000: можно", send(path, A, B, 500)["amount"], 500)
    path = world()
    sql(path, "UPDATE players SET total_staked = 0 WHERE telegram_id = ?", (OWNER,))
    check("у владельца ставок 0: отправляет", send(path, OWNER, B, 500, owner=OWNER)["amount"], 500)
    # кулдаун
    path = world()
    send(path, A, B, 100, n=1, now=NOW)
    e = raises(transfers.TransferError, send, path, A, B, 100, n=2, now=NOW + 3)
    check("cooldown и остаток секунд", (e.code, e.extra), ("cooldown", {"seconds": 7}))
    e = raises(transfers.TransferError, send, path, A, B, 100, n=2, now=NOW + 9)
    check("остаток 1 секунда", e.extra, {"seconds": 1})
    send(path, A, B, 100, n=2, now=NOW + 10)
    check("через 10 секунд можно", sql(path, "SELECT COUNT(*) FROM transfers")[0][0], 2)
    # суточный лимит отправки: 500000 за скользящие 24 часа, считается списанное (amount, вместе с комиссией)
    path = world(balance=2_000_000)
    for i in range(10):
        r = send(path, A, B, 50000, n=i + 1, now=NOW + 10 * i)
    check("500 000 в сутки проходит: daily_left 0", r["daily_left"], 0)
    check("отправитель потратил 500 000", row(path, A)[0], 2_000_000 - 500_000)
    e = raises(transfers.TransferError, send, path, A, B, 100, n=11, now=NOW + 100)
    check("сверх 500 000: daily_limit", e.code, "daily_limit")
    check("в лимитах daily_left 0", db.transfer_status(A, owner_id=OWNER, now=NOW + 100, db_path=path)[0]["daily_left"], 0)
    e = raises(transfers.TransferError, send, path, A, B, 100, n=11, now=NOW + DAY - 1)
    check("за секунду до выхода первого перевода из окна ещё нельзя", e.code, "daily_limit")
    r = send(path, A, B, 50000, n=11, now=NOW + DAY)
    check("ровно через 24 часа первый перевод вышел из окна", r["daily_left"], 0)
    path = world(balance=2_000_000)
    for i in range(9):
        send(path, A, B, 50000, n=i + 1, now=NOW + 10 * i)
    send(path, A, B, 49999, n=10, now=NOW + 90)
    e = raises(transfers.TransferError, send, path, A, B, 100, n=11, now=NOW + 100)
    check("осталось 1: перевод 100 не проходит", e.code, "daily_limit")
    # максимум за один перевод 500 000: границы, комиссия, суточные лимиты отправки и получения после него
    for amount, ok in ((499_999, True), (500_000, True), (500_001, False)):
        path = world(balance=2_000_000)
        if ok:
            r = send(path, A, B, amount, n=1)
            check("перевод %d проходит: списано всё, получено за вычетом комиссии 5 %%" % amount,
                  (row(path, A)[0], r["received"], r["fee"]), (2_000_000 - amount, amount - transfers.fee_for(amount), transfers.fee_for(amount)))
        else:
            raises(ValueError, send, path, A, B, amount, n=1)     # тот же отказ, что и раньше при превышении максимума
            check("500 001: ничего не списано", row(path, A)[0], 2_000_000)
    path = world(balance=2_000_000)
    r = send(path, A, B, 500_000, n=1, now=NOW)
    check("500 000: списано 500 000, получатель получил 475 000, комиссия 25 000", (row(path, A)[0], row(path, B)[0], row(path, OWNER)[0], r["received"], r["fee"]),
          (1_500_000, 5000 + 475_000, 1000 + 25_000, 475_000, 25_000))
    check("после 500 000 суточный лимит отправки исчерпан", r["daily_left"], 0)
    e = raises(transfers.TransferError, send, path, A, C, 100, n=2, now=NOW + 10)
    check("следующий перевод 100 в тот же день: daily_limit", e.code, "daily_limit")
    # получатель с уже полученной суммой: суточного лимита на получение нет, перевод проходит
    path = world(balance=2_000_000)
    add_player(path, 4001, 100_000, created=NOW - 2 * 3600)
    with mock.patch.object(transfers, "FEE_PERCENT", 0):
        send(path, 4001, B, 25_100, n=1, now=NOW)
    r = send(path, A, B, 500_000, n=2, now=NOW + 10)
    check("получено уже 25 100: ещё 475 000 принято без отказа", (r["received"], row(path, B)[0]), (475_000, 5000 + 25_100 + 475_000))
    # владелец: суточных лимитов нет, максимум за раз действует
    path = world(balance=100)
    sql(path, "UPDATE players SET balance = 3000000 WHERE telegram_id = ?", (OWNER,))
    r1 = send(path, OWNER, B, 500_000, n=1, now=NOW, owner=OWNER)
    r2 = send(path, OWNER, C, 500_000, n=2, now=NOW + 10, owner=OWNER)
    check("владелец: два перевода по 500 000 подряд без комиссии и суточного отказа", (r1["fee"], r2["fee"], row(path, OWNER)[0]), (0, 0, 2_000_000))
    raises(ValueError, send, path, OWNER, B, 500_001, n=3, now=NOW + 20, owner=OWNER)
    # владелец отправляет без суточного лимита (сумма, пауза и макс. за перевод действуют)
    path = world(balance=100)
    sql(path, "UPDATE players SET balance = 3000000 WHERE telegram_id = ?", (OWNER,))
    for i in range(12):
        r = send(path, OWNER, [C, B][i % 2], 50000, n=i + 1, now=NOW + 10 * i, owner=OWNER)
    check("владелец отправил 600 000 за сутки (12 по 50 000) без отказа и комиссии", (sql(path, "SELECT SUM(amount) FROM transfers WHERE sender = ?", (OWNER,))[0][0], r["fee"]), (600_000, 0))
    check("daily_left владельца: потолка нет", (r["daily_left"], db.transfer_status(OWNER, owner_id=OWNER, now=NOW + 250, db_path=path)[0]["unlimited"]), (MAX_SAFE_INT, True))
    e = raises(transfers.TransferError, send, path, OWNER, B, 100, n=99, now=NOW + 115, owner=OWNER)
    check("кулдаун у владельца сохраняется", e.code, "cooldown")
    for bad in (99, 500_001):
        raises(ValueError, send, path, OWNER, B, bad, n=98, now=NOW + 400, owner=OWNER)
    # лимита на получение нет: 20 свежих аккаунтов шлют одному получателю, каждый в пределах своих лимитов отправителя
    path = new_db()
    add_player(path, OWNER, 1000)
    for i in range(20):
        add_player(path, 3000 + i, 100_000, created=NOW - 2 * 3600)     # 20 свежих аккаунтов (2 часа, уровень 3, ставки 20 000)
    add_player(path, B, 5000)
    for i in range(20):
        send(path, 3000 + i, B, 50000, n=i + 1, now=NOW)
    check("принято 20 переводов по 47 500 = 950 000 (больше прежнего лимита 500 000)", sql(path, "SELECT COUNT(*), SUM(amount - fee) FROM transfers WHERE recipient = ?", (B,))[0], (20, 950_000))
    check("баланс получателя вырос на всё полученное", row(path, B)[0], 5000 + 950_000)
    check("комиссии 20 x 2 500 владельцу", row(path, OWNER)[0], 1000 + 50_000)
    check("у каждого отправителя списано ровно 50 000", {row(path, 3000 + i)[0] for i in range(20)}, {50_000})
    # владелец-получатель без лимита получения
    path = new_db()
    add_player(path, OWNER, 1000)
    for i in range(12):
        add_player(path, 3000 + i, 100_000)
    for i in range(12):
        send(path, 3000 + i, OWNER, 50000, n=i + 1, now=NOW)
    check("владелец получил 600 000 за сутки", row(path, OWNER)[0], 1000 + 12 * 50000)
    # владелец-получатель без лимита получения; владелец-отправитель получателей тоже не ограничивает
    path = new_db()
    add_player(path, OWNER, 3_000_000)
    add_player(path, B, 5000)
    for i in range(11):
        r = send(path, OWNER, B, 50000, n=i + 1, now=NOW + 10 * i, owner=OWNER)
    check("получатель принял 550 000 от владельца без отказа", row(path, B)[0], 5000 + 11 * 50000)
    # параллельные переводы лимиты не обходят
    import threading
    from concurrent.futures import ThreadPoolExecutor

    def parallel(fn, n=20):
        barrier = threading.Barrier(n)

        def work(i):
            barrier.wait()
            try:
                return ("ok", fn(i))
            except transfers.TransferError as exc:
                return ("err", exc.code)
        with ThreadPoolExecutor(n) as pool:
            return list(pool.map(work, range(n)))

    path = new_db()
    add_player(path, OWNER, 1000)
    for i in range(20):
        add_player(path, 3000 + i, 100_000)
    add_player(path, B, 5000)
    res = parallel(lambda i: send(path, 3000 + i, B, 50000, n=100 + i, now=NOW))
    check("20 параллельных отправителей одному получателю: все 20 успешны", (sum(1 for x in res if x[0] == "ok"), sorted({x[1] for x in res if x[0] == "err"})), (20, []))
    check("получено 950 000 (лимита на получение нет)", sql(path, "SELECT SUM(amount - fee) FROM transfers WHERE recipient = ?", (B,))[0][0], 950_000)
    path = new_db()
    add_player(path, OWNER, 1000)
    add_player(path, A, 5_000_000)
    for i in range(20):
        add_player(path, 4000 + i, 5000)
    with mock.patch.object(transfers, "COOLDOWN_SECONDS", 0):   # кулдаун выключен, чтобы проверить именно суточный лимит под гонкой
        res = parallel(lambda i: send(path, A, 4000 + i, 50000, n=200 + i, now=NOW))
    check("20 параллельных переводов одного отправителя: ровно 10 (500 000)", (sum(1 for x in res if x[0] == "ok"), sql(path, "SELECT SUM(amount) FROM transfers WHERE sender = ?", (A,))[0][0]), (10, 500_000))
    res = parallel(lambda i: send(path, A, 4000 + i, 100, n=300 + i, now=NOW + DAY * 3))
    check("параллельно в одну секунду: кулдаун пропускает один", sum(1 for x in res if x[0] == "ok"), 1)
    # нехватка фишек, потолок получателя
    path = world(balance=300)
    raises(InsufficientFunds, send, path, A, B, 301)
    check("insufficient_funds: ничего не изменилось", (row(path, A)[0], row(path, B)[0], row(path, OWNER)[0], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (300, 5000, 1000, 0))
    send(path, A, B, 300)
    check("перевод на весь баланс", row(path, A)[0], 0)
    path = world()
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 949, B))
    e = raises(transfers.TransferError, send, path, A, B, 1000)
    check("recipient_limit", e.code, "recipient_limit")
    check("recipient_limit: ничего не списано", (row(path, A)[0], row(path, OWNER)[0]), (100_000, 1000))
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 950, B))
    send(path, A, B, 1000, n=3)
    check("получатель ровно до потолка", row(path, B)[0], MAX_SAFE_INT)
    path = world()
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 999, OWNER))
    e = raises(transfers.TransferError, send, path, A, OWNER, 1000)
    check("владелец-получатель у потолка: recipient_limit", e.code, "recipient_limit")

    # ================= идемпотентность =================
    path = world()
    first = send(path, A, B, 1000, n=10, now=NOW)
    again = send(path, A, B, 1000, n=10, now=NOW + 5)
    check("повтор: тот же перевод", {k: v for k, v in again.items() if k not in ("replayed", "daily_left")}, {k: v for k, v in first.items() if k not in ("replayed", "daily_left")})
    check("replayed", (first["replayed"], again["replayed"]), (False, True))
    check("списано один раз, комиссия владельцу один раз", (row(path, A)[0], row(path, B)[0], row(path, OWNER)[0], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (99_000, 5950, 1050, 1))
    check("повтор не требует кулдауна", again["amount"], 1000)
    for kwargs in (dict(to=B, amount=1001), dict(to=C, amount=1000)):
        raises(transfers.RequestConflict, send, path, A, kwargs["to"], kwargs["amount"], n=10, now=NOW + 5)
    raises(transfers.RequestConflict, send, path, A, B, 1000, n=10, chat=None)
    raises(transfers.RequestConflict, send, path, A, B, 1000, n=10, chat=OTHER_CHAT)
    check("после конфликтов всё цело", (row(path, A)[0], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (99_000, 1))
    send(path, C, B, 100, n=10, now=NOW + 5)
    check("request_id у каждого отправителя свой", sql(path, "SELECT COUNT(*) FROM transfers")[0][0], 2)

    # ================= атомарность =================
    for fail_at in (1, 2):
        path = world()
        before = {u: row(path, u) for u in (A, B, OWNER)}
        calls = [0]
        real_credit = wallet.credit

        def flaky(conn, uid, amount, _real=real_credit, _calls=calls, _fail=fail_at):
            _calls[0] += 1
            if _calls[0] == _fail:
                raise RuntimeError("boom")
            return _real(conn, uid, amount)

        with mock.patch.object(wallet, "credit", side_effect=flaky):
            raises(RuntimeError, send, path, A, B, 1000)
        check("сбой на credit #%d: ни одна операция не применена" % fail_at, ({u: row(path, u) for u in (A, B, OWNER)}, sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (before, 0))
    path = world()
    with mock.patch.object(wallet, "debit", side_effect=RuntimeError("boom")):
        raises(RuntimeError, send, path, A, B, 1000)
    check("сбой на debit", (row(path, A)[0], row(path, B)[0], row(path, OWNER)[0]), (100_000, 5000, 1000))
    # после сбоя перевод с тем же request_id проходит
    send(path, A, B, 1000)

    # ================= статус, история, просмотр =================
    path = world()
    limits, incoming = db.transfer_status(B, owner_id=OWNER, now=NOW, db_path=path)
    check("лимиты", limits, {"min": 100, "max": 500000, "daily_left": 500_000, "fee_percent": 5, "min_level": 3, "cooldown_seconds": 10,
                       "min_age_hours": 1, "min_staked": 20000, "unlimited": False})
    check("входящих нет", incoming, {"count": 0, "total": 0})
    send(path, A, B, 1000, n=1, now=NOW)
    send(path, C, B, 500, n=2, now=NOW + 5)
    limits, incoming = db.transfer_status(B, owner_id=OWNER, now=NOW + 6, db_path=path)
    check("два непросмотренных: сумма получено", incoming, {"count": 2, "total": 950 + 475})
    check("у отправителя входящих нет", db.transfer_status(A, owner_id=OWNER, now=NOW + 6, db_path=path)[1], {"count": 0, "total": 0})
    check("daily_left отправителя", db.transfer_status(A, owner_id=OWNER, now=NOW + 6, db_path=path)[0]["daily_left"], 499_000)
    hist = db.transfer_history(B, db_path=path)
    check("история получателя (новые первыми, имена как в рейтинге)", hist, [
        {"direction": "in", "name": "Игрок%d" % (C % 100), "amount": 500, "fee": 25, "time": NOW + 5},
        {"direction": "in", "name": "Игрок%d" % (A % 100), "amount": 1000, "fee": 50, "time": NOW}])
    check("просмотрено: transfers_seen_at и incoming_unseen", (sql(path, "SELECT transfers_seen_at FROM players WHERE telegram_id = ?", (B,))[0][0],
                                                              db.transfer_status(B, owner_id=OWNER, now=NOW + 7, db_path=path)[1]), (NOW + 5, {"count": 0, "total": 0}))
    send(path, A, B, 300, n=3, now=NOW + 20)
    check("новый входящий снова непросмотренный", db.transfer_status(B, owner_id=OWNER, now=NOW + 21, db_path=path)[1], {"count": 1, "total": 285})
    hist_a = db.transfer_history(A, db_path=path)
    check("у отправителя: out, имя получателя", [(h["direction"], h["name"], h["amount"]) for h in hist_a], [("out", "Игрок%d" % (B % 100), 300), ("out", "Игрок%d" % (B % 100), 1000)])
    check("история отправителя не трогает seen", sql(path, "SELECT transfers_seen_at FROM players WHERE telegram_id = ?", (A,))[0][0], 0)
    check("transfer_history не отдаёт идентификаторов", any(str(u) in json.dumps(hist_a) for u in (A, B, C, OWNER)), False)
    for i in range(25):
        sql(path, "INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (?, ?, 100, 5, ?, ?)", (C, A, NOW + 100 + i, "bulk-%05d" % i))
    check("в истории не больше 20", len(db.transfer_history(A, db_path=path)), 20)
    # имя удалившего данные
    db.delete_player_data(C, db_path=path, now=NOW)
    check("после удаления данных переводов с его участием нет", sql(path, "SELECT COUNT(*) FROM transfers WHERE sender = ? OR recipient = ?", (C, C))[0][0], 0)

    # ================= /mydata, удаление, очистка =================
    path = world()
    os.environ["DB_PATH"] = path
    send(path, A, B, 1000, n=1, now=NOW)
    send(path, C, A, 500, n=2, now=NOW + 20)
    export = db.get_player_export(A, db_path=path)
    check("в выгрузке два перевода без идентификаторов", ([(t["direction"], t["amount"], t["fee"]) for t in export["transfers"]]), [("in", 500, 25), ("out", 1000, 50)])
    text = json.dumps(export, ensure_ascii=False)
    for uid in (B, C, OWNER):
        assert str(uid) not in text, "идентификатор другого игрока в выгрузке"
    upd = FakeUpdate("private", user_id=A)
    asyncio.run(bot.mydata(upd, type("C", (), {})()))
    payload = json.loads(upd.effective_message.documents[0]["data"].decode("utf-8"))
    check("/mydata: переводы", len(payload["transfers"]), 2)
    others = sql(path, "SELECT COUNT(*) FROM transfers WHERE sender != ? AND recipient != ?", (A, A))[0][0]
    send(path, B, C, 100, n=3, now=NOW + 40)
    others = sql(path, "SELECT COUNT(*) FROM transfers WHERE sender != ? AND recipient != ?", (A, A))[0][0]
    counts = db.delete_player_data(A, db_path=path, now=NOW)
    check("удалены переводы, где игрок отправитель или получатель", counts["transfers"], 2)
    check("чужие переводы целы", sql(path, "SELECT COUNT(*) FROM transfers")[0][0], others)
    add_player(path, 900, 5000)
    sql(path, "INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (900, ?, 100, 5, ?, 'wipe-0001')", (B, NOW))
    asyncio.run(bot.deletemydata(FakeUpdate("private", user_id=900), type("C", (), {})()))
    assert "история переводов (отправленных и полученных)" in bot.DELETE_WARNING, bot.DELETE_WARNING
    with mock.patch("time.time", lambda: float(NOW)):      # управляемые часы: метка подтверждения и проверка свежести берут одно время
        q = FakeUpdate("private", user_id=900, query_data="del:yes:%d" % int(time.time()))
        asyncio.run(bot.delete_callback(q, type("C", (), {})()))
    assert "переводы — 1" in q.callback_query.edits[-1]["text"], q.callback_query.edits
    os.environ.pop("DB_PATH", None)
    path = new_db()
    add_player(path, A)
    ins = "INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (?, ?, 100, 5, ?, ?)"
    for i, age in enumerate((40, 31, 29, 5)):
        sql(path, ins, (A, B, NOW - age * DAY, "old-%05d" % i))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка старше 30 дней", (deleted["transfers"], sql(path, "SELECT COUNT(*) FROM transfers")[0][0]), (2, 2))

    # ================= миграция =================
    counter[0] += 1
    path = os.path.join(tmp, "old%d.db" % counter[0])
    conn = sqlite3.connect(path)   # база прежней схемы: players без transfers_seen_at, таблицы transfers нет
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, rate INTEGER NOT NULL, "
                 "last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL, total_staked INTEGER NOT NULL DEFAULT 0, "
                 "xp INTEGER NOT NULL DEFAULT 0, income_level INTEGER NOT NULL DEFAULT 0, storage_level INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (1, 500, 100, %d, 0)" % FUT_ACCRUAL)
    conn.commit()
    conn.close()
    with mock.patch("time.time", lambda: float(NOW)):      # миграция начисления берёт время из часов: управляемые, метка FUT_ACCRUAL остаётся в будущем
        db.init_db(path)
        db.init_db(path)
    check("существующий игрок: seen = 0, баланс цел", sql(path, "SELECT balance, transfers_seen_at FROM players WHERE telegram_id = 1")[0], (500, 0))
    check("колонка и индексы есть", ("transfers_seen_at" in [r[1] for r in sql(path, "PRAGMA table_info(players)")],
                                     sorted(r[1] for r in sql(path, "SELECT * FROM sqlite_master WHERE type = 'index' AND tbl_name = 'transfers' AND name LIKE 'idx_%'"))),
          (True, ["idx_transfers_recipient", "idx_transfers_sender"]))
    check("транзакция уникальна по (отправитель, request_id)", [r[1] for r in sql(path, "SELECT * FROM sqlite_master WHERE type = 'index' AND tbl_name = 'transfers' AND name LIKE 'sqlite_autoindex%'")], [sql(path, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transfers' AND name LIKE 'sqlite_autoindex%'")[0][0]])

    # ================= API =================
    path = new_db()
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    # Управляемые часы: время сервера и подпись initData берут одно значение, которое двигает сам тест (реального времени и пауз нет: кулдаун,
    # возраст аккаунта и суточное окно не зависят от скорости прогона и нагрузки на машину)
    frozen = [NOW + 100]
    clock_patch = mock.patch("time.time", lambda: float(frozen[0]))
    clock_patch.start()
    now_real = frozen[0]
    for uid, name in ((A, "Аня"), (B, "Борис"), (C, "Вера")):
        sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
                  "VALUES (?, 100000, 100, ?, ?, 20000, ?, 0, 0)", (uid, now_real + 3 * DAY, now_real - 5 * DAY, XP3 + 100))
        sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('room', ?, ?, ?, ?)", (uid, name, now_real - DAY, now_real))
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 1000, 100, ?, ?, 0, 0, 0, 0)",
        (OWNER, now_real + 3 * DAY, now_real - 5 * DAY))
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "100", "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "100"}), clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid, name="Игрок", group=True):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=name,
                                                         chat_type="group" if group else None, chat_instance="room" if group else None)}

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "transfers.json"), encoding="utf-8"))

    def shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for k, v in example.items():
            assert type(body[k]) is type(v), "%s.%s: %r" % (name, k, body[k])

    def post(body, uid=A, group=True, name="Аня"):
        return client.post("/api/transfers/send", headers=auth(uid, name, group), json=body)

    check("без подписи", [client.post("/api/transfers/send", json={}).status_code, client.get("/api/transfers").status_code], [401, 401])
    top = client.get("/api/chat/top", headers=auth(A, "Аня")).json()
    refs_by_name = {e["name"]: e["member_ref"] for e in top["top"]}
    check("рейтинг: у себя member_ref null, у остальных метка", (refs_by_name["Аня"], bool(re.fullmatch(r"[0-9a-f]{32}", refs_by_name["Борис"]))), (None, True))
    check("метка соответствует (беседа, игрок)", refs_by_name["Борис"], transfers.member_ref("room", B))
    top_text = json.dumps(top)
    for uid in (A, B, C, OWNER):
        assert str(uid) not in top_text, "идентификатор в рейтинге"
    me0 = client.get("/api/me", headers=auth(A, "Аня")).json()
    check("/api/me: лимиты и входящие", (me0["transfer_limits"], me0["incoming_unseen"]), ({"min": 100, "max": 500000, "daily_left": 500_000, "fee_percent": 5, "min_level": 3, "cooldown_seconds": 10, "min_age_hours": 1, "min_staked": 20000, "unlimited": False}, {"count": 0, "total": 0}))
    check("/api/me владельца: fee_percent 0", client.get("/api/me", headers=auth(OWNER, "Владелец")).json()["transfer_limits"]["fee_percent"], 0)
    good = {"request_id": "api-req-00001", "member_ref": refs_by_name["Борис"], "amount": 1000}
    for body in ({}, dict(good, extra=1), {"request_id": good["request_id"], "amount": 1000}, dict(good, amount=99), dict(good, amount=500_001), dict(good, amount=1.5),
                 dict(good, amount="1000"), dict(good, amount=True), dict(good, member_ref="x"), dict(good, member_ref=5), dict(good, member_ref="A" * 32), dict(good, request_id="short")):
        r = post(body)
        check("400 %s" % json.dumps(body)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = post(good, group=False)
    check("вне беседы: no_chat", (r.status_code, r.json()), (409, {"detail": "no_chat"}))
    r = post(dict(good, member_ref=transfers.member_ref("room", A)))
    check("самому себе: self_transfer", (r.status_code, r.json()), (409, {"detail": "self_transfer"}))
    r = post(dict(good, member_ref="a" * 32))
    check("not_in_chat", (r.status_code, r.json()), (409, {"detail": "not_in_chat"}))
    ok = post(good)
    check("send 200", ok.status_code, 200)
    shape("send", ok.json(), examples["send"])
    check("send: значения", {k: ok.json()[k] for k in ("amount", "fee", "received", "balance", "daily_left", "replayed")},
          {"amount": 1000, "fee": 50, "received": 950, "balance": 99_000, "daily_left": 499_000, "replayed": False})
    assert not any(str(u) in ok.text for u in (A, B, C, OWNER))
    check("комиссия на счёт владельца, получателю amount - fee", (row(path, OWNER)[0], row(path, B)[0], row(path, A)[0]), (1050, 100_950, 99_000))
    rep = post(good)
    check("повтор: replayed, ничего не списано", (rep.status_code, rep.json()["replayed"], row(path, A)[0], row(path, OWNER)[0]), (200, True, 99_000, 1050))
    check("request_conflict", (post(dict(good, amount=2000)).status_code, post(dict(good, amount=2000)).json()), (409, {"detail": "request_conflict"}))
    cool = post(dict(good, request_id="api-req-00002"))
    check("cooldown: остаток ровно 10 с (время не двигалось)", (cool.status_code, cool.json()["detail"], cool.json()["seconds"]), (409, "cooldown", 10))
    frozen[0] += 4
    check("cooldown: через 4 с остаток 6", post(dict(good, request_id="api-req-00002")).json()["seconds"], 6)
    frozen[0] += 6
    for k, v in examples["errors"].items():
        check("пример ошибки " + k, v, {"detail": k})
    check("пример cooldown", set(examples["cooldown"]), {"detail", "seconds"})
    # история и просмотр у получателя
    me_b = client.get("/api/me", headers=auth(B, "Борис")).json()
    check("получатель видит непросмотренный", me_b["incoming_unseen"], {"count": 1, "total": 950})
    h = client.get("/api/transfers", headers=auth(B, "Борис"))
    check("history 200", h.status_code, 200)
    assert set(h.json()) == {"items"} and set(h.json()["items"][0]) == set(examples["history"]["items"][0]), h.json()
    check("история: направление и имя", [(i["direction"], i["name"], i["amount"], i["fee"]) for i in h.json()["items"]], [("in", "Аня", 1000, 50)])
    assert not any(str(u) in h.text for u in (A, B, C, OWNER))
    check("после просмотра непросмотренных нет", client.get("/api/me", headers=auth(B, "Борис")).json()["incoming_unseen"], {"count": 0, "total": 0})
    check("история пустого", client.get("/api/transfers", headers=auth(C, "Вера")).json(), examples["history_empty"])
    # нехватка фишек, уровень
    sql(path, "UPDATE players SET balance = 50 WHERE telegram_id = ?", (C,))
    r = post({"request_id": "api-req-00010", "member_ref": refs_by_name["Борис"], "amount": 100}, uid=C, name="Вера")
    check("insufficient_funds", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    sql(path, "UPDATE players SET xp = 0 WHERE telegram_id = ?", (C,))
    r = post({"request_id": "api-req-00011", "member_ref": refs_by_name["Борис"], "amount": 100}, uid=C, name="Вера")
    check("level_too_low", (r.status_code, r.json()), (409, {"detail": "level_too_low"}))
    # ограничение частоты
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1", "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim2))
    codes = [c2.post("/api/transfers/send", headers=auth(A, "Аня"), json=dict(good, request_id="rate-req-%05d" % i)).status_code for i in range(5)]
    check("write: после лимита 429", [c == 429 for c in codes], [False, False, False, True, True])
    r = c2.post("/api/transfers/send", headers=auth(A, "Аня"), json=dict(good, request_id="rate-req-99999"))
    check("429 тело", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    check("read: третий 429", [c2.get("/api/transfers", headers=auth(B, "Борис")).status_code for _ in range(3)], [200, 200, 429])
    os.environ.pop("OWNER_CHAT_ID")
    clock_patch.stop()

    # ================= большая беседа: поиск получателя не держит блокировку записи =================
    path = new_db()
    add_player(path, OWNER, 1000)
    add_player(path, A, 100_000)
    add_player(path, B, 5000)
    add_player(path, C, 5000)
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                     [(CHAT, 10_000_000 + i, "Участник", NOW - 5 * DAY, NOW - 3 * DAY - i) for i in range(50_000)])
    conn.commit()
    conn.close()
    fake_ref = "0" * 32
    # Поиск получателя не держит блокировку записи и ограничен первой тысячей участников. Проверяется структурой, а не временем (замеры плавают под
    # нагрузкой): при каждом вычислении метки второе соединение с нулевым ожиданием берёт блокировку записи (получится, только если перевод её не держит),
    # а число вычислений не больше MAX_CHAT_MEMBERS.
    lock_ok, calls = [], [0]
    real_ref = transfers.member_ref

    def probing_ref(chat, uid):
        calls[0] += 1
        if calls[0] % 97 == 1:       # проба на каждой 97-й метке и на первой
            probe = sqlite3.connect(path, timeout=0)
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.execute("ROLLBACK")
                lock_ok.append(True)
            except sqlite3.OperationalError:
                lock_ok.append(False)
            finally:
                probe.close()
        return real_ref(chat, uid)

    with mock.patch.object(transfers, "member_ref", probing_ref):
        e = raises(transfers.TransferError, send, path, A, fake_ref, 100, n=1)
    check("перевод с несуществующей меткой в беседе на 50 000 участников: not_in_chat", e.code, "not_in_chat")
    check("поиск получателя не держит блокировку записи", (len(lock_ok) >= 5, all(lock_ok)), (True, True))
    check("вычислений метки не больше первой тысячи участников", calls[0] <= 1000, True)
    # метка участника из первой тысячи находится, из хвоста за пределами тысячи нет (как и в списке «Кому перевести»)
    check("метка из первой тысячи работает", send(path, A, B, 100, n=2, now=NOW + 20)["amount"], 100)
    e = raises(transfers.TransferError, send, path, A, 10_000_000 + 49_999, 100, n=3, now=NOW + 40)
    check("участник за пределами тысячи не находится", e.code, "not_in_chat")
    # пока идут переводы с несуществующими метками (в потоках), запись другого игрока не ждёт: пробное соединение с нулевым ожиданием
    # берёт блокировку записи, когда её никто не держит; перевод держит её только в транзакции, которой при ненайденной метке нет
    import threading
    stop = threading.Event()

    def spam():
        k = 100
        while not stop.is_set():
            k += 1
            try:
                send(path, A, fake_ref, 100, n=k, now=NOW + 60)
            except transfers.TransferError:
                pass

    th = [threading.Thread(target=spam) for _ in range(4)]
    for t in th:
        t.start()
    try:
        taken = 0
        for i in range(20):
            wc = db._connect(path)
            wc.execute("BEGIN IMMEDIATE")      # ожидание блокировки записи (до 10 с); перебор меток её не держит
            wallet.credit(wc, C, 1)
            wc.execute("COMMIT")
            wc.close()
            taken += 1
    finally:
        stop.set()
        for t in th:
            t.join()
    check("двадцать записей другого игрока прошли во время перебора меток в четырёх потоках", taken, 20)
    # ================= документы и политика =================
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "историю ваших переводов" in privacy and "История переводов хранится 30 дней" in privacy and "видят имя друг друга в истории переводов" in privacy
    assert "Комиссия за перевод зачисляется на игровой аккаунт разработчика в виде игровых фишек" in privacy and "переводы только подарок между участниками игры" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert all(p in api_doc for p in ("/api/transfers/send", "/api/transfers", "member_ref", "incoming_unseen", "transfer_limits"))

    # ================= в логах нет идентификаторов, имён, сумм, балансов и request_id =================
    for secret in (str(A), str(B), str(C), str(OWNER), "Аня", "Борис", "Вера", "api-req-", "tr-req-", "1000", "950", "99000", "99_000"):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:90]
    assert any(l == "Перевод выполнен" for l in cap.lines)
finally:
    mock.patch.stopall()
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
