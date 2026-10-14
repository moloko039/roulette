// #region Общий забег свинок (UI_REVIEW 3.5)
// Режим «Общий» экрана «Свинки»: раунд один на весь сервер, раз в 30 секунд (20 секунд приём ставок, затем забег). Все данные и исход только с сервера
// (GET /api/race/live раз в секунду, POST /api/race/live/bet); анимация та же, что в одиночном забеге (runRace). Баланс на экране меняется после забега.
const RL_POLL_MS = 1000;
const RL_TICK_MS = 250;
const rl = {
  mode: false, timer: 0, tick: 0, d: null, skew: 0, shownRound: 0, animating: false, busy: false, loading: false, pendingBalance: null,
  els: {
    box: document.getElementById('race-live'), phase: document.getElementById('race-live-phase'), time: document.getElementById('race-live-time'),
    fill: document.getElementById('race-live-fill'), feed: document.getElementById('race-feed'),
    modes: Array.from(document.querySelectorAll('[data-race-mode]'))
  }
};

const raceLiveActive = () => rl.mode;

function validRaceLive(d) {
  if (!d || typeof d !== 'object' || !Number.isInteger(d.server_ms) || d.lanes !== 4 || !d.round || !Number.isInteger(d.round.id) || !Number.isInteger(d.round.close_ms)) return false;
  if (!['bets', 'race', 'result'].includes(d.round.phase) || !Number.isInteger(d.round.start_ms) || !Number.isInteger(d.round.race_end_ms) || !Number.isInteger(d.round.next_ms)) return false;
  if (!Array.isArray(d.bets) || !Array.isArray(d.counts) || d.counts.length !== 4 || !d.counts.every(isCount)) return false;
  if (d.me !== null && !(d.me && Number.isInteger(d.me.lane) && isCount(d.me.bet))) return false;
  if (d.result !== null) {
    const r = d.result;
    if (!r || !Number.isInteger(r.winner) || r.winner < 0 || r.winner > 3 || !Array.isArray(r.script) || r.script.length !== RACE_FRAMES) return false;
    if (!r.script.every((f) => Array.isArray(f) && f.length === 4 && f.every((v) => Number.isInteger(v) && v >= 0 && v <= RACE_FINISH))) return false;
  }
  return true;
}

const rlNow = () => Date.now() + rl.skew;

// Подписи над дорожками: сколько ставок беседы на каждой
function rlRenderCrowd() {
  if (!rl.d) return;
  rc.pigs.forEach((p, j) => {
    let el = p.lane.querySelector('.race-crowd');
    if (!el) { el = document.createElement('span'); el.className = 'race-crowd'; p.lane.appendChild(el); }
    const n = rl.d.counts[j] || 0;
    el.textContent = n ? String(n) : '';
    el.hidden = !n;
    el.setAttribute('aria-label', n ? 'Ставок на дорожку: ' + n : '');
  });
}

function rlRenderFeed() {
  const feed = rl.els.feed;
  feed.hidden = !rl.mode || !rl.d || !rl.d.bets.length;
  feed.textContent = '';
  if (!rl.d) return;
  rl.d.bets.slice(-5).reverse().forEach((b) => {
    const li = document.createElement('li');
    li.className = b.me ? 'me' : '';
    const who = document.createElement('b');
    who.textContent = b.me ? 'Вы' : b.name;
    const what = document.createElement('span');
    what.textContent = ' поставил на №' + (b.lane + 1) + ' · ' + formatCompact(b.bet);
    li.append(who, what);
    feed.appendChild(li);
  });
}

// Таймер раунда: приём ставок (убывающая полоса), забег, итог
function rlRenderClock() {
  if (!rl.mode || !rl.d) return;
  const r = rl.d.round;
  const now = rlNow();
  let text;
  let ratio;
  if (r.phase === 'bets' && now < r.close_ms) {
    const left = Math.max(0, r.close_ms - now);
    text = 'Приём ставок';
    rl.els.time.textContent = Math.ceil(left / 1000) + ' с';
    ratio = left / (r.close_ms - r.start_ms);
  } else if (now < r.race_end_ms && r.phase !== 'result') {
    text = rl.animating ? 'Забег идёт' : 'Забег';
    rl.els.time.textContent = '';
    ratio = 0;
  } else {
    text = 'Следующий забег';
    rl.els.time.textContent = Math.max(0, Math.ceil((r.next_ms - now) / 1000)) + ' с';
    ratio = 0;
  }
  rl.els.phase.textContent = text;
  rl.els.fill.style.transform = 'scaleX(' + Math.max(0, Math.min(1, ratio)).toFixed(3) + ')';
}

// Кнопка ставки и состояние выбора, поверх обычной отрисовки экрана (renderRace вызывает это в конце)
function raceLiveRender() {
  const live = rl.mode;
  document.querySelector('[data-screen=race]').classList.toggle('live', live);
  rl.els.box.hidden = !live;
  rl.els.modes.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.raceMode === (live ? 'live' : 'solo'))));
  if (!live) { rl.els.feed.hidden = true; return; }
  const d = rl.d;
  const bets = !!d && d.round.phase === 'bets' && rlNow() < d.round.close_ms;
  const placed = !!d && !!d.me;
  const betNum = Number(raceEls.bet.value);
  const betText = Number.isInteger(betNum) && betNum > 0 ? ' · ' + formatCompact(betNum) : '';
  raceEls.play.disabled = !bets || placed || rl.busy || rc.lane === null;
  if (!d) raceEls.play.textContent = 'Загрузка…';
  else if (placed) raceEls.play.textContent = 'Ставка принята: №' + (d.me.lane + 1) + ' · ' + formatCompact(d.me.bet);
  else if (!bets) raceEls.play.textContent = 'Ждите следующий забег';
  else raceEls.play.textContent = rc.lane === null ? 'Выберите дорожку' : 'Поставить' + betText;
  rc.pigs.forEach((p, j) => {
    const mine = placed && d.me.lane === j;
    const on = mine || (!placed && j === rc.lane);
    p.lane.setAttribute('aria-pressed', String(on));
    p.tag.hidden = !on;
    p.el.classList.toggle('mine', on);
  });
  rlRenderCrowd();
  rlRenderFeed();
  rlRenderClock();
}

// Тап по дорожке в общем режиме: выбор только во время приёма и пока своей ставки нет; во время забега тап показывает финиш
function raceLivePick(n) {
  if (!rl.mode) return false;
  if (rc.running) { if (rc.skip) rc.skip(); return true; }
  const d = rl.d;
  if (!d || d.round.phase !== 'bets' || d.me || rl.busy) return true;
  rc.lane = n;
  haptic('light');
  renderRace();
  return true;
}

function rlMarkFinish(winner, end) {
  const winnerPig = rc.pigs[winner];
  winnerPig.lane.classList.add('win');
  winnerPig.el.classList.add('winner');
  rc.pigs.forEach((p) => p.el.classList.add('done'));
  raceEls.field.classList.add('finished');
  winnerPig.finish.classList.remove('flash');
  void winnerPig.finish.offsetWidth;
  winnerPig.finish.classList.add('flash');
  if (end && !end.layout.lite) raceSparks(winnerPig, end.finalState.xs[winner]);
}

function rlShowResult(d) {
  const w = d.result.winner;
  const note = 'Победила №' + (w + 1) + ' ' + RACE_CHARS[w].name;
  const me = d.me;
  if (me && me.payout !== null && me.payout !== undefined) {
    const won = me.payout > 0;
    showRaceResult(won ? { kind: 'win', title: 'Выигрыш', amount: me.payout - me.bet, sign: '+', note } : { kind: 'lose', title: 'Проигрыш', amount: me.bet, sign: '−', note });
    haptic(won ? 'success' : 'error');
  } else {
    showRaceResult({ kind: 'push', title: 'Забег окончен', note });
  }
}

// Применить баланс сервера после показа результата
function rlApplyBalance(d) {
  if (d && Number.isInteger(d.balance)) { srv.balance = d.balance; srv.loaded = true; srv.error = null; renderBalance(); }
  renderRace();
  loadServer('after');
}

// Забег раунда: с начала, если приём закрылся только что, иначе сразу финиш (зашли посреди забега или после него)
async function rlPlayRound(d) {
  const id = d.round.id;
  rl.shownRound = id;
  rl.animating = true;
  const late = rlNow() - d.round.close_ms;
  const w = d.result.winner;
  try {
    resetRaceTrack();
    raceEls.result.hidden = true;
    let end = null;
    if (late < 2500 && !reducedMotion()) {
      end = await runRace(d.result.script, w, false);
    } else {
      const layout = raceLayout();
      const st = raceFrameState(d.result.script[RACE_FRAMES - 1], layout);
      rc.pigs.forEach((p, j) => {
        p.el.style.transform = 'translate3d(' + st.xs[j].toFixed(1) + 'px,0,0)';
        p.scroll.style.transform = 'translate3d(' + (-st.cam * layout.k).toFixed(1) + 'px,0,0)';
      });
      end = { layout, finalState: st };
    }
    rlMarkFinish(w, end);
  } catch (e) {
    cancelRaceAnims();
    rc.running = false;
    raceEls.field.classList.remove('running');
  }
  rl.animating = false;
  if (!rl.mode) return;
  const latest = rl.d && rl.d.round.id === id ? rl.d : d;
  rlShowResult(latest);
  rlApplyBalance(latest);
}

function rlNewRound() {
  rc.lane = null;
  rl.pendingBalance = null;
  raceEls.result.hidden = true;
  resetRaceTrack();
  renderRace();
}

function rlHandle(d) {
  const prev = rl.d;
  rl.d = d;
  rl.skew = d.server_ms - Date.now();
  if (!prev || prev.round.id !== d.round.id) {
    if (d.round.phase === 'bets') {
      if (prev && prev.round.id !== d.round.id && !rl.animating) rlNewRound();
      else if (!prev) { rc.lane = null; }
    }
  }
  if (d.result && d.round.phase !== 'bets' && rl.shownRound !== d.round.id && !rl.animating) {
    rlPlayRound(d);
  } else if (!d.result && d.round.phase === 'bets' && !rl.animating && !prev) {
    // зашли в приём ставок: свинки у ворот
    resetRaceTrack();
  }
  if (!d.result && d.last && !prev && d.round.phase === 'bets' && d.last.payout !== null && rl.shownRound === 0) {
    // вернулись в игру: что стало с прошлой ставкой
    const note = 'Прошлый забег: победила №' + (d.last.winner + 1);
    const won = d.last.payout > 0;
    showRaceResult(won ? { kind: 'win', title: 'Выигрыш', amount: d.last.payout - d.last.bet, sign: '+', note } : { kind: 'lose', title: 'Проигрыш', amount: d.last.bet, sign: '−', note });
    rl.shownRound = -1;
  }
  renderRace();
}

async function rlPoll() {
  if (!rl.mode || rl.loading || !(tg && tg.initData)) return;
  rl.loading = true;
  try {
    rlHandle(await fetchGameState('/api/race/live', validRaceLive));
  } catch (e) {
    if (rl.mode && !rl.d) rl.els.phase.textContent = e && e.text ? e.text : 'Нет связи с сервером';
  } finally {
    rl.loading = false;
  }
}

function raceLiveStop() {
  clearInterval(rl.timer);
  clearInterval(rl.tick);
  rl.timer = 0;
  rl.tick = 0;
}

function raceLiveStart() {
  raceLiveStop();
  if (!rl.mode) return;
  rlPoll();
  rl.timer = setInterval(rlPoll, RL_POLL_MS);
  rl.tick = setInterval(() => { rlRenderClock(); if (rl.d && rl.d.round.phase === 'bets' && rlNow() >= rl.d.round.close_ms) renderRace(); }, RL_TICK_MS);
}

// Экран «Свинки» открыт или закрыт (showTab в js/14-lobby.js)
function raceLiveScreen(open) {
  if (open && rl.mode) raceLiveStart();
  else raceLiveStop();
}

function raceSetMode(live) {
  if (rc.busy || rl.animating || rl.mode === live) return;
  rl.mode = live;
  rl.d = null;
  rl.shownRound = 0;
  rc.lane = null;
  rc.played = false;
  raceEls.result.hidden = true;
  if (live) {
    if (rc.lanes !== 4) setRaceLanes(4); else { resetRaceTrack(); }
    raceLiveStart();
  } else {
    raceLiveStop();
    resetRaceTrack();
  }
  renderRace();
}

async function postRaceLiveOnce(payload) {
  try {
    const res = await postJson('/api/race/live/bet', payload);
    if (res.status === 401) return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    if (res.status === 400) return { kind: 'fatal', text: 'Неверные параметры' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      return { kind: 'conflict', detail: typeof body.detail === 'string' ? body.detail : '' };
    }
    if (!res.ok) return isServerError(res) ? { kind: 'retry', code: String(res.status) } : { kind: 'fatal', text: 'Не удалось поставить' };
    const body = await readJsonBody(res);
    return body.ok && body.data && Number.isInteger(body.data.round_id) && Number.isInteger(body.data.balance) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

async function raceLiveBet() {
  const d = rl.d;
  if (!rl.mode || rl.busy || !d || d.me || d.round.phase !== 'bets') return;
  const bet = Number(raceEls.bet.value);
  if (!Number.isInteger(bet) || bet < 1 || bet > RACE_BET_MAX) { setRaceNotice('Введите целую ставку от 1 до ' + formatNumber(RACE_BET_MAX)); return; }
  if (rc.lane === null) { setRaceNotice('Выберите дорожку'); return; }
  if (!srv.loaded || bet > srv.balance) { setRaceNotice('Не хватает фишек'); return; }
  const id = makeRequestId();
  if (!id) { setRaceNotice('Ошибка'); return; }
  rl.busy = true;
  setRaceNotice('');
  renderRace();
  haptic('medium');
  const lane = rc.lane;
  const result = await postWithRetries(() => postRaceLiveOnce({ request_id: id, lane, bet }));
  rl.busy = false;
  if (result && result.kind === 'ok') {
    srv.balance = result.data.balance;        // ставка уже списана: исход ещё неизвестен
    srv.loaded = true;
    renderBalance();
    rlPoll();
  } else if (result && result.kind === 'conflict') {
    const map = { betting_closed: 'Приём ставок закрыт', already_bet: 'Ставка в этом забеге уже есть', insufficient_funds: 'Не хватает фишек', balance_limit: 'Достигнут максимальный баланс', round_full: 'В забеге слишком много ставок' };
    setRaceNotice(map[result.detail] || 'Не удалось поставить');
    rlPoll();
  } else if (result && result.kind === 'fatal') {
    setRaceNotice(result.text);
  } else {
    setRaceNotice('Нет связи с сервером');
    rlPoll();
  }
  renderRace();
}

rl.els.modes.forEach((b) => b.addEventListener('click', () => raceSetMode(b.dataset.raceMode === 'live')));
document.addEventListener('visibilitychange', () => { if (rl.mode) { if (document.hidden) raceLiveStop(); else if (!document.querySelector('[data-screen=race]').hidden) raceLiveStart(); } });
// #endregion
