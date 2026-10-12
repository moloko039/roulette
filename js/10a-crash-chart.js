// #region Краш: график и отрисовка
// ---------- краш: график и отрисовка ----------
const CR_DOUBLING_MS = 6000;     // как crash.DOUBLING_MS на сервере (только для показа; решает сервер)
const CR_HISTORY = 10;

// m в сотях по времени от старта полёта (только для показа; сервер считает так же, но решает именно он)
function crM100(elapsed) {
  const x = Math.max(0, elapsed) / CR_DOUBLING_MS;
  return Math.min(CR_TARGET_MAX, Math.floor(100 * Math.pow(2, x)));
}

function crDrawCurve(elapsedMs) {
  const m = Math.pow(2, Math.max(0, elapsedMs) / CR_DOUBLING_MS);
  const xmax = Math.max(8000, elapsedMs * 1.1);
  const ymax = Math.max(2, m * 1.15);
  const pts = [];
  const N = 36;
  let px = 0, py = 149, x = 0, y = 149;
  for (let i = 0; i <= N; i++) {
    const t = (elapsedMs * i) / N;
    px = x; py = y;
    x = (t / xmax) * 300;
    y = 149 - ((Math.pow(2, t / CR_DOUBLING_MS) - 1) / (ymax - 1)) * 148;
    pts.push((i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1));
  }
  crTip.x = x; crTip.y = y; crTip.px = px; crTip.py = py;      // конец линии и предыдущий узел
  const d = pts.join('');
  crEls.curve.setAttribute('d', d);
  if (crEls.smoke) crEls.smoke.setAttribute('d', d);       // толстый дымный след (виден только в скинах, которым он нужен)
}

// Конец линии в координатах графика 300×150 (x, y) и предыдущий узел (px, py): сцены скинов ставят ракету по ним
const crTip = { x: 0, y: 149, px: 0, py: 149 };

// События для сцен скинов (js/01a-skin-runtime.js): смена показанной фазы и раунда, кадр (только если на него кто-то подписан). Объект кадра один и тот же, сцена его не хранит.
const crFrame = { phase: '', roundId: 0, x100: 100, elapsed: 0, x: 0, y: 149, px: 0, py: 149 };
function crEmitSkin(phase, r, x100, elapsed) {
  if (cr.phaseShown !== phase || cr.phaseRound !== r.id) {
    cr.phaseShown = phase;
    cr.phaseRound = r.id;
    if (phase === 'flight') cr.flightSeen = r.id;
    skinEvents.emit('crash:phase', { phase, roundId: r.id, x100 });
  }
  if (skinEvents.has('crash:frame')) {
    crFrame.phase = phase; crFrame.roundId = r.id; crFrame.x100 = x100; crFrame.elapsed = elapsed;
    crFrame.x = crTip.x; crFrame.y = crTip.y; crFrame.px = crTip.px; crFrame.py = crTip.py;
    skinEvents.emit('crash:frame', crFrame);
  }
}

function crSetTone(tone) { if (crEls.chart.dataset.tone !== tone) crEls.chart.dataset.tone = tone; }
// Зона множителя (UI_REVIEW, краш): 1 до ×2, 2 до ×10, 3 выше; меняет цвет цифры и свечение за ней. Фаза нужна полоске приёма ставок
function crSetZone(x100) {
  const zone = x100 >= 1000 ? '3' : x100 >= 200 ? '2' : '1';
  if (crEls.chart.dataset.zone !== zone) crEls.chart.dataset.zone = zone;
}
function crSetPhase(phase) { if (crEls.chart.dataset.phase !== phase) crEls.chart.dataset.phase = phase; }
function crShowMult(x100) { crEls.mult.textContent = crText(x100); }

// Показанная фаза по локальным часам: приём ставок сменяется полётом в момент flight_start_ms, не дожидаясь ответа (график не запаздывает на интервал опроса);
// действия (ставка, вывод) разрешает только фаза сервера
function crDisplayPhase(r) {
  if (r.phase === 'betting' && crServerNow() >= r.flight_start_ms) return 'flight';
  return r.phase;
}

const crSeconds = (ms) => Math.max(0, Math.ceil(ms / 1000));

// Один кадр: множитель, кривая, подпись с обратным отсчётом, «Забрать N»
function crDraw() {
  const r = crRound();
  if (!r || cr.resync) return;
  const phase = crDisplayPhase(r);
  const me = crMe();
  if (phase === 'flight') {
    const elapsed = Math.max(0, crServerNow() - r.flight_start_ms);
    const x100 = crM100(elapsed);
    crSetTone('idle');
    crSetPhase('flight');
    crSetZone(x100);
    crShowMult(x100);
    crDrawCurve(elapsed);
    crEmitSkin('flight', r, x100, elapsed);
    crEls.label.textContent = me && me.status === 'open' ? (me.target_x100 === null ? 'Нажмите «Забрать» до краха' : 'авто ' + crText(me.target_x100)) : 'Раунд идёт';
    if (me && me.status === 'open' && !cr.busy) crEls.cash.textContent = 'Забрать ' + formatCompact(Math.floor(me.bet * x100 / 100));
  } else if (phase === 'betting') {
    crSetTone('idle');
    crSetPhase('betting');
    crSetZone(100);
    const total = Math.max(1, r.flight_start_ms - r.bet_open_ms);
    crEls.barFill.style.transform = 'scaleX(' + Math.min(1, Math.max(0, (r.flight_start_ms - crServerNow()) / total)).toFixed(3) + ')';   // полоска убывает до старта полёта
    crShowMult(100);
    crDrawCurve(0);
    crEmitSkin('betting', r, 100, 0);
    crEls.label.textContent = 'Приём ставок: ' + crSeconds(r.flight_start_ms - crServerNow()) + ' с';
  } else if (r.result) {
    crSetTone('crash');
    crSetPhase('result');
    crSetZone(100);
    crShowMult(r.result.crash_x100);
    crDrawCurve(CR_DOUBLING_MS * Math.log2(Math.max(1, r.result.crash_x100 / 100)));
    crEmitSkin('result', r, r.result.crash_x100, CR_DOUBLING_MS * Math.log2(Math.max(1, r.result.crash_x100 / 100)));
    const next = typeof r.next_open_ms === 'number' ? ' · следующий раунд через ' + crSeconds(r.next_open_ms - crServerNow()) + ' с' : '';
    crEls.label.textContent = 'Крах ' + crText(r.result.crash_x100) + next;
  }
}

function crRaf() {
  cr.raf = 0;
  crDraw();
  if (crOnScreen()) cr.raf = requestAnimationFrame(crRaf);
}

const crOnScreen = () => activeTab === 'play' && currentGame === 'crash';

function crStartLoop() {
  crStopLoop();
  crDraw();
  if (reducedMotion()) cr.iv = setInterval(crDraw, 500);   // без плавной анимации: число реже
  else cr.raf = requestAnimationFrame(crRaf);
}

function crStopLoop() {
  if (cr.raf) cancelAnimationFrame(cr.raf);
  if (cr.iv) clearInterval(cr.iv);
  cr.raf = 0;
  cr.iv = 0;
}

// ---------- отрисовка экрана (не кадр графика) ----------
function renderCrHistory() {
  const hist = cr.state ? cr.state.history.slice(0, CR_HISTORY) : [];
  crEls.history.hidden = hist.length === 0;
  crEls.history.textContent = '';
  hist.forEach((h) => {
    const li = document.createElement('li');
    li.textContent = crText(h.crash_x100);
    if (h.crash_x100 >= 1000) li.className = 'z3';           // серый до ×2, янтарный до ×10, золотой выше
    else if (h.crash_x100 >= 200) li.className = 'z2';
    crEls.history.appendChild(li);
  });
}

function renderCrFeed() {
  const bets = cr.state ? cr.state.bets : [];
  const r = crRound();
  crEls.feedTitle.textContent = bets.length ? 'Ставки в раунде: ' + bets.length : (cr.loaded ? 'Ставок пока нет' : '');
  crEls.feed.textContent = '';
  bets.forEach((b) => {
    const li = document.createElement('li');
    li.dataset.status = b.status;
    const name = document.createElement('span');
    name.className = 'cr-feed-name';
    name.textContent = b.name;
    const amount = document.createElement('span');
    amount.className = 'cr-feed-bet';
    amount.textContent = formatCompact(b.bet);
    amount.title = formatNumber(b.bet);
    const res = document.createElement('span');
    res.className = 'cr-feed-res';
    if (b.status === 'cashed') res.textContent = crText(b.cashed_x100) + ' +' + formatCompact(b.payout - b.bet);
    else if (b.status === 'lost') res.textContent = r && r.result ? '−' + formatCompact(b.bet) : '…';
    else res.textContent = '…';
    li.append(name, amount, res);
    crEls.feed.appendChild(li);
  });
}

// Итог моей ставки единым блоком результата (UI_REVIEW 1.6). Пока ставка только принята или идёт полёт, блока нет: ставка видна на кнопке и в ленте
function renderCrBanner() {
  const r = crRound();
  const me = crMe();
  let show = false;
  if (r && me && me.status === 'cashed' && (r.phase === 'flight' || r.result)) {
    const profit = me.payout - me.bet;
    const counting = cr.countRound === r.id && performance.now() < cr.countUntil;
    fillResult(crEls.banner, { kind: 'win', title: 'Забрали ' + crText(me.cashed_x100), amount: profit, sign: '+', note: r.result ? 'Крах был ' + crText(r.result.crash_x100) : '', keepAmount: counting });
    show = true;
    crCountUpOnce(r.id, profit);
  } else if (r && r.result && me && me.status === 'lost') {
    fillResult(crEls.banner, { kind: 'lose', title: 'Крах ' + crText(r.result.crash_x100), amount: me.bet, sign: '−' });
    show = true;
  }
  crEls.banner.hidden = !show;
}

// Выигрыш «набегает» цифрами и всплывает «+X» (один раз за раунд; без анимации при уменьшенном движении)
function crCountUpOnce(roundId, profit) {
  if (cr.popShown.has(roundId)) return;
  if (cr.popShown.size > 30) cr.popShown.clear();
  cr.popShown.add(roundId);
  if (reducedMotion()) return;
  cr.countRound = roundId;
  cr.countUntil = performance.now() + 650;
  const el = crEls.bannerAmount;
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / 600);
    const v = Math.round(profit * (1 - Math.pow(1 - k, 3)));
    el.textContent = '+' + formatCompact(v);
    if (k < 1) requestAnimationFrame(step);
    else el.textContent = '+' + formatCompact(profit);
  };
  requestAnimationFrame(step);
  const pop = crEls.pop;
  pop.textContent = '+' + formatCompact(profit);
  if (pop.animate) pop.animate([{ opacity: 0, transform: 'translate(-50%, 8px) scale(0.8)' }, { opacity: 1, transform: 'translate(-50%, -6px) scale(1.1)', offset: 0.3 }, { opacity: 0, transform: 'translate(-50%, -34px) scale(1)' }], { duration: 1200, easing: 'ease-out' });
}

// Стопка аватарок игроков раунда (до пяти) и «+N»: раунд общий, это должно быть видно
function renderCrCrowd() {
  const bets = cr.state ? cr.state.bets : [];
  const key = bets.length + ':' + bets.slice(0, 5).map((b) => b.name).join('|');
  if (crEls.crowd.dataset.key === key) return;
  crEls.crowd.dataset.key = key;
  crEls.crowd.textContent = '';
  bets.slice(0, 5).forEach((b) => {
    const av = document.createElement('span');
    av.className = 'avatar';
    av.textContent = initialOf(b.name);
    crEls.crowd.appendChild(av);
  });
  if (bets.length > 5) {
    const more = document.createElement('span');
    more.className = 'cr-crowd-more';
    more.textContent = '+' + (bets.length - 5);
    crEls.crowd.appendChild(more);
  }
}

// Лента «Имя забрал ×N» над графиком: до трёх строк, появляются и гаснут. Новые выводы берём из разницы ответов (при входе в раунд ленту не заливаем историей)
function crTickerUpdate() {
  const r = crRound();
  const bets = cr.state ? cr.state.bets : [];
  if (!r) return;
  const first = cr.tickRound !== r.id;
  if (first) { cr.tickRound = r.id; cr.tickSeen = new Set(); }
  bets.forEach((b) => {
    if (b.status !== 'cashed') return;
    const key = b.name + '|' + b.bet + '|' + b.cashed_x100;
    if (cr.tickSeen.has(key)) return;
    cr.tickSeen.add(key);
    if (!first && r.phase === 'flight' && !reducedMotion()) crTickerPush(b.name, b.cashed_x100);
  });
}

function crTickerPush(name, x100) {
  const li = document.createElement('li');
  const nick = document.createElement('span');
  nick.textContent = name + ' забрал ';
  const x = document.createElement('b');
  x.textContent = crText(x100);
  li.append(nick, x);
  crEls.ticker.appendChild(li);
  while (crEls.ticker.children.length > 3) crEls.ticker.firstChild.remove();
  if (!li.animate) { setTimeout(() => li.remove(), 3500); return; }
  li.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none', offset: 0.1 }, { opacity: 1, transform: 'none', offset: 0.8 }, { opacity: 0, transform: 'none' }], { duration: 3500, easing: 'ease-out' }).onfinish = () => li.remove();
}

// шапка и кнопки без перерисовки графика
function renderCrashChrome() {
  if (cr.balance !== null) {
    crEls.balance.classList.remove('skeleton');
    crEls.balance.textContent = spacedNumber(cr.balance);
    fitNumberFont(crEls.balance, crEls.balance.textContent.length);
  }
  crEls.switchBtn.disabled = cr.busy;
  crEls.cash.disabled = cr.busy;
  if (cr.busy && crMe() && crMe().status === 'open') crEls.cash.textContent = 'Фиксируем…';
}

function renderCrash() {
  if (cr.balance === null && srv.balance !== null) cr.balance = srv.balance;      // у живого состояния своего баланса нет: стартовый берётся из общего (/api/me)
  const loading = !cr.loaded;
  crEls.skel.hidden = !(loading && !cr.error);
  crEls.chart.hidden = loading;
  const r = crRound();
  const me = crMe();
  const betting = !!r && r.phase === 'betting';
  const cashing = !!r && r.phase === 'flight' && !!me && me.status === 'open';
  crEls.bets.hidden = loading || cashing;
  crEls.actions.hidden = !cashing;
  renderCrashChrome();
  renderCrHistory();
  renderCrFeed();
  renderCrBanner();
  renderCrProof();
  renderCrCrowd();
  crTickerUpdate();
  renderCrChips();
  syncCrChips();
  const canBet = betting && !me && !cr.busy && cr.balance !== null;
  crEls.bets.querySelectorAll('button, input').forEach((el) => { el.disabled = !canBet; });
  crEls.start.disabled = !canBet;
  crEls.start.textContent = me && betting ? 'Ставка принята' : (betting ? 'Поставить' : 'Ждём приёма ставок');
  if (cr.loaded && crOnScreen() && !cr.raf && !cr.iv) crStartLoop();
  crDraw();
  refreshBetPanels();
}

// #endregion
