"""Tiny JSON file cache in the user's cache directory."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    env = os.environ.get("EGD_SPOT_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "egd-spot"


class JsonCache:
    """Key/value JSON cache. ``directory=None`` disables caching entirely."""

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory

    def _path(self, namespace: str, key: str) -> Path:
        assert self.directory is not None
        return self.directory / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        if self.directory is None:
            return None
        path = self._path(namespace, key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        if self.directory is None:
            return
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
