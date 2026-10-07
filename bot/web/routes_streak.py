"""Серия входов: карточка «Награда дня» и сбор награды (ECONOMY_ADDITIONS.md, п. 3)."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import claim_streak, streak_status
from web.http import BodyTooLarge, read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled
    user_of = ctx.user_of

    @app.get("/api/streak")
    def streak(authorization: str = Header(default=None)):
        user_id = ctx.auth(authorization)
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited
        return streak_status(user_id, db_path=db_path)

    @app.post("/api/streak/claim")
    async def streak_claim(request: Request):
        user_id, limited = user_of(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw) if raw else {}
            if type(data) is not dict or data:
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        return await run_in_threadpool(claim_streak, user_id, None, db_path)
