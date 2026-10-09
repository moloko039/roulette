// Сцена скина кено «Бочонки» (коллекция «Дачный сезон», DESIGN.md раздел 2):
// деревянные бочонки лото, картонные карточки под выбранными, холщовый мешок в углу поля,
// выкатывающиеся бочонки при розыгрыше, стук и пуговицы при совпадениях, сникающий мешок при нуле совпадений.
// Только рисует: ничего не считает и не знает исхода раньше сервера.
// События: 'keno:start', 'keno:draw', 'keno:end'. Пул не больше 12 частиц; только transform и opacity.
(() => {
  const POOL = 12;

  const BAG_SVG =
    '<div class="skl-bag" aria-hidden="true">' +
    '<svg class="skl-bag-svg" viewBox="0 0 56 56" width="56" height="56" aria-hidden="true">' +
    '<ellipse cx="28" cy="46" rx="22" ry="7" fill="#3B2A1E" opacity="0.25"/>' +
    '<path d="M12 44 C8 32, 14 18, 26 18 C28 14, 34 12, 42 16 C48 20, 52 32, 48 42 C45 48, 20 50, 12 44 Z" fill="#C8A870" stroke="#8A6A3E" stroke-width="1.2"/>' +
    '<path d="M18 42 C22 34, 28 26, 32 20 M26 44 C30 36, 36 30, 40 24 M15 32 C22 34, 32 30, 44 34" fill="none" stroke="#A88248" stroke-width="1" opacity="0.6"/>' +
    '<path d="M24 19 Q30 22 36 17" fill="none" stroke="#5C3A1E" stroke-width="2.2" stroke-linecap="round"/>' +
    '<circle cx="30" cy="20" r="2.2" fill="#4A2E16"/>' +
    '<path d="M30 21 Q27 27 25 31 M31 21 Q34 26 33 30" fill="none" stroke="#5C3A1E" stroke-width="1.6" stroke-linecap="round"/>' +
    '<ellipse cx="32" cy="16" rx="8" ry="4" fill="#3B2512" transform="rotate(-15 32 16)"/>' +
    '<rect x="28" y="12" width="9" height="7" rx="2" fill="#E3B878" stroke="#8A5E30" stroke-width="0.8" transform="rotate(-10 32 15)"/>' +
    '</svg>' +
    '</div>';

  function mount(rt) {
    const root = rt.root;
    const host = rt.host;
    root.innerHTML = BAG_SVG + '<div class="skl-fx" aria-hidden="true"></div>';
    const bagEl = root.querySelector('.skl-bag');
    const fx = root.querySelector('.skl-fx');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
      return t;
    };

    // Пул частиц (не больше POOL=12 одновременно): каждый элемент — бочонок с числом
    const pool = [];
    for (let i = 0; i < POOL; i++) {
      const el = document.createElement('div');
      el.className = 'skl-p';
      el.setAttribute('aria-hidden', 'true');
      el.innerHTML =
        '<svg viewBox="0 0 32 24" width="32" height="24">' +
        '<rect x="1" y="1" width="30" height="22" rx="6" fill="#E3B878" stroke="#FCA13D" stroke-width="2"/>' +
        '<line x1="3" y1="4" x2="29" y2="4" stroke="#5C3A1E" stroke-width="1"/>' +
        '<line x1="3" y1="20" x2="29" y2="20" stroke="#5C3A1E" stroke-width="1"/>' +
        '<text class="skl-p-num" x="16" y="16" text-anchor="middle" font-size="12" font-weight="600" font-family="sans-serif" fill="#2E1805"></text>' +
        '</svg>';
      fx.appendChild(el);
      const numText = el.querySelector('.skl-p-num');
      pool.push({ el, numText, busy: false, anim: null });
    }

    function launchBarrel(n) {
      if (rt.still()) return null;
      const p = pool.find((q) => !q.busy);
      if (!p) return null;

      const hostRect = host.getBoundingClientRect();
      if (!hostRect.width) return null;

      const bagRect = bagEl.getBoundingClientRect();
      const bx = bagRect.left + bagRect.width / 2 - hostRect.left;
      const by = bagRect.top + bagRect.height / 2 - hostRect.top;

      const balls = host.querySelectorAll('.keno-ball');
      const target = balls[n - 1];
      if (!target) return null;
      const targetRect = target.getBoundingClientRect();
      const tx = targetRect.left + targetRect.width / 2 - hostRect.left;
      const ty = targetRect.top + targetRect.height / 2 - hostRect.top;

      p.busy = true;
      p.numText.textContent = String(n);
      p.el.classList.add('on');

      const startTransform = 'translate(' + (bx - 16).toFixed(1) + 'px,' + (by - 12).toFixed(1) + 'px) scale(0.35) rotate(-35deg)';
      const midX = (bx + tx) / 2 - 16;
      const midY = (by + ty) / 2 - 12 - Math.min(32, Math.hypot(tx - bx, ty - by) * 0.15);
      const midTransform = 'translate(' + midX.toFixed(1) + 'px,' + midY.toFixed(1) + 'px) scale(1.1) rotate(15deg)';
      const endTransform = 'translate(' + (tx - 16).toFixed(1) + 'px,' + (ty - 12).toFixed(1) + 'px) scale(1) rotate(0deg)';

      const frames = [
        { transform: startTransform, opacity: 0.4 },
        { transform: midTransform, opacity: 1, offset: 0.5 },
        { transform: endTransform, opacity: 1, offset: 0.85 },
        { transform: endTransform, opacity: 0, offset: 1.0 }
      ];

      p.anim = p.el.animate(frames, { duration: 230, easing: 'ease-out', fill: 'forwards' });
      p.anim.onfinish = p.anim.oncancel = () => {
        p.busy = false;
        p.anim = null;
        p.el.classList.remove('on');
      };

      if (!bagEl.classList.contains('slump')) {
        try {
          bagEl.animate(
            [
              { transform: 'scale(1) rotate(0deg)' },
              { transform: 'scale(1.08) rotate(-4deg)' },
              { transform: 'scale(1) rotate(0deg)' }
            ],
            { duration: 180, easing: 'ease-out' }
          );
        } catch (e) {}
      }

      return p;
    }

    rt.on('keno:start', () => {
      bagEl.classList.remove('slump');
      pool.forEach((p) => {
        if (p.anim) p.anim.cancel();
      });
    });

    rt.on('keno:draw', (d) => {
      if (!d || !d.n) return;
      launchBarrel(d.n);
    });

    rt.on('keno:end', (d) => {
      if (d && d.hits === 0 && d.picks > 0) {
        bagEl.classList.add('slump');
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

  registerSkinScene('keno_lotto', { slot: 'keno_ball', mount });
})();
