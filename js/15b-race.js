// #region Забег свинок
// ---------- игра «Забег свинок» ----------
// Один раунд за запрос: клиент шлёт число дорожек, выбранную дорожку и ставку; сервер выбирает победителя и отдаёт script
// (40 кадров позиций всех дорожек, последний кадр: победитель первым на финише). Клиент денег не считает: баланс берётся
// из ответа сервера после анимации. Выбор дорожки и ставка живут только в памяти.
const RACE_BET_MAX = 1000000000;
const RACE_FRAMES = 40;
const RACE_FRAME_MS = 100;        // 40 кадров ≈ 4 секунды
const RACE_FINISH = 1000;
const RACE_LANES = [3, 4];

const raceEls = {
  balance: document.getElementById('race-balance'),
  switchBtn: document.getElementById('race-switch'),
  notice: document.getElementById('race-notice'),
  laneButtons: Array.from(document.querySelectorAll('[data-race-lanes]')),
  track: document.getElementById('race-track'),
  pick: document.getElementById('race-pick'),
  result: document.getElementById('race-result'),
  resultTitle: document.getElementById('race-result-title'),
  resultDetail: document.getElementById('race-result-detail'),
  panel: document.getElementById('race-bets'),
  bet: document.getElementById('race-bet'),
  half: document.getElementById('race-half'),
  double: document.getElementById('race-double'),
  maxBtn: document.getElementById('race-max'),
  play: document.getElementById('race-play')
};

const rc = {
  lanes: 3,          // число дорожек: 3 или 4
  lane: null,        // выбранная дорожка (0..lanes-1) или null
  busy: false,       // идёт раунд (запрос и анимация): выбор и кнопки заблокированы
  pigs: [],          // { el, lane } по дорожкам текущей сборки
  result: null,      // { lane, winner, payout, bet } последнего раунда
  noticeTimer: null
};
registerGame({ id: 'race', busy: () => rc.busy });

// Свинка: один и тот же SVG на всех дорожках, цвет через класс (палитра в css/15b-race.css)
const RACE_PIG_SVG = '<svg viewBox="0 0 40 28" aria-hidden="true">'
  + '<ellipse class="race-pig-body" cx="19" cy="15" rx="13" ry="9"/>'
  + '<ellipse class="race-pig-snout" cx="31" cy="15" rx="3.5" ry="3"/>'
  + '<circle class="race-pig-eye" cx="25" cy="11" r="1.2"/>'
  + '<path class="race-pig-ear" d="M22 7 L25 3 L27 8 Z"/>'
  + '<rect class="race-pig-leg" x="11" y="21" width="3" height="5" rx="1"/>'
  + '<rect class="race-pig-leg" x="21" y="21" width="3" height="5" rx="1"/>'
  + '</svg>';

const raceBetLimit = () => Math.max(1, Math.min(RACE_BET_MAX, srv.loaded ? srv.balance : RACE_BET_MAX));

function setRaceNotice(text) {
  clearTimeout(rc.noticeTimer);
  raceEls.notice.textContent = text;
  if (text) rc.noticeTimer = setTimeout(() => { raceEls.notice.textContent = ''; }, 3500);
}

function resetRaceResult() {
  rc.result = null;
  raceEls.result.hidden = true;
  rc.pigs.forEach((pig) => pig.lane.classList.remove('win'));
}

// Дорожки и кнопки выбора строятся заново при смене числа дорожек
function buildRaceTrack() {
  raceEls.track.textContent = '';
  raceEls.pick.textContent = '';
  rc.pigs = [];
  for (let n = 0; n < rc.lanes; n++) {
    const lane = document.createElement('div');
    lane.className = 'race-lane';
    lane.dataset.lane = String(n);
    const pig = document.createElement('div');
    pig.className = 'race-pig';
    pig.innerHTML = RACE_PIG_SVG;
    lane.appendChild(pig);
    raceEls.track.appendChild(lane);
    rc.pigs.push({ el: pig, lane });

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'race-pick-btn';
    btn.dataset.lane = String(n);
    btn.setAttribute('aria-pressed', 'false');
    btn.textContent = 'Дорожка ' + (n + 1);
    btn.addEventListener('click', () => pickRaceLane(n));
    raceEls.pick.appendChild(btn);
  }
}

function renderRace() {
  raceEls.laneButtons.forEach((btn) => {
    btn.setAttribute('aria-pressed', String(Number(btn.dataset.raceLanes) === rc.lanes));
    btn.disabled = rc.busy;
  });
  raceEls.pick.querySelectorAll('.race-pick-btn').forEach((btn) => {
    const on = Number(btn.dataset.lane) === rc.lane;
    btn.setAttribute('aria-pressed', String(on));
    btn.disabled = rc.busy;
  });
  raceEls.play.disabled = rc.busy || rc.lane === null;
  raceEls.balance.textContent = srv.loaded ? spacedNumber(availableBalance()) : (srv.error ? '—' : 'Загрузка…');
  raceEls.balance.classList.toggle('skeleton', !srv.loaded && !srv.error);
}

function setRaceLanes(n) {
  if (rc.busy || rc.lanes === n) return;
  rc.lanes = n;
  if (rc.lane !== null && rc.lane >= n) rc.lane = null;
  resetRaceResult();
  buildRaceTrack();
  renderRace();
}

function pickRaceLane(n) {
  if (rc.busy) return;
  resetRaceResult();
  rc.lane = n;
  haptic('light');
  renderRace();
}

// Фишки ставки по серверному балансу (те же номиналы, что в других играх)
const raceChipBar = makeChipBar({
  root: raceEls.panel,
  attr: 'rbet',
  getBalance: () => (srv.loaded ? srv.balance : 0),
  getBet: () => Number(raceEls.bet.value),
  setBet: (v) => { raceEls.bet.value = String(v); }
});
const renderRaceChips = raceChipBar.render;
const syncRaceChips = raceChipBar.sync;

// Позиции кадра (0..1000 на дорожку) на полосе: свинка смещается transform по ширине дорожки
function paintRaceFrame(positions) {
  rc.pigs.forEach((pig, j) => {
    const room = Math.max(0, pig.lane.clientWidth - pig.el.offsetWidth);
    pig.el.style.transform = 'translateX(' + Math.round((positions[j] / RACE_FINISH) * room) + 'px)';
  });
}

// Анимация: линейная интерполяция между кадрами script; без движения (reduced motion) сразу финальный кадр
function animateRace(script) {
  if (reducedMotion()) {
    paintRaceFrame(script[script.length - 1]);
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const total = (script.length - 1) * RACE_FRAME_MS;
    const t0 = performance.now();
    const step = (now) => {
      const t = Math.min(total, now - t0);
      const i = Math.min(script.length - 2, Math.floor(t / RACE_FRAME_MS));
      const k = (t - i * RACE_FRAME_MS) / RACE_FRAME_MS;
      const a = script[i];
      const b = script[i + 1];
      paintRaceFrame(a.map((v, j) => v + (b[j] - v) * k));
      if (t < total) requestAnimationFrame(step);
      else resolve();
    };
    requestAnimationFrame(step);
  });
}

// Ответ сервера: все поля обязательны, script — 40 кадров по числу дорожек; на финише победитель 1000, остальные ниже
function validRaceRound(d, lanes, lane) {
  if (!d || typeof d !== 'object') return false;
  if (!Number.isInteger(d.round_id) || d.lanes !== lanes || d.lane !== lane) return false;
  if (!Number.isInteger(d.winner) || d.winner < 0 || d.winner >= lanes) return false;
  if (!Number.isInteger(d.payout) || d.payout < 0 || !Number.isInteger(d.balance) || d.balance < 0) return false;
  if (!Array.isArray(d.script) || d.script.length !== RACE_FRAMES) return false;
  const frameOk = (f) => Array.isArray(f) && f.length === lanes && f.every((v) => Number.isInteger(v) && v >= 0 && v <= RACE_FINISH);
  if (!d.script.every(frameOk)) return false;
  const last = d.script[RACE_FRAMES - 1];
  return last[d.winner] === RACE_FINISH && last.every((v, j) => j === d.winner || v < RACE_FINISH);
}

async function postRaceOnce(payload, lanes, lane) {
  try {
    const res = await postJson('/api/race/play', payload);
    if (res.status === 401) {
      return { kind: 'fatal', text: 'Не удалось подтвердить Telegram. Закройте игру и откройте её заново через бота' };
    }
    if (res.status === 400) return { kind: 'fatal', text: 'Неверные параметры' };
    if (res.status === 409) {
      let body = {};
      try { body = await res.json(); } catch (e) { body = {}; }
      return { kind: 'conflict', detail: typeof body.detail === 'string' ? body.detail : '' };
    }
    if (!res.ok) {
      return isServerError(res) ? { kind: 'retry', code: String(res.status) }
        : { kind: 'fatal', text: 'Не удалось выполнить раунд' };
    }
    const body = await readJsonBody(res);
    // 2xx не повторяем: раунд уже обработан сервером; непонятный ответ = повод обновить баланс отдельным запросом
    return body.ok && validRaceRound(body.data, lanes, lane) ? { kind: 'ok', data: body.data } : { kind: 'invalid' };
  } catch (e) {
    return { kind: 'retry', code: e && e.name === 'AbortError' ? 'таймаут' : 'сеть' };
  }
}

function showRaceResult(title, detail) {
  raceEls.resultTitle.textContent = title;
  raceEls.resultDetail.textContent = detail;
  raceEls.result.hidden = false;
}

async function raceStart() {
  if (rc.busy) return;
  const bet = Number(raceEls.bet.value);
  if (!Number.isInteger(bet) || bet < 1 || bet > RACE_BET_MAX) {
    setRaceNotice('Введите целую ставку от 1 до ' + formatNumber(RACE_BET_MAX));
    return;
  }
  if (rc.lane === null) { setRaceNotice('Выберите дорожку'); return; }
  if (!srv.loaded || bet > srv.balance) { setRaceNotice('Не хватает фишек'); return; }
  if (!(tg && tg.initData)) { setRaceNotice('Откройте игру через бота в Telegram'); return; }
  const id = makeRequestId();
  if (!id) { setRaceNotice('Ошибка'); return; }
  const lanes = rc.lanes;
  const lane = rc.lane;
  setRaceNotice('');
  resetRaceResult();
  rc.busy = true;
  renderRace();
  const result = await postWithRetries(() => postRaceOnce({ request_id: id, lanes, lane, bet }, lanes, lane));
  let note = 'Баланс обновлён';
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      await animateRace(d.script);
      rc.pigs[d.winner].lane.classList.add('win');
      rc.result = { lane, winner: d.winner, payout: d.payout, bet };
      const won = d.payout > 0;
      showRaceResult(won ? 'Выигрыш ' + formatNumber(d.payout) : 'Проигрыш',
        won ? 'Дорожка ' + (d.winner + 1) + ' пришла первой' : 'Первой пришла дорожка ' + (d.winner + 1));
      srv.balance = d.balance;      // баланс на экране меняется только после анимации
      srv.loaded = true;
      srv.error = null;
      rc.busy = false;
      renderBalance();
      renderRace();
      haptic(won ? 'success' : 'error');
      loadServer('after');
      return;
    } catch (e) {
      // ошибка нашего кода после успешного ответа: POST не повторяем, баланс берём отдельным запросом
      note = 'Не удалось показать результат. Баланс обновлён';
    }
  } else if (result && result.kind === 'invalid') {
    note = 'Ответ сервера не распознан. Баланс обновлён';
  } else if (result && result.kind === 'fatal') {
    note = result.text;
  } else if (result && result.kind === 'conflict') {
    if (result.detail === 'insufficient_funds') note = 'Не хватает фишек';
    else if (result.detail === 'balance_limit') note = 'Достигнут максимальный баланс';
    else note = 'Не удалось выполнить раунд';
  } else {
    note = 'Нет связи. Баланс обновлён';
  }
  rc.busy = false;
  setRaceNotice(note);
  renderRace();
  loadServer('after');
}

raceEls.laneButtons.forEach((btn) => {
  btn.addEventListener('click', () => setRaceLanes(Number(btn.dataset.raceLanes)));
});
raceEls.play.addEventListener('click', () => raceStart());
raceEls.half.addEventListener('click', () => {
  if (rc.busy) return;
  raceEls.bet.value = String(Math.max(1, Math.floor(Number(raceEls.bet.value) / 2)));
  syncRaceChips();
});
raceEls.double.addEventListener('click', () => {
  if (rc.busy) return;
  raceEls.bet.value = String(Math.min(raceBetLimit(), Number(raceEls.bet.value) * 2));
  syncRaceChips();
});
raceEls.maxBtn.addEventListener('click', () => {
  if (rc.busy) return;
  raceEls.bet.value = String(raceBetLimit());
  syncRaceChips();
});
raceEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js

buildRaceTrack();
renderRaceChips();
renderRace();
// #endregion
