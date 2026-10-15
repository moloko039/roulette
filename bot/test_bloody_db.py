"""Bloody slot, этап 3: партия в базе. Ставка и выплата через wallet, состояние между спинами, повтор по request_id, автозакрытие через 7 дней с выплатой накопленного,
выгрузка и удаление данных, очистка, active_game. Случайность подменяется заглушкой спина, чтобы сценарии были точными."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import random
import sqlite3
import tempfile

import bloody_config as cfg
import bloody_round as br
import db
from features.active_game_db import active_game_of
from features.data_rights_db import delete_player_data, get_player_export
from features import purge_db
from games import bloody_db
from roulette import BalanceLimit, InsufficientFunds

A, B = 717171701, 717171702
NOW = 1_760_000_000
BET = 100


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["DB_PATH"] = path
db.init_db(path)
for uid in (A, B):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 100000, 100, ?, ?, 0, 0, 0, 0)",
        (uid, NOW + 864000, NOW - 86400))


def bal(uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


n = [0]


def rid():
    n[0] += 1
    return "request-bl-%08d" % n[0]


# заглушка спина: сценарий (выигрыш в единицах, оставшиеся Candle Spins после спина)
real_play = br.play_spin


def scripted(plan):
    steps = list(plan)

    def fake(rnd, rng):
        units, candle_after = steps.pop(0)
        mode = rnd.next_mode()
        rnd.spins += 1
        if mode == br.CANDLE:
            rnd.candle_left -= 1
        rnd.candle_left = candle_after
        rnd.units += units
        if rnd.units >= br.CAP_UNITS:
            rnd.units, rnd.capped = br.CAP_UNITS, True
        rnd.finished = rnd.capped or rnd.candle_left <= 0
        return {"mode": mode, "index": rnd.spins, "start": {"grid": [], "mults": None, "bar": None}, "steps": [], "units": units, "total_units": rnd.units,
                "bar": rnd.bar.to_dict(), "candle_left": rnd.candle_left, "reign_left": 0, "sticky": [0] * 5, "finished": rnd.finished, "capped": rnd.capped}
    return fake


# ---- проверки ставки
check("ставка вне лестницы", raises(br.BadBet, bloody_db.bloody_start, A, rid(), 7, now=NOW, db_path=path), True)
check("без активного раунда спина нет", raises(br.NoActiveRound, bloody_db.bloody_spin, A, rid(), now=NOW, db_path=path), True)
check("состояние без раундов", bloody_db.bloody_state(A, now=NOW, db_path=path)["status"], "none")
sql(path, "UPDATE players SET balance = 50 WHERE telegram_id = ?", (B,))
check("не хватает фишек", raises(InsufficientFunds, bloody_db.bloody_start, B, rid(), BET, now=NOW, db_path=path), True)
check("баланс не изменился при ошибке", bal(B), 50)
sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (2 ** 53 - 6, B))
check("потолок баланса: выплата 16161× не поместилась бы", raises(BalanceLimit, bloody_db.bloody_start, B, rid(), 1000000, now=NOW, db_path=path), True)
sql(path, "UPDATE players SET balance = 100000 WHERE telegram_id = ?", (B,))

# ---- раунд из трёх спинов: старт (выигрыш 100 ед.), Candle 1 (200 ед.), Candle 2 (0)
br.play_spin = scripted([(100, 2), (200, 1), (0, 0)])
try:
    start_id = rid()
    r1 = bloody_db.bloody_start(A, start_id, BET, now=NOW, db_path=path)
    check("старт: активен, ставка списана, выплаты ещё нет", (r1["status"], r1["cost"], bal(A), r1["payout"]), ("active", BET, 100000 - BET, None))
    check("состояние раунда в ответе", (r1["round"]["spins"], r1["round"]["units"], r1["round"]["candle_left"], r1["round"]["next"]), (1, 100, 2, br.CANDLE))
    check("накопленный выигрыш в фишках: 100 ед. × 100 / 100", r1["round"]["win"], 100)
    check("ответ содержит последний спин", r1["spin"]["units"], 100)
    check("второй старт при активном раунде запрещён", raises(br.ActiveRoundExists, bloody_db.bloody_start, A, rid(), BET, now=NOW + 1, db_path=path), True)
    check("повтор старта по request_id: тот же раунд, второй раз не списывается", (bloody_db.bloody_start(A, start_id, BET, now=NOW + 2, db_path=path)["replayed"], bal(A)), (True, 100000 - BET))
    check("повтор с другой ставкой: конфликт", raises(br.RequestConflict, bloody_db.bloody_start, A, start_id, 200, now=NOW + 3, db_path=path), True)
    check("активная игра в /api/me", active_game_of(A, db_path=path), "bloody")
    # продолжение после «перезагрузки»: состояние читается из базы
    st = bloody_db.bloody_state(A, now=NOW + 10, db_path=path)
    check("состояние после перезагрузки то же", (st["status"], st["round"]["units"], st["round"]["candle_left"], st["spin"]["units"]), ("active", 100, 2, 100))
    spin_id = rid()
    r2 = bloody_db.bloody_spin(A, spin_id, now=NOW + 20, db_path=path)
    check("Candle Spin 1: накоплено 300", (r2["status"], r2["round"]["units"], r2["round"]["candle_left"]), ("active", 300, 1))
    check("повтор спина не играет второй раз", (bloody_db.bloody_spin(A, spin_id, now=NOW + 21, db_path=path)["round"]["units"], bloody_db.bloody_spin(A, spin_id, now=NOW + 22, db_path=path)["replayed"]), (300, True))
    r3 = bloody_db.bloody_spin(A, rid(), now=NOW + 30, db_path=path)
    check("конец раунда: выплата накопленного 300 ед. = 300 фишек", (r3["status"], r3["payout"], bal(A)), ("finished", 300, 100000 - BET + 300))
    check("после конца активной игры нет", active_game_of(A, db_path=path), None)
    check("спин после конца запрещён", raises(br.NoActiveRound, bloody_db.bloody_spin, A, rid(), now=NOW + 40, db_path=path), True)
    check("опыт и учёт ставок", sql(path, "SELECT xp, total_staked FROM players WHERE telegram_id = ?", (A,))[0], (BET * cfg.XP_LOSE_X1000[None] // 1000, BET))
    check("счётчики игр", sql(path, "SELECT game, rounds, wins, staked, paid FROM player_game_stats WHERE telegram_id = ? AND game = 'bloody'", (A,)), [("bloody", 1, 1, BET, 300)])
    check("личный рекорд: чистый выигрыш 200", sql(path, "SELECT game, net_amount FROM player_best_win WHERE telegram_id = ?", (A,)), [("bloody", 200)])

    # ---- автозакрытие через 7 дней: выплата накопленного
    br.play_spin = scripted([(500, 3)])
    bloody_db.bloody_start(B, rid(), 200, now=NOW, db_path=path)
    check("до срока раунд жив", bloody_db.bloody_state(B, now=NOW + cfg.IDLE_SECONDS - 5, db_path=path)["status"], "active")
    before = bal(B)
    st = bloody_db.bloody_state(B, now=NOW + cfg.IDLE_SECONDS + 5, db_path=path)
    check("через 7 дней закрыт с выплатой накопленного (500 ед. × 200 / 100)", (st["status"], st["payout"], bal(B) - before), ("expired", 1000, 1000))
    check("фоновое закрытие ничего не находит", bloody_db.close_expired_bloody(now=NOW + 10 ** 7, db_path=path), 0)

    # ---- фоновое автозакрытие находит брошенный раунд
    br.play_spin = scripted([(0, 2)])
    bloody_db.bloody_start(A, rid(), BET, now=NOW + 100, db_path=path)
    check("фоновое закрытие закрывает брошенный раунд", bloody_db.close_expired_bloody(now=NOW + 100 + cfg.IDLE_SECONDS + 1, db_path=path), 1)
    check("и ровно один раз", sql(path, "SELECT status FROM bloody_games WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (A,)), [("expired",)])

    # ---- потолок 16161×: раунд заканчивается сразу
    br.play_spin = scripted([(10 ** 9, 5)])
    cap = bloody_db.bloody_start(A, rid(), BET, now=NOW + 1000, db_path=path)
    check("потолок: раунд закрыт, выплата 16161 ставок", (cap["status"], cap["payout"], cap["round"]["capped"]), ("capped", 16161 * BET, True))
finally:
    br.play_spin = real_play

# ---- настоящий движок: раунд до конца, деньги сходятся
rng = random.Random(77)
start_bal = bal(A)
st = bloody_db.bloody_start(A, rid(), 200, now=NOW + 2000, db_path=path, rng=rng)
guard = 0
while st["status"] == "active" and guard < 400:
    st = bloody_db.bloody_spin(A, rid(), now=NOW + 2001 + guard, db_path=path, rng=rng)
    guard += 1
check("раунд на настоящем движке закончился", st["status"] in ("finished", "capped"), True)
check("баланс = было - ставка + выплата", bal(A), start_bal - 200 + st["payout"])
check("выплата = единицы × ставка // 100", st["payout"], br.payout_chips(st["round"]["units"], 200))

# ---- выгрузка, удаление, очистка
exp = get_player_export(A, db_path=path)
check("выгрузка: завершённые раунды без полей и колод", sorted(exp["bloody_games"][0]), ["bet", "buy", "cost", "created_at", "finished_at", "payout", "spins", "status", "units"])
br.play_spin = scripted([(0, 1)])
try:
    bloody_db.bloody_start(A, rid(), BET, now=NOW + 9000, db_path=path)
    check("выгрузка: незавершённый раунд", get_player_export(A, db_path=path)["bloody_active"]["candle_left"], 1)
finally:
    br.play_spin = real_play
sql(path, "UPDATE bloody_games SET finished_at = ? WHERE status != 'active'", (NOW - 100 * 86400,))
purge_db.purge_old_data(now=NOW + 9100, db_path=path)
check("очистка: завершённые старше срока удалены, активный остался", sql(path, "SELECT status FROM bloody_games WHERE telegram_id = ?", (A,)), [("active",)])
counts = delete_player_data(A, db_path=path)
check("удаление вместе с профилем", (counts["bloody_games"], sql(path, "SELECT COUNT(*) FROM bloody_games WHERE telegram_id = ?", (A,))[0][0]), (1, 0))
os.unlink(path)
print("test_bloody_db OK")
