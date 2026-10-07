// #region Ввод суммы и фишки
// ---------- динамические фишки и ввод суммы ----------
// Фишки считаются от серверного баланса: T = наибольшая степень 10, не больше баланса, но не меньше 100;
// номиналы T/10, T/2, T, 5T (баланс 305: 10/50/100/500; 25 000: 1000/5000/10 000/50 000).
const CHIP_BASE = 100;
function chipSet(balance) {
  const b = Number.isSafeInteger(balance) && balance > 0 ? balance : 0;
  let t = CHIP_BASE;
  while (t * 10 <= b) t *= 10;
  return [t / 10, t / 2, t, 5 * t];
}

const CHIP_UNITS = [[1e15, 'Q'], [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
function chipLabel(n) {
  for (const [unit, name] of CHIP_UNITS) {
    if (n >= unit) return String(n / unit) + name;   // номиналы вида 10^k и 5 * 10^k делятся нацело
  }
  return String(n);
}

function setChipText(btn, n) {
  const label = chipLabel(n);
  btn.textContent = label;
  btn.dataset.len = String(label.length);
  btn.title = formatNumber(n);
  btn.setAttribute('aria-label', formatNumber(n));
}

const nearestChip = (values, v) => values.reduce((best, x) => (Math.abs(x - v) < Math.abs(best - v) ? x : best), values[0]);

// Число из поля: пусто или нечисло = минимальная ставка 1; не больше безопасного целого
function parseBet(input) {
  const n = Number(input.value);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(Math.floor(n), Number.MAX_SAFE_INTEGER);
}

function setBetValue(input, n) {
  input.value = String(n);
  input.dispatchEvent(new Event('input', { bubbles: true })); // поле остаётся валидным: слушатели видят обычный ввод
}

const halfBet = (input) => setBetValue(input, Math.max(1, Math.floor(parseBet(input) / 2)));
const doubleBet = (input, max) => setBetValue(input, Math.max(1, Math.min(parseBet(input) * 2, max))); // не выше предела

// Предел ставки одинаков в обеих играх: не больше баланса и максимума ставки игры.
// Рулетка: поле суммы это размер ставки на один клик по клетке, предел = доступный баланс (серверный минус ставки
// на столе), как проверяет placeBet. Мины: min(баланс, 10**9).
const rouletteBetLimit = () => (srv.loaded ? Math.max(1, availableBalance()) : Number.MAX_SAFE_INTEGER);
const minesBetLimit = () => Math.max(1, Math.min(MINES_BET_MAX, mn.balance === null ? MINES_BET_MAX : mn.balance));

const betPanels = [];
const refreshBetPanels = () => betPanels.forEach((p) => p.refresh());

// Поле суммы и кнопки панели: только цифры (до 16), без ведущих нулей; число выше предела при вводе и потере
// фокуса ограничивается пределом; «Макс» ставит предел, а пока поле в фокусе та же кнопка работает как «Готово»;
// Enter тоже закрывает клавиатуру. Панель прижимается к клавиатуре (dockPanel).
function setupBetPanel({ input, maxBtn, halfBtn, doubleBtn, getLimit }) {
  const panel = input.closest('.bets-dock');
  const clampToLimit = () => {
    const limit = getLimit();
    if (input.value !== '' && Number(input.value) > limit) input.value = String(limit);
  };
  const refresh = () => {
    doubleBtn.classList.toggle('dim', input.value !== '' && parseBet(input) >= getLimit());
    doubleBtn.setAttribute('aria-disabled', String(doubleBtn.classList.contains('dim')));
  };
  input.setAttribute('inputmode', 'numeric');
  input.setAttribute('enterkeyhint', 'done');
  input.addEventListener('input', () => {
    const clean = input.value.replace(/\D/g, '').slice(0, 16).replace(/^0+(?=\d)/, '');
    if (clean !== input.value) input.value = clean;
    clampToLimit();
    refresh();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      input.blur();
    }
  });
  input.addEventListener('focus', () => {
    maxBtn.dataset.mode = 'done';
    maxBtn.textContent = 'Готово';
    dockPanel(panel);
  });
  input.addEventListener('blur', () => {
    clampToLimit();
    refresh();
    maxBtn.dataset.mode = 'max';
    maxBtn.textContent = 'Макс';
    undockPanel(panel);
  });
  maxBtn.addEventListener('pointerdown', (e) => e.preventDefault()); // фокус остаётся, пока не сработает click
  maxBtn.addEventListener('click', () => {
    if (maxBtn.dataset.mode === 'done') input.blur();
    else setBetValue(input, getLimit());
  });
  halfBtn.addEventListener('click', () => halfBet(input));
  doubleBtn.addEventListener('click', () => doubleBet(input, getLimit()));
  maxBtn.dataset.mode = 'max';
  betPanels.push({ refresh });
  refresh();
  return refresh;
}

// #endregion

// #region Панель ставок над клавиатурой и защита тапов
// ---------- панель ставок над клавиатурой ----------
// Пока поле в фокусе и клавиатура открыта, панель смещается (transform: раскладка не меняется, стол не прыгает)
// так, чтобы её низ был у верхнего края клавиатуры. Видимый низ экрана берём из visualViewport (offsetTop + height),
// запасной вариант: Telegram.WebApp.viewportHeight. Клавиатура считается открытой, если низ оболочки заметно ниже видимого.
let dockedPanel = null;
let dockFrame = 0;

function visibleBottom() {
  const vv = window.visualViewport;
  if (vv) return vv.offsetTop + vv.height;
  if (tg && typeof tg.viewportHeight === 'number' && tg.viewportHeight > 0) return tg.viewportHeight;
  return window.innerHeight;
}

function positionDock() {
  const p = dockedPanel;
  if (!p) return;
  p.style.transform = '';
  const shellBottom = document.querySelector('.shell').getBoundingClientRect().bottom;
  const bottom = visibleBottom();
  if (shellBottom - bottom <= 80) {          // клавиатуры нет
    p.classList.remove('docked');
    return;
  }
  const dy = Math.round(bottom - p.getBoundingClientRect().bottom - 4);
  p.classList.add('docked');
  if (dy) p.style.transform = `translateY(${dy}px)`;
}

function scheduleDock() {
  if (dockFrame) return;
  dockFrame = requestAnimationFrame(() => {
    dockFrame = 0;
    positionDock();
  });
}

function dockPanel(panel) {
  dockedPanel = panel;
  [0, 150, 350, 700].forEach((ms) => setTimeout(() => { if (dockedPanel === panel) scheduleDock(); }, ms)); // клавиатура выезжает не сразу
}

function undockPanel(panel) {
  if (dockedPanel !== panel) return;
  dockedPanel = null;
  panel.style.transform = '';
  panel.classList.remove('docked');
  window.scrollTo(0, 0);
}

if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', scheduleDock);
  window.visualViewport.addEventListener('scroll', scheduleDock);
}
window.addEventListener('resize', scheduleDock);
window.addEventListener('scroll', scheduleDock, true);

// Когда клавиатура уходит, видимая область и вёрстка перестраиваются (панель опускается из-под клавиатуры на место). Если
// тап по кнопке успел снять фокус с поля, то клик касанием определяется уже по новой раскладке и попадает в другой элемент
// (например, в затемнение окна, которое закрывает его). Поэтому: (1) кнопки панелей действуют сразу по нажатию, после
// закрытия клавиатуры, а следующий за нажатием клик гасится (действие ровно один раз; Enter и клавиатурный клик идут как
// обычно); (2) затемнение закрывает окно, только если нажатие и клик были именно на нём и вёрстка не менялась ~400 мс.
let lastLayoutShiftAt = 0;        // последнее изменение видимой области или потеря фокуса поля
const markLayoutShift = () => { lastLayoutShiftAt = performance.now(); };
if (window.visualViewport) window.visualViewport.addEventListener('resize', markLayoutShift);
window.addEventListener('resize', markLayoutShift);
document.addEventListener('focusout', (e) => { if (e.target && e.target.tagName === 'INPUT') markLayoutShift(); }, true);
let lastPointerDownTarget = null;
document.addEventListener('pointerdown', (e) => { lastPointerDownTarget = e.target; }, true);

const BACKDROP_QUIET_MS = 400;
function closeOnBackdropTap(backdrop, close) {
  backdrop.addEventListener('click', (e) => {
    const startedHere = lastPointerDownTarget === backdrop;
    lastPointerDownTarget = null;
    if (e.target !== backdrop || !startedHere) return;                       // нажатие началось не на затемнении (или ушло с окна)
    if (performance.now() - lastLayoutShiftAt < BACKDROP_QUIET_MS) return;   // клавиатура или вёрстка только что менялись
    close();
  });
}

// Тап вне поля и прокрутка закрывают клавиатуру; кнопка панели при открытой клавиатуре: сначала закрыть клавиатуру,
// затем выполнить действие кнопки (один раз)
(() => {
  const active = () => {
    const a = document.activeElement;
    return a && a.classList && a.classList.contains('bet-input') ? a : null;
  };
  let swallow = 0;   // до этого момента следующий клик считается хвостом уже обработанного нажатия
  document.addEventListener('pointerdown', (e) => {
    swallow = 0;
    const a = active();
    if (!a || e.target === a || !e.target.closest || e.target.closest('.bet-maxdone')) return;
    if (e.target.closest('.picker-list')) {   // строки списка: фокус остаётся (без него клавиатура не уходит и вёрстка не двигается), выбор по клику
      e.preventDefault();                      // (прокрутка списка пальцем строку не выбирает)
      return;
    }
    const btn = e.target.closest('button');
    const immediate = btn && !btn.disabled && e.isPrimary !== false && (e.button || 0) === 0 && btn.closest('.bets-dock, .transfer-panel');
    a.blur();
    if (immediate) {
      btn.click();
      swallow = performance.now() + 1000;
    }
  }, true);
  document.addEventListener('click', (e) => {
    if (!swallow) return;
    const live = performance.now() < swallow;
    swallow = 0;
    if (live) { e.stopImmediatePropagation(); e.preventDefault(); }
  }, true);
  let startY = null;
  document.addEventListener('touchstart', (e) => { startY = e.touches[0].clientY; }, { passive: true, capture: true });
  document.addEventListener('touchmove', (e) => {
    const a = active();
    if (a && startY !== null && Math.abs(e.touches[0].clientY - startY) > 8) a.blur();
  }, { passive: true, capture: true });
  document.addEventListener('wheel', () => { const a = active(); if (a) a.blur(); }, { passive: true, capture: true });
})();


// #endregion

