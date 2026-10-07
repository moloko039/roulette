"""Краш: правила игры. Чистые функции, без базы; время только серверное (в миллисекундах).

Множитель растёт по времени: m100(t) = floor(100 * 2 ** (t / DOUBLING_MS)), не больше CAP_X100. Точка краха выбирается при старте
раунда и хранится только на сервере до конца раунда. Игрок выигрывает при выводе на множителе c (в сотых), если c <= crash_x100.
Режим «авто»: цель target_x100 задана при старте, но раунд остаётся активным, как ручной: его закрывает одно из трёх (что наступит раньше по
серверному времени): ручной вывод игрока на текущем множителе, достижение цели T (выплата по T, не выше) или крах в точке C (проигрыш).
Режим «вручную»: цель не задана, игрок жмёт cashout, пока раунд активен.
Все проверки (cashout, state, автозакрытие, /api/me) используют ОДНО эффективное время: now_ms - GRACE_MS - started_ms.
"""
import math
import random

DOUBLING_MS = 6000
CAP_X100 = 25000           # ×250.00 (достигается за 6000 * log2(250) = 47 776 мс)
MIN_TARGET_X100 = 101      # вывод на ×1.00 запрещён (иначе возврат ставки без риска)
GRACE_MS = 150             # задержка сети не должна проигрывать игроку
M = 2 ** 53
CRASH_STORE_MAX = 10 ** 9  # точка краха в базе не больше этого
ABANDON_MS = 70000         # раунд, брошенный дольше этого, закрывается «игрок не вывел»
CRASH_MAX_BET = 10 ** 9    # выплата не больше bet * 250, то есть 2,5 * 10**11 при максимальной ставке


class CrashError(Exception):
    """Ошибка действия: code идёт в ответ API (409)."""
    code = "error"


class ActiveGameExists(CrashError):
    code = "active_game_exists"


class NoActiveGame(CrashError):
    code = "no_active_game"


class TooEarly(CrashError):
    code = "too_early"


class RequestConflict(CrashError):
    code = "request_conflict"


def m100(elapsed_ms):
    """Множитель в сотых долях по времени (не меньше 100, не больше CAP_X100)."""
    if elapsed_ms <= 0:
        return 100
    exponent = elapsed_ms / DOUBLING_MS
    if exponent >= 10:                    # 2 ** 10 = 1024 > 250: предел давно достигнут, степень не считаем
        return CAP_X100
    return min(CAP_X100, math.floor(100 * 2 ** exponent))


def effective_ms(now_ms, started_ms):
    """Эффективное время раунда: с запасом на задержку сети, не отрицательное."""
    return max(0, now_ms - GRACE_MS - started_ms)


def crash_from_u(u):
    """Точка краха (в сотых) по равномерному числу u из 0..M-1: max(100, 3600 * M // (37 * (M - u))), не больше CRASH_STORE_MAX."""
    return min(CRASH_STORE_MAX, max(100, 3600 * M // (37 * (M - u))))


def new_crash(rng=None):
    """Точка краха нового раунда. rng нужен тестам (объект с randrange), по умолчанию SystemRandom."""
    source = rng if rng is not None else random.SystemRandom()
    return crash_from_u(source.randrange(M))


def payout(bet, c_x100):
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    return bet * c_x100 // 100


def xp_for(bet, m_x100):
    """Опыт раунда: bet * (37*m - 3600) // (37*m) (доля потери всей ставки), не меньше нуля."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be a non-negative integer")
    if type(m_x100) is not int or m_x100 < 1:
        raise ValueError("m must be a positive integer")
    return max(0, bet * (37 * m_x100 - 3600) // (37 * m_x100))


def text(x100):
    """«X.YY» из сотых долей."""
    return "%d.%02d" % (x100 // 100, x100 % 100)


def decide_auto(target_x100, crash_x100):
    """Итог авто-раунда, в котором игрок так и не вывел вручную: выигрыш, если цель не выше точки краха. Возвращает (result, множитель итога в сотых).
    Совпадает с итогом settle() на любом достаточно позднем времени (проверяет test_crash.py)."""
    if type(target_x100) is not int or not MIN_TARGET_X100 <= target_x100 <= CAP_X100:
        raise ValueError("target out of range")
    return ("win", target_x100) if target_x100 <= crash_x100 else ("lose", 0)


def settle(crash_x100, started_ms, now_ms, target_x100=None):
    """Раунд на момент now_ms: None, пока он идёт; иначе (result, множитель итога в сотых).
    Ручной (target_x100 None): разбился, если crash_x100 < m100(эффективное время). Достиг предела при crash >= CAP: выигрыш ×250. Брошенный (дольше
    ABANDON_MS, на практике уже решён правилами выше): проигрыш, если crash < CAP, иначе выигрыш ×250.
    Авто (цель T): если T <= crash (как в decide_auto, равенство выигрывает) и m >= T: выигрыш ровно по T, выше T платить нельзя, даже если проверка
    поздняя и множитель давно прошёл T; если T > crash и crash < m: проигрыш; иначе раунд идёт. Порядок событий задаёт сравнение T и crash:
    при T <= crash цель достигается не позже краха, при T > crash крах случается раньше цели."""
    m = m100(effective_ms(now_ms, started_ms))
    if target_x100 is not None:
        if target_x100 <= crash_x100:
            if m >= target_x100:
                return ("win", target_x100)
            return ("win", target_x100) if now_ms - started_ms > ABANDON_MS else None
        if crash_x100 < m or now_ms - started_ms > ABANDON_MS:
            return ("lose", 0)
        return None
    if crash_x100 < m:
        return ("lose", 0)
    if m >= CAP_X100 and crash_x100 >= CAP_X100:
        return ("win", CAP_X100)
    if now_ms - started_ms > ABANDON_MS:
        return ("lose", 0) if crash_x100 < CAP_X100 else ("win", CAP_X100)
    return None


def cashout_multiplier(started_ms, now_ms):
    """Множитель вывода в этот момент (в сотых); TooEarly, если он меньше MIN_TARGET_X100."""
    c = m100(effective_ms(now_ms, started_ms))
    if c < MIN_TARGET_X100:
        raise TooEarly()
    return c


def xp_multiplier(mode, result, mult_x100, target_x100):
    """m для формулы опыта: авто: множитель выплаты при выигрыше (цель, а при ручном выводе раньше цели множитель вывода), цель при проигрыше;
    ручной: множитель вывода при выигрыше, CAP_X100 при проигрыше или брошенном раунде."""
    if mode == "auto":
        return mult_x100 if result == "win" else target_x100
    return mult_x100 if result == "win" else CAP_X100
