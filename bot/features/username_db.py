"""Юзернейм Telegram игрока: только чтобы владелец мог найти id по нику (/find). Один ник принадлежит одному игроку."""
import re
import time

from core.db_conn import _connect


USERNAME_RE = re.compile(r"[A-Za-z0-9_]{4,32}")


def normalize_username(value):
    """Ник без @ в нижнем регистре или None, если он не похож на юзернейм Telegram."""
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip("@")
    return value.lower() if USERNAME_RE.fullmatch(value) else None


def remember_username(telegram_id, username, now=None, db_path=None):
    """Запоминает ник игрока (повтор и смена безопасны). Без ника (None или неверный вид) прежняя запись удаляется.
    Если ник раньше принадлежал другому игроку, у того запись снимается: ник уникален."""
    name = normalize_username(username)
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if name is None:
                conn.execute("DELETE FROM player_usernames WHERE telegram_id = ?", (telegram_id,))
            else:
                conn.execute("DELETE FROM player_usernames WHERE username = ? AND telegram_id != ?", (name, telegram_id))
                conn.execute(
                    "INSERT INTO player_usernames (telegram_id, username, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username, updated_at = excluded.updated_at",
                    (telegram_id, name, now))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def find_by_username(username, db_path=None):
    """id игрока с таким ником (только если игрок есть в базе) или None."""
    name = normalize_username(username)
    if name is None:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT u.telegram_id FROM player_usernames u JOIN players p ON p.telegram_id = u.telegram_id WHERE u.username = ?",
            (name,)).fetchone()
        return row["telegram_id"] if row is not None else None
    finally:
        conn.close()
