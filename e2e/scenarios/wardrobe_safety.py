"""Безопасность вывода: значения в ответах сервера (теги в названиях и описаниях, неизвестные коды, чужие слоты) не попадают в DOM как разметка и
не создают элементов и атрибутов; неизвестные коды предметов и публичных слотов игнорируются. Ответы подменяются в странице (боевой код не меняется)."""
from harness import check

NAME = "wardrobe_safety"
USERS = {"me": {"rate": 0}, "bob": {"balance": 5000, "rate": 0}}
EVIL = '<img src=x onerror="window.__pwn=1"><b>жирный</b>'

OVERRIDE = """
(() => {
  const orig = window.fetch;
  const json = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { 'Content-Type': 'application/json' } });
  const evil = %s;
  window.fetch = async (u, o) => {
    const url = String(u);
    if (url.includes('/api/cosmetics/catalog')) {
      return json({ slots: [], items: [
        { code: 'back_classic', slot: 'card_back', name: evil, description: evil, rarity: 'starter', price_stars: 0, starter: true, available: true },
        { code: 'evil_code', slot: 'card_back', name: 'Чужой', description: '', rarity: 'common', price_stars: 1, starter: false, available: true },
        { code: 'back_midnight', slot: 'chip', name: 'Не тот слот', description: '', rarity: 'common', price_stars: 1, starter: false, available: true },
        { code: '__proto__', slot: 'card_back', name: 'Прото', description: '', rarity: 'common', price_stars: 1, starter: false, available: true },
        { code: 'back_midnight', slot: 'card_back', name: 'Полночь', description: '<script>window.__pwn=2</script>', rarity: 'common', price_stars: 50, starter: false, available: true } ] });
    }
    if (url.includes('/api/cosmetics/mine')) {
      return json({ owned: [{ code: 'back_midnight', source: 'owner_gift', acquired_at: 1 }, { code: evil, source: 'x', acquired_at: 2 }], equipped: { card_back: evil, chip: 'chip_ring', table: ['x'] }, show_in_rating: true });
    }
    if (url.includes('/api/chat/top')) {
      const row = (rank, name, me, cos) => ({ rank, name, balance: 1000 - rank, is_me: me, staked: 0, level: 1, member_ref: me ? null : '0'.repeat(32), cosmetics: cos });
      return json({ scope: 'chat', chat_staked: 0, me: { rank: 1, balance: 1000, total: 3, staked: 0, level: 1 }, top: [
        row(1, 'me', true, {}),
        row(2, evil, false, { avatar_frame: '<script>window.__pwn=3</script>', badge: 'x" onmouseover="window.__pwn=4' }),
        row(3, 'bob', false, { avatar_frame: 'frame_crown_unknown', badge: '__proto__', chip: 'chip_ring', table: 'table_blue' }) ] });
    }
    return orig(u, o);
  };
  return true;
})()
"""


async def run(w):
    import json
    p = w.page
    await p.ev(OVERRIDE % json.dumps(EVIL))
    await p.tap(".tab[data-tab=profile]")
    await p.wait("!document.getElementById('profile-data').hidden", 15, "профиль")
    await p.tap("#wardrobe-open")
    await p.wait("!document.getElementById('wd-sheet').hidden && document.querySelectorAll('#wd-grid .wd-card').length > 0", 10, "гардероб")
    check("в карточках нет разметки из ответа сервера", await p.ev("[document.querySelectorAll('#wd-grid img, #wd-grid b, #wd-grid script').length, window.__pwn || 0]"), [0, 0])
    check("название выведено текстом", await p.ev("document.querySelector('#wd-grid .wd-card .wd-name').textContent"), EVIL[:40])
    check("неизвестные коды и предметы чужого слота пропущены", await p.ev("[...document.querySelectorAll('#wd-grid .wd-card')].map(c => c.dataset.code)"), ["back_classic", "back_midnight"])
    await p.tap("#wd-grid .wd-card:nth-child(1)")
    await p.wait("!document.getElementById('wd-prev-sheet').hidden", 5, "предпросмотр")
    check("описание в предпросмотре текстом, без элементов", await p.ev("[document.querySelectorAll('#wd-prev-sheet img, #wd-prev-sheet b, #wd-prev-sheet script').length, document.getElementById('wd-prev-desc').textContent === %s, window.__pwn || 0]" % json.dumps(EVIL)), [0, True, 0])
    await p.tap("#wd-prev-close")
    await p.tap("#wd-back")
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length === 3", 15, "рейтинг")
    check("в рейтинге нет разметки из имён и публичных слотов", await p.ev("[document.querySelectorAll('#rating-list img, #rating-list b, #rating-list script').length, window.__pwn || 0]"), [0, 0])
    check("неизвестные рамки и значки игнорируются, атрибутов нет", await p.ev(
        "[...document.querySelectorAll('#rating-list li')].map(li => [li.querySelector('.avatar').getAttribute('data-skin-avatar_frame'), !!li.querySelector('.rating-badge'), li.outerHTML.includes('onmouseover')])"),
        [[None, False, False], [None, False, False], [None, False, False]])
    check("приватные слоты чужих не применены", await p.ev("document.documentElement.outerHTML.includes('chip_ring') || document.documentElement.outerHTML.includes('table_blue')"), False)
    check("страница цела", await p.ev("typeof window.__pwn"), "undefined")
