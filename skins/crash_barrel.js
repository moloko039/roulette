// Сцена скина краша «Бочка» (коллекция «Дачный сезон», DESIGN.md раздел 2): ракета из бочки поднимается над дачей через пять высотных зон.
// Только рисует: ничего не считает и не знает исхода раньше сервера. События приходят из js/10-crash.js через skinEvents (crash:phase, crash:frame, crash:bet,
// crash:crash, crash:cashout). Декор каждой зоны это одна готовая группа SVG; в DOM одновременно не больше двух зон (уходящая и приходящая); частицы из пула в 12 элементов;
// анимации только по transform и opacity (WAAPI и CSS). Без движения (reduced-motion, perf-lite) события заменяются сменой кадра.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [150, 300, 1000, 5000];                 // границы зон в сотых: огород, над крышами, облака, стратосфера, космос
  const POOL = 12;
  const FADE_MS = 520;

  // детерминированная «случайность»: одна картинка у одного игрока между запусками
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':crash';
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
  const sky = (id, top, bottom) => '<linearGradient id="scb-' + id + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + top + '"/><stop offset="1" stop-color="' + bottom + '"/></linearGradient>';
  const bg = (id) => '<rect y="-30" width="300" height="210" fill="url(#scb-' + id + ')"/>';       // выше и ниже кадра: параллакс сдвигает декор, края неба не открываются

  function pickets() {
    let d = '';
    for (let x = 0; x < 300; x += 9) d += 'M' + x + ' 150V121l3-4 3 4v29z';
    return '<path d="' + d + '" fill="#E9DCC0"/><rect y="126" width="300" height="3" fill="#8A5A35"/><rect y="138" width="300" height="3" fill="#8A5A35"/>';
  }

  function zoneGarden() {
    return '<defs>' + sky('g0', '#7FB2D9', '#F7C27A') + '</defs>' + bg('g0') +
      '<circle cx="238" cy="106" r="15" fill="#FFE3A3" opacity=".9"/>' +
      '<path d="M0 118Q80 108 150 118T300 116V150H0z" fill="#5C8A3A"/>' +
      // яблоня
      '<rect x="36" y="92" width="6" height="30" fill="#8A5A35"/><circle cx="39" cy="86" r="19" fill="#4E7A31"/><circle cx="27" cy="94" r="12" fill="#5C8A3A"/><circle cx="52" cy="95" r="12" fill="#5C8A3A"/>' +
      '<circle cx="30" cy="84" r="2.2" fill="#B8323A"/><circle cx="46" cy="80" r="2.2" fill="#B8323A"/><circle cx="40" cy="95" r="2.2" fill="#B8323A"/><circle cx="55" cy="92" r="2.2" fill="#B8323A"/><circle cx="24" cy="97" r="2.2" fill="#B8323A"/>' +
      // баня с дымком
      '<rect x="214" y="98" width="44" height="24" fill="#8A5A35"/><path d="M214 106h44M214 114h44" stroke="#6B4326" stroke-width="1.2"/>' +
      '<path d="M210 98l26-16 26 16z" fill="#3B2A1E"/><rect x="247" y="80" width="6" height="12" fill="#3B2A1E"/><rect x="230" y="106" width="9" height="16" fill="#3B2A1E"/>' +
      '<g class="scb-smoke"><circle cx="250" cy="74" r="4" fill="#F3E7CF" opacity=".55"/><circle cx="254" cy="66" r="5" fill="#F3E7CF" opacity=".4"/></g>' +
      // бельевая верёвка
      '<path d="M96 94Q122 102 150 95" fill="none" stroke="#3B2A1E" stroke-width="1"/><path d="M104 97l-1 14h13l-1-12z" fill="#F3E7CF"/><path d="M128 99l-1 12h12l-1-13z" fill="#F3E7CF"/><circle cx="109" cy="103" r="1.6" fill="#B8323A"/><circle cx="133" cy="104" r="1.6" fill="#B8323A"/>' +
      pickets();
  }

  function zoneRoofs() {
    return '<defs>' + sky('g1', '#4F93CB', '#F0C28A') + '</defs>' + bg('g1') +
      '<circle cx="40" cy="112" r="13" fill="#FFE3A3" opacity=".85"/>' +
      // водонапорная башня
      '<path d="M236 150l5-34M262 150l-5-34M239 136h20" stroke="#3B2A1E" stroke-width="2" fill="none"/><rect x="230" y="96" width="38" height="22" rx="4" fill="#B0522F"/><path d="M230 104h38M230 111h38" stroke="#8A3F23" stroke-width="1.4"/><path d="M228 96l21-12 21 12z" fill="#3B2A1E"/>' +
      // крыши домиков
      '<path d="M0 150v-20l22-16 22 16v20zM52 150v-24l24-17 24 17v24zM108 150v-18l20-14 20 14v18zM156 150v-26l24-18 24 18v26z" fill="#8A5A35"/>' +
      '<path d="M-4 130l26-20 26 20zM48 126l28-21 28 21zM104 132l24-17 24 17zM152 124l28-21 28 21z" fill="#A5432F"/>' +
      '<rect x="64" y="132" width="8" height="8" fill="#F7E3A3"/><rect x="170" y="136" width="8" height="8" fill="#F7E3A3"/><rect x="118" y="140" width="7" height="7" fill="#F7E3A3"/>' +
      // ласточки
      '<g class="scb-fly"><path d="M62 52q3-4 6 0 3-4 6 0M82 44q3-4 6 0 3-4 6 0M74 62q3-4 6 0 3-4 6 0" fill="none" stroke="#3B2A1E" stroke-width="1.4" stroke-linecap="round"/></g>';
  }

  function zoneClouds(rnd) {
    let c = '';
    for (let i = 0; i < 6; i++) {
      const x = 20 + i * 52 + rnd() * 16;
      const y = 28 + ((i * 37) % 90) + rnd() * 10;
      const s = 0.8 + rnd() * 0.7;
      c += '<g transform="translate(' + x.toFixed(0) + ' ' + y.toFixed(0) + ') scale(' + s.toFixed(2) + ')"><ellipse rx="26" ry="9" fill="#F7F3EA" opacity=".92"/><circle cx="-8" cy="-6" r="10" fill="#F7F3EA" opacity=".95"/><circle cx="8" cy="-8" r="12" fill="#F7F3EA" opacity=".95"/></g>';
    }
    return '<defs>' + sky('g2', '#3A5C8F', '#E7A57A') + '</defs>' + bg('g2') + '<g class="scb-drift">' + c + '</g>';
  }

  function zoneStrato() {
    return '<defs>' + sky('g3', '#0B1634', '#24508F') + '</defs>' + bg('g3') +
      '<path d="M-10 150Q150 98 310 150z" fill="#2D5C96"/><path d="M-10 150Q150 98 310 150" fill="none" stroke="#8CC4F0" stroke-width="2"/>' +
      '<path d="M40 146Q150 108 260 146" fill="none" stroke="#5E9AD1" stroke-width="1" opacity=".6"/>' +
      // спутник-ведро
      '<g transform="translate(206 38)"><g class="scb-sat"><path d="M0 0h16l-3 15H3z" fill="#9AA3AD"/><path d="M1 0q7-9 14 0" fill="none" stroke="#C8CED4" stroke-width="1.4"/><path d="M8 15v7M4 22h8" stroke="#C8CED4" stroke-width="1.4"/><circle cx="8" cy="22" r="1.6" fill="#F6EB61"/></g></g>' +
      '<path d="M34 24h1v1h-1zM72 14h1v1h-1zM120 30h1v1h-1zM166 12h1v1h-1zM250 20h1v1h-1z" fill="#fff" opacity=".8"/>';
  }

  function zoneSpace(rnd) {
    let d = '';
    for (let i = 0; i < 46; i++) d += 'M' + (rnd() * 298).toFixed(0) + ' ' + (rnd() * 120).toFixed(0) + 'h1v1h-1z';
    return '<defs>' + sky('g4', '#04050E', '#0D1236') + '</defs>' + bg('g4') + '<path d="' + d + '" fill="#fff" opacity=".85"/>' +
      '<circle cx="226" cy="150" r="64" fill="#D9D6C9"/><circle cx="206" cy="118" r="7" fill="#C2BEAE"/><circle cx="244" cy="124" r="5" fill="#C2BEAE"/><circle cx="226" cy="104" r="3.5" fill="#C2BEAE"/>' +
      // огородный участок на Луне с пугалом
      '<path d="M200 98h14M220 96h14M240 98h12" stroke="#5C8A3A" stroke-width="2" stroke-linecap="round"/><path d="M222 78v16M214 84h16" stroke="#3B2A1E" stroke-width="1.6"/><path d="M219 78l3-5 3 5z" fill="#3B2A1E"/><circle cx="222" cy="78" r="2.4" fill="#E3A33B"/>';
  }

  const ZONE_BUILDERS = [zoneGarden, zoneRoofs, zoneClouds, zoneStrato, zoneSpace];

  // ---------- символы для частиц и обломков ----------
  const DEFS = '<svg class="scb-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<symbol id="scb-spark" viewBox="0 0 10 10"><circle cx="5" cy="5" r="3.2" fill="#FCA13D"/><circle cx="5" cy="5" r="1.6" fill="#FFE08A"/></symbol>' +
    '<symbol id="scb-steam" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="#F3E7CF" opacity=".55"/></symbol>' +
    '<symbol id="scb-clod" viewBox="0 0 10 10"><path d="M1 8Q0 3 4 2t5 5-3 2z" fill="#5B3A1F"/></symbol>' +
    '<symbol id="scb-plank" viewBox="0 0 10 24"><rect x="1" y="1" width="8" height="22" rx="1.5" fill="#8A5A35" stroke="#5B3A1F" stroke-width="1"/></symbol>' +
    '<symbol id="scb-hoop" viewBox="0 0 28 10"><path d="M1 4Q14 8 27 4V7Q14 11 1 7z" fill="#3B3F45"/></symbol>' +
    '<symbol id="scb-cone" viewBox="0 0 14 18"><path d="M1 17L7 1l6 16z" fill="#C8CED4" stroke="#8D949B" stroke-width="1"/></symbol>' +
    '<symbol id="scb-flash" viewBox="0 0 40 40"><circle cx="20" cy="20" r="18" fill="#FFE08A"/><circle cx="20" cy="20" r="10" fill="#FFF6D6"/></symbol>' +
    '</defs></svg>';

  const ROCKET = '<svg class="scb-rocket" viewBox="0 0 32 52" width="40" height="64" aria-hidden="true">' +
    '<g class="scb-flame"><path d="M11 43Q16 62 21 43z" fill="#FCA13D"/><path d="M13.5 43Q16 54 18.5 43z" fill="#FFE08A"/></g>' +
    '<path d="M9 18Q5 30 9 43h14q4-13 0-25z" fill="#8A5A35"/><path d="M12 18v25M16 18v25M20 18v25" stroke="#5B3A1F" stroke-width=".8" fill="none"/>' +
    '<path d="M7 24Q16 27.4 25 24v3Q16 30.4 7 27z" fill="#3B3F45"/><path d="M6.6 36Q16 39.4 25.4 36v3Q16 42.4 6.6 39z" fill="#3B3F45"/>' +
    '<path d="M9 18L16 2l7 16z" fill="#C8CED4"/><path d="M16 2l-4 16H9z" fill="#E4E8EC"/>' +
    '<rect x="11" y="42" width="10" height="3" rx="1" fill="#2B2D31"/></svg>';
  const CROW = '<svg class="scb-crow" viewBox="0 0 30 20" width="30" height="20" aria-hidden="true">' +
    '<path d="M3 9l-3 1.5L4 12z" fill="#3B2A1E"/><ellipse cx="14" cy="11" rx="9" ry="5" fill="#1F1A1A"/><circle cx="7" cy="8" r="3.6" fill="#1F1A1A"/>' +
    '<path d="M4 8l-3.4 1.2L4.4 10z" fill="#E3A33B"/><path d="M17 8q6-8 12-5-3 2-5 6z" fill="#2B2424"/><path d="M12 15.5v3M16 15.5v3" stroke="#3B2A1E" stroke-width="1.2"/></svg>';
  const CHUTE = '<svg class="scb-chute" viewBox="0 0 44 34" width="56" height="43" aria-hidden="true">' +
    '<path d="M2 20Q4 2 22 2t20 18q-5-4-10 0-5-4-10 0-5-4-10 0-5-4-10 0z" fill="#F3E7CF" stroke="#B8323A" stroke-width="1"/>' +
    '<circle cx="12" cy="11" r="2" fill="#B8323A"/><circle cx="22" cy="9" r="2" fill="#B8323A"/><circle cx="32" cy="11" r="2" fill="#B8323A"/>' +
    '<path d="M3 20L21 34M22 18v16M41 20L23 34" stroke="#3B2A1E" stroke-width=".8" fill="none"/></svg>';

  function mount(rt) {
    const rnd = rng(seedOf());
    const root = rt.root;
    root.innerHTML = DEFS +
      '<svg class="scb-bg" viewBox="0 0 300 150" preserveAspectRatio="xMidYMax slice" aria-hidden="true"><g class="scb-par"></g></svg>' +
      '<div class="scb-r">' + CHUTE + ROCKET + '</div>' + '<div class="scb-fx"></div>' + CROW;
    const par = root.querySelector('.scb-par');
    const rocketBox = root.querySelector('.scb-r');
    const fx = root.querySelector('.scb-fx');
    const timers = new Set();
    const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };

    // пул частиц (не больше POOL одновременно): каждый элемент это svg с <use>
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'scb-p');
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
      p.use.setAttribute('href', '#scb-' + symbol);
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
    // Сцена монтируется, пока график ещё скрыт (размер 0), поэтому размер отслеживается: ResizeObserver (иначе при смене фазы), после смены ракета ставится заново по последнему узлу
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
      g.setAttribute('class', 'scb-zone');
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

    let lastSpark = 0;
    let lastSteam = 0;
    let broken = false;

    function place(x, y, px, py) {
      tipNow.x = x; tipNow.y = y; tipNow.px = px; tipNow.py = py;
      const left = 8 + (x / 300) * (W - 16);
      const top = 8 + (y / 150) * (H - 16);
      const dx = ((x - px) / 300) * (W - 16);
      const dy = ((y - py) / 150) * (H - 16);
      const angle = dx === 0 && dy === 0 ? 0 : Math.atan2(dx, -dy) * 57.2958;
      rocketBox.style.transform = 'translate(' + (left - 28).toFixed(1) + 'px,' + (top - 40).toFixed(1) + 'px) rotate(' + angle.toFixed(1) + 'deg)';
      return { left, top, angle };
    }

    function reset() {
      broken = false;
      rocketBox.classList.remove('flying', 'broken', 'open', 'gone');
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
      setZone(0);
      par.style.transform = '';
      place(0, 149, 0, 149);
    }
    reset();

    rt.on('crash:phase', (d) => {
      measure();
      if (d.phase === 'betting') { reset(); return; }
      if (d.phase === 'flight') {
        broken = false;
        rocketBox.classList.remove('broken', 'gone');
        rocketBox.classList.add('flying');
        const left = 8, top = H - 8;
        for (let i = 0; i < 4; i++) {                   // комья земли при взлёте
          const dx = (rnd() - 0.5) * 60;
          particle('clod', '0 0 10 10', left + 4, top - 14, 9, 9,
            [{ transform: 'translate(0,0) rotate(0deg)', opacity: 1 }, { transform: 'translate(' + dx.toFixed(0) + 'px,' + (-(14 + rnd() * 22)).toFixed(0) + 'px) rotate(' + (rnd() * 200).toFixed(0) + 'deg)', opacity: 1, offset: 0.4 },
              { transform: 'translate(' + (dx * 1.4).toFixed(0) + 'px,16px) rotate(320deg)', opacity: 0 }], 700 + i * 60, 'ease-in');
        }
      }
    });

    let px = 0, py = 149;
    rt.on('crash:frame', (f) => {
      if (f.phase === 'result') { if (!broken) setZone(zoneOf(f.x100)); return; }
      if (f.phase !== 'flight') return;
      const pos = place(f.x, f.y, f.px, f.py);
      px = f.x; py = f.y;
      setZone(zoneOf(f.x100));
      if (rt.still()) {
        par.style.transform = '';                // без движения декор не сдвигается: зоны меняются как неподвижные фоны
      } else {
        par.style.transform = 'translate(0,' + Math.min(26, Math.log2(f.x100 / 100) * 4.2).toFixed(1) + 'px)';       // декор медленнее бочки
        const now = performance.now();
        const a = pos.angle * 0.0174533;
        const bx = pos.left - Math.sin(a) * 24 - 3, by = pos.top + Math.cos(a) * 24;      // сопло: позади ракеты вдоль её оси
        if (now - lastSpark > 140 && free() > 6) {
          lastSpark = now;
          particle('spark', '0 0 10 10', bx, by, 6, 6,
            [{ transform: 'translate(0,0)', opacity: 1 }, { transform: 'translate(' + ((rnd() - 0.5) * 18).toFixed(0) + 'px,' + (10 + rnd() * 12).toFixed(0) + 'px)', opacity: 0 }], 480);
        }
        if (now - lastSteam > 330 && free() > 7) {
          lastSteam = now;
          particle('steam', '0 0 20 20', bx - 4, by, 12, 12,
            [{ transform: 'translate(0,0) scale(.6)', opacity: 0.6 }, { transform: 'translate(' + ((rnd() - 0.5) * 12).toFixed(0) + 'px,16px) scale(1.5)', opacity: 0 }], 800, 'ease-out');
        }
      }
    });

    rt.on('crash:crash', (d) => {
      if (broken) return;
      broken = true;
      setZone(zoneOf(d.x100));
      rocketBox.classList.remove('flying', 'open');
      if (!d.fresh) { rocketBox.classList.add('gone'); return; }          // итог без полёта (зашли после краха): бочки уже нет
      if (rt.still()) { rocketBox.classList.add('broken'); return; }      // смена кадра вместо разлёта: бочка лежит набок
      rocketBox.classList.add('gone');
      const left = 8 + (px / 300) * (W - 16), top = 8 + (py / 150) * (H - 16);
      particle('flash', '0 0 40 40', left - 20, top - 20, 40, 40, [{ transform: 'scale(.3)', opacity: 0.95 }, { transform: 'scale(1.6)', opacity: 0 }], 280, 'ease-out');
      const parts = [['hoop', '0 0 28 10', 22, 8], ['hoop', '0 0 28 10', 22, 8], ['plank', '0 0 10 24', 7, 17], ['plank', '0 0 10 24', 7, 17], ['plank', '0 0 10 24', 7, 17], ['plank', '0 0 10 24', 7, 17], ['cone', '0 0 14 18', 14, 18]];
      parts.forEach((p, i) => {
        const fan = -150 + i * 50 + (rnd() - 0.5) * 24;      // веер вверх и в стороны
        const rad = fan * 0.0174533;
        const out = 26 + rnd() * 26;
        const ux = Math.cos(rad) * out, uy = Math.sin(rad) * out;
        const fall = p[0] === 'cone' ? H * 0.9 : 40 + rnd() * 40;
        particle(p[0], p[1], left - p[2] / 2, top - p[3] / 2, p[2], p[3],
          [{ transform: 'translate(0,0) rotate(0deg)', opacity: 1 },
            { transform: 'translate(' + ux.toFixed(0) + 'px,' + uy.toFixed(0) + 'px) rotate(' + (140 + rnd() * 160).toFixed(0) + 'deg)', opacity: 1, offset: 0.35 },
            { transform: 'translate(' + (ux * 1.5).toFixed(0) + 'px,' + (uy + fall).toFixed(0) + 'px) rotate(' + (360 + rnd() * 320).toFixed(0) + 'deg)', opacity: 0 }],
          p[0] === 'cone' ? 1300 : 950, 'cubic-bezier(.2,.6,.4,1)');
      });
    });

    // эффект полного набора «Дачный сезон»: раз в несколько минут над огородом пролетает ворона и садится на забор (только в покое и в зоне огорода, без движения её нет)
    const crow = root.querySelector('.scb-crow');
    let crowBusy = false;
    let crowAnim = null;
    const crowStep = (frames, ms, easing, next) => {
      crowAnim = crow.animate(frames, { duration: ms, easing, fill: 'forwards' });
      crowAnim.onfinish = next;
    };
    rt.on('skin:effect', (d) => {
      if (d.set !== 'dacha' || crowBusy || rt.still() || zone !== 0 || rocketBox.classList.contains('flying')) return;
      crowBusy = true;
      const scale = Math.max(W / 300, H / 150);
      crow.style.left = (W * 0.3).toFixed(0) + 'px';
      crow.style.top = (H - 33 * scale - 18).toFixed(0) + 'px';      // сидит на верхней планке забора
      crow.style.display = 'block';
      const away = 'translate(' + (W * 0.75).toFixed(0) + 'px,' + (-H * 0.5).toFixed(0) + 'px)';
      const gone = 'translate(' + (-W * 0.45).toFixed(0) + 'px,' + (-H * 0.6).toFixed(0) + 'px)';
      crowStep([{ transform: away, opacity: 0 }, { transform: 'translate(0,0)', opacity: 1 }], 1500, 'ease-out', () => {
        crowStep([{ transform: 'translate(0,0) rotate(0deg)' }, { transform: 'translate(0,0) rotate(-5deg)', offset: 0.3 }, { transform: 'translate(0,0) rotate(3deg)', offset: 0.6 }, { transform: 'translate(0,0) rotate(0deg)' }], 4200, 'ease-in-out', () => {
          crowStep([{ transform: 'translate(0,0)', opacity: 1 }, { transform: gone, opacity: 0 }], 1100, 'ease-in', () => { crow.style.display = 'none'; crowBusy = false; });
        });
      });
    });

    rt.on('crash:cashout', () => { if (!broken) rocketBox.classList.add('open'); });      // из бочки раскрывается парашют из старой простыни

    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        if (ro) ro.disconnect();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
        if (crowAnim) { crowAnim.onfinish = null; crowAnim.cancel(); }
        root.textContent = '';
      }
    };
  }

  registerSkinScene('crash_barrel', { slot: 'crash', mount });
})();
