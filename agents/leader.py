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
            max_turns=config.max_turns,
        )

        self.engine = QueryEngine(self.context, activity_logger=self.logger)
        self._collected_inbox_messages: list[dict[str, Any]] = []
        self._original_execute_tools = self.engine.execute_tools

        def _hooked_execute_tools(tool_calls):
            results = self._original_execute_tools(tool_calls)
            spawned_any = False
            for tc in tool_calls:
                if tc.name == "spawn_agent":
                    spawned_any = True
                    self._handle_spawn_request(tc.input)
            if spawned_any and self._dispatch_is_complete():
                self.engine.request_early_exit(
                    "Team dispatched. Waiting for teammate progress updates."
                )
            elif spawned_any:
                self.logger.log_event(
                    "dispatch_incomplete",
                    {"unassigned_task_ids": self._get_unassigned_active_task_ids()},
                )
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
        task_desc = params.get("task_description", "") or params.get("mission", "")
        model = params.get("model", "") or self.config.default_model
        task_ids = self.task_board.normalize_task_ref_list(params.get("task_ids") or [])
        spawn_spec = dict(params)
        spawn_spec["task_ids"] = task_ids

        if not name:
            return

        definition = AgentDefinition.teammate(
            name=name,
            agent_type=agent_type,
            model=model,
        )

        updates = self._assign_spawned_tasks(name, agent_type, task_ids)
        try:
            self.agent_manager.spawn_agent(
                definition,
                task_description=task_desc,
                spawn_spec=spawn_spec,
            )
            self.logger.log_event("agent_spawned", {
                "name": name,
                "type": agent_type,
                "model": model,
                "task_ids": task_ids,
            })
        except Exception:
            self._rollback_spawned_task_assignments(updates)
            raise

    def _assign_spawned_tasks(
        self,
        agent_name: str,
        agent_type: str,
        task_ids: list[str],
    ) -> list[dict[str, str | None]]:
        """Assign spawned tasks to the new agent before the worker starts polling."""
        updates: list[dict[str, str | None]] = []
        for task_id in task_ids:
            normalized_task_id = self.task_board.normalize_task_ref(task_id)
            task = self.task_board.get_task(normalized_task_id)
            if task is None:
                self.logger.log_error(
                    f"spawn_agent referenced missing task '{normalized_task_id}'",
                    {"agent": agent_name, "agent_type": agent_type},
                )
                continue

            owner = task.get("owner")
            task_role = task.get("agentType")
            if owner and owner != agent_name:
                self.logger.log_error(
                    f"Task '{normalized_task_id}' is already owned by '{owner}', cannot assign to '{agent_name}'.",
                    {"agent_type": agent_type},
                )
                continue

            changes: dict[str, Any] = {}
            if owner != agent_name:
                changes["owner"] = agent_name
            if not task_role:
                changes["agentType"] = agent_type
            if not changes:
                continue

            updates.append({
                "task_id": normalized_task_id,
                "previous_owner": owner,
                "previous_agent_type": task_role,
            })
            self.task_board.update_task(normalized_task_id, **changes)

        return updates

    def _rollback_spawned_task_assignments(
        self,
        updates: list[dict[str, str | None]],
    ) -> None:
        for item in updates:
            task_id = item["task_id"] or ""
            restore: dict[str, Any] = {"owner": item.get("previous_owner")}
            previous_agent_type = item.get("previous_agent_type")
            if previous_agent_type:
                restore["agentType"] = previous_agent_type
            else:
                task = self.task_board.get_task(task_id)
                if task and "agentType" in task:
                    restore["agentType"] = None
            try:
                self.task_board.update_task(task_id, **restore)
            except Exception as exc:
                self.logger.log_error(
                    f"Failed to rollback task assignment for '{task_id}': {exc}",
                    {},
                )

    def _get_unassigned_active_task_ids(self) -> list[str]:
        """Return unfinished task IDs that still have no owner assigned."""
        unassigned: list[str] = []
        for task in self.task_board.list_tasks():
            if task.get("status") == "completed":
                continue
            owner = str(task.get("owner") or "").strip()
            if owner:
                continue
            task_id = str(task.get("id") or "").strip()
            if task_id:
                unassigned.append(task_id)
        return unassigned

    def _dispatch_is_complete(self) -> bool:
        """Return True when every unfinished task has been assigned an owner."""
        return not self._get_unassigned_active_task_ids()

    def _get_available_unassigned_task_ids(self) -> list[str]:
        """Return unassigned pending tasks whose dependencies are already satisfied."""
        return [
            str(task.get("id"))
            for task in self.task_board.get_available_tasks()
            if task.get("id")
        ]

    def _build_dispatch_guard_prompt(self, task_ids: list[str]) -> str:
        task_refs = ", ".join(f"Task {task_id}" for task_id in task_ids)
        return (
            "[SYSTEM ORCHESTRATION RULE]\n"
            f"You still have unfinished tasks with no owner assigned: {task_refs}.\n"
            "Before ending your turn, you MUST do one of the following for each such task:\n"
            "1. spawn_agent with task_ids covering that task so the runtime can assign ownership automatically, or\n"
            "2. explicitly update/restructure the task board so the task no longer exists as unassigned work.\n"
            "Blocked tasks may still be assigned and spawned now; teammate workers will wait until dependencies clear.\n"
            "Do not poll progress or inspect files right now. Finish dispatch first."
        )

    def _run_until_dispatch_stable(
        self,
        initial_message: str | list[dict[str, Any]] | None = None,
        stream_callback: Any = None,
        max_attempts: int = 4,
    ) -> str:
        """Run the leader loop until there are no unfinished unassigned tasks."""
        next_message = initial_message
        last_result = ""
        for attempt in range(1, max_attempts + 1):
            last_result = self.engine.run_loop(
                initial_message=next_message,
                stream_callback=stream_callback,
            )
            unassigned = self._get_unassigned_active_task_ids()
            if not unassigned:
                return last_result

            self.logger.log_event(
                "dispatch_guard_retry",
                {"attempt": attempt, "unassigned_task_ids": unassigned},
            )
            next_message = self._build_dispatch_guard_prompt(unassigned)

        self.logger.log_event(
            "dispatch_guard_unresolved",
            {"unassigned_task_ids": self._get_unassigned_active_task_ids()},
        )
        return last_result

    def _resume_dispatch_for_available_tasks(self, task_ids: list[str]) -> str:
        """Wake the leader to assign/spawn owners for newly actionable tasks."""
        prompt = (
            "[TEAM EVENT]\n"
            f"The following tasks are now actionable and still unassigned: {', '.join('Task ' + task_id for task_id in task_ids)}.\n"
            "Assign owners and spawn the corresponding teammates now. "
            "Do not wait or poll progress before dispatch is complete."
        )
        self.logger.log_event("dispatch_resume_requested", {"task_ids": task_ids})
        result = self._run_until_dispatch_stable(initial_message=prompt)
        self.logger.log_message("assistant", result)
        return result

    def handle_user_message(self, message: str, stream_callback: Any = None) -> str:
        """Process a user message through the leader's query loop."""
        self.logger.log_message("user", message)
        result = self._run_until_dispatch_stable(
            initial_message=message,
            stream_callback=stream_callback,
        )
        self.logger.log_message("assistant", result)
        return result

    def handle_followup(self, message: str, stream_callback: Any = None) -> str:
        """Handle follow-up messages in the ongoing conversation."""
        self.logger.log_message("user", message)
        result = self._run_until_dispatch_stable(
            initial_message=message,
            stream_callback=stream_callback,
        )
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

    def _remember_inbox_messages(self, messages: list[dict[str, Any]]) -> None:
        """Keep consumed inbox messages available for final synthesis."""
        if not messages:
            return

        seen_ids = {
            str(message.get("id") or "").strip()
            for message in self._collected_inbox_messages
            if str(message.get("id") or "").strip()
        }
        for message in messages:
            message_id = str(message.get("id") or "").strip()
            if message_id and message_id in seen_ids:
                continue
            self._collected_inbox_messages.append(message)
            if message_id:
                seen_ids.add(message_id)

    def _drain_leader_inbox(self) -> list[dict[str, Any]]:
        """Collect and remember leader inbox messages that were just consumed."""
        messages = self.engine.collect_pending_inbox_messages()
        self._remember_inbox_messages(messages)
        return messages

    def wait_for_completion(
        self,
        timeout: float = 600,
        poll_interval: float = 5,
        progress_callback: Any = None,
    ) -> bool:
        """Wait for all teammate agents to complete, with health monitoring.

        Parameters
        ----------
        progress_callback:
            Optional callable receiving a dict with keys ``elapsed``,
            ``completed``, ``total``, ``active_agents`` every poll cycle.

        Returns
        -------
        bool
            True if all tasks are completed when the wait loop ends, else False.
        """
        start = time.monotonic()
        seen_exited: set[str] = set()
        seen_inbox_ids: set[str] = set()
        seen_available_unassigned: set[str] = set()
        stall_state: dict[str, dict[str, float | str]] = {}
        stall_threshold = max(poll_interval * 12, 180.0)
        nudge_cooldown = max(poll_interval * 24, 300.0)

        while time.monotonic() - start < timeout:
            try:
                tasks = self.task_board.list_tasks()
                total = len(tasks)
                completed = sum(1 for t in tasks if t["status"] == "completed")
                all_completed = total > 0 and completed == total
            except Exception:
                tasks = []
                total = 0
                completed = 0
                all_completed = False

            try:
                available_unassigned_ids = set(self._get_available_unassigned_task_ids())
            except Exception:
                available_unassigned_ids = set()

            if all_completed:
                break

            # Read inbox messages (log them for visibility)
            messages = self._drain_leader_inbox()
            if messages:
                for msg in messages:
                    message_id = msg.get("id")
                    if message_id in seen_inbox_ids:
                        continue
                    if message_id:
                        seen_inbox_ids.add(message_id)
                    self.logger.log_event("inbox_message", {
                        "from": msg["from_agent"],
                        "summary": msg.get("summary", ""),
                        "content": msg["content"][:500],
                    })

            fresh_available = sorted(available_unassigned_ids - seen_available_unassigned)
            if fresh_available:
                try:
                    self._resume_dispatch_for_available_tasks(fresh_available)
                except Exception as exc:
                    self.logger.log_error(
                        f"Failed to resume dispatch for available tasks {fresh_available}: {exc}",
                        {},
                    )
                finally:
                    seen_available_unassigned.difference_update(fresh_available)
                continue
            seen_available_unassigned.intersection_update(available_unassigned_ids)

            if self.agent_manager.active_count == 0:
                break

            # Check for dead processes with orphaned tasks
            process_status = self.agent_manager.get_status()
            for name, status in process_status.items():
                if "exited" in status and name not in seen_exited:
                    seen_exited.add(name)
                    self._recover_orphaned_tasks(name, status)

            self._nudge_stalled_tasks(stall_state, stall_threshold, nudge_cooldown)

            if progress_callback is not None:
                try:
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

        try:
            tasks = self.task_board.list_tasks()
            return bool(tasks) and all(t["status"] == "completed" for t in tasks)
        except Exception:
            return False

    def _nudge_stalled_tasks(
        self,
        stall_state: dict[str, dict[str, float | str]],
        stall_threshold: float,
        nudge_cooldown: float,
    ) -> None:
        """Detect long-idle in-progress tasks and send a status-check nudge."""
        now = time.monotonic()
        try:
            in_progress = self.task_board.list_tasks(filter_status="in_progress")
        except Exception:
            return

        active_ids = {task["id"] for task in in_progress}
        for task_id in list(stall_state):
            if task_id not in active_ids:
                stall_state.pop(task_id, None)

        for task in in_progress:
            task_id = task["id"]
            signature = "|".join(
                [
                    str(task.get("status", "")),
                    str(task.get("owner", "")),
                    str(task.get("updatedAt", "")),
                ]
            )
            state = stall_state.get(task_id)
            if state is None or state.get("signature") != signature:
                stall_state[task_id] = {
                    "signature": signature,
                    "first_seen": now,
                    "last_nudged": 0.0,
                }
                continue

            first_seen = float(state.get("first_seen", now))
            last_nudged = float(state.get("last_nudged", 0.0))
            owner = task.get("owner") or ""
            if (
                owner
                and now - first_seen >= stall_threshold
                and now - last_nudged >= nudge_cooldown
            ):
                try:
                    self.mailbox.send_message(
                        from_agent=self.agent_name,
                        to_agent=owner,
                        summary=f"Status check for Task #{task_id}",
                        content=(
                            f"[STATUS CHECK] Task #{task_id} '{task.get('subject', '')}' "
                            f"has shown no task-board progress for about {int(now - first_seen)} seconds.\n"
                            "If you are done, call task_update(status='completed') immediately.\n"
                            "If you are blocked, send_message to team-lead and mark the task blocked."
                        ),
                    )
                    state["last_nudged"] = now
                    self.logger.log_event(
                        "task_stall_nudged",
                        {"task_id": task_id, "owner": owner, "idle_seconds": int(now - first_seen)},
                    )
                except Exception:
                    pass

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

    def synthesize_results(self, stream_callback: Any = None) -> str:
        """Collect completion reports and wake Leader LLM for final synthesis."""
        self.logger.log_event("synthesis_started")

        self._drain_leader_inbox()
        tasks = self.task_board.list_tasks()
        messages = list(self._collected_inbox_messages)
        prompt = self._build_synthesis_prompt(tasks, messages)
        self.context.add_user_message(prompt)

        result = self.engine.run_loop(stream_callback=stream_callback)
        self._collected_inbox_messages = []
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
