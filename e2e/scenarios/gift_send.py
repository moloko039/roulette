"""Подарок косметикой (настоящий сервер): в листе предпросмотра предмета за кристаллы кнопка «Подарить участнику беседы», список участников беседы по именам, подтверждение вторым нажатием
(без запроса до него), один запрос, у получателя предмет с источником gift, у отправителя списаны кристаллы и фишки целы; у предмета за фишки кнопки подарка нет; повторный подарок того же предмета
тому же участнику отклоняется до списания."""
import time

from harness import check

NAME = "gift_send"
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}
CLOCK_MOD = 1
ALLOW_CONSOLE = (r"status of 409",)
SP = "(s) => s.replace(/\\s/g, ' ')"


async def bob_sel(p):
    """Селектор кнопки получателя bob (метка с сервера; кнопки перерисовываются, поэтому по data-ref)."""
    ref = await p.ev("giftState.recipients.find((r) => r.name === 'bob').ref")
    return "#wd-gift-list .wd-gift-to[data-ref='%s']" % ref


async def run(w):
    p = w.page
    uid, bob = w.users["me"].id, w.users["bob"].id
    now = int(time.time())
    w.sql("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, 400, 'owner_grant', 'e2e-grant-gift', ?)", (uid, now))
    w.sql("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, 400)", (uid,))
    chips_before = w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,))
    await p.tap(".tab[data-tab=shop]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "магазин")
    await p.wait("giftState.available", 10, "получатели загружены")
    # предмет за фишки: подарить нельзя
    await p.tap("#wd-tabs .wd-tab:nth-child(8)")           # значок: «Пика» за фишки
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр значка")
    check("предмет за фишки: кнопки подарка нет", await p.ev("document.getElementById('wd-gift-open').hidden"), True)
    await p.tap("#wd-prev-close")
    # предмет за кристаллы
    await p.tap("#wd-tabs .wd-tab:nth-child(3)")           # стол: «Лагуна» за 150 кристаллов
    await p.tap("#wd-grid .wd-card:nth-child(2)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр стола")
    check("предмет за кристаллы: кнопка подарка видна, список закрыт", await p.ev("[document.getElementById('wd-gift-open').hidden, document.getElementById('wd-gift-box').hidden, (%s)(document.getElementById('wd-gift-open').textContent)]" % SP),
          [False, True, "Подарить участнику беседы"])
    await p.tap("#wd-gift-open")
    check("список участников: bob и владелец беседы (себя нет), цена видна", sorted(await p.ev("[...document.querySelectorAll('#wd-gift-list .wd-gift-to')].map(b => [b.querySelector('strong').textContent, (%s)(b.querySelector('.gem-pack-price').textContent)])" % SP)),
          [["bob", "150 кристаллов"], ["owner", "150 кристаллов"]])
    base = await p.ev("E.count('/api/gifts/send')")
    await p.tap(await bob_sel(p))
    check("первое нажатие: просьба подтвердить, запроса нет", await p.ev("[(%s)(document.getElementById('wd-prev-msg').textContent), E.count('/api/gifts/send')]" % SP),
          ["Подарить «Лагуна» участнику bob за 150 кристаллов? Вернуть нельзя", base])
    await p.tap(await bob_sel(p))
    await p.wait("document.getElementById('wd-prev-msg').textContent.includes('Подарок отправлен')", 10, "подарок отправлен")
    check("один запрос, сообщение", await p.ev("[E.count('/api/gifts/send') - %d, (%s)(document.getElementById('wd-prev-msg').textContent)]" % (base, SP)), [1, "Подарок отправлен: «Лагуна» для bob"])
    check("в базе: у получателя Лагуна с источником gift, у отправителя 250 кристаллов, фишки целы, запись подарка", [
        w.sql_value("SELECT source FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'table_blue'", (bob,)),
        w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)),
        w.sql_value("SELECT balance FROM players WHERE telegram_id = ?", (uid,)) == chips_before,
        w.sql_value("SELECT COUNT(*) FROM gifts WHERE from_user = ? AND to_user = ?", (uid, bob))], ["gift", 250, True, 1])
    check("у отправителя Лагуны нет", w.sql_value("SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'table_blue'", (uid,)), 0)
    check("баланс кристаллов в магазине обновился", await p.ev("document.getElementById('shop-gems').textContent.replace(/\\s/g, '')"), "250")
    # тот же подарок тому же участнику: отказ до списания
    await p.tap("#wd-gift-open")
    await p.tap(await bob_sel(p))
    await p.tap(await bob_sel(p))
    await p.wait("document.getElementById('wd-prev-msg').textContent.includes('уже есть')", 10, "уже есть")
    check("уже есть: понятный текст, кристаллы не списаны", [await p.ev("(%s)(document.getElementById('wd-prev-msg').textContent)" % SP), w.sql_value("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,))],
          ["У этого участника такой предмет уже есть", 250])
