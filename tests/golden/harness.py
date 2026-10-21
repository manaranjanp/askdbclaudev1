from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.db.analytics import open_readonly
from app.db.store import AppStore
from app.pipeline.run import OUTCOME_SUCCESS, Answer, Pipeline

CASES_PATH = Path(__file__).parent / "cases.yaml"
GOLDEN_RESULTS = pytest.StashKey[tuple[int, int]]()
GATE = 0.95
# The 95% gate only applies once the set is big enough for a rate to mean something (PLAN slice 2).
GATE_MIN_CASES = 10


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    sql: str
    expect: dict[str, Any]


class RecordedLLM:
    # Replay mode: the SQL is recorded in cases.yaml, so the suite needs no API key.
    def __init__(self, sql: str) -> None:
        self._sql = sql

    def generate_sql(self, question: str) -> str:
        return self._sql


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    return [
        Case(
            id=item["id"],
            question=item["question"],
            sql=item["sql"],
            expect=item["expect"],
        )
        for item in raw["cases"]
    ]


def run_case(case: Case, db_path: Path, store_path: Path) -> Answer:
    pipeline = Pipeline(
        {"sample": open_readonly(db_path)},
        RecordedLLM(case.sql),
        AppStore(store_path),
    )
    return pipeline.ask(user="golden", database="sample", question=case.question)


def mismatches(case: Case, answer: Answer) -> list[str]:
    problems: list[str] = []
    for key, expected in case.expect.items():
        actual = _actual(answer, key)
        if actual != expected:
            problems.append(f"{key}: expected {expected!r}, got {actual!r}")
    return problems


def _actual(answer: Answer, key: str) -> Any:
    if key == "outcome":
        return answer.outcome
    if key == "columns":
        return answer.columns if answer.outcome == OUTCOME_SUCCESS else None
    if key == "rows":
        return (
            [list(row) for row in answer.rows]
            if answer.outcome == OUTCOME_SUCCESS
            else None
        )
    if key == "error":
        return answer.error
    raise KeyError(f"Unknown golden expectation: {key}")
