"""Игрок: get_player находит или регистрирует игрока и подтягивает поминутное начисление (быстрый путь без записи)."""

import time

import economy
import farm

from core.db_conn import _connect
from core.kernel import _accrue_conn, _register_player


def get_player(telegram_id, now=None, db_path=None):
    """Находит игрока (или регистрирует), подтягивает поминутное начисление, возвращает словарь (accrued: зачислено этим вызовом).

    Быстрый путь: если игрок есть и полностью прошедших минут нет, только чтение, без BEGIN IMMEDIATE и без записи (GET /api/me
    не превращается в запись для всех). Иначе транзакция BEGIN IMMEDIATE: два одновременных запроса одного игрока выполняются
    по очереди, второй видит уже обновлённую метку и ничего не начисляет."""
    if now is None:
        now = int(time.time())
    cols = "balance, rate, last_accrual, accrual_acc, total_staked, xp, income_level, storage_level"
    accrued = 0
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT %s FROM players WHERE telegram_id = ?" % cols, (telegram_id,)).fetchone()
        due = row is None or economy.accrue_minutes(row["last_accrual"], row["accrual_acc"], now, row["rate"],
                                                    farm.storage_hours(row["storage_level"])) != (0, row["last_accrual"], row["accrual_acc"])
        if due:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _register_player(conn, telegram_id, now)   # новый игрок получает стартовый баланс (или 0 в период защиты)
                accrued = _accrue_conn(conn, telegram_id, now)
                row = conn.execute("SELECT %s FROM players WHERE telegram_id = ?" % cols, (telegram_id,)).fetchone()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()

    return {
        "telegram_id": telegram_id,
        "balance": row["balance"],
        "rate": row["rate"],
        "last_accrual": row["last_accrual"],
        "accrued": accrued,
        "total_staked": row["total_staked"],
        "xp": row["xp"],
        "income_level": row["income_level"],
        "storage_level": row["storage_level"],
    }
