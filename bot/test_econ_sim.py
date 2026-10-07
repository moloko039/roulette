"""Симулятор экономики (tools/econ_sim.py): гарантии плана выполняются на текущих числах, а нарушение ловится (мутация: нулевое хранилище и очень дорогая ферма)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import econ_sim  # noqa: E402
import farm  # noqa: E402


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


rows = econ_sim.report()
check("гарантии плана выполняются на текущих числах", econ_sim.check(rows), [])
check("донатёр не отстаёт, а больше заходов не медленнее", (rows["donor"]["income_level"] >= rows["regular"]["income_level"], rows["active"]["income_level"] >= rows["casual"]["income_level"]), (True, True))
check("игрок без вложений реально растёт: фишек к 60-му дню больше стартовых", all(h["balance"] > econ_sim.START_BALANCE for h in rows.values()), True)

# мутация: ферма в разы дороже: проверка обязана упасть
with mock.patch.object(farm, "INCOME_COST_NUM", 4), mock.patch.object(farm, "INCOME_COST_DEN", 1):
    harsh = econ_sim.check(econ_sim.report())
check("мутация (цена x4 за уровень): гарантии нарушены и это видно", len(harsh) > 0, True)
# мутация: потолок накопления 1 час: casual почти не растёт
with mock.patch.object(farm, "STORAGE_BASE_HOURS", 1), mock.patch.object(farm, "STORAGE_HOURS_STEP", 0):
    stingy = econ_sim.check(econ_sim.report())
check("мутация (потолок накопления 1 час): гарантии нарушены", len(stingy) > 0, True)
# симулятор детерминирован
check("повторный прогон даёт то же", econ_sim.simulate(3)["balance"], econ_sim.simulate(3)["balance"])
print("Все проверки прошли")
