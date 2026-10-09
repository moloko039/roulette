// Сцена скина краша «Клён» (коллекция «Листопад», DESIGN.md раздел 7): кленовый лист несёт порыв ветра через четыре высотные зоны.
// Только рисует: ничего не считает и не знает исхода раньше сервера. События приходят из js/10-crash.js через skinEvents.
// Декор каждой зоны это одна готовая группа SVG; в DOM одновременно не больше двух зон (уходящая и приходящая); частицы из пула в 12 элементов;
// анимации только по transform и opacity (WAAPI и CSS). Без движения (reduced-motion, perf-lite) события заменяются сменой кадра.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [150, 500, 2000];                       // границы зон в сотых: аллея, кроны, над парком, тучи
  const POOL = 12;
  const FADE_MS = 520;

  // детерминированная «случайность»: одна картинка у одного игрока между запусками
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':crash:maple';
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

  // ---------- рисунок зон (viewBox 300×150) ----------
  const sky = (id, top, bottom) => '<linearGradient id="scm-' + id + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + top + '"/><stop offset="1" stop-color="' + bottom + '"/></linearGradient>';
  const bg = (id) => '<rect y="-30" width="300" height="210" fill="url(#scm-' + id + ')"/>';

  function zoneAlley() {
    return '<defs>' + sky('m0', '#3E261B', '#D88435') + '</defs>' + bg('m0') +
      '<path d="M0 120Q50 102 100 112T200 104T300 110V150H0z" fill="#5A2E1F" opacity=".75"/>' +
      '<path d="M0 123Q150 119 300 123V150H0z" fill="#3A3A3D"/>' +
      '<ellipse cx="230" cy="138" rx="34" ry="7" fill="#4B423B"/>' +
      '<ellipse cx="230" cy="138" rx="22" ry="3.5" fill="#E3A33B" opacity=".5"/>' +
      '<ellipse cx="100" cy="141" rx="38" ry="6" fill="#4B423B"/>' +
      '<ellipse cx="100" cy="141" rx="24" ry="3" fill="#C9C2B5" opacity=".35"/>' +
      '<line x1="20" y1="133" x2="50" y2="133" stroke="#C9C2B5" stroke-width="1" opacity=".3"/>' +
      '<line x1="160" y1="135" x2="190" y2="135" stroke="#E3A33B" stroke-width="1" opacity=".35"/>' +
      '<rect x="42" y="122" width="46" height="3.5" rx="1" fill="#7E2533"/>' +
      '<rect x="42" y="127" width="46" height="3.5" rx="1" fill="#7E2533"/>' +
      '<rect x="44" y="132" width="42" height="3" rx="1" fill="#7E2533"/>' +
      '<path d="M44 122v18M86 122v18M46 138h38" stroke="#262628" stroke-width="1.8"/>' +
      '<path d="M42 124q-3-6 2-8M88 124q3-6 -2-8" stroke="#262628" stroke-width="1.3" fill="none"/>' +
      '<path d="M242 144V54q0-7 7-7h5" stroke="#262628" stroke-width="2.4" fill="none"/>' +
      '<path d="M250 47l6-5 6 5-2 14h-8z" fill="#3A3A3D" stroke="#262628" stroke-width="1"/>' +
      '<circle cx="255" cy="55" r="5" fill="#FFEAA0" opacity=".95"/>' +
      '<circle cx="255" cy="55" r="14" fill="#E3A33B" opacity=".3"/>' +
      '<polygon points="250,60 210,144 280,144 260,60" fill="#E3A33B" opacity=".09"/>' +
      '<circle cx="56" cy="123" r="2" fill="#D9541E"/><circle cx="72" cy="138" r="2" fill="#E3A33B"/><circle cx="150" cy="133" r="1.8" fill="#D9541E"/><circle cx="264" cy="142" r="1.8" fill="#7E2533"/>';
  }

  function zoneCanopy() {
    return '<defs>' + sky('m1', '#5A2A14', '#E3A33B') + '</defs>' + bg('m1') +
      '<path d="M-10 145Q50 90 90 70T170 50" fill="none" stroke="#4A2814" stroke-width="6"/>' +
      '<path d="M310 135Q250 85 200 65T120 40" fill="none" stroke="#4A2814" stroke-width="5"/>' +
      '<path d="M60 85Q90 50 130 35" fill="none" stroke="#4A2814" stroke-width="3"/>' +
      '<path d="M210 65Q180 35 150 20" fill="none" stroke="#4A2814" stroke-width="2.4"/>' +
      '<g class="scm-rustle">' +
      '<path d="M-10 130Q20 70 70 60T120 40T80 10Q20 20-10 40z" fill="#7E2533" opacity=".9"/>' +
      '<path d="M310 120Q260 65 210 50T160 30T200 5Q270 10 310 30z" fill="#7E2533" opacity=".9"/>' +
      '<circle cx="65" cy="55" r="24" fill="#D9541E"/><circle cx="100" cy="45" r="20" fill="#E3A33B"/><circle cx="45" cy="70" r="18" fill="#E3A33B"/>' +
      '<circle cx="235" cy="50" r="24" fill="#D9541E"/><circle cx="195" cy="40" r="22" fill="#E3A33B"/><circle cx="255" cy="65" r="18" fill="#E3A33B"/>' +
      '<circle cx="145" cy="32" r="18" fill="#D9541E" opacity=".95"/><circle cx="155" cy="22" r="14" fill="#E3A33B"/>' +
      '<path d="M78 30l-4 6 5 2-3 5 6-3 2 6 3-6 6 2-3-5 5-2-5-4z" fill="#E3A33B"/>' +
      '<path d="M218 25l-4 6 5 2-3 5 6-3 2 6 3-6 6 2-3-5 5-2-5-4z" fill="#D9541E"/>' +
      '</g>';
  }

  function zoneOverPark() {
    return '<defs>' + sky('m2', '#263445', '#C9A57B') + '</defs>' + bg('m2') +
      '<path d="M0 136Q70 120 150 128T300 122V150H0z" fill="#7E2533" opacity=".65"/>' +
      '<path d="M0 150v-18l22-14 22 14v18zM42 150v-24l26-17 26 17v24zM92 150v-16l20-13 20 13v16zM130 150v-28l28-19 28 19v28zM184 150v-20l24-15 24 15v20zM230 150v-26l32-18 32 18v26zM292 150v-16l14-9 14 9v16z" fill="#2E2528"/>' +
      '<path d="M-2 133l24-15 24 15M40 127l28-18 28 18M128 123l30-20 30 20M228 125l34-19 34 19" stroke="#7E2533" stroke-width="1.3" fill="none"/>' +
      '<rect x="62" y="104" width="6" height="10" fill="#1C181A"/><rect x="154" y="98" width="6" height="12" fill="#1C181A"/><rect x="256" y="101" width="6" height="11" fill="#1C181A"/>' +
      '<g class="scm-cranes">' +
      '<path d="M146 39q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.5" fill="none" stroke-linecap="round"/>' +
      '<path d="M132 46q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.4" fill="none" stroke-linecap="round"/>' +
      '<path d="M118 54q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.3" fill="none" stroke-linecap="round"/>' +
      '<path d="M104 62q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.3" fill="none" stroke-linecap="round"/>' +
      '<path d="M162 46q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.4" fill="none" stroke-linecap="round"/>' +
      '<path d="M176 54q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.3" fill="none" stroke-linecap="round"/>' +
      '<path d="M190 62q4-4 8 0 4-4 8 0" stroke="#1E191C" stroke-width="1.3" fill="none" stroke-linecap="round"/>' +
      '</g>';
  }

  function zoneClouds() {
    return '<defs>' + sky('m3', '#161C26', '#343B48') + '</defs>' + bg('m3') +
      '<g class="scm-drift">' +
      '<ellipse cx="65" cy="38" rx="55" ry="22" fill="#242B38" opacity=".95"/>' +
      '<ellipse cx="150" cy="28" rx="68" ry="26" fill="#2E3646" opacity=".95"/>' +
      '<ellipse cx="235" cy="40" rx="60" ry="24" fill="#262D3B" opacity=".95"/>' +
      '<circle cx="105" cy="20" r="26" fill="#2E3646" opacity=".95"/>' +
      '<circle cx="195" cy="18" r="28" fill="#242B38" opacity=".95"/>' +
      '<ellipse cx="140" cy="65" rx="120" ry="22" fill="#3A4252" opacity=".55"/>' +
      '</g>' +
      '<g class="scm-rain-wrap">' +
      '<line class="scm-drop d1" x1="45" y1="45" x2="41" y2="60" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '<line class="scm-drop d2" x1="100" y1="30" x2="96" y2="45" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '<line class="scm-drop d3" x1="155" y1="55" x2="151" y2="70" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '<line class="scm-drop d4" x1="210" y1="38" x2="206" y2="53" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '<line class="scm-drop d5" x1="260" y1="62" x2="256" y2="77" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '<line class="scm-drop d6" x1="125" y1="78" x2="121" y2="93" stroke="#C9C2B5" stroke-width="1.2" stroke-linecap="round"/>' +
      '</g>';
  }

  const ZONE_BUILDERS = [zoneAlley, zoneCanopy, zoneOverPark, zoneClouds];

  // ---------- символы и элементы (viewBox 40×40) ----------
  const LEAF_PATH = 'M20 38V30C18 30 14 27 15 24C16 23 18 24 19 25L10 17C8 16 9 14 11 15L17 19L11 9C9 7 11 6 13 8L18 14L16 3C18 1 20 2 20 5C20 2 22 1 24 3L22 14L27 8C29 6 31 7 29 9L23 19L29 15C31 14 32 16 30 17L21 25C22 24 24 23 25 24C26 27 22 30 20 30Z';
  const VEIN_PATH = 'M20 30V6M20 26L12 10M20 26L28 10M20 26L11 17M20 26L29 17';

  const DEFS = '<svg class="scm-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<symbol id="scm-leaf-p" viewBox="0 0 40 40"><path d="' + LEAF_PATH + '" fill="#D9541E" stroke="#7E2533" stroke-width="0.8"/><path d="' + VEIN_PATH + '" stroke="#7E2533" stroke-width="1.1" fill="none" stroke-linecap="round"/></symbol>' +
    '<symbol id="scm-leaf-gold" viewBox="0 0 40 40"><path d="' + LEAF_PATH + '" fill="#E3A33B" stroke="#8E4A10" stroke-width="0.8"/><path d="' + VEIN_PATH + '" stroke="#8E4A10" stroke-width="1.1" fill="none" stroke-linecap="round"/></symbol>' +
    '<symbol id="scm-leaf-burgundy" viewBox="0 0 40 40"><path d="' + LEAF_PATH + '" fill="#7E2533" stroke="#4A1520" stroke-width="0.8"/><path d="' + VEIN_PATH + '" stroke="#4A1520" stroke-width="1.1" fill="none" stroke-linecap="round"/></symbol>' +
    '<symbol id="scm-gust" viewBox="0 0 24 16"><path d="M2 14Q10 2 18 6T22 4" stroke="#C9C2B5" stroke-width="2" fill="none" stroke-linecap="round" opacity=".8"/></symbol>' +
    '<symbol id="scm-drop" viewBox="0 0 8 16"><path d="M4 1C4 1 1 8 1 11C1 13 2.5 15 4 15C5.5 15 7 13 7 11C7 8 4 1 4 1Z" fill="#C9C2B5" opacity=".7"/></symbol>' +
    '</defs></svg>';

  const GLOVE = '<svg class="scm-glove" viewBox="0 0 44 40" width="48" height="44" aria-hidden="true">' +
    '<rect x="14" y="28" width="16" height="10" rx="2" fill="#7E2533" stroke="#4A1820" stroke-width="1"/>' +
    '<line x1="18" y1="28" x2="18" y2="38" stroke="#4A1820" stroke-width="0.8"/>' +
    '<line x1="22" y1="28" x2="22" y2="38" stroke="#4A1820" stroke-width="0.8"/>' +
    '<line x1="26" y1="28" x2="26" y2="38" stroke="#4A1820" stroke-width="0.8"/>' +
    '<path d="M14 28C10 24 6 18 10 13C12 11 15 13 16 18L18 8C19 5 22 5 23 8L24 6C25 3 28 3 29 6L30 8C31 5 34 5 35 9L35 20C35 25 32 28 30 28Z" fill="#5C3826" stroke="#3A2012" stroke-width="1.2"/>' +
    '<path d="M16 20Q22 23 28 19" stroke="#E3A33B" stroke-width="0.8" fill="none" opacity="0.6"/></svg>';

  const LEAF = '<svg class="scm-leaf" viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">' +
    '<path d="' + LEAF_PATH + '" fill="#D9541E" stroke="#7E2533" stroke-width="0.8"/>' +
    '<path d="' + VEIN_PATH + '" stroke="#7E2533" stroke-width="1.1" fill="none" stroke-linecap="round"/></svg>';

  function mount(rt) {
    const rnd = rng(seedOf());
    const root = rt.root;
    root.innerHTML = DEFS +
      '<svg class="scm-bg" viewBox="0 0 300 150" preserveAspectRatio="xMidYMax slice" aria-hidden="true"><g class="scm-par"></g></svg>' +
      '<svg class="scm-wind-trail" viewBox="0 0 300 150" aria-hidden="true"><path class="scm-w1" d="M0 149"/><path class="scm-w2" d="M0 149"/></svg>' +
      '<div class="scm-r">' + GLOVE + '<div class="scm-flutter">' + LEAF + '</div></div>' +
      '<div class="scm-fx"></div>';

    const par = root.querySelector('.scm-par');
    const leafBox = root.querySelector('.scm-r');
    const fx = root.querySelector('.scm-fx');
    const w1 = root.querySelector('.scm-w1');
    const w2 = root.querySelector('.scm-w2');
    const timers = new Set();
    const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };

    // пул частиц (не больше POOL одновременно): каждый элемент это svg с <use>
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'scm-p');
      el.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS(NS, 'use');
      el.appendChild(use);
      fx.appendChild(el);
      pool.push({ el, use, busy: false, anim: null });
    }
    const free = () => pool.filter((p) => !p.busy).length;

    function particle(symbol, vb, x, y, w, h, frames, ms, easing) {
      if (rt.still()) return null;
      const p = pool.find((q) => !q.busy);
      if (!p) return null;
      p.busy = true;
      p.use.setAttribute('href', '#scm-' + symbol);
      p.el.setAttribute('viewBox', vb);
      p.el.style.width = w + 'px';
      p.el.style.height = h + 'px';
      p.el.style.left = x + 'px';
      p.el.style.top = y + 'px';
      p.el.classList.add('on');
      p.anim = p.el.animate(frames, { duration: ms, easing: easing || 'ease-out', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => { p.busy = false; p.anim = null; p.el.classList.remove('on'); };
      return p;
    }

    // размеры графика (внутренний отступ 8 px, как у .cr-svg)
    let W = 300, H = 190;
    const tipNow = { x: 0, y: 149, px: 0, py: 149 };
    const measure = () => {
      const r = rt.host.getBoundingClientRect();
      if (r.width > 0 && (r.width !== W || r.height !== H)) { W = r.width; H = r.height; place(tipNow.x, tipNow.y, tipNow.px, tipNow.py); }
    };
    const onResize = () => measure();
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(onResize) : null;
    if (ro) ro.observe(rt.host);

    // зоны
    let zone = -1;
    let zoneGroup = null;
    const zoneOf = (x100) => ZONES.reduce((n, b) => n + (x100 >= b ? 1 : 0), 0);
    function setZone(i) {
      if (i === zone) return;
      const prev = zoneGroup;
      zone = i;
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scm-zone');
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
      void g.getBoundingClientRect();           // чтобы переход непрозрачности начался с нуля
      g.classList.add('in');
      prev.classList.add('out');
      later(() => prev.remove(), FADE_MS + 80);
    }

    let broken = false;
    let phase = 'betting';
    let idleTimer = null;
    let activeIdle = 0;

    function stopIdle() {
      if (idleTimer) { clearInterval(idleTimer); idleTimer = null; }
    }

    function spawnIdleLeaf() {
      if (phase !== 'betting' || rt.still() || activeIdle >= 3 || free() < 4) return;
      activeIdle++;
      const sym = rnd() > 0.5 ? 'leaf-gold' : 'leaf-p';
      const sx = 20 + rnd() * (W - 40);
      const sw = 14 + rnd() * 6;
      const dx1 = (rnd() - 0.5) * 40;
      const dx2 = dx1 + (rnd() - 0.5) * 50;
      const p = particle(sym, '0 0 40 40', sx, -15, sw, sw,
        [
          { transform: 'translate(0, 0) rotate(0deg)', opacity: 0.85 },
          { transform: 'translate(' + dx1.toFixed(0) + 'px, ' + (H * 0.45).toFixed(0) + 'px) rotate(' + (rnd() * 160).toFixed(0) + 'deg)', opacity: 0.85, offset: 0.45 },
          { transform: 'translate(' + dx2.toFixed(0) + 'px, ' + (H + 20).toFixed(0) + 'px) rotate(' + (rnd() * 320).toFixed(0) + 'deg)', opacity: 0 }
        ],
        3000 + rnd() * 1000, 'cubic-bezier(.3, .4, .4, 1)');
      if (p && p.anim) {
        const origFinish = p.anim.onfinish;
        p.anim.onfinish = () => {
          activeIdle = Math.max(0, activeIdle - 1);
          if (origFinish) origFinish();
        };
        const origCancel = p.anim.oncancel;
        p.anim.oncancel = () => {
          activeIdle = Math.max(0, activeIdle - 1);
          if (origCancel) origCancel();
        };
      } else {
        activeIdle = Math.max(0, activeIdle - 1);
      }
    }

    function startIdle() {
      stopIdle();
      if (rt.still()) return;
      idleTimer = setInterval(() => {
        if (phase === 'betting') spawnIdleLeaf();
        else stopIdle();
      }, 5000);
    }

    function place(x, y, px, py) {
      tipNow.x = x; tipNow.y = y; tipNow.px = px; tipNow.py = py;
      const left = 8 + (x / 300) * (W - 16);
      const top = 8 + (y / 150) * (H - 16);
      const dx = ((x - px) / 300) * (W - 16);
      const dy = ((y - py) / 150) * (H - 16);
      const angle = dx === 0 && dy === 0 ? 0 : Math.atan2(dx, -dy) * 57.2958;
      leafBox.style.transform = 'translate(' + (left - 28).toFixed(1) + 'px,' + (top - 28).toFixed(1) + 'px) rotate(' + angle.toFixed(1) + 'deg)';
      return { left, top, angle };
    }

    function reset() {
      broken = false;
      stopIdle();
      activeIdle = 0;
      leafBox.classList.remove('flying', 'broken', 'caught', 'gone');
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
      setZone(0);
      par.style.transform = '';
      w1.setAttribute('d', 'M0 149');
      w2.setAttribute('d', 'M0 149');
      place(0, 149, 0, 149);
    }
    reset();

    rt.on('crash:phase', (d) => {
      measure();
      phase = d.phase;
      if (d.phase === 'betting') {
        reset();
        startIdle();
        return;
      }
      stopIdle();
      if (d.phase === 'flight') {
        broken = false;
        leafBox.classList.remove('broken', 'gone', 'caught');
        leafBox.classList.add('flying');
        const left = 8, top = H - 8;
        for (let i = 0; i < 3; i++) {
          const dx = (rnd() - 0.5) * 50;
          const sym = i % 2 === 0 ? 'leaf-gold' : 'leaf-p';
          particle(sym, '0 0 40 40', left + 4, top - 12, 11, 11,
            [
              { transform: 'translate(0,0) rotate(0deg)', opacity: 0.9 },
              { transform: 'translate(' + dx.toFixed(0) + 'px,' + (-(14 + rnd() * 20)).toFixed(0) + 'px) rotate(' + (rnd() * 180).toFixed(0) + 'deg)', opacity: 0.9, offset: 0.4 },
              { transform: 'translate(' + (dx * 1.3).toFixed(0) + 'px, 14px) rotate(300deg)', opacity: 0 }
            ],
            650 + i * 70, 'ease-in');
        }
      }
    });

    let px = 0, py = 149;
    let lastGust = 0, lastSmallLeaf = 0;
    rt.on('crash:frame', (f) => {
      if (f.phase === 'result') { if (!broken) setZone(zoneOf(f.x100)); return; }
      if (f.phase !== 'flight') return;
      const pos = place(f.x, f.y, f.px, f.py);
      px = f.x; py = f.y;
      setZone(zoneOf(f.x100));

      const curve = rt.host.querySelector('.cr-curve');
      if (curve) {
        const d = curve.getAttribute('d');
        if (d) { w1.setAttribute('d', d); w2.setAttribute('d', d); }
      }

      if (rt.still()) {
        par.style.transform = '';
      } else {
        par.style.transform = 'translate(0,' + Math.min(26, Math.log2(f.x100 / 100) * 4.2).toFixed(1) + 'px)';
        const now = performance.now();
        const a = pos.angle * 0.0174533;
        const bx = pos.left - Math.sin(a) * 18, by = pos.top + Math.cos(a) * 18;
        if (now - lastGust > 150 && free() > 6) {
          lastGust = now;
          particle('gust', '0 0 24 16', bx - 10, by - 6, 20, 13,
            [{ transform: 'translate(0,0) scale(.8)', opacity: 0.75 }, { transform: 'translate(' + ((rnd() - 0.5) * 16).toFixed(0) + 'px,' + (10 + rnd() * 12).toFixed(0) + 'px) scale(1.3)', opacity: 0 }], 520, 'ease-out');
        }
        if (now - lastSmallLeaf > 360 && free() > 7) {
          lastSmallLeaf = now;
          const sym = rnd() > 0.5 ? 'leaf-gold' : 'leaf-p';
          particle(sym, '0 0 40 40', bx - 6, by - 6, 12, 12,
            [{ transform: 'translate(0,0) rotate(0deg)', opacity: 0.8 }, { transform: 'translate(' + ((rnd() - 0.5) * 20).toFixed(0) + 'px,14px) rotate(' + (rnd() * 180).toFixed(0) + 'deg)', opacity: 0 }], 750, 'ease-out');
        }
      }
    });

    rt.on('crash:crash', (d) => {
      if (broken) return;
      broken = true;
      stopIdle();
      setZone(zoneOf(d.x100));
      leafBox.classList.remove('flying', 'caught');
      if (!d.fresh) { leafBox.classList.add('gone'); return; }
      if (rt.still()) { leafBox.classList.add('broken'); return; }
      leafBox.classList.add('gone');
      const left = 8 + (px / 300) * (W - 16), top = 8 + (py / 150) * (H - 16);
      particle('leaf-p', '0 0 40 40', left - 20, top - 20, 40, 40,
        [
          { transform: 'translate(0, 0) rotate(0deg)', opacity: 1 },
          { transform: 'translate(22px, ' + (H * 0.22).toFixed(0) + 'px) rotate(55deg)', opacity: 1, offset: 0.25 },
          { transform: 'translate(-24px, ' + (H * 0.50).toFixed(0) + 'px) rotate(135deg)', opacity: 0.95, offset: 0.55 },
          { transform: 'translate(18px, ' + (H * 0.80).toFixed(0) + 'px) rotate(220deg)', opacity: 0.9, offset: 0.8 },
          { transform: 'translate(0, ' + (H + 20).toFixed(0) + 'px) rotate(310deg)', opacity: 0 }
        ],
        1400, 'cubic-bezier(.25, .6, .35, 1)');
      const parts = [['leaf-gold', 16, 16], ['leaf-burgundy', 16, 16], ['gust', 20, 14]];
      parts.forEach((p, i) => {
        const dx = (rnd() - 0.5) * 60;
        const dy = 20 + rnd() * 40;
        particle(p[0], p[0] === 'gust' ? '0 0 24 16' : '0 0 40 40', left - p[1] / 2, top - p[2] / 2, p[1], p[2],
          [
            { transform: 'translate(0, 0) rotate(0deg)', opacity: 0.85 },
            { transform: 'translate(' + dx.toFixed(0) + 'px, ' + dy.toFixed(0) + 'px) rotate(' + (rnd() * 180).toFixed(0) + 'deg)', opacity: 0.6, offset: 0.4 },
            { transform: 'translate(' + (dx * 1.4).toFixed(0) + 'px, ' + (H * 0.85).toFixed(0) + 'px) rotate(' + (rnd() * 360).toFixed(0) + 'deg)', opacity: 0 }
          ],
          1100 + i * 140, 'ease-in');
      });
    });

    rt.on('crash:cashout', () => {
      if (!broken) leafBox.classList.add('caught');
    });

    // эффект полного набора «Листопад»: по экрану проходит порыв ветра (8-10 листьев пролетают поперёк)
    rt.on('skin:effect', (d) => {
      if (d.set !== 'leaves' || rt.still() || (phase !== 'betting' && phase !== 'result')) return;
      stopIdle();
      const count = Math.min(free(), 8 + Math.floor(rnd() * 3));
      for (let i = 0; i < count; i++) {
        const startY = 10 + rnd() * (H * 0.7);
        const endY = startY + (rnd() - 0.25) * 50;
        const sym = i % 3 === 0 ? 'leaf-burgundy' : (i % 2 === 0 ? 'leaf-gold' : 'leaf-p');
        const s = 14 + rnd() * 8;
        const delay = i * 20;
        const dur = 1750 - delay;
        later(() => {
          particle(sym, '0 0 40 40', -30, startY, s, s,
            [
              { transform: 'translate(0, 0) rotate(' + (rnd() * 60).toFixed(0) + 'deg)', opacity: 0 },
              { transform: 'translate(' + ((W + 60) * 0.25).toFixed(0) + 'px, -12px) rotate(' + (120 + rnd() * 80).toFixed(0) + 'deg)', opacity: 0.95, offset: 0.25 },
              { transform: 'translate(' + ((W + 60) * 0.7).toFixed(0) + 'px, 10px) rotate(' + (240 + rnd() * 80).toFixed(0) + 'deg)', opacity: 0.95, offset: 0.7 },
              { transform: 'translate(' + (W + 60).toFixed(0) + 'px, ' + (endY - startY).toFixed(0) + 'px) rotate(' + (360 + rnd() * 120).toFixed(0) + 'deg)', opacity: 0 }
            ],
            dur, 'cubic-bezier(.25, .5, .4, 1)');
        }, delay);
      }
    });

    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        if (ro) ro.disconnect();
        stopIdle();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
        root.textContent = '';
      }
    };
  }

  registerSkinScene('crash_maple', { slot: 'crash', mount });
})();
