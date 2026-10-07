"""Сборка Application бота: список команд, обработчики, ошибки, запуск."""
from telegram import BotCommand
from telegram import BotCommandScopeAllGroupChats
from telegram import BotCommandScopeAllPrivateChats
from telegram.ext import Application
from telegram.ext import CallbackQueryHandler
from telegram.ext import ChatMemberHandler
from telegram.ext import CommandHandler
from telegram.ext import MessageHandler
from telegram.ext import PreCheckoutQueryHandler
from telegram.ext import filters

from db import init_db
from tg.common import guarded, logger, warn_missing_config
from tg.data import CALLBACK_PATTERN, delete_callback, deletemydata, mydata
from tg.groups import my_chat_member, note_group
from tg.owner import backupnow, give, givegems, giveitem, grantall, refund, regrant, stats, teststars
from tg.payments import paysupport, pre_checkout, successful_payment, terms
from tg.user import balance, developer_info, help_command, play, privacy, start
from tg import common


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
    BotCommand("terms", "Условия покупок"),
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
                          ("grantall", grantall), ("give", give), ("givegems", givegems), ("stats", stats), ("giveitem", giveitem), ("paysupport", paysupport), ("terms", terms),
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
    if not common.TOKEN or not common.WEBAPP_URL:
        raise SystemExit("Не заданы BOT_TOKEN или common.WEBAPP_URL в файле .env")
    init_db()
    build_application(common.TOKEN).run_polling()
