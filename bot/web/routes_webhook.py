"""Webhook Telegram."""
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from telegram import Update

from web.http import _forbidden
from web.http import WEBHOOK_PATH

SECRET_HEADER = "x-telegram-bot-api-secret-token"


def register(app, ctx):
    webhook_secret = ctx.webhook_secret

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(request: Request):
        got = request.headers.get(SECRET_HEADER)
        bot_app = app.state.application
        if (not webhook_secret or not got or bot_app is None
                or not hmac.compare_digest(got.encode(), webhook_secret.encode())):
            return _forbidden()
        try:
            update = Update.de_json(await request.json(), bot_app.bot)
        except Exception:
            update = None
        if update is None:
            return JSONResponse({"detail": "Bad request"}, status_code=400)
        # обработка не здесь: кладём в очередь, Application разберёт обновление сам
        await bot_app.update_queue.put(update)
        return {"ok": True}
