// #region Доступность: окна
// Escape закрывает верхнее открытое окно; при открытии фокус переходит в окно (без вызова клавиатуры), при закрытии возвращается на кнопку, с которой окно открыли.
// Следим за самими элементами окон (hidden и class), поэтому функции открытия и закрытия окон не знают про фокус.
// Порядок от верхнего окна к нижнему: Escape закрывает первое открытое.
const A11Y_OVERLAYS = [
  { el: () => arcadeEls.view, isOpen: (el) => !el.hidden, close: () => closeEmbedded(), focus: () => document.getElementById('embed-back') },
  { el: () => wdEls.pSheet, isOpen: (el) => !el.hidden, close: () => closeWdPreview(), focus: (el) => el.querySelector('[role="dialog"]') },
  { el: () => gameMenu, isOpen: (el) => el.classList.contains('open'), close: () => closeGameMenu(), focus: () => gamePanel }
];

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || e.defaultPrevented) return;
  const top = A11Y_OVERLAYS.find((o) => o.isOpen(o.el()));
  if (!top) return;
  e.preventDefault();
  top.close();
});

A11Y_OVERLAYS.forEach((o) => {
  const el = o.el();
  let wasOpen = o.isOpen(el);
  let returnTo = null;
  new MutationObserver(() => {
    const open = o.isOpen(el);
    if (open === wasOpen) return;
    wasOpen = open;
    if (open) {
      returnTo = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
      if (el.contains(document.activeElement)) return;   // окно само поставило фокус (поле поиска)
      const target = o.focus(el);
      if (target) {
        if (!target.hasAttribute('tabindex') && !/^(BUTTON|INPUT|A)$/.test(target.tagName)) target.setAttribute('tabindex', '-1');
        // окно может быть ещё невидимо (переход visibility при открытии): фокус не берётся, пробуем снова, пока окно открыто
        [0, 80, 350].forEach((delay) => setTimeout(() => {
          if (o.isOpen(el) && !el.contains(document.activeElement)) target.focus({ preventScroll: true });
        }, delay));
      }
    } else if (returnTo && returnTo.isConnected && !returnTo.hidden) {
      returnTo.focus({ preventScroll: true });
      returnTo = null;
    }
  }).observe(el, { attributes: true, attributeFilter: ['hidden', 'class'] });
});
// #endregion
