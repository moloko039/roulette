import asyncio
import html
import io
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                      InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update, WebAppInfo)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import antiabuse
from db import delete_player_data, get_player, get_player_export, init_db
from economy import HOUR
from notify import load_owner_id

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")

logger = logging.getLogger("depnaya.bot")

UNAVAILABLE = "Эта функция пока недоступна"
PRIVATE_ONLY = "Эта команда работает в личной переписке с ботом"
CHAT_TYPES = ("private", "group", "supergroup")  # в channel бот молчит
GROUP_TYPES = ("group", "supergroup")

GROUP_INTERVAL = 20      # не чаще одного ответа на чат раз в 20 секунд
MYDATA_INTERVAL = 60     # /mydata: не чаще раза в минуту на пользователя
DELETE_INTERVAL = 30     # /deletemydata: не чаще раза в 30 секунд на пользователя
CONFIRM_TTL = 300        # подтверждение удаления действует 5 минут
EXPORT_ROUNDS = 100      # сколько последних раундов попадает в /mydata
LIMITER_MAX_KEYS = 10000


# ---------- время (отдельные функции, чтобы тесты могли их подменять) ----------

def _clock():
    return time.monotonic()


def _wall():
    return int(time.time())


class RateLimiter:
    """Не чаще одного раза за interval секунд на ключ. Старые записи удаляются при каждом
    обращении, так что словарь содержит только ключи за последние interval секунд."""

    def __init__(self, interval):
        self.interval = interval
        self.last = {}

    def can(self, key):
        now = _clock()
        for k in [k for k, t in self.last.items() if now - t >= self.interval]:
            del self.last[k]
        return key not in self.last and len(self.last) < LIMITER_MAX_KEYS

    def record(self, key):
        self.last[key] = _clock()

    def allow(self, key):
        if not self.can(key):
            return False
        self.record(key)
        return True


class WindowLimiter:
    """Не больше max_count раз за window секунд на ключ (скользящее окно). Пустые ключи
    удаляются, словарь не растёт."""

    def __init__(self, max_count, window):
        self.max_count = max_count
        self.window = window
        self.times = {}

    def can(self, key):
        now = _clock()
        for k in list(self.times):
            fresh = [t for t in self.times[k] if now - t < self.window]
            if fresh:
                self.times[k] = fresh
            else:
                del self.times[k]
        return len(self.times.get(key, ())) < self.max_count and len(self.times) < LIMITER_MAX_KEYS

    def record(self, key):
        self.times.setdefault(key, []).append(_clock())


BALANCE_PAIR_INTERVAL = 20   # /balance в группе: раз в 20 секунд на пару (чат, игрок)
BALANCE_CHAT_PER_MINUTE = 10  # и не больше 10 ответов в минуту на чат

group_limiter = RateLimiter(GROUP_INTERVAL)
balance_pair_limiter = RateLimiter(BALANCE_PAIR_INTERVAL)
balance_chat_limiter = WindowLimiter(BALANCE_CHAT_PER_MINUTE, 60)
mydata_limiter = RateLimiter(MYDATA_INTERVAL)
delete_limiter = RateLimiter(DELETE_INTERVAL)
BACKUPNOW_INTERVAL = 600     # /backupnow: раз в 10 минут
backupnow_limiter = RateLimiter(BACKUPNOW_INTERVAL)


# ---------- настройки из окружения (необязательные) ----------

def _env(name):
    return (os.environ.get(name) or "").strip()


def game_link():
    value = _env("GAME_LINK")
    return value if len(value) <= 300 and re.fullmatch(r"https://t\.me/\S+", value) else None


def privacy_url():
    value = _env("PRIVACY_URL")
    return value if len(value) <= 300 and re.fullmatch(r"https://\S+", value) else None


def developer_contact():
    value = _env("DEVELOPER_CONTACT")
    ok = value and len(value) <= 200 and not any(ord(c) < 32 for c in value)
    return value if ok else None


PLAY_MODES = ("card", "link", "button")


def play_mode():
    """card (по умолчанию), link или button. Неизвестное значение считается card."""
    value = _env("PLAY_MODE").lower()
    return value if value in PLAY_MODES else "card"


def warn_missing_config():
    """Одно предупреждение при старте: каких необязательных настроек нет (только имена)."""
    missing = [name for name, getter in (("GAME_LINK", game_link), ("PRIVACY_URL", privacy_url),
                                         ("DEVELOPER_CONTACT", developer_contact)) if getter() is None]
    if missing:
        logger.warning("Не заданы или неверны переменные: %s. Соответствующие команды отвечают «%s»",
                       ", ".join(missing), UNAVAILABLE)
    if antiabuse.tombstone_secret() is None:
        logger.warning("Не задан TOMBSTONE_SECRET: удаление данных командой /deletemydata недоступно")
    if _env("PLAY_MODE") and _env("PLAY_MODE").lower() not in PLAY_MODES:
        logger.warning("Неизвестное значение PLAY_MODE, используется card")


# ---------- вспомогательное ----------

def _chat_type(update):
    chat = update.effective_chat
    return chat.type if chat is not None else None


def guarded(handler):
    """Исключение в обработчике не роняет сервис: в лог только тип ошибки и имя обработчика."""
    async def wrapper(update, context):
        try:
            await handler(update, context)
        except Exception as exc:
            logger.error("Ошибка в обработчике %s: %s", handler.__name__, type(exc).__name__)
    wrapper.__name__ = handler.__name__
    return wrapper


async def _reply(update, text, **kwargs):
    # без parse_mode: никакого Markdown/HTML
    await update.effective_message.reply_text(text, **kwargs)


def _thread_kwargs(message):
    """Из темы форума отвечаем в ту же тему."""
    if getattr(message, "is_topic_message", False) and getattr(message, "message_thread_id", None):
        return {"message_thread_id": message.message_thread_id}
    return {}


async def _group_send(update, context, text):
    """Ответ в группе без цитаты команды (send_message): не чаще одного на чат раз в 20 секунд,
    лишние вызовы игнорируются молча."""
    if not group_limiter.allow(update.effective_chat.id):
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, **_thread_kwargs(update.effective_message)
    )


async def _group_reply(update, text, **kwargs):
    """Ответ в группе: не чаще одного на чат раз в 20 секунд, лишние вызовы игнорируются молча."""
    if group_limiter.allow(update.effective_chat.id):
        await _reply(update, text, **kwargs)


# ---------- /start, /play ----------

async def _open_game_private(update):
    if not WEBAPP_URL:
        await _reply(update, UNAVAILABLE)
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Играть", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await _reply(update, "Нажми кнопку, чтобы открыть игру\nСписок команд: /help", reply_markup=keyboard)


async def _open_game_group(update, context):
    link = game_link()
    if link is None:
        await _group_reply(update, UNAVAILABLE)
        return
    if not group_limiter.allow(update.effective_chat.id):
        return
    mode = play_mode()
    message = update.effective_message
    kwargs = {"chat_id": update.effective_chat.id, "text": "🎰"}
    kwargs.update(_thread_kwargs(message))
    if mode == "link":
        # карточку строит Telegram из ссылки в тексте
        kwargs["text"] = '<a href="%s">🎰</a>' % html.escape(link)
        kwargs["parse_mode"] = "HTML"
    else:
        # карточка мини-приложения без ссылки в тексте: адрес задаётся параметром предпросмотра
        kwargs["link_preview_options"] = LinkPreviewOptions(url=link)
        if mode == "button":
            # обычная URL-кнопка (не web_app): в группе мини-апп открывается по прямой ссылке
            kwargs["reply_markup"] = InlineKeyboardMarkup([[InlineKeyboardButton("Играть", url=link)]])
    # send_message, а не reply_text: команда не цитируется
    await context.bot.send_message(**kwargs)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind == "private":
        await _open_game_private(update)
    elif kind in GROUP_TYPES:
        await _open_game_group(update, context)


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ---------- /balance ----------

def _balance_text(user_id):
    now = _wall()
    player = get_player(user_id, now=now)  # нового игрока создаёт, как в личном /balance
    # до следующего начисления: от last_accrual ровно час, минуты округляем вверх
    minutes = math.ceil((player["last_accrual"] + HOUR - now) / 60)
    return (
        f"Баланс: {player['balance']} фишек\n"
        f"До следующего начисления: {minutes} мин"
    )


def _balance_allowed_in_group(update):
    """Не отвечаем и ничего не создаём, если сообщение от имени чата (анонимный администратор,
    канал), пользователя нет или это бот. Затем лимиты: пара (чат, игрок) раз в 20 секунд
    и не больше 10 ответов в минуту на чат."""
    message = update.effective_message
    user = update.effective_user
    if getattr(message, "sender_chat", None) is not None:
        return False
    if user is None or getattr(user, "is_bot", False):
        return False
    chat_id = update.effective_chat.id
    pair = (chat_id, user.id)
    if not (balance_pair_limiter.can(pair) and balance_chat_limiter.can(chat_id)):
        return False
    balance_pair_limiter.record(pair)
    balance_chat_limiter.record(chat_id)
    return True


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        if _balance_allowed_in_group(update):
            # единственная групповая команда с цитатой: ответ идёт reply_text, без имени игрока
            await _reply(update, _balance_text(update.effective_user.id))
        return
    if kind != "private":
        return
    await _reply(update, _balance_text(update.effective_user.id))


# ---------- /help, /privacy, /developer_info ----------

PRIVATE_HELP = (
    "Команды:\n"
    "/start — открыть игру\n"
    "/play — открыть игру\n"
    "/balance — ваш баланс\n"
    "/privacy — политика конфиденциальности\n"
    "/developer_info — о разработчике\n"
    "/mydata — выгрузить мои данные\n"
    "/deletemydata — удалить мои данные"
)
GROUP_HELP = (
    "Команды:\n"
    "/play — открыть игру\n"
    "/help — список команд"
)


def _bot_username(context):
    try:
        return context.bot.username or None
    except Exception:
        return None  # имя бота ещё не известно


def _group_help_text(name):
    return (
        f"🎰 /play@{name} — открыть игру\n"
        f"💰 /balance@{name} — ваш баланс\n"
        f"ℹ️ /help@{name} — список команд\n"
        f"🔒 /privacy@{name} — политика конфиденциальности\n"
        f"👤 /developer_info@{name} — о разработчике\n"
        "Копия и удаление данных — в личной переписке с ботом."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind == "private":
        await _reply(update, PRIVATE_HELP)
    elif kind in GROUP_TYPES:
        name = _bot_username(context)
        await _group_send(update, context, _group_help_text(name) if name else GROUP_HELP)


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    url = privacy_url()
    if kind == "private":
        await _reply(update, url if url else UNAVAILABLE)
    elif kind in GROUP_TYPES:
        await _group_send(update, context, url if url else UNAVAILABLE)


async def developer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind not in CHAT_TYPES:
        return
    contact, url = developer_contact(), privacy_url()
    text = UNAVAILABLE
    if contact is not None and url is not None:
        text = f"Независимый разработчик. Контакт: {contact}. Политика конфиденциальности: {url}"
    if kind == "private":
        await _reply(update, text)
    else:
        await _group_send(update, context, text)


# ---------- права на данные: /mydata, /deletemydata ----------

def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def mydata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _reply(update, PRIVATE_ONLY)
        return
    if kind != "private":
        return
    user_id = update.effective_user.id
    if not mydata_limiter.allow(user_id):
        await _reply(update, "Слишком часто, повторите чуть позже")
        return
    export = await asyncio.to_thread(get_player_export, user_id, EXPORT_ROUNDS)
    if export is None:
        await _reply(update, "Данных о вас нет")
        return
    now = _wall()
    payload = {
        "generated_at": now,
        "generated_at_iso": _iso(now),
        "player": export["player"],
        "rounds": export["rounds"],
        "farm_purchases": export["farm_purchases"],
        "mines_games": export["mines_games"],
        "chats": {"count": len(export["chats"]), "items": export["chats"]},
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    # файл формируется в памяти, на диск ничего не пишется
    await update.effective_message.reply_document(document=io.BytesIO(data), filename="mydata.json")


async def backupnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца (нет в меню и в /help): отправить зашифрованную копию вне расписания.
    Все остальные (и любой чат, кроме личного) не получают ответа, в лог про них ничего не пишется."""
    if _chat_type(update) != "private":
        return
    user = update.effective_user
    owner_id = load_owner_id()
    if user is None or owner_id is None or user.id != owner_id:
        return
    sender = context.application.bot_data.get("backup_sender")
    if sender is None or not sender.enabled:
        await _reply(update, "Отключено: нужны BACKUP_PUBLIC_KEY и OWNER_CHAT_ID")
        return
    if not backupnow_limiter.allow(owner_id):
        await _reply(update, "Слишком часто, повторите через 10 минут")
        return
    await _reply(update, "Отправляю")
    result = await sender.send_now(context.bot, _wall())
    if result == "busy":
        await _reply(update, "Отправка уже идёт")
    elif result == "too_big":
        await _reply(update, "Не отправлено: копия больше лимита BACKUP_SEND_MAX_MB")
    elif result != "ok":
        await _reply(update, "Не удалось отправить, подробности в логах сервиса")


DELETE_WARNING = (
    "Будут удалены ваш баланс, уровни улучшений, история раундов и покупок, история игр в мины, незавершённая игра в мины (вместе со ставкой), а также участие в рейтингах. Это нельзя отменить. "
    "Данные на вашем устройстве (последние числа и ставки) останутся, их можно убрать очисткой "
    "кэша Telegram. Если вы снова откроете игру в ближайшие %d дней, стартовые 1000 фишек не выдаются: "
    "фишки будут начисляться по 100 в час. Для защиты от злоупотреблений на это время сохраняется "
    "обезличенный идентификатор, через %d дней он удаляется."
    % (antiabuse.REGISTRATION_COOLDOWN_DAYS, antiabuse.REGISTRATION_COOLDOWN_DAYS)
)
DELETE_UNAVAILABLE = "Автоматическое удаление сейчас недоступно."


def _delete_unavailable_text():
    contact = developer_contact()
    return DELETE_UNAVAILABLE + (f" Напишите разработчику: {contact}" if contact else "")
EXPIRED = "Время подтверждения истекло, отправьте /deletemydata снова"
NOTHING_TO_DELETE = "Данных для удаления нет"
CALLBACK_PATTERN = r"^del:(yes:\d{1,12}|no)$"


async def deletemydata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _reply(update, PRIVATE_ONLY)
        return
    if kind != "private":
        return
    if not delete_limiter.allow(update.effective_user.id):
        await _reply(update, "Слишком часто, повторите чуть позже")
        return
    if antiabuse.tombstone_secret() is None:
        # без секрета удалять нельзя (защиту от повторной регистрации потом не включить)
        await _reply(update, _delete_unavailable_text())
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Удалить всё", callback_data="del:yes:%d" % _wall()),
        InlineKeyboardButton("Отмена", callback_data="del:no"),
    ]])
    await _reply(update, DELETE_WARNING, reply_markup=keyboard)


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # всегда отвечаем на нажатие, иначе кнопка «крутится»
    message = query.message
    chat = getattr(message, "chat", None)
    if chat is None or chat.type != "private":
        return
    if query.data == "del:no":
        await query.edit_message_text("Отменено, данные не тронуты")
        return
    try:
        issued = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        return
    if _wall() - issued > CONFIRM_TTL:
        await query.edit_message_text(EXPIRED)
        return
    # id берём из данных Telegram (кто нажал), а не из callback_data
    try:
        counts = await asyncio.to_thread(delete_player_data, query.from_user.id)
    except Exception as exc:
        # нет TOMBSTONE_SECRET или сбой базы: ничего не удалено; в лог только тип ошибки
        logger.error("Удаление данных не выполнено: %s", type(exc).__name__)
        await query.edit_message_text(_delete_unavailable_text())
        return
    if sum(counts.values()) == 0:
        await query.edit_message_text(NOTHING_TO_DELETE)
        return
    # edit_message_text без reply_markup убирает кнопки
    await query.edit_message_text(
        "Готово: ваши данные удалены. Удалено записей: игрок — %d, раунды рулетки — %d, участие в рейтингах — %d, "
        "покупки улучшений — %d, игры в мины — %d"
        % (counts["players"], counts["roulette_rounds"], counts["chat_members"], counts["farm_purchases"],
           counts["mines_games"])
    )


# ---------- меню команд, ошибки, сборка приложения ----------

PRIVATE_COMMANDS = [
    BotCommand("start", "Открыть игру"),
    BotCommand("play", "Открыть игру"),
    BotCommand("balance", "Мой баланс"),
    BotCommand("help", "Список команд"),
    BotCommand("privacy", "Политика конфиденциальности"),
    BotCommand("developer_info", "О разработчике"),
    BotCommand("mydata", "Выгрузить мои данные"),
    BotCommand("deletemydata", "Удалить мои данные"),
]
GROUP_COMMANDS = [
    BotCommand("play", "Открыть игру"),
    BotCommand("balance", "Мой баланс"),
    BotCommand("help", "Список команд"),
    BotCommand("privacy", "Политика конфиденциальности"),
    BotCommand("developer_info", "О разработчике"),
]


async def register_commands(application):
    """Список команд для меню Telegram. Ошибка не роняет запуск: в лог только её тип."""
    try:
        await application.bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
    except Exception as exc:
        logger.error("Не удалось зарегистрировать команды: %s", type(exc).__name__)


async def on_error(update, context):
    # только тип исключения: без update, id, текста сообщений и данных игроков
    logger.error("Ошибка обработки обновления: %s", type(context.error).__name__)


def build_application(token, use_updater=True):
    """Создаёт приложение бота и регистрирует обработчики, но не запускает его.

    use_updater=False нужен для webhook внутри чужого веб-сервера: обновления
    приходят снаружи и кладутся в update_queue, собственный Updater не нужен.
    """
    builder = Application.builder().token(token)
    if not use_updater:
        builder = builder.updater(None)
    app = builder.build()
    for name, handler in (("start", start), ("play", play), ("balance", balance),
                          ("help", help_command), ("privacy", privacy),
                          ("developer_info", developer_info), ("mydata", mydata),
                          ("deletemydata", deletemydata), ("backupnow", backupnow)):
        app.add_handler(CommandHandler(name, guarded(handler)))
    app.add_handler(CallbackQueryHandler(guarded(delete_callback), pattern=CALLBACK_PATTERN))
    app.add_error_handler(on_error)
    warn_missing_config()
    return app


def main():
    if not TOKEN or not WEBAPP_URL:
        raise SystemExit("Не заданы BOT_TOKEN или WEBAPP_URL в файле .env")
    init_db()
    build_application(TOKEN).run_polling()


if __name__ == "__main__":
    main()
