"""LLM client abstraction: supports Anthropic and OpenAI-compatible providers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
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

    @abstractmethod
    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        ...


class AnthropicLLMClient(LLMClient):
    """Client for the Anthropic Messages API."""

    def __init__(self, api_key: str, base_url: str | None = None):
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
    ) -> LLMResponse:
        from anthropic import APIError
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools or [],
                temperature=temperature,
            )
        except APIError:
            raise

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input if isinstance(block.input, dict) else {},
                })

        usage: dict[str, int] = {}
        if response.usage:
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
    """Client for any OpenAI-compatible API (OpenAI, DeepSeek, Qwen, vLLM, etc.).

    Translates between the internal Anthropic-style schemas and OpenAI's format.
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        from openai import OpenAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    # ---- Format conversion helpers ----

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic tool schema -> OpenAI function-calling schema."""
        result = []
        for t in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return result

    @staticmethod
    def _convert_messages(
        system: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Anthropic message list -> OpenAI message list.

        Key differences handled:
        - system prompt becomes a system message
        - tool_use content blocks become assistant messages with tool_calls
        - tool_result content blocks become tool role messages
        """
        oai_msgs: list[dict[str, Any]] = []

        if system:
            oai_msgs.append({"role": "system", "content": system})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, str):
                    oai_msgs.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                    text_parts = [b for b in content if isinstance(b, dict) and b.get("type") != "tool_result"]

                    if tool_results:
                        for tr in tool_results:
                            oai_msgs.append({
                                "role": "tool",
                                "tool_call_id": tr.get("tool_use_id", ""),
                                "content": tr.get("content", ""),
                            })
                    if text_parts:
                        texts = " ".join(
                            b.get("text", str(b)) if isinstance(b, dict) else str(b)
                            for b in text_parts
                        )
                        if texts.strip():
                            oai_msgs.append({"role": "user", "content": texts})

            elif role == "assistant":
                if isinstance(content, str):
                    oai_msgs.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts_str = []
                    tool_uses = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts_str.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            import json
                            tool_uses.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })

                    assistant_msg: dict[str, Any] = {"role": "assistant"}
                    combined_text = "\n".join(text_parts_str)
                    if tool_uses:
                        assistant_msg["content"] = combined_text or None
                        assistant_msg["tool_calls"] = tool_uses
                    else:
                        assistant_msg["content"] = combined_text
                    oai_msgs.append(assistant_msg)

        return oai_msgs

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        import json as _json

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

        response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0] if response.choices else None
        if choice is None:
            return LLMResponse(stop_reason="end_turn")

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        if choice.message.content:
            text_parts.append(choice.message.content)

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                except _json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        finish = choice.finish_reason or ""
        stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", 0),
                "output_tokens": getattr(response.usage, "completion_tokens", 0),
            }

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )


def create_llm_client(
    provider: str,
    api_key: str,
    base_url: str | None = None,
) -> LLMClient:
    """Factory: create the appropriate LLM client based on provider string.

    Parameters
    ----------
    provider:
        ``"anthropic"`` or ``"openai"`` (covers any OpenAI-compatible service).
    api_key:
        API key for the provider.
    base_url:
        Optional base URL override (for proxies, self-hosted, third-party services).
    """
    provider = provider.lower().strip()
    if provider == "anthropic":
        return AnthropicLLMClient(api_key=api_key, base_url=base_url)
    elif provider in ("openai", "openai-compatible"):
        return OpenAILLMClient(api_key=api_key, base_url=base_url)
    else:
        raise ValueError(
            f"Unknown provider '{provider}'. Supported: 'anthropic', 'openai'."
        )
