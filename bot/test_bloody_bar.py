"""Bloody slot: Ritual bar. Пороги, очки за средние и высокие символы, состав таблицы падения по уровням (переходы в раунде: test_bloody_round.py)."""
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
check("старт: уровень 0", (st.level, st.progress, st.final), (0, 0, False))
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


check("уровень 0: только низкие и средние, высоких нет", sorted(pool_syms(0)), sorted(cfg.LOW + cfg.MID))
p1 = pool_syms(1)
check("уровень 1: нет L5 и M5, вес M5 перешёл H5 (ворон улучшен первым)", ("L5" in p1, "M5" in p1, p1["H5"], [h for h in p1 if h in cfg.HIGH]), (False, False, cfg.SYMBOL_WEIGHTS["M5"], ["H5"]))
p3 = pool_syms(3)
check("уровень 3: нет L5 L4 L3 и M5 M4 M3, открыты H5 H4 H3", ([s for s in ("L5", "L4", "L3", "M5", "M4", "M3") if s in p3], sorted(h for h in p3 if h in cfg.HIGH)), ([], ["H3", "H4", "H5"]))
p5 = pool_syms(5)
check("уровень 5: остались только высокие", sorted(p5), sorted(cfg.HIGH))
check("общий вес не потерян при превращении средних", sum(pool_syms(5).values()) + sum(cfg.SYMBOL_WEIGHTS[s] for s in cfg.LOW), sum(cfg.SYMBOL_WEIGHTS.values()))
check("Wild и Scatter остаются в таблице", {s for s, _ in eng.pool_for_level(5)} - set(cfg.PAY_SYMBOLS), {"W", "S"})

# предел: после последнего порога переходов больше нет (сами переходы проверяет test_bloody_round.py: они делаются в раунде)
st = eng.BarState(5, 1000)
check("на последнем уровне переходов нет", (st.final, eng.level_ready(st)), (True, False))
# ускорение xBet 1 даёт дробные очки: игроку показываются целые, внутри точное значение
st = eng.BarState(1, 15.918000000000001)
check("очки для игрока целые", st.to_dict()["progress"], 15)
check("внутреннее значение не округлено", st.progress, 15.918000000000001)
print("test_bloody_bar OK")
