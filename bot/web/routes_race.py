"""Забег свинок: маршрут POST /api/race/play (по образцу slot_spin)."""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from features.race_db import play_race
import race
from roulette import BalanceLimit, InsufficientFunds
from web.http import read_json_object


def register(app, ctx):
    db_path = ctx.db_path
    user_of = ctx.user_of

    @app.post("/api/race/play")
    async def race_play_endpoint(request: Request):
        user_id, limited = user_of(request.headers, "write")
        if limited is not None:
            return limited

        data = await read_json_object(request, {"request_id", "lanes", "lane", "bet"})
        if isinstance(data, JSONResponse):
            return data
        lanes = data["lanes"]
        lane = data["lane"]
        bet = data["bet"]
        if type(lanes) is not int or lanes not in race.RACE_LANES:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        if type(lane) is not int or not 0 <= lane < lanes:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        if type(bet) is not int or not race.RACE_MIN_BET <= bet <= race.RACE_MAX_BET:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        request_id = data["request_id"]
        call = lambda: play_race(user_id, request_id, lanes, lane, bet, db_path=db_path)

        try:
            return await run_in_threadpool(call)
        except race.RaceError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)
