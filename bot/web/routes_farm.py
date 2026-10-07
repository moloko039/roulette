"""Ферма: статус и покупка улучшений."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import farm
from db import (buy_upgrade, farm_status)
from roulette import InsufficientFunds, validate_request_id
from web.http import BodyTooLarge, read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/farm")
    def farm_endpoint(authorization: str = Header(default=None)):
        user_id = ctx.auth(authorization)
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited
        return farm_status(user_id, db_path=db_path)

    @app.post("/api/farm/buy")
    async def farm_buy(request: Request):
        user_id = ctx.auth(request.headers.get("authorization"))
        limited = throttled(user_id, "write")
        if limited is not None:
            return limited

        # любая ошибка формы тела: 400 с одним и тем же текстом
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "kind"}:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            kind = data["kind"]
            if type(kind) is not str or kind not in farm.KINDS:
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)

        try:
            return await run_in_threadpool(buy_upgrade, user_id, request_id, kind, None, db_path)
        except farm.MaxLevel:
            return JSONResponse({"detail": "max_level"}, status_code=409)
        except farm.LevelLocked as exc:
            return JSONResponse({"detail": "level_locked", "required_level": exc.required_level}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
