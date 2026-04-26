"""Team management tool: create new teams via the TeamManager."""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from config import OpenTeamsConfig
    from coordination.team import TeamManager


class TeamCreateTool(Tool):
    def __init__(self):
        self.name = "team_create"
        self.description = (
            "Create a new team with a given name and optional description. "
            "The team will be registered in the workspace and ready to accept members."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "The unique name for the new team.",
                },
                "description": {
                    "type": "string",
                    "description": "A short description of the team's purpose.",
                },
            },
            "required": ["team_name"],
        }
        self._config: Optional["OpenTeamsConfig"] = None
        self._manager: Optional["TeamManager"] = None

    def set_context(self, config: "OpenTeamsConfig") -> None:
        """Provide the configuration needed for team management."""
        self._config = config
        self._manager = None  # reset so it gets re-created lazily

    def _get_manager(self) -> "TeamManager":
        if self._manager is None:
            if self._config is None:
                raise RuntimeError(
                    "TeamCreateTool: set_context() must be called before execute()."
                )
            from coordination.team import TeamManager
            self._manager = TeamManager(self._config)
        return self._manager

    def execute(self, params: dict[str, Any]) -> str:
        team_name: str = params["team_name"]
        description: str = params.get("description", "")

        try:
            manager = self._get_manager()
            # Create the team with a placeholder leader so the schema is valid.
            # The actual leader is typically registered separately when the team
            # is bootstrapped; here we use sensible defaults.
            from utils.helpers import generate_id
            leader_id = generate_id("agent_")
            team = manager.create_team(
                team_name=team_name,
                description=description,
                leader_name="team-lead",
                leader_id=leader_id,
                leader_model=self._config.leader_model if self._config else "",
            )
            return (
                f"Team '{team_name}' created successfully.\n"
                f"Description: {team.get('description', '')}\n"
                f"Created at:  {team.get('createdAt', '')}"
            )
        except ValueError as exc:
            return f"Error: {exc}"
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error creating team '{team_name}': {exc}"
