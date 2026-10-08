"""Контраст скинов числами (WCAG): для каждого из восьми предметов (и для стартового вида слота, справочно) читаются вычисленные цвета пар из правил
docs/ARCHITECTURE.md; там, где различие ТОЛЬКО цветом, требуется контраст не ниже 3:1; пары, различимые формой, подписью или свечением, помечены явно
(режим shape/fx) и проверяются отдельно (разные формы, разные фильтры свечения). Таблица значений печатается при запуске сценария."""
from harness import check

BYPASS_CSP = True     # сценарий внедряет <style> и inline-стили для проверки значений CSS; сама CSP проверяется сценарием csp
NAME = "skin_contrast"
USERS = {"me": {"rate": 0}}
MIN = 3.0
SKINS = [("card_back", "back_midnight", "back_classic"), ("chip", "chip_ring", "chip_plain"), ("table", "table_blue", "table_green"),
         ("mine_icons", "mine_star", "mine_classic"), ("keno_ball", "keno_hex", "keno_round"), ("crash", "crash_neon", "crash_line"),
         ("avatar_frame", "frame_thin", "frame_plain"), ("badge", "badge_spade", "badge_none"),
         ("card_back", "back_leaves", "back_classic"), ("table", "table_autumn", "table_green"), ("mine_icons", "mine_acorn", "mine_classic"),     # части коллекции «Листопад»
         ("crash", "draft_crash", "crash_line"), ("mine_icons", "draft_mines", "mine_classic"), ("table", "draft_table", "table_green"),
         ("badge", "draft_badge", "badge_none"), ("table", "void_table", "table_green"), ("chip", "void_chip", "chip_plain"),
         ("badge", "void_badge", "badge_none"),
         ("card_back", "back_rug", "back_classic"), ("chip", "chip_cork", "chip_plain"), ("table", "table_oilcloth", "table_green"),
         ("mine_icons", "mine_beetle", "mine_classic"), ("keno_ball", "keno_lotto", "keno_round"), ("crash", "crash_barrel", "crash_line")]     # наборы за кристаллы: Черновик, Пустота, Дачный сезон

# (пара, цвет A, цвет B, режим): режим «>=3» требует контраст не ниже 3:1; «shape» только документирует, чем пара различается (текст)
PAIRS = {
    "table": [("красный и чёрный сектор", "red", "black", ">=3"), ("зелёный и чёрный сектор", "green", "black", ">=3"),
              ("красный и зелёный сектор", "red", "green", "shape: зелёный только единственный «0» с подписью, другое положение"),
              ("подпись на красном", "label", "red", ">=3"), ("подпись на чёрном", "label", "black", ">=3"), ("подпись на зелёном", "label", "green", ">=3"),
              ("сукно и красная клетка", "felt", "red", ">=3")],
    "card_back": [("рубашка и лицо карты", "back", "face", ">=3"), ("рамка рубашки и рубашка", "border", "back", ">=3"),
                  ("красная масть на лице", "suitRed", "face", ">=3"), ("чёрная масть на лице", "ink", "face", ">=3")],
    "chip": [("цифра и фон фишки", "text", "bg", ">=3"), ("кольцо 10 и фон", "ring10", "bg", ">=3"), ("кольцо 50 и фон", "ring50", "bg", ">=3"),
             ("кольцо 100 и фон", "ring100", "bg", ">=3"), ("кольцо 500 и фон", "ring500", "bg", ">=3"),
             ("выбранная и обычная фишка", "onBg", "bg", ">=3"), ("цифра на выбранной", "onText", "onBg", ">=3"),
             ("номиналы между собой", "ring10", "ring500", "shape: номинал напечатан цифрой, кольца различаются оттенком дополнительно")],
    "mine_icons": [("значок на открытой плитке", "safeText", "safeBg", ">=3"), ("значок на мине", "bombText", "bombBg", ">=3"),
                   ("рамка открытой и закрытая плитка", "safeBorder", "closedBg", ">=3"), ("рамка взорванной и мина", "hitBorder", "bombBg", ">=3"),
                   ("закрытая и открытая плитка", "closedBg", "safeBg", "shape: значок (звезда или самоцвет) и рамка у открытой, закрытая пустая"),
                   ("мина и закрытая плитка", "bombBg", "closedBg", "shape: колючий круг (форма значка) и красная заливка дополнительно"),
                   ("приглушённая мина и закрытая", "mutedText", "mutedBg", "shape: значок мины виден после конца партии, плитка не красная")],
    "keno_ball": [("цифра на обычном шарике", "text", "bg", ">=3"), ("цифра на выбранном", "selText", "selBg", ">=3"),
                  ("цифра на выпавшем", "drawnText", "bg", ">=3"), ("цифра на совпавшем", "hitText", "hitBg", ">=3"),
                  ("совпавший и обычный (заливка)", "hitBg", "bg", ">=3"),
                  ("выбран, выпал, совпал, мимо", "selBg", "bg", "fx: разные цвета контура и заливки шестиугольника (проверяется отдельно)")],
    "crash": [("обычная линия и фон", "line", "bg", ">=3"), ("линия выигрыша и фон", "win", "bg", ">=3"), ("линия краха и фон", "crash", "bg", ">=3"),
              ("выигрыш и крах", "win", "crash", ">=3"), ("подпись и фон", "label", "bg", ">=3")],
    "avatar_frame": [("внешнее кольцо рамки и фон", "ring", "page", ">=3")],
    "badge": [("значок и фон строки (обычная)", "badge", "row", ">=3"), ("значок и фон строки (своя)", "badge", "me", ">=3")],
}

JS = r"""
(() => {
  const parse = (c) => { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map((x) => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (fg, bg) => { const a = fg[3]; return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1]; };
  const L = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const page = parse(getComputedStyle(document.body).backgroundColor);
  const surface = [15, 15, 18, 1], surface2 = [23, 23, 27, 1];
  const mount = (slot, code, html) => {
    const host = document.createElement('div');
    host.setAttribute('data-skin-' + slot, code);
    Object.assign(host.style, {position: 'fixed', left: '-9999px', top: '0', width: '300px'});   // не cssText: CSP страницы запрещает inline-стили
    host.innerHTML = html;
    document.body.appendChild(host);
    return host;
  };
  const cs = (host, sel) => getComputedStyle(sel ? host.querySelector(sel) : host);
  const col = (host, sel, prop, base) => { const c = parse(cs(host, sel)[prop]); return c ? over(c, base || page) : null; };
  const colors = {};
  const measure = {
    table(h) {
      const v = (n) => parse(getComputedStyle(h).getPropertyValue(n).trim() || 'rgb(0,0,0)');
      const hexTo = (s) => { s = s.trim(); if (s[0] === '#') { const n = parseInt(s.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1]; } return parse(s); };
      return { red: col(h, '.cell.red', 'backgroundColor'), black: col(h, '.cell.black', 'backgroundColor'), green: col(h, '.cell.green', 'backgroundColor'),
               label: hexTo(getComputedStyle(h).getPropertyValue('--wheel-text')), felt: col(h, '.table', 'backgroundColor') };
    },
    card_back(h) {
      const a = getComputedStyle(h).getPropertyValue('--card-back-a').trim();
      const n = parseInt(a.slice(1), 16);
      return { back: [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1], border: col(h, '.bj-card.back', 'borderTopColor'), face: col(h, '.face', 'backgroundColor'), suitRed: col(h, '.red', 'color', parse(cs(h, '.red').backgroundColor)), ink: col(h, '.face', 'color', parse(cs(h, '.face').backgroundColor)) };
    },
    chip(h) {
      return { text: col(h, '.c1', 'color', parse(cs(h, '.c1').backgroundColor)), bg: col(h, '.c1', 'backgroundColor'), ring10: col(h, '.c10', 'borderTopColor'), ring50: col(h, '.c50', 'borderTopColor'),
               ring100: col(h, '.c100', 'borderTopColor'), ring500: col(h, '.c500', 'borderTopColor'), onBg: col(h, '.con', 'backgroundColor'), onText: col(h, '.con', 'color', parse(cs(h, '.con').backgroundColor)) };
    },
    mine_icons(h) {
      return { closedBg: col(h, '.m', 'backgroundColor'), safeBg: col(h, '.ms', 'backgroundColor'), safeText: col(h, '.ms', 'color', parse(cs(h, '.ms').backgroundColor)), safeBorder: col(h, '.ms', 'borderTopColor'),
               bombBg: col(h, '.mm', 'backgroundColor'), bombText: col(h, '.mm', 'color', parse(cs(h, '.mm').backgroundColor)), mutedBg: col(h, '.mu', 'backgroundColor'),
               mutedText: col(h, '.mu', 'color', parse(cs(h, '.mu').backgroundColor)), hitBorder: col(h, '.mh', 'borderTopColor') };
    },
    keno_ball(h) {
      // у шестиугольника заливка внутри SVG-фона (background-color прозрачный): берётся fill из фона состояния
      const fill = (cls, base) => { const m = /fill=.%23([0-9a-f]{6})/i.exec(cs(h, '.' + cls + ' span').backgroundImage); if (!m) return col(h, '.' + cls + ' span', 'backgroundColor', base); const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1]; };
      const sp = (cls, prop, base) => col(h, '.' + cls + ' span', prop, base);
      const bg = fill('k', surface);
      return { bg, text: sp('k', 'color', bg), selBg: fill('ks', surface), selText: sp('ks', 'color', fill('ks', surface)),
               drawnText: sp('kd', 'color', fill('kd', surface)), hitBg: fill('kh', surface), hitText: sp('kh', 'color', fill('kh', surface)) };
    },
    crash(h) {
      const bg = col(h, '.cw', 'backgroundColor', page);
      const base = col(h, '.c0', 'backgroundColor', page);
      return { bg: base, line: col(h, '.c0 .cr-curve', 'stroke'), win: col(h, '.cw .cr-curve', 'stroke'), crash: col(h, '.cc .cr-curve', 'stroke'), label: col(h, '.c0 .cr-label', 'color') };
    },
    avatar_frame(h) {
      const sh = cs(h, '.avatar').boxShadow;
      const first = sh && sh !== 'none' ? parse(sh.match(/rgba?\([^)]+\)/g).slice(-1)[0]) : null;
      return { ring: first, page };
    },
    badge(h) {
      return { badge: col(h, '.rating-badge', 'color'), row: surface, me: surface2 };
    }
  };
  const HTML = {
    table: '<div class="table"><span class="cell red"></span><span class="cell black"></span><span class="cell green"></span></div>',
    card_back: '<div class="bj-card back"></div><div class="bj-card face"></div><div class="bj-card red"></div>',
    chip: '<span class="chip c1">1</span><span class="chip c10" data-amount="10">1</span><span class="chip c50" data-amount="50">1</span><span class="chip c100" data-amount="100">1</span><span class="chip c500" data-amount="500">1</span><span class="chip con" aria-pressed="true">1</span>',
    mine_icons: '<div style="display:grid;grid-template-columns:repeat(5,44px);gap:6px"><div class="mines-cell m"></div><div class="mines-cell safe ms"></div><div class="mines-cell mine mm"></div><div class="mines-cell mine muted mu"></div><div class="mines-cell mine hit mh"></div></div>',
    keno_ball: '<div style="display:grid;grid-template-columns:repeat(5,44px);gap:6px"><div class="keno-ball k"><span></span></div><div class="keno-ball sel ks"><span></span></div><div class="keno-ball drawn kd"><span></span></div><div class="keno-ball hit kh"><span></span></div><div class="keno-ball miss km"><span></span></div></div>',
    crash: '<div class="cr-chart c0"><svg class="cr-svg"><path class="cr-curve"></path></svg><div class="cr-label"></div></div><div class="cr-chart cw" data-tone="win"><svg class="cr-svg"><path class="cr-curve"></path></svg></div><div class="cr-chart cc" data-tone="crash"><svg class="cr-svg"><path class="cr-curve"></path></svg></div>',
    avatar_frame: '<span class="avatar"></span>',
    badge: '<span class="rating-badge"></span>'
  };
  const out = {};
  for (const [slot, code] of %SPECS%) {
    const h = mount(slot, code, HTML[slot]);
    const raw = measure[slot](h);
    const res = {};
    for (const [k, v] of Object.entries(raw)) res[k] = v ? v.slice(0, 3).map((x) => Math.round(x)) : null;
    const extra = {};
    if (slot === 'keno_ball') {
      const f = (cls) => getComputedStyle(h.querySelector('.' + cls + ' span')).backgroundImage;
      extra.images = ['k', 'ks', 'kd', 'kh'].map(f);
      extra.shape = (/points=.([0-9. ,]+)/.exec(extra.images[0]) || [])[1] || '';
      extra.filters = ['k', 'ks', 'kd', 'kh'].map((cls) => getComputedStyle(h.querySelector('.' + cls)).filter);
      extra.clips = ['k', 'ks', 'kd', 'kh'].map((cls) => getComputedStyle(h.querySelector('.' + cls + ' span')).clipPath);
    }
    if (slot === 'crash') extra.filters = ['.c0', '.cw', '.cc'].map((s) => getComputedStyle(h.querySelector(s + ' .cr-curve')).filter);
    if (slot === 'avatar_frame') { const r = h.querySelector('.avatar').getBoundingClientRect(); extra.size = [r.width, r.height]; extra.shadows = getComputedStyle(h.querySelector('.avatar')).boxShadow; }
    extra.sizes = [...h.querySelectorAll('*')].map((e) => { const r = e.getBoundingClientRect(); return [Math.round(r.width * 100) / 100, Math.round(r.height * 100) / 100]; });
    out[slot + ':' + code] = { colors: res, extra };
    h.remove();
  }
  out.ratios = null;
  return out;
})()
"""


def lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def ratio(a, b):
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


async def run(w):
    import json
    p = w.page
    specs = []
    for slot, skin, start in SKINS:
        specs += [[slot, skin], [slot, start]]
    data = await p.ev(JS.replace("%SPECS%", json.dumps(specs)))
    lines = ["| слот | скин | пара | контраст | чем различаются |", "|---|---|---|---|---|"]
    failed = []
    for slot, skin, start in SKINS:
        for code in (skin, start):
            entry = data["%s:%s" % (slot, code)]
            colors = entry["colors"]
            for name, a, b, mode in PAIRS[slot]:
                if colors.get(a) is None or colors.get(b) is None:
                    if code != skin:
                        continue                    # у стартового вида слота (например, «Без рамки») нет цвета: пара не применима
                    failed.append("%s %s: нет цвета для пары «%s» (%s, %s)" % (slot, code, name, a, b))
                    continue
                r = ratio(colors[a], colors[b])
                if mode == ">=3":
                    ok = r >= MIN
                    lines.append("| %s | %s | %s | %.2f | цвет (>= 3:1) %s |" % (slot, code, name, r, "ok" if ok else "НИЖЕ ПОРОГА"))
                    if code == skin and not ok:
                        failed.append("%s %s: «%s» контраст %.2f < %.1f" % (slot, code, name, r, MIN))
                else:
                    lines.append("| %s | %s | %s | %.2f | %s |" % (slot, code, name, r, mode))
    # скин не меняет размеры: все элементы образца того же размера, что у стартового предмета слота
    for slot, skin, start in SKINS:
        check("размеры не меняются скином %s" % skin, data["%s:%s" % (slot, skin)]["extra"]["sizes"], data["%s:%s" % (slot, start)]["extra"]["sizes"])
    # проверки «не только цветом»
    k = data["keno_ball:keno_hex"]["extra"]
    check("кено hex: форма шестиугольника (6 вершин в фоне-SVG)", len(k["shape"].split()), 6)
    check("кено hex: выбран, выпал, совпал имеют разные картинки контура и заливки", len(set(k["images"][1:])), 3)
    check("кено hex: картинки отличаются от обычного шарика", all(f != k["images"][0] for f in k["images"][1:]), True)
    check("кено hex: без filter и clip-path (дёшево для WebKit)", [set(k["filters"]), set(k["clips"])], [{"none"}, {"none"}])
    c = data["crash:crash_neon"]["extra"]
    check("краш neon: линия светится, у выигрыша и краха разное свечение", [f != "none" for f in c["filters"]] + [len(set(c["filters"])) == 3], [True, True, True, True])
    a = data["avatar_frame:frame_thin"]["extra"]
    check("рамка: двойная (два кольца), размер аватара прежний 44x44", [a["shadows"].count("rgb(203, 214, 230)"), a["size"]], [2, [44, 44]])
    for row in lines:
        print(row)
    assert not failed, "контраст ниже порога или нет цвета: %s" % failed
