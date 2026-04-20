"""TeamManager: create, read, and mutate team configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..config import OpenTeamsConfig
from ..utils.file_lock import FileLock
from ..utils.helpers import generate_id, timestamp_now


class TeamManager:
    """Manage team configuration stored under ``config.team_dir(team_name)/config.json``.

    Team config schema::

        {
            "name":        str,
            "description": str,
            "createdAt":   str,       # ISO-8601 UTC
            "updatedAt":   str,
            "members": [
                {
                    "name":       str,
                    "agent_id":   str,
                    "agent_type": str,
                    "model":      str,
                    "role":       str,    # "leader" | "member"
                    "status":     str,    # "active" | "idle" | "busy" | ...
                    "joinedAt":   str,
                }
            ]
        }
    """

    def __init__(self, config: OpenTeamsConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _config_path(self, team_name: str) -> Path:
        return self.config.team_dir(team_name) / "config.json"

    def _read_config(self, team_name: str) -> Optional[dict]:
        path = self._config_path(team_name)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _ensure_team_dir(self, team_name: str) -> Path:
        """Ensure the team directory exists and return the config path."""
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_config(self, team_name: str, data: dict) -> None:
        """Atomically write *data* to the team config file."""
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_team(
        self,
        team_name: str,
        description: str,
        leader_name: str,
        leader_id: str,
        leader_model: str,
    ) -> dict:
        """Create a new team with an initial leader member.

        Returns the new team config dict.
        Raises ``ValueError`` if the team already exists.
        """
        path = self._ensure_team_dir(team_name)
        lock = FileLock(path)
        with lock:
            if path.exists():
                raise ValueError(f"Team {team_name!r} already exists")
            now = timestamp_now()
            team: dict = {
                "name": team_name,
                "description": description,
                "createdAt": now,
                "updatedAt": now,
                "members": [
                    {
                        "name": leader_name,
                        "agent_id": leader_id,
                        "agent_type": "leader",
                        "model": leader_model,
                        "role": "leader",
                        "status": "active",
                        "joinedAt": now,
                    }
                ],
            }
            self._write_config(team_name, team)
        return team

    def get_team(self, team_name: str) -> Optional[dict]:
        """Return the team config dict, or ``None`` if the team does not exist."""
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            return self._read_config(team_name)

    def add_member(
        self,
        team_name: str,
        name: str,
        agent_id: str,
        agent_type: str,
        model: str,
    ) -> dict:
        """Add a new member to *team_name*.

        Returns the new member dict.
        Raises ``KeyError`` if the team does not exist.
        Raises ``ValueError`` if a member with *name* already exists.
        """
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            team = self._read_config(team_name)
            if team is None:
                raise KeyError(f"Team {team_name!r} not found")
            members: list[dict] = team.get("members", [])
            if any(m["name"] == name for m in members):
                raise ValueError(f"Member {name!r} already exists in team {team_name!r}")
            now = timestamp_now()
            member: dict = {
                "name": name,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "model": model,
                "role": "member",
                "status": "active",
                "joinedAt": now,
            }
            members.append(member)
            team["members"] = members
            team["updatedAt"] = now
            self._write_config(team_name, team)
        return member

    def remove_member(self, team_name: str, agent_name: str) -> bool:
        """Remove *agent_name* from *team_name*.

        Returns ``True`` if the member was found and removed, ``False`` otherwise.
        Raises ``KeyError`` if the team does not exist.
        """
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            team = self._read_config(team_name)
            if team is None:
                raise KeyError(f"Team {team_name!r} not found")
            members: list[dict] = team.get("members", [])
            original_len = len(members)
            members = [m for m in members if m["name"] != agent_name]
            if len(members) == original_len:
                return False
            team["members"] = members
            team["updatedAt"] = timestamp_now()
            self._write_config(team_name, team)
        return True

    def update_member_status(self, team_name: str, agent_name: str, status: str) -> dict:
        """Set *agent_name*'s ``status`` field in *team_name*.

        Returns the updated member dict.
        Raises ``KeyError`` if the team or the member does not exist.
        """
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            team = self._read_config(team_name)
            if team is None:
                raise KeyError(f"Team {team_name!r} not found")
            members: list[dict] = team.get("members", [])
            for member in members:
                if member["name"] == agent_name:
                    member["status"] = status
                    team["updatedAt"] = timestamp_now()
                    self._write_config(team_name, team)
                    return member
            raise KeyError(f"Member {agent_name!r} not found in team {team_name!r}")

    def list_members(self, team_name: str) -> list[dict]:
        """Return the list of member dicts for *team_name*.

        Raises ``KeyError`` if the team does not exist.
        """
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            team = self._read_config(team_name)
        if team is None:
            raise KeyError(f"Team {team_name!r} not found")
        return list(team.get("members", []))

    def delete_team(self, team_name: str) -> bool:
        """Delete the team config file.

        Returns ``True`` if the file was deleted, ``False`` if it did not exist.
        The team directory itself is left in place (it may contain other data).
        """
        path = self._config_path(team_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path)
        with lock:
            if not path.exists():
                return False
            try:
                path.unlink()
                return True
            except OSError:
                return False
