import re

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import crash
import crash_live
from wallet import InsufficientFunds, BalanceLimitExceeded as BalanceLimit
from web.http import _in_group, read_json_object
from features import crash_live_db


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/crash/live")
    async def live_state_endpoint(request: Request, authorization: str = Header(default=None)):
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

        v_param = request.query_params.get("v")
        if v_param and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", v_param):
            v_param = None

        args = dict(telegram_id=info["user_id"], room_key=r_key, now_ms=crash_live.now_ms(), chat_instance=chat_instance, db_path=db_path, client_v=v_param)
        # опрос: читающий путь (доли миллисекунды) прямо в цикле событий, запись (смена фазы раунда) в пуле потоков
        res = crash_live_db.live_state_read(**args)
        if res is None and request.query_params.get("peek") == "1":
            # «посмотреть, не начиная»: живая карточка на главной не должна открывать раунды и тратить запись (только читающий путь)
            return {"server_ms": args["now_ms"], "v": "peek", "history": [], "round": None, "me": None, "bets": []}
        if res is None:
            res = await run_in_threadpool(lambda: crash_live_db.live_state(**args))
        return res

    @app.post("/api/crash/live/bet")
    async def live_bet_endpoint(request: Request):
        info = ctx.auth_full(request.headers.get("authorization"))
        limited = throttled(info["user_id"], "write")
        if limited is not None:
            return limited

        data = await read_json_object(request, {"request_id", "bet"}, optional={"target_x100"})
        if isinstance(data, JSONResponse):
            return data
        if "target_x100" in data and data["target_x100"] is not None and type(data["target_x100"]) is not int:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        if type(data["bet"]) is not int or isinstance(data["bet"], bool):
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        target_x100 = data.get("target_x100")
        if target_x100 is not None and isinstance(target_x100, bool):
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        request_id = data["request_id"]
        bet = data["bet"]

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

        data = await read_json_object(request, {"request_id"})
        if isinstance(data, JSONResponse):
            return data
        request_id = data["request_id"]

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
