import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import crash
import crash_live
from wallet import InsufficientFunds, BalanceLimitExceeded as BalanceLimit
from web.http import BodyTooLarge, read_body_limited, _in_group
from features import crash_live_db
from roulette import validate_request_id


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/crash/live")
    def live_state_endpoint(authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "live")
        if limited is not None:
            return limited

        chat_instance = None
        if _in_group(info):
            chat_instance = info["chat_instance"]
            r_key = crash_live.room_key(chat_instance=chat_instance)
        else:
            r_key = crash_live.room_key()      # вне беседы: общая анонимная комната (ставки видны без имён)

        return crash_live_db.live_state(
            telegram_id=info["user_id"],
            room_key=r_key,
            now_ms=crash_live.now_ms(),
            chat_instance=chat_instance,
            db_path=db_path
        )

    @app.post("/api/crash/live/bet")
    async def live_bet_endpoint(request: Request):
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
            if type(data) is not dict or not ({"request_id", "bet"} <= set(data) <= {"request_id", "bet", "target_x100"}):
                raise ValueError()
            if "target_x100" in data and data["target_x100"] is not None and type(data["target_x100"]) is not int:
                raise ValueError()
            if type(data["bet"]) is not int or isinstance(data["bet"], bool):
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            bet = data["bet"]
            target_x100 = data.get("target_x100")
            if target_x100 is not None and isinstance(target_x100, bool):
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)

        if _in_group(info):
            r_key = crash_live.room_key(chat_instance=info["chat_instance"])
        else:
            r_key = crash_live.room_key()      # вне беседы: общая анонимная комната (ставки видны без имён)

        def call():
            return crash_live_db.place_bet(
                telegram_id=info["user_id"],
                room_key=r_key,
                request_id=request_id,
                bet=bet,
                target_x100=target_x100,
                now_ms=crash_live.now_ms(),
                db_path=db_path
            )

        try:
            return await run_in_threadpool(call)
        except crash.CrashError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)
        except crash_live.RequestConflict:
            return JSONResponse({"detail": "request_conflict"}, status_code=409)
        except ValueError:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)

    @app.post("/api/crash/live/cashout")
    async def live_cashout_endpoint(request: Request):
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

        def call():
            return crash_live_db.cashout(
                telegram_id=info["user_id"],
                request_id=request_id,
                now_ms=crash_live.now_ms(),
                db_path=db_path
            )

        try:
            return await run_in_threadpool(call)
        except crash.CrashError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except crash_live.RequestConflict:
            return JSONResponse({"detail": "request_conflict"}, status_code=409)
