"""Запуск и остановка: бот (webhook, polling или только API) и фоновая задача копий и очистки."""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from telegram import Update

import backup
import backup_send
import notify
from web.http import WEBHOOK_PATH

logger = logging.getLogger("depnaya")


async def _register_commands(bot_app):
    from bot import register_commands  # меню команд; ошибка регистрации не роняет запуск
    await register_commands(bot_app)


def make_lifespan(mode, bot_token, public_url, webhook_secret, application, maintenance=None):
    @asynccontextmanager
    async def bot_lifespan(app):
        app.state.application = None
        app.state.notify_bot = None
        if mode == "api":
            logger.info("Бот отключён: режим только API")
            yield
            return

        bot_app = application
        if bot_app is None:
            from bot import build_application  # импорт здесь: режиму «только API» бот не нужен
            bot_app = build_application(bot_token, use_updater=(mode == "polling"))

        if hasattr(bot_app, "bot_data"):  # для команды /backupnow (только владелец)
            bot_app.bot_data["backup_sender"] = getattr(app.state, "backup_sender", None)
        await bot_app.initialize()
        await bot_app.start()
        try:
            if mode == "webhook":
                try:
                    await bot_app.bot.set_webhook(
                        url=public_url + WEBHOOK_PATH,
                        secret_token=webhook_secret,
                        allowed_updates=Update.ALL_TYPES,
                    )
                except Exception as exc:
                    # в тексте ошибки может быть адрес с токеном, поэтому пишем только тип
                    logger.error("Не удалось зарегистрировать webhook: %s", type(exc).__name__)
                    raise RuntimeError("webhook registration failed") from None
                app.state.application = bot_app
                logger.info("Бот запущен в режиме webhook")
                await _register_commands(bot_app)
            else:
                # ВНИМАНИЕ: start_polling снимает webhook у бота (Telegram не отдаёт
                # обновления одновременно через webhook и getUpdates). Если тот же бот
                # работает на Railway, он перестанет получать сообщения, пока там не
                # перезапустят сервис и webhook не зарегистрируется снова.
                await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                logger.info("Бот запущен в режиме polling")
                await _register_commands(bot_app)
            app.state.notify_bot = bot_app.bot  # уведомления владельцу возможны, только пока бот работает
            yield
        finally:
            app.state.notify_bot = None
            if mode == "polling":
                await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()

    @asynccontextmanager
    async def lifespan(app):
        # фоновая задача (резервные копии и очистка) работает во всех режимах; в тестах выключена
        task = None
        app.state.backup_sender = None
        if maintenance is not None:
            notifier = None
            sender = None
            send_config = maintenance.get("send_config")
            if send_config is not None and send_config["enabled"]:
                sender = backup_send.EncryptedSender(maintenance["owner_id"], lambda: app.state.notify_bot,
                                                     maintenance["db_path"], maintenance["config"], send_config)
                app.state.backup_sender = sender
            if maintenance.get("owner_id") is not None:
                notifier = notify.Notifier(maintenance["owner_id"], lambda: app.state.notify_bot,
                                           maintenance["db_path"], maintenance["config"],
                                           encrypted_send=sender is not None)
            task = asyncio.create_task(backup.maintenance_loop(
                maintenance["config"], maintenance["db_path"], notifier=notifier, sender=sender,
                **maintenance.get("loop_args", {})))
        try:
            async with bot_lifespan(app):
                yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan
