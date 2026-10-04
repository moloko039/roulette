"""Хило: старт, ничья выигрывает, пропуск, кэшаут; запрет хода на тузе (k=13); потолок ×1000."""
from harness import check, open_game, set_bet, shown

NAME = "hilo"
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках


async def action(p, button, expect_posts, finished=False):
    """Нажать кнопку и дождаться ответа сервера и конца анимации."""
    await p.tap(button)
    await p.wait("E.count('/api/hilo/') >= %d" % expect_posts, 10, "запрос ушёл")
    cond = "!document.getElementById('hl-banner').hidden && document.getElementById('hl-banner-title').textContent.length > 0" if finished \
        else "!document.getElementById('hl-skip').disabled && !document.getElementById('hl-actions').hidden"
    await p.wait(cond, 15, "анимация закончилась")


async def start(p, bet=100):
    await set_bet(p, "hl-bet", bet)
    await p.tap("#hl-start")
    await p.wait("!document.getElementById('hl-actions').hidden && !document.getElementById('hl-skip').disabled", 10, "партия началась")


async def run(w):
    p = w.page
    n = [0]
    w.server.script(hilo=[[7, "H"], [7, "S"], [3, "C"], [1, "S"], [1, "H"], [1, "D"], [1, "C"]])
    await open_game(p, "hilo")
    await p.wait("!document.getElementById('hl-bets').hidden", 10, "панель ставки")
    await start(p)
    n[0] = 1
    check("первая карта и ходы", await p.ev("[document.getElementById('hl-card').getAttribute('aria-label'), document.getElementById('hl-hi-sub').textContent,"
                                            "document.getElementById('hl-lo-sub').textContent, document.getElementById('hl-cash').disabled]"),
          ["7 червей", "×1.80 · 53.8%", "×1.80 · 53.8%", True])
    check("баланс после ставки (после анимации)", await shown(p, "#hl-balance"), 99900)
    await action(p, "#hl-hi", 2)       # ничья 7 и 7: равенство выигрывает
    check("ничья выиграла: 1 ход, множитель ×1.80, забрать доступно", await p.ev(
        "[document.getElementById('hl-steps').textContent, document.getElementById('hl-mult').textContent, document.getElementById('hl-cash').disabled]"),
        ["1", "×1.80", False])
    await action(p, "#hl-skip", 3)
    check("пропуск: множитель и ходы те же, карта новая", await p.ev(
        "[document.getElementById('hl-steps').textContent, document.getElementById('hl-mult').textContent, document.getElementById('hl-card').getAttribute('aria-label')]"),
        ["1", "×1.80", "3 треф"])
    await action(p, "#hl-cash", 4, finished=True)
    check("забрали 180 со ставки 100", await shown(p, "#hl-balance"), 100080)

    # запрет хода на тузе и потолок
    await start(p)
    check("на тузе «выше» недоступно, «ниже» доступно", await p.ev(
        "[document.getElementById('hl-hi').disabled, document.getElementById('hl-lo').disabled, document.getElementById('hl-hi-sub').textContent.includes('смысла нет')]"),
        [True, False, True])
    base = await p.ev("E.count('/api/hilo/')")
    for i, card in enumerate(("1H", "1D", "1C")):
        await action(p, "#hl-lo", base + i + 1, finished=(i == 2))
    check("потолок ×1000: партия закрыта", await p.ev("document.getElementById('hl-banner-title').textContent.includes('Потолок')"), True)
    check("выплата 1000 ставок", await shown(p, "#hl-balance"), 100080 - 100 + 100000)
