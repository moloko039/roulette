"""Bloody slot: GET /api/bloody/state (активный или последний раунд и настройки игры), POST /api/bloody/start (ставка или покупка функции, стартовый спин),
POST /api/bloody/spin (следующий бонусный спин активного раунда). Правила и числа: bloody_config.py, форма ответов: docs/API.md."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import bloody_config as cfg
import bloody_round as br
from games import bloody_db
from roulette import BalanceLimit, InsufficientFunds, validate_request_id
from web.http import BodyTooLarge, read_body_limited


def settings():
    """Настройки для интерфейса (одни и те же числа, что использует сервер): лестница ставок, цены покупок в ставках, потолок выигрыша."""
    return {"bets": list(cfg.BET_LADDER), "buys": {k: v / cfg.UNITS_PER_BET for k, v in cfg.BUY_COST_UNITS.items()}, "max_win_bets": cfg.MAX_WIN_BETS,
            "reels": cfg.REELS, "rows": cfg.WORKING_ROWS, "bar_thresholds": list(cfg.BAR_THRESHOLDS), "idle_days": cfg.IDLE_SECONDS // 86400,
            "paytable": {sym: {str(n): v / cfg.UNITS_PER_BET for n, v in pays.items()} for sym, pays in cfg.PAYTABLE_UNITS.items()},
            "wild_pay": cfg.WILD_PAY_UNITS[5] / cfg.UNITS_PER_BET, "min_length": cfg.MIN_WIN_LENGTH, "candle_per_level": cfg.CANDLE_SPINS_PER_LEVEL, "reign_spins": cfg.REIGN_SPINS}


def register(app, ctx):
    db_path = ctx.db_path

    @app.get("/api/bloody/state")
    async def bloody_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = ctx.user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        res = await run_in_threadpool(lambda: bloody_db.bloody_state(user_id, db_path=db_path))
        res["settings"] = settings()
        return res

    async def parse(request, keys):
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return None, JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or not ({"request_id"} <= set(data) <= keys):
                raise ValueError()
            data["request_id"] = validate_request_id(data["request_id"])
            return data, None
        except Exception:
            return None, JSONResponse({"detail": "invalid_request"}, status_code=400)

    async def run(call):
        try:
            return await run_in_threadpool(call)
        except br.SpinError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)

    @app.post("/api/bloody/start")
    async def bloody_start_endpoint(request: Request):
        user_id, limited = ctx.user_of(request.headers, "write")
        if limited is not None:
            return limited
        data, err = await parse(request, {"request_id", "bet", "buy"})
        if err is not None:
            return err
        bet, buy = data.get("bet"), data.get("buy")
        if type(bet) is not int or (buy is not None and (type(buy) is not str or buy not in cfg.BUY_COST_UNITS)):
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        return await run(lambda: bloody_db.bloody_start(user_id, data["request_id"], bet, buy=buy, db_path=db_path))

    @app.post("/api/bloody/spin")
    async def bloody_spin_endpoint(request: Request):
        user_id, limited = ctx.user_of(request.headers, "write")
        if limited is not None:
            return limited
        data, err = await parse(request, {"request_id"})
        if err is not None:
            return err
        return await run(lambda: bloody_db.bloody_spin(user_id, data["request_id"], db_path=db_path))
