"""Bloody slot, этап 3: раунд целиком. Scatter (очки и спины), Sticky Wild, xSplit (множители до ×32), Candle Spins и Reign of Terror Spins, лимит 16161×,
сохранение состояния раунда (JSON), конец раунда. Поле подставляется в тестах (_new_field), случайность задаётся списком."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import random

import bloody_config as cfg
import bloody_engine as eng
import bloody_round as br


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def grid(*reels):
    return [r.split() for r in reels]


class Cycle:
    """random(): по кругу значения, выбирающие L1, L2, L3, L4, L5 из пула [(L1..L5, вес 1)] (то есть по одному новому символу подряд, разные)."""
    def __init__(self):
        self.n = 0

    def random(self):
        v = (self.n % 5) / 5 + 0.05
        self.n += 1
        return v


POOL5 = [("L1", 1), ("L2", 1), ("L3", 1), ("L4", 1), ("L5", 1)]


def spin(rnd, field_grid, rng=None, pool=POOL5, field_mults=None):
    """Один спин на заданном поле и фиксированной таблице падения."""
    orig_field, orig_pool = br._new_field, br.pool_for
    br._new_field = lambda r, mode, g, p: br.Field([list(c) for c in field_grid], [list(c) for c in field_mults] if field_mults else None)
    br.pool_for = lambda r, mode: pool
    try:
        return br.play_spin(rnd, rng or Cycle())
    finally:
        br._new_field, br.pool_for = orig_field, orig_pool


QUIET = grid("M1 M2 M3 M4", "M5 H5 H4 H3", "H2 M1 M2 M3", "M4 M5 H5 H4", "H3 H2 M5 M1")   # без выигрышей: символы разные на барабанах подряд

# ---- состояние раунда: JSON туда и обратно
r = br.Round()
check("новый раунд: режим старта", r.next_mode(), br.BASE)
r.candle_left, r.reign_left, r.sticky, r.units, r.spins = 3, 2, [0, 1, 0, 2, 0], 777, 4
r.bar = eng.BarState(2, 13)
back = br.Round.from_json(r.to_json())
check("JSON сохраняет всё", back.to_dict(), r.to_dict())
check("порядок режимов: Reign раньше Candle", back.next_mode(), br.REIGN)
back.reign_left = 0
check("затем Candle", back.next_mode(), br.CANDLE)
back.candle_left = 0
check("затем конец", back.next_mode(), None)


def _raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    return False


fin = br.Round()
fin.finished = True
check("спин завершённого раунда запрещён", _raises(br.RoundFinished, br.play_spin, fin, random.Random(1)), True)

# ---- Scatter в основной игре: +10 очков за каждый и исчезает
rnd = br.Round()
g = [list(c) for c in QUIET]
g[0][0] = "S"
log = spin(rnd, g)
sc = [s for s in log["steps"] if s["type"] == "scatter"]
check("Scatter: один шаг, очки 10", (len(sc), sc[0]["points"], rnd.bar.progress), (1, 10, 10))
g2 = [list(c) for c in QUIET]
g2[0][0] = "S"
g2[3][2] = "S"
rnd2 = br.Round()
spin(rnd2, g2)
check("два Scatter: 20 очков = порог первого уровня, переход", (rnd2.bar.level, rnd2.bar.progress), (1, 0))
check("Scatter исчез с поля", [c for col in sc[0]["grid"] for c in col if c == "S"], [])
check("спин без выигрышей, раунд закончен", (log["units"], rnd.finished), (0, True))

# ---- Scatter в Reign: +1 спин, очки не копятся
rnd = br.Round()
rnd.spins, rnd.reign_left, rnd.reign_started = 1, 2, True
rnd.bar = eng.BarState(5, 0)
g = [list(c) for c in QUIET]
g[1][1] = "S"
log = spin(rnd, g)
check("Reign: спин потрачен, Scatter добавил один", (log["mode"], rnd.reign_left), (br.REIGN, 1 + 1))
check("Reign: очки полоски не растут", rnd.bar.progress, 0)

# ---- Sticky Wild в Candle Spins: прилипшие стоят внизу, упавший Wild опускается
rnd = br.Round()
rnd.spins, rnd.candle_left = 1, 2
rnd.sticky = [0, 0, 2, 0, 0]
seen = {}
orig = br._new_field


def capture(r, mode, g, p):
    f = orig(r, mode, g, p)
    seen["grid"] = [list(c) for c in f.grid]       # снимок сразу после сдачи: дальше поле меняют каскады
    return f


br._new_field = capture
br.pool_for = lambda r, mode: POOL5
try:
    br.play_spin(rnd, Cycle())
finally:
    br._new_field = orig
    import importlib
    importlib.reload(br)
check("прилипшие Wild внизу барабана 3", seen["grid"][2][2:], ["W", "W"])
rnd = br.Round()
rnd.spins, rnd.candle_left = 1, 3
g = [list(c) for c in QUIET]
g[0] = ["W", "M1", "M2", "M3"]
log = spin(rnd, g)
check("Wild упал вниз барабана и остался (не часть выигрыша)", (log["sticky"][0], [s["type"] for s in log["steps"]][:1]), (1, ["sink"]))
check("прилипший Wild сохранён для следующего спина", rnd.sticky[0], 1)
# в основной игре Wild не прилипает
rnd = br.Round()
log = spin(rnd, [["W", "M1", "M2", "M3"]] + [list(c) for c in QUIET[1:]])
check("в основной игре Wild не опускается и не сохраняется", ([s["type"] for s in log["steps"] if s["type"] == "sink"], rnd.sticky), ([], [0] * 5))

# ---- xSplit: Dagger делит свой ряд, Guillotine делит всё под собой на барабане, результат ≤ ×32
rnd = br.Round()
g = [list(c) for c in QUIET]
g[1][1] = cfg.XDAGGER
log = spin(rnd, g)
xs = [s for s in log["steps"] if s["type"] == "xsplit"][0]
check("Dagger: ряд 1 на барабанах 0, 2, 3, 4 удвоен", [[c[1] for c in xs["mults"]]], [[2, 1, 2, 2, 2]])
check("Dagger стал Wild", xs["grid"][1][1], "W")
check("в записи шага перечислены ячейки", sorted(xs["splits"][0]["cells"]), [[0, 1], [2, 1], [3, 1], [4, 1]])
rnd = br.Round()
g = [list(c) for c in QUIET]
g[2][0] = cfg.XGUILLOTINE
log = spin(rnd, g)
xs = [s for s in log["steps"] if s["type"] == "xsplit"][0]
check("Guillotine: три ячейки под ней удвоены", xs["mults"][2], [1, 2, 2, 2])
# оба: ячейка на пересечении получает ×4; ряд из пяти Dagger-ов не превышает ×32
rnd = br.Round()
g = [list(c) for c in QUIET]
g[1][1] = cfg.XDAGGER
g[2][0] = cfg.XGUILLOTINE
log = spin(rnd, g)
xs = [s for s in log["steps"] if s["type"] == "xsplit"][0]
check("пересечение Dagger и Guillotine: ×4", xs["mults"][2][1], 4)
rnd = br.Round()
g = [list(c) for c in QUIET]
for i in range(4):
    g[i][i] = cfg.XDAGGER
for i in range(5):
    pass
res = spin(rnd, g, field_mults=[[16] * 4 for _ in range(5)])
xs = [s for s in res["steps"] if s["type"] == "xsplit"][0]
check("множитель не больше ×32", max(m for col in xs["mults"] for m in col), cfg.XSPLIT_MAX_MULT)
# множители участвуют в способах: ячейка с множителем 2 считается за два способа
ev = eng.evaluate(grid("H1 L1 L2 L3", "H1 L1 L2 L3", "H1 L4 L5 M1", "M2 M3 M4 M5", "L1 L2 L3 L4"), 0, [[2, 1, 1, 1], [1, 1, 1, 1], [4, 1, 1, 1], [1] * 4, [1] * 4])
w = [x for x in ev if x.sym == "H1"][0]
check("способы с множителями: 2 * 1 * 4", (w.ways, w.units), (8, 150 * 8))

# ---- Candle Spins за уровни 1-4, Reign на пятом
rnd = br.Round()
rnd.bar = eng.BarState(0, 20)
log = spin(rnd, QUIET)
lu = [s for s in log["steps"] if s["type"] == "level_up"]
check("уровень 1: +2 Candle Spins", (len(lu), lu[0]["candle_spins"], rnd.candle_left, rnd.finished), (1, 2, 2, False))
check("после перехода режим Candle", rnd.next_mode(), br.CANDLE)
rnd = br.Round()
rnd.spins, rnd.candle_left = 1, 1
rnd.bar = eng.BarState(3, 60)
log = spin(rnd, QUIET)
check("уровень 4: ещё +2, пока Candle Spins не кончились", (rnd.bar.level, rnd.candle_left), (4, 2))
rnd = br.Round()
rnd.spins, rnd.candle_left = 1, 5
rnd.bar = eng.BarState(4, 80)
log = spin(rnd, QUIET)
lu = [s for s in log["steps"] if s["type"] == "level_up"][0]
check("пятый уровень: +6 Reign, Candle Spins сгорают, полоска стоит", (rnd.bar.level, rnd.reign_left, rnd.candle_left, rnd.reign_started, lu["reign_spins"]), (5, 6, 0, True, 6))
check("следующий спин Reign", rnd.next_mode(), br.REIGN)
# Reign: пул только из высоких (+ Wild, Scatter, xSplit)
pool = br.pool_for(rnd, br.REIGN)
check("Reign: символы поля только высокие и особые", sorted({s for s, _ in pool}), sorted(set(cfg.HIGH) | {"W", "S", "XD", "XG"}))
# повторно пятый уровень Reign не запускает
rnd.reign_left = 0
check("после Reign раунд заканчивается", (rnd.next_mode()), None)

# ---- лимит 16161×
rnd = br.Round()
rnd.units = br.CAP_UNITS - 50
big = grid("H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1")
log = spin(rnd, big, random.Random(3), pool=[("H1", 1)])
check("потолок: выплата обрезана, раунд закончен", (rnd.units, rnd.capped, rnd.finished, log["units"]), (br.CAP_UNITS, True, True, 50))
check("потолок: бонусные спины сгорают", (rnd.candle_left, rnd.reign_left), (0, 0))
check("выплата в фишках: 16161 ставок", br.payout_chips(br.CAP_UNITS, 200), 16161 * 200)
check("выплата в фишках округляется вниз", br.payout_chips(5, 20), 1)

# ---- случайные раунды целиком: инварианты
rng = random.Random(2026)
total = 0
longest = 0
for _ in range(400):
    r = br.Round()
    mult_seen = 1
    while not r.finished:
        log = br.play_spin(r, rng)
        for s in log["steps"]:
            assert all(len(c) == cfg.WORKING_ROWS for c in s["grid"]), "поле неполное"
            if s["mults"]:
                mult_seen = max(mult_seen, max(m for c in s["mults"] for m in c))
        assert r.units <= br.CAP_UNITS
        assert r.candle_left >= 0 and r.reign_left >= 0
        assert br.Round.from_json(r.to_json()).to_dict() == r.to_dict()
    assert mult_seen <= cfg.XSPLIT_MAX_MULT
    total += r.units
    longest = max(longest, r.spins)
assert longest < cfg.MAX_SPINS_PER_ROUND
print("test_bloody_round OK")
