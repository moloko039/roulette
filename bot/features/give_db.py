"""Начисление владельцу себе (/give)."""

import wallet
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger


GIVE_MAX_AMOUNT = 100_000_000


class PlayerMissing(Exception):
    """У владельца ещё нет игрока в базе (игру не открывал): из команды игрок не создаётся."""


def give_owner(telegram_id, amount, db_path=None):
    """Начисляет amount (1..GIVE_MAX_AMOUNT) на баланс этого игрока через wallet.credit в одной транзакции BEGIN IMMEDIATE:
    не больше, чем помещается под MAX_SAFE_INT. total_staked, XP, уровень и метка минутного начисления не меняются, игрок не
    создаётся (PlayerMissing). Возвращает (зачислено, баланс)."""
    if type(amount) is not int or not 1 <= amount <= GIVE_MAX_AMOUNT:
        raise ValueError("amount")
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if row is None:
                raise PlayerMissing()
            part = min(amount, MAX_SAFE_INT - row["balance"])
            if part > 0:
                wallet.credit(conn, telegram_id, part)
            balance = wallet.get_balance(conn, telegram_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.info("Начисление владельцу выполнено")   # без суммы, баланса и идентификаторов
    return part, balance
