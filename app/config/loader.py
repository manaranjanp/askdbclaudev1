import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DatabaseConfig:
    name: str
    path: Path


def load_databases(config_dir: Path) -> dict[str, DatabaseConfig]:
    databases: dict[str, DatabaseConfig] = {}
    for file in sorted(config_dir.glob("*.yaml")):
        raw: dict[str, Any] = yaml.safe_load(file.read_text())
        name = str(raw["name"])
        # Env references keep machine-specific paths and secrets out of the YAML (SPEC 4.1).
        path = Path(os.path.expandvars(str(raw["path"])))
        databases[name] = DatabaseConfig(name=name, path=path)
    return databases
