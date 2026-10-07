"""Права на данные: /mydata, /deletemydata и подтверждение удаления."""
from datetime import datetime
from datetime import timezone
import asyncio
import io
import json

from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.ext import ContextTypes

from db import delete_player_data
from db import get_player_export
import antiabuse
import cosmetics
from tg.common import CONFIRM_TTL, EXPORT_ROUNDS, GROUP_TYPES, PRIVATE_ONLY, _chat_type, _reply, delete_limiter, developer_contact, logger, mydata_limiter
from tg import common


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
    now = common._wall()
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
        InlineKeyboardButton("Удалить всё", callback_data="del:yes:%d" % common._wall()),
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
    if common._wall() - issued > CONFIRM_TTL:
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
