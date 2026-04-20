"""Agent definition schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentDefinition:
    agent_type: str  # "leader" | "coder" | "researcher" | "tester" | "reviewer"
    name: str = ""
    model: str = ""
    prompt_override: str = ""
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 0
    background: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def leader(name: str = "team-lead", model: str = "") -> AgentDefinition:
        return AgentDefinition(
            agent_type="leader",
            name=name,
            model=model,
        )

    @staticmethod
    def teammate(name: str, agent_type: str, model: str = "", max_turns: int = 0) -> AgentDefinition:
        return AgentDefinition(
            agent_type=agent_type,
            name=name,
            model=model,
            max_turns=max_turns,
            disallowed_tools=["spawn_agent", "team_create"],
        )
