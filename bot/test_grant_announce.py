"""Объявление в группах после /grantall: мок-бот, настоящий Telegram не используется."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest import mock

from telegram.error import BadRequest, Forbidden, RetryAfter

import bot
import db
from stubs import FakeChat, FakeUpdate

OWNER = 424242421
STRANGER = 777000111
NOW = 1_760_000_000
SECRET_NAMES = ("Аня", "Борис")

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "BACKUP_DIR", "BACKUP_ENABLED", "BACKUP_KEEP",
             "BACKUP_INTERVAL_HOURS", "BACKUP_PUBLIC_KEY", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
LINK = "https://t.me/test_bot/app"


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


def new_db():
    counter[0] += 1
    folder = os.path.join(tmp, "g%d" % counter[0])
    os.makedirs(folder)
    path = os.path.join(folder, "players.db")
    db.init_db(path)
    os.environ["DB_PATH"] = path
    os.environ["BACKUP_DIR"] = os.path.join(folder, "backups")
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=1000):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, 0, 0, 0, 0)", (uid, balance, NOW, NOW))


def balances(path):
    return dict(sql(path, "SELECT telegram_id, balance FROM players"))


class MockBot:
    """send_message пишет аргументы; failures: chat_id -> список исключений (по одному на попытку)."""

    def __init__(self, path, failures=None, gate=None):
        self.path, self.failures, self.gate = path, dict(failures or {}), gate
        self.sent = []
        self.committed_at_send = []

    async def send_message(self, **kw):
        if self.gate is not None:
            await self.gate.wait()
        queue = self.failures.get(kw["chat_id"])
        if queue:
            raise queue.pop(0)
        self.committed_at_send.append(sql(self.path, "SELECT COUNT(*) FROM admin_grants")[0][0])
        self.sent.append(kw)

    def to_chats(self, owner=OWNER):
        return [m for m in self.sent if m["chat_id"] != owner]

    def to_owner(self, owner=OWNER):
        return [m["text"] for m in self.sent if m["chat_id"] == owner]


def say(mock_bot, args, uid=OWNER, chat="private", store=None, wait=True):
    """Одна команда /grantall; возвращает ответы бота владельцу. wait: дождаться фоновой отправки."""
    store = store if store is not None else {}

    async def run():
        update = FakeUpdate(chat, user_id=uid)
        ctx = SimpleNamespace(args=args, bot=mock_bot, application=SimpleNamespace(bot_data=store))
        await bot.grantall(update, ctx)
        task = store.get("grant_announce_task")
        if wait and task is not None:
            await task
        return [r["text"] for r in update.replies]
    return asyncio.run(run())


def grant(mock_bot, store, amount="10000", gid="oct4", extra=()):
    say(mock_bot, [amount, gid] + list(extra), store=store)
    return say(mock_bot, ["confirm", gid], store=store)


try:
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    os.environ["GAME_LINK"] = LINK
    mock.patch.object(bot, "ANNOUNCE_PAUSE", 0).start()
    CHATS = (-1001, -1002, -1003)

    def world(chats=CHATS):
        path = new_db()
        for uid in (1, 2, 3):
            add_player(path, uid, 1000)
        for c in chats:
            db.chat_register(c, NOW, db_path=path)
        return path

    # ================= успешное начисление: одно объявление на группу =================
    path = world()
    mb, store = MockBot(path), {}
    out = say(mb, ["10000", "oct4"], store=store)
    assert "групп: 3" in out[0] and "игроков 3" in out[0], out
    check("превью ничего не шлёт и не начисляет", (mb.sent, balances(path)), ([], {1: 1000, 2: 1000, 3: 1000}))
    out = say(mb, ["confirm", "oct4"], store=store)
    assert out[0].startswith("Начисление выполнено"), out
    check("баланс начислен", balances(path), {1: 11000, 2: 11000, 3: 11000})
    check("по одному сообщению в каждую группу", sorted(m["chat_id"] for m in mb.to_chats()), sorted(CHATS))
    text = "🎁 Всем игрокам Necasino начислено 10 000 фишек! Заходите играть"
    check("текст объявления", {m["text"] for m in mb.to_chats()}, {text})
    button = mb.to_chats()[0]["reply_markup"].inline_keyboard[0][0]
    check("кнопка со ссылкой на игру", (button.text, button.url), ("Играть", LINK))
    check("итог владельцу", mb.to_owner(), ["Объявление: групп 3, отправлено 3, не доставлено 0"])
    check("отправка только после коммита", mb.committed_at_send, [1, 1, 1, 1])
    assert not any(n in m["text"] for m in mb.sent for n in SECRET_NAMES)
    assert all(not re.search(r"\d", m["text"].replace("10 000", "")) for m in mb.to_chats()), "лишние числа (id, балансы) в объявлении"

    # ================= повтор подтверждения и того же id =================
    n = len(mb.sent)
    out = say(mb, ["confirm", "oct4"], store=store)
    assert "Нет начисления" in out[0], out
    out = say(mb, ["10000", "oct4"], store=store)
    assert "уже было" in out[0], out
    check("повтор: ни начисления, ни сообщений", (len(mb.sent), balances(path)), (n, {1: 11000, 2: 11000, 3: 11000}))

    # ================= обработчик не ждёт отправки (фон) =================
    path = world()

    async def background():
        gate = asyncio.Event()
        mb2, st = MockBot(path, gate=gate), {}
        for args in (["5000", "bg1"], ["confirm", "bg1"]):
            update = FakeUpdate("private", user_id=OWNER)
            await bot.grantall(update, SimpleNamespace(args=args, bot=mb2, application=SimpleNamespace(bot_data=st)))
        returned_with_nothing_sent = mb2.sent == []
        task = st["grant_announce_task"]
        done_early = task.done()
        gate.set()
        await task
        return returned_with_nothing_sent, done_early, len(mb2.to_chats())
    check("команда вернулась до отправки, фоновая задача доставила", asyncio.run(background()), (True, False, 3))

    # ================= ошибки отправки не мешают остальным и начислению =================
    path = world()
    mb = MockBot(path, failures={-1002: [Forbidden("bot was kicked")], -1003: [BadRequest("chat not found")]})
    grant(mb, {})
    check("начисление выполнено", balances(path), {1: 11000, 2: 11000, 3: 11000})
    check("доставлено в исправную группу", [m["chat_id"] for m in mb.to_chats()], [-1001])
    check("недоступные группы забыты, исправная осталась", db.chat_ids(db_path=path), [-1001])
    check("итог с ошибками", mb.to_owner(), ["Объявление: групп 3, отправлено 1, не доставлено 2"])
    path = world()
    mb = MockBot(path, failures={-1001: [RuntimeError("сеть")]})
    grant(mb, {})
    check("прочая ошибка: группа не забывается, остальные получили", (sorted(m["chat_id"] for m in mb.to_chats()), db.chat_ids(db_path=path)),
          (sorted([-1002, -1003]), sorted(CHATS)))
    # RetryAfter: одно ожидание и один повтор
    path = world(chats=(-1001,))
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(sec, *a, **k):
        sleeps.append(sec)
        await real_sleep(0)
    mb = MockBot(path, failures={-1001: [RetryAfter(3)]})
    with mock.patch.object(bot.asyncio, "sleep", fake_sleep):
        grant(mb, {})
    check("RetryAfter: подождали и повторили, доставлено", (len(mb.to_chats()), 3 in [int(s) for s in sleeps]), (1, True))
    path = world(chats=(-1001,))
    mb = MockBot(path, failures={-1001: [RetryAfter(1), RetryAfter(1)]})
    with mock.patch.object(bot.asyncio, "sleep", fake_sleep):
        grant(mb, {})
    check("RetryAfter дважды: один повтор, потом пропуск; начисление цело", (len(mb.to_chats()), balances(path)[1], mb.to_owner()),
          (0, 11000, ["Объявление: групп 1, отправлено 0, не доставлено 1"]))
    # ошибка отправки итога владельцу не ломает ничего
    path = world()
    mb = MockBot(path, failures={OWNER: [RuntimeError("x")]})
    grant(mb, {})
    check("итог владельцу не дошёл: начисление и объявления целы", (balances(path)[1], len(mb.to_chats())), (11000, 3))

    # ================= отмена, silent, чужие =================
    path = world()
    mb, store = MockBot(path), {}
    say(mb, ["10000", "c1"], store=store)
    out = say(mb, ["confirm", "other"], store=store)
    assert "Неверный id" in out[0], out
    out = say(mb, ["confirm", "c1"], store=store)
    assert "Нет начисления" in out[0], out
    check("отмена: ничего не начислено и не отправлено", (mb.sent, balances(path)), ([], {1: 1000, 2: 1000, 3: 1000}))
    out = say(mb, ["10000", "s1", "silent"], store=store)
    assert "silent" in out[0] and "объявление не отправляется" in out[0], out
    say(mb, ["confirm", "s1"], store=store)
    check("silent: начислено, сообщений нет", (balances(path)[1], mb.sent, "grant_announce_task" in store), (11000, [], False))
    path = world()
    mb, store = MockBot(path), {}
    check("чужой: ничего", (say(mb, ["10000", "x1"], uid=STRANGER, store=store), say(mb, ["confirm", "x1"], uid=STRANGER, store=store)), ([], []))
    check("группа и канал: ничего", (say(mb, ["10000", "x1"], chat="supergroup", store=store), say(mb, ["10000", "x1"], chat="channel", store=store)), ([], []))
    check("ничего не запомнено, не отправлено, не начислено", (store, mb.sent, balances(path)), ({}, [], {1: 1000, 2: 1000, 3: 1000}))

    # ================= удалённые игроки не получают; нет групп =================
    path = world(chats=())
    db.delete_player_data(3, db_path=path, now=NOW)
    mb = MockBot(path)
    grant(mb, {})
    check("удалённый игрок не получил и не создан", balances(path), {1: 11000, 2: 11000})
    check("групп нет: сообщений группам нет, владельцу итог", (mb.to_chats(), mb.to_owner()), ([], ["Объявление не отправлено: бот не знает ни одной группы"]))

    # ================= запоминание групп =================
    path = new_db()
    bot._noted_chats.clear()

    def group_update(chat_id, kind="supergroup"):
        return SimpleNamespace(effective_chat=FakeChat(chat_id, kind))

    asyncio.run(bot.note_group(group_update(-500), None))
    asyncio.run(bot.note_group(group_update(-500), None))
    asyncio.run(bot.note_group(group_update(42, "private"), None))
    asyncio.run(bot.note_group(group_update(-600, "channel"), None))
    check("команда из группы запоминает группу один раз; личный чат и канал нет", db.chat_ids(db_path=path), [-500])
    check("в таблице только число и время", [r[1] for r in sql(path, "PRAGMA table_info(bot_chats)")], ["chat_id", "seen_at"])

    def member_update(chat_id, status, kind="group"):
        return SimpleNamespace(my_chat_member=SimpleNamespace(chat=FakeChat(chat_id, kind), new_chat_member=SimpleNamespace(status=status)))

    asyncio.run(bot.my_chat_member(member_update(-700, "member"), None))
    asyncio.run(bot.my_chat_member(member_update(-701, "administrator", "supergroup"), None))
    asyncio.run(bot.my_chat_member(member_update(55, "member", "private"), None))
    check("бота добавили в группы", db.chat_ids(db_path=path), [-701, -700, -500])
    asyncio.run(bot.my_chat_member(member_update(-700, "kicked"), None))
    asyncio.run(bot.my_chat_member(member_update(-500, "left", "supergroup"), None))
    check("бота убрали: записи удалены", db.chat_ids(db_path=path), [-701])
    asyncio.run(bot.note_group(group_update(-500), None))
    check("после удаления команда из группы снова запоминает", db.chat_ids(db_path=path), [-701, -500])
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
    kinds = sorted(type(h).__name__ for g in app.handlers.values() for h in g)
    assert "ChatMemberHandler" in kinds and "MessageHandler" in kinds, kinds
    check("обработчик групп в группе -1", -1 in app.handlers, True)

    privacy = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "privacy.html"), encoding="utf-8").read()
    assert "идентификатор каждой группы, в которой состоит бот" in privacy and "удаляется, когда бота убирают из группы" in privacy

    # ================= в логах нет идентификаторов групп и игроков =================
    for line in cap.lines:
        for secret in ("1001", "1002", "1003", str(OWNER), "10 000", "10000"):
            assert secret not in line, "секрет в логе: " + line[:80]
    assert any(l.startswith("Объявление о начислении: отправлено=") for l in cap.lines)
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
