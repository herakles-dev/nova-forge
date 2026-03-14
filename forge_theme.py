"""Nova Forge Design Token System — single source of truth for visual identity.

Brand palette derived from forge.herakles.dev (web/style.css).
Provides: BRAND colors, Rich Theme, shared Console, spinners, icons, and helpers.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

# ── Brand palette (matches web/style.css :root) ──────────────────────────────

BRAND = {
    "accent":  "#a78bfa",   # purple
    "accent2": "#c084fc",   # magenta
    "cyan":    "#67e8f9",
    "green":   "#4ade80",
    "orange":  "#fb923c",
    "red":     "#f87171",
    "muted":   "#7a7a94",
    "dim":     "#4a4a62",
    "text":    "#e4e4f0",
}

# Gradient stops for character-level interpolation (purple → magenta → cyan)
_GRADIENT = [
    (167, 139, 250),   # #a78bfa
    (192, 132, 252),   # #c084fc
    (232, 121, 249),   # #e879f9
    (192, 132, 252),   # #c084fc
    (103, 232, 249),   # #67e8f9
]

# ── Rich Theme (superset of both forge_cli + forge_display themes) ────────────

THEME = Theme({
    # Semantic
    "info":       "cyan",
    "success":    "bold green",
    "warning":    "bold yellow",
    "error":      "bold red",
    "muted":      "dim",
    "hint":       "italic dim cyan",
    "step":       "bold white",
    # Brand
    "accent":     f"bold {BRAND['cyan']}",
    "nova":       f"bold {BRAND['accent2']}",
    "brand":      f"bold {BRAND['accent2']}",
    # Build states
    "tool":       "dim cyan",
    "blocked":    "dim yellow",
    "retry":      "yellow",
    # File actions
    "file.read":  "dim",
    "file.write": "green",
    "file.edit":  "yellow",
    "file.run":   "cyan",
    # Waves
    "wave":       f"bold {BRAND['accent']}",
    "wave.count": f"{BRAND['cyan']}",
    # Interview phases
    "phase.core":   f"bold {BRAND['accent2']}",
    "phase.deep":   f"bold {BRAND['accent']}",
    "phase.review": f"bold {BRAND['cyan']}",
})

console = Console(theme=THEME)

# ── Spinner presets ───────────────────────────────────────────────────────────

SPINNERS = {
    "thinking":  "dots",
    "building":  "arc",
    "searching": "point",
    "deploying": "moon",
}

# ── Tool icons (emoji-prefixed) ──────────────────────────────────────────────

TOOL_ICONS = {
    "read_file":   "\U0001f441  ",   # eye
    "write_file":  "\u270f\ufe0f  ",  # pencil
    "edit_file":   "\U0001f527 ",     # wrench
    "bash":        "\U0001f4bb ",     # terminal
    "glob_files":  "\U0001f50d ",     # magnifying glass
    "grep":        "\U0001f50e ",     # right-pointing magnifying glass
    "append_file": "\u2795 ",         # plus
    "list_dir":    "\U0001f4c2 ",     # open folder
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    """Linearly interpolate between two RGB tuples, return hex string."""
    r = int(c1[0] + (c2[0] - c1[0]) * t)
    g = int(c1[1] + (c2[1] - c1[1]) * t)
    b = int(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def gradient_text(text: str) -> Text:
    """Character-level color interpolation (purple -> cyan) for headers."""
    result = Text()
    chars = [c for c in text if c.strip()]  # non-space chars for gradient
    total = max(len(chars) - 1, 1)
    ci = 0
    for ch in text:
        if not ch.strip():
            result.append(ch)
            continue
        # Map character index to gradient position
        pos = ci / total * (len(_GRADIENT) - 1)
        seg = int(pos)
        seg = min(seg, len(_GRADIENT) - 2)
        t = pos - seg
        color = _lerp_color(_GRADIENT[seg], _GRADIENT[seg + 1], t)
        result.append(ch, style=f"bold {color}")
        ci += 1
    return result


def wave_header(idx: int, total: int, task_count: int) -> Text:
    """Styled wave header line: ━━ Wave 1/3 ━━━━━ 4 tasks"""
    label = f" Wave {idx + 1}/{total} "
    suffix = f" {task_count} task{'s' if task_count != 1 else ''}"
    bar_width = max(40 - len(label) - len(suffix), 4)
    line = f"\u2501\u2501{label}" + "\u2501" * bar_width + suffix

    result = Text()
    result.append("\u2501\u2501", style=f"bold {BRAND['accent']}")
    result.append(label, style=f"bold {BRAND['accent2']}")
    result.append("\u2501" * bar_width, style=f"bold {BRAND['accent']}")
    result.append(suffix, style=f"{BRAND['cyan']}")
    return result


def status_bar(done: int, total: int, width: int = 30) -> str:
    """Block-char progress bar (█/░) with color by percentage."""
    if total == 0:
        return "\u2591" * width + " 0%"
    pct = done / total
    filled = int(width * pct)
    if pct >= 1.0:
        color = BRAND["green"]
    elif pct > 0.5:
        color = BRAND["cyan"]
    else:
        color = BRAND["accent"]
    filled_str = "\u2588" * filled
    empty_str = "\u2591" * (width - filled)
    bar = f"[{color}]{filled_str}[/][dim]{empty_str}[/]"
    return f"{bar} {pct:.0%}"


def file_tree(files: list[str], base_path: str = "") -> Tree:
    """Build a Rich Tree from a flat file list with directory grouping."""
    label = Path(base_path).name if base_path else "project"
    tree = Tree(f"[bold {BRAND['accent2']}]{label}/[/]")

    # Group files by top-level directory
    dirs: dict[str, list[str]] = {}
    root_files: list[str] = []
    for f in sorted(files):
        parts = Path(f).parts
        if len(parts) > 1:
            d = parts[0]
            dirs.setdefault(d, []).append(str(Path(*parts[1:])))
        else:
            root_files.append(f)

    # Add directories
    for d in sorted(dirs):
        branch = tree.add(f"[bold {BRAND['cyan']}]{d}/[/]")
        for f in dirs[d][:10]:
            branch.add(f"[{BRAND['text']}]{f}[/]")
        remaining = len(dirs[d]) - 10
        if remaining > 0:
            branch.add(f"[dim]+{remaining} more[/]")

    # Add root files
    for f in root_files:
        tree.add(f"[{BRAND['text']}]{f}[/]")

    return tree
