// Порядок чисел на колесе европейской рулетки (по часовой стрелке)
const WHEEL_ORDER = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
];

const RED_NUMBERS = new Set([
  1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
]);

const COLOR_HEX = { red: '#c62828', black: '#111111', green: '#1b8f3a' };
const COLOR_NAME = { red: 'красное', black: 'чёрное', green: 'зелёное (зеро)' };

const SECTOR = 360 / WHEEL_ORDER.length; // угол одного сектора
const SPIN_TIME_MS = 5000;               // должно совпадать с transition в style.css

const canvas = document.getElementById('wheel');
const ctx = canvas.getContext('2d');
const spinBtn = document.getElementById('spin');
const numberEl = document.getElementById('result-number');
const colorEl = document.getElementById('result-color');

let rotation = 0; // сколько градусов колесо прокрутили всего

function getColor(n) {
  if (n === 0) return 'green';
  return RED_NUMBERS.has(n) ? 'red' : 'black';
}

// Рисуем колесо один раз. Сектор i находится по центру в углу i * SECTOR
// от верха (по часовой стрелке), поэтому 0 изначально под стрелкой.
function drawWheel() {
  const size = canvas.width;
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 4;
  const rad = (deg) => (deg * Math.PI) / 180;

  ctx.clearRect(0, 0, size, size);

  WHEEL_ORDER.forEach((num, i) => {
    // в canvas угол 0 — справа, поэтому сдвигаем на -90°, чтобы считать от верха
    const start = rad(i * SECTOR - SECTOR / 2 - 90);
    const end = rad(i * SECTOR + SECTOR / 2 - 90);

    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, start, end);
    ctx.closePath();
    ctx.fillStyle = COLOR_HEX[getColor(num)];
    ctx.fill();
    ctx.strokeStyle = '#d9b84a';
    ctx.lineWidth = 2;
    ctx.stroke();

    // подпись числа: поворачиваем холст к центру сектора и рисуем у края
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(i * SECTOR));
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 28px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(num), 0, -radius + 34);
    ctx.restore();
  });

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = '#2b1d0e';
  ctx.fill();
  ctx.strokeStyle = '#d9b84a';
  ctx.lineWidth = 4;
  ctx.stroke();
}

function spin() {
  spinBtn.disabled = true;

  // 1. Выбираем случайный индекс сектора (а значит и число)
  const index = Math.floor(Math.random() * WHEEL_ORDER.length);
  const winner = WHEEL_ORDER[index];

  // 2. Считаем, на какой угол надо повернуть колесо, чтобы этот сектор
  //    оказался под стрелкой. Небольшой случайный сдвиг внутри сектора
  //    делает остановку более «живой».
  const jitter = (Math.random() - 0.5) * SECTOR * 0.7;
  const targetMod = (360 - index * SECTOR + jitter + 360) % 360;
  const currentMod = ((rotation % 360) + 360) % 360;
  const delta = (targetMod - currentMod + 360) % 360;
  const fullTurns = 5 + Math.floor(Math.random() * 3); // 5–7 полных оборотов

  rotation += fullTurns * 360 + delta;
  canvas.style.transform = `rotate(${rotation}deg)`;

  // 3. Когда анимация закончилась — показываем результат
  setTimeout(() => showResult(winner), SPIN_TIME_MS + 100);
}

function showResult(n) {
  const color = getColor(n);
  numberEl.textContent = n;
  numberEl.className = 'result-number ' + color;
  colorEl.textContent = COLOR_NAME[color];
  spinBtn.disabled = false;
}

drawWheel();
spinBtn.addEventListener('click', spin);
