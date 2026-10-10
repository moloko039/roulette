// Сцена скина краша «Глубина» (коллекция «Глубина», DESIGN.md раздел 3):
// погружение в батискафе в бездну, график идёт вниз через пять высотных зон (поверхность, сумерки, полночь, бездна, дно мира).
// Только рисует: ничего не считает и не знает исхода раньше сервера. События приходят из js/10-crash.js через skinEvents.
// Декор каждой зоны — одна готовая группа SVG; в DOM одновременно не больше двух зон; частицы из пула в 12 элементов;
// анимации только по transform, opacity и stroke-dashoffset (WAAPI и CSS). Без движения (reduced-motion, perf-lite) события заменяются сменой кадра.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [150, 500, 2000, 10000]; // границы зон в сотых: поверхность, сумерки, полночь, бездна, дно мира
  const POOL = 12;
  const FADE_MS = 480;

  // Детерминированная «случайность»: один рисунок у одного игрока между запусками
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':crash:deep';
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  };
  const rng = (seed) => () => {
    seed = (seed + 0x6D2B79F5) >>> 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  // ---------- символы и шаблоны (viewBox 300×150) ----------
  const grad = (id, top, bot) => '<linearGradient id="scdp-g' + id + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + top + '"/><stop offset="1" stop-color="' + bot + '"/></linearGradient>';
  const bg = (id) => '<rect y="-30" width="300" height="210" fill="url(#scdp-g' + id + ')"/>';

  function zoneSurface() {
    return '<defs>' + grad('z0', '#2E8FB8', '#144F6E') + '</defs>' + bg('z0') +
      '<path d="M0 6Q25 1 50 6T100 6T150 6T200 6T250 6T300 6V0H0z" fill="#5AD1E6" opacity=".45"/>' +
      '<path d="M0 12Q30 7 60 12T120 12T180 12T240 12T300 12" fill="none" stroke="#E0F7FA" stroke-width="1.2" opacity=".4"/>' +
      '<g class="scdp-rays">' +
      '<polygon points="35,-10 65,-10 115,160 80,160" fill="#3CF2D8" opacity=".14"/>' +
      '<polygon points="120,-10 155,-10 215,160 170,160" fill="#3CF2D8" opacity=".16"/>' +
      '<polygon points="210,-10 240,-10 295,160 260,160" fill="#3CF2D8" opacity=".13"/>' +
      '</g>' +
      '<g class="scdp-fish-school">' +
      '<path d="M60 40q6-3 12 0l3-3v6l-3-3q-6 3-12 0z" fill="#3CF2D8" opacity=".75"/>' +
      '<path d="M78 46q5-2.5 10 0l2.5-2.5v5l-2.5-2.5q-5 2.5-10 0z" fill="#3CF2D8" opacity=".7"/>' +
      '<path d="M52 50q5-2.5 10 0l2.5-2.5v5l-2.5-2.5q-5 2.5-10 0z" fill="#9B7BFF" opacity=".65"/>' +
      '<path d="M86 38q4-2 8 0l2-2v4l-2-2q-4 2-8 0z" fill="#3CF2D8" opacity=".65"/>' +
      '<path d="M68 56q5-2.5 10 0l2.5-2.5v5l-2.5-2.5q-5 2.5-10 0z" fill="#9B7BFF" opacity=".7"/>' +
      '</g>';
  }

  function zoneTwilight() {
    return '<defs>' + grad('z1', '#12405E', '#082033') + '</defs>' + bg('z1') +
      '<g class="scdp-rays" opacity=".35">' +
      '<polygon points="50,-10 75,-10 120,160 90,160" fill="#3CF2D8" opacity=".08"/>' +
      '<polygon points="150,-10 180,-10 235,160 200,160" fill="#3CF2D8" opacity=".09"/>' +
      '</g>' +
      '<g class="scdp-whale">' +
      '<path d="M120 75c30-15 90-10 120 0c15 3 25-3 35-7c-2 7-3 10 0 16c-10-4-20-9-35-6c-30 10-90 12-120-3z" fill="#06192B" opacity=".75"/>' +
      '<path d="M165 80q10 12 20 8q-7-6-15-8" fill="#06192B" opacity=".75"/>' +
      '</g>' +
      '<circle cx="45" cy="55" r="1.2" fill="#3CF2D8" opacity=".35"/>' +
      '<circle cx="85" cy="110" r="1.5" fill="#3CF2D8" opacity=".3"/>' +
      '<circle cx="215" cy="40" r="1.2" fill="#9B7BFF" opacity=".35"/>';
  }

  function zoneMidnight() {
    return '<defs>' + grad('z2', '#06192B', '#020B14') + '</defs>' + bg('z2') +
      '<g class="scdp-jelly-drift">' +
      '<path d="M45 45c0-15 30-15 30 0c-5 3-10-1-15 3c-5-4-10 0-15-3z" fill="#9B7BFF" opacity=".6" stroke="#3CF2D8" stroke-width="1"/>' +
      '<path d="M52 47q-2 13 2 23M60 48q2 14-2 24M68 47q4 13-2 23" fill="none" stroke="#9B7BFF" stroke-width="1" opacity=".75"/>' +
      '<path d="M210 75c0-11 22-11 22 0c-4 2-7-1-11 2c-4-3-7 0-11-2z" fill="#3CF2D8" opacity=".5" stroke="#9B7BFF" stroke-width="0.8"/>' +
      '<path d="M215 76q-1 10 2 17M221 77q2 10-1 18M227 76q2 10-2 17" fill="none" stroke="#3CF2D8" stroke-width="0.8" opacity=".75"/>' +
      '</g>' +
      '<g class="scdp-angler">' +
      '<path d="M140 105c5-10 25-7 35 1c5-4 10-5 15-8c-2 6-2 9 0 14c-5-3-10-3-15-5c-10 7-30 8-35-2z" fill="#030C16" stroke="#06192B" stroke-width="0.8"/>' +
      '<path d="M146 100q-8-10-12-8" fill="none" stroke="#3CF2D8" stroke-width="0.9"/>' +
      '<circle class="scdp-pulse" cx="133" cy="92" r="2.5" fill="#3CF2D8"/>' +
      '<circle class="scdp-pulse" cx="133" cy="92" r="5" fill="#3CF2D8" opacity=".25"/>' +
      '<circle cx="147" cy="103" r="1" fill="#3CF2D8" opacity=".8"/>' +
      '</g>';
  }

  function zoneAbyss() {
    return '<defs>' + grad('z3', '#020812', '#010408') + '</defs>' + bg('z3') +
      '<path d="M180 150l15-42q10-2 20 0l15 42z" fill="#0C1520" stroke="#162738" stroke-width="1.2"/>' +
      '<path d="M192 110q13-5 26 0" stroke="#C49A52" stroke-width="1.5" fill="none" opacity=".7"/>' +
      '<path d="M225 150l10-25q7-2 15 0l10 25z" fill="#080F18"/>' +
      '<g class="scdp-vent-bubbles">' +
      '<circle cx="204" cy="102" r="2" fill="#3CF2D8" opacity=".75"/>' +
      '<circle cx="208" cy="94" r="1.5" fill="#3CF2D8" opacity=".65"/>' +
      '<circle cx="202" cy="85" r="2.5" fill="#3CF2D8" opacity=".55"/>' +
      '<circle cx="207" cy="74" r="2" fill="#3CF2D8" opacity=".45"/>' +
      '<circle cx="242" cy="118" r="1.5" fill="#3CF2D8" opacity=".65"/>' +
      '<circle cx="244" cy="108" r="1.8" fill="#3CF2D8" opacity=".45"/>' +
      '</g>' +
      '<g class="scdp-flashes">' +
      '<circle cx="48" cy="38" r="1.5" fill="#3CF2D8"/>' +
      '<circle cx="112" cy="62" r="1.8" fill="#9B7BFF"/>' +
      '<circle cx="82" cy="118" r="1.2" fill="#3CF2D8"/>' +
      '<circle cx="270" cy="45" r="1.5" fill="#9B7BFF"/>' +
      '</g>';
  }

  function zoneWorldFloor() {
    return '<defs>' + grad('z4', '#020812', '#04111E') + '</defs>' + bg('z4') +
      '<path d="M0 135q80-5 160-2t140-1v18H0z" fill="#06121C"/>' +
      '<polygon points="30,88 90,65 150,88" fill="#0A1E2B" stroke="#14364D" stroke-width="1.2"/>' +
      '<rect x="25" y="88" width="130" height="6" fill="#0D2738" stroke="#1E4864" stroke-width="1"/>' +
      '<rect x="36" y="94" width="8" height="42" fill="#0E2D40" stroke="#1E4864" stroke-width="0.8"/>' +
      '<rect x="64" y="94" width="8" height="42" fill="#0E2D40" stroke="#1E4864" stroke-width="0.8"/>' +
      '<rect x="92" y="94" width="8" height="42" fill="#0E2D40" stroke="#1E4864" stroke-width="0.8"/>' +
      '<rect x="120" y="94" width="8" height="42" fill="#0E2D40" stroke="#1E4864" stroke-width="0.8"/>' +
      '<rect x="20" y="136" width="140" height="4" fill="#0A1E2B"/>' +
      '<rect x="16" y="140" width="148" height="4" fill="#071722"/>' +
      '<rect x="200" y="112" width="7" height="24" fill="#0A1E2B" stroke="#14364D" stroke-width="0.8"/>' +
      '<rect x="225" y="118" width="7" height="18" fill="#0A1E2B" stroke="#14364D" stroke-width="0.8"/>' +
      '<rect x="245" y="136" width="22" height="6" rx="1" transform="rotate(-15 245 136)" fill="#0A1E2B" stroke="#14364D" stroke-width="0.8"/>' +
      '<circle cx="90" cy="72" r="3" fill="#C49A52" opacity=".7"/>' +
      '<path d="M30 90h120" stroke="#3CF2D8" stroke-width="1" opacity=".5"/>' +
      '<path d="M37 96v40M65 96v40M93 96v40M121 96v40" stroke="#3CF2D8" stroke-width="0.8" opacity=".4"/>' +
      '<circle cx="85" cy="110" r="1.2" fill="#3CF2D8" opacity=".6"/>' +
      '<circle cx="135" cy="122" r="1.5" fill="#3CF2D8" opacity=".5"/>';
  }

  const ZONE_BUILDERS = [zoneSurface, zoneTwilight, zoneMidnight, zoneAbyss, zoneWorldFloor];

  const DEFS = '<svg class="scdp-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<linearGradient id="scdp-cone" x1="0" y1="0" x2="1" y2="0">' +
    '<stop offset="0%" stop-color="#3CF2D8" stop-opacity="0.5"/>' +
    '<stop offset="35%" stop-color="#3CF2D8" stop-opacity="0.2"/>' +
    '<stop offset="100%" stop-color="#3CF2D8" stop-opacity="0"/>' +
    '</linearGradient>' +
    '<symbol id="scdp-bubble" viewBox="0 0 16 16">' +
    '<circle cx="8" cy="8" r="6" fill="#3CF2D8" fill-opacity="0.25" stroke="#3CF2D8" stroke-width="1.2"/>' +
    '<circle cx="6" cy="6" r="1.5" fill="#FFFFFF" fill-opacity="0.8"/>' +
    '</symbol>' +
    '<symbol id="scdp-ballast" viewBox="0 0 16 16">' +
    '<polygon points="4,2 12,2 15,14 1,14" fill="#C49A52" stroke="#8B6914" stroke-width="1"/>' +
    '<circle cx="8" cy="6" r="1.5" fill="#8B6914"/>' +
    '</symbol>' +
    '</defs></svg>';

  const SUB = '<div class="scdp-r">' +
    '<svg class="scdp-spotlight" viewBox="0 0 80 60" width="80" height="60" aria-hidden="true">' +
    '<polygon points="0,30 80,4 80,56" fill="url(#scdp-cone)"/>' +
    '</svg>' +
    '<svg class="scdp-sub" viewBox="0 0 44 44" width="44" height="44" aria-hidden="true">' +
    '<circle cx="22" cy="22" r="16" fill="#0E2C44" stroke="#C49A52" stroke-width="2.5"/>' +
    '<circle class="scdp-glow-ring" cx="22" cy="22" r="19" fill="none" stroke="#3CF2D8" stroke-width="2"/>' +
    '<circle cx="38" cy="22" r="1" fill="#8B6914"/>' +
    '<circle cx="33.3" cy="33.3" r="1" fill="#8B6914"/>' +
    '<circle cx="22" cy="38" r="1" fill="#8B6914"/>' +
    '<circle cx="10.7" cy="33.3" r="1" fill="#8B6914"/>' +
    '<circle cx="6" cy="22" r="1" fill="#8B6914"/>' +
    '<circle cx="10.7" cy="10.7" r="1" fill="#8B6914"/>' +
    '<circle cx="22" cy="6" r="1" fill="#8B6914"/>' +
    '<circle cx="33.3" cy="10.7" r="1" fill="#8B6914"/>' +
    '<path d="M6 18l-4-4v16l4-4z" fill="#C49A52" stroke="#8B6914" stroke-width="0.8"/>' +
    '<line x1="2" y1="16" x2="2" y2="28" stroke="#3CF2D8" stroke-width="1.5" stroke-linecap="round"/>' +
    '<path d="M37 18l5-2v12l-5-2z" fill="#C49A52" stroke="#8B6914" stroke-width="0.8"/>' +
    '<line x1="42" y1="17" x2="42" y2="27" stroke="#3CF2D8" stroke-width="2" stroke-linecap="round"/>' +
    '<circle cx="22" cy="22" r="9" fill="#2E8FB8" stroke="#C49A52" stroke-width="1.8"/>' +
    '<path d="M16 19c2-4 10-4 12 0c-3-2-9-2-12 0z" fill="#E0F7FA" opacity=".65"/>' +
    '<path class="scdp-cracks" d="M16 18l6 4 5-3M22 22l1 6M22 22l-5 3" fill="none" stroke="#FFFFFF" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>' +
    '</div>';

  const GAUGE = '<div class="scdp-gauge" aria-hidden="true">' +
    '<div class="scdp-gauge-ticks"></div>' +
    '<div class="scdp-needle"></div>' +
    '</div>';

  const JELLY = '<svg class="scdp-jelly" viewBox="0 0 80 100" width="80" height="100" aria-hidden="true">' +
    '<path d="M10 50C10 16 70 16 70 50C60 55 50 48 40 55C30 48 20 55 10 50Z" fill="#9B7BFF" fill-opacity="0.38" stroke="#3CF2D8" stroke-width="1.8"/>' +
    '<path d="M20 46C20 25 60 25 60 46Z" fill="#3CF2D8" fill-opacity="0.45"/>' +
    '<path d="M22 52Q18 75 25 95M30 54Q38 75 32 98M40 55Q42 78 40 100M50 54Q44 75 48 98M58 52Q62 75 55 95" fill="none" stroke="#9B7BFF" stroke-width="1.5" stroke-linecap="round" opacity="0.85"/>' +
    '</svg>';

  const DEPTH = '<div class="scdp-depth">×1.00 · 100 м</div>';
  const DARKEN = '<div class="scdp-darken"></div>';

  function mount(rt) {
    const rnd = rng(seedOf());
    const root = rt.root;
    root.innerHTML = DEFS +
      '<svg class="scdp-bg" viewBox="0 0 300 150" preserveAspectRatio="xMidYMax slice" aria-hidden="true"><g class="scdp-par"></g></svg>' +
      SUB +
      GAUGE +
      DEPTH +
      DARKEN +
      '<div class="scdp-fx"></div>' +
      JELLY;

    const par = root.querySelector('.scdp-par');
    const subBox = root.querySelector('.scdp-r');
    const depthEl = root.querySelector('.scdp-depth');
    const darkenEl = root.querySelector('.scdp-darken');
    const jellyEl = root.querySelector('.scdp-jelly');
    const fx = root.querySelector('.scdp-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
    };

    // Пул частиц (не больше POOL = 12 одновременно)
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'scdp-p');
      el.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS(NS, 'use');
      el.appendChild(use);
      fx.appendChild(el);
      pool.push({ el, use, busy: false, anim: null });
    }

    function particle(symbol, vb, x, y, w, h, frames, ms, easing) {
      if (rt.still()) return null;
      const p = pool.find((q) => !q.busy);
      if (!p) return null;
      p.busy = true;
      p.use.setAttribute('href', '#scdp-' + symbol);
      p.el.setAttribute('viewBox', vb);
      p.el.style.width = w + 'px';
      p.el.style.height = h + 'px';
      p.el.style.left = x + 'px';
      p.el.style.top = y + 'px';
      p.el.classList.add('on');
      p.anim = p.el.animate(frames, { duration: ms, easing: easing || 'ease-out', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => {
        p.busy = false;
        p.anim = null;
        p.el.classList.remove('on');
      };
      return p;
    }

    // Размеры контейнера графика
    let W = 300, H = 190;
    const tipNow = { x: 0, y: 149, px: 0, py: 149 };
    const measure = () => {
      const r = rt.host.getBoundingClientRect();
      if (r.width > 0 && (r.width !== W || r.height !== H)) {
        W = r.width;
        H = r.height;
        place(tipNow.x, tipNow.y, tipNow.px, tipNow.py);
      }
    };
    const onResize = () => measure();
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(onResize) : null;
    if (ro) ro.observe(rt.host);

    // Зоны
    let zone = -1;
    let zoneGroup = null;
    const zoneOf = (x100) => ZONES.reduce((n, b) => n + (x100 >= b ? 1 : 0), 0);

    function setZone(i) {
      if (i === zone) return;
      const prev = zoneGroup;
      par.querySelectorAll('.scdp-zone').forEach((old) => { if (old !== prev) old.remove(); });
      zone = i;
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scdp-zone');
      g.setAttribute('data-zone', String(i));
      g.innerHTML = ZONE_BUILDERS[i](rnd);
      par.appendChild(g);
      zoneGroup = g;
      const instant = rt.still() || !prev;
      if (instant) {
        g.classList.add('in');
        if (prev) prev.remove();
        return;
      }
      void g.getBoundingClientRect();
      g.classList.add('in');
      prev.classList.add('out');
      later(() => prev.remove(), FADE_MS + 80);
    }

    let broken = false;

    // Расчёт позиции: отражение по вертикали y' = 149 - y, py' = 149 - py
    function place(x, y, px, py) {
      tipNow.x = x; tipNow.y = y; tipNow.px = px; tipNow.py = py;
      const my = 149 - y;
      const mpy = 149 - py;
      const left = 8 + (x / 300) * (W - 16);
      const top = 8 + (my / 150) * (H - 16);
      const dx = ((x - px) / 300) * (W - 16);
      const dy = ((my - mpy) / 150) * (H - 16);
      const angle = (dx === 0 && dy === 0) ? 25 : Math.atan2(dy, dx) * 57.2957795;
      subBox.style.transform = 'translate(' + (left - 22).toFixed(1) + 'px,' + (top - 22).toFixed(1) + 'px) rotate(' + angle.toFixed(1) + 'deg)';
      return { left, top, angle, dx, dy };
    }

    function reset() {
      broken = false;
      subBox.classList.remove('flying', 'broken', 'gone', 'cashout');
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
      setZone(0);
      par.style.transform = '';
      depthEl.textContent = '×1.00 · 100 м';
      place(0, 149, 0, 149);
    }
    reset();

    rt.on('crash:phase', (d) => {
      measure();
      if (d.phase === 'betting') {
        reset();
        return;
      }
      if (d.phase === 'flight') {
        broken = false;
        subBox.classList.remove('broken', 'gone', 'cashout');
        subBox.classList.add('flying');
      }
    });

    rt.on('crash:frame', (f) => {
      const depthText = '×' + (f.x100 / 100).toFixed(2) + ' · ' + f.x100 + ' м';
      if (depthEl.textContent !== depthText) depthEl.textContent = depthText;

      if (f.phase === 'result') {
        if (!broken) setZone(zoneOf(f.x100));
        return;
      }
      if (f.phase !== 'flight') return;

      place(f.x, f.y, f.px, f.py);
      setZone(zoneOf(f.x100));

      if (rt.still()) {
        par.style.transform = '';
      } else {
        par.style.transform = 'translate(0,-' + Math.min(26, Math.log2(f.x100 / 100) * 4.2).toFixed(1) + 'px)';
      }
    });

    rt.on('crash:crash', (d) => {
      if (broken) return;
      broken = true;
      setZone(zoneOf(d.x100));
      subBox.classList.remove('flying');
      subBox.classList.add('broken');

      if (!d.fresh) return; // итог без полёта

      if (rt.still()) return; // при rt.still() только смена кадра

      // Затемнение экрана: 0 -> 0.6 -> 0.0 за 0.8 с
      darkenEl.animate([
        { opacity: 0 },
        { opacity: 0.6, offset: 0.3 },
        { opacity: 0 }
      ], { duration: 800, easing: 'ease-out' });

      // Пузыри: до 8 частиц из пула
      const pos = place(tipNow.x, tipNow.y, tipNow.px, tipNow.py);
      const count = 8;
      for (let i = 0; i < count; i++) {
        const px = pos.left + (rnd() - 0.5) * 36 - 8;
        const py = pos.top + (rnd() - 0.5) * 36 - 8;
        const rise = -(40 + rnd() * 60);
        const drift = (rnd() - 0.5) * 50;
        const sz = 10 + rnd() * 8;
        particle('bubble', '0 0 16 16', px, py, sz, sz, [
          { transform: 'translate(0,0) scale(0.6)', opacity: 0.95 },
          { transform: 'translate(' + (drift * 0.5).toFixed(0) + 'px,' + (rise * 0.5).toFixed(0) + 'px) scale(1.1)', opacity: 0.9, offset: 0.4 },
          { transform: 'translate(' + drift.toFixed(0) + 'px,' + rise.toFixed(0) + 'px) scale(1.4)', opacity: 0 }
        ], 750 + i * 40, 'ease-out');
      }
    });

    rt.on('crash:cashout', () => {
      if (broken) return;
      subBox.classList.add('cashout');
      if (rt.still()) return;

      // Сброс балласта: 2-3 частицы вниз
      const pos = place(tipNow.x, tipNow.y, tipNow.px, tipNow.py);
      for (let i = 0; i < 3; i++) {
        const bx = pos.left + (rnd() - 0.5) * 16 - 8;
        const by = pos.top + 14;
        const fallDist = 35 + rnd() * 30;
        particle('ballast', '0 0 16 16', bx, by, 10, 10, [
          { transform: 'translate(0,0) rotate(0deg)', opacity: 1 },
          { transform: 'translate(' + ((rnd() - 0.5) * 14).toFixed(0) + 'px,' + fallDist.toFixed(0) + 'px) rotate(' + (rnd() * 180).toFixed(0) + 'deg)', opacity: 0 }
        ], 600 + i * 80, 'ease-in');
      }

      // Батискаф ускоряется вверх (короткий сдвиг вверх на 0.6 с)
      const currentTransform = subBox.style.transform;
      subBox.animate([
        { transform: currentTransform },
        { transform: currentTransform + ' translateY(-20px)' }
      ], { duration: 600, easing: 'ease-out', fill: 'forwards' });
    });

    // Эффект полного набора «Глубина»: огромная светящаяся медуза проплывает 6-8 с
    let jellyBusy = false;
    let jellyAnim = null;
    rt.on('skin:effect', (d) => {
      if (d.set !== 'deep' || jellyBusy || rt.still()) return;
      jellyBusy = true;
      jellyEl.style.display = 'block';
      const startX = W + 40;
      const endX = -100;
      const startY = Math.max(10, H * 0.25);
      jellyEl.style.left = '0px';
      jellyEl.style.top = startY + 'px';
      jellyAnim = jellyEl.animate([
        { transform: 'translate(' + startX.toFixed(0) + 'px, 0px) scale(0.9)', opacity: 0 },
        { transform: 'translate(' + (W * 0.6).toFixed(0) + 'px, -12px) scale(1)', opacity: 0.9, offset: 0.3 },
        { transform: 'translate(' + (W * 0.25).toFixed(0) + 'px, 10px) scale(1.05)', opacity: 0.9, offset: 0.7 },
        { transform: 'translate(' + endX.toFixed(0) + 'px, 0px) scale(0.9)', opacity: 0 }
      ], { duration: 7000, easing: 'ease-in-out', fill: 'forwards' });
      jellyAnim.onfinish = jellyAnim.oncancel = () => {
        jellyEl.style.display = 'none';
        jellyBusy = false;
        jellyAnim = null;
      };
    });

    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        if (ro) ro.disconnect();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
        if (jellyAnim) { jellyAnim.onfinish = null; jellyAnim.cancel(); }
        root.textContent = '';
      }
    };
  }

  registerSkinScene('crash_deep', { slot: 'crash', mount });
})();
