"""Рефералка (E5): эндпоинт получения ссылки и статистики."""
import os

import economy_config

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
                "SELECT COUNT(*) as invited, SUM(qualified_at IS NOT NULL) as qualified, COALESCE(SUM(commission_paid), 0) as commission "
                "FROM referrals WHERE referrer_id = ?",
                (user_id,)
            ).fetchone()
            invited = row["invited"] if row else 0
            qualified = row["qualified"] if row and row["qualified"] is not None else 0
            commission = row["commission"] if row else 0
        finally:
            conn.close()

        rules = {
            "invitee_chips": economy_config.REFERRAL_INVITEE_CHIPS,
            "inviter_chips": economy_config.REFERRAL_INVITER_CHIPS,
            "inviter_gems": economy_config.REFERRAL_INVITER_GEMS,
            "qualify_hours": economy_config.REFERRAL_QUALIFY_HOURS,
            "qualify_level": economy_config.REFERRAL_QUALIFY_LEVEL,
            "qualify_rounds": economy_config.REFERRAL_QUALIFY_ROUNDS,
            "commission_pct": economy_config.REFERRAL_COMMISSION_PCT,
            "commission_days": economy_config.REFERRAL_COMMISSION_DAYS,
            "commission_cap": economy_config.REFERRAL_COMMISSION_CAP,
            "founder_chips": economy_config.FOUNDER_CHIPS,
            "founder_gems": economy_config.FOUNDER_GEMS,
            "founder_players": economy_config.FOUNDER_ACTIVE_PLAYERS,
            "founder_level": economy_config.FOUNDER_PLAYER_LEVEL
        }

        milestones = [{"count": need, "item": code, "reached": qualified >= need} for need, code in economy_config.REFERRAL_MILESTONES]
        return {"link": link, "invited": invited, "qualified": qualified, "commission_earned": commission, "milestones": milestones, "rules": rules}
