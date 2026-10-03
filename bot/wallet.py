"""Кошелёк: единственное место, где игровые списания и выплаты меняют players.balance.

Функции работают ВНУТРИ уже открытой транзакции: соединение передаётся аргументом, транзакцию они
не открывают, не закрывают и не откатывают (это делает вызывающий код, например spin_roulette).
Начисление по часам (economy.accrue) сюда не относится и остаётся в db.py.
"""
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
