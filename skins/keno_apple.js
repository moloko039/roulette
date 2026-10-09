// Сцена скина кено «Яблоки» (коллекция «Листопад», DESIGN.md раздел 7, подраздел «Кено»):
// числа на красных, жёлтых и зелёных яблоках, ветка с листьями над полем,
// вылетающие яблоки-частицы при розыгрыше с падением и подскоками,
// плетёная корзина под полем справа, наполняющаяся при совпадениях.
// Только рисует: ничего не считает и не знает исхода раньше сервера.
// События: 'keno:start', 'keno:draw', 'keno:end'. Пул не больше 12 частиц; только transform и opacity.
(() => {
  const POOL = 12;

  const BRANCH_SVG =
    '<div class="ska-branch" aria-hidden="true">' +
    '<svg class="ska-branch-svg" viewBox="0 0 360 44" width="100%" height="44" aria-hidden="true">' +
    '<path d="M-10 12 Q60 18 120 14 T240 16 T370 10" fill="none" stroke="#4A2810" stroke-width="6" stroke-linecap="round"/>' +
    '<path d="M-10 11 Q60 17 120 13 T240 15 T370 9" fill="none" stroke="#6B3E1C" stroke-width="2.4" stroke-linecap="round"/>' +
    '<path d="M80 16 Q95 24 110 26" fill="none" stroke="#4A2810" stroke-width="3" stroke-linecap="round"/>' +
    '<path d="M190 15 Q210 25 225 22" fill="none" stroke="#4A2810" stroke-width="2.5" stroke-linecap="round"/>' +
    '<path d="M280 13 Q300 22 315 20" fill="none" stroke="#4A2810" stroke-width="2.5" stroke-linecap="round"/>' +
    '<path d="M70 12 C60 4 75 0 85 8 C80 14 74 14 70 12 Z" fill="#E3A33B"/>' +
    '<path d="M108 26 C118 34 128 30 125 22 C118 20 112 22 108 26 Z" fill="#D9541E"/>' +
    '<path d="M140 14 C150 6 162 10 158 18 C150 20 144 18 140 14 Z" fill="#4E8C22"/>' +
    '<path d="M180 15 C175 6 190 4 195 12 C190 18 184 17 180 15 Z" fill="#E3A33B"/>' +
    '<path d="M225 22 C235 30 245 26 242 18 C235 17 228 19 225 22 Z" fill="#7E2533"/>' +
    '<path d="M270 13 C280 4 292 8 288 16 C280 18 274 16 270 13 Z" fill="#D9541E"/>' +
    '<path d="M315 20 C325 28 335 24 332 16 C324 15 318 17 315 20 Z" fill="#4E8C22"/>' +
    '<g transform="translate(100,24)"><path d="M0 0 Q2 4 4 6" stroke="#4A2E12" stroke-width="1.2" fill="none"/><circle cx="5" cy="11" r="5" fill="#A8221D"/><path d="M5 6 Q7 4 8 5 Q7 8 5 6" fill="#4E8C22"/></g>' +
    '<g transform="translate(260,16)"><path d="M0 0 Q1 3 3 5" stroke="#4A2E12" stroke-width="1.2" fill="none"/><circle cx="4" cy="10" r="4.5" fill="#E5A01C"/><path d="M4 5 Q6 3 7 4 Q6 7 4 5" fill="#4E8C22"/></g>' +
    '<g transform="translate(170,18)"><path d="M0 0 Q1 3 3 5" stroke="#4A2E12" stroke-width="1.2" fill="none"/><circle cx="4" cy="10" r="4.5" fill="#44751A"/><path d="M4 5 Q6 3 7 4 Q6 7 4 5" fill="#68B02C"/></g>' +
    '</svg>' +
    '</div>';

  const APPLE_ICON_SVG =
    '<svg viewBox="0 0 16 16" width="16" height="16">' +
    '<path d="M8 4 C7.7 2, 9 0.5, 10.5 0.5" fill="none" stroke="#5A3414" stroke-width="1.2" stroke-linecap="round"/>' +
    '<path d="M8.5 2 C11 1, 12 2, 12 2.5 C11.5 4, 9.5 3.5, 8.5 2.5 Z" fill="#4E8C22"/>' +
    '<path class="ska-ba-body" d="M8 4.5 C6 3.5, 2 4.5, 2 9 C2 13, 5.5 15.5, 8 15 C10.5 15.5, 14 13, 14 9 C14 4.5, 10 3.5, 8 4.5 Z" stroke-width="0.8"/>' +
    '</svg>';

  let basketApplesHtml = '';
  for (let i = 0; i < 10; i++) {
    basketApplesHtml += '<span class="ska-b-apple" data-idx="' + i + '">' + APPLE_ICON_SVG + '</span>';
  }

  const BASKET_SVG =
    '<div class="ska-basket" aria-hidden="true">' +
    '<svg class="ska-basket-svg" viewBox="0 0 86 48" width="86" height="48" aria-hidden="true">' +
    '<ellipse cx="43" cy="45" rx="34" ry="3" fill="#20150A" opacity="0.3"/>' +
    '<path d="M12 18 C14 36, 18 44, 43 44 C68 44, 72 36, 74 18 C74 16, 12 16, 12 18 Z" fill="#8C5828" stroke="#5A3414" stroke-width="1.4"/>' +
    '<path d="M16 24 Q43 28 70 24 M18 31 Q43 35 68 31 M22 38 Q43 41 64 38" fill="none" stroke="#B87E3E" stroke-width="1.2" opacity="0.8"/>' +
    '<path d="M22 18 L25 43 M32 18 L34 44 M43 18 L43 44 M54 18 L52 44 M64 18 L61 43" fill="none" stroke="#5A3414" stroke-width="1" opacity="0.55"/>' +
    '<ellipse cx="43" cy="18" rx="32" ry="5" fill="#A86E32" stroke="#5A3414" stroke-width="1.4"/>' +
    '<ellipse cx="43" cy="18" rx="28" ry="3.5" fill="#4A2B10"/>' +
    '<path d="M11 18 C7 14, 8 8, 14 11 C15 12, 13 16, 12 18" fill="none" stroke="#5A3414" stroke-width="2" stroke-linecap="round"/>' +
    '<path d="M75 18 C79 14, 78 8, 72 11 C71 12, 73 16, 74 18" fill="none" stroke="#5A3414" stroke-width="2" stroke-linecap="round"/>' +
    '</svg>' +
    '<div class="ska-basket-apples" aria-hidden="true">' +
    basketApplesHtml +
    '</div>' +
    '</div>';

  function mount(rt) {
    const root = rt.root;
    const host = rt.host;
    root.innerHTML = BRANCH_SVG + BASKET_SVG + '<div class="ska-fx" aria-hidden="true"></div>';
    const branchEl = root.querySelector('.ska-branch');
    const basketEl = root.querySelector('.ska-basket');
    const basketApples = root.querySelectorAll('.ska-b-apple');
    const fx = root.querySelector('.ska-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
      return t;
    };

    let hitCount = 0;

    // Пул частиц (не больше POOL=12 одновременно): каждый элемент — яблоко с числом
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElement('div');
      el.className = 'ska-p';
      el.setAttribute('aria-hidden', 'true');
      el.innerHTML =
        '<svg viewBox="0 0 36 36" width="32" height="32">' +
        '<path class="ska-p-stem" d="M18 10 C17.5 5, 20 2, 23 1" fill="none" stroke="#5A3414" stroke-width="2.2" stroke-linecap="round"/>' +
        '<path class="ska-p-leaf" d="M19 5 C24 3, 27 5, 27 6 C26 9, 22 8.5, 19 6 Z" fill="#4E8C22" stroke="#366416" stroke-width="0.7"/>' +
        '<path class="ska-p-body" d="M18 10 C14 8, 5 10, 5 20 C5 29, 13 34, 18 33 C23 34, 31 29, 31 20 C31 10, 22 8, 18 10 Z" stroke-width="1.4"/>' +
        '<path d="M9 16 C8 20, 10 24, 10 25" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="1.5" stroke-linecap="round"/>' +
        '<text class="ska-p-num" x="18" y="24" text-anchor="middle" font-size="13" font-weight="700" font-family="sans-serif"></text>' +
        '</svg>';
      fx.appendChild(el);
      const numText = el.querySelector('.ska-p-num');
      pool.push({ el, numText, busy: false, anim: null });
    }

    function colorClass(n) {
      const m = n % 3;
      return m === 1 ? 'c-red' : m === 2 ? 'c-yellow' : 'c-green';
    }

    function launchApple(n) {
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

      const branchRect = branchEl.getBoundingClientRect();
      const bx = Math.max(16, Math.min(hostRect.width - 16, tx + (((n * 13) % 25) - 12)));
      const by = branchRect.top + branchRect.height * 0.7 - hostRect.top;

      p.busy = true;
      p.numText.textContent = String(n);
      p.el.className = 'ska-p on ' + colorClass(n);

      const rot = (n % 2 === 0 ? 1 : -1) * 14;
      const startT = 'translate(' + (bx - 16).toFixed(1) + 'px,' + (by - 16).toFixed(1) + 'px) scale(0.65) rotate(' + rot + 'deg)';
      const hitT = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 16).toFixed(1) + 'px) scale(1.08) rotate(0deg)';
      const bounce1T = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 24).toFixed(1) + 'px) scale(0.96) rotate(' + (-rot * 0.5) + 'deg)';
      const land1T = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 16).toFixed(1) + 'px) scale(1.02) rotate(0deg)';
      const bounce2T = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 19).toFixed(1) + 'px) scale(0.98) rotate(0deg)';
      const endT = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 16).toFixed(1) + 'px) scale(1) rotate(0deg)';

      const frames = [
        { transform: startT, opacity: 0.5 },
        { transform: hitT, opacity: 1, offset: 0.55 },
        { transform: bounce1T, opacity: 1, offset: 0.72 },
        { transform: land1T, opacity: 1, offset: 0.85 },
        { transform: bounce2T, opacity: 1, offset: 0.93 },
        { transform: endT, opacity: 1, offset: 0.97 },
        { transform: endT, opacity: 0, offset: 1.0 }
      ];

      p.anim = p.el.animate(frames, { duration: 280, easing: 'ease-out', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => {
        p.busy = false;
        p.anim = null;
        p.el.classList.remove('on');
      };

      try {
        branchEl.animate(
          [
            { transform: 'scale(1) rotate(0deg)' },
            { transform: 'scale(1.01) rotate(-1deg)' },
            { transform: 'scale(1) rotate(0deg)' }
          ],
          { duration: 180, easing: 'ease-out' }
        );
      } catch (e) {}

      return p;
    }

    function addHitApple(n) {
      if (hitCount >= 10) return;
      const apple = basketApples[hitCount];
      if (!apple) return;
      hitCount++;
      apple.className = 'ska-b-apple on ' + colorClass(n);
      if (!rt.still()) {
        try {
          apple.animate(
            [
              { transform: 'translateY(-16px) scale(0.4)', opacity: 0 },
              { transform: 'translateY(2px) scale(1.15)', opacity: 1, offset: 0.7 },
              { transform: 'translateY(0) scale(1)', opacity: 1 }
            ],
            { duration: 240, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1.2)' }
          );
          basketEl.animate(
            [
              { transform: 'scale(1)' },
              { transform: 'scale(1.08, 0.92)' },
              { transform: 'scale(0.96, 1.03)' },
              { transform: 'scale(1)' }
            ],
            { duration: 220, easing: 'ease-out' }
          );
        } catch (e) {}
      }
    }

    rt.on('keno:start', () => {
      hitCount = 0;
      basketApples.forEach((a) => {
        a.className = 'ska-b-apple';
      });
      pool.forEach((p) => {
        if (p.anim) p.anim.cancel();
      });
    });

    rt.on('keno:draw', (d) => {
      if (!d || !d.n) return;
      launchApple(d.n);
      if (d.hit) addHitApple(d.n);
    });

    rt.on('keno:end', (d) => {
      if (d && typeof d.hits === 'number' && hitCount !== d.hits) {
        hitCount = d.hits;
        basketApples.forEach((a, i) => {
          if (i < hitCount) {
            if (!a.classList.contains('on')) a.className = 'ska-b-apple on c-red';
          } else {
            a.className = 'ska-b-apple';
          }
        });
      }
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

  registerSkinScene('keno_apple', { slot: 'keno_ball', mount });
})();
