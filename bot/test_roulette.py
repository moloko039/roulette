import os
import sqlite3
import tempfile
import time

from db import get_player, init_db, spin_roulette
from roulette import (MAX_BETS, MAX_SAFE_INT, BalanceLimit, InsufficientFunds, InvalidBets, RequestConflict,

                      bet_payout, is_win, max_payout, settle, validate_bets, validate_request_id)


FUT_ACCRUAL = int(time.time()) + 10 * 86400   # старая база в тесте миграции: метка в будущем, начисления нет


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def bet(t, v=None, a=10):
    return {"type": t, "value": v, "amount": a}


def invalid(raw):
    try:
        validate_bets(raw)
    except InvalidBets:
        return True
    return False


REDS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

# ---------- выигрыш по каждому типу ставки ----------
for n in range(37):
    check(f"number {n}", is_win(bet("number", n), n), True)
    check(f"number {n} мимо", is_win(bet("number", (n + 1) % 37), n), False)
    if n == 0:
        for t in ("red", "black", "even", "odd"):
            check(f"ноль и {t}", is_win(bet(t), 0), False)
        for t in ("dozen", "column"):
            for v in (1, 2, 3):
                check(f"ноль и {t} {v}", is_win(bet(t, v), 0), False)
        continue
    check(f"red {n}", is_win(bet("red"), n), n in REDS)
    check(f"black {n}", is_win(bet("black"), n), n not in REDS)
    check(f"even {n}", is_win(bet("even"), n), n % 2 == 0)
    check(f"odd {n}", is_win(bet("odd"), n), n % 2 == 1)

# границы дюжин
for n, d in [(1, 1), (12, 1), (13, 2), (24, 2), (25, 3), (36, 3)]:
    for v in (1, 2, 3):
        check(f"дюжина {v} число {n}", is_win(bet("dozen", v), n), v == d)

# три колонки: 1 — 1,4,7…34; 2 — 2,5…35; 3 — 3,6…36
for col, first in [(1, 1), (2, 2), (3, 3)]:
    members = set(range(first, 37, 3))
    for n in range(1, 37):
        check(f"колонка {col} число {n}", is_win(bet("column", col), n), n in members)
check("колонка 1", sorted(n for n in range(1, 37) if is_win(bet("column", 1), n))[:3], [1, 4, 7])
check("колонка 3", sorted(n for n in range(1, 37) if is_win(bet("column", 3), n))[-2:], [33, 36])

# выплаты: возврат ставки плюс прибыль
check("red выплата", bet_payout(bet("red", None, 100), 1), 200)
check("dozen выплата", bet_payout(bet("dozen", 1, 100), 5), 300)
check("column выплата", bet_payout(bet("column", 2, 100), 5), 300)
check("number выплата", bet_payout(bet("number", 17, 10), 17), 360)
check("проигрыш", bet_payout(bet("number", 17, 10), 18), 0)

# сумма нескольких ставок: на 7 (красное, нечёт, дюжина 1, колонка 1)
bets = [bet("number", 7, 10), bet("red", None, 20), bet("odd", None, 30), bet("dozen", 1, 40),
        bet("column", 1, 50), bet("black", None, 60)]
check("несколько ставок на 7", settle(bets, 7), (210, 360 + 40 + 60 + 120 + 150))
check("несколько ставок на 0", settle(bets, 0), (210, 0))

# закрытие всего поля: все 47 ставок по 1
field = ([bet("number", n, 1) for n in range(37)] + [bet("dozen", d, 1) for d in (1, 2, 3)]
         + [bet("column", c, 1) for c in (1, 2, 3)] + [bet(t, None, 1) for t in ("red", "black", "even", "odd")])
check("всего ставок", len(field), MAX_BETS)
check("поле валидно", len(validate_bets(field)), 47)
for n in range(1, 37):
    check(f"поле, число {n}", settle(field, n), (47, 36 + 3 + 3 + 2 + 2))
check("поле, ноль", settle(field, 0), (47, 36))
check("max_payout поля", max_payout(field), 46)

# ---------- невалидные данные ----------
good = bet("number", 5, 10)
assert validate_bets([good]) == [good]
assert validate_bets([bet("red")]) == [bet("red")]
bad_cases = {
    "не список": {"a": 1},
    "пустой список": [],
    "больше 47": [bet("number", n % 37, 1) for n in range(48)],
    "true вместо amount": [bet("number", 5, True)],
    "true вместо value": [bet("number", True, 10)],
    "дробный amount": [bet("number", 5, 1.5)],
    "дробный но целый amount": [bet("number", 5, 10.0)],
    "1e3": [bet("number", 5, 1e3)],
    "строка amount": [bet("number", 5, "10")],
    "строка value": [bet("number", "5", 10)],
    "отрицательный amount": [bet("number", 5, -1)],
    "нулевой amount": [bet("number", 5, 0)],
    "amount больше MAX_SAFE_INT": [bet("number", 5, MAX_SAFE_INT + 1)],
    "сумма больше MAX_SAFE_INT": [bet("number", 5, MAX_SAFE_INT), bet("number", 6, 1)],
    "дубликат": [bet("number", 5, 10), bet("number", 5, 20)],
    "value у red": [bet("red", 1)],
    "value у even": [bet("even", 0)],
    "value строкой у red": [bet("red", "x")],
    "неизвестный тип": [bet("split", 1)],
    "тип не строка": [bet(5, 1)],
    "number 37": [bet("number", 37)],
    "number -1": [bet("number", -1)],
    "dozen 0": [bet("dozen", 0)],
    "dozen 4": [bet("dozen", 4)],
    "column 0": [bet("column", 0)],
    "column 4": [bet("column", 4)],
    "dozen без value": [bet("dozen", None)],
    "ставка не объект": [[1, 2, 3]],
    "лишнее поле": [{"type": "red", "value": None, "amount": 1, "extra": 1}],
    "нет поля": [{"type": "red", "amount": 1}],
}
for name, raw in bad_cases.items():
    assert invalid(raw), f"принято: {name}"
# граничные допустимые значения
assert not invalid([bet("number", 0, MAX_SAFE_INT)])
assert not invalid([bet("number", 36, 1), bet("dozen", 3, 1), bet("column", 3, 1)])

for rid in ["abcdefgh", "a" * 64, "12345678-abcd-ef01"]:
    validate_request_id(rid)
for rid in [None, 5, "short", "a" * 65, "abcd efgh", "abcdefgh\n", "абвгдежз12", "abcdefg_h"]:
    try:
        validate_request_id(rid)
        raise AssertionError(f"request_id принят: {rid!r}")
    except InvalidBets:
        pass

# ---------- спин в базе (случайность подменена) ----------
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    now = 1_700_000_000
    rng = lambda n: 17  # noqa: E731 — 17 чёрное, нечётное, 2-я дюжина, колонка 2

    r = spin_roulette(1, "request-0001", [bet("number", 17, 10), bet("black", None, 20)], now=now, db_path=path, rng=rng)
    check("спин", (r["number"], r["stake_total"], r["payout_total"], r["net"], r["balance"], r["replayed"]),
          (17, 30, 400, 370, 1370, False))

    # ставка на весь баланс
    r = spin_roulette(2, "request-0002", [bet("black", None, 1000)], now=now, db_path=path, rng=rng)
    check("весь баланс", (r["balance"], r["net"]), (2000, 1000))
    r = spin_roulette(3, "request-0003", [bet("red", None, 1000)], now=now, db_path=path, rng=rng)
    check("весь баланс, проигрыш", (r["balance"], r["net"]), (0, -1000))

    # нехватка: баланс не меняется
    try:
        spin_roulette(3, "request-0004", [bet("red", None, 1)], now=now, db_path=path, rng=rng)
        raise AssertionError("ставка при нуле принята")
    except InsufficientFunds:
        pass
    check("баланс после отказа", get_player(3, now=now, db_path=path)["balance"], 0)

    # повтор с теми же ставками (порядок не важен): ничего не меняется, число прежнее
    again = spin_roulette(1, "request-0001", [bet("black", None, 20), bet("number", 17, 10)], now=now, db_path=path, rng=lambda n: 0)
    check("повтор", (again["number"], again["replayed"], again["balance"], again["stake_total"]), (17, True, 1370, 30))
    check("повтор: баланс в базе", get_player(1, now=now, db_path=path)["balance"], 1370)
    # тот же request_id с другими ставками: отказ, ничего не меняется
    for other_bets in ([bet("red", None, 999)], [bet("number", 17, 10)], [bet("number", 17, 10), bet("black", None, 21)]):
        try:
            spin_roulette(1, "request-0001", other_bets, now=now, db_path=path, rng=lambda n: 0)
            raise AssertionError("повтор с другими ставками принят")
        except RequestConflict:
            pass
    check("конфликт: баланс не менялся", get_player(1, now=now, db_path=path)["balance"], 1370)

    # другой игрок с тем же request_id — новый раунд
    other = spin_roulette(4, "request-0001", [bet("black", None, 10)], now=now, db_path=path, rng=rng)
    check("тот же request_id у другого игрока", (other["replayed"], other["balance"]), (False, 1010))

    # потратить можно начисленное: 5 часов = +500
    r = spin_roulette(5, "request-0005", [bet("number", 1, 1)], now=now, db_path=path, rng=rng)
    later = now + 5 * 3600
    r = spin_roulette(5, "request-0006", [bet("red", None, 1400)], now=later, db_path=path, rng=lambda n: 0)
    check("начисление тратится", (r["stake_total"], r["balance"]), (1400, 1000 - 1 + 500 - 1400))

    # число вне 0..36 от источника случайности не принимается, в базе ничего не остаётся
    before = get_player(6, now=now, db_path=path)["balance"]
    try:
        spin_roulette(6, "request-0007", [bet("red", None, 1)], now=now, db_path=path, rng=lambda n: 99)
        raise AssertionError("число 99 принято")
    except RuntimeError:
        pass
    conn = sqlite3.connect(path)
    check("раунд не записан", conn.execute("SELECT COUNT(*) FROM roulette_rounds WHERE request_id = 'request-0007'").fetchone()[0], 0)
    conn.close()
    check("баланс не тронут", get_player(6, now=now, db_path=path)["balance"], before)

    # balance_limit
    conn = sqlite3.connect(path)
    conn.execute("UPDATE players SET balance = ? WHERE telegram_id = 1", (MAX_SAFE_INT - 10,))
    conn.commit()
    conn.close()
    try:
        spin_roulette(1, "request-0008", [bet("number", 5, 1)], now=now, db_path=path, rng=rng)
        raise AssertionError("balance_limit не сработал")
    except BalanceLimit:
        pass
    check("баланс после balance_limit", get_player(1, now=now, db_path=path)["balance"], MAX_SAFE_INT - 10)
    # обычная ставка рядом с пределом проходит, если выигрыш не упрётся в него
    r = spin_roulette(1, "request-0009", [bet("red", None, 1)], now=now, db_path=path, rng=lambda n: 0)
    check("ставка рядом с пределом", r["balance"], MAX_SAFE_INT - 11)
finally:
    os.remove(path)

# ---------- миграция: база со старой схемой ----------
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE players (telegram_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL, "
                 "rate INTEGER NOT NULL, last_accrual INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("INSERT INTO players VALUES (42, 777, 100, %d, 1000)" % FUT_ACCRUAL)
    conn.commit()
    conn.close()

    init_db(path)
    init_db(path)  # повторный вызов безопасен

    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("таблицы", {"players", "roulette_rounds"} <= tables, True)
    check("игрок на месте", conn.execute("SELECT balance, rate, last_accrual, created_at FROM players WHERE telegram_id = 42").fetchone(),
          (777, 100, FUT_ACCRUAL, 1000))
    conn.close()
    check("игрок читается", get_player(42, now=1000, db_path=path)["balance"], 777)
finally:
    os.remove(path)

print("Все проверки прошли")
