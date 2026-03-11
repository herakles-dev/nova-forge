"""Nova Forge interactive prompts — async wrappers over questionary."""
from __future__ import annotations

import sys

import questionary
from questionary import Choice, Style

# Match the existing Rich/PT theme
PROMPT_STYLE = Style([
    ("qmark", "fg:#ff66ff bold"),       # ? marker — magenta (matches nova brand)
    ("question", "bold"),               # question text — bold white
    ("answer", "fg:#00d7ff bold"),      # selected answer — cyan (matches accent)
    ("pointer", "fg:#ff66ff bold"),     # >> arrow — magenta
    ("highlighted", "fg:#ff66ff bold"), # current option — magenta
    ("selected", "fg:#00d7ff"),         # checkbox checked — cyan
    ("instruction", "fg:#808080"),      # hints — dim (matches muted)
])


async def ask_select(
    message: str,
    choices: list[str] | list[Choice],
    *,
    default: str | None = None,
    instruction: str = "(arrow keys to move, Enter to select)",
) -> str | None:
    """Single choice with arrow keys. Returns value or None on cancel."""
    if not sys.stdin.isatty():
        return default or (choices[0] if choices else None)
    try:
        return await questionary.select(
            message,
            choices=choices,
            default=default,
            instruction=instruction,
            style=PROMPT_STYLE,
            qmark="",
        ).unsafe_ask_async()
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
        return await questionary.confirm(
            message,
            default=default,
            style=PROMPT_STYLE,
            qmark="",
        ).unsafe_ask_async()
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
        return await questionary.text(
            message,
            default=default,
            validate=validate,
            instruction=instruction,
            style=PROMPT_STYLE,
            qmark="",
        ).unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return None


async def ask_checkbox(
    message: str,
    choices: list[str] | list[Choice],
    *,
    instruction: str = "(arrow keys move, Space toggles, Enter confirms)",
    validate: callable | None = None,
) -> list[str] | None:
    """Multi-select with checkboxes. Returns list or None on cancel."""
    if not sys.stdin.isatty():
        return []
    try:
        return await questionary.checkbox(
            message,
            choices=choices,
            instruction=instruction,
            validate=validate,
            style=PROMPT_STYLE,
            qmark="",
        ).unsafe_ask_async()
    except (KeyboardInterrupt, EOFError):
        return None
