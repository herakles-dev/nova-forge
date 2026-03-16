/* Forge Defense — Tower Defense Game Engine
   Built by Nova Forge + Amazon Nova Pro */

const COLS = 20, ROWS = 14, TILE = 40;
const W = COLS * TILE, H = ROWS * TILE;

// Path waypoints (grid coords) — winding S-curve
const PATH_COORDS = [
  [0,2],[1,2],[2,2],[3,2],[4,2],[5,2],[6,2],[7,2],
  [7,3],[7,4],[7,5],[7,6],
  [6,6],[5,6],[4,6],[3,6],[2,6],[1,6],
  [1,7],[1,8],[1,9],[1,10],
  [2,10],[3,10],[4,10],[5,10],[6,10],[7,10],[8,10],[9,10],[10,10],[11,10],
  [11,9],[11,8],[11,7],[11,6],[11,5],[11,4],
  [12,4],[13,4],[14,4],[15,4],
  [15,5],[15,6],[15,7],[15,8],
  [16,8],[17,8],[18,8],[19,8]
];

const PATH_SET = new Set(PATH_COORDS.map(([c,r]) => `${c},${r}`));
const PATH_PX = PATH_COORDS.map(([c,r]) => ({x: c*TILE+TILE/2, y: r*TILE+TILE/2}));

// Tower definitions
const TOWER_DEFS = {
  bolt:   {name:'Bolt',   color:'#67e8f9', cost:50,  range:120, damage:8,  rate:0.3,  special:'Fast single-target'},
  flame:  {name:'Flame',  color:'#fb923c', cost:75,  range:80,  damage:4,  rate:0.1,  special:'Area burn (60px)'},
  arc:    {name:'Arc',    color:'#a78bfa', cost:100, range:150, damage:12, rate:0.8,  special:'Chain 3 enemies'},
  frost:  {name:'Frost',  color:'#38bdf8', cost:80,  range:100, damage:5,  rate:0.6,  special:'Slows 40% for 2s'},
  cannon: {name:'Cannon', color:'#f472b6', cost:120, range:130, damage:25, rate:1.2,  special:'Splash (60px)'},
  sniper: {name:'Sniper', color:'#4ade80', cost:150, range:250, damage:40, rate:2.0,  special:'Instant, targets strongest'}
};

// Enemy definitions
const ENEMY_DEFS = {
  scout:   {name:'Scout',   color:'#ffffff', hp:30,  speed:2.5, reward:10, radius:6},
  soldier: {name:'Soldier', color:'#fb923c', hp:80,  speed:1.5, reward:15, radius:8},
  tank:    {name:'Tank',    color:'#ef4444', hp:200, speed:1.0, reward:30, radius:11},
  healer:  {name:'Healer',  color:'#4ade80', hp:60,  speed:1.5, reward:25, radius:7},
  flyer:   {name:'Flyer',   color:'#67e8f9', hp:50,  speed:2.0, reward:20, radius:6},
  boss:    {name:'Boss',    color:'#c084fc', hp:500, speed:0.8, reward:100,radius:14}
};

// Wave compositions (20 waves)
const WAVES = [
  [{type:'scout',count:6,delay:0.6}],
  [{type:'scout',count:8,delay:0.5}],
  [{type:'scout',count:5,delay:0.5},{type:'soldier',count:3,delay:0.8}],
  [{type:'soldier',count:6,delay:0.7}],
  [{type:'soldier',count:5,delay:0.6},{type:'tank',count:2,delay:1.2},{type:'boss',count:1,delay:2}],
  [{type:'scout',count:10,delay:0.3},{type:'soldier',count:4,delay:0.7}],
  [{type:'soldier',count:6,delay:0.5},{type:'healer',count:2,delay:1.0}],
  [{type:'tank',count:4,delay:1.0},{type:'healer',count:2,delay:0.8}],
  [{type:'flyer',count:5,delay:0.7},{type:'soldier',count:5,delay:0.6}],
  [{type:'tank',count:3,delay:1.0},{type:'flyer',count:4,delay:0.6},{type:'boss',count:1,delay:2}],
  [{type:'scout',count:12,delay:0.2},{type:'healer',count:3,delay:0.8}],
  [{type:'soldier',count:8,delay:0.4},{type:'tank',count:3,delay:1.0}],
  [{type:'flyer',count:6,delay:0.5},{type:'healer',count:3,delay:0.7}],
  [{type:'tank',count:5,delay:0.8},{type:'healer',count:3,delay:0.7}],
  [{type:'tank',count:4,delay:0.7},{type:'flyer',count:5,delay:0.5},{type:'boss',count:1,delay:2}],
  [{type:'scout',count:15,delay:0.15},{type:'soldier',count:8,delay:0.3}],
  [{type:'tank',count:6,delay:0.7},{type:'healer',count:4,delay:0.6}],
  [{type:'flyer',count:8,delay:0.4},{type:'tank',count:4,delay:0.8}],
  [{type:'soldier',count:10,delay:0.3},{type:'tank',count:5,delay:0.6},{type:'healer',count:4,delay:0.5}],
  [{type:'tank',count:6,delay:0.5},{type:'flyer',count:6,delay:0.4},{type:'healer',count:4,delay:0.5},{type:'boss',count:2,delay:2.5}]
];

// Game state
let state = 'menu'; // menu, playing, gameover, victory
let gold = 200, lives = 20, score = 0, wave = 0;
let speedMult = 1, selectedTower = null, selectedPlaced = null;
let towers = [], enemies = [], projectiles = [], particles = [];
let waveState = 'waiting'; // waiting, countdown, spawning, active
let countdownTimer = 0, spawnQueue = [], spawnTimer = 0;
let hoverTile = null;
let lastTime = 0;

const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
canvas.width = W; canvas.height = H;

// Grid occupancy
const grid = Array.from({length:ROWS}, () => Array(COLS).fill(0)); // 0=buildable, 1=path, 2=tower
PATH_COORDS.forEach(([c,r]) => { if(r>=0&&r<ROWS&&c>=0&&c<COLS) grid[r][c]=1; });

// --- Utility ---
function dist(a,b){ return Math.hypot(a.x-b.x, a.y-b.y); }
function lerp(a,b,t){ return a+(b-a)*t; }
function rand(a,b){ return Math.random()*(b-a)+a; }

// --- Enemy class ---
class Enemy {
  constructor(type, waveNum) {
    const def = ENEMY_DEFS[type];
    this.type = type;
    this.color = def.color;
    this.maxHp = def.hp * (1 + waveNum * 0.08);
    this.hp = this.maxHp;
    this.baseSpeed = def.speed;
    this.speed = def.speed;
    this.reward = def.reward;
    this.radius = def.radius;
    this.alive = true;
    this.slowTimer = 0;
    this.pathIdx = 0;
    this.pathT = 0;
    this.isFlyer = type === 'flyer';
    this.isBoss = type === 'boss';
    this.isHealer = type === 'healer';
    this.shieldRegen = this.isBoss ? 3 : 0;
    this.shieldTimer = 0;
    // Position
    if (this.isFlyer) {
      this.x = -10; this.y = rand(TILE, H-TILE);
      this.targetX = W+10; this.targetY = rand(TILE*3, H-TILE*3);
    } else {
      this.x = PATH_PX[0].x; this.y = PATH_PX[0].y;
    }
  }
  update(dt) {
    if (!this.alive) return;
    this.slowTimer = Math.max(0, this.slowTimer - dt);
    this.speed = this.slowTimer > 0 ? this.baseSpeed * 0.6 : this.baseSpeed;
    // Boss shield regen
    if (this.isBoss) {
      this.shieldTimer += dt;
      if (this.shieldTimer >= 1) {
        this.hp = Math.min(this.maxHp, this.hp + this.shieldRegen);
        this.shieldTimer = 0;
      }
    }
    // Healer: heal nearby enemies
    if (this.isHealer) {
      for (const e of enemies) {
        if (e !== this && e.alive && dist(this,e) < 80) {
          e.hp = Math.min(e.maxHp, e.hp + 2 * dt);
        }
      }
    }
    const moveSpeed = this.speed * TILE * dt;
    if (this.isFlyer) {
      const dx = this.targetX - this.x, dy = this.targetY - this.y;
      const d = Math.hypot(dx,dy);
      if (d < 5) { this.reachEnd(); return; }
      this.x += (dx/d)*moveSpeed;
      this.y += (dy/d)*moveSpeed;
    } else {
      if (this.pathIdx >= PATH_PX.length - 1) { this.reachEnd(); return; }
      const from = PATH_PX[this.pathIdx], to = PATH_PX[this.pathIdx+1];
      const segLen = dist(from, to);
      this.pathT += moveSpeed / Math.max(segLen, 1);
      while (this.pathT >= 1 && this.pathIdx < PATH_PX.length - 2) {
        this.pathT -= 1;
        this.pathIdx++;
      }
      if (this.pathIdx >= PATH_PX.length - 1) { this.reachEnd(); return; }
      const a = PATH_PX[this.pathIdx], b = PATH_PX[this.pathIdx+1];
      this.x = lerp(a.x, b.x, Math.min(this.pathT,1));
      this.y = lerp(a.y, b.y, Math.min(this.pathT,1));
    }
  }
  reachEnd() {
    this.alive = false;
    lives = Math.max(0, lives - (this.isBoss ? 3 : 1));
    if (lives <= 0) state = 'gameover';
  }
  takeDamage(dmg) {
    this.hp -= dmg;
    if (this.hp <= 0) {
      this.alive = false;
      gold += this.reward;
      score += this.reward * 2;
      spawnDeathParticles(this);
    }
  }
  draw() {
    if (!this.alive) return;
    const r = this.radius;
    ctx.beginPath();
    if (this.isBoss) {
      // Boss: hexagon
      for (let i=0;i<6;i++){
        const a = Math.PI/3*i - Math.PI/2;
        const px = this.x+Math.cos(a)*r, py = this.y+Math.sin(a)*r;
        i===0?ctx.moveTo(px,py):ctx.lineTo(px,py);
      }
      ctx.closePath();
      ctx.fillStyle = this.color;
      ctx.fill();
      ctx.strokeStyle = '#facc15'; ctx.lineWidth = 2; ctx.stroke();
    } else if (this.isFlyer) {
      // Flyer: diamond
      ctx.moveTo(this.x, this.y-r);
      ctx.lineTo(this.x+r, this.y);
      ctx.lineTo(this.x, this.y+r);
      ctx.lineTo(this.x-r, this.y);
      ctx.closePath();
      ctx.fillStyle = this.color; ctx.fill();
    } else {
      ctx.arc(this.x, this.y, r, 0, Math.PI*2);
      ctx.fillStyle = this.color; ctx.fill();
    }
    // Healer cross
    if (this.isHealer) {
      ctx.fillStyle='#fff';
      ctx.fillRect(this.x-1, this.y-4, 2, 8);
      ctx.fillRect(this.x-4, this.y-1, 8, 2);
    }
    // HP bar
    const hpPct = this.hp / this.maxHp;
    if (hpPct < 1) {
      const bw = r*2.5, bh = 3;
      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.fillRect(this.x-bw/2, this.y-r-7, bw, bh);
      ctx.fillStyle = hpPct>0.5?'#4ade80':hpPct>0.25?'#facc15':'#ef4444';
      ctx.fillRect(this.x-bw/2, this.y-r-7, bw*hpPct, bh);
    }
    // Slow indicator
    if (this.slowTimer > 0) {
      ctx.strokeStyle = 'rgba(56,189,248,0.5)'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(this.x, this.y, r+3, 0, Math.PI*2); ctx.stroke();
    }
  }
}

// --- Tower class ---
class Tower {
  constructor(type, col, row) {
    const def = TOWER_DEFS[type];
    this.type = type; this.col = col; this.row = row;
    this.x = col*TILE+TILE/2; this.y = row*TILE+TILE/2;
    this.color = def.color;
    this.baseCost = def.cost; this.totalInvested = def.cost;
    this.range = def.range; this.baseDamage = def.damage;
    this.damage = def.damage; this.rate = def.rate;
    this.tier = 1; this.cooldown = 0;
    this.angle = 0;
  }
  upgradeCost() {
    if (this.tier >= 3) return Infinity;
    return Math.floor(this.baseCost * (this.tier === 1 ? 0.6 : 1.0));
  }
  upgrade() {
    const cost = this.upgradeCost();
    if (this.tier >= 3 || gold < cost) return false;
    gold -= cost; this.totalInvested += cost; this.tier++;
    const mult = this.tier === 2 ? 1.5 : 2.0;
    this.damage = Math.floor(this.baseDamage * mult);
    this.range = TOWER_DEFS[this.type].range * (1 + (this.tier-1)*0.1);
    return true;
  }
  sellValue() { return Math.floor(this.totalInvested * 0.6); }
  findTarget() {
    let best = null, bestVal = -1;
    for (const e of enemies) {
      if (!e.alive) continue;
      const d = dist(this, e);
      if (d > this.range) continue;
      if (this.type === 'sniper') {
        if (e.hp > bestVal) { best = e; bestVal = e.hp; }
      } else {
        // Priority: furthest along path
        const progress = e.isFlyer ? e.x / W : e.pathIdx + e.pathT;
        if (progress > bestVal) { best = e; bestVal = progress; }
      }
    }
    return best;
  }
  update(dt) {
    this.cooldown = Math.max(0, this.cooldown - dt);
    if (this.cooldown > 0) return;
    const target = this.findTarget();
    if (!target) return;
    this.angle = Math.atan2(target.y-this.y, target.x-this.x);
    this.cooldown = this.rate;
    this.fire(target);
  }
  fire(target) {
    switch(this.type) {
      case 'bolt':
        projectiles.push(new Projectile(this.x,this.y,target,'bolt',this.damage,this.color));
        break;
      case 'flame':
        projectiles.push(new FlameEffect(target.x,target.y,this.damage,60));
        break;
      case 'arc':
        projectiles.push(new ArcLightning(this.x,this.y,target,this.damage,this.range));
        break;
      case 'frost':
        projectiles.push(new Projectile(this.x,this.y,target,'frost',this.damage,'#38bdf8'));
        break;
      case 'cannon':
        projectiles.push(new Projectile(this.x,this.y,target,'cannon',this.damage,'#f472b6'));
        break;
      case 'sniper':
        projectiles.push(new SniperLaser(this.x,this.y,target,this.damage));
        break;
    }
  }
  draw() {
    const size = TILE/2 - 4 + (this.tier-1)*2;
    ctx.save(); ctx.translate(this.x, this.y); ctx.rotate(this.angle);
    // Base
    ctx.fillStyle = 'rgba(0,0,0,0.4)';
    ctx.fillRect(-size,-size,size*2,size*2);
    ctx.strokeStyle = this.color; ctx.lineWidth = 2;
    ctx.strokeRect(-size,-size,size*2,size*2);
    // Barrel
    ctx.fillStyle = this.color;
    ctx.fillRect(0,-2, size+4, 4);
    ctx.restore();
    // Tier dots
    for (let i=0;i<this.tier;i++) {
      ctx.fillStyle = '#facc15';
      ctx.beginPath();
      ctx.arc(this.x - 6 + i*6, this.y - TILE/2 + 4, 2, 0, Math.PI*2);
      ctx.fill();
    }
    // Range circle if selected
    if (selectedPlaced === this) {
      ctx.strokeStyle = 'rgba(167,139,250,0.3)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(this.x,this.y,this.range,0,Math.PI*2); ctx.stroke();
    }
  }
}

// --- Projectiles ---
class Projectile {
  constructor(x,y,target,type,damage,color) {
    this.x=x; this.y=y; this.target=target; this.type=type;
    this.damage=damage; this.color=color; this.alive=true;
    this.speed = type==='cannon' ? 200 : 350;
  }
  update(dt) {
    if (!this.alive) return;
    if (!this.target.alive) {
      // Fly to last known position
      this.alive = false; return;
    }
    const dx=this.target.x-this.x, dy=this.target.y-this.y;
    const d = Math.hypot(dx,dy);
    if (d < 8) { this.hit(); return; }
    const spd = this.speed * dt;
    this.x += (dx/d)*spd; this.y += (dy/d)*spd;
  }
  hit() {
    this.alive = false;
    if (this.type === 'frost') {
      this.target.takeDamage(this.damage);
      this.target.slowTimer = 2;
    } else if (this.type === 'cannon') {
      // Splash
      for (const e of enemies) {
        if (e.alive && dist(e, this.target) < 60) {
          e.takeDamage(this.damage * (dist(e,this.target)<20 ? 1 : 0.5));
        }
      }
      projectiles.push(new SplashEffect(this.target.x, this.target.y));
    } else {
      this.target.takeDamage(this.damage);
    }
  }
  draw() {
    if (!this.alive) return;
    ctx.fillStyle = this.color;
    const r = this.type==='cannon' ? 5 : 3;
    ctx.beginPath(); ctx.arc(this.x,this.y,r,0,Math.PI*2); ctx.fill();
    ctx.shadowColor = this.color; ctx.shadowBlur = 6;
    ctx.beginPath(); ctx.arc(this.x,this.y,r-1,0,Math.PI*2); ctx.fill();
    ctx.shadowBlur = 0;
  }
}

class FlameEffect {
  constructor(x,y,dps,radius) {
    this.x=x; this.y=y; this.dps=dps; this.radius=radius;
    this.alive=true; this.timer=0; this.duration=0.5;
  }
  update(dt) {
    this.timer += dt;
    if (this.timer >= this.duration) { this.alive=false; return; }
    for (const e of enemies) {
      if (e.alive && dist(e,this) < this.radius) e.takeDamage(this.dps*dt);
    }
  }
  draw() {
    if (!this.alive) return;
    const alpha = 0.4 * (1 - this.timer/this.duration);
    const r = this.radius * (0.5 + this.timer/this.duration * 0.5);
    ctx.fillStyle = `rgba(251,146,60,${alpha})`;
    ctx.beginPath(); ctx.arc(this.x,this.y,r,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle = `rgba(251,146,60,${alpha+0.2})`; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(this.x,this.y,r,0,Math.PI*2); ctx.stroke();
  }
}

class ArcLightning {
  constructor(x,y,target,damage,range) {
    this.alive=true; this.timer=0; this.duration=0.25;
    this.chains = []; this.x=x; this.y=y;
    const hit = new Set();
    let cur = target;
    for (let i=0;i<3&&cur;i++) {
      cur.takeDamage(damage * (i===0?1:0.6));
      hit.add(cur);
      this.chains.push({x:cur.x,y:cur.y});
      // Find next chain target
      let next=null, nd=Infinity;
      for (const e of enemies) {
        if (e.alive && !hit.has(e)) {
          const d = dist(e,cur);
          if (d < range*0.6 && d < nd) { next=e; nd=d; }
        }
      }
      cur=next;
    }
  }
  update(dt) { this.timer+=dt; if(this.timer>=this.duration) this.alive=false; }
  draw() {
    if (!this.alive || !this.chains.length) return;
    const alpha = 1 - this.timer/this.duration;
    ctx.strokeStyle = `rgba(167,139,250,${alpha})`; ctx.lineWidth = 2;
    ctx.shadowColor = '#a78bfa'; ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.moveTo(this.x, this.y);
    for (const c of this.chains) {
      // Jagged lightning
      const mx = (this.x+c.x)/2 + rand(-10,10), my = (this.y+c.y)/2 + rand(-10,10);
      ctx.lineTo(mx,my); ctx.lineTo(c.x,c.y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;
  }
}

class SniperLaser {
  constructor(x,y,target,damage) {
    this.x=x; this.y=y; this.tx=target.x; this.ty=target.y;
    this.alive=true; this.timer=0; this.duration=0.15;
    target.takeDamage(damage);
  }
  update(dt) { this.timer+=dt; if(this.timer>=this.duration) this.alive=false; }
  draw() {
    if (!this.alive) return;
    const alpha = 1 - this.timer/this.duration;
    ctx.strokeStyle = `rgba(74,222,128,${alpha})`; ctx.lineWidth = 2;
    ctx.shadowColor = '#4ade80'; ctx.shadowBlur = 8;
    ctx.beginPath(); ctx.moveTo(this.x,this.y); ctx.lineTo(this.tx,this.ty); ctx.stroke();
    ctx.shadowBlur = 0;
  }
}

class SplashEffect {
  constructor(x,y) { this.x=x; this.y=y; this.alive=true; this.timer=0; this.duration=0.3; }
  update(dt) { this.timer+=dt; if(this.timer>=this.duration) this.alive=false; }
  draw() {
    if (!this.alive) return;
    const t = this.timer/this.duration;
    const alpha = 0.5*(1-t);
    ctx.strokeStyle=`rgba(244,114,182,${alpha})`; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(this.x,this.y,60*t,0,Math.PI*2); ctx.stroke();
  }
}

// Particles
function spawnDeathParticles(e) {
  for (let i=0;i<8;i++) {
    particles.push({
      x:e.x, y:e.y, vx:rand(-60,60), vy:rand(-60,60),
      color:e.color, life:0.5, maxLife:0.5, size:rand(2,4)
    });
  }
}

// --- Wave system ---
function startWave() {
  if (wave >= WAVES.length) return;
  waveState = 'spawning';
  spawnQueue = [];
  const groups = WAVES[wave];
  for (const g of groups) {
    for (let i=0;i<g.count;i++) {
      spawnQueue.push({type:g.type, delay:g.delay});
    }
  }
  spawnTimer = 0.3;
}

function endWave() {
  waveState = 'waiting';
  wave++;
  // Interest
  const interest = Math.floor(gold * 0.05);
  gold += interest;
  score += interest;
  if (wave >= WAVES.length) state = 'victory';
  else countdownTimer = 5;
}

function getWavePreview(w) {
  if (w >= WAVES.length) return 'Final wave!';
  const groups = WAVES[w];
  return groups.map(g => `${g.count}x ${g.type}`).join(', ');
}

// --- Input ---
canvas.addEventListener('mousemove', (e) => {
  const rect = canvas.getBoundingClientRect();
  const scaleX = W / rect.width, scaleY = H / rect.height;
  const mx = (e.clientX - rect.left) * scaleX;
  const my = (e.clientY - rect.top) * scaleY;
  const col = Math.floor(mx/TILE), row = Math.floor(my/TILE);
  hoverTile = (col>=0&&col<COLS&&row>=0&&row<ROWS) ? {col,row,mx,my} : null;
});

canvas.addEventListener('click', (e) => {
  if (state !== 'playing' || !hoverTile) return;
  const {col,row} = hoverTile;
  // Check if clicking existing tower
  const existing = towers.find(t => t.col===col && t.row===row);
  if (existing) {
    selectedPlaced = selectedPlaced === existing ? null : existing;
    selectedTower = null;
    updateTowerInfo();
    return;
  }
  // Place tower
  if (selectedTower && grid[row][col] === 0) {
    const def = TOWER_DEFS[selectedTower];
    if (gold >= def.cost) {
      gold -= def.cost;
      const t = new Tower(selectedTower, col, row);
      towers.push(t);
      grid[row][col] = 2;
      selectedPlaced = t;
      updateTowerInfo();
    }
  } else {
    selectedPlaced = null;
    updateTowerInfo();
  }
});

// Tower selector buttons
function selectTowerType(type) {
  selectedTower = selectedTower === type ? null : type;
  selectedPlaced = null;
  updateTowerInfo();
  // Highlight active button
  document.querySelectorAll('.tower-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.type === selectedTower);
  });
}

function updateTowerInfo() {
  const panel = document.getElementById('towerInfo');
  if (selectedPlaced) {
    const t = selectedPlaced;
    const def = TOWER_DEFS[t.type];
    const uc = t.upgradeCost();
    panel.innerHTML = `
      <div class="info-name" style="color:${t.color}">${def.name} (Tier ${t.tier})</div>
      <div class="info-stat">Damage: ${t.damage}</div>
      <div class="info-stat">Range: ${Math.floor(t.range)}px</div>
      <div class="info-stat">Special: ${def.special}</div>
      ${t.tier<3?`<button class="upgrade-btn" onclick="upgradeTower()">Upgrade (${uc}g)</button>`:'<div class="info-stat" style="color:#facc15">MAX TIER</div>'}
      <button class="sell-btn" onclick="sellTower()">Sell (${t.sellValue()}g)</button>
    `;
  } else if (selectedTower) {
    const def = TOWER_DEFS[selectedTower];
    panel.innerHTML = `
      <div class="info-name" style="color:${def.color}">${def.name}</div>
      <div class="info-stat">Cost: ${def.cost}g</div>
      <div class="info-stat">Damage: ${def.damage}</div>
      <div class="info-stat">Range: ${def.range}px</div>
      <div class="info-stat">Special: ${def.special}</div>
      <div class="info-stat" style="color:#94a3b8">Click grid to place</div>
    `;
  } else {
    panel.innerHTML = '<div class="info-stat" style="color:#64748b">Select a tower or click a placed tower</div>';
  }
}

function upgradeTower() {
  if (selectedPlaced) { selectedPlaced.upgrade(); updateTowerInfo(); }
}
function sellTower() {
  if (!selectedPlaced) return;
  gold += selectedPlaced.sellValue();
  grid[selectedPlaced.row][selectedPlaced.col] = 0;
  towers = towers.filter(t => t !== selectedPlaced);
  selectedPlaced = null;
  updateTowerInfo();
}

// Speed controls
function setSpeed(s) {
  speedMult = s;
  document.querySelectorAll('.speed-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.speed) === s);
  });
}

// Start wave button
function onStartWave() {
  if (state === 'playing' && waveState === 'waiting' && wave < WAVES.length) {
    startWave();
  }
}

// Start game
function startGame() {
  gold=200; lives=20; score=0; wave=0; speedMult=1;
  towers=[]; enemies=[]; projectiles=[]; particles=[];
  selectedTower=null; selectedPlaced=null;
  waveState='waiting'; countdownTimer=3;
  // Reset grid
  for(let r=0;r<ROWS;r++) for(let c=0;c<COLS;c++) grid[r][c] = PATH_SET.has(`${c},${r}`) ? 1 : 0;
  state='playing';
  document.getElementById('startScreen').style.display='none';
  document.getElementById('endScreen').style.display='none';
  document.getElementById('hud').style.display='flex';
  document.getElementById('sidePanel').style.display='flex';
  updateTowerInfo();
  setSpeed(1);
}

// --- Rendering ---
function drawGrid() {
  // Background
  ctx.fillStyle = '#0f0f1a';
  ctx.fillRect(0,0,W,H);

  for(let r=0;r<ROWS;r++) {
    for(let c=0;c<COLS;c++) {
      const x=c*TILE, y=r*TILE;
      if (grid[r][c]===1) {
        // Path tile
        ctx.fillStyle='#1a1a2e';
        ctx.fillRect(x,y,TILE,TILE);
        // Subtle dots
        ctx.fillStyle='rgba(167,139,250,0.12)';
        ctx.beginPath(); ctx.arc(x+TILE/2,y+TILE/2,2,0,Math.PI*2); ctx.fill();
      } else if (grid[r][c]===0) {
        // Buildable
        ctx.fillStyle='#12121e';
        ctx.fillRect(x+1,y+1,TILE-2,TILE-2);
      }
      // Grid lines
      ctx.strokeStyle='rgba(167,139,250,0.08)'; ctx.lineWidth=0.5;
      ctx.strokeRect(x,y,TILE,TILE);
    }
  }

  // Hover highlight
  if (hoverTile && state==='playing') {
    const {col,row,mx,my} = hoverTile;
    if (selectedTower && grid[row][col]===0) {
      ctx.fillStyle='rgba(167,139,250,0.15)';
      ctx.fillRect(col*TILE,row*TILE,TILE,TILE);
      // Range preview
      const def = TOWER_DEFS[selectedTower];
      ctx.strokeStyle=`rgba(167,139,250,0.25)`; ctx.lineWidth=1;
      ctx.beginPath(); ctx.arc(col*TILE+TILE/2,row*TILE+TILE/2,def.range,0,Math.PI*2); ctx.stroke();
    } else if (grid[row][col]===0) {
      ctx.fillStyle='rgba(167,139,250,0.06)';
      ctx.fillRect(col*TILE,row*TILE,TILE,TILE);
    }
  }

  // Entrance / exit markers
  ctx.fillStyle='rgba(74,222,128,0.3)';
  ctx.fillRect(0,2*TILE,4,TILE);
  ctx.fillStyle='rgba(239,68,68,0.3)';
  ctx.fillRect(W-4,8*TILE,4,TILE);
}

function drawParticles(dt) {
  for (let i=particles.length-1;i>=0;i--) {
    const p = particles[i];
    p.life -= dt;
    if (p.life<=0) { particles.splice(i,1); continue; }
    p.x += p.vx*dt; p.y += p.vy*dt;
    const alpha = p.life/p.maxLife;
    ctx.fillStyle = p.color;
    ctx.globalAlpha = alpha;
    ctx.fillRect(p.x-p.size/2, p.y-p.size/2, p.size, p.size);
  }
  ctx.globalAlpha = 1;
}

// --- HUD update ---
function updateHUD() {
  document.getElementById('goldVal').textContent = gold;
  document.getElementById('livesVal').textContent = lives;
  document.getElementById('waveVal').textContent = `${Math.min(wave+1,WAVES.length)}/${WAVES.length}`;
  document.getElementById('scoreVal').textContent = score;

  const waveBtn = document.getElementById('waveBtn');
  const waveInfo = document.getElementById('waveInfo');
  if (waveState === 'waiting' && wave < WAVES.length) {
    waveBtn.style.display = 'block';
    waveBtn.textContent = countdownTimer > 0 ? `Next Wave (${Math.ceil(countdownTimer)}s)` : 'Send Wave';
    waveInfo.textContent = 'Next: ' + getWavePreview(wave);
  } else if (waveState === 'spawning' || waveState === 'active') {
    waveBtn.style.display = 'none';
    waveInfo.textContent = `Wave ${wave+1} — ${enemies.filter(e=>e.alive).length} enemies`;
  } else {
    waveBtn.style.display = 'none';
    waveInfo.textContent = '';
  }

  // Update tower button affordability
  document.querySelectorAll('.tower-btn').forEach(b => {
    const def = TOWER_DEFS[b.dataset.type];
    b.classList.toggle('cannot-afford', gold < def.cost);
  });
}

// --- Main loop ---
function gameLoop(timestamp) {
  const rawDt = Math.min((timestamp - lastTime) / 1000, 0.05);
  lastTime = timestamp;
  const dt = rawDt * (state==='playing' ? speedMult : 1);

  // Clear
  ctx.clearRect(0,0,W,H);
  drawGrid();

  if (state === 'playing') {
    // Wave countdown — auto-start when timer expires
    if (waveState === 'waiting' && countdownTimer > 0) {
      countdownTimer -= dt;
      if (countdownTimer <= 0) {
        countdownTimer = 0;
        startWave();
      }
    }

    // Spawning
    if (waveState === 'spawning') {
      spawnTimer -= dt;
      if (spawnTimer <= 0 && spawnQueue.length > 0) {
        const next = spawnQueue.shift();
        enemies.push(new Enemy(next.type, wave));
        spawnTimer = next.delay;
      }
      if (spawnQueue.length === 0) waveState = 'active';
    }

    // Check wave complete
    if (waveState === 'active' && enemies.every(e => !e.alive)) {
      enemies = []; // Clear dead enemies
      endWave();
    }

    // Update towers
    for (const t of towers) t.update(dt);

    // Update enemies
    for (const e of enemies) e.update(dt);

    // Update projectiles
    for (let i=projectiles.length-1;i>=0;i--) {
      projectiles[i].update(dt);
      if (!projectiles[i].alive) projectiles.splice(i,1);
    }

    // Draw towers
    for (const t of towers) t.draw();

    // Draw enemies
    for (const e of enemies) e.draw();

    // Draw projectiles
    for (const p of projectiles) p.draw();

    // Draw particles
    drawParticles(dt);

    updateHUD();
  }

  if (state === 'gameover' || state === 'victory') {
    showEndScreen();
  }

  requestAnimationFrame(gameLoop);
}

function showEndScreen() {
  const el = document.getElementById('endScreen');
  if (el.style.display === 'flex') return;
  el.style.display = 'flex';
  document.getElementById('hud').style.display = 'none';
  document.getElementById('sidePanel').style.display = 'none';
  document.getElementById('endTitle').textContent = state==='victory' ? 'VICTORY!' : 'GAME OVER';
  document.getElementById('endTitle').style.color = state==='victory' ? '#4ade80' : '#ef4444';
  document.getElementById('endScore').textContent = `Score: ${score}  |  Waves: ${Math.min(wave,WAVES.length)}/${WAVES.length}`;
}

// Init
lastTime = performance.now();
requestAnimationFrame(gameLoop);
