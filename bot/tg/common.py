"""Общее для обработчиков бота: настройки окружения, ограничители частоты, время, защита обработчиков, ответы."""
import logging
import os
import re
import time

from dotenv import load_dotenv

from notify import load_owner_id
import antiabuse


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


async def _send_quiet(context, chat_id, text):
    """Сообщение пользователю или владельцу; сбой доставки не роняет обработчик (в лог только тип ошибки)."""
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        logger.error("Не удалось отправить сообщение об оплате: %s", type(exc).__name__)


async def _notify_owner(context, text):
    owner_id = load_owner_id()
    if owner_id is not None:
        await _send_quiet(context, owner_id, text)


def _owner_private(update):
    """Команда владельца: только владелец и только личный чат; остальным молчание."""
    if _chat_type(update) != "private":
        return False
    user = update.effective_user
    owner_id = load_owner_id()
    return user is not None and owner_id is not None and user.id == owner_id
