"""Функции базы данных для реферальной системы (E5)."""

import os
import secrets
import time

import economy_config
import wallet
from core.db_conn import _connect
import antiabuse

def get_or_create_code(telegram_id, now=None, db_path=None):
    """Возвращает реферальный код игрока, при отсутствии генерирует новый."""
    if now is None:
        now = int(time.time())
    
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT code FROM referral_codes WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            return row["code"]
            
        charset = "abcdefghijklmnopqrstuvwxyz0123456789"
        while True:
            code = "".join(secrets.choice(charset) for _ in range(10))
            try:
                conn.execute(
                    "INSERT INTO referral_codes (telegram_id, code, created_at) VALUES (?, ?, ?)",
                    (telegram_id, code, now)
                )
                conn.commit()
                return code
            except Exception as e:
                if "UNIQUE" in str(e):
                    continue
                raise
    finally:
        conn.close()

def link_for(code):
    """Возвращает реферальную ссылку (строка) или None, если GAME_LINK не задан."""
    from tg.common import game_link
    link = game_link()
    if not link:
        return None
    return f"{link}?startapp=ref_{code}"

def bind_referral_in(conn, invitee_id, start_param, now):
    """
    Привязывает приглашённого (invitee_id) к пригласившему по коду из start_param.
    Вызывается внутри открытой транзакции создания игрока.
    Возвращает True, если привязка успешна (и бонус начислен), иначе False.
    Ошибки игнорируются (False).
    """
    if not start_param or not start_param.startswith("ref_"):
        return False
        
    code = start_param[4:]
    
    row = conn.execute("SELECT telegram_id FROM referral_codes WHERE code = ?", (code,)).fetchone()
    if not row:
        return False
        
    referrer_id = row["telegram_id"]
    
    if referrer_id == invitee_id:
        return False
        
    has_ref = conn.execute("SELECT 1 FROM referrals WHERE invitee_id = ?", (invitee_id,)).fetchone()
    if has_ref:
        return False
        
    secret = antiabuse.tombstone_secret()
    if secret is not None:
        key = antiabuse.key_hash(invitee_id, secret)
        tombstone = conn.execute("SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?", (key,)).fetchone()
        if tombstone and tombstone["deleted_at"] + antiabuse.COOLDOWN_SECONDS > now:
            return False
            
    conn.execute(
        "INSERT INTO referrals (invitee_id, referrer_id, created_at) VALUES (?, ?, ?)",
        (invitee_id, referrer_id, now)
    )
    
    wallet.credit(conn, invitee_id, economy_config.REFERRAL_INVITEE_CHIPS)
    
    return True
