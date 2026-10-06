"""Western Slot: правила и генератор исходов (чистые функции, без базы).

Порт движка проекта casinch (src/engine/*, src/math/config.ts, пресет original-like): каскадный слот 6 барабанов высотой
3-4-5-5-4-3, выплаты «способами» (ways), взрыв выигравших ячеек, символ в золотой рамке становится Wild, множитель удваивается
после каждого выигрышного шага до x1024, 3 и более Scatter дают бесплатные вращения (10 + 2 за каждый сверх трёх),
покупка бонуса за 75 ставок, потолок выигрыша раунда 5000 ставок. Правила и числа здесь единственный источник истины для
сервера; клиент в games/western-slot только показывает присланный раунд и ничего не считает.

Деньги: движок считает в единицах (монета = 1), ставка 20 единиц; выплата в фишках = единицы * монета (COIN_VALUES).
Случайность: объект с методом random() в [0, 1) (random.SystemRandom на сервере, random.Random(seed) в тестах).

Формат раунда (JSON для клиента, он же хранится в slot_rounds.round_json): ключи как в типах движка casinch (camelCase),
поля в текстовой записи: барабаны слева направо через «|», ячейки сверху вниз, «*» после символа = золотая рамка
(пример: «K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A»).
"""
import random
from collections import namedtuple

PAY_SYMBOLS = ("B", "R", "H", "V", "A", "K", "Q", "J")
SCATTER = "S"
WILD = "W"
ALL_SYMBOLS = PAY_SYMBOLS + (SCATTER, WILD)
REEL_HEIGHTS = (3, 4, 5, 5, 4, 3)
REEL_COUNT = len(REEL_HEIGHTS)
MIN_WIN_LENGTH = 3

# выплата за один способ при длине комбинации 3, 4, 5, 6 (в единицах)
PAYTABLE = {
    "B": (10, 20, 30, 50),
    "R": (8, 15, 20, 30),
    "H": (5, 10, 15, 20),
    "V": (5, 10, 15, 20),
    "A": (2, 4, 6, 10),
    "K": (2, 4, 6, 10),
    "Q": (1, 2, 3, 5),
    "J": (1, 2, 3, 5),
}

# допустимые монеты (как COIN_VALUES клиента): ставка 20 монет, то есть от 20 до 1 000 000 фишек
COIN_VALUES = (1, 2, 5, 10, 25, 50, 100, 250, 500, 2500, 10000, 25000, 50000)

GenParams = namedtuple("GenParams", "hit tau q scatter refill_scatter")
Config = namedtuple("Config", (
    "name paytable weights adj_redraw frame_p base fs buy_scatter buy_cost_bets bet_coins base_start_multiplier "
    "fs_start_multiplier max_multiplier fs_min_scatters fs_base_award fs_per_extra_scatter win_cap_bets "
    "first_board_max_tries refill_max_tries max_free_spins_per_round"))

# Пресет original-like (итог настройки симулятором casinch: RTP 96,7 %, бонус 1 на 175 спинов, два Scatter 7 % спинов)
CONFIG = Config(
    name="original-like",
    paytable=PAYTABLE,
    weights={"B": 100, "R": 100, "H": 111, "V": 126, "A": 140, "K": 125, "Q": 131, "J": 131},
    adj_redraw=0.6,
    frame_p=(0, 0, 0.3, 0.19, 0, 0),
    base=GenParams(hit=0.28, tau=2, q=(0.11, 0.2279, 0.2735, 0.3191, 0.3647, 0.3989),
                   scatter=(0.2, 0.0625, 0.001887, 0.00027, 0.0000385), refill_scatter=0.03),
    fs=GenParams(hit=0.3, tau=3, q=(0.1993, 0.2492, 0.299, 0.3488, 0.3986),
                 scatter=(0.15, 0.05, 0.006, 0.0006, 0), refill_scatter=0.03),
    buy_scatter=(0.85, 0.13, 0.02),
    buy_cost_bets=75,
    bet_coins=20,
    base_start_multiplier=1,
    fs_start_multiplier=8,
    max_multiplier=1024,
    fs_min_scatters=3,
    fs_base_award=10,
    fs_per_extra_scatter=2,
    win_cap_bets=5000,
    first_board_max_tries=400,
    refill_max_tries=120,
    max_free_spins_per_round=500,
)

# Стартовые параметры постановки (до настройки): нужны контрольным тестам цепочки каскадов
START_CONFIG = CONFIG._replace(
    name="start",
    base=GenParams(hit=0.28, tau=2, q=(0.18, 0.2, 0.24, 0.28, 0.32, 0.35),
                   scatter=(0.2, 0.07, 0.0049, 0.0007, 0.0001), refill_scatter=0),
    fs=GenParams(hit=0.3, tau=3, q=(0.2, 0.25, 0.3, 0.35, 0.4),
                 scatter=(0.15, 0.05, 0.006, 0.0006, 0), refill_scatter=0),
)

# Вероятность потерять всю ставку (для опыта, xp.slot_xp), в тысячных. Платный спин: первое поле проигрывает с
# вероятностью 1 - hit = 0,72, из них около 0,2 % дают бонус тремя Scatter, который почти всегда платит: 0,718.
# Покупка бонуса: все 10 бесплатных вращений без выигрыша, (1 - 0,3)^10 = 0,028.
SPIN_LOSE_X1000 = 718
BUY_LOSE_X1000 = 28


class SlotError(Exception):
    """Ошибки раунда, которые уходят клиенту как 409 с кодом."""
    code = "error"


class RequestConflict(SlotError):
    code = "request_conflict"


# ---------- деньги ----------

def validate_coin(coin):
    """Монета из списка COIN_VALUES (bool не монета). Возвращает её или бросает ValueError."""
    if type(coin) is not int or coin not in COIN_VALUES:
        raise ValueError("coin out of range")
    return coin


def bet_units(cfg=CONFIG):
    return cfg.bet_coins


def buy_cost_units(cfg=CONFIG):
    return cfg.buy_cost_bets * cfg.bet_coins


def win_cap_units(cfg=CONFIG):
    return cfg.win_cap_bets * cfg.bet_coins


def cost_chips(coin, buy, cfg=CONFIG):
    """Списание за раунд в фишках: ставка или цена бонуса."""
    return (buy_cost_units(cfg) if buy else bet_units(cfg)) * coin


def max_payout_chips(coin, cfg=CONFIG):
    """Наибольшая выплата раунда в фишках (потолок 5000 ставок)."""
    return win_cap_units(cfg) * coin


def lose_x1000(buy):
    return BUY_LOSE_X1000 if buy else SPIN_LOSE_X1000


# ---------- запись поля ----------

def parse_cell(token):
    framed = token.endswith("*")
    code = token[:-1] if framed else token
    if code not in ALL_SYMBOLS:
        raise ValueError("unknown symbol token %r" % token)
    return (code, framed)


def parse_cells(text):
    text = text.strip()
    return tuple(parse_cell(t) for t in text.split()) if text else ()


def parse_board(text, allow_short=False):
    """Поле из текстовой записи. allow_short: барабаны могут быть короче высоты (поле после взрыва)."""
    reels = tuple(parse_cells(part) for part in text.split("|"))
    if len(reels) != REEL_COUNT:
        raise ValueError("expected %d reels, got %d" % (REEL_COUNT, len(reels)))
    for r, reel in enumerate(reels):
        h = REEL_HEIGHTS[r]
        ok = len(reel) <= h if allow_short else len(reel) == h
        if not ok:
            raise ValueError("reel %d has %d cells, expected %d" % (r + 1, len(reel), h))
    return reels


def format_cell(cell):
    return cell[0] + "*" if cell[1] else cell[0]


def format_cells(cells):
    return " ".join(format_cell(c) for c in cells)


def format_board(board):
    return " | ".join(format_cells(reel) for reel in board)


# ---------- оценка поля ----------

def _count_matches(reel, sym):
    n = 0
    for cell in reel:
        s = cell[0]
        if s == sym or s == WILD:
            n += 1
    return n


def evaluate(board, paytable=PAYTABLE):
    """Выигрышные комбинации поля (единицы, без множителя): [{sym, length, ways, pay}]."""
    wins = []
    for sym in PAY_SYMBOLS:
        ways = 1
        length = 0
        for r in range(REEL_COUNT):
            count = _count_matches(board[r], sym)
            if count == 0:
                break
            ways *= count
            length += 1
        if length >= MIN_WIN_LENGTH:
            wins.append({"sym": sym, "length": length, "ways": ways, "pay": paytable[sym][length - MIN_WIN_LENGTH] * ways})
    return wins


def evaluate_total(board, paytable=PAYTABLE):
    """Сумма выплат поля без списка комбинаций (горячий путь генератора)."""
    total = 0
    for sym in PAY_SYMBOLS:
        ways = 1
        length = 0
        for r in range(REEL_COUNT):
            count = _count_matches(board[r], sym)
            if count == 0:
                break
            ways *= count
            length += 1
        if length >= MIN_WIN_LENGTH:
            total += paytable[sym][length - MIN_WIN_LENGTH] * ways
    return total


def sum_wins(wins):
    return sum(w["pay"] for w in wins)


# ---------- каскад ----------

WILD_CELL = (WILD, False)
SCATTER_CELL = (SCATTER, False)


def resolve_wins(board, wins):
    """Что взрывается и что становится Wild: ({reel, row}...). Символ в рамке не взрывается, а становится Wild без рамки;
    Wild, участвовавший в комбинации, взрывается как обычный символ."""
    marks = [[0] * len(reel) for reel in board]   # 0 не тронут, 1 взрыв, 2 становится Wild
    for win in wins:
        sym = win["sym"]
        for r in range(win["length"]):
            reel = board[r]
            for row, cell in enumerate(reel):
                s = cell[0]
                if s != sym and s != WILD:
                    continue
                marks[r][row] = 2 if cell[1] and s != WILD else 1
    exploded = []
    to_wild = []
    for r in range(REEL_COUNT):
        for row, mark in enumerate(marks[r]):
            if mark == 1:
                exploded.append({"reel": r, "row": row})
            elif mark == 2:
                to_wild.append({"reel": r, "row": row})
    return exploded, to_wild


def collapse(board, exploded, to_wild):
    """Поле без взорванных ячеек, рамочные победители заменены на Wild; барабаны становятся короче (пустота сверху)."""
    explode = {(c["reel"], c["row"]) for c in exploded}
    wild = {(c["reel"], c["row"]) for c in to_wild}
    result = []
    for r, reel in enumerate(board):
        kept = []
        for row, cell in enumerate(reel):
            key = (r, row)
            if key in explode:
                continue
            kept.append(WILD_CELL if key in wild else cell)
        result.append(tuple(kept))
    return tuple(result)


def describe_refill(collapsed, refilled):
    """Новые ячейки сверху каждого барабана: [{reel, cells: текст}]."""
    result = []
    for r in range(REEL_COUNT):
        missing = REEL_HEIGHTS[r] - len(collapsed[r])
        if missing > 0:
            result.append({"reel": r, "cells": format_cells(refilled[r][:missing])})
    return result


def count_scatters(board):
    return sum(1 for reel in board for cell in reel if cell[0] == SCATTER)


# ---------- генератор ----------

def make_picker(weights):
    """Взвешенный выбор платящего символа: функция от rng."""
    cumulative = []
    total = 0
    for sym in PAY_SYMBOLS:
        total += weights[sym]
        cumulative.append(total)
    last = len(PAY_SYMBOLS) - 1
    pairs = tuple(zip(cumulative[:last], PAY_SYMBOLS[:last]))
    last_sym = PAY_SYMBOLS[last]

    def pick(rng):
        x = rng.random() * total
        for bound, sym in pairs:
            if x < bound:
                return sym
        return last_sym
    return pick


def generate_cell(cfg, pick, rng, reel, below):
    sym = pick(rng)
    if sym == below and rng.random() < cfg.adj_redraw:
        sym = pick(rng)
    fp = cfg.frame_p[reel]
    framed = fp > 0 and rng.random() < fp
    return (sym, framed)


def fill_reel(cfg, pick, rng, reel, existing):
    """Барабан, досыпанный снизу вверх поверх existing (возможно пустого)."""
    missing = REEL_HEIGHTS[reel] - len(existing)
    if missing <= 0:
        return tuple(existing)
    fresh = [None] * missing
    below = existing[0][0] if existing else None
    for i in range(missing - 1, -1, -1):
        cell = generate_cell(cfg, pick, rng, reel, below)
        fresh[i] = cell
        below = cell[0]
    return tuple(fresh) + tuple(existing)


ALL_CELLS = tuple((r, row) for r, h in enumerate(REEL_HEIGHTS) for row in range(h))


def pick_distinct_cells(rng, n):
    """n разных ячеек поля равновероятно (частичный Фишер-Йетс); несколько могут быть на одном барабане."""
    cells = list(ALL_CELLS)
    count = min(n, len(cells))
    for i in range(count):
        j = i + int(rng.random() * (len(cells) - i))
        cells[i], cells[j] = cells[j], cells[i]
    return cells[:count]


def generate_board(cfg, pick, rng, n_scatters):
    """Полное первое поле ровно с n_scatters Scatter в любых ячейках."""
    board = [fill_reel(cfg, pick, rng, r, ()) for r in range(REEL_COUNT)]
    for r, row in pick_distinct_cells(rng, n_scatters):
        reel = list(board[r])
        reel[row] = SCATTER_CELL
        board[r] = tuple(reel)
    return tuple(board)


def plan_refill_scatters(params, rng, collapsed):
    """Какие новые ячейки досыпки станут Scatter: по барабанам список индексов (сверху) среди досыпаемых ячеек.
    Каждая новая ячейка независимо с вероятностью refill_scatter; барабан с Scatter может получить ещё."""
    p = params.refill_scatter
    plan = []
    for r, reel in enumerate(collapsed):
        missing = REEL_HEIGHTS[r] - len(reel)
        rows = []
        if missing > 0 and p > 0:
            for i in range(missing):
                if rng.random() < p:
                    rows.append(i)
        plan.append(rows)
    return plan


def refill_board(cfg, pick, rng, collapsed, scatter_rows=None):
    """Досыпка пустых ячеек сверху; scatter_rows (из plan_refill_scatters) делает выбранные новые ячейки Scatter."""
    result = []
    for r, reel in enumerate(collapsed):
        filled = fill_reel(cfg, pick, rng, r, reel)
        rows = scatter_rows[r] if scatter_rows else ()
        if rows:
            chosen = set(rows)
            filled = tuple(SCATTER_CELL if i in chosen else cell for i, cell in enumerate(filled))
        result.append(filled)
    return tuple(result)


class BoardSource:
    """Источник полей одного режима (base или fs): сначала решается, должен ли шаг выиграть, потом поля тянутся,
    пока одно не совпадёт с решением."""

    def __init__(self, cfg, params, rng, pick=None):
        self.cfg = cfg
        self.params = params
        self.rng = rng
        self.pick = pick or make_picker(cfg.weights)

    def first(self, n_scatters):
        cfg, params, rng = self.cfg, self.params, self.rng
        want_win = rng.random() < params.hit
        board = ()
        for _ in range(cfg.first_board_max_tries):
            board = generate_board(cfg, self.pick, rng, n_scatters)
            win = evaluate_total(board, cfg.paytable)
            if (win > 0) != want_win:
                continue
            if win > 0 and rng.random() > min(1.0, params.tau / win):
                continue
            return board
        return board

    def refill(self, collapsed, step_index):
        cfg, params, rng = self.cfg, self.params, self.rng
        q_index = min(step_index, len(params.q)) - 1
        want_win = rng.random() < params.q[q_index]
        scatter_rows = plan_refill_scatters(params, rng, collapsed)   # решается один раз: повторы перетягивают только символы
        board = ()
        for _ in range(cfg.refill_max_tries):
            board = refill_board(cfg, self.pick, rng, collapsed, scatter_rows)
            if (evaluate_total(board, cfg.paytable) > 0) == want_win:
                return board
        return board   # желание может быть невыполнимым (например, новый Wild уже платит)


# ---------- спин и раунд ----------

def free_spins_for(cfg, scatters):
    if scatters < cfg.fs_min_scatters:
        return 0
    return cfg.fs_base_award + cfg.fs_per_extra_scatter * (scatters - cfg.fs_min_scatters)


def _step(board, wins, multiplier, step_win, exploded, to_wild, refilled, board_after):
    return {"board": format_board(board), "wins": wins, "multiplier": multiplier, "stepWin": step_win,
            "exploded": exploded, "toWild": to_wild, "refilled": refilled, "boardAfter": format_board(board_after)}


def play_spin(cfg, source, mode, n_scatters, start_multiplier, win_cap):
    """Один спин целиком: все каскады посчитаны заранее, клиент только проигрывает шаги."""
    steps = []
    board = source.first(n_scatters)
    multiplier = start_multiplier
    total = 0
    capped = False
    step_index = 0
    while True:
        wins = evaluate(board, cfg.paytable)
        raw = sum_wins(wins)
        if raw == 0:
            steps.append(_step(board, [], multiplier, 0, [], [], [], board))
            break
        step_index += 1
        step_win = raw * multiplier
        if total + step_win >= win_cap:
            step_win = max(0, win_cap - total)
            capped = True
        total += step_win
        exploded, to_wild = resolve_wins(board, wins)
        if capped:
            steps.append(_step(board, wins, multiplier, step_win, exploded, to_wild, [], board))
            break
        collapsed = collapse(board, exploded, to_wild)
        board_after = source.refill(collapsed, step_index)
        steps.append(_step(board, wins, multiplier, step_win, exploded, to_wild, describe_refill(collapsed, board_after), board_after))
        board = board_after
        multiplier = min(multiplier * 2, cfg.max_multiplier)
    scatters = count_scatters(board)
    return {"mode": mode, "steps": steps, "scatters": scatters, "freeSpinsAwarded": 0 if capped else free_spins_for(cfg, scatters),
            "totalWin": total, "capped": capped}


def sample_scatter_count(rng, table):
    """Число Scatter по таблице, где элемент i = P(i + 1 Scatter); остаток вероятности = без Scatter."""
    x = rng.random()
    for i, p in enumerate(table):
        x -= p
        if x < 0:
            return i + 1
    return 0


def sample_buy_scatter_count(rng, cfg):
    """Число Scatter купленного бонуса (3, 4 или 5); таблица трактуется как веса."""
    total = sum(cfg.buy_scatter)
    if not total > 0:
        return cfg.fs_min_scatters
    x = rng.random() * total
    for i, p in enumerate(cfg.buy_scatter):
        x -= p
        if x < 0:
            return cfg.fs_min_scatters + i
    return cfg.fs_min_scatters + len(cfg.buy_scatter) - 1


def play_round_with(cfg, rng, base_source, fs_source, buy=False):
    """Полный раунд с заданными источниками полей (тесты); rng нужен для розыгрыша числа Scatter."""
    cap = win_cap_units(cfg)
    bought = bool(buy)
    n_scatters = sample_buy_scatter_count(rng, cfg) if bought else sample_scatter_count(rng, cfg.base.scatter)
    base = play_spin(cfg, base_source, "base", n_scatters, cfg.base_start_multiplier, cap)
    total = base["totalWin"]
    capped = base["capped"]
    remaining = base["freeSpinsAwarded"]
    free_spins = []
    left_after = []
    while remaining > 0 and not capped and len(free_spins) < cfg.max_free_spins_per_round:
        fs = play_spin(cfg, fs_source, "fs", sample_scatter_count(rng, cfg.fs.scatter), cfg.fs_start_multiplier, cap - total)
        free_spins.append(fs)
        total += fs["totalWin"]
        capped = fs["capped"]
        remaining = remaining - 1 + fs["freeSpinsAwarded"]
        left_after.append(0 if capped else remaining)
    return {"base": base, "freeSpins": free_spins, "freeSpinsLeftAfter": left_after, "totalWin": total,
            "bonusWin": total - base["totalWin"], "capped": capped, "bought": bought}


def play_round(cfg=CONFIG, rng=None, buy=False):
    """Раунд с боевым генератором. rng: объект с random() (по умолчанию SystemRandom)."""
    if rng is None:
        rng = random.SystemRandom()
    pick = make_picker(cfg.weights)
    return play_round_with(cfg, rng, BoardSource(cfg, cfg.base, rng, pick), BoardSource(cfg, cfg.fs, rng, pick), buy)
