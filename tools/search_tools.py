"""Search tools: glob file search and grep content search."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Tool

_IGNORED_DIR_NAMES = {
    ".git",
    ".open_Agent_Team",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
}


def _is_ignored_path(path: Path) -> bool:
    return any(part in _IGNORED_DIR_NAMES for part in path.parts)


class GlobTool(Tool):
    def __init__(self):
        self.name = "glob_search"
        self.description = (
            "Search for files matching a glob pattern. "
            "Supports patterns like '**/*.py' for recursive search. "
            "Returns up to 200 matching file paths."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files, e.g. '**/*.py' or 'src/*.ts'.",
                },
                "path": {
                    "type": "string",
                    "description": "Root directory to search in. Defaults to current working directory.",
                },
            },
            "required": ["pattern"],
        }

    def execute(self, params: dict[str, Any]) -> str:
        pattern: str = params["pattern"]
        search_root_str: str | None = params.get("path")

        search_root = Path(search_root_str) if search_root_str else Path.cwd()

        if not search_root.exists():
            return f"Error: Path does not exist: {search_root}"
        if not search_root.is_dir():
            return f"Error: Path is not a directory: {search_root}"

        try:
            # Use rglob for patterns containing **, otherwise glob
            if "**" in pattern:
                # rglob prepends "**/" so strip leading **/ if present
                rglob_pattern = pattern.lstrip("*").lstrip("/")
                if not rglob_pattern:
                    rglob_pattern = "*"
                matches = list(search_root.rglob(rglob_pattern))
            else:
                matches = list(search_root.glob(pattern))
        except Exception as exc:
            return f"Error performing glob search: {exc}"

        # Filter to files only, sort, and cap at 200
        file_matches = sorted(
            [str(m) for m in matches if m.is_file() and not _is_ignored_path(m)],
            key=lambda p: p.lower(),
        )[:200]

        if not file_matches:
            return f"No files found matching pattern '{pattern}' in {search_root}"

        result_lines = [f"Found {len(file_matches)} file(s) matching '{pattern}':"]
        result_lines.extend(file_matches)
        return "\n".join(result_lines)


class GrepTool(Tool):
    def __init__(self):
        self.name = "grep_search"
        self.description = (
            "Search file contents for a regular expression pattern. "
            "Returns matching lines in 'file:line_number:content' format. "
            "Returns up to 200 matches."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for in file contents.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in. Defaults to current working directory.",
                },
                "include": {
                    "type": "string",
                    "description": (
                        "Glob pattern to filter files, e.g. '*.py' or '*.{ts,tsx}'. "
                        "Only files matching this pattern will be searched."
                    ),
                },
            },
            "required": ["pattern"],
        }

    def execute(self, params: dict[str, Any]) -> str:
        pattern: str = params["pattern"]
        search_path_str: str | None = params.get("path")
        include_pattern: str | None = params.get("include")

        search_path = Path(search_path_str) if search_path_str else Path.cwd()

        if not search_path.exists():
            return f"Error: Path does not exist: {search_path}"

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"Error: Invalid regular expression '{pattern}': {exc}"

        results: list[str] = []

        def search_file(filepath: Path) -> None:
            """Search a single file for the pattern."""
            if len(results) >= 200:
                return
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                    for line_num, line in enumerate(fh, start=1):
                        if len(results) >= 200:
                            break
                        if regex.search(line):
                            results.append(
                                f"{filepath}:{line_num}:{line.rstrip()}"
                            )
            except OSError:
                pass  # skip unreadable files

        def matches_include(filepath: Path) -> bool:
            """Check if the filepath matches the include glob pattern."""
            if include_pattern is None:
                return True
            return filepath.match(include_pattern)

        if search_path.is_file():
            if matches_include(search_path):
                search_file(search_path)
        else:
            # Walk the directory tree
            for dirpath, _dirnames, filenames in _walk(search_path):
                for filename in filenames:
                    if len(results) >= 200:
                        break
                    filepath = dirpath / filename
                    if matches_include(filepath):
                        search_file(filepath)
                if len(results) >= 200:
                    break

        if not results:
            return f"No matches found for pattern '{pattern}'"

        header = f"Found {len(results)} match(es) for '{pattern}':"
        if len(results) == 200:
            header += " (truncated at 200 results)"
        return header + "\n" + "\n".join(results)


def _walk(root: Path):
    """Simple recursive directory walker yielding (dirpath, dirnames, filenames)."""
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return
    dirnames = [
        e for e in entries
        if e.is_dir() and not e.name.startswith(".") and e.name not in _IGNORED_DIR_NAMES
    ]
    filenames = [e.name for e in entries if e.is_file()]
    yield root, [d.name for d in dirnames], filenames
    for d in dirnames:
        yield from _walk(d)
