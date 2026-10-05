import asyncio
import contextlib
import logging
import os
import re
import sqlite3
import tempfile
from unittest import mock

import bot
from db import init_db
from stubs import FakeChat, FakeUpdate, StubApplication

# тест не зависит от окружения и bot/.env: на время теста эти переменные очищаются
_ENV_KEYS = ("GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

LINK = "https://t.me/test_bot/game"
PRIVACY = "https://example.test/privacy.html"
CONTACT = "dev@example.test"
WEBAPP = "https://example.test/app/"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class FakeBot:
    """Запоминает send_message (ответы в группе уходят через него, а не через reply_text)."""

    def __init__(self, username=None):
        self.sent = []
        self.username = username

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class Ctx:
    def __init__(self, username=None):
        self.bot = FakeBot(username)


def run(handler, update, username=None):
    ctx = Ctx(username)
    asyncio.run(handler(update, ctx))
    update.sent = ctx.bot.sent
    return update


def answers(update):
    """Сколько ответов бот отправил в любом виде."""
    return len(update.replies) + len(update.sent)


@contextlib.contextmanager
def mode(value):
    old = os.environ.get("PLAY_MODE")
    try:
        if value is None:
            os.environ.pop("PLAY_MODE", None)
        else:
            os.environ["PLAY_MODE"] = value
        yield
    finally:
        if old is None:
            os.environ.pop("PLAY_MODE", None)
        else:
            os.environ["PLAY_MODE"] = old


@contextlib.contextmanager
def env(**values):
    """Задаёт GAME_LINK, PRIVACY_URL, DEVELOPER_CONTACT; значение None удаляет переменную."""
    names = ("GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT")
    saved = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            v = values.get(n)
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


FULL = dict(GAME_LINK=LINK, PRIVACY_URL=PRIVACY, DEVELOPER_CONTACT=CONTACT)


def reset():
    for lim in (bot.group_limiter, bot.mydata_limiter, bot.delete_limiter, bot.balance_pair_limiter):
        lim.last.clear()
    bot.balance_chat_limiter.times.clear()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t
        self.patch = mock.patch.object(bot, "_clock", lambda: self.t)

    def __enter__(self):
        self.patch.start()
        return self

    def __exit__(self, *a):
        self.patch.stop()


def button(update, index=0):
    markup = update.replies[0]["reply_markup"]
    return markup.inline_keyboard[0][index]


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
old_db = os.environ.get("DB_PATH")
old_secret = os.environ.get("TOMBSTONE_SECRET")
os.environ["DB_PATH"] = path
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"  # без него бот предупреждает при старте
try:
    init_db()
    with mock.patch.object(bot, "WEBAPP_URL", WEBAPP), env(**FULL):
        reset()

        # ---------- /start и /play ----------
        u = run(bot.start, FakeUpdate("private"))
        check("/start private", len(u.replies), 1)
        assert "Список команд: /help" in u.replies[0]["text"] and u.replies[0]["text"].startswith("Нажми кнопку")
        b = button(u)
        check("кнопка web_app", (b.text, b.web_app.url, b.url), ("Играть", WEBAPP, None))

        u = run(bot.play, FakeUpdate("private"))
        check("/play private как /start", (u.replies[0]["text"].startswith("Нажми кнопку"), button(u).web_app.url), (True, WEBAPP))

        for kind in ("group", "supergroup"):
            # режим card (по умолчанию): только эмодзи и карточка, без кнопки, ссылки и цитаты
            for m in (None, "card", "CARD"):
                with mode(m):
                    u = run(bot.play, FakeUpdate(kind, user_id=5, chat_id=-100))
                    check("card: reply_text не используется", u.replies, [])
                    m1 = u.sent[0]
                    check("card: чат и текст", (m1["chat_id"], m1["text"]), (-100, "🎰"))
                    assert LINK not in m1["text"] and "Играть" not in m1["text"], "в тексте ссылка или слово"
                    check("card: предпросмотр", m1["link_preview_options"].url, LINK)
                    assert not m1["link_preview_options"].is_disabled, "предпросмотр отключён"
                    for absent in ("reply_markup", "reply_parameters", "reply_to_message_id", "parse_mode", "message_thread_id"):
                        assert absent not in m1, "в card есть " + absent
                    reset()
        u = run(bot.start, FakeUpdate("group", chat_id=-101))
        check("/start в группе как /play", (u.replies, u.sent[0]["text"], u.sent[0]["link_preview_options"].url), ([], "🎰", LINK))
        reset()

        # режим link: ссылка внутри эмодзи, parse_mode HTML, карточку строит Telegram
        with mode("link"):
            u = run(bot.play, FakeUpdate("supergroup", chat_id=-102))
            m1 = u.sent[0]
            check("link", (m1["text"], m1["parse_mode"]), ('<a href="%s">🎰</a>' % LINK, "HTML"))
            for absent in ("reply_markup", "reply_parameters", "link_preview_options"):
                assert absent not in m1
            reset()
            with env(GAME_LINK="https://t.me/bot/app?startapp=a&b=\"c\""):
                u = run(bot.play, FakeUpdate("group", chat_id=-103))
                check("link: значение экранируется", u.sent[0]["text"],
                      '<a href="https://t.me/bot/app?startapp=a&amp;b=&quot;c&quot;">🎰</a>')
                reset()

        # режим button: карточка и кнопка с url (не web_app)
        with mode("button"):
            u = run(bot.play, FakeUpdate("group", chat_id=-104))
            m1 = u.sent[0]
            check("button", (m1["text"], m1["link_preview_options"].url), ("🎰", LINK))
            b = m1["reply_markup"].inline_keyboard[0][0]
            check("кнопка", (b.text, b.url, b.web_app), ("Играть", LINK, None))
            assert "reply_parameters" not in m1
            reset()

        # неизвестный PLAY_MODE даёт card
        for weird in ("fancy", "cards", "  ", "1"):
            with mode(weird):
                check("play_mode %r" % weird, bot.play_mode(), "card")
                u = run(bot.play, FakeUpdate("group", chat_id=-105))
                m1 = u.sent[0]
                check("неизвестный режим как card", (m1["text"], m1["link_preview_options"].url, "reply_markup" in m1), ("🎰", LINK, False))
                reset()

        # тема форума: message_thread_id сохраняется, вне темы его нет
        u = FakeUpdate("supergroup", chat_id=-106)
        u.effective_message.is_topic_message = True
        u.effective_message.message_thread_id = 777
        run(bot.play, u)
        check("тема форума", u.sent[0]["message_thread_id"], 777)
        reset()
        u = FakeUpdate("supergroup", chat_id=-107)
        u.effective_message.is_topic_message = False
        u.effective_message.message_thread_id = 888  # ответ в обычной группе: не тема
        run(bot.play, u)
        assert "message_thread_id" not in u.sent[0], "тема подставлена вне форума"
        reset()

        # без parse_mode во всех ответах
        for h in (bot.start, bot.play, bot.help_command, bot.privacy, bot.developer_info, bot.balance):
            for kind in ("private", "group"):
                u = run(h, FakeUpdate(kind, chat_id=-7 if kind == "group" else None))
                for r in u.replies:
                    assert "parse_mode" not in r, f"parse_mode в {h.__name__}"
                for r in u.sent:
                    assert "parse_mode" not in r, f"parse_mode в {h.__name__} (режим card)"
                reset()

        # ---------- ограничение 20 секунд на чат ----------
        with Clock(1000.0) as clk:
            reset()
            check("1-й вызов", answers(run(bot.play, FakeUpdate("group", chat_id=-1))), 1)
            clk.t = 1010
            check("2-й вызов через 10с молча", answers(run(bot.play, FakeUpdate("group", chat_id=-1))), 0)
            check("другой чат отвечает", answers(run(bot.play, FakeUpdate("group", chat_id=-2))), 1)
            check("/balance не зависит от лимита /play", answers(run(bot.balance, FakeUpdate("group", chat_id=-1))), 1)
            clk.t = 1021
            check("через 21с отвечает", answers(run(bot.play, FakeUpdate("group", chat_id=-1))), 1)
            # очистка словаря
            reset()
            clk.t = 2000
            for i in range(50):
                run(bot.play, FakeUpdate("group", chat_id=-1000 - i))
            check("50 чатов записаны", len(bot.group_limiter.last), 50)
            clk.t = 2025
            run(bot.play, FakeUpdate("group", chat_id=-5))
            check("старые записи удалены", len(bot.group_limiter.last), 1)
            reset()

        # ---------- /balance ----------
        init_db()
        u = run(bot.balance, FakeUpdate("private", user_id=42))
        assert u.replies[0]["text"].startswith("Баланс: 1000 фишек\nДо следующего начисления: "), u.replies
        # «до следующего начисления» это секунды до ближайшей минутной границы (1..60), как seconds_to_next в /api/me
        T_MIN = 1_760_000_040    # граница минуты
        for offset, seconds, uid in ((0, 60, 7001), (1, 59, 7002), (30, 30, 7003), (59, 1, 7004)):
            with mock.patch.object(bot, "_wall", return_value=T_MIN + offset):
                u = run(bot.balance, FakeUpdate("private", user_id=uid))
            check("до следующего начисления, секунда %d минуты" % offset, u.replies[0]["text"],
                  "Баланс: 1000 фишек\nДо следующего начисления: %d сек." % seconds)
        for now in range(T_MIN, T_MIN + 61):
            with mock.patch.object(bot, "_wall", return_value=now):
                text = bot._balance_text(7001)
            n = int(text.rsplit(": ", 1)[1].split(" ")[0])
            assert 1 <= n <= 60 and n == bot.next_tick_in(now), (now, text)
        assert "мин" not in bot._balance_text(7001).split("\n")[1]
        # в группе настоящий баланс, тем же текстом, что в личке; ответ с цитатой (reply_text)
        for kind in ("group", "supergroup"):
            u = run(bot.balance, FakeUpdate(kind, user_id=4200 + len(kind), chat_id=-9))
            assert u.replies[0]["text"].startswith("Баланс: 1000 фишек\nДо следующего начисления: "), u.replies
            check("balance в группе: цитата, а не send_message", (len(u.replies), u.sent), (1, []))
            assert "parse_mode" not in u.replies[0]
            reset()

        # ---------- channel: молчание ----------
        for h in (bot.start, bot.play, bot.balance, bot.help_command, bot.privacy, bot.developer_info,
                  bot.mydata, bot.deletemydata):
            u = run(h, FakeUpdate("channel", chat_id=-777))
            check("channel молчит: " + h.__name__, (u.replies, u.sent, u.effective_message.documents), ([], [], []))

        # ---------- /help ----------
        t = run(bot.help_command, FakeUpdate("private")).replies[0]["text"]
        for c in ("/start", "/play", "/balance", "/help" if False else "/privacy", "/developer_info", "/mydata", "/deletemydata"):
            assert c in t, "в /help (private) нет " + c
        t = run(bot.help_command, FakeUpdate("group", chat_id=-3)).sent[0]["text"]
        assert "/play" in t and "/help" in t
        for c in ("/mydata", "/deletemydata", "/balance", "/privacy", "/developer_info"):
            assert c not in t, "в /help группы лишняя команда " + c

        # ---------- /privacy и /developer_info ----------
        check("/privacy", run(bot.privacy, FakeUpdate("private")).replies[0]["text"], PRIVACY)
        check("/privacy в группе", run(bot.privacy, FakeUpdate("group", chat_id=-4)).sent[0]["text"], PRIVACY)
        check("/developer_info", run(bot.developer_info, FakeUpdate("private")).replies[0]["text"],
              f"Независимый разработчик. Контакт: {CONTACT}. Политика конфиденциальности: {PRIVACY}")

        # ---------- группы: help, privacy, developer_info через send_message ----------
        NAME = "TestBot"
        for h in (bot.help_command, bot.privacy, bot.developer_info):
            for kind in ("group", "supergroup"):
                reset()
                u = run(h, FakeUpdate(kind, chat_id=-300), username=NAME)
                check("%s в %s: send_message" % (h.__name__, kind), (u.replies, len(u.sent)), ([], 1))
                m1 = u.sent[0]
                check("чат", m1["chat_id"], -300)
                for absent in ("reply_parameters", "reply_to_message_id", "parse_mode", "reply_markup", "message_thread_id"):
                    assert absent not in m1, f"{h.__name__}: в ответе есть {absent}"
        reset()
        # из темы форума: та же тема во всех групповых ответах
        for h in (bot.help_command, bot.privacy, bot.developer_info, bot.play):
            reset()
            u = FakeUpdate("supergroup", chat_id=-301)
            u.effective_message.is_topic_message = True
            u.effective_message.message_thread_id = 555
            run(h, u, username=NAME)
            check("тема форума: " + h.__name__, u.sent[0]["message_thread_id"], 555)
            u = FakeUpdate("supergroup", chat_id=-301)
            u.effective_message.is_topic_message = False
            u.effective_message.message_thread_id = 555
            reset()
            run(h, u, username=NAME)
            assert "message_thread_id" not in u.sent[0], "тема вне форума: " + h.__name__
        reset()

        # текст группового /help
        want = ("🎰 /play@TestBot — открыть игру\n💰 /balance@TestBot — ваш баланс\nℹ️ /help@TestBot — список команд\n"
                "🔒 /privacy@TestBot — политика конфиденциальности\n👤 /developer_info@TestBot — о разработчике\n"
                "Копия и удаление данных — в личной переписке с ботом.")
        check("групповой /help с именем", run(bot.help_command, FakeUpdate("group", chat_id=-302), username=NAME).sent[0]["text"], want)
        reset()
        t = run(bot.help_command, FakeUpdate("group", chat_id=-303)).sent[0]["text"]
        check("без имени бота прежний текст", t, bot.GROUP_HELP)
        assert "@" not in t
        reset()
        check("личный /help без изменений", run(bot.help_command, FakeUpdate("private"), username=NAME).replies[0]["text"], bot.PRIVATE_HELP)

        # лимит 20 секунд на чат для help/privacy/developer_info (общий с /play)
        with Clock(5000.0) as clk:
            reset()
            check("help 1", answers(run(bot.help_command, FakeUpdate("group", chat_id=-310))), 1)
            clk.t = 5010
            check("privacy через 10с молча", answers(run(bot.privacy, FakeUpdate("group", chat_id=-310))), 0)
            check("другой чат отвечает", answers(run(bot.developer_info, FakeUpdate("group", chat_id=-311))), 1)
            clk.t = 5021
            check("через 21с отвечает", answers(run(bot.privacy, FakeUpdate("group", chat_id=-310))), 1)
            reset()

        # ---------- /balance в группе: защита и лимиты ----------
        def players_count(uid):
            conn = sqlite3.connect(path)
            try:
                return conn.execute("SELECT COUNT(*) FROM players WHERE telegram_id = ?", (uid,)).fetchone()[0]
            finally:
                conn.close()

        # новый игрок создаётся с 1000 фишек
        check("до вызова игрока нет", players_count(8001), 0)
        u = run(bot.balance, FakeUpdate("group", user_id=8001, chat_id=-400))
        assert u.replies[0]["text"].startswith("Баланс: 1000 фишек"), u.replies
        check("игрок создан", players_count(8001), 1)
        for foreign in ("8001", "Тест", "-400"):
            assert foreign not in u.replies[0]["text"], "в ответе лишние данные: " + foreign
        reset()

        # от имени чата (анонимный администратор, канал), от бота, без пользователя: молча и без записи
        u = FakeUpdate("supergroup", user_id=8002, chat_id=-401)
        u.effective_message.sender_chat = FakeChat(-401, "supergroup")
        run(bot.balance, u)
        check("sender_chat", (answers(u), players_count(8002)), (0, 0))
        u = FakeUpdate("group", user_id=8003, chat_id=-402)
        u.effective_user.is_bot = True
        run(bot.balance, u)
        check("бот", (answers(u), players_count(8003)), (0, 0))
        u = FakeUpdate("group", user_id=8004, chat_id=-403)
        u.effective_user = None
        run(bot.balance, u)
        check("без пользователя", answers(u), 0)
        # отказ не расходует лимиты: сразу после отказа настоящий игрок отвечает
        u = run(bot.balance, FakeUpdate("group", user_id=8005, chat_id=-401))
        check("после отказов отвечает", (answers(u), players_count(8005)), (1, 1))
        reset()

        # 20 секунд на пару (чат, игрок); другой игрок в том же чате не блокируется
        with Clock(7000.0) as clk:
            check("первый запрос", answers(run(bot.balance, FakeUpdate("group", user_id=8010, chat_id=-410))), 1)
            clk.t = 7010
            check("повтор через 10с молча", answers(run(bot.balance, FakeUpdate("group", user_id=8010, chat_id=-410))), 0)
            check("другой игрок в том же чате", answers(run(bot.balance, FakeUpdate("group", user_id=8011, chat_id=-410))), 1)
            check("тот же игрок в другом чате", answers(run(bot.balance, FakeUpdate("group", user_id=8010, chat_id=-411))), 1)
            clk.t = 7021
            check("через 21с снова", answers(run(bot.balance, FakeUpdate("group", user_id=8010, chat_id=-410))), 1)

            # не больше 10 ответов в минуту на чат
            reset()
            clk.t = 8000.0
            got = []
            for i in range(14):
                got.append(answers(run(bot.balance, FakeUpdate("group", user_id=9000 + i, chat_id=-420))))
                clk.t += 1
            check("10 в минуту на чат", got, [1] * 10 + [0] * 4)
            check("другой чат не затронут", answers(run(bot.balance, FakeUpdate("group", user_id=9100, chat_id=-421))), 1)
            clk.t = 8000.0 + 61
            check("через минуту снова", answers(run(bot.balance, FakeUpdate("group", user_id=9200, chat_id=-420))), 1)

            # очистка словарей
            reset()
            clk.t = 9000.0
            for i in range(30):
                run(bot.balance, FakeUpdate("group", user_id=9300 + i, chat_id=-500 - i))
            check("записано", (len(bot.balance_pair_limiter.last), len(bot.balance_chat_limiter.times)), (30, 30))
            clk.t = 9070.0
            run(bot.balance, FakeUpdate("group", user_id=9400, chat_id=-600))
            check("старые записи удалены", (len(bot.balance_pair_limiter.last), len(bot.balance_chat_limiter.times)), (1, 1))
            reset()

        # личный /balance без групповых лимитов
        for _ in range(3):
            check("личный /balance", answers(run(bot.balance, FakeUpdate("private", user_id=8020))), 1)

    # ---------- настройки не заданы или неверны ----------
    UN = "Эта функция пока недоступна"
    with mock.patch.object(bot, "WEBAPP_URL", WEBAPP):
        with env():
            reset()
            check("/play без GAME_LINK", run(bot.play, FakeUpdate("group", chat_id=-10)).replies[0]["text"], UN)
            check("/privacy без PRIVACY_URL", run(bot.privacy, FakeUpdate("private")).replies[0]["text"], UN)
            check("/developer_info без всего", run(bot.developer_info, FakeUpdate("private")).replies[0]["text"], UN)
        with env(GAME_LINK=LINK, PRIVACY_URL=PRIVACY):
            check("/developer_info без контакта", run(bot.developer_info, FakeUpdate("private")).replies[0]["text"], UN)
        with env(GAME_LINK=LINK, DEVELOPER_CONTACT=CONTACT):
            check("/developer_info без PRIVACY_URL", run(bot.developer_info, FakeUpdate("private")).replies[0]["text"], UN)
        for bad in ["http://t.me/bot/app", "https://t.me/bot app", "https://example.com/x", "https://t.me/", "t.me/bot/app",
                    "https://t.me/a\nb", "   "]:
            with env(GAME_LINK=bad):
                reset()
                check("неверный GAME_LINK %r" % bad, run(bot.play, FakeUpdate("group", chat_id=-11)).replies[0]["text"], UN)
        with env(PRIVACY_URL="http://example.test/x"):
            check("PRIVACY_URL не https", run(bot.privacy, FakeUpdate("private")).replies[0]["text"], UN)
        with env(GAME_LINK="  " + LINK + "  "):
            reset()
            u = run(bot.play, FakeUpdate("group", chat_id=-12))
            check("с пробелами по краям", (u.sent[0]["link_preview_options"].url, u.replies), (LINK, []))
    with mock.patch.object(bot, "WEBAPP_URL", None), env(**FULL):
        check("/start без WEBAPP_URL", run(bot.start, FakeUpdate("private")).replies[0]["text"], UN)
    reset()

    # ---------- предупреждение при старте: одно, только имена ----------
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.records = []

        def emit(self, record):
            self.records.append(record)

    cap = Capture()
    logging.getLogger().addHandler(cap)
    old_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        with env():
            bot.warn_missing_config()
        warns = [r for r in cap.records if r.levelno == logging.WARNING]
        check("одно предупреждение", len(warns), 1)
        text = warns[0].getMessage()
        for name in ("GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT"):
            assert name in text
        cap.records.clear()
        with env(**FULL):
            bot.warn_missing_config()
        check("всё задано: без предупреждений", [r for r in cap.records if r.levelno >= logging.WARNING], [])
        cap.records.clear()
        with env(GAME_LINK=LINK, PRIVACY_URL="плохо"):
            bot.warn_missing_config()
        text = [r for r in cap.records if r.levelno == logging.WARNING][0].getMessage()
        assert "PRIVACY_URL" in text and "DEVELOPER_CONTACT" in text and "GAME_LINK" not in text.split(":")[1].split(".")[0]
        assert "плохо" not in text, "значение переменной попало в лог"
        # нет TOMBSTONE_SECRET: отдельное предупреждение без значений
        cap.records.clear()
        saved = os.environ.pop("TOMBSTONE_SECRET")
        try:
            with env(**FULL):
                bot.warn_missing_config()
        finally:
            os.environ["TOMBSTONE_SECRET"] = saved
        warns = [r.getMessage() for r in cap.records if r.levelno == logging.WARNING]
        check("без TOMBSTONE_SECRET: одно предупреждение", len(warns), 1)
        assert "TOMBSTONE_SECRET" in warns[0] and saved not in warns[0]
        # неизвестный PLAY_MODE: одно предупреждение (без значения), известные значения молчат
        for good in (None, "card", "link", "button", "Link"):
            cap.records.clear()
            with env(**FULL), mode(good):
                bot.warn_missing_config()
            check("PLAY_MODE %r без предупреждений" % good, [r for r in cap.records if r.levelno >= logging.WARNING], [])
        cap.records.clear()
        with env(**FULL), mode("fancy"):
            bot.warn_missing_config()
        warns = [r.getMessage() for r in cap.records if r.levelno == logging.WARNING]
        check("неизвестный PLAY_MODE: одно предупреждение", len(warns), 1)
        assert "PLAY_MODE" in warns[0] and "fancy" not in warns[0]
    finally:
        logging.getLogger().removeHandler(cap)
        logging.getLogger().setLevel(old_level)

    # ---------- меню команд ----------
    app = StubApplication()
    asyncio.run(bot.register_commands(app))
    (priv, priv_scope), (grp, grp_scope) = app.bot.commands_calls
    check("команды личных", [c.command for c in priv],
          ["start", "play", "balance", "help", "privacy", "developer_info", "mydata", "deletemydata", "paysupport", "terms"])
    check("область личных", priv_scope.type, "all_private_chats")
    check("команды групп", [c.command for c in grp], ["play", "balance", "help", "privacy", "developer_info"])
    check("область групп", grp_scope.type, "all_group_chats")
    assert all(c.description for c in priv + grp)

    app = StubApplication()
    app.bot.fail_commands = ConnectionError("https://api.telegram.org/bot123:SECRET/setMyCommands")
    cap = Capture()
    logging.getLogger().addHandler(cap)
    try:
        asyncio.run(bot.register_commands(app))  # не должно бросить исключение
    finally:
        logging.getLogger().removeHandler(cap)
    lines = [r.getMessage() for r in cap.records]
    assert any("ConnectionError" in l for l in lines) and not any("SECRET" in l or "api.telegram.org" in l for l in lines)

    # ---------- сборка приложения и обработка ошибок ----------
    asyncio.set_event_loop(asyncio.new_event_loop())  # Python 3.9: Queue() внутри Application ищет цикл
    real = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
    check("обработчики", len(real.handlers[0]), 21)  # 8 команд, /paysupport и /terms, скрытые /backupnow, /grantall, /give, /giveitem, /refund, /regrant, /teststars, кнопки удаления, my_chat_member, pre_checkout и successful_payment
    assert bot.on_error in real.error_handlers, "нет обработчика ошибок"

    cap = Capture()
    logging.getLogger().addHandler(cap)
    try:
        wrapped = [h for h in real.handlers[0] if getattr(h, "commands", None) and "balance" in h.commands][0].callback
        u = FakeUpdate("private", user_id=1234567891)
        with mock.patch.object(bot, "get_player", side_effect=RuntimeError("balance=7654321 name=Секрет")):
            asyncio.run(wrapped(u, None))  # исключение не вылетает
        check("ответа нет", u.replies, [])

        class Ctx:
            error = ValueError("secret text 7654321")
        asyncio.run(bot.on_error(u, Ctx()))
    finally:
        logging.getLogger().removeHandler(cap)
    lines = [r.getMessage() for r in cap.records if r.name.startswith("depnaya")]
    check("в логе только типы", lines, ["Ошибка в обработчике balance: RuntimeError", "Ошибка обработки обновления: ValueError"])
finally:
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
