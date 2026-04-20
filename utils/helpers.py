"""General utility functions."""

from __future__ import annotations

import json
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_lock import FileLock


def generate_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:8]
    return f"{prefix}{short}" if prefix else short


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_read(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def safe_json_write(path: Path | str, data: Any, use_lock: bool = True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if use_lock:
        lock = FileLock(path)
        with lock:
            _write_json(path, data)
    else:
        _write_json(path, data)


def _write_json(path: Path, data: Any):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def truncate_text(text: str, max_len: int = 2000) -> str:
    if len(text) <= max_len:
        return text
    half = max_len // 2 - 20
    return text[:half] + f"\n... [{len(text) - max_len} chars truncated] ...\n" + text[-half:]
