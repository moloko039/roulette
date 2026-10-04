import json
import re

# Правила рулетки без базы данных: проверка ставок и расчёт выигрыша.
# Совпадают с игрой в script.js, кроме нумерации колонок (см. COLUMN_OF).

# Предел точного хранения целых чисел в JavaScript (2**53 - 1). Это НЕ игровой лимит:
# он нужен, чтобы клиент никогда не получил неточное число и чтобы в запросе не пришло
# гигантское значение. На размер ставки игра никаких ограничений, кроме баланса, не вводит.
MAX_SAFE_INT = 9007199254740991

# Столько разных ставок существует: 37 чисел, 3 дюжины, 3 колонки, 4 простые (red, black, even, odd)
MAX_BETS = 47

# Чистая прибыль к ставке; при выигрыше игроку возвращается ставка плюс прибыль
PAYOUT = {"red": 1, "black": 1, "even": 1, "odd": 1, "dozen": 2, "column": 2, "number": 35}

RED_NUMBERS = frozenset([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36])

SIMPLE_TYPES = ("red", "black", "even", "odd")  # value только null
NUMBERS = range(37)  # европейское колесо: 0-36

REQUEST_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")


class InvalidBets(Exception):
    """Любая ошибка в запросе. Подробности наружу не отдаём."""


class InsufficientFunds(Exception):
    pass


class BalanceLimit(Exception):
    pass


class RequestConflict(Exception):
    """Тот же request_id с другими ставками."""


def bets_fingerprint(bets):
    """Канонический вид набора ставок для сверки при повторе: порядок ставок и ключей значения не имеет."""
    return sorted(json.dumps(b, sort_keys=True, separators=(",", ":")) for b in bets)


def _is_int(v):
    # type(...) is int отсекает True/False (они подкласс int), дробные и строки
    return type(v) is int


def validate_request_id(request_id):
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise InvalidBets()
    return request_id


def validate_bets(raw):
    """Проверяет список ставок и возвращает его копию [{type, value, amount}, ...]."""
    if type(raw) is not list or not 1 <= len(raw) <= MAX_BETS:
        raise InvalidBets()
    bets = []
    seen = set()
    total = 0
    for item in raw:
        if type(item) is not dict or set(item) != {"type", "value", "amount"}:
            raise InvalidBets()
        bet_type, value, amount = item["type"], item["value"], item["amount"]
        if type(bet_type) is not str:
            raise InvalidBets()
        if bet_type == "number":
            if not _is_int(value) or not 0 <= value <= 36:
                raise InvalidBets()
        elif bet_type in ("dozen", "column"):
            if not _is_int(value) or not 1 <= value <= 3:
                raise InvalidBets()
        elif bet_type in SIMPLE_TYPES:
            if value is not None:
                raise InvalidBets()
        else:
            raise InvalidBets()
        if not _is_int(amount) or not 1 <= amount <= MAX_SAFE_INT:
            raise InvalidBets()
        key = (bet_type, value)
        if key in seen:
            raise InvalidBets()  # одинаковые ставки клиент складывает сам
        seen.add(key)
        total += amount
        bets.append({"type": bet_type, "value": value, "amount": amount})
    if total > MAX_SAFE_INT:
        raise InvalidBets()
    return bets


def column_of(n):
    """Колонка числа: 1 — 1, 4, 7…34; 2 — 2, 5…35; 3 — 3, 6…36. Для нуля None."""
    if n == 0:
        return None
    return (n - 1) % 3 + 1


def dozen_of(n):
    if n == 0:
        return None
    return (n - 1) // 12 + 1


def is_win(bet, n):
    t, v = bet["type"], bet["value"]
    if t == "number":
        return n == v
    if n == 0:
        return False  # ноль проигрывает всё, кроме ставки на число 0
    if t == "red":
        return n in RED_NUMBERS
    if t == "black":
        return n not in RED_NUMBERS
    if t == "even":
        return n % 2 == 0
    if t == "odd":
        return n % 2 == 1
    if t == "dozen":
        return dozen_of(n) == v
    if t == "column":
        return column_of(n) == v
    return False


def bet_payout(bet, n):
    """Сколько вернулось по ставке: ставка плюс прибыль или 0."""
    return bet["amount"] * (PAYOUT[bet["type"]] + 1) if is_win(bet, n) else 0


def settle(bets, n):
    """Возвращает (сумма_ставок, сколько_вернулось_всего)."""
    return sum(b["amount"] for b in bets), sum(bet_payout(b, n) for b in bets)


def max_payout(bets):
    """Наибольший возврат этого набора ставок по всем числам колеса."""
    return max(settle(bets, n)[1] for n in NUMBERS)
