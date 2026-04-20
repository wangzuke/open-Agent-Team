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
    config_dict: dict[str, Any],
    working_dir: str,
):
    """Entry point for a teammate worker process."""
    import os
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
        "task_description": task_description,
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
    )

    engine = QueryEngine(context, activity_logger=logger)

    initial_msg = (
        f"You have been spawned as teammate '{agent_name}' (type: {agent_type}) "
        f"in team '{team_name}'.\n\n"
        f"Your initial task description:\n{task_description}\n\n"
        "Start by checking the task board with task_list to see your assigned tasks. "
        "If no tasks are assigned to you yet, check for available tasks you can claim."
    )

    try:
        result = engine.run_loop(initial_message=initial_msg)
        logger.log_event("agent_completed", {"final_output": result[:2000]})
    except Exception as e:
        logger.log_error(f"Agent crashed: {e}", {"traceback": traceback.format_exc()})

    mailbox = Mailbox(config, team_name)
    try:
        mailbox.send_message(
            from_agent=agent_name,
            to_agent="team-lead",
            content=f"Agent '{agent_name}' has finished all work and is shutting down.",
            summary=f"{agent_name} completed",
        )
    except Exception:
        pass

    logger.log_event("agent_shutdown")


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
    ) -> str:
        """Spawn a new teammate agent in a separate process. Returns agent name."""
        name = definition.name
        model = definition.model or self.config.default_model

        config_dict = {
            "project_root": str(self.config.project_root),
            "workspace_dir": str(self.config.workspace_dir),
            "session_dir": str(self.config.session_dir) if self.config.session_dir else None,
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
            "max_agent_turns": definition.max_turns,
            "temperature": self.config.temperature,
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
                definition.max_turns,
                task_description,
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
        """Terminate all agent processes."""
        with self._lock:
            names = list(self._processes.keys())
        for name in names:
            self.terminate_agent(name)

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
