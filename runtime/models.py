"""Message and data models for the runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str | list[dict[str, Any]] = ""
    tool_call_id: str | None = None
    name: str | None = None

    def to_api_format(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role.value}
        if isinstance(self.content, str):
            msg["content"] = self.content
        else:
            msg["content"] = self.content
        if self.tool_call_id:
            msg["tool_use_id"] = self.tool_call_id
        return msg


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_call_id: str
    content: Any = ""
    is_error: bool = False

    def to_api_format(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.tool_call_id,
        }
        if self.is_error:
            result["is_error"] = True
        result["content"] = self.content
        return result


@dataclass
class AgentIdentity:
    agent_id: str
    agent_name: str
    agent_type: str  # "leader" | "teammate"
    team_name: str | None = None
    model: str = ""


@dataclass
class QueryResult:
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    text_output: str = ""
