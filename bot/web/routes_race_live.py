"""Общий забег свинок: GET /api/race/live (состояние раунда и лента комнаты беседы) и POST /api/race/live/bet (ставка на дорожку)."""
from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import race
import race_live
from features import race_live_db
from roulette import BalanceLimit, InsufficientFunds
from web.http import _in_group, read_json_object


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    def room_of(info):
        return race_live.room_key(info["chat_instance"]) if _in_group(info) else race_live.room_key()       # вне беседы: общая анонимная комната

    @app.get("/api/race/live")
    async def race_live_state(request: Request, authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "live")
        if limited is not None:
            return limited
        chat_instance = info["chat_instance"] if _in_group(info) else None
        return await run_in_threadpool(lambda: race_live_db.live_state(info["user_id"], room_of(info), race_live.now_ms(), chat_instance, db_path))

    @app.post("/api/race/live/bet")
    async def race_live_bet(request: Request):
        info = ctx.auth_full(request.headers.get("authorization"))
        limited = throttled(info["user_id"], "write")
        if limited is not None:
            return limited
        data = await read_json_object(request, {"request_id", "lane", "bet"})
        if isinstance(data, JSONResponse):
            return data
        lane, bet = data["lane"], data["bet"]
        if type(lane) is not int or not 0 <= lane < race_live.LANES or type(bet) is not int or not race.RACE_MIN_BET <= bet <= race.RACE_MAX_BET:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        request_id = data["request_id"]
        try:
            return await run_in_threadpool(lambda: race_live_db.place_bet(info["user_id"], room_of(info), request_id, lane, bet, race_live.now_ms(), db_path))
        except race.RaceError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)
