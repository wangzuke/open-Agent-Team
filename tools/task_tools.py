"""Task management tools: create, update, list, and get tasks on the team TaskBoard."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from config import OpenTeamsConfig
    from coordination.task_board import TaskBoard


def _format_list_block(label: str, values: list[str]) -> list[str]:
    if not values:
        return []
    lines = [f"{label}:"]
    for value in values:
        lines.append(f"  - {value}")
    return lines


def _compose_task_description(
    description: str,
    goal: str,
    scope: list[str],
    deliverables: list[str],
    acceptance: list[str],
    constraints: list[str],
    interfaces: list[str],
    handoff: str,
) -> str:
    lines: list[str] = []
    if goal:
        lines.extend(["Goal:", goal, ""])
    if description:
        lines.extend(["Context:", description, ""])
    if scope:
        lines.extend(_format_list_block("Scope", scope))
        lines.append("")
    if deliverables:
        lines.extend(_format_list_block("Deliverables", deliverables))
        lines.append("")
    if acceptance:
        lines.extend(_format_list_block("Acceptance Criteria", acceptance))
        lines.append("")
    if constraints:
        lines.extend(_format_list_block("Constraints", constraints))
        lines.append("")
    if interfaces:
        lines.extend(_format_list_block("Contracts / Interfaces", interfaces))
        lines.append("")
    if handoff:
        lines.extend(["Handoff:", handoff, ""])
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _format_task(task: dict) -> str:
    """Return a human-readable string for a single task dict."""
    lines = [
        f"Task ID:     {task.get('id', '?')}",
        f"Subject:     {task.get('subject', '')}",
        f"Status:      {task.get('status', '')}",
        f"Owner:       {task.get('owner') or '(unassigned)'}",
        f"Agent Type:  {task.get('agentType', '') or '(unspecified)'}",
        f"Priority:    {task.get('priority', '') or '(unspecified)'}",
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
    priority = task.get("priority") or "-"
    agent_type = task.get("agentType") or "-"
    return (
        f"[{task.get('id', '?')}] {task.get('status', '?'):10s} "
        f"owner={owner:15s} role={agent_type:10s} pri={priority:8s} "
        f"blockedBy={blocked}  {task.get('subject', '')}"
    )


class TaskCreateTool(Tool):
    def __init__(self):
        self.name = "task_create"
        self.description = (
            "Create a new task on the team task board. Use this when you want a teammate or your future self to have "
            "a clear, inspectable work item. Prefer the structured fields (goal, scope, deliverables, acceptance, "
            "constraints, interfaces, handoff) so the task is specific instead of vague. Returns the created task "
            "including its canonical numeric task ID, such as '1' or '2'."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Short task title. Keep it specific enough that task_list remains readable.",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Task context. Explain the current state, why this task exists, relevant files, and any "
                        "important background. This is the narrative part of the brief. If you omit it, still provide "
                        "at least a precise goal and acceptance criteria."
                    ),
                },
                "owner": {
                    "type": "string",
                    "description": "Agent name to assign immediately, for example 'coder-backend'. Leave empty to keep the task unassigned.",
                },
                "agent_type": {
                    "type": "string",
                    "description": "Recommended role for this task, such as coder, researcher, tester, reviewer, or architect.",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority hint such as critical, high, medium, or low. Use this to help scheduling, not as a status.",
                },
                "goal": {
                    "type": "string",
                    "description": "One-sentence statement of the concrete outcome this task must achieve. This should read like the finish line.",
                },
                "scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files, directories, or modules this task is allowed or expected to touch. Use repo-relative paths when possible.",
                },
                "deliverables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concrete outputs that must exist when the task is done, for example files, docs, or tests.",
                },
                "acceptance": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific completion checks the assignee must satisfy before marking the task completed.",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Non-negotiable rules, conventions, forbidden actions, or technology constraints for this task.",
                },
                "interfaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contract details that must stay aligned with other teammates, such as API routes, request/response shapes, shared types, or schema names.",
                },
                "handoff": {
                    "type": "string",
                    "description": "What to report when done, where to hand off, and which downstream teammate or task depends on this work.",
                },
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of prerequisite task IDs. Prefer canonical numeric task IDs like '1' or '2', not labels like 'task-1'.",
                },
            },
            "required": ["subject"],
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
            from coordination.task_board import TaskBoard
            self._board = TaskBoard(self._config, self._team_name)
        return self._board

    def execute(self, params: dict[str, Any]) -> str:
        subject: str = params["subject"]
        description: str = params.get("description", "")
        owner: Optional[str] = params.get("owner") or None
        blocked_by: list[str] = params.get("blockedBy") or []
        agent_type: Optional[str] = params.get("agent_type") or None
        priority: Optional[str] = params.get("priority") or None
        goal: str = params.get("goal", "").strip()
        scope: list[str] = params.get("scope") or []
        deliverables: list[str] = params.get("deliverables") or []
        acceptance: list[str] = params.get("acceptance") or []
        constraints: list[str] = params.get("constraints") or []
        interfaces: list[str] = params.get("interfaces") or []
        handoff: str = params.get("handoff", "").strip()

        if not description and not goal:
            return "Error: task_create requires either description or goal."

        final_description = _compose_task_description(
            description=description.strip(),
            goal=goal,
            scope=scope,
            deliverables=deliverables,
            acceptance=acceptance,
            constraints=constraints,
            interfaces=interfaces,
            handoff=handoff,
        )

        try:
            board = self._get_board()
            task = board.create_task(
                subject=subject,
                description=final_description,
                owner=owner,
                blockedBy=blocked_by,
                agent_type=agent_type,
                priority=priority,
                goal=goal or None,
                scope=scope,
                deliverables=deliverables,
                acceptance=acceptance,
                constraints=constraints,
                interfaces=interfaces,
                handoff=handoff or None,
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
            "Update an existing task on the team task board. Use this to change status, owner, description, or add "
            "dependencies. Only include the fields you want to change. Use addBlocks/addBlockedBy to append to "
            "dependency lists instead of rewriting them manually."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to update. Prefer the canonical numeric ID string, for example '1'.",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "New task status. Common values are pending, in_progress, completed, and blocked. "
                        "Set in_progress when starting real work and completed immediately after acceptance criteria are satisfied."
                    ),
                },
                "owner": {
                    "type": "string",
                    "description": "New owner agent name, for example 'coder-backend'. Use this when explicitly assigning or reassigning work.",
                },
                "description": {
                    "type": "string",
                    "description": "Replacement task description. Use this only when the task brief itself needs correction or clarification.",
                },
                "addBlocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs to append to this task's blocks list. Prefer canonical numeric IDs like '2' and '3'.",
                },
                "addBlockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs to append to this task's blockedBy list. Use this when the task cannot start until those prerequisite tasks complete.",
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
            from coordination.task_board import TaskBoard
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
            from coordination.mailbox import Mailbox
            mailbox = Mailbox(self._config, self._team_name)
            sender = self._agent_name or "system"
            for task in tasks:
                owner = task.get("owner")
                if not owner:
                    continue
                interface_notes = task.get("interfaces", []) or []
                handoff = task.get("handoff", "")
                extra_lines: list[str] = []
                if interface_notes:
                    extra_lines.append("Key contracts / interfaces:")
                    extra_lines.extend(f"- {note}" for note in interface_notes[:5])
                if handoff:
                    extra_lines.append(f"Handoff expectation: {handoff}")
                content = (
                    f"[TASK READY] Task #{task['id']} '{task.get('subject', '')}' "
                    f"is now unblocked and ready for you to work on.\n"
                    f"Call task_get(task_id=\"{task['id']}\") for full details, "
                    f"then start working."
                )
                if extra_lines:
                    content = f"{content}\n" + "\n".join(extra_lines)
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
            "List all tasks on the team task board. Use this for a high-level snapshot of status, owner, role, "
            "priority, and dependencies. Do not poll it repeatedly when nothing is changing; use task_get for one "
            "specific task when you need details."
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
            from coordination.task_board import TaskBoard
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
            "Get the full details of one specific task. Use this when you need the complete brief before acting, "
            "especially after receiving a task ID in a spawn brief or a [TASK READY] message. "
            "Do not use this tool as a heartbeat or progress-polling loop."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to retrieve. Prefer the canonical numeric ID string, for example '1'.",
                },
            },
            "required": ["task_id"],
        }
        self._team_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._board: Optional["TaskBoard"] = None
        self._last_fingerprint_by_task: dict[str, str] = {}

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
            from coordination.task_board import TaskBoard
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

        fingerprint = self._fingerprint(task)
        if self._last_fingerprint_by_task.get(task_id) == fingerprint:
            return (
                f"No task changes since your last task_get for Task #{task_id}.\n"
                f"Status: {task.get('status', '') or '(unknown)'}\n"
                f"Owner: {task.get('owner') or '(unassigned)'}\n"
                f"Updated: {task.get('updatedAt', '') or '(unknown)'}\n"
                "Do not poll task_get repeatedly for progress. Wait for inbox updates, "
                "task_update confirmations, dependency changes, or a real need to re-read the brief."
            )

        self._last_fingerprint_by_task[task_id] = fingerprint
        return _format_task(task)

    def _fingerprint(self, task: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "id": task.get("id"),
                "subject": task.get("subject"),
                "description": task.get("description"),
                "status": task.get("status"),
                "owner": task.get("owner"),
                "agentType": task.get("agentType"),
                "priority": task.get("priority"),
                "blockedBy": task.get("blockedBy", []),
                "blocks": task.get("blocks", []),
                "deliverables": task.get("deliverables", []),
                "acceptance": task.get("acceptance", []),
                "constraints": task.get("constraints", []),
                "interfaces": task.get("interfaces", []),
                "handoff": task.get("handoff"),
                "updatedAt": task.get("updatedAt"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()
