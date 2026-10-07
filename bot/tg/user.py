"""Команды пользователя: /start, /play, /balance, /help, /privacy, /developer_info."""
import html

from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import LinkPreviewOptions
from telegram import Update
from telegram import WebAppInfo
from telegram.ext import ContextTypes

from db import get_player
from economy import next_tick_in
from tg.common import CHAT_TYPES, GROUP_TYPES, UNAVAILABLE, _chat_type, _group_reply, _group_send, _reply, _thread_kwargs, balance_chat_limiter, balance_pair_limiter, developer_contact, game_link, group_limiter, play_mode, privacy_url
from tg import common


# ---------- /start, /play ----------

async def _open_game_private(update):
    if not common.WEBAPP_URL:
        await _reply(update, UNAVAILABLE)
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Играть", web_app=WebAppInfo(url=common.WEBAPP_URL))]]
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
    now = common._wall()
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
