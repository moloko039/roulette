// Bloody Slot: эффекты и рисованные сцены (только DOM, SVG и Web Animations; transform и opacity). Игровая логика сюда не входит.
'use strict';
(function () {
  var BFX = window.BFX = { sp: function () { return 1; }, active: 0 };
  var NS = 'http://www.w3.org/2000/svg';
  var MAX_PARTICLES = 90;

  function dur(ms) { return Math.max(1, ms * BFX.sp()); }
  function done(a, fn) { a.onfinish = fn; a.oncancel = fn; }
  function tag(cls, html) { var d = document.createElement('div'); d.className = cls; if (html) d.innerHTML = html; return d; }

  // тряска контейнера (удары, падение тяжёлых плиток, взрывы)
  BFX.shake = function (el, px, ms) {
    if (!el || !el.animate) return;
    var k = [];
    for (var i = 0; i < 8; i++) k.push({ transform: 'translate(' + ((i % 2 ? -1 : 1) * px * (1 - i / 8)).toFixed(1) + 'px,' + ((i % 3 ? 1 : -1) * px * 0.6 * (1 - i / 8)).toFixed(1) + 'px)' });
    k.push({ transform: 'translate(0,0)' });
    el.animate(k, { duration: dur(ms || 320), easing: 'linear' });
  };

  // кровавые брызги, тёмные осколки и красные искры из центра ячейки
  BFX.burst = function (layer, x, y, n) {
    n = n || 10;
    if (BFX.active > MAX_PARTICLES) return;
    for (var i = 0; i < n; i++) {
      var p = tag('part ' + (i % 4 === 0 ? 'spark' : i % 3 === 0 ? 'shard' : 'blood'));
      var size = 3 + Math.random() * (i % 3 === 0 ? 7 : 5);
      p.style.width = size + 'px';
      p.style.height = size + 'px';
      p.style.left = x + 'px';
      p.style.top = y + 'px';
      layer.appendChild(p);
      var ang = Math.random() * Math.PI * 2;
      var d = 18 + Math.random() * 44;
      var dx = Math.cos(ang) * d;
      var dy = Math.sin(ang) * d * 0.8 + 16;
      BFX.active++;
      var a = p.animate([{ transform: 'translate(0,0) scale(1)', opacity: 1 }, { transform: 'translate(' + dx.toFixed(0) + 'px,' + dy.toFixed(0) + 'px) scale(.4)', opacity: 0 }],
        { duration: dur(420 + Math.random() * 260), easing: 'cubic-bezier(.1,.7,.4,1)', fill: 'forwards' });
      done(a, (function (el) { return function () { el.remove(); BFX.active--; }; })(p));
    }
  };

  // огненная печать на выигравшей ячейке (трискелион/пентаграмма)
  BFX.seal = function () {
    var s = tag('seal', '<svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="17" fill="none" stroke="#ff8a3a" stroke-width="2"/><path d="M20 4l5 14h14l-11 9 4 13-12-8-12 8 4-13L1 18h14z" fill="none" stroke="#ffd27a" stroke-width="1.6"/></svg>');
    s.animate([{ transform: 'rotate(0deg) scale(.4)', opacity: 0 }, { transform: 'rotate(120deg) scale(1.15)', opacity: 1, offset: 0.55 }, { transform: 'rotate(200deg) scale(1)', opacity: 0.95 }], { duration: dur(420), easing: 'cubic-bezier(.2,.8,.3,1)', fill: 'forwards' });
    return s;
  };

  // числа очков летят по дуге (кривая Безье) к красной шкале
  BFX.fly = function (layer, from, to, text) {
    if (BFX.active > MAX_PARTICLES) return;
    var f = tag('fly', text);
    f.style.left = '0px';
    f.style.top = '0px';
    layer.appendChild(f);
    var cx = (from.x + to.x) / 2 + (to.x - from.x) * 0.1;
    var cy = Math.min(from.y, to.y) - 36;
    var k = [];
    for (var i = 0; i <= 10; i++) {
      var t = i / 10;
      var x = (1 - t) * (1 - t) * from.x + 2 * (1 - t) * t * cx + t * t * to.x;
      var y = (1 - t) * (1 - t) * from.y + 2 * (1 - t) * t * cy + t * t * to.y;
      k.push({ transform: 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px) scale(' + (1 - t * 0.4).toFixed(2) + ')', opacity: t > 0.9 ? 0.2 : 1 });
    }
    BFX.active++;
    var a = f.animate(k, { duration: dur(620), easing: 'cubic-bezier(.4,0,.6,1)', fill: 'forwards' });
    done(a, function () { f.remove(); BFX.active--; });
  };

  // всплывающая сумма и число путей
  BFX.winPop = function (layer, x, y, amount, ways) {
    var t = tag('winpop', '<b></b><i></i>');
    t.firstChild.textContent = amount;
    t.lastChild.textContent = ways;
    t.style.left = x + 'px';
    t.style.top = y + 'px';
    layer.appendChild(t);
    var a = t.animate([{ transform: 'translate(-50%,-50%) scale(.5)', opacity: 0 }, { transform: 'translate(-50%,-50%) scale(1.1)', opacity: 1, offset: 0.25 }, { transform: 'translate(-50%,-50%) scale(1)', opacity: 1, offset: 0.7 },
      { transform: 'translate(-50%,-70%) scale(1)', opacity: 0 }], { duration: dur(1000), easing: 'ease-out', fill: 'forwards' });
    done(a, function () { t.remove(); });
  };

  // росчерк стального лезвия с кровавым следом
  BFX.slash = function (layer, rect, vertical) {
    var s = tag('slash2' + (vertical ? ' v' : ' h'), '<i></i>');
    s.style.left = rect.x + 'px';
    s.style.top = rect.y + 'px';
    s.style.width = rect.w + 'px';
    s.style.height = rect.h + 'px';
    layer.appendChild(s);
    var a = s.firstChild.animate(vertical ? [{ transform: 'translateY(-100%)', opacity: 1 }, { transform: 'translateY(100%)', opacity: 1, offset: 0.8 }, { transform: 'translateY(100%)', opacity: 0 }]
      : [{ transform: 'translateX(-100%)', opacity: 1 }, { transform: 'translateX(100%)', opacity: 1, offset: 0.8 }, { transform: 'translateX(100%)', opacity: 0 }], { duration: dur(300), easing: 'cubic-bezier(.2,.9,.3,1)', fill: 'forwards' });
    done(a, function () { setTimeout(function () { s.remove(); }, dur(250)); });
  };

  // красный круг в центре разрезанной ячейки
  BFX.circle = function (cell) {
    var c = tag('mcircle');
    cell.appendChild(c);
    var a = c.animate([{ transform: 'scale(.2) rotate(0)', opacity: 0 }, { transform: 'scale(1) rotate(90deg)', opacity: 1, offset: 0.4 }, { transform: 'scale(1.2) rotate(180deg)', opacity: 0 }], { duration: dur(520), easing: 'ease-out', fill: 'forwards' });
    done(a, function () { c.remove(); });
  };

  // луч из ритуальной шкалы вверх к барабанам (по дуге)
  BFX.beam = function (layer, from, to) {
    var b = tag('beam');
    layer.appendChild(b);
    var k = [];
    var cx = (from.x + to.x) / 2 + 60;
    var cy = (from.y + to.y) / 2 - 20;
    for (var i = 0; i <= 12; i++) {
      var t = i / 12;
      var x = (1 - t) * (1 - t) * from.x + 2 * (1 - t) * t * cx + t * t * to.x;
      var y = (1 - t) * (1 - t) * from.y + 2 * (1 - t) * t * cy + t * t * to.y;
      var dx = 2 * (1 - t) * (cx - from.x) + 2 * t * (to.x - cx);
      var dy = 2 * (1 - t) * (cy - from.y) + 2 * t * (to.y - cy);
      k.push({ transform: 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px) rotate(' + (Math.atan2(dy, dx) * 57.3 + 90).toFixed(0) + 'deg)', opacity: i === 12 ? 0 : 1 });
    }
    var a = b.animate(k, { duration: dur(600), easing: 'cubic-bezier(.3,0,.5,1)', fill: 'forwards' });
    done(a, function () { b.remove(); });
  };

  // пламя снизу вверх по слоту (открытие Wild, превращение символа)
  BFX.flames = function (host, n) {
    for (var i = 0; i < (n || 5); i++) {
      var f = tag('flame');
      f.style.left = (8 + i * (84 / (n || 5))) + '%';
      host.appendChild(f);
      var a = f.animate([{ transform: 'translateY(40%) scale(.5,.3)', opacity: 0 }, { transform: 'translateY(-10%) scale(1,1.1)', opacity: 1, offset: 0.4 }, { transform: 'translateY(-60%) scale(.6,1.4)', opacity: 0 }],
        { duration: dur(700 + i * 60), delay: dur(i * 50), easing: 'ease-out', fill: 'forwards' });
      done(a, (function (el) { return function () { el.remove(); }; })(f));
    }
  };

  // пепел летит вверх
  BFX.ashes = function (layer, n) {
    for (var i = 0; i < n; i++) {
      var p = tag('ash');
      p.style.left = (Math.random() * 100) + '%';
      p.style.bottom = '-6px';
      layer.appendChild(p);
      var a = p.animate([{ transform: 'translate(0,0)', opacity: 0 }, { opacity: 0.9, offset: 0.2 }, { transform: 'translate(' + ((Math.random() - 0.5) * 80).toFixed(0) + 'px,' + (-(160 + Math.random() * 260)).toFixed(0) + 'px)', opacity: 0 }],
        { duration: dur(1800 + Math.random() * 1600), delay: dur(Math.random() * 900), easing: 'ease-out', fill: 'forwards' });
      done(a, (function (el) { return function () { el.remove(); }; })(p));
    }
  };

  // пол бонуса: горящая пентаграмма в окружении свечей и черепов
  BFX.floorHTML = function () {
    var candles = '';
    for (var i = 0; i < 7; i++) candles += '<i class="fc" style="left:' + (4 + i * 15) + '%;bottom:' + (i % 2 ? 6 : 16) + 'px"><b></b></i>';
    return '<svg viewBox="0 0 300 120" preserveAspectRatio="xMidYMax slice" class="pent"><ellipse cx="150" cy="70" rx="120" ry="40" fill="none" stroke="#ff5a2a" stroke-width="2"/>' +
      '<ellipse cx="150" cy="70" rx="100" ry="32" fill="none" stroke="#ff9a4a" stroke-width="1" stroke-dasharray="4 5"/>' +
      '<path d="M150 36l14 28 30-2-24 20 10 28-30-17-30 17 10-28-24-20 30 2z" fill="rgba(255,60,20,.14)" stroke="#ff7a3a" stroke-width="2" stroke-linejoin="round"/></svg>' + candles +
      '<span class="skull sl">☠</span><span class="skull sr">☠</span>';
  };

  // ---------- рисованные сцены больших выигрышей (силуэты в готическом стиле) ----------
  function robe(x, mask) {
    var heads = {
      wolf: '<path d="M-9 -2l-3-14 8 7zM9 -2l3-14-8 7z" fill="#0b0708"/><path d="M-6 6l6 7 6-7z" fill="#e9ddc2"/>',
      ram: '<path d="M-8 -6c-14-2-14 14-4 14M8 -6c14-2 14 14 4 14" fill="none" stroke="#0b0708" stroke-width="3.5"/>',
      crow: '<path d="M0 2l14 4-14 5z" fill="#2a2326"/>',
      snake: '<path d="M-10 -4q10-12 20 0" fill="none" stroke="#2b3a1f" stroke-width="5"/>',
      boar: '<path d="M-5 8l-6 6M5 8l6 6" stroke="#e9ddc2" stroke-width="3" stroke-linecap="round"/>'
    };
    return '<g transform="translate(' + x + ' 250)"><path d="M-22 80L-12 10h24L22 80z" fill="#14090b"/><path d="M-10 10q10-8 20 0v-14q-10-9-20 0z" fill="#1d1013"/><circle cx="0" cy="-2" r="10" fill="#241417"/>' + heads[mask] +
      '<circle cx="-4" cy="-3" r="1.8" fill="#ff3b2a"/><circle cx="4" cy="-3" r="1.8" fill="#ff3b2a"/><path d="M-22 20l-14-16M22 20l14-16" stroke="#14090b" stroke-width="7" stroke-linecap="round"/></g>';
  }
  BFX.scene1 = function () {
    return '<svg viewBox="0 0 300 400" preserveAspectRatio="xMidYMid slice" class="art"><defs><radialGradient id="s1g" cx="50%" cy="62%" r="60%"><stop offset="0" stop-color="#ff5a1a"/><stop offset=".35" stop-color="#7a1008"/><stop offset="1" stop-color="#0a0405"/></radialGradient></defs>' +
      '<rect width="300" height="400" fill="url(#s1g)"/>' +
      '<ellipse cx="150" cy="330" rx="130" ry="36" fill="rgba(0,0,0,.5)"/><ellipse cx="150" cy="320" rx="118" ry="30" fill="none" stroke="#ff9a3a" stroke-width="3" class="glow"/>' +
      '<path d="M150 292l13 25 28-3-22 19 9 27-28-16-28 16 9-27-22-19 28 3z" fill="rgba(255,120,30,.25)" stroke="#ffcf7a" stroke-width="2.5" class="glow"/>' +
      '<path class="fire" d="M130 330q4-40 20-60 16 20 20 60q-20 8-40 0z" fill="#ff7a1a"/><path class="fire f2" d="M140 330q2-24 10-40 8 16 10 40q-10 4-20 0z" fill="#ffd27a"/>' +
      robe(40, 'wolf') + robe(95, 'ram') + robe(205, 'crow') + robe(260, 'snake') + robe(150, 'boar') + '</svg>';
  };
  function raven(x, y, s) {
    return '<path class="rav" transform="translate(' + x + ' ' + y + ') scale(' + s + ')" d="M0 0q8-8 16-2l10-6-4 8 8 2-10 3q-4 10-12 8-8-2-8-13z" fill="#050304"/>';
  }
  BFX.scene2 = function () {
    var ravens = '';
    for (var i = 0; i < 12; i++) ravens += raven(20 + (i * 53) % 260, 40 + (i * 37) % 200, 0.9 + (i % 3) * 0.3);
    return '<svg viewBox="0 0 300 400" preserveAspectRatio="xMidYMid slice" class="art"><defs><radialGradient id="s2g" cx="50%" cy="45%" r="65%"><stop offset="0" stop-color="#3a0d12"/><stop offset="1" stop-color="#050203"/></radialGradient></defs>' +
      '<rect width="300" height="400" fill="url(#s2g)"/>' +
      '<g class="nun"><path d="M80 400l22-170h96l22 170z" fill="#0c0708"/><ellipse cx="150" cy="170" rx="38" ry="46" fill="#0c0708"/><ellipse cx="150" cy="178" rx="24" ry="30" fill="#d9d2c4"/>' +
      '<circle cx="141" cy="174" r="4.5" fill="#ff2a1a" class="eye"/><circle cx="159" cy="174" r="4.5" fill="#ff2a1a" class="eye"/><rect x="108" y="270" width="84" height="60" rx="4" fill="#3a1015" stroke="#c9a24a"/><path d="M150 278v44M138 292h24" stroke="#c9a24a" stroke-width="3"/></g>' +
      '<g class="witch"><path d="M70 400l30-160h100l30 160z" fill="#1a0a20"/><path d="M95 130l55-120 55 120z" fill="#0c0510"/><ellipse cx="150" cy="132" rx="62" ry="12" fill="#0c0510"/><ellipse cx="150" cy="168" rx="28" ry="36" fill="#e6d8cb"/>' +
      '<circle cx="141" cy="164" r="4" fill="#ff2a1a" class="eye"/><circle cx="159" cy="164" r="4" fill="#ff2a1a" class="eye"/><path d="M120 250L52 150M180 250l68-100" stroke="#1a0a20" stroke-width="14" stroke-linecap="round"/><circle cx="48" cy="140" r="12" fill="#ff5a2a" class="glow"/><circle cx="252" cy="140" r="12" fill="#ff5a2a" class="glow"/></g>' +
      '<g class="ravens">' + ravens + '</g></svg>';
  };
  BFX.scene3 = function () {
    return '<svg viewBox="0 0 300 400" preserveAspectRatio="xMidYMid slice" class="art"><defs><linearGradient id="s3g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8a0f12"/><stop offset="1" stop-color="#2a0507"/></linearGradient></defs>' +
      '<rect width="300" height="400" fill="url(#s3g)"/><rect y="330" width="300" height="70" fill="#1a0507"/>' +
      '<g><rect x="58" y="70" width="14" height="270" fill="#4a2e1c"/><rect x="168" y="70" width="14" height="270" fill="#4a2e1c"/><rect x="48" y="54" width="144" height="22" fill="#5a3822"/>' +
      '<g class="blade"><rect x="76" y="86" width="88" height="14" fill="#2b2b2e"/><path d="M78 100h84v70L78 190z" fill="#d6d9de" stroke="#8d949b" stroke-width="2"/></g><rect x="64" y="300" width="112" height="40" fill="#2a1710"/><ellipse cx="120" cy="300" rx="44" ry="8" fill="#3a2418"/></g>' +
      '<g transform="translate(240 250)"><path d="M-20 90L-12 20h24l8 70z" fill="#0a0405"/><circle cx="0" cy="8" r="13" fill="#0a0405"/><path d="M-9 -2l9-14 9 14z" fill="#0a0405"/><path d="M-12 30L-60 -100" stroke="#0a0405" stroke-width="8" stroke-linecap="round"/></g>' +
      '<path d="M120 54L184 -20" stroke="#c9a24a" stroke-width="2"/><path d="M186 52L200 158" stroke="#c9a24a" stroke-width="2" opacity="0"/></svg>';
  };
  BFX.guillotineIntro = function () {
    return '<svg viewBox="0 0 300 300" class="gil"><rect x="60" y="10" width="16" height="250" fill="#4a2e1c"/><rect x="224" y="10" width="16" height="250" fill="#4a2e1c"/><rect x="50" y="2" width="200" height="24" fill="#5a3822"/>' +
      '<g class="blade"><rect x="80" y="26" width="140" height="14" fill="#2b2b2e"/><path d="M82 40h136v90L82 160z" fill="#e1e4e9" stroke="#8d949b" stroke-width="2"/><path d="M82 160l136-30" stroke="#b01818" stroke-width="3"/></g>' +
      '<rect x="70" y="250" width="160" height="26" fill="#2a1710"/></svg>';
  };
  BFX.ticket = function () {
    return '<svg viewBox="0 0 140 90" class="ticket"><path d="M6 14q0-8 8-8h112q8 0 8 8v14a10 10 0 0 0 0 20v28q0 8-8 8H14q-8 0-8-8V48a10 10 0 0 0 0-20z" fill="#e6b85c" stroke="#8c6a2c" stroke-width="3"/><path d="M70 22l7 14 15 2-11 10 3 15-14-8-14 8 3-15-11-10 15-2z" fill="#7a1010"/><path d="M22 74h96" stroke="#8c6a2c" stroke-width="2" stroke-dasharray="5 4"/></svg>';
  };
})();
