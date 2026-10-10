// #region Колесо рулетки
// ---------- рулетка: колесо и canvas ----------
const SECTOR = 360 / WHEEL_ORDER.length; // угол одного сектора
const SPIN_TIME_MS = 7000;               // сколько длится вращение колеса и шарика

const canvas = document.getElementById('wheel');
const ctx = canvas.getContext('2d');

let wheelAngle = 0;  // поворот колеса, градусы (по часовой от верха)
let ballRel = 0;     // положение шарика относительно колеса, градусы
let ballRadius = 0;  // расстояние шарика от центра, в долях радиуса колеса

const ballEl = document.getElementById('ball');
const wheelBox = document.querySelector('.wheel-box');
const BALL_TRACK = 0.955;  // радиус, по которому шарик катится по ободу
const BALL_POCKET = 0.885; // радиус, на котором он лежит в ячейке

function getColor(n) {
  if (n === 0) return 'green';
  return RED_NUMBERS.has(n) ? 'red' : 'black';
}

// Рисуем колесо один раз. Сектор i находится по центру в углу i * SECTOR
// от верха (по часовой стрелке), поэтому 0 изначально под стрелкой.
// Палитра колеса из CSS-переменных корня (скин «стол» переопределяет их): читается при каждой отрисовке, колесо рисуется редко.
function wheelPalette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim() || 'transparent';
  return {
    sector: { red: v(WHEEL_SECTOR_VAR.red), black: v(WHEEL_SECTOR_VAR.black), green: v(WHEEL_SECTOR_VAR.green) },
    rimA: v('--wheel-rim-a'), rimB: v('--wheel-rim-b'), rimC: v('--wheel-rim-c'), line: v('--wheel-sector-line'),
    text: v('--wheel-text'), edge: v('--wheel-edge'), hub: v('--wheel-hub'), hubLine: v('--wheel-hub-line')
  };
}

function drawWheel() {
  const pal = wheelPalette();
  const size = canvas.width;
  const cx = size / 2;
  const cy = size / 2;
  const outer = size / 2;       // внешний край обода
  const radius = outer - 22;    // внешний край секторов — обод одинаковой толщины со всех сторон
  const rad = (deg) => (deg * Math.PI) / 180;

  ctx.clearRect(0, 0, size, size);

  // Обод рисуем прямо на колесе: он вращается вместе с ним и везде одинаков
  const rim = ctx.createRadialGradient(cx, cy, radius, cx, cy, outer);
  rim.addColorStop(0, pal.rimA);
  rim.addColorStop(0.5, pal.rimB);
  rim.addColorStop(1, pal.rimC);
  ctx.beginPath();
  ctx.arc(cx, cy, outer, 0, Math.PI * 2);
  ctx.fillStyle = rim;
  ctx.fill();

  WHEEL_ORDER.forEach((num, i) => {
    // в canvas угол 0 — справа, поэтому сдвигаем на -90°, чтобы считать от верха
    const start = rad(i * SECTOR - SECTOR / 2 - 90);
    const end = rad(i * SECTOR + SECTOR / 2 - 90);

    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, start, end);
    ctx.closePath();
    ctx.fillStyle = pal.sector[getColor(num)];
    ctx.fill();
    ctx.strokeStyle = pal.line;
    ctx.lineWidth = 2;
    ctx.stroke();

    // подпись числа: поворачиваем холст к центру сектора и рисуем у края
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rad(i * SECTOR));
    ctx.fillStyle = pal.text;
    ctx.font = 'bold 28px "Playfair Display", Georgia, serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(num), 0, -radius + 34);
    ctx.restore();
  });

  // тонкая линия по границе секторов и обода
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = pal.edge;
  ctx.lineWidth = 3;
  ctx.stroke();

  // центр колеса
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = pal.hub;
  ctx.fill();
  ctx.strokeStyle = pal.hubLine;
  ctx.lineWidth = 4;
  ctx.stroke();
}

const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

// Шарик подпрыгивает, прежде чем успокоиться в ячейке
function easeOutBounce(t) {
  const n = 7.5625;
  const d = 2.75;
  if (t < 1 / d) return n * t * t;
  if (t < 2 / d) return n * (t -= 1.5 / d) * t + 0.75;
  if (t < 2.5 / d) return n * (t -= 2.25 / d) * t + 0.9375;
  return n * (t -= 2.625 / d) * t + 0.984375;
}

// Ставит колесо и шарик в нужное положение
function renderSpin() {
  canvas.style.transform = `rotate(${wheelAngle}deg)`;
  const theta = ((wheelAngle + ballRel) * Math.PI) / 180;
  const half = wheelBox.clientWidth / 2;
  const x = Math.sin(theta) * ballRadius * half;
  const y = -Math.cos(theta) * ballRadius * half;
  ballEl.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px)`;
}

// Шарик (angle, radius) считается относительно колеса: сначала он несётся по ободу
// против вращения колеса, затем падает вниз и, попрыгав, замирает в ячейке.
// Так он всегда заканчивает в нужной ячейке и дальше едет вместе с колесом.
function animateSpin(index, onDone) {
  const finalRel = index * SECTOR;
  const wheelStart = wheelAngle;
  const wheelTravel = 720 + Math.random() * 360;
  const laps = 7 + Math.floor(Math.random() * 3);
  // стартовое положение шарика: ячейка прошлого результата, откуда он взлетает
  const back = (((ballRel - finalRel) % 360) + 360) % 360;
  const startRel = finalRel + back - laps * 360;
  const startRadius = ballRadius;
  const t0 = performance.now();

  function frame(now) {
    const p = Math.min((now - t0) / SPIN_TIME_MS, 1);
    const e = easeOutCubic(p);
    wheelAngle = wheelStart + wheelTravel * e;

    let rel = finalRel + (startRel - finalRel) * (1 - e);
    if (p < 0.04) {
      const u = p / 0.04; // взлёт на обод
      ballRadius = startRadius + (BALL_TRACK - startRadius) * (u * u * (3 - 2 * u));
    } else if (p < 0.6) {
      ballRadius = BALL_TRACK;
    } else {
      const q = (p - 0.6) / 0.4; // падение в ячейки
      ballRadius = BALL_TRACK + (BALL_POCKET - BALL_TRACK) * easeOutBounce(q);
      rel += SECTOR * 1.6 * Math.sin(q * Math.PI * 5) * Math.pow(1 - q, 2);
    }
    ballRel = rel;
    renderSpin();

    if (p < 1) {
      requestAnimationFrame(frame);
    } else {
      ballRel = finalRel;
      ballRadius = BALL_POCKET;
      renderSpin();
      onDone();
    }
  }
  requestAnimationFrame(frame);
}

// #endregion
