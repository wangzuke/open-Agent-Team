"""File operation tools: read, write, and edit files on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import Tool


def _resolve_path(file_path: str, project_root: Optional[Path]) -> Path:
    """Resolve *file_path* relative to *project_root* when it is a relative path."""
    p = Path(file_path)
    if p.is_absolute() or project_root is None:
        return p
    return (project_root / p).resolve()


class ReadFileTool(Tool):
    def __init__(self, project_root: Optional[Path] = None):
        self.name = "read_file"
        self.description = (
            "Read the contents of a file from disk. "
            "Returns the file content with line numbers. "
            "Use offset and limit to read a specific range of lines."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number (1-based) to start reading from. Defaults to 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Defaults to 200.",
                },
            },
            "required": ["file_path"],
        }
        self._project_root = Path(project_root) if project_root is not None else None

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        offset = int(params.get("offset") or 1)
        limit = int(params.get("limit") or 200)

        if offset < 1:
            offset = 1

        try:
            path = _resolve_path(file_path, self._project_root)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except OSError as exc:
            return f"Error reading file {file_path}: {exc}"

        total_lines = len(all_lines)
        start_idx = offset - 1  # convert to 0-based
        end_idx = start_idx + limit
        selected = all_lines[start_idx:end_idx]

        if not selected:
            return f"(No lines in range offset={offset}, limit={limit}; file has {total_lines} lines)"

        lines_out: list[str] = []
        for i, line in enumerate(selected, start=start_idx + 1):
            lines_out.append(f"{i}\t{line}")

        result = "".join(lines_out)
        if not result.endswith("\n"):
            result += "\n"

        if end_idx < total_lines:
            result += f"(Showing lines {offset}-{start_idx + len(selected)} of {total_lines})"

        return result


class WriteFileTool(Tool):
    def __init__(self, project_root: Optional[Path] = None):
        self.name = "write_file"
        self.description = (
            "Write content to a file on disk. "
            "Creates parent directories if they do not exist. "
            "Overwrites existing content."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path of the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The full text content to write into the file.",
                },
            },
            "required": ["file_path", "content"],
        }
        self._project_root = Path(project_root) if project_root is not None else None

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        content = params["content"]

        try:
            path = _resolve_path(file_path, self._project_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            return f"Successfully wrote {len(content)} characters to {file_path}"
        except OSError as exc:
            return f"Error writing file {file_path}: {exc}"


class EditFileTool(Tool):
    def __init__(self, project_root: Optional[Path] = None):
        self.name = "edit_file"
        self.description = (
            "Edit a file by replacing a specific string with a new string. "
            "The old_string must exist exactly once in the file. "
            "Returns an error if old_string is not found or appears more than once."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to find in the file. Must appear exactly once.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The string to replace old_string with.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }
        self._project_root = Path(project_root) if project_root is not None else None

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        old_string = params["old_string"]
        new_string = params["new_string"]

        try:
            path = _resolve_path(file_path, self._project_root)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except OSError as exc:
            return f"Error reading file {file_path}: {exc}"

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {file_path}. "
                "Provide more context to make it unique."
            )

        new_content = content.replace(old_string, new_string, 1)

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            return f"Successfully edited {file_path}: replaced the specified string."
        except OSError as exc:
            return f"Error writing file {file_path}: {exc}"
