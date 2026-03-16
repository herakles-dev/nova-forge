/* Void Racer — Pseudo-3D Racing Engine
 * Mode 7-style scanline rendering, procedural tracks, AI opponents.
 * Built by Nova Forge + Amazon Nova Premier.
 */

(() => {
"use strict";

// ── Canvas Setup ──────────────────────────────────────────────────
const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const miniCanvas = document.getElementById("minimap-container");
const miniCtx = miniCanvas.getContext("2d");

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resize);
resize();

// ── Constants ─────────────────────────────────────────────────────
const SEGMENT_LENGTH = 200;
const TRACK_SEGMENTS = 200;
const TOTAL_LAPS = 3;
const ROAD_WIDTH = 2200;
const CAMERA_HEIGHT = 1000;
const CAMERA_DEPTH = 1 / Math.tan((80 / 2) * Math.PI / 180);
const MAX_SPEED = 220;
const ACCEL = 0.9;
const BRAKE = 0.7;
const FRICTION = 0.15;
const OFFROAD_PENALTY = 0.6;
const STEER_SPEED = 0.028;
const STEER_FRICTION = 0.92;
const CENTRIFUGAL = 0.3;
const BOOST_MULTIPLIER = 1.5;
const BOOST_DURATION = 2000;

// ── Colors (Void Theme) ──────────────────────────────────────────
const C = {
  skyTop: "#0a0a12", skyMid: "#1a1030", skyHorizon: "#0f2027",
  road1: "#1e1e3a", road2: "#16162a",
  shoulder1: "#a78bfa", shoulder2: "#2a2a3e",
  lane1: "rgba(167,139,250,0.35)", lane2: "transparent",
  grass1: "#0d1117", grass2: "#111827",
  rumble1: "#a78bfa", rumble2: "#1a1030",
  playerBody: "#67e8f9", playerAccent: "#a78bfa",
  boost: "#facc15",
  hud: "rgba(10,10,18,0.8)", text: "#e4e4f0",
};

// ── Track Generation ─────────────────────────────────────────────
let track = [];
let sceneryObjects = [];
let boostPads = [];

function generateTrack() {
  track = [];
  sceneryObjects = [];
  boostPads = [];
  for (let i = 0; i < TRACK_SEGMENTS; i++) {
    const t = i / TRACK_SEGMENTS;
    let curve = 0;
    // Create interesting curve sequences
    if (t > 0.05 && t < 0.15) curve = Math.sin((t - 0.05) * 10 * Math.PI) * 0.7;
    if (t > 0.22 && t < 0.32) curve = -0.9;
    if (t > 0.38 && t < 0.48) curve = Math.sin((t - 0.38) * 8 * Math.PI) * 0.5;
    if (t > 0.55 && t < 0.62) curve = 1.0;
    if (t > 0.68 && t < 0.78) curve = Math.sin((t - 0.68) * 6 * Math.PI) * 0.8;
    if (t > 0.85 && t < 0.95) curve = -0.6;
    const hill = Math.sin(t * 4 * Math.PI) * 0.3 + Math.sin(t * 7 * Math.PI) * 0.15;
    track.push({ curve, hill });
    // Scenery pylons on curves
    if (Math.abs(curve) > 0.3 && i % 4 === 0) {
      sceneryObjects.push({ segment: i, side: curve > 0 ? 1 : -1, type: "pylon", color: i % 8 < 4 ? "#67e8f9" : "#a78bfa" });
    }
    // Crystals every 15 segments
    if (i % 15 === 7) {
      sceneryObjects.push({ segment: i, side: (i % 2 === 0) ? 1.3 : -1.3, type: "crystal", color: "#a78bfa" });
    }
  }
  // Boost pads (3 per lap)
  [30, 95, 155].forEach(s => boostPads.push(s));
}

// ── Stars ────────────────────────────────────────────────────────
const stars = Array.from({ length: 120 }, () => ({
  x: Math.random(), y: Math.random() * 0.4,
  size: Math.random() * 1.8 + 0.5,
  flicker: Math.random() * Math.PI * 2,
  speed: Math.random() * 2 + 1,
}));

// ── Player State ─────────────────────────────────────────────────
let player = {};
function resetPlayer() {
  player = {
    x: 0, speed: 0, steerVel: 0,
    position: 0, // fractional segment index
    lap: 1, lapTriggered: false,
    boosting: false, boostEnd: 0,
    shakeX: 0, shakeY: 0, shakeDur: 0,
    maxSpeedReached: 0,
    finished: false,
  };
}

// ── AI Opponents ─────────────────────────────────────────────────
let aiCars = [];
function resetAI() {
  aiCars = [
    { x: -0.25, position: -3, speed: 0, maxSpeed: MAX_SPEED * 0.93, color: "#f87171", name: "RED", accel: 0.38 },
    { x: 0.30,  position: -6, speed: 0, maxSpeed: MAX_SPEED * 0.88, color: "#38bdf8", name: "BLU", accel: 0.35 },
    { x: -0.05, position: -9, speed: 0, maxSpeed: MAX_SPEED * 0.82, color: "#4ade80", name: "GRN", accel: 0.32 },
  ];
}

// ── Input ─────────────────────────────────────────────────────────
const keys = {};
window.addEventListener("keydown", e => { if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) { keys[e.code] = true; e.preventDefault(); } });
window.addEventListener("keyup", e => { keys[e.code] = false; });

let touchLeft = false, touchRight = false, touchBrake = false, touchActive = false;
canvas.addEventListener("touchstart", handleTouch, { passive: false });
canvas.addEventListener("touchmove", handleTouch, { passive: false });
canvas.addEventListener("touchend", () => { touchLeft = touchRight = touchBrake = false; });
function handleTouch(e) {
  e.preventDefault();
  touchActive = true;
  touchLeft = touchRight = touchBrake = false;
  for (const t of e.touches) {
    const rx = t.clientX / window.innerWidth;
    const ry = t.clientY / window.innerHeight;
    if (ry > 0.8) touchBrake = true;
    else if (rx < 0.35) touchLeft = true;
    else if (rx > 0.65) touchRight = true;
  }
}

function steerLeft() { return keys["ArrowLeft"] || keys["KeyA"] || touchLeft; }
function steerRight() { return keys["ArrowRight"] || keys["KeyD"] || touchRight; }
function accelerate() { return keys["ArrowUp"] || keys["KeyW"] || (touchActive && !touchBrake); }
function brake() { return keys["ArrowDown"] || keys["KeyS"] || touchBrake; }

// ── Game State ───────────────────────────────────────────────────
let gameState = "start"; // start | countdown | racing | finish
let countdownVal = 3;
let countdownTimer = 0;
let raceTime = 0;
let lastTime = 0;
let speedLines = [];

// ── DOM Elements ─────────────────────────────────────────────────
const $hud = document.getElementById("hud");
const $speedBar = document.getElementById("speed-bar");
const $posBadge = document.getElementById("position-badge");
const $lapDisplay = document.getElementById("lap-display");
const $timerDisplay = document.getElementById("timer-display");
const $startScreen = document.getElementById("start-screen");
const $countdownOverlay = document.getElementById("countdown-overlay");
const $countdown = document.getElementById("countdown");
const $finishScreen = document.getElementById("finish-screen");
const $resultPos = document.getElementById("result-position");
const $resultTime = document.getElementById("result-time");
const $resultSpeed = document.getElementById("result-speed");

document.getElementById("start-btn").addEventListener("click", startCountdown);
document.getElementById("restart-btn").addEventListener("click", startCountdown);

function startCountdown() {
  generateTrack();
  resetPlayer();
  resetAI();
  raceTime = 0;
  speedLines = [];
  gameState = "countdown";
  countdownVal = 3;
  countdownTimer = 0;
  $startScreen.classList.add("hidden");
  $finishScreen.classList.add("hidden");
  $countdownOverlay.classList.remove("hidden");
  $countdown.textContent = "3";
  $hud.classList.remove("hidden");
}

function startRacing() {
  gameState = "racing";
  $countdownOverlay.classList.add("hidden");
}

function finishRace() {
  gameState = "finish";
  player.finished = true;
  const pos = getPlayerPosition();
  const posNames = ["1st", "2nd", "3rd", "4th"];
  $resultPos.textContent = `Position: ${posNames[pos - 1]}`;
  $resultTime.textContent = `Time: ${formatTime(raceTime)}`;
  $resultSpeed.textContent = `Top Speed: ${Math.round(player.maxSpeedReached)} km/h`;
  $finishScreen.classList.remove("hidden");
}

function formatTime(ms) {
  const s = ms / 1000;
  const min = Math.floor(s / 60);
  const sec = s % 60;
  return `${String(min).padStart(2, "0")}:${sec.toFixed(2).padStart(5, "0")}`;
}

// ── Player Position Tracking ─────────────────────────────────────
function getPlayerPosition() {
  const playerDist = player.lap * TRACK_SEGMENTS + player.position;
  let pos = 1;
  for (const ai of aiCars) {
    const aiLap = Math.floor(ai.position / TRACK_SEGMENTS) + 1;
    const aiDist = aiLap * TRACK_SEGMENTS + (ai.position % TRACK_SEGMENTS);
    if (aiDist > playerDist) pos++;
  }
  return Math.min(pos, 4);
}

// ── Update: Player Physics ───────────────────────────────────────
function updatePlayer(dt) {
  const segIdx = Math.floor(player.position) % TRACK_SEGMENTS;
  const seg = track[segIdx < 0 ? 0 : segIdx];

  // Steering
  if (steerLeft()) player.steerVel -= STEER_SPEED;
  if (steerRight()) player.steerVel += STEER_SPEED;
  player.steerVel *= STEER_FRICTION;
  player.x += player.steerVel * dt * 60;

  // Centrifugal force
  if (seg) player.x += seg.curve * CENTRIFUGAL * (player.speed / MAX_SPEED) * dt * 60;

  player.x = Math.max(-2.5, Math.min(2.5, player.x));

  // Acceleration / braking
  const isOffroad = Math.abs(player.x) > 1.0;
  const effectiveMax = player.boosting ? MAX_SPEED * BOOST_MULTIPLIER : MAX_SPEED;

  if (accelerate()) player.speed += 0.9 * dt * 60;
  if (brake()) player.speed -= BRAKE * dt * 60;
  player.speed -= FRICTION * dt * 60;
  if (isOffroad) player.speed -= player.speed * OFFROAD_PENALTY * dt;
  player.speed = Math.max(0, Math.min(effectiveMax, player.speed));

  if (player.speed > player.maxSpeedReached) player.maxSpeedReached = player.speed;

  // Boost check
  if (player.boosting && performance.now() > player.boostEnd) player.boosting = false;
  const currentSeg = Math.floor(player.position) % TRACK_SEGMENTS;
  if (boostPads.includes(currentSeg >= 0 ? currentSeg : 0) && !player.boosting && Math.abs(player.x) < 0.5) {
    player.boosting = true;
    player.boostEnd = performance.now() + BOOST_DURATION;
  }

  // Position advance
  player.position += player.speed * dt * 0.12;

  // Lap detection
  const segNow = Math.floor(player.position) % TRACK_SEGMENTS;
  if (segNow < 5 && !player.lapTriggered && player.position > TRACK_SEGMENTS * 0.5) {
    player.lap++;
    player.lapTriggered = true;
    if (player.lap > TOTAL_LAPS) { finishRace(); return; }
  }
  if (segNow > 10) player.lapTriggered = false;

if (player.shakeDur <= 0) {
  for (let i = 0; i < sceneryObjects.length; i++) {
    const scenery = sceneryObjects[i];
    const dx = player.x - scenery.x;
    const dy = player.y - scenery.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < 50) {
      player.shakeDur = 300;
      player.shakeX = Math.random() * 2 - 1;
      player.shakeY = Math.random() * 2 - 1;
      player.speed *= 0.6;
      break;
    }
  }
}
  if (player.shakeDur > 0) {
    player.shakeX = (Math.random() - 0.5) * 8;
    player.shakeY = (Math.random() - 0.5) * 6;
    player.shakeDur -= dt * 1000;
  } else {
    player.shakeX = player.shakeY = 0;
  }
}

// ── Update: AI Cars ──────────────────────────────────────────────
function updateAI(dt) {
  for (const ai of aiCars) {
    const segIdx = Math.floor(ai.position >= 0 ? ai.position : 0) % TRACK_SEGMENTS;
    const seg = track[segIdx];
    // Accelerate toward max speed
    if (ai.speed < ai.maxSpeed) ai.speed += ai.accel * dt * 60;
    ai.speed = Math.min(ai.maxSpeed, ai.speed);
    // Follow curves
    if (seg) ai.x += seg.curve * 0.012 * dt * 60;
    ai.x = Math.max(-0.8, Math.min(0.8, ai.x));
    // Advance position
    ai.position += ai.speed * dt * 0.12;
  }
}

// ── Speed Lines ──────────────────────────────────────────────────
function updateSpeedLines(dt) {
  const spd = player.speed / MAX_SPEED;
  if (spd > 0.5 && Math.random() < spd * 3 * dt) {
    const side = Math.random() > 0.5 ? 1 : -1;
    speedLines.push({
      x: canvas.width / 2 + side * (canvas.width * 0.15 + Math.random() * canvas.width * 0.3),
      y: canvas.height * 0.3 + Math.random() * canvas.height * 0.5,
      len: 20 + Math.random() * 60 * spd,
      life: 0.5,
    });
  }
  for (let i = speedLines.length - 1; i >= 0; i--) {
    speedLines[i].y += 800 * spd * dt;
    speedLines[i].life -= dt * 2;
    if (speedLines[i].life <= 0 || speedLines[i].y > canvas.height) speedLines.splice(i, 1);
  }
}

// ── Render: Sky & Stars ──────────────────────────────────────────
function renderSky() {
  const h = canvas.height;
  const horizonY = h * 0.4;
  const grad = ctx.createLinearGradient(0, 0, 0, horizonY);
  grad.addColorStop(0, C.skyTop);
  grad.addColorStop(0.5, C.skyMid);
  grad.addColorStop(1, C.skyHorizon);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, canvas.width, horizonY + 2);

  // Stars
  const now = performance.now() / 1000;
  for (const s of stars) {
    const alpha = 0.4 + 0.6 * Math.sin(now * s.speed + s.flicker);
    ctx.fillStyle = `rgba(228,228,240,${alpha.toFixed(2)})`;
    ctx.fillRect(s.x * canvas.width, s.y * canvas.height, s.size, s.size);
  }
}

// ── Render: Road (Mode 7 Scanline Projection) ───────────────────
function renderRoad() {
  const w = canvas.width;
  const h = canvas.height;
  const horizonY = Math.floor(h * 0.4);
  const playerSeg = Math.floor(player.position);
  const playerPercent = player.position - playerSeg;

  // Precompute projected segments for scenery/AI rendering
  const projectedSegments = [];

  // Render scanlines bottom to horizon
  for (let screenY = h; screenY > horizonY; screenY--) {
    const normalizedY = (screenY - horizonY) / (h - horizonY); // 1 at bottom, 0 at horizon
    const z = CAMERA_DEPTH / normalizedY; // perspective depth

    // Which track segment does this z correspond to?
    const segOffset = z * 0.025;
    const absSegIndex = playerSeg + segOffset;
    const segIdx = Math.floor(absSegIndex >= 0 ? absSegIndex : 0) % TRACK_SEGMENTS;
    const seg = track[segIdx];

    // Hill effect: shift horizon
    const hillShift = seg ? seg.hill * 60 * (1 - normalizedY) : 0;
    const effectiveY = screenY + hillShift;

    // Road width at this depth
    const roadW = (ROAD_WIDTH / z) * (w / 1920);
    const shoulderW = roadW * 0.08;

    // Curve offset accumulation
    let curveOffset = 0;
    const startSeg = Math.max(0, playerSeg);
    const endSeg = Math.floor(absSegIndex);
    for (let s = startSeg; s <= endSeg && s < startSeg + 60; s++) {
      const ts = track[((s % TRACK_SEGMENTS) + TRACK_SEGMENTS) % TRACK_SEGMENTS];
      if (ts) curveOffset += ts.curve * 1.8;
    }
    curveOffset -= player.x * roadW * 0.5;

    const cx = w / 2 + curveOffset * (1 - normalizedY * 0.6) + player.shakeX;

    // Alternating segment colors (rumble strip effect)
    const stripe = Math.floor(absSegIndex * 1.5) % 2;

    // Grass
    ctx.fillStyle = stripe ? C.grass1 : C.grass2;
    ctx.fillRect(0, effectiveY, w, 1);

    // Road surface
    ctx.fillStyle = stripe ? C.road1 : C.road2;
    ctx.fillRect(cx - roadW / 2, effectiveY, roadW, 1);

    // Shoulders / rumble strips
    ctx.fillStyle = stripe ? C.shoulder1 : C.shoulder2;
    ctx.fillRect(cx - roadW / 2 - shoulderW, effectiveY, shoulderW, 1);
    ctx.fillRect(cx + roadW / 2, effectiveY, shoulderW, 1);

    // Center dashed line
    if (stripe) {
      ctx.fillStyle = C.lane1;
      ctx.fillRect(cx - 1, effectiveY, 3, 1);
    }

    // Boost pad highlight
    const boostSeg = ((segIdx) + TRACK_SEGMENTS) % TRACK_SEGMENTS;
    if (boostPads.includes(boostSeg) && Math.abs(absSegIndex - Math.floor(absSegIndex)) < 0.3) {
      ctx.fillStyle = `rgba(250,204,21,${0.15 + stripe * 0.15})`;
      ctx.fillRect(cx - roadW * 0.15, effectiveY, roadW * 0.3, 1);
    }

    // Store projection data for object rendering
    if (screenY % 3 === 0) {
      projectedSegments.push({ screenY: effectiveY, z, segIdx, cx, roadW, normalizedY });
    }
  }

  return projectedSegments;
}

// ── Render: Scenery Objects ──────────────────────────────────────
function renderScenery(projectedSegments) {
  const playerSeg = Math.floor(player.position) % TRACK_SEGMENTS;

  for (const obj of sceneryObjects) {
    let relSeg = obj.segment - playerSeg;
    if (relSeg < -10) relSeg += TRACK_SEGMENTS;
    if (relSeg < 0 || relSeg > 80) continue;

    // Find closest projected segment
    const proj = projectedSegments.find(p => {
      const si = ((p.segIdx) + TRACK_SEGMENTS) % TRACK_SEGMENTS;
      return si === obj.segment;
    });
    if (!proj) continue;

    const scale = Math.max(0.1, 1 - proj.normalizedY) * 2;
    const objX = proj.cx + obj.side * proj.roadW * 0.65;
    const objY = proj.screenY;

    if (obj.type === "pylon") {
      const pylonH = 30 * scale;
      const pylonW = 6 * scale;
      ctx.fillStyle = obj.color;
      ctx.fillRect(objX - pylonW / 2, objY - pylonH, pylonW, pylonH);
      // Glow cap
      ctx.shadowColor = obj.color;
      ctx.shadowBlur = 10 * scale;
      ctx.fillRect(objX - pylonW, objY - pylonH - 4 * scale, pylonW * 2, 4 * scale);
      ctx.shadowBlur = 0;
    } else if (obj.type === "crystal") {
      const sz = 12 * scale;
      ctx.save();
      ctx.translate(objX, objY - sz);
      ctx.rotate(performance.now() / 1500 + obj.segment);
      ctx.fillStyle = obj.color;
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(0, -sz);
      ctx.lineTo(sz * 0.5, 0);
      ctx.lineTo(0, sz * 0.4);
      ctx.lineTo(-sz * 0.5, 0);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.restore();
    }
  }
}

// ── Render: AI Cars ──────────────────────────────────────────────
function renderAICars(projectedSegments) {
  const playerSeg = Math.floor(player.position);

  for (const ai of aiCars) {
    let relPos = ai.position - playerSeg;
    if (relPos < -10 || relPos > 80) continue;

    // Find projection for AI position
    let closest = null;
    let bestDist = Infinity;
    for (const p of projectedSegments) {
      const d = Math.abs(p.segIdx - (Math.floor(ai.position) % TRACK_SEGMENTS));
      if (d < bestDist) { bestDist = d; closest = p; }
    }
    if (!closest || closest.normalizedY < 0.02) continue;

    const scale = Math.max(0.15, (1 - closest.normalizedY)) * 1.8;
    const carW = 40 * scale;
    const carH = 20 * scale;
    const carX = closest.cx + ai.x * closest.roadW * 0.4;
    const carY = closest.screenY - carH;

    // Car body
    ctx.fillStyle = ai.color;
    ctx.beginPath();
    ctx.moveTo(carX - carW / 2, carY + carH);
    ctx.lineTo(carX - carW * 0.35, carY);
    ctx.lineTo(carX + carW * 0.35, carY);
    ctx.lineTo(carX + carW / 2, carY + carH);
    ctx.closePath();
    ctx.fill();

    // Windshield
    ctx.fillStyle = "rgba(10,10,18,0.7)";
    ctx.fillRect(carX - carW * 0.2, carY + 2 * scale, carW * 0.4, carH * 0.35);
  }
}

// ── Render: Player Car ───────────────────────────────────────────
function renderPlayerCar() {
  const cx = canvas.width / 2 + player.shakeX;
  const cy = canvas.height * 0.82 + player.shakeY;
  const w = 56;
  const h = 36;

  // Shadow
  ctx.fillStyle = "rgba(0,0,0,0.4)";
  ctx.beginPath();
  ctx.ellipse(cx, cy + h + 4, w * 0.7, 6, 0, 0, Math.PI * 2);
  ctx.fill();

  // Body
  ctx.fillStyle = C.playerBody;
  ctx.beginPath();
  ctx.moveTo(cx - w / 2, cy + h);
  ctx.lineTo(cx - w * 0.3, cy - h * 0.1);
  ctx.lineTo(cx - w * 0.15, cy - h * 0.5);
  ctx.lineTo(cx + w * 0.15, cy - h * 0.5);
  ctx.lineTo(cx + w * 0.3, cy - h * 0.1);
  ctx.lineTo(cx + w / 2, cy + h);
  ctx.closePath();
  ctx.fill();

  // Cockpit
  ctx.fillStyle = C.playerAccent;
  ctx.beginPath();
  ctx.moveTo(cx - w * 0.15, cy);
  ctx.lineTo(cx - w * 0.08, cy - h * 0.35);
  ctx.lineTo(cx + w * 0.08, cy - h * 0.35);
  ctx.lineTo(cx + w * 0.15, cy);
  ctx.closePath();
  ctx.fill();

  // Steer tilt
  const tiltX = player.steerVel * 200;
  ctx.fillStyle = "#0a0a12";
  ctx.fillRect(cx + tiltX - 1.5, cy + h - 6, 3, 6);
  ctx.fillRect(cx + tiltX - w * 0.35, cy + h - 2, w * 0.15, 4);
  ctx.fillRect(cx + tiltX + w * 0.2, cy + h - 2, w * 0.15, 4);

  // Boost flame
  if (player.boosting) {
    const flameH = 10 + Math.random() * 15;
    ctx.fillStyle = C.boost;
    ctx.globalAlpha = 0.7 + Math.random() * 0.3;
    ctx.beginPath();
    ctx.moveTo(cx - 8, cy + h);
    ctx.lineTo(cx, cy + h + flameH);
    ctx.lineTo(cx + 8, cy + h);
    ctx.closePath();
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

// ── Render: Speed Lines ──────────────────────────────────────────
function renderSpeedLines() {
  for (const line of speedLines) {
    ctx.strokeStyle = `rgba(167,139,250,${line.life * 0.5})`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(line.x, line.y);
    ctx.lineTo(line.x, line.y - line.len);
    ctx.stroke();
  }
}

// ── Render: Boost Tint ───────────────────────────────────────────
function renderBoostTint() {
  if (player.boosting) {
    ctx.fillStyle = "rgba(250,204,21,0.06)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}

// ── HUD Update ───────────────────────────────────────────────────
function updateHUD() {
  // Speed bar
  const spd = player.speed / (MAX_SPEED * BOOST_MULTIPLIER);
  $speedBar.style.height = `${Math.min(100, spd * 100)}%`;

  // Position
  const pos = getPlayerPosition();
  const posNames = ["1st", "2nd", "3rd", "4th"];
  $posBadge.textContent = posNames[pos - 1];
  $posBadge.className = `pos-${pos}`;

  // Lap
  const displayLap = Math.min(player.lap, TOTAL_LAPS);
  $lapDisplay.textContent = `Lap ${displayLap}/${TOTAL_LAPS}`;

  // Timer
  $timerDisplay.textContent = formatTime(raceTime);
}

// ── Minimap ──────────────────────────────────────────────────────
function renderMinimap() {
  const mw = 100, mh = 100;
  miniCtx.clearRect(0, 0, mw, mh);
  miniCtx.fillStyle = C.hud;
  miniCtx.fillRect(0, 0, mw, mh);

  // Draw track as oval with curve influence
  miniCtx.strokeStyle = "rgba(167,139,250,0.4)";
  miniCtx.lineWidth = 2;
  miniCtx.beginPath();
  for (let i = 0; i < TRACK_SEGMENTS; i++) {
    const t = (i / TRACK_SEGMENTS) * Math.PI * 2;
    const seg = track[i];
    const r = 30 + seg.curve * 8;
    const x = mw / 2 + Math.cos(t) * r;
    const y = mh / 2 + Math.sin(t) * r * 0.7;
    if (i === 0) miniCtx.moveTo(x, y);
    else miniCtx.lineTo(x, y);
  }
  miniCtx.closePath();
  miniCtx.stroke();

  // Player dot
  const pt = ((player.position % TRACK_SEGMENTS) / TRACK_SEGMENTS) * Math.PI * 2;
  const pSeg = track[Math.floor(player.position >= 0 ? player.position : 0) % TRACK_SEGMENTS];
  const pr = 30 + (pSeg ? pSeg.curve * 8 : 0);
  miniCtx.fillStyle = C.playerBody;
  miniCtx.beginPath();
  miniCtx.arc(mw / 2 + Math.cos(pt) * pr, mh / 2 + Math.sin(pt) * pr * 0.7, 4, 0, Math.PI * 2);
  miniCtx.fill();

  // AI dots
  for (const ai of aiCars) {
    const at = ((ai.position % TRACK_SEGMENTS) / TRACK_SEGMENTS) * Math.PI * 2;
    const aSeg = track[Math.floor(ai.position >= 0 ? ai.position : 0) % TRACK_SEGMENTS];
    const ar = 30 + (aSeg ? aSeg.curve * 8 : 0);
    miniCtx.fillStyle = ai.color;
    miniCtx.beginPath();
    miniCtx.arc(mw / 2 + Math.cos(at) * ar, mh / 2 + Math.sin(at) * ar * 0.7, 3, 0, Math.PI * 2);
    miniCtx.fill();
  }
}

// ── Main Update ──────────────────────────────────────────────────
function update(dt) {
  if (gameState === "countdown") {
    countdownTimer += dt;
    if (countdownTimer >= 1) {
      countdownTimer -= 1;
      countdownVal--;
      if (countdownVal <= 0) {
        $countdown.textContent = "GO!";
        setTimeout(startRacing, 400);
      } else {
        $countdown.textContent = String(countdownVal);
      }
    }
  }
  if (gameState === "racing") {
    raceTime += dt * 1000;
    updatePlayer(dt);
    updateAI(dt);
    updateSpeedLines(dt);
  }
}

// ── Main Render ──────────────────────────────────────────────────
function render() {
  ctx.save();
  ctx.translate(player.shakeX * 0.3, player.shakeY * 0.3);

  renderSky();
  const projSegs = renderRoad();
  renderScenery(projSegs);
  renderAICars(projSegs);
  renderSpeedLines();
  renderPlayerCar();
  renderBoostTint();

  ctx.restore();

  if (gameState === "racing" || gameState === "countdown") {
    updateHUD();
    renderMinimap();
  }
}

// ── Game Loop ────────────────────────────────────────────────────
function loop(timestamp) {
  const dt = lastTime ? Math.min((timestamp - lastTime) / 1000, 0.05) : 0.016;
  lastTime = timestamp;

  update(dt);
  render();

  requestAnimationFrame(loop);
}

// ── Init ─────────────────────────────────────────────────────────
generateTrack();
resetPlayer();
resetAI();
requestAnimationFrame(loop);

})();
