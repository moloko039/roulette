import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
"""RTP краша не меняется от ручного вывода в раунде с автовыводом.

Метод. Берутся настоящие функции правил (crash.new_crash, crash.settle, crash.cashout_multiplier) и фиксированный seed генератора (random.Random),
по N раундов на цель T из набора {1.5, 2, 5, 20}. Платёж считается на единицу ставки без округления (выплата в сотых / 100).
  A: «автовывод на T»: раунд (цель T) закрывается по времени settle() на позднем времени.
  B: «автовывод на T + ручной вывод в случайное время ≤ T»: случайное эффективное время t равномерно от момента ×1.01 до момента достижения цели
     (раунд с целью T, ручной вывод на t; если раунд к t уже решён, платится его итог, иначе множитель m(t)).
  C: контроль: обычный ручной раунд (без цели) с тем же выводом в то же время t и той же точкой краха.
Эталонное ожидание: 36/37 = 0,97297 на единицу ставки при любом T и любом времени вывода (P(краха не ниже c) = 3600/(37 c)).
Допуск: |среднее - 36/37| < 4,5 стандартной ошибки (по выборочной дисперсии) + 0,001 (запас на целочисленные сотые); для сравнения A с B допуск 4,5 sqrt(se_A² + se_B²) +
0,001. Дополнительно, точно (без допуска): B и C совпадают в каждом раунде (авто-раунд до цели ничем не отличается от ручного), и ни одна выплата в B не выше T."""
import math
import random

import crash

N = 200_000
TARGETS = (150, 200, 500, 2000)
THEORY = 36 / 37
SIGMAS = 4.5
SLACK = 0.001


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def eff_time(m_x100):
    """Первое эффективное время (мс), на котором m100 >= m_x100."""
    t = crash.DOUBLING_MS * math.log2(m_x100 / 100)
    t = int(t)
    while crash.m100(t) < m_x100:
        t += 1
    while t > 0 and crash.m100(t - 1) >= m_x100:
        t -= 1
    return t


def payout_unit(verdict, cash_x100):
    """Выплата на единицу ставки: итог раунда, если он решён, иначе множитель вывода."""
    if verdict is not None:
        return verdict[1] / 100
    return cash_x100 / 100


def stats(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)


for target in TARGETS:
    rng = random.Random(20261006 + target)
    t_low, t_high = eff_time(crash.MIN_TARGET_X100), eff_time(target)
    a_pay, b_pay, c_pay = [], [], []
    for _ in range(N):
        c = crash.new_crash(rng)
        # A: автовывод на T без участия игрока (поздний момент)
        a_pay.append(payout_unit(crash.settle(c, 0, 10 ** 6, target), 0))
        # B и C: вывод в случайное время в [t(1.01), t(T)] (момент запроса = эффективное время + запас сети)
        t = rng.randint(t_low, t_high)
        now = t + crash.GRACE_MS
        cash = crash.cashout_multiplier(0, now)
        b = payout_unit(crash.settle(c, 0, now, target), cash)
        base = payout_unit(crash.settle(c, 0, now), cash)
        b_pay.append(b)
        c_pay.append(base)
        assert b == base, ("авто-раунд до цели отличается от ручного", target, c, t, b, base)
        assert b <= target / 100, ("выплата выше цели", target, c, t, b)
    ma, sa = stats(a_pay)
    mb, sb = stats(b_pay)
    mc, sc = stats(c_pay)
    for name, m, s in (("A автовывод", ma, sa), ("B авто + ручной вывод", mb, sb), ("C ручной", mc, sc)):
        assert abs(m - THEORY) < SIGMAS * s + SLACK, "цель %.2f, %s: среднее %.5f, эталон %.5f, допуск %.5f" % (target / 100, name, m, THEORY, SIGMAS * s + SLACK)
    assert abs(ma - mb) < SIGMAS * math.sqrt(sa ** 2 + sb ** 2) + SLACK, "цель %.2f: A %.5f и B %.5f расходятся" % (target / 100, ma, mb)
    print("цель %.2f: A=%.5f±%.5f  B=%.5f±%.5f  C=%.5f±%.5f  эталон %.5f (N=%d)" % (target / 100, ma, sa, mb, sb, mc, sc, THEORY, N))

# ограничение сверху: при любом времени и любой точке краха авто-раунд не платит больше цели
rng = random.Random(1)
for _ in range(20000):
    target = rng.randint(101, crash.CAP_X100)
    c = crash.new_crash(rng)
    t = rng.randint(0, 80_000)
    v = crash.settle(c, 0, t, target)
    if v is not None:
        assert v[1] <= target, (target, c, t, v)
    else:
        assert crash.m100(crash.effective_ms(t, 0)) <= max(target, c), (target, c, t)
print("Все проверки прошли")
