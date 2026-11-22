"""Small, robust JSON file cache in the user's cache directory.

* one JSON file per key: ``<cache dir>/<namespace>/<key>.json``
* atomic writes (temporary file in the same directory + ``os.replace``)
* a corrupted or unreadable file is reported through ``warn`` and treated as missing,
  so the data is downloaded again and the file rewritten
* ``refresh=True`` ignores existing entries (but still writes fresh ones)

Callers store versioned documents (``{"format": N, ...}``) and validate them themselves.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: cache namespaces written by this package (``hdo`` = removed feature, cleaned up by clear)
NAMESPACES = ("ote", "cnb")
LEGACY_NAMESPACES = ("hdo",)


def default_cache_dir() -> Path:
    """``$EGD_CACHE_DIR``, else ``%LOCALAPPDATA%\\egd-api-client`` on Windows, else
    ``$XDG_CACHE_HOME/egd-api-client`` (default ``~/.cache/egd-api-client``; on Termux
    ``~`` is the Termux home)."""
    env = os.environ.get("EGD_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "egd-api-client"


class JsonCache:
    """Key/value JSON cache. ``directory=None`` disables caching entirely."""

    def __init__(
        self,
        directory: Path | None,
        *,
        warn: Callable[[str], None] | None = None,
        refresh: bool = False,
    ) -> None:
        self.directory = directory
        self.refresh = refresh
        self._warn = warn

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def warn(self, msg: str) -> None:
        if self._warn is not None:
            self._warn(msg)

    def _path(self, namespace: str, key: str) -> Path:
        assert self.directory is not None
        return self.directory / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        if self.directory is None or self.refresh:
            return None
        path = self._path(namespace, key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.warn(f"Cache file {path} is unreadable ({exc}); downloading the data again.")
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        if self.directory is None:
            return
        path = self._path(namespace, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))
                Path(tmp).replace(path)
            except BaseException:
                with contextlib.suppress(OSError):
                    Path(tmp).unlink()
                raise
        except OSError as exc:  # read-only or full disk: keep working without the cache
            self.warn(f"Cannot write cache file {path} ({exc}).")

    # -- maintenance --------------------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        """Summary of the cache directory: files and bytes per namespace."""
        out: dict[str, Any] = {"directory": str(self.directory) if self.directory else None}
        spaces: dict[str, dict[str, int]] = {}
        if self.directory is not None and self.directory.is_dir():
            for sub in sorted(p for p in self.directory.iterdir() if p.is_dir()):
                files = [f for f in sub.glob("*.json") if f.is_file()]
                spaces[sub.name] = {
                    "files": len(files),
                    "bytes": sum(f.stat().st_size for f in files),
                }
        out["namespaces"] = spaces
        return out

    def clear(self, namespaces: tuple[str, ...] | None = None) -> list[str]:
        """Delete the given namespaces (default: all of this package's); returns those removed."""
        if self.directory is None:
            return []
        removed = []
        for ns in namespaces or (*NAMESPACES, *LEGACY_NAMESPACES):
            path = self.directory / ns
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(ns)
        return removed
