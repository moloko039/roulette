"""Прежний краш (партия на игрока, таблица crash_games) закрыт 2026-10-09: новых партий нет, их заменил живой краш (crash_live_db).

Здесь остаётся одно: партия, открытая на момент выкладки, закрывается возвратом ставки (раунд без исхода, опыта и рекорда нет). Возврат делает wallet, партия закрывается один раз
(условие status = 'active'), ставка снимается из total_staked, потому что раунд не состоялся. Таблица и закрытые партии живут до очистки по сроку хранения."""
import logging
import time

import wallet

from core.db_conn import _connect
from games.round_common import run_tx

logger = logging.getLogger("depnaya")
REFUND_BATCH = 200


def _refund_in(conn, game_id, now):
    row = conn.execute("SELECT telegram_id, bet FROM crash_games WHERE id = ? AND status = 'active'", (game_id,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE crash_games SET status = 'finished', result = 'refund', mult_x100 = 100, payout = bet, auto = 1, finished_at = ? "
                 "WHERE id = ? AND status = 'active'", (now, game_id))
    wallet.credit(conn, row["telegram_id"], row["bet"])
    conn.execute("UPDATE players SET total_staked = MAX(total_staked - ?, 0) WHERE telegram_id = ?", (row["bet"], row["telegram_id"]))
    return True


def refund_legacy_crash(telegram_id=None, now=None, db_path=None, batch=REFUND_BATCH):
    """Возвращает ставки открытых партий прежнего краша (одного игрока или всех, не больше batch за проход). Возвращает число закрытых; без таблицы или открытых партий только читает."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'crash_games'").fetchone() is None:
            return 0
        if telegram_id is None:
            ids = [r["id"] for r in conn.execute("SELECT id FROM crash_games WHERE status = 'active' LIMIT ?", (batch,))]
        else:
            ids = [r["id"] for r in conn.execute("SELECT id FROM crash_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,))]
    finally:
        conn.close()
    closed = 0
    for game_id in ids:
        conn = _connect(db_path)
        try:
            closed += 1 if run_tx(conn, lambda c: _refund_in(c, game_id, now)) else 0
        except Exception as exc:    # потолок баланса и подобное: партия остаётся открытой, повторится в следующий проход
            logger.error("Возврат ставки прежнего краша не выполнен: %s", type(exc).__name__)
        finally:
            conn.close()
    if closed:
        logger.info("Возвращено ставок прежнего краша: %d", closed)
    return closed
