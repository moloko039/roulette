"""Ферма: улучшения дохода и хранилища за фишки. Чистые функции, без базы, расчёты целочисленные.

Все значения СТАРТОВЫЕ: их можно менять, когда появятся реальные данные об игре.
"""
import economy_config
import levels

INCOME_MAX_LEVEL = 20
STORAGE_MAX_LEVEL = 8

# доход: ставка в час на уровне n = floor(100 * 1.35^n) = 100 * 27^n // 20^n
INCOME_BASE_RATE = 100
INCOME_RATE_NUM, INCOME_RATE_DEN = 27, 20
# цена перехода с уровня n на n+1 = floor(1000 * 1.6^n) = 1000 * 8^n // 5^n (число роста в economy_config.FARM_INCOME_COST_GROWTH)
INCOME_BASE_COST = 1000
INCOME_COST_NUM, INCOME_COST_DEN = economy_config.FARM_INCOME_COST_GROWTH

# хранилище: потолок накопления в часах на уровне n = 30 + 6n; цена перехода с n на n+1 = 1000 * 2^n
STORAGE_BASE_HOURS = 30
STORAGE_HOURS_STEP = 6
STORAGE_BASE_COST = 1000

KINDS = ("income", "storage")


def _check_level(level, maximum):
    if type(level) is not int or level < 0 or level > maximum:
        raise ValueError("level out of range")


def income_rate(level):
    """Фишек в час на уровне дохода level (0..INCOME_MAX_LEVEL)."""
    _check_level(level, INCOME_MAX_LEVEL)
    return (INCOME_BASE_RATE * INCOME_RATE_NUM ** level) // INCOME_RATE_DEN ** level


def income_cost(level):
    """Цена перехода с уровня level на следующий или None на максимальном уровне."""
    _check_level(level, INCOME_MAX_LEVEL)
    if level >= INCOME_MAX_LEVEL:
        return None
    return (INCOME_BASE_COST * INCOME_COST_NUM ** level) // INCOME_COST_DEN ** level


def storage_hours(level):
    """Потолок накопления в часах на уровне хранилища level (0..STORAGE_MAX_LEVEL)."""
    _check_level(level, STORAGE_MAX_LEVEL)
    return STORAGE_BASE_HOURS + STORAGE_HOURS_STEP * level


def storage_cost(level):
    """Цена перехода с уровня level на следующий или None на максимальном уровне."""
    _check_level(level, STORAGE_MAX_LEVEL)
    if level >= STORAGE_MAX_LEVEL:
        return None
    return STORAGE_BASE_COST * 2 ** level


def block_reason(kind, income_level, storage_level, xp, balance):
    """Почему покупку нельзя сделать: None | "max_level" | "level_locked" | "insufficient_funds".
    Причины в том порядке, в каком проверяет покупка."""
    if kind == "income":
        cost = income_cost(income_level)
    elif kind == "storage":
        cost = storage_cost(storage_level)
    else:
        raise ValueError("unknown kind")
    if cost is None:
        return "max_level"
    if income_level + storage_level >= levels.profile_level(xp):   # уровень профиля по опыту
        return "level_locked"
    if balance < cost:
        return "insufficient_funds"
    return None


def status(balance, xp, staked, income_level, storage_level):
    """Данные экрана фермы (GET /api/farm)."""
    level, xp, next_threshold = levels.level_progress(xp)
    income_next = income_level < INCOME_MAX_LEVEL
    storage_next = storage_level < STORAGE_MAX_LEVEL
    reason_income = block_reason("income", income_level, storage_level, xp, balance)
    reason_storage = block_reason("storage", income_level, storage_level, xp, balance)
    return {
        "balance": balance,
        "profile": {"level": level, "xp": xp, "staked": staked, "next_threshold": next_threshold},
        "slots": {"used": income_level + storage_level, "total": level},
        "income": {
            "level": income_level,
            "max": INCOME_MAX_LEVEL,
            "rate": income_rate(income_level),
            "next_rate": income_rate(income_level + 1) if income_next else None,
            "next_cost": income_cost(income_level),
            "can_buy": reason_income is None,
            "reason": reason_income,
        },
        "storage": {
            "level": storage_level,
            "max": STORAGE_MAX_LEVEL,
            "hours": storage_hours(storage_level),
            "next_hours": storage_hours(storage_level + 1) if storage_next else None,
            "next_cost": storage_cost(storage_level),
            "can_buy": reason_storage is None,
            "reason": reason_storage,
        },
    }


class MaxLevel(Exception):
    """Улучшение уже на максимальном уровне (API: 409 max_level)."""


class LevelLocked(Exception):
    """Сумма уровней улучшений достигла уровня профиля (API: 409 level_locked)."""

    def __init__(self, required_level):
        super().__init__("level_locked")
        self.required_level = required_level   # какой уровень профиля нужен для покупки
