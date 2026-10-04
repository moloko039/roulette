import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import db
import ratelimit
import transfers
from api import create_app
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = int(time.time())
ME = 424242421

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["MEMBER_REF_SECRET"] = "test-ref-secret-not-real"


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
path = os.path.join(tmp, "m.db")
db.init_db(path)


def sql(query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add(uid, name, chat, last_seen, balance=777_001, xp=4321, with_player=True):
    if with_player and not sql("SELECT 1 FROM players WHERE telegram_id = ?", (uid,)):
        sql("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
            "VALUES (?, ?, 100, ?, ?, 0, ?, 0, 0)", (uid, balance, NOW + 10 ** 6, NOW - 10 ** 6, xp))
    sql("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
        (chat, uid, name, last_seen - 100, last_seen))


try:
    # беседа 'room': я + 44 участника (порядок активности: больший номер = позже был)
    add(ME, "Я сам", "room", NOW)
    names = ["Аня", "Борис", "Вера", "Глеб", "ДАРЬЯ", "Дарья-2", "Ёлка"] + ["Игрок%02d" % i for i in range(1, 38)]
    for i, name in enumerate(names):
        add(1000 + i, name, "room", NOW - 1000 + i)          # i растёт: свежее
    add(5000, "Чужой", "other", NOW)                         # другая беседа
    add(5001, "Игрок99", "other", NOW)
    add(6000, "Призрак", "room", NOW - 5, with_player=False)  # в беседе есть, игрока в базе нет

    # ================= база: страницы, порядок, поиск =================
    items, nxt = db.chat_members_page(ME, "room", "", 0, db_path=path)
    check("первая страница 30, дальше есть", (len(items), nxt), (30, 30))
    assert set(items[0]) == {"name", "member_ref"}
    check("порядок: по last_seen по убыванию", items[0]["name"], "Игрок37")
    items2, nxt2 = db.chat_members_page(ME, "room", "", 30, db_path=path)
    check("вторая страница: остаток, дальше нет", (len(items2), nxt2), (14, None))
    all_names = [i["name"] for i in items + items2]
    check("всего 44 (себя и чужой беседы нет, призрак без игрока не включён)", (len(all_names), len(set(all_names))), (44, 44))
    assert "Я сам" not in all_names and "Чужой" not in all_names and "Призрак" not in all_names
    check("последний в порядке", all_names[-1], "Аня")
    check("пустой запрос = все", db.chat_members_page(ME, "room", "   ", 0, db_path=path)[0], items)
    check("поиск без учёта регистра по вхождению (кириллица)", [i["name"] for i in db.chat_members_page(ME, "room", "дарья", 0, db_path=path)[0]], ["Дарья-2", "ДАРЬЯ"])
    check("поиск в верхнем регистре", len(db.chat_members_page(ME, "room", "ДАРЬЯ", 0, db_path=path)[0]), 2)
    check("вхождение в середине", [i["name"] for i in db.chat_members_page(ME, "room", "орис", 0, db_path=path)[0]], ["Борис"])
    check("ё и е различаются как в имени", [i["name"] for i in db.chat_members_page(ME, "room", "ёлк", 0, db_path=path)[0]], ["Ёлка"])
    check("латиница и цифры", len(db.chat_members_page(ME, "room", "игрок1", 0, db_path=path)[0]), 10)    # Игрок10..Игрок19 и Игрок1? (01 нет): 10..19
    check("ничего не найдено", db.chat_members_page(ME, "room", "несуществует", 0, db_path=path), ([], None))
    check("пагинация поиска", [db.chat_members_page(ME, "room", "игрок", o, db_path=path)[1] for o in (0, 30)], [30, None])
    check("offset за пределами", db.chat_members_page(ME, "room", "", 1000, db_path=path), ([], None))
    check("запрос обрезается до 32 символов и чистится", db.chat_members_page(ME, "room", "Аня" + "\x00\x07‮" + " " * 3, 0, db_path=path)[0][0]["name"], "Аня")
    long_q = "Аня" + "я" * 100
    check("длинный запрос не падает", db.chat_members_page(ME, "room", long_q, 0, db_path=path), ([], None))
    # метки
    by_name = {i["name"]: i["member_ref"] for i in items + items2}
    check("метка = member_ref (беседа, игрок)", by_name["Аня"], transfers.member_ref("room", 1000))
    check("метки уникальны и 32 hex", (len(set(by_name.values())), all(re.fullmatch(r"[0-9a-f]{32}", v) for v in by_name.values())), (44, True))
    assert not any(str(1000 + i) in json.dumps(items + items2) for i in range(44)) and str(ME) not in json.dumps(items + items2)
    # чужая беседа
    other, _ = db.chat_members_page(5000, "other", "", 0, db_path=path)
    check("чужая беседа: только свои", sorted(i["name"] for i in other), ["Игрок99"])
    check("метки чужой беседы другие", other[0]["member_ref"], transfers.member_ref("other", 5001))
    # ошибки
    for call, code in ((lambda: db.chat_members_page(ME, None, "", 0, db_path=path), "no_chat"),
                       (lambda: db.chat_members_page(1000, "other", "", 0, db_path=path), "not_in_chat"),
                       (lambda: db.chat_members_page(424242999, "room", "", 0, db_path=path), "not_in_chat")):
        try:
            call()
            raise AssertionError("нет ошибки " + code)
        except transfers.TransferError as e:
            check("код " + code, e.code, code)
    for bad in (-1, 100_001, 1.5, "0", None, True):
        try:
            db.chat_members_page(ME, "room", "", bad, db_path=path)
            raise AssertionError("offset принят: %r" % (bad,))
        except ValueError:
            pass
    check("перевод по метке из списка работает", transfers.valid_member_ref(by_name["Аня"]), True)

    # ================= API =================
    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "chat_members.json"), encoding="utf-8"))
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "100", "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "100"}), clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid=ME, chat="room", name="Я сам"):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name=name,
                                                         chat_type="group" if chat else None, chat_instance=chat)}

    def shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for item in body["items"]:
            assert set(item) == {"name", "member_ref"} and type(item["name"]) is str and type(item["member_ref"]) is str
        assert body["next_offset"] is None or type(body["next_offset"]) is int

    check("без подписи", client.get("/api/chat/members").status_code, 401)
    r = client.get("/api/chat/members", headers=auth())
    check("200", r.status_code, 200)
    shape("page", r.json(), examples["page"])
    check("первая страница и next_offset", (len(r.json()["items"]), r.json()["next_offset"]), (30, 30))
    r2 = client.get("/api/chat/members?offset=30", headers=auth())
    shape("last", r2.json(), examples["last_page"])
    check("вторая страница", (len(r2.json()["items"]), r2.json()["next_offset"]), (14, None))
    r3 = client.get("/api/chat/members?q=несуществует", headers=auth())
    check("пустой результат", r3.json(), examples["empty"])
    r4 = client.get("/api/chat/members", params={"q": "ДАРЬЯ"}, headers=auth())
    check("поиск по API", [i["name"] for i in r4.json()["items"]], ["Дарья-2", "ДАРЬЯ"])
    text = r.text + r2.text + r4.text
    for secret in ("balance", "level", "777001", "4321", str(ME), "1000", "staked", "xp"):
        assert secret not in text, "лишнее в ответе: " + secret
    check("чужая беседа не видна", sorted(i["name"] for i in client.get("/api/chat/members", headers=auth(5000, "other", "Чужой")).json()["items"]), ["Игрок99"])
    r = client.get("/api/chat/members", headers=auth(ME, None))
    check("вне беседы: no_chat", (r.status_code, r.json()), (409, examples["errors"]["no_chat"]))
    r = client.get("/api/chat/members", headers=auth(424242999, "room", "Новичок"))
    check("не участник: not_in_chat", (r.status_code, r.json()), (409, examples["errors"]["not_in_chat"]))
    r = client.get("/api/chat/members", headers=auth(ME, "other"))
    check("участник другой беседы по подписи 'other': not_in_chat", (r.status_code, r.json()), (409, examples["errors"]["not_in_chat"]))
    for bad in ("offset=-1", "offset=abc", "offset=1.5", "offset=1000000", "offset="):
        r = client.get("/api/chat/members?" + bad, headers=auth())
        check("400 " + bad, (r.status_code, r.json()), (400, examples["errors"]["invalid_request"]))
    check("запрос не создаёт участника", sql("SELECT COUNT(*) FROM chat_members WHERE telegram_id = 424242999")[0][0], 0)
    # ограничение частоты (группа read)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1", "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim2))
    check("read: третий запрос 429", [c2.get("/api/chat/members", headers=auth()).status_code for _ in range(3)], [200, 200, 429])
    r = c2.get("/api/chat/members", headers=auth())
    check("429 тело", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    # документы и политика
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert "/api/chat/members" in api_doc and "next_offset" in api_doc
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "доступны имена всех участников этой беседы (без балансов)" in privacy
    # в логе нет имён и поисковых запросов
    for secret in ("ДАРЬЯ", "Дарья", "дарья", "несуществует", "Аня", "Борис", "q=", str(ME)):
        for line in cap.lines:
            assert secret not in line, "в логе: " + line[:90]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
