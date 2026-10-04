"""Блэкджек: правила игры. Чистые функции и небольшой автомат состояния, без базы, расчёты целочисленные.

6 колод по 52 карты, перетасовка перед каждой раздачей. Действия: hit, stand, double (без сплита, страховки и сдачи).
Дилер берёт до 17 и останавливается на любых 17 (в том числе мягких). Блэкджек платит 3:2 (округление вниз).
Карта: строка «ранг + масть», например "AS", "10H", "KD" (масти S, H, D, C).

Состояние раздачи (dict): bet, wager, shoe (колода, порядок только на сервере), pos, player, dealer, status
("active" | "finished"), result, payout. Колода никогда не уходит в ответы (view() её не содержит).
"""
import random

DECKS = 6
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
BLACKJACK_MAX_BET = 10 ** 9   # выплата не больше 2,5 * 10**9, баланс остаётся точным числом для JavaScript

ACTIONS = ("hit", "stand", "double")
RESULTS = ("win", "push", "lose", "bust", "dealer_bust", "blackjack")

# Через сколько секунд без действий активная раздача закрывается автоматически (stand)
BLACKJACK_IDLE_SECONDS = 86400


class BlackjackError(Exception):
    """Ошибка действия: code идёт в ответ API (409)."""
    code = "error"


class ActiveGameExists(BlackjackError):
    code = "active_game_exists"


class NoActiveGame(BlackjackError):
    code = "no_active_game"


class InvalidAction(BlackjackError):
    code = "invalid_action"


class RequestConflict(BlackjackError):
    code = "request_conflict"


def card_value(card):
    rank = card[:-1]
    if rank == "A":
        return 11
    return 10 if rank in ("10", "J", "Q", "K") else int(rank)


def hand_value(cards):
    """(очки, мягкая): туз считается за 11 или 1 как выгоднее; мягкая, если хотя бы один туз считается за 11."""
    total = sum(card_value(c) for c in cards)
    soft_aces = sum(1 for c in cards if c[:-1] == "A")
    while total > 21 and soft_aces:
        total -= 10
        soft_aces -= 1
    return total, soft_aces > 0


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def new_shoe(rng=None):
    """Новая перетасованная колода (6 x 52). rng нужен тестам (объект с shuffle), по умолчанию SystemRandom."""
    source = rng if rng is not None else random.SystemRandom()
    shoe = [r + s for _ in range(DECKS) for s in SUITS for r in RANKS]
    source.shuffle(shoe)
    return shoe


def _draw(state):
    card = state["shoe"][state["pos"]]
    state["pos"] += 1
    return card


def _finish(state, result):
    bet, wager = state["bet"], state["wager"]
    if result == "blackjack":
        payout = bet + bet * 3 // 2
    elif result in ("win", "dealer_bust"):
        payout = 2 * wager
    elif result == "push":
        payout = wager
    else:
        payout = 0
    state["status"] = "finished"
    state["result"] = result
    state["payout"] = payout


def start(bet, shoe):
    """Раздача: игрок, дилер (открытая), игрок, дилер (скрытая). Блэкджек решается сразу (дилер смотрит под туз и десятку)."""
    if type(bet) is not int or not 1 <= bet <= BLACKJACK_MAX_BET:
        raise ValueError("bet out of range")
    state = {"bet": bet, "wager": bet, "shoe": shoe, "pos": 0, "player": [], "dealer": [],
             "status": "active", "result": None, "payout": None}
    state["player"].append(_draw(state))
    state["dealer"].append(_draw(state))
    state["player"].append(_draw(state))
    state["dealer"].append(_draw(state))
    player_bj, dealer_bj = is_blackjack(state["player"]), is_blackjack(state["dealer"])
    if player_bj and dealer_bj:
        _finish(state, "push")
    elif player_bj:
        _finish(state, "blackjack")
    elif dealer_bj:
        _finish(state, "lose")
    return state


def can_double(state):
    return state["status"] == "active" and len(state["player"]) == 2


def _dealer_plays_and_settle(state):
    while hand_value(state["dealer"])[0] < 17:   # стоит на любых 17, в том числе мягких
        state["dealer"].append(_draw(state))
    player = hand_value(state["player"])[0]
    dealer = hand_value(state["dealer"])[0]
    if dealer > 21:
        _finish(state, "dealer_bust")
    elif player > dealer:
        _finish(state, "win")
    elif player == dealer:
        _finish(state, "push")
    else:
        _finish(state, "lose")


def act(state, action):
    """Применяет действие к активной раздаче. Двойная ставка (wager) удваивается здесь, списание делает вызывающий код."""
    if state["status"] != "active":
        raise NoActiveGame()
    if action == "hit":
        state["player"].append(_draw(state))
        if hand_value(state["player"])[0] > 21:
            _finish(state, "bust")
    elif action == "stand":
        _dealer_plays_and_settle(state)
    elif action == "double":
        if not can_double(state):
            raise InvalidAction()
        state["wager"] = 2 * state["bet"]
        state["player"].append(_draw(state))
        if hand_value(state["player"])[0] > 21:
            _finish(state, "bust")
        else:
            _dealer_plays_and_settle(state)
    else:
        raise InvalidAction()
    return state


def legal_actions(state, balance):
    """Допустимые действия: double только на первых двух картах и если баланса хватает на вторую ставку."""
    if state["status"] != "active":
        return []
    actions = ["hit", "stand"]
    if can_double(state) and balance >= state["bet"]:
        actions.append("double")
    return actions


def xp_for(wager):
    """Опыт раздачи: wager * 12 // 25 (0,48 от общей ставки: доля раздач с полной потерей ставки)."""
    if type(wager) is not int or wager < 0:
        raise ValueError("wager must be a non-negative integer")
    return wager * 12 // 25


def view(state, balance, level, xp_total, auto=False, replayed=False):
    """Ответ API (одна форма для start, action и state). Колоды в нём нет; пока игра идёт, вторая карта дилера null,
    а очки дилера считаются только по открытой карте."""
    active = state["status"] == "active"
    p_total, p_soft = hand_value(state["player"])
    if active:
        dealer = {"cards": [state["dealer"][0], None], "total": card_value(state["dealer"][0])}
    else:
        dealer = {"cards": list(state["dealer"]), "total": hand_value(state["dealer"])[0]}
    return {
        "status": state["status"],
        "bet": state["bet"],
        "wager": state["wager"],
        "player": {"cards": list(state["player"]), "total": p_total, "soft": p_soft},
        "dealer": dealer,
        "actions": legal_actions(state, balance),
        "result": state["result"],
        "payout": state["payout"],
        "balance": balance,
        "level": level,
        "xp": xp_total,
        "auto": bool(auto),
        "replayed": replayed,
    }


def none_view(balance, level, xp_total):
    """state без игры."""
    return {"status": "none", "bet": None, "wager": None, "player": None, "dealer": None, "actions": [],
            "result": None, "payout": None, "balance": balance, "level": level, "xp": xp_total,
            "auto": False, "replayed": False}
