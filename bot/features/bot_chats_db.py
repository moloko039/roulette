"""Группы, где состоит бот (только chat_id), для объявления /grantall."""

import time

from core.db_conn import _connect


def chat_register(chat_id, now=None, db_path=None):
    """Запомнить группу (только число chat_id и время); повтор обновляет время."""
    if type(chat_id) is not int or isinstance(chat_id, bool):
        raise ValueError("chat_id")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO bot_chats (chat_id, seen_at) VALUES (?, ?) "
                     "ON CONFLICT(chat_id) DO UPDATE SET seen_at = excluded.seen_at", (chat_id, now))
    finally:
        conn.close()


def chat_forget(chat_id, db_path=None):
    """Забыть группу (бота удалили или чат недоступен). True, если запись была."""
    conn = _connect(db_path)
    try:
        return conn.execute("DELETE FROM bot_chats WHERE chat_id = ?", (chat_id,)).rowcount > 0
    finally:
        conn.close()


def chat_ids(db_path=None):
    conn = _connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT chat_id FROM bot_chats ORDER BY chat_id")]
    finally:
        conn.close()
