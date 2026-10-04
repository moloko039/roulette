"""Незавершённая игра игрока для /api/me."""

from core.db_conn import _connect


def active_game_of(telegram_id, db_path=None):
    """Название незавершённой игры игрока ("mines", "blackjack", "crash", "hilo") или None: один лёгкий запрос по
    индексам. Если активных несколько, берётся та, где действие было позже. Просроченные игры уже закрыты вызывающим кодом."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT game FROM ("
            "SELECT 'mines' AS game, updated_at AS ts FROM mines_games WHERE telegram_id = ? AND status = 'active' "
            "UNION ALL SELECT 'blackjack', updated_at FROM blackjack_games WHERE telegram_id = ? AND status = 'active' "
            "UNION ALL SELECT 'crash', created_at FROM crash_games WHERE telegram_id = ? AND status = 'active' "
            "UNION ALL SELECT 'hilo', updated_at FROM hilo_games WHERE telegram_id = ? AND status = 'active') "
            "ORDER BY ts DESC LIMIT 1", (telegram_id, telegram_id, telegram_id, telegram_id)).fetchone()
        return row["game"] if row is not None else None
    finally:
        conn.close()
