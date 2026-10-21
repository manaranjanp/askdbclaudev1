import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.config.schema import DatabaseSpec, TableSpec

APP_SETTINGS_FILE = "app.yaml"


@dataclass(frozen=True)
class Limits:
    # App-wide, not per database (SPEC 7.1, 7.3). Defaults match the spec for a missing app.yaml.
    row_cap: int = 1000
    query_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DatabaseConfig:
    # name is the file stem when the file cannot be read, so a broken file still has a row in the picker.
    name: str
    file: Path
    path: Path | None
    tables: dict[str, TableSpec]
    error: str | None = None


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


DEFAULT_LLM_MODEL = "claude-sonnet-5-5"


def load_llm_model(config_dir: Path) -> str:
    # One model for the whole app, chosen in app config (SPEC 10.1).
    file = config_dir / APP_SETTINGS_FILE
    if not file.exists():
        return DEFAULT_LLM_MODEL
    raw: dict[str, Any] = yaml.safe_load(file.read_text()) or {}
    return str(raw.get("llm_model", DEFAULT_LLM_MODEL))


def load_databases(config_dir: Path) -> list[DatabaseConfig]:
    return [_load_one(file) for file in sorted(config_dir.glob("*.yaml")) if file.name != APP_SETTINGS_FILE]


def _load_one(file: Path) -> DatabaseConfig:
    try:
        raw = yaml.safe_load(file.read_text())
    except yaml.YAMLError as exc:
        return _broken(file, _yaml_reason(file, exc))
    try:
        spec = DatabaseSpec.model_validate(raw)
    except ValidationError as exc:
        return _broken(file, f"{file.name}: {_validation_reason(exc)}")
    # Env references keep machine-specific paths and secrets out of the YAML (SPEC 4.1).
    path = Path(os.path.expandvars(spec.path))
    return DatabaseConfig(name=spec.name, file=file, path=path, tables=spec.tables)


def _broken(file: Path, reason: str) -> DatabaseConfig:
    return DatabaseConfig(name=file.stem, file=file, path=None, tables={}, error=reason)


def _yaml_reason(file: Path, exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        return f"{file.name}: malformed YAML at line {exc.problem_mark.line + 1}: {exc.problem}"
    return f"{file.name}: malformed YAML: {exc}"


def _validation_reason(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors())
