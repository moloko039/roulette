"""Игровой опыт: чистые функции, без базы, расчёты целочисленные (без float).

Уровень профиля считается по опыту (players.xp). Опыт за игру = ставка * вероятность потерять ВСЮ ставку.
Так безрисковые комбинации (например, красное и чёрное одновременно, ставки на все числа) почти не дают
опыта, и уровни нельзя накрутить циклами без риска. Возврат ставки (в минах) опыта не даёт.
Значения опыта не больше ставки, а ставки не больше MAX_SAFE_INT; опыт игрока в базе ограничен MAX_SAFE_INT.
"""
from math import comb

from roulette import settle
from blackjack import xp_for
from crash import xp_for as crash_xp_for
from keno import TOTAL_DRAWS, lose_combinations

ROULETTE_OUTCOMES = 37   # исходы 0..36
FIELD_CELLS = 25         # поле мин 5 на 5


def roulette_xp(stake_total, bets):
    """Опыт раунда рулетки: stake_total * (число исходов из 37, где общая выплата раунда равна нулю) // 37.

    Выплаты считает roulette.settle (правила не копируются): по всем ставкам раунда вместе для каждого исхода."""
    if type(stake_total) is not int or stake_total < 0:
        raise ValueError("stake_total must be a non-negative integer")
    losing = sum(1 for n in range(ROULETTE_OUTCOMES) if settle(bets, n)[1] == 0)
    return stake_total * losing // ROULETTE_OUTCOMES


def keno_xp(bet, picked):
    """Опыт раунда кено: bet * (число розыгрышей из C(40, 10), где выплата равна нулю) // C(40, 10).
    Зависит только от ставки и числа выбранных (вероятность проигрыша всей ставки при k числах)."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    return bet * lose_combinations(picked) // TOTAL_DRAWS


def blackjack_xp(wager):
    """Опыт раздачи блэкджека: wager * 12 // 25 (формула в blackjack.xp_for, здесь единая точка для db)."""
    return xp_for(wager)


def crash_xp(bet, m_x100):
    """Опыт раунда краша (формула в crash.xp_for, единая точка для db)."""
    return crash_xp_for(bet, m_x100)


def mines_xp(bet, mines, opened, lost):
    """Опыт одной игры в мины.

    lost (сработала мина): bet. opened = 0 (возврат ставки): 0. Иначе bet * (1 - P(выжить)) с округлением вниз,
    где P(выжить) = C(25 - m, k) / C(25, k): bet * (C(25, k) - C(25 - m, k)) // C(25, k)."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    if type(mines) is not int or not 1 <= mines <= 24:
        raise ValueError("mines out of range")
    if type(opened) is not int or not 0 <= opened <= FIELD_CELLS - mines:
        raise ValueError("opened out of range")
    if lost:
        return bet
    if opened == 0:
        return 0
    return bet * (comb(FIELD_CELLS, opened) - comb(FIELD_CELLS - mines, opened)) // comb(FIELD_CELLS, opened)
