"""Сцена скина рамки аватара «Патина» (skins/frame_patina.css, коллекция «Патина», DESIGN.md раздел 8).
Проверяется:
1. Наличие мета-тега скина <meta name="skin-css" data-code="frame_patina"> в index.html;
2. На стадии 3 (стаж аккаунта >= 180 дней):
   - у аватара профиля и в рейтинге выставлены data-skin-avatar_frame="frame_patina" и data-patina-avatar_frame="3";
   - применён блеск серебра (тонкий диагональный linear-gradient с прозрачностью);
   - края натёрты (radial-gradient светлее там, где блеск);
   - аватар виден и не перекрыт псевдоэлементом (elementFromPoint в центре возвращает сам .avatar);
   - инициалы аватара видны;
3. На стадии 0 (стаж аккаунта < 30 дней):
   - data-patina-avatar_frame="0";
   - аватар без блеска (linear-gradient отсутствует);
   - аватар виден и не перекрыт;
4. На стадии 4 (стаж аккаунта >= 365 дней):
   - data-patina-avatar_frame="4";
   - вся рамка с мягкой зеленоватой патиной через цвет из палитры (#77806f);
5. Снятие скина: у аватара нет data-skin-avatar_frame."""
import asyncio
import time

from harness import check

NAME = "skin_scene_frame_patina"
USERS = {"me": {"rate": 0}}
META = "document.querySelectorAll('meta[name=\"skin-css\"][data-code=\"frame_patina\"]').length"
AVATAR_AFTER_BG = "getComputedStyle(document.getElementById('profile-avatar'), '::after').backgroundImage"


async def ensure_skin_css(p):
    """Подключает CSS скина по мета-тегу, если он ещё не подключён."""
    await p.ev("""(() => {
        const m = document.querySelector('meta[name="skin-css"][data-code="frame_patina"]');
        if (m && !document.querySelector('link[href*="skins/frame_patina.css"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = m.content;
            document.head.appendChild(link);
        }
    })()""")


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 1. Проверяем наличие мета-тега скина в index.html
    check("в index.html есть meta name=skin-css data-code=frame_patina",
          await p.ev(META),
          1)

    # 2. Стадия 3: 200 дней стажа аккаунта (пороги рамки: 30, 90, 180, 365)
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now - 200 * 86400, uid))
    w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, 'frame_patina', 'free', NULL, ?)", (uid, now))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'avatar_frame', 'frame_patina')", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    # Открываем профиль
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля")

    # Проверка атрибутов скина и стадии на аватаре профиля
    check("у аватара профиля выставлен data-skin-avatar_frame='frame_patina'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame')"),
          "frame_patina")
    check("стаж 200 дней даёт стадию data-patina-avatar_frame='3'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-patina-avatar_frame')"),
          "3")

    # Проверка наличия блеска серебра и натёртости краёв на стадии 3
    after_bg_3 = await p.ev(AVATAR_AFTER_BG)
    check("на стадии 3 есть блеск серебра (linear-gradient)",
          "linear-gradient" in after_bg_3,
          True)
    check("на стадии 3 края светлее там, где блеск (radial-gradient)",
          "radial-gradient" in after_bg_3,
          True)

    # Проверка, что аватар виден и не перекрыт (elementFromPoint в центре)
    hit_profile = await p.ev("""(() => {
        const el = document.getElementById('profile-avatar');
        const r = el.getBoundingClientRect();
        const topEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return topEl === el;
    })()""")
    check("аватар профиля виден и не перекрыт (elementFromPoint в центре)",
          hit_profile,
          True)

    # Проверка, что инициалы видны
    init_text = await p.ev("document.getElementById('profile-avatar').textContent.trim()")
    check("инициалы аватара видны",
          len(init_text) > 0,
          True)

    # Проверка аватара в рейтинге
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 10, "экран рейтинга")
    check("у своего аватара в рейтинге data-skin-avatar_frame='frame_patina'",
          await p.ev("document.querySelector('#rating-me .avatar').getAttribute('data-skin-avatar_frame')"),
          "frame_patina")
    check("у своего аватара в рейтинге data-patina-avatar_frame='3'",
          await p.ev("document.querySelector('#rating-me .avatar').getAttribute('data-patina-avatar_frame')"),
          "3")

    hit_rating = await p.ev("""(() => {
        const el = document.querySelector('#rating-me .avatar');
        const r = el.getBoundingClientRect();
        const topEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return topEl === el;
    })()""")
    check("аватар в рейтинге виден и не перекрыт (elementFromPoint в центре)",
          hit_rating,
          True)

    # 3. Стадия 0: стаж 0 дней (< 30 дней) — чистый цвет, без блеска
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now, uid))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля (стадия 0)")

    check("стаж 0 дней даёт стадию data-patina-avatar_frame='0'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-patina-avatar_frame')"),
          "0")

    after_bg_0 = await p.ev(AVATAR_AFTER_BG)
    check("стадия 0: аватар без блеска (linear-gradient отсутствует)",
          "linear-gradient" not in after_bg_0,
          True)
    check("стадия 0: аватар без натёртостей (radial-gradient отсутствует)",
          "radial-gradient" not in after_bg_0,
          True)

    hit_profile_0 = await p.ev("""(() => {
        const el = document.getElementById('profile-avatar');
        const r = el.getBoundingClientRect();
        const topEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return topEl === el;
    })()""")
    check("стадия 0: аватар виден и не перекрыт (elementFromPoint в центре)",
          hit_profile_0,
          True)

    # 4. Стадия 4: стаж 400 дней (>= 365 дней) — мягкая зеленоватая патина (#77806f)
    w.sql("UPDATE players SET created_at = ? WHERE telegram_id = ?", (now - 400 * 86400, uid))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")
    await ensure_skin_css(p)

    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля (стадия 4)")

    check("стаж 400 дней даёт стадию data-patina-avatar_frame='4'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-patina-avatar_frame')"),
          "4")

    after_bg_4 = await p.ev(AVATAR_AFTER_BG)
    check("на стадии 4 есть блеск серебра (linear-gradient)",
          "linear-gradient" in after_bg_4,
          True)
    check("на стадии 4 вся рамка с мягкой зеленоватой патиной через цвет палитры (119, 128, 111)",
          "119, 128, 111" in after_bg_4 or "77806f" in after_bg_4,
          True)

    # 5. Снятие скина
    w.sql("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = 'avatar_frame'", (uid,))
    await w.reload()
    await p.ev("skinFpsStop(); skinFpsStart = () => {}")

    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 10, "экран профиля без скина")

    check("скин снят: у аватара профиля нет data-skin-avatar_frame",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame')"),
          None)
