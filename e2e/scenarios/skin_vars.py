"""Основа темизации: CSS-переменные скинов реально управляют ВСЕМ внешним видом слотов. В страницу добавляется ВРЕМЕННЫЙ стиль
(только в этом сценарии) с яркими «страшными» цветами для каждой переменной; проверяются пиксели колеса (canvas) и вычисленные цвета
рубашек, фишек, плиток мин, шариков кено, линии краша и стола; затем стиль убирается и вид возвращается к исходному. Размеры не меняются."""
import json
import os
import re

import harness
from harness import check

NAME = "skin_vars"
USERS = {"me": {"rate": 0}}

# цвет каждой переменной скинов (все различны); fallback-имя переменной без цвета в этом словаре падает на проверке полноты
SCARY = {
    "--card-back-a": "#0a0b0c", "--card-back-b": "#0d0e0f", "--card-back-border": "#102030", "--card-face": "#112233", "--card-ink": "#223344", "--card-red": "#334455",
    "--chip-bg": "#445566", "--chip-text": "#556677", "--chip-ring": "#667788", "--chip-ring-10": "#778899", "--chip-ring-50": "#8899aa", "--chip-ring-100": "#99aabb",
    "--chip-ring-500": "#aabbcc", "--chip-on-bg": "#bbccdd", "--chip-on-text": "#ccddee", "--chip-on-ring": "#ddeeff",
    "--table-red": "#00ffff", "--table-black": "#ffff00", "--table-green": "#ff00ff",
    "--wheel-rim-a": "#123450", "--wheel-rim-b": "#234560", "--wheel-rim-c": "#345670", "--wheel-sector-line": "#456780", "--wheel-text": "#abcdef",
    "--wheel-edge": "#fedcba", "--wheel-hub": "#13579b", "--wheel-hub-line": "#2468ac",
    "--mines-cell-bg": "#ff8800", "--mines-cell-border": "#88ff00", "--mines-cell-text": "#0088ff", "--mines-cell-active-bg": "#ff0088", "--mines-safe-bg": "#00ff88",
    "--mines-safe-border": "#8800ff", "--mines-bomb-bg": "#ee1100", "--mines-bomb-text": "#00ee11", "--mines-muted-bg": "#1100ee", "--mines-muted-text": "#eeee11",
    "--mines-hit-border": "#11eeee", "--mines-opening-a": "#ee11ee", "--mines-opening-b": "#aa5500",
    "--keno-bg": "#5500aa", "--keno-border": "#00aa55", "--keno-text": "#aa0055", "--keno-active-bg": "#55aa00", "--keno-sel-text": "#0055aa", "--keno-sel-bg": "#aa5555",
    "--keno-sel-border": "#55aa55", "--keno-drawn-text": "#5555aa", "--keno-hit-text": "#a0a0f0", "--keno-hit-bg": "#f0a0a0",
    "--cr-bg": "#a0f0a0", "--cr-border": "#c0c000", "--cr-axis": "#00c0c0", "--cr-line": "#c000c0", "--cr-win": "#c03000", "--cr-crash": "#0030c0", "--cr-label": "#30c000",
    "--cr-label-strong": "#00c030", "--cr-history-text": "#3000c0", "--cr-history-border": "#c00030",
}
# переменные-составные (тень, градиент, прозрачности): переопределяются целиком и проверяются отдельно по содержимому
SCARY_RAW = {
    "--card-back-bj": "repeating-linear-gradient(45deg, #fe0001, #fe0001 4px, #01fe02 4px, #01fe02 8px)",
    "--card-back-hl": "repeating-linear-gradient(45deg, #fd0001, #fd0001 6px, #01fd02 6px, #01fd02 12px)",
    "--chip-on-glow": "0 0 9px #fc0001", "--wheel-sector-line-x": "",
    "--mines-safe-glow": "#fb0001", "--mines-hit-ring": "#fa0001",
    "--keno-sel-glow": "0 0 7px #f90001", "--keno-drawn-ring": "0 0 0 3px #f80001", "--keno-hit-glow": "0 0 8px #f70001", "--keno-miss-glow": "0 0 5px #f60001",
    "--cr-win-soft": "#f50001", "--cr-crash-soft": "#f40001",
}
SCARY_RAW.pop("--wheel-sector-line-x")


def rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


PROBES = """
(() => {
  const host = document.createElement('div');
  host.id = 'e2e-probes';
  host.style.cssText = 'position:fixed;left:-9999px;top:0;';
  host.innerHTML = `
    <button class="chip" id="p-chip" data-amount="100">1</button>
    <button class="chip" id="p-chip-on" aria-pressed="true">1</button>
    <div class="bj-card back" id="p-bjback"></div><div class="hl-card back" id="p-hlback"></div>
    <div class="bj-card" id="p-face"></div><div class="bj-card red" id="p-red"></div>
    <div class="mines-cell" id="p-m"></div><div class="mines-cell safe" id="p-ms"></div><div class="mines-cell mine" id="p-mm"></div><div class="mines-cell mine muted" id="p-mmu"></div>
    <div class="mines-cell mine hit" id="p-mh"></div>
    <div class="keno-ball" id="p-k"><span></span></div><div class="keno-ball sel" id="p-ks"><span></span></div><div class="keno-ball drawn" id="p-kd"><span></span></div>
    <div class="keno-ball hit" id="p-kh"><span></span></div><div class="keno-ball miss" id="p-km"><span></span></div>
    <div class="cr-chart" id="p-c"><svg class="cr-svg"><path class="cr-curve"></path><path class="cr-axis"></path></svg><div class="cr-label"></div></div>
    <div class="cr-chart" data-tone="win" id="p-cw"><svg class="cr-svg"><path class="cr-curve"></path></svg></div>
    <div class="cr-chart" data-tone="crash" id="p-cc"><svg class="cr-svg"><path class="cr-curve"></path></svg></div>
    <div class="cell red" id="p-tr"></div><div class="cell black" id="p-tb"></div><div class="cell green" id="p-tg"></div>
    <div class="result-number green" id="p-rg"></div>`;
  document.body.appendChild(host);
  return true;
})()
"""

READ = """
(() => {
  const g = (id, prop, sel) => { const e = document.getElementById(id); const t = sel ? e.querySelector(sel) : e; return getComputedStyle(t)[prop]; };
  const size = (id) => { const r = document.getElementById(id).getBoundingClientRect(); return [r.width, r.height]; };
  return {
    chipBg: g('p-chip', 'backgroundColor'), chipRing100: g('p-chip', 'borderTopColor'), chipText: g('p-chip', 'color'), chipSize: size('p-chip'),
    chipOnBg: g('p-chip-on', 'backgroundColor'), chipOnRing: g('p-chip-on', 'borderTopColor'), chipOnText: g('p-chip-on', 'color'), chipOnGlow: g('p-chip-on', 'boxShadow'),
    bjBack: g('p-bjback', 'backgroundImage'), bjBackBorder: g('p-bjback', 'borderTopColor'), hlBack: g('p-hlback', 'backgroundImage'), bjSize: size('p-bjback'), hlSize: size('p-hlback'),
    face: g('p-face', 'backgroundColor'), ink: g('p-face', 'color'), red: g('p-red', 'color'),
    mBg: g('p-m', 'backgroundColor'), mBorder: g('p-m', 'borderTopColor'), mText: g('p-m', 'color'), mSafeBg: g('p-ms', 'backgroundColor'), mSafeBorder: g('p-ms', 'borderTopColor'),
    mBomb: g('p-mm', 'backgroundColor'), mBombText: g('p-mm', 'color'), mMuted: g('p-mmu', 'backgroundColor'), mMutedText: g('p-mmu', 'color'),
    mHitBorder: g('p-mh', 'borderTopColor'), mHitRing: g('p-mh', 'boxShadow'),
    kBg: g('p-k', 'backgroundColor', 'span'), kBorder: g('p-k', 'borderTopColor', 'span'), kText: g('p-k', 'color', 'span'),
    kSel: [g('p-ks', 'color', 'span'), g('p-ks', 'backgroundColor', 'span'), g('p-ks', 'borderTopColor', 'span'), g('p-ks', 'boxShadow', 'span')],
    kDrawn: [g('p-kd', 'color', 'span'), g('p-kd', 'boxShadow', 'span')], kHit: [g('p-kh', 'color', 'span'), g('p-kh', 'backgroundColor', 'span'), g('p-kh', 'boxShadow', 'span')],
    kMiss: g('p-km', 'boxShadow', 'span'),
    cBg: g('p-c', 'backgroundColor'), cBorder: g('p-c', 'borderTopColor'), cAxis: g('p-c', 'stroke', '.cr-axis'), cLine: g('p-c', 'stroke', '.cr-curve'), cLabel: g('p-c', 'color', '.cr-label'),
    cWin: [g('p-cw', 'stroke', '.cr-curve'), g('p-cw', 'borderTopColor'), g('p-cw', 'backgroundImage')], cCrash: [g('p-cc', 'stroke', '.cr-curve'), g('p-cc', 'borderTopColor'), g('p-cc', 'backgroundImage')],
    tr: g('p-tr', 'backgroundColor'), tb: g('p-tb', 'backgroundColor'), tg: g('p-tg', 'backgroundColor'), rg: g('p-rg', 'backgroundColor'),
    wheel: document.getElementById('wheel').toDataURL(),
    wheelColors: (() => { const c = document.getElementById('wheel'); const t = document.createElement('canvas'); t.width = c.width; t.height = c.height;
      const tc = t.getContext('2d', { willReadFrequently: true }); tc.drawImage(c, 0, 0); const d = tc.getImageData(0, 0, c.width, c.height).data; const seen = {};
      for (let i = 0; i < d.length; i += 4) { if (d[i + 3] === 255) { const k = d[i] + ',' + d[i + 1] + ',' + d[i + 2]; seen[k] = (seen[k] || 0) + 1; } } return seen; })()
  };
})()
"""


async def run(w):
    p = w.page
    css = open(os.path.join(harness.CLIENT_ROOT, "style.css"), encoding="utf-8").read()
    js = open(os.path.join(harness.CLIENT_ROOT, "script.js"), encoding="utf-8").read()
    block = css[css.index("переменные скинов (косметика)"):css.index("* {\n  box-sizing")]
    declared = re.findall(r"^\s*(--(?:card|chip|table|wheel|mines|keno|cr)-[a-z0-9-]+):", block, re.M)
    check("переменных скинов объявлено не меньше 60", len(declared) >= 60, True)
    covered = set(SCARY) | set(SCARY_RAW)
    # каждая объявленная переменная переопределяется сценарием и читается правилами или скриптом (мёртвых переменных нет)
    # (--wheel-* и --table-* читает drawWheel через getComputedStyle, остальные правила через var())
    missing = sorted(set(declared) - covered)
    assert not missing, "сценарий не переопределяет: %s" % missing
    unused = [v for v in declared if ("var(%s" % v) not in css[css.index("* {\n  box-sizing"):] and ("'%s'" % v) not in js and ("var(%s" % v) not in block]
    assert not unused, "объявлены, но не читаются: %s" % unused

    await p.ev("document.getElementById('wheel').width")
    await p.ev(PROBES)
    base = await p.ev(READ)
    check("размер фишки 44x44, карты 46x64 и 118x164", [base["chipSize"], base["bjSize"], base["hlSize"]], [[44, 44], [46, 64], [118, 164]])

    override = "".join("%s: %s !important;" % (k, v) for k, v in dict(SCARY, **SCARY_RAW).items())
    await p.ev("(() => { const s = document.createElement('style'); s.id = 'e2e-skin'; s.textContent = ':root { %s }'; document.head.appendChild(s); drawWheel(); return true; })()" % override)
    await p.ev("E.sleep(700)")      # переходы цвета (0.15 с) успевают закончиться
    got = await p.ev(READ)

    def eq(name, key, color):
        check(name, got[key], rgb(color))
    eq("фишка: фон", "chipBg", SCARY["--chip-bg"]); eq("фишка: кольцо номинала 100", "chipRing100", SCARY["--chip-ring-100"]); eq("фишка: текст", "chipText", SCARY["--chip-text"])
    eq("фишка выбрана: фон", "chipOnBg", SCARY["--chip-on-bg"]); eq("фишка выбрана: кольцо", "chipOnRing", SCARY["--chip-on-ring"]); eq("фишка выбрана: текст", "chipOnText", SCARY["--chip-on-text"])
    assert "rgb(252, 0, 1)" in got["chipOnGlow"], got["chipOnGlow"]
    check("размер фишки не изменился", got["chipSize"], base["chipSize"])
    assert "rgb(254, 0, 1)" in got["bjBack"] and "rgb(1, 254, 2)" in got["bjBack"], got["bjBack"]
    assert "rgb(253, 0, 1)" in got["hlBack"] and "rgb(1, 253, 2)" in got["hlBack"], got["hlBack"]
    eq("рубашка: рамка", "bjBackBorder", SCARY["--card-back-border"]); eq("лицо карты", "face", SCARY["--card-face"]); eq("чернила карты", "ink", SCARY["--card-ink"]); eq("красная масть", "red", SCARY["--card-red"])
    check("размеры карт не изменились", [got["bjSize"], got["hlSize"]], [base["bjSize"], base["hlSize"]])
    eq("плитка мин: фон", "mBg", SCARY["--mines-cell-bg"]); eq("плитка мин: рамка", "mBorder", SCARY["--mines-cell-border"]); eq("плитка мин: значок", "mText", SCARY["--mines-cell-text"])
    eq("открытая плитка: фон", "mSafeBg", SCARY["--mines-safe-bg"]); eq("открытая плитка: рамка", "mSafeBorder", SCARY["--mines-safe-border"])
    eq("мина: фон", "mBomb", SCARY["--mines-bomb-bg"]); eq("мина: значок", "mBombText", SCARY["--mines-bomb-text"])
    eq("мина приглушённая: фон", "mMuted", SCARY["--mines-muted-bg"]); eq("мина приглушённая: значок", "mMutedText", SCARY["--mines-muted-text"])
    eq("взрыв: рамка", "mHitBorder", SCARY["--mines-hit-border"]); assert "rgb(250, 0, 1)" in got["mHitRing"], got["mHitRing"]
    eq("шарик кено: фон", "kBg", SCARY["--keno-bg"]); eq("шарик кено: рамка", "kBorder", SCARY["--keno-border"]); eq("шарик кено: цифра", "kText", SCARY["--keno-text"])
    check("выбран: цифра, фон, рамка", got["kSel"][:3], [rgb(SCARY["--keno-sel-text"]), rgb(SCARY["--keno-sel-bg"]), rgb(SCARY["--keno-sel-border"])])
    assert "rgb(249, 0, 1)" in got["kSel"][3], got["kSel"]
    check("выпал: цифра", got["kDrawn"][0], rgb(SCARY["--keno-drawn-text"])); assert "rgb(248, 0, 1)" in got["kDrawn"][1], got["kDrawn"]
    check("совпал: цифра и фон", got["kHit"][:2], [rgb(SCARY["--keno-hit-text"]), rgb(SCARY["--keno-hit-bg"])]); assert "rgb(247, 0, 1)" in got["kHit"][2], got["kHit"]
    assert "rgb(246, 0, 1)" in got["kMiss"], got["kMiss"]
    eq("краш: фон", "cBg", SCARY["--cr-bg"]); eq("краш: рамка", "cBorder", SCARY["--cr-border"]); eq("краш: ось", "cAxis", SCARY["--cr-axis"]); eq("краш: линия", "cLine", SCARY["--cr-line"])
    eq("краш: подпись", "cLabel", SCARY["--cr-label"])
    check("краш выигрыш: линия и рамка", got["cWin"][:2], [rgb(SCARY["--cr-win"]), rgb(SCARY["--cr-win"])]); assert "rgb(245, 0, 1)" in got["cWin"][2], got["cWin"]
    check("краш проигрыш: линия и рамка", got["cCrash"][:2], [rgb(SCARY["--cr-crash"]), rgb(SCARY["--cr-crash"])]); assert "rgb(244, 0, 1)" in got["cCrash"][2], got["cCrash"]
    eq("стол: красная клетка", "tr", SCARY["--table-red"]); eq("стол: чёрная клетка", "tb", SCARY["--table-black"]); eq("стол: зелёная клетка", "tg", SCARY["--table-green"])
    eq("стол: зелёный результат", "rg", SCARY["--table-green"])
    check("колесо перерисовано", got["wheel"] != base["wheel"], True)
    colors = got["wheelColors"]
    for name, key in (("красный сектор", "--table-red"), ("чёрный сектор", "--table-black"), ("зелёный сектор", "--table-green"), ("ступица", "--wheel-hub"),
                      ("подпись чисел", "--wheel-text"), ("граница секторов", "--wheel-edge")):
        h = SCARY[key].lstrip("#")
        k = "%d,%d,%d" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        assert colors.get(k, 0) > 20, "на колесе нет цвета %s (%s): %s" % (name, key, k)
    # линия секторов и ободок (градиент): их цвета смешиваются с соседними, проверяется только что исходные цвета ушли
    for old in ("179,38,43", "23,23,27", "30,158,74"):
        assert colors.get(old, 0) == 0, "на колесе остался стартовый цвет %s" % old

    await p.ev("document.getElementById('e2e-skin').remove(); drawWheel(); true")
    await p.ev("E.sleep(700)")
    back = await p.ev(READ)
    check("после снятия переопределения вид вернулся к исходному (в том числе пиксели колеса)", back, base)
    await p.ev("document.getElementById('e2e-probes').remove(); true")
