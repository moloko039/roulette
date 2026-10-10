// Сцена скина краша «Пустота» (коллекция «Пустота», DESIGN.md раздел 5):
// линия графика 1 px, белая, никакого декора; множитель огромный тонким начертанием.
// Зоны: на ×2 линия 1.5 px, на ×10 — 2 px, на ×50 — фон еле заметно светлеет к графиту (#111111).
// Событие «crash» (crash:crash): линия обрывается, число исчезает мгновенно,
// на месте обрыва остаётся точка, которая тихо гаснет за 1 с.
// Событие «cashout» (забрать): сумма выигрыша появляется тёплым белым #F5EBDD и задерживается.
// В DOM одновременно не больше двух зон; без движения (rt.still(), perf-lite) события без задержек и анимаций.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const ZONES = [200, 1000, 5000]; // границы зон в сотых: ×2, ×10, ×50
  const FADE_MS = 480;

  function zone0() {
    return '';
  }
  function zone1() {
    return '';
  }
  function zone2() {
    return '';
  }
  function zone3() {
    return '<rect width="300" height="150" fill="#111111" opacity=".35"/>';
  }
  const ZONE_BUILDERS = [zone0, zone1, zone2, zone3];

  function mount(rt) {
    const root = rt.root;
    root.innerHTML =
      '<svg class="scvc-svg" viewBox="0 0 300 150" preserveAspectRatio="none" aria-hidden="true">' +
      '<g class="scvc-par"></g>' +
      '<g class="scvc-cashout-wrap"></g>' +
      '<g class="scvc-dot-wrap"></g>' +
      '</svg>' +
      '<div class="scvc-win" aria-hidden="true"><span class="scvc-win-text"></span></div>';

    const par = root.querySelector('.scvc-par');
    const cashoutWrap = root.querySelector('.scvc-cashout-wrap');
    const dotWrap = root.querySelector('.scvc-dot-wrap');
    const winBox = root.querySelector('.scvc-win');
    const winTextEl = root.querySelector('.scvc-win-text');
    const curveEl = rt.host.querySelector('.cr-curve');

    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
      timers.add(t);
      return t;
    };

    const tipNow = { x: 0, y: 149, px: 0, py: 149 };
    let broken = false;
    let dotAnim = null;
    let winAnim = null;

    // Зоны: в DOM одновременно не больше двух зон
    let zone = -1;
    let zoneGroup = null;
    const zoneOf = (x100) => ZONES.reduce((n, b) => n + (x100 >= b ? 1 : 0), 0);
    const STROKE_WIDTHS = ['1px', '1.5px', '2px', '2px'];

    function setZone(i) {
      if (i === zone) return;
      const prev = zoneGroup;
      par.querySelectorAll('.scvc-zone').forEach((old) => { if (old !== prev) old.remove(); });
      zone = i;
      rt.host.dataset.zone = String(i);
      if (curveEl) curveEl.style.strokeWidth = STROKE_WIDTHS[i];

      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', 'scvc-zone');
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

    function reset() {
      broken = false;
      rt.host.classList.remove('cr-void-crashed', 'cr-void-cashed');
      if (dotAnim) { dotAnim.cancel(); dotAnim = null; }
      if (winAnim) { winAnim.cancel(); winAnim = null; }
      dotWrap.innerHTML = '';
      cashoutWrap.innerHTML = '';
      winBox.classList.remove('show');
      winBox.style.opacity = '';
      winBox.style.transform = '';
      tipNow.x = 0; tipNow.y = 149; tipNow.px = 0; tipNow.py = 149;
      setZone(0);
    }

    reset();

    rt.on('crash:phase', (d) => {
      if (d.phase === 'betting') {
        reset();
        return;
      }
      if (d.phase === 'flight') {
        reset();
      }
    });

    rt.on('crash:frame', (f) => {
      if (typeof f.x === 'number') {
        tipNow.x = f.x; tipNow.y = f.y; tipNow.px = f.px; tipNow.py = f.py;
      }
      if (f.phase === 'result') {
        if (!broken) setZone(zoneOf(f.x100));
        return;
      }
      if (f.phase !== 'flight') return;
      setZone(zoneOf(f.x100));
    });

    rt.on('crash:crash', (d) => {
      if (broken) return;
      broken = true;
      setZone(zoneOf(d.x100));
      rt.host.classList.add('cr-void-crashed');

      // На месте обрыва остаётся точка
      dotWrap.innerHTML = '';
      const dot = document.createElementNS(NS, 'circle');
      dot.setAttribute('class', 'scvc-dot');
      dot.setAttribute('cx', tipNow.x.toFixed(1));
      dot.setAttribute('cy', tipNow.y.toFixed(1));
      dot.setAttribute('r', '2.5');
      dot.setAttribute('fill', '#FFFFFF');
      dotWrap.appendChild(dot);

      if (rt.still()) {
        dot.remove();
        return;
      }

      if (dotAnim) { dotAnim.cancel(); dotAnim = null; }
      dotAnim = dot.animate([
        { opacity: 1 },
        { opacity: 0 }
      ], { duration: 1000, easing: 'ease-out', fill: 'forwards' });

      dotAnim.onfinish = dotAnim.oncancel = () => {
        dot.remove();
        dotAnim = null;
      };
    });

    rt.on('crash:cashout', (d) => {
      if (broken) return;
      rt.host.classList.add('cr-void-cashed');

      // Отметка точки на графике
      cashoutWrap.innerHTML = '';
      const mark = document.createElementNS(NS, 'circle');
      mark.setAttribute('class', 'scvc-cash-mark');
      mark.setAttribute('cx', tipNow.x.toFixed(1));
      mark.setAttribute('cy', tipNow.y.toFixed(1));
      mark.setAttribute('r', '3');
      mark.setAttribute('fill', '#F5EBDD');
      cashoutWrap.appendChild(mark);

      // Сумма выигрыша тёплым белым #F5EBDD
      let winText = '';
      const me = (typeof crMe === 'function' ? crMe() : null) || (window.cr && window.cr.state && window.cr.state.me);
      if (me && typeof me.payout === 'number') {
        const profit = me.payout - (me.bet || 0);
        const amount = profit > 0 ? profit : me.payout;
        winText = '+' + (typeof formatCompact === 'function' ? formatCompact(amount) : String(amount));
      } else if (d && d.amount) {
        winText = '+' + d.amount;
      } else if (d && d.x100) {
        winText = '×' + (d.x100 / 100).toFixed(2);
      } else {
        winText = '+100';
      }

      winTextEl.textContent = winText;
      winBox.classList.add('show');

      if (rt.still()) {
        winBox.style.opacity = '1';
        later(() => {
          winBox.classList.remove('show');
          winBox.style.opacity = '';
        }, 2000);
        return;
      }

      if (winAnim) { winAnim.cancel(); winAnim = null; }
      winAnim = winBox.animate([
        { opacity: 0, transform: 'scale(0.92)' },
        { opacity: 1, transform: 'scale(1)', offset: 0.15 },
        { opacity: 1, transform: 'scale(1)', offset: 0.8 },
        { opacity: 0, transform: 'scale(1.04)', offset: 1 }
      ], { duration: 2500, easing: 'ease-out', fill: 'forwards' });

      winAnim.onfinish = winAnim.oncancel = () => {
        winBox.classList.remove('show');
        winBox.style.opacity = '';
        winBox.style.transform = '';
        winAnim = null;
      };
    });

    return {
      destroy() {
        timers.forEach(clearTimeout);
        timers.clear();
        if (dotAnim) { dotAnim.cancel(); dotAnim = null; }
        if (winAnim) { winAnim.cancel(); winAnim = null; }
        rt.host.classList.remove('cr-void-crashed', 'cr-void-cashed');
        rt.host.removeAttribute('data-zone');
        if (curveEl) curveEl.style.strokeWidth = '';
        root.textContent = '';
      }
    };
  }

  registerSkinScene('void_crash', { slot: 'crash', mount });
})();
