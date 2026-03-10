"""Nova Forge ModelRouter — direct SDK adapters for Bedrock, OpenAI-compatible, and Anthropic."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from config import ModelConfig, get_provider


# ── Token estimation helper ───────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count. Uses char/4 heuristic."""
    return max(1, len(text) // 4)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ModelResponse:
    """Normalised response returned by every adapter."""
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict[str, int]  # keys: input_tokens, output_tokens


@dataclass
class StreamDelta:
    """A chunk of streaming response.

    kind values:
      "text"       — partial text content (see .text)
      "tool_start" — a new tool call begins (see .tool_name, .tool_id)
      "tool_delta" — partial JSON for tool args (see .tool_args_chunk)
      "tool_end"   — the current tool call's args are complete
      "done"       — stream is finished
    """
    kind: str
    text: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_args_chunk: str = ""


# ── Abstract base ─────────────────────────────────────────────────────────────

class ProviderAdapter(ABC):
    """Common interface for all provider adapters."""

    @abstractmethod
    async def send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> ModelResponse:
        """Send a conversation turn and return a normalised response.

        Args:
            messages: Conversation history in provider-agnostic format.
            tools: List of tool definitions in common format
                   ``{"name": str, "description": str, "parameters": dict}``.
            model_config: Model selection and inference parameters.
        """

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> AsyncGenerator[StreamDelta, None]:
        """Stream a conversation turn, yielding StreamDelta objects.

        Implementations must be async generators (use ``yield``).
        Each delta represents a partial chunk of the model response.
        """

    @abstractmethod
    def format_tool_result(self, call_id: str, result_str: str) -> dict:
        """Return a provider-specific message dict for a tool result."""

    @abstractmethod
    def format_assistant_message(self, response: ModelResponse) -> dict:
        """Return a provider-specific message dict for the assistant turn."""


# ── Bedrock adapter ───────────────────────────────────────────────────────────

class BedrockAdapter(ProviderAdapter):
    """Adapter for Amazon Bedrock Converse API (Nova, Titan, …)."""

    def __init__(self) -> None:
        import boto3  # type: ignore[import]

        region = os.environ.get("AWS_REGION", "us-east-1")
        self._client = boto3.client("bedrock-runtime", region_name=region)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "inputSchema": {"json": t.get("parameters", {})},
                }
            }
            for t in tools
        ]

    @staticmethod
    def _bare_model_id(model_id: str) -> str:
        """Strip 'bedrock/' prefix."""
        return model_id.removeprefix("bedrock/")

    def _call_converse(self, **kwargs: Any) -> dict:
        """Synchronous wrapper called inside asyncio.to_thread."""
        return self._client.converse(**kwargs)

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """Convert generic messages to Bedrock converse format.

        Bedrock requires:
        - No "system" role — extract as separate system list
        - content must be list of content blocks, not a string
        - Roles must alternate user/assistant
        Returns (system_blocks, normalized_messages).
        """
        system_blocks: list[dict] = []
        normalized: list[dict] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # Extract system messages for the system parameter
                if isinstance(content, str):
                    system_blocks.append({"text": content})
                continue

            # Normalize content to list of blocks
            if isinstance(content, str):
                content = [{"text": content}]
            elif isinstance(content, list):
                # Already in block format — pass through
                pass
            else:
                content = [{"text": str(content)}]

            # Bedrock only accepts "user" and "assistant" roles
            if role == "tool":
                role = "user"
                # Convert OpenAI tool result format to Bedrock
                tool_call_id = msg.get("tool_call_id", "")
                if tool_call_id:
                    content = [{
                        "toolResult": {
                            "toolUseId": tool_call_id,
                            "content": [{"text": msg.get("content", "")}]
                        }
                    }]

            # Merge consecutive same-role messages (Bedrock requires alternation)
            if normalized and normalized[-1]["role"] == role:
                # Deduplicate toolResult blocks by toolUseId
                existing_ids = {
                    b.get("toolResult", {}).get("toolUseId")
                    for b in normalized[-1]["content"]
                    if "toolResult" in b
                }
                for block in content:
                    tid = block.get("toolResult", {}).get("toolUseId")
                    if tid and tid in existing_ids:
                        continue  # skip duplicate
                    normalized[-1]["content"].append(block)
                    if tid:
                        existing_ids.add(tid)
            else:
                normalized.append({"role": role, "content": content})

        return system_blocks, normalized

    async def send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> ModelResponse:
        system_blocks, norm_messages = self._normalize_messages(messages)

        kwargs: dict[str, Any] = {
            "modelId": self._bare_model_id(model_config.model_id),
            "messages": norm_messages,
            "inferenceConfig": {
                "maxTokens": model_config.max_tokens,
                "temperature": model_config.temperature,
            },
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if tools:
            kwargs["toolConfig"] = {"tools": self._convert_tools(tools)}

        raw = await asyncio.to_thread(self._call_converse, **kwargs)

        content_blocks: list[dict] = raw["output"]["message"].get("content", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        id=tu["toolUseId"],
                        name=tu["name"],
                        args=tu.get("input", {}),
                    )
                )

        usage_raw = raw.get("usage", {})
        return ModelResponse(
            text=" ".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=raw.get("stopReason", ""),
            usage={
                "input_tokens": usage_raw.get("inputTokens", 0),
                "output_tokens": usage_raw.get("outputTokens", 0),
            },
        )

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> AsyncGenerator[StreamDelta, None]:
        """Stream responses from the Bedrock converse_stream API.

        Bedrock streaming events:
          contentBlockStart  — new content block (text or toolUse)
          contentBlockDelta  — delta for current block
          contentBlockStop   — block complete
          messageStop        — message complete
        """
        system_blocks, norm_messages = self._normalize_messages(messages)

        kwargs: dict[str, Any] = {
            "modelId": self._bare_model_id(model_config.model_id),
            "messages": norm_messages,
            "inferenceConfig": {
                "maxTokens": model_config.max_tokens,
                "temperature": model_config.temperature,
            },
        }
        if system_blocks:
            kwargs["system"] = system_blocks
        if tools:
            kwargs["toolConfig"] = {"tools": self._convert_tools(tools)}

        def _call_converse_stream(**kw: Any):
            return self._client.converse_stream(**kw)

        raw = await asyncio.to_thread(_call_converse_stream, **kwargs)
        event_stream = raw.get("stream", [])

        for event in event_stream:
            if "contentBlockStart" in event:
                start_payload = event["contentBlockStart"].get("start", {})
                if "toolUse" in start_payload:
                    tu = start_payload["toolUse"]
                    yield StreamDelta(
                        kind="tool_start",
                        tool_name=tu.get("name", ""),
                        tool_id=tu.get("toolUseId", ""),
                    )
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    yield StreamDelta(kind="text", text=delta["text"])
                elif "toolUse" in delta:
                    yield StreamDelta(
                        kind="tool_delta",
                        tool_args_chunk=delta["toolUse"].get("input", ""),
                    )
            elif "contentBlockStop" in event:
                yield StreamDelta(kind="tool_end")
            elif "messageStop" in event:
                yield StreamDelta(kind="done")

    def format_tool_result(self, call_id: str, result_str: str) -> dict:
        return _bedrock_tool_result(call_id, result_str)

    def format_assistant_message(self, response: ModelResponse) -> dict:
        content: list[dict] = []
        if response.text:
            content.append({"text": response.text})
        for tc in response.tool_calls:
            content.append(
                {
                    "toolUse": {
                        "toolUseId": tc.id,
                        "name": tc.name,
                        "input": tc.args,
                    }
                }
            )
        return {"role": "assistant", "content": content}


# ── OpenAI-compatible adapter ─────────────────────────────────────────────────

class OpenAIAdapter(ProviderAdapter):
    """Adapter for OpenAI, OpenRouter, and Ollama (all share the OpenAI API surface)."""

    def __init__(self, model_id: str) -> None:
        import openai  # type: ignore[import]

        if model_id.startswith("openrouter/"):
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise EnvironmentError(
                    "OPENROUTER_API_KEY is not set — required for OpenRouter models."
                )
            self._client = openai.AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
        elif model_id.startswith("ollama/"):
            self._client = openai.AsyncOpenAI(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
            )
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise EnvironmentError(
                    "OPENAI_API_KEY is not set — required for OpenAI models."
                )
            self._client = openai.AsyncOpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_prefix(model_id: str) -> str:
        """Remove provider prefix (e.g. 'openrouter/', 'ollama/')."""
        if "/" in model_id:
            _, remainder = model_id.split("/", 1)
            return remainder
        return model_id

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {}),
                },
            }
            for t in tools
        ]

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    async def send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self._strip_prefix(model_config.model_id),
            "messages": messages,
            "max_tokens": model_config.max_tokens,
            "temperature": model_config.temperature,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        completion = await self._client.chat.completions.create(**kwargs)

        choice = completion.choices[0]
        message = choice.message
        text = message.content or ""

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        args=json.loads(tc.function.arguments or "{}"),
                    )
                )

        usage = completion.usage
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            usage={
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            },
        )

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> AsyncGenerator[StreamDelta, None]:
        """Stream responses from an OpenAI-compatible API."""
        kwargs: dict[str, Any] = {
            "model": self._strip_prefix(model_config.model_id),
            "messages": messages,
            "max_tokens": model_config.max_tokens,
            "temperature": model_config.temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**kwargs)

        # Track in-progress tool calls by index (OpenAI streaming delivers them
        # across multiple chunks, each with a delta.tool_calls list entry).
        # We need to detect when a new tool call starts vs. continues.
        last_tool_index: int | None = None

        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            if delta.content:
                yield StreamDelta(kind="text", text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    # tc.index tells us which tool call position this delta belongs to
                    current_index = getattr(tc, "index", None)

                    # New tool call begins when the index changes
                    if current_index != last_tool_index:
                        if last_tool_index is not None:
                            # Close the previous tool call
                            yield StreamDelta(kind="tool_end")
                        last_tool_index = current_index
                        yield StreamDelta(
                            kind="tool_start",
                            tool_name=tc.function.name if tc.function else "",
                            tool_id=tc.id or "",
                        )

                    # Partial arguments chunk
                    if tc.function and tc.function.arguments:
                        yield StreamDelta(
                            kind="tool_delta",
                            tool_args_chunk=tc.function.arguments,
                        )

        # Close any open tool call
        if last_tool_index is not None:
            yield StreamDelta(kind="tool_end")

        yield StreamDelta(kind="done")

    def format_tool_result(self, call_id: str, result_str: str) -> dict:
        return _openai_tool_result(call_id, result_str)

    def format_assistant_message(self, response: ModelResponse) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.args),
                    },
                }
                for tc in response.tool_calls
            ]
        return msg


# ── Anthropic adapter ─────────────────────────────────────────────────────────

class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic Claude models."""

    def __init__(self) -> None:
        import anthropic  # type: ignore[import]

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set — required for Anthropic models."
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_prefix(model_id: str) -> str:
        return model_id.removeprefix("anthropic/")

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {}),
            }
            for t in tools
        ]

    # ------------------------------------------------------------------
    # ProviderAdapter interface
    # ------------------------------------------------------------------

    async def send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self._strip_prefix(model_config.model_id),
            "messages": messages,
            "max_tokens": model_config.max_tokens,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        if model_config.temperature is not None:
            kwargs["temperature"] = model_config.temperature

        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        args=block.input or {},
                    )
                )

        return ModelResponse(
            text=" ".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> AsyncGenerator[StreamDelta, None]:
        """Stream responses from the Anthropic messages API."""
        kwargs: dict[str, Any] = {
            "model": self._strip_prefix(model_config.model_id),
            "messages": messages,
            "max_tokens": model_config.max_tokens,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        if model_config.temperature is not None:
            kwargs["temperature"] = model_config.temperature

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block and getattr(block, "type", None) == "tool_use":
                        yield StreamDelta(
                            kind="tool_start",
                            tool_name=block.name,
                            tool_id=block.id,
                        )

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        if hasattr(delta, "text"):
                            yield StreamDelta(kind="text", text=delta.text)
                        elif hasattr(delta, "partial_json"):
                            yield StreamDelta(
                                kind="tool_delta",
                                tool_args_chunk=delta.partial_json,
                            )

                elif event_type == "content_block_stop":
                    yield StreamDelta(kind="tool_end")

                elif event_type == "message_stop":
                    yield StreamDelta(kind="done")

    def format_tool_result(self, call_id: str, result_str: str) -> dict:
        return _anthropic_tool_result(call_id, result_str)

    def format_assistant_message(self, response: ModelResponse) -> dict:
        content: list[dict] = []
        if response.text:
            content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.args,
                }
            )
        return {"role": "assistant", "content": content}


# ── Retry helper ──────────────────────────────────────────────────────────────

_TRANSIENT_PATTERNS = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "timeout",
    "timed out",
    "service unavailable",
    "internal server error",
    "server error",
    "throttl",
)


def _is_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in _TRANSIENT_PATTERNS)


async def _with_retry(coro_fn, *args, **kwargs) -> ModelResponse:
    """Call ``coro_fn(*args, **kwargs)`` with exponential back-off (3 attempts)."""
    delays = [1, 2, 4]
    last_exc: Exception | None = None

    for attempt, delay in enumerate(delays, start=1):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as exc:
            if _is_transient(exc) and attempt < len(delays):
                await asyncio.sleep(delay)
                last_exc = exc
                continue
            raise

    raise RuntimeError("All retry attempts exhausted") from last_exc


# ── Provider-agnostic tool-result formatters (no SDK imports needed) ──────────

def _bedrock_tool_result(call_id: str, result_str: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": call_id,
                    "content": [{"text": result_str}],
                }
            }
        ],
    }


def _openai_tool_result(call_id: str, result_str: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result_str,
    }


def _anthropic_tool_result(call_id: str, result_str: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": result_str,
            }
        ],
    }


# ── ModelRouter ───────────────────────────────────────────────────────────────

class ModelRouter:
    """Routes requests to the correct provider adapter based on the model ID.

    Adapters are created on demand — no persistent state required.
    """

    def route(self, model_id: str) -> ProviderAdapter:
        """Return the appropriate adapter for *model_id*.

        Provider is determined by prefix:
        - ``bedrock/``   → BedrockAdapter
        - ``anthropic/`` → AnthropicAdapter
        - anything else  → OpenAIAdapter (OpenAI, openrouter/, ollama/)
        """
        provider = get_provider(model_id)
        if provider == "bedrock":
            return BedrockAdapter()
        elif provider == "anthropic":
            return AnthropicAdapter()
        else:
            return OpenAIAdapter(model_id)

    async def send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> ModelResponse:
        """Convenience wrapper: route, then send with retry."""
        adapter = self.route(model_config.model_id)
        return await _with_retry(adapter.send, messages, tools, model_config)

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
    ) -> AsyncGenerator[StreamDelta, None]:
        """Stream a response. Yields StreamDelta objects.

        Delegates to the appropriate adapter's stream() method.
        """
        adapter = self.route(model_config.model_id)
        async for delta in adapter.stream(messages, tools, model_config):
            yield delta

    async def stream_send(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
        on_delta: Callable[[StreamDelta], None] | None = None,
    ) -> ModelResponse:
        """Stream a response while accumulating a final ModelResponse.

        Calls *on_delta* for each StreamDelta (useful for real-time UI updates)
        and returns a complete ModelResponse when the stream is exhausted.

        Falls back silently to the blocking send() if the adapter's stream()
        raises any exception.
        """
        try:
            return await self._stream_accumulate(messages, tools, model_config, on_delta)
        except Exception:
            # Fallback: use the non-streaming path
            return await self.send(messages, tools, model_config)

    async def _stream_accumulate(
        self,
        messages: list[dict],
        tools: list[dict],
        model_config: ModelConfig,
        on_delta: Callable[[StreamDelta], None] | None,
    ) -> ModelResponse:
        """Internal: stream and accumulate into a ModelResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool: dict[str, str] | None = None
        current_args: list[str] = []

        async for delta in self.stream(messages, tools, model_config):
            if on_delta is not None:
                try:
                    on_delta(delta)
                except Exception:
                    pass  # Never let UI callback crash the agent loop

            if delta.kind == "text":
                text_parts.append(delta.text)
            elif delta.kind == "tool_start":
                current_tool = {"name": delta.tool_name, "id": delta.tool_id}
                current_args = []
            elif delta.kind == "tool_delta":
                current_args.append(delta.tool_args_chunk)
            elif delta.kind == "tool_end":
                if current_tool is not None:
                    args_str = "".join(current_args)
                    try:
                        args: dict[str, Any] = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {"_raw": args_str}
                    tool_calls.append(
                        ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            args=args,
                        )
                    )
                    current_tool = None
                    current_args = []
            # "done" — nothing to do; loop will end naturally

        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason="end_turn",
            # Streaming APIs typically don't return token counts in the stream
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    def extract_tool_calls(self, response: ModelResponse) -> list[ToolCall]:
        """Return the tool calls embedded in *response*."""
        return response.tool_calls

    def format_tool_result(
        self,
        provider: str,
        call_id: str,
        result: str,
    ) -> dict:
        """Format a tool result for the given provider.

        Args:
            provider: One of ``"bedrock"``, ``"anthropic"``, or ``"openai"``.
            call_id: The tool call ID from the original ToolCall.
            result: The tool output as a string.
        """
        if provider == "bedrock":
            return _bedrock_tool_result(call_id, result)
        elif provider == "anthropic":
            return _anthropic_tool_result(call_id, result)
        else:
            return _openai_tool_result(call_id, result)
