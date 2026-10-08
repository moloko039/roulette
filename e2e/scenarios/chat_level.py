"""Отображение уровня беседы в рейтинге: прогресс-бар, очки до следующего уровня и скрытие в личном чате."""
import time

from harness import check

NAME = "chat_level"
# Пользователь me будет иметь staked 5000, bob будет иметь 4000, carol будет иметь 2000. Сумма = 11000 (уровень 2)
USERS = {"me": {"rate": 0, "balance": 1000, "staked": 5000}, "bob": {"balance": 5000, "rate": 0, "staked": 4000}, "carol": {"balance": 4000, "rate": 0, "staked": 2000}}

async def run(w):
    p = w.page
    
    # 1. Личный чат (нет беседы) - плашка скрыта
    # Для этого имитируем scope none, перезагрузив с нужным chat_type. Harness по умолчанию запускает с групповым чатом, 
    # поэтому мы просто переключим initData
    await p.ev("window.tg.initDataUnsafe.chat_type = 'private'; window.tg.initData = window.tg.initData.replace('group', 'private'); undefined;")
    await w.reload()
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.getElementById('rating-msg').textContent.includes('Рейтинг работает в беседах')", 15, "сообщение о личной переписке")
    check("в личном чате строка уровня скрыта", await p.ev("document.getElementById('chat-level-wrap').hidden"), True)

    # Вернём обратно group chat
    await p.ev("window.tg.initDataUnsafe.chat_type = 'group'; window.tg.initData = window.tg.initData.replace('private', 'group'); undefined;")
    await w.reload()

    # 2. Групповой чат, уровень 2
    # Общая сумма staked = 5000 + 4000 + 2000 = 11000. Порог для ур. 2 это 10_000, следующий порог 25_000.
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 3", 15, "рейтинг загрузился")
    await p.wait("!document.getElementById('chat-level-wrap').hidden", 5, "строка уровня видна")

    wrap = await p.ev("(() => { const w = document.getElementById('chat-level-wrap'); return { hidden: w.hidden, title: document.getElementById('chat-level-title').textContent, score: document.getElementById('chat-level-score').textContent, ariaValueNow: document.getElementById('chat-level-bar').getAttribute('aria-valuenow'), barWidth: document.getElementById('chat-level-bar-fill').style.width }; })()")

    check("уровень 2: wrap виден", wrap["hidden"], False)
    check("уровень 2: текст уровня", wrap["title"], "Беседа: уровень 2")
    check("уровень 2: текст очков", wrap["score"], "11 000 из 25 000 очков")
    
    # прогресс: 11000 / 25000 = 44%
    check("уровень 2: aria-valuenow", wrap["ariaValueNow"], "44")
    check("уровень 2: ширина полосы", wrap["barWidth"], "44%")

    # 3. Групповой чат, уровень 1
    # Снизим staked участников
    w.sql("UPDATE players SET total_staked = 2000 WHERE telegram_id = ?", (w.users["bob"].id,))
    w.sql("UPDATE players SET total_staked = 1000 WHERE telegram_id = ?", (w.users["carol"].id,))
    # Сумма staked: 5000 + 2000 + 1000 = 8000. Уровень 1.
    await w.reload()
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 3", 15, "рейтинг загрузился")
    await p.wait("!document.getElementById('chat-level-wrap').hidden", 5, "строка уровня видна")

    wrap = await p.ev("(() => { return { title: document.getElementById('chat-level-title').textContent, score: document.getElementById('chat-level-score').textContent, ariaValueNow: document.getElementById('chat-level-bar').getAttribute('aria-valuenow'), barWidth: document.getElementById('chat-level-bar-fill').style.width }; })()")

    check("уровень 1: текст уровня", wrap["title"], "Беседа: уровень 1")
    check("уровень 1: текст очков", wrap["score"], "8 000 из 10 000 очков")
    check("уровень 1: aria-valuenow", wrap["ariaValueNow"], "80")
    check("уровень 1: ширина полосы", wrap["barWidth"], "80%")
