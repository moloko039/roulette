"""Фишки за кристаллы: пакеты в часах фермы и покупка (план экономики, этап E6). Фишки можно только купить: продать или вывести нельзя."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import wallet
from db import ChipsError, buy_chip_pack, chip_packs_state
from roulette import BalanceLimit, RequestConflict, validate_request_id
from web.http import BodyTooLarge, read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled
    user_of = ctx.user_of

    @app.get("/api/chips/packs")
    def chips_packs(authorization: str = Header(default=None)):
        user_id = ctx.auth(authorization)
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited
        return chip_packs_state(user_id, db_path=db_path)

    @app.post("/api/chips/buy")
    async def chips_buy(request: Request):
        user_id, limited = user_of(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "pack_code"} or type(data["pack_code"]) is not str:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        try:
            return await run_in_threadpool(buy_chip_pack, user_id, request_id, data["pack_code"], None, db_path)
        except ChipsError as exc:
            return JSONResponse({"detail": exc.code}, status_code=404 if exc.code == "unknown_pack" else 409)
        except wallet.InsufficientGems:
            return JSONResponse({"detail": "insufficient_gems"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)
        except RequestConflict:
            return JSONResponse({"detail": "request_conflict"}, status_code=409)
