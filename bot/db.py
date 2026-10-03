import os
import sqlite3
import time

from economy import START_BALANCE, BASE_RATE, accrue

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.db")


def _connect(db_path):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    # транзакциями управляем вручную (BEGIN IMMEDIATE), а не автоматически
    conn.isolation_level = None
    return conn


def init_db(db_path=None):
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                telegram_id  INTEGER PRIMARY KEY,
                balance      INTEGER NOT NULL,
                rate         INTEGER NOT NULL,
                last_accrual INTEGER NOT NULL,
                created_at   INTEGER NOT NULL
            )
            """
        )
    finally:
        conn.close()


def get_player(telegram_id, now=None, db_path=None):
    """Находит игрока (или регистрирует), начисляет фишки, возвращает словарь."""
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        # IMMEDIATE сразу берёт блокировку на запись: два одновременных запроса
        # одного игрока выполняются по очереди, а не читают одно и то же старое значение
        conn.execute("BEGIN IMMEDIATE")
        try:
            # новый игрок получает стартовый баланс; существующего не трогаем
            conn.execute(
                "INSERT OR IGNORE INTO players "
                "(telegram_id, balance, rate, last_accrual, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (telegram_id, START_BALANCE, BASE_RATE, now, now),
            )
            row = conn.execute(
                "SELECT balance, rate, last_accrual FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            earned, new_last = accrue(row["last_accrual"], now, row["rate"])
            balance = row["balance"] + earned

            if earned or new_last != row["last_accrual"]:
                conn.execute(
                    "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                    (balance, new_last, telegram_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return {
        "telegram_id": telegram_id,
        "balance": balance,
        "rate": row["rate"],
        "last_accrual": new_last,
    }
