"""SystemPromptBuilder: assembles full system prompts for leaders and teammates."""

import platform
from datetime import datetime, timezone

from open_teams.config import OpenTeamsConfig
from . import templates, roles


class SystemPromptBuilder:
    """Builds complete system prompts for leader and teammate agents."""

    def __init__(self, config: OpenTeamsConfig):
        self.config = config

    def build_leader_prompt(
        self,
        team_name: str,
        tool_names: list[str],
        working_dir: str,
    ) -> str:
        """Return a fully assembled system prompt for the team leader."""
        env = templates.ENVIRONMENT_TEMPLATE.format(
            working_dir=working_dir,
            platform=platform.system(),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            team_name=team_name,
            agent_name="team-lead",
            agent_type="leader",
            tool_names=", ".join(tool_names),
        )
        return "\n".join([
            templates.BASE_SYSTEM_PROMPT,
            roles.LEADER_PROMPT,
            env,
            templates.TOOL_USAGE_INSTRUCTIONS,
            templates.COLLABORATION_INSTRUCTIONS,
        ])

    def build_teammate_prompt(
        self,
        agent_name: str,
        agent_type: str,
        team_name: str,
        tool_names: list[str],
        working_dir: str,
    ) -> str:
        """Return a fully assembled system prompt for a non-leader teammate."""
        role_instr = roles.ROLE_MAP.get(agent_type, roles.CODER_ROLE)
        teammate = roles.TEAMMATE_PROMPT_TEMPLATE.format(
            agent_name=agent_name,
            agent_type=agent_type,
            team_name=team_name,
            role_instructions=role_instr,
        )
        env = templates.ENVIRONMENT_TEMPLATE.format(
            working_dir=working_dir,
            platform=platform.system(),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            team_name=team_name,
            agent_name=agent_name,
            agent_type=agent_type,
            tool_names=", ".join(tool_names),
        )
        return "\n".join([
            templates.BASE_SYSTEM_PROMPT,
            teammate,
            env,
            templates.TOOL_USAGE_INSTRUCTIONS,
            templates.COLLABORATION_INSTRUCTIONS,
        ])
