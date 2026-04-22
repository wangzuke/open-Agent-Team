"""Thread-safe JSONL activity logger for open-teams agents."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from open_teams.config import OpenTeamsConfig


class ActivityLogger:
    """Appends structured JSONL log entries for a single agent."""

    def __init__(self, config: OpenTeamsConfig, team_name: str, agent_name: str):
        self.agent_name = agent_name
        self.log_path: Path = config.agent_log_path(team_name, agent_name)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_line(self, data: dict) -> None:
        """Serialize *data* as JSON and append it to the log file."""
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        data["agent"] = self.agent_name
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _truncate(self, text: str, max_len: int = 3000) -> str:
        """Return *text* truncated to *max_len* characters with a marker."""
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text

    # ------------------------------------------------------------------
    # Public logging methods
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        tool_name: str,
        params: dict,
        result: str,
        duration_ms: float = 0,
        *,
        status: str = "ok",
        tool_call_id: int = 0,
    ) -> None:
        """Log a single tool invocation with its parameters and result."""
        payload = {
            "type": "tool_call",
            "tool": tool_name,
            "status": status,
            "params": params,
            "result": self._truncate(result),
            "duration_ms": duration_ms,
        }
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        self._write_line(payload)

    def log_message(self, role: str, content: str) -> None:
        """Log an LLM conversation message (role = 'user' | 'assistant' | etc.)."""
        self._write_line({
            "type": "message",
            "role": role,
            "content": self._truncate(content),
        })

    def log_event(self, event_type: str, data: dict | None = None) -> None:
        """Log a named lifecycle event (e.g. 'agent_started', 'task_claimed')."""
        self._write_line({
            "type": "event",
            "event": event_type,
            "data": data or {},
        })

    def log_token_usage(self, model: str, usage: dict, totals: dict) -> None:
        """Log token usage for a single LLM turn and cumulative totals."""
        self._write_line({
            "type": "token_usage",
            "model": model,
            "usage": usage,
            "totals": totals,
        })

    def log_error(self, error: str, context: dict | None = None) -> None:
        """Log an error with optional contextual metadata."""
        self._write_line({
            "type": "error",
            "error": error,
            "context": context or {},
        })
