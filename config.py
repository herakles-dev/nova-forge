"""Nova Forge configuration — paths, model configs, project detection."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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
    "planning": "bedrock/us.amazon.nova-2-lite-v1:0",
    "coding":   "openrouter/google/gemini-2.0-flash-001",
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
    return ModelConfig(
        model_id=model_id,
        provider=provider,
        context_window=ctx,
        max_tokens=overrides.get("max_tokens", min(4096, ctx // 4)),
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
