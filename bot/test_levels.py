import os
import re
from fractions import Fraction

import levels
from roulette import MAX_SAFE_INT

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError("ожидали ValueError для %r" % (args,))


# ================= пороги =================
for level, expected in ((2, 1600), (3, 2560), (5, 6553), (10, 68719)):
    check("порог уровня %d" % level, levels.threshold(level), expected)
check("у уровня 1 порога нет", levels.threshold(1), 0)
# те же числа точной дробной арифметикой (без float)
for level in range(2, levels.MAX_PROFILE_LEVEL + 1):
    exact = (1000 * Fraction(8, 5) ** (level - 1)).__floor__()
    check("порог %d" % level, levels.threshold(level), exact)
    assert type(levels.threshold(level)) is int
check("максимальный уровень", levels.MAX_PROFILE_LEVEL, 60)
assert levels.threshold(60) < MAX_SAFE_INT, "порог последнего уровня должен помещаться в безопасное целое"
for bad in (0, -1, 61, 1.5, "3", None, True):
    raises(levels.threshold, bad)

# монотонность порогов
values = [levels.threshold(level) for level in range(2, 61)]
assert all(a < b for a, b in zip(values, values[1:])), "пороги не растут строго"

# ================= уровень =================
check("0 ставок", levels.profile_level(0), 1)
check("1599", levels.profile_level(1599), 1)
check("1600", levels.profile_level(1600), 2)
check("1601", levels.profile_level(1601), 2)
for level in range(2, 61):
    t = levels.threshold(level)
    check("порог-1 для %d" % level, levels.profile_level(t - 1), level - 1)
    check("порог для %d" % level, levels.profile_level(t), level)
    check("порог+1 для %d" % level, levels.profile_level(t + 1), level)
check("MAX_SAFE_INT", levels.profile_level(MAX_SAFE_INT), 60)
check("выше порога 60 уровень не растёт", levels.profile_level(levels.threshold(60) * 1000), 60)
totals = sorted(set(list(range(0, 5000, 7)) + [10 ** k for k in range(4, 16)]))
seen = [levels.profile_level(n) for n in totals]
assert seen == sorted(seen), "уровень убывает с ростом ставок"
sample = [levels.profile_level(n) for n in sorted([0, 1, 1599, 1600, 2559, 2560, 10 ** 6, 10 ** 12, MAX_SAFE_INT])]
assert sample == sorted(sample), "уровень не убывает с ростом ставок"
for bad in (-1, 1.5, "10", None, True):
    raises(levels.profile_level, bad)

# ================= прогресс =================
check("прогресс на старте", levels.level_progress(0), (1, 0, 1600))
check("прогресс внутри уровня", levels.level_progress(2000), (2, 2000, 2560))
check("прогресс на пороге", levels.level_progress(1600), (2, 1600, 2560))
check("на максимуме порога следующего нет", levels.level_progress(MAX_SAFE_INT), (60, MAX_SAFE_INT, None))
t60 = levels.threshold(60)
check("ровно на пороге максимума", levels.level_progress(t60), (60, t60, None))
check("перед максимумом", levels.level_progress(t60 - 1)[0::2], (59, t60))

# ================= без float =================
src = open(os.path.join(HERE, "levels.py"), encoding="utf-8").read()
no_doc = re.sub(r'""".*?"""', "", src, flags=re.S)
code = "\n".join(line.split("#")[0] for line in no_doc.splitlines())
assert "float" not in code and not re.search(r"\d\.\d", code), "в levels.py есть число с плавающей точкой"
assert not re.search(r"(?<!/)/(?!/)", code), "обычное деление в levels.py"

# ================= страница политики =================
page = open(os.path.join(ROOT, "privacy.html"), encoding="utf-8").read()
assert "<script" not in page.lower(), "на странице скрипт"
assert "http://" not in page and "https://" not in page and "src=" not in page.lower(), "внешние ресурсы"
assert "[КОНТАКТ]" not in page and "[РЕГИОН]" not in page
assert ("видят в рейтинге ваше имя в Telegram, баланс, общую сумму ваших ставок и уровень профиля "
        "(число, считается по накопленному игровому опыту), а также общую сумму ставок всех участников беседы "
        "(суммарное число без разбивки по людям)") in page
sec4 = page[page.index("<h2>4."):page.index("<h2>5.")]
assert "уровень профиля" in sec4
assert "Дата последнего обновления:" in page

print("Все проверки прошли")
