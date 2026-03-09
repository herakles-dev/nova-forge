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
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import ModelConfig, get_model_config, get_provider
from forge_guards import PathSandbox, RiskClassifier, RiskLevel, SandboxViolation
from forge_hooks import HookSystem, HookResult
from model_router import ModelRouter, ModelResponse, ToolCall

logger = logging.getLogger(__name__)

# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Outcome of a ForgeAgent.run() invocation."""
    output: str = ""
    turns: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    tool_calls_made: int = 0
    error: str | None = None


# ── Tool definitions (common format for all providers) ───────────────────────

BUILT_IN_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating it if necessary.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_string must appear exactly once.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique)"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash",
        "description": "Execute a shell command and return stdout+stderr.",
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
        "description": "Find files matching a glob pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
                "path": {"type": "string", "description": "Base directory (default: project root)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents with a regex pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search"},
            },
            "required": ["pattern"],
        },
    },
]


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

        for turn in range(self.max_turns):
            # Call the model
            try:
                response = await self.router.send(messages, self.tools, self.model_config)
            except Exception as exc:
                logger.error("Model call failed on turn %d: %s", turn, exc)
                return AgentResult(
                    output=f"Model error: {exc}",
                    turns=turn + 1,
                    artifacts=artifacts,
                    tool_calls_made=total_tool_calls,
                    error=str(exc),
                )

            tool_calls = self.router.extract_tool_calls(response)

            # No tool calls → agent is done
            if not tool_calls:
                return AgentResult(
                    output=response.text,
                    turns=turn + 1,
                    artifacts=artifacts,
                    tool_calls_made=total_tool_calls,
                )

            # Append assistant message to history
            adapter = self.router.route(self.model_config.model_id)
            messages.append(adapter.format_assistant_message(response))

            # Execute each tool call
            for call in tool_calls:
                total_tool_calls += 1
                result_str = await self._execute_tool_call(call, artifacts)
                messages.append(
                    adapter.format_tool_result(call.id, result_str)
                )

            # Context compaction at 80% of model's context window
            estimated_tokens = self._estimate_tokens(messages)
            if estimated_tokens > self.model_config.context_window * 0.8:
                messages = self._compact_messages(messages)
                logger.info(
                    "Compacted context: %d tokens → %d tokens",
                    estimated_tokens,
                    self._estimate_tokens(messages),
                )

        return AgentResult(
            output="Max turns reached",
            turns=self.max_turns,
            artifacts=artifacts,
            tool_calls_made=total_tool_calls,
            error="max_turns_exceeded",
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

        # Risk check
        command = args.get("command", "")
        file_path = args.get("path", "")
        risk = self.risk_classifier.classify(call.name, command, file_path)
        if risk == RiskLevel.HIGH:
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
        elif name == "edit_file":
            return await self._tool_edit_file(args, artifacts)
        elif name == "bash":
            return await self._tool_bash(args)
        elif name == "glob_files":
            return await self._tool_glob(args)
        elif name == "grep":
            return await self._tool_grep(args)
        else:
            return f"Unknown tool: {name}"

    # ── Tool implementations ─────────────────────────────────────────────────

    async def _tool_read_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        self.sandbox.validate_read(path)
        if not path.exists():
            return f"File not found: {path}"
        content = path.read_text(encoding="utf-8", errors="replace")
        # Truncate very large files
        if len(content) > 100_000:
            content = content[:100_000] + f"\n\n... (truncated, {len(content)} chars total)"
        return content

    async def _tool_write_file(self, args: dict, artifacts: dict) -> str:
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        artifacts[str(path)] = {"action": "write", "size": len(args["content"])}
        return f"File written: {path} ({len(args['content'])} chars)"

    async def _tool_edit_file(self, args: dict, artifacts: dict) -> str:
        path = self._resolve_path(args["path"])
        self.sandbox.validate_write(path)
        if not path.exists():
            return f"File not found: {path}"
        content = path.read_text(encoding="utf-8", errors="replace")
        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)
        if count == 0:
            return f"old_string not found in {path}"
        if count > 1:
            return f"old_string appears {count} times in {path} — must be unique (1 occurrence)"
        content = content.replace(old, new, 1)
        path.write_text(content, encoding="utf-8")
        artifacts[str(path)] = {"action": "edit", "old_len": len(old), "new_len": len(new)}
        return f"File edited: {path} (replaced {len(old)} chars with {len(new)} chars)"

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

        # Truncate very long output
        if len(result) > 50_000:
            result = result[:50_000] + f"\n\n... (truncated, {len(result)} chars total)"

        return result

    async def _tool_glob(self, args: dict) -> str:
        pattern = args["pattern"]
        base = self._resolve_path(args.get("path", str(self.project_root)))
        matches = sorted(str(p) for p in base.glob(pattern))
        if not matches:
            return f"No files matching '{pattern}' in {base}"
        # Limit results
        if len(matches) > 200:
            return "\n".join(matches[:200]) + f"\n\n... ({len(matches)} total, showing first 200)"
        return "\n".join(matches)

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
            return f"No matches for '{pattern}' in {path}"
        lines = output.strip().split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n\n... ({len(lines)} matches, showing first 100)"
        return output.strip()

    # ── Path resolution ──────────────────────────────────────────────────────

    def _resolve_path(self, path: str | Path) -> Path:
        """Resolve a path relative to project_root if not absolute."""
        p = Path(path)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    # ── Context compaction (AD-6) ────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total_chars = sum(
            len(json.dumps(m)) for m in messages
        )
        return total_chars // 4

    def _compact_messages(self, messages: list[dict]) -> list[dict]:
        """Compact older messages to fit within context window.

        Strategy: preserve first message (system/prompt) + last 5 turns verbatim.
        Summarize everything in between into a single compressed context block.
        """
        if len(messages) <= 7:
            return messages  # Nothing to compact

        keep_first = 1  # System/prompt
        keep_last = 5   # Recent turns

        head = messages[:keep_first]
        middle = messages[keep_first:-keep_last]
        tail = messages[-keep_last:]

        # Summarize middle section
        summary_parts = []
        for msg in middle:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Provider-specific content blocks
                text_bits = []
                for block in content:
                    if isinstance(block, dict):
                        if "text" in block:
                            text_bits.append(block["text"][:100])
                        elif "toolResult" in block:
                            text_bits.append("[tool result]")
                        elif "toolUse" in block:
                            text_bits.append(f"[tool: {block['toolUse'].get('name', '?')}]")
                content = " | ".join(text_bits)
            if isinstance(content, str) and len(content) > 200:
                content = content[:200] + "..."
            summary_parts.append(f"{role}: {content}")

        summary = (
            "[Context compacted — older turns summarized]\n"
            + "\n".join(summary_parts)
        )

        # Insert as a user message so all providers accept it
        compacted = head + [{"role": "user", "content": summary}]
        # Need an assistant ack for providers that require alternating turns
        compacted.append({"role": "assistant", "content": "Understood, continuing with the compacted context."})
        compacted.extend(tail)

        return compacted
