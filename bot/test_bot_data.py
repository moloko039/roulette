import asyncio
import json
import logging
import os
import re
import sqlite3
import tempfile
from unittest import mock

import bot
from db import (delete_player_data, get_player, get_player_export, init_db, spin_roulette,
                touch_chat_member)
from stubs import FakeUpdate

# тест не зависит от окружения и bot/.env: на время теста эти переменные очищаются
_ENV_KEYS = ("GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

TOKEN = "123456:TEST-TOKEN-not-real"
ME, OTHER, STRANGER = 1234567891, 2222222222, 3333333333
CHAT_A, CHAT_B = "chat-secret-AAA", "chat-secret-BBB"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class _Bot:
    username = None

    async def send_message(self, **kwargs):
        pass  # ответы в группе уходят через send_message


class _Ctx:
    bot = _Bot()


def run(handler, update):
    asyncio.run(handler(update, _Ctx()))
    return update


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


def reset():
    for lim in (bot.group_limiter, bot.mydata_limiter, bot.delete_limiter, bot.balance_pair_limiter):
        lim.last.clear()
    bot.balance_chat_limiter.times.clear()


class Clock:
    def __init__(self, mono=1000.0, wall=1_700_000_000):
        self.mono, self.wall = mono, wall
        self.patches = [mock.patch.object(bot, "_clock", lambda: self.mono),
                        mock.patch.object(bot, "_wall", lambda: self.wall)]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def seed(path):
    """Два игрока: у каждого баланс, раунды и участие в беседах."""
    # после удаления действует защита от повторной регистрации (баланс 0): сбрасываем её,
    # чтобы тест снова мог завести игроков со стартовым балансом
    sql(path, "DELETE FROM deletion_tombstones")
    for table in ("players", "roulette_rounds", "chat_members"):
        sql(path, "DELETE FROM %s WHERE telegram_id IN (?, ?)" % table, (ME, OTHER))
    rng = lambda n: 17  # noqa: E731
    for uid, name, chats in [(ME, "СекретноеИмя", (CHAT_A, CHAT_B)), (OTHER, "Другой", (CHAT_A,))]:
        spin_roulette(uid, "seed-request-%d-1" % uid, [{"type": "red", "value": None, "amount": 10}],
                      now=1_700_000_000, db_path=path, rng=rng)
        spin_roulette(uid, "seed-request-%d-2" % uid, [{"type": "number", "value": 17, "amount": 5}],
                      now=1_700_000_100, db_path=path, rng=rng)
        for c in chats:
            touch_chat_member(c, uid, name, now=1_700_000_200, db_path=path)
    sql(path, "UPDATE players SET balance = 7654321 WHERE telegram_id = ?", (ME,))


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
old_db = os.environ.get("DB_PATH")
old_secret = os.environ.get("TOMBSTONE_SECRET")
os.environ["DB_PATH"] = path
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"  # удаление данных требует секрет
cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)
try:
    init_db(path)
    seed(path)
    reset()

    # ================= db: выгрузка и удаление =================
    ex = get_player_export(ME, db_path=path)
    check("player", sorted(ex["player"]), ["balance", "created_at", "income_level", "last_accrual", "rate", "storage_level", "telegram_id", "total_staked"])
    check("player.id", (ex["player"]["telegram_id"], ex["player"]["balance"]), (ME, 7654321))
    check("раундов", len(ex["rounds"]), 2)
    check("раунд", sorted(ex["rounds"][0]), ["bets", "number", "payout_total", "stake_total", "time"])
    check("порядок: новые первыми", [r["time"] for r in ex["rounds"]], [1_700_000_100, 1_700_000_000])
    check("беседы", [(c["name"], sorted(c)) for c in ex["chats"]], [("СекретноеИмя", ["first_seen", "last_seen", "name"])] * 2)
    dump = json.dumps(ex, ensure_ascii=False)
    for foreign in (str(OTHER), "Другой", CHAT_A, CHAT_B, "chat_instance"):
        assert foreign not in dump, "в выгрузке есть " + foreign
    check("нет такого игрока", get_player_export(STRANGER, db_path=path), None)
    # лимит раундов
    for i in range(110):
        sql(path, "INSERT INTO roulette_rounds VALUES (?, ?, 1, 1, 0, '[]', ?)", (ME, "bulk-request-%03d" % i, 1_700_001_000 + i))
    ex = get_player_export(ME, rounds_limit=100, db_path=path)
    check("не больше 100 раундов", len(ex["rounds"]), 100)
    check("самый новый первым", ex["rounds"][0]["time"], 1_700_001_109)
    sql(path, "DELETE FROM roulette_rounds WHERE request_id LIKE 'bulk-request-%'")

    # откат: ошибка при удалении третьей таблицы отменяет всё
    sql(path, "CREATE TRIGGER block_del BEFORE DELETE ON chat_members BEGIN SELECT RAISE(ABORT, 'нельзя'); END")
    try:
        delete_player_data(ME, db_path=path)
        raise AssertionError("ошибка не пробросилась")
    except sqlite3.DatabaseError:
        pass
    check("откат: всё на месте", counts(path, ME), (1, 2, 2))
    sql(path, "DROP TRIGGER block_del")

    # ================= /mydata =================
    with Clock() as clk:
        u = run(bot.mydata, FakeUpdate("private", user_id=ME))
        check("файл отправлен", (len(u.effective_message.documents), u.replies), (1, []))
        doc = u.effective_message.documents[0]
        check("имя файла", doc["filename"], "mydata.json")
        text = doc["data"].decode("utf-8")
        data = json.loads(text)
        check("поля", sorted(data), ["chats", "farm_purchases", "generated_at", "generated_at_iso", "mines_games", "player", "rounds"])
        check("время", (data["generated_at"], data["generated_at_iso"]), (1_700_000_000, "2023-11-14T22:13:20Z"))
        check("player", (data["player"]["telegram_id"], data["player"]["balance"]), (ME, 7654321))
        check("раунды", [(r["number"], r["stake_total"], r["payout_total"]) for r in data["rounds"]], [(17, 5, 180), (17, 10, 0)])
        check("chats.count", (data["chats"]["count"], len(data["chats"]["items"])), (2, 2))
        check("поля беседы", sorted(data["chats"]["items"][0]), ["first_seen", "last_seen", "name"])
        assert "Секретное" in text and "\\u" not in text, "имя должно быть в UTF-8 без \\u-экранирования"
        assert "\n  " in text, "нет отступов"
        for foreign in (str(OTHER), "Другой", CHAT_A, CHAT_B, "chat_instance"):
            assert foreign not in text, "в файле есть " + foreign

        # 60 секунд на пользователя
        clk.mono += 30
        u = run(bot.mydata, FakeUpdate("private", user_id=ME))
        check("слишком часто", (u.effective_message.documents, u.replies[0]["text"]), ([], "Слишком часто, повторите чуть позже"))
        clk.mono += 31
        check("через 61с можно", len(run(bot.mydata, FakeUpdate("private", user_id=ME)).effective_message.documents), 1)
        # другой пользователь не ограничен чужим запросом
        check("другой пользователь", len(run(bot.mydata, FakeUpdate("private", user_id=OTHER)).effective_message.documents), 1)

        # нет данных
        u = run(bot.mydata, FakeUpdate("private", user_id=STRANGER))
        check("нет данных", (u.replies[0]["text"], u.effective_message.documents), ("Данных о вас нет", []))

        # в группе отказ
        for kind in ("group", "supergroup"):
            u = run(bot.mydata, FakeUpdate(kind, user_id=ME, chat_id=-5))
            check("mydata в группе", (u.replies[0]["text"], u.effective_message.documents), ("Эта команда работает в личной переписке с ботом", []))
        reset()

        # ================= /deletemydata =================
        u = run(bot.deletemydata, FakeUpdate("private", user_id=ME))
        r = u.replies[0]
        assert r["text"].startswith("Будут удалены ваш баланс, уровни улучшений, история раундов и покупок, история игр в мины, незавершённая игра в мины (вместе со ставкой), а также участие в рейтингах. Это нельзя отменить.")
        assert "уровни улучшений" in r["text"] and "история раундов и покупок" in r["text"], r["text"]
        assert "1000 фишек" in r["text"] and "очисткой кэша Telegram" in r["text"]
        buttons = r["reply_markup"].inline_keyboard[0]
        check("кнопки", [b.text for b in buttons], ["Удалить всё", "Отмена"])
        check("callback_data", (buttons[0].callback_data, buttons[1].callback_data), ("del:yes:1700000000", "del:no"))
        check("ничего не удалено", counts(path, ME), (1, 2, 2))
        assert "parse_mode" not in r
        # 30 секунд
        clk.mono += 10
        u = run(bot.deletemydata, FakeUpdate("private", user_id=ME))
        check("слишком часто", u.replies[0]["text"], "Слишком часто, повторите чуть позже")
        clk.mono += 21
        check("через 31с можно", "reply_markup" in run(bot.deletemydata, FakeUpdate("private", user_id=ME)).replies[0], True)
        # в группе отказ
        u = run(bot.deletemydata, FakeUpdate("supergroup", user_id=ME, chat_id=-6))
        check("в группе", u.replies[0]["text"], "Эта команда работает в личной переписке с ботом")

        # шаблон callback_data
        for good in ("del:yes:1700000000", "del:yes:1", "del:no"):
            assert re.match(bot.CALLBACK_PATTERN, good), good
        for bad in ("del:yes:abc", "del:yes:1:2", "del:yes:", "del:maybe", "x:del:no", "del:yes:1700000000000000"):
            assert not re.match(bot.CALLBACK_PATTERN, bad), bad

        def press(data, user_id, chat_type="private"):
            u = FakeUpdate(chat_type, user_id=user_id, chat_id=user_id if chat_type == "private" else -50, query_data=data)
            run(bot.delete_callback, u)
            return u.callback_query

        before_other = counts(path, OTHER)
        # отмена ничего не удаляет
        q = press("del:no", ME)
        check("отмена", (q.answers, counts(path, ME), q.edits[0]["text"]), (1, (1, 2, 2), "Отменено, данные не тронуты"))
        # просроченное подтверждение
        q = press("del:yes:%d" % (clk.wall - 301), ME)
        check("просрочено", (q.answers, q.edits[0]["text"], counts(path, ME)),
              (1, "Время подтверждения истекло, отправьте /deletemydata снова", (1, 2, 2)))
        # ровно 5 минут ещё действует
        # из сообщения не в личной переписке: удаления нет, ответ на нажатие есть
        q = press("del:yes:%d" % clk.wall, ME, chat_type="supergroup")
        check("не в личке", (q.answers, q.edits, counts(path, ME)), (1, [], (1, 2, 2)))
        # нажал чужой игрок без данных: удаляется только его (то есть ничего)
        q = press("del:yes:%d" % clk.wall, STRANGER)
        check("чужой без данных", (q.answers, q.edits[0]["text"], counts(path, ME), counts(path, OTHER)),
              (1, "Данных для удаления нет", (1, 2, 2), before_other))
        # подтверждение: удаляются данные только нажавшего
        q = press("del:yes:%d" % (clk.wall - 299), ME)
        check("ответ на нажатие", q.answers, 1)
        t = q.edits[0]["text"]
        assert t.startswith("Готово: ваши данные удалены") and "игрок — 1" in t and "раунды рулетки — 2" in t and "участие в рейтингах — 2" in t and "покупки улучшений — 0" in t, t
        assert "reply_markup" not in q.edits[0], "кнопки должны исчезнуть"
        check("данные нажавшего удалены", counts(path, ME), (0, 0, 0))
        check("другой игрок цел", counts(path, OTHER), before_other)
        check("чужой профиль цел", get_player(OTHER, now=1_700_000_100, db_path=path)["balance"] > 0, True)
        # повторное нажатие
        q = press("del:yes:%d" % clk.wall, ME)
        check("повторное нажатие", (q.answers, q.edits[0]["text"]), (1, "Данных для удаления нет"))
        check("после удаления нет данных", run(bot.mydata, FakeUpdate("private", user_id=ME)).replies[0]["text"], "Данных о вас нет")
        # после удаления игра начинается заново, но 30 дней без стартовых фишек (защита от абьюза)
        check("новый игрок: в период защиты баланс 0", get_player(ME, now=1_700_000_500, db_path=path)["balance"], 0)

    # id берётся из данных Telegram, а не из callback_data: удаляем второго игрока его же нажатием
    with Clock() as clk:
        q = FakeUpdate("private", user_id=OTHER, chat_id=OTHER, query_data="del:yes:%d" % clk.wall)
        run(bot.delete_callback, q)
        check("OTHER удалил себя", counts(path, OTHER), (0, 0, 0))
        check("ME (новый) не затронут", counts(path, ME)[0], 1)

    # ================= db: удаление =================
    seed(path)
    init_db(path)
    check("удалено по таблицам", delete_player_data(OTHER, db_path=path), {"players": 1, "roulette_rounds": 2, "chat_members": 1, "farm_purchases": 0, "mines_games": 0})
    check("повтор ничего не удаляет", delete_player_data(OTHER, db_path=path), {"players": 0, "roulette_rounds": 0, "chat_members": 0, "farm_purchases": 0, "mines_games": 0})
    check("другие не затронуты", counts(path, ME)[0], 1)

    # ================= логи =================
    reset()
    seed(path)
    with Clock():
        for kind, uid, cid in (("private", ME, ME), ("group", ME, -90)):
            for h in (bot.start, bot.play, bot.balance, bot.help_command, bot.privacy, bot.developer_info,
                      bot.mydata, bot.deletemydata):
                run(h, FakeUpdate(kind, user_id=uid, chat_id=cid))
                reset()
        run(bot.delete_callback, FakeUpdate("private", user_id=ME, chat_id=ME, query_data="del:yes:1700000000"))
        wrapped = bot.guarded(bot.balance)
        with mock.patch.object(bot, "get_player", side_effect=RuntimeError("balance=7654321 id=%d" % ME)):
            asyncio.run(wrapped(FakeUpdate("private", user_id=ME), None))
        asyncio.run(bot.on_error(None, type("C", (), {"error": ValueError("СекретноеИмя %d" % ME)})()))
    assert cap.lines, "логи не перехвачены, проверка бессмысленна"
    for line in cap.lines:
        for secret in (TOKEN, str(ME), str(OTHER), "СекретноеИмя", "Другой", "7654321", CHAT_A, CHAT_B, "api.telegram.org"):
            assert secret not in line, f"в логе есть {secret!r}: {line}"
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    if old_db is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = old_db
    if old_secret is None:
        os.environ.pop("TOMBSTONE_SECRET", None)
    else:
        os.environ["TOMBSTONE_SECRET"] = old_secret
    os.remove(path)

for _k, _v in _saved_env.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

print("Все проверки прошли")
