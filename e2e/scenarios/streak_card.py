"""Серия входов (настоящий сервер): на титульном экране карточка «Награда дня» с наградой первого дня, лента из семи делений; нажатие забирает награду один раз (фишки в базе и на экране),
после сбора карточка неактивна и обещает завтрашнюю награду, повторное нажатие запроса не шлёт, после перезагрузки страницы карточка остаётся собранной."""
from harness import check

NAME = "streak_card"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5
SP = "(s) => s.replace(/\\s/g, ' ')"


async def run(w):
    p = w.page
    uid = w.users["me"].id
    await p.wait("!document.getElementById('streak-card').hidden", 10, "карточка награды дня")
    check("до сбора: заголовок, награда дня 1, семь делений, первое текущее", await p.ev(
        "[(%s)(document.getElementById('streak-title').textContent), (%s)(document.getElementById('streak-sub').textContent), document.querySelectorAll('#streak-week .streak-dot').length, "
        "document.querySelector('#streak-week .streak-dot').classList.contains('now'), document.getElementById('streak-card').disabled]" % (SP, SP)),
        ["Награда дня", "День 1 из 7: +300 фишек", 7, True, False])
    chips_before = w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,))
    base = await p.ev("E.count('/api/streak/claim')")
    await p.tap("#streak-card")
    await p.wait("document.getElementById('streak-msg').textContent.includes('Награда получена')", 10, "сбор")
    check("сообщение и один запрос", await p.ev("[(%s)(document.getElementById('streak-msg').textContent), E.count('/api/streak/claim') - %d]" % (SP, base)), ["Награда получена: +300 фишек", 1])
    check("в базе: баланс +300, одна запись сбора, день 1 цикл 1", [
        w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)) - chips_before,
        w.sql_value("SELECT COUNT(*) FROM streak_claims WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT streak_day || ':' || cycle || ':' || chips FROM streak_claims WHERE telegram_id = ?", (uid,))], [300, 1, "1:1:300"])
    await p.wait("document.getElementById('streak-card').disabled", 10, "карточка неактивна")
    check("после сбора: заголовок, завтрашняя награда, первое деление закрашено", await p.ev(
        "[(%s)(document.getElementById('streak-title').textContent), (%s)(document.getElementById('streak-sub').textContent), document.querySelector('#streak-week .streak-dot').classList.contains('done')]" % (SP, SP)),
        ["Награда получена", "Завтра: +400 фишек", True])
    check("баланс на экране вырос на 300", await p.ev("srv.balance - %d" % chips_before), 300)
    await p.ev("document.getElementById('streak-card').click()")
    check("повторное нажатие запроса не шлёт", await p.ev("E.count('/api/streak/claim') - %d" % base), 1)
    await w.reload()
    await p.wait("!document.getElementById('streak-card').hidden && document.getElementById('streak-card').disabled", 10, "после перезагрузки карточка собрана")
    check("после перезагрузки награда не выдаётся второй раз", w.sql_value("SELECT COUNT(*) FROM streak_claims WHERE telegram_id = ?", (uid,)), 1)
