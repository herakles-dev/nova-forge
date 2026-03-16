"""Nova Forge configuration — paths, model configs, project detection, user profiles."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Directory constants ──────────────────────────────────────────────
FORGE_DIR_NAME = ".forge"
STATE_DIR_NAME = "state"
AUDIT_DIR_NAME = "audit"
ARTIFACTS_DIR_NAME = "artifacts"
HOOKS_DIR_NAME = "hooks"
AGENTS_DIR_NAME = "agents"
SKILLS_DIR_NAME = "skills"
FORMATIONS_DIR_NAME = "formations"
SCHEMAS_DIR_NAME = "schemas"

# Directories that are NOT projects (exclude from detection)
NON_PROJECTS = frozenset({
    "v11", "v8", "sessions", ".claude", ".forge", ".git", ".github",
    "node_modules", "__pycache__", ".venv", "venv", ".archive",
    "scripts", "system-apps-config", ".secrets", ".ssh",
})

# ── Model configuration ─────────────────────────────────────────────

@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a single model."""
    model_id: str           # e.g. "bedrock/us.amazon.nova-2-lite-v1:0"
    provider: str           # "bedrock", "openai", "anthropic"
    context_window: int     # tokens
    max_tokens: int         # max output tokens
    temperature: float = 0.3

    @property
    def short_name(self) -> str:
        return self.model_id.split("/")[-1]


# Model aliases → full model IDs
MODEL_ALIASES: dict[str, str] = {
    "nova-lite":    "bedrock/us.amazon.nova-2-lite-v1:0",
    "nova-pro":     "bedrock/us.amazon.nova-pro-v1:0",
    "nova-premier": "bedrock/us.amazon.nova-premier-v1:0",
    "gemini-flash": "openrouter/google/gemini-2.0-flash-001",
    "gemini-pro":   "openrouter/google/gemini-2.5-pro-preview",
    "claude-sonnet": "anthropic/claude-sonnet-4-6-20250514",
    "claude-haiku":  "anthropic/claude-haiku-4-5-20251001",
}

# Default models for each pipeline phase
DEFAULT_MODELS: dict[str, str] = {
    "planning": "bedrock/us.amazon.nova-pro-v1:0",
    "coding":   "bedrock/us.amazon.nova-pro-v1:0",
    "review":   "bedrock/us.amazon.nova-2-lite-v1:0",
}

# Provider context windows (conservative estimates)
CONTEXT_WINDOWS: dict[str, int] = {
    "us.amazon.nova-2-lite-v1:0": 32_000,
    "us.amazon.nova-pro-v1:0": 300_000,
    "us.amazon.nova-premier-v1:0": 1_000_000,
    "google/gemini-2.0-flash-001": 1_000_000,
    "google/gemini-2.5-pro-preview": 1_000_000,
    "claude-sonnet-4-6-20250514": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def get_prompt_budget(context_window: int) -> dict:
    """Return token budget allocation for each prompt zone.

    Zones: system_prompt, project_index, chat_history, memory, working_space
    All values in tokens (1 token ≈ 4 chars).
    """
    if context_window <= 32_000:
        return {
            "system_prompt": 4_000,      # 12.5%
            "project_index": 1_500,
            "chat_history": 2_000,
            "memory": 1_000,
            "working_space": 23_500,     # 73.4%
            "compaction_threshold": 0.60,
        }
    elif context_window <= 200_000:
        return {
            "system_prompt": 8_000,
            "project_index": 4_000,
            "chat_history": 6_000,
            "memory": 2_000,
            "working_space": 108_000,
            "compaction_threshold": 0.65,   # was 0.75 — more headroom for tool history
        }
    else:
        return {
            "system_prompt": 16_000,
            "project_index": 8_000,
            "chat_history": 12_000,
            "memory": 4_000,
            "working_space": 260_000,
            "compaction_threshold": 0.65,   # was 0.80 — match lite's discipline
        }


def compute_turn_budget(task_metadata: dict, max_turns_ceiling: int = 50) -> dict:
    """Compute adaptive turn budget based on task complexity.

    Returns dict with keys: soft_limit, hard_limit, verify_budget, escalation_turns.
    The max_turns_ceiling is the user-configured max_turns that we never exceed.
    """
    files = task_metadata.get("files", [])
    num_files = len(files)
    acceptance = task_metadata.get("acceptance_criteria", [])
    blocked_by = task_metadata.get("blocked_by", [])

    # Base budget scales with file count
    # 1-file needs room for write+append+verify+fix cycles (~15 turns)
    if num_files == 0:
        base = 12  # Read-only/discovery tasks need room for exploration
    elif num_files == 1:
        base = 15
    elif num_files == 2:
        base = 18
    else:
        base = min(12 + num_files * 4, 30)

    # Modifiers: integration tests or dependencies add budget
    ac_text = " ".join(str(c) for c in acceptance).lower()
    if any(kw in ac_text for kw in ("curl", "localhost", "http://", "server")):
        base += 4
    if blocked_by:
        base += 2

    # Apply ceiling
    soft_limit = min(base, max_turns_ceiling)

    # Derived limits
    hard_limit = max(soft_limit + 4, int(soft_limit * 1.3))
    hard_limit = min(hard_limit, max_turns_ceiling + 4)  # never wildly exceed ceiling
    verify_budget = max(2, soft_limit // 4)
    escalation_turns = max(8, soft_limit // 2)

    return {
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "verify_budget": verify_budget,
        "escalation_turns": escalation_turns,
    }


def resolve_model(model_str: str) -> str:
    """Resolve alias or passthrough full model ID."""
    return MODEL_ALIASES.get(model_str, model_str)


def get_provider(model_id: str) -> str:
    """Extract provider from model ID string."""
    if model_id.startswith("bedrock/"):
        return "bedrock"
    elif model_id.startswith("anthropic/"):
        return "anthropic"
    else:
        return "openai"  # default: openai-compatible (OpenRouter, OpenAI, Ollama)


def get_context_window(model_id: str) -> int:
    """Get context window for a model. Conservative default: 32K."""
    bare = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    return CONTEXT_WINDOWS.get(bare, 32_000)


def get_model_config(model_str: str, **overrides) -> ModelConfig:
    """Build ModelConfig from a model string (alias or full ID)."""
    model_id = resolve_model(model_str)
    provider = get_provider(model_id)
    ctx = get_context_window(model_id)
    # Scale max_tokens by context window — larger models need more output room
    if "max_tokens" in overrides:
        mt = overrides["max_tokens"]
    elif ctx <= 32_000:
        mt = 4096
    elif ctx <= 200_000:
        mt = 4096      # Claude-class: 4K is sufficient, good discipline
    elif ctx <= 500_000:
        mt = 8192      # Nova Pro (300K), Gemini: allow longer writes
    else:
        mt = 16384     # Nova Premier (1M): needs more output room to avoid truncation

    return ModelConfig(
        model_id=model_id,
        provider=provider,
        context_window=ctx,
        max_tokens=mt,
        temperature=overrides.get("temperature", 0.3),
    )


# ── Project detection ────────────────────────────────────────────────
# Port of v11_detect_project() (common.sh:64-89)

HERCULES_ROOT = Path(os.environ.get("HERCULES_ROOT", "/home/hercules"))
SESSIONS_ROOT = Path(os.environ.get("SESSIONS_ROOT", HERCULES_ROOT / "sessions"))

# Files that are infrastructure, not project content
METADATA_FILES = frozenset({
    "state.md", "spec.md", "gates.md", "FORGE.md", "CLAUDE.md",
    ".autonomy-state", "autonomy.json", ".task-config.json",
    ".formation-registry.json", ".audit-config.json",
})

METADATA_DIRS = frozenset({
    ".forge", ".claude", ".git", ".github", "hooks",
    ".agent-metrics", "schemas",
})


def detect_project(path: str | Path) -> Optional[str]:
    """Detect project name from a file path.

    Checks (in order):
    1. sessions/{name}/ prefix
    2. Contains .forge/ directory → directory name
    3. HERCULES_ROOT/{name}/ where name not in NON_PROJECTS
    """
    p = Path(path).resolve()

    # Convention 1: sessions/{name}/...
    try:
        rel = p.relative_to(SESSIONS_ROOT)
        parts = rel.parts
        if parts:
            return parts[0]
    except ValueError:
        pass

    # Convention 2: directory contains .forge/
    check = p if p.is_dir() else p.parent
    while check != check.parent:
        if (check / FORGE_DIR_NAME).is_dir():
            return check.name
        check = check.parent

    # Convention 3: HERCULES_ROOT/{name}/
    try:
        rel = p.relative_to(HERCULES_ROOT)
        parts = rel.parts
        if parts and parts[0] not in NON_PROJECTS:
            return parts[0]
    except ValueError:
        pass

    return None


def is_metadata_file(path: str | Path) -> bool:
    """Check if a path is infrastructure/metadata (skip in hooks)."""
    p = Path(path)
    if p.name in METADATA_FILES:
        return True
    for part in p.parts:
        if part in METADATA_DIRS:
            return True
    return False


# ── Global user profile ──────────────────────────────────────────────

GLOBAL_FORGE_DIR = Path.home() / ".forge"
GLOBAL_PROFILE_PATH = GLOBAL_FORGE_DIR / "profile.json"


def load_global_profile() -> dict:
    """Load the global user profile (skill level, preferences).

    Returns an empty dict if the file does not exist or is malformed.
    The global profile lives at ~/.forge/profile.json and provides
    defaults that any project can inherit.
    """
    if not GLOBAL_PROFILE_PATH.exists():
        return {}
    try:
        return json.loads(GLOBAL_PROFILE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load global profile: %s", exc)
        return {}


def save_global_profile(profile: dict) -> None:
    """Save global user profile to ~/.forge/profile.json.

    Creates ~/.forge/ directory if it does not exist.
    """
    GLOBAL_FORGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        GLOBAL_PROFILE_PATH.write_text(
            json.dumps(profile, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to save global profile: %s", exc)


# ── .forge/ directory initialization ─────────────────────────────────

@dataclass
class ForgeProject:
    """Represents a .forge/-enabled project."""
    root: Path
    forge_dir: Path = field(init=False)
    state_dir: Path = field(init=False)
    audit_dir: Path = field(init=False)
    artifacts_dir: Path = field(init=False)
    hooks_dir: Path = field(init=False)
    agents_dir: Path = field(init=False)
    skills_dir: Path = field(init=False)
    formations_dir: Path = field(init=False)
    schemas_dir: Path = field(init=False)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.forge_dir = self.root / FORGE_DIR_NAME
        self.state_dir = self.forge_dir / STATE_DIR_NAME
        self.audit_dir = self.forge_dir / AUDIT_DIR_NAME
        self.artifacts_dir = self.state_dir / ARTIFACTS_DIR_NAME
        self.hooks_dir = self.forge_dir / HOOKS_DIR_NAME
        self.agents_dir = self.forge_dir / AGENTS_DIR_NAME
        self.skills_dir = self.forge_dir / SKILLS_DIR_NAME
        self.formations_dir = self.forge_dir / FORMATIONS_DIR_NAME
        self.schemas_dir = self.forge_dir / SCHEMAS_DIR_NAME

    @property
    def tasks_file(self) -> Path:
        return self.state_dir / "tasks.json"

    @property
    def autonomy_file(self) -> Path:
        return self.state_dir / "autonomy.json"

    @property
    def settings_file(self) -> Path:
        return self.forge_dir / "settings.json"

    @property
    def forge_md(self) -> Path:
        return self.forge_dir / "FORGE.md"

    @property
    def name(self) -> str:
        return self.root.name


def init_forge_dir(project_path: str | Path) -> ForgeProject:
    """Create .forge/ directory structure for a project."""
    project = ForgeProject(root=project_path)
    for d in [
        project.forge_dir, project.state_dir, project.audit_dir,
        project.artifacts_dir, project.hooks_dir, project.agents_dir,
        project.skills_dir, project.formations_dir, project.schemas_dir,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return project
