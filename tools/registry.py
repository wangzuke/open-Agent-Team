"""Registry factories: build pre-configured ToolRegistry instances for leaders and teammates."""

from __future__ import annotations

from open_teams.config import OpenTeamsConfig
from .base import ToolRegistry
from .file_tools import ReadFileTool, WriteFileTool, EditFileTool
from .search_tools import GlobTool, GrepTool
from .shell_tool import ShellTool
from .agent_tool import SpawnAgentTool
from .message_tool import SendMessageTool, CheckInboxTool
from .task_tools import TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool
from .team_tool import TeamCreateTool


def create_leader_tools(
    config: OpenTeamsConfig,
    team_name: str,
    agent_name: str = "team-lead",
) -> ToolRegistry:
    """Build a ToolRegistry with the full leader tool set.

    Includes: file I/O, search, shell, spawn_agent, send_message, check_inbox,
    all task tools, and team_create.
    """
    registry = ToolRegistry()

    # File tools — resolve relative paths against project_root
    project_root = config.project_root
    for tool_cls in [ReadFileTool, WriteFileTool, EditFileTool]:
        registry.register(tool_cls(project_root=project_root))

    # Search tools
    for tool_cls in [GlobTool, GrepTool]:
        registry.register(tool_cls())

    # Shell
    shell = ShellTool()
    shell.set_working_dir(str(config.project_root))
    registry.register(shell)

    # Agent spawning
    registry.register(SpawnAgentTool())

    # Messaging — send + check inbox
    msg = SendMessageTool()
    msg.set_context(team_name, agent_name, config)
    registry.register(msg)

    inbox = CheckInboxTool()
    inbox.set_context(team_name, agent_name, config)
    registry.register(inbox)

    # Task management
    for tool_cls in [TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool]:
        t = tool_cls()
        t.set_context(team_name, config)
        registry.register(t)

    # Team management
    tc = TeamCreateTool()
    tc.set_context(config)
    registry.register(tc)

    return registry


def create_teammate_tools(
    config: OpenTeamsConfig,
    team_name: str,
    agent_name: str,
) -> ToolRegistry:
    """Build a ToolRegistry with the standard teammate tool set.

    Includes: file I/O, search, shell, send_message, check_inbox, and all task tools.
    Does NOT include spawn_agent or team_create (leader-only capabilities).
    """
    registry = ToolRegistry()

    # File tools — resolve relative paths against project_root
    project_root = config.project_root
    for tool_cls in [ReadFileTool, WriteFileTool, EditFileTool]:
        registry.register(tool_cls(project_root=project_root))

    # Search tools
    for tool_cls in [GlobTool, GrepTool]:
        registry.register(tool_cls())

    # Shell
    shell = ShellTool()
    shell.set_working_dir(str(config.project_root))
    registry.register(shell)

    # Messaging — send + check inbox
    msg = SendMessageTool()
    msg.set_context(team_name, agent_name, config)
    registry.register(msg)

    inbox = CheckInboxTool()
    inbox.set_context(team_name, agent_name, config)
    registry.register(inbox)

    # Task management
    for tool_cls in [TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool]:
        t = tool_cls()
        t.set_context(team_name, config)
        registry.register(t)

    return registry
