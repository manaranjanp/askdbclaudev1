from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config.loader import Limits
from app.db.analytics import open_readonly
from app.db.store import AppStore, audit_log
from app.llm.client import GenerationError, LLMUnavailable
from app.pipeline.run import AI_UNAVAILABLE_MESSAGE, Pipeline
from tests.fixtures.make_sample_db import make_sample_db

BAD_SQL = "SELECT region, SUM(total) FROM sales GROUP BY region"
GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"
RUNAWAY_SQL = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
    "SELECT x FROM c WHERE x < 0"
)


class ScriptedLLM:
    # Generation and repair replies come from one script, in call order. Every call is recorded.
    prompt_version = "test"

    def __init__(self, *script: str | Exception) -> None:
        self._script = list(script)
        self.calls: list[str] = []
        self.repair_inputs: list[tuple[str, str]] = []

    def classify_intent(self, question: str) -> str:
        return "data"

    def explain(self, question: str, sql: str, columns: list, rows: list) -> str:
        return "Explained."

    def generate_sql(self, question: str, schema: str, examples: str) -> str:
        self.calls.append("generate")
        return self._next()

    def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        self.calls.append("repair")
        self.repair_inputs.append((failed_sql, error))
        return self._next()

    def _next(self) -> str:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def make_pipeline(tmp_path: Path) -> Any:
    db = tmp_path / "sample.sqlite"
    make_sample_db(db)
    store_path = tmp_path / "app_store.sqlite"

    def build(llm: ScriptedLLM, limits: Limits | None = None) -> Pipeline:
        return Pipeline({"sample": open_readonly(db)}, llm, AppStore(store_path), limits or Limits(), unavailable={})

    return build


def audit_rows(tmp_path: Path) -> list[tuple[str, str | None, bool]]:
    with AppStore(tmp_path / "app_store.sqlite").engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.outcome, audit_log.c.sql, audit_log.c.repair_used))
        return [(r.outcome, r.sql, bool(r.repair_used)) for r in rows]


def test_bad_then_good_gives_one_repair_and_success(make_pipeline: Any, tmp_path: Path) -> None:
    llm = ScriptedLLM(BAD_SQL, GOOD_SQL)

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "success"
    assert answer.repair_used is True
    assert llm.calls == ["generate", "repair"]
    assert audit_rows(tmp_path) == [("success", GOOD_SQL, True)]


def test_repair_sees_the_failed_sql_and_its_error(make_pipeline: Any) -> None:
    llm = ScriptedLLM(BAD_SQL, GOOD_SQL)

    make_pipeline(llm).ask("analyst", "sample", "Totals?")

    failed_sql, error = llm.repair_inputs[0]
    assert failed_sql == BAD_SQL
    assert "no such column" in error.lower()


def test_bad_twice_gives_failure_with_sql_and_no_third_call(make_pipeline: Any, tmp_path: Path) -> None:
    llm = ScriptedLLM(BAD_SQL, BAD_SQL, GOOD_SQL)

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "execution error"
    assert answer.sql == BAD_SQL
    assert answer.repair_used is True
    assert llm.calls == ["generate", "repair"]
    assert audit_rows(tmp_path) == [("execution error", BAD_SQL, True)]


def test_repair_returning_a_write_is_rejected_by_the_validator(make_pipeline: Any, tmp_path: Path) -> None:
    llm = ScriptedLLM("SELECT * FROM nowhere", "DELETE FROM sales")

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "validation failure"
    assert answer.sql == "DELETE FROM sales"
    assert answer.error == "Only SELECT statements are allowed."
    assert llm.calls == ["generate", "repair"]


def test_first_attempt_that_works_is_not_repaired(make_pipeline: Any) -> None:
    llm = ScriptedLLM(GOOD_SQL)

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "success"
    assert answer.repair_used is False
    assert llm.calls == ["generate"]


def test_timeout_is_not_repaired(make_pipeline: Any) -> None:
    llm = ScriptedLLM(RUNAWAY_SQL)

    answer = make_pipeline(llm, Limits(query_timeout_seconds=0.2)).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "timeout"
    assert llm.calls == ["generate"]


def test_outage_during_repair_is_reported(make_pipeline: Any, tmp_path: Path) -> None:
    llm = ScriptedLLM(BAD_SQL, LLMUnavailable("down"))

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "execution error"
    assert answer.error == AI_UNAVAILABLE_MESSAGE
    assert audit_rows(tmp_path) == [("execution error", BAD_SQL, True)]


def test_empty_repair_keeps_the_original_failure(make_pipeline: Any) -> None:
    llm = ScriptedLLM(BAD_SQL, GenerationError("The model did not return a SQL statement."))

    answer = make_pipeline(llm).ask("analyst", "sample", "Totals?")

    assert answer.outcome == "execution error"
    assert "no such column" in (answer.error or "").lower()
    assert answer.sql == BAD_SQL
    assert answer.repair_used is True
