START_BALANCE = 1000
BASE_RATE = 100        # фишек в час
MAX_HOURS = 30         # потолок накопления
HOUR = 3600            # секунд в часе


def accrue(last_accrual, now, rate, max_hours=MAX_HOURS):
    """Возвращает (сколько_начислить, новое_время_последнего_начисления).

    max_hours — потолок накопления игрока (у каждого свой, зависит от уровня хранилища);
    по умолчанию общий MAX_HOURS, поэтому старые вызовы работают как раньше."""
    hours = (now - last_accrual) // HOUR   # целые часы

    if hours <= 0:
        # прошло меньше часа (или часы на сервере "прыгнули назад")
        return 0, last_accrual

    if hours > max_hours:
        # игрока долго не было: платим по потолку, лишнее сгорает
        return max_hours * rate, now

    # сдвигаем время только на целые часы, остаток минут сохраняется
    return hours * rate, last_accrual + hours * HOUR