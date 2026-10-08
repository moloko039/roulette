"""Беседа: атрибуция и бонусы фермы."""

import time
from datetime import datetime, timezone, timedelta

from economy_config import (
    CHAT_BONUS_PER_PLAYER_PCT,
    CHAT_BONUS_MAX_PCT,
    CHAT_BOOST_GEMS,
    CHAT_BOOST_PCT,
    CHAT_BOOST_HOURS,
    CHAT_BOOST_MAX_PCT,
    STREAK_UTC_OFFSET_HOURS,
)
from core.db_conn import _connect
import wallet

def _is_active_today(last_played_at, now):
    if not last_played_at:
        return False
    tz = timezone(timedelta(hours=STREAK_UTC_OFFSET_HOURS))
    now_dt = datetime.fromtimestamp(now, tz)
    last_dt = datetime.fromtimestamp(last_played_at, tz)
    return now_dt.date() == last_dt.date()

def get_chat_bonus(conn, telegram_id, now=None, db_path=None):
    """Вычисляет бонус фермы для игрока и его принадлежность к беседе."""
    if now is None:
        now = int(time.time())

    close_conn = False
    if conn is None:
        conn = _connect(db_path)
        close_conn = True

    try:
        # 1. Атрибуция (найти основную беседу игрока)
        chat_row = conn.execute(
            "SELECT chat_instance FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1",
            (telegram_id,)
        ).fetchone()

        if not chat_row:
            return {"in_chat": False, "bonus_pct": 0, "active_today": 0, "boost_until": None, "boost_gems": CHAT_BOOST_GEMS}

        chat_instance = chat_row["chat_instance"]

        rows = conn.execute(
            """
            SELECT m.telegram_id, p.last_played_at 
            FROM chat_members m
            JOIN players p ON p.telegram_id = m.telegram_id
            WHERE m.chat_instance = ?
            """,
            (chat_instance,)
        ).fetchall()

        active_count = 0
        for r in rows:
            if _is_active_today(r["last_played_at"], now):
                last_seen_max = conn.execute(
                    "SELECT chat_instance FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1",
                    (r["telegram_id"],)
                ).fetchone()
                if last_seen_max and last_seen_max["chat_instance"] == chat_instance:
                    active_count += 1

        members_bonus = min(active_count * CHAT_BONUS_PER_PLAYER_PCT, CHAT_BONUS_MAX_PCT)

        # 3. Активные бусты
        boost_rows = conn.execute(
            "SELECT expires_at FROM chat_boosts WHERE chat_instance = ? AND expires_at > ?",
            (chat_instance, now)
        ).fetchall()
        
        boost_count = len(boost_rows)
        boosts_bonus = min(boost_count * CHAT_BOOST_PCT, CHAT_BOOST_MAX_PCT)
        
        max_boost_until = max((r["expires_at"] for r in boost_rows), default=None)

        total_bonus_pct = members_bonus + boosts_bonus

        return {
            "in_chat": True,
            "bonus_pct": total_bonus_pct,
            "active_today": active_count,
            "boost_until": max_boost_until,
            "boost_gems": CHAT_BOOST_GEMS,
        }

    finally:
        if close_conn:
            conn.close()

class NoChat(Exception): pass
class NotAttributed(Exception): pass
class BoostCapReached(Exception): pass

def buy_chat_boost(telegram_id, request_id, expected_chat_instance, now=None, db_path=None):
    if now is None:
        now = int(time.time())
    
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. Атрибуция
            chat_row = conn.execute(
                "SELECT chat_instance FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1",
                (telegram_id,)
            ).fetchone()
            if not chat_row:
                raise NoChat()
            chat_instance = chat_row["chat_instance"]
            
            if chat_instance != expected_chat_instance:
                raise NotAttributed()

            # Идемпотентность (т.к. мы списываем гемы)
            ledger_entry = conn.execute(
                "SELECT ref FROM gems_ledger WHERE telegram_id = ? AND reason = 'boost_purchase' AND ref = ?",
                (telegram_id, request_id)
            ).fetchone()
            
            if ledger_entry:
                # Уже купил с этим request_id. Возвращаем результат
                bonus = get_chat_bonus(conn, telegram_id, now)
                balance = wallet.gems_balance(conn, telegram_id)
                conn.execute("COMMIT")
                return {
                    "bonus_pct": bonus["bonus_pct"],
                    "boost_until": bonus["boost_until"],
                    "gems": balance
                }

            # Потолок бустов уже достигнут: новый буст не добавит бонуса, поэтому кристаллы не списываем
            active = conn.execute(
                "SELECT COUNT(*) FROM chat_boosts WHERE chat_instance = ? AND expires_at > ?",
                (chat_instance, now)
            ).fetchone()[0]
            if active * CHAT_BOOST_PCT >= CHAT_BOOST_MAX_PCT:
                raise BoostCapReached()

            expires_at = now + CHAT_BOOST_HOURS * 3600
            cur = conn.execute(
                "INSERT INTO chat_boosts (chat_instance, telegram_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (chat_instance, telegram_id, now, expires_at)
            )
            boost_id = cur.lastrowid
            
            balance = wallet.gems_debit(conn, telegram_id, CHAT_BOOST_GEMS, "boost_purchase", request_id, now)
            
            bonus = get_chat_bonus(conn, telegram_id, now)
            conn.execute("COMMIT")
            return {
                "bonus_pct": bonus["bonus_pct"],
                "boost_until": bonus["boost_until"],
                "gems": balance
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
