"""Рефералка (E5): личный код приглашения и ссылка. Привязка приглашённого и бонус: core/referral.py (в транзакции создания игрока)."""
import secrets
import sqlite3
import time

from core.db_conn import _connect

_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
CODE_LENGTH = 10


def get_or_create_code(telegram_id, now=None, db_path=None):
    """Код приглашения игрока: создаётся лениво, непрозрачный (не Telegram-id и не производная от него), один на игрока.
    Гонка двух запросов одного игрока безопасна: проигравший читает код победителя."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        for _ in range(20):
            row = conn.execute("SELECT code FROM referral_codes WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if row is not None:
                return row["code"]
            code = "".join(secrets.choice(_CHARS) for _ in range(CODE_LENGTH))
            try:
                conn.execute("INSERT INTO referral_codes (telegram_id, code, created_at) VALUES (?, ?, ?)", (telegram_id, code, now))
                conn.commit()
                return code
            except sqlite3.IntegrityError:
                conn.rollback()      # либо код занят (пробуем другой), либо код игрока уже создан параллельным запросом (следующий круг его прочитает)
        raise RuntimeError("referral code generation failed")
    finally:
        conn.close()


def link_for(code, game_link):
    """Ссылка приглашения <ссылка приложения>?startapp=ref_<код> или None, если ссылка приложения не задана."""
    if not game_link or not code:
        return None
    return "%s?startapp=ref_%s" % (game_link, code)
