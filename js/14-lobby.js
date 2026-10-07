// #region Лобби, меню игр и вкладки
// ---------- титульный экран ----------
// Баланс и уровень берутся из общего серверного состояния (srv), новых запросов нет. Карточки игр строятся из реестра GAMES.
const lobbyEls = {
  root: document.querySelector('.screen.lobby'),
  balance: document.getElementById('lobby-balance'),
  level: document.getElementById('lobby-level'),
  grid: document.getElementById('lobby-grid')
};

function renderLobby() {
  if (srv.loaded) {
    lobbyEls.balance.classList.remove('skeleton');
    lobbyEls.balance.textContent = spacedNumber(availableBalance());
  } else {
    lobbyEls.balance.textContent = srv.error ? '—' : 'Загрузка…';
  }
  fitNumberFont(lobbyEls.balance, lobbyEls.balance.textContent.length);
  lobbyEls.level.hidden = srv.level === null || !srv.loaded;
  if (srv.level !== null) lobbyEls.level.textContent = 'Ур. ' + srv.level;
  // пока не пришёл первый ответ, содержимое скрыто: если есть незавершённая игра, титульный экран не мелькает
  lobbyEls.root.classList.toggle('booting', !(srv.loaded || srv.error));
}
lobbyRender = renderLobby;

// Реестр игр: чтобы добавить игру, нужна запись здесь и экран с data-screen="<id>".
// Порядок записей = порядок карточек на титульном экране и пунктов меню (два столбца: слева направо, сверху вниз).
// Для ready: false игра не открывается: нажатие показывает «Скоро».
// Иконка — вложенный SVG (24×24, контур)
const GAMES = [
  { id: 'crash',     label: 'Краш',      hint: 'Забери вовремя', desc: 'Успей забрать до краха', ready: true, icon: '<path d="M3 20h18M4 16l5-5 4 3 7-8M15 6h5v5"/>' },
  { id: 'roulette',  label: 'Рулетка',   hint: 'Угадай цвет', desc: 'Классическая европейская рулетка', ready: true,  icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>' },
  { id: 'keno',      label: 'Кено',      hint: 'Угадай числа', desc: 'Выбери числа и жди розыгрыш', ready: true, icon: '<circle cx="6" cy="6" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18" cy="12" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>' },
  { id: 'mines',     label: 'Мины',      hint: 'Обойди мины', desc: 'Открывай клетки и забирай выигрыш', ready: true, icon: '<circle cx="11" cy="14" r="7"/><path d="M16 9l3-3M18 4l2 2M11 3v2M4 14H2M20 14h2"/>' },
  { id: 'hilo',      label: 'Хило',      hint: 'Выше или ниже?', desc: 'Угадай: выше или ниже карта', ready: true,  icon: '<rect x="3" y="4" width="8" height="12" rx="2"/><rect x="13" y="8" width="8" height="12" rx="2"/><path d="M7 7.5v5M5.2 9.3 7 7.5l1.8 1.8M17 11.5v5M15.2 14.7 17 16.5l1.8-1.8"/>' },
  { id: 'blackjack', label: 'Блэкджек',  hint: 'Набери 21', desc: 'Набери 21', ready: true, icon: '<rect x="4" y="6" width="11" height="15" rx="2"/><path d="M9 3h9a2 2 0 0 1 2 2v12"/>' },
  // раздел встроенных игр на фишках приложения: список внутри строится из EMBEDDED_GAMES
  { id: 'arcade',    label: 'Не слоты',  hint: 'Другие игры', desc: 'Western Slot и другие игры', ready: true, icon: '<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M8 10v4M6 12h4M15 11h.01M17.5 13h.01"/>' }
];
// Пока игра не выбрана (currentGame = 'lobby'), вкладка «Играть» показывает титульный экран. После выбора он не возвращается
// до следующего запуска; выбранная игра хранится только в памяти.
let currentGame = 'lobby';

// Нижняя панель: названия и иконки меняются здесь. Иконка — вложенный SVG (24×24, контур).
// Иконка центральной кнопки: нейтральная, пока игра не выбрана; после выбора подменяется иконкой открытой игры.
const TABS = [
  { id: 'shop',    label: 'Магазин', icon: '<path d="M5 8h14l-1 12H6L5 8z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>' },
  { id: 'rating',  label: 'Рейтинг', icon: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3"/>' },
  { id: 'play',    label: 'Играть',  icon: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>', main: true },
  { id: 'farm',    label: 'Ферма',   icon: '<path d="M7 20h10"/><path d="M10 20c5.5-2.5.8-6.4 3-10"/><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z"/><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z"/>' },
  { id: 'profile', label: 'Профиль', icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>' }
];
const START_TAB = 'play';
const navEl = document.getElementById('nav');

const gameMenu = document.getElementById('game-menu');
const gamePanel = document.getElementById('game-panel');
const gameGrid = document.getElementById('game-grid');
let activeTab = START_TAB;

const iconSvg = (path) => `<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
const getGame = (id) => GAMES.find((g) => g.id === id);

// Экраны только прячутся и показываются, игровые элементы не пересоздаются.
// Вкладка «Играть» показывает экран выбранной игры.
function showTab(id) {
  activeTab = id;
  const screen = id === 'play' ? currentGame : id;
  document.querySelectorAll('[data-screen]').forEach((el) => { el.hidden = el.dataset.screen !== screen; });
  closeGameMenu();
  if (screen === 'shop') { if (started) openShop(); } else closeShop();
  if (screen === 'crash') crResume();
  else crPause();
  if (started && (screen === 'profile' || screen === 'roulette' || screen === 'lobby')) loadServer('open');
  if (started && screen === 'lobby') loadStreak();
  if (started && screen === 'rating') loadRating('open');
  if (started && screen === 'farm') loadFarm('open');
  if (started && screen === 'mines') loadMines('open');
  if (started && screen === 'blackjack') loadBj('open');
  if (started && screen === 'crash') loadCrash('open');
  if (started && screen === 'hilo') loadHilo('open');
  if (started && screen === 'keno') {
    loadServer('open');
    loadKenoPay();
    renderKeno();
  }
  navEl.querySelectorAll('.tab').forEach((btn) => {
    if (btn.dataset.tab === id) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
}

TABS.forEach((tab) => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'tab' + (tab.main ? ' main' : '');
  btn.dataset.tab = tab.id;
  btn.innerHTML = `${iconSvg(tab.icon)}<span>${tab.label}</span>`;
  btn.addEventListener('click', () => {
    // повторное нажатие на «Играть» открывает и закрывает меню игр (в том числе поверх титульного экрана)
    if (tab.id === 'play' && activeTab === 'play') toggleGameMenu();
    else showTab(tab.id);
  });
  navEl.appendChild(btn);
});

// Меню игр: нельзя открыть, пока колесо на экране (вращение и пауза после него)
function closeGameMenu() {
  gameMenu.classList.remove('open');
  gamePanel.classList.remove('dragging');
  gamePanel.style.transform = '';
  navEl.querySelector('.tab.main').setAttribute('aria-expanded', 'false');
}

function toggleGameMenu() {
  if (gameMenu.classList.contains('open')) {
    closeGameMenu();
    return;
  }
  if (wheelLayer.classList.contains('active') || gameBusy()) return;
  gameMenu.classList.add('open');
  navEl.querySelector('.tab.main').setAttribute('aria-expanded', 'true');
}

function selectGame(id) {
  if (!getGame(id).ready) { showSoon(); return; }
  currentGame = id;
  navEl.querySelector('.tab.main svg').outerHTML = iconSvg(getGame(id).icon);
  // кнопка смены игры есть в шапке рулетки и в шапке мин; разметка иконки постоянная, из реестра
  document.querySelectorAll('.switch-icon').forEach((el) => { el.innerHTML = iconSvg(getGame(id).icon); });
  document.querySelectorAll('.switch-name').forEach((el) => { el.textContent = getGame(id).label; });
  gamePanel.querySelectorAll('.tile').forEach((t) => {
    t.setAttribute('aria-current', String(t.dataset.game === id));
  });
  showTab('play');
}

// Короткое сообщение «Скоро» для игр-заглушек (игра не открывается)
function showSoon() {
  showToast('Скоро');
}

// Плитки меню и карточки титульного экрана строятся из реестра GAMES
GAMES.forEach((game, i) => {
  const tile = document.createElement('button');
  tile.type = 'button';
  tile.className = 'tile';
  tile.dataset.game = game.id;
  tile.style.setProperty('--i', i);
  tile.setAttribute('role', 'menuitem');
  tile.dataset.soon = String(!game.ready);
  tile.innerHTML = `${iconSvg(game.icon)}<span class="tile-name">${game.label}</span><span class="tile-hint">${game.hint || ''}</span>`;
  tile.addEventListener('click', () => selectGame(game.id));
  gameGrid.appendChild(tile);

  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'lobby-card';
  card.dataset.game = game.id;
  card.dataset.soon = String(!game.ready);
  card.setAttribute('role', 'menuitem');
  card.innerHTML = `${iconSvg(game.icon)}<span class="lobby-name"></span><span class="lobby-desc"></span>`;
  card.querySelector('.lobby-name').textContent = game.label;
  card.querySelector('.lobby-desc').textContent = game.desc || '';
  card.addEventListener('click', () => selectGame(game.id));
  lobbyEls.grid.appendChild(card);
});
renderLobby();

gameMenu.addEventListener('click', (e) => {
  if (e.target === gameMenu) closeGameMenu(); // нажатие по затемнению
});
gameSwitchEl.addEventListener('click', toggleGameMenu);

// Шторка закрывается свайпом вниз
(() => {
  let startY = null;
  let dy = 0;
  gamePanel.addEventListener('touchstart', (e) => {
    startY = e.touches[0].clientY;
    dy = 0;
  }, { passive: true });
  gamePanel.addEventListener('touchmove', (e) => {
    if (startY === null) return;
    dy = Math.max(0, e.touches[0].clientY - startY);
    gamePanel.classList.add('dragging');
    gamePanel.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  const end = () => {
    if (startY === null) return;
    const far = dy > 80;
    startY = null;
    gamePanel.classList.remove('dragging');
    gamePanel.style.transform = '';
    if (far) closeGameMenu();
  };
  gamePanel.addEventListener('touchend', end);
  gamePanel.addEventListener('touchcancel', end);
})();
// #endregion

