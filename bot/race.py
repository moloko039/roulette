"""Забег свинок: правила игры, расчёт RTP, опыта и детерминированного скрипта анимации.

Чистые функции, без базы, расчёты целочисленные (без float) и через fractions.Fraction.
Игрок выбирает 3 или 4 дорожки и ставит на одну дорожку.
Выплата: floor(ставка * дорожки * 36 / 37).
Опыт (xp): ставка * (дорожки - 1) // дорожки.
"""
from fractions import Fraction
import random

RACE_MIN_BET = 1
RACE_MAX_BET = 1_000_000
RACE_LANES = (3, 4)
RACE_FRAMES = 40
PAYOUT_NUM = 36
PAYOUT_DEN = 37
THEORETICAL_RTP = Fraction(36, 37)


class RaceError(Exception):
    """Ошибки раунда забега свинок, уходящие клиенту как 409 с кодом."""
    code = "error"


class RequestConflict(RaceError):
    code = "request_conflict"


def validate_lanes(lanes):
    """Проверяет число дорожек (3 или 4)."""
    if type(lanes) is not int or lanes not in RACE_LANES:
        raise ValueError("lanes must be 3 or 4")
    return lanes


def validate_lane(lane, lanes):
    """Проверяет выбранную дорожку (0 <= lane < lanes)."""
    if type(lane) is not int or not 0 <= lane < lanes:
        raise ValueError("lane out of range")
    return lane


def validate_bet(bet):
    """Проверяет размер ставки (от 1 до 1 000 000 фишек)."""
    if type(bet) is not int or not RACE_MIN_BET <= bet <= RACE_MAX_BET:
        raise ValueError("bet out of range")
    return bet


def payout(bet, lanes, won):
    """Выплата: floor(ставка * дорожки * 36 / 37) при победе; 0 при проигрыше."""
    if not won:
        return 0
    return (bet * lanes * PAYOUT_NUM) // PAYOUT_DEN


def net_win(bet, payout_amount):
    """Чистый выигрыш = выплата - ставка."""
    return payout_amount - bet


def race_xp(bet, lanes):
    """Опыт (xp): ставка * (дорожки - 1) // дорожки (вероятность потерять всю ставку)."""
    if type(bet) is not int or bet < 0:
        raise ValueError("bet must be non-negative integer")
    if lanes not in RACE_LANES:
        raise ValueError("invalid lanes")
    return bet * (lanes - 1) // lanes


def max_payout(bet, lanes):
    """Наибольшая возможная выплата за раунд."""
    return (bet * lanes * PAYOUT_NUM) // PAYOUT_DEN


def exact_rtp(bet, lanes):
    """Точный RTP как Fraction при фиксированной ставке."""
    return Fraction(payout(bet, lanes, True), lanes * bet)


def generate_script(seed, winner, lanes, frames=RACE_FRAMES):
    """Детерминированная последовательность позиций для анимации клиента.

    Массив из frames (40) кадров. В каждом кадре позиции всех дорожек (целые 0..1000).
    Кадр 0: все дорожки на старте ([0, ..., 0]).
    Кадр frames - 1 (39): победитель первым на финише (1000), остальные строго ниже (< 1000).
    Позиции строго возрастают на каждом шаге.
    На промежуточных этапах гарантированы обгоны (лидер сменяется).
    """
    validate_lanes(lanes)
    validate_lane(winner, lanes)
    if frames != 40:
        raise ValueError("frames must be 40")

    rng = random.Random(seed)

    # Чекпоинты времени: t0=0, t1=10, t2=22, t3=32, t4=39
    t0, t1, t2, t3, t4 = 0, 10, 22, 32, 39

    # Выбираем не-победителей, лидирующих на промежуточных отрезках
    l1 = (winner + 1) % lanes
    l2 = (winner + 2) % lanes if lanes > 2 else (winner + 1) % lanes

    # Финишные позиции (t4): победитель ровно 1000, остальные распределены ниже 1000
    finish = [0] * lanes
    finish[winner] = 1000
    other_lanes = [l for l in range(lanes) if l != winner]
    rng.shuffle(other_lanes)
    base_finish = 960
    for idx, l in enumerate(other_lanes):
        finish[l] = base_finish - idx * 30 - rng.randint(0, 10)

    # Чекпоинт 1 (t1=10): дорожка l1 вырывается вперёд
    p1 = [0] * lanes
    p1[l1] = 270 + rng.randint(0, 20)
    for l in range(lanes):
        if l != l1:
            p1[l] = 180 + rng.randint(0, 40)

    # Чекпоинт 2 (t2=22): дорожка l2 обгоняет и выходит в лидеры
    p2 = [0] * lanes
    p2[l2] = 600 + rng.randint(0, 20)
    for l in range(lanes):
        if l != l2:
            p2[l] = p1[l] + 250 + rng.randint(0, 40)
            if p2[l] >= p2[l2]:
                p2[l] = p2[l2] - rng.randint(10, 30)

    # Чекпоинт 3 (t3=32): победитель ускоряется и захватывает лидерство
    p3 = [0] * lanes
    p3[winner] = 840 + rng.randint(0, 20)
    for l in range(lanes):
        if l != winner:
            p3[l] = min(p2[l] + 150 + rng.randint(0, 30), finish[l] - 25)
            if p3[l] >= p3[winner]:
                p3[l] = p3[winner] - rng.randint(10, 30)

    checkpoints = [(t0, [0] * lanes), (t1, p1), (t2, p2), (t3, p3), (t4, finish)]

    script = [[0] * lanes for _ in range(frames)]
    for seg in range(len(checkpoints) - 1):
        ca_t, ca_p = checkpoints[seg]
        cb_t, cb_p = checkpoints[seg + 1]
        k = cb_t - ca_t
        for l in range(lanes):
            delta = cb_p[l] - ca_p[l]
            weights = [rng.randint(1, 10) for _ in range(k)]
            total_w = sum(weights)
            incs = [delta * w // total_w for w in weights]
            rem = delta - sum(incs)
            for i in range(rem):
                incs[i] += 1
            cur = ca_p[l]
            for step in range(k):
                cur += incs[step]
                script[ca_t + step + 1][l] = cur

    return script
