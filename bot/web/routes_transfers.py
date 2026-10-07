"""Переводы между участниками беседы."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import notify
import transfers
from db import (transfer_history, transfer_send)
from roulette import InsufficientFunds, validate_request_id
from web.http import BodyTooLarge
from web.http import _in_group
from web.http import read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled
    user_of = ctx.user_of

    # ---------- переводы между участниками беседы ----------
    # Идентификаторы Telegram в ответах не показываются: участник задаётся непрозрачной меткой member_ref из рейтинга беседы

    @app.post("/api/transfers/send")
    async def transfers_send_endpoint(request: Request):
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
            if type(data) is not dict or set(data) != {"request_id", "member_ref", "amount"}:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            ref, amount = data["member_ref"], data["amount"]
            if not transfers.valid_member_ref(ref) or not transfers.valid_amount(amount):
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            return await run_in_threadpool(lambda: transfer_send(info["user_id"], chat, info["first_name"], ref, amount,
                                                                   request_id, owner_id=notify.load_owner_id(), db_path=db_path))
        except transfers.TransferError as exc:
            return JSONResponse(dict({"detail": exc.code}, **exc.extra), status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)

    @app.get("/api/transfers")
    def transfers_list_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return {"items": transfer_history(user_id, db_path=db_path)}
