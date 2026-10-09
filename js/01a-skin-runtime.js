// #region Скины: среда исполнения сцен (DESIGN.md, разделы 1 и 1а)
// Скин игры это палитра (CSS-переменные в css/01-skins.css) и необязательная «сцена»: модуль skins/<код>.js (+ .css), который рисует декор и реагирует на события игры.
// Сцена только рисует: она ничего не считает и не знает исхода раньше сервера. Правила бюджета (iPhone убивает страницу по памяти, поэтому главный ресурс память):
//  1. сцена существует только у активной игры: её DOM создаётся, когда экран игры открыт и надет скин со сценой, и удаляется при уходе или снятии скина;
//  2. модуль скина (js и css) загружается при надевании (<meta name="skin-js|skin-css" data-code="..."> в index.html, версия ?v= ставится scripts/stamp_client.py),
//     css снимается вместе со сценой; в index.html сцены не встроены;
//  3. пауза: пока приложение не видно (visibilitychange, события Telegram activated и deactivated), на корне класс skin-paused: анимации стоят;
//  4. облегчённый режим perf-lite: если частота кадров держится ниже SKIN_FPS_MIN дольше SKIN_FPS_LOW_MS, на корень ставится perf-lite до конца сессии (без возврата):
//     движения покоя и частицы выключены, события заменяются сменой кадра, параллакс упрощается до смены неподвижных фонов; игрок ничего не видит в уведомлении;
//  5. prefers-reduced-motion действует так же, как perf-lite (без класса).
// События игр идут через skinEvents: игра вызывает emit, сцена подписывается через rt.on и отписывается при удалении.
const SKIN_FPS_MIN = 40;
const SKIN_FPS_LOW_MS = 3000;
const SKIN_FPS_WINDOW_MS = 1000;

const skinEvents = (() => {
  const map = new Map();
  return {
    on(name, fn) {
      let set = map.get(name);
      if (!set) map.set(name, (set = new Set()));
      set.add(fn);
      return () => set.delete(fn);
    },
    has(name) {
      const set = map.get(name);
      return !!set && set.size > 0;
    },
    emit(name, detail) {
      const set = map.get(name);
      if (!set) return;
      set.forEach((fn) => {
        try { fn(detail); } catch (e) { /* ошибка сцены не должна ломать игру */ }
      });
    }
  };
})();

const skinRt = { paused: false, lite: false };
const skinStill = () => skinRt.lite || reducedMotion();     // без движения: частиц и полётов нет, события заменяются сменой кадра

function skinSetPaused(value) {
  value = !!value;
  if (skinRt.paused === value) return;
  skinRt.paused = value;
  document.documentElement.classList.toggle('skin-paused', value);
  skinEvents.emit('skin:paused', value);
}

function skinSetLite() {
  if (skinRt.lite) return;
  skinRt.lite = true;
  document.documentElement.classList.add('perf-lite');
  skinEvents.emit('skin:lite', true);
}

document.addEventListener('visibilitychange', () => skinSetPaused(document.hidden));
if (tg && typeof tg.onEvent === 'function') {       // сворачивание мини-приложения (Bot API 8.0+); на старых клиентах остаётся visibilitychange
  try {
    tg.onEvent('deactivated', () => skinSetPaused(true));
    tg.onEvent('activated', () => skinSetPaused(document.hidden));
  } catch (e) { /* события не поддерживаются */ }
}

// ----- монитор частоты кадров: работает только пока есть смонтированная сцена -----
const skinFps = { raf: 0, frames: 0, start: 0, lowSince: 0 };

function skinFpsTick(now) {
  skinFps.raf = requestAnimationFrame(skinFpsTick);
  if (skinRt.paused || document.hidden) { skinFps.frames = 0; skinFps.start = 0; skinFps.lowSince = 0; return; }
  if (!skinFps.start) { skinFps.start = now; skinFps.frames = 0; return; }
  skinFps.frames += 1;
  const span = now - skinFps.start;
  if (span < SKIN_FPS_WINDOW_MS) return;
  const fps = (skinFps.frames * 1000) / span;
  skinFps.start = now;
  skinFps.frames = 0;
  if (fps >= SKIN_FPS_MIN) { skinFps.lowSince = 0; return; }
  if (!skinFps.lowSince) skinFps.lowSince = now;
  else if (now - skinFps.lowSince >= SKIN_FPS_LOW_MS) { skinSetLite(); skinFpsStop(); }
}

function skinFpsStart() {
  if (skinFps.raf || skinRt.lite) return;
  skinFps.start = 0; skinFps.frames = 0; skinFps.lowSince = 0;
  skinFps.raf = requestAnimationFrame(skinFpsTick);
}

function skinFpsStop() {
  if (skinFps.raf) cancelAnimationFrame(skinFps.raf);
  skinFps.raf = 0;
}

// ----- модули и сцены -----
const SKIN_FILES = {};      // код скина -> {js, css} из <meta name="skin-js|skin-css">
document.querySelectorAll('meta[name="skin-js"], meta[name="skin-css"]').forEach((m) => {
  const code = m.getAttribute('data-code');
  if (!code || !SKIN_CODE_RE.test(code)) return;
  (SKIN_FILES[code] = SKIN_FILES[code] || {})[m.getAttribute('name') === 'skin-js' ? 'js' : 'css'] = m.getAttribute('content');
});

const SKIN_CSS_ONLY = { mount: () => null };      // скин без модуля js: сцена пустая, css подключается при надевании и снимается вместе с контейнером
const skinScenes = {};      // код -> описание сцены, когда модуль загружен
const skinHosts = {};       // слот -> функция, отдающая контейнер сцены (регистрирует игра)
const skinActive = {};      // слот -> экран игры открыт
const skinMounted = {};     // слот -> {code, root, inst, offs, link}
const skinLoading = {};     // код -> 'loading' | 'failed'

function registerSkinScene(code, def) {
  skinScenes[code] = def;
  delete skinLoading[code];
  skinSync(def.slot);
}

const skinSetHost = (slot, getHost) => { skinHosts[slot] = getHost; };
const skinSetActive = (slot, on) => { skinActive[slot] = !!on; skinSync(slot); };
const skinCode = (slot) => document.documentElement.getAttribute('data-skin-' + slot);

function skinLoad(code) {
  const files = SKIN_FILES[code];
  if (!files || !files.js || skinLoading[code]) return;
  skinLoading[code] = 'loading';
  const script = document.createElement('script');
  script.src = files.js;
  script.onerror = () => { skinLoading[code] = 'failed'; script.remove(); };      // без модуля остаётся палитра скина
  document.head.appendChild(script);
}

function skinUnmount(slot) {
  const cur = skinMounted[slot];
  if (!cur) return;
  delete skinMounted[slot];
  try { if (cur.inst && cur.inst.destroy) cur.inst.destroy(); } catch (e) { /* уборка не должна падать */ }
  cur.offs.forEach((off) => off());
  cur.root.remove();
  if (cur.link) cur.link.remove();
  if (cur.host) delete cur.host.dataset.scene;
  if (!Object.keys(skinMounted).length) skinFpsStop();
}

// Приводит сцену слота к желаемому: экран игры открыт и надет скин со сценой. Вызывается при смене скина, открытии и закрытии экрана, загрузке модуля.
function skinSync(slot) {
  const want = skinActive[slot] ? skinCode(slot) : null;
  const cur = skinMounted[slot];
  if (cur && cur.code !== want) skinUnmount(slot);
  if (!want || skinMounted[slot]) return;
  const files = SKIN_FILES[want] || {};
  const def = skinScenes[want] || (files.css && !files.js ? SKIN_CSS_ONLY : null);     // скин без js: только css, привязанный к меткам состояния игры
  if (!def) { skinLoad(want); return; }
  const host = skinHosts[slot] && skinHosts[slot]();
  if (!host) return;
  let link = null;
  if (files.css) {
    link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = files.css;
    document.head.appendChild(link);
  }
  const root = document.createElement('div');
  root.className = 'skin-scene';
  root.setAttribute('aria-hidden', 'true');
  host.insertBefore(root, host.firstChild);
  host.dataset.scene = want;
  const offs = [];
  const rt = { root, host, still: skinStill, on: (name, fn) => { const off = skinEvents.on(name, fn); offs.push(off); return off; } };
  skinMounted[slot] = { code: want, root, inst: null, offs, link, host };
  try {
    skinMounted[slot].inst = def.mount(rt) || null;
  } catch (e) {
    skinUnmount(slot);        // сломанная сцена не остаётся в DOM: игра идёт с палитрой скина
    return;
  }
  skinFpsStart();
}

// Какие слоты скинов живут на каких экранах (data-screen): сцена слота существует, только пока открыт один из его экранов. Хосты (контейнеры сцен) регистрируют игры (skinSetHost).
const SKIN_SCREEN_SLOTS = {
  crash: ['crash', 'chip'], mines: ['mine_icons', 'chip'], keno: ['keno_ball', 'chip'], blackjack: ['card_back', 'chip'], hilo: ['card_back', 'chip'], roulette: ['table', 'chip']
};
// Фишки встречаются на всех игровых экранах (кнопки панели ставки, стопки на столе рулетки): контейнер сцены слота chip это body (правила паузы и perf-lite базового css его не трогают, скин сам гасит свои анимации)
skinSetHost('chip', () => document.body);

function skinScreenChanged(screen) {
  const wanted = new Set(SKIN_SCREEN_SLOTS[screen] || []);
  SKIN_SLOTS.forEach((slot) => skinSetActive(slot, wanted.has(slot)));
}

// #endregion
