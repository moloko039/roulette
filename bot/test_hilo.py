"""Хило: правила, математика, возврат, статистика, база, гонки, XP, скрытая информация и API (мок-генератор, без сети)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from functools import lru_cache
from unittest import mock

from fastapi.testclient import TestClient

import bot
import db
import hilo
import levels
import ratelimit
import wallet
from api import create_app
from roulette import MAX_SAFE_INT, InsufficientFunds
from stubs import FakeUpdate
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
DAY = 86400
A, B = 424242421, 424242422

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE", "OWNER_CHAT_ID")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)
tmp = tempfile.mkdtemp()
counter = [0]


class Seq:
    """Детерминированный генератор: карты (достоинство, масть) подаются списком; считает вызовы randrange."""

    def __init__(self, *cards):
        self.values = []
        for rank, suit in cards:
            self.values += [rank - 1, hilo.SUITS.index(suit)]
        self.calls = 0

    def randrange(self, n):
        self.calls += 1
        value = self.values.pop(0)
        assert 0 <= value < n, "значение вне диапазона"
        return value


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "h%d.db" % counter[0])
    db.init_db(path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=1_000_000, xp=0, total=0, accrual=NOW + 10 * DAY):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, accrual, NOW - DAY, total, xp))


def player(path, uid=A):
    return sql(path, "SELECT balance, xp, total_staked FROM players WHERE telegram_id = ?", (uid,))[0]


rid_counter = [0]


def rid():
    rid_counter[0] += 1
    return "hilo-req-%06d" % rid_counter[0]


def start(path, bet, cards, uid=A, now=NOW, request_id=None):
    return db.hilo_start(uid, request_id or rid(), bet, now=now, db_path=path, rng=Seq(*cards))


def guess(path, choice, cards, uid=A, now=NOW, request_id=None):
    return db.hilo_guess(uid, request_id or rid(), choice, now=now, db_path=path, rng=Seq(*cards))


def cash(path, uid=A, now=NOW, request_id=None):
    return db.hilo_cashout(uid, request_id or rid(), now=now, db_path=path)


F = Fraction
STEP = lambda k: F(13 * 36, k * 37)


try:
    # ================= математика множителей =================
    check("константы", (hilo.HILO_MAX_X, hilo.HILO_MAX_BET, hilo.HILO_IDLE_SECONDS, hilo.RANKS), (1000, 10 ** 9, 86400, 13))
    assert hilo.HILO_MAX_BET * hilo.HILO_MAX_X <= MAX_SAFE_INT, "ставка * потолок выходит за MAX_SAFE_INT"
    for r in range(1, 14):
        check("k для hi = 14 - r (r=%d)" % r, hilo.ways("hi", r), 14 - r)
        check("k для lo = r (r=%d)" % r, hilo.ways("lo", r), r)
        for choice in ("hi", "lo"):
            k = hilo.ways(choice, r)
            check("множитель шага = 13/k * 36/37 (r=%d %s)" % (r, choice), hilo.step_multiplier(k), F(13, k) * F(36, 37))
            check("ход допустим, только если k < 13 (r=%d %s)" % (r, choice), hilo.can_move(choice, r), k < 13)
    check("hi на тузе запрещён, lo на короле запрещён", (hilo.can_move("hi", 1), hilo.can_move("lo", 13)), (False, False))
    check("lo на тузе допустим (k=1), hi на короле допустим (k=1)", (hilo.can_move("lo", 1), hilo.can_move("hi", 13)), (True, True))
    raises(ValueError, hilo.ways, "skip", 5)
    raises(ValueError, hilo.ways, "hi", 0)
    raises(ValueError, hilo.ways, "hi", 14)
    raises(ValueError, hilo.ways, "hi", True)
    check("вероятности", [hilo.probability_text(k) for k in (1, 6, 7, 12)], ["7.6", "46.1", "53.8", "92.3"])
    check("множитель текстом (вниз)", [hilo.multiplier_text(STEP(k)) for k in (1, 7, 12)], ["12.64", "1.80", "1.05"])
    check("выплата = floor(ставка * M)", (hilo.payout(100, F(37, 36)), hilo.payout(7, F(3, 2)), hilo.payout(1000, F(1))), (102, 10, 1000))
    check("потолок выплаты", hilo.payout(10, F(5000)), 10_000)
    # равенство выигрывает для любого направления
    for r in range(1, 14):
        assert hilo.is_win("hi", r, r) and hilo.is_win("lo", r, r), r
        assert (hilo.is_win("hi", r, r + 1) if r < 13 else True) and not hilo.is_win("lo", r, r + 1) if r < 13 else True
    check("hi: меньше проигрывает, больше выигрывает", (hilo.is_win("hi", 7, 6), hilo.is_win("hi", 7, 8)), (False, True))
    check("lo: больше проигрывает, меньше выигрывает", (hilo.is_win("lo", 7, 8), hilo.is_win("lo", 7, 6)), (False, True))
    # число выигрышных достоинств совпадает с k
    for r in range(1, 14):
        for choice in ("hi", "lo"):
            check("k совпадает с перебором (r=%d %s)" % (r, choice), sum(1 for n in range(1, 14) if hilo.is_win(choice, r, n)), hilo.ways(choice, r))

    # ================= возврат: точный расчёт динамикой по всем состояниям =================
    # Игрок видит достоинство r, множитель M; стратегия выбирает действие; ожидаемая выплата в долях ставки. Правила берутся из hilo.
    TARGET_ONE = 36 / 37

    def ev_best(steps_left, rank, m, cap=True):
        """Играть ещё steps_left ходов, каждый раз выбирая допустимое направление с наибольшим k, затем забрать."""
        if steps_left == 0:
            return min(m, F(hilo.HILO_MAX_X)) if cap else m
        choice = max((c for c in ("hi", "lo") if hilo.can_move(c, rank)), key=lambda c: hilo.ways(c, rank))
        k = hilo.ways(choice, rank)
        total = F(0)
        for new in range(1, 14):
            if hilo.is_win(choice, rank, new):
                after = m * STEP(k)
                total += F(1, 13) * (F(hilo.HILO_MAX_X) if cap and after >= hilo.HILO_MAX_X else ev_best(steps_left - 1, new, after, cap))
        return total

    def ev_longshot(steps_left, rank, m):
        """То же, но выбирая направление с НАИМЕНЬШИМ k (длинные ставки): потолок вступает в игру."""
        if steps_left == 0:
            return min(m, F(hilo.HILO_MAX_X))
        choice = min((c for c in ("hi", "lo") if hilo.can_move(c, rank)), key=lambda c: hilo.ways(c, rank))
        k = hilo.ways(choice, rank)
        total = F(0)
        for new in range(1, 14):
            if hilo.is_win(choice, rank, new):
                after = m * STEP(k)
                total += F(1, 13) * (F(hilo.HILO_MAX_X) if after >= hilo.HILO_MAX_X else ev_longshot(steps_left - 1, new, after))
        return total

    uniform = lambda f: sum(F(1, 13) * f(r) for r in range(1, 14))
    # стратегия 1: после первого угаданного хода забрать: возврат ровно 36/37 из любой начальной карты
    for r in range(1, 14):
        check("один ход и вывод: ровно 36/37 (r=%d)" % r, ev_best(1, r, F(1)), F(36, 37))
    check("с любой стартовой картой в среднем 36/37", uniform(lambda r: ev_best(1, r, F(1))), F(36, 37))
    # «пропускать до крайней карты, затем играть один ход»: пропуск ничего не стоит и не меняет математику хода
    check("пропуск до крайней карты и один ход: 36/37", ev_best(1, 1, F(1)), F(36, 37))
    check("пропуск до крайней карты (король) и один ход: 36/37", ev_best(1, 13, F(1)), F(36, 37))
    # стратегия 2: играть 3 хода с лучшим направлением: (36/37)^3 < 36/37
    check("три хода без потолка: ровно (36/37)^3", uniform(lambda r: ev_best(3, r, F(1), cap=False)), F(36, 37) ** 3)
    for r in range(2, 13):
        check("три хода от средней карты (потолок недостижим): (36/37)^3 (r=%d)" % r, ev_best(3, r, F(1)), F(36, 37) ** 3)
    ev3 = uniform(lambda r: ev_best(3, r, F(1)))
    assert ev3 < F(36, 37) ** 3 < F(36, 37), "потолок только уменьшает возврат"
    # стратегия 3: самые длинные ставки 3 хода: потолок вступает, возврат строго меньше, чем без потолка
    ev_long = uniform(lambda r: ev_longshot(3, r, F(1)))
    ev_long_nocap = F(36, 37) ** 3
    assert ev_long < ev_long_nocap < F(36, 37), (float(ev_long), float(ev_long_nocap))
    # стратегия 4: играть лучшее направление, пока M < 1.5, затем забрать (число ходов случайно): возврат <= 36/37
    @lru_cache(maxsize=None)
    def ev_until(rank, m):
        if m >= F(3, 2):
            return m
        choice = max((c for c in ("hi", "lo") if hilo.can_move(c, rank)), key=lambda c: hilo.ways(c, rank))
        k = hilo.ways(choice, rank)
        return sum((F(1, 13) * ev_until(new, m * STEP(k)) for new in range(1, 14) if hilo.is_win(choice, rank, new)), F(0))
    ev4 = uniform(lambda r: ev_until(r, F(1)))
    assert ev4 <= F(36, 37), float(ev4)
    # общий принцип: ожидание множителя после любого хода = 36/37 * M (каждый ход справедлив с поправкой 36/37)
    for r in range(1, 14):
        for choice in ("hi", "lo"):
            if hilo.can_move(choice, r):
                k = hilo.ways(choice, r)
                check("E[M после хода] = 36/37 M (r=%d %s)" % (r, choice), F(k, 13) * STEP(k), F(36, 37))

    # ================= статистика: равномерность достоинств и мастей =================
    rng = random.Random(20261004)
    counts = [0] * 14
    suit_counts = {s: 0 for s in hilo.SUITS}
    N = 200_000
    for _ in range(N):
        rank, suit = hilo.draw_card(rng)
        counts[rank] += 1
        suit_counts[suit] += 1
    exp_rank = N / 13
    chi_rank = sum((counts[r] - exp_rank) ** 2 / exp_rank for r in range(1, 14))
    chi_suit = sum((c - N / 4) ** 2 / (N / 4) for c in suit_counts.values())
    assert chi_rank < 40, "chi-square достоинств %.1f (12 степеней свободы, порог 40)" % chi_rank
    assert chi_suit < 25, "chi-square мастей %.1f (3 степени свободы, порог 25)" % chi_suit
    assert min(counts[1:]) > 0 and counts[0] == 0
    # настоящий источник (SystemRandom) тоже равномерен на меньшей выборке и даёт только допустимые карты
    sysc = [0] * 14
    for _ in range(20_000):
        rank, suit = hilo.draw_card()
        assert 1 <= rank <= 13 and suit in hilo.SUITS
        sysc[rank] += 1
    assert sum((sysc[r] - 20_000 / 13) ** 2 / (20_000 / 13) for r in range(1, 14)) < 40

    # ================= раунд: старт, ход, равенство, пропуск =================
    path = new_db()
    add_player(path, A, 1_000_000)
    r = start(path, 1000, [(7, "H")])
    check("старт: ставка списана", player(path)[0], 999_000)
    check("старт: состояние", (r["status"], r["card"], r["history"], r["steps"], r["multiplier"], r["can_cashout"], r["payout_now"], r["replayed"]),
          ("active", {"rank": 7, "suit": "H", "how": "start"}, [], 0, "1.00", False, 1000, False))
    check("ходы после старта на 7", {c: (m["available"], m["probability"], m["multiplier"]) for c, m in r["moves"].items()},
          {"hi": (True, "53.8", "1.80"), "lo": (True, "53.8", "1.80")})
    check("в total_staked ставка на старте не идёт", player(path)[2], 0)
    raises(hilo.ActiveGameExists, start, path, 10, [(2, "S")])
    # равенство выигрывает для hi
    r = guess(path, "hi", [(7, "S")])
    check("равенство выигрывает (hi)", (r["status"], r["card"]["how"], r["steps"], r["multiplier"], r["card"]["rank"]), ("active", "tie", 1, "1.80", 7))
    check("история: прошлая карта с пометкой start", r["history"], [{"rank": 7, "suit": "H", "how": "start"}])
    check("ставка пошла в total_staked один раз", player(path)[2], 1000)
    # равенство выигрывает для lo
    r = guess(path, "lo", [(7, "D")])
    check("равенство выигрывает (lo)", (r["card"]["how"], r["steps"]), ("tie", 2))
    check("множитель = (13/7 * 36/37)^2", r["multiplier"], hilo.multiplier_text(STEP(7) ** 2))
    check("total_staked не растёт на следующем ходе", player(path)[2], 1000)
    # пропуск: множитель, ставка и число ходов не меняются; опыта нет
    xp_before, bal_before = player(path)[1], player(path)[0]
    r = guess(path, "skip", [(3, "C")])
    check("пропуск: карта заменена, множитель и шаги те же", (r["card"], r["steps"], r["multiplier"], r["status"]),
          ({"rank": 3, "suit": "C", "how": "skip"}, 2, hilo.multiplier_text(STEP(7) ** 2), "active"))
    check("пропуск: ставка, баланс и опыт не менялись", (player(path)[0], player(path)[1], r["bet"]), (bal_before, xp_before, 1000))
    for _ in range(30):   # пропусков сколько угодно
        r = guess(path, "skip", [(5, "S")])
    check("30 пропусков: игра идёт, множитель прежний", (r["status"], r["multiplier"], r["steps"]), ("active", hilo.multiplier_text(STEP(7) ** 2), 2))
    check("история ограничена 12 картами", len(r["history"]), hilo.HISTORY_SHOWN)
    # проигрыш: ставка потеряна, показана выпавшая карта
    r = guess(path, "lo", [(12, "H")])    # на 5 «ниже или равно», выпала дама
    check("проигрыш: статус и карта", (r["status"], r["card"], r["payout"], r["payout_now"], r["moves"], r["can_cashout"]),
          ("lost", {"rank": 12, "suit": "H", "how": "lose"}, 0, None, None, False))
    check("после проигрыша баланс не вернулся", player(path)[0], 999_000)
    check("в истории проигравшей партии есть предыдущая карта", r["history"][-1], {"rank": 5, "suit": "S", "how": "skip"})
    raises(hilo.NoActiveGame, guess, path, "hi", [(1, "S")])
    raises(hilo.NoActiveGame, cash, path)

    # ================= запрет хода с k = 13 =================
    path = new_db()
    add_player(path, A)
    start(path, 100, [(1, "S")])
    seq = Seq((5, "S"))
    raises(hilo.MoveForbidden, db.hilo_guess, A, rid(), "hi", NOW, path, seq)      # на тузе «выше или равно» выигрывает всегда
    check("запрещённый ход не тянул карту и не менял игру", (seq.calls, sql(path, "SELECT steps, status FROM hilo_games")[0]), (0, (0, "active")))
    check("запрещённый ход не засчитал ставку", player(path)[2], 0)
    r = db.hilo_state(A, now=NOW, db_path=path)
    check("клиенту hi недоступен, lo доступен", (r["moves"]["hi"]["available"], r["moves"]["lo"]["available"]), (False, True))
    check("hi: вероятность 100.0, множитель по формуле 36/37", (r["moves"]["hi"]["probability"], r["moves"]["hi"]["multiplier"]), ("100.0", "0.97"))
    path = new_db()
    add_player(path, A)
    start(path, 100, [(13, "S")])
    raises(hilo.MoveForbidden, guess, path, "lo", [(5, "S")])

    # ================= кэшаут: только после угаданного хода, выплата, XP, однократность =================
    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(7, "H")])
    raises(hilo.NothingToCashOut, cash, path)
    check("кэшаут до хода: партия на месте", sql(path, "SELECT status, steps FROM hilo_games")[0], ("active", 0))
    guess(path, "skip", [(4, "S")])
    raises(hilo.NothingToCashOut, cash, path)       # пропуск угаданным ходом не считается
    guess(path, "hi", [(9, "S")])                   # k = 14 - 4 = 10
    bal_before = player(path)[0]
    r = cash(path)
    m = STEP(10)
    check("кэшаут: выплата = floor(ставка * M)", (r["status"], r["payout"], r["multiplier"]), ("cashed", 1000 * m.numerator // m.denominator, hilo.multiplier_text(m)))
    check("баланс вырос на выплату", player(path)[0], bal_before + r["payout"])
    check("опыт по формуле bet * (1 - 36/37 / M)", player(path)[1], 1000 * (37 * m.numerator - 36 * m.denominator) // (37 * m.numerator))
    xp_once = player(path)[1]
    raises(hilo.NoActiveGame, cash, path)
    check("двойной кэшаут: ни выплаты, ни опыта второй раз", (player(path)[0], player(path)[1]), (bal_before + r["payout"], xp_once))
    # повтор того же request_id возвращает сохранённый ответ
    path = new_db()
    add_player(path, A)
    start(path, 1000, [(7, "H")])
    guess(path, "hi", [(9, "S")])
    first = cash(path, request_id="cash-req-0001")
    again = cash(path, request_id="cash-req-0001")
    check("повтор кэшаута: тот же результат с replayed", ({k: v for k, v in again.items() if k != "replayed"}, again["replayed"], first["replayed"]),
          ({k: v for k, v in first.items() if k != "replayed"}, True, False))
    check("повтор не платит второй раз", player(path)[0], 1_000_000 - 1000 + first["payout"])

    # ================= проигрыш: XP по множителю проигранного хода; пропуски XP не дают =================
    path = new_db()
    add_player(path, A)
    start(path, 1000, [(7, "H")])
    guess(path, "skip", [(7, "S")])
    check("пропуски опыта не дают", player(path)[1], 0)
    r = guess(path, "hi", [(2, "S")])               # k = 7: M цели = 13/7*36/37 = 1.8069; проигрыш
    mt = STEP(7)
    check("опыт проигрыша: ставка * (1 - 36/37 / M хода)", player(path)[1], 1000 * (37 * mt.numerator - 36 * mt.denominator) // (37 * mt.numerator))
    check("после проигрыша опыт в ответе совпадает с базой", (r["xp"], r["level"]), (player(path)[1], levels.profile_level(player(path)[1])))
    # отыгранный второй ход: M цели = M * шаг
    path = new_db()
    add_player(path, A)
    start(path, 1000, [(7, "H")])
    guess(path, "hi", [(9, "S")])                   # k = 7
    guess(path, "lo", [(12, "S")])                  # на 9 k = 9: выигрыш (12 > 9? нет: lo проигрывает при 12)
    # lo от 9: выигрыш при <= 9, выпала 12: проигрыш; M цели = STEP(7)*STEP(9)
    mt = STEP(7) * STEP(9)
    check("опыт проигрыша на втором ходу", player(path)[1], 1000 * (37 * mt.numerator - 36 * mt.denominator) // (37 * mt.numerator))
    check("статус lost", sql(path, "SELECT status, payout FROM hilo_games")[0], ("lost", 0))

    # ================= потолок ×1000 =================
    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(1, "S")])
    guess(path, "lo", [(1, "H")])
    guess(path, "lo", [(1, "D")])
    mv = db.hilo_state(A, now=NOW, db_path=path)["moves"]["lo"]
    check("ход, который упрётся в потолок: множитель не выше 1000.00, выплата 1000 ставок", (mv["multiplier"], mv["payout"]), ("1000.00", 1_000_000))
    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(1, "S")])
    r = guess(path, "lo", [(1, "H")])               # k = 1, равенство выигрывает: M = 468/37 = 12.64
    r = guess(path, "lo", [(1, "D")])               # 159.9
    check("до потолка: игра идёт", (r["status"], r["steps"]), ("active", 2))
    assert hilo.payout(1000, hilo.frac(sql(path, "SELECT mult_num FROM hilo_games")[0][0], sql(path, "SELECT mult_den FROM hilo_games")[0][0])) < 1_000_000
    r = guess(path, "lo", [(1, "C")])               # 2022: потолок
    check("потолок: статус capped, выплата ровно 1000 * ставка", (r["status"], r["payout"], r["payout_now"], r["moves"], r["card"]["how"]), ("capped", 1_000_000, None, None, "tie"))
    check("потолок: баланс", player(path)[0], 1_000_000 - 1000 + 1_000_000)
    check("потолок: опыт при M цели = 1000", player(path)[1], 1000 * (37 * 1000 - 36) // (37 * 1000))
    check("потолок: партия закрыта", sql(path, "SELECT status FROM hilo_games")[0][0], "capped")
    raises(hilo.NoActiveGame, cash, path)
    # потолок и MAX_SAFE_INT: максимальная ставка проекта
    path = new_db()
    add_player(path, A, hilo.HILO_MAX_BET)
    start(path, hilo.HILO_MAX_BET, [(1, "S")])
    for card in ((1, "H"), (1, "D"), (1, "C")):
        r = guess(path, "lo", [card])
    check("максимальная ставка: выплата 1000 * 10**9, не больше MAX_SAFE_INT", (r["status"], r["payout"], r["payout"] <= MAX_SAFE_INT), ("capped", 10 ** 12, True))
    check("баланс точный", player(path)[0], 10 ** 12)
    raises(ValueError, db.hilo_start, A, rid(), hilo.HILO_MAX_BET + 1, NOW, path)
    raises(ValueError, db.hilo_start, A, rid(), 0, NOW, path)
    raises(ValueError, db.hilo_start, A, rid(), True, NOW, path)
    # баланс у потолка точных чисел: зачисляется только то, что помещается
    path = new_db()
    add_player(path, A, 1000)
    start(path, 1000, [(1, "S")])
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 10, A))
    for card in ((1, "H"), (1, "D"), (1, "C")):
        guess(path, "lo", [card])
    check("зачисление не выходит за MAX_SAFE_INT", player(path)[0], MAX_SAFE_INT)

    # ================= автозакрытие брошенных партий (как у мин) =================
    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(7, "H")], now=NOW)
    check("до срока партия жива", db.settle_expired_hilo(A, now=NOW + DAY - 1, db_path=path), False)
    check("через 24 часа без ходов нет угаданных: возврат ставки", db.settle_expired_hilo(A, now=NOW + DAY, db_path=path), True)
    check("возврат ставки без опыта и total_staked", (player(path), sql(path, "SELECT status, payout, auto FROM hilo_games")[0]),
          ((1_000_000, 0, 0), ("refunded", 1000, 1)))
    check("повторное закрытие ничего не делает", db.settle_expired_hilo(A, now=NOW + DAY, db_path=path), False)
    start(path, 1000, [(7, "H")], now=NOW)
    guess(path, "hi", [(9, "S")], now=NOW + 100)
    st = sql(path, "SELECT mult_num, mult_den FROM hilo_games WHERE status = 'active'")[0]
    check("ход обновляет срок: до 24 часов после хода жива", db.settle_expired_hilo(A, now=NOW + 100 + DAY - 1, db_path=path), False)
    n = db.close_expired_hilo(now=NOW + 100 + DAY, db_path=path)
    check("фоновая очистка закрыла одну партию", n, 1)
    row = sql(path, "SELECT status, payout, auto FROM hilo_games WHERE id = (SELECT MAX(id) FROM hilo_games)")[0]
    check("автозакрытие с угаданным ходом: выплата по текущему множителю", row, ("cashed", hilo.payout(1000, hilo.frac(*st)), 1))
    check("опыт за автокэшаут", player(path)[1] > 0, True)
    # игра с ходом через гейт старта: просроченная закрывается перед новым действием
    start(path, 1000, [(7, "H")], now=NOW + 10 * DAY)
    r = start(path, 1000, [(8, "H")], now=NOW + 12 * DAY)
    check("новая партия после просроченной: старая закрыта возвратом", (r["status"], sql(path, "SELECT COUNT(*) FROM hilo_games WHERE status = 'refunded'")[0][0]), ("active", 2))

    # ================= две активные партии невозможны (индекс) =================
    path = new_db()
    add_player(path, A)
    start(path, 100, [(7, "H")])
    try:
        sql(path, "INSERT INTO hilo_games (telegram_id, bet, card_rank, card_suit, steps, mult_num, mult_den, hist_json, status, payout, "
                  "staked_counted, auto, created_at, updated_at) VALUES (?, 1, 5, 'S', 0, '1', '1', '[]', 'active', 0, 0, 0, 0, 0)", (A,))
        raise AssertionError("вторая активная партия вставилась")
    except sqlite3.IntegrityError:
        pass
    check("в базе нет колонки со следующей картой", [c[1] for c in sql(path, "PRAGMA table_info(hilo_games)")],
          ["id", "telegram_id", "bet", "card_rank", "card_suit", "steps", "mult_num", "mult_den", "hist_json", "status", "payout",
           "staked_counted", "auto", "created_at", "updated_at", "finished_at"])
    # недостаточно фишек
    path = new_db()
    add_player(path, A, 50)
    raises(InsufficientFunds, start, path, 100, [(7, "H")])
    check("при нехватке ничего не изменилось", (player(path)[0], sql(path, "SELECT COUNT(*) FROM hilo_games")[0][0]), (50, 0))

    # ================= будущая карта не существует до хода =================
    path = new_db()
    add_player(path, A)
    seq = Seq((7, "H"), (9, "S"))
    db.hilo_start(A, rid(), 100, now=NOW, db_path=path, rng=seq)
    check("на старте тянется только первая карта", seq.calls, 2)
    for _ in range(3):
        db.hilo_state(A, now=NOW, db_path=path)
    db.get_player(A, now=NOW, db_path=path)
    check("чтение состояния карт не тянет", seq.calls, 2)
    db.hilo_guess(A, rid(), "hi", now=NOW, db_path=path, rng=seq)
    check("карта тянется в момент хода (ещё две единицы случайности)", seq.calls, 4)
    export = db.get_player_export(A, db_path=path)
    dump = json.dumps(export, ensure_ascii=False)
    check("в /mydata активная партия только флагом", (export["hilo_active"], export["hilo_games"]), (True, []))
    assert '"card' not in dump and "hist" not in dump and "rank" not in dump.lower().replace("ranking", ""), dump[:200]

    # ================= повтор хода с одним request_id и конфликт параметров =================
    path = new_db()
    add_player(path, A)
    start(path, 1000, [(7, "H")])
    first = db.hilo_guess(A, "same-req-0001", "hi", now=NOW, db_path=path, rng=Seq((10, "S")))
    seq2 = Seq((1, "S"))     # при повторе генератор не должен быть нужен
    again = db.hilo_guess(A, "same-req-0001", "hi", now=NOW, db_path=path, rng=seq2)
    check("повтор хода: тот же ответ, карта не тянулась", (seq2.calls, again["card"], again["replayed"], again["steps"]), (0, first["card"], True, 1))
    raises(hilo.RequestConflict, db.hilo_guess, A, "same-req-0001", "lo", NOW, path, Seq((1, "S")))
    raises(hilo.RequestConflict, db.hilo_cashout, A, "same-req-0001", NOW, path)
    raises(hilo.RequestConflict, db.hilo_start, A, "same-req-0001", 500, NOW, path)
    add_player(path, B)
    s1 = db.hilo_start(B, "req-start-0001", 100, now=NOW, db_path=path, rng=Seq((5, "S")))
    s2 = db.hilo_start(B, "req-start-0001", 100, now=NOW, db_path=path, rng=Seq((6, "S")))
    check("повтор старта: та же карта, ставка списана один раз", (s2["card"], s2["replayed"], player(path, B)[0]), (s1["card"], True, 1_000_000 - 100))
    raises(hilo.RequestConflict, db.hilo_start, B, "req-start-0001", 200, NOW, path, Seq((5, "S")))
    raises(ValueError, db.hilo_guess, A, rid(), "up", NOW, path)
    raises(ValueError, db.hilo_guess, A, rid(), None, NOW, path)

    # ================= гонки: 20 параллельных ходов и кэшаутов на одной партии =================
    def parallel(fn, n=20):
        barrier = threading.Barrier(n)

        def work(i):
            barrier.wait()
            try:
                return ("ok", fn(i))
            except hilo.HiloError as exc:
                return ("err", exc.code)
        with ThreadPoolExecutor(n) as pool:
            return list(pool.map(work, range(n)))

    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(7, "H")])
    guess(path, "hi", [(9, "S")])
    bal0 = player(path)[0]
    res = parallel(lambda i: db.hilo_cashout(A, "race-cash-%04d" % i, now=NOW, db_path=path))
    ok = [x for x in res if x[0] == "ok"]
    check("20 параллельных кэшаутов: один успешный", (len(ok), sorted({x[1] for x in res if x[0] == "err"})), (1, ["no_active_game"]))
    check("выплата ровно один раз", player(path)[0], bal0 + ok[0][1]["payout"])
    check("опыт ровно один раз", player(path)[1], hilo.xp_for(1000, STEP(7)))
    # один request_id: 20 одинаковых запросов дают один результат (один настоящий, остальные повторы)
    path = new_db()
    add_player(path, A, 1_000_000)
    start(path, 1000, [(7, "H")])
    guess(path, "hi", [(9, "S")])
    bal0 = player(path)[0]
    res = parallel(lambda i: db.hilo_cashout(A, "race-same-0001", now=NOW, db_path=path))
    check("20 запросов с одним request_id: все успешны, один не повтор", (sum(1 for x in res if x[0] == "ok"), sum(1 for x in res if x[0] == "ok" and not x[1]["replayed"])), (20, 1))
    check("выплачено один раз", player(path)[0], bal0 + hilo.payout(1000, STEP(7)))
    # смесь: 10 ходов и 10 кэшаутов; итог один
    for trial in range(3):
        path = new_db()
        add_player(path, A, 1_000_000)
        start(path, 1000, [(7, "H")])
        guess(path, "hi", [(8, "S")])

        def mixed(i):
            if i % 2:
                return db.hilo_cashout(A, "mix-c-%04d" % i, now=NOW, db_path=path)
            return db.hilo_guess(A, "mix-g-%04d" % i, "hi", now=NOW, db_path=path, rng=Seq((13, "S")))
        parallel(mixed)
        rows = sql(path, "SELECT status, payout, steps FROM hilo_games")
        check("после смеси одна партия", len(rows), 1)
        status, paid, steps = rows[0]
        check("итог безопасен (баланс = начало - ставка + выплата)", player(path)[0], 1_000_000 - 1000 + paid)
        assert status in ("cashed", "active", "lost", "capped"), status
        num, den = sql(path, "SELECT mult_num, mult_den FROM hilo_games")[0]
        m_now = hilo.frac(num, den)
        if status == "cashed":
            check("опыт кэшаута начислен ровно один раз", player(path)[1], hilo.xp_for(1000, m_now))
            check("выплата по множителю партии", paid, hilo.payout(1000, m_now))
        elif status == "capped":
            check("потолок под гонкой: выплата 1000 ставок, опыт один раз", (paid, player(path)[1]), (1_000_000, hilo.xp_for(1000, F(1000))))
        elif status == "active":
            check("активная партия: опыта и выплаты нет", (player(path)[1], paid), (0, 0))
        else:
            assert player(path)[1] > 0 and paid == 0

    # ================= очистка по сроку хранения и удаление данных =================
    path = new_db()
    add_player(path, A)
    start(path, 100, [(7, "H")], now=NOW)
    guess(path, "hi", [(2, "S")], now=NOW)                   # проигрыш, finished_at = NOW
    start(path, 100, [(7, "H")], now=NOW)                    # активная
    res = db.purge_old_data(now=NOW + 40 * DAY, db_path=path, rounds_days=30)
    check("завершённая партия старше срока удалена, активная осталась", (res["hilo_games"], sql(path, "SELECT status FROM hilo_games")[0][0], res["hilo_actions"] > 0), (1, "active", True))
    res = db.purge_old_data(now=NOW + 40 * DAY, db_path=path, rounds_days=30)
    check("повторная очистка ничего не удаляет", (res["hilo_games"], res["hilo_actions"]), (0, 0))
    counts = db.delete_player_data(A, db_path=path, now=NOW)
    check("удаление данных: партии и действия стёрты", (counts["hilo_games"], sql(path, "SELECT COUNT(*) FROM hilo_games")[0][0], sql(path, "SELECT COUNT(*) FROM hilo_actions")[0][0]), (1, 0, 0))

    # ================= API =================
    path = new_db()
    far = int(time.time()) + 10 * DAY    # API работает по настоящим часам: начисление по часам не должно мешать точным суммам
    add_player(path, A, 1_000_000, accrual=far)
    add_player(path, B, 1_000_000, accrual=far)
    app = create_app(TOKEN, ["https://example.invalid"], db_path=path)
    client = TestClient(app)
    auth = lambda uid: {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()))}

    def post(url, body, uid=A):
        return client.post(url, json=body, headers=auth(uid))

    def scripted(*cards):
        seq = list(cards)
        return mock.patch.object(hilo, "draw_card", side_effect=lambda rng=None: seq.pop(0))

    # без подписи: 401
    check("без подписи 401", client.post("/api/hilo/start", json={"request_id": rid(), "bet": 10}).status_code, 401)
    check("state без подписи 401", client.get("/api/hilo/state").status_code, 401)
    st = client.get("/api/hilo/state", headers=auth(A)).json()
    check("state без игры", (st["status"], st["card"], st["moves"], st["history"], st["multiplier"], st["cap"]), ("none", None, None, [], "1.00", "1000.00"))
    # ошибки параметров
    for body in ({}, {"request_id": rid()}, {"request_id": rid(), "bet": 0}, {"request_id": rid(), "bet": -5}, {"request_id": rid(), "bet": 10 ** 9 + 1},
                 {"request_id": rid(), "bet": True}, {"request_id": rid(), "bet": 1.5}, {"request_id": rid(), "bet": "10"},
                 {"request_id": rid(), "bet": 10, "extra": 1}, {"request_id": "x", "bet": 10}):
        r = post("/api/hilo/start", body)
        check("400 %s" % json.dumps(body)[:50], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    for body in ({}, {"request_id": rid()}, {"request_id": rid(), "choice": "up"}, {"request_id": rid(), "choice": None}, {"request_id": rid(), "choice": "hi", "x": 1}):
        r = post("/api/hilo/guess", body)
        check("400 guess %s" % json.dumps(body)[:50], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    check("400 cashout с лишним ключом", post("/api/hilo/cashout", {"request_id": rid(), "x": 1}).status_code, 400)
    check("409 ход без партии", (post("/api/hilo/guess", {"request_id": rid(), "choice": "hi"}).json()), {"detail": "no_active_game"})
    check("409 кэшаут без партии", (post("/api/hilo/cashout", {"request_id": rid()}).json()), {"detail": "no_active_game"})
    check("при ошибках ничего не списано", player(path)[0], 1_000_000)
    r = post("/api/hilo/start", {"request_id": rid(), "bet": 2_000_000})
    check("409 нехватка фишек", (r.status_code, r.json()), (409, {"detail": "insufficient_funds"}))
    # полный раунд
    with scripted((1, "S"), (5, "H")):
        r = post("/api/hilo/start", {"request_id": "api-start-0001", "bet": 1000})
    body = r.json()
    check("API старт", (r.status_code, body["status"], body["card"], body["replayed"], body["balance"]), (200, "active", {"rank": 1, "suit": "S", "how": "start"}, False, 999_000))
    assert set(body) == {"status", "bet", "card", "history", "steps", "multiplier", "payout_now", "can_cashout", "moves", "cap", "payout",
                         "balance", "level", "xp", "auto", "replayed"}, sorted(body)
    check("ходы на тузе: hi запрещён", body["moves"]["hi"]["available"], False)
    r = post("/api/hilo/start", {"request_id": rid(), "bet": 10})
    check("409 вторая партия", (r.status_code, r.json()), (409, {"detail": "active_game_exists"}))
    check("409 запрещённый ход", post("/api/hilo/guess", {"request_id": rid(), "choice": "hi"}).json(), {"detail": "move_forbidden"})
    check("409 кэшаут до угаданного хода", post("/api/hilo/cashout", {"request_id": rid()}).json(), {"detail": "nothing_to_cash_out"})
    me = client.get("/api/me", headers=auth(A)).json()
    check("/api/me: активная игра hilo", me["active_game"], "hilo")
    with scripted((5, "H")):
        r = post("/api/hilo/guess", {"request_id": "api-guess-0001", "choice": "lo"})   # на тузе lo: k = 1; выпала 5 > 1: проигрыш
    check("API проигрыш", (r.json()["status"], r.json()["card"]["rank"], r.json()["payout"]), ("lost", 5, 0))
    check("после конца active_game пуст", client.get("/api/me", headers=auth(A)).json()["active_game"], None)
    st = client.get("/api/hilo/state", headers=auth(A)).json()
    check("state показывает последнюю партию", (st["status"], st["card"]["rank"], st["payout"]), ("lost", 5, 0))
    # повтор и конфликт через API
    r = post("/api/hilo/guess", {"request_id": "api-guess-0001", "choice": "lo"})
    check("повтор guess: тот же ответ, replayed", (r.status_code, r.json()["status"], r.json()["replayed"]), (200, "lost", True))
    r = post("/api/hilo/guess", {"request_id": "api-guess-0001", "choice": "hi"})
    check("тот же request_id с другим выбором: 409 request_conflict", (r.status_code, r.json()), (409, {"detail": "request_conflict"}))
    r = post("/api/hilo/start", {"request_id": "api-start-0001", "bet": 777})
    check("тот же request_id старта с другой ставкой: 409", (r.status_code, r.json()), (409, {"detail": "request_conflict"}))
    # выигрыш и кэшаут
    with scripted((7, "D"), (7, "C")):
        post("/api/hilo/start", {"request_id": rid(), "bet": 1000})
        r = post("/api/hilo/guess", {"request_id": rid(), "choice": "hi"})
    check("равенство выигрывает через API", (r.json()["card"]["how"], r.json()["steps"], r.json()["can_cashout"]), ("tie", 1, True))
    r = post("/api/hilo/cashout", {"request_id": rid()})
    check("API кэшаут", (r.json()["status"], r.json()["payout"], r.json()["auto"]), ("cashed", hilo.payout(1000, STEP(7)), False))
    # возобновление
    with scripted((9, "S")):
        post("/api/hilo/start", {"request_id": rid(), "bet": 500})
    check("возобновление: state отдаёт активную партию", (client.get("/api/hilo/state", headers=auth(A)).json()["status"], client.get("/api/me", headers=auth(A)).json()["active_game"]), ("active", "hilo"))
    # чужой игрок не видит и не трогает партию
    check("у другого игрока партии нет", client.get("/api/hilo/state", headers=auth(B)).json()["status"], "none")
    # лимит частоты (группа write)
    limiter = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1"}))
    app2 = create_app(TOKEN, ["https://example.invalid"], db_path=path, rate_limiter=limiter)
    c2 = TestClient(app2)
    codes = [c2.post("/api/hilo/guess", json={"request_id": rid(), "choice": "skip"}, headers=auth(B)).status_code for _ in range(5)]
    check("лимит частоты write для хило", (codes[:3], codes[3:]), ([409, 409, 409], [429, 429]))

    # ================= форма ответов совпадает с docs/examples/hilo.json =================
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "hilo.json"), encoding="utf-8"))
    NULLABLE = {"bet": int, "card": dict, "payout_now": int, "moves": dict, "payout": int}

    def same_shape(name, body, example):
        assert set(body) == set(example), "%s: поля %s, в примере %s" % (name, sorted(body), sorted(example))
        for k, ex in example.items():
            v = body[k]
            if k in NULLABLE:
                assert v is None or type(v) is NULLABLE[k], "%s.%s: %r" % (name, k, v)
            else:
                assert type(v) is type(ex), "%s.%s: %r, в примере %s" % (name, k, v, type(ex).__name__)
        for c in ([body["card"]] if body["card"] else []) + body["history"]:
            assert set(c) == {"rank", "suit", "how"} and type(c["rank"]) is int and c["suit"] in hilo.SUITS
            assert c["how"] in ("start", "win", "tie", "skip", "lose")
        if body["moves"]:
            assert set(body["moves"]) == {"hi", "lo"}
            for mv in body["moves"].values():
                assert set(mv) == {"available", "probability", "multiplier", "payout"} and type(mv["available"]) is bool
                assert type(mv["probability"]) is str and type(mv["multiplier"]) is str and type(mv["payout"]) is int

    path = new_db()
    far = int(time.time()) + 10 * DAY
    add_player(path, A, 1_000_000, accrual=far)
    ex_client = TestClient(create_app(TOKEN, ["https://example.invalid"], db_path=path))
    ex_auth = {"Authorization": "tma " + make_init_data(TOKEN, user_id=A, auth_date=int(time.time()))}
    ex_post = lambda url, body: ex_client.post(url, json=body, headers=ex_auth).json()
    same_shape("none", ex_client.get("/api/hilo/state", headers=ex_auth).json(), examples["none"])
    for name, cards in (("active_start", [(7, "H")]), ("active_forbidden_hi", [(1, "S")])):
        with scripted(*cards):
            same_shape(name, ex_post("/api/hilo/start", {"request_id": rid(), "bet": 100}), examples[name])
        with scripted((5, "S")):
            ex_post("/api/hilo/guess", {"request_id": rid(), "choice": "lo"})   # закрыть партию проигрышем или продолжить
        db.settle_expired_hilo(A, now=int(time.time()) + 2 * DAY, db_path=path)
    with scripted((7, "H"), (7, "C"), (3, "D")):
        ex_post("/api/hilo/start", {"request_id": rid(), "bet": 100})
        same_shape("tie", ex_post("/api/hilo/guess", {"request_id": rid(), "choice": "hi"}), examples["active_after_steps"])
        same_shape("skip", ex_post("/api/hilo/guess", {"request_id": rid(), "choice": "skip"}), examples["active_after_steps"])
        same_shape("cashed", ex_post("/api/hilo/cashout", {"request_id": rid()}), examples["finished_cashed"])
    with scripted((7, "H"), (12, "S")):
        ex_post("/api/hilo/start", {"request_id": rid(), "bet": 100})
        same_shape("lost", ex_post("/api/hilo/guess", {"request_id": rid(), "choice": "lo"}), examples["finished_lost"])
    with scripted((1, "S"), (1, "H"), (1, "D"), (1, "C")):
        ex_post("/api/hilo/start", {"request_id": rid(), "bet": 100})
        for _ in range(3):
            body = ex_post("/api/hilo/guess", {"request_id": rid(), "choice": "lo"})
        same_shape("capped", body, examples["finished_capped"])
    same_shape("state finished", ex_client.get("/api/hilo/state", headers=ex_auth).json(), examples["finished_capped"])
    with scripted((7, "H")):
        ex_post("/api/hilo/start", {"request_id": rid(), "bet": 100})
    db.settle_expired_hilo(A, now=int(time.time()) + 2 * DAY, db_path=path)
    refunded = ex_client.get("/api/hilo/state", headers=ex_auth).json()
    same_shape("refunded auto", refunded, examples["finished_refunded_auto"])
    check("возврат по просрочке: auto", (refunded["status"], refunded["auto"]), ("refunded", True))
    for k, v in examples["errors"].items():
        check("пример ошибки " + k, v, {"detail": k})
    # примеры не противоречат правилам: ходы, множители и выплаты пересчитываются по формулам из hilo.py
    for name, (bet, rank, ks) in {"active_start": (100, 7, []), "active_forbidden_hi": (100, 1, []), "active_after_steps": (100, 4, [7])}.items():
        m = F(1)
        for k in ks:
            m *= hilo.step_multiplier(k)
        ex = examples[name]
        check("пример %s: ставка, карта" % name, (ex["bet"], ex["card"]["rank"]), (bet, rank))
        check("пример %s: множитель и payout_now" % name, (ex["multiplier"], ex["payout_now"], ex["can_cashout"]), (hilo.multiplier_text(m), hilo.payout(bet, m), bool(ks)))
        for choice in ("hi", "lo"):
            k = hilo.ways(choice, rank)
            after = m * hilo.step_multiplier(k)
            check("пример %s: ход %s" % (name, choice), ex["moves"][choice],
                  {"available": k < 13, "probability": hilo.probability_text(k), "multiplier": hilo.multiplier_text(min(after, F(1000))), "payout": hilo.payout(bet, after)})
    check("пример капа: выплата 1000 ставок, опыт по формуле", (examples["finished_capped"]["payout"], examples["finished_capped"]["xp"]), (100_000, hilo.xp_for(100, F(1000))))
    check("пример проигрыша: опыт по формуле", examples["finished_lost"]["xp"], hilo.xp_for(100, STEP(7)))
    check("пример вывода: выплата по формуле", examples["finished_cashed"]["payout"], hilo.payout(100, STEP(7)))

    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "историю ваших партий в хило" in privacy and "незавершённой партии в хило" in privacy and "История партий в хило хранится 30 дней" in privacy
    assert "Если вы не продолжаете начатую партию в хило 24 часа" in privacy

    # ================= просроченная партия закрывается при /api/me и в фоне =================
    path = new_db()
    add_player(path, A, 1_000_000, accrual=int(time.time()) + 10 * DAY)
    start(path, 1000, [(7, "H")], now=int(time.time()) - 2 * DAY)
    app3 = create_app(TOKEN, ["https://example.invalid"], db_path=path)
    me = TestClient(app3).get("/api/me", headers=auth(A)).json()
    check("/api/me закрыла просроченную партию возвратом", (me["active_game"], me["balance"] >= 1_000_000), (None, True))

    # ================= логи и выгрузка: нет карт, ставок, идентификаторов =================
    for line in cap.lines:
        assert "hist" not in line and "rank" not in line.lower() and str(A) not in line and str(B) not in line, line
    # сообщения об удалении данных и очистке считают партии
    assert any(l.startswith("Очистка старых данных:") and "хило=" in l for l in cap.lines)
finally:
    mock.patch.stopall()
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
