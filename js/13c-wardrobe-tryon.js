// #region Примерка скина (UI_REVIEW, магазин)
// В окне предпросмотра кнопка «Примерить» запускает 10 секунд короткой игры на образце слота: игровые классы те же, поэтому палитру и рисунок предмета видно
// в движении. Только рисунок: ничего не считается и на сервер не уходит. Сцены-модули скинов (skins/<код>.js) живут на экране игры и в образец не монтируются.
const WD_TRY_MS = 10000;
const WD_TRY_SLOTS = ['chip', 'card_back', 'table', 'mine_icons', 'keno_ball', 'crash'];
let wdTry = null;       // {timers, raf, item}

function wdTryStop() {
  if (!wdTry) return;
  wdTry.timers.forEach((t) => clearTimeout(t));
  if (wdTry.raf) cancelAnimationFrame(wdTry.raf);
  wdTry = null;
}

function wdTryButtonSync() {
  const btn = document.getElementById('wd-prev-try');
  if (!btn) return;
  const item = wd.preview;
  btn.hidden = !item || !WD_TRY_SLOTS.includes(item.slot);
  btn.textContent = wdTry ? 'Стоп' : 'Примерить';
}

const wdTryAt = (ms, fn) => { if (wdTry) wdTry.timers.push(setTimeout(() => { if (wdTry) fn(); }, ms)); };
const wdTryRestart = (el, cls) => { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); };       // повтор анимации на том же элементе

function wdTryScript(scene, item) {
  const q = (sel) => Array.from(scene.querySelectorAll(sel));
  if (item.slot === 'chip') {
    const chips = q('.chip');
    const cell = document.createElement('div');
    cell.className = 'cell black';
    const slot = document.createElement('div');
    slot.className = 'stack-slot';
    slot.style.setProperty('inset', '0');
    const stack = document.createElement('i');
    stack.className = 'stack';
    slot.appendChild(stack);
    cell.appendChild(slot);
    scene.appendChild(cell);
    ['10', '100', '500', '100'].forEach((amount, i) => {
      wdTryAt(500 + i * 2400, () => {
        chips.forEach((c) => c.setAttribute('aria-pressed', String(c.getAttribute('data-amount') === amount)));
        stack.setAttribute('data-amount', amount);
        stack.textContent = amount;
        wdTryRestart(stack, 'drop');
      });
    });
  } else if (item.slot === 'card_back') {
    const [back, face] = q('.bj-card');
    if (!back || !face) return;
    face.hidden = true;
    for (let i = 0; i < 4; i++) {
      wdTryAt(900 + i * 2300, () => {
        const toFace = i % 2 === 0;
        back.hidden = toFace;
        face.hidden = !toFace;
        wdTryRestart(toFace ? face : back, 'flip');
      });
    }
  } else if (item.slot === 'table') {
    const cells = q('.cell');
    cells.forEach((c, i) => { c.style.setProperty('--i', String(i)); });
    for (let i = 0; i < 6; i++) {
      wdTryAt(600 + i * 1600, () => {
        cells.forEach((c) => c.classList.remove('hit'));
        const c = cells[i % cells.length];
        wdTryRestart(c, 'hit');
      });
    }
  } else if (item.slot === 'mine_icons') {
    const cells = q('.mines-cell');
    const safe = cells.find((c) => c.classList.contains('safe'));
    const mine = cells.find((c) => c.classList.contains('mine'));
    const empty = cells.find((c) => c !== safe && c !== mine);
    [empty, safe, mine].forEach((c) => { if (c) { c.classList.remove('safe', 'mine'); c.classList.add('closed'); } });
    const steps = [[empty, 'safe'], [safe, 'safe'], [mine, 'mine']];
    steps.forEach(([c, kind], i) => {
      if (!c) return;
      wdTryAt(900 + i * 1700, () => {
        c.classList.remove('closed');
        c.classList.add(kind);
        if (kind === 'safe') wdTryRestart(c, 'fresh');
        else wdTryRestart(scene, 'shake');
      });
    });
    wdTryAt(7600, () => { cells.forEach((c) => c.classList.remove('safe', 'mine', 'fresh')); });
  } else if (item.slot === 'keno_ball') {
    const balls = q('.keno-ball');
    balls.forEach((b) => b.classList.remove('sel', 'drawn', 'hit'));
    balls.forEach((b, i) => {
      wdTryAt(600 + i * 800, () => { b.classList.add('sel'); wdTryRestart(b, 'press'); });
      wdTryAt(4200 + i * 1500, () => { b.classList.add(i === 0 ? 'hit' : 'drawn'); wdTryRestart(b, 'pop'); });
    });
  } else if (item.slot === 'crash') {
    const chart = scene.querySelector('.cr-chart');
    const curves = Array.from(scene.querySelectorAll('path')).filter((e) => (e.getAttribute('d') || '').startsWith('M0 149L'));      // линия образца (у части скинов их две: след и линия)
    if (!chart) return;
    const readout = document.createElement('div');
    readout.className = 'cr-readout';
    const mult = document.createElement('strong');
    mult.className = 'cr-mult';
    readout.appendChild(mult);
    chart.appendChild(readout);
    const RISE_MS = 7600;                                   // раунд со всеми зонами: до ×2 серая, до ×10 янтарная, выше золотая
    const TOP = 60;
    chart.setAttribute('data-tone', 'idle');
    const t0 = performance.now();
    const frame = (now) => {
      if (!wdTry) return;
      const k = Math.min(1, (now - t0) / RISE_MS);
      const m = Math.pow(TOP, k);
      let d = '';
      for (let i = 0; i <= 24; i++) {
        const kk = (k * i) / 24;
        d += (i ? 'L' : 'M') + (kk * 300).toFixed(1) + ' ' + (149 - 147 * (Math.log(Math.pow(TOP, kk)) / Math.log(TOP))).toFixed(1);
      }
      curves.forEach((c) => c.setAttribute('d', d));
      mult.textContent = '×' + (m < 10 ? m.toFixed(2) : m.toFixed(1));
      chart.setAttribute('data-zone', m >= 10 ? '3' : (m >= 2 ? '2' : '1'));
      if (k < 1) { wdTry.raf = requestAnimationFrame(frame); return; }
      wdTry.raf = 0;
      chart.setAttribute('data-tone', 'win');              // забрал на потолке образца
    };
    wdTry.raf = requestAnimationFrame(frame);
  }
}

function wdTryToggle() {
  const item = wd.preview;
  if (!item) return;
  if (wdTry) { wdTryStop(); renderWdPreview(); return; }
  renderWdPreview();                                        // свежий образец
  wdTry = { timers: [], raf: 0, item };
  const scene = document.getElementById('wd-prev-scene');
  if (!scene) { wdTry = null; return; }
  wdTryScript(scene, item);
  wdTryAt(WD_TRY_MS, () => { wdTryStop(); renderWdPreview(); });
  wdTryButtonSync();
}

document.getElementById('wd-prev-try').addEventListener('click', wdTryToggle);
// #endregion
