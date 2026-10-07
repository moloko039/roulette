// #region Серия входов («Награда дня»)
// Карточка на титульном экране: награда раз в сутки (день по московскому времени), растёт по дням недели и циклам, пропуск дня откатывает на начало недели (ECONOMY_ADDITIONS.md, п. 3).
// Данные только с сервера: GET /api/streak (что даст сбор и лента недели), POST /api/streak/claim (пустое тело; повтор в тот же день ничего не начисляет).
// Баланс фишек из ответа идёт через wdApplyBalance (файл js/13-wardrobe.js), кристаллы в shop.gems (js/12-shop.js): эти функции вызываются уже после загрузки всех файлов.
const streakEls = {
  card: document.getElementById('streak-card'), title: document.getElementById('streak-title'), sub: document.getElementById('streak-sub'),
  week: document.getElementById('streak-week'), msg: document.getElementById('streak-msg')
};
const streakState = { data: null, loading: false, busy: false, msg: '' };

function validStreakStatus(d) {
  return !!d && typeof d.claimed_today === 'boolean' && Number.isSafeInteger(d.streak_day) && d.streak_day >= 1 && d.streak_day <= 7 && Number.isSafeInteger(d.cycle) && d.cycle >= 1
    && !!d.reward && Number.isSafeInteger(d.reward.chips) && d.reward.chips >= 0 && Number.isSafeInteger(d.reward.gems) && d.reward.gems >= 0 && Array.isArray(d.week) && d.week.length === 7;
}

const validStreakClaim = (d) => !!d && Number.isSafeInteger(d.chips) && d.chips >= 0 && (d.collection_part === null || (!!d.collection_part && typeof d.collection_part.collection_name === 'string' && typeof d.collection_part.name === 'string')) && Number.isSafeInteger(d.gems) && d.gems >= 0 && Number.isSafeInteger(d.balance) && d.balance >= 0
  && Number.isSafeInteger(d.gems_balance) && d.gems_balance >= 0 && typeof d.gems_capped === 'boolean';

function streakRewardText(chips, gems) {
  return '+' + formatNumber(chips) + ' фишек' + (gems > 0 ? ' и ' + formatNumber(gems) + ' кристаллов' : '');
}

function setStreakMsg(text) {
  streakState.msg = text;
  streakEls.msg.textContent = text;
}

function renderStreak() {
  const d = streakState.data;
  streakEls.card.hidden = !d;
  if (!d) return;
  streakEls.card.disabled = d.claimed_today || streakState.busy;
  streakEls.card.setAttribute('aria-disabled', String(d.claimed_today));
  streakEls.title.textContent = d.claimed_today ? 'Награда получена' : 'Награда дня';
  streakEls.sub.textContent = d.claimed_today ? 'Завтра: ' + streakRewardText(d.reward.chips, d.reward.gems) : 'День ' + d.streak_day + ' из 7: ' + streakRewardText(d.reward.chips, d.reward.gems);
  streakEls.week.textContent = '';
  for (let i = 1; i <= 7; i += 1) {
    const dot = document.createElement('span');
    dot.className = 'streak-dot' + (i < d.streak_day ? ' done' : '') + (i === d.streak_day && !d.claimed_today ? ' now' : '') + (i === 7 ? ' gem' : '');
    streakEls.week.appendChild(dot);
  }
}

async function loadStreak() {
  if (streakState.loading || !(tg && tg.initData)) return;
  streakState.loading = true;
  try {
    streakState.data = await fetchGameState('/api/streak', validStreakStatus);
  } catch (e) {
    // без карточки приложение работает: награда не обязательна
  } finally {
    streakState.loading = false;
    renderStreak();
  }
}

async function claimStreak() {
  const d = streakState.data;
  if (!d || d.claimed_today || streakState.busy) return;
  if (!(tg && tg.initData)) { setStreakMsg('Откройте игру через бота в Telegram'); return; }
  streakState.busy = true;
  setStreakMsg('');
  renderStreak();
  const result = await postWithRetries(() => wdPostOnce('/api/streak/claim', {}, validStreakClaim));
  streakState.busy = false;
  if (result && result.kind === 'ok') {
    const r = result.data;
    wdApplyBalance(r.balance);
    shop.gems = r.gems_balance;
    setStreakMsg('Награда получена: ' + streakRewardText(r.chips, r.gems) + (r.gems_capped ? ' (кристаллов за этот месяц больше нет)' : '') +
      (r.collection_part ? '. Часть коллекции «' + r.collection_part.collection_name.slice(0, 40) + '»: ' + r.collection_part.name.slice(0, 40) : ''));
    haptic('success');
    await loadStreak();
    return;
  }
  setStreakMsg(result && result.kind === 'conflict' ? 'Не удалось получить награду' : wdErrorText(result || { kind: 'retry' }));
  renderStreak();
}

streakEls.card.addEventListener('click', claimStreak);
// #endregion
