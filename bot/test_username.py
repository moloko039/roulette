"""Поиск id по нику (/find): ник запоминается при открытии игры, уникален, снимается при смене, входит в выгрузку и удаляется с профилем."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import os
import tempfile
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import db
import tg.owner
from api import create_app
from core.db_conn import _connect
from core.schema import init_db
from features.data_rights_db import delete_player_data, get_player_export
from stubs import FakeUpdate
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://pazrabi.github.io"
OWNER, A, B, C = 424242421, 424242422, 424242423, 424242424
NOW = 1_760_000_000


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def auth(uid, username=None):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), username=username)}


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["OWNER_CHAT_ID"] = str(OWNER)
os.environ["DB_PATH"] = path
init_db(path)
client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))

# нормализация
check("нормализация", [db.normalize_username(v) for v in ("@Ab_cd", "ABCDE", " abcde ", "abc", "a b cde", "", None, "a" * 33)],
      ["ab_cd", "abcde", "abcde", None, None, None, None, None])

# запоминается при /api/me, регистр не важен
client.get("/api/me", headers=auth(A, "Alpha_One"))
check("найден по нику", (db.find_by_username("alpha_one"), db.find_by_username("@ALPHA_ONE")), (A, A))
check("неизвестный ник", db.find_by_username("nobody1"), None)

# без юзернейма в initData: записи нет, старая запись снимается
client.get("/api/me", headers=auth(B))
check("нет юзернейма", db.find_by_username("bravo_two"), None)
client.get("/api/me", headers=auth(B, "bravo_two"))
check("игрок B найден", db.find_by_username("bravo_two"), B)
client.get("/api/me", headers=auth(B))
check("ник снят, когда юзернейм убран", db.find_by_username("bravo_two"), None)

# смена ника: старый не ищется
client.get("/api/me", headers=auth(A, "alpha_new"))
check("смена ника", (db.find_by_username("alpha_one"), db.find_by_username("alpha_new")), (None, A))

# ник перешёл к другому игроку: у прежнего снимается
client.get("/api/me", headers=auth(C, "alpha_new"))
check("ник уникален: теперь у C", (db.find_by_username("alpha_new"), db.find_by_username("alpha_new") == A), (C, False))

# выгрузка и удаление
check("в выгрузке", get_player_export(C, db_path=path)["username"], "alpha_new")
check("в выгрузке без ника", get_player_export(B, db_path=path)["username"], None)
res = delete_player_data(C, db_path=path, now=NOW)
check("профиль удалён", res["players"], 1)
check("ник удалён с профилем", db.find_by_username("alpha_new"), None)
conn = _connect(path)
check("строки ника нет", conn.execute("SELECT COUNT(*) FROM player_usernames WHERE telegram_id = ?", (C,)).fetchone()[0], 0)
conn.close()


# команда /find
def find(args, uid=OWNER, chat="private"):
    update = FakeUpdate(chat, user_id=uid)
    asyncio.run(tg.owner.find(update, SimpleNamespace(args=args, application=SimpleNamespace(bot_data={}))))
    return [r["text"] for r in update.replies]


def find_raw(args, uid=OWNER, chat="private"):
    update = FakeUpdate(chat, user_id=uid)
    asyncio.run(tg.owner.find(update, SimpleNamespace(args=args, application=SimpleNamespace(bot_data={}))))
    return update.replies


client.get("/api/me", headers=auth(A, "alpha_find"))
client.get("/api/me", headers=auth(B, None))
conn = _connect(path)
conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('c', ?, 'Ярик Тестов', 1, 2)", (A,))
conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('c', ?, 'Ярослав', 1, 2)", (B,))
conn.commit()
conn.close()


def card_id(update_replies):
    return update_replies[0]["text"].split("id: ")[1].split("\n")[0]


check("по нику: карточка с id", card_id(find_raw(["alpha_find"])), str(A))
check("с @ и регистром", card_id(find_raw(["@Alpha_Find"])), str(A))
check("по началу ника", card_id(find_raw(["alpha_f"])), str(A))
check("по имени (часть, регистр)", card_id(find_raw(["тестов"])), str(A))
check("по id", card_id(find_raw([str(A)])), str(A))
check("имя из двух слов", card_id(find_raw(["Ярик", "Тестов"])), str(A))
r = find_raw(["яр"])
check("несколько совпадений: список кнопок", (len(r), len(r[0]["reply_markup"].inline_keyboard), "несколько" in r[0]["text"]), (1, 2, True))
r = find_raw(["alpha_find"])
check("кнопки начисления", [b.callback_data for row in r[0]["reply_markup"].inline_keyboard for b in row],
      ["own:c:%d:%d" % (n, A) for n in (10000, 100000, 1000000)] + ["own:g:%d:%d" % (n, A) for n in (50, 250, 1000)])
check("не найден", find(["nobody_1"])[0].startswith("Никого не нашёл"), True)
check("формат", [find(a)[0].startswith("Формат") for a in ([],)], [True])
check("чужой молчит", find(["alpha_find"], uid=B), [])
check("в группе молчит", find(["alpha_find"], chat="supergroup"), [])


def press(data, uid=OWNER, chat="private"):
    update = FakeUpdate(chat, user_id=uid, query_data=data)
    asyncio.run(tg.owner.owner_panel_callback(update, SimpleNamespace(bot=SimpleNamespace(send_message=_noop), application=SimpleNamespace(bot_data={}))))
    return update.callback_query.message.replies


async def _noop(*a, **k):
    return None


def balances(uid):
    conn = _connect(path)
    try:
        return conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (uid,)).fetchone()[0], conn.execute("SELECT COALESCE((SELECT gems FROM gem_balances WHERE telegram_id = ?), 0)", (uid,)).fetchone()[0]
    finally:
        conn.close()


before = balances(A)
rep = press("own:c:10000:%d" % A)
check("кнопка фишек", (balances(A)[0] - before[0], rep[0]["text"].startswith("Готово")), (10000, True))
press("own:g:250:%d" % A)
check("кнопка кристаллов", balances(A)[1] - before[1], 250)
check("чужой нажимает: ничего", (press("own:c:10000:%d" % A, uid=B), balances(A)[0] - before[0]), ([], 10000))
check("в группе: ничего", (press("own:c:10000:%d" % A, chat="supergroup"), balances(A)[0] - before[0]), ([], 10000))
check("неразрешённая сумма: ничего", (press("own:c:99:%d" % A), balances(A)[0] - before[0]), ([], 10000))
check("нет игрока", press("own:c:10000:999999")[0]["text"], tg.owner.MISSING_PLAYER)
os.environ.pop("OWNER_CHAT_ID")
check("без владельца молчит", find(["alpha_find"]), [])
os.unlink(path)
print("test_username OK")
