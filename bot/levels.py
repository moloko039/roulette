"""Уровень профиля по накопленному опыту (players.xp, правила опыта в xp.py). Чистые функции, без базы.

Все значения СТАРТОВЫЕ: их можно менять, когда появятся реальные данные об игре. Считаем только целыми
числами (без float), чтобы пороги не зависели от округления.
"""
from bisect import bisect_right

BASE_THRESHOLD = 1000          # порог уровня 2 при множителе 1,6 даёт 1000 * 1,6 = 1600
GROWTH_NUM, GROWTH_DEN = 8, 5  # множитель роста порога 8/5 = 1,6
MAX_PROFILE_LEVEL = 60


def threshold(level):
    """Сколько нужно поставить за всё время, чтобы получить уровень level (2..MAX_PROFILE_LEVEL).
    floor(1000 * 1.6^(level-1)), целочисленно. Для уровня 1 порога нет (0)."""
    if type(level) is not int or level < 1 or level > MAX_PROFILE_LEVEL:
        raise ValueError("level out of range")
    if level == 1:
        return 0
    return (BASE_THRESHOLD * GROWTH_NUM ** (level - 1)) // (GROWTH_DEN ** (level - 1))


_THRESHOLDS = [threshold(level) for level in range(2, MAX_PROFILE_LEVEL + 1)]


def profile_level(total_staked):
    """Уровень 1 при опыте (аргумент) меньше 1600, дальше растёт с порогами threshold(L); не выше MAX_PROFILE_LEVEL."""
    if type(total_staked) is not int or total_staked < 0:
        raise ValueError("total_staked must be a non-negative integer")
    return 1 + bisect_right(_THRESHOLDS, total_staked)


def level_progress(total_staked):
    """(уровень, ставок накоплено, порог следующего уровня или None на максимальном)."""
    level = profile_level(total_staked)
    next_threshold = threshold(level + 1) if level < MAX_PROFILE_LEVEL else None
    return level, total_staked, next_threshold
