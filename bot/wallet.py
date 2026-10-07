"""Кошелёк: единственное место, где игровые списания и выплаты меняют players.balance, а кристаллы меняют gem_balances и gems_ledger.

Функции работают ВНУТРИ уже открытой транзакции: соединение передаётся аргументом, транзакцию они
не открывают, не закрывают и не откатывают (это делает вызывающий код, например spin_roulette).
Минутное начисление дохода (db._accrue_conn) зачисляет через wallet.credit; единственная прямая правка баланса вне wallet это
разовая миграция db._migrate_minute_accrual.
"""
import economy_config
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds as _InsufficientFunds


class InsufficientFunds(_InsufficientFunds):
    """Списание больше баланса. Наследует ошибку рулетки: API отвечает на неё как раньше (insufficient_funds)."""


class BalanceLimitExceeded(BalanceLimit):
    """Баланс после начисления превысил бы MAX_SAFE_INT. API отвечает как раньше (balance_limit)."""


class PlayerNotFound(LookupError):
    """Игрока нет в таблице players."""


def _check_amount(amount):
    # bool является подклассом int, но суммой быть не может
    if type(amount) is not int or amount <= 0:
        raise ValueError("amount must be a positive integer")


def get_balance(conn, telegram_id):
    """Текущий баланс игрока. PlayerNotFound, если игрока нет."""
    row = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        raise PlayerNotFound()
    return row[0]


def debit(conn, telegram_id, amount):
    """Списывает amount (целое > 0). Не уводит баланс ниже нуля (InsufficientFunds). Возвращает новый баланс."""
    _check_amount(amount)
    if amount > MAX_SAFE_INT:  # такого баланса быть не может
        get_balance(conn, telegram_id)
        raise InsufficientFunds()
    # проверка и списание одним запросом: баланс не может уйти ниже нуля
    changed = conn.execute(
        "UPDATE players SET balance = balance - ? WHERE telegram_id = ? AND balance >= ?",
        (amount, telegram_id, amount),
    ).rowcount
    if changed == 0:
        get_balance(conn, telegram_id)  # PlayerNotFound, если игрока нет
        raise InsufficientFunds()
    return get_balance(conn, telegram_id)


def credit(conn, telegram_id, amount):
    """Начисляет amount (целое > 0). Баланс не выше MAX_SAFE_INT (BalanceLimitExceeded). Возвращает новый баланс."""
    _check_amount(amount)
    if amount > MAX_SAFE_INT:
        get_balance(conn, telegram_id)
        raise BalanceLimitExceeded()
    changed = conn.execute(
        "UPDATE players SET balance = balance + ? WHERE telegram_id = ? AND balance <= ?",
        (amount, telegram_id, MAX_SAFE_INT - amount),
    ).rowcount
    if changed == 0:
        get_balance(conn, telegram_id)
        raise BalanceLimitExceeded()
    return get_balance(conn, telegram_id)


# ---------- кристаллы ----------
class InsufficientGems(Exception):
    """Списание больше баланса кристаллов."""


class GemsLimitExceeded(Exception):
    """Баланс кристаллов после начисления превысил бы economy_config.GEMS_MAX_BALANCE."""


def _check_gem_reason(reason, ref):
    if reason not in economy_config.GEM_REASONS:
        raise ValueError("unknown gems reason")
    if ref is not None and (type(ref) is not str or not 1 <= len(ref) <= 256):
        raise ValueError("bad gems ref")


def gems_balance(conn, telegram_id):
    """Баланс кристаллов (0, если записи нет)."""
    row = conn.execute("SELECT gems FROM gem_balances WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return 0 if row is None else row[0]


def gems_credit(conn, telegram_id, amount, reason, ref, now):
    """Начисляет кристаллы и пишет строку журнала. ВНУТРИ открытой транзакции. reason из economy_config.GEM_REASONS (класс source).
    Одинаковая тройка (игрок, reason, ref) второй раз не проходит (sqlite3.IntegrityError): так платёж и выдача идемпотентны. Возвращает новый баланс."""
    _check_amount(amount)
    _check_gem_reason(reason, ref)
    if economy_config.GEM_REASONS[reason] != "source":
        raise ValueError("reason is not a source")
    if amount > economy_config.GEMS_MAX_BALANCE or gems_balance(conn, telegram_id) + amount > economy_config.GEMS_MAX_BALANCE:
        raise GemsLimitExceeded()
    conn.execute("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, ?, ?, ?, ?)", (telegram_id, amount, reason, ref, now))
    conn.execute("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, ?) ON CONFLICT(telegram_id) DO UPDATE SET gems = gems + excluded.gems", (telegram_id, amount))
    return gems_balance(conn, telegram_id)


def gems_debit(conn, telegram_id, amount, reason, ref, now):
    """Списывает кристаллы (reason класса sink) и пишет строку журнала (delta отрицательная). Не уводит баланс ниже нуля (InsufficientGems).
    ВНУТРИ открытой транзакции. Возвращает новый баланс."""
    _check_amount(amount)
    _check_gem_reason(reason, ref)
    if economy_config.GEM_REASONS[reason] != "sink":
        raise ValueError("reason is not a sink")
    changed = conn.execute("UPDATE gem_balances SET gems = gems - ? WHERE telegram_id = ? AND gems >= ?", (amount, telegram_id, amount)).rowcount
    if changed == 0:
        raise InsufficientGems()
    conn.execute("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, ?, ?, ?, ?)", (telegram_id, -amount, reason, ref, now))
    return gems_balance(conn, telegram_id)
