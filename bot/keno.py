"""Кено: правила игры. Чистые функции, без базы, расчёты целочисленные (без float).

Поле 1..40. Игрок выбирает от 1 до 10 разных чисел, сервер вытягивает 10 разных чисел из 40.
Совпадения (hits) = пересечение выбора и розыгрыша. Выплата = bet * mult_x100 // 100, множитель берётся
из таблицы PAYTABLE по паре (выбрано k, совпало h); пары нет в таблице: выплата 0.
"""
import random
from math import comb

FIELD_MIN, FIELD_MAX = 1, 40
DRAW_COUNT = 10
PICKS_MIN, PICKS_MAX = 1, 10

# Технический потолок ставки: наибольший множитель 1000, выплата не больше 10**12, это намного ниже
# MAX_SAFE_INT (9 007 199 254 740 991), баланс остаётся точным числом для JavaScript.
KENO_MAX_BET = 10 ** 9

# Множители в сотых долях: PAYTABLE[k][h] = mult_x100. Возврат около 97,2 % (тест проверяет: от 97 % до 36/37)
PAYTABLE = {
    1: {1: 389},
    2: {2: 1686},
    3: {2: 213, 3: 5607},
    4: {3: 988, 4: 25405},
    5: {3: 367, 4: 4064, 5: 76217},
    6: {3: 213, 4: 1656, 5: 12869, 6: 100000},
    7: {4: 1249, 5: 5385, 6: 23205, 7: 100000},
    8: {4: 711, 5: 2450, 6: 8436, 7: 29045, 8: 100000},
    9: {4: 450, 5: 1326, 6: 3907, 7: 11515, 8: 33934, 9: 100000},
    10: {5: 1589, 6: 3639, 7: 8332, 8: 19077, 9: 43677, 10: 100000},
}

MAX_MULT_X100 = max(m for row in PAYTABLE.values() for m in row.values())   # 100000 (то есть ×1000)
TOTAL_DRAWS = comb(FIELD_MAX, DRAW_COUNT)   # число равновероятных розыгрышей


class InvalidPicks(ValueError):
    """Выбор чисел не подходит под правила."""


def validate_picks(raw):
    """Проверяет список чисел игрока и возвращает его копию по возрастанию. Типы строгие (bool, float, строки нельзя)."""
    if type(raw) is not list or not PICKS_MIN <= len(raw) <= PICKS_MAX:
        raise InvalidPicks()
    for n in raw:
        if type(n) is not int or not FIELD_MIN <= n <= FIELD_MAX:
            raise InvalidPicks()
    if len(set(raw)) != len(raw):
        raise InvalidPicks()
    return sorted(raw)


def multiplier_x100(picked, hits):
    """Множитель в сотых долях для пары (выбрано, совпало); 0, если пара не платит."""
    if type(picked) is not int or not PICKS_MIN <= picked <= PICKS_MAX:
        raise ValueError("picked out of range")
    if type(hits) is not int or not 0 <= hits <= picked:
        raise ValueError("hits out of range")
    return PAYTABLE[picked].get(hits, 0)


def multiplier_text(mult_x100):
    """«X.YY» из сотых долей; «0.00», если не платит."""
    return "%d.%02d" % (mult_x100 // 100, mult_x100 % 100)


def payout(bet, picked, hits):
    """Выплата (целая, округление вниз); 0, если пара не платит."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    return bet * multiplier_x100(picked, hits) // 100


def draw_numbers(rng=None):
    """10 разных чисел из 1..40 по возрастанию. rng нужен тестам (объект с sample), по умолчанию SystemRandom."""
    source = rng if rng is not None else random.SystemRandom()
    return sorted(source.sample(range(FIELD_MIN, FIELD_MAX + 1), DRAW_COUNT))


def play(picks, draw):
    """Совпавшие числа по возрастанию."""
    drawn = set(draw)
    return [n for n in sorted(picks) if n in drawn]


def lose_combinations(picked):
    """Число розыгрышей (из C(40, 10)), при которых выплата равна нулю: сумма C(k,h) * C(40-k, 10-h) по h не из таблицы."""
    if type(picked) is not int or not PICKS_MIN <= picked <= PICKS_MAX:
        raise ValueError("picked out of range")
    return sum(comb(picked, h) * comb(FIELD_MAX - picked, DRAW_COUNT - h)
               for h in range(0, picked + 1) if h not in PAYTABLE[picked] and DRAW_COUNT - h <= FIELD_MAX - picked)


def paytable_text():
    """Таблица для клиента: {"k": {"h": "X.YY"}} (ключи строками, как в JSON); только платные пары."""
    return {str(k): {str(h): multiplier_text(m) for h, m in sorted(row.items())} for k, row in sorted(PAYTABLE.items())}


class KenoError(Exception):
    """Ошибка раунда: code идёт в ответ API (409)."""
    code = "error"


class RequestConflict(KenoError):
    code = "request_conflict"
