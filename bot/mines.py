"""Мины: правила игры. Чистые функции, без базы, расчёты целочисленные (без float).

Поле 5 на 5: клетки 0..24, индекс = ряд * 5 + колонка. Раскладка мин и открытые клетки хранятся
как битовые маски из 25 бит (бит i соответствует клетке i).
"""
import random
from math import comb

FIELD_CELLS = 25
MINES_MIN_COUNT = 1
MINES_MAX_COUNT = 24

# Технический потолок ставки. Выплата после k открытых клеток при m минах не больше
# bet * 36 * C(25, m) / 37 (максимум при k = 25 - m); наибольшее C(25, m) = 5 200 300 при m = 12, то есть
# выплата не больше около 5,06 миллиона ставок. При ставке 10**9 это около 5,06 * 10**15, что меньше
# MAX_SAFE_INT (9 007 199 254 740 991): баланс остаётся точным числом для JavaScript.
# Тест перебирает все пары (мин, открыто) для этой ставки.
MINES_MAX_BET = 10 ** 9

# Через сколько секунд без действий активная игра закрывается автоматически
MINES_IDLE_SECONDS = 86400

# Честная игра с тем же преимуществом казино 1/37, что в рулетке: возврат 36/37, округление вниз
PAYOUT_NUM, PAYOUT_DEN = 36, 37

FULL_MASK = (1 << FIELD_CELLS) - 1


def _check_pair(mines, opened):
    if type(mines) is not int or not MINES_MIN_COUNT <= mines <= MINES_MAX_COUNT:
        raise ValueError("mines out of range")
    if type(opened) is not int or not 0 <= opened <= FIELD_CELLS - mines:
        raise ValueError("opened out of range")


def payout(bet, mines, opened):
    """Выплата после opened открытых безопасных клеток при mines минах (ставка при opened = 0)."""
    _check_pair(mines, opened)
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    if opened == 0:
        return bet
    return (bet * PAYOUT_NUM * comb(FIELD_CELLS, opened)) // (PAYOUT_DEN * comb(FIELD_CELLS - mines, opened))


def multiplier_text(mines, opened):
    """Множитель для показа «X.YY», округление вниз, целочисленно; «1.00» при opened = 0."""
    _check_pair(mines, opened)
    if opened == 0:
        return "1.00"
    hundredths = (PAYOUT_NUM * comb(FIELD_CELLS, opened) * 100) // (PAYOUT_DEN * comb(FIELD_CELLS - mines, opened))
    return "%d.%02d" % (hundredths // 100, hundredths % 100)


def new_layout(mines, rng=None):
    """Случайная раскладка: маска из 25 бит ровно с mines единицами. rng нужен тестам (объект с sample)."""
    _check_pair(mines, 0)
    source = rng if rng is not None else random.SystemRandom()   # secrets-уровень случайности
    mask = 0
    for cell in source.sample(range(FIELD_CELLS), mines):
        mask |= 1 << cell
    return mask


def popcount(mask):
    return bin(mask).count("1")


def cells_of(mask):
    """Индексы клеток из маски по возрастанию."""
    return [i for i in range(FIELD_CELLS) if mask >> i & 1]


class MinesError(Exception):
    """Ошибка действия в игре: code идёт в ответ API (409)."""
    code = "error"


class ActiveGameExists(MinesError):
    code = "active_game_exists"


class NoActiveGame(MinesError):
    code = "no_active_game"


class AlreadyRevealed(MinesError):
    code = "already_revealed"


class RequestConflict(MinesError):
    code = "request_conflict"
