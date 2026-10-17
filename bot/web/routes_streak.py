"""Серия входов: карточка «Награда дня» и сбор награды (ECONOMY_ADDITIONS.md, п. 3)."""
from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import claim_streak, streak_status
from web.http import read_json_object


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
        data = await read_json_object(request, (), request_id=False)
        if isinstance(data, JSONResponse):
            return data
        return await run_in_threadpool(claim_streak, user_id, None, db_path)
