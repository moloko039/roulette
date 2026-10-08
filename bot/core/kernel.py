"""ЯДРО: общие функции нескольких игр и переводов (минутное начисление, регистрация игрока, выплата, опыт, служебные отметки).

Все функции работают ВНУТРИ уже открытой транзакции вызвавшего кода (сами транзакцию не открывают):
  _accrue_conn / _accrue_write  подтягивают поминутное начисление дохода игрока
  _register_player              единственное место, где создаётся строка в players
  _credit_capped                зачисляет выплату через wallet.credit не выше MAX_SAFE_INT
  _record_best_win              личный рекорд выигрыша (единственное место записи player_best_win)
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
from core.referral import bind_referral_in


def _accrue_conn(conn, telegram_id, now):
    """Подтягивает начисление игрока за все полностью прошедшие минуты ВНУТРИ открытой транзакции: зачисление через wallet
    (не выше MAX_SAFE_INT, остаток при упоре не копится), метка и остаток пишутся там же. Если тиков нет, ничего не пишет.
    Возвращает, сколько фишек зачислено. Игрока нет: 0."""
    row = conn.execute(
        "SELECT rate, last_accrual, accrual_acc, storage_level FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        return 0
    from core.chat_bonus import get_chat_bonus
    bonus = get_chat_bonus(conn, telegram_id, now)
    eff_rate = row["rate"] * (100 + bonus["bonus_pct"]) // 100
    credit, new_last, new_acc = economy.accrue_minutes(
        row["last_accrual"], row["accrual_acc"], now, eff_rate, farm.storage_hours(row["storage_level"]))
    if credit == 0 and new_last == row["last_accrual"] and new_acc == row["accrual_acc"]:
        return 0
    paid = _credit_capped(conn, telegram_id, credit) if credit > 0 else 0
    conn.execute("UPDATE players SET last_accrual = ?, accrual_acc = ? WHERE telegram_id = ?", (new_last, new_acc, telegram_id))
    return paid


def _pending_accrual(row, now, conn=None, telegram_id=None):
    """Сколько было бы начислено сейчас (только расчёт, без записи): для рейтинга беседы."""
    eff_rate = row["rate"]
    if conn is not None and telegram_id is not None:
        from core.chat_bonus import get_chat_bonus
        eff_rate = row["rate"] * (100 + get_chat_bonus(conn, telegram_id, now)["bonus_pct"]) // 100
    return economy.accrue_minutes(row["last_accrual"], row["accrual_acc"], now, eff_rate,
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


def _register_player(conn, telegram_id, now, start_param=None):
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
    cooldown_hit = False
    if secret is not None:
        row = conn.execute(
            "SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?",
            (antiabuse.key_hash(telegram_id, secret),),
        ).fetchone()
        if row is not None and row["deleted_at"] + COOLDOWN_SECONDS > now:
            balance = 0
            cooldown_hit = True
    conn.execute(
        "INSERT OR IGNORE INTO players "
        "(telegram_id, balance, rate, last_accrual, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (telegram_id, balance, BASE_RATE, now // economy.TICK * economy.TICK, now),   # метка на границе минуты
    )
    if start_param and not cooldown_hit:
        # привязка не должна ломать создание игрока: при любой ошибке откатывается только она (запись referrals и бонус вместе)
        conn.execute("SAVEPOINT referral_bind")
        try:
            bind_referral_in(conn, telegram_id, start_param, now)
            conn.execute("RELEASE SAVEPOINT referral_bind")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT referral_bind")
            conn.execute("RELEASE SAVEPOINT referral_bind")


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


BEST_WIN_GAMES = ("roulette", "mines", "keno", "blackjack", "crash", "hilo", "slot")   # закрытый список кодов игр в рекордах


def _record_best_win(conn, telegram_id, game, stake, paid, now):
    """Единственное место записи личного рекорда: лучший ЧИСТЫЙ выигрыш за раунд (выплата минус вся ставка раунда) по всем играм.
    Пишется только если чистый выигрыш больше нуля и строго больше сохранённого (проигрыш, возврат ставки и ничья ничего не пишут;
    при равной сумме остаётся более ранний рекорд). Один оператор UPSERT в той же транзакции, что выплата; рекорды не читает ни одна игра,
    деньги, опыт и уровни от них не зависят. paid: фактически зачисленное, stake: все поставленные в раунде суммы."""
    net = min(paid, MAX_SAFE_INT) - stake
    if net <= 0:
        return False
    if game not in BEST_WIN_GAMES:
        raise ValueError("unknown game")
    conn.execute(
        "INSERT INTO player_best_win (telegram_id, game, net_amount, achieved_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET game = excluded.game, net_amount = excluded.net_amount, "
        "achieved_at = excluded.achieved_at WHERE excluded.net_amount > player_best_win.net_amount",
        (telegram_id, game, net, now))
    return True


def _add_xp(conn, telegram_id, amount):
    """Опыт игрока (в той же транзакции, что и результат), не выше MAX_SAFE_INT."""
    if amount > 0:
        conn.execute("UPDATE players SET xp = MIN(xp + ?, ?) WHERE telegram_id = ?", (amount, MAX_SAFE_INT, telegram_id))
