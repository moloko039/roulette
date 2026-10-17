"""Подарки косметикой участникам своей беседы (ECONOMY_ADDITIONS.md, п. 2): список получателей и отправка подарка за кристаллы."""
import logging

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import cosmetics
import wallet
from db import GiftError, gift_recipients, send_gift
from roulette import RequestConflict
from web.http import _in_group, read_json_object

logger = logging.getLogger("depnaya")


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/gifts/recipients")
    def gifts_recipients(authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            return gift_recipients(info["user_id"], chat, db_path=db_path)
        except GiftError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)

    @app.post("/api/gifts/send")
    async def gifts_send(request: Request):
        info = ctx.auth_full(request.headers.get("authorization"))
        limited = throttled(info["user_id"], "write")
        if limited is not None:
            return limited
        data = await read_json_object(request, {"request_id", "ref", "item_code"})
        if isinstance(data, JSONResponse):
            return data
        if type(data["ref"]) is not str or type(data["item_code"]) is not str or not 1 <= len(data["ref"]) <= 64:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        request_id = data["request_id"]
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            result, notice = await run_in_threadpool(send_gift, info["user_id"], chat, request_id, data["ref"], data["item_code"], info["first_name"], None, db_path)
        except cosmetics.UnknownItem:
            return JSONResponse({"detail": "unknown_item"}, status_code=404)
        except cosmetics.CosmeticsError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except GiftError as exc:
            return JSONResponse({"detail": exc.code}, status_code=404 if exc.code == "unknown_recipient" else 409)
        except wallet.InsufficientGems:
            return JSONResponse({"detail": "insufficient_gems"}, status_code=409)
        except RequestConflict:
            return JSONResponse({"detail": "request_conflict"}, status_code=409)
        if notice is not None:
            bot = getattr(getattr(app.state, "application", None), "bot", None)
            if bot is not None:
                try:
                    await bot.send_message(notice["to_user"], "Подарок от %s: предмет «%s» уже в разделе «Оформление» магазина." % (notice["from_name"] or "участника беседы", notice["item_name"]))
                except Exception as exc:       # получатель мог не открывать бота: подарок уже выдан, уведомление не обязательно
                    logger.info("Уведомление о подарке не отправлено: %s", type(exc).__name__)
        return result
