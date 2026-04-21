"""LLM client abstraction: supports Anthropic and OpenAI-compatible providers."""

from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    text_parts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(ABC):
    """Abstract interface for LLM API calls."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max(0, int(max_retries))

    @abstractmethod
    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool = False,
        stream_handler: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        ...

    def _call_with_retry(self, fn: Callable[[], LLMResponse]) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return fn()
            except Exception as exc:
                if attempt >= self.max_retries or not _is_retryable_error(exc):
                    raise
                delay = min(8.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.25)
                logger.warning(
                    "LLM request failed with retryable error (%s). Retrying in %.2fs [%d/%d].",
                    exc,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(delay)
                attempt += 1


def _is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status in {429, 500, 502, 503, 504}:
        return True

    body = f"{type(exc).__name__}: {exc}".lower()
    return any(
        needle in body
        for needle in ("rate limit", "timeout", "temporarily unavailable", "connection reset", "overloaded")
    )


class AnthropicLLMClient(LLMClient):
    """Client for the Anthropic Messages API."""

    def __init__(self, api_key: str, base_url: str | None = None, max_retries: int = 3):
        super().__init__(max_retries=max_retries)
        from anthropic import Anthropic

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool = False,
        stream_handler: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools or [],
            "temperature": temperature,
        }
        if not stream:
            return self._call_with_retry(
                lambda: self._parse_response(self.client.messages.create(**kwargs))
            )
        return self._call_with_retry(
            lambda: self._stream_response(kwargs, stream_handler=stream_handler)
        )

    def _stream_response(
        self,
        kwargs: dict[str, Any],
        stream_handler: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        with self.client.messages.stream(**kwargs) as stream:
            for chunk in stream.text_stream:
                if stream_handler and chunk:
                    stream_handler(chunk)
            final_message = stream.get_final_message()
        return self._parse_response(final_message)

    def _parse_response(self, response: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if isinstance(block.input, dict) else {},
                    }
                )

        usage: dict[str, int] = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            }

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage=usage,
        )


class OpenAILLMClient(LLMClient):
    """Client for any OpenAI-compatible API."""

    def __init__(self, api_key: str, base_url: str | None = None, max_retries: int = 3):
        super().__init__(max_retries=max_retries)
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "input_schema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return result

    @classmethod
    def _convert_messages(
        cls,
        system: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        oai_msgs: list[dict[str, Any]] = []
        if system:
            oai_msgs.append({"role": "system", "content": system})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, str):
                    oai_msgs.append({"role": "user", "content": content})
                    continue

                if not isinstance(content, list):
                    oai_msgs.append({"role": "user", "content": str(content)})
                    continue

                tool_results = [
                    block
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "tool_result"
                ]
                non_tool_blocks = [
                    block
                    for block in content
                    if isinstance(block, dict) and block.get("type") != "tool_result"
                ]

                for tool_result in tool_results:
                    tr_content = tool_result.get("content", "")
                    if isinstance(tr_content, list):
                        tr_content = cls._flatten_content_blocks(tr_content)
                    oai_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_result.get("tool_use_id", ""),
                            "content": tr_content,
                        }
                    )

                if non_tool_blocks:
                    converted_blocks: list[dict[str, Any]] = []
                    for block in non_tool_blocks:
                        btype = block.get("type")
                        if btype == "text":
                            converted_blocks.append(
                                {"type": "text", "text": block.get("text", "")}
                            )
                    if converted_blocks:
                        oai_msgs.append({"role": "user", "content": converted_blocks})

            elif role == "assistant":
                if isinstance(content, str):
                    oai_msgs.append({"role": "assistant", "content": content})
                    continue

                text_parts: list[str] = []
                tool_uses: list[dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_uses.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            }
                        )

                assistant_msg: dict[str, Any] = {"role": "assistant"}
                combined_text = "\n".join(text_parts) or None
                assistant_msg["content"] = combined_text
                if tool_uses:
                    assistant_msg["tool_calls"] = tool_uses
                oai_msgs.append(assistant_msg)

        return oai_msgs

    @staticmethod
    def _flatten_content_blocks(blocks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool = False,
        stream_handler: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        oai_messages = self._convert_messages(system, messages)
        oai_tools = self._convert_tools(tools) if tools else None
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        if not stream:
            return self._call_with_retry(
                lambda: self._parse_response(self.client.chat.completions.create(**kwargs))
            )
        return self._call_with_retry(
            lambda: self._stream_response(kwargs, stream_handler=stream_handler)
        )

    def _stream_response(
        self,
        kwargs: dict[str, Any],
        stream_handler: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        stream = self.client.chat.completions.create(stream=True, **kwargs)

        text_parts: list[str] = []
        tool_calls_accumulator: dict[int, dict[str, str]] = {}
        usage: dict[str, int] = {}
        finish_reason = ""

        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = {
                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0),
                }

            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            delta = choice.delta
            if getattr(delta, "content", None):
                text_parts.append(delta.content)
                if stream_handler:
                    stream_handler(delta.content)

            if getattr(delta, "tool_calls", None):
                for tool_call in delta.tool_calls:
                    index = getattr(tool_call, "index", 0)
                    state = tool_calls_accumulator.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if getattr(tool_call, "id", None):
                        state["id"] = tool_call.id
                    function = getattr(tool_call, "function", None)
                    if function and getattr(function, "name", None):
                        state["name"] = function.name
                    if function and getattr(function, "arguments", None):
                        state["arguments"] += function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        tool_calls: list[dict[str, Any]] = []
        for state in tool_calls_accumulator.values():
            try:
                args = json.loads(state["arguments"]) if state["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": state["id"], "name": state["name"], "input": args})

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            stop_reason="tool_use" if finish_reason == "tool_calls" else "end_turn",
            usage=usage,
        )

    def _parse_response(self, response: Any) -> LLMResponse:
        choice = response.choices[0] if response.choices else None
        if choice is None:
            return LLMResponse(stop_reason="end_turn")

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        if choice.message.content:
            text_parts.append(choice.message.content)

        if choice.message.tool_calls:
            for tool_call in choice.message.tool_calls:
                try:
                    args = (
                        json.loads(tool_call.function.arguments)
                        if tool_call.function.arguments
                        else {}
                    )
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": args,
                    }
                )

        usage: dict[str, int] = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", 0),
                "output_tokens": getattr(response.usage, "completion_tokens", 0),
            }

        finish_reason = choice.finish_reason or ""
        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            stop_reason="tool_use" if finish_reason == "tool_calls" else "end_turn",
            usage=usage,
        )


def create_llm_client(
    provider: str,
    api_key: str,
    base_url: str | None = None,
    max_retries: int = 3,
) -> LLMClient:
    """Factory: create the appropriate LLM client based on provider string."""

    provider = provider.lower().strip()
    if provider == "anthropic":
        return AnthropicLLMClient(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )
    if provider in ("openai", "openai-compatible"):
        return OpenAILLMClient(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: 'anthropic', 'openai'."
    )
