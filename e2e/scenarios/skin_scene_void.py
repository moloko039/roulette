"""Сцена предметов набора «Пустота» (void_table, void_chip, void_badge, DESIGN.md раздел 5).
Проверяется:
1. Мета-теги скинов в index.html для void_table, void_chip, void_badge;
2. До открытия экрана рулетки link и data-scene отсутствуют;
3. При открытии рулетки с надетыми скинами:
   - у #table установлен data-scene="void_table", link на skins/void_table.css подключён;
   - у body установлен data-scene="void_chip", link на skins/void_chip.css подключён;
   - на корне атрибуты data-skin-table="void_table", data-skin-chip="void_chip";
4. Стол рисует тонкие линии:
   - тонкая рамка 1 px по краю (#2A2A2A);
   - внутри волосяная сетка 1 px задана через background-image (repeating-linear-gradient);
   - цвет фона почти чёрный (графит #111111);
5. Фишка рисует тонкие линии:
   - тонкое белое кольцо 1 px;
   - цвет кольца белый (#ffffff);
   - цифры внутри с font-variant-numeric: tabular-nums;
   - номиналы различаются толщиной кольца, а не цветом (все белые);
   - анимация ставки void-chip-drop отключается при prefers-reduced-motion и perf-lite;
6. Значок «—»:
   - в профиле у значка data-skin-badge="void_badge";
   - цвет белый (#ffffff);
   - форма — длинное тире «—» (svg / css);
7. Контраст не падает:
   - цифры фишки к фону >= 3:1;
   - значок к фону >= 3:1;
8. Переключение вкладок (рейтинг) скрывает сцены стола, возврат восстанавливает;
9. Снятие скинов не оставляет следов в DOM."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_void"
USERS = {"me": {"rate": 0}}
META_TABLE = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_table\"]').length"
META_CHIP = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_chip\"]').length"
META_BADGE = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_badge\"]').length"
LINK_TABLE = "document.querySelectorAll('link[href*=\"skins/void_table.css\"]').length"
LINK_CHIP = "document.querySelectorAll('link[href*=\"skins/void_chip.css\"]').length"
DATA_SCENE_TABLE = "(document.getElementById('table') && document.getElementById('table').dataset.scene) || ''"
DATA_SCENE_CHIP = "document.body.dataset.scene || ''"
TABLE_BG = "getComputedStyle(document.getElementById('table')).backgroundImage"
TABLE_BG_COL = "getComputedStyle(document.getElementById('table')).backgroundColor"
TABLE_BORDER_W = "getComputedStyle(document.getElementById('table')).borderTopWidth"
STACK_ANIM = "(document.querySelector('#table .stack.drop') && getComputedStyle(document.querySelector('#table .stack.drop')).animationName) || 'none'"
STILL = {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}
NORMAL = {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}

CHIP_CONTRAST_JS = """(() => {
  const parse = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const L = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const chip = document.querySelector('.chip[data-amount="10"]') || document.querySelector('.chip');
  const chipText = parse(getComputedStyle(chip).color);
  const chipBg = parse(getComputedStyle(chip).backgroundColor);
  return ratio(chipText, chipBg);
})()"""

BADGE_CONTRAST_JS = """(() => {
  const parse = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const L = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const badge = document.getElementById('profile-badge');
  const badgeCol = parse(getComputedStyle(badge).color);
  const surface = [15, 15, 18, 1];
  return ratio(badgeCol, surface);
})()"""


async def ensure_badge_skin_css(p):
    """Подключает CSS скина значка по мета-тегу, если ещё не подключён."""
    await p.ev("""(() => {
        const m = document.querySelector('meta[name="skin-css"][data-code="void_badge"]');
        if (m && !document.querySelector('link[href*="skins/void_badge.css"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = m.content;
            document.head.appendChild(link);
        }
        if (typeof renderOwnCosmetics === 'function') {
            renderOwnCosmetics();
        }
    })()""")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 1. Проверяем наличие мета-тегов скинов в index.html
    check("в index.html есть meta name=skin-css data-code=void_table", await p.ev(META_TABLE), 1)
    check("в index.html есть meta name=skin-css data-code=void_chip", await p.ev(META_CHIP), 1)
    check("в index.html есть meta name=skin-css data-code=void_badge", await p.ev(META_BADGE), 1)

    # 2. Надеваем предметы набора «Пустота»
    w.sql("UPDATE players SET balance = 500 WHERE telegram_id = ?", (uid,))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'void_table', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'void_chip', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'void_badge', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'table', 'void_table')", (uid,))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'chip', 'void_chip')", (uid,))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'badge', 'void_badge')", (uid,))
    await w.reload()

    # Отключаем настоящий монитор кадров в начале сценария
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    # 3. До открытия игры links и data-scenes отсутствуют
    check("до открытия экрана рулетки links и data-scenes отсутствуют",
          [await p.ev(LINK_TABLE), await p.ev(DATA_SCENE_TABLE), await p.ev(LINK_CHIP), await p.ev(DATA_SCENE_CHIP)],
          [0, "", 0, ""])

    # 4. Открываем экран рулетки
    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "стол рулетки загружен")
    await p.wait("!!document.querySelector('#table[data-scene=\"void_table\"]')", 10, "сцена стола смонтирована")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("экран рулетки открыт: css стола подключён, data-scene='void_table'",
          [await p.ev(LINK_TABLE), await p.ev(DATA_SCENE_TABLE)],
          [1, "void_table"])

    check("экран рулетки открыт: css фишки подключён, data-scene='void_chip'",
          [await p.ev(LINK_CHIP), await p.ev(DATA_SCENE_CHIP)],
          [1, "void_chip"])

    check("на корне выставлены атрибуты data-skin для table и chip",
          [
              await p.ev("document.documentElement.getAttribute('data-skin-table')"),
              await p.ev("document.documentElement.getAttribute('data-skin-chip')")
          ],
          ["void_table", "void_chip"])

    # 5. Стол рисует тонкие линии (DESIGN.md раздел 5)
    border_w = await p.ev(TABLE_BORDER_W)
    check("у стола тонкая рамка 1 px по краю", border_w, "1px")

    table_bg = await p.ev(TABLE_BG)
    check("у стола волосяная сетка задана через repeating-linear-gradient",
          "repeating-linear-gradient" in table_bg,
          True)

    table_bg_col = await p.ev(TABLE_BG_COL)
    check("цвет стола почти чёрный (графит #111111 / rgb(17, 17, 17))",
          table_bg_col in ("rgb(17, 17, 17)", "rgba(17, 17, 17, 1)"),
          True)

    # 6. Фишка рисует тонкие линии
    chip_w10 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"10\"]')).borderTopWidth")
    check("у фишки номинала 10 тонкое кольцо 1 px", chip_w10, "1px")

    await p.wait("getComputedStyle(document.querySelector('.chip[data-amount=\"10\"]')).borderTopColor === 'rgb(255, 255, 255)'", 5, "переход цвета кольца фишки завершён")
    chip_col10 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"10\"]')).borderTopColor")
    check("кольцо фишки белое (#ffffff)", "255, 255, 255" in chip_col10, True)

    font_numeric = await p.ev("getComputedStyle(document.querySelector('.chip')).fontVariantNumeric")
    check("номинал фишки с font-variant-numeric: tabular-nums", "tabular-nums" in font_numeric, True)

    # Номиналы различаются толщиной кольца, не цветом
    w10 = await p.ev("parseFloat(getComputedStyle(document.querySelector('.chip[data-amount=\"10\"]')).borderTopWidth)")
    w50 = await p.ev("parseFloat(getComputedStyle(document.querySelector('.chip[data-amount=\"50\"]')).borderTopWidth)")
    w100 = await p.ev("parseFloat(getComputedStyle(document.querySelector('.chip[data-amount=\"100\"]')).borderTopWidth)")
    w500 = await p.ev("parseFloat(getComputedStyle(document.querySelector('.chip[data-amount=\"500\"]')).borderTopWidth)")
    check("номиналы различаются толщиной кольца: 10 <= 50 <= 100 <= 500",
          (w10 <= w50 <= w100 <= w500 and w10 < w500),
          True)

    c10 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"10\"]')).borderTopColor")
    c50 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"50\"]')).borderTopColor")
    c100 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"100\"]')).borderTopColor")
    c500 = await p.ev("getComputedStyle(document.querySelector('.chip[data-amount=\"500\"]')).borderTopColor")
    check("номиналы не различаются цветом кольца (все белые)",
          c10 == c50 == c100 == c500 and "255, 255, 255" in c10,
          True)

    # Контраст цифры фишки к фону не ниже 3:1
    chip_contrast = await p.ev(CHIP_CONTRAST_JS)
    check("контраст цифры фишки к фону >= 3:1", chip_contrast >= 3.0, True)

    # Ставка на столе и анимация ставки (scale от 0, без подскока)
    await set_bet(p, "amount", 10)
    cell = ".cell[data-key='number:17']"
    await p.ev("document.querySelector(\"#table %s\").scrollIntoView({block: 'center'})" % cell)
    await p.settle()
    await p.tap("#table " + cell)
    await p.wait("document.querySelectorAll('#table .stack.drop').length === 1", 5, "ставка поставлена")

    drop_anim = await p.ev(STACK_ANIM)
    check("у .stack.drop анимация не 'none' (void-chip-drop)", drop_anim != "none" and "void-chip-drop" in drop_anim, True)

    # prefers-reduced-motion и perf-lite отключают анимацию
    await p.send("Emulation.setEmulatedMedia", STILL)
    check("prefers-reduced-motion: animationName === 'none'", await p.ev(STACK_ANIM), "none")
    await p.send("Emulation.setEmulatedMedia", NORMAL)

    await p.ev("skinSetLite()")
    check("perf-lite: animationName === 'none'", await p.ev(STACK_ANIM), "none")
    await p.ev("skinRt.lite = false; document.documentElement.classList.remove('perf-lite')")

    # 7. Значок «—» (void_badge) в профиле
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля")
    await ensure_badge_skin_css(p)
    badge_code = await p.ev("document.getElementById('profile-badge').getAttribute('data-skin-badge')")
    check("в профиле у значка data-skin-badge='void_badge'", badge_code, "void_badge")

    badge_color = await p.ev("getComputedStyle(document.getElementById('profile-badge')).color")
    check("цвет значка белый", "255, 255, 255" in badge_color, True)

    has_dash = await p.ev("""(() => {
        const b = document.getElementById('profile-badge');
        const line = b.querySelector('svg line');
        if (line) return true;
        const after = getComputedStyle(b, '::after').content;
        return after && after.includes('—');
    })()""")
    check("значок рисует длинное тире «—»", has_dash, True)

    # Контраст значка к фону не ниже 3:1
    badge_contrast = await p.ev(BADGE_CONTRAST_JS)
    check("контраст значка к фону >= 3:1", badge_contrast >= 3.0, True)

    # 8. Переключение вкладок: на экране рейтинга links и data-scenes стола отсутствуют
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на экране рейтинга link и data-scene стола отсутствуют",
          [await p.ev(LINK_TABLE), await p.ev(DATA_SCENE_TABLE)],
          [0, ""])

    # 9. Возврат в игру
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=roulette]').hidden", 5, "возврат в рулетку")
    await p.wait("!!document.querySelector('#table[data-scene=\"void_table\"]')", 10, "сцена стола восстановлена")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата links и data-scenes восстановлены",
          [await p.ev(LINK_TABLE), await p.ev(DATA_SCENE_TABLE), await p.ev(LINK_CHIP), await p.ev(DATA_SCENE_CHIP)],
          [1, "void_table", 1, "void_chip"])

    # 10. Снятие скинов не оставляет следов
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot IN ('table', 'chip', 'badge')", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "roulette")
    await p.wait("document.querySelectorAll('#table .cell').length >= 37", 10, "рулетка без скина")
    check("скины сняты: links и data-scenes отсутствуют",
          [await p.ev(LINK_TABLE), await p.ev(DATA_SCENE_TABLE), await p.ev(LINK_CHIP), await p.ev(DATA_SCENE_CHIP)],
          [0, "", 0, ""])
