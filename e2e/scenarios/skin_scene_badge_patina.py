"""Сцена скина значка «Патина» (skins/badge_patina.css, коллекция «Патина», DESIGN.md раздел 8).
Проверяется:
1. Наличие мета-тега скина <meta name="skin-css" data-code="badge_patina"> в index.html;
2. На стадии 0 (стаж 12 дней):
   - у значка в рейтинге выставлены data-skin-badge="badge_patina", data-days="12" и data-patina-badge="0";
   - getComputedStyle(::after).content показывает «12»;
   - значок без натёртого края (без inset в box-shadow);
3. Без атрибута data-days текст ::after пустой;
4. На стадии 3 (стаж 200 дней):
   - выставлены data-patina-badge="3" и data-days="200";
   - getComputedStyle(::after).content показывает «200»;
   - стадия 3 даёт натёртый край (inset в box-shadow);
5. На стадии 4 (стаж 400 дней):
   - выставлены data-patina-badge="4" и data-days="400";
   - размер шрифта меньше, чем на стадии 0 (уменьшение шрифта при большом числе дней);
6. Снятие скина: у значка нет data-skin-badge."""
import time

from harness import check

NAME = "skin_scene_badge_patina"
USERS = {"me": {"rate": 0}}
META = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"badge_patina\"]').length"
BADGE_SEL = "#rating-me .rating-badge"


async def ensure_skin_css(p):
    """Подключает CSS скина по мета-тегу и гарантирует SVG-заглушку значка."""
    await p.ev("""(() => {
        const m = document.querySelector('meta[name="skin-css"][data-code="badge_patina"]');
        if (m && !document.querySelector('link[href*="skins/badge_patina.css"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = m.content;
            document.head.appendChild(link);
        }
        if (typeof BADGE_SVG !== 'undefined' && !BADGE_SVG.badge_patina) {
            BADGE_SVG.badge_patina = '<svg viewBox="0 0 24 24" aria-hidden="true"></svg>';
        }
        if (typeof renderOwnCosmetics === 'function') {
            renderOwnCosmetics();
        }
    })()""")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 1. Проверяем наличие мета-тега скина в index.html
    check("в index.html есть meta name=skin-css data-code=badge_patina",
          await p.ev(META),
          1)

    # 2. Стадия 0: стаж 12 дней (< 30 дней)
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now - 12 * 86400, uid))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'badge_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'badge', 'badge_patina')", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    # Проверяем профиль
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля (стадия 0)")
    check("в профиле у #profile-badge data-skin-badge='badge_patina'",
          await p.ev("document.getElementById('profile-badge').getAttribute('data-skin-badge')"),
          "badge_patina")
    check("в профиле у #profile-badge data-days='12'",
          await p.ev("document.getElementById('profile-badge').getAttribute('data-days')"),
          "12")
    check("в профиле значок показывает «12» через getComputedStyle(::after).content",
          "12" in (await p.ev("getComputedStyle(document.getElementById('profile-badge'), '::after').content")),
          True)

    # Открываем вкладку рейтинга
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 10, "экран рейтинга (стадия 0)")

    # Проверка атрибутов значка в рейтинге
    check("у своего значка в рейтинге data-skin-badge='badge_patina'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-skin-badge')"),
          "badge_patina")
    check("у своего значка в рейтинге data-days='12'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-days')"),
          "12")
    check("стаж 12 дней даёт стадию data-patina-badge='0'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-patina-badge')"),
          "0")

    # В рейтинге значок с data-days=12 показывает «12» через getComputedStyle(::after).content
    content_12 = await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}'), '::after').content")
    check("в рейтинге значок с data-days=12 показывает «12» через getComputedStyle(::after).content",
          "12" in content_12,
          True)

    # На стадии 0 нет натёртого края
    bs_0 = await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}')).boxShadow")
    check("на стадии 0 нет натёртого края (без inset)",
          "inset" not in bs_0,
          True)

    # Запоминаем размер шрифта для сравнения с большими числами
    fs_0 = float((await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}'), '::after').fontSize")).replace("px", ""))

    # 3. Без data-days текст пустой
    await p.ev(f"document.querySelector('{BADGE_SEL}').removeAttribute('data-days')")
    content_empty = await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}'), '::after').content")
    check("без data-days текст пустой",
          content_empty.strip("\"'"),
          "")

    # 4. Стадия 3: 200 дней стажа (>= 180 дней): стадия 3 даёт натёртый край
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now - 200 * 86400, uid))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 10, "экран рейтинга (стадия 3)")

    check("стаж 200 дней даёт стадию data-patina-badge='3'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-patina-badge')"),
          "3")
    check("у значка в рейтинге data-days='200'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-days')"),
          "200")

    content_200 = await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}'), '::after').content")
    check("на стадии 3 content ::after содержит «200»",
          "200" in content_200,
          True)

    # Стадия 3 даёт натёртый край (inset в box-shadow)
    bs_3 = await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}')).boxShadow")
    check("стадия 3 даёт натёртый край (inset в box-shadow)",
          "inset" in bs_3,
          True)

    # 5. Стадия 4: 400 дней стажа (>= 365 дней): уменьшение шрифта при большом числе
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now - 400 * 86400, uid))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 10, "экран рейтинга (стадия 4)")

    check("стаж 400 дней даёт стадию data-patina-badge='4'",
          await p.ev(f"document.querySelector('{BADGE_SEL}').getAttribute('data-patina-badge')"),
          "4")

    fs_4 = float((await p.ev(f"getComputedStyle(document.querySelector('{BADGE_SEL}'), '::after').fontSize")).replace("px", ""))
    check("при большом числе дней шрифт уменьшается (fs_4 < fs_0)",
          fs_4 < fs_0,
          True)

    # 6. Снятие скина
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'badge'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля без скина")

    check("скин снят: у profile-badge нет data-skin-badge",
          await p.ev("document.getElementById('profile-badge').getAttribute('data-skin-badge')"),
          None)
