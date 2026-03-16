// game.js - Breakout Arcade Game implementation

// Game constants
const PADDLE_WIDTH = 100;
const PADDLE_HEIGHT = 10;
const BALL_RADIUS = 10;
const BRICK_ROWS = 6;
const BRICK_COLUMNS = 10;
const BRICK_WIDTH = 70;
const BRICK_HEIGHT = 20;
const BRICK_PADDING = 10;
const BRICK_OFFSET_TOP = 60;
const BRICK_OFFSET_LEFT = 35;

// Game variables
let canvas, ctx;
let paddleX, ballX, ballY, ballSpeedX, ballSpeedY;
let score = 0;
let lives = 3;
let gameRunning = false;
let bricks = [];
let rightPressed = false;
let leftPressed = false;

// Initialize game
function init() {
    canvas = document.getElementById('gameCanvas');
    ctx = canvas.getContext('2d');

    // Responsive scaling
    function scaleGame() {
        var scaleX = window.innerWidth / 800;
        var scaleY = window.innerHeight / 600;
        var scale = Math.min(scaleX, scaleY, 1);
        var container = document.getElementById('game-container');
        container.style.width = (800 * scale) + 'px';
        container.style.height = (600 * scale) + 'px';
    }
    scaleGame();
    window.addEventListener('resize', scaleGame);

    // Initialize paddle position
    paddleX = (canvas.width - PADDLE_WIDTH) / 2;

    // Initialize ball position and speed
    resetBall();

    // Initialize bricks
    createBricks();

    // Add event listeners
    document.addEventListener('keydown', keyDownHandler);
    document.addEventListener('keyup', keyUpHandler);
    canvas.addEventListener('mousemove', function(e) {
        var rect = canvas.getBoundingClientRect();
        var scaleX = canvas.width / rect.width;
        paddleX = (e.clientX - rect.left) * scaleX - PADDLE_WIDTH / 2;
        paddleX = Math.max(0, Math.min(canvas.width - PADDLE_WIDTH, paddleX));
    });
    document.getElementById('restartButton').addEventListener('click', restartGame);

    // Start screen
    var startBtn = document.getElementById('start-btn');
    if (startBtn) {
        startBtn.addEventListener('click', function() {
            document.getElementById('start-screen').style.display = 'none';
            gameRunning = true;
            requestAnimationFrame(draw);
        });
    }
}

// Reset ball position and speed
function resetBall() {
    ballX = canvas.width / 2;
    ballY = canvas.height - PADDLE_HEIGHT - BALL_RADIUS * 2;
    ballSpeedX = 5 * (Math.random() > 0.5 ? 1 : -1);
    ballSpeedY = -5;
}

// Create brick layout
function createBricks() {
    bricks = [];
    for (let c = 0; c < BRICK_COLUMNS; c++) {
        bricks[c] = [];
        for (let r = 0; r < BRICK_ROWS; r++) {
            const brickX = c * (BRICK_WIDTH + BRICK_PADDING) + BRICK_OFFSET_LEFT;
            const brickY = r * (BRICK_HEIGHT + BRICK_PADDING) + BRICK_OFFSET_TOP;
            const color = getBrickColor(r);
            
            bricks[c][r] = { 
                x: brickX, 
                y: brickY, 
                width: BRICK_WIDTH, 
                height: BRICK_HEIGHT, 
                color: color, 
                visible: true 
            };
        }
    }
}

// Get color for brick based on row — Nova Forge brand palette
function getBrickColor(row) {
    const colors = ['#f472b6', '#a78bfa', '#c084fc', '#67e8f9', '#4ade80', '#fb923c'];
    return colors[row % colors.length];
}

// Draw everything
function draw() {
    if (!gameRunning) return;
    
    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Draw paddle — cyan with glow
    ctx.shadowColor = '#67e8f9';
    ctx.shadowBlur = 12;
    ctx.fillStyle = '#67e8f9';
    ctx.fillRect(paddleX, canvas.height - PADDLE_HEIGHT, PADDLE_WIDTH, PADDLE_HEIGHT);
    ctx.shadowBlur = 0;

    // Draw ball — purple with glow
    ctx.beginPath();
    ctx.arc(ballX, ballY, BALL_RADIUS, 0, Math.PI * 2);
    ctx.shadowColor = '#a78bfa';
    ctx.shadowBlur = 16;
    ctx.fillStyle = '#a78bfa';
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.closePath();
    
    // Draw bricks
    drawBricks();
    
    // Draw score and lives
    updateScoreboard();
    
    // Collision detection
    collisionDetection();
    
    // Move ball
    ballX += ballSpeedX;
    ballY += ballSpeedY;
    
    // Wall collision (left/right)
    if (ballX + BALL_RADIUS > canvas.width || ballX - BALL_RADIUS < 0) {
        ballSpeedX = -ballSpeedX;
    }
    
    // Wall collision (top)
    if (ballY - BALL_RADIUS < 0) {
        ballSpeedY = -ballSpeedY;
    }
    
    // Wall collision (bottom) - lose a life
    if (ballY + BALL_RADIUS > canvas.height) {
        lives--;
        if (!lives) {
            gameOver();
        } else {
            resetBall();
        }
    }
    
    // Paddle collision
    if (
        ballY + BALL_RADIUS > canvas.height - PADDLE_HEIGHT &&
        ballY - BALL_RADIUS < canvas.height &&
        ballX > paddleX &&
        ballX < paddleX + PADDLE_WIDTH
    ) {
        ballSpeedY = -Math.abs(ballSpeedY);
    }
    
    // Move paddle with arrow keys
    if (rightPressed && paddleX < canvas.width - PADDLE_WIDTH) {
        paddleX += 7;
    } else if (leftPressed && paddleX > 0) {
        paddleX -= 7;
    }
    
    // Continue game loop
    requestAnimationFrame(draw);
}

// Draw all bricks
function drawBricks() {
    for (let c = 0; c < BRICK_COLUMNS; c++) {
        for (let r = 0; r < BRICK_ROWS; r++) {
            if (bricks[c][r].visible) {
                const brick = bricks[c][r];
                ctx.fillStyle = brick.color;
                ctx.fillRect(brick.x, brick.y, brick.width, brick.height);
            }
        }
    }
}

// Collision detection between ball and bricks
function collisionDetection() {
    for (let c = 0; c < BRICK_COLUMNS; c++) {
        for (let r = 0; r < BRICK_ROWS; r++) {
            const brick = bricks[c][r];
            if (brick.visible) {
                if (
                    ballX > brick.x &&
                    ballX < brick.x + brick.width &&
                    ballY > brick.y &&
                    ballY < brick.y + brick.height
                ) {
                    brick.visible = false;
                    ballSpeedY = -ballSpeedY;
                    score += 10;
                }
            }
        }
    }
}

// Update score and lives display
function updateScoreboard() {
    document.getElementById('scoreDisplay').innerHTML = `Score: ${score}`;
    document.getElementById('livesDisplay').innerHTML = `Lives: ${lives}`;
}

// Handle keydown events
function keyDownHandler(e) {
    if (e.key === 'Right' || e.key === 'ArrowRight') {
        rightPressed = true;
    } else if (e.key === 'Left' || e.key === 'ArrowLeft') {
        leftPressed = true;
    }
}

// Handle keyup events
function keyUpHandler(e) {
    if (e.key === 'Right' || e.key === 'ArrowRight') {
        rightPressed = false;
    } else if (e.key === 'Left' || e.key === 'ArrowLeft') {
        leftPressed = false;
    }
}

// Game over screen
function gameOver() {
    gameRunning = false;
    document.getElementById('finalScore').innerHTML = score;
    document.getElementById('gameOver').style.display = 'block';
}

// Restart game
function restartGame() {
    // Reset game state
    score = 0;
    lives = 3;
    document.getElementById('gameOver').style.display = 'none';
    
    // Reset game objects
    resetBall();
    createBricks();
    
    // Restart game loop
    gameRunning = true;
    requestAnimationFrame(draw);
}

// Start the game when page loads (but wait for start button)
window.onload = init;
