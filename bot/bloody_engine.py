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


class BarState:
    """Состояние Ritual bar: уровень (число пройденных порогов, 0..5) и очки внутри уровня. Перед каждым новым раундом создаётся заново."""
    __slots__ = ("level", "progress")

    def __init__(self, level=0, progress=0):
        self.level, self.progress = level, progress

    @property
    def wilds_open(self):
        return self.level

    @property
    def final(self):
        return self.level >= cfg.BAR_MAX_LEVEL

    def to_dict(self):
        return {"level": self.level, "progress": self.progress, "threshold": None if self.final else cfg.BAR_THRESHOLDS[self.level]}

    def copy(self):
        return BarState(self.level, self.progress)


def pool_for_level(level, weights=None, wild_weight=None, scatter_weight=None):
    """Таблица падения на уровне полоски: убраны самые дешёвые низкие символы (по одному за уровень), их заменяют высокие вместо превращённых средних
    (вес среднего переходит соответствующему высокому)."""
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


def level_up(grid, state, rng, weights=None, wild_weight=None, scatter_weight=None):
    """Один переход на следующий уровень (вызывать, пока level_ready и цепочка каскадов закончена). Меняет state, возвращает (новое поле, шаг):
    излишек очков переносится; открывается Wild номер `level`; самый дешёвый оставшийся низкий символ взрывается (ячейки уходят, остальные падают, новые
    берутся из таблицы нового уровня); самый дорогой оставшийся средний превращается в высокий на месте."""
    state.progress -= cfg.BAR_THRESHOLDS[state.level]
    state.level += 1
    lvl = state.level
    low = cfg.LOW_REMOVE_ORDER[lvl - 1]
    mid = cfg.MID_TRANSFORM_ORDER[lvl - 1]
    high = cfg.MID_TO_HIGH[mid]
    converted = [[r, row, high] for r, col in enumerate(grid) for row, c in enumerate(col) if c == mid]
    work = [[high if c == mid else c for c in col] for col in grid]
    gone = [(r, row) for r, col in enumerate(work) for row, c in enumerate(col) if c == low]
    pool = pool_for_level(lvl, weights, wild_weight, scatter_weight)
    moves, fills = [], []
    if gone:
        fake = [Win(low, 0, 0, 0, gone)]
        work, moves, fills = cascade(work, fake, rng, pool)
    step = {"type": "level_up", "level": lvl, "wild": lvl - 1, "removed": low, "transform": [mid, high], "converted": converted,
            "cells": [list(c) for c in gone], "moves": moves, "fills": fills, "units": 0, "bar": state.to_dict()}
    return work, step


def play_spin(grid, state, rng, weights=None, wild_weight=None, scatter_weight=None, limit_units=None):
    """Один розыгрыш поля от готовой сетки до конца: каскады, очки полоски, переходы уровней (после того как цепочка каскадов доиграна и выплачена,
    Wild открывается и состав символов меняется, затем каскады продолжаются). Возвращает {"steps", "units", "grid", "final_reached": дошла ли полоска до
    последнего уровня}. Шаги: type cascade (выигрыши, взрыв, падение; bar: состояние полоски после шага) и type level_up."""
    steps, total = [], 0
    final_reached = state.final
    for _ in range(cfg.MAX_CASCADE_STEPS):
        pool = pool_for_level(state.level, weights, wild_weight, scatter_weight)
        remaining = None if limit_units is None else limit_units - total
        chain = play_cascades_bar(grid, state, rng, pool, remaining)
        steps.extend(chain["steps"])
        total += chain["units"]
        grid = chain["grid"]
        if limit_units is not None and total >= limit_units:
            break
        if not level_ready(state):
            break
        grid, step = level_up(grid, state, rng, weights, wild_weight, scatter_weight)
        steps.append(step)
        if state.final:
            final_reached = True
    return {"steps": steps, "units": total, "grid": grid, "final_reached": final_reached}


def play_cascades_bar(grid, state, rng, pool, limit_units=None):
    """Каскады текущей цепочки с начислением очков полоски (по взорванным ячейкам до удаления)."""
    steps, total = [], 0
    fixed_wild_paid = False
    for _ in range(cfg.MAX_CASCADE_STEPS):
        wins = evaluate(grid, state.wilds_open)
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
        cells = sorted({cell for w in wins for cell in w.cells})
        if not state.final:
            add_points(state, bar_points(grid, cells))
        nxt, moves, fills = cascade(grid, wins, rng, pool)
        steps.append({"type": "cascade", "wins": [w.as_dict() for w in wins], "cells": [list(c) for c in cells], "units": step_units,
                      "moves": moves, "fills": fills, "bar": state.to_dict()})
        total += step_units
        grid = nxt
        if capped:
            break
    return {"steps": steps, "units": total, "grid": grid}


def play_cascades(grid, wilds_open, rng, pool, limit_units=None):
    """Каскады без переходов уровней (для проверок движка поля): полоска стоит на уровне wilds_open, очки не влияют на ход."""
    chain = play_cascades_bar(grid, BarState(wilds_open), rng, pool, limit_units)
    return chain
