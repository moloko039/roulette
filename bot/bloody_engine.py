"""Bloody slot: движок поля (стадия 1): сетка 5 барабанов, выигрыши «способами», Wild, каскады. Чистые функции без базы.

Поле: барабан = список символов сверху вниз (рабочие ряды); закреплённый нижний ряд Wild хранится отдельно как число открытых Wild
`wilds_open` (открыты первые N барабанов слева, остальные закрыты и в расчёте не участвуют).
Выигрыш: для каждого платящего символа комбинация идёт слева направо с первого барабана, минимум 3 барабана; число способов = произведение
числа подходящих ячеек (символ или Wild) на каждом барабане комбинации; платится только самая длинная комбинация символа. Scatter ничем не
заменяется и в выигрышах не участвует. Единицы: см. bloody_config.UNITS_PER_BET.
Случайность: объект с random() в [0, 1) (random.SystemRandom на сервере, random.Random(seed) в тестах)."""
import bloody_config as cfg


class Win:
    """Выигрыш по символу: sym, длина, способы, выплата в единицах, ячейки (барабан, ряд) рабочих рядов, участвующие в комбинации."""
    __slots__ = ("sym", "length", "ways", "units", "cells")

    def __init__(self, sym, length, ways, units, cells):
        self.sym, self.length, self.ways, self.units, self.cells = sym, length, ways, units, cells

    def as_dict(self):
        return {"sym": self.sym, "length": self.length, "ways": self.ways, "units": self.units}


def make_pool(weights=None, wild_weight=None, scatter_weight=None):
    """Таблица падения: список (символ, вес) с ненулевым весом. Стадия 2 меняет её по уровню Ritual bar."""
    weights = cfg.SYMBOL_WEIGHTS if weights is None else weights
    pool = [(s, w) for s, w in weights.items() if w > 0]
    ww = cfg.WILD_WEIGHT if wild_weight is None else wild_weight
    sw = cfg.SCATTER_WEIGHT if scatter_weight is None else scatter_weight
    if ww > 0:
        pool.append((cfg.WILD, ww))
    if sw > 0:
        pool.append((cfg.SCATTER, sw))
    return pool


def draw_symbol(rng, pool):
    total = sum(w for _, w in pool)
    x = rng.random() * total
    acc = 0
    for sym, w in pool:
        acc += w
        if x < acc:
            return sym
    return pool[-1][0]


def new_grid(rng, pool, rows=None):
    rows = cfg.WORKING_ROWS if rows is None else rows
    return [[draw_symbol(rng, pool) for _ in range(rows)] for _ in range(cfg.REELS)]


def _reel_count(grid, reel, sym, wilds_open):
    """(настоящие, всего): сколько ячеек барабана подходит символу. Настоящие: сам символ и Wild рабочих рядов (взрываются);
    всего: плюс открытый закреплённый Wild (только подставляется, не исчезает)."""
    real = sum(1 for c in grid[reel] if c == sym or c == cfg.WILD)
    return real, real + (1 if reel < wilds_open else 0)


def evaluate(grid, wilds_open=0):
    """Список Win по полю (порядок: по убыванию выплаты, затем по коду символа). Платит только самая длинная комбинация каждого символа."""
    wins = []
    for sym in cfg.PAY_SYMBOLS:
        ways = 1
        counts, reals = [], 0
        for reel in range(cfg.REELS):
            real, total = _reel_count(grid, reel, sym, wilds_open)
            if total == 0:
                break
            counts.append(total)
            reals += 1 if real > 0 else 0
        length = len(counts)
        if length < cfg.MIN_WIN_LENGTH or reals < cfg.MIN_REAL_REELS:
            continue
        for n in counts:
            ways *= n
        units = cfg.PAYTABLE_UNITS[sym][length] * ways
        cells = [(r, row) for r in range(length) for row, c in enumerate(grid[r]) if c == sym or c == cfg.WILD]
        wins.append(Win(sym, length, ways, units, cells))
    # Wild платит сам по себе только за 5 барабанов: на каждом барабане есть Wild (в рабочих рядах или открытый нижний)
    wild_counts = [sum(1 for c in grid[r] if c == cfg.WILD) + (1 if r < wilds_open else 0) for r in range(cfg.REELS)]
    if all(n > 0 for n in wild_counts):
        ways = 1
        for n in wild_counts:
            ways *= n
        cells = [(r, row) for r in range(cfg.REELS) for row, c in enumerate(grid[r]) if c == cfg.WILD]
        wins.append(Win(cfg.WILD, cfg.REELS, ways, cfg.WILD_PAY_UNITS[5] * ways, cells))
    wins.sort(key=lambda w: (-w.units, w.sym))
    return wins


def cascade(grid, wins, rng, pool):
    """Один шаг каскада: выигравшие ячейки исчезают, остальные падают вниз, сверху падают новые. Возвращает (новое поле, падения, новые):
    падения [[барабан, из ряда, в ряд], ...] для существующих символов, новые [[барабан, ряд, символ], ...]. Поле на входе не меняется."""
    gone = {cell for w in wins for cell in w.cells}
    new_grid_ = []
    moves, fills = [], []
    for reel, col in enumerate(grid):
        keep = [(row, c) for row, c in enumerate(col) if (reel, row) not in gone]
        height = len(col)
        shift = height - len(keep)
        out = [None] * height
        for i, (row, c) in enumerate(keep):
            to = shift + i
            out[to] = c
            if to != row:
                moves.append([reel, row, to])
        for row in range(shift):
            sym = draw_symbol(rng, pool)
            out[row] = sym
            fills.append([reel, row, sym])
        new_grid_.append(out)
    return new_grid_, moves, fills


def play_cascades(grid, wilds_open, rng, pool, limit_units=None):
    """Каскады до отсутствия выигрышей. Возвращает {"steps": [...], "units": сумма, "grid": итоговое поле}.
    Шаг: {"wins": [{sym, length, ways, units}], "cells": [[барабан, ряд], ...], "units": выплата шага, "moves": [...], "fills": [...]}.
    limit_units: потолок суммы за цепочку (раунд заканчивается на 16161×); при достижении выплата обрезается до потолка и цепочка прекращается."""
    steps, total = [], 0
    fixed_wild_paid = False                       # Wild на всех 5 барабанах без единой взрываемой ячейки (весь ряд открыт) платит один раз за цепочку
    for _ in range(cfg.MAX_CASCADE_STEPS):
        wins = evaluate(grid, wilds_open)
        if fixed_wild_paid:
            wins = [w for w in wins if not (w.sym == cfg.WILD and not w.cells)]
        elif any(w.sym == cfg.WILD and not w.cells for w in wins):
            fixed_wild_paid = True
        if not wins:
            break
        step_units = sum(w.units for w in wins)
        capped = False
        if limit_units is not None and total + step_units >= limit_units:
            step_units = limit_units - total
            capped = True
        nxt, moves, fills = cascade(grid, wins, rng, pool)
        cells = sorted({cell for w in wins for cell in w.cells})
        steps.append({"wins": [w.as_dict() for w in wins], "cells": [list(c) for c in cells], "units": step_units, "moves": moves, "fills": fills})
        total += step_units
        grid = nxt
        if capped:
            break
    return {"steps": steps, "units": total, "grid": grid}
