"""Nova Forge PromptBuilder — 7-section prompt construction for agents.

Builds (system_prompt, user_prompt) tuples from role definitions,
FORGE.md project context, task data, upstream artifacts, and tool policies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# YAML is optional — gracefully degrade to a simple --- splitter
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ── Constants ─────────────────────────────────────────────────────────────────

# Chars per token (rough estimate, same convention used in ForgeAgent)
_CHARS_PER_TOKEN = 4

# Maximum chars for a single context value before truncation
_CONTEXT_ITEM_MAX_CHARS = 8_000

# Section index of CONTEXT in the user-prompt section list
_CONTEXT_SECTION_IDX = 1  # 0-based index within the user-prompt sections


# ── PromptBuilder ─────────────────────────────────────────────────────────────

class PromptBuilder:
    """Construct system and user prompts for a ForgeAgent invocation.

    System prompt (stable across turns):
        Section 1 — Role identity + constraints
        Section 2 — FORGE.md project context

    User prompt (changes per invocation):
        Section 3 — Current task details
        Section 4 — Upstream context / prior-wave artifacts
        Section 5 — Available tools + restrictions
        Section 6 — File-ownership constraints + forbidden paths
        Section 7 — Expected output deliverables
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    def build(
        self,
        role: str,
        task: dict[str, Any],
        context: dict[str, Any] | None = None,
        formation: dict[str, Any] | None = None,
        tool_policy: dict[str, Any] | None = None,
        max_tokens: int = 32_000,
    ) -> tuple[str, str]:
        """Build a system prompt and user prompt for an agent.

        Args:
            role:        Role name, e.g. "spec-implementer" or "spec-tester".
            task:        Task dict with keys: subject, description, metadata (optional).
            context:     Upstream artifact dict — key → text content.
            formation:   Formation config dict (used for ownership/tool restrictions).
            tool_policy: Dict with 'available' and 'restricted' tool name lists.
            max_tokens:  Context-window budget; user prompt truncated to 70% of this.

        Returns:
            (system_prompt, user_prompt) as a two-element tuple.
        """
        formation = formation or {}
        tool_policy = tool_policy or {}

        # ── System prompt (sections 1 + 2) ───────────────────────────────────
        section1 = self._build_section_role(role)
        section2 = self._build_section_project()
        system_prompt = "\n\n".join(s for s in [section1, section2] if s)

        # ── User prompt sections (sections 3-7) ──────────────────────────────
        section3 = self._build_section_task(task)
        section4 = self._build_section_context(context)
        section5 = self._build_section_tools(tool_policy, formation)
        section6 = self._build_section_constraints(formation)
        section7 = self._build_section_output()

        user_sections = [section3, section4, section5, section6, section7]
        user_sections = self._truncate_to_budget(user_sections, max_tokens)
        user_prompt = "\n\n".join(s for s in user_sections if s)

        return system_prompt, user_prompt

    # ── Agent definition loading ──────────────────────────────────────────────

    def load_agent_definition(self, name: str) -> tuple[dict, str]:
        """Load .forge/agents/{name}.md — return (frontmatter_dict, body_markdown).

        If the file doesn't exist, return (empty_dict, empty_string).
        YAML frontmatter is parsed from between the first pair of '---' markers.
        """
        agent_file = self.project_root / ".forge" / "agents" / f"{name}.md"
        if not agent_file.exists():
            return {}, ""

        raw = agent_file.read_text(encoding="utf-8", errors="replace")
        return self._parse_frontmatter(raw)

    def load_forge_md(self) -> str:
        """Read .forge/FORGE.md if it exists, return content or empty string."""
        forge_md = self.project_root / ".forge" / "FORGE.md"
        if not forge_md.exists():
            return ""
        return forge_md.read_text(encoding="utf-8", errors="replace")

    # ── Token budget helpers ──────────────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Rough estimate: 4 chars per token."""
        return len(text) // _CHARS_PER_TOKEN

    def _truncate_to_budget(
        self,
        sections: list[str],
        max_tokens: int,
    ) -> list[str]:
        """Truncate the CONTEXT section (index 1 in user-prompt list) if the
        total user-prompt token estimate exceeds 70% of max_tokens.

        Only the context section is shortened; all other sections are preserved.
        """
        budget = int(max_tokens * 0.70)
        total = sum(self._estimate_tokens(s) for s in sections)

        if total <= budget or not sections[_CONTEXT_SECTION_IDX]:
            return sections

        # How many tokens are available for the context section?
        non_context_tokens = sum(
            self._estimate_tokens(s)
            for i, s in enumerate(sections)
            if i != _CONTEXT_SECTION_IDX
        )
        context_budget = budget - non_context_tokens
        if context_budget <= 0:
            # No room at all — drop the context section
            result = list(sections)
            result[_CONTEXT_SECTION_IDX] = ""
            return result

        context_text = sections[_CONTEXT_SECTION_IDX]
        max_chars = context_budget * _CHARS_PER_TOKEN
        if len(context_text) > max_chars:
            context_text = (
                context_text[:max_chars]
                + "\n\n... [context truncated to fit token budget]"
            )

        result = list(sections)
        result[_CONTEXT_SECTION_IDX] = context_text
        return result

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_section_role(self, role: str) -> str:
        """Section 1 — Role identity + constraints."""
        frontmatter, body = self.load_agent_definition(role)

        role_name = frontmatter.get("name", role)
        role_description = frontmatter.get("description", "")

        lines: list[str] = [
            f"You are {role_name}, a specialized agent in the Nova Forge framework.",
        ]
        if role_description:
            lines.append(f"Your role: {role_description}")
        else:
            lines.append(
                f"Your role: Complete assigned tasks precisely and report results clearly."
            )

        if body.strip():
            lines.append("")
            lines.append(body.strip())

        return "\n".join(lines)

    def _build_section_project(self) -> str:
        """Section 2 — FORGE.md project context."""
        content = self.load_forge_md()
        if not content:
            return ""
        return content.strip()

    def _build_section_task(self, task: dict[str, Any]) -> str:
        """Section 3 — Current task details."""
        metadata = task.get("metadata") or {}
        sprint = metadata.get("sprint", "N/A")
        risk = metadata.get("risk", "N/A")

        lines = [
            "## Current Task",
            f"Subject: {task.get('subject', 'N/A')}",
            f"Description: {task.get('description', 'N/A')}",
            f"Sprint: {sprint}",
            f"Risk: {risk}",
        ]

        # Include any additional metadata fields that callers may add
        extra_keys = {k for k in metadata if k not in {"sprint", "risk"}}
        for key in sorted(extra_keys):
            value = metadata[key]
            if isinstance(value, (str, int, float, bool)):
                lines.append(f"{key.capitalize()}: {value}")

        return "\n".join(lines)

    def _build_section_context(self, context: dict[str, Any] | None) -> str:
        """Section 4 — Upstream artifacts and prior wave outputs."""
        if not context:
            return ""

        parts: list[str] = []
        for key, value in context.items():
            text = str(value)
            if len(text) > _CONTEXT_ITEM_MAX_CHARS:
                text = (
                    text[:_CONTEXT_ITEM_MAX_CHARS]
                    + f"\n... [truncated: {len(text)} chars total]"
                )
            parts.append(f"## Context: {key}\n{text}")

        return "\n\n".join(parts)

    def _build_section_tools(
        self,
        tool_policy: dict[str, Any],
        formation: dict[str, Any],
    ) -> str:
        """Section 5 — Available tools list + restrictions."""
        # Merge tool_policy with formation-level overrides
        available: list[str] = tool_policy.get("available", [])
        restricted: list[str] = tool_policy.get("restricted", [])

        # Formation may specify additional restrictions
        formation_restricted: list[str] = (
            formation.get("tool_policy", {}).get("restricted", [])
        )
        all_restricted = sorted(set(restricted) | set(formation_restricted))

        lines = ["## Available Tools"]
        if available:
            lines.append(f"You have access to: {', '.join(available)}")
        else:
            lines.append(
                "You have access to: read_file, write_file, edit_file, bash, "
                "glob_files, grep"
            )
        if all_restricted:
            lines.append(f"Restricted tools: {', '.join(all_restricted)}")
        else:
            lines.append("Restricted tools: none")

        return "\n".join(lines)

    def _build_section_constraints(self, formation: dict[str, Any]) -> str:
        """Section 6 — File ownership + forbidden paths + rules."""
        ownership: list[str] = formation.get("ownership", [])
        forbidden: list[str] = formation.get("forbidden_paths", [])

        lines = ["## Constraints"]

        if ownership:
            ownership_str = ", ".join(ownership)
            lines.append(f"- Only modify files in: {ownership_str}")
        else:
            lines.append("- Only modify files within the project root")

        lines.append("- Do NOT modify files outside your ownership")

        if forbidden:
            for path in forbidden:
                lines.append(f"- Do NOT touch: {path}")

        lines.append("- Read existing files before editing")
        lines.append("- Report blockers instead of guessing")

        return "\n".join(lines)

    def _build_section_output(self) -> str:
        """Section 7 — Expected deliverables."""
        return (
            "## Expected Output\n"
            "- Complete the task described above\n"
            "- Report files changed and their purpose\n"
            "- If blocked, explain why and what's needed"
        )

    # ── Frontmatter parsing ───────────────────────────────────────────────────

    def _parse_frontmatter(self, raw: str) -> tuple[dict, str]:
        """Parse YAML frontmatter between --- markers.

        Returns (frontmatter_dict, body_markdown).
        Falls back to empty dict if frontmatter is malformed or yaml is absent.
        """
        raw = raw.lstrip()
        if not raw.startswith("---"):
            return {}, raw

        # Find closing ---
        end_marker = raw.find("\n---", 3)
        if end_marker == -1:
            return {}, raw

        fm_text = raw[3:end_marker].strip()
        body = raw[end_marker + 4:].lstrip("\n")

        if _YAML_AVAILABLE:
            try:
                parsed = _yaml.safe_load(fm_text)
                if isinstance(parsed, dict):
                    return parsed, body
            except _yaml.YAMLError:
                pass
        else:
            # Simple key: value parser (no nesting, no lists)
            result: dict[str, str] = {}
            for line in fm_text.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    result[k.strip()] = v.strip()
            if result:
                return result, body

        return {}, body
