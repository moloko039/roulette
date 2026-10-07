"""События групп: запоминание бесед и добавление бота в группу."""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

import db as db_module
from tg.common import GROUP_TYPES, logger
from tg import common


# ---------- группы, где состоит бот ----------
_noted_chats = {}            # chat_id -> время последней записи (не пишем в базу чаще раза в 6 часов)


NOTE_EVERY = 6 * 3600


async def note_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любая команда из группы: запоминаем числовой chat_id (без названия и участников)."""
    chat = update.effective_chat
    if chat is None or chat.type not in GROUP_TYPES:
        return
    now = common._wall()
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
        await asyncio.to_thread(db_module.chat_register, event.chat.id, common._wall())
        _noted_chats[event.chat.id] = common._wall()
    elif status in ("left", "kicked"):
        await asyncio.to_thread(db_module.chat_forget, event.chat.id)
        _noted_chats.pop(event.chat.id, None)
