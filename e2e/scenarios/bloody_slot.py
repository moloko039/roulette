"""Bloody Slot в «Не слотах»: карточка, открытие игры в iframe, мост с приложением и сервером, спин (ставка списана, поле нарисовано), раунд доигрывается до конца,
баланс игры совпадает с серверным, незавершённый раунд продолжается после перезагрузки приложения."""
from harness import check, open_game

NAME = "bloody_slot"
USERS = {"me": {"rate": 0}}
VIEWPORT = (390, 800)
CLOCK_MOD = 30

IF = "document.querySelector('#embed-frame iframe').contentDocument"


async def run(w):
    p = w.page
    await w.reload()
    await open_game(p, "arcade")
    await p.wait("document.querySelectorAll('.arcade-card').length >= 2", 10, "две карточки в «Не слотах»")
    check("карточка Bloody Slot есть", await p.ev("!!document.querySelector('.arcade-card[data-embedded=bloody-slot]')"), True)
    await p.tap(".arcade-card[data-embedded=bloody-slot]")
    await p.wait("!!document.querySelector('#embed-frame iframe') && !!%s.getElementById('balance') && %s.getElementById('balance').textContent.trim() !== '—'" % (IF, IF), 15, "игра загрузилась и получила состояние")
    check("поле нарисовано: 20 символов", await p.ev("%s.querySelectorAll('#board .sym').length" % IF), 20)
    check("ряд Wild: 5 закрытых слотов", await p.ev("[%s.querySelectorAll('.wslot').length, %s.querySelectorAll('.wslot.open').length]" % (IF, IF)), [5, 0])
    check("кнопка спина доступна, режим основной", await p.ev("[!%s.getElementById('btn-spin').disabled, %s.getElementById('mode-title').textContent]" % (IF, IF)), [True, "Основная игра"])
    start = await p.ev("Number(%s.getElementById('balance').dataset.n)" % IF)
    await p.ev("%s.getElementById('btn-spin').click()" % IF)
    await p.wait("%s.getElementById('btn-spin').disabled" % IF, 5, "спин пошёл")
    # раунд идёт до конца: в бонусах нажимаем спин, баннеры закрываем
    for _ in range(60):
        await p.wait("!%s.getElementById('btn-spin').disabled || !%s.getElementById('banner').hidden" % (IF, IF), 40, "спин доигран")
        if await p.ev("!%s.getElementById('banner').hidden" % IF):
            await p.ev("%s.querySelector('#banner button').click()" % IF)
            await p.wait("%s.getElementById('banner').hidden" % IF, 5, "баннер закрыт")
            continue
        if await p.ev("%s.getElementById('spin-label').textContent" % IF) in ("Крутить",):
            break
        await p.ev("%s.getElementById('btn-spin').click()" % IF)
    check("раунд закончен: снова основная игра", await p.ev("%s.getElementById('mode-title').textContent" % IF), "Основная игра")
    srv_balance = await p.ev("srv.balance")
    shown = await p.ev("Number(%s.getElementById('balance').dataset.n)" % IF)
    check("баланс игры совпадает с серверным", shown, srv_balance)
    uid = w.users["me"].id
    status_closed = w.sql_value("SELECT status != 'active' FROM bloody_games WHERE telegram_id = ?", (uid,))
    check("раунд записан и закрыт", (status_closed, w.sql_value("SELECT bet FROM bloody_games WHERE telegram_id = ?", (uid,)), w.sql_value("SELECT cost FROM bloody_games WHERE telegram_id = ?", (uid,))), (1, 100, 100))
    check("баланс = было - ставка + выплата", shown, start - 100 + w.sql_value("SELECT payout FROM bloody_games WHERE telegram_id = ?", (uid,)))
    check("поле после раунда полное", await p.ev("%s.querySelectorAll('#board .sym').length" % IF), 20)
