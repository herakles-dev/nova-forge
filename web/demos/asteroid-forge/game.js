/* Asteroid Forge — game.js */
(function () {
    'use strict';

    const canvas = document.getElementById('game-canvas');
    const ctx = canvas.getContext('2d');

    /* --- Palette --- */
    const C = {
        bg: '#0a0a12', purple: '#a78bfa', cyan: '#67e8f9', green: '#4ade80',
        orange: '#fb923c', pink: '#f472b6', text: '#e4e4f0'
    };

    /* --- State --- */
    let W, H;
    let state = 'start'; // start | playing | upgrade | over | win
    let score = 0, wave = 1, resourcePct = 0;
    let fireRateLevel = 0, bulletSizeLevel = 0;
    let ship, asteroids, bullets, orbs, particles, stars;
    let keys = {};
    let lastShot = 0, fireCooldown = 280;
    let touchLeft = null, touchShoot = false;

    /* --- Resize --- */
    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
    }
    window.addEventListener('resize', resize);
    resize();

    /* --- Stars (parallax) --- */
    function createStars() {
        stars = [];
        for (let i = 0; i < 120; i++) {
            stars.push({
                x: Math.random() * W, y: Math.random() * H,
                r: Math.random() * 1.2 + 0.3,
                speed: Math.random() * 0.3 + 0.05,
                alpha: Math.random() * 0.5 + 0.2
            });
        }
    }
    createStars();

    function drawStars() {
        for (const s of stars) {
            ctx.globalAlpha = s.alpha;
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fill();
            s.y += s.speed;
            if (s.y > H) { s.y = 0; s.x = Math.random() * W; }
        }
        ctx.globalAlpha = 1;
    }

    /* --- Ship --- */
    function createShip() {
        return {
            x: W / 2, y: H / 2, angle: -Math.PI / 2,
            vx: 0, vy: 0, radius: 14, shield: true, invuln: 0, dead: false
        };
    }

    function drawShip(s) {
        if (s.dead) return;
        ctx.save();
        ctx.translate(s.x, s.y);
        ctx.rotate(s.angle);
        /* Ship body */
        ctx.strokeStyle = s.invuln > 0 && Math.floor(Date.now() / 80) % 2 ? '#fff' : C.cyan;
        ctx.lineWidth = 2;
        ctx.shadowColor = C.cyan;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.moveTo(20, 0);
        ctx.lineTo(-12, -11);
        ctx.lineTo(-6, 0);
        ctx.lineTo(-12, 11);
        ctx.closePath();
        ctx.stroke();
        /* Thrust flame */
        if (keys['ArrowUp'] || keys['w'] || (touchLeft && touchLeft.dy < -15)) {
            ctx.strokeStyle = C.orange;
            ctx.shadowColor = C.orange;
            ctx.beginPath();
            ctx.moveTo(-8, -5);
            ctx.lineTo(-18 - Math.random() * 8, 0);
            ctx.lineTo(-8, 5);
            ctx.stroke();
        }
        /* Shield ring */
        if (s.shield) {
            ctx.strokeStyle = C.cyan;
            ctx.globalAlpha = 0.35 + 0.15 * Math.sin(Date.now() / 200);
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.arc(0, 0, 22, 0, Math.PI * 2);
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        ctx.restore();
    }

    function updateShip(s, dt) {
        if (s.dead) return;
        const rotSpeed = 4.5 * dt;
        const thrust = 220 * dt;
        const friction = 0.988;

        if (keys['ArrowLeft'] || keys['a']) s.angle -= rotSpeed;
        if (keys['ArrowRight'] || keys['d']) s.angle += rotSpeed;
        if (keys['ArrowUp'] || keys['w']) {
            s.vx += Math.cos(s.angle) * thrust;
            s.vy += Math.sin(s.angle) * thrust;
        }
        /* Touch joystick */
        if (touchLeft) {
            if (touchLeft.dx < -15) s.angle -= rotSpeed;
            if (touchLeft.dx > 15) s.angle += rotSpeed;
            if (touchLeft.dy < -15) {
                s.vx += Math.cos(s.angle) * thrust;
                s.vy += Math.sin(s.angle) * thrust;
            }
        }

        s.vx *= friction; s.vy *= friction;
        s.x += s.vx * dt; s.y += s.vy * dt;
        /* Wrap */
        if (s.x < -20) s.x = W + 20;
        if (s.x > W + 20) s.x = -20;
        if (s.y < -20) s.y = H + 20;
        if (s.y > H + 20) s.y = -20;
        if (s.invuln > 0) s.invuln -= dt;
    }

    /* --- Asteroids --- */
    function randomShape(r) {
        const pts = [];
        const n = 7 + Math.floor(Math.random() * 5);
        for (let i = 0; i < n; i++) {
            const a = (Math.PI * 2 / n) * i;
            const rr = r * (0.7 + Math.random() * 0.5);
            pts.push({ x: Math.cos(a) * rr, y: Math.sin(a) * rr });
        }
        return pts;
    }

    function createAsteroid(x, y, size) {
        const radii = { large: 40, medium: 22, small: 12 };
        const r = radii[size];
        const speed = (size === 'large' ? 40 : size === 'medium' ? 70 : 110) * (1 + wave * 0.12);
        const a = Math.random() * Math.PI * 2;
        return {
            x, y, vx: Math.cos(a) * speed, vy: Math.sin(a) * speed,
            radius: r, size, shape: randomShape(r), rot: Math.random() * Math.PI * 2,
            rotSpeed: (Math.random() - 0.5) * 2
        };
    }

    function spawnAsteroids() {
        asteroids = [];
        const count = 3 + wave * 2;
        for (let i = 0; i < count; i++) {
            let x, y;
            do {
                x = Math.random() * W;
                y = Math.random() * H;
            } while (dist(x, y, ship.x, ship.y) < 140);
            asteroids.push(createAsteroid(x, y, 'large'));
        }
        /* Wave 4+ adds medium strays */
        if (wave >= 4) {
            for (let i = 0; i < wave - 2; i++) {
                let x, y;
                do { x = Math.random() * W; y = Math.random() * H; }
                while (dist(x, y, ship.x, ship.y) < 100);
                asteroids.push(createAsteroid(x, y, 'medium'));
            }
        }
    }

    function drawAsteroid(a) {
        ctx.save();
        ctx.translate(a.x, a.y);
        ctx.rotate(a.rot);
        ctx.strokeStyle = C.orange;
        ctx.lineWidth = 1.8;
        ctx.shadowColor = C.orange;
        ctx.shadowBlur = 6;
        ctx.beginPath();
        ctx.moveTo(a.shape[0].x, a.shape[0].y);
        for (let i = 1; i < a.shape.length; i++) ctx.lineTo(a.shape[i].x, a.shape[i].y);
        ctx.closePath();
        ctx.stroke();
        ctx.restore();
    }

    function updateAsteroid(a, dt) {
        a.x += a.vx * dt; a.y += a.vy * dt;
        a.rot += a.rotSpeed * dt;
        if (a.x < -50) a.x = W + 50;
        if (a.x > W + 50) a.x = -50;
        if (a.y < -50) a.y = H + 50;
        if (a.y > H + 50) a.y = -50;
    }

    /* --- Bullets --- */
    function shoot() {
        const now = Date.now();
        const cd = fireCooldown * Math.pow(0.7, fireRateLevel);
        if (now - lastShot < cd) return;
        lastShot = now;
        const bSize = 3 + bulletSizeLevel * 1.5;
        bullets.push({
            x: ship.x + Math.cos(ship.angle) * 22,
            y: ship.y + Math.sin(ship.angle) * 22,
            vx: Math.cos(ship.angle) * 480 + ship.vx * 0.3,
            vy: Math.sin(ship.angle) * 480 + ship.vy * 0.3,
            radius: bSize, life: 1.6
        });
    }

    function drawBullet(b) {
        ctx.fillStyle = C.pink;
        ctx.shadowColor = C.pink;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(b.x, b.y, b.radius, 0, Math.PI * 2);
        ctx.fill();
    }

    function updateBullet(b, dt) {
        b.x += b.vx * dt; b.y += b.vy * dt;
        b.life -= dt;
    }

    /* --- Resource Orbs --- */
    function createOrb(x, y) {
        const rng = Math.random();
        let color, value;
        if (rng < 0.6) { color = C.purple; value = 5; }
        else if (rng < 0.9) { color = C.cyan; value = 10; }
        else { color = C.green; value = 20; }
        const a = Math.random() * Math.PI * 2;
        return {
            x, y, vx: Math.cos(a) * 30, vy: Math.sin(a) * 30,
            color, value, radius: 5, life: 12, pulse: Math.random() * Math.PI * 2
        };
    }

    function drawOrb(o) {
        const glow = 0.6 + 0.4 * Math.sin(o.pulse);
        ctx.globalAlpha = Math.min(1, o.life * 2) * glow;
        ctx.fillStyle = o.color;
        ctx.shadowColor = o.color;
        ctx.shadowBlur = 12;
        ctx.beginPath();
        ctx.arc(o.x, o.y, o.radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
    }

    function updateOrb(o, dt) {
        o.pulse += dt * 4;
        o.life -= dt;
        o.vx *= 0.97; o.vy *= 0.97;
        /* Magnetic pull toward ship */
        const d = dist(o.x, o.y, ship.x, ship.y);
        if (d < 120 && !ship.dead) {
            const pull = 260 * dt / Math.max(d, 1);
            o.vx += (ship.x - o.x) * pull;
            o.vy += (ship.y - o.y) * pull;
        }
        o.x += o.vx * dt; o.y += o.vy * dt;
    }

    /* --- Particles --- */
    function spawnExplosion(x, y, color, count) {
        for (let i = 0; i < count; i++) {
            const a = Math.random() * Math.PI * 2;
            const s = Math.random() * 160 + 40;
            particles.push({
                x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
                life: Math.random() * 0.6 + 0.3, maxLife: 0.9,
                radius: Math.random() * 2.5 + 0.5, color
            });
        }
    }

    function drawParticle(p) {
        ctx.globalAlpha = Math.max(0, p.life / p.maxLife);
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
    }

    function updateParticle(p, dt) {
        p.x += p.vx * dt; p.y += p.vy * dt;
        p.vx *= 0.96; p.vy *= 0.96;
        p.life -= dt;
    }

    /* --- Utilities --- */
    function dist(x1, y1, x2, y2) {
        const dx = x1 - x2, dy = y1 - y2;
        return Math.sqrt(dx * dx + dy * dy);
    }

    /* --- Collisions --- */
    function checkCollisions() {
        /* Bullets vs Asteroids */
        for (let bi = bullets.length - 1; bi >= 0; bi--) {
            const b = bullets[bi];
            for (let ai = asteroids.length - 1; ai >= 0; ai--) {
                const a = asteroids[ai];
                if (dist(b.x, b.y, a.x, a.y) < a.radius + b.radius) {
                    bullets.splice(bi, 1);
                    splitAsteroid(ai);
                    break;
                }
            }
        }
        /* Ship vs Asteroids */
        if (!ship.dead && ship.invuln <= 0) {
            for (const a of asteroids) {
                if (dist(ship.x, ship.y, a.x, a.y) < ship.radius + a.radius) {
                    if (ship.shield) {
                        ship.shield = false;
                        ship.invuln = 1.5;
                        spawnExplosion(ship.x, ship.y, C.cyan, 15);
                        updateShieldHUD();
                    } else {
                        shipDestroyed();
                    }
                    break;
                }
            }
        }
        /* Ship vs Orbs */
        if (!ship.dead) {
            for (let i = orbs.length - 1; i >= 0; i--) {
                const o = orbs[i];
                if (dist(ship.x, ship.y, o.x, o.y) < ship.radius + o.radius + 8) {
                    collectOrb(o);
                    orbs.splice(i, 1);
                }
            }
        }
    }

    function splitAsteroid(idx) {
        const a = asteroids[idx];
        score += a.size === 'large' ? 20 : a.size === 'medium' ? 50 : 100;
        spawnExplosion(a.x, a.y, C.orange, a.size === 'large' ? 18 : 10);
        /* Drop orbs */
        const orbCount = a.size === 'large' ? 2 : a.size === 'medium' ? 2 : 1;
        for (let i = 0; i < orbCount; i++) orbs.push(createOrb(a.x, a.y));
        /* Split into children */
        const next = a.size === 'large' ? 'medium' : a.size === 'medium' ? 'small' : null;
        if (next) {
            for (let i = 0; i < 2; i++) {
                asteroids.push(createAsteroid(
                    a.x + (Math.random() - 0.5) * 20,
                    a.y + (Math.random() - 0.5) * 20,
                    next
                ));
            }
        }
        asteroids.splice(idx, 1);
        updateHUD();
    }

    function collectOrb(o) {
        resourcePct = Math.min(100, resourcePct + o.value);
        score += o.value;
        spawnExplosion(o.x, o.y, o.color, 6);
        updateHUD();
        /* Check wave complete */
        if (resourcePct >= 100 && asteroids.length === 0) {
            waveComplete();
        }
    }

    function shipDestroyed() {
        ship.dead = true;
        spawnExplosion(ship.x, ship.y, C.cyan, 30);
        spawnExplosion(ship.x, ship.y, C.pink, 20);
        setTimeout(gameOver, 1200);
    }

    /* --- Wave management --- */
    function waveComplete() {
        if (wave >= 5) {
            state = 'win';
            document.getElementById('win-score').textContent = score;
            document.getElementById('win-screen').style.display = '';
            return;
        }
        state = 'upgrade';
        document.getElementById('upgrade-screen').style.display = '';
    }

    function applyUpgrade(type) {
        if (type === 'firerate') fireRateLevel++;
        else if (type === 'bulletsize') bulletSizeLevel++;
        else if (type === 'shield') { ship.shield = true; updateShieldHUD(); }
        document.getElementById('upgrade-screen').style.display = 'none';
        wave++;
        resourcePct = 0;
        spawnAsteroids();
        state = 'playing';
        updateHUD();
    }

    function gameOver() {
        state = 'over';
        document.getElementById('final-score').textContent = score;
        document.getElementById('final-wave').textContent = wave;
        document.getElementById('game-over-screen').style.display = '';
    }

    /* --- HUD --- */
    function updateHUD() {
        document.getElementById('score-display').textContent = 'SCORE: ' + score;
        document.getElementById('wave-display').textContent = 'WAVE: ' + wave + '/5';
        document.getElementById('resource-bar').style.width = resourcePct + '%';
        document.getElementById('resource-text').textContent = Math.floor(resourcePct) + '%';
    }

    function updateShieldHUD() {
        const el = document.getElementById('shield-display');
        el.textContent = ship.shield ? 'SHIELD: ON' : 'SHIELD: OFF';
        el.className = ship.shield ? '' : 'off';
    }

    /* --- Init / Reset --- */
    function initGame() {
        ship = createShip();
        asteroids = []; bullets = []; orbs = []; particles = [];
        score = 0; wave = 1; resourcePct = 0;
        fireRateLevel = 0; bulletSizeLevel = 0;
        lastShot = 0;
        spawnAsteroids();
        updateHUD();
        updateShieldHUD();
        state = 'playing';
    }

    /* --- Main Loop --- */
    let lastTime = 0;
    function loop(ts) {
        requestAnimationFrame(loop);
        const dt = Math.min((ts - lastTime) / 1000, 0.05);
        lastTime = ts;

        /* Clear */
        ctx.fillStyle = C.bg;
        ctx.fillRect(0, 0, W, H);
        ctx.shadowBlur = 0;
        drawStars();

        if (state !== 'playing') return;

        /* Shooting */
        if (keys[' '] || keys['Space'] || touchShoot) shoot();

        /* Update */
        updateShip(ship, dt);
        asteroids.forEach(a => updateAsteroid(a, dt));
        bullets.forEach(b => updateBullet(b, dt));
        orbs.forEach(o => updateOrb(o, dt));
        particles.forEach(p => updateParticle(p, dt));

        /* Prune */
        bullets = bullets.filter(b => b.life > 0);
        orbs = orbs.filter(o => o.life > 0);
        particles = particles.filter(p => p.life > 0);

        /* Collisions */
        checkCollisions();

        /* Auto-complete wave: all asteroids gone and resources met */
        if (asteroids.length === 0 && resourcePct >= 100) {
            waveComplete();
        }

        /* Draw */
        asteroids.forEach(a => drawAsteroid(a));
        bullets.forEach(b => drawBullet(b));
        orbs.forEach(o => drawOrb(o));
        particles.forEach(p => drawParticle(p));
        drawShip(ship);
    }
    requestAnimationFrame(loop);

    /* --- Input --- */
    window.addEventListener('keydown', e => {
        keys[e.key] = true;
        if (e.key === ' ') e.preventDefault();
    });
    window.addEventListener('keyup', e => { keys[e.key] = false; });

    /* Touch controls */
    canvas.addEventListener('touchstart', e => {
        e.preventDefault();
        for (const t of e.changedTouches) {
            if (t.clientX < W / 2) {
                touchLeft = { id: t.identifier, sx: t.clientX, sy: t.clientY, dx: 0, dy: 0 };
            } else {
                touchShoot = true;
            }
        }
    }, { passive: false });

    canvas.addEventListener('touchmove', e => {
        e.preventDefault();
        for (const t of e.changedTouches) {
            if (touchLeft && t.identifier === touchLeft.id) {
                touchLeft.dx = t.clientX - touchLeft.sx;
                touchLeft.dy = t.clientY - touchLeft.sy;
            }
        }
    }, { passive: false });

    canvas.addEventListener('touchend', e => {
        for (const t of e.changedTouches) {
            if (touchLeft && t.identifier === touchLeft.id) touchLeft = null;
            else touchShoot = false;
        }
    });

    /* --- UI Buttons --- */
    document.getElementById('start-btn').addEventListener('click', () => {
        document.getElementById('start-screen').style.display = 'none';
        initGame();
    });

    document.getElementById('restart-btn').addEventListener('click', () => {
        document.getElementById('game-over-screen').style.display = 'none';
        initGame();
    });

    document.getElementById('win-restart-btn').addEventListener('click', () => {
        document.getElementById('win-screen').style.display = 'none';
        initGame();
    });

    document.querySelectorAll('.upgrade-card').forEach(card => {
        card.addEventListener('click', () => {
            applyUpgrade(card.dataset.upgrade);
        });
    });
})();
