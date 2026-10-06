"""Western Slot: правила (контрольные тесты постановки casinch), раунд в базе, API, права на данные, очистка, документы."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

from fastapi.testclient import TestClient

import db
import ratelimit
import slot
import xp
from api import create_app
from core.kernel import BEST_WIN_GAMES
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
SECRET_ID, SECRET_BALANCE = 424242431, 7654321

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, "%s: получили %r, ожидали %r" % (name, got, expected)


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


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "s%d.db" % counter[0])
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


def add_player(path, uid, balance=100_000, total=0, last_accrual=NOW):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, "
              "income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, 0, 0)",
        (uid, balance, last_accrual, last_accrual, total))


def player(path, uid):
    return sql(path, "SELECT balance, total_staked, xp FROM players WHERE telegram_id = ?", (uid,))[0]


def rid(n):
    return "slot-req-%04d" % n


def wins_of(board_text, cfg=slot.CONFIG):
    return [(w["sym"], w["length"], w["ways"], w["pay"]) for w in slot.evaluate(slot.parse_board(board_text), cfg.paytable)]


def total_of(board_text):
    return slot.sum_wins(slot.evaluate(slot.parse_board(board_text)))


# Цепочка шести каскадов из постановки (монета 0,60, ставка 12,00): поля, комбинации и выигрыш шага
CHAIN = [
    (1, "H Q J | J R B J | B* J* A H A* | J Q V* V Q | Q V J R | H K B", [("J", 5, 2, 6)], 6),
    (2, "B H Q | R Q R B | B* W A H A* | J Q V* V Q | J Q V R | H K B", [("Q", 5, 2, 6), ("B", 3, 2, 20)], 52),
    (4, "R B H | A H R R | V W A H A* | K R J V* V | K J V R | H K B", [("R", 5, 2, 40), ("H", 3, 2, 10)], 200),
    (8, "V H B | B V V A | H* V V A A* | R K J V* V | R K J V | H K B", [("V", 5, 8, 120)], 960),
    (16, "H H B | H H B A | A* H H* A A* | H R K J W | Q R K J | H K B", [("H", 4, 16, 160)], 2560),
    (32, "H B B | R R B A | B A* W A A* | V V R K J | Q R K J | H K B", [("B", 3, 4, 40)], 1280),
    (64, "Q J H | A R R A | B B* A* A A* | V V R K J | Q R K J | H K B", [], 0),
]
CHAIN_BOARDS = [slot.parse_board(step[1]) for step in CHAIN]


class ScriptedSource:
    """Источник полей по записанной цепочке; сверяет полученное после взрыва поле с низом следующего."""

    def __init__(self, boards):
        self.boards = boards

    def first(self, n_scatters):
        return self.boards[0]

    def refill(self, collapsed, step_index):
        nxt = self.boards[step_index]
        for r in range(slot.REEL_COUNT):
            h = slot.REEL_HEIGHTS[r]
            check("низ барабана %d шага %d" % (r + 1, step_index + 1), collapsed[r], nxt[r][h - len(collapsed[r]):])
        return nxt


class FixedRng:
    def __init__(self, values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


STEP_KEYS = {"board", "wins", "multiplier", "stepWin", "exploded", "toWild", "refilled", "boardAfter"}
SPIN_KEYS = {"mode", "steps", "scatters", "freeSpinsAwarded", "totalWin", "capped"}
ROUND_KEYS = {"base", "freeSpins", "freeSpinsLeftAfter", "totalWin", "bonusWin", "capped", "bought"}
FRAME_REELS = {2, 3}


def check_spin_invariants(spin, cfg, name):
    start = cfg.fs_start_multiplier if spin["mode"] == "fs" else cfg.base_start_multiplier
    check(name + ": ключи спина", set(spin), SPIN_KEYS)
    total = 0
    for i, step in enumerate(spin["steps"]):
        check(name + ": ключи шага", set(step), STEP_KEYS)
        board = slot.parse_board(step["board"])
        after = slot.parse_board(step["boardAfter"])
        for b in (board, after):
            for r, reel in enumerate(b):
                for sym, framed in reel:
                    assert not framed or r in FRAME_REELS, name + ": рамка вне барабанов 3-4"
                    assert sym != "W" or r in FRAME_REELS, name + ": Wild вне барабанов 3-4"
                    assert not (framed and sym in ("W", "S")), name + ": рамка на Wild или Scatter"
        if i == 0:
            assert all(cell[0] != "W" for reel in board for cell in reel), name + ": Wild на первом поле"
        check(name + ": множитель шага %d" % i, step["multiplier"], min(start * 2 ** i, cfg.max_multiplier))
        check(name + ": шаг пересчитан", [(w["sym"], w["length"], w["ways"], w["pay"]) for w in step["wins"]],
              [(w["sym"], w["length"], w["ways"], w["pay"]) for w in slot.evaluate(board, cfg.paytable)])
        if not spin["capped"] or i < len(spin["steps"]) - 1:
            check(name + ": выигрыш шага", step["stepWin"], slot.sum_wins(step["wins"]) * step["multiplier"])
        exploded, to_wild = slot.resolve_wins(board, step["wins"])
        check(name + ": взрыв и Wild", (step["exploded"], step["toWild"]), (exploded, to_wild))
        if step["wins"] and not (spin["capped"] and i == len(spin["steps"]) - 1):
            collapsed = slot.collapse(board, exploded, to_wild)
            for r in range(slot.REEL_COUNT):   # выжившие внизу на своих местах, сверху ровно недостающее число новых ячеек
                h = slot.REEL_HEIGHTS[r]
                check(name + ": низ барабана", after[r][h - len(collapsed[r]):], collapsed[r])
            check(name + ": досыпка", [(f["reel"], slot.parse_cells(f["cells"])) for f in step["refilled"]],
                  [(r, after[r][:slot.REEL_HEIGHTS[r] - len(collapsed[r])]) for r in range(slot.REEL_COUNT)
                   if slot.REEL_HEIGHTS[r] - len(collapsed[r]) > 0])
        else:
            check(name + ": последний шаг без досыпки", (step["refilled"], step["boardAfter"]), ([], step["board"]))
        total += step["stepWin"]
    check(name + ": сумма шагов", total, spin["totalWin"])
    check(name + ": Scatter итогового поля", spin["scatters"], slot.count_scatters(slot.parse_board(spin["steps"][-1]["boardAfter"])))
    check(name + ": вращения за Scatter", spin["freeSpinsAwarded"], 0 if spin["capped"] else slot.free_spins_for(cfg, spin["scatters"]))


def check_round_invariants(r, cfg, name):
    check(name + ": ключи раунда", set(r), ROUND_KEYS)
    check_spin_invariants(r["base"], cfg, name + " base")
    for i, fs in enumerate(r["freeSpins"]):
        check_spin_invariants(fs, cfg, "%s fs%d" % (name, i))
    check(name + ": итог", r["totalWin"], r["base"]["totalWin"] + sum(fs["totalWin"] for fs in r["freeSpins"]))
    check(name + ": выигрыш бонуса", r["bonusWin"], r["totalWin"] - r["base"]["totalWin"])
    assert r["totalWin"] <= slot.win_cap_units(cfg), name + ": выше потолка"
    check(name + ": потолок помечен", r["capped"], r["totalWin"] == slot.win_cap_units(cfg) and (r["base"]["capped"] or any(fs["capped"] for fs in r["freeSpins"])))
    check(name + ": остаток вращений", len(r["freeSpinsLeftAfter"]), len(r["freeSpins"]))
    if r["freeSpins"]:
        left = r["base"]["freeSpinsAwarded"]
        for fs, after in zip(r["freeSpins"], r["freeSpinsLeftAfter"]):
            left = left - 1 + fs["freeSpinsAwarded"]
            check(name + ": остаток по шагам", after, 0 if fs["capped"] else left)
        if not r["capped"]:
            check(name + ": все вращения сыграны", r["freeSpinsLeftAfter"][-1], 0)
    else:
        assert r["base"]["freeSpinsAwarded"] == 0 or r["base"]["capped"], name + ": бонус не сыгран"


try:
    # ================= правила: контрольные поля T1-T5 =================
    check("T1", sorted(wins_of("K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A")), [("A", 3, 1, 2), ("H", 4, 1, 10)])
    b1 = slot.parse_board("K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A")
    ex, tw = slot.resolve_wins(b1, slot.evaluate(b1))
    check("T1: H в рамке становится Wild", tw, [{"reel": 2, "row": 4}])
    check("T1: взорвано 6", sorted((c["reel"], c["row"]) for c in ex), [(0, 1), (0, 2), (1, 1), (1, 3), (2, 3), (3, 3)])
    check("T1: поле после взрыва", slot.format_board(slot.collapse(b1, ex, tw)), "K | R V | B* R J W | R J V Q | J Q R V | K B A")
    check("T2", sorted(wins_of("K K A | A K J V | K J H A H | V Q R B R | A V R H | K V B")), [("A", 3, 1, 2), ("K", 3, 2, 4)])
    check("T3: Scatter и рамки не платят", wins_of("B K Q | A Q J A | V R Q B R* | H S K H* H* | J A H A | R Q B"), [("Q", 3, 1, 1)])
    check("T4: без выигрыша", wins_of("V R Q | J A H A | V B B* Q R* | J K J A H | K J A H | A V R"), [])
    check("T5: 2x2x2 способов", wins_of("B B Q | B B J K | B B A K J | A K Q J H | Q J A K | H V R"), [("B", 3, 8, 80)])
    check("шесть бандитов", total_of("B K Q | B K Q J | B K Q J A | B K Q J A | B K Q J | B K Q"), 65)
    check("Wild заменяет платящие, Scatter нет", sorted(wins_of("S A K | S A K Q | S W Q J B | J H V R B | Q J A K | H V R")), [("A", 3, 1, 2), ("K", 3, 1, 2)])
    check("evaluate_total = сумма", slot.evaluate_total(b1), slot.sum_wins(slot.evaluate(b1)))
    check("запись поля туда и обратно", slot.format_board(b1), "K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A")
    raises(ValueError, slot.parse_board, "K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B")
    raises(ValueError, slot.parse_board, "K A X | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A")
    check("короткое поле разрешено явно", len(slot.parse_board("K | R V | B* R J W | R J V Q | J Q R V | K B A", allow_short=True)[0]), 1)

    # ================= правила: цепочка шести каскадов =================
    for mult, board, wins, step_win in CHAIN:
        check("цепочка: комбинации " + board[:7], sorted(wins_of(board, slot.START_CONFIG)), sorted(wins))
        check("цепочка: выигрыш шага " + board[:7], total_of(board) * mult, step_win)
    spin = slot.play_spin(slot.START_CONFIG, ScriptedSource(CHAIN_BOARDS), "base", 0, 1, slot.win_cap_units(slot.START_CONFIG))
    check("цепочка: число шагов", len(spin["steps"]), len(CHAIN))
    check("цепочка: множители и выигрыши", [(s["multiplier"], s["stepWin"]) for s in spin["steps"]], [(c[0], c[3]) for c in CHAIN])
    check("цепочка: поля шагов", [s["board"] for s in spin["steps"]], [c[1] for c in CHAIN])
    check("цепочка: итог 5058 единиц", (spin["totalWin"], spin["capped"], spin["scatters"], spin["freeSpinsAwarded"]), (5058, False, 0, 0))
    check("цепочка: 3034,80 при монете 0,60", round(spin["totalWin"] * 60) / 100, 3034.8)
    check("цепочка: Wild в месте рамки", [s["toWild"] for s in spin["steps"]],
          [[{"reel": 2, "row": 1}], [{"reel": 2, "row": 0}], [], [{"reel": 3, "row": 3}], [{"reel": 2, "row": 2}], [], []])
    check("цепочка: досыпка шага 1", spin["steps"][0]["refilled"], [{"reel": 0, "cells": "B"}, {"reel": 1, "cells": "R Q"}, {"reel": 3, "cells": "J"}, {"reel": 4, "cells": "J"}])
    check("цепочка: потолок", slot.play_spin(slot.START_CONFIG, ScriptedSource(CHAIN_BOARDS), "base", 0, 1, 100)["totalWin"], 100)
    capped = slot.play_spin(slot.START_CONFIG, ScriptedSource(CHAIN_BOARDS), "base", 0, 1, 100)
    check("цепочка: при потолке шаг последний, без досыпки и без вращений", (capped["capped"], len(capped["steps"]), capped["steps"][-1]["refilled"], capped["freeSpinsAwarded"]), (True, 3, [], 0))

    # ================= правила: Scatter, вращения, числа =================
    check("вращения за Scatter", [slot.free_spins_for(slot.CONFIG, n) for n in range(7)], [0, 0, 0, 10, 12, 14, 16])
    check("таблица Scatter: без Scatter", slot.sample_scatter_count(FixedRng([0.999]), slot.CONFIG.base.scatter), 0)
    check("таблица Scatter: один", slot.sample_scatter_count(FixedRng([0.1]), slot.CONFIG.base.scatter), 1)
    check("таблица Scatter: три", slot.sample_scatter_count(FixedRng([0.2 + 0.0625 + 0.001]), slot.CONFIG.base.scatter), 3)
    check("покупка: 3, 4, 5 Scatter", [slot.sample_buy_scatter_count(FixedRng([x]), slot.CONFIG) for x in (0.5, 0.9, 0.995)], [3, 4, 5])
    check("ставка 20 монет, бонус 75 ставок, потолок 5000 ставок", (slot.bet_units(), slot.buy_cost_units(), slot.win_cap_units()), (20, 1500, 100000))
    check("списание в фишках", (slot.cost_chips(1, False), slot.cost_chips(50000, False), slot.cost_chips(10, True)), (20, 1_000_000, 15000))
    check("наибольшая выплата", slot.max_payout_chips(50000), 5_000_000_000)
    for bad in (0, 3, -1, True, 1.0, "1", None, 100000):
        raises(ValueError, slot.validate_coin, bad)
    check("монеты", slot.COIN_VALUES, (1, 2, 5, 10, 25, 50, 100, 250, 500, 2500, 10000, 25000, 50000))
    check("опыт: спин", xp.slot_xp(1000, False), 718)
    check("опыт: покупка", xp.slot_xp(15000, True), 420)
    check("опыт не выше ставки", all(xp.slot_xp(c, b) <= c for c in (1, 20, 999) for b in (False, True)), True)
    raises(ValueError, xp.slot_xp, -1, False)
    pick = slot.make_picker(slot.CONFIG.weights)
    check("взвешенный выбор: края", (pick(FixedRng([0.0])), pick(FixedRng([0.999999]))), ("B", "J"))
    assert "slot" in BEST_WIN_GAMES

    # ================= правила: инварианты на случайных раундах =================
    rng = random.Random(2024)
    stats = {"rounds": 0, "bonus": 0, "cascades": 0, "refill_scatter": 0, "two": 0}
    for i in range(3000):
        r = slot.play_round(slot.CONFIG, rng)
        check_round_invariants(r, slot.CONFIG, "раунд %d" % i)
        stats["rounds"] += 1
        stats["bonus"] += 1 if r["freeSpins"] else 0
        stats["cascades"] += len(r["base"]["steps"]) - 1
        stats["two"] += 1 if r["base"]["scatters"] == 2 else 0
        for sp in [r["base"]] + r["freeSpins"]:
            stats["refill_scatter"] += sum("S" in f["cells"].split() for s in sp["steps"] for f in s["refilled"])
    assert stats["cascades"] > 300 and stats["refill_scatter"] > 20 and 100 < stats["two"] < 350, stats
    for i in range(300):
        r = slot.play_round(slot.CONFIG, rng, buy=True)
        check_round_invariants(r, slot.CONFIG, "покупка %d" % i)
        first = slot.count_scatters(slot.parse_board(r["base"]["steps"][0]["board"]))
        assert 3 <= first <= 5 and r["base"]["scatters"] >= first and (r["freeSpins"] or r["capped"]) and r["bought"], "покупка без бонуса"
    rnd = slot.play_round()   # боевой генератор без сида
    check_round_invariants(rnd, slot.CONFIG, "SystemRandom")
    check("JSON раунда компактен", len(json.dumps(rnd, separators=(",", ":"))) < 200_000, True)

    # ================= раунд в базе =================
    seed = 5
    FIXED = slot.play_round(slot.CONFIG, random.Random(seed))
    while FIXED["totalWin"] == 0 or FIXED["freeSpins"]:
        seed += 1
        FIXED = slot.play_round(slot.CONFIG, random.Random(seed))
    UNITS = FIXED["totalWin"]
    path = new_db()
    add_player(path, SECRET_ID, balance=SECRET_BALANCE)
    with mock.patch.object(slot, "play_round", return_value=FIXED):
        res = db.play_slot(SECRET_ID, rid(1), 5, False, now=NOW + 1, db_path=path)
    check("ответ: ключи", set(res), {"coin", "bought", "cost", "payout", "round", "balance", "level", "xp", "replayed"})
    check("ответ: значения", (res["coin"], res["bought"], res["cost"], res["payout"], res["round"], res["replayed"]), (5, False, 100, UNITS * 5, FIXED, False))
    bal, staked, xp_total = player(path, SECRET_ID)
    check("баланс: минус ставка, плюс выплата", (res["balance"], bal), (SECRET_BALANCE - 100 + UNITS * 5,) * 2)
    check("total_staked и опыт", (staked, xp_total, res["xp"], res["level"]), (100, xp.slot_xp(100, False), xp.slot_xp(100, False), 1))
    check("строка раунда", sql(path, "SELECT request_id, coin, bought, cost, payout, created_at FROM slot_rounds"), [(rid(1), 5, 0, 100, UNITS * 5, NOW + 1)])
    check("раунд в строке целиком", json.loads(sql(path, "SELECT round_json FROM slot_rounds")[0][0]), FIXED)
    expected_best = [("slot", UNITS * 5 - 100)] if UNITS * 5 > 100 else []
    check("рекорд: чистый выигрыш", sql(path, "SELECT game, net_amount FROM player_best_win WHERE telegram_id = ?", (SECRET_ID,)), expected_best)
    # повтор того же запроса: сохранённый раунд, баланс текущий, второго списания нет
    rep = db.play_slot(SECRET_ID, rid(1), 5, False, now=NOW + 2, db_path=path)
    check("повтор", ({k: v for k, v in rep.items() if k != "replayed"}, rep["replayed"]), ({k: v for k, v in res.items() if k != "replayed"}, True))
    check("повтор ничего не списал", player(path, SECRET_ID), (bal, staked, xp_total))
    raises(slot.RequestConflict, db.play_slot, SECRET_ID, rid(1), 10, False, db_path=path)
    raises(slot.RequestConflict, db.play_slot, SECRET_ID, rid(1), 5, True, db_path=path)
    check("конфликт ничего не менял", (player(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM slot_rounds")[0][0]), ((bal, staked, xp_total), 1))
    # проигрыш: выплаты нет, рекорд не трогается, строка есть
    seed = 1
    LOSS = slot.play_round(slot.CONFIG, random.Random(seed))
    while LOSS["totalWin"] != 0:
        seed += 1
        LOSS = slot.play_round(slot.CONFIG, random.Random(seed))
    with mock.patch.object(slot, "play_round", return_value=LOSS):
        res2 = db.play_slot(SECRET_ID, rid(2), 1, False, now=NOW + 3, db_path=path)
    check("проигрыш", (res2["payout"], res2["balance"], player(path, SECRET_ID)[0]), (0, bal - 20, bal - 20))
    check("рекорд не изменился", sql(path, "SELECT game, net_amount FROM player_best_win WHERE telegram_id = ?", (SECRET_ID,)), expected_best)
    # покупка бонуса: списание 75 ставок, опыт по формуле покупки
    bal_before = player(path, SECRET_ID)[0]
    BUY = slot.play_round(slot.CONFIG, random.Random(3), buy=True)
    with mock.patch.object(slot, "play_round", return_value=BUY):
        res3 = db.play_slot(SECRET_ID, rid(3), 2, True, now=NOW + 4, db_path=path)
    check("покупка: списание и выплата", (res3["cost"], res3["payout"], res3["balance"]), (3000, BUY["totalWin"] * 2, bal_before - 3000 + BUY["totalWin"] * 2))
    check("покупка: ставка и опыт", player(path, SECRET_ID)[1:], (120 + 3000, xp.slot_xp(100, False) + xp.slot_xp(20, False) + xp.slot_xp(3000, True)))
    check("покупка: bought в строке", sql(path, "SELECT bought, round_json FROM slot_rounds WHERE request_id = ?", (rid(3),))[0], (1, json.dumps(BUY, separators=(",", ":"))))
    # нехватка фишек и потолок баланса: ничего не меняется
    before = (player(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM slot_rounds")[0][0])
    raises(InsufficientFunds, db.play_slot, SECRET_ID, rid(4), 50000, True, db_path=path)
    check("нехватка: без изменений", (player(path, SECRET_ID), sql(path, "SELECT COUNT(*) FROM slot_rounds")[0][0]), before)
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 1000, SECRET_ID))
    raises(BalanceLimit, db.play_slot, SECRET_ID, rid(5), 1, False, db_path=path)
    check("потолок: без изменений", (player(path, SECRET_ID)[0], sql(path, "SELECT COUNT(*) FROM slot_rounds")[0][0]), (MAX_SAFE_INT - 1000, before[1]))
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (before[0][0], SECRET_ID))
    # неверные параметры до транзакции
    for coin, buy in ((3, False), (True, False), (5, 1), (5, None), ("5", False)):
        raises(ValueError, db.play_slot, SECRET_ID, rid(6), coin, buy, db_path=path)
    # новый игрок регистрируется стартовым балансом в той же транзакции
    newcomer = db.play_slot(777, rid(7), 1, False, now=NOW + 5, db_path=path, rng=random.Random(9))
    check("новый игрок", (newcomer["balance"], sql(path, "SELECT COUNT(*) FROM players WHERE telegram_id = 777")[0][0]), (1000 - 20 + newcomer["payout"], 1))

    # ================= API =================
    path = new_db()
    now_real = int(time.time())
    add_player(path, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "60",
                                                       "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "60"}), clock=lambda: clock[0])
    client = TestClient(create_app(TOKEN, [], db_path=path, rate_limiter=lim))

    def auth(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    def spin(body, uid=SECRET_ID):
        return client.post("/api/slot/spin", headers=auth(uid), json=body)

    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "slot.json"), encoding="utf-8"))

    def shape_of(value):
        """Форма из примера: объекты с теми же ключами, списки по первому элементу (пустой список: любой список)."""
        if isinstance(value, dict):
            return {k: shape_of(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shape_of(value[0])] if value else []
        return type(value)

    def same_shape(name, body, spec):
        if isinstance(spec, dict):
            assert isinstance(body, dict) and set(body) == set(spec), "%s: поля %s, в примере %s" % (name, sorted(body) if isinstance(body, dict) else body, sorted(spec))
            for key, sub in spec.items():
                same_shape(name + "." + key, body[key], sub)
        elif isinstance(spec, list):
            assert type(body) is list, (name, body)
            for i, item in enumerate(body):
                if spec:
                    same_shape("%s[%d]" % (name, i), item, spec[0])
        else:
            assert type(body) is spec, "%s: %r, ожидали %s" % (name, body, spec.__name__)

    check("без подписи", client.post("/api/slot/spin", json={}).status_code, 401)
    good = {"request_id": rid(100), "coin": 10, "buy": False}
    bad_bodies = [
        {}, {"request_id": rid(100), "coin": 10}, dict(good, extra=1),
        dict(good, coin=0), dict(good, coin=3), dict(good, coin=100000), dict(good, coin=10.0), dict(good, coin="10"), dict(good, coin=True), dict(good, coin=None),
        dict(good, buy=1), dict(good, buy=0), dict(good, buy="true"), dict(good, buy=None),
        dict(good, request_id="short"), dict(good, request_id=5), dict(good, request_id="bad id with spaces!!"),
    ]
    for body in bad_bodies:
        r = spin(body)
        check("400 %s" % json.dumps(body)[:60], (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/slot/spin", headers=dict(auth(SECRET_ID), **{"content-type": "application/json"}), content=b"not json")
    check("не JSON", (r.status_code, r.json()), (400, {"detail": "invalid_request"}))
    r = client.post("/api/slot/spin", headers=auth(SECRET_ID), content=b"[" + b"1," * 40000 + b"1]")
    check("слишком большое тело", r.status_code, 413)
    check("при 400 ничего не списано", (player(path, SECRET_ID)[0], sql(path, "SELECT COUNT(*) FROM slot_rounds")[0][0]), (SECRET_BALANCE, 0))
    # 200: спин с подставленным раундом, проигрыш, покупка
    with mock.patch.object(slot, "play_round", return_value=FIXED):
        w = spin({"request_id": rid(101), "coin": 10, "buy": False})
    check("спин 200", w.status_code, 200)
    wj = w.json()
    same_shape("spin_win", wj, shape_of(examples["spin_win"]))
    check("спин: значения", (wj["coin"], wj["bought"], wj["cost"], wj["payout"], wj["round"], wj["replayed"], wj["balance"]),
          (10, False, 200, UNITS * 10, FIXED, False, SECRET_BALANCE - 200 + UNITS * 10))
    with mock.patch.object(slot, "play_round", return_value=LOSS):
        lj = spin({"request_id": rid(102), "coin": 1, "buy": False}).json()
    same_shape("spin_loss", lj, shape_of(examples["spin_loss"]))
    check("проигрыш: значения", (lj["payout"], lj["cost"], lj["balance"]), (0, 20, wj["balance"] - 20))
    with mock.patch.object(slot, "play_round", return_value=BUY):
        bj = spin({"request_id": rid(103), "coin": 2, "buy": True}).json()
    same_shape("buy", bj, shape_of(examples["buy"]))
    check("покупка: значения", (bj["bought"], bj["cost"], bj["payout"], bj["round"]["bought"], len(bj["round"]["freeSpins"]) > 0), (True, 3000, BUY["totalWin"] * 2, True, True))
    # повтор по сети: тот же ответ, баланс текущий
    rep = spin({"request_id": rid(101), "coin": 10, "buy": False})
    rj = rep.json()
    cur = ("replayed", "balance", "level", "xp")
    check("повтор", (rep.status_code, rj["replayed"], {k: v for k, v in rj.items() if k not in cur}), (200, True, {k: v for k, v in wj.items() if k not in cur}))
    check("повтор: баланс текущий", rj["balance"], bj["balance"])
    check("request_conflict по монете", spin({"request_id": rid(101), "coin": 25, "buy": False}).json(), {"detail": "request_conflict"})
    conflict = spin({"request_id": rid(101), "coin": 10, "buy": True})
    check("request_conflict по покупке", (conflict.status_code, conflict.json()), (409, {"detail": "request_conflict"}))
    poor = spin({"request_id": rid(104), "coin": 50000, "buy": True})
    check("insufficient_funds", (poor.status_code, poor.json()), (409, {"detail": "insufficient_funds"}))
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 10, SECRET_ID))
    lim_r = spin({"request_id": rid(105), "coin": 1, "buy": False})
    check("balance_limit", (lim_r.status_code, lim_r.json()), (409, {"detail": "balance_limit"}))
    sql(path, "UPDATE players SET balance = ? WHERE telegram_id = ?", (bj["balance"], SECRET_ID))
    # настоящий раунд без подмены
    real = spin({"request_id": rid(106), "coin": 1, "buy": False}).json()
    same_shape("spin_real", real, shape_of(examples["spin_win"]))
    check_round_invariants(real["round"], slot.CONFIG, "раунд по сети")
    check("настоящий раунд: деньги сходятся", real["balance"], bj["balance"] - 20 + real["round"]["totalWin"])
    for k in ("invalid_request", "insufficient_funds", "request_conflict", "balance_limit"):
        check("пример ошибки " + k, examples["errors"][k], {"detail": k})
    for name in ("spin_win", "spin_loss", "buy"):   # примеры в документации сами по себе честные раунды
        ex = examples[name]
        check_round_invariants(ex["round"], slot.CONFIG, "пример " + name)
        check("пример: деньги", (ex["cost"], ex["payout"]), (slot.cost_chips(ex["coin"], ex["bought"]), ex["round"]["totalWin"] * ex["coin"]))
    # ограничение частоты: группа write
    path2 = new_db()
    add_player(path2, SECRET_ID, balance=SECRET_BALANCE, last_accrual=now_real)
    lim2 = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_BURST": "3", "WRITE_RATE_PER_SEC": "1",
                                                        "READ_RATE_BURST": "2", "READ_RATE_PER_SEC": "1"}), clock=lambda: clock[0])
    c2 = TestClient(create_app(TOKEN, [], db_path=path2, rate_limiter=lim2))
    codes = [c2.post("/api/slot/spin", headers=auth(SECRET_ID), json={"request_id": rid(200 + i), "coin": 1, "buy": False}).status_code for i in range(5)]
    check("write: после лимита 429", codes[:3] + [c != 200 for c in codes[3:]], [200, 200, 200, True, True])
    r = c2.post("/api/slot/spin", headers=auth(SECRET_ID), json={"request_id": rid(300), "coin": 1, "buy": False})
    check("429 тело и заголовок", (r.status_code, r.json(), int(r.headers["Retry-After"]) >= 1), (429, {"error": "too_many_requests"}, True))
    check("429 не создал раундов сверх разрешённых", sql(path2, "SELECT COUNT(*) FROM slot_rounds")[0][0], 3)

    # ================= права на данные: выгрузка и удаление =================
    export = db.get_player_export(SECRET_ID, db_path=path)
    check("выгрузка: число раундов", len(export["slot_rounds"]), 4)
    check("выгрузка: поля раунда", sorted(export["slot_rounds"][0]), ["bought", "coin", "cost", "payout", "round", "time"])
    check("выгрузка: раунд целиком и порядок (новые первыми)", (export["slot_rounds"][-1]["round"], export["slot_rounds"][1]["bought"]), (FIXED, True))
    counts = db.delete_player_data(SECRET_ID, db_path=path)
    check("удаление", (counts["slot_rounds"], counts["players"]), (4, 1))
    check("удалено из базы", sql(path, "SELECT COUNT(*) FROM slot_rounds WHERE telegram_id = ?", (SECRET_ID,)), [(0,)])
    check("повторное удаление", db.delete_player_data(SECRET_ID, db_path=path)["slot_rounds"], 0)
    check("нет данных: None", db.get_player_export(SECRET_ID, db_path=path), None)

    # ================= очистка по сроку =================
    path = new_db()
    add_player(path, SECRET_ID, balance=SECRET_BALANCE)
    for i, age in enumerate((40, 31, 29, 1)):
        db.play_slot(SECRET_ID, rid(400 + i), 1, False, now=NOW - age * 86400, db_path=path, rng=random.Random(i))
    deleted = db.purge_old_data(now=NOW, db_path=path, rounds_days=30)
    check("очистка: старше 30 дней", (deleted["slot_rounds"], sorted(r[0] for r in sql(path, "SELECT request_id FROM slot_rounds"))), (2, [rid(402), rid(403)]))
    db.purge_old_data(now=NOW, db_path=path, rounds_days=0)
    check("срок не короче 2 суток", sorted(r[0] for r in sql(path, "SELECT request_id FROM slot_rounds")), [rid(403)])
    db.init_db(path)
    check("миграция идемпотентна", sql(path, "SELECT name FROM sqlite_master WHERE name = 'slot_rounds'"), [("slot_rounds",)])

    # ================= политика и документы =================
    privacy = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
    assert "Western Slot" in privacy and "История раундов Western Slot хранится 30 дней" in privacy
    api_doc = open(os.path.join(ROOT, "docs", "API.md"), encoding="utf-8").read()
    assert "/api/slot/spin" in api_doc and "freeSpinsLeftAfter" in api_doc
    client_js = open(os.path.join(ROOT, "script.js"), encoding="utf-8").read()
    assert "/api/slot/spin" in client_js and "slot: 'Western Slot'" in client_js

    # ================= в логах нет id, балансов и request_id =================
    for secret in (str(SECRET_ID), str(SECRET_BALANCE), rid(101), "slot-req-"):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:80]
finally:
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")
