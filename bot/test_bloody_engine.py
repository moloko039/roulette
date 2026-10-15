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
check("Wild на всех 5 барабанах не платит, пока экран не заполнен Wild", one(eng.evaluate(g), "W"), None)
w = one(eng.evaluate(grid(*["W W W W"] * 4, "W W W W W")), "W")
check("экран из Wild платит 5.00", (w.length, w.ways, w.units), (5, 1, 500))
check("одни Wild за символ не играют (нужен сам символ)", [x.sym for x in eng.evaluate(grid("W L1 L2 L3", "W M1 M2 M3", "W L4 L5 L3", "H1 H2 H3 H4", "M5 M5 M5 M5")) if x.sym == "H5"], [])
check("Wild на первых трёх + символ на четвёртом: длина 4", [(x.length, x.ways) for x in eng.evaluate(grid("W L1 L2 L3", "W M1 M2 M3", "W L4 L5 L3", "H5 H2 H3 H4", "M5 M5 M5 M5")) if x.sym == "H5"], [(4, 1)])

# ---- барабаны разной высоты: открытый Wild нижнего ряда даёт барабану пятую ячейку (правила владельца v3)
g = grid("H3 L1 L2 L3 W", "L4 L1 L2 H3", "H3 L1 L2 L3", "M1 M2 M3 M4", "M5 M5 M5 M5")
w = one(eng.evaluate(g), "H3")
check("Wild в пятой ячейке первого барабана играет как обычный", (w.length, w.ways), (3, 2))
check("и взрывается вместе с комбинацией", (0, 4) in w.cells, True)
full = grid(*["H1 H1 H1 H1"] * 5)
check("1024 способа при 4 рядах", one(eng.evaluate(full), "H1").ways, 4 ** 5)
check("3125 способов на поле 5-5-5-5-5", one(eng.evaluate(grid(*["H1 H1 H1 H1 H1"] * 5)), "H1").ways, 5 ** 5)
check("поле 5-4-4-4-4", one(eng.evaluate(grid("H1 H1 H1 H1 H1", *["H1 H1 H1 H1"] * 4)), "H1").ways, 5 * 4 ** 4)


# ---- каскад: исчезновение, падение, новые символы (порядок розыгрыша: барабан слева направо, ряды сверху вниз)
pool = [("L5", 1)]                      # любой розыгрыш даёт L5
g = grid("H1 L1 L2 L3", "H1 L4 L5 M1", "H1 M2 M3 M4", "L1 L2 L3 L4", "L5 M1 M2 M3")
wins = eng.evaluate(g)
nxt, moves, fills = eng.cascade(g, wins, random.Random(0), pool)
check("вход каскада не изменён", g[0], ["H1", "L1", "L2", "L3"])
check("исчезли H1 из первых трёх барабанов", [nxt[0], nxt[1], nxt[2]], [["L5", "L1", "L2", "L3"], ["L5", "L4", "L5", "M1"], ["L5", "M2", "M3", "M4"]])
check("исчез верхний ряд: остальные остались на местах", moves, [])
check("новые сверху в трёх барабанах", sorted(fills), [[0, 0, "L5"], [1, 0, "L5"], [2, 0, "L5"]])
check("барабаны 4 и 5 не тронуты", nxt[3:], g[3:])
g2 = grid("L1 L2 L3 H1", "L4 L5 M1 H1", "M2 M3 M4 H1", "L1 L2 L3 L4", "L5 M1 M2 M3")
nxt2, moves2, fills2 = eng.cascade(g2, eng.evaluate(g2), random.Random(0), pool)
check("символы над выигравшим падают на один ряд", nxt2[0], ["L5", "L1", "L2", "L3"])
check("падения записаны (барабан, из, в)", sorted(m for m in moves2 if m[0] == 0), [[0, 0, 1], [0, 1, 2], [0, 2, 3]])
g3 = grid("L1 L2 L3 L4 W", "H1 L1 L2 L3", "H1 L1 L2 L3", "M1 M2 M3 M4", "M5 M5 M5 M5")
nxt3, _, _ = eng.cascade(g3, eng.evaluate(g3), random.Random(0), pool)
check("барабан из 5 ячеек остаётся из 5 после взрыва Wild", len(nxt3[0]), 5)

# ---- раскладка барабанов: bias 0 даёт одинаковые барабаны, bias > 0 чаще своё существо
base = eng.pool_for_level(0, wild_weight=0, scatter_weight=0)
same = eng.reel_pools(base, 0)
check("bias 0: барабаны одинаковы", len({tuple(p.items) for p in same}), 1)
bent = eng.reel_pools(base, 1.0)
w0 = dict(bent[0].items)
check("bias 1: на первом барабане ворон (M5) чаще ведьмы (M1)", w0["M5"] > w0["M1"] * cfg.SYMBOL_WEIGHTS["M5"] / cfg.SYMBOL_WEIGHTS["M1"], True)
check("существа символов", [eng.family(x) for x in ("L5", "M5", "H5", "M1", "W", "S", "XD")], [0, 0, 0, 4, None, None, None])
rng = random.Random(4)
counts = {}
for _ in range(4000):
    x = same[0].draw(rng)
    counts[x] = counts.get(x, 0) + 1
check("розыгрыш по весам: L5 (16) чаще M1 (10)", counts["L5"] > counts["M1"], True)
print("test_bloody_engine OK")
