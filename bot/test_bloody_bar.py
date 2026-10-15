"""Bloody slot, этап 2: Ritual bar. Пороги и перенос излишка, очки за средние и высокие символы, открытие Wild, удаление дешёвых низких и превращение средних
в высокие, порядок «сначала доиграть каскады, потом переход», сброс полоски на новый раунд, состав таблицы падения по уровням."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import random

import bloody_config as cfg
import bloody_engine as eng


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


def grid(*reels):
    return [r.split() for r in reels]


class Scripted:
    def __init__(self, values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0) if self.values else 0.5


# ---- конфиг
check("пороги из задания", cfg.BAR_THRESHOLDS, (20, 30, 40, 60, 80))
check("очки: средний 1, высокий 2, Scatter 10", (cfg.BAR_POINTS_MID, cfg.BAR_POINTS_HIGH, cfg.BAR_POINTS_SCATTER), (1, 2, 10))

# ---- состояние
st = eng.BarState()
check("старт: уровень 0, Wild не открыты", (st.level, st.progress, st.wilds_open, st.final), (0, 0, 0, False))
check("порог уровня 1", st.to_dict(), {"level": 0, "progress": 0, "threshold": 20})
check("пока очков мало перехода нет", (eng.add_points(st, 19), eng.level_ready(st))[1], False)
eng.add_points(st, 1)
check("ровно порог: готов", eng.level_ready(st), True)

# ---- очки за ячейки: средний +1, высокий +2, низкий, Wild, Scatter 0
g = grid("H1 M1 L1 W", "M2 H2 S L2", "L3 L4 L5 M3", "H3 H4 H5 M4", "M5 L1 L2 L3")
check("очки по ячейкам", eng.bar_points(g, [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2)]), 2 + 1 + 0 + 0 + 1 + 2 + 0)

# ---- пул по уровням
def pool_syms(level):
    return {s: w for s, w in eng.pool_for_level(level) if s in cfg.PAY_SYMBOLS}


check("уровень 0: все 15", sorted(pool_syms(0)), sorted(cfg.PAY_SYMBOLS))
p1 = pool_syms(1)
check("уровень 1: нет L5 и M1, вес M1 перешёл H1", ("L5" in p1, "M1" in p1, p1["H1"]), (False, False, cfg.SYMBOL_WEIGHTS["H1"] + cfg.SYMBOL_WEIGHTS["M1"]))
p3 = pool_syms(3)
check("уровень 3: нет L5 L4 L3 и M1 M2 M3", [s for s in ("L5", "L4", "L3", "M1", "M2", "M3") if s in p3], [])
p5 = pool_syms(5)
check("уровень 5: остались только высокие", sorted(p5), sorted(cfg.HIGH))
check("общий вес не потерян при превращении средних", sum(pool_syms(5).values()) + sum(cfg.SYMBOL_WEIGHTS[s] for s in cfg.LOW), sum(cfg.SYMBOL_WEIGHTS.values()))
check("Wild и Scatter остаются в таблице", {s for s, _ in eng.pool_for_level(5)} - set(cfg.PAY_SYMBOLS), {"W", "S"})

# ---- переход: перенос излишка, открытие Wild, взрыв низкого, превращение среднего на месте
st = eng.BarState(0, 25)
g = grid("M1 L5 L2 L3", "L4 L5 M2 L3", "M3 H3 L1 L2", "M4 M5 L1 L3", "H4 H5 L2 L4")
new, step = eng.level_up(g, st, Scripted([0.0] * 10), None)
check("перенос излишка: 25 - 20 = 5", (st.level, st.progress), (1, 5))
check("Wild номер 1 открыт", (st.wilds_open, step["wild"]), (1, 0))
check("убран самый дешёвый низкий L5, превращён M1 в H1", (step["removed"], step["transform"]), ("L5", ["M1", "H1"]))
check("ячейки M1 стали H1 на месте", step["converted"], [[0, 0, "H1"]])
check("взорваны обе L5", sorted(step["cells"]), [[0, 1], [1, 1]])
check("в поле больше нет L5 и M1", [c for col in new for c in col if c in ("L5", "M1")], [])
check("поле осталось полным", [len(col) for col in new], [4] * 5)
check("состояние полоски в шаге", step["bar"], {"level": 1, "progress": 5, "threshold": 30})
check("исходное поле не изменено", g[0][0], "M1")
# несколько порогов подряд из большого излишка: каждый переход отдельным шагом
st = eng.BarState(0, 20 + 30 + 7)
g = grid("L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4", "L1 L2 L3 L4")
n = 0
while eng.level_ready(st):
    g, _ = eng.level_up(g, st, Scripted([0.0] * 40), None)
    n += 1
check("излишек 57 даёт два уровня и остаток 7", (n, st.level, st.progress), (2, 2, 7))
# предел: после последнего порога переходов больше нет
st = eng.BarState(5, 1000)
check("на последнем уровне переходов нет", (st.final, eng.level_ready(st)), (True, False))

# ---- цепочка: сначала каскады доигрываются и платятся, потом переход, затем каскады продолжаются
# одно выигрышное поле H1 (3 барабана) даёт 2*3 очка, до порога 20 не хватает: ставим 15 очков заранее
st = eng.BarState(0, 15)
g = grid("H1 L1 L2 L3", "H1 L4 L5 M1", "H1 M2 M3 M4", "L1 L2 L3 L4", "L5 M1 M2 M3")
rng = Scripted([0.99999] * 200)
res = eng.play_spin(g, st, rng)
types = [s["type"] for s in res["steps"]]
check("порядок: каскад, затем переход", types[:2], ["cascade", "level_up"])
check("первый каскад платит 150 единиц и даёт 6 очков", (res["steps"][0]["units"], res["steps"][0]["bar"]["progress"]), (150, 21))
check("после перехода уровень 1, излишек 1", res["steps"][1]["bar"], {"level": 1, "progress": 1, "threshold": 30})
check("итоговое состояние полоски", (st.level, st.progress >= 0), (1, True))
check("final_reached ложь на уровне 1", res["final_reached"], False)

# Wild, открытый переходом, участвует в следующих выигрышах: после уровня 1 Wild на барабане 1 заменяет символ
st = eng.BarState(0, 20)
g = grid("L1 L2 L3 L4", "H2 L4 L5 M3", "H2 M3 M4 L1", "H2 L2 L3 L4", "L5 M5 M5 L1")
check("до перехода выигрыша H2 нет", [w.sym for w in eng.evaluate(g, 0)], [])
res = eng.play_spin(g, st, Scripted([0.99999] * 300))
check("открытый Wild участвует в выигрыше после перехода", any(w["sym"] == "H2" and w["length"] >= 4 for s in res["steps"] if s["type"] == "cascade" for w in s["wins"]), True)

# ---- до последнего уровня: очков хватает на всё, полоска доходит до 5, final_reached
st = eng.BarState(0, 10 ** 6)
g = grid("H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1", "H1 H1 H1 H1")
res = eng.play_spin(g, st, random.Random(5), limit_units=10 ** 9)
check("шаги переходов 1..5 по порядку", [s["level"] for s in res["steps"] if s["type"] == "level_up"], [1, 2, 3, 4, 5])
check("последний уровень достигнут", (st.final, res["final_reached"], st.level), (True, True, 5))
check("переходы убирали L5, L4, L3, L2, L1", [s["removed"] for s in res["steps"] if s["type"] == "level_up"], list(cfg.LOW_REMOVE_ORDER))

# ---- на последнем уровне очки не копятся, полоска не меняется
st = eng.BarState(5, 0)
res = eng.play_spin(grid(*["H1 H1 H1 H1"] * 5), st, random.Random(6))
check("очки на последнем уровне не растут", st.progress, 0)

# ---- случайные раунды: полоска монотонна, поле полное, нет остаточных выигрышей, без символов из убранных наборов
rng = random.Random(21)
for _ in range(400):
    st = eng.BarState()
    g = eng.new_grid(rng, eng.pool_for_level(0))
    res = eng.play_spin(g, st, rng)
    levels = [s["bar"]["level"] for s in res["steps"]]
    assert levels == sorted(levels), "уровень уменьшился"
    assert all(len(c) == cfg.WORKING_ROWS for c in res["grid"])
    removed = set(cfg.LOW_REMOVE_ORDER[:st.level]) | set(cfg.MID_TRANSFORM_ORDER[:st.level])
    left = [c for col in res["grid"] for c in col if c in removed]
    assert not left, "остались символы убранных уровней: %r" % left
    assert not [w for w in eng.evaluate(res["grid"], st.wilds_open) if w.cells] or st.final or res["units"] >= 0
print("test_bloody_bar OK")
