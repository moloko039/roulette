"""Сцена скина рубашки карт «Патина» (skins/back_patina.css, коллекция «Патина», DESIGN.md раздел 8).
Проверяется:
1. При открытии экрана блэкджека с надетым скином link на skins/back_patina.css подключён,
   у контейнера стола установлен data-scene="back_patina";
2. До открытия игры и на другой вкладке (рейтинг) link и data-scene отсутствуют;
3. Возврат на экран игры восстанавливает link и data-scene;
4. Снятие скина не оставляет следов в DOM;
5. У рубашки применён стиль скина (тиснение орнаментом в backgroundImage содержит url или gradient);
6. На стадии 3 есть складки (линии под углом) и протёртые углы; тиснение мягчеет (opacity);
7. Стадия 0 без патины: рубашка без складок и протёртостей;
8. Контраст рубашки к рамке карты не ниже 3:1."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_back_patina"
USERS = {"me": {"rate": 0}}
LINK = "document.querySelectorAll('link[href*=\"skins/back_patina.css\"]').length"
DATA_SCENE = "(document.getElementById('bj-table') && document.getElementById('bj-table').dataset.scene) || ''"
CARD_EMBOSS_BG = "getComputedStyle(document.querySelector('.bj-card.back'), '::before').backgroundImage"
CARD_EMBOSS_OPACITY = "parseFloat(getComputedStyle(document.querySelector('.bj-card.back'), '::before').opacity)"
CARD_AFTER_BG = "getComputedStyle(document.querySelector('.bj-card.back'), '::after').backgroundImage"

CONTRAST_JS = """(() => {
  const parse = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const L = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const el = document.querySelector('.bj-card.back');
  const borderCol = parse(getComputedStyle(el).borderTopColor);
  const a = getComputedStyle(document.documentElement).getPropertyValue('--card-back-a').trim();
  const n = parseInt(a.slice(1), 16);
  const backCol = [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  return ratio(borderCol, backCol);
})()"""


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 3000 раундов дают стадию износа 3 для рубашки карт (пороги: 200, 1000, 3000, 8000)
    w.sql(
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<=3000) "
        "INSERT INTO roulette_rounds (telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) "
        "SELECT ?, 'req_' || x, 0, 10, 0, '[]', ? FROM cnt",
        (uid, now),
    )
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'back_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'card_back', 'back_patina')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("до открытия экрана блэкджека link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    await open_game(p, "blackjack")
    await p.wait("!document.getElementById('bj-bets').hidden", 10, "панель ставки")
    check("экран блэкджека открыт: css подключён, у bj-table data-scene='back_patina'",
          [await p.ev(LINK), await p.ev(DATA_SCENE)],
          [1, "back_patina"])

    check("на корне выставлен атрибут data-skin-card_back='back_patina'",
          await p.ev("document.documentElement.getAttribute('data-skin-card_back')"),
          "back_patina")

    # Проверка стадии износа при 3000 раундах
    check("3000 раундов дают стадию data-patina-card_back='3'",
          await p.ev("document.documentElement.getAttribute('data-patina-card_back')"),
          "3")

    # Раздача карт в блэкджеке для появления закрытой карты дилера (.bj-card.back)
    w.server.script(shoe=[["10S", "9H", "10D", "8C"]])
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("document.querySelectorAll('#bj-dealer-cards .bj-card.back').length > 0", 10, "закрытая карта дилера")

    # Проверка применения стиля скина к рубашке (тиснение орнаментом в ::before)
    card_emboss = await p.ev(CARD_EMBOSS_BG)
    check("у рубашки применён стиль скина (тиснение орнаментом в backgroundImage)",
          "url" in card_emboss or "gradient" in card_emboss,
          True)

    # Проверка наличия складок и протёртостей на стадии 3
    after_bg_3 = await p.ev(CARD_AFTER_BG)
    check("на стадии 3 есть складки (linear-gradient)",
          "linear-gradient" in after_bg_3,
          True)
    check("на стадии 3 есть протёртые углы (radial-gradient)",
          "radial-gradient" in after_bg_3,
          True)

    # На стадии 3 тиснение мягчеет (меньше контраста через opacity)
    opacity_3 = await p.ev(CARD_EMBOSS_OPACITY)
    check("на стадии 3 тиснение мягчеет (opacity < 0.5)",
          opacity_3 < 0.5,
          True)

    # Контраст рубашки к рамке карты не ниже 3:1
    contrast_3 = await p.ev(CONTRAST_JS)
    check("на стадии 3 контраст рубашки к рамке карты не ниже 3:1",
          contrast_3 >= 3.0,
          True)

    # Завершаем партию блэкджека (Стоп)
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden", 15, "раунд завершён")

    # Проверка стадии 0: 0 раундов дают стадию 0 без складок и протёртостей
    w.sql("DELETE FROM roulette_rounds WHERE telegram_id = ?", (uid,))
    w.sql("DELETE FROM blackjack_games WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "blackjack")
    await p.wait("!document.getElementById('bj-bets').hidden", 10, "панель ставки (стадия 0)")

    check("стадия 0: data-patina-card_back равен '0'",
          await p.ev("document.documentElement.getAttribute('data-patina-card_back')"),
          "0")

    w.server.script(shoe=[["10S", "9H", "10D", "8C"]])
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("document.querySelectorAll('#bj-dealer-cards .bj-card.back').length > 0", 10, "закрытая карта дилера (стадия 0)")

    after_bg_0 = await p.ev(CARD_AFTER_BG)
    check("стадия 0: рубашка без складок (linear-gradient отсутствует)",
          "linear-gradient" not in after_bg_0,
          True)
    check("стадия 0: рубашка без протёртостей (radial-gradient отсутствует)",
          "radial-gradient" not in after_bg_0,
          True)

    opacity_0 = await p.ev(CARD_EMBOSS_OPACITY)
    check("стадия 0: тиснение чёткое (opacity >= 0.8)",
          opacity_0 >= 0.8,
          True)

    contrast_0 = await p.ev(CONTRAST_JS)
    check("на стадии 0 контраст рубашки к рамке карты не ниже 3:1",
          contrast_0 >= 3.0,
          True)

    # Завершаем раздачу стадии 0
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden", 15, "раунд стадии 0 завершён")

    # Переключение вкладок: на экране рейтинга link и data-scene отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране другой вкладки (рейтинг) link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])

    # Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=blackjack]').hidden", 5, "возврат в блэкджек")
    check("после возврата link и data-scene восстановлены", [await p.ev(LINK), await p.ev(DATA_SCENE)], [1, "back_patina"])

    # Снятие скина не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'card_back'", (uid,))
    w.sql("DELETE FROM blackjack_games WHERE telegram_id = ?", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "blackjack")
    await p.wait("!document.getElementById('bj-bets').hidden", 10, "блэкджек без скина")
    check("скин снят: link и data-scene отсутствуют", [await p.ev(LINK), await p.ev(DATA_SCENE)], [0, ""])
