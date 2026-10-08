"""Миграции существующих баз (идемпотентные, каждая одной транзакцией), включая переход на поминутное начисление."""

import time

import economy
from economy import accrue
import farm
from roulette import MAX_SAFE_INT


def _migrate_total_staked(conn):
    """Добавляет players.total_staked в старую базу (идемпотентно, одной транзакцией).

    Начальное значение игрока = сумма stake_total из roulette_rounds. Учитывается только то, что ещё
    хранится: раунды старше срока хранения уже удалены очисткой, поэтому у старых игроков значение
    может быть меньше фактической суммы ставок. Дальше счётчик растёт в spin_roulette и очисткой
    раундов не уменьшается.
    """
    def has_column():
        return any(r["name"] == "total_staked" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():  # повторная проверка внутри транзакции
            conn.execute("ALTER TABLE players ADD COLUMN total_staked INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "UPDATE players SET total_staked = MIN(COALESCE("
                "(SELECT SUM(r.stake_total) FROM roulette_rounds r WHERE r.telegram_id = players.telegram_id), 0), ?)",
                (MAX_SAFE_INT,),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_xp(conn):
    """Добавляет players.xp в старую базу (идемпотентно, одной транзакцией).

    У существующих игроков xp = total_staked, чтобы уровни профиля не изменились; дальше опыт растёт по правилам
    xp.py. Новые игроки (и после удаления данных) начинают с 0."""
    def has_column():
        return any(r["name"] == "xp" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():  # повторная проверка внутри транзакции
            conn.execute("ALTER TABLE players ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE players SET xp = total_staked")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_farm_levels(conn):
    """Добавляет players.income_level и players.storage_level в старую базу (идемпотентно).

    Новые столбцы 0: потолок накопления прежний (30 часов). Столбец rate не трогаем: у старых игроков он
    уже равен базовой ставке, а при покупке дохода обновляется до farm.income_rate(уровень)."""
    def columns():
        return {r["name"] for r in conn.execute("PRAGMA table_info(players)")}

    if {"income_level", "storage_level"} <= columns():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        present = columns()  # повторная проверка внутри транзакции
        if "income_level" not in present:
            conn.execute("ALTER TABLE players ADD COLUMN income_level INTEGER NOT NULL DEFAULT 0")
        if "storage_level" not in present:
            conn.execute("ALTER TABLE players ADD COLUMN storage_level INTEGER NOT NULL DEFAULT 0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_transfers_seen(conn):
    """Добавляет players.transfers_seen_at в старую базу (идемпотентно, одной транзакцией): время самого свежего входящего
    перевода, о котором получатель уже знает."""
    def has_column():
        return any(r["name"] == "transfers_seen_at" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():
            conn.execute("ALTER TABLE players ADD COLUMN transfers_seen_at INTEGER NOT NULL DEFAULT 0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_minute_accrual(conn, now=None):
    """Переход на поминутное начисление (идемпотентно, ОДНОЙ транзакцией BEGIN IMMEDIATE вместе с добавлением столбца).

    Признак «перенос выполнен» это сам столбец players.accrual_acc: он добавляется и игроки переносятся в одной транзакции,
    при сбое откатывается всё. Новая база уже создаётся со столбцом (игроков нет, переносить нечего), повторный запуск ничего
    не делает. Каждому игроку один раз: целые часы по СТАРЫМ правилам (economy.accrue с его потолком; лишнее после потолка
    сгорает, как и раньше), затем неполный остаток часа в минутах по новым (accrue_minutes): метка становится границей минуты
    «сейчас», остаток acc сохраняет долю фишки. Итог равен старой логике, потерь и лишнего нет (до одной фишки от округления;
    секунды меньше минуты в остатке не считаются). Метка в будущем ничего не даёт."""
    if now is None:
        now = int(time.time())
    if "accrual_acc" in {r["name"] for r in conn.execute("PRAGMA table_info(players)")}:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "accrual_acc" not in {r["name"] for r in conn.execute("PRAGMA table_info(players)")}:   # повторная проверка внутри
            conn.execute("ALTER TABLE players ADD COLUMN accrual_acc INTEGER NOT NULL DEFAULT 0")
            for row in conn.execute("SELECT telegram_id, balance, rate, last_accrual, storage_level FROM players").fetchall():
                cap = farm.storage_hours(row["storage_level"])
                earned, last = accrue(row["last_accrual"], now, row["rate"], max_hours=cap)   # прежние правила: целые часы
                credit, new_last, new_acc = economy.accrue_minutes(last, 0, now, row["rate"], cap)   # неполный час по минутам
                if new_last <= now:
                    new_last = new_last // economy.TICK * economy.TICK   # метка на границе минуты (не больше одного тика один раз)
                paid = max(0, min(earned + credit, MAX_SAFE_INT - row["balance"]))
                conn.execute("UPDATE players SET balance = balance + ?, last_accrual = ?, accrual_acc = ? WHERE telegram_id = ?",
                             (paid, new_last, new_acc, row["telegram_id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_last_played_at(conn):
    """Добавляет players.last_played_at в старую базу (идемпотентно)."""
    def columns():
        return {r["name"] for r in conn.execute("PRAGMA table_info(players)")}

    if "last_played_at" in columns():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "last_played_at" not in columns():
            conn.execute("ALTER TABLE players ADD COLUMN last_played_at INTEGER NOT NULL DEFAULT 0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
