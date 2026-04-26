"""Registry factories: build pre-configured ToolRegistry instances for leaders and teammates."""

from __future__ import annotations

from config import OpenTeamsConfig
from .base import ToolRegistry
from .file_tools import ReadFileTool, WriteFileTool, EditFileTool
from .git_tools import GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool
from .search_tools import GlobTool, GrepTool
from .shell_tool import ShellTool
from .agent_tool import SpawnAgentTool
from .message_tool import SendMessageTool
from .task_tools import TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool


def create_leader_tools(
    config: OpenTeamsConfig,
    team_name: str,
    agent_name: str = "team-lead",
) -> ToolRegistry:
    """Build a ToolRegistry with the full leader tool set.

    Includes: file I/O, search, shell, spawn_agent, send_message, and all task tools.
    """
    registry = ToolRegistry()

    # File tools — resolve relative paths against project_root
    project_root = config.project_root
    for tool_cls in [ReadFileTool, WriteFileTool, EditFileTool]:
        registry.register(tool_cls(project_root=project_root, config=config))

    # Search tools
    for tool_cls in [GlobTool, GrepTool]:
        registry.register(tool_cls())

    # Shell
    shell = ShellTool()
    shell.set_working_dir(str(config.project_root), config=config)
    registry.register(shell)

    for tool_cls in [GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool]:
        registry.register(tool_cls(config.project_root))

    # Agent spawning
    registry.register(SpawnAgentTool())

    # Messaging
    msg = SendMessageTool()
    msg.set_context(team_name, agent_name, config)
    registry.register(msg)

    # Task management
    for tool_cls in [TaskCreateTool, TaskListTool, TaskGetTool]:
        t = tool_cls()
        t.set_context(team_name, config)
        registry.register(t)

    task_update = TaskUpdateTool()
    task_update.set_context(team_name, config, agent_name=agent_name)
    registry.register(task_update)

    return registry


def create_teammate_tools(
    config: OpenTeamsConfig,
    team_name: str,
    agent_name: str,
) -> ToolRegistry:
    """Build a ToolRegistry with the standard teammate tool set.

    Includes: file I/O, search, shell, send_message, and all task tools.
    Does NOT include spawn_agent or team_create (leader-only capabilities).
    """
    registry = ToolRegistry()

    # File tools — resolve relative paths against project_root
    project_root = config.project_root
    for tool_cls in [ReadFileTool, WriteFileTool, EditFileTool]:
        registry.register(tool_cls(project_root=project_root, config=config))

    # Search tools
    for tool_cls in [GlobTool, GrepTool]:
        registry.register(tool_cls())

    # Shell
    shell = ShellTool()
    shell.set_working_dir(str(config.project_root), config=config)
    registry.register(shell)

    for tool_cls in [GitStatusTool, GitDiffTool, GitLogTool, GitCommitTool]:
        registry.register(tool_cls(config.project_root))

    # Messaging
    msg = SendMessageTool()
    msg.set_context(team_name, agent_name, config)
    registry.register(msg)

    # Task management
    for tool_cls in [TaskCreateTool, TaskListTool, TaskGetTool]:
        t = tool_cls()
        t.set_context(team_name, config)
        registry.register(t)

    task_update = TaskUpdateTool()
    task_update.set_context(team_name, config, agent_name=agent_name)
    registry.register(task_update)

    return registry
