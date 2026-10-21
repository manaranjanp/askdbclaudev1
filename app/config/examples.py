import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from app.pipeline.validate import validate

logger = logging.getLogger(__name__)


class ExampleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sql: str


@dataclass(frozen=True)
class ValidExample:
    question: str
    sql: str


def load_examples(examples_dir: Path, database: str, known_tables: set[str]) -> list[ValidExample]:
    # One file per database, config/examples/<database>.yaml (SPEC 5.8). A missing file is not an error.
    file = examples_dir / f"{database}.yaml"
    if not file.exists():
        return []
    try:
        raw: dict[str, Any] = yaml.safe_load(file.read_text()) or {}
    except yaml.YAMLError as exc:
        logger.warning("%s: examples skipped, malformed YAML: %s", file, exc)
        return []

    valid: list[ValidExample] = []
    for index, item in enumerate(raw.get("examples") or []):
        try:
            entry = ExampleEntry.model_validate(item)
        except ValidationError as exc:
            logger.warning("%s: example index %d skipped: %s", file, index, exc.errors()[0]["msg"])
            continue
        # Examples go through the same validator as generated SQL, so a DROP in an example is rejected.
        verdict = validate(entry.sql, known_tables)
        if not verdict.ok:
            logger.warning("%s: example index %d skipped: %s", file, index, verdict.reason)
            continue
        valid.append(ValidExample(question=entry.question, sql=entry.sql))
    return valid
