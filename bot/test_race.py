"""Тесты игры «Забег свинок»: RTP (exact-дробь и выборка 200 000), выплаты, границы ставок,
идемпотентность, опыт, детерминированный script с победителем первым на финише и обгонами,
транзакции базы данных, wallet и API.
"""
import testenv  # noqa: F401 (первым: очищает окружение проекта и отключает .env)
from fractions import Fraction
import json
import os
import secrets
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

from core.db_conn import _connect
from core.kernel import BEST_WIN_GAMES
from core.schema import init_db
from features.race_db import play_race
import race
import ratelimit
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds
from tg_testutil import make_init_data
import wallet
import web.api
from web.api import create_app

TOKEN = "123456:TEST-TOKEN-not-real"
ORIGIN = "https://pazrabi.github.io"
NOW = 1_760_000_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:
        raise AssertionError(f"ожидали {exc.__name__}, получили {type(other).__name__}: {other}")
    raise AssertionError(f"ожидали {exc.__name__}, исключения не было")


def auth(user_id, token=TOKEN):
    return {"Authorization": "tma " + make_init_data(token, user_id=user_id, auth_date=int(time.time()))}


import sqlite3


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=10_000, total=0, last_accrual=NOW):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, 0, 0, 0)",
        (uid, balance, last_accrual, last_accrual, total))


def player_data(path, uid):
    conn = _connect(path)
    try:
        row = conn.execute("SELECT balance, xp, total_staked, last_played_at FROM players WHERE telegram_id = ?",
                           (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ================= 1. RTP exact-дробь и математика =================
print("Тест 1: RTP exact-дробь...")
check("теоретический RTP", race.THEORETICAL_RTP, Fraction(36, 37))

# Для любой ставки кратной 37 exact_rtp ровно равен 36/37
for b in (37, 74, 370, 3700, 37000):
    check(f"exact RTP кратный 37 (lanes=3, bet={b})", race.exact_rtp(b, 3), Fraction(36, 37))
    check(f"exact RTP кратный 37 (lanes=4, bet={b})", race.exact_rtp(b, 4), Fraction(36, 37))

# Для произвольных ставок exact_rtp <= 36/37 из-за округления вниз
for b in range(1, 1000):
    for lanes in (3, 4):
        rtp = race.exact_rtp(b, lanes)
        assert rtp <= Fraction(36, 37), f"RTP выше 36/37: bet={b}, lanes={lanes}, rtp={rtp}"
        # Отклонение не больше потери одной фишки на lanes розыгрышей
        diff = Fraction(36, 37) - rtp
        assert diff < Fraction(1, lanes * b), f"RTP слишком сильно отклоняется: bet={b}, lanes={lanes}"


# ================= 2. Выплаты =================
print("Тест 2: Выплаты...")
# 3 дорожки: floor(ставка * 3 * 36 / 37)
check("победа 10 фишек на 3 дорожках", race.payout(10, 3, True), (10 * 3 * 36) // 37)  # 29
check("чистый выигрыш 10 фишек на 3 дорожках", race.net_win(10, race.payout(10, 3, True)), 19)
check("проигрыш 10 фишек на 3 дорожках", race.payout(10, 3, False), 0)
check("чистый выигрыш при проигрыше", race.net_win(10, 0), -10)

# 4 дорожки: floor(ставка * 4 * 36 / 37)
check("победа 10 фишек на 4 дорожках", race.payout(10, 4, True), (10 * 4 * 36) // 37)  # 38
check("чистый выигрыш 10 фишек на 4 дорожках", race.net_win(10, race.payout(10, 4, True)), 28)
check("проигрыш 10 фишек на 4 дорожках", race.payout(10, 4, False), 0)

# Потолок ставки
check("максимальная выплата 1 000 000 (3)", race.payout(1_000_000, 3, True), (1_000_000 * 3 * 36) // 37)  # 2918918
check("максимальная выплата 1 000 000 (4)", race.payout(1_000_000, 4, True), (1_000_000 * 4 * 36) // 37)  # 3891891


# ================= 3. Границы ставки и валидация =================
print("Тест 3: Границы ставки и параметров...")
check("минимальная ставка", race.validate_bet(1), 1)
check("максимальная ставка", race.validate_bet(1_000_000), 1_000_000)

for bad_bet in (0, -1, -100, 1_000_001, 10_000_000, True, False, 1.5, "100", None):
    raises(ValueError, race.validate_bet, bad_bet)

for bad_lanes in (0, 1, 2, 5, 6, 3.0, "3", None, True, False):
    raises(ValueError, race.validate_lanes, bad_lanes)

check("валидные дорожки", (race.validate_lanes(3), race.validate_lanes(4)), (3, 4))

for bad_lane in (-1, 3, 4, 1.0, "0", None, True, False):
    raises(ValueError, race.validate_lane, bad_lane, 3)

for good_lane in (0, 1, 2):
    check(f"валидная дорожка {good_lane} из 3", race.validate_lane(good_lane, 3), good_lane)


# ================= 4. Опыт (XP) =================
print("Тест 4: Опыт (xp)...")
# Формула: ставка * (дорожки - 1) // дорожки
check("xp bet=10 lanes=3", race.race_xp(10, 3), 10 * 2 // 3)  # 6
check("xp bet=10 lanes=4", race.race_xp(10, 4), 10 * 3 // 4)  # 7
check("xp bet=37 lanes=3", race.race_xp(37, 3), 37 * 2 // 3)  # 24
check("xp bet=37 lanes=4", race.race_xp(37, 4), 37 * 3 // 4)  # 27
check("xp bet=1 lanes=3", race.race_xp(1, 3), 0)
check("xp bet=1 lanes=4", race.race_xp(1, 4), 0)

raises(ValueError, race.race_xp, -5, 3)
raises(ValueError, race.race_xp, 10, 2)


# ================= 5. Script (детерминированность, финиш, обгоны) =================
print("Тест 5: Script анимации...")
for lanes in (3, 4):
    for winner in range(lanes):
        s1 = race.generate_script(42, winner, lanes)
        s2 = race.generate_script(42, winner, lanes)
        check("детерминированность скрипта", s1, s2)
        check("длина скрипта (N=40)", len(s1), 40)

        # Кадр 0: все 0
        check(f"кадр 0 (lanes={lanes}, winner={winner})", s1[0], [0] * lanes)

        # Кадр 39: победитель 1000, остальные строго меньше
        check(f"победитель на финише равен 1000", s1[39][winner], 1000)
        for other in range(lanes):
            if other != winner:
                assert s1[39][other] < 1000, f"дорожка {other} не меньше 1000: {s1[39][other]}"

        # Все позиции в диапазоне 0..1000 и целочисленные
        for frame_idx, frame in enumerate(s1):
            check(f"число дорожек в кадре {frame_idx}", len(frame), lanes)
            for l_idx, pos in enumerate(frame):
                assert type(pos) is int, f"позиция не int: {pos}"
                assert 0 <= pos <= 1000, f"позиция вне 0..1000: {pos}"

        # Монотонность: позиции строго или нестрого возрастают
        for t in range(39):
            for l_idx in range(lanes):
                assert s1[t + 1][l_idx] >= s1[t][l_idx], f"позиция уменьшилась: {s1[t+1][l_idx]} < {s1[t][l_idx]}"

# Проверка наличия обгонов по ходу гонки на 100 случайных сидах
for test_seed in range(100):
    for lanes in (3, 4):
        winner = test_seed % lanes
        script = race.generate_script(test_seed, winner, lanes)
        # Лидер на каждом кадре
        leaders = []
        for frame in script[1:]:
            max_p = max(frame)
            leader = [l for l in range(lanes) if frame[l] == max_p][0]
            if not leaders or leaders[-1] != leader:
                leaders.append(leader)
        # Должна произойти сменяемость лидеров (обгоны)
        assert len(leaders) >= 2, f"сид {test_seed}: недостаточно смен лидеров {leaders}"
        # Итоговый победитель совпадает с winner
        check(f"финишный победитель совпадает", leaders[-1], winner)


def rid(n):
    return f"race-req-{n:08d}"


# ================= 6. Транзакции, идемпотентность, Wallet и DB =================
print("Тест 6: DB, Wallet, транзакции и идемпотентность...")
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    uid = 1001
    add_player(path, uid, balance=10_000)

    # 1. Первый раунд — выигрыш
    r1 = play_race(uid, rid(1), 3, 0, 100, now=NOW, db_path=path, winner=0, seed=123)
    check("round_id раунда 1", r1["round_id"], 1)
    check("lanes раунда 1", r1["lanes"], 3)
    check("lane раунда 1", r1["lane"], 0)
    check("winner раунда 1", r1["winner"], 0)
    check("payout раунда 1", r1["payout"], 291)
    # баланс: 10000 - 100 + 291 = 10191
    check("balance раунда 1", r1["balance"], 10191)
    check("script раунда 1 len", len(r1["script"]), 40)

    p_data = player_data(path, uid)
    check("баланс игрока в базе", p_data["balance"], 10191)
    check("опыт игрока в базе", p_data["xp"], race.race_xp(100, 3))
    check("total_staked в базе", p_data["total_staked"], 100)

    # 2. Идемпотентность: повтор с тем же request_id
    r1_rep = play_race(uid, rid(1), 3, 0, 100, now=NOW + 5, db_path=path, winner=1, seed=999)
    check("повтор round_id", r1_rep["round_id"], 1)
    check("повтор winner (сохранённый)", r1_rep["winner"], 0)
    check("повтор payout (сохранённый)", r1_rep["payout"], 291)
    check("повтор script", r1_rep["script"], r1["script"])
    check("повтор balance", r1_rep["balance"], 10191)
    # Повтор не должен менять опыт и total_staked
    p_data_after = player_data(path, uid)
    check("опыт после повтора", p_data_after["xp"], race.race_xp(100, 3))
    check("total_staked после повтора", p_data_after["total_staked"], 100)

    # 3. Конфликт request_id: тот же request_id, другие параметры
    raises(race.RequestConflict, play_race, uid, rid(1), 4, 0, 100, now=NOW, db_path=path)
    raises(race.RequestConflict, play_race, uid, rid(1), 3, 1, 100, now=NOW, db_path=path)
    raises(race.RequestConflict, play_race, uid, rid(1), 3, 0, 200, now=NOW, db_path=path)

    # 4. Проигрыш: ставка на дорожку 1, побеждает 2
    r2 = play_race(uid, rid(2), 4, 1, 500, now=NOW + 10, db_path=path, winner=2, seed=456)
    check("round_id раунда 2", r2["round_id"], 2)
    check("payout при проигрыше", r2["payout"], 0)
    # баланс: 10191 - 500 = 9691
    check("balance раунда 2", r2["balance"], 9691)

    p_data = player_data(path, uid)
    check("баланс после проигрыша", p_data["balance"], 9691)
    check("опыт после проигрыша", p_data["xp"], race.race_xp(100, 3) + race.race_xp(500, 4))
    check("total_staked", p_data["total_staked"], 600)

    # 5. InsufficientFunds: ставка превышает баланс
    raises(InsufficientFunds, play_race, uid, rid(3), 3, 0, 100_000, now=NOW, db_path=path)

    # 6. BalanceLimit: выплата превысит MAX_SAFE_INT
    add_player(path, 1002, balance=MAX_SAFE_INT - 100)
    raises(BalanceLimit, play_race, 1002, rid(4), 3, 0, 100, now=NOW, db_path=path)

    # 7. Рекорды раунда записаны через _round_finished
    conn = _connect(path)
    try:
        best_win = conn.execute("SELECT game, net_amount FROM player_best_win WHERE telegram_id = ?",
                                (uid,)).fetchone()
        assert best_win is not None
        check("игра рекорда", best_win["game"], "race")
        check("чистый выигрыш рекорда", best_win["net_amount"], 191)  # 291 - 100 = 191
    finally:
        conn.close()

finally:
    if os.path.exists(path):
        os.remove(path)


# ================= 7. Тестирование API (FastAPI / TestClient) =================
print("Тест 7: API /api/race/play...")
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    uid = 2001
    add_player(path, uid, balance=5000)

    client = TestClient(create_app(TOKEN, [ORIGIN], db_path=path))

    # 200 OK
    res = client.post("/api/race/play", headers=auth(uid),
                      json={"request_id": "api-req-1", "lanes": 3, "lane": 1, "bet": 200})
    check("HTTP статус 200", res.status_code, 200)
    body = res.json()
    check("поля ответа API", set(body), {"round_id", "lanes", "lane", "winner", "payout", "balance", "script"})
    check("длина script в API", len(body["script"]), 40)
    check("число дорожек в кадре", len(body["script"][0]), 3)

    # Форма ответа совпадает с примером в docs/examples/race.json
    example_path = os.path.join(ROOT, "docs", "examples", "race.json")
    with open(example_path, encoding="utf-8") as f:
        examples = json.load(f)
    check("форма ответа совпадает с race.json play_win", set(body), set(examples["play_win"]))

    # Повтор запроса — 200 OK с тем же round_id
    res_rep = client.post("/api/race/play", headers=auth(uid),
                          json={"request_id": "api-req-1", "lanes": 3, "lane": 1, "bet": 200})
    check("HTTP повтор статус 200", res_rep.status_code, 200)
    check("повтор round_id", res_rep.json()["round_id"], body["round_id"])

    # 409 Request Conflict
    res_conf = client.post("/api/race/play", headers=auth(uid),
                           json={"request_id": "api-req-1", "lanes": 3, "lane": 2, "bet": 200})
    check("HTTP конфликт 409", res_conf.status_code, 409)
    check("ошибка конфликта", res_conf.json(), {"detail": "request_conflict"})

    # 400 Invalid Request (неверные параметры)
    for bad_json in [
        {"request_id": rid(100), "lanes": 5, "lane": 0, "bet": 100},
        {"request_id": rid(101), "lanes": 3, "lane": 3, "bet": 100},
        {"request_id": rid(102), "lanes": 3, "lane": 0, "bet": 0},
        {"request_id": rid(103), "lanes": 3, "lane": 0, "bet": 1_000_001},
        {"request_id": "short", "lanes": 3, "lane": 0, "bet": 100},
        {"request_id": rid(104), "lanes": 3, "lane": 0},
        {"request_id": rid(105), "lanes": 3, "lane": 0, "bet": 100, "extra": 1},
    ]:
        r_bad = client.post("/api/race/play", headers=auth(uid), json=bad_json)
        check(f"400 на {bad_json}", r_bad.status_code, 400)
        check("текст ошибки 400", r_bad.json(), {"detail": "invalid_request"})

    # 409 Insufficient Funds
    r_nofunds = client.post("/api/race/play", headers=auth(uid),
                            json={"request_id": "api-nofunds", "lanes": 3, "lane": 0, "bet": 100_000})
    check("409 insufficient_funds", r_nofunds.status_code, 409)
    check("текст 409", r_nofunds.json(), {"detail": "insufficient_funds"})

    # 401 Unauthorized
    r_noauth = client.post("/api/race/play",
                           json={"request_id": "api-noauth", "lanes": 3, "lane": 0, "bet": 100})
    check("401 unauthorized", r_noauth.status_code, 401)
    check("текст 401", r_noauth.json(), {"detail": "Unauthorized"})

finally:
    if os.path.exists(path):
        os.remove(path)


# ================= 8. Проверка выборкой 200 000 (3 и 4 дорожки) =================
print("Тест 8: Проверка выборкой 200 000 (3 и 4 дорожки)...")
SAMPLE_SIZE = 200_000
BET = 37  # При bet=37 теоретический RTP ровно 36/37 = 0.97297...

# 3 дорожки
t0 = time.time()
wins_3 = [0, 0, 0]
total_payout_3 = 0
total_bet_3 = SAMPLE_SIZE * BET
payout_3_win = race.payout(BET, 3, True)

for _ in range(SAMPLE_SIZE):
    w = secrets.randbelow(3)
    wins_3[w] += 1
    if w == 0:  # Игрок ставит на дорожку 0
        total_payout_3 += payout_3_win

rtp_sample_3 = total_payout_3 / total_bet_3
elapsed_3 = time.time() - t0

# Проверка равномерности (каждая дорожка должна выиграть примерно 1/3 раз)
for l_idx, count in enumerate(wins_3):
    freq = count / SAMPLE_SIZE
    assert abs(freq - (1 / 3)) < 0.007, f"дорожка {l_idx} частота {freq} отклоняется от 1/3"

# Проверка RTP около 36/37 (~0.97297)
assert abs(rtp_sample_3 - (36 / 37)) < 0.015, f"RTP на 3 дорожках {rtp_sample_3:.5f} слишком далеко от 36/37"
print(f"  3 дорожки: {SAMPLE_SIZE} раундов за {elapsed_3:.3f}с, RTP = {rtp_sample_3:.5f} (теория {36/37:.5f}), победы = {wins_3}")

# 4 дорожки
t0 = time.time()
wins_4 = [0, 0, 0, 0]
total_payout_4 = 0
total_bet_4 = SAMPLE_SIZE * BET
payout_4_win = race.payout(BET, 4, True)

for _ in range(SAMPLE_SIZE):
    w = secrets.randbelow(4)
    wins_4[w] += 1
    if w == 0:  # Игрок ставит на дорожку 0
        total_payout_4 += payout_4_win

rtp_sample_4 = total_payout_4 / total_bet_4
elapsed_4 = time.time() - t0

# Проверка равномерности (каждая дорожка должна выиграть примерно 1/4 раз)
for l_idx, count in enumerate(wins_4):
    freq = count / SAMPLE_SIZE
    assert abs(freq - (1 / 4)) < 0.007, f"дорожка {l_idx} частота {freq} отклоняется от 1/4"

# Проверка RTP около 36/37 (~0.97297)
assert abs(rtp_sample_4 - (36 / 37)) < 0.015, f"RTP на 4 дорожках {rtp_sample_4:.5f} слишком далеко от 36/37"
print(f"  4 дорожки: {SAMPLE_SIZE} раундов за {elapsed_4:.3f}с, RTP = {rtp_sample_4:.5f} (теория {36/37:.5f}), победы = {wins_4}")

print("\nВсе тесты забега свинок (test_race.py) успешно пройдены!")
