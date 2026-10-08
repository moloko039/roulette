"""Привязка приглашённого к пригласившему (рефералка, E5, шаг 1): внутри транзакции создания игрока. Ядро, поэтому живёт в core/ (kernel его вызывает)."""
import antiabuse
import economy_config
import wallet

PREFIX = "ref_"


def bind_referral_in(conn, invitee_id, start_param, now):
    """Привязывает нового игрока по коду из start_param (ref_<код>) и один раз начисляет ему стартовый бонус. ВНУТРИ открытой транзакции.
    Условия: код существует; пригласивший не сам игрок; привязки ещё нет; игрок не под блокировкой повторной регистрации. Возвращает True, если привязал."""
    if type(start_param) is not str or not start_param.startswith(PREFIX):
        return False
    code = start_param[len(PREFIX):]
    row = conn.execute("SELECT telegram_id FROM referral_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        return False
    referrer_id = row["telegram_id"]
    if referrer_id == invitee_id:
        return False
    if conn.execute("SELECT 1 FROM referrals WHERE invitee_id = ?", (invitee_id,)).fetchone() is not None:
        return False
    secret = antiabuse.tombstone_secret()
    if secret is not None:
        tomb = conn.execute("SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?",
                            (antiabuse.key_hash(invitee_id, secret),)).fetchone()
        if tomb is not None and tomb["deleted_at"] + antiabuse.COOLDOWN_SECONDS > now:
            return False
    conn.execute("INSERT INTO referrals (invitee_id, referrer_id, created_at) VALUES (?, ?, ?)", (invitee_id, referrer_id, now))
    wallet.credit(conn, invitee_id, economy_config.REFERRAL_INVITEE_CHIPS)
    return True
