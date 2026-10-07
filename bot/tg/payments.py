"""Оплата Stars косметики: pre_checkout, successful_payment, возвраты, /paysupport, /terms."""
import asyncio
import re

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import cosmetics
import db as db_module
from tg.common import GROUP_TYPES, PRIVATE_ONLY, UNAVAILABLE, _chat_type, _env, _notify_owner, _reply, _send_quiet, logger, privacy_url
from tg import common


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


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение заказа до списания (Telegram ждёт ответ 10 секунд): метка, цена, предмет и отсутствие его у игрока. Ничего долгого."""
    query = update.pre_checkout_query
    ok, message = True, None
    try:
        code = cosmetics.parse_payload(query.invoice_payload, query.from_user.id, now=common._wall())
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
    await _send_quiet(context, user_id, "Предмет добавлен во вкладку «Стиль»")


async def _refund_and_record_unknown(context, user_id, charge_id):
    try:
        await context.bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
        return True
    except TelegramError as exc:
        logger.error("Возврат Stars не выполнен: %s", type(exc).__name__)
        return False
