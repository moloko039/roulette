"""Ферма: покупка улучшений дохода и хранилища, данные экрана фермы."""

import time

import farm
import wallet
from levels import profile_level

from core.db_conn import _connect
from core.kernel import _accrue_conn, _register_player
from core.players import get_player


def buy_upgrade(telegram_id, request_id, kind, now=None, db_path=None):
    """Покупка улучшения (kind "income" или "storage") в одной транзакции BEGIN IMMEDIATE.

    Порядок: повтор по request_id; начисление накопленного по старой ставке и старому потолку; проверки
    (максимальный уровень, лимит по уровню профиля, достаточно ли фишек); списание через wallet.debit;
    повышение уровня (для дохода и players.rate); запись покупки. Бросает farm.MaxLevel, farm.LevelLocked,
    wallet.InsufficientFunds; при любой ошибке в базе ничего не меняется.
    """
    if kind not in farm.KINDS:
        raise ValueError("unknown kind")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # (а) повтор: тот же ответ, без списания (balance, как у spin, текущий)
            old = conn.execute(
                "SELECT kind, level_after, cost FROM farm_purchases WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                cur = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                result = {"kind": old["kind"], "level_after": old["level_after"], "cost": old["cost"],
                          "balance": cur["balance"] if cur else 0, "replayed": True}
                conn.execute("COMMIT")
                return result

            # (б) начисление по СТАРОЙ ставке и СТАРОМУ потолку: новые значения на прошлое не действуют
            _register_player(conn, telegram_id, now)
            _accrue_conn(conn, telegram_id, now)   # по старой ставке и старому потолку: дальше минуты считаются уже по новым
            row = conn.execute(
                "SELECT balance, rate, last_accrual, xp, income_level, storage_level "
                "FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            # (в) проверки по порядку
            level = row["income_level"] if kind == "income" else row["storage_level"]
            cost = farm.income_cost(level) if kind == "income" else farm.storage_cost(level)
            if cost is None:
                raise farm.MaxLevel()
            used = row["income_level"] + row["storage_level"]
            if used >= profile_level(row["xp"]):   # уровень профиля по опыту
                raise farm.LevelLocked(used + 1)

            # (г) списание, повышение уровня, запись покупки
            wallet.debit(conn, telegram_id, cost)   # wallet.InsufficientFunds, если фишек не хватает
            if kind == "income":
                conn.execute(
                    "UPDATE players SET income_level = ?, rate = ? WHERE telegram_id = ?",
                    (level + 1, farm.income_rate(level + 1), telegram_id),
                )
            else:
                conn.execute("UPDATE players SET storage_level = ? WHERE telegram_id = ?", (level + 1, telegram_id))
            conn.execute(
                "INSERT INTO farm_purchases (telegram_id, request_id, kind, level_after, cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, kind, level + 1, cost, now),
            )
            result = {"kind": kind, "level_after": level + 1, "cost": cost,
                      "balance": wallet.get_balance(conn, telegram_id), "replayed": False}
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def farm_status(telegram_id, now=None, db_path=None):
    """Данные экрана фермы (GET /api/farm). Баланс с начислением, как в /api/me."""
    player = get_player(telegram_id, now=now, db_path=db_path)
    return farm.status(player["balance"], player["xp"], player["total_staked"], player["income_level"],
                       player["storage_level"])
