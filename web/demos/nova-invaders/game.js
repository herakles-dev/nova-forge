// Nova Invaders — Space arcade game built by Nova Forge + Amazon Nova Lite
// Canvas & context
const canvas = document.getElementById('game-canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas() {
  const maxW = Math.min(window.innerWidth - 20, 700);
  const maxH = Math.min(window.innerHeight - 20, 900);
  const ratio = 7 / 9;
  if (maxW / maxH > ratio) {
    canvas.height = maxH;
    canvas.width = Math.floor(maxH * ratio);
  } else {
    canvas.width = maxW;
    canvas.height = Math.floor(maxW / ratio);
  }
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

// ── Game State ──────────────────────────────────────────────────────
const COLORS = {
  player: '#67e8f9',
  bullet: '#f472b6',
  enemyA: '#fb923c',
  enemyB: '#a78bfa',
  enemyC: '#4ade80',
  powerUp: '#facc15',
  shield: '#38bdf8',
  star: 'rgba(255,255,255,0.6)',
};

let state = 'menu'; // menu | playing | gameover
let score = 0, wave = 1, lives = 3, combo = 0;
let player, bullets, enemies, particles, powerUps, stars;
let keys = {};
let lastShot = 0, shootCooldown = 180;
let shieldTimer = 0, rapidTimer = 0;
let touchLeft = false, touchRight = false, touchShoot = false;
let frameCount = 0;

// ── Entities ────────────────────────────────────────────────────────
function createPlayer() {
  return { x: canvas.width / 2, y: canvas.height - 50, w: 28, h: 22, speed: 4.5 };
}

function createBullet(x, y) {
  return { x, y, w: 3, h: 12, speed: 7, color: rapidTimer > 0 ? '#4ade80' : COLORS.bullet };
}

function createEnemy(type, x, y) {
  const defs = {
    A: { w: 26, h: 20, hp: 1, speed: 0.6, pts: 10, color: COLORS.enemyA },
    B: { w: 22, h: 22, hp: 2, speed: 0.8, pts: 25, color: COLORS.enemyB },
    C: { w: 18, h: 18, hp: 1, speed: 1.4, pts: 15, color: COLORS.enemyC },
  };
  const d = defs[type] || defs.A;
  return { type, x, y, ...d, baseX: x, age: 0, alive: true };
}

function createPowerUp(x, y) {
  const types = ['shield', 'rapid', 'life'];
  const t = types[Math.floor(Math.random() * types.length)];
  return { type: t, x, y, w: 14, h: 14, speed: 1.8, age: 0 };
}

function createParticle(x, y, color, count) {
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const speed = Math.random() * 3 + 1;
    particles.push({
      x, y, color,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life: 1,
      decay: 0.02 + Math.random() * 0.03,
      size: Math.random() * 3 + 1,
    });
  }
}

function initStars() {
  stars = [];
  for (let i = 0; i < 80; i++) {
    stars.push({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      size: Math.random() * 1.5 + 0.3,
      speed: Math.random() * 0.5 + 0.2,
      brightness: Math.random(),
    });
  }
}

// ── Wave Spawner ────────────────────────────────────────────────────
function spawnWave() {
  enemies = [];
  const rows = Math.min(3 + Math.floor(wave / 2), 6);
  const cols = Math.min(5 + Math.floor(wave / 3), 9);
  const gapX = 38, gapY = 34;
  const startX = (canvas.width - cols * gapX) / 2 + 10;

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let type = r < 1 ? 'B' : r < 3 ? 'A' : 'C';
      if (wave > 3 && r === 0 && c % 2 === 0) type = 'B';
      enemies.push(createEnemy(type, startX + c * gapX, 50 + r * gapY));
    }
  }
}

// ── Input ───────────────────────────────────────────────────────────
document.addEventListener('keydown', e => { keys[e.key] = true; });
document.addEventListener('keyup', e => { keys[e.key] = false; });

canvas.addEventListener('touchstart', e => {
  e.preventDefault();
  for (const t of e.changedTouches) {
    const rx = t.clientX - canvas.getBoundingClientRect().left;
    const third = canvas.width / 3;
    if (rx < third) touchLeft = true;
    else if (rx > third * 2) touchRight = true;
    else touchShoot = true;
  }
}, { passive: false });

canvas.addEventListener('touchend', e => {
  e.preventDefault();
  touchLeft = false; touchRight = false; touchShoot = false;
}, { passive: false });

// ── Update ──────────────────────────────────────────────────────────
function update() {
  frameCount++;

  // Stars
  stars.forEach(s => {
    s.y += s.speed;
    if (s.y > canvas.height) { s.y = 0; s.x = Math.random() * canvas.width; }
  });

  if (state !== 'playing') return;

  // Timers
  if (shieldTimer > 0) shieldTimer--;
  if (rapidTimer > 0) rapidTimer--;

  // Player movement
  const moveL = keys['ArrowLeft'] || keys['a'] || keys['A'] || touchLeft;
  const moveR = keys['ArrowRight'] || keys['d'] || keys['D'] || touchRight;
  if (moveL) player.x -= player.speed;
  if (moveR) player.x += player.speed;
  player.x = Math.max(player.w / 2, Math.min(canvas.width - player.w / 2, player.x));

  // Shooting
  const wantShoot = keys[' '] || keys['ArrowUp'] || keys['w'] || keys['W'] || touchShoot;
  const now = performance.now();
  const cd = rapidTimer > 0 ? shootCooldown / 2.5 : shootCooldown;
  if (wantShoot && now - lastShot > cd) {
    bullets.push(createBullet(player.x, player.y - player.h / 2));
    lastShot = now;
    createParticle(player.x, player.y - player.h / 2, COLORS.bullet, 3);
  }

  // Bullets
  bullets.forEach(b => { b.y -= b.speed; });
  bullets = bullets.filter(b => b.y > -10);

  // Enemies
  const edgeMargin = 20;
  let hitEdge = false;
  enemies.forEach(e => {
    e.age++;
    e.x = e.baseX + Math.sin(e.age * 0.02 + e.y * 0.01) * (8 + wave);
    e.y += e.speed * (0.15 + wave * 0.02);
    if (e.x < edgeMargin || e.x > canvas.width - edgeMargin) hitEdge = true;

    // Hit player
    if (e.alive && Math.abs(e.x - player.x) < (e.w + player.w) / 2 && Math.abs(e.y - player.y) < (e.h + player.h) / 2) {
      if (shieldTimer > 0) {
        e.alive = false;
        createParticle(e.x, e.y, COLORS.shield, 12);
        shieldTimer = 0;
      } else {
        e.alive = false;
        loseLife();
      }
    }

    // Off screen
    if (e.y > canvas.height + 20) {
      e.alive = false;
      combo = 0;
    }
  });

  // Bullet-enemy collision
  bullets.forEach(b => {
    enemies.forEach(e => {
      if (!e.alive) return;
      if (Math.abs(b.x - e.x) < (b.w + e.w) / 2 && Math.abs(b.y - e.y) < (b.h + e.h) / 2) {
        e.hp--;
        b.y = -100; // remove
        if (e.hp <= 0) {
          e.alive = false;
          combo++;
          const comboMult = Math.min(combo, 10);
          score += e.pts * comboMult;
          createParticle(e.x, e.y, e.color, 15);
          // Drop power-up (12% chance)
          if (Math.random() < 0.12) {
            powerUps.push(createPowerUp(e.x, e.y));
          }
        } else {
          createParticle(b.x, b.y, '#fff', 4);
        }
      }
    });
  });

  enemies = enemies.filter(e => e.alive);

  // Power-ups
  powerUps.forEach(p => {
    p.y += p.speed;
    p.age++;
    if (Math.abs(p.x - player.x) < (p.w + player.w) / 2 && Math.abs(p.y - player.y) < (p.h + player.h) / 2) {
      if (p.type === 'shield') { shieldTimer = 300; createParticle(player.x, player.y, COLORS.shield, 20); }
      else if (p.type === 'rapid') { rapidTimer = 360; createParticle(player.x, player.y, '#4ade80', 20); }
      else if (p.type === 'life') { lives = Math.min(lives + 1, 5); createParticle(player.x, player.y, '#f472b6', 20); }
      p.y = canvas.height + 100;
    }
  });
  powerUps = powerUps.filter(p => p.y < canvas.height + 50);

  // Particles
  particles.forEach(p => {
    p.x += p.vx; p.y += p.vy;
    p.life -= p.decay;
    p.vx *= 0.98; p.vy *= 0.98;
  });
  particles = particles.filter(p => p.life > 0);

  // Wave cleared
  if (enemies.length === 0) {
    wave++;
    combo = 0;
    score += wave * 100;
    createParticle(canvas.width / 2, canvas.height / 2, COLORS.powerUp, 40);
    spawnWave();
  }

  updateHUD();
}

function loseLife() {
  lives--;
  combo = 0;
  createParticle(player.x, player.y, '#f472b6', 30);
  if (lives <= 0) {
    state = 'gameover';
    document.getElementById('final-score').textContent = score;
    document.getElementById('final-wave').textContent = wave;
    document.getElementById('game-over-screen').style.display = 'flex';
  } else {
    player.x = canvas.width / 2;
    shieldTimer = 90; // brief invincibility
  }
}

function updateHUD() {
  document.getElementById('score-display').textContent = 'SCORE: ' + score;
  document.getElementById('level-display').textContent = 'WAVE: ' + wave;
  let hearts = '';
  for (let i = 0; i < lives; i++) hearts += '♥';
  document.getElementById('lives-display').textContent = hearts;
}

// ── Draw ────────────────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Stars
  stars.forEach(s => {
    const b = 0.4 + Math.sin(frameCount * 0.03 + s.brightness * 10) * 0.3;
    ctx.fillStyle = `rgba(255,255,255,${b})`;
    ctx.fillRect(s.x, s.y, s.size, s.size);
  });

  if (state !== 'playing') return;

  // Player ship (triangle with glow)
  ctx.save();
  if (shieldTimer > 0) {
    ctx.shadowColor = COLORS.shield;
    ctx.shadowBlur = 15;
    ctx.strokeStyle = COLORS.shield;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(player.x, player.y, player.w * 0.8, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = COLORS.player;
  ctx.shadowColor = COLORS.player;
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.moveTo(player.x, player.y - player.h / 2);
  ctx.lineTo(player.x - player.w / 2, player.y + player.h / 2);
  ctx.lineTo(player.x + player.w / 2, player.y + player.h / 2);
  ctx.closePath();
  ctx.fill();
  // Engine glow
  ctx.fillStyle = rapidTimer > 0 ? '#4ade80' : '#fb923c';
  ctx.beginPath();
  ctx.arc(player.x, player.y + player.h / 2 + 3, 4 + Math.sin(frameCount * 0.3) * 2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  // Bullets
  bullets.forEach(b => {
    ctx.save();
    ctx.fillStyle = b.color;
    ctx.shadowColor = b.color;
    ctx.shadowBlur = 8;
    ctx.fillRect(b.x - b.w / 2, b.y, b.w, b.h);
    ctx.restore();
  });

  // Enemies
  enemies.forEach(e => {
    ctx.save();
    ctx.fillStyle = e.color;
    ctx.shadowColor = e.color;
    ctx.shadowBlur = 6;
    if (e.type === 'A') {
      // Diamond
      ctx.beginPath();
      ctx.moveTo(e.x, e.y - e.h / 2);
      ctx.lineTo(e.x + e.w / 2, e.y);
      ctx.lineTo(e.x, e.y + e.h / 2);
      ctx.lineTo(e.x - e.w / 2, e.y);
      ctx.closePath();
      ctx.fill();
    } else if (e.type === 'B') {
      // Hexagon
      ctx.beginPath();
      for (let i = 0; i < 6; i++) {
        const a = Math.PI / 3 * i - Math.PI / 6;
        const px = e.x + Math.cos(a) * e.w / 2;
        const py = e.y + Math.sin(a) * e.h / 2;
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
      // HP indicator
      if (e.hp > 1) {
        ctx.fillStyle = '#fff';
        ctx.font = '8px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(e.hp, e.x, e.y + 3);
      }
    } else {
      // Circle (fast)
      ctx.beginPath();
      ctx.arc(e.x, e.y, e.w / 2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  });

  // Power-ups
  powerUps.forEach(p => {
    ctx.save();
    const pulse = 0.8 + Math.sin(p.age * 0.1) * 0.2;
    ctx.globalAlpha = pulse;
    const pColor = p.type === 'shield' ? COLORS.shield : p.type === 'rapid' ? '#4ade80' : '#f472b6';
    ctx.fillStyle = pColor;
    ctx.shadowColor = pColor;
    ctx.shadowBlur = 12;
    ctx.beginPath();
    // Star shape
    for (let i = 0; i < 5; i++) {
      const a = Math.PI / 2.5 * i - Math.PI / 2;
      const px = p.x + Math.cos(a) * p.w / 2;
      const py = p.y + Math.sin(a) * p.h / 2;
      i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fill();
    // Label
    ctx.fillStyle = '#fff';
    ctx.font = '6px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(p.type === 'shield' ? 'S' : p.type === 'rapid' ? 'R' : '+', p.x, p.y + 3);
    ctx.restore();
  });

  // Particles
  particles.forEach(p => {
    ctx.save();
    ctx.globalAlpha = p.life;
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  });

  // Combo display
  if (combo >= 3) {
    ctx.save();
    ctx.fillStyle = COLORS.powerUp;
    ctx.shadowColor = COLORS.powerUp;
    ctx.shadowBlur = 10;
    ctx.font = '10px "Press Start 2P"';
    ctx.textAlign = 'center';
    ctx.fillText(`${combo}x COMBO!`, canvas.width / 2, 50);
    ctx.restore();
  }
}

// ── Game Loop ───────────────────────────────────────────────────────
function gameLoop() {
  update();
  draw();
  requestAnimationFrame(gameLoop);
}

// ── Start / Restart ─────────────────────────────────────────────────
function startGame() {
  state = 'playing';
  score = 0; wave = 1; lives = 3; combo = 0;
  shieldTimer = 0; rapidTimer = 0;
  player = createPlayer();
  bullets = []; enemies = []; particles = []; powerUps = [];
  initStars();
  spawnWave();
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('game-over-screen').style.display = 'none';
  updateHUD();
}

document.getElementById('start-btn').addEventListener('click', startGame);
document.getElementById('restart-btn').addEventListener('click', startGame);

// Init
initStars();
gameLoop();
