// Synth Swarm — Boid Flocking Simulation Game
// Built by Nova Forge + Amazon Nova Premier

(() => {
"use strict";

// ── Constants ──────────────────────────────────────────────────────────
const WORLD_W = 2000, WORLD_H = 2000;
const INITIAL_BOIDS = 200;
const MIN_BOIDS = 20;
const MAX_BOIDS = 400;
const BOID_MAX_SPEED = 3.2;
const BOID_MAX_FORCE = 0.08;
const PLAYER_ACCEL = 0.35;
const PLAYER_FRICTION = 0.96;
const PLAYER_MAX_SPEED = 4.5;
const NUM_CRYSTALS = 15;
const NUM_PREDATORS = 4;
const NUM_PORTALS = 2;
const PREDATOR_SPEED = 2.0;
const PREDATOR_SCATTER_RADIUS = 100;
const CRYSTAL_RADIUS = 18;
const PORTAL_RADIUS = 50;
const COLLECTION_RADIUS = 60;
const BOID_PERCEPTION = 80;
const PLAYER_ATTRACT_RADIUS = 250;

// ── Brand Colors ───────────────────────────────────────────────────────
const COL_BG       = "#0a0a12";
const COL_BOID     = [167, 139, 250]; // #a78bfa
const COL_PLAYER   = "#67e8f9";
const COL_CRYSTAL  = "#4ade80";
const COL_PREDATOR = "#f87171";
const COL_PORTAL   = "#facc15";
const COL_OBSTACLE = "rgba(42, 42, 62, 0.6)";
const COL_TEXT     = "#e4e4f0";
const COL_GRID     = "rgba(167, 139, 250, 0.06)";

// ── DOM Elements ───────────────────────────────────────────────────────
const canvas    = document.getElementById("game-canvas");
const ctx       = canvas.getContext("2d");
const minimap   = document.getElementById("minimap");
const mctx      = minimap.getContext("2d");
const hudSwarm  = document.getElementById("swarm-display");
const hudEnergy = document.getElementById("energy-display");
const hudScore  = document.getElementById("score-display");
const startScreen    = document.getElementById("start-screen");
const gameoverScreen = document.getElementById("gameover-screen");

// ── State ──────────────────────────────────────────────────────────────
let boids = [], crystals = [], predators = [], portals = [], obstacles = [];
let player = { x: WORLD_W / 2, y: WORLD_H / 2, vx: 0, vy: 0 };
let camera = { x: 0, y: 0 };
let keys = {};
let energy = 0, score = 0, crystalsCollected = 0, peakSwarm = INITIAL_BOIDS;
let running = false, gameOver = false;
let tick = 0;

// ── Utility ────────────────────────────────────────────────────────────
function rand(min, max) { return Math.random() * (max - min) + min; }
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function clampVec(vx, vy, max) {
    const m = Math.hypot(vx, vy);
    if (m > max) { const s = max / m; return [vx * s, vy * s]; }
    return [vx, vy];
}
function wrapColor(base, variance) {
    return `rgb(${base[0] + rand(-variance, variance)|0}, ${base[1] + rand(-variance, variance)|0}, ${base[2] + rand(-variance, variance)|0})`;
}

// ── Resize ─────────────────────────────────────────────────────────────
function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    minimap.width = 160;
    minimap.height = 160;
}
window.addEventListener("resize", resize);
resize();

// ── Input ──────────────────────────────────────────────────────────────
window.addEventListener("keydown", e => { keys[e.key] = true; });
window.addEventListener("keyup",   e => { keys[e.key] = false; });

// ── Initialization ─────────────────────────────────────────────────────
function spawnBoid(x, y) {
    return {
        x: x || rand(100, WORLD_W - 100),
        y: y || rand(100, WORLD_H - 100),
        vx: rand(-1, 1), vy: rand(-1, 1),
        ax: 0, ay: 0,
        color: wrapColor(COL_BOID, 20),
        alive: true
    };
}

function spawnCrystal() {
    return {
        x: rand(100, WORLD_W - 100),
        y: rand(100, WORLD_H - 100),
        pulse: rand(0, Math.PI * 2),
        alive: true
    };
}

function spawnPredator() {
    const cx = rand(200, WORLD_W - 200), cy = rand(200, WORLD_H - 200);
    const angle = rand(0, Math.PI * 2);
    return {
        x: cx, y: cy,
        vx: Math.cos(angle) * PREDATOR_SPEED,
        vy: Math.sin(angle) * PREDATOR_SPEED,
        patrolCx: cx, patrolCy: cy,
        patrolRadius: rand(150, 350),
        angle: angle
    };
}

function spawnPortal() {
    return {
        x: rand(200, WORLD_W - 200),
        y: rand(200, WORLD_H - 200),
        cooldown: 0,
        pulse: rand(0, Math.PI * 2)
    };
}

function generateObstacles() {
    obstacles = [];
    for (let i = 0; i < 8; i++) {
        obstacles.push({
            x: rand(150, WORLD_W - 150),
            y: rand(150, WORLD_H - 150),
            w: rand(60, 160),
            h: rand(60, 160)
        });
    }
}

function initGame() {
    boids = [];
    for (let i = 0; i < INITIAL_BOIDS; i++) boids.push(spawnBoid());
    crystals = [];
    for (let i = 0; i < NUM_CRYSTALS; i++) crystals.push(spawnCrystal());
    predators = [];
    for (let i = 0; i < NUM_PREDATORS; i++) predators.push(spawnPredator());
    portals = [];
    for (let i = 0; i < NUM_PORTALS; i++) portals.push(spawnPortal());
    generateObstacles();
    player = { x: WORLD_W / 2, y: WORLD_H / 2, vx: 0, vy: 0 };
    energy = 0; score = 0; crystalsCollected = 0; peakSwarm = INITIAL_BOIDS;
    gameOver = false; tick = 0;
}

// ── Boid Flocking ──────────────────────────────────────────────────────
function updateBoids() {
    const count = boids.length;
    for (let i = 0; i < count; i++) {
        const b = boids[i];
        if (!b.alive) continue;

        let sepX = 0, sepY = 0, sepN = 0;
        let aliX = 0, aliY = 0, aliN = 0;
        let cohX = 0, cohY = 0, cohN = 0;

        for (let j = 0; j < count; j++) {
            if (i === j || !boids[j].alive) continue;
            const o = boids[j];
            const d = dist(b, o);
            if (d < BOID_PERCEPTION && d > 0) {
                // Separation
                if (d < 30) {
                    sepX += (b.x - o.x) / d;
                    sepY += (b.y - o.y) / d;
                    sepN++;
                }
                // Alignment
                aliX += o.vx; aliY += o.vy; aliN++;
                // Cohesion
                cohX += o.x; cohY += o.y; cohN++;
            }
        }

        b.ax = 0; b.ay = 0;

        // Separation force
        if (sepN > 0) {
            sepX /= sepN; sepY /= sepN;
            const m = Math.hypot(sepX, sepY) || 1;
            b.ax += (sepX / m) * 1.8 * BOID_MAX_FORCE;
            b.ay += (sepY / m) * 1.8 * BOID_MAX_FORCE;
        }

        // Alignment force
        if (aliN > 0) {
            aliX /= aliN; aliY /= aliN;
            const m = Math.hypot(aliX, aliY) || 1;
            b.ax += (aliX / m - b.vx) * 1.0 * BOID_MAX_FORCE;
            b.ay += (aliY / m - b.vy) * 1.0 * BOID_MAX_FORCE;
        }

        // Cohesion force
        if (cohN > 0) {
            cohX /= cohN; cohY /= cohN;
            const dx = cohX - b.x, dy = cohY - b.y;
            const m = Math.hypot(dx, dy) || 1;
            b.ax += (dx / m) * 0.8 * BOID_MAX_FORCE;
            b.ay += (dy / m) * 0.8 * BOID_MAX_FORCE;
        }

        // Player attraction
        const dp = dist(b, player);
        if (dp < PLAYER_ATTRACT_RADIUS && dp > 0) {
            const dx = player.x - b.x, dy = player.y - b.y;
            const m = Math.hypot(dx, dy);
            const strength = 0.5 * (1 - dp / PLAYER_ATTRACT_RADIUS);
            b.ax += (dx / m) * strength * BOID_MAX_FORCE;
            b.ay += (dy / m) * strength * BOID_MAX_FORCE;
        }

        // Obstacle avoidance
        for (const obs of obstacles) {
            const cx = Math.max(obs.x, Math.min(b.x, obs.x + obs.w));
            const cy = Math.max(obs.y, Math.min(b.y, obs.y + obs.h));
            const od = Math.hypot(b.x - cx, b.y - cy);
            if (od < 40 && od > 0) {
                b.ax += (b.x - cx) / od * BOID_MAX_FORCE * 3;
                b.ay += (b.y - cy) / od * BOID_MAX_FORCE * 3;
            }
        }

        // Predator avoidance
        for (const p of predators) {
            const pd = dist(b, p);
            if (pd < PREDATOR_SCATTER_RADIUS && pd > 0) {
                b.ax += (b.x - p.x) / pd * BOID_MAX_FORCE * 4;
                b.ay += (b.y - p.y) / pd * BOID_MAX_FORCE * 4;
            }
        }

        // Apply forces
        b.vx += b.ax; b.vy += b.ay;
        [b.vx, b.vy] = clampVec(b.vx, b.vy, BOID_MAX_SPEED);
        b.x += b.vx; b.y += b.vy;

        // Border bounce
        if (b.x < 20)  { b.x = 20;  b.vx = Math.abs(b.vx) * 0.8; }
        if (b.x > WORLD_W - 20) { b.x = WORLD_W - 20; b.vx = -Math.abs(b.vx) * 0.8; }
        if (b.y < 20)  { b.y = 20;  b.vy = Math.abs(b.vy) * 0.8; }
        if (b.y > WORLD_H - 20) { b.y = WORLD_H - 20; b.vy = -Math.abs(b.vy) * 0.8; }
    }
}

// ── Player ─────────────────────────────────────────────────────────────
function updatePlayer() {
    if (keys["ArrowUp"]    || keys["w"] || keys["W"]) player.vy -= PLAYER_ACCEL;
    if (keys["ArrowDown"]  || keys["s"] || keys["S"]) player.vy += PLAYER_ACCEL;
    if (keys["ArrowLeft"]  || keys["a"] || keys["A"]) player.vx -= PLAYER_ACCEL;
    if (keys["ArrowRight"] || keys["d"] || keys["D"]) player.vx += PLAYER_ACCEL;

    player.vx *= PLAYER_FRICTION;
    player.vy *= PLAYER_FRICTION;
    [player.vx, player.vy] = clampVec(player.vx, player.vy, PLAYER_MAX_SPEED);
    player.x += player.vx;
    player.y += player.vy;

    // Clamp to world
    player.x = Math.max(20, Math.min(WORLD_W - 20, player.x));
    player.y = Math.max(20, Math.min(WORLD_H - 20, player.y));
}

// ── Camera ─────────────────────────────────────────────────────────────
function updateCamera() {
    const targetX = player.x - canvas.width / 2;
    const targetY = player.y - canvas.height / 2;
    camera.x += (targetX - camera.x) * 0.08;
    camera.y += (targetY - camera.y) * 0.08;
    camera.x = Math.max(0, Math.min(WORLD_W - canvas.width, camera.x));
    camera.y = Math.max(0, Math.min(WORLD_H - canvas.height, camera.y));
}

// ── Predators ──────────────────────────────────────────────────────────
function updatePredators() {
    for (const p of predators) {
        // Patrol in circular pattern
        p.angle += 0.012;
        const tx = p.patrolCx + Math.cos(p.angle) * p.patrolRadius;
        const ty = p.patrolCy + Math.sin(p.angle) * p.patrolRadius;
        const dx = tx - p.x, dy = ty - p.y;
        const m = Math.hypot(dx, dy) || 1;
        p.vx = (dx / m) * PREDATOR_SPEED;
        p.vy = (dy / m) * PREDATOR_SPEED;
        p.x += p.vx;
        p.y += p.vy;
        p.x = Math.max(20, Math.min(WORLD_W - 20, p.x));
        p.y = Math.max(20, Math.min(WORLD_H - 20, p.y));

        // Scatter boids on contact
        let killed = 0;
        for (let i = boids.length - 1; i >= 0; i--) {
            if (!boids[i].alive) continue;
            if (dist(boids[i], p) < 25) {
                boids[i].alive = false;
                killed++;
                if (killed >= 3) break; // max 3 per frame per predator
            }
        }
        if (killed > 0) {
            boids = boids.filter(b => b.alive);
        }
    }
}

// ── Crystal Collection ─────────────────────────────────────────────────
function updateCrystals() {
    for (const c of crystals) {
        if (!c.alive) continue;
        c.pulse += 0.05;
        if (dist(player, c) < COLLECTION_RADIUS) {
            // Count nearby boids for multiplier
            let nearby = 0;
            for (const b of boids) {
                if (dist(b, c) < COLLECTION_RADIUS * 1.5) nearby++;
            }
            const points = 10 + nearby * 2;
            energy += points;
            crystalsCollected++;
            c.alive = false;
            // Respawn after delay
            setTimeout(() => {
                c.x = rand(100, WORLD_W - 100);
                c.y = rand(100, WORLD_H - 100);
                c.alive = true;
            }, 3000);
        }
    }
}

// ── Portals ────────────────────────────────────────────────────────────
function updatePortals() {
    for (const p of portals) {
        p.pulse += 0.04;
        if (p.cooldown > 0) { p.cooldown--; continue; }
        if (dist(player, p) < PORTAL_RADIUS && boids.length < MAX_BOIDS) {
            const add = Math.min(15, MAX_BOIDS - boids.length);
            for (let i = 0; i < add; i++) {
                boids.push(spawnBoid(p.x + rand(-30, 30), p.y + rand(-30, 30)));
            }
            p.cooldown = 300; // 5 seconds at 60fps
            if (boids.length > peakSwarm) peakSwarm = boids.length;
        }
    }
}

// ── Score ──────────────────────────────────────────────────────────────
function updateScore() {
    const multiplier = 1 + boids.length / 100;
    score = Math.floor(energy * multiplier);
    hudSwarm.textContent  = `SWARM: ${boids.length}`;
    hudEnergy.textContent = `ENERGY: ${energy}`;
    hudScore.textContent  = `SCORE: ${score}`;
}

// ── Rendering ──────────────────────────────────────────────────────────
function drawGrid() {
    ctx.strokeStyle = COL_GRID;
    ctx.lineWidth = 1;
    const gs = 100;
    const sx = -(camera.x % gs), sy = -(camera.y % gs);
    for (let x = sx; x < canvas.width; x += gs) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
    }
    for (let y = sy; y < canvas.height; y += gs) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
    }
}

function drawBorders() {
    ctx.strokeStyle = "rgba(167, 139, 250, 0.25)";
    ctx.lineWidth = 3;
    ctx.strokeRect(-camera.x, -camera.y, WORLD_W, WORLD_H);
}

function drawObstacles() {
    ctx.fillStyle = COL_OBSTACLE;
    for (const o of obstacles) {
        ctx.fillRect(o.x - camera.x, o.y - camera.y, o.w, o.h);
        ctx.strokeStyle = "rgba(167, 139, 250, 0.15)";
        ctx.lineWidth = 1;
        ctx.strokeRect(o.x - camera.x, o.y - camera.y, o.w, o.h);
    }
}

function drawBoids() {
    for (const b of boids) {
        const sx = b.x - camera.x, sy = b.y - camera.y;
        if (sx < -20 || sx > canvas.width + 20 || sy < -20 || sy > canvas.height + 20) continue;
        const angle = Math.atan2(b.vy, b.vx);
        ctx.save();
        ctx.translate(sx, sy);
        ctx.rotate(angle);
        ctx.fillStyle = b.color;
        ctx.globalAlpha = 0.85;
        ctx.beginPath();
        ctx.moveTo(6, 0);
        ctx.lineTo(-4, -3.5);
        ctx.lineTo(-4, 3.5);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.restore();
    }
}

function drawPlayer() {
    const sx = player.x - camera.x, sy = player.y - camera.y;
    const angle = Math.atan2(player.vy, player.vx);

    // Glow
    ctx.save();
    ctx.shadowColor = COL_PLAYER;
    ctx.shadowBlur = 20;
    ctx.translate(sx, sy);
    ctx.rotate(angle);
    ctx.fillStyle = COL_PLAYER;
    ctx.beginPath();
    ctx.moveTo(12, 0);
    ctx.lineTo(-8, -7);
    ctx.lineTo(-4, 0);
    ctx.lineTo(-8, 7);
    ctx.closePath();
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.restore();

    // Attraction radius hint (faint)
    ctx.beginPath();
    ctx.arc(sx, sy, PLAYER_ATTRACT_RADIUS, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(103, 232, 249, 0.06)";
    ctx.lineWidth = 1;
    ctx.stroke();
}

function drawCrystals() {
    for (const c of crystals) {
        if (!c.alive) continue;
        const sx = c.x - camera.x, sy = c.y - camera.y;
        if (sx < -40 || sx > canvas.width + 40 || sy < -40 || sy > canvas.height + 40) continue;
        const s = CRYSTAL_RADIUS + Math.sin(c.pulse) * 4;

        ctx.save();
        ctx.shadowColor = COL_CRYSTAL;
        ctx.shadowBlur = 15 + Math.sin(c.pulse) * 5;
        ctx.fillStyle = COL_CRYSTAL;
        ctx.globalAlpha = 0.7 + Math.sin(c.pulse) * 0.2;
        // Diamond shape
        ctx.beginPath();
        ctx.moveTo(sx, sy - s);
        ctx.lineTo(sx + s * 0.6, sy);
        ctx.lineTo(sx, sy + s);
        ctx.lineTo(sx - s * 0.6, sy);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.shadowBlur = 0;
        ctx.restore();
    }
}

function drawPredators() {
    const t = tick * 0.08;
    for (const p of predators) {
        const sx = p.x - camera.x, sy = p.y - camera.y;
        if (sx < -40 || sx > canvas.width + 40 || sy < -40 || sy > canvas.height + 40) continue;

        ctx.save();
        ctx.shadowColor = COL_PREDATOR;
        ctx.shadowBlur = 12 + Math.sin(t) * 4;
        ctx.fillStyle = COL_PREDATOR;

        // Spiky shape
        const spikes = 5, outerR = 16, innerR = 8;
        ctx.beginPath();
        for (let i = 0; i < spikes * 2; i++) {
            const angle = (i * Math.PI) / spikes - Math.PI / 2 + t;
            const r = i % 2 === 0 ? outerR : innerR;
            const px = sx + Math.cos(angle) * r;
            const py = sy + Math.sin(angle) * r;
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        }
        ctx.closePath();
        ctx.fill();

        // Scatter radius hint
        ctx.beginPath();
        ctx.arc(sx, sy, PREDATOR_SCATTER_RADIUS, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(248, 113, 113, 0.08)";
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.shadowBlur = 0;
        ctx.restore();
    }
}

function drawPortals() {
    for (const p of portals) {
        const sx = p.x - camera.x, sy = p.y - camera.y;
        if (sx < -80 || sx > canvas.width + 80 || sy < -80 || sy > canvas.height + 80) continue;

        const active = p.cooldown === 0;
        const alpha = active ? 0.5 + Math.sin(p.pulse) * 0.2 : 0.15;

        ctx.save();
        if (active) {
            ctx.shadowColor = COL_PORTAL;
            ctx.shadowBlur = 20;
        }
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = COL_PORTAL;
        ctx.lineWidth = 2;

        // Concentric rings
        for (let r = PORTAL_RADIUS; r > 10; r -= 15) {
            ctx.beginPath();
            ctx.arc(sx, sy, r + Math.sin(p.pulse + r * 0.1) * 3, 0, Math.PI * 2);
            ctx.stroke();
        }

        // Label
        ctx.fillStyle = COL_PORTAL;
        ctx.globalAlpha = active ? 0.7 : 0.2;
        ctx.font = "9px 'Press Start 2P'";
        ctx.textAlign = "center";
        ctx.fillText(active ? "RECRUIT" : "RECHARGING", sx, sy + PORTAL_RADIUS + 16);

        ctx.globalAlpha = 1;
        ctx.shadowBlur = 0;
        ctx.restore();
    }
}

// ── Minimap ────────────────────────────────────────────────────────────
function drawMinimap() {
    const mw = minimap.width, mh = minimap.height;
    const sx = mw / WORLD_W, sy = mh / WORLD_H;

    mctx.clearRect(0, 0, mw, mh);
    mctx.fillStyle = "rgba(10, 10, 18, 0.8)";
    mctx.fillRect(0, 0, mw, mh);

    // Border
    mctx.strokeStyle = "rgba(167, 139, 250, 0.3)";
    mctx.lineWidth = 1;
    mctx.strokeRect(0, 0, mw, mh);

    // Viewport rectangle
    mctx.strokeStyle = "rgba(103, 232, 249, 0.3)";
    mctx.strokeRect(camera.x * sx, camera.y * sy, canvas.width * sx, canvas.height * sy);

    // Obstacles
    mctx.fillStyle = "rgba(42, 42, 62, 0.8)";
    for (const o of obstacles) {
        mctx.fillRect(o.x * sx, o.y * sy, o.w * sx, o.h * sy);
    }

    // Crystals
    mctx.fillStyle = COL_CRYSTAL;
    for (const c of crystals) {
        if (!c.alive) continue;
        mctx.fillRect(c.x * sx - 1, c.y * sy - 1, 3, 3);
    }

    // Predators
    mctx.fillStyle = COL_PREDATOR;
    for (const p of predators) {
        mctx.fillRect(p.x * sx - 2, p.y * sy - 2, 4, 4);
    }

    // Portals
    mctx.fillStyle = COL_PORTAL;
    for (const p of portals) {
        mctx.beginPath();
        mctx.arc(p.x * sx, p.y * sy, 3, 0, Math.PI * 2);
        mctx.fill();
    }

    // Boid cluster (sample for performance)
    mctx.fillStyle = "rgba(167, 139, 250, 0.6)";
    const step = Math.max(1, Math.floor(boids.length / 60));
    for (let i = 0; i < boids.length; i += step) {
        const b = boids[i];
        mctx.fillRect(b.x * sx, b.y * sy, 1.5, 1.5);
    }

    // Player
    mctx.fillStyle = COL_PLAYER;
    mctx.beginPath();
    mctx.arc(player.x * sx, player.y * sy, 3, 0, Math.PI * 2);
    mctx.fill();
}

// ── Game Loop ──────────────────────────────────────────────────────────
function update() {
    tick++;
    updatePlayer();
    updateBoids();
    updatePredators();
    updateCrystals();
    updatePortals();
    updateCamera();
    updateScore();

    // Check game over
    if (boids.length < MIN_BOIDS) {
        endGame();
        return;
    }
}

function render() {
    ctx.fillStyle = COL_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    drawGrid();
    drawBorders();
    drawObstacles();
    drawPortals();
    drawCrystals();
    drawBoids();
    drawPredators();
    drawPlayer();
    drawMinimap();
}

function loop() {
    if (!running) return;
    update();
    render();
    requestAnimationFrame(loop);
}

// ── Start / End ────────────────────────────────────────────────────────
function startGame() {
    initGame();
    startScreen.style.display = "none";
    gameoverScreen.style.display = "none";
    running = true;
    loop();
}

function endGame() {
    running = false;
    gameOver = true;
    document.getElementById("final-score").textContent = score;
    document.getElementById("final-energy").textContent = energy;
    document.getElementById("final-peak").textContent = peakSwarm;
    document.getElementById("final-crystals").textContent = crystalsCollected;
    gameoverScreen.style.display = "flex";
}

document.getElementById("start-btn").addEventListener("click", startGame);
document.getElementById("restart-btn").addEventListener("click", startGame);

// Draw idle swarm on start screen
function idleRender() {
    if (running) return;
    if (!boids.length) initGame();
    tick++;
    updateBoids();
    for (const p of predators) {
        p.angle += 0.012;
        p.x = p.patrolCx + Math.cos(p.angle) * p.patrolRadius;
        p.y = p.patrolCy + Math.sin(p.angle) * p.patrolRadius;
    }
    for (const c of crystals) c.pulse += 0.05;
    for (const p of portals) p.pulse += 0.04;

    camera.x = WORLD_W / 2 - canvas.width / 2 + Math.sin(tick * 0.005) * 100;
    camera.y = WORLD_H / 2 - canvas.height / 2 + Math.cos(tick * 0.003) * 80;

    render();
    requestAnimationFrame(idleRender);
}
idleRender();

})();
