// Сцена скина краша «Черновик» (коллекция «Черновик», DESIGN.md раздел 4):
// синяя ручка на миллиметровке, график рисуется карандашом с SVG-карандашом на конце,
// оси «×» и «время», заметки на полях по высотным зонам (×1.5, ×2, ×5, ×10, ×50+),
// краш: каракуль зачёркивания, крошки от ластика (до 6 частиц), наклон листа 1.5°;
// вывод: галочка и сумма в кружочке на точке выхода.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [150, 200, 500, 1000, 5000]; // границы зон в сотых
  const POOL = 12;
  const FADE_MS = 480;

  // Детерминированная «случайность»: один рисунок у одного игрока между запусками
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':crash:draft';
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

  // Символы частиц-крошек от ластика
  const DEFS = '<svg class="scd-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<symbol id="scd-crumb-0" viewBox="0 0 10 10"><path d="M1 5 Q3 1 7 2 Q9 5 8 8 Q4 9 1 5 Z" fill="#5A5A5A" opacity=".85"/></symbol>' +
    '<symbol id="scd-crumb-1" viewBox="0 0 8 8"><ellipse cx="4" cy="4" rx="3.5" ry="2.2" fill="#4A4A4A" opacity=".8" transform="rotate(25 4 4)"/></symbol>' +
    '<symbol id="scd-crumb-2" viewBox="0 0 6 6"><circle cx="3" cy="3" r="2.5" fill="#5A5A5A" opacity=".75"/></symbol>' +
    '</defs></svg>';

  // Карандаш с грифелем в центре вращения (16, 16)
  const PENCIL = '<div class="scd-r"><svg class="scd-pencil" viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">' +
    '<path d="M12.5 24 L12.5 30.5 L19.5 30.5 L19.5 24 Z" fill="#F4B41A" stroke="#5A5A5A" stroke-width="0.8"/>' +
    '<line x1="16" y1="24" x2="16" y2="30.5" stroke="#D19310" stroke-width="0.7"/>' +
    '<path d="M12.5 24 L16 16 L19.5 24 Z" fill="#E8D5B5" stroke="#5A5A5A" stroke-width="0.8"/>' +
    '<path d="M14.5 19.5 L16 16 L17.5 19.5 Z" fill="#5A5A5A"/>' +
    '<rect x="12.5" y="30.5" width="7" height="1.8" fill="#B0B5BA"/>' +
    '<rect x="13" y="32" width="6" height="1.5" rx="0.7" fill="#E88D8D"/>' +
    '</svg></div>';

  // Заметки на полях
  function noteSmiley(isNew) {
    const cls = isNew ? ' class="scd-draw-note"' : '';
    return '<g class="scd-note scd-note-smiley">' +
      '<circle cx="20" cy="38" r="9" fill="#F6EB61" opacity=".35"/>' +
      '<path' + cls + ' d="M12 37 C11.5 30 28.5 29 28.5 37 C28.5 45 11.5 44 12 37 Z" fill="none" stroke="#1F3A93" stroke-width="1.3" stroke-linecap="round"/>' +
      '<circle cx="17" cy="35" r="1.1" fill="#1F3A93"/><circle cx="23" cy="35" r="1.1" fill="#1F3A93"/>' +
      '<path' + cls + ' d="M16 39.5 Q20 43.5 24 39.5" fill="none" stroke="#1F3A93" stroke-width="1.3" stroke-linecap="round"/>' +
      '</g>';
  }

  function noteNorm(isNew) {
    const cls = isNew ? ' class="scd-draw-note"' : '';
    return '<g class="scd-note scd-note-norm">' +
      '<text x="6" y="62" font-style="italic" font-weight="600" font-size="9" fill="#1F3A93">уже норм</text>' +
      '<path' + cls + ' d="M10 68 Q22 69 34 68 M30 64 L35 68 L29 71" fill="none" stroke="#1F3A93" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</g>';
  }

  function noteStar(isNew) {
    const cls = isNew ? ' class="scd-draw-note"' : '';
    return '<g class="scd-note scd-note-star">' +
      '<ellipse cx="20" cy="94" rx="11" ry="10" fill="#F6EB61" opacity=".32"/>' +
      '<path' + cls + ' d="M20 86 L22 92 L28 92 L23 96 L25 102 L20 98 L15 102 L17 96 L12 92 L18 92 Z" fill="none" stroke="#1F3A93" stroke-width="1.2" stroke-linejoin="round"/>' +
      '<path' + cls + ' d="M9 94 C9 85 31 84 31 94 C31 103 8 103 9 94 Z" fill="none" stroke="#1F3A93" stroke-width="1" stroke-linecap="round"/>' +
      '<path' + cls + ' d="M7 93.5 C7 82.5 33 81.5 33 93.5 C33 105.5 6 104.5 7 93.5 Z" fill="none" stroke="#1F3A93" stroke-width="1" stroke-linecap="round"/>' +
      '<path' + cls + ' d="M5 93 C5 79.5 35 78.5 35 93 C35 107.5 4 106.5 5 93 Z" fill="none" stroke="#1F3A93" stroke-width="1.1" stroke-linecap="round"/>' +
      '</g>';
  }

  function noteTake(isNew) {
    const cls = isNew ? ' class="scd-draw-note"' : '';
    return '<g class="scd-note scd-note-take">' +
      '<rect x="4" y="116" width="68" height="13" rx="2" fill="#F6EB61" opacity=".4"/>' +
      '<text x="5" y="126" font-style="italic" font-weight="700" font-size="10" fill="#1F3A93">ЗАБИРАЙ!!!</text>' +
      '<path' + cls + ' d="M4 129.5 L70 129 M3.5 133 L71 132.5" fill="none" stroke="#1F3A93" stroke-width="1.4" stroke-linecap="round"/>' +
      '</g>';
  }

  function noteRocket(isNew) {
    const cls = isNew ? ' class="scd-draw-note"' : '';
    return '<g class="scd-note scd-note-rocket">' +
      '<path d="M260 36 Q250 42 240 48 M262 33 Q245 38 234 44" stroke="#F6EB61" stroke-width="3" stroke-linecap="round" opacity=".5"/>' +
      '<path' + cls + ' d="M266 32 L277 19 L284 25 L273 38 Z" fill="none" stroke="#1F3A93" stroke-width="1.4" stroke-linejoin="round"/>' +
      '<path' + cls + ' d="M277 19 L296 6 L284 25 Z" fill="none" stroke="#1F3A93" stroke-width="1.4" stroke-linejoin="round"/>' +
      '<path' + cls + ' d="M266 32 L258 37 L268 38 Z M273 38 L276 46 L280 39 Z" fill="none" stroke="#1F3A93" stroke-width="1.2" stroke-linejoin="round"/>' +
      '<path' + cls + ' d="M292 2 L306 -7 M298 7 L312 0" fill="none" stroke="#1F3A93" stroke-width="1.2" stroke-linecap="round"/>' +
      '</g>';
  }

  const ZONE_BUILDERS = [
    () => '',
    () => noteSmiley(true),
    () => noteSmiley(false) + noteNorm(true),
    () => noteSmiley(false) + noteNorm(false) + noteStar(true),
    () => noteSmiley(false) + noteNorm(false) + noteStar(false) + noteTake(true),
    () => noteSmiley(false) + noteNorm(false) + noteStar(false) + noteTake(false) + noteRocket(true)
  ];

  function mount(rt) {
    const rnd = rng(seedOf());
    const root = rt.root;
    root.innerHTML = DEFS +
      '<div class="scd-paper"></div>' +
      '<svg class="scd-bg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<path class="scd-margin" d="M38 0 L37.5 35 L38.5 75 L37.8 115 L38.2 150" stroke="#E8A0A0" stroke-width="1.8" fill="none"/>' +
      '<path class="scd-axis-v" d="M8 149 L8.4 110 L7.6 72 L8.3 35 L8 7 M5.5 12 L8 6 L10.5 12" stroke="#1F3A93" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path class="scd-axis-h" d="M8 149 L70 148.6 L145 149.4 L220 148.7 L295 149 M290 146.5 L296 149 L290 151.5" stroke="#1F3A93" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<text x="14" y="16" font-style="italic" font-weight="700" font-size="12" fill="#1F3A93" class="scd-axis-lbl">×</text>' +
      '<text x="250" y="143" font-style="italic" font-size="10" fill="#1F3A93" class="scd-axis-lbl">время</text>' +
      '<g class="scd-par"></g>' +
      '</svg>' +
      '<svg class="scd-event-svg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<g class="scd-cashout-wrap"></g>' +
      '<g class="scd-scribble-wrap"></g>' +
      '</svg>' +
      PENCIL +
      '<div class="scd-fx"></div>';

    const paperEl = root.querySelector('.scd-paper');
    const par = root.querySelector('.scd-par');
    const cashoutWrap = root.querySelector('.scd-cashout-wrap');
    const scribbleWrap = root.querySelector('.scd-scribble-wrap');
    const pencilBox = root.querySelector('.scd-r');
    const fx = root.querySelector('.scd-fx');
    const timers = new Set();
    const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };

    // Пул частиц (не больше POOL одновременно)
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'scd-p');
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
      p.use.setAttribute('href', '#scd-' + symbol);
      p.el.setAttribute('viewBox', vb);
      p.el.style.width = w + 'px';
      p.el.style.height = h + 'px';
      p.el.style.left = x + 'px';
      p.el.style.top = y + 'px';
      p.el.classList.add('on');
      p.anim = p.el.animate(frames, { duration: ms, easing: easing || 'ease-in', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => { p.busy = false; p.anim = null; p.el.classList.remove('on'); };
      return p;
    }

    // Размеры графика
    let W = 300, H = 190;
    const tipNow = { x: 0, y: 149, px: 0, py: 149 };
    const measure = () => {
      const r = rt.host.getBoundingClientRect();
      if (r.width > 0 && (r.width !== W || r.height !== H)) {
        W = r.width; H = r.height;
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
      zone = i;
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scd-zone');
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
      later(() => prev.remove(), FADE_MS + 40);
    }

    function place(x, y, px, py) {
      tipNow.x = x; tipNow.y = y; tipNow.px = px; tipNow.py = py;
      const left = 8 + (x / 300) * (W - 16);
      const top = 8 + (y / 150) * (H - 16);
      const dx = ((x - px) / 300) * (W - 16);
      const dy = ((y - py) / 150) * (H - 16);
      const angle = (dx === 0 && dy === 0) ? 0 : Math.atan2(dx, -dy) * 57.2958;
      pencilBox.style.transform = 'translate(' + (left - 24).toFixed(1) + 'px,' + (top - 24).toFixed(1) + 'px) rotate(' + angle.toFixed(1) + 'deg)';
      return { left, top, angle };
    }

    function reset() {
      pencilBox.classList.remove('drawing', 'broken', 'gone');
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
      scribbleWrap.innerHTML = '';
      cashoutWrap.innerHTML = '';
      setZone(0);
      place(0, 149, 0, 149);
    }
    reset();

    // Каракуль зачёркивания при краше
    function drawScribble(x, y) {
      scribbleWrap.innerHTML = '';
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('class', 'scd-scribble');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', '#1F3A93');
      path.setAttribute('stroke-width', '2.2');
      path.setAttribute('stroke-linecap', 'round');
      path.setAttribute('stroke-linejoin', 'round');

      let d = 'M' + (x - 24).toFixed(1) + ' ' + (y + 12).toFixed(1);
      const points = [
        [x + 20, y - 14],
        [x - 18, y - 6],
        [x + 24, y + 10],
        [x - 22, y + 2],
        [x + 18, y - 12],
        [x - 14, y - 14],
        [x + 22, y + 14],
        [x - 10, y + 8],
        [x + 14, y - 8]
      ];
      points.forEach(([px, py]) => {
        d += ' L' + px.toFixed(1) + ' ' + py.toFixed(1);
      });
      path.setAttribute('d', d);
      scribbleWrap.appendChild(path);

      if (rt.still()) {
        path.style.strokeDashoffset = '0';
      } else {
        path.style.strokeDasharray = '400';
        path.style.strokeDashoffset = '400';
        path.animate([
          { strokeDashoffset: '400' },
          { strokeDashoffset: '0' }
        ], { duration: 350, easing: 'ease-out', fill: 'forwards' });
      }
    }

    function onCrash() {
      drawScribble(tipNow.x, tipNow.y);
      if (!rt.still()) {
        root.animate([
          { transform: 'rotate(0deg)' },
          { transform: 'rotate(1.5deg)', offset: 0.28 },
          { transform: 'rotate(-0.5deg)', offset: 0.65 },
          { transform: 'rotate(0deg)' }
        ], { duration: 500, easing: 'ease-in-out' });

        const left = 8 + (tipNow.x / 300) * (W - 16);
        const top = 8 + (tipNow.y / 150) * (H - 16);
        const count = 4 + Math.floor(rnd() * 3); // от 4 до 6 частиц
        for (let i = 0; i < count; i++) {
          const sym = 'crumb-' + (i % 3);
          const vb = i % 3 === 0 ? '0 0 10 10' : (i % 3 === 1 ? '0 0 8 8' : '0 0 6 6');
          const sz = 7 + rnd() * 3;
          const sx = left + (rnd() - 0.5) * 24;
          const sy = top + (rnd() - 0.5) * 16;
          const dx = (rnd() - 0.5) * 36;
          const dy = 25 + rnd() * 45;
          const drot = (rnd() - 0.5) * 220;
          particle(sym, vb, sx, sy, sz, sz, [
            { transform: 'translate(0, 0) rotate(0deg)', opacity: 0.95 },
            { transform: 'translate(' + dx.toFixed(1) + 'px, ' + dy.toFixed(1) + 'px) rotate(' + drot.toFixed(0) + 'deg)', opacity: 0 }
          ], 650 + i * 80, 'ease-in');
        }
      }
      pencilBox.classList.add('broken');
    }

    function onCashout(d) {
      cashoutWrap.innerHTML = '';
      const x = tipNow.x;
      const y = tipNow.y;
      const x100 = d && d.x100 ? d.x100 : 100;
      const multText = '×' + (x100 / 100).toFixed(2);

      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scd-cashout');

      const checkPath = document.createElementNS(NS, 'path');
      checkPath.setAttribute('class', 'scd-cash-check');
      checkPath.setAttribute('fill', 'none');
      checkPath.setAttribute('stroke', '#1E6B2F');
      checkPath.setAttribute('stroke-width', '2.2');
      checkPath.setAttribute('stroke-linecap', 'round');
      checkPath.setAttribute('stroke-linejoin', 'round');
      checkPath.setAttribute('d', 'M' + (x - 16).toFixed(1) + ' ' + (y - 8).toFixed(1) +
        ' L' + (x - 8).toFixed(1) + ' ' + (y - 2).toFixed(1) +
        ' L' + (x + 2).toFixed(1) + ' ' + (y - 18).toFixed(1));

      const cx = x > 230 ? x - 42 : x + 34;
      const cy = y > 25 ? y - 12 : y + 16;

      const bgMarker = document.createElementNS(NS, 'ellipse');
      bgMarker.setAttribute('cx', cx.toFixed(1));
      bgMarker.setAttribute('cy', cy.toFixed(1));
      bgMarker.setAttribute('rx', '20');
      bgMarker.setAttribute('ry', '10');
      bgMarker.setAttribute('fill', '#F6EB61');
      bgMarker.setAttribute('opacity', '.4');

      const circlePath = document.createElementNS(NS, 'path');
      circlePath.setAttribute('class', 'scd-cash-circle');
      circlePath.setAttribute('fill', 'none');
      circlePath.setAttribute('stroke', '#1F3A93');
      circlePath.setAttribute('stroke-width', '1.5');
      circlePath.setAttribute('stroke-linecap', 'round');
      circlePath.setAttribute('d', 'M' + (cx - 20).toFixed(1) + ' ' + cy.toFixed(1) +
        ' C' + (cx - 20).toFixed(1) + ' ' + (cy - 12).toFixed(1) + ' ' + (cx + 20).toFixed(1) + ' ' + (cy - 12).toFixed(1) + ' ' + (cx + 20).toFixed(1) + ' ' + cy.toFixed(1) +
        ' C' + (cx + 20).toFixed(1) + ' ' + (cy + 12).toFixed(1) + ' ' + (cx - 20).toFixed(1) + ' ' + (cy + 12).toFixed(1) + ' ' + (cx - 20).toFixed(1) + ' ' + cy.toFixed(1) + ' Z');

      const textEl = document.createElementNS(NS, 'text');
      textEl.setAttribute('class', 'scd-cash-text');
      textEl.setAttribute('x', cx.toFixed(1));
      textEl.setAttribute('y', (cy + 3.5).toFixed(1));
      textEl.setAttribute('text-anchor', 'middle');
      textEl.setAttribute('font-style', 'italic');
      textEl.setAttribute('font-weight', '700');
      textEl.setAttribute('font-size', '10');
      textEl.setAttribute('fill', '#1F3A93');
      textEl.textContent = multText;

      g.appendChild(bgMarker);
      g.appendChild(checkPath);
      g.appendChild(circlePath);
      g.appendChild(textEl);
      cashoutWrap.appendChild(g);

      if (rt.still()) {
        checkPath.style.strokeDashoffset = '0';
        circlePath.style.strokeDashoffset = '0';
      } else {
        checkPath.style.strokeDasharray = '50';
        checkPath.style.strokeDashoffset = '50';
        checkPath.animate([
          { strokeDashoffset: '50' },
          { strokeDashoffset: '0' }
        ], { duration: 250, easing: 'ease-out', fill: 'forwards' });

        circlePath.style.strokeDasharray = '160';
        circlePath.style.strokeDashoffset = '160';
        circlePath.animate([
          { strokeDashoffset: '160' },
          { strokeDashoffset: '0' }
        ], { duration: 350, easing: 'ease-out', fill: 'forwards' });
      }
    }

    rt.on('crash:phase', (d) => {
      measure();
      if (d.phase === 'betting') { reset(); return; }
      if (d.phase === 'flight') {
        pencilBox.classList.remove('broken', 'gone');
        pencilBox.classList.add('drawing');
      }
    });

    rt.on('crash:frame', (d) => {
      measure();
      place(d.x, d.y, d.px, d.py);
      const z = zoneOf(d.x100);
      if (z !== zone) setZone(z);
    });

    rt.on('crash:crash', onCrash);
    rt.on('crash:cashout', onCashout);

    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        if (ro) ro.disconnect();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
      }
    };
  }

  registerSkinScene('draft_crash', { slot: 'crash', mount });
})();
