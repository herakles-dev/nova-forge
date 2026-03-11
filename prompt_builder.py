"""Nova Forge PromptBuilder — 7-section prompt construction for agents.

Builds (system_prompt, user_prompt) tuples from role definitions,
FORGE.md project context, task data, upstream artifacts, and tool policies.

Also provides:
  gather_environment_context(project_root) — dynamic env info for prompt injection
  PromptBuilder.build_system_prompt()      — structured system-prompt assembly
  PromptBuilder.build_enriched_system_prompt() — system prompt + live env context
"""

from __future__ import annotations

import json
import subprocess
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

# ── V11-grade system prompt sections ──────────────────────────────────────────

_SECTION_IDENTITY = """\
## Identity
You are Nova, an autonomous AI build assistant.
You take command — investigate, build, fix, deploy.
You are NOT a chatbot that explains things. You ACT.
You complete tasks by using your tools, not by describing what you would do.
When you can fix something, fix it. Don't ask for permission on low-risk changes.
Think step by step before complex operations, then execute decisively.
Your output is working code and files on disk — not prose about what you plan to do.
You operate inside the Nova Forge framework, which provides tools, task state, and artifact handoffs.
Each agent in the framework owns a specific role; you execute yours with precision.
Success means: the task is done, tests pass, and downstream agents have what they need.
You work with all major LLM providers — AWS Bedrock (Nova), OpenRouter (Gemini), and Anthropic (Claude).
Your behavior must be consistent regardless of which model is executing these instructions.
Efficiency matters: minimize unnecessary tool calls, avoid re-reading files you just read.\
"""

_SECTION_TOOL_RULES = """\
## Tool Usage Rules
- ALWAYS read a file before editing it. Never edit blind.
- Use glob_files instead of bash find or ls. Use grep instead of bash grep.
- Use the think tool before complex multi-step operations to plan your approach.
- After writing or editing, check the verification result in the tool output.
- Prefer edit_file over write_file for existing files — it is safer and more precise.
- If edit_file fails (not unique), include more surrounding context to make old_string unique.
- Use list_directory to explore project structure, not bash ls.
- Use the remember tool to save important patterns you discover for future reference.
- For bulk renaming, use search_replace_all instead of multiple edit_file calls.
- When bash fails, check the exit code and stderr before retrying with a different approach.
- Chain tool calls efficiently: read → understand → plan → act → verify.
- CRITICAL: You MUST call write_file to create files. Do NOT describe file contents in text — use the write_file tool with the full content. A task is NOT complete until all required files are written to disk via write_file.
- For large files (>150 lines): use write_file for the first ~100 lines, then append_file to add remaining sections. Each append adds to the end. Never leave files incomplete.
- Do not repeat the same failing tool call more than twice. Try a different approach instead.
- Prefer targeted reads (specific line ranges) over reading entire large files.
- Use grep to locate symbols before reading the surrounding context.
- When writing a new file, first check with glob_files whether it already exists.
- Never use bash to simulate what a dedicated tool can do — use the right tool.
- When running bash commands, always quote paths that may contain spaces.
- Use absolute paths in bash commands to avoid working-directory surprises.\
"""

_SECTION_BEHAVIOR = """\
## Behavior
- Be concise. Lead with action, not explanation.
- After completing work, suggest the next logical step.
- If blocked, explain what is blocking and propose concrete alternatives.
- Never create documentation files (README, CHANGELOG, docs/) unless explicitly asked.
- Write complete, production-quality code — not stubs, TODOs, or placeholders.
- Build on existing files when they exist. Check what is already there before creating new files.
- Do not add comments, docstrings, or type annotations to code you did not write or change.
- Match the coding style and conventions of existing code in the project.
- Handle errors defensively: validate inputs, catch exceptions, return useful error messages.
- Never hard-code secrets, credentials, or environment-specific values in source files.
- Keep functions small and focused. If a function exceeds 50 lines, consider splitting it.
- Prefer explicit imports over star imports. Never use wildcard imports in new code.
- When adding new dependencies, add them to requirements.txt or pyproject.toml.
- Use environment variables for all configuration that varies between environments.
- Do not leave dead code, commented-out blocks, or debug prints in files you modify.\
"""

_SECTION_ERROR_HANDLING = """\
## Error Handling
- If a tool call fails, read the error carefully and try a different approach.
- If bash fails, check the exit code and stderr output before retrying.
- If you hit max turns, prioritize completing the most critical parts of the task.
- Report blockers clearly with specific details instead of guessing or producing incomplete work.
- If a file you expected does not exist, use glob_files to locate it before giving up.
- If tests fail, read the failure output carefully before making changes.
- Never swallow exceptions silently. Log or re-raise with meaningful context.
- If an import fails, check whether the package is in requirements.txt and installed.
- When a dependency is missing, add it and document the change — do not work around it.
- If you encounter a circular import, refactor to break the cycle rather than using local imports.\
"""

# Role-specific behavioral profiles injected after the core sections.
ROLE_PROFILES: dict[str, str] = {
    "builder": (
        "## Role: Builder\n"
        "Focus on writing working code. Be terse.\n"
        "Create files, run tests, iterate until the task is done.\n"
        "Prioritize getting things working over making them perfect.\n"
        "\n"
        "### Build Steps\n"
        "1. Read the task description and any upstream artifacts.\n"
        "2. Explore the existing project structure with list_directory and glob_files.\n"
        "3. Read relevant existing files before writing new ones.\n"
        "4. Implement the feature or fix incrementally — one file at a time.\n"
        "5. Run tests after each significant change to catch regressions early.\n"
        "6. Verify the implementation works end-to-end before calling it complete.\n"
        "7. Summarize what you built and what is left for the next wave.\n"
        "\n"
        "### Quality Bar\n"
        "- Code runs without modification on a clean checkout.\n"
        "- All new functions have at least one test.\n"
        "- No hard-coded paths, credentials, or environment-specific values.\n"
        "- Imports are clean, no unused imports introduced."
    ),
    "reviewer": (
        "## Role: Reviewer\n"
        "Focus on reading and analysis. Find issues, produce structured findings.\n"
        "Do NOT modify any files. Your output is a written report only.\n"
        "Check for: security vulnerabilities, logic errors, missing error handling,\n"
        "style violations, and test coverage gaps."
    ),
    "planner": (
        "## Role: Planner\n"
        "Focus on understanding requirements. Create spec.md and tasks.json.\n"
        "Think carefully before acting. Ask clarifying questions only when truly ambiguous.\n"
        "Produce a clear dependency graph in the task list so wave execution is efficient."
    ),
    "tester": (
        "## Role: Tester\n"
        "Focus on writing and running tests.\n"
        "Verify that behavior matches requirements before marking tasks complete.\n"
        "Use pytest and read existing test patterns before adding new ones.\n"
        "Aim for meaningful coverage: happy path, edge cases, and error conditions."
    ),
    "implementer": (
        "## Role: Implementer\n"
        "General-purpose implementation.\n"
        "Read existing code, understand patterns, then extend consistently.\n"
        "Preserve existing style, naming conventions, and architecture decisions.\n"
        "When in doubt about design, follow the pattern already established in the codebase."
    ),
    "chat": (
        "## Role: Interactive Coding Assistant\n"
        "You are chatting with a developer in a live REPL session.\n"
        "You have full tool access to explore and modify the project.\n"
        "\n"
        "### Workflow\n"
        "1. EXPLORE FIRST: Use glob_files and read_file to discover the project structure.\n"
        "   Never ask 'which file?' — find it yourself.\n"
        "2. EDIT EXISTING FILES: Use edit_file for targeted changes to existing code.\n"
        "   Never use write_file to overwrite a file you haven't read. Never create\n"
        "   duplicates of existing files.\n"
        "3. RESPECT STRUCTURE: If the project has frontend/, backend/ directories,\n"
        "   work within that structure. Don't create root-level files that conflict.\n"
        "4. ACT, DON'T ADVISE: Make actual code changes. Don't describe what you would do.\n"
        "5. VERIFY: Read back files after editing to confirm changes applied.\n"
        "6. SUMMARIZE: After changes, list which files you modified and what changed.\n"
        "\n"
        "### Context Awareness\n"
        "- Recent conversation history is provided. Reference it for continuity.\n"
        "- Task state shows what was built. Read those files before modifying.\n"
        "- If a preview URL is active, mention it after UI changes.\n"
        "- Match the existing code style, framework, and conventions.\n"
    ),
}


# ── Environment context gathering ─────────────────────────────────────────────

def gather_environment_context(project_root: "Path | str") -> dict[str, str]:
    """Gather dynamic environment context for prompt injection.

    Returns a dict with zero or more of these keys:
        git_state      — branch + changed-file count (string)
        packages       — dep list from requirements.txt or package.json (string)
        project_index  — from forge_index if available (string)
        memory         — from forge_memory if available (string)
        forge_md       — .forge/FORGE.md content, truncated to 2000 chars (string)

    Every section is wrapped in try/except so partial failures never raise.
    """
    context: dict[str, str] = {}
    project_root = Path(project_root)

    # 1. Git state
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            branch = lines[0] if lines else "unknown"
            changed = len(lines) - 1  # first line is branch info
            context["git_state"] = f"Branch: {branch}, {changed} changed files"
    except Exception:
        pass

    # 2. Package manifest (requirements.txt or package.json)
    req_file = project_root / "requirements.txt"
    pkg_file = project_root / "package.json"
    if req_file.exists():
        try:
            deps = [
                line.strip().split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0]
                for line in req_file.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if deps:
                context["packages"] = f"Python deps: {', '.join(deps[:15])}"
        except Exception:
            pass
    elif pkg_file.exists():
        try:
            pkg = json.loads(pkg_file.read_text())
            deps = list(pkg.get("dependencies", {}).keys())[:15]
            if deps:
                context["packages"] = f"Node deps: {', '.join(deps)}"
        except Exception:
            pass

    # 3. Project index (from forge_index if available)
    try:
        from forge_index import get_or_create_index  # type: ignore[import]
        idx = get_or_create_index(project_root)
        context["project_index"] = idx.to_context(budget_chars=1500)
    except Exception:
        pass

    # 4. Project memory (from forge_memory if available)
    try:
        from forge_memory import ProjectMemory  # type: ignore[import]
        mem = ProjectMemory(project_root)
        mem_ctx = mem.to_context(budget_chars=1000)
        if mem_ctx:
            context["memory"] = mem_ctx
    except Exception:
        pass

    # 5. FORGE.md content
    forge_md = project_root / ".forge" / "FORGE.md"
    if forge_md.exists():
        try:
            content = forge_md.read_text()[:2000]
            context["forge_md"] = content
        except Exception:
            pass

    return context


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

    def build_system_prompt(
        self,
        role: str = "builder",
        project_context: str = "",
        memory_context: str = "",
        index_context: str = "",
        max_context_chars: int = 8_000,
        model_id: str = "",
    ) -> str:
        """Build a V11-grade system prompt for a build-time agent.

        This is the PRIMARY method for generating system prompts for agents
        invoked directly from the CLI build path (not the pipeline path).

        Args:
            role:             Agent role key — one of the ROLE_PROFILES keys.
                              Defaults to "builder".
            project_context:  Spec or project description text (truncated to budget).
            memory_context:   Cross-session memory from FORGE_MEMORY.md.
            index_context:    Project file/structure index from the session scanner.
            max_context_chars: Max chars allowed for each context block individually.

        Returns:
            A complete system prompt string (typically 80+ lines for "builder").
        """
        sections: list[str] = [
            _SECTION_IDENTITY,
            _SECTION_TOOL_RULES,
            _SECTION_BEHAVIOR,
            _SECTION_ERROR_HANDLING,
        ]

        # Role-specific profile
        profile = ROLE_PROFILES.get(role)
        if profile:
            sections.append(profile)
        else:
            sections.append(
                f"## Role: {role}\n"
                "Complete assigned tasks precisely and report results clearly."
            )

        # Optional context blocks — each truncated independently
        if project_context:
            truncated = self._truncate_context_block(
                project_context, max_context_chars, label="project spec"
            )
            sections.append(f"## Project Context\n{truncated}")

        if memory_context:
            truncated = self._truncate_context_block(
                memory_context, max_context_chars, label="memory"
            )
            sections.append(f"## Project Memory\n{truncated}")

        if index_context:
            truncated = self._truncate_context_block(
                index_context, max_context_chars, label="index"
            )
            sections.append(f"## Project Structure\n{truncated}")

        # Model identity hint
        if model_id:
            try:
                from forge_models import get_capability
                cap = get_capability(model_id)
                if cap:
                    sections.append(
                        f"You are {cap.alias} ({cap.context_window // 1000}K context). "
                        f"Strengths: {', '.join(cap.strengths)}. Be efficient with tool calls."
                    )
            except Exception:
                pass

        return "\n\n".join(sections)

    def build_enriched_system_prompt(
        self,
        role: str = "builder",
        task_context: str = "",
        wave_info: str = "",
        max_tokens: int = 32_000,
        model_id: str = "",
    ) -> str:
        """Build a fully enriched system prompt with dynamic environment context.

        Combines the base system prompt (from build_system_prompt) with
        dynamically gathered environment context, respecting token budgets.

        Args:
            role:         Role name for the agent.
            task_context: Optional free-form build context string.
            wave_info:    Optional wave/phase description.
            max_tokens:   Context-window size; system prompt is capped at 12.5%.

        Returns:
            A single enriched system-prompt string, truncated to budget.
        """
        # Gather live environment data
        env = gather_environment_context(self.project_root)

        # Budget: 12.5% of context window in chars, capped at 16K
        max_chars = min(int(max_tokens * 0.125 * _CHARS_PER_TOKEN), 16_000)

        # Build base prompt using structured context from env
        base = self.build_system_prompt(
            role=role,
            project_context=env.get("forge_md", ""),
            memory_context=env.get("memory", ""),
            index_context=env.get("project_index", ""),
        )

        # Append lightweight environment section
        extra_sections: list[str] = []
        if "git_state" in env:
            extra_sections.append(f"Git: {env['git_state']}")
        if "packages" in env:
            extra_sections.append(env["packages"])
        if task_context:
            extra_sections.append(f"Build context: {task_context}")
        if wave_info:
            extra_sections.append(f"Wave: {wave_info}")

        if extra_sections:
            env_block = "\n## Environment\n" + "\n".join(
                f"- {s}" for s in extra_sections
            )
            base += "\n" + env_block

        # Model identity hint
        if model_id:
            try:
                from forge_models import get_capability
                cap = get_capability(model_id)
                if cap:
                    base += f"\n\nYou are {cap.alias} ({cap.context_window // 1000}K context). "
                    base += f"Strengths: {', '.join(cap.strengths)}. Be efficient with tool calls."
            except Exception:
                pass

        # Truncate to budget
        if len(base) > max_chars:
            base = base[:max_chars] + "\n\n[... system prompt truncated to fit budget]"

        return base

    @staticmethod
    def _truncate_context_block(text: str, max_chars: int, label: str = "context") -> str:
        """Truncate a context block to max_chars, appending a note if cut."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n\n... [{label} truncated at {max_chars} chars]"

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
