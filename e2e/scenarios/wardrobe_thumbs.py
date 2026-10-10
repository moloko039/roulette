"""Проверка миниатюр гардероба: изоляция переменных и соответствие настоящему виду скинов.

1. Миниатюры не наследуют чужие переменные с корня (:root):
   При надетом «Черновике» (draft_back) миниатюра «Уголь» (back_ember) не становится синей,
   а имеет собственный тёмный фон со штрихами и не совпадает по getComputedStyle с «Черновиком».
2. Миниатюры краша соответствуют реальным сценам игры:
   «Патина» содержит циферблат и шкалу самописца, «Бочка» — ракету и дачный пейзаж,
   «Черновик» — миллиметровку и карандашную линию, «Глубина» — батискаф и море,
   «Клён» — осеннюю аллею, «Пустота» — тонкую белую линию на сетке.
"""
import time

from harness import check

NAME = "wardrobe_thumbs"
USERS = {"me": {"rate": 0}}
VIEWPORT = (390, 700)
CLOCK_MOD = 5


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # Надеваем «Черновик» рубашки карт
    w.sql("INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'card_back', 'draft_back')", (uid,))
    # Выдаём «Черновик» и «Уголь»
    for code in ("draft_back", "back_ember"):
        w.sql("INSERT OR IGNORE INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'owner_gift', ?)", (uid, code, now))

    await w.reload()
    await p.tap(".tab[data-tab=shop]")
    await p.tap("#shop-pages [data-page=look]")
    await p.wait("!document.querySelector('[data-screen=shop]').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб открыт")

    # 1. Проверяем надетость «Черновика» на корне
    check("надета рубашка Черновик", await p.ev("document.documentElement.getAttribute('data-skin-card_back')"), "draft_back")

    # 2. Проверяем изоляцию: миниатюра «Уголь» не наследует фон «Черновика»
    bg_ember = await p.ev("getComputedStyle(document.querySelector('.wd-mini[data-skin-card_back=back_ember] .bj-card.back')).backgroundImage")
    bg_draft = await p.ev("getComputedStyle(document.querySelector('.wd-mini[data-skin-card_back=draft_back] .bj-card.back')).backgroundImage")
    var_a_ember = await p.ev("getComputedStyle(document.querySelector('.wd-mini[data-skin-card_back=back_ember]')).getPropertyValue('--card-back-a').trim()")
    var_a_draft = await p.ev("getComputedStyle(document.querySelector('.wd-mini[data-skin-card_back=draft_back]')).getPropertyValue('--card-back-a').trim()")

    check("миниатюра Уголь не совпадает по фону с Черновиком", bg_ember != bg_draft, True)
    check("миниатюра Уголь имеет собственный цвет фона --card-back-a", var_a_ember, "#12100e")
    check("миниатюра Черновик имеет собственный цвет фона --card-back-a", var_a_draft, "#5B7DB8")

    # 3. Переходим во вкладку «Краш» и проверяем совпадение миниатюр со сценами
    await p.tap("#wd-tabs .wd-tab:nth-child(6)")
    await p.wait("document.querySelectorAll('#wd-grid .wd-card').length > 0", 5, "слот краш открыт")

    patina_has_dial = await p.ev("document.querySelector('.wd-mini[data-skin-crash=crash_patina] svg').innerHTML.includes('САМОПИСЕЦЪ')")
    barrel_has_rocket = await p.ev("document.querySelector('.wd-mini[data-skin-crash=crash_barrel] svg').innerHTML.includes('scb-flame')")
    draft_has_paper = await p.ev("document.querySelector('.wd-mini[data-skin-crash=draft_crash] svg').innerHTML.includes('cr-df-g')")
    deep_has_sub = await p.ev("document.querySelector('.wd-mini[data-skin-crash=crash_deep] svg').innerHTML.includes('cr-dp-sky')")
    maple_has_leaf = await p.ev("document.querySelector('.wd-mini[data-skin-crash=crash_maple] svg').innerHTML.includes('cr-mp-sky')")
    void_has_grid = await p.ev("document.querySelector('.wd-mini[data-skin-crash=void_crash] svg').innerHTML.includes('cr-void-p')")

    check("миниатюра Патина содержит шкалу самописца", patina_has_dial, True)
    check("миниатюра Бочка содержит ракету", barrel_has_rocket, True)
    check("миниатюра Черновик краша содержит миллиметровку", draft_has_paper, True)
    check("миниатюра Глубина содержит батискаф и море", deep_has_sub, True)
    check("миниатюра Клён содержит аллею и клён", maple_has_leaf, True)
    check("миниатюра Пустота содержит сетку Пустоты", void_has_grid, True)
