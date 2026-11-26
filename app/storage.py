"""
Crash-safe JSON persistence for LOY Studio user data.

Every settings/schedule/library/auth file goes through `atomic_write_json`, so a crash or
power loss mid-write can never leave a half-written (corrupt) file behind.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("loy.storage")


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write `text` to `path` atomically (temp file in the same dir + fsync + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        if private:
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, *, indent: Optional[int] = 2, private: bool = False) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent), private=private)


def read_json(
    path: Path,
    default: Callable[[], Any],
    *,
    validate: Optional[Callable[[Any], bool]] = None,
) -> Any:
    """
    Read JSON from `path`. A missing file returns `default()`. A corrupt or invalid file is
    moved aside to `<name>.corrupt-<ts>` (never silently overwritten) and `default()` is returned.
    """
    path = Path(path)
    if not path.exists():
        return default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if validate is None or validate(data):
            return data
        reason = "failed validation"
    except Exception as exc:  # noqa: BLE001 - any parse/IO error means "unreadable"
        reason = f"unreadable ({exc})"
    backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        path.replace(backup)
        log.error("%s was %s; moved it to %s and using defaults", path.name, reason, backup.name)
    except OSError:
        log.error("%s was %s and could not be moved aside; using defaults", path.name, reason)
    return default()
