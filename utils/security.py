"""Sandbox validation helpers for file and shell operations."""

from __future__ import annotations

import re
from pathlib import Path

from config import OpenTeamsConfig


class SandboxViolation(RuntimeError):
    """Raised when a sandbox rule is violated."""


_DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|\s)rmdir\b", re.IGNORECASE), "Directory deletion commands are blocked."),
    (re.compile(r"(^|\s)rm\b[^\n]*\s-[^\n]*r", re.IGNORECASE), "Recursive delete commands are blocked."),
    (re.compile(r"(^|\s)(del|erase)\b[^\n]*/s\b", re.IGNORECASE), "Recursive delete commands are blocked."),
    (re.compile(r"(^|\s)(rm|del|erase)\b[^\n]*(\*|\?)", re.IGNORECASE), "Wildcard delete commands are blocked."),
    (re.compile(r"Remove-Item\b.*-Recurse", re.IGNORECASE), "Recursive PowerShell deletion is blocked."),
    (re.compile(r"(^|\s)(shutdown|reboot|halt)\b", re.IGNORECASE), "System power commands are blocked."),
    (re.compile(r"(^|\s)(mkfs|fdisk|diskpart|format)\b", re.IGNORECASE), "Disk formatting commands are blocked."),
    (re.compile(r"(^|\s)(sudo|su)\b", re.IGNORECASE), "Privilege escalation commands are blocked."),
    (re.compile(r"Stop-Computer|Restart-Computer", re.IGNORECASE), "System power commands are blocked."),
    (re.compile(r"curl\b.+\|\s*(sh|bash|pwsh|powershell)", re.IGNORECASE), "Piped remote shell execution is blocked."),
]

_SENSITIVE_SEGMENTS = {
    ".ssh",
    ".gnupg",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    ".aws",
    ".azure",
    ".config",
}


def _allowed_roots(config: OpenTeamsConfig, project_root: Path | None = None) -> list[Path]:
    root = (project_root or config.project_root).resolve()
    extra = [p.resolve() for p in config.sandbox_allowed_dirs]
    return [root, *extra]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_file_path(
    file_path: str,
    config: OpenTeamsConfig,
    project_root: Path | None = None,
    access: str = "read",
) -> Path:
    """Resolve *file_path* and ensure it stays inside the sandbox."""
    resolved = Path(file_path)
    if not resolved.is_absolute():
        resolved = ((project_root or config.project_root) / resolved).resolve()
    else:
        resolved = resolved.resolve()

    if not config.sandbox_enabled:
        return resolved

    if not any(_is_within(resolved, root) for root in _allowed_roots(config, project_root)):
        raise SandboxViolation(
            f"Sandbox blocked {access} access outside allowed directories: {resolved}"
        )

    lowered_parts = {part.lower() for part in resolved.parts}
    if _SENSITIVE_SEGMENTS & lowered_parts and not _is_within(resolved, config.project_root.resolve()):
        raise SandboxViolation(f"Sandbox blocked access to sensitive path: {resolved}")

    return resolved


def validate_shell_command(
    command: str,
    config: OpenTeamsConfig,
    working_dir: str | Path | None = None,
) -> None:
    """Validate a shell command against sandbox rules."""
    if not config.sandbox_enabled:
        return

    for pattern, message in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            raise SandboxViolation(f"{message} Command: {command}")

    if re.search(r"(^|\s)(C:\\|/etc/|/var/|/usr/|/bin/)", command, re.IGNORECASE):
        raise SandboxViolation(f"Sandbox blocked command targeting system paths: {command}")

    if working_dir is not None:
        validate_file_path(str(working_dir), config, access="execute")
