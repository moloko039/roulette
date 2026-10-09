"""Привязка приглашённого к пригласившему (рефералка, E5, шаг 1): внутри транзакции создания игрока. Ядро, поэтому живёт в core/ (kernel его вызывает)."""
import antiabuse
import economy_config
import wallet
from roulette import MAX_SAFE_INT

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


def commission_in(conn, invitee_id, stake, paid, now):
    """Процент пригласившему от выигрыша казино (решение владельца 2026-10-09): исход одного раунда приглашённого, ВНУТРИ открытой транзакции раунда. Работает только в срок
    REFERRAL_COMMISSION_DAYS после квалификации. Чистый проигрыш приглашённого (ставка минус выплата) копится нарастающим итогом house_net; пригласивший получает
    REFERRAL_COMMISSION_PCT % от его максимума house_peak, не больше REFERRAL_COMMISSION_CAP за весь срок на одного приглашённого. Проигрыш, отыгранный обратно, обратно не отбирается,
    но и второй раз не оплачивается. Зачисление не выше MAX_SAFE_INT и никогда не ломает раунд. Возвращает выплаченное в этом раунде (для тестов)."""
    row = conn.execute("SELECT referrer_id, qualified_at, house_net, house_peak, commission_paid FROM referrals WHERE invitee_id = ?", (invitee_id,)).fetchone()
    if row is None or row["qualified_at"] is None or now >= row["qualified_at"] + economy_config.REFERRAL_COMMISSION_DAYS * 86400:
        return 0
    net = row["house_net"] + stake - paid
    peak = max(row["house_peak"], net)
    target = min(economy_config.REFERRAL_COMMISSION_CAP, peak * economy_config.REFERRAL_COMMISSION_PCT // 100)
    due = max(0, target - row["commission_paid"])
    credited = 0
    if due > 0 and conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (row["referrer_id"],)).fetchone() is not None:
        credited = min(due, MAX_SAFE_INT - wallet.get_balance(conn, row["referrer_id"]))
        if credited > 0:
            wallet.credit(conn, row["referrer_id"], credited)
    if net != row["house_net"] or peak != row["house_peak"] or due > 0:
        conn.execute("UPDATE referrals SET house_net = ?, house_peak = ?, commission_paid = commission_paid + ? WHERE invitee_id = ?", (net, peak, due, invitee_id))
    return credited

