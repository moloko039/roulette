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
    # максимум за перевод 500 000 берётся с сервера: «Макс» = min(баланс, остаток на сегодня, максимум), шесть цифр помещаются на всех ширинах
    await w.reseed({"me": {"balance": 2_000_000, "rate": 0}})
    p = w.page
    await to_bob(p)
    await p.tap("#transfer-max")
    check("«Макс»: 500 000 (максимум за перевод меньше баланса)", await p.ev("document.getElementById('transfer-amount').value"), "500000")
    check("получатель получит 475 000", await p.ev("document.getElementById('transfer-recv').textContent.includes('Получит 475')"), True)
    await p.ev("(() => { const i = document.getElementById('transfer-amount'); i.value = '500001'; i.dispatchEvent(new Event('input')); })()")
    check("500 001 ограничивается 500 000", await p.ev("document.getElementById('transfer-amount').value"), "500000")
    await p.ev("(() => { const i = document.getElementById('transfer-amount'); i.value = '500000'; i.dispatchEvent(new Event('input')); })()")
    for width, height in ((320, 600), (360, 640), (390, 700), (430, 800)):
        await p.viewport(width, height)
        await p.settle(3)
        check("500 000 без горизонтального скролла и обрезки поля %dx%d" % (width, height), await p.ev(
            "[E.hs(), E.overflow(), (() => { const i = document.getElementById('transfer-amount'); return i.scrollWidth <= i.clientWidth; })()]"), [0, [], True])
    await p.viewport(390, 700)
    # владелец: без ограничений
    await w.reseed({"me": {}})
    await w.login("owner")
    p = w.page
    await to_bob(p)
    check("у владельца «без ограничений»", await p.ev("document.getElementById('transfer-limits').textContent.includes('без ограничений')"), True)
