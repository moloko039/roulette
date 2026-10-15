"""Bloody slot, этап 4: покупки функций (Виселица, Reign of Terror Spins, Ритуальный буст xBet 1 и xBet 2): цены, стартовое состояние, первый спин, списание в базе.
RTP покупок проверяет не этот тест, а симулятор tools/simulate_bloody_slot.py (миллионы раундов)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import random
import sqlite3
import tempfile

import bloody_config as cfg
import bloody_round as br
import db
from games import bloody_db
from roulette import InsufficientFunds

A = 818181801
NOW = 1_760_000_000


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


# ---- цены: целые фишки на любой ставке лестницы
check("покупки", sorted(cfg.BUY_KINDS), ["gibbet", "reign", "xbet1", "xbet2"])
for bet in cfg.BET_LADDER:
    for kind, units in cfg.BUY_COST_UNITS.items():
        assert bet * units % cfg.UNITS_PER_BET == 0, "цена %s на ставке %d не целая" % (kind, bet)
check("цены в ставках: 240, 100, 2.5, 66", [cfg.BUY_COST_UNITS[k] / 100 for k in ("gibbet", "reign", "xbet1", "xbet2")], [cfg.BUY_COST_UNITS["gibbet"] / 100, 100.0, 2.5, 66.0])
check("цена xBet 1 на ставке 20: 50 фишек", br.buy_cost_chips(20, "xbet1"), 50)
check("цена xBet 2 на ставке 100: 6600", br.buy_cost_chips(100, "xbet2"), 6600)
check("обычная ставка стоит ставку", br.buy_cost_chips(100, None), 100)

# ---- стартовые состояния
r = br.new_round("gibbet")
check("Виселица: уровень 3, +6 Candle Spins, три замены", (r.bar.level, r.bar.progress, r.candle_left, r.upgrade_cells, r.buy), (3, 0, 6, 3, "gibbet"))
check("Виселица: сначала стартовый спин", r.next_mode(), br.BASE)
r = br.new_round("reign")
check("Reign: 10 спинов, бонус сразу", (r.reign_left, r.bar.final, r.reign_started, r.next_mode()), (10, True, True, br.REIGN))
r = br.new_round("xbet1")
check("xBet 1: уровень 1, ускоренные очки", (r.bar.level, r.point_mult, r.candle_left), (1, cfg.XBET1_POINT_MULT, 0))
r = br.new_round("xbet2")
check("xBet 2: уровень 5, Reign не запускается, xSplit гарантированы", (r.bar.level, r.no_reign, r.guarantee_xsplit), (5, True, True))
try:
    br.new_round("zzz")
    raise AssertionError("неизвестная покупка принята")
except br.BadBet:
    pass
check("состояние покупки сохраняется в JSON", br.Round.from_json(br.new_round("gibbet").to_json()).to_dict(), br.new_round("gibbet").to_dict())

# ---- Виселица: три низких или средних символа первого поля заменены высокими
rnd = br.new_round("gibbet")
field = br.Field([["L1", "L2", "L3", "L4"], ["L5", "M1", "M2", "M3"], ["M4", "M5", "L1", "L2"], ["L3", "L4", "L5", "M1"], ["M2", "M3", "M4", "M5"]])
br._apply_start_buy(field, rnd, random.Random(1))
check("Виселица: ровно три высоких символа", sum(1 for col in field.grid for c in col if c in cfg.HIGH), 3)
# ---- xBet 2: Dagger и Guillotine на первом поле
rnd = br.new_round("xbet2")
field = br.Field([["H1"] * 4 for _ in range(5)])
br._apply_start_buy(field, rnd, random.Random(2))
flat = [c for col in field.grid for c in col]
check("xBet 2: по одному Dagger и Guillotine", (flat.count(cfg.XDAGGER), flat.count(cfg.XGUILLOTINE)), (1, 1))

# ---- спины покупок на настоящем движке
rng = random.Random(5)
r = br.new_round("xbet2")
log = br.play_spin(r, rng)
check("xBet 2: один спин и раунд закончен", (r.finished, r.spins, log["mode"]), (True, 1, br.BASE))
check("xBet 2: разделение xSplit было в логе", any(s["type"] == "xsplit" for s in log["steps"]), True)
r = br.new_round("reign")
modes = []
while not r.finished:
    modes.append(br.play_spin(r, rng)["mode"])
check("Reign: все спины Reign, не меньше десяти", (set(modes), len(modes) >= 10), ({br.REIGN}, True))
r = br.new_round("gibbet")
first = br.play_spin(r, rng)
check("Виселица: первый спин основной, дальше Candle", (first["mode"], r.candle_left >= 6 or r.finished), (br.BASE, True))

# ---- база: списание цены покупки
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["DB_PATH"] = path
db.init_db(path)
conn = sqlite3.connect(path)
conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) VALUES (?, 1000000, 100, ?, ?, 0, 0, 0, 0)",
             (A, NOW + 864000, NOW - 86400))
conn.commit()
conn.close()


def bal():
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT balance FROM players WHERE telegram_id = ?", (A,)).fetchone()[0]
    finally:
        c.close()


n = [0]


def rid():
    n[0] += 1
    return "request-by-%08d" % n[0]


before = bal()
res = bloody_db.bloody_start(A, rid(), 100, buy="xbet2", now=NOW, db_path=path, rng=random.Random(3))
check("xBet 2 в базе: цена 6600, раунд закрыт одним запросом", (res["cost"], res["buy"], res["status"] in ("finished", "capped")), (6600, "xbet2", True))
check("баланс = было - цена + выплата", bal(), before - 6600 + res["payout"])
before = bal()
res = bloody_db.bloody_start(A, rid(), 20, buy="xbet1", now=NOW + 5, db_path=path, rng=random.Random(4))
check("xBet 1: цена 50 фишек на ставке 20", (res["cost"], res["round"]["bar"]["level"] >= 1), (50, True))
c = sqlite3.connect(path)
xp_now = c.execute("SELECT xp FROM players WHERE telegram_id = ?", (A,)).fetchone()[0]
c.close()
check("опыт считается от цены покупки и режима", xp_now, 6600 * cfg.XP_LOSE_X1000["xbet2"] // 1000 + 50 * cfg.XP_LOSE_X1000["xbet1"] // 1000)
# конфликт повтора: тот же request_id с другой покупкой
rid_x = rid()
bloody_db.bloody_start(A, rid_x, 20, buy="xbet1", now=NOW + 10, db_path=path, rng=random.Random(6))
try:
    bloody_db.bloody_start(A, rid_x, 20, buy="xbet2", now=NOW + 11, db_path=path)
    raise AssertionError("конфликт не обнаружен")
except br.RequestConflict:
    pass
try:
    bloody_db.bloody_start(A, rid(), 20, buy="zzz", now=NOW + 12, db_path=path)
    raise AssertionError("неизвестная покупка принята")
except br.BadBet:
    pass
conn = sqlite3.connect(path)
conn.execute("UPDATE players SET balance = 10 WHERE telegram_id = ?", (A,))
conn.commit()
conn.close()
try:
    bloody_db.bloody_start(A, rid(), 100, buy="gibbet", now=NOW + 13, db_path=path)
    raise AssertionError("не хватает фишек, а покупка прошла")
except InsufficientFunds:
    pass
check("при нехватке фишек баланс цел", bal(), 10)
os.unlink(path)
print("test_bloody_buy OK")
