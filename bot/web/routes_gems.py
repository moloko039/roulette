"""Кристаллы: список пакетов и ссылка на оплату пакета в Telegram Stars (план экономики, этап E2).

Баланс кристаллов отдаёт /api/me (поле gems). Оплату принимает бот (pre_checkout_query и successful_payment, tg/payments.py)."""
import json
import logging
import time

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from telegram import LabeledPrice

import cosmetics
from db import gems_state, pack, packs_view
from features.gems_db import UnknownPack
from roulette import validate_request_id
from web.http import BodyTooLarge, read_body_limited

GEM_INVOICE_INTERVAL = 10      # не чаще одной ссылки на оплату на игрока и пакет за это число секунд

logger = logging.getLogger("depnaya")


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled
    user_of = ctx.user_of
    invoice_cache = {}      # (игрок, пакет) -> (время, request_id, ссылка): повтор того же request_id отдаёт ту же ссылку

    @app.get("/api/gems/packs")
    def gems_packs(authorization: str = Header(default=None)):
        user_id = ctx.auth(authorization)
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited
        return {"packs": packs_view(), "gems": gems_state(user_id, db_path=db_path)["gems"]}

    @app.post("/api/gems/invoice")
    async def gems_invoice(request: Request):
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
        code = data["pack_code"]
        bot = getattr(getattr(app.state, "application", None), "bot", None)
        if bot is None:
            return JSONResponse({"detail": "payments_unavailable"}, status_code=503)
        try:
            stars, gems = pack(code)
        except UnknownPack:
            return JSONResponse({"detail": "unknown_pack"}, status_code=404)
        now = time.monotonic()
        last = invoice_cache.get((user_id, code))
        if last is not None and now - last[0] < GEM_INVOICE_INTERVAL:
            if last[1] == request_id:
                return {"invoice_url": last[2], "replayed": True}
            return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": str(max(1, int(GEM_INVOICE_INTERVAL - (now - last[0]))))})
        title = ("Кристаллы: %d" % gems)[:32]
        text = "Пакет кристаллов (%d). Кристаллы тратятся на предметы оформления, на шансы в играх не влияют." % gems
        try:
            url = await bot.create_invoice_link(title=title, description=text[:255], payload=cosmetics.make_payload(user_id, code, int(time.time())),
                                                currency="XTR", prices=[LabeledPrice(title, stars)], provider_token="")
        except Exception as exc:
            logger.error("Не удалось создать ссылку на оплату пакета: %s", type(exc).__name__)
            return JSONResponse({"detail": "invoice_failed"}, status_code=502)
        invoice_cache[(user_id, code)] = (now, request_id, url)
        return {"invoice_url": url, "replayed": False}

