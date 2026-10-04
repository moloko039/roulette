"""ЯДРО: общие функции нескольких игр и переводов (минутное начисление, регистрация игрока, выплата, опыт, служебные отметки).

Все функции работают ВНУТРИ уже открытой транзакции вызвавшего кода (сами транзакцию не открывают):
  _accrue_conn / _accrue_write  подтягивают поминутное начисление дохода игрока
  _register_player              единственное место, где создаётся строка в players
  _credit_capped                зачисляет выплату через wallet.credit не выше MAX_SAFE_INT
  _add_xp                       прибавляет опыт игрока (не выше MAX_SAFE_INT)"""

import antiabuse
from antiabuse import COOLDOWN_SECONDS
import economy
from economy import START_BALANCE
from economy import BASE_RATE
import farm
import wallet
from roulette import MAX_SAFE_INT

from core.db_conn import _connect


def _accrue_conn(conn, telegram_id, now):
    """Подтягивает начисление игрока за все полностью прошедшие минуты ВНУТРИ открытой транзакции: зачисление через wallet
    (не выше MAX_SAFE_INT, остаток при упоре не копится), метка и остаток пишутся там же. Если тиков нет, ничего не пишет.
    Возвращает, сколько фишек зачислено. Игрока нет: 0."""
    row = conn.execute(
        "SELECT rate, last_accrual, accrual_acc, storage_level FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        return 0
    credit, new_last, new_acc = economy.accrue_minutes(
        row["last_accrual"], row["accrual_acc"], now, row["rate"], farm.storage_hours(row["storage_level"]))
    if credit == 0 and new_last == row["last_accrual"] and new_acc == row["accrual_acc"]:
        return 0
    paid = _credit_capped(conn, telegram_id, credit) if credit > 0 else 0
    conn.execute("UPDATE players SET last_accrual = ?, accrual_acc = ? WHERE telegram_id = ?", (new_last, new_acc, telegram_id))
    return paid


def _pending_accrual(row, now):
    """Сколько было бы начислено сейчас (только расчёт, без записи): для рейтинга беседы."""
    return economy.accrue_minutes(row["last_accrual"], row["accrual_acc"], now, row["rate"],
                                  farm.storage_hours(row["storage_level"]))[0]


def get_meta(key, db_path=None):
    """Значение служебной отметки или None."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM service_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_meta(key, value, db_path=None):
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO service_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    finally:
        conn.close()


def _register_player(conn, telegram_id, now):
    """Единственное место, где создаётся строка в players (get_player, spin_roulette, chat_top).

    Новый игрок получает START_BALANCE, кроме случая, когда его данные удалили меньше
    REGISTRATION_COOLDOWN_DAYS назад: тогда баланс 0, скорость и время начисления обычные.
    Если игрок уже есть, ничего не меняется. Без TOMBSTONE_SECRET защита не работает,
    поведение прежнее.
    """
    if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is not None:
        return
    balance = START_BALANCE
    secret = antiabuse.tombstone_secret()
    if secret is not None:
        row = conn.execute(
            "SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?",
            (antiabuse.key_hash(telegram_id, secret),),
        ).fetchone()
        if row is not None and row["deleted_at"] + COOLDOWN_SECONDS > now:
            balance = 0
    conn.execute(
        "INSERT OR IGNORE INTO players "
        "(telegram_id, balance, rate, last_accrual, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (telegram_id, balance, BASE_RATE, now // economy.TICK * economy.TICK, now),   # метка на границе минуты
    )


def _accrue_write(conn, telegram_id, now):
    """Подтягивает поминутное начисление внутри открытой транзакции (единая _accrue_conn); без тиков ничего не пишет."""
    return _accrue_conn(conn, telegram_id, now)


def _credit_capped(conn, telegram_id, amount):
    """Зачисляет min(amount, MAX_SAFE_INT - баланс) (на практике недостижимо). Возвращает зачисленное."""
    amount = min(amount, MAX_SAFE_INT - wallet.get_balance(conn, telegram_id))
    if amount > 0:
        wallet.credit(conn, telegram_id, amount)
        return amount
    return 0


def _add_xp(conn, telegram_id, amount):
    """Опыт игрока (в той же транзакции, что и результат), не выше MAX_SAFE_INT."""
    if amount > 0:
        conn.execute("UPDATE players SET xp = MIN(xp + ?, ?) WHERE telegram_id = ?", (amount, MAX_SAFE_INT, telegram_id))
