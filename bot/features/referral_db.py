"""Рефералка (E5): личный код приглашения и ссылка. Привязка приглашённого и бонус: core/referral.py (в транзакции создания игрока)."""
import secrets
import sqlite3
import time

from core.db_conn import _connect
import wallet
import economy_config
import levels
from features.round_counts import ROUND_COUNT_SQL
from features.streak_db import _free_gems_used, _gems_used

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


def grant_referral_gems(conn, telegram_id, gems, reason, ref, now):
    """Кристаллы за рефералку внутри открытой транзакции: не больше остатка общего месячного потолка бесплатных кристаллов и не больше месячного потолка рефералки
    (иначе десять приглашений съели бы весь лимит серии входов). Возвращает, сколько начислено (может быть 0)."""
    left = min(economy_config.FREE_GEMS_MONTHLY_CAP - _free_gems_used(conn, telegram_id, now),
               economy_config.REFERRAL_GEMS_MONTHLY_CAP - _gems_used(conn, telegram_id, now, economy_config.REFERRAL_GEM_REASONS))
    gems = min(gems, max(0, left))
    if gems > 0:
        wallet.gems_credit(conn, telegram_id, gems, reason, ref, now)
    return gems


def check_qualification(invitee_id, now=None, db_path=None):
    """Проверяет квалификацию приглашённого (E5 шаг 2). Если условия выполнены (время, уровень, раунды) и награда
    ещё не выдана, устанавливает qualified_at = now и начисляет награду пригласившему. Одна транзакция."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT rowid, referrer_id, qualified_at FROM referrals WHERE invitee_id = ?", (invitee_id,)).fetchone()
            if row is None or row["qualified_at"] is not None or row["referrer_id"] == 0:
                conn.execute("COMMIT")
                return
            referrer_id = row["referrer_id"]
            ref_rowid = row[0]
            
            p_row = conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (referrer_id,)).fetchone()
            if p_row is None:
                conn.execute("COMMIT")
                return
            
            p_row = conn.execute("SELECT created_at, xp FROM players WHERE telegram_id = ?", (invitee_id,)).fetchone()
            if p_row is None:
                conn.execute("COMMIT")
                return
            created_at, xp = p_row["created_at"], p_row["xp"]
            
            if now - created_at < economy_config.REFERRAL_QUALIFY_HOURS * 3600:
                conn.execute("COMMIT")
                return
            
            if levels.profile_level(xp) < economy_config.REFERRAL_QUALIFY_LEVEL:
                conn.execute("COMMIT")
                return
            
            total_rounds = 0
            for query in ROUND_COUNT_SQL:
                total_rounds += conn.execute(query, (invitee_id,)).fetchone()[0]
                if total_rounds >= economy_config.REFERRAL_QUALIFY_ROUNDS:
                    break

            if total_rounds < economy_config.REFERRAL_QUALIFY_ROUNDS:
                conn.execute("COMMIT")
                return
            
            changed = conn.execute("UPDATE referrals SET qualified_at = ? WHERE invitee_id = ? AND qualified_at IS NULL", (now, invitee_id)).rowcount
            if changed == 0:
                conn.execute("COMMIT")
                return
            
            wallet.credit(conn, referrer_id, economy_config.REFERRAL_INVITER_CHIPS)
            
            grant_referral_gems(conn, referrer_id, economy_config.REFERRAL_INVITER_GEMS, "referral_reward", f"invitee-{ref_rowid}", now)
            
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def is_unqualified_invitee(telegram_id, db_path=None):
    """Быстрая проверка одним запросом: игрок приглашён и квалификация ещё не засчитана (нужна ли ленивая проверка при входе)."""
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT 1 FROM referrals WHERE invitee_id = ? AND qualified_at IS NULL", (telegram_id,)).fetchone() is not None
    finally:
        conn.close()
