"""Окно перевода: тексты ошибок сервера (новый аккаунт, мало ставок, низкий уровень, лимит получения), владелец без ограничений."""
from harness import check

NAME = "transfers_ui"
ALLOW_CONSOLE = (r"status of 409",)    # отказы сервера (409) здесь ожидаемы: проверяются тексты ошибок


async def to_bob(p):
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 2", 10, "рейтинг")
    await p.ev("[...document.querySelectorAll('#rating-list li')].find(l => l.textContent.includes('bob')).id = 'e2e-bob'")
    await p.tap("#e2e-bob")
    await p.wait("!document.getElementById('transfer-sheet').hidden", 5, "окно перевода")


async def send(p, amount):
    await p.ev("(() => { const i = document.getElementById('transfer-amount'); i.value = '%d'; i.dispatchEvent(new Event('input')); })()" % amount)
    await p.tap("#transfer-send")
    await p.wait("document.getElementById('transfer-msg').textContent.includes('Отправить')", 5, "подтверждение")
    await p.tap("#transfer-send")
    await p.wait("E.count('/api/transfers/send') >= 1 && document.getElementById('transfer-send').textContent !== 'Отправляем…'", 10, "ответ сервера")


async def expect_error(w, users, text):
    await w.reseed(users)
    p = w.page
    await to_bob(p)
    await send(p, 500)
    check("текст ошибки", await p.ev("document.getElementById('transfer-msg').textContent"), text)
    check("окно осталось открытым", await p.ev("!document.getElementById('transfer-sheet').hidden"), True)


async def run(w):
    await expect_error(w, {"me": {"age_days": 0}}, "Аккаунт слишком новый: переводы откроются через 1 ч после начала игры")
    await expect_error(w, {"me": {"staked": 0}}, "Переводы откроются, когда вы поставите в играх не менее 20 000 фишек")
    await expect_error(w, {"me": {"xp": 0}}, "Нужен уровень 3 или выше")
    await expect_error(w, {"me": {}, "bob": {"balance": 5000, "received": 499_950}}, "Этот игрок уже получил максимум за сутки, попробуйте позже")
    # владелец: без ограничений
    await w.reseed({"me": {}})
    await w.login("owner")
    p = w.page
    await to_bob(p)
    check("у владельца «без ограничений»", await p.ev("document.getElementById('transfer-limits').textContent.includes('без ограничений')"), True)
