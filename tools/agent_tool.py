"""Agent spawning tool: signal the runtime to create a new sub-agent."""

from __future__ import annotations

import json
from typing import Any

from .base import Tool


class SpawnAgentTool(Tool):
    def __init__(self):
        self.name = "spawn_agent"
        self.description = (
            "Spawn a new sub-agent to work on a specific task. "
            "The agent will be created with the given name and type, "
            "and will begin working on the provided task description. "
            "Agent types: coder, researcher, tester, reviewer."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name for the new agent within the team.",
                },
                "agent_type": {
                    "type": "string",
                    "description": (
                        "The type of agent to spawn. "
                        "One of: coder, researcher, tester, reviewer."
                    ),
                },
                "task_description": {
                    "type": "string",
                    "description": "A detailed description of the task the agent should perform.",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "The model identifier to use for this agent. "
                        "If omitted, the team default model is used."
                    ),
                },
            },
            "required": ["name", "agent_type", "task_description"],
        }

    def execute(self, params: dict[str, Any]) -> str:
        payload = {
            "action": "spawn_agent",
            "name": params["name"],
            "agent_type": params["agent_type"],
            "task_description": params["task_description"],
            "model": params.get("model", ""),
        }
        return json.dumps(payload)
