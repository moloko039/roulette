"""Доступность окон: Escape закрывает меню игр, фокус входит в окно при открытии и возвращается на кнопку при закрытии; Escape без окон ничего не ломает;
у диалогов есть aria-modal."""
from harness import check

NAME = "a11y_overlays"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5

MENU_OPEN = "document.getElementById('game-menu').classList.contains('open')"


async def run(w):
    p = w.page
    await p.wait("!!document.querySelector('.tab.main')", 10, "нижняя панель")
    check("диалог (предпросмотр в «Стиле») помечен aria-modal", await p.ev("[...document.querySelectorAll('[role=dialog]')].map(e => e.getAttribute('aria-modal'))"), ["true"])
    await p.key("Escape", "Escape", 27)
    check("Escape без окон: меню закрыто, страница жива", await p.ev(MENU_OPEN), False)

    await p.tap(".tab.main")
    await p.wait(MENU_OPEN, 5, "меню игр открыто")
    await p.wait("document.activeElement.id === 'game-panel'", 5, "фокус в меню игр")
    check("меню игр: aria-expanded у кнопки", await p.ev("document.querySelector('.tab.main').getAttribute('aria-expanded')"), "true")
    await p.key("Escape", "Escape", 27)
    await p.wait("!(" + MENU_OPEN + ")", 5, "Escape закрыл меню")
    check("после закрытия фокус вернулся на кнопку меню", await p.ev("document.activeElement.classList.contains('main')"), True)
    check("aria-expanded сброшен", await p.ev("document.querySelector('.tab.main').getAttribute('aria-expanded')"), "false")
