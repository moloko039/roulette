// Сцена скина краша «Патина» (коллекция «Патина», DESIGN.md раздел 8):
// вместо фона приборный циферблат-шкала, график — стрелка-перо самописца на бумажной ленте.
// Стадия износа 0..4: с износом лента желтеет, стекло циферблата царапается (штрихи со стадии 2+, из seed = user_id + slot).
// Событие «краш» (crash:crash): стрелка падает к нулю с короткой дребезжащей анимацией.
// Пул частиц до 12 элементов, в DOM одновременно не больше двух зон, перо и циферблат не создают DOM на каждый кадр,
// при rt.still() и perf-lite — без движения (смена кадра).
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [150, 500, 2000]; // границы зон в сотых: норма, напор, тяга, предел
  const POOL = 12;
  const FADE_MS = 480;

  // Детерминированная «случайность»: один рисунок царапин у одного игрока
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':crash:patina';
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

  const DEFS = '<svg class="scpt-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<symbol id="scpt-drop" viewBox="0 0 10 10">' +
    '<circle cx="5" cy="5" r="3.2" fill="#D9B25C" opacity=".85"/>' +
    '<circle cx="4" cy="4" r="1.2" fill="#FFF4D8" opacity=".6"/>' +
    '</symbol>' +
    '<symbol id="scpt-spark" viewBox="0 0 10 10">' +
    '<polygon points="5,0 6.5,3.5 10,5 6.5,6.5 5,10 3.5,6.5 0,5 3.5,3.5" fill="#D9B25C" opacity=".9"/>' +
    '</symbol>' +
    '</defs></svg>';

  // Зоны шкалы циферблата
  function zone0() {
    return '<g class="scpt-zone-scale">' +
      '<path d="M10 142 Q 150 138 290 142" stroke="#8C6B2E" stroke-width="0.8" stroke-dasharray="2 6" fill="none" opacity=".5"/>' +
      '<path d="M15 130 Q 150 120 285 130" stroke="#D9B25C" stroke-width="1" stroke-dasharray="4 8" fill="none" opacity=".4"/>' +
      '<text x="252" y="136" font-size="8" fill="#8C6B2E" font-family="monospace" opacity=".6">I-НОРМА</text>' +
      '</g>';
  }

  function zone1() {
    return '<g class="scpt-zone-scale">' +
      '<path d="M10 115 Q 150 95 290 115" stroke="#D9B25C" stroke-width="1.2" stroke-dasharray="6 6" fill="none" opacity=".6"/>' +
      '<text x="246" y="105" font-size="8" fill="#D9B25C" font-family="monospace" opacity=".75">II-НАПОР</text>' +
      '</g>';
  }

  function zone2() {
    return '<g class="scpt-zone-scale">' +
      '<path d="M10 80 Q 150 55 290 80" stroke="#D9B25C" stroke-width="1.4" stroke-dasharray="8 4" fill="none" opacity=".7"/>' +
      '<text x="248" y="70" font-size="8" fill="#D9B25C" font-family="monospace" opacity=".85">III-ТЯГА</text>' +
      '</g>';
  }

  function zone3() {
    return '<g class="scpt-zone-scale">' +
      '<path d="M10 45 Q 150 20 290 45" stroke="#E0675C" stroke-width="1.6" stroke-dasharray="10 3" fill="none" opacity=".8"/>' +
      '<text x="238" y="35" font-size="8" font-weight="bold" fill="#E0675C" font-family="monospace" opacity=".95">IV-ПРЕДЕЛ</text>' +
      '</g>';
  }

  const ZONE_BUILDERS = [zone0, zone1, zone2, zone3];

  // Генерация царапин на стекле циферблата (со стадии 2+)
  function makeScratches(rnd) {
    let res = '';
    for (let i = 0; i < 9; i++) {
      const minStage = i < 3 ? 2 : (i < 6 ? 3 : 4);
      const x1 = Math.round(15 + rnd() * 270);
      const y1 = Math.round(15 + rnd() * 120);
      const len = Math.round(14 + rnd() * 32);
      const ang = rnd() * Math.PI * 2;
      const x2 = Math.round(x1 + Math.cos(ang) * len);
      const y2 = Math.round(y1 + Math.sin(ang) * len);
      const cx = Math.round((x1 + x2) / 2 + (rnd() - 0.5) * 8);
      const cy = Math.round((y1 + y2) / 2 + (rnd() - 0.5) * 8);
      const op = (0.35 + rnd() * 0.35).toFixed(2);
      res += '<path class="scpt-scratch s' + minStage + '" data-min-stage="' + minStage + '" d="M' + x1 + ' ' + y1 + ' Q' + cx + ' ' + cy + ' ' + x2 + ' ' + y2 + '" stroke="rgba(255,255,255,' + op + ')" stroke-width="0.9" fill="none" stroke-linecap="round"/>';
    }
    return res;
  }

  // Стрелка-перо самописца
  const PEN = '<div class="scpt-r">' +
    '<svg class="scpt-pen" viewBox="0 0 48 48" width="48" height="48" aria-hidden="true">' +
    '<path d="M22 24 L24 19 L26 24 Z" fill="#2E2413" stroke="#8C6B2E" stroke-width="0.8"/>' +
    '<circle cx="24" cy="24" r="1.6" fill="#D9B25C"/>' +
    '<path d="M22.5 24 L21 44 L27 44 L25.5 24 Z" fill="#B58F3E" stroke="#6E5018" stroke-width="0.8"/>' +
    '<line x1="24" y1="24" x2="24" y2="44" stroke="#F0D68A" stroke-width="0.7"/>' +
    '<circle cx="24" cy="42" r="3.2" fill="#8C6B2E" stroke="#5A4015" stroke-width="0.8"/>' +
    '<circle cx="24" cy="42" r="1.2" fill="#D9B25C"/>' +
    '</svg>' +
    '</div>';

  function mount(rt) {
    const rnd = rng(seedOf());
    const root = rt.root;

    // Циферблат-шкала вместо фона
    const bgSvg = '<svg class="scpt-bg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<rect x="2" y="2" width="296" height="146" rx="6" fill="none" stroke="#8C6B2E" stroke-width="1.6" opacity=".5"/>' +
      '<circle cx="10" cy="10" r="3" fill="#8C6B2E" stroke="#5A4015" stroke-width="0.8"/><line x1="8.5" y1="10" x2="11.5" y2="10" stroke="#3A2808" stroke-width="0.6"/>' +
      '<circle cx="290" cy="10" r="3" fill="#8C6B2E" stroke="#5A4015" stroke-width="0.8"/><line x1="288.5" y1="10" x2="291.5" y2="10" stroke="#3A2808" stroke-width="0.6"/>' +
      '<circle cx="10" cy="140" r="3" fill="#8C6B2E" stroke="#5A4015" stroke-width="0.8"/><line x1="8.5" y1="140" x2="11.5" y2="140" stroke="#3A2808" stroke-width="0.6"/>' +
      '<circle cx="290" cy="140" r="3" fill="#8C6B2E" stroke="#5A4015" stroke-width="0.8"/><line x1="288.5" y1="140" x2="291.5" y2="140" stroke="#3A2808" stroke-width="0.6"/>' +
      '<path d="M 8 149 L 292 149" stroke="#8C6B2E" stroke-width="1.2" opacity=".6"/>' +
      '<line x1="38" y1="146" x2="38" y2="149" stroke="#8C6B2E" stroke-width="0.8" opacity=".6"/>' +
      '<line x1="98" y1="146" x2="98" y2="149" stroke="#8C6B2E" stroke-width="0.8" opacity=".6"/>' +
      '<line x1="158" y1="146" x2="158" y2="149" stroke="#8C6B2E" stroke-width="0.8" opacity=".6"/>' +
      '<line x1="218" y1="146" x2="218" y2="149" stroke="#8C6B2E" stroke-width="0.8" opacity=".6"/>' +
      '<line x1="278" y1="146" x2="278" y2="149" stroke="#8C6B2E" stroke-width="0.8" opacity=".6"/>' +
      '<path d="M 8 8 L 8 149" stroke="#8C6B2E" stroke-width="1.2" opacity=".6"/>' +
      '<line x1="8" y1="35" x2="12" y2="35" stroke="#8C6B2E" stroke-width="1" opacity=".7"/>' +
      '<line x1="8" y1="75" x2="12" y2="75" stroke="#8C6B2E" stroke-width="1" opacity=".7"/>' +
      '<line x1="8" y1="115" x2="12" y2="115" stroke="#8C6B2E" stroke-width="1" opacity=".7"/>' +
      '<text x="14" y="16" font-size="9" font-family="serif" fill="#8C6B2E" opacity=".7">×</text>' +
      '<text x="14" y="38" font-size="7" font-family="monospace" fill="#8C6B2E" opacity=".5">50</text>' +
      '<text x="14" y="78" font-size="7" font-family="monospace" fill="#8C6B2E" opacity=".5">10</text>' +
      '<text x="14" y="118" font-size="7" font-family="monospace" fill="#8C6B2E" opacity=".5">2</text>' +
      '<text x="14" y="146" font-size="7" font-family="monospace" fill="#8C6B2E" opacity=".5">1</text>' +
      '<path d="M 230 40 A 50 50 0 0 1 280 90" fill="none" stroke="#D9B25C" stroke-width="1" stroke-dasharray="2 4" opacity=".35"/>' +
      '<text x="236" y="24" font-size="8" font-family="serif" letter-spacing="1" fill="#8C6B2E" opacity=".6">САМОПИСЕЦЪ</text>' +
      '<g class="scpt-par"></g>' +
      '</svg>';

    // Стекло циферблата с царапинами
    const glassSvg = '<svg class="scpt-glass" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="M 0 0 L 140 0 L 20 150 L 0 150 Z" fill="rgba(255, 255, 255, 0.03)"/>' +
      '<path d="M 120 0 L 190 0 L 100 150 L 30 150 Z" fill="rgba(255, 255, 255, 0.015)"/>' +
      '<g class="scpt-scratches">' + makeScratches(rnd) + '</g>' +
      '</svg>';

    root.innerHTML = DEFS +
      '<div class="scpt-tape"></div>' +
      bgSvg +
      glassSvg +
      '<svg class="scpt-event-svg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<g class="scpt-cashout-wrap"></g>' +
      '</svg>' +
      PEN +
      '<div class="scpt-fx"></div>';

    const par = root.querySelector('.scpt-par');
    const penBox = root.querySelector('.scpt-r');
    const cashoutWrap = root.querySelector('.scpt-cashout-wrap');
    const fx = root.querySelector('.scpt-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
    };

    // Пул частиц (не больше POOL = 12 одновременно)
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'scpt-p');
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
      p.use.setAttribute('href', '#scpt-' + symbol);
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
    let curAngle = 0;
    let crashAnim = null;

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

    // Зоны шкалы: в DOM одновременно не больше двух зон
    let zone = -1;
    let zoneGroup = null;
    const zoneOf = (x100) => ZONES.reduce((n, b) => n + (x100 >= b ? 1 : 0), 0);

    function setZone(i) {
      if (i === zone) return;
      const prev = zoneGroup;
      par.querySelectorAll('.scpt-zone').forEach((old) => { if (old !== prev) old.remove(); });
      zone = i;
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scpt-zone');
      g.setAttribute('data-zone', String(i));
      g.innerHTML = ZONE_BUILDERS[i]();
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

    // Позиционирование стрелки-пера по координатам графика (кончик пера по центру 24, 24)
    function place(x, y, px, py) {
      tipNow.x = x; tipNow.y = y; tipNow.px = px; tipNow.py = py;
      const left = 8 + (x / 300) * (W - 16);
      const top = 8 + (y / 150) * (H - 16);
      const dx = ((x - px) / 300) * (W - 16);
      const dy = ((y - py) / 150) * (H - 16);
      const angle = (dx === 0 && dy === 0) ? 0 : Math.atan2(dx, -dy) * 57.2958;
      curAngle = angle;
      penBox.style.transform = 'translate(' + (left - 24).toFixed(1) + 'px,' + (top - 24).toFixed(1) + 'px) rotate(' + angle.toFixed(1) + 'deg)';
      return { left, top, angle };
    }

    function reset() {
      broken = false;
      if (crashAnim) { crashAnim.cancel(); crashAnim = null; }
      penBox.classList.remove('flying', 'broken', 'gone', 'cashout');
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); p.busy = false; p.el.classList.remove('on'); });
      cashoutWrap.innerHTML = '';
      setZone(0);
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
        penBox.classList.remove('broken', 'gone', 'cashout');
        penBox.classList.add('flying');
      }
    });

    rt.on('crash:frame', (f) => {
      if (f.phase === 'result') {
        if (!broken) setZone(zoneOf(f.x100));
        return;
      }
      if (f.phase !== 'flight') return;

      place(f.x, f.y, f.px, f.py);
      setZone(zoneOf(f.x100));
    });

    rt.on('crash:crash', (d) => {
      if (broken) return;
      broken = true;
      setZone(zoneOf(d.x100));
      penBox.classList.remove('flying');
      penBox.classList.add('broken');

      const curLeft = 8 + (tipNow.x / 300) * (W - 16);
      const curTop = 8 + (tipNow.y / 150) * (H - 16);
      const zeroTop = 8 + (149 / 150) * (H - 16);
      const tX = (curLeft - 24).toFixed(1);
      const tY0 = (zeroTop - 24).toFixed(1);

      if (!d.fresh || rt.still()) {
        penBox.style.transform = 'translate(' + tX + 'px,' + tY0 + 'px) rotate(0deg)';
        tipNow.y = 149;
        return;
      }

      // Стрелка падает к нулю с короткой дребезжащей анимацией
      const dropDist = Math.max(10, zeroTop - curTop);
      const b1 = Math.min(12, dropDist * 0.18);
      const b2 = b1 * 0.45;
      const startY = (curTop - 24).toFixed(1);
      const curRot = curAngle || 0;

      if (crashAnim) { crashAnim.cancel(); crashAnim = null; }
      crashAnim = penBox.animate([
        { transform: 'translate(' + tX + 'px,' + startY + 'px) rotate(' + curRot.toFixed(1) + 'deg)', offset: 0 },
        { transform: 'translate(' + tX + 'px,' + tY0 + 'px) rotate(6deg)', offset: 0.35, easing: 'ease-in' },
        { transform: 'translate(' + tX + 'px,' + (tY0 - b1).toFixed(1) + 'px) rotate(-4deg)', offset: 0.52, easing: 'ease-out' },
        { transform: 'translate(' + tX + 'px,' + tY0 + 'px) rotate(3deg)', offset: 0.68, easing: 'ease-in' },
        { transform: 'translate(' + tX + 'px,' + (tY0 - b2).toFixed(1) + 'px) rotate(-1.5deg)', offset: 0.82, easing: 'ease-out' },
        { transform: 'translate(' + tX + 'px,' + tY0 + 'px) rotate(0deg)', offset: 1.0 }
      ], { duration: 420, easing: 'linear', fill: 'forwards' });

      crashAnim.onfinish = crashAnim.oncancel = () => {
        penBox.style.transform = 'translate(' + tX + 'px,' + tY0 + 'px) rotate(0deg)';
        tipNow.y = 149;
        crashAnim = null;
      };

      // Чернильные брызги от падения пера (до 3 частиц)
      for (let i = 0; i < 3; i++) {
        const sx = curLeft + (rnd() - 0.5) * 10;
        const sy = zeroTop - 4;
        const dx = (rnd() - 0.5) * 20;
        const dy = -(6 + rnd() * 12);
        const sz = 6 + rnd() * 4;
        particle('drop', '0 0 10 10', sx, sy, sz, sz, [
          { transform: 'translate(0,0) scale(1)', opacity: 0.85 },
          { transform: 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scale(0.6)', opacity: 0 }
        ], 480 + i * 40, 'ease-out');
      }
    });

    rt.on('crash:cashout', () => {
      if (broken) return;
      penBox.classList.add('cashout');

      const curLeft = 8 + (tipNow.x / 300) * (W - 16);
      const curTop = 8 + (tipNow.y / 150) * (H - 16);

      // Отметка вывода на ленте
      const stamp = document.createElementNS(NS, 'circle');
      stamp.setAttribute('cx', String(tipNow.x));
      stamp.setAttribute('cy', String(tipNow.y));
      stamp.setAttribute('r', '4');
      stamp.setAttribute('fill', 'none');
      stamp.setAttribute('stroke', '#D9B25C');
      stamp.setAttribute('stroke-width', '1.5');
      cashoutWrap.appendChild(stamp);

      if (rt.still()) return;

      // Искры / блик при выводе (3 частицы)
      for (let i = 0; i < 3; i++) {
        const angle = (i * 120 + rnd() * 30) * (Math.PI / 180);
        const dist = 10 + rnd() * 12;
        const dx = Math.cos(angle) * dist;
        const dy = Math.sin(angle) * dist;
        particle('spark', '0 0 10 10', curLeft, curTop, 8, 8, [
          { transform: 'translate(0,0) scale(0.6)', opacity: 1 },
          { transform: 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scale(1.1)', opacity: 0 }
        ], 400 + i * 60, 'ease-out');
      }
    });

    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        if (ro) ro.disconnect();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
        if (crashAnim) { crashAnim.cancel(); crashAnim = null; }
        root.textContent = '';
      }
    };
  }

  registerSkinScene('crash_patina', { slot: 'crash', mount });
})();
