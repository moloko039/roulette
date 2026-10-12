"""Забег свинок: режимы анимации (UI_REVIEW, раздел 3.6). После забега не остаётся запущенных анимаций (fill: forwards не перекрывает
конечное положение), тап по дорожке во время бега сразу показывает финиш, в облегчённом режиме (perf-lite) нет параллакса, пыли и ворот,
свинки идут по прямой и приходят к финишу, до сигнала «Марш» победитель не подсвечен. Исход случайный, сценарий его не подменяет."""
from harness import check, open_game, set_bet

NAME = "race_modes"
USERS = {"me": {"rate": 0}}

PIG_ANIMS = "document.getAnimations().filter((a) => a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('.race-lane') && (a.effect.target.classList.contains('race-pig') || a.effect.target.classList.contains('race-scroll'))).length"


async def play(p, lane, wait_s=60):
    await p.tap(".race-lane[data-lane='%d']" % lane)
    await p.tap("#race-play")


async def run(w):
    p = w.page
    await w.reload()
    await open_game(p, "race")
    await p.wait("document.querySelectorAll('.race-lane').length === 3", 10, "три дорожки")
    await set_bet(p, "race-bet", 10)

    # обычный забег: победитель не подсвечен до «Марш», после финиша анимаций нет, свинки у финиша
    await play(p, 1)
    await p.wait("document.getElementById('race-field').classList.contains('running')", 20, "забег идёт")
    check("во время бега победитель не подсвечен", await p.ev("document.querySelectorAll('.race-lane.win, .race-pig.winner').length"), 0)
    check("идут анимации свинок и камеры", await p.ev(PIG_ANIMS) >= 6, True)
    await p.tap(".race-lane[data-lane='0']")                      # тап по дорожке: показать финиш
    await p.wait("document.getElementById('race-result').hidden === false", 10, "итог после пропуска")
    check("после финиша анимаций свинок нет", await p.ev(PIG_ANIMS), 0)
    check("подсвечена одна победившая дорожка", await p.ev("document.querySelectorAll('.race-lane.win').length"), 1)
    check("кнопка «Ещё раз»", await p.ev("document.getElementById('race-play').textContent.trim()"), "Ещё раз · 10")
    pos = await p.ev("Array.from(document.querySelectorAll('.race-pig')).map((e) => e.getBoundingClientRect().right > document.querySelector('.race-lane').getBoundingClientRect().left + document.querySelector('.race-lane').clientWidth * 0.8)")
    check("все свинки в финишной части дорожки", pos, [True, True, True])

    # облегчённый режим: прямая линия, без ворот и параллакса
    await p.ev("document.documentElement.classList.add('perf-lite')")
    await p.tap("#race-play")
    await p.wait("document.getElementById('race-field').classList.contains('running')", 20, "забег (perf-lite)")
    check("perf-lite: ворота скрыты", await p.ev("getComputedStyle(document.querySelector('.race-gate')).display"), "none")
    check("perf-lite: камера не двигается", await p.ev("Array.from(document.querySelectorAll('.race-scroll')).every((e) => e.getAnimations().length === 0)"), True)
    await p.wait("document.getElementById('race-result').hidden === false", 40, "итог (perf-lite)")
    check("perf-lite: анимаций нет", await p.ev(PIG_ANIMS), 0)
    check("perf-lite: одна победившая дорожка", await p.ev("document.querySelectorAll('.race-lane.win').length"), 1)
    await p.ev("document.documentElement.classList.remove('perf-lite')")
