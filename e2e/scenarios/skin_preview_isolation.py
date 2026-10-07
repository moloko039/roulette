"""Изоляция образцов скинов: образец ЛЮБОГО предмета слота выглядит одинаково, что бы ни было надето на корне (<html>).
Баг, из-за которого сценарий написан: образец стартового «Круги» показывал шестиугольники при надетом keno_hex, потому что необязательные хуки
(--keno-shape и др.) не были объявлены в стартовой группе и наследовались с корня. Здесь для каждого слота и каждого предмета слота образец (wdScene,
как в гардеробе) сравнивается с образцом при пустом гардеробе (все слоты стартовые) по трём признакам: вычисленные значения ВСЕХ переменных слота,
вычисленные стили элементов образца (фон, контур, тени, filter, clip-path и др.) и пиксели образца (снимок элемента). Надеваются по очереди все
нестартовые предметы (по одному) и все сразу; рамка и значок, которые приложение на корень не ставит, ставятся туда принудительно (худший случай).
Статически проверяется: каждая переменная, которую задаёт скин, объявлена и в стартовой группе слота."""
import base64
import json
import os
import re

import harness
from harness import check

NAME = "skin_preview_isolation"
CLOCK_MOD = 5      # минутная граница начисления далеко (55 с): лишний /api/me по таймеру не вклинивается в сетевой эталон
USERS = {"me": {"rate": 0}}
SLOTS = ("card_back", "chip", "table", "mine_icons", "keno_ball", "crash", "avatar_frame", "badge")

MEASURE = r"""
(([slot, code, names]) => {
  const old = document.getElementById('iso-host'); if (old) old.remove();
  const host = document.createElement('div');
  host.id = 'iso-host';
  Object.assign(host.style, {position: 'fixed', left: '0', top: '0', width: '320px', padding: '12px', background: '#000', zIndex: '99999'});   // не cssText: CSP страницы запрещает inline-стили
  const box = wdScene(slot, code, false);
  host.appendChild(box);
  document.body.appendChild(host);
  const cs = getComputedStyle(box);
  const vars = {};
  for (const n of names) vars[n] = cs.getPropertyValue(n).trim();
  const props = ['color', 'backgroundColor', 'backgroundImage', 'boxShadow', 'outline', 'outlineOffset', 'filter', 'clipPath', 'borderTopColor', 'borderRadius',
                 'opacity', 'stroke', 'fill', 'width', 'height'];
  const desc = [...box.querySelectorAll('*')].map((e) => { const c = getComputedStyle(e); return props.map((p) => c[p]).join('|'); });
  const r = host.getBoundingClientRect();
  return { vars, desc, rect: [r.left, r.top, r.width, r.height] };
})
"""


def starter_blocks(css):
    """{слот: (имена переменных стартовой группы, {код: имена переменных скина})} по тексту style.css."""
    out = {}
    for slot in SLOTS:
        groups = re.findall(r'\[data-skin-%s="([a-z0-9_]+)"\][^{}]*\{([^}]*)\}' % slot, css)
        names = {}
        for code, body in groups:
            names.setdefault(code, set()).update(re.findall(r"(--[a-z0-9-]+)\s*:", body))
        out[slot] = names
    return out


async def run(w):
    p = w.page
    css = harness.client_css()
    blocks = starter_blocks(css)
    starters = {"card_back": "back_classic", "chip": "chip_plain", "table": "table_green", "mine_icons": "mine_classic", "keno_ball": "keno_round",
                "crash": "crash_line", "avatar_frame": "frame_plain", "badge": "badge_none"}
    items = {}
    static_bad = []
    for slot in SLOTS:
        start = starters[slot]
        # стартовая группа это правило с :root и атрибутом стартового кода: имена берутся из неё
        start_names = blocks[slot].get(start, set())
        check("%s: стартовая группа найдена и не пуста" % slot, bool(start_names), True)
        for code, names in blocks[slot].items():
            extra = names - start_names
            if extra:
                static_bad.append("%s: скин %s задаёт переменные, не объявленные в стартовой группе (образец стартового их унаследует с корня): %s" % (slot, code, sorted(extra)))
        items[slot] = sorted(blocks[slot])
        check("%s: стартовый предмет и хотя бы один скин в CSS" % slot, [start in items[slot], len(items[slot]) >= 2], [True, True])
    names_by_slot = {slot: sorted(blocks[slot][starters[slot]]) for slot in SLOTS}

    async def measure_all():
        out = {}
        for slot in SLOTS:
            for code in items[slot]:
                m = await p.ev("(%s)(%s)" % (MEASURE, json.dumps([slot, code, names_by_slot[slot]])))
                x, y, width, height = m.pop("rect")
                await p.settle(1)
                shot = await p.send("Page.captureScreenshot", {"format": "png", "clip": {"x": x, "y": y, "width": width, "height": height, "scale": 1}})
                m["png"] = shot["data"]
                out[(slot, code)] = m
        await p.ev("(() => { const o = document.getElementById('iso-host'); if (o) o.remove(); return true; })()")
        return out

    async def wear(equipped):
        await p.ev("""((eq) => { applySkins(eq); for (const s of ['avatar_frame', 'badge']) {
            if (eq[s]) document.documentElement.setAttribute('data-skin-' + s, eq[s]); else document.documentElement.removeAttribute('data-skin-' + s); }
            return true; })(%s)""" % json.dumps(equipped))
        await p.ev("E.sleep(300)")

    await wear({})
    base = await measure_all()
    check("образцы при пустом гардеробе измерены", len(base), sum(len(v) for v in items.values()))
    # в базовом наборе образцы разных предметов слота отличаются (иначе проверка бессмысленна)
    for slot in SLOTS:
        a, b = (base[(slot, c)] for c in items[slot][:2])
        assert a["png"] != b["png"] or a["desc"] != b["desc"] or a["vars"] != b["vars"], "образцы %s не различаются" % slot

    wears = [("%s=%s" % (s, c), {s: c}) for s in SLOTS for c in items[s] if c != starters[s]]
    wears.append(("все нестартовые сразу", {s: c for s in SLOTS for c in items[s] if c != starters[s]}))
    bad = []
    for label, eq in wears:
        await wear(eq)
        now = await measure_all()
        for key, m in now.items():
            for part in ("vars", "desc", "png"):
                if m[part] != base[key][part]:
                    bad.append("надето %s: образец %s/%s отличается от образца при пустом гардеробе (%s)" % (label, key[0], key[1], {"vars": "переменные", "desc": "стили элементов", "png": "пиксели"}[part]))
    bad = static_bad + bad      # статическая и динамическая проверки выполняются обе, чтобы отчёт показывал обе
    assert not bad, "\n".join(bad[:12]) + ("\n… и ещё %d" % (len(bad) - 12) if len(bad) > 12 else "")
    check("образцы всех предметов не зависят от надетого (%d вариантов надетого, %d образцов, три признака)" % (len(wears), len(base)), True, True)
    await wear({})
