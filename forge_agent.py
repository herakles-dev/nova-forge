"""Nova Forge Agent — the core tool-use loop replacing Claude Code.

ForgeAgent sends a prompt + tools to any LLM via ModelRouter, executes
tool calls with hook enforcement and PathSandbox checks, and loops until
the model stops requesting tools or max_turns is reached.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import random
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import ModelConfig, get_model_config, get_provider, get_prompt_budget
from forge_guards import PathSandbox, RiskClassifier, RiskLevel, SandboxViolation, AutonomyManager
from forge_hooks import HookSystem, HookResult
from model_router import ModelRouter, ModelResponse, ToolCall, StreamDelta

logger = logging.getLogger(__name__)

MAX_API_RETRIES = 3

# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Outcome of a ForgeAgent.run() invocation."""
    output: str = ""
    turns: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    tool_calls_made: int = 0
    error: str | None = None
    model_id: str = ""        # which model completed this
    tokens_in: int = 0        # total input tokens
    tokens_out: int = 0       # total output tokens
    escalated: bool = False   # was model escalated?


@dataclass
class AgentEvent:
    """Structured event emitted during agent execution."""
    kind: str              # turn_start, model_response, tool_start, tool_end, compact, error, stream_delta, model_escalation
    turn: int = 0
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    file_path: str = ""
    file_action: str = ""  # read, write, edit, run, search
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    error: str = ""
    delta: Any = None      # StreamDelta for stream_delta events


# ── Tool definitions (common format for all providers) ───────────────────────

BUILT_IN_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns line-numbered content with a metadata header.\n\n"
            "- You MUST read a file before editing it with edit_file.\n"
            "- Use offset and limit for large files (e.g., offset=100, limit=50 to read lines 100-150).\n"
            "- For searching file contents, prefer grep over reading entire files.\n"
            "- Returns: metadata header (type, lines, size) + numbered content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to project root or absolute)"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-based, optional)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to return (optional, default: all)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or completely overwrite an existing file with the given content.\n\n"
            "- PREFER edit_file for modifying existing files — it is safer and more precise.\n"
            "- Never overwrite a file you haven't read first.\n"
            "- Creates parent directories automatically.\n"
            "- Runs a syntax check after writing (.py, .json, .yaml) and reports the result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to project root or absolute)"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_file",
        "description": (
            "Append content to the end of an existing file, or create it if it does not exist.\n\n"
            "- Use write_file FIRST to create the file with the initial section.\n"
            "- Then call append_file one or more times to add remaining sections.\n"
            "- For large files (>150 lines), use: write_file (first part) + append_file (rest).\n"
            "- Runs syntax check after appending (.py, .json, .yaml)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to append"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace an exact string in a file with new content. Performs a precise single replacement.\n\n"
            "- ALWAYS call read_file first to understand the current content.\n"
            "- old_string MUST appear exactly once — if it appears multiple times, include more surrounding\n"
            "  context to make it unique.\n"
            "- For renaming a variable everywhere, use search_replace_all instead.\n"
            "- Runs a syntax check after editing (.py, .json, .yaml) and reports the result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "Exact text to find (must appear exactly once)"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Execute a shell command in the project directory and return stdout + stderr.\n\n"
            "- Do NOT use for cat/head/tail — use read_file instead.\n"
            "- Do NOT use for grep/find/ls — use grep, glob_files, list_directory instead.\n"
            "- Always check exit codes in the output (non-zero = failure).\n"
            "- Long-running commands have a 120-second timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory (default: project root)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "glob_files",
        "description": (
            "Find files matching a glob pattern. Use INSTEAD of bash find or ls.\n\n"
            "- Patterns: '**/*.py' (all Python files), 'src/**/*.ts' (TypeScript in src/).\n"
            "- Returns relative paths sorted by modification time (newest first).\n"
            "- Use path parameter to restrict search to a subdirectory.\n"
            "- Faster and safer than running bash find commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"},
                "path": {"type": "string", "description": "Base directory to search (default: project root)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Search file contents by regex pattern. Use INSTEAD of bash grep.\n\n"
            "- Returns line numbers, match count summary, and matching lines.\n"
            "- Supports standard regex: '\\bfoo\\b', 'def \\w+', 'import.*from'.\n"
            "- Searches .py, .js, .ts, .json, .yaml, .yml, .md, .txt, .html, .css, .sh files.\n"
            "- Compact output: first 50 matches shown, with total count."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search (default: project root)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "think",
        "description": (
            "Use this tool to reason through a problem step by step before taking action. "
            "The output is not shown to the user — this is your private scratchpad.\n\n"
            "Use it when:\n"
            "- Planning multi-step changes\n"
            "- Debugging complex issues\n"
            "- Weighing multiple approaches\n"
            "- Before writing significant code"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string", "description": "Your step-by-step reasoning"},
            },
            "required": ["reasoning"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List contents of a directory with file types, sizes, and item counts.\n\n"
            "Use this instead of bash ls. Returns structured output with metadata.\n"
            "Subdirectories show item counts. Files show size and modification time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: project root)"},
            },
            "required": [],
        },
    },
    {
        "name": "search_replace_all",
        "description": (
            "Replace ALL occurrences of a string in a file. Use for renaming variables, "
            "updating imports, or bulk replacements.\n\n"
            "For single precise edits, use edit_file instead.\n"
            "Returns the count of replacements made."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "String to find (all occurrences)"},
                "new_string": {"type": "string", "description": "Replacement string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Save a note to project memory that persists across sessions.\n\n"
            "Use for: patterns discovered, conventions confirmed, user preferences, "
            "solutions to recurring problems. Do NOT save session-specific context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "What to remember"},
                "category": {"type": "string", "description": "Category: pattern | preference | solution | convention"},
            },
            "required": ["note"],
        },
    },
    {
        "name": "claim_file",
        "description": (
            "Claim exclusive write access to a file. Other agents cannot modify "
            "files you've claimed. Call BEFORE writing to prevent conflicts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "check_context",
        "description": (
            "Check what other agents have done: files claimed/written, "
            "announcements (endpoints, exports), module dependencies."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "Optional filter: 'api', 'frontend', 'imports'"},
            },
            "required": [],
        },
    },
]


# ── Slim tool definitions for 32K models ─────────────────────────────────────
# 8 essential tools with 1-line descriptions (~2,800 chars vs 7,312 for full set)

SLIM_TOOLS: list[dict] = [
    {"name": "read_file", "description": "Read a file. Args: path, offset (opt), limit (opt).",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}
     }, "required": ["path"]}},
    {"name": "write_file", "description": "Create/overwrite a file. Max ~80 lines per call.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}
     }, "required": ["path", "content"]}},
    {"name": "append_file", "description": "Append to file (or create). Use after write_file for large files.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}
     }, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace old_string with new_string in a file. old_string must be unique.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}
     }, "required": ["path", "old_string", "new_string"]}},
    {"name": "bash", "description": "Run a shell command.",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string"}
     }, "required": ["command"]}},
    {"name": "glob_files", "description": "Find files by pattern (e.g. '**/*.py').",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}
     }, "required": ["pattern"]}},
    {"name": "grep", "description": "Search file contents by regex.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"}
     }, "required": ["pattern"]}},
    {"name": "list_directory", "description": "List files in a directory.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}
     }, "required": ["path"]}},
]


def get_tools_for_model(context_window: int, has_build_context: bool = False) -> list[dict]:
    """Return appropriate tool set based on model context window size.

    32K models get SLIM_TOOLS (8 essential tools, ~1,350 fewer tokens per turn).
    Larger models get the full BUILT_IN_TOOLS set.
    """
    if context_window <= 32_000:
        if has_build_context:
            return SLIM_TOOLS + [t for t in BUILT_IN_TOOLS if t["name"] in ("claim_file", "check_context")]
        return list(SLIM_TOOLS)
    return list(BUILT_IN_TOOLS)


# ── ForgeAgent ───────────────────────────────────────────────────────────────

class ForgeAgent:
    """Core agent runtime: prompt → LLM → tool calls → execute → loop.

    Replaces Claude Code's closed-source agent with ~200 lines of Python
    that works with any LLM supporting function calling.
    """

    def __init__(
        self,
        model_config: ModelConfig,
        project_root: Path | str = ".",
        hooks: HookSystem | None = None,
        sandbox: PathSandbox | None = None,
        tools: list[dict] | None = None,
        max_turns: int = 25,
        agent_id: str = "forge-agent",
        wire_v11_hooks: bool = True,
        on_event: Any = None,
        streaming: bool = True,
        escalation_model: str | None = None,
        build_context: Any = None,
    ) -> None:
        self.model_config = model_config
        self.project_root = Path(project_root).resolve()
        self.router = ModelRouter()
        self.hooks = hooks or HookSystem()
        self.sandbox = sandbox or PathSandbox(self.project_root, extra_allowed=[Path(tempfile.gettempdir())])
        self.risk_classifier = RiskClassifier()
        self.tools = tools if tools is not None else BUILT_IN_TOOLS
        self.max_turns = max_turns
        self.agent_id = agent_id
        self.provider = get_provider(model_config.model_id)
        self._hook_state = None
        self._files_read: set[str] = set()
        self.on_event = on_event
        self.streaming = streaming
        self.escalation_model = escalation_model
        self._escalated = False
        self.autonomy_manager: AutonomyManager | None = None
        self.build_context = build_context  # BuildContext for multi-agent coordination

        # Auto-wire V11 hooks into the active HookSystem (provided or default)
        if wire_v11_hooks:
            self._wire_v11_hooks()

    def _wire_v11_hooks(self) -> None:
        """Auto-wire the 12 V11 hook implementations into the HookSystem."""
        try:
            from forge_hooks_impl import wire_all_hooks
            autonomy_file = self.project_root / ".forge" / "state" / "autonomy.json"
            am = None
            if autonomy_file.exists():
                am = AutonomyManager(autonomy_file)
            self._hook_state = wire_all_hooks(
                self.hooks,
                project_root=self.project_root,
                autonomy_manager=am,
            )
            if am is not None:
                self.autonomy_manager = am
            logger.debug("V11 hooks auto-wired for project: %s", self.project_root.name)
        except ImportError:
            logger.debug("forge_hooks_impl not available — running without V11 hooks")
        except Exception as exc:
            logger.warning("Failed to wire V11 hooks: %s", exc)

    async def run(
        self,
        prompt: str,
        system: str = "",
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute the agent loop.

        Args:
            prompt: User instruction for the agent.
            system: Optional system message prepended to conversation.
            context: Optional context dict injected into the first user message.
        """
        messages = self._build_initial_messages(prompt, system, context)
        artifacts: dict[str, Any] = {}
        total_tool_calls = 0
        _total_in = 0
        _total_out = 0

        for turn in range(self.max_turns):
            # Emit turn_start event
            if self.on_event:
                try:
                    self.on_event(AgentEvent(kind="turn_start", turn=turn + 1))
                except Exception:
                    pass

            # Call the model with retry logic for transient errors
            response = None
            last_error = None
            for attempt in range(MAX_API_RETRIES):
                try:
                    if self.streaming:
                        response = await self.router.stream_send(
                            messages, self.tools, self.model_config,
                            on_delta=self._on_stream_delta,
                        )
                    else:
                        response = await self.router.send(messages, self.tools, self.model_config)
                    break  # Success
                except Exception as exc:
                    last_error = exc
                    error_str = str(exc).lower()
                    # Retry on transient errors
                    if any(code in error_str for code in ("429", "500", "502", "503", "throttl", "rate")):
                        logger.warning(
                            "Transient error (attempt %d/%d): %s",
                            attempt + 1, MAX_API_RETRIES, exc,
                        )
                        if self.on_event:
                            self.on_event(AgentEvent(kind="error", error=f"Retry {attempt + 1}: {exc}"))
                        if attempt < MAX_API_RETRIES - 1:
                            delay = min(2 ** attempt + random.uniform(0, 1), 30)
                            logger.warning("Retrying in %.1fs", delay)
                            await asyncio.sleep(delay)
                        continue
                    # Context overflow — compact and retry once
                    if "context" in error_str and (
                        "length" in error_str or "exceed" in error_str or "too long" in error_str
                    ):
                        logger.warning("Context overflow — compacting and retrying")
                        budget = get_prompt_budget(self.model_config.context_window)
                        messages = self._compact_messages(messages, budget)
                        continue
                    # Non-transient error — fail immediately
                    break

            if response is None:
                logger.error("Model call failed on turn %d after %d attempts: %s", turn, MAX_API_RETRIES, last_error)
                return AgentResult(
                    output=f"Model error after {MAX_API_RETRIES} attempts: {last_error}",
                    turns=turn + 1,
                    artifacts=artifacts,
                    tool_calls_made=total_tool_calls,
                    error=str(last_error),
                    model_id=self.model_config.model_id,
                    tokens_in=_total_in,
                    tokens_out=_total_out,
                )

            # Track tokens
            _resp_in = response.usage.get("input_tokens", 0)
            _resp_out = response.usage.get("output_tokens", 0)
            _total_in += _resp_in
            _total_out += _resp_out

            # Emit model_response event
            if self.on_event:
                try:
                    self.on_event(AgentEvent(
                        kind="model_response", turn=turn + 1,
                        tokens_in=_resp_in,
                        tokens_out=_resp_out,
                    ))
                except Exception:
                    pass

            tool_calls = self.router.extract_tool_calls(response)

            # Self-correction for malformed tool calls
            if tool_calls:
                valid_calls = []
                malformed = []
                for call in tool_calls:
                    if not isinstance(call.args, dict):
                        malformed.append(call)
                    elif "_raw" in call.args or "_truncated" in call.args:
                        malformed.append(call)
                    else:
                        valid_calls.append(call)

                if malformed and not valid_calls:
                    # All calls malformed — inject error and let model retry
                    has_truncated = any("_truncated" in (c.args if isinstance(c.args, dict) else {}) or "_raw" in (c.args if isinstance(c.args, dict) else {}) for c in malformed)
                    if has_truncated:
                        error_msg = (
                            "Tool call truncated — output hit token limit. "
                            "Write SHORTER content: max ~80 lines per write_file. "
                            "Use write_file for first 80 lines, then append_file for the rest. "
                        )
                    else:
                        error_msg = "Your tool calls had invalid arguments. Please retry with valid JSON. Errors: "
                    for mc in malformed:
                        error_msg += f"{mc.name}(args={mc.args!r}) — args must be a JSON object. "
                    adapter = self.router.route(self.model_config.model_id)
                    messages.append(adapter.format_assistant_message(response))
                    messages.append({"role": "user", "content": error_msg})
                    continue  # Let the model retry this turn

                tool_calls = valid_calls  # Use only valid calls

            # No tool calls → agent is done
            if not tool_calls:
                await self.hooks.on_stop(project=self.project_root.name)
                return AgentResult(
                    output=response.text,
                    turns=turn + 1,
                    artifacts=artifacts,
                    tool_calls_made=total_tool_calls,
                    model_id=self.model_config.model_id,
                    tokens_in=_total_in,
                    tokens_out=_total_out,
                )

            # Append assistant message to history
            adapter = self.router.route(self.model_config.model_id)
            messages.append(adapter.format_assistant_message(response))

            # Execute each tool call
            for call in tool_calls:
                total_tool_calls += 1

                # Emit tool_start event
                _tool_file = call.args.get("path", call.args.get("file_path", call.args.get("pattern", ""))) if isinstance(call.args, dict) else ""
                if self.on_event:
                    try:
                        self.on_event(AgentEvent(
                            kind="tool_start", turn=turn + 1,
                            tool_name=call.name,
                            tool_args=call.args if isinstance(call.args, dict) else {},
                            file_path=str(_tool_file),
                        ))
                    except Exception:
                        pass

                _tool_t0 = time.monotonic()
                result_str = await self._execute_tool_call(call, artifacts)
                _tool_dur = int((time.monotonic() - _tool_t0) * 1000)

                # Determine file action
                _fa = {"read_file": "read", "write_file": "write", "append_file": "append",
                       "edit_file": "edit", "bash": "run", "glob_files": "search",
                       "grep": "search", "search_replace_all": "edit"}.get(call.name, "")
                _tool_err = ""
                if result_str and ("ERROR" in result_str[:80] or "BLOCKED" in result_str[:80]):
                    _tool_err = result_str[:200]

                # Emit tool_end event
                if self.on_event:
                    try:
                        self.on_event(AgentEvent(
                            kind="tool_end", turn=turn + 1,
                            tool_name=call.name,
                            file_path=str(_tool_file),
                            file_action=_fa,
                            duration_ms=_tool_dur,
                            error=_tool_err,
                        ))
                    except Exception:
                        pass

                messages.append(
                    adapter.format_tool_result(call.id, result_str)
                )

            # Context compaction — threshold from budget (60% for 32K, 75% for 200K, 80% for 1M+)
            estimated_tokens = self._estimate_tokens(messages)
            budget = get_prompt_budget(self.model_config.context_window)
            if estimated_tokens > self.model_config.context_window * budget["compaction_threshold"]:
                messages = self._compact_messages(messages, budget)
                logger.info(
                    "Compacted context: %d tokens → %d tokens",
                    estimated_tokens,
                    self._estimate_tokens(messages),
                )

        # Escalation: if max_turns hit and we have an escalation target, retry with smarter model
        if self.escalation_model and not self._escalated:
            self._escalated = True
            new_config = get_model_config(self.escalation_model, max_tokens=self.model_config.max_tokens)

            if self.on_event:
                try:
                    self.on_event(AgentEvent(
                        kind="model_escalation",
                        error=f"Escalating: {self.model_config.short_name} -> {new_config.short_name}",
                    ))
                except Exception:
                    pass

            old_config = self.model_config
            old_provider = self.provider
            self.model_config = new_config
            self.provider = get_provider(new_config.model_id)

            escalated_result = await self.run(prompt=prompt, system=system, context=context)
            escalated_result.escalated = True
            escalated_result.tokens_in += _total_in
            escalated_result.tokens_out += _total_out

            self.model_config = old_config
            self.provider = old_provider
            return escalated_result

        # Session end — fire stop hooks
        await self.hooks.on_stop(project=self.project_root.name)

        return AgentResult(
            output="Max turns reached",
            turns=self.max_turns,
            artifacts=artifacts,
            tool_calls_made=total_tool_calls,
            error="max_turns_exceeded",
            model_id=self.model_config.model_id,
            tokens_in=_total_in,
            tokens_out=_total_out,
        )

    # ── Message construction ─────────────────────────────────────────────────

    def _build_initial_messages(
        self,
        prompt: str,
        system: str,
        context: dict[str, Any] | None,
    ) -> list[dict]:
        """Build the initial message list for the conversation."""
        messages: list[dict] = []

        # System message (provider-specific handling)
        if system:
            if self.provider == "bedrock":
                # Bedrock uses a separate system parameter, but we embed in first user msg
                # for simplicity across the message format
                prompt = f"{system}\n\n---\n\n{prompt}"
            elif self.provider == "anthropic":
                # Anthropic handles system separately in the API call,
                # but we keep it in messages for our normalized format
                messages.append({"role": "user", "content": f"[System]\n{system}"})
                messages.append({"role": "assistant", "content": "Understood. I'll follow these instructions."})
            else:
                messages.append({"role": "system", "content": system})

        # Context injection
        if context:
            context_block = "\n".join(
                f"## {k}\n{v}" for k, v in context.items()
            )
            prompt = f"{prompt}\n\n---\nContext:\n{context_block}"

        messages.append({"role": "user", "content": prompt})
        return messages

    # ── Tool execution ───────────────────────────────────────────────────────

    async def _execute_tool_call(
        self,
        call: ToolCall,
        artifacts: dict[str, Any],
    ) -> str:
        """Execute a single tool call with hook enforcement and sandbox checks."""
        # Pre-tool hook
        hook_result = await self.hooks.pre_tool_use(call.name, call.args)
        if hook_result.blocked:
            return f"BLOCKED by hook: {hook_result.reason}"

        # Use modified args if hook provided them
        args = hook_result.modified_args if hook_result.modified_args else call.args

        # Reject truncated tool calls that slipped past malformed detection
        if "_raw" in args or "_truncated" in args:
            return (
                f"ERROR: Tool '{call.name}' had truncated args (output limit). "
                f"Write shorter content — max ~80 lines per call. Use append_file for the rest."
            )

        # Risk check — autonomy-aware when AutonomyManager is wired in
        command = args.get("command", "")
        file_path = args.get("path", "")
        risk = self.risk_classifier.classify(call.name, command, file_path)
        if self.autonomy_manager:
            allowed = self.autonomy_manager.check_permission(risk)
            if not allowed:
                level = self.autonomy_manager.current_level
                logger.debug(
                    "Autonomy A%d blocked %s risk: %s", level, risk.name, call.name
                )
                return (
                    f"BLOCKED by autonomy level A{level}: "
                    f"{risk.name} risk operation ({call.name}) requires higher trust. "
                    f"Current level: A{level}."
                )
        elif risk == RiskLevel.HIGH:
            logger.warning("HIGH risk tool call blocked: %s %s", call.name, args)
            return f"BLOCKED: HIGH risk operation detected ({call.name}). Requires user approval."

        # Execute the tool
        try:
            result_str = await self._run_tool(call.name, args, artifacts)
        except SandboxViolation as exc:
            result_str = f"SANDBOX VIOLATION: {exc}"
        except Exception as exc:
            result_str = f"ERROR: {type(exc).__name__}: {exc}"

        # Post-tool hook
        await self.hooks.post_tool_use(call.name, call.args, result_str)

        return result_str

    async def _run_tool(
        self,
        name: str,
        args: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> str:
        """Dispatch to the appropriate tool implementation."""
        if name == "read_file":
            return await self._tool_read_file(args)
        elif name == "write_file":
            return await self._tool_write_file(args, artifacts)
        elif name == "append_file":
            return await self._tool_append_file(args, artifacts)
        elif name == "edit_file":
            return await self._tool_edit_file(args, artifacts)
        elif name == "bash":
            return await self._tool_bash(args)
        elif name == "glob_files":
            return await self._tool_glob(args)
        elif name == "grep":
            return await self._tool_grep(args)
        elif name == "think":
            return "Reasoning noted."
        elif name == "list_directory":
            return await self._tool_list_directory(args)
        elif name == "search_replace_all":
            return await self._tool_search_replace_all(args, artifacts)
        elif name == "remember":
            return await self._tool_remember(args)
        elif name == "claim_file":
            return self._tool_claim_file(args)
        elif name == "check_context":
            return self._tool_check_context(args)
        else:
            return f"Unknown tool: {name}"

    # ── Tool implementations ─────────────────────────────────────────────────

    async def _tool_read_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        self.sandbox.validate_read(path)
        if not path.exists():
            return f"File not found: {path}"

        # Binary detection
        try:
            with open(path, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:
                    size_kb = path.stat().st_size / 1024
                    return f"Binary file: {path.name} ({size_kb:.1f} KB)"
        except Exception:
            pass

        self._files_read.add(str(path))
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.split('\n')
        total_lines = len(lines)

        # Offset/limit support
        has_offset = args.get("offset") is not None
        has_limit = args.get("limit") is not None
        offset = (args.get("offset") or 1) - 1  # Convert to 0-based
        limit = args.get("limit") or total_lines
        offset = max(0, min(offset, total_lines))
        selected = lines[offset:offset + limit]

        # Smart head+tail truncation for large files with no explicit offset/limit
        omit_notice = ""
        if not has_offset and not has_limit and total_lines > 300:
            head = lines[:80]
            tail = lines[-20:]
            omitted = total_lines - 80 - 20
            omit_notice = f"\n[... {omitted} lines omitted — use offset/limit to read specific sections ...]\n"
            selected = head + ["<omitted>"] + tail
            # We'll replace the placeholder when building numbered output

        # Metadata header
        ext = path.suffix.lower()
        type_map = {
            '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
            '.html': 'HTML', '.css': 'CSS', '.json': 'JSON', '.yml': 'YAML',
            '.yaml': 'YAML', '.md': 'Markdown', '.sh': 'Shell',
        }
        file_type = type_map.get(ext, ext.lstrip('.').upper() or 'text')
        size_kb = path.stat().st_size / 1024
        rel = self._relative_path(path)
        header = f"{rel} ({file_type}, {total_lines} lines, {size_kb:.1f} KB)"

        # Line-numbered output
        if omit_notice:
            # Head section (lines 1..80)
            numbered = []
            for i, line in enumerate(lines[:80], start=1):
                numbered.append(f"{i:>6}│{line}")
            numbered.append(omit_notice.strip())
            # Tail section (last 20 lines)
            tail_start = total_lines - 20
            for i, line in enumerate(lines[-20:], start=tail_start + 1):
                numbered.append(f"{i:>6}│{line}")
        else:
            numbered = []
            for i, line in enumerate(selected, start=offset + 1):
                numbered.append(f"{i:>6}│{line}")

        # Truncation based on context window
        ctx = self.model_config.context_window
        max_chars = 8_000 if ctx <= 32_000 else (30_000 if ctx <= 128_000 else 100_000)
        result = header + "\n" + "\n".join(numbered)
        if len(result) > max_chars:
            result = result[:max_chars] + f"\n\n... (truncated, {total_lines} total lines)"

        return result

    @staticmethod
    def _unescape_content(content: str) -> str:
        """Fix Nova's double-escaping: model sometimes wraps content in quotes and escapes internals.

        Detects pattern like: "\\\"\\\"\\\"docstring..." and unescapes to: \"\"\"docstring...
        Also handles content wrapped in outer quotes: '"actual content"'
        """
        if not content:
            return content
        # Pattern: content starts with " and ends with " — model wrapped it as a JSON string
        if len(content) > 2 and content[0] == '"' and content[-1] == '"' and '\\"' in content:
            try:
                import json
                unescaped = json.loads(content)
                if isinstance(unescaped, str):
                    return unescaped
            except (json.JSONDecodeError, ValueError):
                pass
        # Pattern: backslash-escaped quotes throughout (\"  ->  ")
        if '\\"' in content and '\\n' not in content:
            content = content.replace('\\"', '"')
        return content

    async def _tool_write_file(self, args: dict, artifacts: dict) -> str:
        args["content"] = self._unescape_content(args.get("content", ""))
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        rel = self._relative_path(path)

        # Auto-claim via BuildContext if available
        if self.build_context is not None:
            if not self.build_context.claim_file(str(rel), self.agent_id):
                existing = self.build_context.is_claimed(str(rel))
                owner = existing.agent_id if existing else "unknown"
                if self.on_event:
                    self.on_event(AgentEvent(kind="file_conflict", file_path=str(rel), error=f"owned by {owner}"))
                return f"CONFLICT: {rel} is owned by agent '{owner}'. SKIP this file — it is NOT yours. Focus ONLY on your assigned files."
            self.build_context.update_claim_status(str(rel), self.agent_id, "writing")
            if self.on_event:
                self.on_event(AgentEvent(kind="file_claimed", file_path=str(rel)))

        # Read-tracking: warn on overwrite without reading (but allow new files)
        warning = ""
        if path.exists() and str(path) not in self._files_read:
            warning = (
                "WARNING: You are overwriting an existing file you haven't read. "
                "Use read_file first to understand current content, then edit_file to make changes.\n\n"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        artifacts[str(path)] = {"action": "write", "size": len(args["content"])}
        verify = await self._auto_verify(path)

        # Completeness check: detect stubs, TODOs, placeholders
        completeness = self._check_completeness(args["content"], path.suffix)

        # Auto-announce file creation with interface summary
        if self.build_context is not None:
            self.build_context.update_claim_status(str(rel), self.agent_id, "done")
            summary = self._extract_interface_summary(path)
            self.build_context.announce(self.agent_id, "file_created", f"{rel}: {summary}")

        return f"{warning}File written: {rel} ({len(args['content'])} chars){verify}{completeness}"

    async def _tool_append_file(self, args: dict, artifacts: dict) -> str:
        args["content"] = self._unescape_content(args.get("content", ""))
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        rel = self._relative_path(path)

        # Auto-claim via BuildContext if available
        if self.build_context is not None:
            if not self.build_context.claim_file(str(rel), self.agent_id):
                existing = self.build_context.is_claimed(str(rel))
                owner = existing.agent_id if existing else "unknown"
                if self.on_event:
                    self.on_event(AgentEvent(kind="file_conflict", file_path=str(rel), error=f"owned by {owner}"))
                return f"CONFLICT: {rel} is owned by agent '{owner}'. SKIP this file — it is NOT yours. Focus ONLY on your assigned files."
            self.build_context.update_claim_status(str(rel), self.agent_id, "writing")
            if self.on_event:
                self.on_event(AgentEvent(kind="file_claimed", file_path=str(rel)))

        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = args["content"]
        with open(path, "a", encoding="utf-8") as f:
            f.write(chunk)
        total = path.stat().st_size
        artifacts[str(path)] = {"action": "append", "size": total, "appended": len(chunk)}
        verify = await self._auto_verify(path)

        # Auto-announce
        if self.build_context is not None:
            self.build_context.update_claim_status(str(rel), self.agent_id, "done")
            self.build_context.announce(self.agent_id, "file_appended", f"{rel}: +{len(chunk)} chars")

        # Completeness check on full file after append
        full_content = path.read_text(encoding="utf-8", errors="replace")
        completeness = self._check_completeness(full_content, path.suffix)

        return f"Appended to {rel}: +{len(chunk)} chars (total: {total}){verify}{completeness}"

    async def _tool_edit_file(self, args: dict, artifacts: dict) -> str:
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        rel = self._relative_path(path)
        if not path.exists():
            return f"File not found: {rel}"

        # Auto-claim via BuildContext if available
        if self.build_context is not None:
            if not self.build_context.claim_file(str(rel), self.agent_id):
                existing = self.build_context.is_claimed(str(rel))
                owner = existing.agent_id if existing else "unknown"
                if self.on_event:
                    self.on_event(AgentEvent(kind="file_conflict", file_path=str(rel), error=f"owned by {owner}"))
                return f"CONFLICT: {rel} is owned by agent '{owner}'. SKIP this file — it is NOT yours. Focus ONLY on your assigned files."
            if self.on_event:
                self.on_event(AgentEvent(kind="file_claimed", file_path=str(rel)))

        # Read-tracking: BLOCK edit on unread files (agent must read first)
        if str(path) not in self._files_read:
            return (
                f"BLOCKED: You must read_file('{self._relative_path(path)}') before editing it. "
                f"Read the file first to find the exact text to replace."
            )

        content = path.read_text(encoding="utf-8", errors="replace")
        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)
        if count == 0:
            # Smart hint: show nearby matches to help agent self-correct
            hint = ""
            first_line = old.split('\n')[0].strip()[:60]
            if first_line:
                # Try exact substring match first
                matches = [i+1 for i, line in enumerate(content.split('\n')) if first_line in line]
                if not matches:
                    # Try matching key identifiers (function/class/variable names)
                    import re as _re
                    keywords = _re.findall(r'\b[a-zA-Z_]\w+\b', first_line)
                    # Find lines containing the most distinctive keyword (longest, non-common)
                    common = {'def', 'class', 'function', 'const', 'let', 'var', 'return', 'if', 'else', 'for', 'while', 'import', 'from', 'self', 'this'}
                    distinctive = [k for k in keywords if k.lower() not in common and len(k) > 2]
                    if distinctive:
                        key = distinctive[0]
                        matches = [i+1 for i, line in enumerate(content.split('\n')) if key in line]
                if matches:
                    hint = f" Similar text found at lines {matches[:5]}. Read those lines to find the exact text."
                else:
                    hint = " The text may have been modified or has different whitespace/indentation."
            return f"old_string not found in {rel}.{hint} Use read_file to see current contents."
        if count > 1:
            # Show line numbers of each occurrence
            lines_with = [i+1 for i, line in enumerate(content.split('\n')) if old.split('\n')[0] in line]
            return f"old_string appears {count} times in {rel} (lines: {lines_with[:10]}) — include more surrounding context to make it unique."
        content = content.replace(old, new, 1)
        path.write_text(content, encoding="utf-8")
        artifacts[str(path)] = {"action": "edit", "old_len": len(old), "new_len": len(new)}
        verify = await self._auto_verify(path)

        # Auto-announce file edit with interface summary
        if self.build_context is not None:
            summary = self._extract_interface_summary(path)
            self.build_context.announce(self.agent_id, "file_edited", f"{rel}: {summary}")

        return f"File edited: {rel} (replaced {len(old)} chars with {len(new)} chars){verify}"

    def _extract_interface_summary(self, path: Path, max_chars: int = 500) -> str:
        """Extract compact interface summary using AST (Python) or regex (JS/TS)."""
        if not path.exists():
            return str(path.name)
        ext = path.suffix.lower()

        # Python: AST-based extraction
        if ext == ".py":
            try:
                import ast as _ast
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                parts = []
                for node in _ast.iter_child_nodes(tree):
                    if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        if not node.name.startswith("_"):
                            args = ", ".join(a.arg for a in node.args.args if a.arg != "self")
                            parts.append(f"{node.name}({args})")
                    elif isinstance(node, _ast.ClassDef) and not node.name.startswith("_"):
                        parts.append(f"class {node.name}")
                return "; ".join(parts)[:max_chars] if parts else str(path.name)
            except Exception:
                return str(path.name)

        # JS/TS: regex-based extraction of exports and top-level functions
        if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
            try:
                import re
                content = path.read_text(encoding="utf-8", errors="replace")
                parts = []
                # Exported functions: export function name(params)
                for m in re.finditer(r'(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', content):
                    parts.append(f"{m.group(1)}({m.group(2).strip()})")
                # Arrow exports: export const name = (params) =>
                for m in re.finditer(r'export\s+(?:const|let|var)\s+(\w+)\s*=', content):
                    if m.group(1) not in [p.split('(')[0] for p in parts]:
                        parts.append(f"export {m.group(1)}")
                # Express/Flask-style routes: app.get('/path', ...) or @app.route
                for m in re.finditer(r"app\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)", content):
                    parts.append(f"{m.group(1).upper()} {m.group(2)}")
                return "; ".join(parts)[:max_chars] if parts else str(path.name)
            except Exception:
                return str(path.name)

        return str(path.name)

    @staticmethod
    def _check_completeness(content: str, suffix: str) -> str:
        """Detect stubs, TODOs, and placeholder patterns that indicate incomplete code."""
        import re
        issues = []

        # TODO/FIXME/HACK markers
        todo_count = len(re.findall(r'\b(?:TODO|FIXME|HACK|XXX|STUB)\b', content, re.IGNORECASE))
        if todo_count:
            issues.append(f"{todo_count} TODO/FIXME markers")

        # Placeholder patterns: "pass", "...", "# implement", "throw new Error('not implemented')"
        lines = content.split('\n')
        stub_patterns = [
            r'^\s*pass\s*$',           # Python stubs
            r'^\s*\.\.\.\s*$',          # Python ellipsis stubs
            r'//\s*implement',          # JS comment stubs
            r'#\s*implement',           # Python comment stubs
            r"raise\s+NotImplementedError",
            r"throw\s+new\s+Error\s*\(\s*['\"]not\s+implemented",
        ]
        stub_count = 0
        for line in lines:
            for pat in stub_patterns:
                if re.search(pat, line, re.IGNORECASE):
                    stub_count += 1
                    break
        if stub_count > 0:
            issues.append(f"{stub_count} stub/placeholder lines")

        # Empty function bodies (JS/TS)
        if suffix in ('.js', '.ts', '.jsx', '.tsx'):
            # Matches: function name() {}, () => {}, (x) => {}
            empty_funcs = len(re.findall(r'(?:function\s+\w+\s*\([^)]*\)|=>\s*)\s*\{\s*\}', content))
            if empty_funcs:
                issues.append(f"{empty_funcs} empty function bodies")

        if issues:
            return f" (INCOMPLETE: {', '.join(issues)} — replace with real implementation)"
        return ""

    async def _tool_bash(self, args: dict) -> str:
        command = args["command"]
        cwd = args.get("cwd", str(self.project_root))
        cwd_path = Path(cwd).resolve()
        if not cwd_path.exists():
            cwd_path = self.project_root

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "FORGE_AGENT_ID": self.agent_id},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            return "Command timed out after 120 seconds"
        except Exception as exc:
            return f"Command failed: {exc}"

        output = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        result = output
        if err:
            result += f"\nSTDERR:\n{err}"
        if proc.returncode != 0:
            result += f"\nExit code: {proc.returncode}"

        # Adaptive truncation: 8K for 32K models, 30K for 128K+, 100K for 1M+
        ctx = self.model_config.context_window
        max_output = 8_000 if ctx <= 32_000 else (30_000 if ctx <= 200_000 else 100_000)
        if len(result) > max_output:
            result = result[:max_output] + f"\n\n... (truncated, {len(result)} chars total)"

        return result

    async def _tool_glob(self, args: dict) -> str:
        pattern = args["pattern"]
        base = self._resolve_path(args.get("path", str(self.project_root)))
        matches = sorted(str(p) for p in base.glob(pattern))
        if not matches:
            return f"No files matching '{pattern}' in {self._relative_path(base)}"
        # Convert to relative paths
        rel_matches = []
        for m in matches:
            try:
                rel_matches.append(str(Path(m).relative_to(self.project_root)))
            except ValueError:
                rel_matches.append(m)
        # Limit results
        if len(rel_matches) > 200:
            return "\n".join(rel_matches[:200]) + f"\n\n... ({len(rel_matches)} total, showing first 200)"
        return "\n".join(rel_matches)

    async def _tool_grep(self, args: dict) -> str:
        pattern = args["pattern"]
        path = self._resolve_path(args.get("path", str(self.project_root)))

        try:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
                "--include=*.json", "--include=*.yaml", "--include=*.yml",
                "--include=*.md", "--include=*.txt", "--include=*.html",
                "--include=*.css", "--include=*.sh",
                "-E", pattern, str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return "grep timed out after 30 seconds"
        except Exception as exc:
            return f"grep failed: {exc}"

        output = stdout.decode(errors="replace")
        if not output:
            return f"No matches for '{pattern}' in {self._relative_path(path)}"

        lines = output.strip().split("\n")

        # Convert absolute paths to relative in output
        project_prefix = str(self.project_root) + "/"
        rel_lines = [line.replace(project_prefix, "") for line in lines]

        # Build per-file line number index for summary
        file_lines: dict[str, list[str]] = {}
        for line in rel_lines:
            if ":" in line:
                parts = line.split(":")
                fname = parts[0]
                lineno = parts[1] if len(parts) > 1 else "?"
                file_lines.setdefault(fname, []).append(lineno)

        n_matches = len(rel_lines)
        n_files = len(file_lines)

        # Model-adaptive limit: 30 matches for 32K models, 50 for larger
        ctx = self.model_config.context_window
        show_limit = 30 if ctx <= 32_000 else 50

        # Summary-first format for 10+ matches
        if n_matches >= 10:
            file_summaries = ", ".join(
                f"{fname}:{','.join(lnos[:5])}" + ("…" if len(lnos) > 5 else "")
                for fname, lnos in list(file_lines.items())[:8]
            )
            if n_files > 8:
                file_summaries += f" … (+{n_files - 8} more files)"
            summary = f"{n_matches} matches in {n_files} files: {file_summaries} — showing first {min(n_matches, show_limit)} lines"
            return summary + "\n" + "\n".join(rel_lines[:show_limit]) + (
                f"\n\n... ({n_matches} total, showing first {show_limit})" if n_matches > show_limit else ""
            )

        return f"Found {n_matches} matches across {n_files} files\n" + "\n".join(rel_lines)

    async def _tool_list_directory(self, args: dict) -> str:
        """List directory contents with types, sizes, and item counts."""
        dir_path = self._resolve_path(args.get("path", str(self.project_root)))
        if not dir_path.exists():
            return f"Directory not found: {self._relative_path(dir_path)}"
        if not dir_path.is_dir():
            return f"Not a directory: {self._relative_path(dir_path)}"

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return f"Permission denied: {self._relative_path(dir_path)}"

        lines = [f"{self._relative_path(dir_path)}/"]
        for entry in entries:
            try:
                if entry.is_dir():
                    try:
                        item_count = sum(1 for _ in entry.iterdir())
                        lines.append(f"  [dir]  {entry.name}/  ({item_count} items)")
                    except PermissionError:
                        lines.append(f"  [dir]  {entry.name}/")
                else:
                    stat = entry.stat()
                    size = stat.st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1_048_576:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / 1_048_576:.1f} MB"
                    lines.append(f"  [file] {entry.name}  ({size_str})")
            except Exception:
                lines.append(f"  {entry.name}")

        lines.append(f"\n{len(entries)} items total")
        return "\n".join(lines)

    async def _tool_search_replace_all(self, args: dict, artifacts: dict) -> str:
        """Replace all occurrences of a string in a file."""
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        if not path.exists():
            return f"File not found: {self._relative_path(path)}"

        content = path.read_text(encoding="utf-8", errors="replace")
        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)
        if count == 0:
            return f"String not found in {self._relative_path(path)}"

        content = content.replace(old, new)
        path.write_text(content, encoding="utf-8")
        artifacts[str(path)] = {"action": "search_replace_all", "replacements": count}
        rel = self._relative_path(path)
        verify = await self._auto_verify(path)
        return f"Replaced {count} occurrence(s) of '{old[:40]}' in {rel}{verify}"

    async def _tool_remember(self, args: dict) -> str:
        """Append a note to project memory file."""
        import datetime
        note = args["note"]
        category = args.get("category", "note")
        memory_dir = self.project_root / ".forge"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / "FORGE_MEMORY.md"

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## [{category.upper()}] {timestamp}\n{note}\n"

        with open(memory_file, "a", encoding="utf-8") as f:
            f.write(entry)

        return f"Remembered [{category}]: {note[:80]}{'...' if len(note) > 80 else ''}"

    # ── Communication tools ─────────────────────────────────────────────────

    def _tool_claim_file(self, args: dict) -> str:
        """Claim exclusive write access to a file."""
        path = args["path"]
        if self.build_context is None:
            return f"Claimed: {path} (no build context — standalone mode)"
        if self.build_context.claim_file(path, self.agent_id):
            if self.on_event:
                self.on_event(AgentEvent(kind="file_claimed", file_path=path))
            return f"Claimed: {path}"
        else:
            existing = self.build_context.is_claimed(path)
            owner = existing.agent_id if existing else "unknown"
            if self.on_event:
                self.on_event(AgentEvent(kind="file_conflict", file_path=path, error=f"owned by {owner}"))
            return f"CONFLICT: {path} already claimed by {owner}"

    def _tool_check_context(self, args: dict) -> str:
        """Read shared build state."""
        if self.build_context is None:
            return "No build context available — standalone mode."
        context = self.build_context.to_context(self.agent_id, budget_chars=3000)
        if not context:
            return "No coordination data yet — you are the first agent running."
        focus = args.get("focus", "")
        if focus:
            # Filter lines by focus keyword
            lines = context.split("\n")
            filtered = [l for l in lines if focus.lower() in l.lower() or l.startswith("#")]
            if filtered:
                return "\n".join(filtered)
        return context

    # ── Auto-verify after writes ─────────────────────────────────────────────

    async def _auto_verify(self, path: Path) -> str:
        """Auto-verify syntax after write/edit. Returns verification status string.

        Checks: Python (py_compile), JSON, YAML, JavaScript (node --check),
        HTML (tag balance), CSS (brace balance).
        """
        ext = path.suffix.lower()

        # Shell-based checks
        checks = {
            '.py': f"python3 -c \"import py_compile; py_compile.compile('{path}', doraise=True)\"",
            '.json': f"python3 -c \"import json; json.load(open('{path}'))\"",
            '.yaml': f"python3 -c \"import yaml; yaml.safe_load(open('{path}'))\"",
            '.yml': f"python3 -c \"import yaml; yaml.safe_load(open('{path}'))\"",
            '.js': f"node --check '{path}'",
            '.mjs': f"node --check '{path}'",
        }
        cmd = checks.get(ext)
        if cmd:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    return " (syntax OK)"
                else:
                    err = stderr.decode(errors='replace').strip().split('\n')[-1][:200]
                    return f" (SYNTAX ERROR: {err})"
            except Exception:
                return ""

        # In-process checks for HTML/CSS
        if ext in ('.html', '.htm'):
            return self._verify_html(path)
        elif ext == '.css':
            return self._verify_css(path)

        return ""  # No check available for this type

    @staticmethod
    def _verify_html(path: Path) -> str:
        """Basic HTML verification: checks tag balance and required structure."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return " (WARNING: empty file)"
            # Check for unclosed script/style tags (common LLM mistake)
            import re
            for tag in ("script", "style"):
                opens = len(re.findall(rf'<{tag}[\s>]', content, re.IGNORECASE))
                closes = len(re.findall(rf'</{tag}>', content, re.IGNORECASE))
                if opens > closes:
                    return f" (HTML ERROR: unclosed <{tag}> tag — {opens} opened, {closes} closed)"
            # Check basic HTML structure
            lower = content.lower()
            if '<html' in lower and '</html>' not in lower:
                return " (HTML ERROR: missing </html> closing tag)"
            if '<body' in lower and '</body>' not in lower:
                return " (HTML ERROR: missing </body> closing tag)"
            return " (HTML OK)"
        except Exception:
            return ""

    @staticmethod
    def _verify_css(path: Path) -> str:
        """Basic CSS verification: checks brace balance."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return " (WARNING: empty file)"
            opens = content.count('{')
            closes = content.count('}')
            if opens != closes:
                return f" (CSS ERROR: unbalanced braces — {opens} open, {closes} close)"
            return " (CSS OK)"
        except Exception:
            return ""

    # ── Path helpers ─────────────────────────────────────────────────────────

    def _resolve_path(self, path: str | Path) -> Path:
        """Resolve a path relative to project_root if not absolute."""
        p = Path(path)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def _relative_path(self, path: Path) -> str:
        """Return path relative to project root for compact output."""
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    # ── Streaming callback ────────────────────────────────────────────────────

    def _on_stream_delta(self, delta: StreamDelta) -> None:
        """Forward a StreamDelta to the on_event callback (if registered)."""
        if self.on_event is not None:
            try:
                self.on_event(AgentEvent(kind="stream_delta", delta=delta))
            except Exception:
                pass  # Never let UI callbacks crash the agent loop

    # ── Context compaction (AD-6) ────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total_chars = sum(
            len(json.dumps(m)) for m in messages
        )
        return total_chars // 4

    def _compact_messages(self, messages: list[dict], budget: dict | None = None) -> list[dict]:
        """Compact older messages to fit within context window.

        Strategy:
        - 32K models: keep last 3 complete tool-use/tool-result pairs, compress older to 40-char summaries
        - 128K+ models: keep last 10 pairs, compress older
        - Drop read_file content from compacted turns (agent can re-read)
        - Preserve complete tool_use/tool_result pairs (never split mid-pair)
        """
        if len(messages) <= 7:
            return messages  # Nothing to compact

        ctx = self.model_config.context_window
        # Number of recent messages to keep verbatim based on context size
        if ctx <= 32_000:
            keep_recent_pairs = 3
        elif ctx <= 200_000:
            keep_recent_pairs = 10
        else:
            keep_recent_pairs = 20

        keep_last = keep_recent_pairs * 2 + 1  # pairs of (assistant, user/tool) + 1

        keep_first = 1  # System/prompt
        if len(messages) <= keep_first + keep_last:
            return messages

        head = messages[:keep_first]
        middle = messages[keep_first:-keep_last]
        tail = messages[-keep_last:]

        # Ensure tail starts at a safe cut point (after a tool result, not mid-pair)
        # Walk backward from middle/tail boundary to find a safe split
        for i in range(len(middle) - 1, -1, -1):
            msg = middle[i]
            content = msg.get("content", "")
            # Safe to cut after a plain assistant text message (no tool_calls)
            if msg.get("role") == "assistant":
                if isinstance(content, str):
                    break  # plain text assistant message — safe cut point
                if isinstance(content, list):
                    has_tool_use = any(
                        isinstance(b, dict) and ("toolUse" in b or b.get("type") == "tool_use")
                        for b in content
                    )
                    if not has_tool_use:
                        break  # no tool use in this assistant turn — safe

        # Compress middle section — preserve file paths for write/edit/append
        files_acted = []
        summary_parts = []
        for msg in middle:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_bits = []
                for block in content:
                    if isinstance(block, dict):
                        if "text" in block:
                            # Drop read_file content — model can re-read
                            text = block["text"]
                            if len(text) > 40:
                                text = text[:40]
                            text_bits.append(text)
                        elif "toolResult" in block:
                            text_bits.append("[tool result]")
                        elif "toolUse" in block:
                            name = block["toolUse"].get("name", "?")
                            inp = block["toolUse"].get("input", {})
                            if name == "read_file":
                                text_bits.append("[read — dropped]")
                            elif name in ("write_file", "append_file", "edit_file"):
                                fpath = inp.get("path", "?")
                                text_bits.append(f"[{name}:{fpath}]")
                                files_acted.append(fpath)
                            else:
                                text_bits.append(f"[tool:{name}]")
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "?")
                            inp = block.get("input", {})
                            if name == "read_file":
                                text_bits.append("[read — dropped]")
                            elif name in ("write_file", "append_file", "edit_file"):
                                fpath = inp.get("path", "?")
                                text_bits.append(f"[{name}:{fpath}]")
                                files_acted.append(fpath)
                            else:
                                text_bits.append(f"[tool:{name}]")
                        elif block.get("type") == "tool_result":
                            text_bits.append("[tool result]")
                content = " | ".join(text_bits)
            if isinstance(content, str) and len(content) > 40:
                content = content[:40] + "…"
            summary_parts.append(f"{role}: {content}")

        # Prepend files-created summary so agent remembers what it already wrote
        summary_prefix = ""
        if files_acted:
            unique_files = list(dict.fromkeys(files_acted))
            summary_prefix = f"Files written so far: {', '.join(unique_files)}\n"

        summary = (
            "[Context compacted — older turns summarized]\n"
            + summary_prefix
            + "\n".join(summary_parts)
        )

        # Insert as a user message so all providers accept it
        compacted = head + [{"role": "user", "content": summary}]
        # Need an assistant ack for providers that require alternating turns
        compacted.append({"role": "assistant", "content": "Understood, continuing with the compacted context."})

        # Fix Bedrock toolResult pairing: if tail starts with a user message
        # containing toolResult blocks, those are orphaned (no matching toolUse
        # in the preceding synthetic assistant message). Skip such messages
        # to prevent "number of toolResult blocks exceeds toolUse blocks" error.
        tail_start = 0
        while tail_start < len(tail):
            msg = tail[tail_start]
            if msg.get("role") != "user":
                break
            content = msg.get("content", "")
            if isinstance(content, list):
                has_tool_result = any(
                    isinstance(b, dict) and (
                        "toolResult" in b or b.get("type") == "tool_result"
                    )
                    for b in content
                )
                if has_tool_result:
                    tail_start += 1
                    continue
            break
        compacted.extend(tail[tail_start:])

        return compacted
