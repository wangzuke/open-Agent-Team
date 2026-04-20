"""TaskBoard: shared task tracking for a team, with file-based persistence and locking."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ..config import OpenTeamsConfig
from ..utils.file_lock import FileLock
from ..utils.helpers import generate_id, timestamp_now


class TaskBoard:
    """Persistent task board for a named team.

    All task files live under ``config.team_tasks_dir(team_name)``.
    A ``_counter.json`` file in the same directory tracks the auto-increment ID.
    Every read/write of an individual task file is protected by a ``FileLock``
    so concurrent agents can safely access the board.
    """

    def __init__(self, config: OpenTeamsConfig, team_name: str) -> None:
        self.config = config
        self.team_name = team_name
        self.tasks_dir: Path = config.team_tasks_dir(team_name)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _counter_path(self) -> Path:
        return self.tasks_dir / "_counter.json"

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"task_{task_id}.json"

    def _next_id(self) -> str:
        """Read, increment, and persist the task counter; return the new ID as a string."""
        counter_path = self._counter_path()
        lock = FileLock(counter_path)
        with lock:
            if counter_path.exists():
                try:
                    with open(counter_path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    current: int = int(data.get("counter", 0))
                except (json.JSONDecodeError, OSError, ValueError):
                    current = 0
            else:
                current = 0
            next_val = current + 1
            tmp = counter_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"counter": next_val}, fh)
            tmp.replace(counter_path)
        return str(next_val)

    def _read_task_file(self, task_id: str) -> Optional[dict]:
        """Read a single task file without acquiring a caller-level lock."""
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_task_file(self, task: dict) -> None:
        """Atomically write a task dict under its canonical path."""
        path = self._task_path(task["id"])
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(task, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _all_task_ids(self) -> list[str]:
        """Return the task IDs inferred from all task_*.json files."""
        ids: list[str] = []
        for p in self.tasks_dir.glob("task_*.json"):
            stem = p.stem  # task_<id>
            task_id = stem[len("task_"):]
            if task_id:
                ids.append(task_id)
        return ids

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_task(
        self,
        subject: str,
        description: str,
        owner: Optional[str] = None,
        blocks: Optional[list[str]] = None,
        blockedBy: Optional[list[str]] = None,
    ) -> dict:
        """Create a new task and persist it; returns the task dict."""
        task_id = self._next_id()
        task: dict[str, Any] = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": owner,
            "blocks": blocks if blocks is not None else [],
            "blockedBy": blockedBy if blockedBy is not None else [],
            "createdAt": timestamp_now(),
            "updatedAt": timestamp_now(),
        }
        path = self._task_path(task_id)
        lock = FileLock(path)
        with lock:
            self._write_task_file(task)
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        """Return the task dict for *task_id*, or ``None`` if not found."""
        path = self._task_path(task_id)
        lock = FileLock(path)
        with lock:
            return self._read_task_file(task_id)

    def update_task(self, task_id: str, **kwargs: Any) -> dict:
        """Update fields on an existing task.

        Special keyword arguments:
        - ``addBlocks``: list[str] – append IDs to the ``blocks`` list.
        - ``addBlockedBy``: list[str] – append IDs to the ``blockedBy`` list.
        - ``status="completed"``: additionally remove this task from the
          ``blockedBy`` list of every other task.

        Returns the updated task dict.
        Raises ``KeyError`` if the task does not exist.
        """
        path = self._task_path(task_id)
        lock = FileLock(path)
        with lock:
            task = self._read_task_file(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")

            # Handle list extension helpers before applying remaining kwargs
            add_blocks: list[str] = kwargs.pop("addBlocks", None) or []
            add_blocked_by: list[str] = kwargs.pop("addBlockedBy", None) or []

            if add_blocks:
                existing: list[str] = task.get("blocks", [])
                for bid in add_blocks:
                    if bid not in existing:
                        existing.append(bid)
                task["blocks"] = existing

            if add_blocked_by:
                existing_bb: list[str] = task.get("blockedBy", [])
                for bid in add_blocked_by:
                    if bid not in existing_bb:
                        existing_bb.append(bid)
                task["blockedBy"] = existing_bb

            # Apply remaining scalar/list updates
            for key, value in kwargs.items():
                task[key] = value

            task["updatedAt"] = timestamp_now()
            self._write_task_file(task)

        # If the task was just marked completed, scrub it from every other
        # task's blockedBy.  We do this outside the original lock to avoid
        # dead-lock while still using per-file locks below.
        if kwargs.get("status") == "completed":
            self._remove_from_all_blocked_by(task_id)

        return task

    def _remove_from_all_blocked_by(self, completed_id: str) -> None:
        """Remove *completed_id* from the ``blockedBy`` list of all tasks."""
        for tid in self._all_task_ids():
            if tid == completed_id:
                continue
            other_path = self._task_path(tid)
            lock = FileLock(other_path)
            with lock:
                other = self._read_task_file(tid)
                if other is None:
                    continue
                blocked_by: list[str] = other.get("blockedBy", [])
                if completed_id in blocked_by:
                    blocked_by.remove(completed_id)
                    other["blockedBy"] = blocked_by
                    other["updatedAt"] = timestamp_now()
                    self._write_task_file(other)

    def list_tasks(
        self,
        filter_status: Optional[str] = None,
        filter_owner: Optional[str] = None,
    ) -> list[dict]:
        """Return all tasks, optionally filtered by status and/or owner."""
        results: list[dict] = []
        for tid in self._all_task_ids():
            task = self.get_task(tid)
            if task is None:
                continue
            if filter_status is not None and task.get("status") != filter_status:
                continue
            if filter_owner is not None and task.get("owner") != filter_owner:
                continue
            results.append(task)
        # Sort by numeric id for stable ordering
        results.sort(key=lambda t: int(t["id"]))
        return results

    def delete_task(self, task_id: str) -> bool:
        """Delete the task file; returns ``True`` if deleted, ``False`` if not found."""
        path = self._task_path(task_id)
        lock = FileLock(path)
        with lock:
            if not path.exists():
                return False
            try:
                path.unlink()
                return True
            except OSError:
                return False

    def get_available_tasks(self) -> list[dict]:
        """Return tasks that are available to be picked up.

        A task is *available* when:
        - its status is ``"pending"``
        - it has no owner (``owner`` is ``None`` or empty string)
        - its ``blockedBy`` list is empty **or** every ID in it belongs to a
          task whose status is ``"completed"``
        """
        all_tasks = self.list_tasks()
        # Build a quick status lookup
        status_map: dict[str, str] = {t["id"]: t.get("status", "pending") for t in all_tasks}

        available: list[dict] = []
        for task in all_tasks:
            if task.get("status") != "pending":
                continue
            if task.get("owner"):
                continue
            blocked_by: list[str] = task.get("blockedBy", [])
            if all(status_map.get(bid) == "completed" for bid in blocked_by):
                available.append(task)
        return available
