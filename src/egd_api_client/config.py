"""Configuration: TOML file + environment variables (+ CLI flags, applied in cli.py)."""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_CLIENT_ID = "EGD_CLIENT_ID"
ENV_CLIENT_SECRET = "EGD_CLIENT_SECRET"
ENV_EAN = "EGD_EAN"
ENV_CONFIG = "EGD_CONFIG"


def default_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "egd-api-client" / "config.toml"


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class Config:
    egd: dict[str, Any] = field(default_factory=dict)
    spot: dict[str, Any] = field(default_factory=dict)  # [spot] – tariff defaults
    fixed: dict[str, Any] = field(default_factory=dict)  # [spot.fixed] – comparison tariff
    low_tariff: dict[str, Any] = field(default_factory=dict)  # [low_tariff] – NT periods
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def client_id(self) -> str | None:
        return os.environ.get(ENV_CLIENT_ID) or self.egd.get("client_id")

    @property
    def client_secret(self) -> str | None:
        return os.environ.get(ENV_CLIENT_SECRET) or self.egd.get("client_secret")

    @property
    def ean(self) -> str | None:
        v = os.environ.get(ENV_EAN) or self.egd.get("ean")
        return str(v) if v is not None else None


KNOWN = {
    "egd": {
        "client_id",
        "client_secret",
        "ean",
        "profile",
        "test_env",
        "api_variant",
        "timestamps",
    },
    "spot": {
        "multiplier",
        "add_czk_mwh",
        "negative_multiplier",
        "negative_add_czk_mwh",
        "monthly_fee",
        "vat",
        "negative",
        "price_resolution",
    },
    "spot.fixed": {"price_czk_kwh", "price_low_czk_kwh", "monthly_fee"},
    "low_tariff": {"hours", "hours_weekend", "holidays"},
}


def load_config(path: Path | None = None) -> Config:
    explicit = path is not None or bool(os.environ.get(ENV_CONFIG))
    path = path or (Path(os.environ[ENV_CONFIG]) if os.environ.get(ENV_CONFIG) else None)
    path = (path or default_config_path()).expanduser()
    if not path.exists():
        if explicit:
            raise ConfigError(f"Config file not found: {path}")
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    cfg = Config(path=path)
    for section, keys in KNOWN.items():
        sec: Any = data
        for part in section.split("."):
            sec = sec.get(part, {}) if isinstance(sec, dict) else None
        if not isinstance(sec, dict):
            raise ConfigError(f"{path}: [{section}] must be a table")
        values = {k: v for k, v in sec.items() if not isinstance(v, dict)}
        unknown = set(values) - keys
        if unknown:
            cfg.warnings.append(
                f"{path}: unknown keys in [{section}]: {', '.join(sorted(unknown))}"
            )
        setattr(cfg, "fixed" if section == "spot.fixed" else section, values)
    for section in set(data) - {s.split(".")[0] for s in KNOWN}:
        cfg.warnings.append(f"{path}: unknown section [{section}]")

    if cfg.egd.get("client_secret") and os.name == "posix":
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            cfg.warnings.append(
                f"{path} contains client_secret and is readable by other users; "
                f"consider: chmod 600 {path}"
            )
    return cfg
