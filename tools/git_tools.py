"""Git integration tools for repository inspection and commits."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .base import Tool


def _truncate(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]..."


class _GitTool(Tool):
    _working_dir: Path

    def __init__(self, working_dir: Path):
        self._working_dir = working_dir.resolve()

    def _run_git(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self._working_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except FileNotFoundError:
            return "Error: git is not installed or not available in PATH."
        except subprocess.TimeoutExpired:
            return f"Error: git command timed out: {' '.join(args)}"

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return f"[Exit code: {proc.returncode}]\n{output or '(No output)'}"
        return output or "(No output)"


class GitStatusTool(_GitTool):
    def __init__(self, working_dir: Path):
        super().__init__(working_dir)
        self.name = "git_status"
        self.description = "Show repository status with branch and changed files."
        self.input_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, params: dict[str, Any]) -> str:
        return self._run_git(["status", "--short", "--branch"])


class GitDiffTool(_GitTool):
    def __init__(self, working_dir: Path):
        super().__init__(working_dir)
        self.name = "git_diff"
        self.description = "Show git diff, optionally for a target path or staged changes."
        self.input_schema = {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Optional file or pathspec to diff.",
                },
                "cached": {
                    "type": "boolean",
                    "description": "Whether to inspect staged changes.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return before truncation.",
                },
            },
            "required": [],
        }

    def execute(self, params: dict[str, Any]) -> str:
        args = ["diff"]
        if params.get("cached"):
            args.append("--cached")
        target = params.get("target")
        if target:
            args.extend(["--", target])
        max_chars = int(params.get("max_chars") or 12000)
        return _truncate(self._run_git(args), max_chars=max_chars)


class GitLogTool(_GitTool):
    def __init__(self, working_dir: Path):
        super().__init__(working_dir)
        self.name = "git_log"
        self.description = "Show recent git commit history."
        self.input_schema = {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of commits to show. Defaults to 10.",
                }
            },
            "required": [],
        }

    def execute(self, params: dict[str, Any]) -> str:
        limit = max(1, int(params.get("limit") or 10))
        return self._run_git(
            ["log", f"-{limit}", "--oneline", "--decorate", "--graph"]
        )


class GitCommitTool(_GitTool):
    def __init__(self, working_dir: Path):
        super().__init__(working_dir)
        self.name = "git_commit"
        self.description = "Stage changes and create a commit with a message."
        self.input_schema = {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message to use.",
                },
                "add_all": {
                    "type": "boolean",
                    "description": "Whether to stage all changes before committing. Defaults to true.",
                },
            },
            "required": ["message"],
        }

    def execute(self, params: dict[str, Any]) -> str:
        message = (params.get("message") or "").strip()
        if not message:
            return "Error: commit message cannot be empty."

        add_all = params.get("add_all", True)
        if add_all:
            add_output = self._run_git(["add", "-A"])
            if add_output.startswith("[Exit code:"):
                return add_output

        commit_output = self._run_git(["commit", "-m", message])
        return _truncate(commit_output, max_chars=12000)
