"""Cross-process file locking utility."""

from __future__ import annotations

import time
import os
from pathlib import Path


class FileLock:
    """Simple file-based lock using .lock files with retry logic."""

    def __init__(self, path: Path | str, timeout: float = 10.0, retry_interval: float = 0.05):
        self.lock_path = Path(str(path) + ".lock")
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._fd = None

    def acquire(self) -> bool:
        start = time.monotonic()
        while True:
            try:
                self._fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.lock_path.unlink()
                        continue
                    except OSError:
                        pass
                if time.monotonic() - start >= self.timeout:
                    return False
                time.sleep(self.retry_interval)

    def release(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def _is_stale(self, max_age: float = 60.0) -> bool:
        try:
            stat = self.lock_path.stat()
            return (time.time() - stat.st_mtime) > max_age
        except OSError:
            return True

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()
