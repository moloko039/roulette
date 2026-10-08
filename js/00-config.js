// Адрес сервера с балансом. Менять только здесь.
const API_URL = 'https://roulette-production-4b93.up.railway.app';

// Клиент: обычные скрипты без модулей и сборщика; index.html подключает js/*.js по порядку номеров, общая область видимости одна (top-level const и function видны во всех файлах).
// Порядок важен: файл может использовать только то, что объявлено выше него или вызывается позже загрузки (обработчики, таймеры).
// Секции внутри файлов помечены // #region ... // #endregion. После правки js/ запусти python scripts/stamp_client.py (метки версий в index.html).
//   00-config     адрес сервера, константы и форматирование чисел
//   01-core       ЯДРО: время, Telegram, сеть, реестр игр, общие каркасы игр (загрузка состояния, повторы, фишки)
//   02-bet        ввод суммы и фишки, панель ставок над клавиатурой и защита тапов
//   03-roulette   рулетка и ставка через сервер
//   04-profile    баланс и профиль (/api/me)
//   05-rating     рейтинг беседы и рекорды
//   06-farm       ферма, минутное начисление и бусты беседы
//   07-mines      мины (состояние, рисование, запросы, действия)
//   08-keno       кено
//   09-blackjack  блэкджек
//   10-crash      краш
//   11-hilo       хило
//   12-shop       магазин: страницы Оформление, Фишки (за кристаллы), Кристаллы (покупка за Stars)
//   13-gift       подарки косметикой участникам беседы (в листе предпросмотра предмета)
//   13-wardrobe   гардероб (страница «Оформление» магазина)
//   14-lobby      лобби, меню игр и вкладки
//   14-streak     серия входов: карточка «Награда дня» на титульном экране
//   15-arcade     «Не слоты» (встроенные игры и мост)
//   16-a11y       доступность окон: Escape закрывает верхнее окно, фокус входит в окно и возвращается
//   17-start      запуск

// #region Константы и форматирование чисел
// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

// цвета секторов колеса задают CSS-переменные стола (--table-red, --table-black, --table-green в style.css); читаются при отрисовке (см. wheelPalette)
const WHEEL_SECTOR_VAR = { red: '--table-red', black: '--table-black', green: '--table-green' };

// числа с разделителем тысяч («1 300»); если Intl недоступен, без него
const formatNumber = (() => {
  try {
    const f = new Intl.NumberFormat('ru-RU');
    return (n) => f.format(n);
  } catch (e) {
    return (n) => String(n);
  }
})();

// Сокращение от миллиона: «1,2 млн», «3,4 млрд». Полное значение остаётся в title и открывается нажатием
const COMPACT_UNITS = [[1e15, 'квадр.'], [1e12, 'трлн'], [1e9, 'млрд'], [1e6, 'млн']];
function formatCompact(n) {
  if (!(n >= 1e6)) return formatNumber(n);
  for (const [unit, name] of COMPACT_UNITS) {
    if (n >= unit) return String(Math.floor((n / unit) * 10) / 10).replace('.', ',') + ' ' + name;
  }
  return formatNumber(n);
}

function setNumber(el, n) {
  const full = formatNumber(n);
  const short = formatCompact(n);
  el.textContent = short;
  el.title = full;
  el.dataset.short = short;
  el.dataset.full = full;
  el.classList.toggle('num-tap', short !== full);
}

document.addEventListener('click', (e) => {
  const el = e.target instanceof Element ? e.target.closest('.num-tap') : null;
  if (!el) return;
  el.textContent = el.textContent === el.dataset.full ? el.dataset.short : el.dataset.full;
});

// #endregion Константы и форматирование чисел

