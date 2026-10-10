"""Миграции и очистка устаревших данных при инициализации базы."""

import time

from antiabuse import COOLDOWN_SECONDS
from core.migrations import (
    _migrate_farm_levels,
    _migrate_last_played_at,
    _migrate_minute_accrual,
    _migrate_referral_commission,
    _migrate_total_staked,
    _migrate_transfers_seen,
    _migrate_xp,
)


def drop_anti_achievements(conn):
    """Удаление антидостижений («Клуб ×1.01») по решению владельца."""
    # удаление антидостижений («Клуб ×1.01») по решению владельца
    conn.execute("DROP TABLE IF EXISTS achievement_progress")
    conn.execute("DELETE FROM cosmetic_items WHERE item_code LIKE 'achv_%'")
    conn.execute("DELETE FROM cosmetic_equipped WHERE item_code LIKE 'achv_%'")


def cleanup_tombstones(conn, now=None):
    """Удаление записей «надгробий» старше срока защиты."""
    # записи старше срока защиты не нужны
    ts = int(time.time()) if now is None else int(now)
    conn.execute(
        "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?",
        (COOLDOWN_SECONDS, ts),
    )


def run_migrations(conn):
    """Выполняет миграции и очистку в исходном порядке."""
    drop_anti_achievements(conn)
    _migrate_total_staked(conn)
    _migrate_xp(conn)
    _migrate_farm_levels(conn)
    _migrate_transfers_seen(conn)
    _migrate_minute_accrual(conn)
    _migrate_last_played_at(conn)
    _migrate_referral_commission(conn)
    cleanup_tombstones(conn)
