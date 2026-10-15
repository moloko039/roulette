"""Bloody slot: раунд целиком (чистая логика, без базы). Раунд = стартовый спин и серия бесплатных спинов (Candle Spins, Reign of Terror Spins), ставка
списывается один раз, выплата накапливается в единицах (bloody_config.UNITS_PER_BET) и выплачивается в конце раунда. Состояние раунда (Round) целиком
сериализуется в JSON: после перезагрузки Mini App игрок продолжает с того же места.

Правила владельца (v3), одинаковые во всех режимах. Один вызов play_spin = один спин от падения символов до конца всех каскадов:
  1. поле «устаивается»: Wild, которые не сыграли, опускаются вниз барабана и закрепляются (Sticky Wild); Scatter срабатывает (+очки полоски, в Reign +спин) и
     исчезает; xSplit (Dagger, Guillotine) разделяет ячейки и становится Wild; повторяется, пока поле не устоится;
  2. выигрыши способами (3+ барабана подряд с первого); сыгравшие символы и Wild исчезают, остальные падают, сверху падают новые (и снова п. 1), пока выигрыши есть;
  3. когда выигрышей нет и полоска набрала порог, переход на уровень N: самый дешёвый низкий символ взрывается, средний символ существа N превращается в высокий,
     открывается Wild нижнего ряда на барабане N (барабан становится из 5 ячеек, Wild в нижней), +2 фриспина; на уровне 5 Reign of Terror Spins (+10 спинов).
Высота барабана: 5, если его Wild нижнего ряда открыт (уровень полоски > номера барабана), иначе 4."""
import json

import bloody_config as cfg
import bloody_engine as eng

BASE, CANDLE, REIGN = "base", "candle", "reign"
CAP_UNITS = cfg.MAX_WIN_BETS * cfg.UNITS_PER_BET


class SpinError(Exception):
    code = "bloody_error"


class RoundFinished(SpinError):
    code = "round_finished"


class ActiveRoundExists(SpinError):
    code = "active_round"


class NoActiveRound(SpinError):
    code = "no_active_round"


class RequestConflict(SpinError):
    code = "request_conflict"


class BadBet(SpinError):
    code = "bad_bet"


class Round:
    """Состояние раунда. Хранится в базе как JSON (to_json / from_json)."""

    def __init__(self):
        self.bar = eng.BarState()
        self.candle_left = 0
        self.reign_left = 0
        self.reign_started = False
        self.sticky = [0] * cfg.REELS        # число прилипших Wild на барабанах (стоят внизу)
        self.units = 0                       # накопленный выигрыш раунда в единицах
        self.spins = 0                       # сыграно спинов (стартовый и бонусные)
        self.capped = False
        self.finished = False
        self.point_mult = 1                  # множитель очков полоски (Ритуальный буст xBet 1)
        self.no_reign = False                # спин покупки xBet 2: Reign не запускается
        self.buy = None                      # покупка функции: gibbet, reign, xbet1, xbet2 или None
        self.upgrade_cells = 0               # Виселица: сколько низких и средних символов первого поля заменить высокими
        self.guarantee_xsplit = False        # xBet 2: на первом поле гарантированы Dagger и Guillotine
        self.draw = None                     # Lucky Draw: выпавший уровень бонуса (3, 4 или 5)

    def next_mode(self):
        if self.finished:
            return None
        if self.spins == 0 and self.buy not in ("reign",) + cfg.BONUS_START_BUYS:
            return BASE
        if self.reign_left > 0:
            return REIGN
        if self.candle_left > 0:
            return CANDLE
        return None

    def to_dict(self):
        return {"bar": {"level": self.bar.level, "progress": self.bar.progress}, "candle_left": self.candle_left, "reign_left": self.reign_left,
                "reign_started": self.reign_started, "sticky": list(self.sticky), "units": self.units, "spins": self.spins, "capped": self.capped,
                "finished": self.finished, "point_mult": self.point_mult, "no_reign": self.no_reign, "buy": self.buy,
                "upgrade_cells": self.upgrade_cells, "guarantee_xsplit": self.guarantee_xsplit, "draw": self.draw}

    @classmethod
    def from_dict(cls, d):
        r = cls()
        r.bar = eng.BarState(d["bar"]["level"], d["bar"]["progress"])
        r.candle_left, r.reign_left, r.reign_started = d["candle_left"], d["reign_left"], d["reign_started"]
        r.sticky, r.units, r.spins = list(d["sticky"]), d["units"], d["spins"]
        r.capped, r.finished = d["capped"], d["finished"]
        r.point_mult, r.no_reign = d.get("point_mult", 1), d.get("no_reign", False)
        r.buy, r.upgrade_cells, r.guarantee_xsplit = d.get("buy"), d.get("upgrade_cells", 0), d.get("guarantee_xsplit", False)
        r.draw = d.get("draw")
        return r

    def to_json(self):
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))


def new_round(buy=None, rng=None):
    """Раунд с учётом покупки функции (None: обычная ставка). Для Lucky Draw нужен rng: розыгрыш бонуса делается здесь, на сервере."""
    r = Round()
    if buy is None:
        return r
    if buy not in cfg.BUY_COST_UNITS:
        raise BadBet()
    r.buy = buy
    if buy == "gibbet":
        r.bar = eng.BarState(cfg.GIBBET_LEVEL, 0)
        r.candle_left = cfg.GIBBET_CANDLE_SPINS
        r.upgrade_cells = cfg.GIBBET_UPGRADED_CELLS
    elif buy == "reign":
        r.bar = eng.BarState(cfg.BAR_MAX_LEVEL, 0)
        r.reign_left = cfg.REIGN_BUY_SPINS
        r.reign_started = True
    elif buy == "xbet1":
        r.bar = eng.BarState(cfg.XBET1_START_LEVEL, 0)
        r.point_mult = cfg.XBET1_POINT_MULT
    elif buy == "xbet2":
        r.bar = eng.BarState(cfg.XBET2_LEVEL, 0)
        r.no_reign = True
        r.guarantee_xsplit = True
    elif buy == "lucky":
        u = rng.random()
        acc = 0.0
        pick = cfg.LUCKY_DRAW[-1]
        for item in cfg.LUCKY_DRAW:
            acc += item[0]
            if u < acc:
                pick = item
                break
        level, spins = pick[1], pick[2]
        r.draw = level
        r.bar = eng.BarState(level, 0)
        if level >= cfg.BAR_MAX_LEVEL:
            r.reign_left = spins
            r.reign_started = True
        else:
            r.candle_left = spins
    return r


def heights(rnd):
    """Высота барабанов: 5, если Wild нижнего ряда этого барабана открыт, иначе 4. Покупка, стартующая с уровня N (Виселица, Lucky Draw, xBet 1, Reign,
    xBet 2), начинается так, будто уровни уже пройдены и их Wild уже сыграли (ДОПУЩЕНИЕ): барабаны 1..N из 5 свободных ячеек."""
    return [cfg.WORKING_ROWS + (1 if r < rnd.bar.level else 0) for r in range(cfg.REELS)]


def buy_cost_chips(bet, buy):
    """Цена покупки в фишках (для обычной ставки равна ставке)."""
    return bet if buy is None else bet * cfg.BUY_COST_UNITS[buy] // cfg.UNITS_PER_BET


_LOG = [True]


def set_logging(on):
    """Симулятор отключает сборку лога шагов (снимки поля), движок и деньги не затрагиваются."""
    _LOG[0] = bool(on)


class Field:
    """Поле спина: символы и множители xSplit (параллельные списки барабанов сверху вниз)."""

    def __init__(self, grid, mults=None):
        self.grid = grid
        self.mults = mults if mults is not None else [[1] * len(c) for c in grid]

    def snap(self):
        return [list(c) for c in self.grid], (None if all(m == 1 for c in self.mults for m in c) else [list(c) for c in self.mults])


def payout_chips(units, bet):
    """Выплата раунда в фишках: единицы * ставка // 100 (один раз на раунд, округление вниз)."""
    return units * bet // cfg.UNITS_PER_BET


_POOLS = {}


def pool_for(rnd, mode):
    """Таблицы падения по барабанам (список из REELS штук): состав символов по уровню полоски (на уровне 5 только высокие), раскладка барабанов REEL_BIAS,
    Wild, Scatter и xSplit по режиму. Кэш по всем числам, от которых таблица зависит."""
    level = rnd.bar.level
    xbet2 = rnd.buy == "xbet2"
    if mode == REIGN:
        wild, scatter, xs = cfg.WILD_WEIGHT_REIGN, cfg.SCATTER_WEIGHT_REIGN, cfg.XSPLIT_WEIGHT_REIGN
    elif xbet2:
        wild, scatter, xs = cfg.WILD_WEIGHT_REIGN, 0, cfg.XSPLIT_WEIGHT_XBET2
    elif mode == CANDLE:
        wild, scatter, xs = cfg.WILD_WEIGHT_CANDLE, cfg.SCATTER_WEIGHT_CANDLE[level], cfg.XSPLIT_WEIGHT
    else:
        wild, scatter, xs = cfg.WILD_WEIGHT, cfg.SCATTER_WEIGHT, cfg.XSPLIT_WEIGHT
    if not (mode == REIGN or level >= cfg.XSPLIT_MIN_LEVEL):
        xs = 0
    bias = cfg.REEL_BIAS_XBET2 if xbet2 else cfg.REEL_BIAS[level]
    key = (level, wild, scatter, xs, bias)
    pools = _POOLS.get(key)
    if pools is None:
        base = eng.pool_for_level(level, wild_weight=wild, scatter_weight=scatter)
        if xs > 0:
            base = base + [(cfg.XDAGGER, xs), (cfg.XGUILLOTINE, xs)]
        pools = _POOLS[key] = eng.reel_pools(base, bias)
    return pools


def _new_field(rnd, mode, rng, pools):
    """Новое поле спина: прилипшие Wild остаются внизу своих барабанов, остальные ячейки падают заново."""
    grid = []
    for reel, h in enumerate(heights(rnd)):
        k = min(rnd.sticky[reel], h)
        col = [pools[reel].draw(rng) for _ in range(h - k)] + [cfg.WILD] * k
        grid.append(col)
    return Field(grid)


def _remove(field, cells, rng, pool):
    """Ячейки исчезают, остальные (с множителями) падают, сверху падают новые (множитель 1). Возвращает (падения, новые)."""
    gone = set(cells)
    moves, fills = [], []
    for reel, col in enumerate(field.grid):
        mcol = field.mults[reel]
        keep = [(row, c, mcol[row]) for row, c in enumerate(col) if (reel, row) not in gone]
        height = len(col)
        shift = height - len(keep)
        out, mout = [None] * height, [1] * height
        for i, (row, c, m) in enumerate(keep):
            to = shift + i
            out[to], mout[to] = c, m
            if to != row:
                moves.append([reel, row, to])
        for row in range(shift):
            sym = pool[reel].draw(rng)
            out[row] = sym
            fills.append([reel, row, sym])
        field.grid[reel], field.mults[reel] = out, mout
    return moves, fills


def _sink(field):
    """Sticky Wild: Wild каждого барабана опускаются вниз (остальные символы сохраняют порядок). Возвращает падения."""
    moves = []
    for reel, col in enumerate(field.grid):
        mcol = field.mults[reel]
        order = [i for i, c in enumerate(col) if c != cfg.WILD] + [i for i, c in enumerate(col) if c == cfg.WILD]
        if order == list(range(len(col))):
            continue
        for to, frm in enumerate(order):
            if to != frm:
                moves.append([reel, frm, to])
        field.grid[reel] = [col[i] for i in order]
        field.mults[reel] = [mcol[i] for i in order]
    return moves


def _step(kind, field, **extra):
    if not _LOG[0]:
        return None
    grid, mults = field.snap()
    d = {"type": kind, "grid": grid, "mults": mults}
    d.update(extra)
    return d


def _settle(field, rnd, mode, rng, pool, steps):
    """Поле устаивается: Sticky Wild (во всех режимах), Scatter, xSplit. Повторяется, пока что-то происходит."""
    for _ in range(cfg.MAX_CASCADE_STEPS):
        moves = _sink(field)
        if moves:
            steps.append(_step("sink", field, moves=moves, units=0))
        scatters = [(r, row) for r, col in enumerate(field.grid) for row, c in enumerate(col) if c == cfg.SCATTER]
        if scatters:
            n = len(scatters)
            extra = {}
            if mode == REIGN:
                rnd.reign_left += n * cfg.REIGN_SCATTER_SPINS
                extra["spins"] = n * cfg.REIGN_SCATTER_SPINS
            elif not rnd.bar.final:
                pts = n * cfg.BAR_POINTS_SCATTER * rnd.point_mult
                eng.add_points(rnd.bar, pts)
                extra["points"] = pts
            moves, fills = _remove(field, scatters, rng, pool)
            steps.append(_step("scatter", field, cells=[list(c) for c in scatters], moves=moves, fills=fills, units=0, bar=rnd.bar.to_dict(),
                               reign_left=rnd.reign_left, **extra))
            continue
        specials = [(r, row, c) for r, col in enumerate(field.grid) for row, c in enumerate(col) if c in (cfg.XDAGGER, cfg.XGUILLOTINE)]
        if specials:
            special_cells = {(r, row) for r, row, _ in specials}
            splits = []
            for reel, row, kind in specials:
                if kind == cfg.XDAGGER:
                    cells = [(c, row) for c in range(cfg.REELS) if row < len(field.grid[c])]
                else:
                    cells = [(reel, j) for j in range(row + 1, len(field.grid[reel]))]
                cells = [c for c in cells if c not in special_cells]
                for c, j in cells:
                    field.mults[c][j] = min(cfg.XSPLIT_MAX_MULT, field.mults[c][j] * cfg.XSPLIT_FACTOR)
                splits.append({"kind": "dagger" if kind == cfg.XDAGGER else "guillotine", "at": [reel, row], "cells": [list(c) for c in cells]})
            for reel, row, _ in specials:
                field.grid[reel][row] = cfg.WILD
                field.mults[reel][row] = 1
            steps.append(_step("xsplit", field, splits=splits, units=0))
            continue
        break


def _level_up(field, rnd, rng):
    """Переход полоски на следующий уровень N (после доигранных каскадов): излишек очков переносится, самый дешёвый низкий символ взрывается, средний символ
    существа N превращается в высокий (на поле и в таблице падения), открывается Wild нижнего ряда на барабане N: барабан вырастает до 5 ячеек, Wild в нижней.
    Уровни 1-4: +2 Candle Spins; уровень 5: Reign of Terror Spins +10 (оставшиеся Candle Spins переходят в Reign)."""
    state = rnd.bar
    state.progress -= cfg.BAR_THRESHOLDS[state.level]
    state.level += 1
    lvl = state.level
    low, mid = cfg.LOW_REMOVE_ORDER[lvl - 1], cfg.MID_TRANSFORM_ORDER[lvl - 1]
    high = cfg.MID_TO_HIGH[mid]
    converted = []
    for r, col in enumerate(field.grid):
        for row, c in enumerate(col):
            if c == mid:
                col[row] = high
                converted.append([r, row, high])
    opened = lvl - 1
    field.grid[opened].append(cfg.WILD)
    field.mults[opened].append(1)
    gone = [(r, row) for r, col in enumerate(field.grid) for row, c in enumerate(col) if c == low]
    pools = pool_for(rnd, BASE)
    moves, fills = _remove(field, gone, rng, pools) if gone else ([], [])
    extra = {}
    if lvl <= cfg.CANDLE_MAX_LEVEL:
        rnd.candle_left += cfg.CANDLE_SPINS_PER_LEVEL
        extra["candle_spins"] = cfg.CANDLE_SPINS_PER_LEVEL
    elif state.final and not rnd.no_reign and not rnd.reign_started:
        rnd.reign_started = True
        rnd.reign_left += cfg.REIGN_SPINS + rnd.candle_left
        rnd.candle_left = 0
        extra["reign_spins"] = cfg.REIGN_SPINS
    return _step("level_up", field, level=lvl, wild=opened, opened=[opened, len(field.grid[opened]) - 1], removed=low, transform=[mid, high],
                 converted=converted, cells=[list(c) for c in gone], moves=moves, fills=fills, units=0, bar=state.to_dict(), candle_left=rnd.candle_left,
                 reign_left=rnd.reign_left, **extra)


def _apply_start_buy(field, rnd, rng):
    """Первое поле покупки: Виселица заменяет несколько низких и средних символов высокими, xBet 2 кладёт Dagger и Guillotine."""
    cells = [(r, row) for r, col in enumerate(field.grid) for row in range(len(col))]
    if rnd.upgrade_cells:
        weak = [c for c in cells if field.grid[c[0]][c[1]] in cfg.LOW or field.grid[c[0]][c[1]] in cfg.MID]
        rng_pick = list(weak)
        for _ in range(min(rnd.upgrade_cells, len(rng_pick))):
            i = int(rng.random() * len(rng_pick))
            r, row = rng_pick.pop(i)
            unlocked = [cfg.MID_TO_HIGH[m] for m in cfg.MID_TRANSFORM_ORDER[:rnd.bar.level]] or list(cfg.HIGH)       # какие высокие уже открыты на уровне покупки
            field.grid[r][row] = eng.draw_symbol(rng, [(h, 1) for h in unlocked])
    if rnd.guarantee_xsplit:
        free = list(cells)
        for sym in (cfg.XDAGGER, cfg.XGUILLOTINE):
            i = int(rng.random() * len(free))
            r, row = free.pop(i)
            field.grid[r][row] = sym


def play_spin(rnd, rng):
    """Один спин раунда. Меняет rnd; возвращает лог спина {mode, index, start, steps, units, total_units, ...} для клиента."""
    mode = rnd.next_mode()
    if mode is None:
        raise RoundFinished()
    rnd.spins += 1
    if mode == REIGN:
        rnd.reign_left -= 1
    elif mode == CANDLE:
        rnd.candle_left -= 1
    pools = pool_for(rnd, mode)
    field = _new_field(rnd, mode, rng, pools)
    if rnd.spins == 1:
        _apply_start_buy(field, rnd, rng)
    start_grid, start_mults = field.snap() if _LOG[0] else (None, None)
    steps = []
    spin_units = 0
    for _ in range(cfg.MAX_CASCADE_STEPS):
        pools = pool_for(rnd, mode)
        _settle(field, rnd, mode, rng, pools, steps)
        wins = eng.evaluate(field.grid, field.mults)
        if wins:
            units = sum(w.units for w in wins)
            capped = rnd.units + spin_units + units >= CAP_UNITS
            if capped:
                units = CAP_UNITS - rnd.units - spin_units
            cells = sorted({cell for w in wins for cell in w.cells})
            if mode != REIGN and not rnd.bar.final:
                eng.add_points(rnd.bar, eng.bar_points(field.grid, cells) * rnd.point_mult)
            wins_log = [w.as_dict() for w in wins]
            moves, fills = _remove(field, cells, rng, pools)
            spin_units += units
            steps.append(_step("cascade", field, wins=wins_log, cells=[list(c) for c in cells], units=units, moves=moves, fills=fills,
                               bar=rnd.bar.to_dict()))
            if capped:
                rnd.capped = True
                break
            continue
        if eng.level_ready(rnd.bar):
            steps.append(_level_up(field, rnd, rng))
            continue
        break
    rnd.units += spin_units
    rnd.sticky = [sum(1 for c in col if c == cfg.WILD) for col in field.grid]
    if rnd.capped or rnd.spins >= cfg.MAX_SPINS_PER_ROUND or (rnd.reign_left <= 0 and rnd.candle_left <= 0):
        rnd.finished = True
    if rnd.finished:
        rnd.candle_left = rnd.reign_left = 0
    steps = [x for x in steps if x is not None]
    return {"mode": mode, "index": rnd.spins, "start": {"grid": start_grid, "mults": start_mults, "bar": None}, "steps": steps, "units": spin_units,
            "total_units": rnd.units, "bar": rnd.bar.to_dict(), "candle_left": rnd.candle_left, "reign_left": rnd.reign_left, "sticky": list(rnd.sticky),
            "heights": heights(rnd), "finished": rnd.finished, "capped": rnd.capped}
