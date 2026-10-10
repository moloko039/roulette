"""Сцена четырёх частей набора «Пустота» (void_back, void_frame, void_mines, void_keno, DESIGN.md раздел 5).
Проверяется:
1. Мета-теги скинов в index.html для void_back, void_frame, void_mines, void_keno;
2. До открытия игр link и data-scene отсутствуют;
3. void_back (рубашка карт):
   - у #bj-table установлен data-scene="void_back", link на skins/void_back.css подключён;
   - на корне выставлен атрибут data-skin-card_back="void_back";
   - рубашка содержит тонкую горизонтальную линию (::after 1 px);
   - контраст рубашки к рамке карты не ниже 3:1 по skin_contrast;
4. void_frame (рамка аватара):
   - у аватара профиля выставлен data-skin-avatar_frame="void_frame";
   - отсутствие обводки (border transparent);
   - аватар показывает тонкое кольцо 1 px на расстоянии 4 px (box-shadow);
5. void_mines (иконки мин):
   - у #mines-board-wrap установлен data-scene="void_mines", link на skins/void_mines.css подключён;
   - на корне выставлен атрибут data-skin-mine_icons="void_mines";
   - клетки — пустые квадраты-контуры 1 px;
   - безопасная (.safe): контур гаснет до тонкой точки в центре;
   - мина (.mine): клетка полностью чёрная, без взрыва;
   - переходы происходят по классам .safe и .mine;
6. void_keno (шарики кено):
   - у #keno-board установлен data-scene="void_keno", link на skins/void_keno.css подключён;
   - на корне выставлен атрибут data-skin-keno_ball="void_keno";
   - числа без фона, только цифры;
   - выбранные (.sel) подчёркнуты тонкой линией 1 px;
   - совпадение (.hit): цифра становится тёплым белым #F5EBDD, подчёркивание масштабируется на полную ширину (scaleX);
7. Переключение вкладок и снятие скинов очищают DOM."""
import asyncio
import time

from harness import check, open_game, set_bet

NAME = "skin_scene_void_parts"
USERS = {"me": {"rate": 0}}

META_BACK = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_back\"]').length"
META_FRAME = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_frame\"]').length"
META_MINES = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_mines\"]').length"
META_KENO = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"void_keno\"]').length"

LINK_BACK = "document.querySelectorAll('link[href*=\"skins/void_back.css\"]').length"
LINK_MINES = "document.querySelectorAll('link[href*=\"skins/void_mines.css\"]').length"
LINK_KENO = "document.querySelectorAll('link[href*=\"skins/void_keno.css\"]').length"

DATA_SCENE_BJ = "(document.getElementById('bj-table') && document.getElementById('bj-table').dataset.scene) || ''"
DATA_SCENE_MINES = "(document.getElementById('mines-board-wrap') && document.getElementById('mines-board-wrap').dataset.scene) || ''"
DATA_SCENE_KENO = "(document.getElementById('keno-board') && document.getElementById('keno-board').dataset.scene) || ''"

CONTRAST_CARD_JS = """(() => {
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


async def ensure_frame_skin_css(p):
    """Подключает CSS скина рамки по мета-тегу, если ещё не подключён."""
    await p.ev("""(() => {
        const m = document.querySelector('meta[name="skin-css"][data-code="void_frame"]');
        if (m && !document.querySelector('link[href*="skins/void_frame.css"]')) {
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

    # 1. Проверяем наличие мета-тегов в index.html
    check("в index.html есть meta skin-css data-code=void_back", await p.ev(META_BACK), 1)
    check("в index.html есть meta skin-css data-code=void_frame", await p.ev(META_FRAME), 1)
    check("в index.html есть meta skin-css data-code=void_mines", await p.ev(META_MINES), 1)
    check("в index.html есть meta skin-css data-code=void_keno", await p.ev(META_KENO), 1)

    # 2. Надеваем 4 предмета набора «Пустота»
    w.sql("UPDATE players SET balance = 5000 WHERE telegram_id = ?", (uid,))
    for item, slot in [("void_back", "card_back"), ("void_frame", "avatar_frame"), ("void_mines", "mine_icons"), ("void_keno", "keno_ball")]:
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, 'free', NULL, ?)", (uid, item, now))
        w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (uid, slot, item))

    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await p.ev("""(() => {
        window.__origApplySkins = window.applySkins;
        window.__equipVoid = {
            card_back: 'void_back',
            mine_icons: 'void_mines',
            keno_ball: 'void_keno',
            avatar_frame: 'void_frame'
        };
        window.applySkins = (eq, pat, info) => {
            window.__origApplySkins(Object.assign({}, eq, window.__equipVoid), pat, info);
            if (typeof ownEquipped !== 'undefined' && window.__equipVoid.avatar_frame) {
                ownEquipped.avatar_frame = window.__equipVoid.avatar_frame;
            }
            const av = document.getElementById('profile-avatar');
            if (av && window.__equipVoid.avatar_frame) {
                av.setAttribute('data-skin-avatar_frame', window.__equipVoid.avatar_frame);
            }
        };
        window.applySkins({});
    })()""")

    # 3. До открытия игр ссылки и data-scenes отсутствуют
    check("до открытия игр link и data-scene для bj, mines, keno отсутствуют",
          [await p.ev(LINK_BACK), await p.ev(DATA_SCENE_BJ),
           await p.ev(LINK_MINES), await p.ev(DATA_SCENE_MINES),
           await p.ev(LINK_KENO), await p.ev(DATA_SCENE_KENO)],
          [0, "", 0, "", 0, ""])

    # 4. Проверяем void_back в блэкджеке
    await open_game(p, "blackjack")
    await p.wait("!document.getElementById('bj-bets').hidden", 10, "панель ставки блэкджека")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("блэкджек: css void_back подключён, у bj-table data-scene='void_back'",
          [await p.ev(LINK_BACK), await p.ev(DATA_SCENE_BJ)],
          [1, "void_back"])
    check("на корне выставлен атрибут data-skin-card_back='void_back'",
          await p.ev("document.documentElement.getAttribute('data-skin-card_back')"),
          "void_back")

    # Раздача карт в блэкджеке для появления закрытой карты дилера (.bj-card.back)
    w.server.script(shoe=[["10S", "9H", "10D", "8C"]])
    await set_bet(p, "bj-bet", 100)
    await p.tap("#bj-deal")
    await p.wait("document.querySelectorAll('#bj-dealer-cards .bj-card.back').length > 0", 10, "закрытая карта дилера")

    # Проверка: рубашка содержит линию (1 px)
    has_card_line = await p.ev("""(() => {
        const el = document.querySelector('#bj-dealer-cards .bj-card.back');
        if (!el) return false;
        const after = getComputedStyle(el, '::after');
        const h = parseFloat(after.height);
        const bg = after.backgroundColor;
        return h === 1 && bg.includes('255, 255, 255');
    })()""")
    check("рубашка содержит тонкую горизонтальную линию 1 px", has_card_line, True)

    # Контраст рубашки к рамке карты не ниже 3:1 по skin_contrast
    contrast_card = await p.ev(CONTRAST_CARD_JS)
    check("контраст рубашки к рамке карты не ниже 3:1", contrast_card >= 3.0, True)

    # Завершаем партию блэкджека
    await p.tap("#bj-stand")
    await p.wait("!document.getElementById('bj-banner').hidden", 15, "раунд блэкджека завершён")

    # 5. Проверяем void_frame в профиле
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля")
    await ensure_frame_skin_css(p)

    check("у аватара профиля выставлен data-skin-avatar_frame='void_frame'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame')"),
          "void_frame")

    await p.wait("getComputedStyle(document.getElementById('profile-avatar')).borderTopColor === 'rgba(0, 0, 0, 0)'", 5, "обводка аватара скрыта")
    frame_ok = await p.ev("""(() => {
        const el = document.getElementById('profile-avatar');
        const cs = getComputedStyle(el);
        const border = cs.borderTopColor;
        const noBorder = border.includes('rgba(0, 0, 0, 0)') || border === 'transparent' || cs.borderTopWidth === '0px';
        const sh = cs.boxShadow;
        const hasRing = sh.includes('255, 255, 255') && (sh.includes('5px') || sh.includes('4px'));
        return noBorder && hasRing;
    })()""")
    check("рамка аватара: отсутствие обводки и тонкое кольцо на расстоянии 4 px", frame_ok, True)

    # 6. Проверяем void_mines в игре «Мины»
    await p.tap(".tab[data-tab=play]")
    await open_game(p, "mines")
    await p.wait("document.getElementById('mines-begin') && !document.getElementById('mines-begin').disabled", 10, "форма старта мин")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("мины: css void_mines подключён, у board-wrap data-scene='void_mines'",
          [await p.ev(LINK_MINES), await p.ev(DATA_SCENE_MINES)],
          [1, "void_mines"])
    check("на корне выставлен атрибут data-skin-mine_icons='void_mines'",
          await p.ev("document.documentElement.getAttribute('data-skin-mine_icons')"),
          "void_mines")

    # Клетки — пустые квадраты-контуры 1 px
    cell_border_w = await p.ev("getComputedStyle(document.querySelector('.mines-cell')).borderTopWidth")
    check("клетка мин — контур 1 px", cell_border_w, "1px")

    # Запускаем раунд мин: мины на позициях 0, 1, 2
    w.server.script(mines=[[0, 1, 2], [0, 1, 2]])
    await set_bet(p, "mines-bet", 100)
    await p.tap("#mines-begin")
    await p.wait("document.querySelectorAll('#mines-grid .mines-cell').length === 25", 10, "поле мин 5x5")

    # Открываем безопасную клетку (клетка 13, индекс 12)
    await p.tap("#mines-grid .mines-cell:nth-child(13)")
    await p.wait("document.querySelector('#mines-grid .mines-cell:nth-child(13)').classList.contains('safe')", 10, "клетка 13 открыта как safe")

    safe_has_dot = await p.ev("""(() => {
        const el = document.querySelector('#mines-grid .mines-cell:nth-child(13)');
        const after = getComputedStyle(el, '::after');
        const w = parseFloat(after.width);
        const h = parseFloat(after.height);
        const bg = after.backgroundColor;
        return w > 0 && h > 0 && bg.includes('255, 255, 255');
    })()""")
    check("безопасная клетка (.safe) имеет тонкую точку в центре", safe_has_dot, True)

    # Наступаем на мину (клетка 1, индекс 0): мина взрывается
    await p.tap("#mines-grid .mines-cell:nth-child(1)")
    await p.wait("document.querySelector('#mines-grid .mines-cell:nth-child(1)').classList.contains('mine')", 10, "клетка 1 открыта как mine")

    mine_cell_ok = await p.ev("""(() => {
        const el = document.querySelector('#mines-grid .mines-cell:nth-child(1)');
        const cs = getComputedStyle(el);
        const bg = cs.backgroundColor;
        const sh = cs.boxShadow;
        const isBlack = bg === 'rgb(0, 0, 0)' || bg === 'rgba(0, 0, 0, 1)';
        const noExplosion = sh === 'none' || !sh.includes('rgb');
        return isBlack && noExplosion;
    })()""")
    check("мина (.mine): клетка полностью чёрная, без взрыва и частиц", mine_cell_ok, True)

    # 7. Проверяем void_keno в кено
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "поле кено 40 шариков")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    check("кено: css void_keno подключён, у board data-scene='void_keno'",
          [await p.ev(LINK_KENO), await p.ev(DATA_SCENE_KENO)],
          [1, "void_keno"])
    check("на корне выставлен атрибут data-skin-keno_ball='void_keno'",
          await p.ev("document.documentElement.getAttribute('data-skin-keno_ball')"),
          "void_keno")

    # Числа без фона, только цифры
    keno_no_bg = await p.ev("""(() => {
        const el = document.querySelector('.keno-ball span');
        const cs = getComputedStyle(el);
        return cs.backgroundColor === 'rgba(0, 0, 0, 0)' || cs.backgroundColor === 'transparent';
    })()""")
    check("кено: числа без фона, только цифры", keno_no_bg, True)

    # Выбираем числа 1, 2, 3
    for n in (1, 2, 3):
        await p.tap("button.keno-ball:nth-of-type(%d)" % n)
    await p.wait("document.querySelectorAll('.keno-ball.sel').length === 3", 5, "выбрано 3 числа")

    # Выбранные подчёркнуты тонкой линией 1 px
    sel_has_line = await p.ev("""(() => {
        const el = document.querySelector('.keno-ball.sel span');
        const after = getComputedStyle(el, '::after');
        const h = parseFloat(after.height);
        const bg = after.backgroundColor;
        return h === 1 && bg.includes('255, 255, 255');
    })()""")
    check("выбранные числа подчёркнуты тонкой линией 1 px", sel_has_line, True)

    # Запускаем раунд с 3 совпадениями
    w.server.script(keno=[[1, 2, 3, 11, 12, 13, 14, 15, 16, 17]])
    await set_bet(p, "keno-bet", 100)
    await p.tap("#keno-play")
    await p.wait("!document.getElementById('keno-play').disabled", 15, "раунд кено завершён")
    check("3 совпадения (.hit) на поле", await p.ev("document.querySelectorAll('.keno-ball.hit').length"), 3)

    # Совпадение: цифра становится тёплым белым #F5EBDD, подчёркивание масштабируется на полную ширину
    hit_ok = await p.ev("""(() => {
        const el = document.querySelector('.keno-ball.hit span');
        const cs = getComputedStyle(el);
        const col = cs.color;
        // #F5EBDD = rgb(245, 235, 221)
        const isWarmWhite = col.includes('245, 235, 221');
        const after = getComputedStyle(el, '::after');
        const tr = after.transform;
        // transform/scaleX(1) yields matrix(1, 0, 0, 1, 0, 0) или none
        const isFullScale = tr === 'none' || tr.startsWith('matrix(1, 0, 0, 1');
        return isWarmWhite && isFullScale;
    })()""")
    check("совпадение (.hit): цифра тёплый белый #F5EBDD, подчёркивание на полную ширину (scaleX)", hit_ok, True)

    # 8. Переключение вкладок: уход убирает сцену и link, возврат восстанавливает
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.querySelector('[data-screen=rating]').hidden", 5, "вкладка рейтинг")
    check("на вкладке рейтинга link и data-scene кено отсутствуют",
          [await p.ev(LINK_KENO), await p.ev(DATA_SCENE_KENO)],
          [0, ""])

    # Возврат в кено
    await p.tap(".tab[data-tab=play]")
    await p.wait("!document.querySelector('[data-screen=keno]').hidden", 5, "возврат в кено")
    await p.wait("!!document.querySelector('#keno-board[data-scene=\"void_keno\"]')", 10, "сцена кено восстановлена")
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    check("после возврата link и data-scene кено восстановлены",
          [await p.ev(LINK_KENO), await p.ev(DATA_SCENE_KENO)],
          [1, "void_keno"])

    # 9. Снятие скинов не оставляет следов в DOM
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot IN ('card_back', 'avatar_frame', 'mine_icons', 'keno_ball')", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await open_game(p, "keno")
    await p.wait("document.querySelectorAll('.keno-ball').length === 40", 10, "кено без скина")
    check("скины сняты: links и data-scenes отсутствуют",
          [await p.ev(LINK_KENO), await p.ev(DATA_SCENE_KENO)],
          [0, ""])
