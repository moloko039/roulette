"""Участники бесед: очистка имён, запись участника (_touch_member), имя участника; общие для рейтинга, переводов и выгрузки данных."""

import unicodedata


def _member_name(conn, telegram_id):
    row = conn.execute("SELECT first_name FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1",
                       (telegram_id,)).fetchone()
    return row["first_name"] if row is not None else DEFAULT_NAME


NAME_MAX = 32
DEFAULT_NAME = "Игрок"
TOUCH_INTERVAL = 60       # запись участника обновляется не чаще раза в 60 секунд
# Ограничение для больших чатов: в рейтинге учитываются не больше 1000 участников одной
# беседы, самые недавно активные (по last_seen). Остальные в рейтинг не попадают, и
# me.total считается по этим же участникам, поэтому ранг и total всегда согласованы.
MAX_CHAT_MEMBERS = 1000
# управляющие направления текста (могут перевернуть соседний текст на экране)
_BIDI = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_name(name):
    """Убирает управляющие символы, обрезает пробелы по краям и длину до 32 символов."""
    if not isinstance(name, str):
        return DEFAULT_NAME
    kept = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cs") and ch not in _BIDI)
    kept = kept.strip()[:NAME_MAX].strip()
    return kept or DEFAULT_NAME


def _touch_member(conn, chat_instance, telegram_id, first_name, now):
    """Запись или обновление участника внутри открытой транзакции. True, если база изменена."""
    name = clean_name(first_name)
    row = conn.execute(
        "SELECT last_seen FROM chat_members WHERE chat_instance = ? AND telegram_id = ?",
        (chat_instance, telegram_id),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_instance, telegram_id, name, now, now),
        )
        return True
    if now - row["last_seen"] < TOUCH_INTERVAL:
        return False
    conn.execute(
        "UPDATE chat_members SET first_name = ?, last_seen = ? WHERE chat_instance = ? AND telegram_id = ?",
        (name, now, chat_instance, telegram_id),
    )
    return True
