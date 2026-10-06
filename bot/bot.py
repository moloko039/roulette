import asyncio
import html
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                      InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update, WebAppInfo)
from telegram import LabeledPrice
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes,
                          MessageHandler, PreCheckoutQueryHandler, filters)

import antiabuse
import backup
import cosmetics
import db as db_module
from db import delete_player_data, get_player, get_player_export, init_db
from economy import next_tick_in
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
    # доход начисляется поминутно: до следующей минутной границы (1..60 с), как seconds_to_next в /api/me
    seconds = next_tick_in(now)
    return (
        f"Баланс: {player['balance']} фишек\n"
        f"До следующего начисления: {seconds} сек."
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
    "/deletemydata — удалить мои данные\n"
    "/paysupport — помощь по оплате\n"
    "/terms — условия покупки предметов"
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
        "keno_rounds": export["keno_rounds"],
        "slot_rounds": export["slot_rounds"],
        "blackjack_games": export["blackjack_games"],
        "blackjack_active": export["blackjack_active"],
        "crash_games": export["crash_games"],
        "crash_active": export["crash_active"],
        "hilo_games": export["hilo_games"],
        "hilo_active": export["hilo_active"],
        "transfers": export["transfers"],
        "cosmetics": export["cosmetics"],
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


# ---------- оплата косметики Telegram Stars ----------
# Официальная документация: https://core.telegram.org/bots/payments-stars (валюта XTR, provider_token пустой, один LabeledPrice;
# pre_checkout_query нужно подтвердить за 10 секунд; в successful_payment есть telegram_payment_charge_id; возврат refundStarPayment).
PAY_RETRIES = 3          # попыток записать оплату, потом уведомление владельцу и ручная выдача (/regrant)
PAY_RETRY_DELAY = 0.5
CHARGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,200}$")


def pay_support_contact():
    value = _env("PAY_SUPPORT_CONTACT")
    ok = value and len(value) <= 200 and not any(ord(c) < 32 for c in value)
    return value if ok else None


def terms_url():
    """TERMS_URL или адрес рядом с политикой (privacy.html -> terms.html), иначе None."""
    value = _env("TERMS_URL")
    if value and len(value) <= 300 and re.fullmatch(r"https://\S+", value):
        return value
    url = privacy_url()
    return url[:-len("privacy.html")] + "terms.html" if url and url.endswith("privacy.html") else None


async def paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _reply(update, PRIVATE_ONLY)
        return
    if kind != "private":
        return
    contact, url = pay_support_contact(), terms_url()
    text = ("Помощь по оплате. Покупки в приложении: косметические предметы (внешний вид, на игру не влияют) за фишки или Telegram Stars. "
            "Если оплата прошла, а предмета нет, или нужен возврат за Stars, напишите владельцу бота")
    text += (": " + contact) if contact else " через это сообщение (контакт для связи пока не указан)"
    text += ". Условия: " + url if url else ". Условия покупки: /terms"
    await _reply(update, text)


async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _reply(update, PRIVATE_ONLY)
        return
    if kind != "private":
        return
    url = terms_url()
    await _reply(update, ("Условия покупки предметов: " + url) if url else UNAVAILABLE)


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


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение заказа до списания (Telegram ждёт ответ 10 секунд): метка, цена, предмет и отсутствие его у игрока. Ничего долгого."""
    query = update.pre_checkout_query
    ok, message = True, None
    try:
        code = cosmetics.parse_payload(query.invoice_payload, query.from_user.id, now=_wall())
        if code is None or query.currency != "XTR":
            raise cosmetics.UnknownItem()
        item = await asyncio.to_thread(db_module.stars_offer, query.from_user.id, code)
        if item["price"]["amount"] != query.total_amount:
            raise cosmetics.ItemUnavailable()
    except cosmetics.AlreadyOwned:
        ok, message = False, "Этот предмет у вас уже есть"
    except (cosmetics.CosmeticsError, ValueError):
        ok, message = False, "Предмет сейчас недоступен или цена изменилась. Откройте магазин и попробуйте снова"
    except Exception as exc:
        logger.error("Проверка заказа не выполнена: %s", type(exc).__name__)
        ok, message = False, "Не удалось проверить заказ, попробуйте позже"
    await query.answer(ok=ok, error_message=message)


async def _refund_and_record(context, user_id, charge_id):
    """Возврат Stars и отметка в журнале. True, если возврат выполнен (или Telegram сообщил, что он уже был)."""
    try:
        await context.bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except TelegramError as exc:
        if "ALREADY_REFUNDED" not in str(exc).upper():
            logger.error("Возврат Stars не выполнен: %s", type(exc).__name__)
            return False
    await asyncio.to_thread(db_module.mark_refunded, charge_id)
    return True


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Оплата прошла: одна транзакция (журнал с уникальным charge_id и выдача предмета); повтор апдейта дублей не создаёт. Предмет уже есть:
    автоматический возврат. Сбой записи: до PAY_RETRIES повторов, затем сообщение владельцу и ручная выдача (/regrant)."""
    payment = update.effective_message.successful_payment
    user_id = update.effective_user.id
    charge_id, amount = payment.telegram_payment_charge_id, payment.total_amount
    code = cosmetics.parse_payload(payment.invoice_payload, user_id, ttl=None) if payment.currency == "XTR" else None
    if code is None:      # метка не распознана (например, сменился ключ при перезапуске): записать нечего, деньги возвращаются
        refunded = await _refund_and_record_unknown(context, user_id, charge_id)
        await _send_quiet(context, user_id, "Не удалось распознать заказ, оплата возвращена." if refunded else
                          "Не удалось распознать заказ. Напишите в поддержку: /paysupport")
        if not refunded:
            await _notify_owner(context, "Оплата без распознанного заказа, возврат не удался. Платёж: %s, игрок: %d" % (charge_id, user_id))
        return
    result = None
    for attempt in range(PAY_RETRIES):
        try:
            result = await asyncio.to_thread(db_module.record_stars_payment, user_id, charge_id, code, amount)
            break
        except Exception as exc:
            logger.error("Запись оплаты не удалась (попытка %d): %s", attempt + 1, type(exc).__name__)
            if attempt + 1 < PAY_RETRIES:
                await asyncio.sleep(PAY_RETRY_DELAY)
    if result is None:
        context.application.bot_data.setdefault("failed_payments", {})[charge_id] = {"user": user_id, "code": code, "amount": amount}
        await _send_quiet(context, user_id, "Оплата получена, но предмет не удалось выдать сразу. Владелец выдаст его вручную; если предмета нет, напишите: /paysupport")
        await _notify_owner(context, "Не удалось записать оплату. Платёж: %s, игрок: %d, предмет: %s, сумма: %d. Выдать: /regrant %s (или /regrant %s %d %s %d)"
                            % (charge_id, user_id, code, amount, charge_id, charge_id, user_id, code, amount))
        return
    if result["result"] == "duplicate":
        return                # повторная доставка того же платежа: ничего не делаем
    if result["result"] == "already_owned":
        if await _refund_and_record(context, user_id, charge_id):
            await _send_quiet(context, user_id, "Этот предмет у вас уже был, оплата возвращена.")
        else:
            await _send_quiet(context, user_id, "Этот предмет у вас уже был. Возврат оформит владелец, подробности: /paysupport")
            await _notify_owner(context, "Автоматический возврат не удался (предмет уже был). Платёж: %s. Вернуть: /refund %s" % (charge_id, charge_id))
        return
    await _send_quiet(context, user_id, "Предмет добавлен в гардероб")


async def _refund_and_record_unknown(context, user_id, charge_id):
    try:
        await context.bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
        return True
    except TelegramError as exc:
        logger.error("Возврат Stars не выполнен: %s", type(exc).__name__)
        return False


def _owner_private(update):
    """Команда владельца: только владелец и только личный чат; остальным молчание."""
    if _chat_type(update) != "private":
        return False
    user = update.effective_user
    owner_id = load_owner_id()
    return user is not None and owner_id is not None and user.id == owner_id


async def refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца: /refund <charge_id> возвращает Stars, помечает платёж, убирает предмет у игрока. Повтор безопасен."""
    if not _owner_private(update):
        return
    args = list(context.args or [])
    if len(args) != 1 or not CHARGE_ID_RE.fullmatch(args[0]):
        await _reply(update, "Формат: /refund <идентификатор платежа>")
        return
    charge_id = args[0]
    row = await asyncio.to_thread(db_module.purchase_by_charge, charge_id)
    if row is None:
        await _reply(update, "Платёж не найден в журнале")
        return
    if row["status"] == "refunded":
        await _reply(update, "Этот платёж уже возвращён")
        return
    if not await _refund_and_record(context, row["telegram_id"], charge_id):
        await _reply(update, "Возврат не выполнен (подробности в логах сервиса)")
        return
    await _send_quiet(context, row["telegram_id"], "Платёж возвращён, предмет убран из гардероба.")
    await _reply(update, "Возврат выполнен, предмет убран у игрока")


async def regrant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца: /regrant <charge_id> выдаёт оплаченный предмет, если запись есть, а предмета нет. Если записи нет из-за
    сбоя, уведомление владельцу содержит нужные данные: /regrant <charge_id> <id игрока> <код предмета> <сумма>."""
    if not _owner_private(update):
        return
    args = list(context.args or [])
    if len(args) not in (1, 4) or not CHARGE_ID_RE.fullmatch(args[0]) or (len(args) == 4 and not (re.fullmatch(r"\d{1,15}", args[1]) and re.fullmatch(r"\d{1,6}", args[3]))):
        await _reply(update, "Формат: /regrant <платёж> или /regrant <платёж> <id игрока> <код предмета> <сумма>")
        return
    charge_id = args[0]
    try:
        if len(args) == 4:
            res = (await asyncio.to_thread(db_module.record_stars_payment, int(args[1]), charge_id, args[2], int(args[3])))["result"]
            res = {"granted": "granted", "duplicate": "already_has", "already_owned": "already_has"}[res]
            user_id = int(args[1])
        else:
            res = await asyncio.to_thread(db_module.regrant_purchase, charge_id)
            row = await asyncio.to_thread(db_module.purchase_by_charge, charge_id)
            user_id = row["telegram_id"] if row else None
    except ValueError:
        await _reply(update, "Неверные данные платежа или предмета")
        return
    except Exception as exc:
        logger.error("Ручная выдача не выполнена: %s", type(exc).__name__)
        await _reply(update, "Не удалось выдать (подробности в логах сервиса)")
        return
    text = {"granted": "Выдано", "already_has": "Предмет у игрока уже есть", "refunded": "Платёж уже возвращён, выдавать нечего",
            "missing": "Платёж не найден в журнале (используйте форму с идентификатором игрока, кодом и суммой)"}[res]
    if res == "granted" and user_id is not None:
        await _send_quiet(context, user_id, "Предмет добавлен в гардероб")
    await _reply(update, text)


async def teststars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца: инвойс скрытого тестового предмета за 1 Star для проверки всего пути оплаты и возврата (/refund)."""
    if not _owner_private(update):
        return
    owner_id = update.effective_user.id
    item = cosmetics.TEST_ITEM
    try:
        await context.bot.send_invoice(
            chat_id=owner_id, title=item["name"], description="Проверка оплаты Telegram Stars (1 Star). Возврат: /refund",
            payload=cosmetics.make_payload(owner_id, item["code"], _wall()), currency="XTR", prices=[LabeledPrice(item["name"], item["price"]["amount"])],
            provider_token="")
    except Exception as exc:
        logger.error("Тестовый инвойс не отправлен: %s", type(exc).__name__)
        await _reply(update, "Не удалось создать счёт (подробности в логах сервиса)")


GRANT_TTL = 300   # подтверждение начисления действует 5 минут


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца (нет в меню и в /help): /give <сумма> начисляет фишки ТОЛЬКО самому владельцу.
    Все остальные (и любой чат, кроме личного) не получают ответа, в лог про них ничего не пишется."""
    if _chat_type(update) != "private":
        return
    user = update.effective_user
    owner_id = load_owner_id()
    if user is None or owner_id is None or user.id != owner_id:
        return
    args = list(context.args or [])
    if len(args) != 1 or not re.fullmatch(r"\d{1,9}", args[0]) or not 1 <= int(args[0]) <= db_module.GIVE_MAX_AMOUNT:
        await _reply(update, "Формат: /give <сумма>, целое от 1 до 100000000")
        return
    amount = int(args[0])
    try:
        given, balance_now = await asyncio.to_thread(db_module.give_owner, user.id, amount)   # начисляется только user.id
    except db_module.PlayerMissing:
        await _reply(update, "Вас ещё нет в базе: откройте игру один раз и повторите команду")
        return
    except Exception as exc:
        logger.error("Начисление владельцу не выполнено: %s", type(exc).__name__)
        await _reply(update, "Не удалось выполнить начисление (подробности в логах сервиса)")
        return
    if given == amount:
        await _reply(update, "Начислено %d. Баланс: %d" % (given, balance_now))
    else:
        await _reply(update, "Баланс у потолка: начислено %d из %d. Баланс: %d" % (given, amount, balance_now))


async def giveitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца (нет в меню и в /help): /giveitem <код> [telegram_id] выдаёт косметический предмет из каталога
    себе или игроку (source=owner_gift, повтор: «уже есть»). Все остальные (и любой чат, кроме личного) не получают ответа;
    в лог не попадают идентификаторы, имена и коды предметов."""
    if _chat_type(update) != "private":
        return
    user = update.effective_user
    owner_id = load_owner_id()
    if user is None or owner_id is None or user.id != owner_id:
        return
    args = list(context.args or [])
    if not 1 <= len(args) <= 2 or (len(args) == 2 and not re.fullmatch(r"\d{1,15}", args[1])):
        await _reply(update, "Формат: /giveitem <код предмета> [id игрока]. Без id предмет выдаётся вам")
        return
    item = cosmetics.item(args[0])
    if item is None:
        await _reply(update, "Такого предмета нет в каталоге")
        return
    if item["starter"]:
        await _reply(update, "Стартовые предметы есть у всех, выдавать их не нужно")
        return
    target = int(args[1]) if len(args) == 2 else user.id
    try:
        added = await asyncio.to_thread(db_module.grant_item, target, item["code"], "owner_gift")
    except cosmetics.NoSuchPlayer:
        await _reply(update, "Игрока нет в базе: он должен хотя бы раз открыть игру")
        return
    except Exception as exc:
        logger.error("Выдача предмета не выполнена: %s", type(exc).__name__)
        await _reply(update, "Не удалось выдать предмет (подробности в логах сервиса)")
        return
    if added:
        logger.info("Предмет выдан")
        await _reply(update, "Выдано: %s" % item["name"])
    else:
        await _reply(update, "Уже есть: %s" % item["name"])


async def grantall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца (нет в меню и в /help): разовое начисление фишек всем игрокам.
    /grantall <сумма> <id> показывает, сколько игроков и фишек, /grantall confirm <id> выполняет (в течение 5 минут;
    сначала копия базы, потом одна транзакция). Все остальные (и любой чат, кроме личного) не получают ответа,
    в лог про них ничего не пишется."""
    if _chat_type(update) != "private":
        return
    user = update.effective_user
    owner_id = load_owner_id()
    if user is None or owner_id is None or user.id != owner_id:
        return
    args = list(context.args or [])
    pending = context.application.bot_data
    if len(args) == 2 and args[0] == "confirm":
        await _grant_confirm(update, context, args[1])
        return
    silent = len(args) == 3 and args[2] == "silent"
    if silent:
        args = args[:2]
    if len(args) != 2 or not re.fullmatch(r"\d{1,7}", args[0]):
        await _reply(update, "Формат: /grantall <сумма> <id> [silent], например /grantall 10000 oct4 (silent: без объявления в беседах)")
        return
    amount, grant_id = int(args[0]), args[1]
    try:
        db_module.validate_grant(amount, grant_id)
        count, total = await asyncio.to_thread(db_module.grant_preview, amount, grant_id)
    except ValueError:
        await _reply(update, "Сумма: целое от 1 до 1000000; id: латиница, цифры и дефис, до 32 символов")
        return
    except db_module.GrantExists:
        await _reply(update, "Начисление с таким id уже было, ничего не изменено")
        return
    if count == 0:
        await _reply(update, "Некому начислять: в базе нет игроков с местом под потолок баланса")
        return
    pending["grant_pending"] = {"id": grant_id, "amount": amount, "at": _wall(), "silent": silent}
    chats = len(await asyncio.to_thread(db_module.chat_ids))
    note = "объявление не отправляется (silent)" if silent else "объявление уйдёт в групп: %d" % chats
    await _reply(update, "Получат фишки: игроков %d, всего будет выдано %d; %s. Копия базы создаётся автоматически перед "
                 "начислением (можно сделать /backupnow заранее). Подтвердите командой "
                 "/grantall confirm %s в течение 5 минут" % (count, total, note, grant_id))


async def _grant_confirm(update, context, grant_id):
    store = context.application.bot_data
    pending = store.pop("grant_pending", None)   # подтверждение одноразовое
    if pending is None or _wall() - pending["at"] > GRANT_TTL:
        await _reply(update, "Нет начисления, ожидающего подтверждения, или время вышло. Отправьте /grantall <сумма> <id> снова")
        return
    if pending["id"] != grant_id:
        await _reply(update, "Неверный id: подтверждение сброшено, отправьте /grantall <сумма> <id> снова")
        return
    db_path = db_module._resolve_path(None)
    config = backup.load_config(None, db_path)
    # сначала копия базы (существующая функция: согласованный снимок с проверкой), без неё начисления нет
    snapshot = await asyncio.to_thread(backup.create_snapshot, db_path, config["dir"], _wall(), config["keep"])
    if snapshot is None:
        await _reply(update, "Резервная копия не создана, начисление не выполнено")
        return
    try:
        players, given = await asyncio.to_thread(db_module.grant_all, pending["amount"], pending["id"], _wall())
    except db_module.GrantExists:
        await _reply(update, "Начисление с таким id уже было, ничего не изменено")
        return
    except Exception as exc:
        logger.error("Начисление не выполнено: %s", type(exc).__name__)
        await _reply(update, "Не удалось выполнить начисление, ничего не изменено (подробности в логах сервиса)")
        return
    await _reply(update, "Начисление выполнено: получили игроков %d, выдано всего %d" % (players, given))
    if pending.get("silent") or players == 0:
        return
    # объявление только после коммита и в фоне; ссылку на задачу держим, чтобы её не убрала сборка мусора
    task = asyncio.ensure_future(_announce_grant(context.bot, update.effective_chat.id, pending["amount"]))
    store["grant_announce_task"] = task


ANNOUNCE_PAUSE = 0.1   # пауза между сообщениями в разные группы (лимит Telegram около 20 в секунду)


def _grant_announcement(amount):
    return "🎁 Всем игрокам Necasino начислено %s фишек! Заходите играть" % "{:,}".format(amount).replace(",", " ")


async def _send_announcement(bot, chat_id, text, markup):
    """Одна отправка; RetryAfter: ждём и повторяем один раз. True, если доставлено."""
    for attempt in (1, 2):
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
            return True
        except RetryAfter as exc:
            if attempt == 2:
                return False
            await asyncio.sleep(min(float(getattr(exc, "retry_after", 1) or 1), 30))
        except ChatMigrated:
            await asyncio.to_thread(db_module.chat_forget, chat_id)   # новый id появится при ближайшем событии из группы
            return False
        except (Forbidden, BadRequest):
            await asyncio.to_thread(db_module.chat_forget, chat_id)   # бота убрали из группы или группы нет
            return False
        except Exception:
            return False
    return False


async def _announce_grant(bot, owner_chat_id, amount):
    """Фоновая задача: одно объявление в каждую известную группу, ошибки не прерывают остальные и не откатывают начисление.
    Владельцу в конце итог. В лог только числа."""
    sent = failed = 0
    try:
        chats = await asyncio.to_thread(db_module.chat_ids)
        link = game_link()
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("Играть", url=link)]]) if link else None
        text = _grant_announcement(amount)
        for i, chat_id in enumerate(chats):
            if i:
                await asyncio.sleep(ANNOUNCE_PAUSE)
            if await _send_announcement(bot, chat_id, text, markup):
                sent += 1
            else:
                failed += 1
        logger.info("Объявление о начислении: отправлено=%d, не доставлено=%d", sent, failed)
        if not chats:
            summary = "Объявление не отправлено: бот не знает ни одной группы"
        else:
            summary = "Объявление: групп %d, отправлено %d, не доставлено %d" % (len(chats), sent, failed)
        await bot.send_message(chat_id=owner_chat_id, text=summary)
    except Exception as exc:
        logger.error("Объявление о начислении прервано: %s", type(exc).__name__)


# ---------- группы, где состоит бот ----------
_noted_chats = {}            # chat_id -> время последней записи (не пишем в базу чаще раза в 6 часов)
NOTE_EVERY = 6 * 3600


async def note_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любая команда из группы: запоминаем числовой chat_id (без названия и участников)."""
    chat = update.effective_chat
    if chat is None or chat.type not in GROUP_TYPES:
        return
    now = _wall()
    if now - _noted_chats.get(chat.id, 0) < NOTE_EVERY:
        return
    try:
        await asyncio.to_thread(db_module.chat_register, chat.id, now)
        _noted_chats[chat.id] = now
    except Exception as exc:
        logger.error("Не удалось запомнить группу: %s", type(exc).__name__)


async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бота добавили в группу или убрали из неё: запись добавляется или удаляется."""
    event = update.my_chat_member
    if event is None or event.chat is None or event.chat.type not in GROUP_TYPES:
        return
    status = event.new_chat_member.status
    if status in ("member", "administrator"):
        await asyncio.to_thread(db_module.chat_register, event.chat.id, _wall())
        _noted_chats[event.chat.id] = _wall()
    elif status in ("left", "kicked"):
        await asyncio.to_thread(db_module.chat_forget, event.chat.id)
        _noted_chats.pop(event.chat.id, None)


DELETE_WARNING = (
    "Будут удалены ваш баланс, уровни улучшений, история раундов и покупок, история игр в мины, история раундов кено, история раздач блэкджека, история раундов краша, история отправленных вами переводов (записи о полученных вами переводах не удаляются, а обезличиваются: ваш идентификатор заменяется, они остаются у отправителей до конца срока хранения), незавершённая игра в мины (вместе со ставкой), незавершённая раздача блэкджека (вместе со ставкой), незавершённый раунд краша (вместе со ставкой), а также участие в рейтингах. Это нельзя отменить. "
    "Данные на вашем устройстве (последние числа и ставки) останутся, их можно убрать очисткой "
    "кэша Telegram. Если вы снова откроете игру в ближайшие %d дней, стартовые 1000 фишек не выдаются: "
    "фишки будут начисляться по 100 в час. Для защиты от злоупотреблений на это время сохраняется "
    "обезличенный идентификатор, через %d дней он удаляется. "
    "Купленные предметы оформления удаляются без возмещения. Запись об оплатах Telegram Stars (предмет, сумма, дата) "
    "сохраняется для споров и возвратов %d дней после покупки, потом удаляется автоматически."
    % (antiabuse.REGISTRATION_COOLDOWN_DAYS, antiabuse.REGISTRATION_COOLDOWN_DAYS, cosmetics.PURCHASE_RETENTION_DAYS)
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
        "покупки улучшений — %d, игры в мины — %d, раунды кено — %d, раунды слота — %d, раздачи блэкджека — %d, раунды краша — %d, партии хило — %d, отправленные переводы — %d. "
        "Записи о полученных вами переводах не удалены, а обезличены (ваш идентификатор заменён), они остаются у отправителей до конца срока хранения: %d"
        % (counts["players"], counts["roulette_rounds"], counts["chat_members"], counts["farm_purchases"],
           counts["mines_games"], counts["keno_rounds"], counts["slot_rounds"], counts["blackjack_games"], counts["crash_games"], counts["hilo_games"], counts["transfers"], counts["transfers_anonymized"])
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
    BotCommand("paysupport", "Помощь по оплате"),
    BotCommand("terms", "Условия покупки предметов"),
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
                          ("deletemydata", deletemydata), ("backupnow", backupnow),
                          ("grantall", grantall), ("give", give), ("giveitem", giveitem), ("paysupport", paysupport), ("terms", terms),
                          ("refund", refund), ("regrant", regrant), ("teststars", teststars)):
        app.add_handler(CommandHandler(name, guarded(handler)))
    app.add_handler(CallbackQueryHandler(guarded(delete_callback), pattern=CALLBACK_PATTERN))
    app.add_handler(PreCheckoutQueryHandler(guarded(pre_checkout)))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, guarded(successful_payment)))
    app.add_handler(ChatMemberHandler(guarded(my_chat_member), ChatMemberHandler.MY_CHAT_MEMBER))
    # в группе -1: срабатывает до остальных и не мешает им (block=False)
    app.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.GROUPS, guarded(note_group), block=False), group=-1)
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
