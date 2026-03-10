"""Nova Forge security guards — RiskClassifier, PathSandbox, OwnershipChecker,
SyntaxVerifier, and AutonomyManager.

Ports V11's hook-based security model (common.sh) into pure Python with
enhancements: interpreter-wrapping detection, sensitive-file reads, 1-hour
cooldown on re-escalation, and persistent error_history.
"""

import ast
import fnmatch
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from filelock import FileLock

from config import ForgeProject, HERCULES_ROOT  # noqa: F401 — re-exported for callers


# ── RiskLevel ─────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── RiskClassifier ────────────────────────────────────────────────────────────

# All 21 HIGH patterns ported verbatim from v11_check_risk (common.sh:121-145),
# plus additions from the spec (kill -9, > /dev/, iptables, passwd, useradd,
# userdel, chmod 777, curl|bash).  Pattern → human-readable description.
_HIGH_PATTERNS: list[tuple[str, str]] = [
    # rm recursive variants (covers -rf/-fr/-Rf/-fR/-r/-R/--recursive)
    (r"(?:^|[\s;|&])rm\s+(?:\S+\s+)*-[rRfF]*[rR]",      "Recursive force delete"),
    (r"(?:^|[\s;|&])rm\s+--recursive",                    "Recursive delete"),
    # SQL destructive
    (r"DROP\s+(?:DATABASE|TABLE)",                         "Database/table deletion"),
    (r"TRUNCATE",                                          "Table truncation"),
    (r"DELETE\s+FROM",                                     "Data deletion"),
    # Docker destructive
    (r"docker\s+system\s+prune\s+-a",                     "Delete all Docker images"),
    (r"docker\s+volume\s+rm",                              "Docker volume deletion"),
    (r"docker\s+rm\b",                                     "Docker container removal"),
    (r"docker\s+rmi\b",                                    "Docker image removal"),
    # Git destructive
    (r"git\s+push\s+--force|git\s+push\s+-f\b",           "Force push to remote"),
    (r"git\s+reset\s+--hard",                              "Hard reset (loses commits)"),
    (r"git\s+clean\s+-f\b",                                "Force clean untracked files"),
    # System destructive
    (r"(?:^|[\s;|&])shutdown\b",                           "System shutdown"),
    (r"(?:^|[\s;|&])reboot\b",                             "System reboot"),
    (r"(?:^|[\s;|&])mkfs\b",                               "Format filesystem"),
    (r"(?:^|[\s;|&])dd\s+if=",                             "Direct disk write"),
    (r"(?:^|[\s;|&])kill\s+-9\b",                          "Force process kill"),
    # Permission / write to device
    (r"chmod\s+(?:-R\s+)?777",                             "Dangerous permission change"),
    (r"chown\s+-R\b",                                      "Recursive ownership change"),
    (r">\s*/dev/(?!null|zero|stdin|stdout|stderr)\S",      "Write to raw device"),
    (r"(?:^|[\s;|&])iptables\b",                           "Firewall rule change"),
    # System user management
    (r"(?:^|[\s;|&])passwd\b",                             "Password change"),
    (r"(?:^|[\s;|&])useradd\b",                            "User creation"),
    (r"(?:^|[\s;|&])userdel\b",                            "User deletion"),
    # Pipe-to-shell (curl|bash, wget|bash)
    (r"(?:curl|wget)\s+.*\|\s*(?:ba)?sh",                  "Pipe remote content to shell"),
    # systemctl stop/disable
    (r"systemctl\s+(?:stop|disable)\b",                    "Service stop/disable"),
]

# Patterns that wrap a real command — extract the wrapped argument and re-classify.
# After the -c/-e flag we capture the remainder of the string; the inner
# content is then re-checked by _matches_high.  We strip outer quotes if
# present so that `bash -c 'rm -rf /'` and `bash -c rm\ -rf\ /` both work.
_INTERPRETER_PATTERNS: list[re.Pattern] = [
    # Shell interpreters with -c
    re.compile(r"(?:^|[\s;|&])(?:bash|sh|dash|zsh)\s+-c\s+(.+)$",   re.IGNORECASE),
    # Python with -c
    re.compile(r"(?:^|[\s;|&])python3?\s+-c\s+(.+)$",               re.IGNORECASE),
    # Perl with -e
    re.compile(r"(?:^|[\s;|&])perl\s+-e\s+(.+)$",                   re.IGNORECASE),
    # Ruby with -e
    re.compile(r"(?:^|[\s;|&])ruby\s+-e\s+(.+)$",                   re.IGNORECASE),
    # env COMMAND ... (used to bypass PATH restrictions)
    re.compile(r"(?:^|[\s;|&])env\s+(\S.+)$",                       re.IGNORECASE),
    # xargs rm ...
    re.compile(r"(?:^|[\s;|&])xargs\s+(rm\S*\s+.+)$",               re.IGNORECASE),
    # find -exec rm ...
    re.compile(r"find\s+.+-exec\s+(rm\s+.+?)\s*[;\\]",              re.IGNORECASE),
]


def _strip_outer_quotes(s: str) -> str:
    """Remove a single pair of matching outer quotes from *s*."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s

# Sensitive file patterns → MEDIUM risk (flagged but not blocked outright).
_SENSITIVE_READ_PATTERNS: list[re.Pattern] = [
    re.compile(r"~/\.secrets/"),
    re.compile(r"/home/\w+/\.secrets/"),
    re.compile(r"~/\.ssh/"),
    re.compile(r"/home/\w+/\.ssh/"),
    re.compile(r"/etc/shadow\b"),
    re.compile(r"/etc/passwd\b"),
    re.compile(r"\.env\b"),
    re.compile(r"\.env\."),
    re.compile(r"/etc/sudoers\b"),
]

# Bash commands that are MEDIUM risk (stateful but not inherently destructive).
_MEDIUM_BASH_TOKENS = frozenset({"docker", "systemctl", "pip", "pip3", "npm",
                                   "yarn", "pnpm", "apt", "apt-get", "brew"})

# Tool names that are always MEDIUM (Write/Edit on non-metadata files).
_MEDIUM_TOOLS = frozenset({"Write", "Edit"})

# Commands that expose environment variables → MEDIUM.
_ENV_INSPECT_RE = re.compile(r"(?:^|[\s;|&])(?:env|printenv|set)\b")


def _resolve_cmd_token(cmd: str) -> str:
    """Return the first token of *cmd* with its PATH-resolved basename."""
    if not cmd:
        return ""
    first = cmd.split()[0]
    resolved = shutil.which(first)
    if resolved:
        return Path(resolved).name
    # If already an absolute path, return basename.
    if first.startswith("/"):
        return Path(first).name
    return first


def _matches_high(cmd: str, loose: bool = False) -> Optional[str]:
    """Return a description string if *cmd* matches any HIGH pattern, else None.

    When *loose* is True (used for interpreter-unwrapped inner commands) the
    boundary anchors are relaxed so that tokens inside quotes/parens still
    match (e.g. ``os.system("rm -rf /")`` → HIGH).
    """
    for pattern, description in _HIGH_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return description
    if loose:
        # Relax boundary: just look for the dangerous token anywhere.
        _LOOSE_HIGH = [
            (r"\brm\s+.*-[rRfF]*[rR]",              "Recursive force delete"),
            (r"\brm\s+--recursive",                   "Recursive delete"),
            (r"\bDROP\s+(?:DATABASE|TABLE)\b",        "Database/table deletion"),
            (r"\bDELETE\s+FROM\b",                    "Data deletion"),
            (r"\bTRUNCATE\b",                         "Table truncation"),
            (r"\bshutdown\b",                         "System shutdown"),
            (r"\breboot\b",                           "System reboot"),
            (r"\bmkfs\b",                             "Format filesystem"),
            (r"\bdd\s+if=",                           "Direct disk write"),
            (r"\bkill\s+-9\b",                        "Force process kill"),
            (r"\biptables\b",                         "Firewall rule change"),
            (r"\bpasswd\b",                           "Password change"),
            (r"\buseradd\b",                          "User creation"),
            (r"\buserdel\b",                          "User deletion"),
            (r"(?:curl|wget)\s+.*\|\s*(?:ba)?sh",     "Pipe to shell"),
            (r"\bsystemctl\s+(?:stop|disable)\b",     "Service stop/disable"),
        ]
        for pattern, description in _LOOSE_HIGH:
            if re.search(pattern, cmd, re.IGNORECASE):
                return description
    return None


def _unwrap_interpreter(cmd: str) -> Optional[str]:
    """If *cmd* is interpreter-wrapped, return the inner command string, else None.

    The returned string is stripped of one outer quote pair so that downstream
    callers can use _matches_high with loose=True.
    """
    for pat in _INTERPRETER_PATTERNS:
        m = pat.search(cmd)
        if m:
            return _strip_outer_quotes(m.group(1))
    return None


class RiskClassifier:
    """Pure-Python risk classifier.

    Phase 1 — HIGH pattern matching (V11 port).
    Phase 2 — PATH-resolved command token re-matching.
    Phase 3 — Interpreter wrapping detection.
    Phase 4 — Sensitive file access → MEDIUM.
    """

    def classify(
        self,
        tool_name: str,
        command: str = "",
        file_path: str = "",
    ) -> RiskLevel:
        """Classify a tool invocation as LOW, MEDIUM, or HIGH risk."""
        # Phase 1: direct HIGH pattern match.
        desc = _matches_high(command)
        if desc:
            return RiskLevel.HIGH

        # Phase 2: resolve first command token through PATH and re-check.
        if command:
            resolved_base = _resolve_cmd_token(command)
            if resolved_base and resolved_base != command.split()[0]:
                # Rebuild command with resolved basename and re-check.
                rebuilt = resolved_base + command[len(command.split()[0]):]
                if _matches_high(rebuilt):
                    return RiskLevel.HIGH

        # Phase 3: interpreter wrapping — extract inner command and re-check.
        # Use loose matching on the inner text (rm may appear inside parens/quotes).
        if command:
            inner = _unwrap_interpreter(command)
            if inner:
                if _matches_high(inner, loose=True):
                    return RiskLevel.HIGH
                # Recurse for full classification (catches stateful tokens etc.).
                inner_risk = self.classify("Bash", inner, file_path)
                if inner_risk == RiskLevel.HIGH:
                    return RiskLevel.HIGH
                if inner_risk == RiskLevel.MEDIUM:
                    return RiskLevel.MEDIUM

        # Phase 4: sensitive file access → MEDIUM.
        target = file_path or command
        if target:
            for pat in _SENSITIVE_READ_PATTERNS:
                if pat.search(target):
                    return RiskLevel.MEDIUM
        # Env inspection commands → MEDIUM.
        if command and _ENV_INSPECT_RE.search(command):
            return RiskLevel.MEDIUM

        # MEDIUM: Write/Edit tools on non-metadata files.
        if tool_name in _MEDIUM_TOOLS:
            return RiskLevel.MEDIUM

        # MEDIUM: Bash with stateful command tokens.
        if tool_name == "Bash" and command:
            first_token = _resolve_cmd_token(command)
            if first_token in _MEDIUM_BASH_TOKENS:
                return RiskLevel.MEDIUM
            # Also check secondary tokens (e.g., "sudo pip install").
            tokens = command.split()
            for tok in tokens[:4]:
                clean = Path(tok).name if "/" in tok else tok
                if clean in _MEDIUM_BASH_TOKENS:
                    return RiskLevel.MEDIUM

        return RiskLevel.LOW


# ── PathSandbox ───────────────────────────────────────────────────────────────

_DEFAULT_DENIED_PATHS: list[Path] = [
    Path.home() / ".secrets",
    Path.home() / ".ssh",
    Path("/etc/shadow"),
    Path("/etc/passwd"),
    Path("/etc/sudoers"),
]

_DENIED_NAME_PATTERNS = re.compile(r"^\.env(\.|$)")


class SandboxViolation(Exception):
    """Raised when a path access violates sandbox policy."""


class PathSandbox:
    """Enforce path-based read/write restrictions for an agent.

    Write policy: only paths inside *project_root* (and *extra_allowed*) are
    permitted.

    Read policy: any path matching the deny list raises SandboxViolation.
    """

    def __init__(
        self,
        project_root: Path,
        extra_allowed: Optional[list[Path]] = None,
        extra_denied: Optional[list[Path]] = None,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._extra_allowed: list[Path] = [
            Path(p).resolve() for p in (extra_allowed or [])
        ]
        self._denied: list[Path] = list(_DEFAULT_DENIED_PATHS) + [
            Path(p).resolve() for p in (extra_denied or [])
        ]

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        # Expand ~ but do NOT resolve symlinks — prevents escape via symlink.
        return Path(str(p).replace("~", str(Path.home())))

    def validate_write(self, path: str | Path) -> None:
        """Raise SandboxViolation if *path* is outside the write allowlist."""
        resolved = self._resolve(path).resolve()
        # Check project root (most common case).
        try:
            resolved.relative_to(self._root)
            return  # Allowed.
        except ValueError:
            pass
        # Check extra allowed paths.
        for allowed in self._extra_allowed:
            try:
                resolved.relative_to(allowed)
                return
            except ValueError:
                continue
        raise SandboxViolation(
            f"Write denied: {path!r} is outside the allowed sandbox.\n"
            f"  Project root : {self._root}\n"
            f"  Extra allowed: {[str(p) for p in self._extra_allowed] or 'none'}"
        )

    def validate_read(self, path: str | Path) -> None:
        """Raise SandboxViolation if *path* is in the read deny list."""
        resolved = self._resolve(path).resolve()
        # Name-based .env check.
        if _DENIED_NAME_PATTERNS.match(resolved.name):
            raise SandboxViolation(
                f"Read denied: {path!r} matches sensitive .env pattern."
            )
        # Explicit deny paths.
        for denied in self._denied:
            denied_r = denied.resolve()
            # Exact match or subtree match.
            if resolved == denied_r:
                raise SandboxViolation(
                    f"Read denied: {path!r} matches sensitive path {denied}."
                )
            try:
                resolved.relative_to(denied_r)
                raise SandboxViolation(
                    f"Read denied: {path!r} is inside sensitive directory {denied}."
                )
            except ValueError:
                continue


# ── OwnershipChecker ──────────────────────────────────────────────────────────

class OwnershipChecker:
    """Port of v11_check_file_ownership (common.sh:328-423).

    3-tier matching: exact file → directory prefix → glob pattern.
    Permissive by default when agent is not in the registry.
    """

    def check(
        self,
        file_path: str,
        agent_id: str,
        registry: dict,
    ) -> bool:
        """Return True if *agent_id* may write *file_path*.

        *registry* is the parsed .formation-registry.json dict.

        If the agent is not listed in the registry the call returns True
        (permissive default for non-formation work).  If the agent IS listed
        but the file is owned by a different teammate, returns False.
        """
        teammates: dict = registry.get("teammates", {})
        if not teammates:
            return True  # No registry data — allow.

        for role, teammate_data in teammates.items():
            if not isinstance(teammate_data, dict):
                continue
            owner_id: str = teammate_data.get("agent_id", "")
            ownership: dict = teammate_data.get("ownership", {})

            # Tier 1: exact file match.
            files: list[str] = ownership.get("files", [])
            if file_path in files:
                return owner_id == agent_id

            # Tier 2: directory prefix match.
            directories: list[str] = ownership.get("directories", [])
            for directory in directories:
                if file_path.startswith(directory):
                    return owner_id == agent_id

            # Tier 3: glob pattern match (fnmatch).
            patterns: list[str] = ownership.get("patterns", [])
            for pattern in patterns:
                if fnmatch.fnmatch(file_path, pattern):
                    return owner_id == agent_id

        # File not claimed by anyone — allow (new file).
        return True


# ── SyntaxVerifier ────────────────────────────────────────────────────────────

@dataclass
class SyntaxResult:
    valid: bool
    error: Optional[str]
    language: str


class SyntaxVerifier:
    """Check syntax of written files without invoking any shell."""

    def check(self, file_path: str | Path) -> SyntaxResult:
        """Return a SyntaxResult for *file_path*.

        Python: ast.parse.  JSON: json.loads.  YAML: yaml.safe_load.
        All other extensions are passed as valid without inspection.

        SECURITY: file paths are never interpolated into shell commands.
        """
        p = Path(file_path)
        suffix = p.suffix.lower()

        if not p.exists():
            return SyntaxResult(valid=False, error="File not found", language="unknown")

        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return SyntaxResult(valid=False, error=str(exc), language="unknown")

        if suffix == ".py":
            return self._check_python(source)
        if suffix == ".json":
            return self._check_json(source)
        if suffix in (".yml", ".yaml"):
            return self._check_yaml(source)

        return SyntaxResult(valid=True, error=None, language=suffix.lstrip(".") or "text")

    # ── private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _check_python(source: str) -> SyntaxResult:
        try:
            ast.parse(source)
            return SyntaxResult(valid=True, error=None, language="python")
        except SyntaxError as exc:
            msg = f"SyntaxError at line {exc.lineno}: {exc.msg}"
            return SyntaxResult(valid=False, error=msg, language="python")

    @staticmethod
    def _check_json(source: str) -> SyntaxResult:
        try:
            json.loads(source)
            return SyntaxResult(valid=True, error=None, language="json")
        except json.JSONDecodeError as exc:
            return SyntaxResult(valid=False, error=str(exc), language="json")

    @staticmethod
    def _check_yaml(source: str) -> SyntaxResult:
        try:
            import yaml  # Optional dependency — tested at import time.
            yaml.safe_load(source)
            return SyntaxResult(valid=True, error=None, language="yaml")
        except ImportError:
            return SyntaxResult(valid=True, error=None, language="yaml")  # Skip if absent.
        except Exception as exc:  # yaml.YAMLError and subclasses.
            return SyntaxResult(valid=False, error=str(exc), language="yaml")


# ── AutonomyManager ───────────────────────────────────────────────────────────

@dataclass
class AutonomyResult:
    allowed: bool
    reason: str


# Escalation thresholds (V11: A0→A1 at 5, A1→A2 at 10, A2→A3 at 25).
_ESCALATION_THRESHOLDS = {0: 5, 1: 10, 2: 25}

# Level names matching V11.
_LEVEL_NAMES = {0: "Manual", 1: "Guided", 2: "Supervised", 3: "Trusted", 4: "Autonomous"}

# Extension → category mapping (V11 common.sh:295-308).
_EXT_TO_CATEGORY: dict[str, str] = {
    "ts": "typescript", "tsx": "typescript", "js": "typescript", "jsx": "typescript",
    "py": "python",
    "sh": "shell", "bash": "shell",
    "md": "markdown",
    "json": "config", "yaml": "config", "yml": "config",
}

# Command prefix → category (V11 common.sh:302-307).
_CMD_TO_CATEGORY: dict[str, str] = {
    "git": "git",
    "docker": "docker",
    "npm": "npm", "yarn": "npm", "pnpm": "npm",
}

_DE_ESCALATION_WINDOW = timedelta(minutes=10)
_COOLDOWN_DURATION = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class AutonomyManager:
    """Full A0-A4 port of v11_check_autonomy and v11_check_risk (common.sh:221-322).

    State is persisted to *autonomy_file* (JSON) with file locking.
    The file path is fixed at init time — agents cannot redirect writes.
    """

    def __init__(self, autonomy_file: Path) -> None:
        # SECURITY (C-4): path locked at construction; _save() uses only this.
        self._state_path = Path(autonomy_file).resolve()
        self._lock_path = self._state_path.with_suffix(".lock")
        self._state: dict = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_level(self) -> int:
        """Return the current autonomy level (0-4)."""
        return int(self._state.get("level", 0))

    def check_permission(self, risk: RiskLevel) -> bool:
        """Check if the current autonomy level permits an operation of *risk*.

        Simple facade over the full check() logic — used by ForgeAgent during
        tool execution when file/command context is not yet needed for the
        permission decision.

        A0 (Manual)    — nothing allowed automatically.
        A1 (Guided)    — only LOW risk allowed.
        A2 (Supervised)— LOW and MEDIUM allowed; HIGH blocked.
        A3 (Trusted)   — LOW and MEDIUM and HIGH allowed.
        A4 (Autonomous)— everything allowed (includes any future CRITICAL level).
        """
        level = self.current_level
        if level >= 4:
            # A4: Autonomous — everything allowed.
            return True
        if level >= 3:
            # A3: Trusted — allow LOW, MEDIUM, HIGH; block only hypothetical CRITICAL.
            return risk in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        if level >= 2:
            # A2: Supervised — allow LOW and MEDIUM; block HIGH.
            return risk in (RiskLevel.LOW, RiskLevel.MEDIUM)
        if level >= 1:
            # A1: Guided — only LOW allowed.
            return risk == RiskLevel.LOW
        # A0: Manual — block everything (all need explicit confirmation).
        return False

    def record_build_result(self, passed: int, failed: int, total: int) -> None:
        """Record a build outcome for trust scoring.

        A clean build (no failures) counts as a success; any failure counts
        as an error.  Both paths call save() to persist the updated state.
        """
        if failed == 0 and total > 0:
            self.record_success()
        elif failed > 0:
            self.record_error()
        self._save()

    def record_success(self) -> None:
        """Increment successful_actions and potentially escalate level."""
        now = _utcnow()
        self._state["successful_actions"] = self._state.get("successful_actions", 0) + 1
        self._maybe_escalate(now)

    def record_error(self) -> None:
        """Increment error_count, append to error_history, and potentially de-escalate."""
        now = _utcnow()
        self._state["error_count"] = self._state.get("error_count", 0) + 1
        history: list = self._state.setdefault("error_history", [])
        history.append({
            "timestamp": _iso(now),
            "tool": "build",
            "error": "build_failure",
        })
        self._maybe_deescalate(now)

    def check(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        file_path: str = "",
        command: str = "",
    ) -> AutonomyResult:
        """Decide whether the action is allowed under the current autonomy level."""
        level: int = self._state.get("level", 0)

        # Always block HIGH at A0-A3.
        if risk_level == RiskLevel.HIGH:
            if level < 4:
                return AutonomyResult(
                    allowed=False,
                    reason=f"HIGH risk blocked at autonomy level A{level} ({_LEVEL_NAMES[level]}). "
                           "Explicit user approval required.",
                )
            # A4: HIGH allowed only if command is in high_risk_history.
            return self._check_a4_high(command)

        # Low risk is always allowed.
        if risk_level == RiskLevel.LOW:
            return AutonomyResult(allowed=True, reason="LOW risk — always allowed.")

        # Medium risk — level-dependent.
        if level == 0:
            return AutonomyResult(
                allowed=False,
                reason="A0 (Manual): all operations require explicit approval.",
            )

        if level >= 3:
            return AutonomyResult(
                allowed=True,
                reason=f"A{level} ({_LEVEL_NAMES[level]}): all MEDIUM risk auto-approved.",
            )

        if level >= 2:
            result = self._check_grants(file_path)
            if result.allowed:
                return result

        if level >= 1:
            result = self._check_category(tool_name, file_path, command)
            if result.allowed:
                return result

        return AutonomyResult(
            allowed=False,
            reason=f"A{level} ({_LEVEL_NAMES[level]}): no matching grant or approved category.",
        )

    def track(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        outcome: str,
    ) -> None:
        """Update autonomy state after an action completes.

        *outcome* must be "success" or "error".
        """
        now = _utcnow()
        if outcome == "success":
            self._state["successful_actions"] = self._state.get("successful_actions", 0) + 1
            # Accumulate approved categories.
            category = _CMD_TO_CATEGORY.get(tool_name.lower(), "")
            if not category:
                category = tool_name.lower()
            cats: list = self._state.setdefault("approved_categories", [])
            if category and category not in cats:
                cats.append(category)
            self._maybe_escalate(now)
        else:
            self._state["error_count"] = self._state.get("error_count", 0) + 1
            # Append to persistent error_history.
            history: list = self._state.setdefault("error_history", [])
            history.append({
                "timestamp": _iso(now),
                "tool": tool_name,
                "error": outcome,
            })
            self._maybe_deescalate(now)
        self._save()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _check_a4_high(self, command: str) -> AutonomyResult:
        """A4 high-risk approval: exact match or 3-token prefix match."""
        history: list[str] = self._state.get("high_risk_history", [])
        if command in history:
            return AutonomyResult(
                allowed=True,
                reason="A4 (Autonomous): HIGH risk command in history (exact match).",
            )
        # 3-token prefix match (V11 common.sh:183).
        prefix = " ".join(command.split()[:3])
        for entry in history:
            if entry.startswith(prefix):
                return AutonomyResult(
                    allowed=True,
                    reason=f"A4 (Autonomous): HIGH risk command matches history prefix '{prefix}'.",
                )
        return AutonomyResult(
            allowed=False,
            reason="A4 (Autonomous): HIGH risk command not in approved history.",
        )

    def _check_grants(self, file_path: str) -> AutonomyResult:
        """A2: check if file_path matches any active grant."""
        grants: list[dict] = self._state.get("grants", [])
        for grant in grants:
            pattern: str = grant.get("pattern", "")
            gtype: str = grant.get("type", "glob")
            if not pattern:
                continue
            matched = False
            if gtype == "glob":
                matched = fnmatch.fnmatch(file_path, pattern)
            elif gtype == "regex":
                try:
                    matched = bool(re.search(pattern, file_path))
                except re.error:
                    pass
            elif gtype == "prefix":
                matched = file_path.startswith(pattern)
            if matched:
                return AutonomyResult(
                    allowed=True,
                    reason=f"A2 (Supervised): grant matched {gtype} pattern '{pattern}'.",
                )
        return AutonomyResult(allowed=False, reason="No grant matched.")

    def _check_category(
        self, tool_name: str, file_path: str, command: str
    ) -> AutonomyResult:
        """A1: check if tool/file/command category is in approved_categories."""
        cats: list[str] = self._state.get("approved_categories", [])
        category = ""
        if file_path:
            ext = Path(file_path).suffix.lstrip(".")
            category = _EXT_TO_CATEGORY.get(ext, "")
        if not category and command:
            first = command.split()[0] if command.split() else ""
            category = _CMD_TO_CATEGORY.get(first, "")
        if not category:
            category = _CMD_TO_CATEGORY.get(tool_name.lower(), tool_name.lower())
        if category and category in cats:
            return AutonomyResult(
                allowed=True,
                reason=f"A1 (Guided): category '{category}' pre-approved.",
            )
        return AutonomyResult(allowed=False, reason=f"Category '{category}' not approved.")

    def _maybe_escalate(self, now: datetime) -> None:
        """Promote autonomy level if success threshold is met."""
        level: int = self._state.get("level", 0)
        if level >= 4:
            return
        # A3→A4 requires explicit grant, not automatic.
        if level >= 3:
            return
        threshold = _ESCALATION_THRESHOLDS.get(level)
        if threshold is None:
            return
        successes = self._state.get("successful_actions", 0)
        if successes < threshold:
            return
        # NEW: 1-hour cooldown after de-escalation before re-escalation.
        last_de = _parse_iso(self._state.get("last_escalation", "") or "")
        if last_de and (now - last_de) < _COOLDOWN_DURATION:
            return
        new_level = level + 1
        self._state["level"] = new_level
        self._state["name"] = _LEVEL_NAMES.get(new_level, str(new_level))
        self._state["last_escalation"] = _iso(now)

    def _maybe_deescalate(self, now: datetime) -> None:
        """Drop autonomy level on error; crash to A0 on rapid errors."""
        level: int = self._state.get("level", 0)
        if level == 0:
            return

        # Check for 5+ errors within the 10-minute window → A0.
        history: list[dict] = self._state.get("error_history", [])
        window_start = now - _DE_ESCALATION_WINDOW
        recent_errors = [
            e for e in history
            if (_parse_iso(e.get("timestamp", "")) or datetime.min.replace(tzinfo=timezone.utc))
            >= window_start
        ]
        if len(recent_errors) >= 5:
            self._state["level"] = 0
            self._state["name"] = _LEVEL_NAMES[0]
            self._state["last_escalation"] = _iso(now)
            return

        # Single error → drop 1 level.
        new_level = max(0, level - 1)
        self._state["level"] = new_level
        self._state["name"] = _LEVEL_NAMES.get(new_level, str(new_level))
        self._state["last_escalation"] = _iso(now)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._state_path.exists():
            return self._default_state()
        try:
            with FileLock(str(self._lock_path)):
                raw = self._state_path.read_text(encoding="utf-8")
            return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return self._default_state()

    def _save(self) -> None:
        """Write state atomically to the fixed path set at construction."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        with FileLock(str(self._lock_path)):
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(self._state_path)

    @staticmethod
    def _default_state() -> dict:
        return {
            "level": 2,
            "name": "Supervised",
            "successful_actions": 0,
            "error_count": 0,
            "approved_categories": [],
            "grants": [],
            "high_risk_history": [],
            "last_escalation": None,
            "error_history": [],
        }
