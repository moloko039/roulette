"""Очистка старых данных по срокам хранения."""

import time

import cosmetics
from antiabuse import COOLDOWN_SECONDS

from core.db_conn import _connect, logger


PURGE_BATCH = 1000


def purge_old_data(now=None, db_path=None, rounds_days=30, member_days=90, batch=PURGE_BATCH):
    """Удаляет старые данные пачками (каждая пачка отдельной короткой транзакцией).

    roulette_rounds старше rounds_days (не меньше 2 суток: на это время нужна защита от
    повторов request_id), chat_members с last_seen старше member_days (не меньше 7) и
    просроченные deletion_tombstones. Таблицу players не трогает никогда.
    Возвращает число удалённых строк по таблицам; в лог идут только количества.
    """
    if now is None:
        now = int(time.time())
    rounds_days = max(int(rounds_days), 2)
    member_days = max(int(member_days), 7)
    conn = _connect(db_path)
    deleted = {"roulette_rounds": 0, "farm_purchases": 0, "mines_games": 0, "mines_actions": 0,
               "keno_rounds": 0, "slot_rounds": 0, "blackjack_games": 0, "blackjack_actions": 0, "crash_games": 0, "crash_actions": 0, "hilo_games": 0, "hilo_actions": 0,
               "transfers": 0, "chat_members": 0,
               "deletion_tombstones": 0}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        def batches(sql, params):
            total = 0
            while True:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    n = conn.execute(sql, params).rowcount
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                total += n
                if n < batch:
                    return total
                time.sleep(0.01)  # между пачками даём пройти другим записям

        if "roulette_rounds" in present:
            deleted["roulette_rounds"] = batches(
                "DELETE FROM roulette_rounds WHERE rowid IN "
                "(SELECT rowid FROM roulette_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "farm_purchases" in present:  # тот же срок хранения, что у раундов
            deleted["farm_purchases"] = batches(
                "DELETE FROM farm_purchases WHERE rowid IN "
                "(SELECT rowid FROM farm_purchases WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "mines_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["mines_games"] = batches(
                "DELETE FROM mines_games WHERE id IN "
                "(SELECT id FROM mines_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "mines_actions" in present:
            deleted["mines_actions"] = batches(
                "DELETE FROM mines_actions WHERE rowid IN "
                "(SELECT rowid FROM mines_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "keno_rounds" in present:  # тот же срок хранения, что у раундов рулетки
            deleted["keno_rounds"] = batches(
                "DELETE FROM keno_rounds WHERE id IN (SELECT id FROM keno_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "slot_rounds" in present:  # тот же срок хранения, что у раундов рулетки
            deleted["slot_rounds"] = batches(
                "DELETE FROM slot_rounds WHERE id IN (SELECT id FROM slot_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "blackjack_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["blackjack_games"] = batches(
                "DELETE FROM blackjack_games WHERE id IN "
                "(SELECT id FROM blackjack_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "blackjack_actions" in present:
            deleted["blackjack_actions"] = batches(
                "DELETE FROM blackjack_actions WHERE rowid IN "
                "(SELECT rowid FROM blackjack_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "crash_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["crash_games"] = batches(
                "DELETE FROM crash_games WHERE id IN "
                "(SELECT id FROM crash_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "crash_actions" in present:
            deleted["crash_actions"] = batches(
                "DELETE FROM crash_actions WHERE rowid IN "
                "(SELECT rowid FROM crash_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "hilo_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["hilo_games"] = batches(
                "DELETE FROM hilo_games WHERE id IN "
                "(SELECT id FROM hilo_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "hilo_actions" in present:
            deleted["hilo_actions"] = batches(
                "DELETE FROM hilo_actions WHERE rowid IN "
                "(SELECT rowid FROM hilo_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "cosmetic_actions" in present:  # только журнал действий (идемпотентность); предметы и надетое не чистятся
            batches("DELETE FROM cosmetic_actions WHERE rowid IN "
                    "(SELECT rowid FROM cosmetic_actions WHERE created_at < ? LIMIT ?)", (now - rounds_days * 86400, batch))
        if "cosmetic_purchases" in present:  # журнал оплат Stars: своё (долгое) время хранения; после удаления данных игрока он остаётся до этого срока
            deleted["cosmetic_purchases"] = batches(
                "DELETE FROM cosmetic_purchases WHERE id IN (SELECT id FROM cosmetic_purchases WHERE created_at < ? LIMIT ?)",
                (now - cosmetics.PURCHASE_RETENTION_DAYS * 86400, batch))
        if "transfers" in present:  # тот же срок хранения, что у раундов
            deleted["transfers"] = batches(
                "DELETE FROM transfers WHERE id IN (SELECT id FROM transfers WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "chat_members" in present:
            deleted["chat_members"] = batches(
                "DELETE FROM chat_members WHERE rowid IN "
                "(SELECT rowid FROM chat_members WHERE last_seen < ? LIMIT ?)",
                (now - member_days * 86400, batch))
        if "deletion_tombstones" in present:
            deleted["deletion_tombstones"] = batches(
                "DELETE FROM deletion_tombstones WHERE rowid IN "
                "(SELECT rowid FROM deletion_tombstones WHERE deleted_at + ? <= ? LIMIT ?)",
                (COOLDOWN_SECONDS, now, batch))
    finally:
        conn.close()
    logger.info("Очистка старых данных: раунды=%d участники=%d надгробия=%d покупки=%d игры=%d кено=%d слот=%d блэкджек=%d краш=%d хило=%d переводы=%d",
                deleted["roulette_rounds"], deleted["chat_members"], deleted["deletion_tombstones"],
                deleted["farm_purchases"], deleted["mines_games"] + deleted["mines_actions"], deleted["keno_rounds"], deleted["slot_rounds"],
                deleted["blackjack_games"] + deleted["blackjack_actions"], deleted["crash_games"] + deleted["crash_actions"],
                deleted["hilo_games"] + deleted["hilo_actions"], deleted["transfers"])
    return deleted
