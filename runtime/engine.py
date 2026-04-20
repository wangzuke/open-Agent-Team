"""QueryEngine: the core LLM request-response loop."""

from __future__ import annotations

import logging
import time
from typing import Any

from .context import RuntimeContext
from .llm_client import LLMClient, LLMResponse, create_llm_client
from .models import QueryResult, ToolCall

_DEFAULT_LOGGER = logging.getLogger(__name__)

_WARN_TURNS_REMAINING = 3


class QueryEngine:
    """Drives the LLM API call loop for a single :class:`RuntimeContext`.

    Supports both Anthropic and OpenAI-compatible providers through the
    :class:`~open_teams.runtime.llm_client.LLMClient` abstraction.

    Parameters
    ----------
    context:
        The :class:`RuntimeContext` that owns this engine.
    logger:
        Optional standard-library logger for debug output.
    activity_logger:
        Optional :class:`~open_teams.logging.ActivityLogger` for structured
        activity logging (tool calls, errors, etc.).
    llm_client:
        Pre-built LLM client.  When *None* one is created automatically from
        ``context.config.provider / api_key / base_url``.
    """

    def __init__(
        self,
        context: RuntimeContext,
        logger: Any = None,
        activity_logger: Any = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.context = context
        self.logger: logging.Logger = (
            logger if isinstance(logger, logging.Logger) else _DEFAULT_LOGGER
        )
        self.activity_logger = activity_logger or (
            logger if not isinstance(logger, logging.Logger) else None
        )
        self._last_inbox_check: float = 0.0
        self._inbox_check_interval: float = 10.0

        if llm_client is not None:
            self.client = llm_client
        else:
            self.client = create_llm_client(
                provider=context.config.provider,
                api_key=context.config.api_key,
                base_url=context.config.base_url,
            )

    # ------------------------------------------------------------------
    # Single turn
    # ------------------------------------------------------------------

    def run_turn(self) -> QueryResult:
        """Send the current message history to the LLM and parse the reply."""
        ctx = self.context
        try:
            resp: LLMResponse = self.client.create_message(
                model=ctx.model,
                system=ctx.system_prompt,
                messages=ctx.get_api_messages(),
                tools=ctx.get_tool_schemas(),
                max_tokens=ctx.config.max_tokens,
                temperature=ctx.config.temperature,
            )
        except Exception as exc:
            self.logger.error("LLM API error: %s", exc)
            raise

        text_output = "\n".join(resp.text_parts)
        tool_calls = [
            ToolCall(id=tc["id"], name=tc["name"], input=tc.get("input", {}))
            for tc in resp.tool_calls
        ]

        self.logger.debug(
            "run_turn: stop_reason=%s tool_calls=%d text_len=%d",
            resp.stop_reason,
            len(tool_calls),
            len(text_output),
        )

        return QueryResult(
            text_output=text_output,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            usage=resp.usage,
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def execute_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Execute tool calls and return Anthropic-format tool_result dicts."""
        results: list[dict[str, Any]] = []

        for tc in tool_calls:
            tool = self.context.tools.get(tc.name)

            if tool is None:
                error_msg = f"Tool '{tc.name}' not found in context."
                self.logger.warning(error_msg)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": error_msg,
                    "is_error": True,
                })
                continue

            self.logger.debug("Executing tool '%s' with input: %s", tc.name, tc.input)
            t0 = time.monotonic()
            try:
                output = tool.execute(tc.input)
                elapsed = (time.monotonic() - t0) * 1000
                self.logger.debug("Tool '%s' returned: %s", tc.name, str(output)[:200])
                if self.activity_logger:
                    self.activity_logger.log_tool_call(
                        tc.name, tc.input,
                        output if isinstance(output, str) else str(output),
                        elapsed,
                    )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": output if isinstance(output, str) else str(output),
                })
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                error_msg = f"Tool '{tc.name}' raised an exception: {exc}"
                self.logger.error(error_msg, exc_info=True)
                if self.activity_logger:
                    self.activity_logger.log_error(
                        error_msg, {"tool": tc.name, "input": tc.input}
                    )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": error_msg,
                    "is_error": True,
                })

        return results

    # ------------------------------------------------------------------
    # Automatic inbox injection
    # ------------------------------------------------------------------

    def _check_and_inject_inbox(self) -> str | None:
        """Check the agent's inbox and return formatted unread messages.

        Returns *None* when there are no new messages or when the throttle
        interval has not elapsed since the last check.  Failures are
        silently swallowed so they never break the main loop.
        """
        now = time.monotonic()
        if now - self._last_inbox_check < self._inbox_check_interval:
            return None
        self._last_inbox_check = now

        ctx = self.context
        team_name = ctx.agent_identity.team_name
        agent_name = ctx.agent_identity.agent_name
        if not team_name:
            return None

        try:
            from open_teams.coordination.mailbox import Mailbox

            mailbox = Mailbox(ctx.config, team_name)
            messages = mailbox.read_inbox(agent_name, unread_only=True)
            if not messages:
                return None

            msg_ids = [m["id"] for m in messages]
            mailbox.mark_as_read(agent_name, msg_ids)

            lines = [f"[INBOX] You have {len(messages)} new message(s):"]
            for m in messages:
                sender = m.get("from_agent", "unknown")
                summary = m.get("summary", "")
                content = m.get("content", "")
                header = f"[From {sender}]"
                if summary:
                    header += f" ({summary})"
                lines.append(f"\n{header}\n{content}")

            if self.activity_logger:
                self.activity_logger.log_event(
                    "inbox_injected",
                    {"count": len(messages), "from": [m.get("from_agent") for m in messages]},
                )

            return "\n".join(lines)
        except Exception as exc:
            self.logger.debug("_check_and_inject_inbox failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Main agentic loop
    # ------------------------------------------------------------------

    def run_loop(self, initial_message: str | None = None) -> str:
        """Run the agent loop until the model stops or the turn limit is hit.

        1. Optionally prepends *initial_message* as a user turn.
        2. Calls :meth:`run_turn` to get the next model response.
        3. Builds the assistant content block list and appends it to context.
        4. If the model called tools, executes them and feeds results back.
        5. Repeats until ``stop_reason == "end_turn"`` with no pending tool
           calls, or until ``max_agent_turns`` is reached.
        """
        ctx = self.context
        max_turns: int = ctx.config.max_agent_turns

        if initial_message is not None:
            ctx.add_user_message(initial_message)

        last_text: str = ""
        warning_injected: bool = False

        for turn_index in range(max_turns):
            if ctx.abort_event.is_set():
                self.logger.info("Abort event set — stopping run_loop.")
                break

            turns_remaining = max_turns - turn_index
            if turns_remaining <= _WARN_TURNS_REMAINING and not warning_injected:
                warning_injected = True
                warn_msg = (
                    f"[SYSTEM WARNING] You have only {turns_remaining} turn(s) remaining "
                    f"before this agent session ends automatically.\n"
                    "IMMEDIATE ACTIONS REQUIRED:\n"
                    "1. Call task_update(status=\"completed\") for any finished tasks NOW.\n"
                    "2. Call check_inbox to read any pending messages.\n"
                    "3. Call send_message to team-lead with a final status summary.\n"
                    "4. If a task cannot be finished, call task_update(status=\"blocked\")."
                )
                ctx.add_user_message(warn_msg)
                self.logger.warning(
                    "Agent '%s' — injecting turn-limit warning at turn %d/%d.",
                    ctx.agent_identity.agent_name,
                    turn_index + 1,
                    max_turns,
                )

            self.logger.info(
                "Agent '%s' — turn %d/%d",
                ctx.agent_identity.agent_name,
                turn_index + 1,
                max_turns,
            )

            inbox_text = self._check_and_inject_inbox()
            if inbox_text:
                ctx.add_user_message(inbox_text)

            result = self.run_turn()
            last_text = result.text_output

            assistant_content: list[dict[str, Any]] = []
            if result.text_output:
                assistant_content.append({"type": "text", "text": result.text_output})
            for tc in result.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })

            if assistant_content:
                ctx.add_assistant_message(assistant_content)
            else:
                ctx.add_assistant_message("")

            if result.stop_reason == "end_turn" and not result.tool_calls:
                self.logger.info(
                    "Agent '%s' finished after %d turn(s).",
                    ctx.agent_identity.agent_name,
                    turn_index + 1,
                )
                return result.text_output

            if result.tool_calls:
                tool_results = self.execute_tools(result.tool_calls)
                ctx.add_tool_results(tool_results)

        self.logger.warning(
            "Agent '%s' reached max turns (%d) without a clean end_turn.",
            ctx.agent_identity.agent_name,
            max_turns,
        )
        self._handle_max_turns_exceeded()
        return last_text

    def _handle_max_turns_exceeded(self) -> None:
        """Auto-complete or block orphaned tasks and notify team-lead."""
        ctx = self.context
        agent_name = ctx.agent_identity.agent_name
        team_name = ctx.agent_identity.team_name

        if not team_name:
            return

        try:
            from open_teams.coordination.task_board import TaskBoard
            from open_teams.coordination.mailbox import Mailbox

            board = TaskBoard(ctx.config, team_name)
            stuck_tasks = board.list_tasks(
                filter_status="in_progress", filter_owner=agent_name
            )

            completed_ids: list[str] = []
            blocked_ids: list[str] = []

            for task in stuck_tasks:
                try:
                    if self._task_output_exists(task):
                        board.update_task(task["id"], status="completed")
                        completed_ids.append(task["id"])
                    else:
                        board.update_task(
                            task["id"],
                            status="blocked",
                            description=(
                                task.get("description", "")
                                + f"\n\n[AUTO-BLOCKED] Agent '{agent_name}' exhausted "
                                f"max_turns without completing this task."
                            ),
                        )
                        blocked_ids.append(task["id"])
                except Exception as exc:
                    self.logger.error(
                        "Failed to update stuck task %s: %s", task["id"], exc
                    )

            parts: list[str] = [
                f"Agent '{agent_name}' exhausted its turn limit and is shutting down."
            ]
            if completed_ids:
                parts.append(
                    "Tasks auto-completed (output files found):\n"
                    + "\n".join(f"  - Task {tid}" for tid in completed_ids)
                )
            if blocked_ids:
                parts.append(
                    "Tasks auto-blocked (incomplete):\n"
                    + "\n".join(f"  - Task {tid}" for tid in blocked_ids)
                )
            if not completed_ids and not blocked_ids:
                parts.append("No in_progress tasks found.")

            mailbox = Mailbox(ctx.config, team_name)
            mailbox.send_message(
                from_agent=agent_name,
                to_agent="team-lead",
                content="\n\n".join(parts),
                summary=(
                    f"{agent_name} done — "
                    f"{len(completed_ids)} completed, {len(blocked_ids)} blocked"
                ),
            )

        except Exception as exc:
            self.logger.error(
                "Error in _handle_max_turns_exceeded for '%s': %s",
                agent_name, exc,
            )

    @staticmethod
    def _task_output_exists(task: dict) -> bool:
        """Heuristic: check if files mentioned in the task description exist."""
        import re
        from pathlib import Path

        desc = task.get("description", "")
        patterns = re.findall(
            r'(?:^|\s)(\w[\w/\\.-]*\.(?:py|ts|js|json|yaml|yml|toml|txt|md|html|css))\b',
            desc,
        )
        if not patterns:
            return False

        found = 0
        for p in patterns:
            if Path(p).exists():
                found += 1

        return found > 0 and found >= len(patterns) * 0.5
