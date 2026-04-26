"""File operation tools: read, write, and edit files on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from config import OpenTeamsConfig
from utils.security import SandboxViolation, validate_file_path

from .base import Tool


def _resolve_path(file_path: str, project_root: Optional[Path]) -> Path:
    p = Path(file_path)
    if p.is_absolute() or project_root is None:
        return p.resolve()
    return (project_root / p).resolve()


def _format_with_line_numbers(text: str, offset: int = 1, limit: int = 200) -> str:
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    start_idx = max(offset - 1, 0)
    end_idx = start_idx + max(limit, 1)
    selected = lines[start_idx:end_idx]
    if not selected:
        return f"(No lines in range offset={offset}, limit={limit}; content has {total_lines} lines)"

    result = "".join(
        f"{line_no}\t{line}"
        for line_no, line in enumerate(selected, start=start_idx + 1)
    )
    if not result.endswith("\n"):
        result += "\n"
    if end_idx < total_lines:
        result += f"(Showing lines {offset}-{start_idx + len(selected)} of {total_lines})"
    return result


class ReadFileTool(Tool):
    def __init__(
        self,
        project_root: Optional[Path] = None,
        config: OpenTeamsConfig | None = None,
    ):
        self.name = "read_file"
        self.description = (
            "Read the contents of a file from disk. "
            "Returns text content with line numbers. "
            "For PDFs it extracts text."
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
        self._config = config
        self._recent_reads: dict[tuple[str, int, int], tuple[int, int]] = {}

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        offset = int(params.get("offset") or 1)
        limit = int(params.get("limit") or 200)

        try:
            path = self._validated_path(file_path, access="read")
            suffix = path.suffix.lower()

            if suffix == ".pdf":
                content = self._read_pdf(path, offset=offset, limit=limit)
                return self._prepend_repeat_notice(path, offset, limit, content)

            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = _format_with_line_numbers(fh.read(), offset=offset, limit=limit)
                return self._prepend_repeat_notice(path, offset, limit, content)
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except SandboxViolation as exc:
            return f"Sandbox error: {exc}"
        except OSError as exc:
            return f"Error reading file {file_path}: {exc}"

    def _validated_path(self, file_path: str, access: str) -> Path:
        if self._config is None:
            return _resolve_path(file_path, self._project_root)
        return validate_file_path(
            file_path,
            self._config,
            project_root=self._project_root,
            access=access,
        )

    def _read_pdf(self, path: Path, offset: int, limit: int) -> str:
        extracted_text = ""
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"[Page {index}]\n{text.strip()}")
            extracted_text = "\n\n".join(pages)
        except ImportError:
            return (
                "Error: Reading PDF files requires the optional dependency 'pypdf'. "
                "Install it to enable PDF extraction."
            )
        except Exception as exc:
            return f"Error extracting text from PDF {path}: {exc}"

        if not extracted_text.strip():
            return f"PDF contains no extractable text: {path}"
        return _format_with_line_numbers(extracted_text, offset=offset, limit=limit)

    def _prepend_repeat_notice(self, path: Path, offset: int, limit: int, content: str) -> str:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return content

        key = (str(path), offset, limit)
        cached = self._recent_reads.get(key)
        count = 1
        if cached and cached[0] == mtime_ns:
            count = cached[1] + 1
        self._recent_reads[key] = (mtime_ns, count)

        if count < 3:
            return content

        notice = (
            "[NOTICE] You have already read this exact file segment multiple times in this session "
            "and the file has not changed. Avoid rereading unchanged files unless you need specific lines.\n"
        )
        return notice + content


class WriteFileTool(Tool):
    def __init__(
        self,
        project_root: Optional[Path] = None,
        config: OpenTeamsConfig | None = None,
    ):
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
        self._config = config

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        content = params["content"]

        try:
            path = self._validated_path(file_path, access="write")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            return f"Successfully wrote {len(content)} characters to {file_path}"
        except SandboxViolation as exc:
            return f"Sandbox error: {exc}"
        except OSError as exc:
            return f"Error writing file {file_path}: {exc}"

    def _validated_path(self, file_path: str, access: str) -> Path:
        if self._config is None:
            return _resolve_path(file_path, self._project_root)
        return validate_file_path(
            file_path,
            self._config,
            project_root=self._project_root,
            access=access,
        )


class EditFileTool(Tool):
    def __init__(
        self,
        project_root: Optional[Path] = None,
        config: OpenTeamsConfig | None = None,
    ):
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
        self._config = config

    def execute(self, params: dict[str, Any]) -> str:
        file_path = params["file_path"]
        old_string = params["old_string"]
        new_string = params["new_string"]

        try:
            path = self._validated_path(file_path, access="edit")
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except SandboxViolation as exc:
            return f"Sandbox error: {exc}"
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

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content.replace(old_string, new_string, 1))
            return f"Successfully edited {file_path}: replaced the specified string."
        except OSError as exc:
            return f"Error writing file {file_path}: {exc}"

    def _validated_path(self, file_path: str, access: str) -> Path:
        if self._config is None:
            return _resolve_path(file_path, self._project_root)
        return validate_file_path(
            file_path,
            self._config,
            project_root=self._project_root,
            access=access,
        )
