"""Mailbox: per-agent message inboxes stored as JSON arrays, with file locking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from config import OpenTeamsConfig
from utils.file_lock import FileLock
from utils.helpers import generate_id, timestamp_now


class Mailbox:
    """Manage per-agent inboxes for a named team.

    Each agent's inbox is a JSON file at
    ``config.agent_inbox_path(team_name, agent_name)`` containing a JSON
    array of message objects.  Every mutation acquires a ``FileLock`` on the
    inbox file to allow safe concurrent access from multiple agents.

    Message schema::

        {
            "id":         str,          # unique message ID
            "from_agent": str,
            "to_agent":   str,
            "content":    str,
            "summary":    str,          # short human-readable summary
            "timestamp":  str,          # ISO-8601 UTC
            "read":       bool          # False on arrival; True after delivery to agent context
        }
    """

    def __init__(self, config: OpenTeamsConfig, team_name: str) -> None:
        self.config = config
        self.team_name = team_name
        self.inboxes_dir: Path = config.team_inboxes_dir(team_name)
        self.inboxes_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inbox_path(self, agent_name: str) -> Path:
        return self.config.agent_inbox_path(self.team_name, agent_name)

    def _read_inbox_raw(self, agent_name: str) -> list[dict]:
        """Read messages from the inbox JSON file; returns [] on any error."""
        path = self._inbox_path(agent_name)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_inbox_raw(self, agent_name: str, messages: list[dict]) -> None:
        """Atomically write the messages list to the inbox file."""
        path = self._inbox_path(agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(messages, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_inbox(self, agent_name: str) -> None:
        """Create an empty inbox for *agent_name* if one does not already exist."""
        path = self._inbox_path(agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        lock = FileLock(path)
        with lock:
            # Double-check after acquiring the lock
            if not path.exists():
                self._write_inbox_raw(agent_name, [])

    def send_message(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        summary: str = "",
    ) -> dict:
        """Append a new message to *to_agent*'s inbox.

        Creates the inbox file if it does not yet exist.
        Returns the message dict that was stored.
        """
        message: dict = {
            "id": generate_id("msg_"),
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "summary": summary,
            "timestamp": timestamp_now(),
            "read": False,
        }
        path = self._inbox_path(to_agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            messages = self._read_inbox_raw(to_agent)
            messages.append(message)
            self._write_inbox_raw(to_agent, messages)
        return message

    def read_inbox(self, agent_name: str, unread_only: bool = True) -> list[dict]:
        """Return messages from *agent_name*'s inbox.

        When *unread_only* is ``True`` (the default) only messages whose
        ``read`` field is ``False`` are returned.  The inbox file is not
        modified; call :meth:`mark_as_read` to update read status.
        """
        path = self._inbox_path(agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            messages = self._read_inbox_raw(agent_name)
        if unread_only:
            return [m for m in messages if not m.get("read", False)]
        return messages

    def mark_as_read(
        self,
        agent_name: str,
        message_ids: Optional[list[str]] = None,
    ) -> None:
        """Mark messages as read.

        If *message_ids* is ``None`` or an empty list, **all** messages in the
        inbox are marked as read.  Otherwise only the messages whose ``id``
        appears in *message_ids* are updated.
        """
        path = self._inbox_path(agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            messages = self._read_inbox_raw(agent_name)
            for msg in messages:
                if message_ids is None or not message_ids or msg["id"] in message_ids:
                    msg["read"] = True
            self._write_inbox_raw(agent_name, messages)

    def clear_inbox(self, agent_name: str) -> None:
        """Remove all messages from *agent_name*'s inbox."""
        path = self._inbox_path(agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            self._write_inbox_raw(agent_name, [])

    def broadcast(
        self,
        from_agent: str,
        content: str,
        exclude: Optional[list[str]] = None,
        summary: str = "",
    ) -> list[dict]:
        """Send *content* to every agent inbox found in the team's inboxes directory.

        Agents listed in *exclude* (including *from_agent* by convention) are
        skipped.  Returns the list of message dicts that were delivered.
        """
        skip: set[str] = set(exclude or [])
        delivered: list[dict] = []
        for inbox_file in self.inboxes_dir.glob("*.json"):
            agent_name = inbox_file.stem
            if agent_name in skip:
                continue
            msg = self.send_message(
                from_agent=from_agent,
                to_agent=agent_name,
                content=content,
                summary=summary,
            )
            delivered.append(msg)
        return delivered
