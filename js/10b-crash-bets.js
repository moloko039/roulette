// #region Краш: ставки и автовывод
// ---------- ставки и автовывод ----------
const CR_BET_MAX = 1000000000;
const CR_TARGET_MIN = 101;
const CR_TARGET_MAX = 100000;    // ×1000.00, как CAP_X100 на сервере

const crBetLimit = () => Math.max(1, Math.min(CR_BET_MAX, cr.balance === null ? CR_BET_MAX : cr.balance));

// Цель из поля: пусто = вручную (null); число 1.01..1000 с двумя знаками; иначе undefined (ошибка)
function crParseTarget() {
  const raw = crEls.target.value.trim().replace(',', '.');
  if (raw === '') return null;
  if (!/^\d{1,4}(\.\d{0,2})?$/.test(raw)) return undefined;
  const x = Math.round(parseFloat(raw) * 100);
  return x >= CR_TARGET_MIN && x <= CR_TARGET_MAX ? x : undefined;
}

const validCrBetDone = (d) => !!d && isCount(d.round_id) && isCount(d.bet) && isCount(d.balance) && typeof d.replayed === 'boolean';
const validCrCashDone = (d) => !!d && isCount(d.round_id) && isCount(d.cashed_x100) && isCount(d.payout) && isCount(d.balance) && typeof d.replayed === 'boolean';

// Фишки ставки по серверному балансу (те же номиналы, что в других играх)
const crChipBar = makeChipBar({
  root: crEls.bets,
  attr: 'cbet',
  getBalance: () => cr.balance === null ? 0 : cr.balance,
  getBet: () => Number(crEls.bet.value),
  setBet: (v) => { crEls.bet.value = String(v); }
});
const renderCrChips = crChipBar.render;

function syncCrChips() {
  crChipBar.sync();
  const t = crParseTarget();
  crEls.bets.querySelectorAll('[data-ctarget]').forEach((b) => {
    b.setAttribute('aria-pressed', String(typeof t === 'number' && t === Math.round(parseFloat(b.dataset.ctarget) * 100)));
  });
}

function crBet() {
  const bet = Number(crEls.bet.value);
  const target = crParseTarget();
  if (!Number.isSafeInteger(bet) || bet < 1 || bet > CR_BET_MAX) { setCrNotice('Введите целую ставку от 1 до ' + formatNumber(CR_BET_MAX)); return; }
  if (target === undefined) { setCrNotice('Авто-вывод: от 1.01 до 1000 (или пусто для ручного режима)'); return; }
  if (cr.balance !== null && bet > cr.balance) { setCrNotice('Не хватает фишек'); return; }
  const body = target === null ? { bet } : { bet, target_x100: target };
  crAct('/api/crash/live/bet', body, validCrBetDone, (d) => {
    cr.balance = d.balance;
    skinEvents.emit('crash:bet', { roundId: d.round_id });
    skinEvents.emit('bet:placed', { game: 'crash' });
    haptic('light');
  }, {
    betting_closed: 'Приём ставок закрыт: дождитесь следующего раунда',
    already_bet: 'Вы уже поставили в этом раунде',
    room_full: 'В ленте слишком много ставок: повторите в следующем раунде',
    insufficient_funds: 'Не хватает фишек',
    balance_limit: 'Баланс слишком велик для такой ставки'
  });
}

function crCash() {
  const me = crMe();
  if (!me || me.status !== 'open' || cr.busy) return;
  crAct('/api/crash/live/cashout', {}, validCrCashDone, (d) => {
    cr.balance = d.balance;
    haptic('success');
  }, {
    too_early: 'Слишком рано: вывод возможен с ×1.01',
    round_over: 'Раунд уже закончился',
    no_bet: 'У вас нет ставки в этом раунде'
  });
}

setupBetPanel({
  input: crEls.bet,
  maxBtn: crEls.maxBtn,
  halfBtn: document.getElementById('cr-half'),
  doubleBtn: document.getElementById('cr-double-bet'),
  getLimit: crBetLimit
});
crEls.bet.addEventListener('input', syncCrChips);
crEls.bets.querySelectorAll('[data-cbet]').forEach((b) => b.addEventListener('click', () => {
  crEls.bet.value = b.dataset.cbet;
  syncCrChips();
  refreshBetPanels();
  haptic('light');
}));
// поле цели: цифры и один разделитель, панель над клавиатурой как у ставки, «Готово» вместо «Макс» на время ввода
crEls.target.addEventListener('input', () => {
  let v = crEls.target.value.replace(',', '.').replace(/[^\d.]/g, '');
  const dot = v.indexOf('.');
  if (dot >= 0) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2);
  crEls.target.value = v.slice(0, 7);
  syncCrChips();
});
crEls.target.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); crEls.target.blur(); } });
crEls.target.addEventListener('focus', () => {
  crEls.maxBtn.dataset.mode = 'done';
  crEls.maxBtn.textContent = 'Готово';
  dockPanel(crEls.bets);
});
crEls.target.addEventListener('blur', () => {
  crEls.maxBtn.dataset.mode = 'max';
  crEls.maxBtn.textContent = 'Макс';
  undockPanel(crEls.bets);
  syncCrChips();
});
crEls.maxBtn.addEventListener('click', () => { if (document.activeElement === crEls.target) crEls.target.blur(); });
crEls.bets.querySelectorAll('[data-ctarget]').forEach((b) => b.addEventListener('click', () => {
  const same = crParseTarget() === Math.round(parseFloat(b.dataset.ctarget) * 100);
  crEls.target.value = same ? '' : b.dataset.ctarget;   // повторное нажатие возвращает ручной режим
  syncCrChips();
  haptic('light');
}));
crEls.start.addEventListener('click', crBet);
crEls.cash.addEventListener('click', crCash);

// #endregion
