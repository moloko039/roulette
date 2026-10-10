"""Порядковый номер основателя (DESIGN.md раздел 6): таблица founder_numbers, номер выдаётся при первой вехе рефералки (referral_db.check_qualification)."""


def founder_no(conn, telegram_id):
    """Номер основателя игрока или None (первой вехи ещё нет). Только чтение."""
    row = conn.execute("SELECT no FROM founder_numbers WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return None if row is None else row[0]
