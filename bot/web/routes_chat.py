"""Рейтинг беседы, рекорды выигрыша, участники беседы для переводов."""
import re

from fastapi import Header, Request
from fastapi.responses import JSONResponse

import transfers
from db import (chat_best_wins, chat_members_page, chat_top)
from web.http import _in_group


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/chat/top")
    def chat_top_endpoint(authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        # в ответе только rank, name, balance, is_me, staked и chat_staked: ни telegram_id, ни chat_instance, ни username
        return chat_top(info["chat_instance"], info["user_id"], info["first_name"], db_path=db_path)

    @app.get("/api/chat/best-wins")
    def chat_best_wins_endpoint(authorization: str = Header(default=None)):
        # отдельный маршрут (а не поле в /api/chat/top): сбой или рост рекордов не ломает основной рейтинг, клиент грузит их независимо;
        # проверки те же (initData, лимит чтения, scope none вне беседы); только чтение. В ответе нет telegram_id, chat_instance и времени
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        return chat_best_wins(info["chat_instance"], info["user_id"], db_path=db_path)

    @app.get("/api/chat/members")
    def chat_members_endpoint(request: Request, authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        raw_offset = request.query_params.get("offset", "0")
        if not re.fullmatch(r"[0-9]{1,6}", raw_offset):
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            items, next_offset = chat_members_page(info["user_id"], chat, request.query_params.get("q", ""), int(raw_offset), db_path=db_path)
        except ValueError:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        except transfers.TransferError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        # только name и member_ref: без балансов, уровней и идентификаторов Telegram; в лог ни имён, ни запросов
        return {"items": items, "next_offset": next_offset}

