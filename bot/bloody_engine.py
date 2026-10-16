"""Bloody slot: движок поля: 5 барабанов, выигрыши «способами», Wild, каскады, Ritual bar. Чистые функции без базы.

Поле: барабан = список символов сверху вниз. Высота барабана 4 или 5: пятая (нижняя) ячейка появляется, когда открыт Wild нижнего ряда этого барабана
(открытый Wild лежит в этой ячейке как обычный Wild поля; сыграв, он исчезает, ячейка остаётся свободной, правила владельца v3).
Выигрыш: для каждого платящего символа комбинация идёт слева направо с первого барабана, минимум 3 барабана подряд; число способов = произведение
числа подходящих ячеек (символ или Wild, с множителями xSplit) на каждом барабане комбинации; платится самая длинная комбинация символа; хотя бы одна
ячейка комбинации должна быть самим символом (одни Wild за символ не играют). Scatter ничем не заменяется и в выигрышах не участвует.
Единицы: см. bloody_config.UNITS_PER_BET. Случайность: объект с random() в [0, 1) (random.SystemRandom на сервере, random.Random(seed) в тестах)."""
import bisect
import math

import bloody_config as cfg


class Win:
    """Выигрыш по символу: sym, длина, способы, выплата в единицах, ячейки (барабан, ряд), которые взрываются (символ и Wild на барабанах комбинации)."""
    __slots__ = ("sym", "length", "ways", "units", "cells")

    def __init__(self, sym, length, ways, units, cells):
        self.sym, self.length, self.ways, self.units, self.cells = sym, length, ways, units, cells

    def as_dict(self):
        return {"sym": self.sym, "length": self.length, "ways": self.ways, "units": self.units}


class Pool:
    """Таблица падения одного барабана: символы и накопленные веса (быстрый розыгрыш делением пополам)."""
    __slots__ = ("items", "syms", "cum", "total")

    def __init__(self, items):
        self.items = [(s, w) for s, w in items if w > 0]
        self.syms = [s for s, _ in self.items]
        self.cum = []
        acc = 0.0
        for _, w in self.items:
            acc += w
            self.cum.append(acc)
        self.total = acc

    def draw(self, rng):
        i = bisect.bisect_right(self.cum, rng.random() * self.total)
        return self.syms[min(i, len(self.syms) - 1)]


def make_pool(weights=None, wild_weight=None, scatter_weight=None):
    """Таблица падения: список (символ, вес) с ненулевым весом."""
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
    if isinstance(pool, Pool):
        return pool.draw(rng)
    total = sum(w for _, w in pool)
    x = rng.random() * total
    acc = 0
    for sym, w in pool:
        acc += w
        if x < acc:
            return sym
    return pool[-1][0]


def family(sym):
    """Существо символа 0..4 (ворон, змея, козёл, волк, ведьма): у L5, M5, H5 это ворон, ... у L1, M1, H1 ведьма. Для Wild, Scatter, xSplit None."""
    if len(sym) == 2 and sym[0] in "LMH" and sym[1].isdigit():
        return 5 - int(sym[1])
    return None


def reel_pools(pool, bias):
    """Раскладка барабанов (аналог лент настоящего слота): на барабане r чаще выпадает существо r, остальные реже с множителем exp(-bias * расстояние по кругу).
    bias = 0: все барабаны одинаковы. Wild, Scatter и xSplit не меняются."""
    out = []
    for reel in range(cfg.REELS):
        items = []
        for sym, w in pool:
            f = family(sym)
            if f is not None and bias:
                w *= math.exp(-bias * ((f - reel) % cfg.REELS))
            items.append((sym, w))
        out.append(Pool(items))
    return out


def new_grid(rng, pool, rows=None):
    rows = cfg.WORKING_ROWS if rows is None else rows
    return [[draw_symbol(rng, pool) for _ in range(rows)] for _ in range(cfg.REELS)]


def evaluate(grid, mults=None, min_length=None):
    """Список Win по полю (порядок: по убыванию выплаты, затем по коду символа). Платит только самая длинная комбинация каждого символа."""
    min_length = cfg.MIN_WIN_LENGTH if min_length is None else min_length
    present = {c for col in grid for c in col}
    wins = []
    for sym in cfg.PAY_SYMBOLS:
        if sym not in present:
            continue
        counts = []
        for reel in range(cfg.REELS):
            col = grid[reel]
            if mults is None:
                n = sum(1 for c in col if c == sym or c == cfg.WILD)
            else:
                n = sum(mults[reel][row] for row, c in enumerate(col) if c == sym or c == cfg.WILD)
            if n == 0:
                break
            counts.append(n)
        length = len(counts)
        if length < min_length or not any(c == sym for r in range(length) for c in grid[r]):
            continue
        ways = 1
        for n in counts:
            ways *= n
        cells = [(r, row) for r in range(length) for row, c in enumerate(grid[r]) if c == sym or c == cfg.WILD]
        wins.append(Win(sym, length, ways, cfg.PAYTABLE_UNITS[sym][length] * ways, cells))
    # Wild платит сам по себе только когда весь экран из Wild (гайд владельца): 5.00 ставки за экран
    if all(c == cfg.WILD for col in grid for c in col):
        cells = [(r, row) for r in range(cfg.REELS) for row in range(len(grid[r]))]
        wins.append(Win(cfg.WILD, cfg.REELS, 1, cfg.WILD_PAY_UNITS[5], cells))
    wins.sort(key=lambda w: (-w.units, w.sym))
    return wins


def cascade(grid, wins, rng, pool):
    """Один шаг каскада: выигравшие ячейки исчезают, остальные падают вниз, сверху падают новые. Возвращает (новое поле, падения, новые):
    падения [[барабан, из ряда, в ряд], ...] для существующих символов, новые [[барабан, ряд, символ], ...]. Поле на входе не меняется.
    pool: одна таблица на все барабаны или список таблиц по барабанам."""
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
        p = pool[reel] if isinstance(pool, list) and pool and isinstance(pool[0], Pool) else pool
        for row in range(shift):
            sym = draw_symbol(rng, p)
            out[row] = sym
            fills.append([reel, row, sym])
        new_grid_.append(out)
    return new_grid_, moves, fills


class BarState:
    """Состояние Ritual bar: уровень (число пройденных порогов, 0..5) и очки внутри уровня. Перед каждым новым раундом создаётся заново."""
    __slots__ = ("level", "progress")

    def __init__(self, level=0, progress=0):
        self.level, self.progress = level, progress

    @property
    def final(self):
        return self.level >= cfg.BAR_MAX_LEVEL

    def to_dict(self):
        """Для игрока и лога: очки целые (ускорение xBet 1 даёт дробные, внутри они копятся точно; пороги целые, поэтому переход тот же)."""
        return {"level": self.level, "progress": math.floor(self.progress), "threshold": None if self.final else cfg.BAR_THRESHOLDS[self.level]}

    def copy(self):
        return BarState(self.level, self.progress)


def pool_for_level(level, weights=None, wild_weight=None, scatter_weight=None):
    """Таблица падения на уровне полоски: убраны самые дешёвые низкие символы (по одному за уровень); высокие символы в начале раунда не падают совсем и появляются
    только как улучшение среднего (M5 → H5 на уровне 1, M4 → H4 на уровне 2, ...): вес среднего переходит высокому, среднего больше нет."""
    weights = dict(cfg.SYMBOL_WEIGHTS if weights is None else weights)
    for sym in cfg.LOW_REMOVE_ORDER[:level]:
        weights[sym] = 0
    for sym in cfg.MID_TRANSFORM_ORDER[:level]:
        weights[cfg.MID_TO_HIGH[sym]] = weights.get(cfg.MID_TO_HIGH[sym], 0) + weights.get(sym, 0)
        weights[sym] = 0
    return make_pool(weights, wild_weight, scatter_weight)


def bar_points(grid, cells):
    pts = 0
    for reel, row in cells:
        sym = grid[reel][row]
        if sym in cfg.MID:
            pts += cfg.BAR_POINTS_MID
        elif sym in cfg.HIGH:
            pts += cfg.BAR_POINTS_HIGH
    return pts


def add_points(state, pts):
    """Добавляет очки; уровень не повышает (переход делается после цепочки каскадов)."""
    state.progress += pts


def level_ready(state):
    return (not state.final) and state.progress >= cfg.BAR_THRESHOLDS[state.level]
