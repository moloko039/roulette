"""Награды «Основателя» (DESIGN.md раздел 6): значок ref_scout («Камень»),
рамка ref_beacon («Колонна») и рамка ref_arch («Арка»).
Номер основателя отображается на плитке значка и замковом камне арки через data-founder-no."""
import time

from harness import check

NAME = "skin_founder"
USERS = {"me": {"rate": 0}}


async def run(w):
    p = w.page
    uid = w.users["me"].id
    now = int(time.time())

    # 1. Игрок получает предметы и номер основателя, надевает значок и рамку «Арка»
    for code in ("ref_scout", "ref_beacon", "ref_arch"):
        w.sql("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (?, ?, 'referral', ?)", (uid, code, now))
    w.sql("INSERT INTO founder_numbers (telegram_id, no) VALUES (?, 37)", (uid,))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'badge', 'ref_scout')", (uid,))
    w.sql("INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'avatar_frame', 'ref_arch')", (uid,))

    # 2. После загрузки профиля у аватара профиля атрибуты data-skin-avatar_frame="ref_arch" и data-founder-no="37"
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль открыт")

    av_frame = await p.ev("document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame')")
    av_no = await p.ev("document.getElementById('profile-avatar').getAttribute('data-founder-no')")
    check("аватар профиля: data-skin-avatar_frame='ref_arch'", av_frame, "ref_arch")
    check("аватар профиля: data-founder-no='37'", av_no, "37")

    av_after = await p.ev("getComputedStyle(document.getElementById('profile-avatar'), '::after').content")
    check("вычисленный content ::after у аватара содержит 37", "37" in av_after, True)

    # 3. У значка в списках data-founder-no="37", content ::after содержит 37
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 15, "рейтинг загружен")

    badge_no = await p.ev("document.querySelector('#rating-me .rating-badge').getAttribute('data-founder-no')")
    check("значок в рейтинге: data-founder-no='37'", badge_no, "37")

    badge_after = await p.ev("getComputedStyle(document.querySelector('#rating-me .rating-badge'), '::after').content")
    check("вычисленный content ::after у значка содержит 37", "37" in badge_after, True)

    # 4. После смены рамки на ref_beacon атрибута data-founder-no у аватара нет
    w.sql("UPDATE cosmetic_equipped SET item_code = 'ref_beacon' WHERE telegram_id = ? AND slot = 'avatar_frame'", (uid,))
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль открыт после смены рамки")

    check("аватар профиля: data-skin-avatar_frame='ref_beacon'",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-skin-avatar_frame')"), "ref_beacon")
    check("после смены рамки на ref_beacon атрибута data-founder-no у аватара нет",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-founder-no')"), None)

    # 5. Без номера (удалить строку founder_numbers) атрибутов нет
    w.sql("DELETE FROM founder_numbers WHERE telegram_id = ?", (uid,))
    w.sql("UPDATE cosmetic_equipped SET item_code = 'ref_arch' WHERE telegram_id = ? AND slot = 'avatar_frame'", (uid,))
    await w.reload()
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль открыт без номера")

    check("без номера у аватара ref_arch нет data-founder-no",
          await p.ev("document.getElementById('profile-avatar').getAttribute('data-founder-no')"), None)

    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 1", 15, "рейтинг загружен без номера")
    check("без номера у значка ref_scout нет data-founder-no",
          await p.ev("document.querySelector('#rating-me .rating-badge').getAttribute('data-founder-no')"), None)
