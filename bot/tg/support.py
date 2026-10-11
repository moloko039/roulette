"""Поддержка: /support пересылает сообщение игрока владельцу без раскрытия личности владельца; ответ владельца уходит игроку.
Сообщения нигде не сохраняются: связь держится на заголовке «Поддержка #<id>» в пересланном сообщении (владелец отвечает на него)."""
import re

from telegram import Update
from telegram.ext import ContextTypes

from notify import load_owner_id
from tg.common import GROUP_TYPES, PRIVATE_ONLY, UNAVAILABLE, RateLimiter, _chat_type, _reply, logger
from tg import common


SUPPORT_MAX_CHARS = 1000

SUPPORT_INTERVAL = 30    # не чаще одного обращения в 30 секунд на игрока

SUPPORT_HEADER = re.compile(r"^✉️ Поддержка #(\d{1,20})\n")

support_limiter = RateLimiter(SUPPORT_INTERVAL)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/support [текст]: с текстом отправляет сразу, без текста ждёт следующее сообщение."""
    kind = _chat_type(update)
    if kind in GROUP_TYPES:
        await _reply(update, PRIVATE_ONLY)
        return
    if kind != "private" or update.effective_user is None:
        return
    if load_owner_id() is None:
        await _reply(update, UNAVAILABLE)
        return
    text = " ".join(context.args or []).strip()
    if text:
        await _deliver(update, context, text)
        return
    context.user_data["support"] = True
    await _reply(update, "Напишите одним сообщением, что случилось. Я передам его поддержке, ответ придёт сюда. Только текст, до %d символов" % SUPPORT_MAX_CHARS)


async def _deliver(update, context, text):
    user = update.effective_user
    owner_id = load_owner_id()
    context.user_data.pop("support", None)
    if len(text) > SUPPORT_MAX_CHARS:
        await _reply(update, "Слишком длинно: до %d символов. Сократите и отправьте снова: /support" % SUPPORT_MAX_CHARS)
        return
    if not support_limiter.allow(user.id):
        await _reply(update, "Слишком часто, повторите через полминуты")
        return
    try:
        await context.bot.send_message(chat_id=owner_id, text="✉️ Поддержка #%d\n%s" % (user.id, text))
    except Exception as exc:
        logger.error("Не удалось передать обращение в поддержку: %s", type(exc).__name__)
        await _reply(update, "Не получилось передать сообщение, попробуйте позже")
        return
    await _reply(update, "Передано. Ответ придёт в этот чат")


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обычный текст в личке: ответ владельца игроку (ответом на заголовок «Поддержка #id») или обращение после /support."""
    if _chat_type(update) != "private" or update.effective_user is None:
        return
    message = update.effective_message
    text = (message.text or "").strip() if message is not None else ""
    if not text:
        return
    owner_id = load_owner_id()
    quoted = getattr(message, "reply_to_message", None)
    if owner_id is not None and update.effective_user.id == owner_id and quoted is not None:
        found = SUPPORT_HEADER.match(quoted.text or "")
        if found is None:
            return
        try:
            await context.bot.send_message(chat_id=int(found.group(1)), text="Ответ поддержки:\n" + text[:3000])
            await _reply(update, "Отправлено")
        except Exception as exc:
            logger.error("Не удалось отправить ответ поддержки: %s", type(exc).__name__)
            await _reply(update, "Не доставлено: игрок заблокировал бота или чат недоступен")
        return
    if context.user_data.get("support") and owner_id is not None:
        await _deliver(update, context, text)
