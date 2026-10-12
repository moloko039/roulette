// #region Живая карточка краша на главной (UI_REVIEW, лобби)
// Краш крупной карточкой во всю ширину с мини-графиком раунда и подписью «сейчас играют N». Данные те же, что у экрана краша
// (GET /api/crash/live, общий раунд), опрос раз в 1,5 с только пока открыт титульный экран и приложение видно; график рисуется
// не чаще двух раз в секунду. Ничего не решает: только показывает то, что прислал сервер.
const LCR_POLL_MS = 1500;
const LCR_DRAW_MS = 500;
const lcr = { card: null, svg: null, curve: null, cap: null, state: null, token: '', syncMs: 0, syncAt: 0, pollTimer: 0, drawTimer: 0, polling: false };

(() => {
  const card = lobbyEls.grid.querySelector('.lobby-card[data-game="crash"]');
  if (!card) return;
  lcr.card = card;
  card.classList.add('wide');
  const live = document.createElement('span');
  live.className = 'lobby-live';
  live.setAttribute('aria-hidden', 'true');
  live.innerHTML = '<svg viewBox="0 0 100 40" preserveAspectRatio="none"><path class="lobby-live-line" d="M0 39"/></svg><span class="lobby-live-cap"></span>';
  card.appendChild(live);
  lcr.svg = live.querySelector('svg');
  lcr.curve = live.querySelector('path');
  lcr.cap = live.querySelector('.lobby-live-cap');
})();

const lcrOnScreen = () => !!lcr.card && activeTab === 'play' && currentGame === 'lobby' && document.visibilityState === 'visible';
const lcrNow = () => lcr.syncMs + (performance.now() - lcr.syncAt);

function lcrDraw() {
  if (!lcr.card || !lcrOnScreen()) return;
  const r = lcr.state && lcr.state.round;
  let d = 'M0 39';
  let text = '';
  let tone = '';
  if (r) {
    const bets = lcr.state.bets ? lcr.state.bets.length : 0;
    const playing = bets > 0 ? ' · играют ' + bets : '';
    const phase = r.phase === 'betting' && lcrNow() >= r.flight_start_ms ? 'flight' : r.phase;
    if (phase === 'flight') {
      const elapsed = Math.max(0, lcrNow() - r.flight_start_ms);
      const m = Math.pow(2, elapsed / CR_DOUBLING_MS);
      const xmax = Math.max(8000, elapsed * 1.1);
      const ymax = Math.max(2, m * 1.15);
      const pts = [];
      for (let i = 0; i <= 20; i++) {
        const t = (elapsed * i) / 20;
        pts.push((i ? 'L' : 'M') + ((t / xmax) * 100).toFixed(1) + ' ' + (39 - ((Math.pow(2, t / CR_DOUBLING_MS) - 1) / (ymax - 1)) * 38).toFixed(1));
      }
      d = pts.join('');
      const x100 = crM100(elapsed);
      text = crText(x100) + playing;
      tone = x100 >= 1000 ? 'z3' : x100 >= 200 ? 'z2' : '';
    } else if (phase === 'betting') {
      text = 'Приём ставок: ' + Math.max(0, Math.ceil((r.flight_start_ms - lcrNow()) / 1000)) + ' с' + playing;
      d = 'M0 39H100';
    } else if (r.result) {
      text = 'Крах ' + crText(r.result.crash_x100);
      tone = 'crash';
    }
  } else {
    text = 'Раунд общий';
  }
  lcr.curve.setAttribute('d', d);
  lcr.cap.textContent = text;
  lcr.card.dataset.tone = tone;
}

async function lcrPoll() {
  lcr.pollTimer = 0;
  if (!lcrOnScreen()) return;
  if (!lcr.polling && started && tg && tg.initData) {
    lcr.polling = true;
    try {
      const q = '?peek=1' + (lcr.token ? '&v=' + encodeURIComponent(lcr.token) : '');       // peek: раунд не открывается ради карточки
      const d = await fetchGameState('/api/crash/live' + q, validCrLive);
      lcr.syncMs = d.server_ms;
      lcr.syncAt = performance.now();
      if (d.unchanged === true) {
        if (lcr.state && lcr.state.round && ['betting', 'flight', 'result', 'idle'].includes(d.phase)) lcr.state.round.phase = d.phase;
      } else {
        lcr.state = d;
        lcr.token = typeof d.v === 'string' ? d.v : '';
      }
    } catch (e) {
      // карточка остаётся прежней: игра от неё не зависит
    } finally {
      lcr.polling = false;
    }
  }
  if (lcrOnScreen()) lcr.pollTimer = setTimeout(lcrPoll, LCR_POLL_MS);
}

function lobbyCrashStart() {
  if (!lcr.card) return;
  lobbyCrashStop();
  lcr.token = '';
  lcr.pollTimer = setTimeout(lcrPoll, 0);
  lcr.drawTimer = setInterval(lcrDraw, LCR_DRAW_MS);
}

function lobbyCrashStop() {
  clearTimeout(lcr.pollTimer);
  clearInterval(lcr.drawTimer);
  lcr.pollTimer = 0;
  lcr.drawTimer = 0;
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') lobbyCrashStop();
  else if (lcrOnScreen()) lobbyCrashStart();
});
if (lcrOnScreen()) lobbyCrashStart();            // титульный экран открыт с самого запуска
// #endregion
