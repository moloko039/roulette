"""Рефералка (E5): эндпоинт получения ссылки и статистики."""
import os

from fastapi import Header

from core.db_conn import _connect
from features.referral_db import get_or_create_code, link_for


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.get("/api/referral")
    def get_referral(authorization: str = Header(default=None)):
        user_id = ctx.auth(authorization)
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited

        game_link = os.environ.get("GAME_LINK")
        if not game_link or len(game_link) > 300 or not game_link.startswith("https://t.me/"):
            link = None
        else:
            code = get_or_create_code(user_id, db_path=db_path)
            link = link_for(code, game_link)

        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) as invited, SUM(qualified_at IS NOT NULL) as qualified "
                "FROM referrals WHERE referrer_id = ?",
                (user_id,)
            ).fetchone()
            invited = row["invited"] if row else 0
            qualified = row["qualified"] if row and row["qualified"] is not None else 0
        finally:
            conn.close()

        return {"link": link, "invited": invited, "qualified": qualified}
