"""Разовое начисление владельца всем игрокам (/grantall)."""

import re
import time

import wallet
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger


GRANT_MAX_AMOUNT = 1_000_000
GRANT_ID_RE = re.compile(r"[A-Za-z0-9-]{1,32}")


class GrantExists(Exception):
    """Начисление с таким id уже было."""


def validate_grant(amount, grant_id):
    """Сумма: целое от 1 до GRANT_MAX_AMOUNT; id: латиница, цифры и дефис, до 32 символов. ValueError при ошибке."""
    if type(amount) is not int or not 1 <= amount <= GRANT_MAX_AMOUNT:
        raise ValueError("amount")
    if type(grant_id) is not str or not GRANT_ID_RE.fullmatch(grant_id):
        raise ValueError("grant_id")


def grant_preview(amount, grant_id, db_path=None):
    """(получателей, всего будет выдано) без изменений; GrantExists, если id уже использован.
    Игрок с балансом у потолка MAX_SAFE_INT получает только то, что помещается (на потолке не получает ничего)."""
    validate_grant(amount, grant_id)
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM admin_grants WHERE grant_id = ?", (grant_id,)).fetchone() is not None:
            raise GrantExists()
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(MIN(?, ? - balance)), 0) FROM players WHERE balance < ?",
            (amount, MAX_SAFE_INT, MAX_SAFE_INT)).fetchone()
        return row[0], row[1]
    finally:
        conn.close()


def grant_all(amount, grant_id, now=None, db_path=None):
    """Разовое начисление всем игрокам, которые есть в базе сейчас: ОДНА транзакция BEGIN IMMEDIATE, каждому credit через
    wallet (не больше, чем помещается под MAX_SAFE_INT), одна итоговая строка в admin_grants. total_staked, XP, уровень и
    метка минутного начисления не меняются. Любая ошибка откатывает всё. Возвращает (получили, выдано всего)."""
    validate_grant(amount, grant_id)
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM admin_grants WHERE grant_id = ?", (grant_id,)).fetchone() is not None:
                raise GrantExists()
            players = given = 0
            for row in conn.execute("SELECT telegram_id, balance FROM players ORDER BY telegram_id").fetchall():
                part = min(amount, MAX_SAFE_INT - row["balance"])
                if part > 0:
                    wallet.credit(conn, row["telegram_id"], part)
                    players += 1
                    given += part
            conn.execute("INSERT INTO admin_grants (grant_id, amount, created_at, players, total_given) VALUES (?, ?, ?, ?, ?)",
                         (grant_id, amount, now, players, given))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.info("Начисление выполнено: игроков=%d", players)   # без id, сумм и балансов
    return players, given
