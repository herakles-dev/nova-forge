"""Nova Forge Model Intelligence — cost tracking, escalation, capability routing.

Provides:
  ModelCapability    — dataclass with cost/context/strengths per model
  MODEL_CAPABILITIES — registry of 7 supported models
  PHASE_DEFAULTS     — smart default model per pipeline phase
  CostTracker        — accumulates cost per model and per task
  estimate_cost()    — dollar cost from token counts
  format_cost()      — human-readable cost string
  get_capability()   — lookup by alias or full model_id
  get_escalation_model() — next-tier model for auto-escalation
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import MODEL_ALIASES, resolve_model


@dataclass
class ModelCapability:
    """Capability profile for a single model."""
    alias: str                      # "nova-lite"
    model_id: str                   # "bedrock/us.amazon.nova-2-lite-v1:0"
    provider: str                   # "bedrock", "openai", "anthropic"
    cost_per_1k_input: float        # $ per 1K input tokens
    cost_per_1k_output: float       # $ per 1K output tokens
    context_window: int             # tokens
    strengths: list[str]            # ["reasoning", "speed", "code"]
    escalation_target: str | None   # alias of next-tier model, or None
    beginner_description: str = ""  # Human-friendly description for beginners


# ── Model registry ───────────────────────────────────────────────────────────

MODEL_CAPABILITIES: dict[str, ModelCapability] = {
    "nova-lite": ModelCapability(
        alias="nova-lite",
        model_id="bedrock/us.amazon.nova-2-lite-v1:0",
        provider="bedrock",
        cost_per_1k_input=0.00006,
        cost_per_1k_output=0.00024,
        context_window=32_000,
        strengths=["speed", "low-cost", "reasoning"],
        escalation_target="nova-pro",
        beginner_description="Fast and affordable — great for learning and quick tasks. Smaller context window means it works best with focused, single-file tasks.",
    ),
    "nova-pro": ModelCapability(
        alias="nova-pro",
        model_id="bedrock/us.amazon.nova-pro-v1:0",
        provider="bedrock",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.0032,
        context_window=300_000,
        strengths=["reasoning", "code", "analysis"],
        escalation_target="nova-premier",
        beginner_description="Strong all-rounder — handles complex coding tasks and can read large codebases. Good balance of capability and cost.",
    ),
    "nova-premier": ModelCapability(
        alias="nova-premier",
        model_id="bedrock/us.amazon.nova-premier-v1:0",
        provider="bedrock",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.008,
        context_window=1_000_000,
        strengths=["deep-reasoning", "code", "long-context"],
        escalation_target=None,
        beginner_description="Most powerful Nova model — best for complex multi-file projects and deep reasoning. Higher cost but top-tier results.",
    ),
    "gemini-flash": ModelCapability(
        alias="gemini-flash",
        model_id="openrouter/google/gemini-2.0-flash-001",
        provider="openai",
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        context_window=1_000_000,
        strengths=["speed", "code", "multimodal"],
        escalation_target="gemini-pro",
        beginner_description="Lightning fast with a huge context window — great for coding tasks. Can process images too. Best value for code generation.",
    ),
    "gemini-pro": ModelCapability(
        alias="gemini-pro",
        model_id="openrouter/google/gemini-2.5-pro-preview",
        provider="openai",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.01,
        context_window=1_000_000,
        strengths=["deep-reasoning", "code", "analysis", "long-context"],
        escalation_target=None,
        beginner_description="Top-tier reasoning and coding — handles the most complex architectural decisions and multi-file refactors.",
    ),
    "claude-sonnet": ModelCapability(
        alias="claude-sonnet",
        model_id="anthropic/claude-sonnet-4-6-20250514",
        provider="anthropic",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        context_window=200_000,
        strengths=["code", "reasoning", "instruction-following"],
        escalation_target=None,
        beginner_description="Excellent at following instructions precisely — great for code that needs to match exact specifications. Premium quality.",
    ),
    "claude-haiku": ModelCapability(
        alias="claude-haiku",
        model_id="anthropic/claude-haiku-4-5-20251001",
        provider="anthropic",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        context_window=200_000,
        strengths=["speed", "low-cost", "code"],
        escalation_target="claude-sonnet",
        beginner_description="Fast and affordable Claude model — good for straightforward coding tasks where speed matters more than deep reasoning.",
    ),
}

# Smart defaults per pipeline phase
PHASE_DEFAULTS: dict[str, str] = {
    "planning":   "nova-lite",
    "coding":     "gemini-flash",
    "review":     "nova-lite",
    "escalation": "nova-pro",
}

# ── Model presets ────────────────────────────────────────────────────────────

MODEL_PRESETS: dict[str, dict] = {
    "nova": {
        "description": "AWS Nova only — optimized for Amazon Nova AI Hackathon",
        "default_model": "nova-lite",
        "phases": {
            "planning":   "nova-lite",
            "coding":     "nova-lite",
            "review":     "nova-lite",
            "escalation": "nova-pro",
        },
        "formation_fast": "nova-lite",
        "formation_smart": "nova-pro",
    },
    "mixed": {
        "description": "Best model per task — Nova planning, Gemini coding, Nova review",
        "default_model": "nova-lite",
        "phases": {
            "planning":   "nova-lite",
            "coding":     "gemini-flash",
            "review":     "nova-lite",
            "escalation": "nova-pro",
        },
        "formation_fast": "gemini-flash",
        "formation_smart": "nova-lite",
    },
    "premium": {
        "description": "Maximum capability — Nova Pro + Premier escalation",
        "default_model": "nova-pro",
        "phases": {
            "planning":   "nova-pro",
            "coding":     "nova-pro",
            "review":     "nova-pro",
            "escalation": "nova-premier",
        },
        "formation_fast": "nova-pro",
        "formation_smart": "nova-premier",
    },
}

_active_preset: str = ""


def get_active_preset() -> str:
    """Return the name of the currently active preset, or empty string."""
    return _active_preset


def apply_preset(preset_name: str) -> str:
    """Apply a model preset. Updates PHASE_DEFAULTS, config.DEFAULT_MODELS, and formation models.

    Returns the preset description on success, raises KeyError on unknown preset.
    """
    global _active_preset

    if preset_name not in MODEL_PRESETS:
        raise KeyError(f"Unknown preset: {preset_name}. Available: {', '.join(MODEL_PRESETS)}")

    preset = MODEL_PRESETS[preset_name]

    # Update PHASE_DEFAULTS
    for phase, alias in preset["phases"].items():
        PHASE_DEFAULTS[phase] = alias

    # Update config.DEFAULT_MODELS so formations pick up the change
    import config
    for phase, alias in preset["phases"].items():
        if phase in config.DEFAULT_MODELS:
            config.DEFAULT_MODELS[phase] = resolve_model(alias)

    # Update formation model constants
    try:
        import formations
        formations._FAST_MODEL = resolve_model(preset["formation_fast"])
        formations._SMART_MODEL = resolve_model(preset["formation_smart"])
    except (ImportError, AttributeError):
        pass

    _active_preset = preset_name
    return preset["description"]


# ── Lookup helpers ───────────────────────────────────────────────────────────

def get_capability(model_id_or_alias: str) -> ModelCapability | None:
    """Look up model capability by alias or full model_id."""
    # Direct alias match
    if model_id_or_alias in MODEL_CAPABILITIES:
        return MODEL_CAPABILITIES[model_id_or_alias]
    # Full model_id match
    for cap in MODEL_CAPABILITIES.values():
        if cap.model_id == model_id_or_alias:
            return cap
    # Try resolving alias first
    resolved = resolve_model(model_id_or_alias)
    for cap in MODEL_CAPABILITIES.values():
        if cap.model_id == resolved:
            return cap
    return None


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate dollar cost for a model call."""
    cap = get_capability(model_id)
    if cap is None:
        return 0.0
    return (input_tokens / 1000) * cap.cost_per_1k_input + (output_tokens / 1000) * cap.cost_per_1k_output


def format_cost(dollars: float) -> str:
    """Format dollar amount for display."""
    if dollars == 0:
        return "$0.00"
    if dollars < 0.01:
        return f"${dollars:.4f}"
    return f"${dollars:.2f}"


def get_escalation_model(current_model_id: str) -> str | None:
    """Get the full model_id of the escalation target, or None."""
    cap = get_capability(current_model_id)
    if cap is None or cap.escalation_target is None:
        return None
    target_cap = MODEL_CAPABILITIES.get(cap.escalation_target)
    if target_cap is None:
        return None
    return target_cap.model_id


# ── Cost tracker ─────────────────────────────────────────────────────────────

@dataclass
class CostTracker:
    """Accumulates cost across model calls during a build."""
    total_cost: float = 0.0
    per_model: dict[str, float] = field(default_factory=dict)
    per_task: dict[int, float] = field(default_factory=dict)

    def record(self, model_id: str, input_tokens: int, output_tokens: int, task_id: int | None = None) -> float:
        """Record a model call and return its cost."""
        cost = estimate_cost(model_id, input_tokens, output_tokens)
        self.total_cost += cost

        cap = get_capability(model_id)
        alias = cap.alias if cap else model_id
        self.per_model[alias] = self.per_model.get(alias, 0.0) + cost

        if task_id is not None:
            self.per_task[task_id] = self.per_task.get(task_id, 0.0) + cost

        return cost

    def summary(self) -> dict:
        """Return a summary dict."""
        return {
            "total_cost": self.total_cost,
            "total_cost_formatted": format_cost(self.total_cost),
            "per_model": dict(self.per_model),
            "per_task": dict(self.per_task),
        }

    def format_summary(self) -> str:
        """Multi-line human-readable summary."""
        lines = [f"Total: {format_cost(self.total_cost)}"]
        if self.per_model:
            lines.append("By model:")
            for alias, cost in sorted(self.per_model.items(), key=lambda x: -x[1]):
                lines.append(f"  {alias:18s} {format_cost(cost)}")
        return "\n".join(lines)
