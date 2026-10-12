"""Поиск игрока для панели владельца (/player, /find): по id, юзернейму Telegram (точно или по началу) и по имени в играх (часть имени).
Только чтение; в ответ идут id, имя, ник и балансы, чтобы владелец убедился, что выбрал того игрока."""
from core.db_conn import _connect
from features.username_db import normalize_username

SEARCH_LIMIT = 6
NAME_SCAN_LIMIT = 20000


def _card(conn, telegram_id):
    row = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        return None
    name = conn.execute("SELECT first_name FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1", (telegram_id,)).fetchone()
    nick = conn.execute("SELECT username FROM player_usernames WHERE telegram_id = ?", (telegram_id,)).fetchone()
    gems = conn.execute("SELECT gems FROM gem_balances WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return {"id": telegram_id, "name": name["first_name"] if name else None, "username": nick["username"] if nick else None,
            "balance": row["balance"], "gems": gems["gems"] if gems else 0}


def player_card(telegram_id, db_path=None):
    conn = _connect(db_path)
    try:
        return _card(conn, telegram_id)
    finally:
        conn.close()


def search_players(query, db_path=None):
    """До SEARCH_LIMIT игроков, подходящих под запрос: число (id), @ник или часть имени. Пустой запрос и неизвестные игроки дают пустой список."""
    query = (query or "").strip()
    if not query:
        return []
    conn = _connect(db_path)
    try:
        found = []

        def add(tid):
            if tid not in found and len(found) < SEARCH_LIMIT:
                found.append(tid)

        if query.isdigit() and len(query) <= 15:
            add(int(query))
        nick = normalize_username(query)
        if nick is not None:
            for r in conn.execute("SELECT telegram_id FROM player_usernames WHERE username = ?", (nick,)):
                add(r["telegram_id"])
        prefix = query.lstrip("@").lower()
        if prefix and prefix.replace("_", "").isalnum() and prefix.isascii():
            for r in conn.execute("SELECT telegram_id FROM player_usernames WHERE username LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT 20",
                                  (prefix.replace("\\", "").replace("%", "").replace("_", "\\_") + "%",)):
                add(r["telegram_id"])
        needle = query.lstrip("@").casefold()
        seen = set()
        for r in conn.execute("SELECT telegram_id, first_name FROM chat_members ORDER BY last_seen DESC LIMIT ?", (NAME_SCAN_LIMIT,)):
            if r["telegram_id"] in seen:
                continue
            seen.add(r["telegram_id"])
            if needle in r["first_name"].casefold():
                add(r["telegram_id"])
        cards = [_card(conn, tid) for tid in found]
        return [c for c in cards if c is not None]
    finally:
        conn.close()
