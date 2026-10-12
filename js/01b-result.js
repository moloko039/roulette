// #region Единый блок результата (UI_REVIEW 1.6)
// Один вид итога раунда во всех играх: заголовок (выигрыш тёплым золотом, проигрыш приглушённо, ничья нейтрально), сумма крупно, пояснение.
// Контейнер даёт игра, в нём три элемента: .res-title, .res-amount, .res-note. Блок живёт до следующей партии (прячет его игра).
// Сумма сокращается по общему правилу (formatCompact), полное значение по нажатию (num-tap из js/00-config.js).
const RES_KINDS = ['win', 'lose', 'push'];

function fillResult(box, r) {
  const title = box.querySelector('.res-title');
  const amount = box.querySelector('.res-amount');
  const note = box.querySelector('.res-note');
  box.classList.add('res-box');
  RES_KINDS.forEach((k) => box.classList.toggle(k, r.kind === k));
  box.dataset.kind = RES_KINDS.includes(r.kind) ? r.kind : '';
  title.textContent = r.title || '';
  if (typeof r.amount === 'number') {
    const sign = r.sign || '';
    const short = sign + formatCompact(r.amount);
    const full = sign + formatNumber(r.amount);
    amount.textContent = short;
    amount.title = full;
    amount.dataset.short = short;
    amount.dataset.full = full;
    amount.classList.toggle('num-tap', short !== full);
    amount.hidden = false;
  } else {
    amount.textContent = '';
    amount.title = '';
    amount.classList.remove('num-tap');
    amount.hidden = true;
  }
  note.textContent = r.note || '';
  note.hidden = !r.note;
}

function clearResult(box) {
  fillResult(box, { kind: '', title: '', amount: null, note: '' });
}
// #endregion
