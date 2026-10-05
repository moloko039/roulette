"""Переводы фишек между участниками беседы: отправка, статус и история (правила и константы в transfers.py)."""

import time
import hmac as _hmac

import transfers
import wallet
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _register_player
from core.members import MAX_CHAT_MEMBERS, _member_name, _touch_member


def _resolve_member(conn, chat_instance, ref):
    """Идентификатор участника беседы по метке или None. Ищет среди тех же MAX_CHAT_MEMBERS последних по активности
    участников, что отдаёт список «Кому перевести» (индекс chat_members_recent), поэтому стоимость не растёт с размером беседы.
    Вызывается до BEGIN IMMEDIATE: перебор HMAC не держит блокировку записи."""
    rows = conn.execute("SELECT telegram_id FROM chat_members WHERE chat_instance = ? "
                        "ORDER BY last_seen DESC, telegram_id LIMIT ?", (chat_instance, MAX_CHAT_MEMBERS)).fetchall()
    found = None
    for r in rows:   # без раннего выхода: время не зависит от места участника в списке
        if _hmac.compare_digest(transfers.member_ref(chat_instance, r["telegram_id"]), ref):
            found = r["telegram_id"]
    return found


def _transfer_daily_left(conn, telegram_id, now, owner_id=None):
    """Сколько отправитель ещё может отправить за скользящие 24 часа. Считается сумма списаний (amount, то есть вместе с комиссией).
    У владельца лимита нет: возвращается MAX_SAFE_INT."""
    if telegram_id == owner_id:
        return MAX_SAFE_INT
    used = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE sender = ? AND created_at > ?",
                        (telegram_id, now - transfers.DAY_SECONDS)).fetchone()[0]
    return max(0, transfers.SEND_DAILY_LIMIT - used)


def _transfer_response(conn, sender, amount, fee, now, replayed, owner_id=None):
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (sender,)).fetchone()
    return {"amount": amount, "fee": fee, "received": amount - fee, "balance": pl["balance"], "level": profile_level(pl["xp"]),
            "daily_left": _transfer_daily_left(conn, sender, now, owner_id), "replayed": replayed}


def transfer_send(sender, chat_instance, sender_name, member_ref, amount, request_id, owner_id=None, now=None, db_path=None):
    """Перевод фишек участнику той же беседы в одной транзакции BEGIN IMMEDIATE: debit отправителя на amount, credit
    получателя на amount - fee и credit комиссии владельцу (если он есть в базе и баланс не упрётся в потолок, иначе комиссия
    сгорает). Отправитель-владелец комиссию не платит; получатель-владелец получает amount. total_staked, XP и уровень не
    меняются ни у кого. Повтор с теми же параметрами и request_id возвращает сохранённый перевод (balance текущий).
    Ошибки: transfers.TransferError (code: no_chat, self_transfer, not_in_chat, level_too_low, account_too_new, cooldown,
    daily_limit, not_enough_staked, recipient_limit, request_conflict),
    wallet.InsufficientFunds. Получатель ищется до транзакции; внутри сначала дешёвые проверки отправителя, потом получателя."""
    if not transfers.valid_amount(amount) or not transfers.valid_member_ref(member_ref):
        raise ValueError("invalid transfer")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        resolved = None if chat_instance is None else _resolve_member(conn, chat_instance, member_ref)
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT recipient, amount, fee FROM transfers WHERE sender = ? AND request_id = ?",
                               (sender, request_id)).fetchone()
            if old is not None:
                same = chat_instance is not None and old["amount"] == amount and resolved == old["recipient"]
                if not same:
                    raise transfers.RequestConflict()
                result = _transfer_response(conn, sender, old["amount"], old["fee"], now, True, owner_id)
                conn.execute("COMMIT")
                return result
            if chat_instance is None:
                raise transfers.TransferError("no_chat")
            _register_player(conn, sender, now)
            _accrue_write(conn, sender, now)
            _touch_member(conn, chat_instance, sender, sender_name, now)
            me = conn.execute("SELECT created_at, xp, total_staked FROM players WHERE telegram_id = ?", (sender,)).fetchone()
            if profile_level(me["xp"]) < transfers.SENDER_MIN_LEVEL:
                raise transfers.TransferError("level_too_low")
            if now - me["created_at"] < transfers.MIN_ACCOUNT_AGE_HOURS * 3600:
                raise transfers.TransferError("account_too_new")
            if sender != owner_id and me["total_staked"] < transfers.MIN_STAKED_TO_SEND:
                raise transfers.TransferError("not_enough_staked")
            last = conn.execute("SELECT MAX(created_at) FROM transfers WHERE sender = ?", (sender,)).fetchone()[0]
            if last is not None and now - last < transfers.COOLDOWN_SECONDS:
                raise transfers.TransferError("cooldown", seconds=transfers.COOLDOWN_SECONDS - (now - last))
            if amount > _transfer_daily_left(conn, sender, now, owner_id):
                raise transfers.TransferError("daily_limit")
            recipient = resolved
            if recipient == sender:
                raise transfers.TransferError("self_transfer")
            # получатель мог выйти из беседы или удалить данные между поиском и транзакцией
            rec = None if recipient is None else conn.execute(
                "SELECT p.balance FROM players p JOIN chat_members m ON m.telegram_id = p.telegram_id "
                "WHERE p.telegram_id = ? AND m.chat_instance = ?", (recipient, chat_instance)).fetchone()
            if rec is None:   # нет в этой беседе или удалил данные
                raise transfers.TransferError("not_in_chat")
            fee = 0 if sender == owner_id else transfers.fee_for(amount)
            to_recipient = amount if recipient == owner_id else amount - fee   # владелец-получатель получает всю сумму
            if rec["balance"] + to_recipient > MAX_SAFE_INT:
                raise transfers.TransferError("recipient_limit")
            burned = False
            wallet.debit(conn, sender, amount)   # InsufficientFunds: ничего не меняется (откат)
            wallet.credit(conn, recipient, to_recipient)
            if fee > 0 and recipient != owner_id:
                owner_row = None if owner_id is None else conn.execute(
                    "SELECT balance FROM players WHERE telegram_id = ?", (owner_id,)).fetchone()
                if owner_row is not None and owner_row["balance"] + fee <= MAX_SAFE_INT:
                    wallet.credit(conn, owner_id, fee)
                else:
                    burned = True   # владельца нет в базе, не задан или упёрся в потолок: комиссия сгорает
            conn.execute("INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (?, ?, ?, ?, ?, ?)",
                         (sender, recipient, amount, fee, now, request_id))
            result = _transfer_response(conn, sender, amount, fee, now, False, owner_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.info("Перевод выполнен")
    if fee > 0 and recipient != owner_id:   # без сумм и идентификаторов
        logger.info("Комиссия за перевод не начислена (владелец недоступен)" if burned else "Комиссия за перевод начислена")
    return result


def transfer_status(telegram_id, owner_id=None, now=None, db_path=None):
    """Для GET /api/me: (transfer_limits, incoming_unseen). Два лёгких запроса по индексам."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        left = _transfer_daily_left(conn, telegram_id, now, owner_id)
        seen = conn.execute("SELECT transfers_seen_at FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount - fee), 0) FROM transfers WHERE recipient = ? AND created_at > ?",
                           (telegram_id, seen[0] if seen is not None else 0)).fetchone()
    finally:
        conn.close()
    limits = {"min": transfers.TRANSFER_MIN, "max": transfers.TRANSFER_MAX, "daily_left": left,
              "fee_percent": 0 if telegram_id == owner_id else transfers.FEE_PERCENT,
              "min_level": transfers.SENDER_MIN_LEVEL, "cooldown_seconds": transfers.COOLDOWN_SECONDS,
              "min_age_hours": transfers.MIN_ACCOUNT_AGE_HOURS, "min_staked": transfers.MIN_STAKED_TO_SEND,
              "unlimited": telegram_id == owner_id}   # владелец: без суточного лимита отправки
    return limits, {"count": row[0], "total": row[1]}


def transfer_history(telegram_id, limit=None, mark_seen=True, db_path=None):
    """Последние переводы игрока (отправленные и полученные): направление, имя второй стороны (как в рейтинге), сумма,
    комиссия, время. Входящие помечаются просмотренными (players.transfers_seen_at = время самого свежего входящего)."""
    limit = transfers.HISTORY_LIMIT if limit is None else limit
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT sender, recipient, amount, fee, created_at FROM transfers WHERE sender = ? OR recipient = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (telegram_id, telegram_id, limit)).fetchall()
            items = []
            for r in rows:
                out = r["sender"] == telegram_id
                items.append({"direction": "out" if out else "in", "name": _member_name(conn, r["recipient" if out else "sender"]),
                              "amount": r["amount"], "fee": r["fee"], "time": r["created_at"]})
            if mark_seen:
                newest = conn.execute("SELECT MAX(created_at) FROM transfers WHERE recipient = ?", (telegram_id,)).fetchone()[0]
                if newest is not None:
                    conn.execute("UPDATE players SET transfers_seen_at = MAX(transfers_seen_at, ?) WHERE telegram_id = ?",
                                 (newest, telegram_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return items
