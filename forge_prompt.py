"""Nova Forge interactive prompts — async wrappers over questionary.

Provides arrow-key navigable selection menus with descriptions,
grouping (Separators), search, and keyboard shortcuts — matching
the UX quality of Claude Code and Gemini CLI.
"""
from __future__ import annotations

import sys
from typing import Any

import questionary
from questionary import Choice, Separator, Style
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

# Match the existing Rich/PT theme
PROMPT_STYLE = Style([
    ("qmark", "fg:#c084fc bold"),       # ? marker — brand magenta
    ("question", "bold"),               # question text — bold white
    ("answer", "fg:#67e8f9 bold"),      # selected answer — brand cyan
    ("pointer", "fg:#c084fc bold"),     # >> arrow — brand magenta
    ("highlighted", "fg:#c084fc bold"), # current option — brand magenta
    ("selected", "fg:#67e8f9"),         # checkbox checked — brand cyan
    ("instruction", "fg:#7a7a94"),      # hints — brand muted
    ("description", "fg:#7a7a94 italic"),  # choice descriptions — brand muted italic
])


def _first_selectable(choices: list) -> Any:
    """Return the value of the first non-Separator, non-disabled choice."""
    for c in choices:
        if isinstance(c, Separator):
            continue
        if isinstance(c, Choice):
            if getattr(c, "disabled", None):
                continue
            return c.value
        # Plain string
        return c
    return None


def _add_escape_binding(question):
    """Inject Escape-to-cancel into a questionary Question's key bindings."""
    app = question.application
    extra = KeyBindings()

    @extra.add(Keys.Escape, eager=True)
    def _escape(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    app.key_bindings = merge_key_bindings([app.key_bindings, extra])
    return question


async def ask_select(
    message: str,
    choices: list[str] | list[Choice | Separator],
    *,
    default: str | None = None,
    instruction: str | None = None,
    use_shortcuts: bool = False,
    use_search_filter: bool = False,
    show_description: bool = True,
    show_selected: bool = True,
) -> str | None:
    """Single choice with arrow keys, optional shortcuts and search.

    Args:
        message: The prompt question text.
        choices: List of plain strings, Choice objects, or Separators.
        default: Pre-selected value.
        instruction: Hint text shown next to the question.
        use_shortcuts: Enable single-key selection (a, b, c...).
        use_search_filter: Enable type-to-filter within the list.
        show_description: Show Choice.description below the highlighted item.
        show_selected: Show the selected value after confirming.

    Returns:
        The selected value string, or None on cancel/Ctrl-C.
    """
    if not sys.stdin.isatty():
        if default is not None:
            return default
        return _first_selectable(choices)

    if instruction is None:
        instruction = _auto_instruction(
            use_shortcuts=use_shortcuts,
            use_search_filter=use_search_filter,
        )

    try:
        question = questionary.select(
            message,
            choices=choices,
            default=default,
            instruction=instruction,
            style=PROMPT_STYLE,
            qmark="",
            use_shortcuts=use_shortcuts,
            use_search_filter=use_search_filter,
            use_jk_keys=not use_search_filter,
            show_description=show_description,
            use_indicator=show_selected,
            show_selected=show_selected,
        )
        _add_escape_binding(question)
        return await question.unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return None


async def ask_confirm(
    message: str,
    *,
    default: bool = True,
) -> bool:
    """Yes/no confirmation. Returns bool (never None)."""
    if not sys.stdin.isatty():
        return default
    try:
        question = questionary.confirm(
            message,
            default=default,
            style=PROMPT_STYLE,
            qmark="",
        )
        _add_escape_binding(question)
        return await question.unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return default


async def ask_text(
    message: str,
    *,
    default: str = "",
    validate: callable | None = None,
    instruction: str | None = None,
) -> str | None:
    """Text input. Returns string or None on cancel."""
    if not sys.stdin.isatty():
        return default or None
    try:
        question = questionary.text(
            message,
            default=default,
            validate=validate,
            instruction=instruction,
            style=PROMPT_STYLE,
            qmark="",
        )
        _add_escape_binding(question)
        return await question.unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return None


async def ask_checkbox(
    message: str,
    choices: list[str] | list[Choice | Separator],
    *,
    instruction: str = "(arrow keys move, Space toggles, Enter confirms, Esc to cancel)",
    validate: callable | None = None,
) -> list[str] | None:
    """Multi-select with checkboxes. Returns list or None on cancel."""
    if not sys.stdin.isatty():
        return []
    try:
        question = questionary.checkbox(
            message,
            choices=choices,
            instruction=instruction,
            validate=validate,
            style=PROMPT_STYLE,
            qmark="",
        )
        _add_escape_binding(question)
        return await question.unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return None


async def ask_text_optional(
    message: str,
    *,
    instruction: str = "(Enter to skip)",
) -> str | None:
    """Optional text input — returns None on empty or cancel."""
    result = await ask_text(message, default="", instruction=instruction)
    if result is None or result.strip() == "":
        return None
    return result.strip()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _auto_instruction(
    *,
    use_shortcuts: bool = False,
    use_search_filter: bool = False,
) -> str:
    """Build context-aware instruction hint."""
    parts = []
    if use_shortcuts:
        parts.append("shortcut key or arrow keys")
    else:
        parts.append("arrow keys to move")
    parts.append("Enter to select")
    if use_search_filter:
        parts.append("type to filter")
    parts.append("Esc to cancel")
    return "(" + ", ".join(parts) + ")"


def build_model_choices(
    available_providers: dict[str, bool] | None = None,
    current_model: str | None = None,
) -> list[Choice | Separator]:
    """Build a rich model selection list grouped by provider.

    Args:
        available_providers: {provider_name: is_configured} dict.
            If None, all models shown without status.
        current_model: Currently active model alias (for marking).

    Returns:
        List of Choice and Separator objects for ask_select.
    """
    from forge_models import MODEL_CAPABILITIES

    groups: dict[str, list[str]] = {
        "Amazon Bedrock (Nova)": ["nova-lite", "nova-pro", "nova-premier"],
        "Google (via OpenRouter)": ["gemini-flash", "gemini-pro"],
        "Anthropic (Claude)": ["claude-sonnet", "claude-haiku"],
    }
    provider_for_group = {
        "Amazon Bedrock (Nova)": "bedrock",
        "Google (via OpenRouter)": "openrouter",
        "Anthropic (Claude)": "anthropic",
    }

    result: list[Choice | Separator] = []

    for group_name, aliases in groups.items():
        prov = provider_for_group[group_name]
        if available_providers is not None:
            ready = available_providers.get(prov, False)
            status = "ready" if ready else "needs /login"
            result.append(Separator(f"── {group_name} [{status}] ──"))
        else:
            result.append(Separator(f"── {group_name} ──"))

        for alias in aliases:
            cap = MODEL_CAPABILITIES.get(alias)
            if cap is None:
                continue

            ctx = f"{cap.context_window // 1000}K"
            strengths = ", ".join(cap.strengths[:3])
            title = f"{alias:18s} {ctx:>5s}  {strengths}"

            # Mark current model
            if current_model and alias == current_model:
                title += "  (current)"

            disabled_reason = None
            if available_providers is not None and not available_providers.get(prov, False):
                disabled_reason = "needs /login"

            result.append(Choice(
                title=title,
                value=alias,
                description=cap.beginner_description,
                disabled=disabled_reason,
            ))

    return result
