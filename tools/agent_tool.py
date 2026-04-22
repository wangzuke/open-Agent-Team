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
            "The agent will be created with the given name and type. "
            "You MUST provide both mission and task_description, plus the structured briefing fields. "
            "Agent types: coder, researcher, tester, reviewer, architect."
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
                        "One of: coder, researcher, tester, reviewer, architect."
                    ),
                },
                "task_description": {
                    "type": "string",
                    "description": (
                        "Detailed execution brief for the agent. Include current context, boundaries, relevant task IDs, "
                        "dependencies, and any contract or scaffold artifacts the agent must follow."
                    ),
                },
                "mission": {
                    "type": "string",
                    "description": "Single-sentence primary mission for the agent. This field is REQUIRED.",
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific task IDs the agent is expected to own first.",
                },
                "owned_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files or directories the agent should treat as its main write scope.",
                },
                "required_reads": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files the agent should read before making decisions.",
                },
                "deliverables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Outputs that must exist when the agent finishes.",
                },
                "definition_of_done": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concrete completion criteria the agent must satisfy before calling task_update(status='completed').",
                },
                "quality_bar": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Quality requirements such as tests, exact patterns to follow, or verification expectations.",
                },
                "coordination_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Coordination instructions, especially for cross-team contracts such as API routes or shared schemas.",
                },
                "startup_checklist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered startup steps the agent should execute before implementing changes.",
                },
                "completion_report_template": {
                    "type": "string",
                    "description": "Optional template describing what the agent must include in its completion report.",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "The model identifier to use for this agent. "
                        "If omitted, the team default model is used."
                    ),
                },
            },
            "required": ["name", "agent_type", "mission", "task_description"],
        }

    def execute(self, params: dict[str, Any]) -> str:
        name = str(params.get("name", "")).strip()
        if not name:
            raise ValueError("spawn_agent requires name.")
        agent_type = str(params.get("agent_type", "")).strip()
        if not agent_type:
            raise ValueError("spawn_agent requires agent_type.")
        mission = str(params.get("mission", "")).strip()
        if not mission:
            raise ValueError("spawn_agent requires mission.")
        task_description = str(params.get("task_description", "")).strip()
        if not task_description:
            raise ValueError("spawn_agent requires task_description.")

        payload = {
            "action": "spawn_agent",
            "name": name,
            "agent_type": agent_type,
            "task_description": task_description,
            "mission": mission,
            "task_ids": params.get("task_ids", []),
            "owned_paths": params.get("owned_paths", []),
            "required_reads": params.get("required_reads", []),
            "deliverables": params.get("deliverables", []),
            "definition_of_done": params.get("definition_of_done", []),
            "quality_bar": params.get("quality_bar", []),
            "coordination_notes": params.get("coordination_notes", []),
            "startup_checklist": params.get("startup_checklist", []),
            "completion_report_template": params.get("completion_report_template", ""),
            "model": params.get("model", ""),
        }
        return json.dumps(payload)
