"""Agent manager - handles spawning and lifecycle of agent processes."""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from open_teams.config import OpenTeamsConfig
from open_teams.agents.definition import AgentDefinition
from open_teams.runtime.models import AgentIdentity
from open_teams.utils.helpers import generate_id, timestamp_now


def _run_worker_process(
    agent_name: str,
    agent_type: str,
    team_name: str,
    model: str,
    max_turns: int,
    task_description: str,
    spawn_spec: dict[str, Any],
    config_dict: dict[str, Any],
    working_dir: str,
):
    """Entry point for a teammate worker process."""
    import os
    import sys
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.chdir(working_dir)

    from open_teams.config import init_config
    from open_teams.runtime.context import RuntimeContext
    from open_teams.runtime.engine import QueryEngine
    from open_teams.runtime.models import AgentIdentity
    from open_teams.tools import create_teammate_tools
    from open_teams.prompts import SystemPromptBuilder
    from open_teams.logging import ActivityLogger
    from open_teams.coordination.mailbox import Mailbox

    config = init_config(**config_dict)

    logger = ActivityLogger(config, team_name, agent_name)
    logger.log_event("agent_started", {
        "agent_type": agent_type,
        "model": model,
        "mission": spawn_spec.get("mission", ""),
        "task_ids": spawn_spec.get("task_ids", []),
        "task_description": task_description,
        "owned_paths": spawn_spec.get("owned_paths", []),
        "required_reads": spawn_spec.get("required_reads", []),
        "deliverables": spawn_spec.get("deliverables", []),
        "definition_of_done": spawn_spec.get("definition_of_done", []),
        "coordination_notes": spawn_spec.get("coordination_notes", []),
        "startup_checklist": spawn_spec.get("startup_checklist", []),
    })

    registry = create_teammate_tools(config, team_name, agent_name)
    prompt_builder = SystemPromptBuilder(config)
    system_prompt = prompt_builder.build_teammate_prompt(
        agent_name=agent_name,
        agent_type=agent_type,
        team_name=team_name,
        tool_names=registry.get_tool_names(),
        working_dir=working_dir,
    )

    identity = AgentIdentity(
        agent_id=generate_id("agent-"),
        agent_name=agent_name,
        agent_type=agent_type,
        team_name=team_name,
        model=model,
    )

    context = RuntimeContext(
        agent_identity=identity,
        tools=registry.as_dict(),
        system_prompt=system_prompt,
        model=model,
        config=config,
        working_dir=Path(working_dir),
        max_turns=max_turns,
    )

    engine = QueryEngine(context, activity_logger=logger)

    initial_msg = _build_initial_teammate_message(
        agent_name=agent_name,
        agent_type=agent_type,
        team_name=team_name,
        task_description=task_description,
        spawn_spec=spawn_spec,
    )

    _wait_for_actionable_task(config, team_name, agent_name, logger)

    agent_crashed = False
    crash_summary = ""
    final_output = ""

    try:
        final_output = engine.run_loop(initial_message=initial_msg)
        logger.log_event("agent_completed", {"final_output": final_output[:2000]})
    except Exception as e:
        agent_crashed = True
        crash_summary = f"{type(e).__name__}: {e}"
        logger.log_error(f"Agent crashed: {e}", {"traceback": traceback.format_exc()})

    mailbox = Mailbox(config, team_name)
    try:
        if agent_crashed:
            content = (
                f"Agent '{agent_name}' has CRASHED and is shutting down.\n\n"
                f"Error: {crash_summary}\n\n"
                "Tasks owned by this agent may be stuck. "
                "Please check the task board and reassign if necessary."
            )
            summary = f"{agent_name} CRASHED: {crash_summary[:80]}"
        else:
            from open_teams.coordination.task_board import TaskBoard
            completed, in_prog = [], []
            try:
                board = TaskBoard(config, team_name)
                my_tasks = board.list_tasks(filter_owner=agent_name)
                completed = [t for t in my_tasks if t["status"] == "completed"]
                in_prog = [t for t in my_tasks if t["status"] == "in_progress"]
                blocked = [t for t in my_tasks if t["status"] == "blocked"]

                task_lines = []
                if completed:
                    task_lines.append(f"  Completed: {', '.join('Task ' + t['id'] for t in completed)}")
                if in_prog:
                    task_lines.append(f"  Still in_progress: {', '.join('Task ' + t['id'] for t in in_prog)}")
                if blocked:
                    task_lines.append(f"  Blocked: {', '.join('Task ' + t['id'] for t in blocked)}")
                task_section = "\n".join(task_lines) if task_lines else "  All tasks handled."
            except Exception:
                task_section = "  (Could not retrieve task status)"

            content = (
                f"Agent '{agent_name}' has finished and is shutting down.\n\n"
                f"Task status:\n{task_section}"
            )
            summary = f"{agent_name} done — {len(completed)} completed, {len(in_prog)} in_progress"

        mailbox.send_message(
            from_agent=agent_name,
            to_agent="team-lead",
            content=content,
            summary=summary,
        )
    except Exception:
        pass

    logger.log_event("agent_shutdown")


def _wait_for_actionable_task(
    config,
    team_name: str,
    agent_name: str,
    logger,
    poll_interval: float = 2.0,
) -> None:
    """Block (zero LLM cost) until the agent has at least one non-blocked task."""
    from open_teams.coordination.task_board import TaskBoard

    board = TaskBoard(config, team_name)
    start = time.monotonic()

    while True:
        try:
            tasks = board.list_tasks(filter_owner=agent_name)
            for t in tasks:
                if t["status"] in ("pending", "in_progress") and not t.get("blockedBy"):
                    logger.log_event("task_ready", {"task_id": t["id"], "wait_seconds": round(time.monotonic() - start, 1)})
                    return
        except Exception:
            pass
        time.sleep(poll_interval)


def _format_bullets(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [title]
    for item in items:
        lines.append(f"- {item}")
    lines.append("")
    return lines


def _build_initial_teammate_message(
    agent_name: str,
    agent_type: str,
    team_name: str,
    task_description: str,
    spawn_spec: dict[str, Any],
) -> str:
    mission = spawn_spec.get("mission", "").strip()
    task_ids = spawn_spec.get("task_ids") or []
    owned_paths = spawn_spec.get("owned_paths") or []
    required_reads = spawn_spec.get("required_reads") or []
    deliverables = spawn_spec.get("deliverables") or []
    definition_of_done = spawn_spec.get("definition_of_done") or []
    quality_bar = spawn_spec.get("quality_bar") or []
    coordination_notes = spawn_spec.get("coordination_notes") or []
    startup_checklist = spawn_spec.get("startup_checklist") or []
    completion_report_template = spawn_spec.get("completion_report_template", "").strip()
    architect_execution_rules = [
        "## Architect Execution Rules",
        "- Your job is to unblock downstream implementation quickly with the minimum complete contract package.",
        "- Prefer concise documents with bullets, tables, route lists, schemas, and short examples.",
        "- Avoid long narrative prose, repeated explanations across files, and speculative future architecture.",
        "- Only create multiple contract docs when each one has a distinct purpose for downstream teammates.",
        "- Keep project-structure guidance limited to the files and directories that this project is likely to build now.",
        "",
    ] if agent_type == "architect" else []

    lines = [
        f"You have been spawned as teammate '{agent_name}' (type: {agent_type}) in team '{team_name}'.",
        "",
        "Treat the instructions below as your operating brief. Follow them literally unless the codebase proves they are impossible or incorrect.",
        "",
    ]
    if mission:
        lines.extend(["## Mission", mission, ""])
    if task_ids:
        lines.extend(["## Assigned Task IDs", ", ".join(task_ids), ""])
    if task_description:
        lines.extend(["## Task Context", task_description, ""])
    lines.extend(_format_bullets("## Owned Write Scope", owned_paths))
    lines.extend(_format_bullets("## Required Reads Before Acting", required_reads))
    lines.extend(_format_bullets("## Expected Deliverables", deliverables))
    lines.extend(_format_bullets("## Definition of Done", definition_of_done))
    lines.extend(_format_bullets("## Quality Bar", quality_bar))
    lines.extend(_format_bullets("## Coordination Notes", coordination_notes))
    lines.extend(_format_bullets("## Startup Checklist", startup_checklist))
    lines.extend(architect_execution_rules)
    if completion_report_template:
        lines.extend(["## Completion Report Template", completion_report_template, ""])
    lines.extend([
        "## Working Rules",
        "- New inbox messages are delivered to you automatically via [INBOX] when they arrive.",
        "- When a dependency completes, you will receive a [TASK READY] message. That is your signal to begin the dependent task.",
        "- Call task_get for the task IDs above before making implementation decisions if any detail is unclear.",
        "- Call task_update(status='in_progress') before you start changing files.",
        "- Call task_update(status='completed') immediately after your acceptance criteria are satisfied.",
        "- Send a completion report to team-lead that names the files changed, validation run, and any remaining risk.",
        "- If a contract, interface, or dependency is unclear, send_message to team-lead immediately instead of guessing.",
    ])
    return "\n".join(lines)


class AgentManager:
    """Manages the lifecycle of teammate agent processes."""

    def __init__(self, config: OpenTeamsConfig, team_name: str):
        self.config = config
        self.team_name = team_name
        self._processes: dict[str, multiprocessing.Process] = {}
        self._lock = threading.Lock()

    def spawn_agent(
        self,
        definition: AgentDefinition,
        task_description: str = "",
        spawn_spec: dict[str, Any] | None = None,
    ) -> str:
        """Spawn a new teammate agent in a separate process. Returns agent name."""
        name = definition.name
        model = definition.model or self.config.default_model

        config_dict = {
            "project_root": str(self.config.project_root),
            "workspace_dir": str(self.config.workspace_dir),
            "teams_dir": str(self.config.teams_dir),
            "tasks_dir": str(self.config.tasks_dir),
            "logs_dir": str(self.config.logs_dir),
            "inboxes_dir": str(self.config.inboxes_dir),
            "provider": self.config.provider,
            "default_model": self.config.default_model,
            "leader_model": self.config.leader_model,
            "api_key": self.config.api_key,
            "base_url": self.config.base_url,
            "max_tokens": self.config.max_tokens,
            "max_turns": self.config.max_turns,
            "max_agent_turns": definition.max_turns or self.config.max_agent_turns,
            "max_retries": self.config.max_retries,
            "max_context_tokens": self.config.max_context_tokens,
            "token_budget": self.config.token_budget,
            "temperature": self.config.temperature,
            "sandbox_enabled": self.config.sandbox_enabled,
            "sandbox_allowed_dirs": [str(p) for p in self.config.sandbox_allowed_dirs],
            "streaming": False,
        }

        from open_teams.coordination import TeamManager, Mailbox
        tm = TeamManager(self.config)
        tm.add_member(
            self.team_name,
            name=name,
            agent_id=generate_id("agent-"),
            agent_type=definition.agent_type,
            model=model,
        )

        mailbox = Mailbox(self.config, self.team_name)
        mailbox.create_inbox(name)

        p = multiprocessing.Process(
            target=_run_worker_process,
            args=(
                name,
                definition.agent_type,
                self.team_name,
                model,
                definition.max_turns or self.config.max_agent_turns,
                task_description,
                spawn_spec or {},
                config_dict,
                str(self.config.project_root),
            ),
            name=f"open-teams-{name}",
            daemon=True,
        )

        with self._lock:
            self._processes[name] = p
        p.start()

        return name

    def is_alive(self, agent_name: str) -> bool:
        with self._lock:
            proc = self._processes.get(agent_name)
            return proc is not None and proc.is_alive()

    def get_status(self) -> dict[str, str]:
        """Returns dict of agent_name -> status string."""
        with self._lock:
            status = {}
            for name, proc in self._processes.items():
                if proc.is_alive():
                    status[name] = "running"
                elif proc.exitcode is not None:
                    status[name] = f"exited ({proc.exitcode})"
                else:
                    status[name] = "unknown"
            return status

    def wait_for_agent(self, agent_name: str, timeout: float = None) -> bool:
        """Wait for a specific agent to finish. Returns True if finished."""
        with self._lock:
            proc = self._processes.get(agent_name)
        if proc is None:
            return True
        proc.join(timeout=timeout)
        return not proc.is_alive()

    def wait_for_all(self, timeout: float = None) -> bool:
        """Wait for all agents to finish."""
        deadline = time.monotonic() + timeout if timeout else None
        with self._lock:
            procs = list(self._processes.values())
        for p in procs:
            remaining = None
            if deadline:
                remaining = max(0, deadline - time.monotonic())
            p.join(timeout=remaining)
        return all(not p.is_alive() for p in procs)

    def terminate_agent(self, agent_name: str):
        """Terminate a specific agent process."""
        with self._lock:
            proc = self._processes.get(agent_name)
        if proc and proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()

    def terminate_all(self):
        """Terminate all agent processes (parallel SIGTERM, then unified join)."""
        with self._lock:
            procs = list(self._processes.values())
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=3)
            if p.is_alive():
                p.kill()

    def cleanup(self):
        """Clean up finished processes."""
        with self._lock:
            finished = [n for n, p in self._processes.items() if not p.is_alive()]
            for name in finished:
                del self._processes[name]

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._processes.values() if p.is_alive())
