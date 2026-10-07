// #region Запуск
// ---------- запуск ----------
showTab('play');   // титульный экран; если у игрока есть незавершённая игра, её откроет ответ /api/me

loadState();
drawWheel();
ballRadius = BALL_POCKET;
renderSpin();
window.addEventListener('resize', renderSpin);
buildTable();
renderHistory();
spinBtn.addEventListener('click', spin);
document.getElementById('clear-bets').addEventListener('click', clearBets);
document.getElementById('repeat-bets').addEventListener('click', repeatBets);
const syncChips = () => {
  betsPanel.querySelectorAll('.chip').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(Number(amountEl.value) === Number(btn.dataset.amount)));
  });
};
betsPanel.querySelectorAll('.chip').forEach((btn) => {
  btn.addEventListener('click', () => {
    amountEl.value = btn.dataset.amount;
    syncChips();
    haptic('light');
  });
});
setupBetPanel({
  input: amountEl,
  maxBtn: document.getElementById('amount-max'),
  halfBtn: document.getElementById('amount-half'),
  doubleBtn: document.getElementById('amount-double'),
  getLimit: rouletteBetLimit
});
amountEl.addEventListener('input', syncChips);
syncChips();

// шрифты грузятся локально: после загрузки колесо перерисовывается (числа на секторах)
if (document.fonts && document.fonts.load) {
  document.fonts.load('700 28px "Playfair Display"').then(drawWheel, () => {});
}

// всё собрано: показываем состояние и загружаем баланс с сервера
started = true;
renderAll();
loadServer('open');

// #endregion