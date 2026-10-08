"""Служебные маршруты: /health, обработчик занятой базы, /api/me."""
import logging
import sqlite3
import time

from fastapi import Header
from fastapi.responses import JSONResponse

import db
import economy
import farm
from db import (active_game_of, cosmetics_state, gems_state, get_player, settle_expired_blackjack, settle_expired_crash, settle_expired_hilo, settle_expired_mines, touch_chat_member)
from core.chat_bonus import get_chat_bonus
from levels import profile_level
from web.http import _in_group

logger = logging.getLogger("depnaya")


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request, exc):
        """База занята дольше таймаута (и после повтора BEGIN): 503 с просьбой повторить, а не 500. Остальные ошибки SQLite
        остаются 500. В лог идёт только факт, без запроса и параметров."""
        if not db.is_busy_error(exc):
            raise exc
        logger.warning("База занята: ответ 503")
        return JSONResponse({"detail": "busy"}, status_code=503, headers={"Retry-After": "1"})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/me")
    def me(authorization: str = Header(default=None)):
        info = ctx.auth_full(authorization)
        user_id = info["user_id"]
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited

        now = int(time.time())
        
        start_param = info.get("start_param")
        if start_param:
            from core.db_conn import _connect
            conn = _connect(db_path)
            try:
                if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (user_id,)).fetchone() is None:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        db._register_player(conn, user_id, now, start_param=start_param)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            finally:
                conn.close()

        settle_expired_mines(user_id, now=now, db_path=db_path)  # просроченная игра в мины закрывается
        settle_expired_blackjack(user_id, now=now, db_path=db_path)  # и просроченная раздача блэкджека
        settle_expired_crash(user_id, db_path=db_path)  # и разбившийся или брошенный раунд краша
        settle_expired_hilo(user_id, now=now, db_path=db_path)  # и просроченная партия в хило

        try:
            from core.db_conn import _connect
            conn = _connect(db_path)
            try:
                is_unqualified = conn.execute("SELECT 1 FROM referrals WHERE invitee_id = ? AND qualified_at IS NULL", (user_id,)).fetchone() is not None
            finally:
                conn.close()
            if is_unqualified:
                db.check_qualification(user_id, now=now, db_path=db_path)
        except Exception:
            logger.error("check_qualification error", exc_info=True)

        player = get_player(user_id, now=now, db_path=db_path)
        if _in_group(info):
            try:
                touch_chat_member(info["chat_instance"], user_id, info["first_name"], now=now, db_path=db_path)
            except Exception:
                pass  # рейтинг не должен ломать /api/me
        return {
            "balance": player["balance"],
            "rate": player["rate"],
            "seconds_to_next": economy.next_tick_in(now),   # до следующей минутной границы начисления (раньше: до часа)
            "level": profile_level(player["xp"]),   # уровень профиля по опыту
            "income_level": player["income_level"],
            "storage_level": player["storage_level"],
            "farm": {                         # поминутное начисление: ленивое, при этом запросе уже подтянуто
                "income_per_hour": player["rate"],
                "per_minute_estimate": economy.per_minute_estimate(player["rate"]),
                "next_tick_in_s": economy.next_tick_in(now),
                "hours_cap": farm.storage_hours(player["storage_level"]),
                "accrued_now": player["accrued"],   # сколько фишек зачислил именно этот запрос
            },
            "active_game": active_game_of(user_id, db_path=db_path),   # "mines" | "blackjack" | "crash" | "hilo" | null
            "gems": gems_state(user_id, db_path=db_path)["gems"],   # кристаллы (премиум-валюта, план экономики E2)
            "cosmetics": cosmetics_state(user_id, db_path=db_path),   # только внешний вид: {equipped: {слот: код}, show_in_rating}
            "chat": get_chat_bonus(None, user_id, now=now, db_path=db_path) if _in_group(info) else {"in_chat": False, "bonus_pct": 0, "active_today": 0, "boost_until": None, "boost_gems": 50},
        }
