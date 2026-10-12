// #region Забег свинок
// ---------- игра «Забег свинок» ----------
// Один раунд за запрос: клиент шлёт число дорожек, выбранную дорожку и ставку; сервер выбирает победителя и отдаёт готовый сценарий
// (40 кадров позиций всех дорожек, последний кадр: победитель первым на финише). Анимация только рисует этот сценарий (логики исхода
// в ней нет, до сигнала «Марш» победитель не подсвечивается). Клиент денег не считает: баланс из ответа сервера после анимации.
// Забег идёт одним набором Web Animations (общее время, только transform): камера следует за лидером, разметка и финиш едут назад.
// Выбор дорожки (тап по самой дорожке) и ставка живут только в памяти; «Быстро» хранится в localStorage.
const RACE_BET_MAX = 1000000000;
const RACE_FRAMES = 40;
const RACE_FRAME_MS = 170;         // обычный забег: 39 интервалов ≈ 6,6 с
const RACE_FRAME_MS_FAST = 80;     // быстрый режим ≈ 3 с, без отсчёта
const RACE_FINISH = 1000;
const RACE_VIEW = 500;             // сколько единиц пути видно в окне камеры
const RACE_ANCHOR = 0.6;           // лидер держится на 60% ширины окна, пока камера не упрётся в финиш
const RACE_LANES = [3, 4];
const RACE_PIG_W = 60;
const RACE_SAMPLE_MS = 50;         // шаг ключевых кадров WAAPI
const RACE_MAX_LEAD_BUZZ = 4;      // смена лидера: вибрация не чаще четырёх раз за забег
const RACE_COUNTDOWN_MS = 480;     // три огня гаснут по очереди
// Свинки с именами и манерой бега (только внешний вид и темп ног, шансы равные и считаются сервером): рывок со старта, ровный темп,
// финишный бросок, ленивая трусца. Имена вымышленные
const RACE_CHARS = [
  { name: 'Хрюня', style: 'sprint' },
  { name: 'Борька', style: 'steady' },
  { name: 'Дымка', style: 'finish' },
  { name: 'Тимоха', style: 'lazy' }
];

const raceEls = {
  balance: document.getElementById('race-balance'),
  switchBtn: document.getElementById('race-switch'),
  notice: document.getElementById('race-notice'),
  laneButtons: Array.from(document.querySelectorAll('[data-race-lanes]')),
  fast: document.getElementById('race-fast'),
  field: document.getElementById('race-field'),
  track: document.getElementById('race-track'),
  lights: document.getElementById('race-lights'),
  go: document.getElementById('race-go'),
  result: document.getElementById('race-result'),
  resultTitle: document.getElementById('race-result-title'),
  resultAmount: document.getElementById('race-result-amount'),
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
  running: false,    // идёт именно забег (после ответа сервера)
  played: false,     // в этой сессии уже был раунд: кнопка «Ещё раз»
  fast: false,
  pigs: [],          // { el, lane, scroll, finish, gate, dust } по дорожкам текущей сборки
  anims: [],         // активные Animation забега (пауза при скрытии приложения, пропуск тапом)
  skip: null,        // функция пропуска забега
  result: null,      // { lane, winner, payout, bet } последнего раунда
  noticeTimer: null
};
registerGame({ id: 'race', busy: () => rc.busy });
raceRender = renderRace;   // ядро перерисовывает экран после ответа /api/me (баланс не «Загрузка…»)
try { rc.fast = localStorage.getItem('race_fast') === '1'; } catch (e) { /* без хранилища обычный режим */ }

// Свинка: один и тот же SVG на всех дорожках (ноги и хвост анимируются transform), цвета через переменные --pg-* (css/15b-race.css)
const RACE_PIG_SVG = '<svg viewBox="0 0 56 36" aria-hidden="true">'
  + '<path class="pg-tail" d="M10 15c-4-1-6-4-4-7"/>'
  + '<g class="pg-legs pg-lb"><rect class="pg-leg" x="14" y="22" width="4.2" height="12" rx="2"/><rect class="pg-leg" x="19.5" y="22" width="4.2" height="12" rx="2"/></g>'
  + '<g class="pg-legs pg-lf"><rect class="pg-leg" x="33" y="22" width="4.2" height="12" rx="2"/><rect class="pg-leg" x="38.5" y="22" width="4.2" height="12" rx="2"/></g>'
  + '<g class="pg-torso"><ellipse class="pg-body" cx="26" cy="18" rx="17" ry="11"/>'
  + '<ellipse class="pg-spot" cx="20" cy="13" rx="4" ry="3"/><ellipse class="pg-spot" cx="31" cy="22" rx="3.2" ry="2.4"/>'
  + '<rect class="pg-blanket" x="18" y="8.5" width="13" height="12" rx="3"/></g>'
  + '<g class="pg-head"><path class="pg-ear" d="M40 11 L43 3.5 L47.5 10 Z"/>'
  + '<circle class="pg-body" cx="44" cy="17" r="8.5"/><ellipse class="pg-snout" cx="51.5" cy="19.5" rx="3.6" ry="3.1"/>'
  + '<circle class="pg-nose" cx="52.4" cy="18.6" r="0.7"/><circle class="pg-nose" cx="52.4" cy="20.6" r="0.7"/>'
  + '<circle class="pg-eye" cx="46.8" cy="14.2" r="1.4"/></g>'
  + '</svg>';

const raceBetLimit = () => Math.max(1, Math.min(RACE_BET_MAX, srv.loaded ? srv.balance : RACE_BET_MAX));
const raceMultText = (lanes) => '×' + (Math.floor((lanes * 36 * 100) / 37) / 100).toFixed(2);

function setRaceNotice(text) {
  clearTimeout(rc.noticeTimer);
  raceEls.notice.textContent = text;
  if (text) rc.noticeTimer = setTimeout(() => { raceEls.notice.textContent = ''; }, 3500);
}

// Размеры дорожки в пикселях: окно камеры или, в облегчённом режиме, прямая линия старт-финиш
function raceLayout() {
  const lane = rc.pigs.length ? rc.pigs[0].lane : null;
  const width = (lane && lane.clientWidth) || 320;
  const usable = Math.max(60, width - RACE_PIG_W - 12);
  const lite = reducedMotion() || document.documentElement.classList.contains('perf-lite');
  return { width, usable, lite, k: usable / RACE_VIEW };
}

// Экранное положение свинки и камеры при позициях пути pos (0..1000). Камера следует за лидером; без камеры (lite) путь линейный
function raceFrameState(pos, layout) {
  if (layout.lite) {
    return { cam: 0, xs: pos.map((v) => 6 + (v / RACE_FINISH) * layout.usable) };
  }
  const lead = Math.max.apply(null, pos);
  const cam = Math.min(RACE_FINISH - RACE_VIEW, Math.max(0, lead - RACE_VIEW * RACE_ANCHOR));
  return { cam, xs: pos.map((v) => Math.max(2, 6 + (v - cam) * layout.k)) };
}

function placeRaceWorld(layout) {
  rc.pigs.forEach((p) => {
    if (layout.lite) {
      p.finish.style.left = (layout.width - 22) + 'px';
      p.gate.style.display = 'none';
    } else {
      p.finish.style.left = Math.round(6 + RACE_FINISH * layout.k + RACE_PIG_W * 0.75) + 'px';
      p.gate.style.display = '';
    }
  });
}

// Дорожки строятся заново при смене числа дорожек; выбор делается тапом по самой дорожке
function buildRaceTrack() {
  raceEls.track.textContent = '';
  rc.pigs = [];
  const mult = raceMultText(rc.lanes);
  const initial = (typeof initialOf === 'function' && profileEls && profileEls.name ? initialOf(profileEls.name.textContent) : '·');
  for (let n = 0; n < rc.lanes; n++) {
    const lane = document.createElement('div');
    lane.className = 'race-lane';
    lane.dataset.lane = String(n);
    lane.setAttribute('role', 'button');
    lane.tabIndex = 0;
    lane.setAttribute('aria-pressed', 'false');
    lane.setAttribute('aria-label', 'Дорожка ' + (n + 1) + ', ' + RACE_CHARS[n].name + ', выплата ' + mult);
    const scroll = document.createElement('div');
    scroll.className = 'race-scroll';
    const label = document.createElement('div');
    label.className = 'race-label';
    const name = document.createElement('b');
    name.textContent = RACE_CHARS[n].name;
    const m = document.createElement('span');
    m.textContent = mult;
    label.append(name, m);
    const gate = document.createElement('div');
    gate.className = 'race-gate';
    const finish = document.createElement('div');
    finish.className = 'race-finish';
    scroll.append(label, gate, finish);
    const pig = document.createElement('div');
    pig.className = 'race-pig' + (n === rc.lanes - 1 ? ' sniff' : '');
    pig.dataset.char = String(n);
    pig.innerHTML = '<div class="race-body">' + RACE_PIG_SVG + '<span class="race-num"></span><span class="race-tag" hidden><span class="avatar"></span>Вы</span></div><span class="race-ring"></span>';
    pig.querySelector('.race-num').textContent = String(n + 1);
    pig.querySelector('.race-tag .avatar').textContent = initial;
    const dust = document.createElement('div');
    dust.className = 'race-dust';
    lane.append(scroll, pig, dust);
    lane.addEventListener('click', () => pickRaceLane(n));
    lane.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickRaceLane(n); } });
    raceEls.track.appendChild(lane);
    rc.pigs.push({ el: pig, lane, scroll, finish, gate, dust, tag: pig.querySelector('.race-tag') });
  }
  resetRaceTrack();
}

// Старт: свинки у ворот, камера в нуле
function resetRaceTrack() {
  cancelRaceAnims();
  const layout = raceLayout();
  placeRaceWorld(layout);
  const st = raceFrameState(new Array(rc.lanes).fill(0), layout);
  rc.pigs.forEach((p, j) => {
    p.el.style.transform = 'translate3d(' + Math.round(st.xs[j]) + 'px,0,0)';
    p.scroll.style.transform = 'translate3d(0,0,0)';
    p.el.classList.remove('winner', 'done');
    p.gate.classList.remove('open');
    p.lane.classList.remove('win');
  });
  raceEls.field.classList.remove('running', 'finished');
}

function cancelRaceAnims() {
  rc.anims.forEach((a) => { try { a.cancel(); } catch (e) { /* уже снята */ } });
  rc.anims = [];
  rc.skip = null;
}

function resetRaceResult() {
  rc.result = null;
  raceEls.result.hidden = true;
  if (rc.played) { rc.played = false; resetRaceTrack(); }
}

function renderRace() {
  raceChipBar.render();     // номиналы фишек по серверному балансу и подсветка нажатой
  raceChipBar.sync();
  raceEls.laneButtons.forEach((btn) => {
    btn.setAttribute('aria-pressed', String(Number(btn.dataset.raceLanes) === rc.lanes));
    btn.disabled = rc.busy;
  });
  raceEls.fast.setAttribute('aria-pressed', String(rc.fast));
  raceEls.fast.disabled = rc.busy;
  rc.pigs.forEach((p, j) => {
    const on = j === rc.lane;
    p.lane.setAttribute('aria-pressed', String(on));
    p.tag.hidden = !on;
    p.el.classList.toggle('mine', on);
  });
  raceEls.field.classList.toggle('busy', rc.busy);
  raceEls.play.disabled = rc.busy || rc.lane === null;
  const betNum = Number(raceEls.bet.value);
  const betText = Number.isInteger(betNum) && betNum > 0 ? ' · ' + formatCompact(betNum) : '';
  raceEls.play.textContent = rc.lane === null ? 'Выберите дорожку' : (rc.played ? 'Ещё раз' : 'Старт') + betText;
  raceEls.balance.textContent = srv.loaded ? spacedNumber(availableBalance()) : (srv.error ? '—' : 'Загрузка…');
  raceEls.balance.classList.toggle('skeleton', !srv.loaded && !srv.error);
}

function setRaceLanes(n) {
  if (rc.busy || rc.lanes === n) return;
  rc.lanes = n;
  if (rc.lane !== null && rc.lane >= n) rc.lane = null;
  rc.played = false;
  resetRaceResult();
  buildRaceTrack();
  renderRace();
}

function pickRaceLane(n) {
  if (rc.running) {            // во время забега тап по дорожке показывает финиш (результат уже известен серверу)
    if (rc.skip) rc.skip();
    return;
  }
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
const syncRaceChips = () => { raceChipBar.sync(); renderRace(); };

// ---------- забег ----------
// Время забега: кадры сценария идут с равным шагом, на последних 8% пути у победителя замедление (фотофиниш)
function raceTimeline(script, winner, frameMs) {
  const last = script.length - 1;
  let sPh = last;                                   // кадр, где победитель проходит 92% пути
  for (let i = 0; i <= last; i++) { if (script[i][winner] >= RACE_FINISH * 0.92) { sPh = i; break; } }
  if (sPh > 0 && script[sPh][winner] > script[sPh - 1][winner]) {
    sPh = sPh - 1 + (RACE_FINISH * 0.92 - script[sPh - 1][winner]) / (script[sPh][winner] - script[sPh - 1][winner]);
  }
  const slowSpan = last - sPh;                      // длина замедляемого куска в кадрах
  const slowReal = Math.min(600, Math.max(400, (slowSpan * frameMs) / 0.4));
  const fastReal = sPh * frameMs;
  const total = fastReal + slowReal;
  // реальное время -> время сценария (в кадрах)
  const toScript = (t) => (t <= fastReal ? t / frameMs : sPh + ((t - fastReal) / slowReal) * slowSpan);
  return { total, toScript, sPh, fastReal, slowReal };
}

function raceAt(script, s) {
  const i = Math.min(script.length - 2, Math.max(0, Math.floor(s)));
  const k = Math.min(1, Math.max(0, s - i));
  return script[i].map((v, j) => v + (script[i + 1][j] - v) * k);
}

// Кто лидирует по кадрам: моменты смены лидера (в кадрах сценария) для вибрации
function raceLeadChanges(script) {
  const out = [];
  let prev = -1;
  script.forEach((f, i) => {
    let lead = 0;
    f.forEach((v, j) => { if (v > f[lead]) lead = j; });
    if (prev !== -1 && lead !== prev && f[lead] > 0) out.push(i);
    prev = lead;
  });
  return out.slice(0, RACE_MAX_LEAD_BUZZ);
}

function raceSleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function raceCountdown() {
  raceEls.lights.classList.add('on');
  const bulbs = Array.from(raceEls.lights.children);
  bulbs.forEach((b) => b.classList.remove('off'));
  for (let i = 0; i < bulbs.length; i++) {
    await raceSleep(RACE_COUNTDOWN_MS);
    bulbs[i].classList.add('off');
  }
  await raceSleep(160);
  raceEls.lights.classList.remove('on');
  raceEls.go.classList.remove('show');
  void raceEls.go.offsetWidth;
  raceEls.go.classList.add('show');
  haptic('medium');
}

// Всплески пыли (по одному облачку на дорожку) и искры победителя (до 8): только transform и opacity
function raceDust(p, x) {
  if (!p.dust.animate) return;
  p.dust.style.left = Math.max(0, Math.round(x - 6)) + 'px';
  p.dust.style.bottom = '4px';
  p.dust.animate([{ opacity: 0.55, transform: 'translate(0,0) scale(0.6)' }, { opacity: 0, transform: 'translate(-16px,-5px) scale(1.7)' }], { duration: 420, easing: 'ease-out' });
}

function raceSparks(p, x) {
  if (!p.lane.animate) return;
  for (let i = 0; i < 8; i++) {
    const sp = document.createElement('div');
    sp.className = 'race-spark';
    sp.style.left = Math.round(x + RACE_PIG_W * 0.5) + 'px';
    sp.style.bottom = '22px';
    p.lane.appendChild(sp);
    const ang = (Math.PI * 2 * i) / 8;
    const a = sp.animate([{ opacity: 1, transform: 'translate(0,0) scale(1)' }, { opacity: 0, transform: 'translate(' + Math.round(Math.cos(ang) * 26) + 'px,' + Math.round(Math.sin(ang) * 18 - 8) + 'px) scale(0.4)' }], { duration: 650, easing: 'ease-out' });
    a.onfinish = () => sp.remove();
  }
}

// Сам забег: возвращает Promise, который завершается, когда все свинки у финиша (или забег пропущен тапом)
function runRace(script, winner, fast) {
  const layout = raceLayout();
  placeRaceWorld(layout);
  const frameMs = fast ? RACE_FRAME_MS_FAST : RACE_FRAME_MS;
  const tl = raceTimeline(script, winner, frameMs);
  const n = Math.ceil(tl.total / RACE_SAMPLE_MS);
  const pigFrames = rc.pigs.map(() => []);
  const camFrames = [];
  for (let i = 0; i <= n; i++) {
    const t = Math.min(tl.total, i * RACE_SAMPLE_MS);
    const st = raceFrameState(raceAt(script, tl.toScript(t)), layout);
    const off = Math.min(1, t / tl.total);
    rc.pigs.forEach((_, j) => pigFrames[j].push({ offset: off, transform: 'translate3d(' + st.xs[j].toFixed(1) + 'px,0,0)' }));
    camFrames.push({ offset: off, transform: 'translate3d(' + (-st.cam * layout.k).toFixed(1) + 'px,0,0)' });
  }
  const finalState = raceFrameState(script[script.length - 1], layout);
  const opts = { duration: tl.total, easing: 'linear', fill: 'forwards' };
  rc.anims = [];
  rc.pigs.forEach((p, j) => {
    rc.anims.push(p.el.animate(pigFrames[j], opts));
    if (!layout.lite) rc.anims.push(p.scroll.animate(camFrames, opts));
    p.gate.classList.add('open');
  });
  raceEls.field.classList.add('running');
  rc.running = true;
  const master = rc.anims[0];
  // события по общему времени: смена лидера (вибрация), стиль бега, пыль
  const leadAt = raceLeadChanges(script).map((f) => (f <= tl.sPh ? f * frameMs : tl.fastReal + ((f - tl.sPh) / Math.max(1e-6, script.length - 1 - tl.sPh)) * tl.slowReal));
  let leadIdx = 0;
  let nextDust = 0;
  let phase = 0;
  return new Promise((resolve) => {
    let done = false;
    let rafId = 0;
    const finish = () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(rafId);
      document.removeEventListener('visibilitychange', onVis);
      rc.skip = null;
      rc.running = false;
      // конечное положение: инлайн-стили и снятие анимаций (fill: forwards иначе перекрывает инлайн)
      rc.pigs.forEach((p, j) => {
        p.el.style.transform = 'translate3d(' + finalState.xs[j].toFixed(1) + 'px,0,0)';
        p.scroll.style.transform = 'translate3d(' + (-finalState.cam * layout.k).toFixed(1) + 'px,0,0)';
      });
      cancelRaceAnims();
      raceEls.field.classList.remove('running');
      resolve({ layout, finalState });
    };
    const onVis = () => {
      rc.anims.forEach((a) => { try { if (document.hidden) a.pause(); else a.play(); } catch (e) { /* не страшно */ } });
    };
    document.addEventListener('visibilitychange', onVis);
    rc.skip = finish;
    master.onfinish = finish;
    const tick = () => {
      if (done) return;
      const t = Number(master.currentTime) || 0;
      while (leadIdx < leadAt.length && t >= leadAt[leadIdx]) { haptic('selection'); leadIdx++; }
      const p3 = t / tl.total;
      const want = p3 < 0.34 ? 0 : p3 < 0.67 ? 1 : 2;
      if (want !== phase) {          // манера бега: рывок со старта, финишный бросок
        phase = want;
        rc.pigs.forEach((p) => {
          const style = RACE_CHARS[Number(p.el.dataset.char)].style;
          const base = { sprint: [0.24, 0.32, 0.4], steady: [0.34, 0.34, 0.34], finish: [0.46, 0.38, 0.24], lazy: [0.52, 0.5, 0.46] }[style];
          p.el.style.setProperty('--stride', base[phase] + 's');
        });
      }
      if (!layout.lite && t >= nextDust) {
        nextDust = t + 260;
        const st = raceFrameState(raceAt(script, tl.toScript(t)), layout);
        rc.pigs.forEach((p, j) => raceDust(p, st.xs[j]));
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    if (reducedMotion()) finish();
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

function showRaceResult(r) {
  fillResult(raceEls.result, r);
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
  rc.played = true;
  resetRaceResult();
  rc.played = false;
  rc.busy = true;
  renderRace();
  haptic('medium');
  const result = await postWithRetries(() => postRaceOnce({ request_id: id, lanes, lane, bet }, lanes, lane));
  let note = 'Баланс обновлён';
  if (result && result.kind === 'ok') {
    const d = result.data;
    try {
      resetRaceTrack();
      if (!rc.fast && !reducedMotion()) await raceCountdown();
      const end = await runRace(d.script, d.winner, rc.fast);
      const winnerPig = rc.pigs[d.winner];
      winnerPig.lane.classList.add('win');
      winnerPig.el.classList.add('winner');
      rc.pigs.forEach((p) => p.el.classList.add('done'));
      raceEls.field.classList.add('finished');
      winnerPig.finish.classList.remove('flash');
      void winnerPig.finish.offsetWidth;
      winnerPig.finish.classList.add('flash');
      if (!end.layout.lite) raceSparks(winnerPig, end.finalState.xs[d.winner]);
      rc.result = { lane, winner: d.winner, payout: d.payout, bet };
      rc.played = true;
      const won = d.payout > 0;
      showRaceResult(won
        ? { kind: 'win', title: 'Выигрыш', amount: d.payout - bet, sign: '+', note: 'Победила №' + (d.winner + 1) + ' ' + RACE_CHARS[d.winner].name }
        : { kind: 'lose', title: 'Проигрыш', amount: bet, sign: '−', note: 'Победила №' + (d.winner + 1) + ' ' + RACE_CHARS[d.winner].name });
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
      cancelRaceAnims();
      rc.running = false;
      raceEls.field.classList.remove('running');
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
raceEls.fast.addEventListener('click', () => {
  if (rc.busy) return;
  rc.fast = !rc.fast;
  try { localStorage.setItem('race_fast', rc.fast ? '1' : '0'); } catch (e) { /* не страшно */ }
  renderRace();
});
raceEls.play.addEventListener('click', () => raceStart());
// поле ставки, ÷2, ×2, «Макс» и панель над клавиатурой: общая для всех игр логика (js/02-bet.js); фишки-пресеты ставят сумму тапом
setupBetPanel({
  input: raceEls.bet,
  maxBtn: raceEls.maxBtn,
  halfBtn: raceEls.half,
  doubleBtn: raceEls.double,
  getLimit: raceBetLimit
});
raceEls.bet.addEventListener('input', syncRaceChips);
[raceEls.half, raceEls.double, raceEls.maxBtn].forEach((b) => b.addEventListener('click', () => { if (!rc.busy) syncRaceChips(); }));
raceEls.panel.querySelectorAll('[data-rbet]').forEach((b) => b.addEventListener('click', () => {
  if (rc.busy) return;
  raceEls.bet.value = b.dataset.rbet;
  syncRaceChips();
  refreshBetPanels();
  haptic('light');
}));
raceEls.switchBtn.addEventListener('click', () => toggleGameMenu());   // функция из js/14-lobby.js

buildRaceTrack();
renderRaceChips();
renderRace();
// #endregion
