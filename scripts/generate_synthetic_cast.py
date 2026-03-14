#!/usr/bin/env python3
"""Generate a synthetic asciinema .cast file that simulates a Nova Forge build.

This creates a realistic-looking recording with Rich-style ANSI output
for use before a real recording is captured.

Usage:
    python3 scripts/generate_synthetic_cast.py > web/demo.cast
"""

import json
import sys

# ANSI color codes matching Rich output
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
MAGENTA = "\x1b[35m"
BLUE = "\x1b[34m"
WHITE = "\x1b[37m"
BRIGHT_GREEN = "\x1b[92m"
BRIGHT_CYAN = "\x1b[96m"
BRIGHT_MAGENTA = "\x1b[95m"
BRIGHT_WHITE = "\x1b[97m"
BG_DARK = "\x1b[48;5;236m"

# Box drawing
BOX_TL = "\u250c"
BOX_TR = "\u2510"
BOX_BL = "\u2514"
BOX_BR = "\u2518"
BOX_H = "\u2500"
BOX_V = "\u2502"
CHECK = "\u2713"
BLOCK = "\u2588"
ARROW = "\u25b6"
BULLET = "\u2022"


def build_events():
    """Generate a sequence of [timestamp, "o", "data"] events."""
    events = []
    t = 0.0

    def emit(text, dt=0.0):
        nonlocal t
        t += dt
        events.append([round(t, 3), "o", text])

    def newline(dt=0.05):
        emit("\r\n", dt)

    def type_text(text, char_delay=0.035):
        """Simulate typing character by character."""
        for ch in text:
            emit(ch, char_delay)

    # === Shell startup ===
    emit(f"{BOLD}{BRIGHT_MAGENTA}")
    emit("  _   _                   _____                    \r\n", 0.05)
    emit(" | \\ | | _____   ____ _  |  ___|__  _ __ __ _  ___ \r\n", 0.02)
    emit(" |  \\| |/ _ \\ \\ / / _` | | |_ / _ \\| '__/ _` |/ _ \\\r\n", 0.02)
    emit(" | |\\  | (_) \\ V / (_| | |  _| (_) | | | (_| |  __/\r\n", 0.02)
    emit(" |_| \\_|\\___/ \\_/ \\__,_| |_|  \\___/|_|  \\__, |\\___|\r\n", 0.02)
    emit("                                         |___/     \r\n", 0.02)
    emit(RESET)
    newline(0.1)
    emit(f"  {DIM}v0.3.0 {RESET}{DIM}{BULLET} 7 models {BULLET} 8 formations {BULLET} 12 tools{RESET}\r\n", 0.1)
    emit(f"  {DIM}Model: {CYAN}nova-lite{RESET} {DIM}{BULLET} Autonomy: {GREEN}A2 Supervised{RESET}\r\n", 0.05)
    newline(0.3)
    emit(f"  {DIM}Type a description to build, or /help for commands.{RESET}\r\n", 0.1)
    newline(0.5)

    # === User types description ===
    emit(f"  {BRIGHT_MAGENTA}{BOLD}> {RESET}", 0.3)
    type_text("Build an expense tracker with charts and CSV export", 0.04)
    newline(0.5)
    newline(0.3)

    # === Interview phase (quick) ===
    emit(f"  {BRIGHT_CYAN}{BOLD}[Interview]{RESET} ", 0.8)
    emit(f"Analyzing project scope...\r\n", 0.05)
    emit(f"  {DIM}{BULLET} Stack: Flask + vanilla JS{RESET}\r\n", 0.6)
    emit(f"  {DIM}{BULLET} Risk: low{RESET}\r\n", 0.2)
    emit(f"  {DIM}{BULLET} Formation: feature-impl (4 agents){RESET}\r\n", 0.2)
    emit(f"  {BRIGHT_GREEN}{CHECK} Interview complete{RESET}\r\n", 0.3)
    newline(0.2)

    # === Planning phase ===
    emit(f"  {BRIGHT_CYAN}{BOLD}[Planning]{RESET} ", 0.5)
    emit(f"Generating spec and task decomposition...\r\n", 0.05)
    newline(0.3)

    # Spec generation progress
    emit(f"  {DIM}Generating spec.md... ", 1.2)
    emit(f"{BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.8)
    emit(f"  {DIM}Decomposing into tasks... ", 0.3)
    emit(f"{BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.6)
    emit(f"  {DIM}Topological sort (Kahn's algorithm)... ", 0.2)
    emit(f"{BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.3)
    newline(0.2)

    # Task table
    emit(f"  {BOLD}{BRIGHT_WHITE}Tasks: 5 total, 3 waves{RESET}\r\n", 0.3)
    emit(f"  {DIM}{BOX_TL}{BOX_H * 4}{BOX_H}{BOX_H * 32}{BOX_H}{BOX_H * 8}{BOX_H}{BOX_H * 12}{BOX_TR}{RESET}\r\n", 0.05)
    emit(f"  {DIM}{BOX_V}{RESET} {BOLD}#{RESET}  {DIM}{BOX_V}{RESET} {BOLD}Task{RESET}                           {DIM}{BOX_V}{RESET} {BOLD}Wave{RESET}   {DIM}{BOX_V}{RESET} {BOLD}Files{RESET}      {DIM}{BOX_V}{RESET}\r\n", 0.05)
    emit(f"  {DIM}{BOX_V}{BOX_H * 4}{BOX_V}{BOX_H * 32}{BOX_V}{BOX_H * 8}{BOX_V}{BOX_H * 12}{BOX_V}{RESET}\r\n", 0.05)

    tasks = [
        ("1", "Create Flask app + data models", "1", "app.py, models.py"),
        ("2", "Build expense CRUD API", "1", "routes.py"),
        ("3", "Chart.js dashboard UI", "2", "templates/, static/"),
        ("4", "CSV export endpoint", "2", "export.py"),
        ("5", "Integration + polish", "3", "app.py, style.css"),
    ]
    for tid, name, wave, files in tasks:
        emit(f"  {DIM}{BOX_V}{RESET} {CYAN}{tid}{RESET}  {DIM}{BOX_V}{RESET} {name:<30} {DIM}{BOX_V}{RESET} {YELLOW}{wave}{RESET}      {DIM}{BOX_V}{RESET} {DIM}{files:<10}{RESET} {DIM}{BOX_V}{RESET}\r\n", 0.08)

    emit(f"  {DIM}{BOX_BL}{BOX_H * 4}{BOX_H}{BOX_H * 32}{BOX_H}{BOX_H * 8}{BOX_H}{BOX_H * 12}{BOX_BR}{RESET}\r\n", 0.05)
    newline(0.5)

    # === Build phase ===
    emit(f"  {BRIGHT_GREEN}{BOLD}[Building]{RESET} Starting parallel build...\r\n", 0.5)
    newline(0.2)

    # Wave 1 — two tasks in parallel
    emit(f"  {BRIGHT_CYAN}{BOLD}Wave 1/3{RESET} {DIM}(2 tasks, parallel){RESET}\r\n", 0.3)

    # Progress for task 1
    emit(f"    {ARROW} {BOLD}T1{RESET} Create Flask app + data models ", 0.3)
    for i in range(20):
        pct = (i + 1) * 5
        bar = BRIGHT_GREEN + BLOCK * (i + 1) + DIM + BLOCK * (19 - i) + RESET
        emit(f"\r    {ARROW} {BOLD}T1{RESET} Create Flask app + data models {bar} {pct:3d}%", 0.15)
    emit(f" {BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.1)

    # Progress for task 2
    emit(f"    {ARROW} {BOLD}T2{RESET} Build expense CRUD API ", 0.1)
    for i in range(20):
        pct = (i + 1) * 5
        bar = BRIGHT_GREEN + BLOCK * (i + 1) + DIM + BLOCK * (19 - i) + RESET
        emit(f"\r    {ARROW} {BOLD}T2{RESET} Build expense CRUD API {bar} {pct:3d}%", 0.12)
    emit(f" {BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.1)

    emit(f"  {BRIGHT_GREEN}{CHECK} Wave 1 complete{RESET} {DIM}(2/2 tasks, 12.4s){RESET}\r\n", 0.3)
    newline(0.2)

    # Wave 2
    emit(f"  {BRIGHT_CYAN}{BOLD}Wave 2/3{RESET} {DIM}(2 tasks, parallel){RESET}\r\n", 0.3)

    emit(f"    {ARROW} {BOLD}T3{RESET} Chart.js dashboard UI ", 0.2)
    for i in range(20):
        pct = (i + 1) * 5
        bar = BRIGHT_GREEN + BLOCK * (i + 1) + DIM + BLOCK * (19 - i) + RESET
        emit(f"\r    {ARROW} {BOLD}T3{RESET} Chart.js dashboard UI {bar} {pct:3d}%", 0.18)
    emit(f" {BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.1)

    emit(f"    {ARROW} {BOLD}T4{RESET} CSV export endpoint ", 0.1)
    for i in range(20):
        pct = (i + 1) * 5
        bar = BRIGHT_GREEN + BLOCK * (i + 1) + DIM + BLOCK * (19 - i) + RESET
        emit(f"\r    {ARROW} {BOLD}T4{RESET} CSV export endpoint {bar} {pct:3d}%", 0.1)
    emit(f" {BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.1)

    emit(f"  {BRIGHT_GREEN}{CHECK} Wave 2 complete{RESET} {DIM}(2/2 tasks, 18.2s){RESET}\r\n", 0.3)
    newline(0.2)

    # Wave 3
    emit(f"  {BRIGHT_CYAN}{BOLD}Wave 3/3{RESET} {DIM}(1 task){RESET}\r\n", 0.3)

    emit(f"    {ARROW} {BOLD}T5{RESET} Integration + polish ", 0.2)
    for i in range(20):
        pct = (i + 1) * 5
        bar = BRIGHT_GREEN + BLOCK * (i + 1) + DIM + BLOCK * (19 - i) + RESET
        emit(f"\r    {ARROW} {BOLD}T5{RESET} Integration + polish {bar} {pct:3d}%", 0.14)
    emit(f" {BRIGHT_GREEN}{CHECK}{RESET}\r\n", 0.1)

    emit(f"  {BRIGHT_GREEN}{CHECK} Wave 3 complete{RESET} {DIM}(1/1 tasks, 9.8s){RESET}\r\n", 0.3)
    newline(0.3)

    # === Gate Review ===
    emit(f"  {BRIGHT_MAGENTA}{BOLD}[Gate Review]{RESET} Running adversarial verification...\r\n", 0.5)
    newline(0.2)

    checks = [
        "Syntax valid (all .py files)",
        "Flask app importable",
        "Routes registered: /add, /list, /export, /dashboard",
        "Chart.js included in templates",
        "CSV export produces valid output",
    ]
    for i, check in enumerate(checks):
        emit(f"    {BRIGHT_GREEN}{CHECK}{RESET} {check}\r\n", 0.3)

    newline(0.2)
    emit(f"  {BOLD}{BRIGHT_GREEN}PASS{RESET} {DIM}{BULLET} 25/25 checks {BULLET} 0 warnings {BULLET} 0 failures{RESET}\r\n", 0.5)
    newline(0.3)

    # === Summary ===
    emit(f"  {BOX_TL}{BOX_H * 58}{BOX_TR}\r\n", 0.1)
    emit(f"  {BOX_V} {BOLD}{BRIGHT_GREEN}Build Complete{RESET}                                         {BOX_V}\r\n", 0.1)
    emit(f"  {BOX_V}                                                          {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Tasks:{RESET}     {BRIGHT_WHITE}5/5 passed{RESET}                                {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Waves:{RESET}     {BRIGHT_WHITE}3{RESET}                                        {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Duration:{RESET}  {BRIGHT_CYAN}68 seconds{RESET}                               {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Model:{RESET}     {CYAN}nova-lite{RESET} {DIM}(Bedrock){RESET}                     {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Cost:{RESET}      {BRIGHT_GREEN}$0.003{RESET}                                   {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Retries:{RESET}   {BRIGHT_WHITE}0{RESET}                                        {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Files:{RESET}     {BRIGHT_WHITE}8 created{RESET}                                 {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}                                                          {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_V}  {DIM}Run {CYAN}/preview{RESET}{DIM} for a shareable URL{RESET}                    {BOX_V}\r\n", 0.05)
    emit(f"  {BOX_BL}{BOX_H * 58}{BOX_BR}\r\n", 0.05)
    newline(0.8)

    # === Prompt again ===
    emit(f"  {BRIGHT_MAGENTA}{BOLD}> {RESET}", 1.0)

    return events


def main():
    header = {
        "version": 2,
        "width": 100,
        "height": 30,
        "timestamp": 1741795200,
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "Nova Forge Demo Build",
    }

    events = build_events()

    # Set duration from last event
    if events:
        header["duration"] = events[-1][0]

    # Output
    output = sys.stdout
    output.write(json.dumps(header) + "\n")
    for event in events:
        output.write(json.dumps(event) + "\n")


if __name__ == "__main__":
    main()
