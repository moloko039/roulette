"""Bloody slot, стадия 1: таблица выплат, подсчёт способов (ways), Wild, каскады. Движок чистый: тест не зависит от окружения и базы."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import random

import bloody_config as cfg
import bloody_engine as eng


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def grid(*reels):
    """Поле из строк «A B C D»: барабан слева направо, символы сверху вниз."""
    return [r.split() for r in reels]


def one(wins, sym):
    found = [w for w in wins if w.sym == sym]
    return found[0] if found else None


# ---- таблица выплат из задания
check("выплаты H1 (5/4/3)", cfg.PAYTABLE_UNITS["H1"], {5: 500, 4: 300, 3: 150})
check("выплаты L5", cfg.PAYTABLE_UNITS["L5"], {5: 15, 4: 10, 3: 5})
check("выплаты M3", cfg.PAYTABLE_UNITS["M3"], {5: 90, 4: 60, 3: 35})
check("все 15 символов в таблице", sorted(cfg.PAYTABLE_UNITS), sorted(cfg.PAY_SYMBOLS))
check("выплата не растёт при укорачивании", all(t[5] > t[4] > t[3] or (t[5] >= t[4] >= t[3]) for t in cfg.PAYTABLE_UNITS.values()), True)
check("Wild платит только за 5", cfg.WILD_PAY_UNITS, {5: 500})

# ---- способы: слева направо с первого барабана, минимум 3
g = grid("H1 L1 L2 L3", "H1 H1 L2 L3", "H1 L3 L4 L5", "M1 M2 M3 M4", "M5 M5 M5 M5")
w = one(eng.evaluate(g), "H1")
check("H1: 3 барабана, способов 1*2*1", (w.length, w.ways, w.units), (3, 2, 150 * 2))
check("ячейки комбинации", sorted(w.cells), [(0, 0), (1, 0), (1, 1), (2, 0)])
g = grid("H1 H1 L2 L3", "H1 H1 L2 L3", "H1 H1 L4 L5", "H1 M2 M3 M4", "M5 M5 M5 M5")
w = one(eng.evaluate(g), "H1")
check("H1: 4 барабана, способов 2*2*2*1", (w.length, w.ways, w.units), (4, 8, 300 * 8))
g = grid("L1 L2 L3 L4", "H1 H1 L2 L3", "H1 L3 L4 L5", "H1 M2 M3 M4", "H1 M5 M5 M5")
check("без первого барабана выигрыша нет", one(eng.evaluate(g), "H1"), None)
g = grid("H1 L1 L2 L3", "H1 L1 L2 L3", "M2 M2 L4 L5", "H1 M2 M3 M4", "H1 M5 M5 M5")
check("две комбинации в два барабана не платят", one(eng.evaluate(g), "H1"), None)
g = grid("H1 L1 L2 L3", "H1 L1 L2 L3", "H1 H1 L4 L5", "H1 M2 M3 M4", "H1 H1 H1 M5")
w = one(eng.evaluate(g), "H1")
check("5 барабанов: способов 1*1*2*1*3", (w.length, w.ways, w.units), (5, 6, 500 * 6))

# ---- только самая длинная комбинация символа (не 3, 4 и 5 по отдельности)
check("один выигрыш на символ", len([x for x in eng.evaluate(g) if x.sym == "H1"]), 1)

# ---- Wild: заменяет любой платящий символ, считается в способах, Scatter не заменяет
g = grid("H2 L1 L2 L3", "W L1 L2 L3", "H2 W L4 L5", "M1 M2 M3 M4", "M5 M5 M5 M5")
w = one(eng.evaluate(g), "H2")
check("Wild в середине продлевает до 3", (w.length, w.ways, w.units), (3, 2, 125 * 2))
check("Wild входит в ячейки комбинации", (1, 0) in w.cells and (2, 1) in w.cells, True)
check("Wild подменяет и другие символы", (one(eng.evaluate(g), "L1") is not None, one(eng.evaluate(g), "L1").length), (True, 3))
g = grid("H2 L1 L2 L3", "S L1 L2 L3", "H2 H2 L4 L5", "M1 M2 M3 M4", "M5 M5 M5 M5")
check("Scatter не заменяет символ", one(eng.evaluate(g), "H2"), None)
g = grid("W L1 L2 L3", "W L1 L2 L3", "W L4 L5 L3", "M1 M2 M3 M4", "M5 M5 M5 M5")
check("Wild сам по себе на трёх барабанах не платит", one(eng.evaluate(g), "W"), None)
g = grid("W L1 L2 L3", "W L1 L2 L3", "W L4 L5 L3", "W M2 M3 M4", "W M5 M5 M5")
w = one(eng.evaluate(g), "W")
check("Wild на всех 5 барабанах платит 5.00 × способы", (w.length, w.ways, w.units), (5, 1, 500))

# ---- закреплённый нижний ряд Wild: закрытые не работают, открытые слева направо
g = grid("H3 L1 L2 L3", "L4 L1 L2 H3", "L5 L1 L2 L3", "H3 M2 M3 M4", "M5 M5 M5 M5")
check("закрытые Wild не участвуют", one(eng.evaluate(g, wilds_open=0), "H3"), None)
w = one(eng.evaluate(g, wilds_open=3), "H3")
check("открытые Wild с первого барабана", (w.length, w.ways) if w else None, (4, 2 * 2 * 1 * 1))
check("открытый Wild нижнего ряда не имеет ячейки для взрыва", all(row < cfg.WORKING_ROWS for _, row in w.cells), True)
check("Wild на барабане справа от открытых не работает", one(eng.evaluate(g, wilds_open=1), "H3"), None)
w5 = one(eng.evaluate(grid("L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4"), wilds_open=5), "W")
check("пять открытых Wild платят 5.00", (w5.length, w5.ways, w5.units), (5, 1, 500))

# ---- число способов поля 4-4-4-4-4 и с открытым рядом Wild
full = grid(*["H1 H1 H1 H1"] * 5)
check("1024 способа при 4 рядах", one(eng.evaluate(full), "H1").ways, 4 ** 5)
check("3125 способов с пятью открытыми Wild", one(eng.evaluate(full, wilds_open=5), "H1").ways, 5 ** 5)

# ---- каскад: исчезновение, падение, новые символы (порядок розыгрыша: барабан слева направо, ряды сверху вниз)
class Scripted:
    def __init__(self, values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


pool = [("L5", 1)]                      # любой розыгрыш даёт L5
g = grid("H1 L1 L2 L3", "H1 L4 L5 M1", "H1 M2 M3 M4", "L1 L2 L3 L4", "L5 M1 M2 M3")
wins = eng.evaluate(g)
nxt, moves, fills = eng.cascade(g, wins, random.Random(0), pool)
check("вход каскада не изменён", g[0], ["H1", "L1", "L2", "L3"])
check("исчезли H1 из первых трёх барабанов", [nxt[0], nxt[1], nxt[2]], [["L5", "L1", "L2", "L3"], ["L5", "L4", "L5", "M1"], ["L5", "M2", "M3", "M4"]])
check("исчез верхний ряд: остальные остались на местах", moves, [])
check("новые сверху в трёх барабанах", sorted(fills), [[0, 0, "L5"], [1, 0, "L5"], [2, 0, "L5"]])
check("барабаны 4 и 5 не тронуты", nxt[3:], g[3:])
# падение: H1 внизу, остальные над ним сдвигаются вниз
g2 = grid("L1 L2 L3 H1", "L4 L5 M1 H1", "M2 M3 M4 H1", "L1 L2 L3 L4", "L5 M1 M2 M3")
nxt2, moves2, fills2 = eng.cascade(g2, eng.evaluate(g2), random.Random(0), pool)
check("символы над выигравшим падают на один ряд", nxt2[0], ["L5", "L1", "L2", "L3"])
check("падения записаны (барабан, из, в)", sorted(m for m in moves2 if m[0] == 0), [[0, 0, 1], [0, 1, 2], [0, 2, 3]])

# ---- цепочка каскадов: выигрыш считается на каждом шаге, суммируется, останавливается без выигрышей
class Chain:
    """Первое падение даёт H1 в трёх барабанах (второй выигрыш), второе разные M1, M2, M3: выигрышей больше нет."""
    def __init__(self):
        self.values = [0.0, 0.0, 0.0, 0.3, 0.55, 0.8]

    def random(self):
        return self.values.pop(0)


pool2 = [("H1", 1), ("M1", 1), ("M2", 1), ("M3", 1)]
g3 = grid("H1 L1 L2 L3", "H1 L4 L5 M1", "H1 M2 M3 M4", "L1 L2 L3 L4", "L5 M1 M2 M3")
res = eng.play_cascades(g3, 0, Chain(), pool2)
check("два шага: выигрыш H1 дважды по 150", [s["units"] for s in res["steps"]], [150, 150])
check("сумма единиц", res["units"], sum(s["units"] for s in res["steps"]))
check("после цепочки выигрышей нет", eng.evaluate(res["grid"]), [])

# ---- потолок цепочки
res = eng.play_cascades(g3, 0, Chain(), pool2, limit_units=200)
check("потолок обрезает выплату и останавливает цепочку", (res["units"], len(res["steps"])), (200, 2))

# ---- случайные поля: суммы совпадают, поле всегда полное, без выигрышей в конце цепочки
rng = random.Random(7)
pool_all = eng.make_pool()
for _ in range(300):
    g = eng.new_grid(rng, pool_all)
    res = eng.play_cascades(g, 2, rng, pool_all)
    assert all(len(col) == cfg.WORKING_ROWS for col in res["grid"]), "поле неполное"
    assert eng.evaluate(res["grid"], 2) == [], "цепочка закончилась с выигрышем"
    assert res["units"] == sum(s["units"] for s in res["steps"])
# ---- закреплённые Wild не делают цепочку вечной: нужны настоящие символы минимум на MIN_REAL_REELS барабанах
g = grid("H3 L1 L2 L3", "L4 L5 M1 M2", "M3 M4 L1 L2", "L3 L4 L5 M1", "M2 M3 M4 L1")
check("открытые Wild + один настоящий барабан: выигрыша нет", [w.sym for w in eng.evaluate(g, wilds_open=2) if w.sym == "H3"], [])
check("два настоящих барабана и открытые Wild: выигрыша нет (нужно 3 настоящих)", [w.sym for w in eng.evaluate(grid("H3 L1 L2 L3", "L4 L5 M1 M2", "H3 M4 L1 L2", "L3 L4 L5 M1", "M2 M3 M4 L1"), wilds_open=2) if w.sym == "H3"], [])
g = grid("H3 L1 L2 L3", "L4 L5 M1 M2", "H3 M4 L1 L2", "H3 L4 L5 M1", "M2 M3 M4 L1")
check("три настоящих барабана: платит, закреплённый Wild заполняет второй", [(w.length, w.ways) for w in eng.evaluate(g, wilds_open=2) if w.sym == "H3"], [(4, 2)])
rng = random.Random(11)
for opened in (0, 1, 2, 3, 4, 5):
    for _ in range(200):
        g = eng.new_grid(rng, pool_all)
        res = eng.play_cascades(g, opened, rng, pool_all)
        assert len(res["steps"]) < cfg.MAX_CASCADE_STEPS, "цепочка упёрлась в предел шагов (открыто Wild: %d)" % opened
        assert [w for w in eng.evaluate(res["grid"], opened) if w.cells] == [], "остался выигрыш со взрываемыми ячейками"
# весь нижний ряд открыт и в рабочих рядах Wild на всех барабанах нет: Wild за 5 платится один раз за цепочку
g5 = grid(*["L1 L2 L3 L4", "L5 M1 M2 M3", "M4 M5 L1 L2", "L3 L4 L5 M1", "M2 M3 M4 M5"])
r5 = eng.play_cascades(g5, 5, random.Random(3), [("M5", 1)])
check("Wild за 5 платит один раз", sum(1 for s in r5["steps"] for w in s["wins"] if w["sym"] == "W"), 1)
print("test_bloody_engine OK")
