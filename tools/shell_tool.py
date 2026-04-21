"""Shell tool: run arbitrary shell commands in a subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from open_teams.config import OpenTeamsConfig
from open_teams.utils.security import SandboxViolation, validate_shell_command

from .base import Tool


class ShellTool(Tool):
    def __init__(self):
        self.name = "shell"
        self.description = (
            "Execute a shell command and return its output. "
            "Runs in the configured working directory. "
            "Returns combined stdout and stderr."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds before the command is killed. Defaults to 120.",
                },
            },
            "required": ["command"],
        }
        self._working_dir: str | None = None
        self._config: OpenTeamsConfig | None = None

    def set_working_dir(self, path: str, config: OpenTeamsConfig | None = None) -> None:
        """Set the working directory for shell command execution."""
        self._working_dir = path
        self._config = config

    def execute(self, params: dict[str, Any]) -> str:
        command: str = params["command"]
        timeout: int = int(params.get("timeout") or 120)

        working_dir = self._working_dir
        if working_dir is not None:
            wd_path = Path(working_dir)
            if not wd_path.exists():
                return f"Error: Working directory does not exist: {working_dir}"
            working_dir = str(wd_path)

        try:
            if self._config is not None:
                validate_shell_command(command, self._config, working_dir=working_dir)
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=working_dir,
            )
        except SandboxViolation as exc:
            return f"Sandbox error: {exc}"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds: {command}"
        except OSError as exc:
            return f"Error executing command: {exc}"

        output_parts: list[str] = []
        if proc.stdout:
            output_parts.append(proc.stdout)
        if proc.stderr:
            output_parts.append(proc.stderr)

        output = "".join(output_parts)

        if proc.returncode != 0:
            if not output:
                output = f"Command exited with return code {proc.returncode}"
            else:
                output = f"[Exit code: {proc.returncode}]\n{output}"

        return output if output else "(No output)"
