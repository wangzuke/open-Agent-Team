"""QueryEngine: the core LLM request-response loop."""

from __future__ import annotations

import logging
import queue
import threading
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
    :class:`~runtime.llm_client.LLMClient` abstraction.

    Parameters
    ----------
    context:
        The :class:`RuntimeContext` that owns this engine.
    logger:
        Optional standard-library logger for debug output.
    activity_logger:
        Optional :class:`~team_logging.ActivityLogger` for structured
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
        stream_callback: Any = None,
    ) -> None:
        self.context = context
        self.logger: logging.Logger = (
            logger if isinstance(logger, logging.Logger) else _DEFAULT_LOGGER
        )
        self.activity_logger = activity_logger or (
            logger if not isinstance(logger, logging.Logger) else None
        )
        self.stream_callback = stream_callback
        self._inbox_queue: queue.Queue = queue.Queue()
        self._queued_inbox_ids: set[str] = set()
        self._inbox_poller_started: bool = False
        self._tool_log_counter: int = 0
        self._early_exit_requested: bool = False
        self._early_exit_message: str = ""

        if llm_client is not None:
            self.client = llm_client
        else:
            self.client = create_llm_client(
                provider=context.config.provider,
                api_key=context.config.api_key,
                base_url=context.config.base_url,
                max_retries=context.config.max_retries,
            )

    # ------------------------------------------------------------------
    # Single turn
    # ------------------------------------------------------------------

    def run_turn(self, stream_callback: Any = None) -> QueryResult:
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
                stream=bool(ctx.config.streaming and (stream_callback or self.stream_callback)),
                stream_handler=stream_callback or self.stream_callback,
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
            self._tool_log_counter += 1
            log_call_id = self._tool_log_counter
            tool = self.context.tools.get(tc.name)

            if tool is None:
                error_msg = f"Tool '{tc.name}' not found in context."
                self.logger.warning(error_msg)
                if self.activity_logger:
                    self.activity_logger.log_tool_call(
                        tc.name,
                        tc.input,
                        error_msg,
                        0,
                        status="not_found",
                        tool_call_id=log_call_id,
                    )
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
                        status="ok",
                        tool_call_id=log_call_id,
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
                    self.activity_logger.log_tool_call(
                        tc.name,
                        tc.input,
                        error_msg,
                        elapsed,
                        status="error",
                        tool_call_id=log_call_id,
                    )
                    self.activity_logger.log_error(
                        error_msg, {"tool": tc.name, "input": tc.input, "tool_use_id": tc.id}
                    )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": error_msg,
                    "is_error": True,
                })

        return results

    def request_early_exit(self, message: str = "") -> None:
        """Stop the current run loop after the active tool batch finishes."""
        self._early_exit_requested = True
        self._early_exit_message = message

    def _consume_early_exit(self) -> str:
        """Consume and clear any pending early-exit request."""
        message = self._early_exit_message
        self._early_exit_requested = False
        self._early_exit_message = ""
        return message

    # ------------------------------------------------------------------
    # Background inbox polling
    # ------------------------------------------------------------------

    def _start_inbox_poller(self) -> None:
        """Start the background inbox polling thread (idempotent)."""
        if self._inbox_poller_started:
            return
        ctx = self.context
        if not ctx.agent_identity.team_name:
            return
        self._inbox_poller_started = True
        t = threading.Thread(target=self._inbox_poll_loop, daemon=True)
        t.start()

    def _inbox_poll_loop(self) -> None:
        """Background thread: poll inbox every 1 s and queue unread messages."""
        ctx = self.context
        team_name = ctx.agent_identity.team_name
        agent_name = ctx.agent_identity.agent_name

        while not ctx.abort_event.is_set():
            try:
                from coordination.mailbox import Mailbox

                mailbox = Mailbox(ctx.config, team_name)
                messages = mailbox.read_inbox(agent_name, unread_only=True)
                fresh_messages = [
                    m for m in messages if m.get("id") and m["id"] not in self._queued_inbox_ids
                ]
                if fresh_messages:
                    for m in fresh_messages:
                        self._queued_inbox_ids.add(m["id"])
                        self._inbox_queue.put(m)
                    if self.activity_logger:
                        self.activity_logger.log_event(
                            "inbox_polled",
                            {
                                "count": len(fresh_messages),
                                "from": [m.get("from_agent") for m in fresh_messages],
                            },
                        )
            except Exception as exc:
                self.logger.debug("_inbox_poll_loop error: %s", exc)

            ctx.abort_event.wait(1.0)

    def _drain_inbox_queue(self) -> list[dict]:
        """Drain queued inbox messages for delivery at the next safe point."""
        messages: list[dict] = []
        while True:
            try:
                messages.append(self._inbox_queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def _dedupe_inbox_messages(self, messages: list[dict]) -> list[dict]:
        """Deduplicate inbox messages while preserving order."""
        deduped: list[dict] = []
        seen_ids: set[str] = set()
        for message in messages:
            message_id = str(message.get("id") or "").strip()
            if message_id:
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
            deduped.append(message)
        return deduped

    def _read_unread_inbox_messages(self) -> list[dict]:
        """Read unread inbox messages directly from the mailbox."""
        team_name = self.context.agent_identity.team_name
        agent_name = self.context.agent_identity.agent_name
        if not team_name:
            return []
        try:
            from coordination.mailbox import Mailbox

            mailbox = Mailbox(self.context.config, team_name)
            return mailbox.read_inbox(agent_name, unread_only=True)
        except Exception as exc:
            self.logger.debug("_read_unread_inbox_messages error: %s", exc)
            return []

    def _format_inbox_messages(self, messages: list[dict]) -> str:
        """Format full inbox messages for persistent context injection."""
        lines = [f"[INBOX]\nYou have {len(messages)} new message(s)."]
        for index, message in enumerate(messages, start=1):
            lines.append("")
            lines.append(f"--- MESSAGE {index} ---")
            lines.append(f"from: {message.get('from_agent', 'unknown')}")
            lines.append(f"summary: {message.get('summary', '')}")
            lines.append("content:")
            lines.append(str(message.get("content", "")))
        return "\n".join(lines)

    def _mark_inbox_messages_read(self, messages: list[dict]) -> None:
        """Mark delivered inbox messages as read and clear local queue tracking."""
        if not messages:
            return
        agent_name = self.context.agent_identity.agent_name
        team_name = self.context.agent_identity.team_name
        message_ids = [m["id"] for m in messages if m.get("id")]
        for message_id in message_ids:
            self._queued_inbox_ids.discard(message_id)
        if not team_name or not message_ids:
            return
        try:
            from coordination.mailbox import Mailbox

            mailbox = Mailbox(self.context.config, team_name)
            mailbox.mark_as_read(agent_name, message_ids)
        except Exception as exc:
            self.logger.debug("_mark_inbox_messages_read error: %s", exc)

    def _requeue_inbox_messages(self, messages: list[dict]) -> None:
        """Requeue inbox messages if a turn fails before they can be consumed."""
        for message in reversed(messages):
            message_id = message.get("id")
            if message_id:
                self._queued_inbox_ids.discard(message_id)
            self._inbox_queue.put(message)

    def collect_pending_inbox_messages(self) -> list[dict]:
        """Collect unread inbox messages, mark them read, and return them."""
        messages = self._drain_inbox_queue()
        messages.extend(self._read_unread_inbox_messages())
        messages = self._dedupe_inbox_messages(messages)
        if not messages:
            return []

        self._mark_inbox_messages_read(messages)
        if self.activity_logger:
            self.activity_logger.log_event(
                "inbox_collected",
                {
                    "count": len(messages),
                    "from": [m.get("from_agent") for m in messages],
                    "ids": [m.get("id") for m in messages],
                },
            )
        return messages

    def _append_inbox_to_pending_tool_results(self, injected_content: str) -> bool:
        """Append inbox text to the current tool-result continuation turn."""
        if not injected_content or not self.context.messages:
            return False

        last_message = self.context.messages[-1]
        content = last_message.get("content", "")
        if last_message.get("role") != "user" or not isinstance(content, list):
            return False

        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                return False
            block_type = block.get("type")
            if block_type == "tool_result":
                has_tool_result = True
                continue
            if block_type == "text":
                continue
            return False

        if not has_tool_result:
            return False

        content.append({"type": "text", "text": injected_content})
        return True

    def _inject_inbox_messages(self) -> tuple[list[dict], str, str]:
        """Inject queued inbox messages into persistent history."""
        messages = self._drain_inbox_queue()
        messages.extend(self._read_unread_inbox_messages())
        messages = self._dedupe_inbox_messages(messages)
        if not messages:
            return [], "", ""

        injected_content = self._format_inbox_messages(messages)
        injection_mode = "new_user_message"
        if self._append_inbox_to_pending_tool_results(injected_content):
            injection_mode = "tool_result_continuation"
        else:
            self.context.add_user_message(injected_content)

        if self.activity_logger:
            self.activity_logger.log_event(
                "inbox_injected",
                {
                    "count": len(messages),
                    "from": [m.get("from_agent") for m in messages],
                    "ids": [m.get("id") for m in messages],
                    "mode": injection_mode,
                },
            )
        return messages, injected_content, injection_mode

    def _rollback_inbox_injection(self, injected_content: str, injection_mode: str) -> None:
        """Remove the most recent injected inbox message from history."""
        if not injected_content or not injection_mode or not self.context.messages:
            return

        last_message = self.context.messages[-1]
        if injection_mode == "new_user_message":
            if (
                last_message.get("role") == "user"
                and last_message.get("content") == injected_content
            ):
                self.context.messages.pop()
            return

        content = last_message.get("content", "")
        if (
            injection_mode == "tool_result_continuation"
            and last_message.get("role") == "user"
            and isinstance(content, list)
            and content
        ):
            last_block = content[-1]
            if (
                isinstance(last_block, dict)
                and last_block.get("type") == "text"
                and last_block.get("text") == injected_content
            ):
                content.pop()

    def _has_pending_tool_continuation(self) -> bool:
        """Return True when the next turn should first consume tool results."""
        if not self.context.messages:
            return False
        last_message = self.context.messages[-1]
        content = last_message.get("content", "")
        if last_message.get("role") != "user" or not isinstance(content, list):
            return False
        if not content:
            return False
        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                return False
            block_type = block.get("type")
            if block_type == "tool_result":
                has_tool_result = True
                continue
            if block_type == "text":
                continue
            return False
        return has_tool_result

    # ------------------------------------------------------------------
    # Main agentic loop
    # ------------------------------------------------------------------

    def run_loop(
        self,
        initial_message: str | list[dict[str, Any]] | None = None,
        stream_callback: Any = None,
    ) -> str:
        """Run the agent loop until the model stops or the turn limit is hit.

        1. Optionally prepends *initial_message* as a user turn.
        2. Calls :meth:`run_turn` to get the next model response.
        3. Builds the assistant content block list and appends it to context.
        4. If the model called tools, executes them and feeds results back.
        5. Repeats until ``stop_reason == "end_turn"`` with no pending tool
           calls, or until ``max_agent_turns`` is reached.
        """
        ctx = self.context
        max_turns: int = ctx.get_max_turns()

        self._start_inbox_poller()

        if initial_message is not None:
            ctx.add_user_message(initial_message)

        last_text: str = ""
        warning_injected: bool = False
        token_budget_exhausted: bool = False

        self._consume_early_exit()

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
                    "2. Call send_message to team-lead with a final status summary.\n"
                    "3. If a task cannot be finished, call task_update(status=\"blocked\")."
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

            compressed = ctx.maybe_compress_history()
            if compressed and self.activity_logger:
                self.activity_logger.log_event(
                    "context_compressed",
                    {"estimated_tokens": ctx.estimate_token_count()},
                )

            delivered_inbox_messages: list[dict] = []
            injected_inbox_content = ""
            inbox_injection_mode = ""
            try:
                delivered_inbox_messages, injected_inbox_content, inbox_injection_mode = self._inject_inbox_messages()

                input_token_estimate = ctx.estimate_token_count()
                result = self.run_turn(stream_callback=stream_callback)
                last_text = result.text_output
                usage = result.usage or {
                    "input_tokens": input_token_estimate,
                    "output_tokens": max(1, len(result.text_output) // 4)
                    + max(0, len(result.tool_calls) * 12),
                }
                delta = ctx.token_tracker.record(usage)
                if self.activity_logger:
                    self.activity_logger.log_token_usage(
                        model=ctx.model,
                        usage=delta,
                        totals=ctx.token_tracker.snapshot(),
                    )

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

                if result.tool_calls:
                    tool_results = self.execute_tools(result.tool_calls)
                    ctx.add_tool_results(tool_results)

                if delivered_inbox_messages:
                    self._mark_inbox_messages_read(delivered_inbox_messages)
                if injected_inbox_content:
                    # Inbox delivery is a per-turn runtime signal, not durable user intent.
                    # Remove it after the model has consumed it so old [INBOX] content
                    # does not linger in history and get mistaken for fresh messages.
                    self._rollback_inbox_injection(
                        injected_inbox_content,
                        inbox_injection_mode,
                    )

                if self._early_exit_requested:
                    early_exit_message = self._consume_early_exit()
                    self.logger.info(
                        "Agent '%s' exiting run_loop early after tool execution.",
                        ctx.agent_identity.agent_name,
                    )
                    return early_exit_message or last_text

                if result.stop_reason == "end_turn" and not result.tool_calls:
                    self.logger.info(
                        "Agent '%s' finished after %d turn(s).",
                        ctx.agent_identity.agent_name,
                        turn_index + 1,
                    )
                    return result.text_output

                if ctx.token_tracker.over_budget:
                    warning = (
                        f"[SYSTEM WARNING] Token budget exhausted for agent "
                        f"'{ctx.agent_identity.agent_name}'. Total tokens: "
                        f"{ctx.token_tracker.total_tokens}."
                    )
                    ctx.add_user_message(warning)
                    if self.activity_logger:
                        self.activity_logger.log_event(
                            "token_budget_exhausted",
                            ctx.token_tracker.snapshot(),
                        )
                    last_text = (last_text + "\n\n" + warning).strip()
                    token_budget_exhausted = True
                    break
            except Exception:
                if delivered_inbox_messages:
                    self._rollback_inbox_injection(injected_inbox_content, inbox_injection_mode)
                    self._requeue_inbox_messages(delivered_inbox_messages)
                raise

        if token_budget_exhausted:
            self._handle_max_turns_exceeded()
            return last_text

        self.logger.warning(
            "Agent '%s' reached max turns (%d) without a clean end_turn.",
            ctx.agent_identity.agent_name,
            max_turns,
        )
        self._handle_max_turns_exceeded()
        return last_text

    def _handle_max_turns_exceeded(self) -> None:
        """Block orphaned tasks and notify team-lead."""
        ctx = self.context
        agent_name = ctx.agent_identity.agent_name
        team_name = ctx.agent_identity.team_name

        if not team_name:
            return

        try:
            from coordination.task_board import TaskBoard
            from coordination.mailbox import Mailbox

            board = TaskBoard(ctx.config, team_name)
            stuck_tasks = board.list_tasks(
                filter_status="in_progress", filter_owner=agent_name
            )

            blocked_ids: list[str] = []

            for task in stuck_tasks:
                try:
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
            if blocked_ids:
                parts.append(
                    "Tasks auto-blocked:\n"
                    + "\n".join(f"  - Task {tid}" for tid in blocked_ids)
                )
            if not blocked_ids:
                parts.append("No in_progress tasks found.")

            mailbox = Mailbox(ctx.config, team_name)
            mailbox.send_message(
                from_agent=agent_name,
                to_agent="team-lead",
                content="\n\n".join(parts),
                summary=(
                    f"{agent_name} done — {len(blocked_ids)} blocked"
                ),
            )

        except Exception as exc:
            self.logger.error(
                "Error in _handle_max_turns_exceeded for '%s': %s",
                agent_name, exc,
            )

