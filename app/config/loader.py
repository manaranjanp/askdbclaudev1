import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

APP_SETTINGS_FILE = "app.yaml"


@dataclass(frozen=True)
class DatabaseConfig:
    name: str
    path: Path


@dataclass(frozen=True)
class Limits:
    # App-wide, not per database (SPEC 7.1, 7.3). Defaults match the spec for a missing app.yaml.
    row_cap: int = 1000
    query_timeout_seconds: float = 30.0


def load_limits(config_dir: Path) -> Limits:
    file = config_dir / APP_SETTINGS_FILE
    if not file.exists():
        return Limits()
    raw: dict[str, Any] = yaml.safe_load(file.read_text()) or {}
    defaults = Limits()
    return Limits(
        row_cap=int(raw.get("row_cap", defaults.row_cap)),
        query_timeout_seconds=float(raw.get("query_timeout_seconds", defaults.query_timeout_seconds)),
    )


def load_databases(config_dir: Path) -> dict[str, DatabaseConfig]:
    databases: dict[str, DatabaseConfig] = {}
    for file in sorted(config_dir.glob("*.yaml")):
        if file.name == APP_SETTINGS_FILE:
            continue
        raw: dict[str, Any] = yaml.safe_load(file.read_text())
        name = str(raw["name"])
        # Env references keep machine-specific paths and secrets out of the YAML (SPEC 4.1).
        path = Path(os.path.expandvars(str(raw["path"])))
        databases[name] = DatabaseConfig(name=name, path=path)
    return databases
