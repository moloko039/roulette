"""Рейтинг беседы и рекорды выигрыша."""
from fastapi import Header

from db import chat_best_wins, chat_top
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
