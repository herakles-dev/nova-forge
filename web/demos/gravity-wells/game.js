/* =====================================================================
   Gravity Wells — Physics Puzzle Game
   Built by Nova Forge + Amazon Nova Premier
   ~500 LOC game logic
   ===================================================================== */

// --- Constants ---
const COLORS = {
    bg: '#0a0a12',
    particleStart: [167, 139, 250],   // #a78bfa
    particleEnd: [103, 232, 249],     // #67e8f9
    well: '#f472b6',
    target: '#4ade80',
    obstacle: '#2a2a3e',
    obstacleBorder: '#3d3d56',
    text: '#e4e4f0',
    trail: [167, 139, 250]
};

const G = 800;                 // gravitational constant (tuned for fun)
const PARTICLE_SPEED = 1.8;
const PARTICLE_RADIUS = 2.5;
const WELL_RADIUS = 14;
const TARGET_RADIUS = 40;
const TRAIL_LENGTH = 18;
const REQUIRED_SCORE = 20;
const MAX_PARTICLES = 200;
const SPAWN_RATE = 2;          // particles per frame

// --- Canvas setup ---
const canvas = document.getElementById('game-canvas');
const ctx = canvas.getContext('2d');
let W, H;

function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
}
window.addEventListener('resize', resize);
resize();

// --- DOM refs ---
const hudLevel = document.getElementById('level-display');
const hudScore = document.getElementById('score-display');
const hudWells = document.getElementById('wells-display');
const startScreen = document.getElementById('start-screen');
const levelCompleteScreen = document.getElementById('level-complete-screen');
const gameCompleteScreen = document.getElementById('game-complete-screen');
const startBtn = document.getElementById('start-btn');
const nextLevelBtn = document.getElementById('next-level-btn');
const replayBtn = document.getElementById('replay-btn');

// --- Game state ---
let state = 'menu'; // menu | playing | levelComplete | gameComplete
let currentLevel = 0;
let score = 0;
let totalScore = 0;
let particles = [];
let wells = [];
let explosions = [];
let frameCount = 0;

// --- Level definitions ---
const LEVELS = [
    { // Level 1 — straight shot, 1 wall, 3 wells
        wells: 3,
        sources: (W, H) => [{ x: 60, y: H / 2, angle: 0 }],
        target: (W, H) => ({ x: W - 80, y: H / 2 }),
        obstacles: (W, H) => [
            { x: W * 0.5, y: H * 0.35, w: 16, h: H * 0.18 }
        ],
        movingObstacles: []
    },
    { // Level 2 — wall in middle, curve required
        wells: 3,
        sources: (W, H) => [{ x: 60, y: H * 0.3, angle: 0 }],
        target: (W, H) => ({ x: W - 80, y: H * 0.7 }),
        obstacles: (W, H) => [
            { x: W * 0.38, y: H * 0.15, w: 16, h: H * 0.50 },
            { x: W * 0.62, y: H * 0.35, w: 16, h: H * 0.50 }
        ],
        movingObstacles: []
    },
    { // Level 3 — maze-like, 4 wells
        wells: 4,
        sources: (W, H) => [{ x: 60, y: H * 0.2, angle: 0 }],
        target: (W, H) => ({ x: W - 80, y: H * 0.8 }),
        obstacles: (W, H) => [
            { x: W * 0.25, y: 0, w: 16, h: H * 0.45 },
            { x: W * 0.50, y: H * 0.55, w: 16, h: H * 0.45 },
            { x: W * 0.75, y: 0, w: 16, h: H * 0.40 }
        ],
        movingObstacles: []
    },
    { // Level 4 — two sources, 4 wells
        wells: 4,
        sources: (W, H) => [
            { x: 60, y: H * 0.25, angle: 0 },
            { x: 60, y: H * 0.75, angle: 0 }
        ],
        target: (W, H) => ({ x: W - 80, y: H / 2 }),
        obstacles: (W, H) => [
            { x: W * 0.35, y: H * 0.40, w: W * 0.12, h: 16 },
            { x: W * 0.55, y: H * 0.20, w: 16, h: H * 0.25 },
            { x: W * 0.55, y: H * 0.55, w: 16, h: H * 0.25 }
        ],
        movingObstacles: []
    },
    { // Level 5 — moving obstacles, 5 wells
        wells: 5,
        sources: (W, H) => [
            { x: 60, y: H * 0.3, angle: 0 },
            { x: 60, y: H * 0.7, angle: 0 }
        ],
        target: (W, H) => ({ x: W - 80, y: H / 2 }),
        obstacles: (W, H) => [
            { x: W * 0.4, y: H * 0.1, w: 16, h: H * 0.25 }
        ],
        movingObstacles: [
            { x: W * 0.5, y: H * 0.5, w: 16, h: H * 0.18, speed: 60, axis: 'y', range: H * 0.3 },
            { x: W * 0.7, y: H * 0.3, w: 16, h: H * 0.15, speed: 45, axis: 'y', range: H * 0.25 }
        ]
    }
];

// --- Resolved level data ---
let levelData = {};

function loadLevel(idx) {
    const def = LEVELS[idx];
    levelData = {
        maxWells: def.wells,
        sources: def.sources(W, H),
        target: def.target(W, H),
        obstacles: def.obstacles(W, H),
        movingObstacles: (def.movingObstacles || []).map(o => ({
            ...o,
            baseX: o.axis === 'x' ? o.x : o.x,
            baseY: o.axis === 'y' ? o.y : o.y,
            x: typeof o.x === 'function' ? o.x(W, H) : o.x,
            y: typeof o.y === 'function' ? o.y(W, H) : o.y
        }))
    };
    particles = [];
    wells = [];
    explosions = [];
    score = 0;
    frameCount = 0;
    updateHUD();
}

// --- Particle color interpolation ---
function lerpColor(t) {
    const r = COLORS.particleStart[0] + (COLORS.particleEnd[0] - COLORS.particleStart[0]) * t;
    const g = COLORS.particleStart[1] + (COLORS.particleEnd[1] - COLORS.particleStart[1]) * t;
    const b = COLORS.particleStart[2] + (COLORS.particleEnd[2] - COLORS.particleStart[2]) * t;
    return [Math.round(r), Math.round(g), Math.round(b)];
}

// --- Particle ---
function createParticle(source) {
    const spread = (Math.random() - 0.5) * 0.6;
    const angle = source.angle + spread;
    const t = Math.random();
    const [r, g, b] = lerpColor(t);
    return {
        x: source.x,
        y: source.y,
        vx: Math.cos(angle) * PARTICLE_SPEED,
        vy: Math.sin(angle) * PARTICLE_SPEED,
        color: `rgb(${r},${g},${b})`,
        r: r, g: g, b: b,
        trail: [],
        alive: true
    };
}

// --- Explosion ---
function createExplosion(x, y, r, g, b) {
    const sparks = [];
    for (let i = 0; i < 10; i++) {
        const angle = Math.random() * Math.PI * 2;
        const speed = 1 + Math.random() * 3;
        sparks.push({
            x, y,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            life: 1.0,
            r, g, b
        });
    }
    explosions.push(...sparks);
}

// --- Collision helpers ---
function rectContains(rect, px, py) {
    return px >= rect.x && px <= rect.x + rect.w &&
           py >= rect.y && py <= rect.y + rect.h;
}

function circleContains(cx, cy, radius, px, py) {
    const dx = px - cx;
    const dy = py - cy;
    return dx * dx + dy * dy <= radius * radius;
}

function particleHitsRect(p, rect) {
    // Closest point on rect to particle center
    const cx = Math.max(rect.x, Math.min(p.x, rect.x + rect.w));
    const cy = Math.max(rect.y, Math.min(p.y, rect.y + rect.h));
    const dx = p.x - cx;
    const dy = p.y - cy;
    return dx * dx + dy * dy <= PARTICLE_RADIUS * PARTICLE_RADIUS;
}

// --- Physics update ---
function updateParticles(dt) {
    for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        if (!p.alive) continue;

        // Record trail
        p.trail.push({ x: p.x, y: p.y });
        if (p.trail.length > TRAIL_LENGTH) p.trail.shift();

        // Gravity from wells
        for (const w of wells) {
            const dx = w.x - p.x;
            const dy = w.y - p.y;
            const distSq = dx * dx + dy * dy;
            const dist = Math.sqrt(distSq);
            if (dist < 6) continue; // avoid singularity
            const force = G / distSq;
            p.vx += (dx / dist) * force * dt;
            p.vy += (dy / dist) * force * dt;
        }

        // Speed cap
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        if (speed > 8) {
            p.vx = (p.vx / speed) * 8;
            p.vy = (p.vy / speed) * 8;
        }

        // Move
        p.x += p.vx;
        p.y += p.vy;

        // Check obstacle collisions (static)
        let hitObstacle = false;
        for (const obs of levelData.obstacles) {
            if (particleHitsRect(p, obs)) {
                hitObstacle = true;
                break;
            }
        }
        // Moving obstacles
        if (!hitObstacle) {
            for (const obs of levelData.movingObstacles) {
                if (particleHitsRect(p, obs)) {
                    hitObstacle = true;
                    break;
                }
            }
        }

        if (hitObstacle) {
            createExplosion(p.x, p.y, p.r, p.g, p.b);
            particles.splice(i, 1);
            continue;
        }

        // Check target zone
        const t = levelData.target;
        if (circleContains(t.x, t.y, TARGET_RADIUS, p.x, p.y)) {
            score++;
            createExplosion(p.x, p.y, 74, 222, 128); // green burst
            particles.splice(i, 1);
            if (score >= REQUIRED_SCORE) {
                onLevelComplete();
            }
            continue;
        }

        // Out of bounds
        if (p.x < -50 || p.x > W + 50 || p.y < -50 || p.y > H + 50) {
            particles.splice(i, 1);
        }
    }
}

function updateMovingObstacles() {
    for (const obs of levelData.movingObstacles) {
        const t = frameCount * 0.016; // ~60fps approximation
        if (obs.axis === 'y') {
            obs.y = obs.baseY + Math.sin(t * obs.speed * 0.02) * obs.range * 0.5;
        } else {
            obs.x = obs.baseX + Math.sin(t * obs.speed * 0.02) * obs.range * 0.5;
        }
    }
}

function updateExplosions() {
    for (let i = explosions.length - 1; i >= 0; i--) {
        const s = explosions[i];
        s.x += s.vx;
        s.y += s.vy;
        s.life -= 0.04;
        if (s.life <= 0) explosions.splice(i, 1);
    }
}

// --- Spawn ---
function spawnParticles() {
    if (particles.length >= MAX_PARTICLES) return;
    for (const src of levelData.sources) {
        for (let i = 0; i < SPAWN_RATE; i++) {
            if (particles.length < MAX_PARTICLES) {
                particles.push(createParticle(src));
            }
        }
    }
}

// --- Rendering ---
function drawBackground() {
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, W, H);

    // Subtle star field (static — based on deterministic seed)
    ctx.fillStyle = 'rgba(228, 228, 240, 0.15)';
    for (let i = 0; i < 80; i++) {
        const sx = ((i * 7919 + 3571) % W);
        const sy = ((i * 6271 + 1033) % H);
        const size = (i % 3 === 0) ? 1.5 : 0.8;
        ctx.beginPath();
        ctx.arc(sx, sy, size, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawSources() {
    for (const src of levelData.sources) {
        ctx.save();
        const pulse = 1 + Math.sin(frameCount * 0.08) * 0.2;
        const grad = ctx.createRadialGradient(src.x, src.y, 0, src.x, src.y, 20 * pulse);
        grad.addColorStop(0, 'rgba(167, 139, 250, 0.8)');
        grad.addColorStop(1, 'rgba(167, 139, 250, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(src.x, src.y, 20 * pulse, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#a78bfa';
        ctx.beginPath();
        ctx.arc(src.x, src.y, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }
}

function drawTarget() {
    const t = levelData.target;
    const pulse = 1 + Math.sin(frameCount * 0.06) * 0.15;
    const r = TARGET_RADIUS * pulse;

    // Outer glow
    const grad = ctx.createRadialGradient(t.x, t.y, r * 0.3, t.x, t.y, r * 1.8);
    grad.addColorStop(0, 'rgba(74, 222, 128, 0.25)');
    grad.addColorStop(1, 'rgba(74, 222, 128, 0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(t.x, t.y, r * 1.8, 0, Math.PI * 2);
    ctx.fill();

    // Ring
    ctx.strokeStyle = COLORS.target;
    ctx.lineWidth = 2;
    ctx.globalAlpha = 0.6 + Math.sin(frameCount * 0.06) * 0.3;
    ctx.beginPath();
    ctx.arc(t.x, t.y, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;

    // Center dot
    ctx.fillStyle = COLORS.target;
    ctx.beginPath();
    ctx.arc(t.x, t.y, 4, 0, Math.PI * 2);
    ctx.fill();
}

function drawObstacles() {
    const allObs = [...levelData.obstacles, ...levelData.movingObstacles];
    for (const obs of allObs) {
        ctx.fillStyle = COLORS.obstacle;
        ctx.strokeStyle = COLORS.obstacleBorder;
        ctx.lineWidth = 1;
        ctx.fillRect(obs.x, obs.y, obs.w, obs.h);
        ctx.strokeRect(obs.x, obs.y, obs.w, obs.h);
    }
}

function drawParticles() {
    for (const p of particles) {
        // Trail
        for (let i = 0; i < p.trail.length; i++) {
            const alpha = (i / p.trail.length) * 0.4;
            const size = PARTICLE_RADIUS * (i / p.trail.length) * 0.8;
            ctx.fillStyle = `rgba(${p.r},${p.g},${p.b},${alpha})`;
            ctx.beginPath();
            ctx.arc(p.trail[i].x, p.trail[i].y, size, 0, Math.PI * 2);
            ctx.fill();
        }

        // Particle body
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, PARTICLE_RADIUS, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawWells() {
    for (const w of wells) {
        const pulse = 1 + Math.sin(frameCount * 0.1 + w.phase) * 0.25;
        const r = WELL_RADIUS * pulse;

        // Outer glow
        const grad = ctx.createRadialGradient(w.x, w.y, 0, w.x, w.y, r * 3);
        grad.addColorStop(0, 'rgba(244, 114, 182, 0.3)');
        grad.addColorStop(0.5, 'rgba(244, 114, 182, 0.08)');
        grad.addColorStop(1, 'rgba(244, 114, 182, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(w.x, w.y, r * 3, 0, Math.PI * 2);
        ctx.fill();

        // Gravity field rings
        ctx.strokeStyle = 'rgba(244, 114, 182, 0.12)';
        ctx.lineWidth = 1;
        for (let ring = 1; ring <= 3; ring++) {
            const rr = r * ring * 1.5 + Math.sin(frameCount * 0.04 + ring) * 4;
            ctx.beginPath();
            ctx.arc(w.x, w.y, rr, 0, Math.PI * 2);
            ctx.stroke();
        }

        // Core
        ctx.fillStyle = COLORS.well;
        ctx.shadowColor = COLORS.well;
        ctx.shadowBlur = 15 * pulse;
        ctx.beginPath();
        ctx.arc(w.x, w.y, 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Ring
        ctx.strokeStyle = COLORS.well;
        ctx.lineWidth = 1.5;
        ctx.globalAlpha = 0.7;
        ctx.beginPath();
        ctx.arc(w.x, w.y, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
    }
}

function drawExplosions() {
    for (const s of explosions) {
        ctx.fillStyle = `rgba(${s.r},${s.g},${s.b},${s.life})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, 2 * s.life, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawPlacementPreview() {
    if (state !== 'playing' || wells.length >= levelData.maxWells) return;
    if (mouseX < 0 || mouseY < 0) return;

    ctx.strokeStyle = 'rgba(244, 114, 182, 0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.arc(mouseX, mouseY, WELL_RADIUS, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);

    // Range indicator
    ctx.strokeStyle = 'rgba(244, 114, 182, 0.08)';
    ctx.beginPath();
    ctx.arc(mouseX, mouseY, WELL_RADIUS * 4.5, 0, Math.PI * 2);
    ctx.stroke();
}

// --- HUD ---
function updateHUD() {
    hudLevel.textContent = `LEVEL ${currentLevel + 1}`;
    hudScore.textContent = `${score} / ${REQUIRED_SCORE}`;
    const remaining = levelData.maxWells ? levelData.maxWells - wells.length : 0;
    hudWells.textContent = `WELLS: ${remaining}`;
}

// --- Level transitions ---
function onLevelComplete() {
    state = 'levelComplete';
    totalScore += score;
    document.getElementById('level-score').textContent = score;

    if (currentLevel >= LEVELS.length - 1) {
        // Game complete
        state = 'gameComplete';
        document.getElementById('total-score').textContent = totalScore;
        gameCompleteScreen.style.display = 'flex';
    } else {
        levelCompleteScreen.style.display = 'flex';
    }
}

function startLevel(idx) {
    currentLevel = idx;
    loadLevel(idx);
    state = 'playing';
    startScreen.style.display = 'none';
    levelCompleteScreen.style.display = 'none';
    gameCompleteScreen.style.display = 'none';
}

// --- Input ---
let mouseX = -100, mouseY = -100;

canvas.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
});

canvas.addEventListener('mouseleave', () => {
    mouseX = -100;
    mouseY = -100;
});

canvas.addEventListener('click', (e) => {
    if (state !== 'playing') return;
    if (wells.length >= levelData.maxWells) return;

    const x = e.clientX;
    const y = e.clientY;

    // Don't place on obstacles
    for (const obs of [...levelData.obstacles, ...levelData.movingObstacles]) {
        if (rectContains(obs, x, y)) return;
    }
    // Don't place on sources or target
    for (const src of levelData.sources) {
        if (circleContains(src.x, src.y, 30, x, y)) return;
    }
    if (circleContains(levelData.target.x, levelData.target.y, TARGET_RADIUS + 10, x, y)) return;

    wells.push({ x, y, phase: Math.random() * Math.PI * 2 });
    updateHUD();
});

canvas.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (state !== 'playing') return;
    if (wells.length > 0) {
        wells.pop();
        updateHUD();
    }
});

// --- Touch support ---
canvas.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (state !== 'playing') return;
    if (wells.length >= levelData.maxWells) return;

    const touch = e.touches[0];
    const x = touch.clientX;
    const y = touch.clientY;

    for (const obs of [...levelData.obstacles, ...levelData.movingObstacles]) {
        if (rectContains(obs, x, y)) return;
    }
    for (const src of levelData.sources) {
        if (circleContains(src.x, src.y, 30, x, y)) return;
    }
    if (circleContains(levelData.target.x, levelData.target.y, TARGET_RADIUS + 10, x, y)) return;

    wells.push({ x, y, phase: Math.random() * Math.PI * 2 });
    updateHUD();
}, { passive: false });

// --- Buttons ---
startBtn.addEventListener('click', () => startLevel(0));
nextLevelBtn.addEventListener('click', () => startLevel(currentLevel + 1));
replayBtn.addEventListener('click', () => {
    totalScore = 0;
    startLevel(0);
});

// --- Main loop ---
function gameLoop() {
    frameCount++;

    if (state === 'playing') {
        spawnParticles();
        updateMovingObstacles();
        updateParticles(0.016);
        updateExplosions();
        updateHUD();
    }

    // Always render
    drawBackground();

    if (state === 'playing' || state === 'levelComplete' || state === 'gameComplete') {
        drawObstacles();
        drawSources();
        drawTarget();
        drawWells();
        drawParticles();
        drawExplosions();
        drawPlacementPreview();
    }

    requestAnimationFrame(gameLoop);
}

// --- Initialize ---
loadLevel(0);
gameLoop();
