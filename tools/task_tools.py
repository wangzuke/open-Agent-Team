"""Task management tools: create, update, list, and get tasks on the team TaskBoard."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from open_teams.config import OpenTeamsConfig
    from open_teams.coordination.task_board import TaskBoard


def _format_task(task: dict) -> str:
    """Return a human-readable string for a single task dict."""
    lines = [
        f"Task ID:     {task.get('id', '?')}",
        f"Subject:     {task.get('subject', '')}",
        f"Status:      {task.get('status', '')}",
        f"Owner:       {task.get('owner') or '(unassigned)'}",
        f"Description: {task.get('description', '')}",
        f"Blocks:      {', '.join(task.get('blocks', [])) or 'none'}",
        f"BlockedBy:   {', '.join(task.get('blockedBy', [])) or 'none'}",
        f"Created:     {task.get('createdAt', '')}",
        f"Updated:     {task.get('updatedAt', '')}",
    ]
    return "\n".join(lines)


def _format_task_summary(task: dict) -> str:
    """Return a compact one-line summary for task list display."""
    owner = task.get("owner") or "(unassigned)"
    blocked = ", ".join(task.get("blockedBy", [])) or "none"
    return (
        f"[{task.get('id', '?')}] {task.get('status', '?'):10s} "
        f"owner={owner:15s} blockedBy={blocked}  {task.get('subject', '')}"
    )


class TaskCreateTool(Tool):
    def __init__(self):
        self.name = "task_create"
        self.description = (
            "Create a new task on the team task board. "
            "Returns the created task with its assigned ID."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Short title/subject for the task.",
                },
                "description": {
                    "type": "string",
                    "description": "Full description of what needs to be done.",
                },
                "owner": {
                    "type": "string",
                    "description": "Agent name to assign the task to. Leave empty for unassigned.",
                },
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs that must be completed before this task.",
                },
            },
            "required": ["subject", "description"],
        }
        self._team_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._board: Optional["TaskBoard"] = None

    def set_context(self, team_name: str, config: "OpenTeamsConfig") -> None:
        self._team_name = team_name
        self._config = config
        self._board = None

    def _get_board(self) -> "TaskBoard":
        if self._board is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "TaskCreateTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.task_board import TaskBoard
            self._board = TaskBoard(self._config, self._team_name)
        return self._board

    def execute(self, params: dict[str, Any]) -> str:
        subject: str = params["subject"]
        description: str = params["description"]
        owner: Optional[str] = params.get("owner") or None
        blocked_by: list[str] = params.get("blockedBy") or []

        try:
            board = self._get_board()
            task = board.create_task(
                subject=subject,
                description=description,
                owner=owner,
                blockedBy=blocked_by,
            )
            return f"Task created successfully:\n{_format_task(task)}"
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error creating task: {exc}"


class TaskUpdateTool(Tool):
    def __init__(self):
        self.name = "task_update"
        self.description = (
            "Update an existing task on the team task board. "
            "Specify only the fields you want to change. "
            "Use addBlocks/addBlockedBy to append to dependency lists."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to update.",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "New status for the task. "
                        "Common values: pending, in_progress, completed, blocked."
                    ),
                },
                "owner": {
                    "type": "string",
                    "description": "New owner (agent name) to assign to the task.",
                },
                "description": {
                    "type": "string",
                    "description": "Updated description for the task.",
                },
                "addBlocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs to append to this task's 'blocks' list.",
                },
                "addBlockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs to append to this task's 'blockedBy' list.",
                },
            },
            "required": ["task_id"],
        }
        self._team_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._board: Optional["TaskBoard"] = None

    def set_context(self, team_name: str, config: "OpenTeamsConfig", agent_name: str = "") -> None:
        self._team_name = team_name
        self._config = config
        self._board = None
        self._agent_name = agent_name

    def _get_board(self) -> "TaskBoard":
        if self._board is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "TaskUpdateTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.task_board import TaskBoard
            self._board = TaskBoard(self._config, self._team_name)
        return self._board

    def execute(self, params: dict[str, Any]) -> str:
        task_id: str = params["task_id"]

        update_kwargs: dict[str, Any] = {}
        for field in ("status", "owner", "description"):
            if field in params and params[field] is not None:
                update_kwargs[field] = params[field]
        if "addBlocks" in params and params["addBlocks"]:
            update_kwargs["addBlocks"] = params["addBlocks"]
        if "addBlockedBy" in params and params["addBlockedBy"]:
            update_kwargs["addBlockedBy"] = params["addBlockedBy"]

        if not update_kwargs:
            return "Error: No fields to update were provided."

        try:
            board = self._get_board()

            if update_kwargs.get("status") == "completed":
                task, newly_unblocked = board.update_task_with_deps(task_id, **update_kwargs)
                self._notify_unblocked(newly_unblocked)
            else:
                task = board.update_task(task_id, **update_kwargs)

            return f"Task updated successfully:\n{_format_task(task)}"
        except KeyError as exc:
            return f"Error: {exc}"
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error updating task {task_id}: {exc}"

    def _notify_unblocked(self, tasks: list[dict]) -> None:
        """Send inbox notifications to owners of newly-unblocked tasks."""
        if not tasks or not self._config or not self._team_name:
            return
        try:
            from open_teams.coordination.mailbox import Mailbox
            mailbox = Mailbox(self._config, self._team_name)
            sender = self._agent_name or "system"
            for task in tasks:
                owner = task.get("owner")
                if not owner:
                    continue
                content = (
                    f"[TASK READY] Task #{task['id']} '{task.get('subject', '')}' "
                    f"is now unblocked and ready for you to work on.\n"
                    f"Call task_get(task_id=\"{task['id']}\") for full details, "
                    f"then start working."
                )
                mailbox.send_message(
                    from_agent=sender,
                    to_agent=owner,
                    content=content,
                    summary=f"Task #{task['id']} ready",
                )
        except Exception:
            pass


class TaskListTool(Tool):
    def __init__(self):
        self.name = "task_list"
        self.description = (
            "List all tasks on the team task board. "
            "Returns a summary of every task including ID, status, owner, and subject."
        )
        self.input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._team_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._board: Optional["TaskBoard"] = None
        self._last_fingerprint: str = ""

    def set_context(self, team_name: str, config: "OpenTeamsConfig") -> None:
        self._team_name = team_name
        self._config = config
        self._board = None

    def _get_board(self) -> "TaskBoard":
        if self._board is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "TaskListTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.task_board import TaskBoard
            self._board = TaskBoard(self._config, self._team_name)
        return self._board

    def execute(self, params: dict[str, Any]) -> str:
        try:
            board = self._get_board()
            tasks = board.list_tasks()
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error listing tasks: {exc}"

        if not tasks:
            return "No tasks found on the task board."

        fingerprint = self._fingerprint(tasks)
        counts = self._counts(tasks)
        counts_line = (
            f"Counts: pending={counts['pending']}, in_progress={counts['in_progress']}, "
            f"completed={counts['completed']}, blocked={counts['blocked']}"
        )
        if fingerprint == self._last_fingerprint:
            return (
                "No task-board changes since your last task_list call.\n"
                f"{counts_line}\n"
                "Avoid polling task_list repeatedly. Wait for inbox updates or inspect a specific task with task_get."
            )

        lines = [f"Task Board ({len(tasks)} task(s)):"]
        lines.append("-" * 70)
        lines.append(counts_line)
        for task in tasks:
            lines.append(_format_task_summary(task))
        self._last_fingerprint = fingerprint
        return "\n".join(lines)

    def _counts(self, tasks: list[dict]) -> dict[str, int]:
        return {
            "pending": sum(1 for task in tasks if task.get("status") == "pending"),
            "in_progress": sum(1 for task in tasks if task.get("status") == "in_progress"),
            "completed": sum(1 for task in tasks if task.get("status") == "completed"),
            "blocked": sum(1 for task in tasks if task.get("status") == "blocked"),
        }

    def _fingerprint(self, tasks: list[dict]) -> str:
        normalized = [
            {
                "id": task.get("id"),
                "status": task.get("status"),
                "owner": task.get("owner"),
                "blockedBy": task.get("blockedBy", []),
                "updatedAt": task.get("updatedAt"),
            }
            for task in tasks
        ]
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class TaskGetTool(Tool):
    def __init__(self):
        self.name = "task_get"
        self.description = (
            "Get the full details of a specific task by its ID. "
            "Returns all task fields including description, status, owner, and dependencies."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to retrieve.",
                },
            },
            "required": ["task_id"],
        }
        self._team_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._board: Optional["TaskBoard"] = None

    def set_context(self, team_name: str, config: "OpenTeamsConfig") -> None:
        self._team_name = team_name
        self._config = config
        self._board = None

    def _get_board(self) -> "TaskBoard":
        if self._board is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "TaskGetTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.task_board import TaskBoard
            self._board = TaskBoard(self._config, self._team_name)
        return self._board

    def execute(self, params: dict[str, Any]) -> str:
        task_id: str = params["task_id"]

        try:
            board = self._get_board()
            task = board.get_task(task_id)
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error retrieving task {task_id}: {exc}"

        if task is None:
            return f"Error: Task '{task_id}' not found."

        return _format_task(task)
