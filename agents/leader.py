"""Team Leader agent - orchestrates the team from the main process."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from open_teams.config import OpenTeamsConfig
from open_teams.runtime.models import AgentIdentity
from open_teams.runtime.context import RuntimeContext
from open_teams.runtime.engine import QueryEngine
from open_teams.agents.definition import AgentDefinition
from open_teams.agents.manager import AgentManager
from open_teams.coordination import TaskBoard, Mailbox, TeamManager
from open_teams.tools import create_leader_tools
from open_teams.prompts import SystemPromptBuilder
from open_teams.logging import ActivityLogger
from open_teams.utils.helpers import generate_id


class TeamLeader:
    """The team leader agent that runs in the main process."""

    def __init__(self, config: OpenTeamsConfig, team_name: str = "default"):
        self.config = config
        self.team_name = team_name
        self.agent_name = "team-lead"
        self.model = config.leader_model

        self.logger = ActivityLogger(config, team_name, self.agent_name)
        self.logger.log_event("leader_init", {"team_name": team_name, "model": self.model})

        self.team_manager = TeamManager(config)
        self.task_board = TaskBoard(config, team_name)
        self.mailbox = Mailbox(config, team_name)
        self.agent_manager = AgentManager(config, team_name)

        self._setup_team()

        registry = create_leader_tools(config, team_name, self.agent_name)
        prompt_builder = SystemPromptBuilder(config)
        system_prompt = prompt_builder.build_leader_prompt(
            team_name=team_name,
            tool_names=registry.get_tool_names(),
            working_dir=str(config.project_root),
        )

        identity = AgentIdentity(
            agent_id=generate_id("leader-"),
            agent_name=self.agent_name,
            agent_type="leader",
            team_name=team_name,
            model=self.model,
        )

        self.context = RuntimeContext(
            agent_identity=identity,
            tools=registry.as_dict(),
            system_prompt=system_prompt,
            model=self.model,
            config=config,
            working_dir=config.project_root,
        )

        self.engine = QueryEngine(self.context, activity_logger=self.logger)
        self._original_execute_tools = self.engine.execute_tools

        def _hooked_execute_tools(tool_calls):
            results = self._original_execute_tools(tool_calls)
            for tc in tool_calls:
                if tc.name == "spawn_agent":
                    self._handle_spawn_request(tc.input)
            return results

        self.engine.execute_tools = _hooked_execute_tools

    def _setup_team(self):
        """Initialize team config and leader's inbox."""
        existing = self.team_manager.get_team(self.team_name)
        if not existing:
            self.team_manager.create_team(
                team_name=self.team_name,
                description="open-teams coding team",
                leader_name=self.agent_name,
                leader_id=generate_id("leader-"),
                leader_model=self.model,
            )
        self.mailbox.create_inbox(self.agent_name)

    def _handle_spawn_request(self, params: dict[str, Any]):
        """Handle a spawn_agent tool result by actually spawning the agent."""
        name = params.get("name", "")
        agent_type = params.get("agent_type", "coder")
        task_desc = params.get("task_description", "")
        model = params.get("model", "") or self.config.default_model

        if not name:
            return

        definition = AgentDefinition.teammate(
            name=name,
            agent_type=agent_type,
            model=model,
        )

        self.agent_manager.spawn_agent(definition, task_description=task_desc)
        self.logger.log_event("agent_spawned", {
            "name": name,
            "type": agent_type,
            "model": model,
        })

    def handle_user_message(self, message: str) -> str:
        """Process a user message through the leader's query loop."""
        self.logger.log_message("user", message)
        result = self.engine.run_loop(initial_message=message)
        self.logger.log_message("assistant", result)
        return result

    def handle_followup(self, message: str) -> str:
        """Handle follow-up messages in the ongoing conversation."""
        self.logger.log_message("user", message)
        self.context.add_user_message(message)
        result = self.engine.run_loop()
        self.logger.log_message("assistant", result)
        return result

    def get_team_status(self) -> dict[str, Any]:
        """Get current team status."""
        tasks = self.task_board.list_tasks()
        agents = self.agent_manager.get_status()
        members = self.team_manager.list_members(self.team_name)

        return {
            "team_name": self.team_name,
            "tasks": {
                "total": len(tasks),
                "pending": sum(1 for t in tasks if t["status"] == "pending"),
                "in_progress": sum(1 for t in tasks if t["status"] == "in_progress"),
                "completed": sum(1 for t in tasks if t["status"] == "completed"),
            },
            "agents": agents,
            "members": members,
        }

    def wait_for_completion(
        self,
        timeout: float = 600,
        poll_interval: float = 5,
        progress_callback: Any = None,
    ):
        """Wait for all teammate agents to complete, with health monitoring.

        Parameters
        ----------
        progress_callback:
            Optional callable receiving a dict with keys ``elapsed``,
            ``completed``, ``total``, ``active_agents`` every poll cycle.
        """
        start = time.monotonic()
        seen_exited: set[str] = set()

        while time.monotonic() - start < timeout:
            if self.agent_manager.active_count == 0:
                break

            # Read inbox messages (log them for visibility)
            messages = self.mailbox.read_inbox(self.agent_name, unread_only=True)
            if messages:
                self.mailbox.mark_as_read(self.agent_name)
                for msg in messages:
                    self.logger.log_event("inbox_message", {
                        "from": msg["from_agent"],
                        "summary": msg.get("summary", ""),
                        "content": msg["content"][:500],
                    })

            # Check for dead processes with orphaned tasks
            process_status = self.agent_manager.get_status()
            for name, status in process_status.items():
                if "exited" in status and name not in seen_exited:
                    seen_exited.add(name)
                    self._recover_orphaned_tasks(name, status)

            if progress_callback is not None:
                try:
                    tasks = self.task_board.list_tasks()
                    total = len(tasks)
                    completed = sum(1 for t in tasks if t["status"] == "completed")
                    active = self.agent_manager.active_count
                    elapsed = time.monotonic() - start
                    progress_callback({
                        "elapsed": elapsed,
                        "completed": completed,
                        "total": total,
                        "active_agents": active,
                    })
                except Exception:
                    pass

            time.sleep(poll_interval)

        # Final sweep after loop exits
        process_status = self.agent_manager.get_status()
        for name, status in process_status.items():
            if "exited" in status and name not in seen_exited:
                seen_exited.add(name)
                self._recover_orphaned_tasks(name, status)

    def _recover_orphaned_tasks(self, agent_name: str, exit_status: str):
        """Auto-mark in_progress tasks of a dead agent as blocked."""
        try:
            stuck = self.task_board.list_tasks(
                filter_status="in_progress", filter_owner=agent_name
            )
            if not stuck:
                return

            is_crash = "exited (0)" not in exit_status
            recovered_ids = []

            for task in stuck:
                try:
                    note = (
                        f"\n\n[AUTO-RECOVERY] Agent '{agent_name}' exited "
                        f"({'CRASH' if is_crash else 'normally'}, {exit_status}) "
                        f"while this task was in_progress. Marked blocked for reassignment."
                    )
                    self.task_board.update_task(
                        task["id"],
                        status="blocked",
                        description=task.get("description", "") + note,
                    )
                    recovered_ids.append(task["id"])
                    self.logger.log_event("task_auto_recovered", {
                        "task_id": task["id"],
                        "agent": agent_name,
                        "exit_status": exit_status,
                    })
                except Exception as exc:
                    self.logger.log_error(
                        f"Failed to recover task {task['id']}: {exc}",
                        {"agent": agent_name},
                    )

            if recovered_ids:
                self.context.add_user_message(
                    f"[PROCESS HEALTH ALERT] Agent '{agent_name}' exited ({exit_status}).\n"
                    f"Tasks auto-marked as blocked: {', '.join('Task ' + tid for tid in recovered_ids)}\n"
                    "Please review and reassign these tasks."
                )
        except Exception as exc:
            self.logger.log_error(
                f"Error in _recover_orphaned_tasks for '{agent_name}': {exc}", {}
            )

    def synthesize_results(self) -> str:
        """Collect completion reports and wake Leader LLM for final synthesis."""
        self.logger.log_event("synthesis_started")

        messages = self.mailbox.read_inbox(self.agent_name, unread_only=True)
        if messages:
            self.mailbox.mark_as_read(self.agent_name, [m["id"] for m in messages])

        tasks = self.task_board.list_tasks()
        prompt = self._build_synthesis_prompt(tasks, messages)
        self.context.add_user_message(prompt)

        result = self.engine.run_loop()
        self.logger.log_message("assistant", result)
        self.logger.log_event("synthesis_completed")
        return result

    def _build_synthesis_prompt(self, tasks: list[dict], messages: list[dict]) -> str:
        """Build the user-turn prompt that wakes Leader for final synthesis."""
        parts: list[str] = [
            "[SYSTEM] All teammate agents have completed their work. "
            "Time for final synthesis.\n"
        ]

        completed = [t for t in tasks if t["status"] == "completed"]
        in_prog = [t for t in tasks if t["status"] == "in_progress"]
        blocked = [t for t in tasks if t["status"] == "blocked"]
        pending = [t for t in tasks if t["status"] == "pending"]

        parts.append(f"## Task Summary ({len(completed)}/{len(tasks)} completed)")
        if completed:
            for t in completed:
                parts.append(f"  [DONE] #{t['id']} {t['subject']} (owner: {t.get('owner', '?')})")
        if in_prog:
            for t in in_prog:
                parts.append(f"  [IN PROGRESS] #{t['id']} {t['subject']} (owner: {t.get('owner', '?')})")
        if blocked:
            for t in blocked:
                parts.append(f"  [BLOCKED] #{t['id']} {t['subject']} (owner: {t.get('owner', '?')})")
        if pending:
            for t in pending:
                parts.append(f"  [PENDING] #{t['id']} {t['subject']}")

        if messages:
            parts.append(f"\n## Completion Reports ({len(messages)} messages)")
            for m in messages:
                sender = m.get("from_agent", "unknown")
                summary = m.get("summary", "")
                content = m.get("content", "")[:500]
                header = f"[From {sender}]"
                if summary:
                    header += f" ({summary})"
                parts.append(f"\n{header}\n{content}")

        parts.append(
            "\n## Your Action\n"
            "1. Read the key output files produced by the team using read_file.\n"
            "2. Run any final validation if appropriate (e.g., shell to run tests).\n"
            "3. Present a comprehensive final report to the user:\n"
            "   - Summary of what was built\n"
            "   - List of files created/modified\n"
            "   - Test results if available\n"
            "   - Known limitations or next steps"
        )

        return "\n".join(parts)

    def shutdown(self):
        """Gracefully shut down the team."""
        self.logger.log_event("shutdown_initiated")
        self.agent_manager.terminate_all()
        self.logger.log_event("shutdown_complete")
