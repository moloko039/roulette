// Сцена скина кено «Патина» (коллекция «Патина», DESIGN.md раздел 8):
// числа на латунных жетонах, декор поля (латунный ободок, тонкие царапины и потёртости из seed = user_id + slot).
// Со стадии износа 2 видны потёртости на жетонах.
// События: 'keno:start', 'keno:draw', 'keno:end' — короткий блеск жетона при розыгрыше.
// Пул не больше 12 частиц; только transform и opacity (WAAPI); не больше 2 зон в DOM; DOM не пересоздаётся на каждый кадр;
// при rt.still() и perf-lite — без движения (смена кадра).
(() => {
  const POOL = 12;

  // Детерминированная «случайность» из seed = user_id + slot
  const seedOf = () => {
    const u = typeof tg !== 'undefined' && tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 1;
    let h = 2166136261;
    const s = String(u) + ':keno_ball:patina';
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

  // Тонкие царапины на поле (появляются со стадии 1+, сеть расширяется к 4)
  function makeScratches(rnd) {
    let res = '';
    for (let i = 0; i < 10; i++) {
      const minStage = i < 2 ? 1 : (i < 5 ? 2 : (i < 8 ? 3 : 4));
      const x1 = Math.round(12 + rnd() * 296);
      const y1 = Math.round(12 + rnd() * 196);
      const len = Math.round(10 + rnd() * 28);
      const ang = rnd() * Math.PI * 2;
      const x2 = Math.round(x1 + Math.cos(ang) * len);
      const y2 = Math.round(y1 + Math.sin(ang) * len);
      const cx = Math.round((x1 + x2) / 2 + (rnd() - 0.5) * 6);
      const cy = Math.round((y1 + y2) / 2 + (rnd() - 0.5) * 6);
      const op = (0.25 + rnd() * 0.35).toFixed(2);
      res += '<path class="skp-scratch s' + minStage + '" data-min-stage="' + minStage + '" d="M' + x1 + ' ' + y1 + ' Q' + cx + ' ' + cy + ' ' + x2 + ' ' + y2 + '" stroke="rgba(255,244,216,' + op + ')" stroke-width="0.8" fill="none" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
    }
    return res;
  }

  // Потёртости на жетонах (появляются со стадии 2+)
  function makeTokenScuffs(rnd) {
    let res = '';
    // Гарантированно формируем потёртости со стадий 2, 3 и 4
    const stages = [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4];
    for (let i = 0; i < stages.length; i++) {
      const minStage = stages[i];
      const ballIdx = Math.floor(rnd() * 40);
      const col = ballIdx % 8;
      const row = Math.floor(ballIdx / 8);
      const cx = 20 + col * 40;
      const cy = 22 + row * 44;
      const r = 16;
      const startAngle = rnd() * Math.PI * 2;
      const arcLen = 0.6 + rnd() * 0.8;
      const endAngle = startAngle + arcLen;
      const x1 = (cx + Math.cos(startAngle) * r).toFixed(1);
      const y1 = (cy + Math.sin(startAngle) * r).toFixed(1);
      const x2 = (cx + Math.cos(endAngle) * r).toFixed(1);
      const y2 = (cy + Math.sin(endAngle) * r).toFixed(1);
      const op = (0.35 + rnd() * 0.35).toFixed(2);
      res += '<path class="skp-scuff s' + minStage + '" data-min-stage="' + minStage + '" d="M' + x1 + ' ' + y1 + ' A' + r + ' ' + r + ' 0 0 1 ' + x2 + ' ' + y2 + '" stroke="rgba(240,214,138,' + op + ')" stroke-width="1.2" fill="none" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
      if (rnd() < 0.5) {
        const sx1 = (cx + (rnd() - 0.5) * 8).toFixed(1);
        const sy1 = (cy + (rnd() - 0.5) * 8).toFixed(1);
        const sx2 = (Number(sx1) + (rnd() - 0.5) * 6).toFixed(1);
        const sy2 = (Number(sy1) + (rnd() - 0.5) * 6).toFixed(1);
        res += '<line class="skp-scuff s' + minStage + '" data-min-stage="' + minStage + '" x1="' + sx1 + '" y1="' + sy1 + '" x2="' + sx2 + '" y2="' + sy2 + '" stroke="rgba(255,244,216,' + op + ')" stroke-width="0.8" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
      }
    }
    return res;
  }

  function mount(rt) {
    const root = rt.root;
    const host = rt.host;
    const rnd = rng(seedOf());

    // Латунный ободок поля + царапины + потёртости на жетонах
    const rimSvg =
      '<rect class="skp-rim" x="1.5" y="1.5" width="317" height="217" rx="8" ry="8" fill="none" stroke="#D9B25C" stroke-width="2.5" vector-effect="non-scaling-stroke"/>' +
      '<rect class="skp-rim-inner" x="4.5" y="4.5" width="311" height="211" rx="6" ry="6" fill="none" stroke="#8C6B2E" stroke-width="1" stroke-dasharray="4 4" opacity="0.6" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="7" cy="7" r="2.2" fill="#D9B25C" stroke="#5C431A" stroke-width="0.8"/>' +
      '<circle cx="313" cy="7" r="2.2" fill="#D9B25C" stroke="#5C431A" stroke-width="0.8"/>' +
      '<circle cx="7" cy="213" r="2.2" fill="#D9B25C" stroke="#5C431A" stroke-width="0.8"/>' +
      '<circle cx="313" cy="213" r="2.2" fill="#D9B25C" stroke="#5C431A" stroke-width="0.8"/>';

    const decorSvg =
      '<svg class="skp-board-svg" viewBox="0 0 320 220" preserveAspectRatio="none" aria-hidden="true">' +
      rimSvg +
      makeScratches(rnd) +
      makeTokenScuffs(rnd) +
      '</svg>';

    // Ровно 2 зоны в root: декор и fx
    root.innerHTML =
      '<div class="skp-decor" aria-hidden="true">' + decorSvg + '</div>' +
      '<div class="skp-fx" aria-hidden="true"></div>';

    const fx = root.querySelector('.skp-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
      return t;
    };

    // Пул частиц (не больше POOL=12): вспышки-блеск на вытянутых жетонах
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElement('div');
      el.className = 'skp-p';
      el.setAttribute('aria-hidden', 'true');
      el.innerHTML =
        '<svg viewBox="0 0 28 28" width="28" height="28" aria-hidden="true">' +
        '<polygon points="14,2 17,11 26,14 17,17 14,26 11,17 2,14 11,11" fill="#FFF4D8" opacity="0.95"/>' +
        '<polygon points="14,6 16,12 22,14 16,16 14,22 12,16 6,14 12,12" fill="#D9B25C"/>' +
        '<circle cx="14" cy="14" r="2.2" fill="#FFFFFF"/>' +
        '</svg>';
      fx.appendChild(el);
      pool.push({ el, busy: false, anim: null });
    }

    function glintBall(n, hit) {
      if (rt.still()) return null;
      const p = pool.find((q) => !q.busy);
      if (!p) return null;

      const hostRect = host.getBoundingClientRect();
      if (!hostRect.width) return null;

      const balls = host.querySelectorAll('.keno-ball');
      const target = balls[n - 1];
      if (!target) return null;

      const targetRect = target.getBoundingClientRect();
      const tx = targetRect.left + targetRect.width / 2 - hostRect.left;
      const ty = targetRect.top + targetRect.height / 2 - hostRect.top;

      p.busy = true;
      p.el.className = 'skp-p on' + (hit ? ' hit' : '');

      const startT = 'translate(' + (tx - 14).toFixed(1) + 'px,' + (ty - 14).toFixed(1) + 'px) scale(0.2) rotate(0deg)';
      const midT = 'translate(' + (tx - 14).toFixed(1) + 'px,' + (ty - 14).toFixed(1) + 'px) scale(' + (hit ? '1.4' : '1.15') + ') rotate(45deg)';
      const endT = 'translate(' + (tx - 14).toFixed(1) + 'px,' + (ty - 14).toFixed(1) + 'px) scale(0.2) rotate(90deg)';

      const frames = [
        { transform: startT, opacity: 0.2 },
        { transform: midT, opacity: 1, offset: 0.4 },
        { transform: endT, opacity: 0, offset: 1.0 }
      ];

      p.anim = p.el.animate(frames, { duration: 240, easing: 'ease-out', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => {
        p.busy = false;
        p.anim = null;
        p.el.className = 'skp-p';
      };

      return p;
    }

    rt.on('keno:start', () => {
      pool.forEach((p) => {
        if (p.anim) p.anim.cancel();
        p.busy = false;
        p.el.className = 'skp-p';
      });
    });

    rt.on('keno:draw', (d) => {
      if (!d || !d.n) return;
      glintBall(d.n, !!d.hit);
    });

    rt.on('keno:end', () => {
      // Итог раунда — ожидание естественного завершения частиц
    });

    return {
      destroy() {
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

  registerSkinScene('keno_patina', { slot: 'keno_ball', mount });
})();
