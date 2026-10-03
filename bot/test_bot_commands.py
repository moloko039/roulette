import asyncio
import contextlib
import logging
import os
import re
import tempfile
from unittest import mock

import bot
from db import init_db
from stubs import FakeUpdate, StubApplication

LINK = "https://t.me/test_bot/game"
PRIVACY = "https://example.test/privacy.html"
CONTACT = "dev@example.test"
WEBAPP = "https://example.test/app/"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class FakeBot:
    """Запоминает send_message (ответы в группе уходят через него, а не через reply_text)."""

    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class Ctx:
    def __init__(self):
        self.bot = FakeBot()


def run(handler, update):
    ctx = Ctx()
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
    for lim in (bot.group_limiter, bot.mydata_limiter, bot.delete_limiter):
        lim.last.clear()


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
os.environ["DB_PATH"] = path
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
            check("/balance делит лимит с /play", answers(run(bot.balance, FakeUpdate("group", chat_id=-1))), 0)
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
        u = run(bot.balance, FakeUpdate("supergroup", user_id=42, chat_id=-9))
        check("/balance в группе", u.replies[0]["text"],
              "Баланс смотрите в игре (вкладка «Профиль») или в личной переписке с ботом")
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
        t = run(bot.help_command, FakeUpdate("group", chat_id=-3)).replies[0]["text"]
        assert "/play" in t and "/help" in t
        for c in ("/mydata", "/deletemydata", "/balance", "/privacy", "/developer_info"):
            assert c not in t, "в /help группы лишняя команда " + c

        # ---------- /privacy и /developer_info ----------
        check("/privacy", run(bot.privacy, FakeUpdate("private")).replies[0]["text"], PRIVACY)
        check("/privacy в группе", run(bot.privacy, FakeUpdate("group", chat_id=-4)).replies[0]["text"], PRIVACY)
        check("/developer_info", run(bot.developer_info, FakeUpdate("private")).replies[0]["text"],
              f"Независимый разработчик. Контакт: {CONTACT}. Политика конфиденциальности: {PRIVACY}")

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
          ["start", "play", "balance", "help", "privacy", "developer_info", "mydata", "deletemydata"])
    check("область личных", priv_scope.type, "all_private_chats")
    check("команды групп", [c.command for c in grp], ["play", "help"])
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
    check("обработчики", len(real.handlers[0]), 9)
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
    os.remove(path)

print("Все проверки прошли")
