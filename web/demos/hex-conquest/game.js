/* Hex Conquest — Turn-based hex strategy game
 * Built by Nova Forge + Amazon Nova Pro
 * ~470 lines of game logic
 */

// ── Constants ──────────────────────────────────────────────
const COLS = 7, ROWS = 7;
const HEX_SIZE = 34;
const OWNER_NONE = 0, OWNER_PLAYER = 1, OWNER_ENEMY = 2;
const MAX_STRENGTH = 6;
const WIN_THRESHOLD = 0.6;

const COLORS = {
    bg: '#0a0a12',
    surface: '#12121e',
    neutral: '#2a2a3e',
    neutralStroke: '#3a3a50',
    player: '#a78bfa',
    playerDark: '#7c5cbf',
    enemy: '#fb923c',
    enemyDark: '#c46d1e',
    highlight: '#67e8f9',
    text: '#e4e4f0',
    textDim: '#888',
    strengthText: '#fff',
};

// ── State ──────────────────────────────────────────────────
let canvas, ctx;
let grid = [];
let turnNumber = 1;
let currentTurn = OWNER_PLAYER;
let selectedHex = null;
let hoveredHex = null;
let gameRunning = false;
let animating = false;
let flashEffects = [];
let gridOffsetX = 0, gridOffsetY = 0;

// ── Hex math (flat-top offset coordinates) ─────────────────
function hexWidth() { return HEX_SIZE * 2; }
function hexHeight() { return Math.sqrt(3) * HEX_SIZE; }

function hexToPixel(col, row) {
    const w = hexWidth();
    const h = hexHeight();
    const x = gridOffsetX + col * (w * 0.75);
    const y = gridOffsetY + row * h + (col % 2 === 1 ? h * 0.5 : 0);
    return { x, y };
}

function pixelToHex(px, py) {
    let closest = null, minDist = Infinity;
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            const { x, y } = hexToPixel(c, r);
            const d = Math.hypot(px - x, py - y);
            if (d < minDist && d < HEX_SIZE * 1.1) {
                minDist = d;
                closest = { col: c, row: r };
            }
        }
    }
    return closest;
}

function getNeighbors(col, row) {
    const parity = col % 2;
    const dirs = parity === 0
        ? [[-1, -1], [-1, 0], [0, -1], [0, 1], [1, -1], [1, 0]]
        : [[-1, 0], [-1, 1], [0, -1], [0, 1], [1, 0], [1, 1]];
    const result = [];
    for (const [dc, dr] of dirs) {
        const nc = col + dc, nr = row + dr;
        if (nc >= 0 && nc < COLS && nr >= 0 && nr < ROWS) {
            result.push(grid[nc][nr]);
        }
    }
    return result;
}

// ── Grid setup ─────────────────────────────────────────────
function createGrid() {
    grid = [];
    for (let c = 0; c < COLS; c++) {
        grid[c] = [];
        for (let r = 0; r < ROWS; r++) {
            grid[c][r] = { col: c, row: r, owner: OWNER_NONE, strength: 0 };
        }
    }
    // Player starts: top-left area
    setHex(0, 0, OWNER_PLAYER, 3);
    setHex(1, 0, OWNER_PLAYER, 2);
    // Enemy starts: bottom-right area
    setHex(COLS - 1, ROWS - 1, OWNER_ENEMY, 3);
    setHex(COLS - 2, ROWS - 1, OWNER_ENEMY, 2);
}

function setHex(c, r, owner, strength) {
    grid[c][r].owner = owner;
    grid[c][r].strength = strength;
}

// ── Drawing ────────────────────────────────────────────────
function drawHexShape(cx, cy, size, fill, stroke, lineWidth) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
        const angle = (Math.PI / 180) * (60 * i);
        const hx = cx + size * Math.cos(angle);
        const hy = cy + size * Math.sin(angle);
        if (i === 0) ctx.moveTo(hx, hy);
        else ctx.lineTo(hx, hy);
    }
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth || 1.5; ctx.stroke(); }
}

function ownerFill(owner) {
    if (owner === OWNER_PLAYER) return COLORS.player;
    if (owner === OWNER_ENEMY) return COLORS.enemy;
    return COLORS.neutral;
}

function ownerStroke(owner) {
    if (owner === OWNER_PLAYER) return COLORS.playerDark;
    if (owner === OWNER_ENEMY) return COLORS.enemyDark;
    return COLORS.neutralStroke;
}

function drawGrid() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw hexes
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            const hex = grid[c][r];
            const { x, y } = hexToPixel(c, r);
            const isSelected = selectedHex && selectedHex.col === c && selectedHex.row === r;
            const isHovered = hoveredHex && hoveredHex.col === c && hoveredHex.row === r;
            const isValidTarget = selectedHex && isAdjacent(selectedHex, hex) &&
                                  hex.owner !== OWNER_PLAYER;

            // Base hex
            let fill = ownerFill(hex.owner);
            let stroke = ownerStroke(hex.owner);
            let lw = 1.5;

            if (isSelected) {
                stroke = COLORS.highlight;
                lw = 3;
            } else if (isHovered && gameRunning && currentTurn === OWNER_PLAYER) {
                if (hex.owner === OWNER_PLAYER && !selectedHex) {
                    stroke = COLORS.highlight;
                    lw = 2.5;
                } else if (selectedHex && isValidTarget) {
                    stroke = COLORS.highlight;
                    lw = 2.5;
                }
            }

            // Darken neutral hexes slightly more
            if (hex.owner === OWNER_NONE && hex.strength === 0) {
                fill = COLORS.neutral;
            }

            drawHexShape(x, y, HEX_SIZE - 2, fill, stroke, lw);

            // Valid target indicator: inner dashed ring
            if (selectedHex && isValidTarget && !isSelected) {
                ctx.save();
                ctx.setLineDash([4, 4]);
                drawHexShape(x, y, HEX_SIZE - 8, null, COLORS.highlight, 1);
                ctx.setLineDash([]);
                ctx.restore();
            }

            // Strength number
            if (hex.owner !== OWNER_NONE || hex.strength > 0) {
                ctx.fillStyle = COLORS.strengthText;
                ctx.font = '700 14px "Press Start 2P", monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(hex.strength.toString(), x, y);
            }
        }
    }

    // Flash effects (capture animation)
    for (const fx of flashEffects) {
        const { x, y } = hexToPixel(fx.col, fx.row);
        ctx.globalAlpha = fx.alpha;
        drawHexShape(x, y, HEX_SIZE + 4, null, fx.color, 3);
        ctx.globalAlpha = 1;
    }
}

function isAdjacent(a, b) {
    const neighbors = getNeighbors(a.col, a.row);
    return neighbors.some(n => n.col === b.col && n.row === b.row);
}

// ── Flash animation ────────────────────────────────────────
function addFlash(col, row, color) {
    flashEffects.push({ col, row, color, alpha: 1.0, decay: 0.04 });
}

function updateFlashes() {
    for (let i = flashEffects.length - 1; i >= 0; i--) {
        flashEffects[i].alpha -= flashEffects[i].decay;
        if (flashEffects[i].alpha <= 0) flashEffects.splice(i, 1);
    }
}

// ── Combat / Expansion ─────────────────────────────────────
function resolveCombat(attacker, defender) {
    if (defender.owner === OWNER_NONE && defender.strength === 0) {
        // Expand into empty neutral hex
        defender.owner = attacker.owner;
        defender.strength = Math.max(1, attacker.strength - 1);
        attacker.strength = Math.max(1, attacker.strength - 1);
        addFlash(defender.col, defender.row, attacker.owner === OWNER_PLAYER ? COLORS.player : COLORS.enemy);
        return 'capture';
    }

    if (attacker.strength > defender.strength) {
        // Attacker wins: capture the hex
        const remaining = attacker.strength - defender.strength;
        defender.owner = attacker.owner;
        defender.strength = Math.max(1, remaining);
        attacker.strength = Math.max(1, Math.ceil(attacker.strength / 2));
        addFlash(defender.col, defender.row, attacker.owner === OWNER_PLAYER ? COLORS.player : COLORS.enemy);
        return 'capture';
    } else {
        // Defender holds: both lose strength
        const atkLoss = Math.min(attacker.strength, defender.strength);
        const defLoss = Math.min(defender.strength, attacker.strength);
        attacker.strength = Math.max(0, attacker.strength - Math.ceil(defLoss * 0.7));
        defender.strength = Math.max(0, defender.strength - Math.ceil(atkLoss * 0.5));
        if (attacker.strength <= 0) { attacker.owner = OWNER_NONE; attacker.strength = 0; }
        if (defender.strength <= 0) { defender.owner = OWNER_NONE; defender.strength = 0; }
        addFlash(attacker.col, attacker.row, '#ef4444');
        addFlash(defender.col, defender.row, '#ef4444');
        return 'repelled';
    }
}

// ── Regeneration ───────────────────────────────────────────
function regenerateHexes() {
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            const hex = grid[c][r];
            if (hex.owner !== OWNER_NONE && hex.strength < MAX_STRENGTH) {
                hex.strength = Math.min(MAX_STRENGTH, hex.strength + 1);
            }
        }
    }
}

// ── AI ─────────────────────────────────────────────────────
function aiTurn() {
    animating = true;
    setTimeout(() => {
        const myHexes = allHexesOf(OWNER_ENEMY);
        if (myHexes.length === 0) { animating = false; endTurn(); return; }

        let bestMove = null, bestScore = -Infinity;

        for (const hex of myHexes) {
            if (hex.strength < 1) continue;
            const neighbors = getNeighbors(hex.col, hex.row);
            for (const neighbor of neighbors) {
                if (neighbor.owner === OWNER_ENEMY) continue;
                let score = 0;

                if (neighbor.owner === OWNER_NONE && neighbor.strength === 0) {
                    // Expand: prioritize empty hexes
                    score = 10 + hex.strength;
                } else if (neighbor.owner === OWNER_PLAYER) {
                    // Attack player: prioritize weaker targets
                    if (hex.strength > neighbor.strength) {
                        score = 20 + (hex.strength - neighbor.strength) * 3;
                    } else {
                        score = -5; // risky attack
                    }
                } else if (neighbor.owner === OWNER_NONE && neighbor.strength > 0) {
                    // Neutral with strength: expand if stronger
                    if (hex.strength > neighbor.strength) {
                        score = 8 + (hex.strength - neighbor.strength);
                    } else {
                        score = -2;
                    }
                }

                // Bonus for hexes closer to center
                const centerDist = Math.hypot(neighbor.col - COLS / 2, neighbor.row - ROWS / 2);
                score += (4 - centerDist) * 0.5;

                // Bonus for using strong hexes
                score += hex.strength * 0.3;

                if (score > bestScore) {
                    bestScore = score;
                    bestMove = { from: hex, to: neighbor };
                }
            }
        }

        if (bestMove && bestScore > 0) {
            resolveCombat(bestMove.from, bestMove.to);
        }

        animating = false;
        endTurn();
    }, 600);
}

function allHexesOf(owner) {
    const result = [];
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            if (grid[c][r].owner === owner) result.push(grid[c][r]);
        }
    }
    return result;
}

// ── Turn management ────────────────────────────────────────
function endTurn() {
    if (currentTurn === OWNER_PLAYER) {
        currentTurn = OWNER_ENEMY;
        updateHUD();
        if (!checkWinCondition()) aiTurn();
    } else {
        regenerateHexes();
        turnNumber++;
        currentTurn = OWNER_PLAYER;
        selectedHex = null;
        updateHUD();
        checkWinCondition();
    }
}

function countHexes(owner) {
    let count = 0;
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            if (grid[c][r].owner === owner) count++;
        }
    }
    return count;
}

function totalStrength(owner) {
    let total = 0;
    for (let c = 0; c < COLS; c++) {
        for (let r = 0; r < ROWS; r++) {
            if (grid[c][r].owner === owner) total += grid[c][r].strength;
        }
    }
    return total;
}

function checkWinCondition() {
    const total = COLS * ROWS;
    const playerCount = countHexes(OWNER_PLAYER);
    const enemyCount = countHexes(OWNER_ENEMY);

    if (playerCount / total >= WIN_THRESHOLD || enemyCount === 0) {
        showVictory();
        return true;
    }
    if (enemyCount / total >= WIN_THRESHOLD || playerCount === 0) {
        showDefeat();
        return true;
    }
    return false;
}

// ── HUD ────────────────────────────────────────────────────
function updateHUD() {
    const total = COLS * ROWS;
    const pc = countHexes(OWNER_PLAYER);
    const ec = countHexes(OWNER_ENEMY);
    document.getElementById('turn-count').textContent = turnNumber;
    document.getElementById('player-hexes').textContent = pc;
    document.getElementById('enemy-hexes').textContent = ec;
    document.getElementById('player-strength').textContent = totalStrength(OWNER_PLAYER);
    document.getElementById('enemy-strength').textContent = totalStrength(OWNER_ENEMY);
    document.getElementById('control-pct').textContent = Math.round((pc / total) * 100) + '%';

    const indicator = document.getElementById('turn-indicator');
    if (currentTurn === OWNER_PLAYER) {
        indicator.textContent = 'YOUR TURN';
        indicator.className = 'player-turn';
    } else {
        indicator.textContent = 'ENEMY TURN';
        indicator.className = 'enemy-turn';
    }
}

// ── Screens ────────────────────────────────────────────────
function showVictory() {
    gameRunning = false;
    document.getElementById('victory-turns').textContent = turnNumber;
    document.getElementById('victory-hexes').textContent = countHexes(OWNER_PLAYER);
    document.getElementById('victory-screen').style.display = 'flex';
}

function showDefeat() {
    gameRunning = false;
    document.getElementById('defeat-turns').textContent = turnNumber;
    document.getElementById('defeat-screen').style.display = 'flex';
}

// ── Input ──────────────────────────────────────────────────
function handleClick(e) {
    if (!gameRunning || currentTurn !== OWNER_PLAYER || animating) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    const clicked = pixelToHex(mx, my);
    if (!clicked) return;

    const hex = grid[clicked.col][clicked.row];

    if (!selectedHex) {
        // Select own hex
        if (hex.owner === OWNER_PLAYER && hex.strength > 0) {
            selectedHex = hex;
        }
    } else {
        // Deselect if clicking same hex
        if (hex.col === selectedHex.col && hex.row === selectedHex.row) {
            selectedHex = null;
            return;
        }
        // Select different own hex
        if (hex.owner === OWNER_PLAYER && hex.strength > 0) {
            selectedHex = hex;
            return;
        }
        // Try to attack/expand
        if (isAdjacent(selectedHex, hex) && hex.owner !== OWNER_PLAYER) {
            resolveCombat(selectedHex, hex);
            selectedHex = null;
            updateHUD();
            if (!checkWinCondition()) endTurn();
        } else {
            selectedHex = null;
        }
    }
}

function handleMouseMove(e) {
    if (!gameRunning) { hoveredHex = null; return; }
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    hoveredHex = pixelToHex(mx, my);
}

// ── Game loop ──────────────────────────────────────────────
function gameLoop() {
    updateFlashes();
    drawGrid();
    requestAnimationFrame(gameLoop);
}

// ── Init ───────────────────────────────────────────────────
function initGame() {
    canvas = document.getElementById('game-canvas');
    ctx = canvas.getContext('2d');

    // Size canvas to fit hex grid with padding
    const w = hexWidth();
    const h = hexHeight();
    const totalW = (COLS - 1) * (w * 0.75) + w + 40;
    const totalH = ROWS * h + h * 0.5 + 40;
    canvas.width = totalW;
    canvas.height = totalH;
    gridOffsetX = w / 2 + 20;
    gridOffsetY = h / 2 + 20;

    canvas.addEventListener('click', handleClick);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave', () => { hoveredHex = null; });

    // Start button
    document.getElementById('start-btn').addEventListener('click', startGame);
    document.getElementById('victory-restart-btn').addEventListener('click', restartGame);
    document.getElementById('defeat-restart-btn').addEventListener('click', restartGame);

    createGrid();
    updateHUD();
    gameLoop();
}

function startGame() {
    document.getElementById('start-screen').style.display = 'none';
    gameRunning = true;
    currentTurn = OWNER_PLAYER;
    updateHUD();
}

function restartGame() {
    document.getElementById('victory-screen').style.display = 'none';
    document.getElementById('defeat-screen').style.display = 'none';
    createGrid();
    turnNumber = 1;
    currentTurn = OWNER_PLAYER;
    selectedHex = null;
    hoveredHex = null;
    flashEffects = [];
    gameRunning = true;
    animating = false;
    updateHUD();
}

// Boot
document.addEventListener('DOMContentLoaded', initGame);
