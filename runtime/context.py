"""RuntimeContext: holds all per-agent state for a single agent run."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import OpenTeamsConfig
from .models import AgentIdentity


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
        )

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    def add_user_message(self, content: str) -> None:
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

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return a list of Anthropic-format tool schema dicts."""
        return [tool.to_schema() for tool in self.tools.values()]

    def get_api_messages(self) -> list[dict[str, Any]]:
        """Return the raw message list ready to pass to the Anthropic API."""
        return self.messages

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RuntimeContext(agent={self.agent_identity.agent_name!r}, "
            f"messages={len(self.messages)}, tools={list(self.tools)!r})"
        )
