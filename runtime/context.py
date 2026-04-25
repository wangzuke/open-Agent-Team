"""RuntimeContext: holds all per-agent state for a single agent run."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import OpenTeamsConfig
from .models import AgentIdentity
from .token_tracker import TokenTracker


@runtime_checkable
class Tool(Protocol):
    """Minimal interface every tool must satisfy."""

    @property
    def name(self) -> str:
        ...

    def to_schema(self) -> dict[str, Any]:
        """Return an Anthropic-style tool schema dict."""
        ...

    def execute(self, input: dict[str, Any]) -> str:
        """Execute the tool and return a string result."""
        ...


class RuntimeContext:
    """Carries all mutable state for one agent invocation.

    Parameters
    ----------
    agent_identity:
        Identity of the owning agent.
    messages:
        Conversation history in Anthropic API format.
    tools:
        Mapping of tool name -> Tool instance available to this agent.
    system_prompt:
        The system prompt string sent to the model.
    model:
        Model ID string (e.g. ``"claude-sonnet-4-6"``).
    config:
        Shared :class:`~open_teams.config.OpenTeamsConfig` instance.
    working_dir:
        Filesystem working directory for the agent.
    abort_event:
        Threading event that, when set, signals the engine to stop.
    """

    def __init__(
        self,
        agent_identity: AgentIdentity,
        messages: list[dict[str, Any]] | None = None,
        tools: dict[str, Tool] | None = None,
        system_prompt: str = "",
        model: str = "",
        config: OpenTeamsConfig | None = None,
        working_dir: Path | None = None,
        abort_event: threading.Event | None = None,
        max_turns: int | None = None,
        token_tracker: TokenTracker | None = None,
    ) -> None:
        self.agent_identity: AgentIdentity = agent_identity
        self.messages: list[dict[str, Any]] = messages if messages is not None else []
        self.tools: dict[str, Tool] = tools if tools is not None else {}
        self.system_prompt: str = system_prompt
        self.model: str = model or (
            config.default_model if config is not None else "claude-sonnet-4-6"
        )
        self.config: OpenTeamsConfig = config if config is not None else OpenTeamsConfig()
        self.working_dir: Path = working_dir if working_dir is not None else Path.cwd()
        self.abort_event: threading.Event = (
            abort_event if abort_event is not None else threading.Event()
        )
        self.max_turns: int | None = max_turns
        self.token_tracker: TokenTracker = token_tracker or TokenTracker(
            model=self.model,
            token_budget=self.config.token_budget,
        )

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def clone(self, new_identity: AgentIdentity) -> RuntimeContext:
        """Return a new :class:`RuntimeContext` for a sub-agent.

        The clone shares ``config`` and ``working_dir`` but starts with an
        empty message list and a fresh ``abort_event``.  Tools are
        shallow-copied so sub-agents get the same tool objects.
        """
        return RuntimeContext(
            agent_identity=copy.deepcopy(new_identity),
            messages=[],
            tools=dict(self.tools),
            system_prompt=self.system_prompt,
            model=new_identity.model or self.model,
            config=self.config,           # shared reference
            working_dir=self.working_dir, # shared reference
            abort_event=threading.Event(),
            max_turns=self.max_turns,
        )

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    def add_user_message(self, content: str | list[dict[str, Any]]) -> None:
        """Append a user-role message with plain text content."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str | list[dict[str, Any]]) -> None:
        """Append an assistant-role message.

        ``content`` may be either a plain string or a list of content blocks
        (text / tool_use) as returned by the Anthropic API.
        """
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        """Append tool results as a user-role message.

        Each entry in *results* should follow the Anthropic tool-result
        format::

            {
                "type": "tool_result",
                "tool_use_id": "<id>",
                "content": "<output string>",
                # optional: "is_error": True
            }
        """
        self.messages.append({"role": "user", "content": results})

    def get_max_turns(self) -> int:
        if self.max_turns is not None:
            return self.max_turns
        if self.agent_identity.agent_type == "leader":
            return self.config.max_turns
        return self.config.max_agent_turns

    def estimate_token_count(self, messages: list[dict[str, Any]] | None = None) -> int:
        items = messages if messages is not None else self.messages
        total = 0
        for msg in items:
            total += 4
            total += self._estimate_content_tokens(msg.get("content", ""))
        return total

    def maybe_compress_history(self) -> bool:
        """Compress history when it exceeds 80% of max_context_tokens."""
        if not self.messages or self.config.max_context_tokens <= 0:
            return False

        current_tokens = self.estimate_token_count()
        if current_tokens < int(self.config.max_context_tokens * 0.8):
            return False

        segments = self._segment_messages(self.messages)
        if len(segments) <= 7:
            return False

        head = segments[:1]
        tail = segments[-6:]
        middle = segments[1:-6]
        if not middle:
            return False

        summary = self._summarize_segments(middle)
        new_messages: list[dict[str, Any]] = []
        for segment in head:
            new_messages.extend(segment)
        new_messages.append({"role": "user", "content": f"[CONTEXT SUMMARY]\n{summary}"})
        for segment in tail:
            new_messages.extend(segment)

        self.messages = new_messages
        return True

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return a list of Anthropic-format tool schema dicts."""
        return [tool.to_schema() for tool in self.tools.values()]

    def get_api_messages(self) -> list[dict[str, Any]]:
        """Return the raw message list ready to pass to the Anthropic API."""
        return self.messages

    def _estimate_content_tokens(self, content: Any) -> int:
        if isinstance(content, str):
            return max(1, len(content) // 4)
        if isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        total += max(1, len(block.get("text", "")) // 4)
                    elif btype == "tool_use":
                        total += max(
                            8,
                            len(json.dumps(block.get("input", {}), ensure_ascii=False)) // 4,
                        )
                    elif btype == "tool_result":
                        total += self._estimate_content_tokens(block.get("content", ""))
                    else:
                        total += max(1, len(str(block)) // 4)
                else:
                    total += max(1, len(str(block)) // 4)
            return total
        return max(1, len(str(content)) // 4)

    def _segment_messages(self, messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        segments: list[list[dict[str, Any]]] = []
        idx = 0
        while idx < len(messages):
            current = messages[idx]
            if self._assistant_has_tool_use(current):
                segment = [current]
                if idx + 1 < len(messages) and self._is_tool_result_message(messages[idx + 1]):
                    segment.append(messages[idx + 1])
                    idx += 2
                else:
                    idx += 1
                segments.append(segment)
                continue
            segments.append([current])
            idx += 1
        return segments

    def _assistant_has_tool_use(self, message: dict[str, Any]) -> bool:
        if message.get("role") != "assistant":
            return False
        content = message.get("content", "")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )

    def _is_tool_result_message(self, message: dict[str, Any]) -> bool:
        if message.get("role") != "user":
            return False
        content = message.get("content", "")
        if not isinstance(content, list):
            return False
        if not content:
            return False
        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                return False
            block_type = block.get("type")
            if block_type == "tool_result":
                has_tool_result = True
                continue
            if block_type == "text":
                continue
            return False
        return has_tool_result

    def _summarize_segments(self, segments: list[list[dict[str, Any]]]) -> str:
        lines = [
            f"Compressed {sum(len(segment) for segment in segments)} messages to stay within the context window.",
            "Key earlier context:",
            "- Avoid rereading unchanged files that already appear in the summarized tool history unless you need specific new lines.",
        ]
        for segment in segments:
            excerpt = " | ".join(
                self._message_excerpt(message) for message in segment if self._message_excerpt(message)
            )
            if excerpt:
                lines.append(f"- {excerpt}")
        return "\n".join(lines[:50])

    def _message_excerpt(self, message: dict[str, Any]) -> str:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        text = self._content_to_text(content).strip()
        if not text:
            return ""
        if len(text) > 240:
            text = text[:240] + "..."
        return f"{role}: {text}"

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    parts.append(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(
                        f"[tool_use:{block.get('name', '')}] {json.dumps(block.get('input', {}), ensure_ascii=False)}"
                    )
                elif btype == "tool_result":
                    parts.append(f"[tool_result] {self._content_to_text(block.get('content', ''))}")
                else:
                    parts.append(str(block))
            return "\n".join(part for part in parts if part)
        return str(content)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RuntimeContext(agent={self.agent_identity.agent_name!r}, "
            f"messages={len(self.messages)}, tools={list(self.tools)!r})"
        )
