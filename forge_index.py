"""Nova Forge Project Indexer — auto-scan project structure for agent context.

Builds a compact project map on startup so the agent knows the codebase
without wasting turns on glob/read discovery. Cached to disk, incrementally
updated after file writes.

Usage:
    from forge_index import ProjectIndex
    idx = ProjectIndex(project_root)
    idx.scan()            # Full scan (< 500ms typical)
    idx.update(path)      # Incremental after write
    idx.to_context(budget) # Render for prompt injection (token-budgeted)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ── File type detection ──────────────────────────────────────────────────────

_TYPE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TSX",
    ".jsx": "JSX", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".md": "Markdown",
    ".sh": "Shell", ".bash": "Shell", ".sql": "SQL", ".toml": "TOML",
    ".cfg": "Config", ".ini": "Config", ".env": "Env", ".txt": "Text",
    ".xml": "XML", ".svg": "SVG", ".dockerfile": "Dockerfile",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".cpp": "C++", ".h": "C Header",
}

_SKIP_DIRS = {
    ".forge", ".git", "__pycache__", "node_modules", ".next", ".venv",
    "venv", "env", ".env", "dist", "build", ".cache", ".pytest_cache",
    "coverage", ".nyc_output", ".tox", "egg-info",
}

_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".mp3", ".mp4", ".wav", ".pdf", ".zip", ".gz",
    ".tar", ".bin", ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo",
    ".class", ".o", ".a", ".db", ".sqlite", ".sqlite3",
}

_ENTRY_POINTS = {
    "app.py", "main.py", "server.py", "index.py", "manage.py", "wsgi.py",
    "index.html", "index.js", "index.ts", "server.js", "server.ts",
    "app.js", "app.ts", "main.js", "main.ts",
}

_CONFIG_FILES = {
    "package.json", "requirements.txt", "pyproject.toml", "setup.py",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", ".env", ".env.example", "tsconfig.json", "vite.config.ts",
    "next.config.js", "next.config.mjs", "webpack.config.js",
    "tailwind.config.js", "postcss.config.js",
}


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FileEntry:
    """Metadata for a single file."""
    path: str          # Relative to project root
    file_type: str     # "Python", "JavaScript", etc.
    lines: int         # Line count
    size: int          # Bytes
    first_line: str    # First non-empty line (for description)
    is_entry: bool     # Entry point file?
    is_config: bool    # Configuration file?

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FileEntry:
        return cls(**d)


@dataclass
class ProjectIndex:
    """Indexed project structure with stack detection and file metadata."""

    project_root: Path
    files: dict[str, FileEntry] = field(default_factory=dict)
    dirs: dict[str, int] = field(default_factory=dict)  # dir path -> item count
    stack: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    total_lines: int = 0
    total_files: int = 0
    languages: dict[str, int] = field(default_factory=dict)  # lang -> file count
    scanned_at: float = 0.0

    def __post_init__(self):
        self.project_root = Path(self.project_root).resolve()

    # ── Scanning ─────────────────────────────────────────────────────────────

    def scan(self) -> "ProjectIndex":
        """Full project scan. Typically < 500ms for projects under 1000 files."""
        self.files.clear()
        self.dirs.clear()
        self.stack.clear()
        self.entry_points.clear()
        self.languages.clear()
        self.total_lines = 0
        self.total_files = 0

        for dirpath, dirnames, filenames in os.walk(self.project_root):
            # Prune skip dirs in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not d.startswith(".")
            ]

            rel_dir = os.path.relpath(dirpath, self.project_root)
            if rel_dir == ".":
                rel_dir = ""

            # Track directory with item count
            if rel_dir:
                self.dirs[rel_dir] = len(filenames) + len(dirnames)

            for fname in filenames:
                fpath = Path(dirpath) / fname
                ext = fpath.suffix.lower()

                # Skip binary files
                if ext in _BINARY_EXTS:
                    continue

                # Skip hidden files
                if fname.startswith(".") and fname not in (".env", ".env.example"):
                    continue

                rel_path = str(fpath.relative_to(self.project_root))
                file_type = _TYPE_MAP.get(ext, "")

                # Special case: Dockerfile without extension
                if fname == "Dockerfile" or fname.startswith("Dockerfile."):
                    file_type = "Dockerfile"

                if not file_type:
                    continue  # Skip unknown file types

                # Count lines and get first meaningful line
                lines = 0
                first_line = ""
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f):
                            lines += 1
                            if not first_line and line.strip() and not line.startswith("#!"):
                                stripped = line.strip()
                                # Skip common boilerplate
                                if not stripped.startswith(("import ", "from ", "require(", "use ", "#", "//", "/*")):
                                    first_line = stripped[:80]
                            if lines > 10000:  # Safety cap
                                break
                except Exception:
                    continue

                is_entry = fname in _ENTRY_POINTS
                is_config = fname in _CONFIG_FILES

                entry = FileEntry(
                    path=rel_path,
                    file_type=file_type,
                    lines=lines,
                    size=fpath.stat().st_size,
                    first_line=first_line,
                    is_entry=is_entry,
                    is_config=is_config,
                )

                self.files[rel_path] = entry
                self.total_files += 1
                self.total_lines += lines
                self.languages[file_type] = self.languages.get(file_type, 0) + 1

                if is_entry:
                    self.entry_points.append(rel_path)

        # Detect stack
        self.stack = self._detect_stack()
        self.scanned_at = time.time()
        return self

    def _detect_stack(self) -> list[str]:
        """Detect technology stack from files."""
        stack = []
        file_names = {Path(f).name for f in self.files}

        # Python
        if "requirements.txt" in file_names or "pyproject.toml" in file_names or "setup.py" in file_names:
            if any(f in file_names for f in ("app.py", "wsgi.py")):
                stack.append("Flask/Python")
            elif "manage.py" in file_names:
                stack.append("Django")
            else:
                stack.append("Python")

        # Node.js
        if "package.json" in file_names:
            # Check for frameworks
            pkg_path = self.project_root / "package.json"
            if pkg_path.exists():
                try:
                    pkg = json.loads(pkg_path.read_text())
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "next" in deps:
                        stack.append("Next.js")
                    elif "react" in deps:
                        stack.append("React")
                    elif "vue" in deps:
                        stack.append("Vue.js")
                    elif "express" in deps:
                        stack.append("Express/Node.js")
                    else:
                        stack.append("Node.js")
                except Exception:
                    stack.append("Node.js")

        # Static site
        if not stack and "index.html" in file_names:
            stack.append("Static HTML")

        # Docker
        if "Dockerfile" in file_names or "docker-compose.yml" in file_names:
            stack.append("Docker")

        return stack or ["Unknown"]

    # ── Incremental update ───────────────────────────────────────────────────

    def update(self, file_path: str | Path) -> None:
        """Update index for a single file after write/edit."""
        fpath = Path(file_path).resolve()
        try:
            rel_path = str(fpath.relative_to(self.project_root))
        except ValueError:
            return  # Outside project

        if not fpath.exists():
            # File deleted
            if rel_path in self.files:
                old = self.files.pop(rel_path)
                self.total_files -= 1
                self.total_lines -= old.lines
                self.languages[old.file_type] = self.languages.get(old.file_type, 1) - 1
            return

        ext = fpath.suffix.lower()
        file_type = _TYPE_MAP.get(ext, "")
        if not file_type:
            return

        # Count lines
        lines = 0
        first_line = ""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    lines += 1
                    if not first_line and line.strip() and not line.startswith("#!"):
                        stripped = line.strip()
                        if not stripped.startswith(("import ", "from ", "require(")):
                            first_line = stripped[:80]
                    if lines > 10000:
                        break
        except Exception:
            return

        # Update totals
        if rel_path in self.files:
            old = self.files[rel_path]
            self.total_lines += lines - old.lines
        else:
            self.total_files += 1
            self.total_lines += lines
            self.languages[file_type] = self.languages.get(file_type, 0) + 1

        self.files[rel_path] = FileEntry(
            path=rel_path,
            file_type=file_type,
            lines=lines,
            size=fpath.stat().st_size,
            first_line=first_line,
            is_entry=fpath.name in _ENTRY_POINTS,
            is_config=fpath.name in _CONFIG_FILES,
        )

    # ── Context rendering (token-budgeted) ───────────────────────────────────

    def to_context(self, budget_chars: int = 6000) -> str:
        """Render project index for prompt injection, respecting token budget.

        Args:
            budget_chars: Maximum characters for the rendered context.
                         Default 6000 (~1500 tokens) for 32K models.
                         Use 16000 (~4000 tokens) for 128K+ models.
        """
        if not self.files:
            return ""

        parts = []

        # Header
        stack_str = " + ".join(self.stack) if self.stack else "Unknown"
        lang_summary = ", ".join(
            f"{count} {lang}" for lang, count in
            sorted(self.languages.items(), key=lambda x: -x[1])[:5]
        )
        parts.append(
            f"## Project Structure (auto-indexed)\n"
            f"Stack: {stack_str}\n"
            f"Files: {self.total_files} files, {self.total_lines:,} lines ({lang_summary})"
        )

        # Entry points
        if self.entry_points:
            entries = []
            for ep in self.entry_points[:5]:
                entry = self.files.get(ep)
                if entry:
                    entries.append(f"  {ep} ({entry.file_type}, {entry.lines} lines)")
            if entries:
                parts.append("Entry points:\n" + "\n".join(entries))

        # Config files
        configs = [f for f in self.files.values() if f.is_config]
        if configs:
            cfg_list = [f.path for f in configs[:8]]
            parts.append("Config: " + ", ".join(cfg_list))

        # Check budget so far
        header_text = "\n".join(parts)
        remaining = budget_chars - len(header_text) - 100  # Reserve 100 for padding

        if remaining <= 0:
            return header_text

        # File tree — prioritize: entry points > config > by directory depth
        tree_lines = []
        sorted_files = sorted(
            self.files.values(),
            key=lambda f: (
                0 if f.is_entry else (1 if f.is_config else 2),
                f.path.count("/"),
                f.path,
            ),
        )

        # Build tree with directory grouping
        current_dir = None
        for entry in sorted_files:
            dir_part = str(Path(entry.path).parent)
            if dir_part == ".":
                dir_part = ""

            # New directory header
            if dir_part != current_dir:
                current_dir = dir_part
                if dir_part:
                    item_count = self.dirs.get(dir_part, 0)
                    tree_lines.append(f"  {dir_part}/")

            # File line
            size_str = _format_size(entry.size)
            desc = ""
            if entry.first_line:
                desc = f" — {entry.first_line}"
                # Truncate description to fit
                max_desc = 50
                if len(desc) > max_desc:
                    desc = desc[:max_desc] + "..."

            fname = Path(entry.path).name
            indent = "    " if current_dir else "  "
            line = f"{indent}{fname:<25} {entry.lines:>4} lines  {size_str:>6}{desc}"
            tree_lines.append(line)

            # Check budget
            if sum(len(l) for l in tree_lines) > remaining:
                tree_lines.append(f"  ... and {self.total_files - len(tree_lines)} more files")
                break

        if tree_lines:
            parts.append("\n" + "\n".join(tree_lines))

        return "\n".join(parts)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, cache_path: Path | None = None) -> None:
        """Save index to disk cache."""
        if cache_path is None:
            cache_path = self.project_root / ".forge" / "project-index.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "scanned_at": self.scanned_at,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "stack": self.stack,
            "entry_points": self.entry_points,
            "languages": self.languages,
            "files": {k: v.to_dict() for k, v in self.files.items()},
            "dirs": self.dirs,
        }
        cache_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, project_root: Path, cache_path: Path | None = None) -> "ProjectIndex | None":
        """Load index from disk cache. Returns None if cache missing/stale."""
        if cache_path is None:
            cache_path = Path(project_root) / ".forge" / "project-index.json"
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text())
            idx = cls(project_root=project_root)
            idx.scanned_at = data.get("scanned_at", 0)
            idx.total_files = data.get("total_files", 0)
            idx.total_lines = data.get("total_lines", 0)
            idx.stack = data.get("stack", [])
            idx.entry_points = data.get("entry_points", [])
            idx.languages = data.get("languages", {})
            idx.dirs = data.get("dirs", {})
            idx.files = {
                k: FileEntry.from_dict(v) for k, v in data.get("files", {}).items()
            }

            # Check staleness: if any tracked file has changed, rescan
            # Quick check: compare file count
            actual_count = sum(
                1 for _ in Path(project_root).rglob("*")
                if _.is_file()
                and _.suffix.lower() in _TYPE_MAP
                and not any(skip in _.parts for skip in _SKIP_DIRS)
            )
            if abs(actual_count - idx.total_files) > 5:
                return None  # Too stale, trigger rescan

            return idx
        except Exception:
            return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_size(size_bytes: int) -> str:
    """Format file size compactly."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_or_create_index(project_root: Path) -> ProjectIndex:
    """Load cached index or create fresh one."""
    idx = ProjectIndex.load(project_root)
    if idx is None:
        idx = ProjectIndex(project_root)
        idx.scan()
        idx.save()
    return idx
