// Сцена скина стола «Октябрь» (коллекция «Листопад», DESIGN.md раздел 7, подраздел «Стол»):
// мокрый асфальт с полупрозрачными отражениями и градиентами, листья по краям (6-8 штук из seed),
// накопление кучи листьев у края стола с каждым сыгранным раундом (до 10 штук),
// кружащиеся падающие листья в покое (не больше 3 одновременно),
// порыв ветра при полном наборе «Листопад» (skin:effect {set:'leaves'}, 8-10 листьев, 1.8 с).
// Только рисует: ничего не считает и не знает исхода раньше сервера.
// Пул не больше 12 частиц; только transform и opacity; строгая CSP.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const POOL = 12;

  // Счётчик раундов сессии живёт в замыкании модуля вне mount, чтобы переживать
  // размонтирование при уходе на другие экраны, и сбрасывается только при перезагрузке страницы.
  let sessionRounds = 0;

  // Детерминированная «случайность» из seed по tg.initDataUnsafe.user.id
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':table:autumn';
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

  // SVG контуры кленового листа и прожилок (viewBox 0 0 40 40)
  const LEAF_PATH = 'M20 38V30C18 30 14 27 15 24C16 23 18 24 19 25L10 17C8 16 9 14 11 15L17 19L11 9C9 7 11 6 13 8L18 14L16 3C18 1 20 2 20 5C20 2 22 1 24 3L22 14L27 8C29 6 31 7 29 9L23 19L29 15C31 14 32 16 30 17L21 25C22 24 24 23 25 24C26 27 22 30 20 30Z';
  const VEIN_PATH = 'M20 30V6M20 26L12 10M20 26L28 10M20 26L11 17M20 26L29 17';

  // Символы осенних цветов: клён #D9541E, охра #E3A33B, бордо #7E2533
  const DEFS_SVG =
    '<svg class="sta-defs" width="0" height="0" aria-hidden="true"><defs>' +
    '<symbol id="sta-leaf-maple" viewBox="0 0 40 40">' +
    '<path d="' + LEAF_PATH + '" fill="#D9541E" stroke="#7E2533" stroke-width="0.8"/>' +
    '<path d="' + VEIN_PATH + '" stroke="#7E2533" stroke-width="1.1" fill="none" stroke-linecap="round"/>' +
    '</symbol>' +
    '<symbol id="sta-leaf-ochre" viewBox="0 0 40 40">' +
    '<path d="' + LEAF_PATH + '" fill="#E3A33B" stroke="#8E4A10" stroke-width="0.8"/>' +
    '<path d="' + VEIN_PATH + '" stroke="#8E4A10" stroke-width="1.1" fill="none" stroke-linecap="round"/>' +
    '</symbol>' +
    '<symbol id="sta-leaf-burgundy" viewBox="0 0 40 40">' +
    '<path d="' + LEAF_PATH + '" fill="#7E2533" stroke="#4A1520" stroke-width="0.8"/>' +
    '<path d="' + VEIN_PATH + '" stroke="#4A1520" stroke-width="1.1" fill="none" stroke-linecap="round"/>' +
    '</symbol>' +
    '</defs></svg>';

  const LEAF_TYPES = ['maple', 'ochre', 'burgundy'];

  // Смещения и повороты для кучи из 10 листьев у края стола (в правом нижнем углу)
  const PILE_OFFSETS = [
    { dx: 0, dy: 0, rot: -15, sc: 1.0 },
    { dx: -5, dy: -5, rot: 42, sc: 0.95 },
    { dx: 5, dy: -7, rot: -60, sc: 1.04 },
    { dx: -9, dy: 3, rot: 85, sc: 0.92 },
    { dx: 3, dy: 5, rot: -125, sc: 1.02 },
    { dx: -4, dy: -8, rot: 28, sc: 0.96 },
    { dx: 7, dy: 2, rot: 148, sc: 1.06 },
    { dx: -7, dy: -2, rot: -40, sc: 0.94 },
    { dx: 2, dy: 7, rot: 100, sc: 1.03 },
    { dx: -1, dy: -1, rot: -155, sc: 0.98 }
  ];

  function makeLeafSvg(symId) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 40 40');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(NS, 'use');
    use.setAttribute('href', '#' + symId);
    svg.appendChild(use);
    return svg;
  }

  function mount(rt) {
    const root = rt.root;
    const host = rt.host;
    const rnd = rng(seedOf());

    root.innerHTML =
      DEFS_SVG +
      '<div class="sta-edge-leaves" aria-hidden="true"></div>' +
      '<div class="sta-pile" aria-hidden="true"></div>' +
      '<div class="sta-fx" aria-hidden="true"></div>';

    const edgeContainer = root.querySelector('.sta-edge-leaves');
    const pileContainer = root.querySelector('.sta-pile');
    const fxContainer = root.querySelector('.sta-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
      return t;
    };

    let W = 340, H = 620;
    const measure = () => {
      const r = host.getBoundingClientRect();
      if (r.width > 0) { W = r.width; H = r.height; }
    };
    measure();
    window.addEventListener('resize', measure);

    // Пул частиц (не больше POOL=12 одновременно): каждый элемент — svg с <use>
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElementNS(NS, 'svg');
      el.setAttribute('class', 'sta-p');
      el.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS(NS, 'use');
      el.appendChild(use);
      fxContainer.appendChild(el);
      pool.push({ el, use, busy: false, anim: null });
    }

    const free = () => pool.filter((p) => !p.busy).length;

    function particle(sym, vb, x, y, w, h, frames, ms, easing) {
      if (rt.still()) return null;
      const p = pool.find((q) => !q.busy);
      if (!p) return null;
      p.busy = true;
      p.use.setAttribute('href', '#sta-leaf-' + sym);
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

    // 1. Листья по краям стола (6-8 штук, позиции из seed)
    const baseCount = 6 + (rnd() * 3 | 0);
    for (let i = 0; i < baseCount; i++) {
      const edge = i % 4; // 0: верх, 1: право, 2: низ, 3: лево
      const sym = LEAF_TYPES[i % 3];
      const w = 22 + Math.floor(rnd() * 6);
      const rot = Math.floor(rnd() * 360);

      const leafEl = document.createElement('div');
      leafEl.className = 'sta-edge-leaf sta-leaf';
      leafEl.setAttribute('aria-hidden', 'true');
      leafEl.style.width = w + 'px';
      leafEl.style.height = w + 'px';

      if (edge === 0) {
        leafEl.style.top = (1 + rnd() * 4).toFixed(0) + 'px';
        leafEl.style.left = (14 + rnd() * 68).toFixed(1) + '%';
      } else if (edge === 1) {
        leafEl.style.right = (1 + rnd() * 4).toFixed(0) + 'px';
        leafEl.style.top = (15 + rnd() * 66).toFixed(1) + '%';
      } else if (edge === 2) {
        leafEl.style.bottom = (2 + rnd() * 4).toFixed(0) + 'px';
        leafEl.style.left = (12 + rnd() * 52).toFixed(1) + '%';
      } else {
        leafEl.style.left = (1 + rnd() * 4).toFixed(0) + 'px';
        leafEl.style.top = (15 + rnd() * 66).toFixed(1) + '%';
      }
      leafEl.style.transform = 'rotate(' + rot + 'deg)';
      leafEl.appendChild(makeLeafSvg('sta-leaf-' + sym));
      edgeContainer.appendChild(leafEl);
    }

    // 2. Куча накопленных листьев у края стола (до 10 штук)
    function addPileLeaf(idx, animate) {
      if (idx >= 10) return;
      const cfg = PILE_OFFSETS[idx] || PILE_OFFSETS[0];
      const sym = LEAF_TYPES[idx % 3];
      const w = 24;

      const leafEl = document.createElement('div');
      leafEl.className = 'sta-pile-leaf sta-leaf';
      leafEl.setAttribute('aria-hidden', 'true');
      leafEl.style.width = w + 'px';
      leafEl.style.height = w + 'px';
      leafEl.style.right = (10 + cfg.dx) + 'px';
      leafEl.style.bottom = (10 + cfg.dy) + 'px';

      const targetTransform = 'rotate(' + cfg.rot + 'deg) scale(' + cfg.sc + ')';
      leafEl.style.transform = targetTransform;
      leafEl.appendChild(makeLeafSvg('sta-leaf-' + sym));
      pileContainer.appendChild(leafEl);

      if (animate && !rt.still()) {
        try {
          leafEl.animate(
            [
              { transform: 'translate(-35px, -50px) rotate(' + (cfg.rot - 90) + 'deg) scale(0.5)', opacity: 0 },
              { transform: targetTransform, opacity: 1 }
            ],
            { duration: 380, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1)' }
          );
        } catch (e) {}
      }
    }

    // Восстанавливаем ранее накопленную кучу (без анимации)
    for (let i = 0; i < sessionRounds && i < 10; i++) {
      addPileLeaf(i, false);
    }

    // Накопление по round:end
    rt.on('round:end', () => {
      if (sessionRounds >= 10) return;
      const idx = sessionRounds;
      sessionRounds++;
      addPileLeaf(idx, true);
    });

    // 3. Покой: изредка падает один лист (не больше 3 на экране одновременно), кружась
    let idleTimer = null;
    let activeIdle = 0;

    function stopIdle() {
      if (idleTimer) { clearInterval(idleTimer); idleTimer = null; }
    }

    function spawnIdleLeaf() {
      if (rt.still() || activeIdle >= 3 || free() < 4) return;
      measure();
      activeIdle++;
      const sym = LEAF_TYPES[Math.floor(rnd() * 3)];
      const sx = 15 + rnd() * (W - 35);
      const sw = 18 + rnd() * 6;
      const dx1 = (rnd() - 0.5) * 45;
      const dx2 = dx1 + (rnd() - 0.5) * 60;
      const rot1 = 90 + Math.floor(rnd() * 120);
      const rot2 = 240 + Math.floor(rnd() * 160);

      const p = particle(sym, '0 0 40 40', sx, -25, sw, sw,
        [
          { transform: 'translate(0, 0) rotate(0deg)', opacity: 0 },
          { transform: 'translate(0, 15px) rotate(25deg)', opacity: 0.85, offset: 0.08 },
          { transform: 'translate(' + dx1.toFixed(0) + 'px, ' + (H * 0.45).toFixed(0) + 'px) rotate(' + rot1 + 'deg)', opacity: 0.85, offset: 0.5 },
          { transform: 'translate(' + dx2.toFixed(0) + 'px, ' + (H + 25).toFixed(0) + 'px) rotate(' + rot2 + 'deg)', opacity: 0 }
        ],
        3400 + rnd() * 800, 'cubic-bezier(.25, .4, .35, 1)');

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
        if (!rt.still()) spawnIdleLeaf();
        else stopIdle();
      }, 4500);
    }
    startIdle();

    // 4. Эффект полного набора «Листопад» (skin:effect {set:'leaves'}):
    // 8-10 листьев пролетают поперёк стола за 1.8 с
    rt.on('skin:effect', (d) => {
      if (!d || d.set !== 'leaves' || rt.still()) return;
      stopIdle();
      measure();
      const count = Math.min(free(), 8 + Math.floor(rnd() * 3));
      for (let i = 0; i < count; i++) {
        const startY = 15 + rnd() * (H - 45);
        const endY = startY + (rnd() - 0.5) * 70;
        const sym = LEAF_TYPES[i % 3];
        const s = 18 + rnd() * 8;
        const delay = i * 25;
        const dur = 1800 - delay;
        later(() => {
          particle(sym, '0 0 40 40', -35, startY, s, s,
            [
              { transform: 'translate(0, 0) rotate(' + (rnd() * 60).toFixed(0) + 'deg)', opacity: 0 },
              { transform: 'translate(' + ((W + 70) * 0.25).toFixed(0) + 'px, -15px) rotate(' + (120 + rnd() * 80).toFixed(0) + 'deg)', opacity: 0.95, offset: 0.25 },
              { transform: 'translate(' + ((W + 70) * 0.7).toFixed(0) + 'px, 12px) rotate(' + (240 + rnd() * 80).toFixed(0) + 'deg)', opacity: 0.95, offset: 0.7 },
              { transform: 'translate(' + (W + 70).toFixed(0) + 'px, ' + (endY - startY).toFixed(0) + 'px) rotate(' + (360 + rnd() * 120).toFixed(0) + 'deg)', opacity: 0 }
            ],
            dur, 'cubic-bezier(.25, .5, .4, 1)');
        }, delay);
      }
      later(() => {
        if (!rt.still()) startIdle();
      }, 2000);
    });

    rt.on('skin:paused', (paused) => {
      if (paused) stopIdle();
      else if (!rt.still()) startIdle();
    });

    rt.on('skin:lite', () => {
      stopIdle();
      pool.forEach((p) => { if (p.anim) p.anim.cancel(); });
    });

    return {
      destroy() {
        window.removeEventListener('resize', measure);
        stopIdle();
        timers.forEach(clearTimeout);
        timers.clear();
        pool.forEach((p) => {
          if (p.anim) p.anim.cancel();
          p.busy = false;
        });
        root.textContent = '';
      }
    };
  }

  registerSkinScene('table_autumn', { slot: 'table', mount });
})();
