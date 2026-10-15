// Bloody Slot: интерфейс. Деньги, исходы и состояние раунда только на сервере: игра шлёт приложению запросы (мост postMessage, js/15-arcade.js), получает готовый лог
// спина (поле после каждого шага, взрывы, падения, уровни полоски, Scatter, xSplit) и только анимирует его. Анимации: transform и opacity (эффекты в fx.js).
'use strict';
(function () {
  var ORIGIN = window.location.origin && window.location.origin !== 'null' ? window.location.origin : '*';
  var $ = function (id) { return document.getElementById(id); };
  var els = {
    app: $('app'), board: $('board'), boardWrap: $('board-wrap'), wildrow: $('wildrow'), fx: $('fx'), fxtop: $('fxtop'), floor: $('floor'), balance: $('balance'),
    modeTitle: $('mode-title'), modeNote: $('mode-note'), bar: $('bar'), runes: $('runes'), barFill: $('bar-fill'), barPoints: $('bar-points'),
    winline: $('winline'), winTotal: $('win-total'), winMult: $('win-mult'), betValue: $('bet-value'), costBox: $('cost-box'), costLabel: $('cost-label'),
    costValue: $('cost-value'), spin: $('btn-spin'), spinLabel: $('spin-label'), minus: $('bet-minus'), plus: $('bet-plus'), star: $('btn-star'), buy: $('btn-buy'),
    auto: $('btn-auto'), turbo: $('btn-turbo'), bets: $('btn-bets'), menu: $('btn-menu'), toast: $('toast'), banner: $('banner'), cut: $('cut'), intro: $('intro'),
    sheet: $('sheet'), sheetTitle: $('sheet-title'), sheetBody: $('sheet-body'), sheetClose: $('sheet-close'), sheetDim: $('sheet-dim')
  };
  var ROWS = 5;                         // рядов в поле: у барабана 4 ячейки или 5 (открыт Wild нижнего ряда), высота по снимку сервера
  var REELS = 5;
  var IMG = { W: 'Wild', S: 'Scatter', XD: 'Dagger', XG: 'Guillotine' };
  var MID = { M1: 1, M2: 1, M3: 1, M4: 1, M5: 1 };
  var HIGH = { H1: 1, H2: 1, H3: 1, H4: 1, H5: 1 };
  var BUY_INFO = {
    gibbet: { name: 'Виселица', text: 'Полоска сразу на уровне 3 (барабаны 5-5-5-4-4), +6 Candle Spins, три слабых символа первого поля становятся высокими.' },
    reign: { name: 'Reign of Terror Spins', text: 'Сразу 10 спинов на поле 5-5-5-5-5 только из высоких символов.' },
    lucky: { name: 'Lucky Draw', text: 'Случайный бонус: 40% уровень 3 и 6 Candle Spins, 40% уровень 4 и 8 Candle Spins, 20% Reign of Terror Spins (10 спинов).' },
    xbet1: { name: 'Ритуальный буст', text: 'Полоска стартует с уровня 1 и копит очки быстрее.' },
    xbet2: { name: 'xBet 2', text: 'Один спин на последнем уровне с гарантированными Dagger и Guillotine.' }
  };
  var LOCK_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M7 11V8a5 5 0 0 1 10 0v3" fill="none" stroke="#6c6258" stroke-width="2"/><rect x="5" y="11" width="14" height="10" rx="2" fill="#6c6258"/></svg>';
  var DEMO = [['L1', 'M2', 'M3', 'L4'], ['M1', 'L2', 'L3', 'L5'], ['L3', 'M4', 'M5', 'L2'], ['M3', 'M2', 'L1', 'M4'], ['L5', 'M1', 'L4', 'L3']];   // заставка до первого раунда: высоких символов в начале нет

  var st = {
    settings: null, bet: 20, buy: null, view: null, busy: false, auto: false, turbo: false, balance: null, cell: 70, cells: [], grid: null,
    favs: [], mode: null, ready: false, sheet: null
  };
  try { st.turbo = localStorage.getItem('bloody_turbo') === '1'; } catch (e) { /* без хранилища */ }
  try { st.favs = JSON.parse(localStorage.getItem('bloody_favs') || '[]'); } catch (e) { st.favs = []; }
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- утилиты ----------
  function speed() { return (reduce ? 0.25 : 1) * (st.turbo ? 0.45 : 1); }
  BFX.sp = speed;
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, Math.max(0, ms * speed())); }); }
  function fmt(n) {
    n = Math.floor(n);
    if (n < 10000) return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    if (n < 1e6) return (Math.floor(n / 100) / 10).toString().replace('.', ',') + ' тыс.';
    return (Math.floor(n / 1e5) / 10).toString().replace('.', ',') + ' млн';
  }
  function plural(n, a, b, c) { return n % 10 === 1 && n % 100 !== 11 ? a : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? b : c); }
  function haptic(kind) { try { window.parent.postMessage({ type: 'bloody:haptic', kind: kind }, ORIGIN); } catch (e) { /* вне приложения */ } }
  function toast(text) {
    els.toast.textContent = text;
    els.toast.hidden = false;
    els.toast.style.animation = 'none';
    void els.toast.offsetWidth;
    els.toast.style.animation = '';
    clearTimeout(toast.t);
    toast.t = setTimeout(function () { els.toast.hidden = true; }, 3000);
  }
  function betCost() {
    var mult = st.buy && st.settings ? st.settings.buys[st.buy] : 1;
    return Math.round(st.bet * mult);
  }
  function raf() { return new Promise(function (r) { requestAnimationFrame(r); }); }

  // ---------- мост с приложением ----------
  var pending = {};
  var seq = 0;
  function call(action, payload) {
    return new Promise(function (resolve) {
      var id = 'b' + (++seq);
      pending[id] = resolve;
      var msg = { type: 'bloody:call', id: id, action: action };
      for (var k in payload) { if (Object.prototype.hasOwnProperty.call(payload, k)) msg[k] = payload[k]; }
      try { window.parent.postMessage(msg, ORIGIN); } catch (e) { delete pending[id]; resolve({ ok: false, code: 'no_parent', text: 'Откройте игру из приложения' }); }
    });
  }
  window.addEventListener('message', function (e) {
    if (e.source !== window.parent) return;
    var m = e.data;
    if (!m || typeof m !== 'object') return;
    if (m.type === 'bloody:ready') { boot(); }
    else if (m.type === 'bloody:reply' && pending[m.id]) { var r = pending[m.id]; delete pending[m.id]; r(m); }
  });

  // ---------- геометрия ----------
  function measure() {
    // клетка по ширине и по высоте окна: всё (шапка, поле, ряд Wild, камни, шкала, панель) должно помещаться без прокрутки
    var byW = (window.innerWidth - 40) / REELS;
    var byH = (window.innerHeight - 262) / 5.5;
    var cell = Math.max(44, Math.min(byW, byH, 84));
    document.documentElement.style.setProperty('--cell', cell.toFixed(1) + 'px');
    var w = els.board.clientWidth;
    st.cell = w > 0 ? w / REELS : cell;
    document.documentElement.style.setProperty('--panel-h', document.querySelector('.panel').offsetHeight + 'px');
  }
  function tr(c, y) { return 'translate(' + (c * st.cell).toFixed(1) + 'px,' + y.toFixed(1) + 'px)'; }
  function posT(c, r) { return tr(c, r * st.cell); }
  function cellCenter(c, r) { return { x: els.board.offsetLeft + (c + 0.5) * st.cell, y: els.board.offsetTop + (r + 0.5) * st.cell }; }
  function appPoint(el, fx, fy) {
    var a = els.app.getBoundingClientRect();
    var b = el.getBoundingClientRect();
    return { x: b.left - a.left + b.width * (fx === undefined ? 0.5 : fx), y: b.top - a.top + b.height * (fy === undefined ? 0.5 : fy) };
  }
  function boardPoint(c, r) {
    var a = els.app.getBoundingClientRect();
    var b = els.board.getBoundingClientRect();
    return { x: b.left - a.left + (c + 0.5) * st.cell, y: b.top - a.top + (r + 0.5) * st.cell };
  }

  // ---------- символы ----------
  function makeSym(sym, mult) {
    var el = document.createElement('div');
    el.className = 'sym';
    el.dataset.sym = sym;
    var img = new Image();
    img.src = 'img/' + (IMG[sym] || sym) + '.webp';
    img.alt = '';
    img.decoding = 'async';
    el.appendChild(img);
    setMult(el, mult);
    return el;
  }
  function setSym(el, sym) {
    if (el.dataset.sym === sym) return;
    el.dataset.sym = sym;
    el.firstChild.src = 'img/' + (IMG[sym] || sym) + '.webp';
  }
  function setMult(el, mult) {
    var b = el.querySelector('.mult');
    if (!mult || mult < 2) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement('i'); b.className = 'mult'; el.appendChild(b); }
    if (b.textContent !== '×' + mult) { b.textContent = '×' + mult; b.style.animation = 'none'; void b.offsetWidth; b.style.animation = ''; }
  }
  function place(el, c, r) { el.dataset.c = c; el.dataset.r = r; el.style.transform = posT(c, r); }
  function hOf(grid, c) { return grid && grid[c] ? grid[c].length : 4; }
  // замки нижнего ряда: открыт столько барабанов слева, сколько из них в снимке поля по 5 ячеек
  function setRows(grid) {
    var open = 0;
    for (var c = 0; c < REELS; c++) if (hOf(grid, c) > 4) open = c + 1;
    renderWilds(open);
  }
  function clearBoard() {
    els.board.textContent = '';
    st.cells = [];
    for (var c = 0; c < REELS; c++) st.cells.push([]);
  }
  function setGridStatic(grid, mults) {
    setRows(grid);
    clearBoard();
    st.grid = grid;
    for (var c = 0; c < REELS; c++) {
      for (var r = 0; r < hOf(grid, c); r++) {
        var el = makeSym(grid[c][r], mults ? mults[c][r] : 1);
        if (grid[c][r] === 'W') el.classList.add('sticky');
        place(el, c, r);
        els.board.appendChild(el);
        st.cells[c][r] = el;
      }
    }
  }

  // падение с «весом каменной плитки»: резкий старт, удар, короткий отскок вверх на 3-5 px и возврат
  function fall(el, c, fromRow, toRow, delay, heavy) {
    var y0 = fromRow * st.cell;
    var y1 = toRow * st.cell;
    var amp = heavy ? 5 : 3.5;
    var dist = Math.abs(toRow - fromRow);
    var d = (200 + dist * 62) * speed();
    var a = el.animate([
      { transform: tr(c, y0), easing: 'cubic-bezier(.62,0,1,.5)' },
      { transform: tr(c, y1), offset: 0.64, easing: 'cubic-bezier(.1,.75,.3,1)' },
      { transform: tr(c, y1 - amp), offset: 0.8, easing: 'cubic-bezier(.6,0,.9,.5)' },
      { transform: tr(c, y1) }
    ], { duration: d, delay: delay * speed(), fill: 'backwards' });
    return a.finished.catch(function () {});
  }
  function dealGrid(grid, mults) {
    setRows(grid);
    clearBoard();
    st.grid = grid;
    var waits = [];
    for (var c = 0; c < REELS; c++) {
      var stagger = c * (40 + (c % 2) * 14);
      var h = hOf(grid, c);
      var chained = 0;                                  // Wild на цепях внизу барабана остаются на месте, падает только остальное
      while (chained < h && grid[c][h - 1 - chained] === 'W') chained++;
      for (var r = 0; r < h; r++) {
        var el = makeSym(grid[c][r], mults ? mults[c][r] : 1);
        if (grid[c][r] === 'W') el.classList.add('sticky');
        place(el, c, r);
        els.board.appendChild(el);
        st.cells[c][r] = el;
        if (r < h - chained) waits.push(fall(el, c, r - h - 1.2, r, stagger + (h - 1 - r) * 18, true));
      }
    }
    return Promise.all(waits).then(function () { BFX.shake(els.boardWrap, 3, 260); });
  }
  // старые символы резко проваливаются вниз за поле (ease-in, 150-200 мс)
  function plunge() {
    var waits = [];
    for (var c = 0; c < REELS; c++) {
      for (var r = 0; r < (st.cells[c] ? st.cells[c].length : 0); r++) {
        var el = st.cells[c] && st.cells[c][r];
        if (!el || el.dataset.sym === 'W') continue;          // прилипшие Wild (на цепях) остаются на месте
        var a = el.animate([{ transform: posT(c, r) }, { transform: posT(c, r + ROWS + 1) }], { duration: 190 * speed(), delay: (c * 18 + (st.cells[c].length - 1 - r) * 10) * speed(), easing: 'cubic-bezier(.6,0,1,.6)', fill: 'forwards' });
        waits.push(a.finished.catch(function () {}));
      }
    }
    return Promise.all(waits);
  }
  function startBlur() {
    stopBlur();
    for (var c = 0; c < REELS; c++) {
      var b = document.createElement('div');
      b.className = 'bcol';
      b.style.left = (c * st.cell) + 'px';
      els.board.appendChild(b);
    }
  }
  function stopBlur() { els.board.querySelectorAll('.bcol').forEach(function (b) { b.remove(); }); }

  // приводит поле к снимку сервера после шага (страховка: всё, что не сошлось, исправляется)
  function reconcile(grid, mults) {
    st.grid = grid;
    for (var c = 0; c < REELS; c++) {
      var h = hOf(grid, c);
      while (st.cells[c].length > h) { var extra = st.cells[c].pop(); if (extra) extra.remove(); }
      for (var r = 0; r < h; r++) {
        var el = st.cells[c][r];
        if (!el) {
          el = makeSym(grid[c][r], mults ? mults[c][r] : 1);
          els.board.appendChild(el);
          st.cells[c][r] = el;
        }
        place(el, c, r);
        el.classList.remove('win', 'boom', 'charge', 'cut');
        el.querySelectorAll('.seal').forEach(function (s) { s.remove(); });
        setSym(el, grid[c][r]);
        setMult(el, mults ? mults[c][r] : 1);
        el.classList.toggle('sticky', grid[c][r] === 'W');
      }
    }
  }
  function applyMovesFills(moves, fills) {
    var waits = [];
    var moving = [];
    var heavy = 0;
    (moves || []).forEach(function (m) { moving.push({ el: st.cells[m[0]][m[1]], c: m[0], f: m[1], t: m[2] }); });
    moving.forEach(function (m) { st.cells[m.c][m.f] = null; });
    moving.forEach(function (m) {
      if (!m.el) return;
      st.cells[m.c][m.t] = m.el;
      m.el.dataset.r = m.t;
      m.el.style.transform = posT(m.c, m.t);
      waits.push(fall(m.el, m.c, m.f, m.t, 0, false));
      heavy += m.t - m.f;
    });
    var perReel = {};
    (fills || []).forEach(function (f) { perReel[f[0]] = (perReel[f[0]] || 0) + 1; });
    (fills || []).forEach(function (f) {
      var el = makeSym(f[2], 1);
      place(el, f[0], f[1]);
      els.board.appendChild(el);
      st.cells[f[0]][f[1]] = el;
      waits.push(fall(el, f[0], f[1] - perReel[f[0]] - 0.6, f[1], f[0] * 40, true));
      heavy += 2;
    });
    return Promise.all(waits).then(function () { if (heavy >= 6) BFX.shake(els.boardWrap, 2.5, 220); });
  }
  function removeCells(cells) {
    cells.forEach(function (p) { var el = st.cells[p[0]][p[1]]; if (el) el.remove(); st.cells[p[0]][p[1]] = null; });
  }

  // ---------- полоска, Wild, режимы ----------
  var VINE = '<svg class="vine" viewBox="0 0 100 60" preserveAspectRatio="none" aria-hidden="true"><path class="sh" d="M-2 14C20 4 40 24 60 12S92 6 102 16"/><path d="M-2 12C20 2 40 22 60 10S92 4 102 14"/><path d="M-2 46C18 56 40 36 62 48S90 54 102 44"/><path d="M6 10l-2-8 7 5zM38 20l4 8-8-2zM66 8l3-7 4 7zM88 8l6-4-2 8zM12 52l-4 7 8-1zM44 40l-3-7 8 3zM74 52l4 6 3-8zM94 48l6 4-8 3z" class="th"/></svg>';
  var RUNE_ORDER = ['L5', 'L4', 'L3', 'L2', 'L1'];     // камни: что сгорает на уровнях 1..5 (самый дешёвый низкий символ первым)
  function buildBar() {
    els.runes.textContent = '';
    RUNE_ORDER.forEach(function (sym) {
      var d = document.createElement('div');
      d.className = 'rune';
      d.innerHTML = '<img src="img/' + sym + '.webp" alt="">';
      els.runes.appendChild(d);
    });
    els.wildrow.textContent = '';
    for (var j = 0; j < REELS; j++) {
      var w = document.createElement('div');
      w.className = 'wslot';
      w.innerHTML = '<span class="lock">' + LOCK_SVG + '</span>' + VINE;
      els.wildrow.appendChild(w);
    }
    els.floor.innerHTML = BFX.floorHTML();
  }
  function renderBar(bar, opts) {
    var level = bar.level;
    var kids = els.runes.children;
    for (var i = 0; i < kids.length; i++) {
      var burned = i < level;
      if (burned && !kids[i].classList.contains('burned') && opts && opts.pop && i === level - 1) BFX.flames(kids[i], 4);
      kids[i].classList.toggle('burned', burned);
    }
    var thr = bar.threshold;
    var ratio = thr ? Math.min(1, bar.progress / thr) : 1;
    els.barFill.style.transform = 'scaleX(' + ratio.toFixed(3) + ')';
    els.barPoints.textContent = thr ? (bar.progress + ' / ' + thr) : 'Ритуал завершён';
  }
  // open: сколько барабанов слева уже из 5 ячеек (их замок снят); just: барабан, чей Wild открылся только что (пламя, замок рассыпается)
  function renderWilds(open, just) {
    var kids = els.wildrow.children;
    for (var i = 0; i < kids.length; i++) {
      kids[i].classList.toggle('open', i < open);
      if (just === i) {
        var k = kids[i];
        k.classList.remove('just'); void k.offsetWidth; k.classList.add('just'); BFX.flames(k, 6);
        setTimeout(function () { k.classList.remove('just'); }, 900 * speed());
      }
    }
  }
  function activeWilds(view) {
    return view && view.round ? view.round.bar.level : 0;
  }
  function setMode(mode, view) {
    st.mode = mode;
    els.app.dataset.mode = mode || 'base';
    var r = view && view.round;
    if (mode === 'reign') { els.modeTitle.textContent = 'Reign of Terror Spins'; els.modeNote.textContent = r ? 'Осталось спинов: ' + r.reign_left : ''; }
    else if (mode === 'candle') { els.modeTitle.textContent = 'Candle Spins'; els.modeNote.textContent = r ? 'Осталось спинов: ' + r.candle_left : ''; }
    else { els.modeTitle.textContent = 'Основная игра'; els.modeNote.textContent = 'Виртуальные фишки, вывода нет'; }
  }

  // ---------- числа ----------
  function setBalance(n, bump) {
    st.balance = n;
    els.balance.textContent = fmt(n);
    els.balance.dataset.n = String(n);          // точное число (текст может быть сокращён)
    if (bump) { els.balance.style.animation = 'none'; void els.balance.offsetWidth; els.balance.style.animation = 'bump 0.35s ease-out'; }
  }
  function setWin(chips, mult) {
    els.winTotal.textContent = fmt(chips);
    els.winMult.textContent = mult > 0 ? '×' + (Math.round(mult * 100) / 100) : '';
    els.winline.classList.remove('bump');
    void els.winline.offsetWidth;
    els.winline.classList.add('bump');
  }
  function renderControls() {
    var v = st.view;
    var active = !!v && v.status === 'active';
    var next = active ? v.round.next : null;
    els.betValue.textContent = fmt(st.bet);
    var showCost = !!st.buy && !active;
    els.costBox.hidden = !showCost;
    if (showCost) { els.costLabel.textContent = 'Общая стоимость'; els.costValue.textContent = fmt(betCost()); }
    els.spin.disabled = st.busy;
    els.spin.classList.toggle('bonus', next === 'candle');
    els.spin.classList.toggle('reign', next === 'reign');
    els.spin.classList.toggle('buy', showCost);
    els.spin.classList.toggle('count', !!next);
    if (next) {
      var n = next === 'reign' ? v.round.reign_left : v.round.candle_left;
      els.spinLabel.innerHTML = '<b class="num">' + n + '</b><small>' + (next === 'reign' ? 'Reign' : 'Candle') + '</small>';
    } else {
      els.spinLabel.textContent = showCost ? 'Купить' : 'Крутить';
    }
    var lock = st.busy || active;
    els.minus.disabled = els.plus.disabled = els.bets.disabled = els.star.disabled = els.buy.disabled = lock;
    els.auto.setAttribute('aria-pressed', String(st.auto));
    els.turbo.setAttribute('aria-pressed', String(st.turbo));
    els.buy.classList.toggle('on', !!st.buy);
  }

  // ---------- шаги спина ----------
  function chips(units) { return units * st.bet / 100; }
  function bbox(cells) {
    var minC = 9, maxC = 0, minR = 9, maxR = 0;
    cells.forEach(function (p) { minC = Math.min(minC, p[0]); maxC = Math.max(maxC, p[0]); minR = Math.min(minR, p[1]); maxR = Math.max(maxR, p[1]); });
    return { c: (minC + maxC) / 2, r: (minR + maxR) / 2 };
  }
  function flyPoints(cells) {
    var target = appPoint(els.barFill, 0.5, 0.5);
    var n = 0;
    cells.forEach(function (p) {
      var el = st.cells[p[0]][p[1]];
      if (!el || n >= 10) return;
      var sym = el.dataset.sym;
      var pts = HIGH[sym] ? 2 : MID[sym] ? 1 : 0;
      if (!pts) return;
      n++;
      setTimeout(function () { BFX.fly(els.fxtop, boardPoint(p[0], p[1]), target, '+' + pts); }, n * 35 * speed());
    });
  }
  async function playCascade(step, ctx) {
    var cells = step.cells;
    var ways = step.wins.reduce(function (a, w) { return a + w.ways; }, 0);
    // 1. заряд: огненная печать и красное свечение рамки
    cells.forEach(function (p) {
      var el = st.cells[p[0]][p[1]];
      if (el) { el.classList.add('charge'); el.appendChild(BFX.seal()); }
    });
    haptic('light');
    await sleep(300);
    // 2. взрыв: кровавые брызги, осколки, искры; очки летят к шкале
    flyPoints(cells);
    cells.forEach(function (p) {
      var el = st.cells[p[0]][p[1]];
      if (!el) return;
      var cc = cellCenter(p[0], p[1]);
      BFX.burst(els.fx, cc.x + 10, cc.y + 10, 9);
      el.classList.remove('charge');
      el.classList.add('boom');
    });
    BFX.shake(els.boardWrap, 4, 300);
    // 3. сумма и число путей
    ctx.units += step.units;
    var mid = bbox(cells);
    var mc = cellCenter(mid.c, mid.r);
    BFX.winPop(els.fx, mc.x + 10, mc.y + 10, fmt(chips(step.units)), ways + ' ' + plural(ways, 'путь', 'пути', 'путей'));
    setWin(chips(ctx.units), ctx.units / 100);
    if (step.bar) renderBar(step.bar);
    await sleep(300);
    removeCells(cells);
    await applyMovesFills(step.moves, step.fills);
  }
  async function playLevelUp(step, ctx) {
    // шкала переполнена: красно-белый луч вверх к барабанам по дуге
    BFX.beam(els.fxtop, appPoint(els.barFill, 0.9, 0.5), appPoint(els.board, 0.5, 0.15));
    haptic('success');
    await sleep(380);
    renderBar(step.bar, { pop: true });
    BFX.shake(els.app, 5, 320);
    // замок слота разрушается в пламени, на его месте в пятой ячейке барабана встаёт Wild (играет один раз, потом ячейка свободна)
    renderWilds(step.level, step.wild);
    if (step.opened) {
      var oc = step.opened[0], orow = step.opened[1];
      var w = makeSym('W', 1);
      w.classList.add('sticky', 'flash');
      place(w, oc, orow);
      els.board.appendChild(w);
      st.cells[oc][orow] = w;
      BFX.flames(w, 5);
    }
    toastLevel(step);
    // символы вспыхивают пламенем и превращаются в высокие
    step.converted.forEach(function (p) {
      var el = st.cells[p[0]][p[1]];
      if (el) { BFX.flames(el, 3); setTimeout(function () { setSym(el, p[2]); el.classList.add('flash'); }, 420 * speed()); }
    });
    await sleep(step.converted.length ? 760 : 520);
    if (step.cells.length) {
      step.cells.forEach(function (p) { var el = st.cells[p[0]][p[1]]; if (el) { el.classList.add('charge'); el.appendChild(BFX.seal()); } });
      await sleep(260);
      step.cells.forEach(function (p) {
        var el = st.cells[p[0]][p[1]];
        if (el) { var cc = cellCenter(p[0], p[1]); BFX.burst(els.fx, cc.x + 10, cc.y + 10, 8); el.classList.remove('charge'); el.classList.add('boom'); }
      });
      await sleep(300);
      removeCells(step.cells);
    }
    await applyMovesFills(step.moves, step.fills);
  }
  function toastLevel(step) {
    if (step.candle_spins) toast('Уровень ' + step.level + ': +' + step.candle_spins + ' Candle Spins');
    else if (step.reign_spins) toast('Последний уровень: Reign of Terror Spins +' + step.reign_spins);
    else toast('Уровень ' + step.level);
  }
  async function playScatter(step) {
    haptic('medium');
    step.cells.forEach(function (p) { var el = st.cells[p[0]][p[1]]; if (el) { el.classList.add('charge'); el.appendChild(BFX.seal()); } });
    var target = appPoint(els.barFill, 0.5, 0.5);
    step.cells.forEach(function (p, i) {
      setTimeout(function () { BFX.fly(els.fxtop, boardPoint(p[0], p[1]), target, step.points ? '+' + (step.points / step.cells.length) : '+1 спин'); }, (160 + i * 60) * speed());
    });
    await sleep(520);
    step.cells.forEach(function (p) {
      var el = st.cells[p[0]][p[1]];
      if (el) { var cc = cellCenter(p[0], p[1]); BFX.burst(els.fx, cc.x + 10, cc.y + 10, 7); el.classList.remove('charge'); el.classList.add('boom'); }
    });
    if (step.bar) renderBar(step.bar);
    await sleep(300);
    removeCells(step.cells);
    await applyMovesFills(step.moves, step.fills);
  }
  async function playXsplit(step) {
    haptic('heavy');
    var ox = els.board.offsetLeft;
    var oy = els.board.offsetTop;
    step.splits.forEach(function (s) {
      var dagger = s.kind === 'dagger';
      BFX.slash(els.fx, dagger ? { x: ox, y: oy + s.at[1] * st.cell, w: st.cell * REELS, h: st.cell } : { x: ox + s.at[0] * st.cell, y: oy + s.at[1] * st.cell, w: st.cell, h: st.cell * (hOf(st.grid, s.at[0]) - s.at[1]) }, !dagger);
      var host = st.cells[s.at[0]][s.at[1]];
      if (host) host.classList.add('flash');
    });
    await sleep(160);
    BFX.shake(els.boardWrap, 5, 260);
    step.splits.forEach(function (s) {
      s.cells.forEach(function (p) {
        var el = st.cells[p[0]][p[1]];
        if (el) { el.classList.remove('cut'); void el.offsetWidth; el.classList.add('cut'); BFX.circle(el); }
      });
    });
    await sleep(420);
  }
  async function playStep(step, ctx) {
    var t = step.type;
    if (t === 'cascade') await playCascade(step, ctx);
    else if (t === 'level_up') await playLevelUp(step, ctx);
    else if (t === 'scatter') await playScatter(step);
    else if (t === 'xsplit') await playXsplit(step);
    else if (t === 'sink') await applyMovesFills(step.moves, []);
    reconcile(step.grid, step.mults);
  }
  async function playSpin(view, firstGridDrop) {
    var spin = view.spin;
    setMode(spin.mode, view);
    await dealGrid(spin.start.grid, spin.start.mults);
    var ctx = { units: view.round.units - spin.units, view: view };
    for (var i = 0; i < spin.steps.length; i++) await playStep(spin.steps[i], ctx);
    setWin(view.round.win, view.round.units / 100);
    renderBar(spin.bar);
    renderWilds(activeWilds(view));
  }

  // ---------- заставки и экраны выигрыша ----------
  function showBanner(kind, title, html) {
    els.banner.className = 'banner ' + kind;
    els.banner.innerHTML = '<h2></h2><p></p><button type="button">Дальше</button>';
    els.banner.querySelector('h2').textContent = title;
    els.banner.querySelector('p').innerHTML = html;
    els.banner.hidden = false;
    return new Promise(function (resolve) {
      var finish = function () { els.banner.hidden = true; resolve(); };
      els.banner.querySelector('button').onclick = finish;
      if (st.auto) setTimeout(finish, 900 * speed() + 200);
    });
  }
  function showReignIntro(spins) {
    els.intro.innerHTML = BFX.guillotineIntro() + '<h2>Reign of Terror</h2><p>Spins ' + spins + '</p>';
    els.intro.hidden = false;
    BFX.ashes(els.intro, 26);
    haptic('heavy');
    setTimeout(function () { BFX.shake(els.app, 10, 420); haptic('heavy'); }, 480 * speed());
    return new Promise(function (resolve) {
      var finish = function () { els.intro.hidden = true; els.intro.onclick = null; resolve(); };
      els.intro.onclick = finish;
      setTimeout(finish, Math.max(900, 2600 * speed()));
    });
  }
  // итог розыгрыша Lucky Draw (сервер выбрал уровень при покупке): Reign показывается своей заставкой
  function showLuckyDraw(view) {
    var level = view.round.draw;
    var spins = view.spin && view.spin.mode === 'reign' ? view.round.reign_left + 1 : null;
    if (level >= 5) return showReignIntro(spins || 10);
    var item = ((st.settings && st.settings.lucky_draw) || []).filter(function (d) { return d.level === level; })[0];
    return showBanner('', 'Lucky Draw', 'Уровень полоски <b>' + level + '</b><br>Candle Spins: ' + (item ? item.spins : ''));
  }
  function bigWin(payout, mult) {
    var scenes = mult >= 100 ? 3 : mult >= 30 ? 2 : 1;
    els.cut.className = 'cut';
    els.cut.innerHTML = '<div class="scene s1 on">' + BFX.scene1() + '</div>' + (scenes >= 2 ? '<div class="scene s2">' + BFX.scene2() + '</div>' : '') + (scenes >= 3 ? '<div class="scene s3">' + BFX.scene3() + '</div>' : '') +
      '<div class="caption"></div><div class="counter"><span></span><small></small></div><div class="flash"></div><button type="button" class="skip">Пропустить</button>';
    els.cut.hidden = false;
    var counter = els.cut.querySelector('.counter span');
    var sub = els.cut.querySelector('.counter small');
    var caption = els.cut.querySelector('.caption');
    var flash = els.cut.querySelector('.flash');
    var sceneEls = els.cut.querySelectorAll('.scene');
    var total = (2400 * scenes + 800) * speed();
    var t0 = performance.now();
    var cur = 0;
    var finished = false;
    var bladeDone = false;
    function showScene(i, cap) {
      cur = i;
      sceneEls.forEach(function (s, k) { s.classList.toggle('on', k === i); });
      caption.textContent = cap || '';
      flash.animate([{ opacity: 0.95 }, { opacity: 0 }], { duration: 260 * speed(), easing: 'ease-out' });
      haptic('medium');
    }
    return new Promise(function (resolve) {
      function end() {
        if (finished) return;
        finished = true;
        counter.textContent = fmt(payout);
        sub.textContent = '×' + (Math.round(mult * 100) / 100);
        if (scenes === 3) { showScene(2, 'OH, GUILLOTINE'); if (!bladeDone) { els.cut.classList.add('drop'); bladeDone = true; } }
        flash.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400 * speed(), easing: 'ease-out' });
        BFX.shake(els.cut, 12, 500);
        haptic('heavy');
        var ok = document.createElement('button');
        ok.type = 'button';
        ok.className = 'okbtn';
        ok.textContent = 'Забрать';
        els.cut.appendChild(ok);
        var close = function () { els.cut.hidden = true; resolve(); };
        ok.onclick = close;
        if (st.auto) setTimeout(close, 900 * speed() + 300);
      }
      els.cut.querySelector('.skip').onclick = function () { if (!finished) end(); };
      (function tick(now) {
        if (finished) return;
        var t = Math.min(1, (now - t0) / total);
        var v = payout * (1 - Math.pow(1 - t, 3));
        counter.textContent = fmt(v);
        sub.textContent = '×' + (Math.round(mult * (1 - Math.pow(1 - t, 3)) * 100) / 100);
        if (scenes === 2 && cur === 0 && t > 0.45) { showScene(1, 'TIME HAS COME'); }
        if (scenes === 3 && cur === 0 && t > 0.28) { showScene(1, 'TIME HAS COME'); }
        if (scenes >= 2 && cur === 1 && t > (scenes === 2 ? 0.78 : 0.5) && !els.cut.classList.contains('morph')) { els.cut.classList.add('morph'); flash.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 420 * speed() }); }
        if (scenes === 3 && cur === 1 && t > 0.64) { showScene(2, 'OH, GUILLOTINE'); }
        if (t >= 1) { end(); return; }
        requestAnimationFrame(tick);
      })(t0);
    });
  }

  // ---------- ход игры ----------
  function errorText(r) {
    var map = { insufficient_funds: 'Не хватает фишек', balance_limit: 'Достигнут потолок баланса', active_round: 'Раунд уже идёт', no_active_round: 'Нет активного раунда',
      bad_bet: 'Такой ставки нет', request_conflict: 'Повтор запроса с другими параметрами', network: 'Нет связи с сервером' };
    return map[r.code] || r.text || 'Не удалось выполнить действие';
  }
  async function resync() {
    var r = await call('state', {});
    if (r.ok) applyState(r.data, false);
  }
  async function onSpin() {
    if (st.busy) return;
    var v = st.view;
    var active = !!v && v.status === 'active';
    if (!active && betCost() > (st.balance || 0)) { toast('Не хватает фишек'); st.auto = false; renderControls(); return; }
    st.busy = true;
    renderControls();
    haptic('medium');
    if (!active) { setWin(0, 0); }
    var req = active ? call('spin', {}) : call('start', { bet: st.bet, buy: st.buy });
    await plunge();
    startBlur();
    var res = await req;
    await sleep(220);
    stopBlur();
    if (!res.ok) {
      toast(errorText(res));
      st.auto = false;
      st.busy = false;
      if (res.code === 'active_round' || res.code === 'no_active_round') await resync();
      else if (st.view && st.view.spin) { var sp = st.view.spin; var ls = sp.steps.length ? sp.steps[sp.steps.length - 1] : null; setGridStatic(ls ? ls.grid : sp.start.grid, ls ? ls.mults : sp.start.mults); }
      renderControls();
      return;
    }
    var view = res.data;
    var ended = view.status !== 'active';
    var wasBuy = view.buy;
    setBalance(ended ? view.balance - (view.payout || 0) : view.balance, true);   // выплата показывается после анимации
    st.view = view;
    if (!active) st.buy = null;
    if (!active && wasBuy === 'reign') await showReignIntro(view.round.reign_left + 1);
    if (!active && wasBuy === 'lucky') await showLuckyDraw(view);
    var prevMode = st.mode;
    await playSpin(view);
    if (ended) {
      setBalance(view.balance, view.payout > 0);
      if (view.payout > 0) {
        haptic('success');
        var xm = view.round.units / 100;
        if (xm >= 10) await bigWin(view.payout, xm);
        else await showBanner(view.round.capped ? 'reign' : '', 'Итог раунда', 'Выигрыш<br><b>' + fmt(view.payout) + '</b><br>×' + (Math.round(xm * 100) / 100));
      } else { haptic('error'); }
      setMode(null, view);
      renderBar({ level: 0, progress: 0, threshold: st.settings ? st.settings.bar_thresholds[0] : 20 });
    } else {
      setMode(view.round.next, view);
      if (view.round.next === 'reign' && prevMode !== 'reign' && view.spin.mode !== 'reign') await showReignIntro(view.round.reign_left);
    }
    st.busy = false;
    renderControls();
    if (st.auto) autoNext();
  }
  function onSpinPress() {
    if (!st.ready || st.busy) return;
    var v = st.view;
    var active = !!v && v.status === 'active';
    if (!active && st.buy) { confirmBuy(); return; }
    onSpin();
  }
  function autoNext() {
    setTimeout(function () {
      if (!st.auto || st.busy) return;
      var v = st.view;
      var active = !!v && v.status === 'active';
      if (!active && betCost() > (st.balance || 0)) { st.auto = false; toast('Автоигра остановлена: не хватает фишек'); renderControls(); return; }
      if (!active && st.buy) { st.buy = null; }
      onSpin();
    }, 450 * speed() + 100);
  }

  // ---------- шторки и окна ----------
  function openSheet(title, node, cls) {
    st.sheet = title;
    els.sheet.className = 'sheet' + (cls ? ' ' + cls : '');
    els.sheetTitle.textContent = title;
    els.sheetBody.textContent = '';
    els.sheetBody.appendChild(node);
    els.sheet.hidden = false;
  }
  function closeSheet() { els.sheet.hidden = true; st.sheet = null; }
  function betsSheet(favOnly) {
    var wrap = document.createElement('div');
    var grid = document.createElement('div');
    grid.className = 'bets-grid';
    var list = favOnly ? st.favs.filter(function (b) { return st.settings.bets.indexOf(b) >= 0; }) : st.settings.bets;
    list.forEach(function (b) {
      var o = document.createElement('button');
      o.type = 'button';
      o.className = 'opt';
      o.textContent = fmt(b);
      o.setAttribute('aria-pressed', String(b === st.bet));
      o.onclick = function () { setBet(b); closeSheet(); };
      grid.appendChild(o);
    });
    if (favOnly && !list.length) { var p = document.createElement('p'); p.className = 'note'; p.textContent = 'Пока пусто: добавьте текущую ставку.'; wrap.appendChild(p); }
    wrap.appendChild(grid);
    if (favOnly) {
      var add = document.createElement('button');
      add.type = 'button';
      add.className = 'opt';
      var has = st.favs.indexOf(st.bet) >= 0;
      add.innerHTML = '<b></b>';
      add.firstChild.textContent = has ? '★ Убрать текущую ставку' : '★ Добавить текущую ставку (' + fmt(st.bet) + ')';
      add.style.marginTop = '10px';
      add.onclick = function () {
        st.favs = has ? st.favs.filter(function (b) { return b !== st.bet; }) : st.favs.concat([st.bet]).slice(-6);
        try { localStorage.setItem('bloody_favs', JSON.stringify(st.favs)); } catch (e) { /* не страшно */ }
        closeSheet();
      };
      wrap.appendChild(add);
    }
    return wrap;
  }
  function buySheet() {
    var wrap = document.createElement('div');
    var none = document.createElement('button');
    none.type = 'button';
    none.className = 'opt';
    none.innerHTML = '<span><b>Без покупки</b><small>Обычная ставка</small></span><span class="price"></span>';
    none.querySelector('.price').textContent = fmt(st.bet);
    none.setAttribute('aria-pressed', String(!st.buy));
    none.onclick = function () { st.buy = null; renderControls(); closeSheet(); };
    wrap.appendChild(none);
    Object.keys(st.settings.buys).forEach(function (k) {
      var info = BUY_INFO[k] || { name: k, text: '' };
      var o = document.createElement('button');
      o.type = 'button';
      o.className = 'opt';
      o.innerHTML = '<span><b></b><small></small></span><span class="price"></span>';
      o.querySelector('b').textContent = info.name;
      o.querySelector('small').textContent = info.text;
      var cost = Math.round(st.bet * st.settings.buys[k]);
      o.querySelector('.price').textContent = fmt(cost);
      o.setAttribute('aria-pressed', String(st.buy === k));
      o.disabled = cost > (st.balance || 0);
      o.onclick = function () { st.buy = k; renderControls(); closeSheet(); };
      wrap.appendChild(o);
    });
    var n = document.createElement('p');
    n.className = 'note';
    n.textContent = 'Цена считается от текущей ставки (' + fmt(st.bet) + '). После выбора нажмите «Купить».';
    wrap.appendChild(n);
    return wrap;
  }
  // подтверждение покупки: красное окно с золотым билетом, который слегка покачивается
  function confirmBuy() {
    var info = BUY_INFO[st.buy] || { name: st.buy, text: '' };
    var wrap = document.createElement('div');
    wrap.innerHTML = BFX.ticket() + '<p></p><p class="note"></p><div class="confirm-actions"><button type="button" class="no">Отмена</button><button type="button" class="go">Купить</button></div>';
    wrap.querySelectorAll('p')[0].textContent = info.name + ' за ' + fmt(betCost());
    wrap.querySelectorAll('p')[1].textContent = info.text;
    wrap.querySelector('.no').onclick = closeSheet;
    wrap.querySelector('.go').onclick = function () { closeSheet(); onSpin(); };
    openSheet('Покупка', wrap, 'confirm');
  }
  function menuSheet() {
    var s = st.settings;
    var wrap = document.createElement('div');
    var h = '<p>Игра идёт на виртуальные фишки: их нельзя вывести или обменять на деньги.</p>';
    h += '<h3>Как играть</h3><p>Выигрыш считается по способам: символ должен стоять на первом, втором и третьем барабане (можно дальше, до пятого), минимум ' + s.min_length + ' барабана подряд слева направо; платится самая длинная комбинация символа. Сыгравшие символы исчезают, остальные падают вниз, сверху падают новые, и так, пока выигрыши не кончатся. Правила одинаковые во всех режимах. Максимальный выигрыш раунда: ×' + fmt(s.max_win_bets) + ' ставки.</p>';
    h += '<h3>Wild</h3><p>Wild заменяет любой символ, кроме Scatter. Сыграв, исчезает. Wild, который не сыграл, опускается вниз барабана и закрепляется цепями: он остаётся и в следующих спинах раунда, пока не войдёт в выигрыш.</p>';
    h += '<h3>Ритуальная полоска</h3><p>Средний символ в выигрыше даёт +1 очко, высокий +2, Scatter +10. Пороги уровней: ' + s.bar_thresholds.join(', ') + '. Высоких символов в начале раунда нет. На каждом новом уровне самый слабый знак сгорает (камни под полем), средний зверь становится высоким (сначала ворон, затем змея, козёл, волк и ведьма; прежний вид пропадает полностью), а под следующим барабаном открывается Wild: он встаёт пятой ячейкой барабана, играет один раз, и ячейка остаётся свободной. После первого уровня барабаны 5-4-4-4-4, после второго 5-5-4-4-4 и так далее. Уровни 1–4 дают по +' + s.candle_per_level + ' Candle Spins, пятый запускает Reign of Terror Spins (+' + s.reign_spins + '): все символы высокие, поле 5-5-5-5-5, игра по тем же правилам.</p>';
    h += '<h3>Бонусы</h3><p>Dagger делит свой ряд, Guillotine делит всё под собой на барабане: число способов у разделённых символов удваивается (до ×32). В Reign Scatter даёт +1 спин.</p>';
    h += '<h3>Таблица выплат (в ставках за 5 / 4 / 3 барабана)</h3><table class="pt">';
    ['H1', 'H2', 'H3', 'H4', 'H5', 'M1', 'M2', 'M3', 'M4', 'M5', 'L1', 'L2', 'L3', 'L4', 'L5'].forEach(function (sym) {
      var p = s.paytable[sym];
      h += '<tr><td><img src="img/' + sym + '.webp" alt="' + sym + '"></td><td>' + p['5'] + '</td><td>' + p['4'] + '</td><td>' + p['3'] + '</td></tr>';
    });
    h += '<tr><td><img src="img/Wild.webp" alt="Wild"></td><td>' + s.wild_pay + '</td><td>–</td><td>–</td></tr></table>';
    h += '<p class="note">Wild заменяет любой символ, кроме Scatter. Wild платит только за 5 барабанов.</p>';
    wrap.innerHTML = h;
    return wrap;
  }

  // ---------- состояние ----------
  function setBet(b) {
    st.bet = b;
    try { localStorage.setItem('bloody_bet', String(b)); } catch (e) { /* не страшно */ }
    renderControls();
  }
  function stepBet(dir) {
    var list = st.settings.bets;
    var i = list.indexOf(st.bet);
    var j = Math.max(0, Math.min(list.length - 1, i + dir));
    if (j !== i) { setBet(list[j]); haptic('light'); }
  }
  function applyState(view, first) {
    st.view = view;
    if (view.settings) st.settings = view.settings;
    setBalance(view.balance, false);
    var spin = view.spin;
    var idle = { level: 0, progress: 0, threshold: st.settings.bar_thresholds[0] };
    if (view.status === 'none' || !spin) {
      setGridStatic(DEMO, null);
      setMode(null, view);
      renderBar(idle);
      renderWilds(0);
      setWin(0, 0);
    } else {
      var last = spin.steps.length ? spin.steps[spin.steps.length - 1] : null;
      var active = view.status === 'active';
      setMode(active ? view.round.next : null, view);
      setGridStatic(last ? last.grid : spin.start.grid, last ? last.mults : spin.start.mults);
      renderBar(active ? view.round.bar : idle);
      renderWilds(active ? activeWilds(view) : 0);
      setWin(view.round.win, view.round.units / 100);
      if (active && first) toast('Продолжаем раунд: бонусные спины ждут');
    }
    renderControls();
  }
  async function boot() {
    buildBar();
    measure();
    var r = await call('state', {});
    if (!r.ok) { toast(errorText(r)); return; }
    st.settings = r.data.settings;
    var saved = 0;
    try { saved = Number(localStorage.getItem('bloody_bet')); } catch (e) { saved = 0; }
    st.bet = st.settings.bets.indexOf(saved) >= 0 ? saved : st.settings.bets[2];
    applyState(r.data, true);
    st.ready = true;
  }

  // ---------- события ----------
  els.spin.onclick = onSpinPress;
  els.minus.onclick = function () { stepBet(-1); };
  els.plus.onclick = function () { stepBet(1); };
  els.bets.onclick = function () { if (st.settings) openSheet('Ставка', betsSheet(false)); };
  els.star.onclick = function () { if (st.settings) openSheet('Быстрые ставки', betsSheet(true)); };
  els.buy.onclick = function () { if (st.settings) openSheet('Купить функцию', buySheet()); };
  els.menu.onclick = function () { if (st.settings) openSheet('Правила и выплаты', menuSheet()); };
  els.auto.onclick = function () { st.auto = !st.auto; renderControls(); if (st.auto && !st.busy) autoNext(); };
  els.turbo.onclick = function () { st.turbo = !st.turbo; try { localStorage.setItem('bloody_turbo', st.turbo ? '1' : '0'); } catch (e) { /* не страшно */ } renderControls(); };
  els.sheetClose.onclick = closeSheet;
  els.sheetDim.onclick = closeSheet;
  window.addEventListener('resize', function () { measure(); if (!st.busy) for (var c = 0; c < REELS; c++) for (var r = 0; st.cells[c] && r < st.cells[c].length; r++) if (st.cells[c][r]) place(st.cells[c][r], c, r); });

  if (/[?&]debug=1/.test(window.location.search)) window.__bloody = { st: st, playSpin: playSpin, bigWin: bigWin, showReignIntro: showReignIntro, confirmBuy: confirmBuy, setMode: setMode, renderBar: renderBar, renderWilds: renderWilds, playStep: playStep, setGridStatic: setGridStatic, dealGrid: dealGrid, plunge: plunge, startBlur: startBlur };   // помощники для проверки анимаций
  buildBar();
  clearBoard();
  setGridStatic(DEMO, null);
  renderBar({ level: 0, progress: 0, threshold: 20 });
  renderWilds(0);
  measure();
  try { window.parent.postMessage({ type: 'bloody:hello' }, ORIGIN); } catch (e) { /* без приложения */ }
  if (window.parent === window) toast('Откройте игру из приложения');
})();
