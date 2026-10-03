import asyncio
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
                      InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from db import delete_player_data, get_player, get_player_export, init_db
from economy import HOUR

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

    def allow(self, key):
        now = _clock()
        for k in [k for k, t in self.last.items() if now - t >= self.interval]:
            del self.last[k]
        if key in self.last or len(self.last) >= LIMITER_MAX_KEYS:
            return False
        self.last[key] = now
        return True


group_limiter = RateLimiter(GROUP_INTERVAL)
mydata_limiter = RateLimiter(MYDATA_INTERVAL)
delete_limiter = RateLimiter(DELETE_INTERVAL)


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


def warn_missing_config():
    """Одно предупреждение при старте: каких необязательных настроек нет (только имена)."""
    missing = [name for name, getter in (("GAME_LINK", game_link), ("PRIVACY_URL", privacy_url),
                                         ("DEVELOPER_CONTACT", developer_contact)) if getter() is None]
    if missing:
        logger.warning("Не заданы или неверны переменные: %s. Соответствующие команды отвечают «%s»",
                       ", ".join(missing), UNAVAILABLE)


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


async def _open_game_group(update):
    link = game_link()
    if link is None:
        await _group_reply(update, UNAVAILABLE)
        return
    # обычная URL-кнопка (не web_app): в группе мини-апп открывается по прямой ссылке
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Играть", url=link)]])
    await _group_reply(update, "Играть: " + link, reply_markup=keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind == "private":
        await _open_game_private(update)
    elif kind in GROUP_TYPES:
        await _open_game_group(update)


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ---------- /balance ----------

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _group_reply(update, "Баланс смотрите в игре (вкладка «Профиль») или в личной переписке с ботом")
        return
    if kind != "private":
        return
    now = _wall()
    player = get_player(update.effective_user.id, now=now)
    # до следующего начисления: от last_accrual ровно час, минуты округляем вверх
    minutes = math.ceil((player["last_accrual"] + HOUR - now) / 60)
    await _reply(
        update,
        f"Баланс: {player['balance']} фишек\n"
        f"До следующего начисления: {minutes} мин"
    )


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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind == "private":
        await _reply(update, PRIVATE_HELP)
    elif kind in GROUP_TYPES:
        await _reply(update, GROUP_HELP)


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _chat_type(update) not in CHAT_TYPES:
        return
    url = privacy_url()
    await _reply(update, url if url else UNAVAILABLE)


async def developer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _chat_type(update) not in CHAT_TYPES:
        return
    contact, url = developer_contact(), privacy_url()
    if contact is None or url is None:
        await _reply(update, UNAVAILABLE)
        return
    await _reply(update, f"Независимый разработчик. Контакт: {contact}. Политика конфиденциальности: {url}")


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
        "chats": {"count": len(export["chats"]), "items": export["chats"]},
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    # файл формируется в памяти, на диск ничего не пишется
    await update.effective_message.reply_document(document=io.BytesIO(data), filename="mydata.json")


DELETE_WARNING = (
    "Будут удалены ваш баланс, история раундов и участие в рейтингах. Это нельзя отменить. "
    "Данные на вашем устройстве (последние числа и ставки) останутся, их можно убрать очисткой "
    "кэша Telegram. Если вы снова откроете игру, будет создан новый игрок с 1000 фишек."
)
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
    counts = await asyncio.to_thread(delete_player_data, query.from_user.id)
    if sum(counts.values()) == 0:
        await query.edit_message_text(NOTHING_TO_DELETE)
        return
    # edit_message_text без reply_markup убирает кнопки
    await query.edit_message_text(
        "Готово: ваши данные удалены. Удалено записей: игрок — %d, раунды рулетки — %d, участие в рейтингах — %d"
        % (counts["players"], counts["roulette_rounds"], counts["chat_members"])
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
    BotCommand("help", "Список команд"),
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
                          ("deletemydata", deletemydata)):
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
