"""Покупка фишек за кристаллы (план экономики, этап E6): пакеты в часах фермы игрока.

Одна транзакция BEGIN IMMEDIATE: повтор по (игрок, request_id), суточный лимит, начисление дохода, списание кристаллов (wallet.gems_debit, причина chip_purchase),
зачисление фишек (wallet.credit), запись в chip_purchases. Опыт, total_staked и уровень не меняются. Фишки купить можно, продать и вывести нельзя."""
import time

import economy_config
import wallet
from roulette import MAX_SAFE_INT, BalanceLimit, RequestConflict

from core.db_conn import _connect
from core.kernel import _accrue_write, _register_player

DAY_SECONDS = 86400


class ChipsError(Exception):
    code = "chips_error"


class UnknownChipPack(ChipsError):
    code = "unknown_pack"


class DailyLimit(ChipsError):
    code = "daily_limit"


def pack(pack_code):
    """(кристаллы, часы) пакета или UnknownChipPack."""
    entry = economy_config.CHIP_PACKS.get(pack_code) if type(pack_code) is str else None
    if entry is None:
        raise UnknownChipPack()
    return entry


def chips_in_pack(hours, rate):
    """Сколько фишек даёт пакет в часах фермы при доходе rate фишек в час: не меньше часов * CHIP_PACK_MIN_RATE, не больше CHIP_PACK_MAX_CHIPS."""
    per_hour = max(int(rate), economy_config.CHIP_PACK_MIN_RATE)
    return min(hours * per_hour, economy_config.CHIP_PACK_MAX_CHIPS)


def _daily_left(conn, telegram_id, now):
    used = conn.execute("SELECT COUNT(*) FROM chip_purchases WHERE telegram_id = ? AND created_at > ?", (telegram_id, now - DAY_SECONDS)).fetchone()[0]
    return max(0, economy_config.CHIP_PACK_DAILY_LIMIT - used)


def chip_packs_state(telegram_id, now=None, db_path=None):
    """Данные страницы «Фишки» (только чтение): пакеты с числом фишек для этого игрока, кристаллы, фишки, сколько покупок осталось сегодня."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT rate, balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
        rate, balance = (row["rate"], row["balance"]) if row is not None else (economy_config.CHIP_PACK_MIN_RATE, 0)
        packs = [{"code": c, "gems": g, "hours": h, "chips": chips_in_pack(h, rate)}
                 for c, (g, h) in sorted(economy_config.CHIP_PACKS.items(), key=lambda kv: kv[1][0])]
        return {"packs": packs, "gems": wallet.gems_balance(conn, telegram_id), "balance": balance, "daily_left": _daily_left(conn, telegram_id, now)}
    finally:
        conn.close()


def buy_chip_pack(telegram_id, request_id, pack_code, now=None, db_path=None):
    """Покупка пакета. Ошибки: UnknownChipPack, RequestConflict (тот же request_id с другим пакетом), DailyLimit, wallet.InsufficientGems,
    roulette.BalanceLimit (фишки не поместились бы под MAX_SAFE_INT: кристаллы не списываются). Повтор того же request_id и пакета отдаёт то же без списания."""
    gems, hours = pack(pack_code)
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT pack_code, gems, chips FROM chip_purchases WHERE telegram_id = ? AND request_id = ?", (telegram_id, request_id)).fetchone()
            if old is not None:
                if old["pack_code"] != pack_code:
                    raise RequestConflict()
                result = {"pack_code": pack_code, "gems_spent": old["gems"], "chips": old["chips"], "balance": wallet.get_balance(conn, telegram_id),
                          "gems": wallet.gems_balance(conn, telegram_id), "daily_left": _daily_left(conn, telegram_id, now), "replayed": True}
                conn.execute("COMMIT")
                return result
            _register_player(conn, telegram_id, now)
            if _daily_left(conn, telegram_id, now) <= 0:
                raise DailyLimit()
            _accrue_write(conn, telegram_id, now)
            rate = conn.execute("SELECT rate FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()["rate"]
            chips = chips_in_pack(hours, rate)
            if wallet.get_balance(conn, telegram_id) + chips > MAX_SAFE_INT:
                raise BalanceLimit()
            wallet.gems_debit(conn, telegram_id, gems, "chip_purchase", request_id, now)
            balance = wallet.credit(conn, telegram_id, chips)
            conn.execute("INSERT INTO chip_purchases (telegram_id, request_id, pack_code, gems, chips, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (telegram_id, request_id, pack_code, gems, chips, now))
            result = {"pack_code": pack_code, "gems_spent": gems, "chips": chips, "balance": balance, "gems": wallet.gems_balance(conn, telegram_id),
                      "daily_left": _daily_left(conn, telegram_id, now), "replayed": False}
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
