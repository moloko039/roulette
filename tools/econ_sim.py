"""Симулятор экономики на 60 дней (план docs/ECONOMY.md, этап E8): профили игроков проходят по настоящим формулам фермы (bot/farm.py) и числам bot/economy_config.py.

Модель (простая и сознательно грубая, нужна для сравнения конфигураций, а не для прогноза денег): игрок заходит visits раз в сутки равными промежутками; при заходе подбирает накопленный доход
(не больше потолка хранилища в часах), покупает улучшения, пока хватает фишек и позволяет уровень профиля, и играет: ставит долю баланса, теряя в среднем 1/37 ставки (преимущество рулетки);
опыт за ставки идёт по правилу «ставка * вероятность проигрыша» (около половины ставки). Профиль «донатёр» дополнительно покупает пакет фишек за кристаллы каждый день.

Запуск: `python tools/econ_sim.py` печатает таблицу, `python tools/econ_sim.py --check` проверяет гарантии из плана (раздел 8) и выходит с кодом 1 при нарушении.
Проверки: никто не застревает (все доходят до заданных уровней фермы за 60 дней), больше заходов не медленнее, баланс на заходе не бывает нулевым после первого дня."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bot"))

import economy_config  # noqa: E402
import farm  # noqa: E402
import levels  # noqa: E402

DAYS = 60
START_BALANCE = 1000
BET_FRACTION = 0.1          # доля баланса, которую игрок ставит за заход
XP_PER_STAKE = 0.5          # опыт за ставку (грубо: вероятность проиграть её)
HOUSE_EDGE = 1 / 37
PROFILES = {
    "casual": {"visits": 1},
    "regular": {"visits": 3},
    "active": {"visits": 6},
    "donor": {"visits": 3, "gems_chip_pack": "chips_24h"},     # покупает пакет фишек за кристаллы каждый день
}
# гарантии (раздел 8 плана), числа подобраны по текущей конфигурации с запасом: ломаются, когда экономика становится заметно жёстче
GUARANTEES = {"casual": 8, "regular": 14, "active": 14, "donor": 16}      # уровень дохода фермы, который профиль обязан достичь за DAYS дней


def simulate(visits, gems_chip_pack=None, days=DAYS):
    income_level, storage_level = 0, 0
    balance, xp = float(START_BALANCE), 0.0
    step_h = 24.0 / visits
    history = {"income_reached": {}, "balance_day": {}, "min_balance_after_day1": None, "farm_earned": 0.0, "spent_on_farm": 0.0}
    t = 0.0
    last = 0.0
    total_visits = int(days * visits)
    for v in range(1, total_visits + 1):
        t = v * step_h
        day = int(t // 24) + 1
        rate = farm.income_rate(income_level)
        hours = min(t - last, farm.storage_hours(storage_level))
        earned = hours * rate
        balance += earned
        history["farm_earned"] += earned
        last = t
        if gems_chip_pack and (v % visits == 1 or visits == 1):
            _gems, pack_hours = economy_config.CHIP_PACKS[gems_chip_pack]
            balance += min(pack_hours * max(rate, economy_config.CHIP_PACK_MIN_RATE), economy_config.CHIP_PACK_MAX_CHIPS)
        # улучшения: доход, пока можно; хранилище, если дохода купить нельзя
        progressed = True
        while progressed:
            progressed = False
            level_cap = levels.profile_level(int(xp))
            if income_level + storage_level >= level_cap:
                break
            cost = farm.income_cost(income_level)
            if cost is not None and balance >= cost:
                balance -= cost
                history["spent_on_farm"] += cost
                income_level += 1
                progressed = True
                history["income_reached"].setdefault(income_level, day)
                continue
            scost = farm.storage_cost(storage_level)
            if scost is not None and balance >= scost and storage_level < income_level // 2 + 1:
                balance -= scost
                history["spent_on_farm"] += scost
                storage_level += 1
                progressed = True
        stake = balance * BET_FRACTION
        balance -= stake * HOUSE_EDGE
        xp += stake * XP_PER_STAKE
        if day > 1:
            m = history["min_balance_after_day1"]
            history["min_balance_after_day1"] = balance if m is None else min(m, balance)
        history["balance_day"][day] = balance
    history["income_level"], history["storage_level"], history["balance"] = income_level, storage_level, balance
    return history


def report():
    rows = {}
    for name, cfg in PROFILES.items():
        rows[name] = simulate(cfg["visits"], cfg.get("gems_chip_pack"))
    return rows


def print_report(rows):
    print("Конфигурация экономики v%d, %d дней" % (economy_config.VERSION, DAYS))
    print("%-8s %6s %7s %9s %9s %9s %12s" % ("профиль", "заходы", "доход", "хранил.", "день ур.5", "день ур.10", "фишек на 60-й"))
    for name, h in rows.items():
        print("%-8s %6d %7d %9d %9s %9s %12s" % (name, PROFILES[name]["visits"], h["income_level"], h["storage_level"],
                                                  h["income_reached"].get(5, "-"), h["income_reached"].get(10, "-"), "{:,}".format(int(h["balance"])).replace(",", " ")))


def check(rows):
    bad = []
    for name, need in GUARANTEES.items():
        got = rows[name]["income_level"]
        if got < need:
            bad.append("%s: за %d дней уровень дохода %d, нужно не меньше %d" % (name, DAYS, got, need))
    order = ("casual", "regular", "active")
    for a, b in zip(order, order[1:]):
        if rows[b]["income_level"] < rows[a]["income_level"] - 1:        # допуск в один уровень: модель грубая
            bad.append("больше заходов не должно быть заметно медленнее: %s %d, %s %d" % (a, rows[a]["income_level"], b, rows[b]["income_level"]))
    for name, h in rows.items():
        if h["min_balance_after_day1"] is not None and h["min_balance_after_day1"] <= 0:
            bad.append("%s: баланс на заходе дошёл до нуля" % name)
    if rows["donor"]["income_level"] < rows["regular"]["income_level"]:
        bad.append("донатёр не должен отставать от такого же игрока без вложений")
    return bad


if __name__ == "__main__":
    result = report()
    print_report(result)
    if "--check" in sys.argv:
        problems = check(result)
        for line in problems:
            print("НАРУШЕНИЕ:", line)
        sys.exit(1 if problems else 0)
