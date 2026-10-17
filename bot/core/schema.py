"""Схема базы: init_db создаёт таблицы и индексы и вызывает миграции."""

import time

from antiabuse import COOLDOWN_SECONDS

from core.db_conn import _apply_journal_mode, _connect, _resolve_path
from core.migrations import (
    _migrate_best_win_mult,
    _migrate_farm_levels,
    _migrate_last_played_at,
    _migrate_minute_accrual,
    _migrate_referral_commission,
    _migrate_total_staked,
    _migrate_transfers_seen,
    _migrate_xp,
)
from core.schema_tables import (
    create_chat_tables,
    create_economy_tables,
    create_game_tables,
    create_player_tables,
    create_referral_tables,
)
from core.schema_migrations import (
    cleanup_tombstones,
    drop_anti_achievements,
)


def init_db(db_path=None):
    conn = _connect(db_path)
    try:
        _apply_journal_mode(conn, _resolve_path(db_path))
        create_player_tables(conn)
        create_game_tables(conn)
        create_chat_tables(conn)
        create_economy_tables(conn)
        create_referral_tables(conn)
        drop_anti_achievements(conn)
        _migrate_total_staked(conn)
        _migrate_xp(conn)
        _migrate_farm_levels(conn)
        _migrate_transfers_seen(conn)
        _migrate_minute_accrual(conn)
        _migrate_last_played_at(conn)
        _migrate_referral_commission(conn)
        _migrate_best_win_mult(conn)
        cleanup_tombstones(conn)
    finally:
        conn.close()
