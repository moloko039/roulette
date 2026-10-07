"""Косметика: только внешний вид (bot/cosmetics.py); оплата Stars через ссылку-инвойс."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import cosmetics
from db import (buy_item, cosmetics_mine, equip_item, set_visibility, unequip_item)
from roulette import validate_request_id
from web.http import BodyTooLarge, read_body_limited



def register(app, ctx):
    db_path = ctx.db_path
    user_of = ctx.user_of

    # ---------- косметика ----------
    # Только внешний вид (bot/cosmetics.py). Принадлежность чужих игроков не раскрывается; платежей нет.

    @app.get("/api/cosmetics/catalog")
    def cosmetics_catalog(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return cosmetics.catalog_view()

    @app.get("/api/cosmetics/mine")
    def cosmetics_mine_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return cosmetics_mine(user_id, db_path=db_path)

    async def _cosmetics_post(request, keys, call):
        user_id, limited = user_of(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != keys:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        try:
            return await run_in_threadpool(call, user_id, request_id, data)
        except ValueError:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        except cosmetics.UnknownItem:
            return JSONResponse({"detail": "unknown_item"}, status_code=404)
        except cosmetics.TooFast:
            return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": "1"})
        except cosmetics.CosmeticsError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)

    @app.post("/api/cosmetics/equip")
    async def cosmetics_equip(request: Request):
        return await _cosmetics_post(request, {"request_id", "slot", "code"},
                                     lambda uid, rid, d: equip_item(uid, rid, d["slot"], d["code"], db_path=db_path))

    @app.post("/api/cosmetics/unequip")
    async def cosmetics_unequip(request: Request):
        return await _cosmetics_post(request, {"request_id", "slot"},
                                     lambda uid, rid, d: unequip_item(uid, rid, d["slot"], db_path=db_path))

    @app.post("/api/cosmetics/visibility")
    async def cosmetics_visibility(request: Request):
        return await _cosmetics_post(request, {"request_id", "show_in_rating"},
                                     lambda uid, rid, d: set_visibility(uid, rid, d["show_in_rating"], db_path=db_path))

    @app.post("/api/cosmetics/buy")
    async def cosmetics_buy(request: Request):
        return await _cosmetics_post(request, {"request_id", "item_code"},
                                     lambda uid, rid, d: buy_item(uid, rid, d["item_code"], db_path=db_path))

    @app.post("/api/cosmetics/invoice")
    async def cosmetics_invoice(request: Request):
        # Предметы за Stars больше не продаются (план экономики, E2): за Stars покупаются кристаллы (POST /api/gems/invoice), предметы покупаются за кристаллы
        # или фишки (POST /api/cosmetics/buy). Счета, выставленные раньше, принимает бот (pre_checkout по прежней цене).
        return JSONResponse({"detail": "gone"}, status_code=410)
