"""Блэкджек: на 21 (твёрдом и мягком) рука завершается сама как «Стоп»: дилер доигрывает без нажатий, «Ещё» и «Удвоить» при 21 не показываются.
Натуральный блэкджек и удвоение работают как раньше; раздача, начатая до правила (рука 21 осталась активной), показывает только «Стоп».
Заодно: подпись мягкой руки «Мягкая N (туз = 11)» не меняет высоту строки заголовка руки."""
from harness import check, open_game, set_bet, shown

NAME = "blackjack_auto_stand"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}     # без дохода: минутное начисление не меняет точные суммы в проверках

PANEL = "!document.getElementById('bj-actions').hidden && !document.getElementById('bj-stand').disabled"
BUTTONS = "['bj-hit', 'bj-stand', 'bj-double'].map((id) => !document.getElementById(id).hidden)"
HEAD = "Math.round(document.querySelector('#bj-player-total').parentElement.getBoundingClientRect().height * 100) / 100"


async def deal(w, deck, bet=100):
    p = w.page
    await p.ev("E.sleep(5300)")      # между запросами /api/me не меньше 5 секунд: порядок запросов не зависит от скорости прогона
    w.server.script(shoe=[deck])
    await set_bet(p, "bj-bet", bet)
    await p.tap("#bj-deal")


async def run(w):
    p = w.page
    await open_game(p, "blackjack")
    done = "!document.getElementById('bj-banner').hidden && !document.getElementById('bj-deal').disabled"

    # 1. твёрдый 21 после hit: 5 + 6 = 11, hit 10 = 21; дилер 9 + 7 = 16 добирает короля и перебирает
    await deal(w, ["5S", "9C", "6D", "7H", "10H", "KS"])
    await p.wait(PANEL, 15, "панель действий")
    check("на 11: доступны Ещё, Стоп, Удвоить", await p.ev(BUTTONS), [True, True, True])
    head_hard = await p.ev(HEAD)
    await p.tap("#bj-hit")
    await p.wait(done, 30, "раздача закончилась без нажатия «Стоп»")
    check("игрок 21, у дилера три карты (добрал сам)", await p.ev("[document.getElementById('bj-player-total').textContent.trim(), document.querySelectorAll('#bj-dealer-cards .bj-card').length]"), ["21", 3])
    check("победа: баланс 100100", await shown(p, "#bj-balance"), 100100)
    check("кнопок действий нет", await p.ev("document.getElementById('bj-actions').hidden"), True)
    check("на сервере два действия (старт и hit), раздача закрыта", [w.sql_value("SELECT COUNT(*) FROM blackjack_actions"), w.sql_value("SELECT status FROM blackjack_games ORDER BY id DESC LIMIT 1")], [2, "finished"])

    # 2. мягкий 21: туз + 2 = мягкие 13; подпись понятная, высота заголовка та же; hit 8 = мягкий 21, дилер перебирает
    await deal(w, ["AS", "9C", "2D", "7H", "8H", "KS"])
    await p.wait(PANEL, 15, "панель действий")
    check("подпись мягкой руки", await p.ev("document.getElementById('bj-player-total').textContent.trim()"), "Мягкая 13 (туз = 11)")
    check("высота заголовка руки не изменилась, подпись помещается", [await p.ev(HEAD), await p.ev(
        "(() => { const t = document.getElementById('bj-player-total'); return t.scrollWidth <= t.clientWidth + 1 && t.getBoundingClientRect().right <= window.innerWidth; })()")], [head_hard, True])
    await p.tap("#bj-hit")
    await p.wait(done, 30, "мягкий 21: раздача закончилась сама")
    check("мягкий 21 и победа", [await p.ev("document.getElementById('bj-player-total').textContent.trim()"), await shown(p, "#bj-balance")], ["Мягкая 21 (туз = 11)", 100200])
    check("кнопок действий нет", await p.ev("document.getElementById('bj-actions').hidden"), True)

    # 3. натуральный блэкджек как раньше (3:2)
    await deal(w, ["AS", "9C", "KD", "7H"])
    await p.wait(done, 30, "блэкджек")
    check("блэкджек: надпись, выплата 3:2, действий нет", [await p.ev("document.getElementById('bj-player-total').textContent.trim()"), await shown(p, "#bj-balance"),
                                                         await p.ev("document.getElementById('bj-actions').hidden")], ["Блэкджек!", 100350, True])

    # 4. удвоение как раньше: одна карта (10 до 21) и стоп, дилер доигрывает
    await deal(w, ["5S", "6C", "6D", "10H", "10D", "7C"])
    await p.wait(PANEL, 15, "панель действий")
    await p.tap("#bj-double")
    await p.wait(done, 30, "удвоение закончилось")
    check("удвоение: баланс 100350 - 200 + 400", await shown(p, "#bj-balance"), 100550)

    # 5. раздача, начатая до правила: рука 21 осталась активной (в базе), после перезагрузки доступен только «Стоп»
    await deal(w, ["5S", "9C", "6D", "7H", "10H", "KS"])
    await p.wait(PANEL, 15, "панель действий")
    w.sql("UPDATE blackjack_games SET player_json = ?, deck_pos = 5 WHERE status = 'active'", ('["5S","6D","10H"]',))
    await w.reload()
    await open_game(p, "blackjack")
    await p.wait(PANEL, 15, "возобновлённая раздача")
    check("при 21 видна только кнопка «Стоп»", await p.ev(BUTTONS), [False, True, False])
    await p.tap("#bj-stand")
    await p.wait(done, 30, "стоп закрыл раздачу")
    check("итог: баланс 100550 - 100 + 200", await shown(p, "#bj-balance"), 100650)
