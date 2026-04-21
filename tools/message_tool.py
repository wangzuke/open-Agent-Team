"""Message tools: send and receive messages via agent inboxes."""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from open_teams.config import OpenTeamsConfig
    from open_teams.coordination.mailbox import Mailbox


class SendMessageTool(Tool):
    def __init__(self):
        self.name = "send_message"
        self.description = (
            "Send a message to another agent on the team. "
            "The message is delivered to the recipient's inbox. "
            "Optionally include a short summary for quick scanning."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "The name of the agent to send the message to.",
                },
                "content": {
                    "type": "string",
                    "description": "The full message content to deliver.",
                },
                "summary": {
                    "type": "string",
                    "description": "A short one-line summary of the message for quick scanning.",
                },
            },
            "required": ["to", "content"],
        }
        self._team_name: Optional[str] = None
        self._agent_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._mailbox: Optional["Mailbox"] = None

    def set_context(self, team_name: str, agent_name: str, config: "OpenTeamsConfig") -> None:
        """Configure which team and agent this tool belongs to."""
        self._team_name = team_name
        self._agent_name = agent_name
        self._config = config
        self._mailbox = None  # reset so it gets re-created lazily

    def _get_mailbox(self) -> "Mailbox":
        """Lazily create and return the Mailbox instance."""
        if self._mailbox is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "SendMessageTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.mailbox import Mailbox
            self._mailbox = Mailbox(self._config, self._team_name)
        return self._mailbox

    def execute(self, params: dict[str, Any]) -> str:
        to: str = params["to"]
        content: str = params["content"]
        summary: str = params.get("summary", "")

        if not self._agent_name:
            return "Error: SendMessageTool has no agent context. Call set_context() first."

        try:
            mailbox = self._get_mailbox()
            message = mailbox.send_message(
                from_agent=self._agent_name,
                to_agent=to,
                content=content,
                summary=summary,
            )
            return (
                f"Message sent successfully to '{to}'. "
                f"Message ID: {message.get('id', 'unknown')}"
            )
        except RuntimeError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error sending message to '{to}': {exc}"


class CheckInboxTool(Tool):
    """Read unread messages from this agent's own inbox."""

    def __init__(self):
        self.name = "check_inbox"
        self.description = (
            "Read the full content of unread inbox messages from teammates or the team leader. "
            "This is a fallback/debug tool; the runtime normally delivers full inbox messages automatically via [INBOX]. "
            "Returns unread messages and marks them as read."
        )
        self.input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._team_name: Optional[str] = None
        self._agent_name: Optional[str] = None
        self._config: Optional["OpenTeamsConfig"] = None
        self._mailbox: Optional["Mailbox"] = None

    def set_context(
        self, team_name: str, agent_name: str, config: "OpenTeamsConfig"
    ) -> None:
        self._team_name = team_name
        self._agent_name = agent_name
        self._config = config
        self._mailbox = None

    def _get_mailbox(self) -> "Mailbox":
        if self._mailbox is None:
            if self._config is None or self._team_name is None:
                raise RuntimeError(
                    "CheckInboxTool: set_context() must be called before execute()."
                )
            from open_teams.coordination.mailbox import Mailbox
            self._mailbox = Mailbox(self._config, self._team_name)
        return self._mailbox

    def execute(self, params: dict[str, Any]) -> str:
        if not self._agent_name:
            return "Error: CheckInboxTool has no agent context."

        try:
            mailbox = self._get_mailbox()
            messages = mailbox.read_inbox(self._agent_name, unread_only=True)

            if not messages:
                return "Inbox is empty. No new messages."

            msg_ids = [m["id"] for m in messages]
            mailbox.mark_as_read(self._agent_name, msg_ids)

            lines = [f"You have {len(messages)} unread message(s):\n"]
            for i, msg in enumerate(messages, 1):
                ts = msg.get("timestamp", "")[:19]
                lines.append(
                    f"--- Message {i} ---\n"
                    f"From:    {msg.get('from_agent', 'unknown')}\n"
                    f"Time:    {ts}\n"
                    f"Summary: {msg.get('summary', '')}\n"
                    f"Content: {msg.get('content', '')}\n"
                )
            return "\n".join(lines)

        except Exception as exc:
            return f"Error checking inbox: {exc}"
