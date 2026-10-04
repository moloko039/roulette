"""Хило (Hi-Lo): правила игры. Чистые функции, без базы; все расчёты целочисленные (без float).

13 достоинств: туз = 1 (младший) ... король = 13 (старший), каждая карта выбирается независимо и равномерно. Масти
(S H D C) только для вида. Ход «hi»: выигрыш, если новая карта >= текущей (k = 14 - r вариантов из 13); «lo»: выигрыш,
если новая <= текущей (k = r). Равенство достоинств выигрывает при любом направлении. Ход с k = 13 запрещён (он выигрывает
всегда). «skip» заменяет текущую карту новой, множитель не меняется.
Множитель шага = 13/k * 36/37, множитель раунда = произведение шагов; хранится дробью из двух целых (Python int без
границ, в базе текстом). Выплата = floor(ставка * M). Потолок: M >= HILO_MAX_X (1000) закрывает раунд выплатой 1000 * ставка.
"""
import random
from fractions import Fraction

RANK_MIN, RANK_MAX = 1, 13
RANKS = RANK_MAX - RANK_MIN + 1
SUITS = "SHDC"
CHOICES = ("hi", "lo", "skip")
PAYOUT_NUM, PAYOUT_DEN = 36, 37      # возврат 36/37, как в других играх

HILO_MAX_X = 1000                    # потолок выплаты: 1000 ставок
HILO_MAX_BET = 10 ** 9               # 1000 * 10**9 = 10**12 << MAX_SAFE_INT (9 * 10**15): баланс остаётся точным в JavaScript
HILO_IDLE_SECONDS = 86400            # как у мин: 24 часа без действий закрывают раунд автоматически
HISTORY_SHOWN = 12                   # сколько прошлых карт отдаётся клиенту (и хранится)


class HiloError(Exception):
    """Ошибка действия: code идёт в ответ API (409)."""
    code = "error"


class ActiveGameExists(HiloError):
    code = "active_game_exists"


class NoActiveGame(HiloError):
    code = "no_active_game"


class MoveForbidden(HiloError):
    code = "move_forbidden"       # ход с k = 13 (выигрывает всегда)


class NothingToCashOut(HiloError):
    code = "nothing_to_cash_out"  # cashout до первого угаданного хода


class RequestConflict(HiloError):
    code = "request_conflict"


def valid_rank(rank):
    return type(rank) is int and RANK_MIN <= rank <= RANK_MAX


def ways(choice, rank):
    """k: сколько из 13 достоинств выигрывают при выборе choice (hi или lo) от rank."""
    if not valid_rank(rank):
        raise ValueError("rank out of range")
    if choice == "hi":
        return RANK_MAX + 1 - rank
    if choice == "lo":
        return rank
    raise ValueError("choice must be hi or lo")


def is_win(choice, rank, new_rank):
    """Равенство выигрывает для любого направления."""
    return new_rank >= rank if choice == "hi" else new_rank <= rank


def step_multiplier(k):
    """Множитель одного шага 13/k * 36/37 точной дробью."""
    if type(k) is not int or not 1 <= k <= RANKS:
        raise ValueError("k out of range")
    return Fraction(RANKS * PAYOUT_NUM, k * PAYOUT_DEN)


def can_move(choice, rank):
    """Ход допустим, если он не выигрывает всегда."""
    return ways(choice, rank) < RANKS


def probability_text(k):
    """Вероятность k/13 в процентах с одним знаком, округление вниз (строка «53.8»)."""
    tenths = k * 1000 // RANKS
    return "%d.%d" % (tenths // 10, tenths % 10)


def multiplier_text(m):
    """Множитель «X.YY», округление вниз, целочисленно (m: Fraction)."""
    hundredths = m.numerator * 100 // m.denominator
    return "%d.%02d" % (hundredths // 100, hundredths % 100)


def payout(bet, m):
    """Выплата floor(ставка * M); не больше потолка 1000 * ставка."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    return min(bet * m.numerator // m.denominator, bet * HILO_MAX_X)


def reached_cap(m):
    return m >= HILO_MAX_X


def xp_for(bet, m):
    """Опыт: bet * (1 - 36/37 / M) = bet * (37 * num - 36 * den) // (37 * num), не меньше нуля. M: Fraction (> 0)."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    num, den = m.numerator, m.denominator
    if num <= 0:
        raise ValueError("m must be positive")
    return max(0, bet * (PAYOUT_DEN * num - PAYOUT_NUM * den) // (PAYOUT_DEN * num))


def draw_card(rng=None):
    """Случайная карта (достоинство 1..13 и масть). rng нужен тестам (объект с randrange), по умолчанию SystemRandom."""
    source = rng if rng is not None else random.SystemRandom()
    rank = RANK_MIN + source.randrange(RANKS)
    suit = SUITS[source.randrange(len(SUITS))]
    return rank, suit


def frac(num_text, den_text):
    """Множитель из двух текстов с целыми (так он хранится в базе)."""
    return Fraction(int(num_text), int(den_text))
