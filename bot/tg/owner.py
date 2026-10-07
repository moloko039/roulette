"""Скрытые команды владельца: /backupnow, /refund, /regrant, /teststars, /give, /giveitem, /grantall и объявление о начислении."""
import asyncio
import re

from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import LabeledPrice
from telegram import Update
from telegram.error import BadRequest
from telegram.error import ChatMigrated
from telegram.error import Forbidden
from telegram.error import RetryAfter
from telegram.ext import ContextTypes

from notify import load_owner_id
import backup
import cosmetics
import db as db_module
from tg.common import _chat_type, _owner_private, _reply, _send_quiet, backupnow_limiter, game_link, logger
from tg.payments import CHARGE_ID_RE, _refund_and_record, _refund_gems_and_record
from tg import common


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
    result = await sender.send_now(context.bot, common._wall())
    if result == "busy":
        await _reply(update, "Отправка уже идёт")
    elif result == "too_big":
        await _reply(update, "Не отправлено: копия больше лимита BACKUP_SEND_MAX_MB")
    elif result != "ok":
        await _reply(update, "Не удалось отправить, подробности в логах сервиса")


async def refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда владельца: /refund <charge_id> [force] возвращает Stars, помечает платёж, убирает предмет у игрока или кристаллы пакета.
    Пакет кристаллов возвращается, только пока они не потрачены; потраченные возвращает только /refund <платёж> force (кристаллы списываются, сколько есть).
    Повтор безопасен."""
    if not _owner_private(update):
        return
    args = list(context.args or [])
    if len(args) not in (1, 2) or not CHARGE_ID_RE.fullmatch(args[0]) or (len(args) == 2 and args[1] != "force"):
        await _reply(update, "Формат: /refund <идентификатор платежа> [force]")
        return
    charge_id = args[0]
    gem_row = await asyncio.to_thread(db_module.gem_purchase_by_charge, charge_id)
    if gem_row is not None:
        if gem_row["status"] == "refunded":
            await _reply(update, "Этот платёж уже возвращён")
            return
        check = await asyncio.to_thread(db_module.refund_check, charge_id)
        if not check["ok"] and len(args) == 1:
            await _reply(update, "Кристаллы этого платежа уже потрачены (или не хватает на балансе). Правило: возвращаем неиспользованные. Принудительный возврат: /refund %s force" % charge_id)
            return
        if not await _refund_gems_and_record(context, gem_row["telegram_id"], charge_id):
            await _reply(update, "Возврат не выполнен (подробности в логах сервиса)")
            return
        await _send_quiet(context, gem_row["telegram_id"], "Платёж возвращён, кристаллы пакета списаны.")
        await _reply(update, "Возврат выполнен, кристаллы пакета списаны")
        return
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
    await _send_quiet(context, row["telegram_id"], "Платёж возвращён, предмет убран из раздела «Оформление».")
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
        await _send_quiet(context, user_id, "Предмет добавлен в раздел «Оформление»")
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
            payload=cosmetics.make_payload(owner_id, item["code"], common._wall()), currency="XTR", prices=[LabeledPrice(item["name"], item["price"]["amount"])],
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
    pending["grant_pending"] = {"id": grant_id, "amount": amount, "at": common._wall(), "silent": silent}
    chats = len(await asyncio.to_thread(db_module.chat_ids))
    note = "объявление не отправляется (silent)" if silent else "объявление уйдёт в групп: %d" % chats
    await _reply(update, "Получат фишки: игроков %d, всего будет выдано %d; %s. Копия базы создаётся автоматически перед "
                 "начислением (можно сделать /backupnow заранее). Подтвердите командой "
                 "/grantall confirm %s в течение 5 минут" % (count, total, note, grant_id))


async def _grant_confirm(update, context, grant_id):
    store = context.application.bot_data
    pending = store.pop("grant_pending", None)   # подтверждение одноразовое
    if pending is None or common._wall() - pending["at"] > GRANT_TTL:
        await _reply(update, "Нет начисления, ожидающего подтверждения, или время вышло. Отправьте /grantall <сумма> <id> снова")
        return
    if pending["id"] != grant_id:
        await _reply(update, "Неверный id: подтверждение сброшено, отправьте /grantall <сумма> <id> снова")
        return
    db_path = db_module._resolve_path(None)
    config = backup.load_config(None, db_path)
    # сначала копия базы (существующая функция: согласованный снимок с проверкой), без неё начисления нет
    snapshot = await asyncio.to_thread(backup.create_snapshot, db_path, config["dir"], common._wall(), config["keep"])
    if snapshot is None:
        await _reply(update, "Резервная копия не создана, начисление не выполнено")
        return
    try:
        players, given = await asyncio.to_thread(db_module.grant_all, pending["amount"], pending["id"], common._wall())
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
