// Neon Drift — Endless runner built by Nova Forge + Amazon Nova Lite
// Glowing orb weaves through scrolling gates with trail effects and close-call bonuses.
const canvas = document.getElementById('game-canvas');
const ctx = canvas.getContext('2d');

// ── Canvas Sizing ───────────────────────────────────────────────
function resizeCanvas() {
  const maxW = Math.min(window.innerWidth - 20, 500);
  const maxH = Math.min(window.innerHeight - 20, 800);
  const ratio = 5 / 8;
  if (maxW / maxH > ratio) {
    canvas.height = maxH; canvas.width = Math.floor(maxH * ratio);
  } else {
    canvas.width = maxW; canvas.height = Math.floor(maxW / ratio);
  }
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

// ── Constants & State ───────────────────────────────────────────
const C = { purple: '#a78bfa', cyan: '#67e8f9', green: '#4ade80',
  orange: '#fb923c', pink: '#f472b6', text: '#e4e4f0' };
const ORB_R = 12, GATE_GAP = 100, GATE_SPACE = 180, TRAIL_LEN = 18, CC_DIST = 15;

let state = 'menu', score = 0, gatesPassed = 0, speed = 3, speedMult = 1;
let orb, gates, trail, particles, stars;
let keys = {}, mouseX = null, touchActive = false, touchX = 0;
let frameCount = 0, closeCallTimer = 0;

// ── Entities ────────────────────────────────────────────────────
function createOrb() { return { x: canvas.width / 2, y: canvas.height * 0.75 }; }

function createGate(y) {
  const m = 40, gapCenter = m + Math.random() * (canvas.width - m * 2);
  return { y, gapCenter, gapWidth: GATE_GAP, passed: false, closeCalled: false };
}

function spawnParticles(x, y, color, count) {
  for (let i = 0; i < count; i++) {
    const a = Math.random() * Math.PI * 2, s = Math.random() * 4 + 1;
    particles.push({ x, y, color, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
      life: 1, decay: 0.02 + Math.random() * 0.04, size: Math.random() * 3 + 1 });
  }
}

function initStars() {
  stars = [];
  for (let i = 0; i < 60; i++)
    stars.push({ x: Math.random() * canvas.width, y: Math.random() * canvas.height,
      size: Math.random() * 1.2 + 0.3, speed: Math.random() * 0.4 + 0.1, flicker: Math.random() });
}

// ── Input ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => { keys[e.key] = true; });
document.addEventListener('keyup', e => { keys[e.key] = false; });
canvas.addEventListener('mousemove', e => {
  const r = canvas.getBoundingClientRect();
  mouseX = (e.clientX - r.left) * (canvas.width / r.width);
});
canvas.addEventListener('mouseleave', () => { mouseX = null; });
canvas.addEventListener('touchstart', e => {
  e.preventDefault(); touchActive = true;
  const r = canvas.getBoundingClientRect();
  touchX = (e.touches[0].clientX - r.left) * (canvas.width / r.width);
}, { passive: false });
canvas.addEventListener('touchmove', e => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  touchX = (e.touches[0].clientX - r.left) * (canvas.width / r.width);
}, { passive: false });
canvas.addEventListener('touchend', e => { e.preventDefault(); touchActive = false; }, { passive: false });

// ── Update ──────────────────────────────────────────────────────
function update() {
  frameCount++;
  // Animate stars regardless of state
  stars.forEach(s => {
    s.y += s.speed + speed * 0.15;
    if (s.y > canvas.height) { s.y = 0; s.x = Math.random() * canvas.width; }
  });
  if (state !== 'playing') return;

  // Orb movement — keyboard, mouse, touch
  if (keys['ArrowLeft'] || keys['a'] || keys['A']) orb.x -= 5.5;
  if (keys['ArrowRight'] || keys['d'] || keys['D']) orb.x += 5.5;
  if (mouseX !== null) orb.x += (mouseX - orb.x) * 0.12;
  if (touchActive) orb.x += (touchX - orb.x) * 0.15;
  orb.x = Math.max(ORB_R, Math.min(canvas.width - ORB_R, orb.x));

  // Trail
  trail.unshift({ x: orb.x, y: orb.y });
  if (trail.length > TRAIL_LEN) trail.pop();

  // Gates scroll down
  gates.forEach(g => { g.y += speed; });

  // Gate collision & passing
  for (let i = gates.length - 1; i >= 0; i--) {
    const g = gates[i];
    const wL = g.gapCenter - g.gapWidth / 2, wR = g.gapCenter + g.gapWidth / 2;
    const thick = 8;
    // Collision — orb overlaps gate row and is outside gap
    if (!g.passed && Math.abs(orb.y - g.y) < ORB_R + thick / 2) {
      if (orb.x - ORB_R < wL || orb.x + ORB_R > wR) {
        spawnParticles(orb.x, orb.y, C.pink, 35);
        spawnParticles(orb.x, orb.y, C.orange, 20);
        state = 'gameover';
        document.getElementById('final-score').textContent = score;
        document.getElementById('final-gates').textContent = gatesPassed;
        document.getElementById('game-over-screen').style.display = 'flex';
        return;
      }
    }
    // Gate passed — orb cleared below
    if (!g.passed && g.y < orb.y - ORB_R - thick) {
      g.passed = true; gatesPassed++; score += 10;
      // Close-call bonus
      if (!g.closeCalled) {
        const closest = Math.min(Math.abs(orb.x - wL), Math.abs(orb.x - wR));
        if (closest < CC_DIST + ORB_R) {
          g.closeCalled = true; score += 50; closeCallTimer = 45;
          spawnParticles(orb.x, orb.y - 20, C.green, 12);
        }
      }
      // Speed ramp every 5 gates
      if (gatesPassed % 5 === 0) {
        speed += 0.1; speedMult = parseFloat((speed / 3).toFixed(1));
      }
    }
    if (g.y > canvas.height + 40) gates.splice(i, 1);
  }

  // Spawn gates at top
  const topY = gates.reduce((min, g) => Math.min(min, g.y), canvas.height);
  if (topY > -GATE_SPACE + speed) gates.push(createGate(topY - GATE_SPACE));

  // Particles update
  particles.forEach(p => {
    p.x += p.vx; p.y += p.vy; p.life -= p.decay; p.vx *= 0.96; p.vy *= 0.96;
  });
  particles = particles.filter(p => p.life > 0);
  if (closeCallTimer > 0) closeCallTimer--;
  updateHUD();
}

// ── Draw ────────────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Stars
  stars.forEach(s => {
    const b = 0.3 + Math.sin(frameCount * 0.02 + s.flicker * 8) * 0.25;
    ctx.fillStyle = `rgba(228,228,240,${b})`;
    ctx.fillRect(s.x, s.y, s.size, s.size);
  });
  if (state !== 'playing' && state !== 'gameover') return;

  // Gates — gradient walls with cyan edge markers
  gates.forEach(g => {
    const wL = g.gapCenter - g.gapWidth / 2, wR = g.gapCenter + g.gapWidth / 2;
    const t = 8, glow = g.passed ? 0 : 6;
    ctx.save();
    ctx.shadowColor = C.purple; ctx.shadowBlur = glow;
    // Left wall gradient
    const gL = ctx.createLinearGradient(0, g.y, wL, g.y);
    gL.addColorStop(0, 'rgba(167,139,250,0.15)'); gL.addColorStop(1, C.purple);
    ctx.fillStyle = gL; ctx.fillRect(0, g.y - t / 2, wL, t);
    // Right wall gradient
    const gR = ctx.createLinearGradient(wR, g.y, canvas.width, g.y);
    gR.addColorStop(0, C.purple); gR.addColorStop(1, 'rgba(167,139,250,0.15)');
    ctx.fillStyle = gR; ctx.fillRect(wR, g.y - t / 2, canvas.width - wR, t);
    // Cyan edge dots
    ctx.fillStyle = C.cyan; ctx.shadowColor = C.cyan; ctx.shadowBlur = 8;
    ctx.beginPath(); ctx.arc(wL, g.y, 3, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(wR, g.y, 3, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  });

  // Trail — fading circles
  trail.forEach((t, i) => {
    const alpha = (1 - i / TRAIL_LEN) * 0.5;
    const r = ORB_R * (1 - i / TRAIL_LEN) * 0.7;
    ctx.save(); ctx.globalAlpha = alpha;
    ctx.fillStyle = C.cyan; ctx.shadowColor = C.cyan; ctx.shadowBlur = 6;
    ctx.beginPath(); ctx.arc(t.x, t.y, r, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  });

  // Orb — layered glow effect
  if (state === 'playing') {
    ctx.save();
    // Outer halo
    const og = ctx.createRadialGradient(orb.x, orb.y, ORB_R * 0.5, orb.x, orb.y, ORB_R * 2.5);
    og.addColorStop(0, 'rgba(103,232,249,0.3)'); og.addColorStop(1, 'rgba(103,232,249,0)');
    ctx.fillStyle = og;
    ctx.beginPath(); ctx.arc(orb.x, orb.y, ORB_R * 2.5, 0, Math.PI * 2); ctx.fill();
    // Core
    ctx.shadowColor = C.cyan; ctx.shadowBlur = 20;
    const cg = ctx.createRadialGradient(orb.x - 3, orb.y - 3, 2, orb.x, orb.y, ORB_R);
    cg.addColorStop(0, '#ffffff'); cg.addColorStop(0.4, C.cyan); cg.addColorStop(1, C.purple);
    ctx.fillStyle = cg;
    ctx.beginPath(); ctx.arc(orb.x, orb.y, ORB_R, 0, Math.PI * 2); ctx.fill();
    // Highlight
    ctx.fillStyle = 'rgba(255,255,255,0.6)';
    ctx.beginPath(); ctx.arc(orb.x - 3, orb.y - 4, 4, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }

  // Particles
  particles.forEach(p => {
    ctx.save(); ctx.globalAlpha = p.life; ctx.fillStyle = p.color;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  });

  // Close-call flash
  document.getElementById('close-call-flash').style.opacity = closeCallTimer > 0 ? '1' : '0';
}

// ── HUD & Loop ──────────────────────────────────────────────────
function updateHUD() {
  document.getElementById('score-display').textContent = 'SCORE: ' + score;
  document.getElementById('speed-display').textContent = 'x' + speedMult.toFixed(1);
}

function gameLoop() { update(); draw(); requestAnimationFrame(gameLoop); }

// ── Start / Restart ─────────────────────────────────────────────
function startGame() {
  state = 'playing'; score = 0; gatesPassed = 0; speed = 3; speedMult = 1;
  orb = createOrb(); gates = []; trail = []; particles = []; closeCallTimer = 0;
  initStars();
  for (let i = 1; i <= 4; i++) gates.push(createGate(orb.y - GATE_SPACE * i));
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('game-over-screen').style.display = 'none';
  updateHUD();
}

document.getElementById('start-btn').addEventListener('click', startGame);
document.getElementById('restart-btn').addEventListener('click', startGame);
initStars();
gameLoop();
