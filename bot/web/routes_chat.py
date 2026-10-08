"""Рейтинг беседы, рекорды выигрыша и буст беседы."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import wallet
from core.chat_bonus import BoostCapReached, NoChat, NotAttributed, buy_chat_boost
from db import chat_best_wins, chat_top
from roulette import validate_request_id
from web.http import BodyTooLarge, _in_group, read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/chat/top")
    def chat_top_endpoint(authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        # в ответе только rank, name, balance, is_me, staked и chat_staked: ни telegram_id, ни chat_instance, ни username
        return chat_top(info["chat_instance"], info["user_id"], info["first_name"], db_path=db_path)

    @app.get("/api/chat/best-wins")
    def chat_best_wins_endpoint(authorization: str = Header(default=None)):
        # отдельный маршрут (а не поле в /api/chat/top): сбой или рост рекордов не ломает основной рейтинг, клиент грузит их независимо;
        # проверки те же (initData, лимит чтения, scope none вне беседы); только чтение. В ответе нет telegram_id, chat_instance и времени
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        return chat_best_wins(info["chat_instance"], info["user_id"], db_path=db_path)

    @app.post("/api/chat/boost")
    async def chat_boost_endpoint(request: Request):
        info = ctx.auth_full(request.headers.get("authorization"))
        limited = throttled(info["user_id"], "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id"}:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        if not _in_group(info):
            return JSONResponse({"detail": "no_chat"}, status_code=409)
        try:
            return await run_in_threadpool(buy_chat_boost, info["user_id"], request_id, info["chat_instance"], None, db_path)
        except NotAttributed:
            return JSONResponse({"detail": "not_attributed"}, status_code=409)
        except NoChat:
            return JSONResponse({"detail": "no_chat"}, status_code=409)
        except BoostCapReached:
            return JSONResponse({"detail": "boost_cap_reached"}, status_code=409)
        except wallet.InsufficientGems:
            return JSONResponse({"detail": "insufficient_gems"}, status_code=409)
