from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config.loader import Limits
from app.db.analytics import table_names
from app.db.store import AppStore, AuditEntry
from app.llm.client import LLMClient
from app.pipeline.execute import QueryTimeout, run_select
from app.pipeline.validate import validate

OUTCOME_SUCCESS = "success"
OUTCOME_VALIDATION_FAILURE = "validation failure"
OUTCOME_EXECUTION_ERROR = "execution error"
OUTCOME_TIMEOUT = "timeout"


@dataclass(frozen=True)
class Answer:
    outcome: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False
    total_rows: int | None = None


class Pipeline:
    def __init__(
        self,
        engines: Mapping[str, Engine],
        llm: LLMClient,
        store: AppStore,
        limits: Limits,
        unavailable: Mapping[str, str],
    ) -> None:
        self._engines = engines
        self._llm = llm
        self._store = store
        self._limits = limits
        self._unavailable = unavailable

    def ask(self, user: str, database: str, question: str) -> Answer:
        answer = self._answer(database, question)
        self._store.log(
            AuditEntry(
                user=user,
                database=database,
                question=question,
                outcome=answer.outcome,
                sql=answer.sql,
                row_count=len(answer.rows) if answer.outcome == OUTCOME_SUCCESS else None,
                error=answer.error,
            )
        )
        return answer

    def _answer(self, database: str, question: str) -> Answer:
        # Stage order is fixed (CLAUDE.md guardrail 2). Intent (slice 7) and retrieval (slice 9)
        # are not built yet and pass straight through. Repair (slice 8) is not built yet either.
        if database in self._unavailable:
            return Answer(OUTCOME_EXECUTION_ERROR, error=f"Database unavailable: {self._unavailable[database]}")
        engine = self._engines.get(database)
        if engine is None:
            return Answer(OUTCOME_EXECUTION_ERROR, error=f"Unknown database: {database}")
        sql = self._llm.generate_sql(question)
        try:
            known_tables = table_names(engine)
        except SQLAlchemyError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, sql=sql, error=str(exc))
        verdict = validate(sql, known_tables)
        if not verdict.ok:
            return Answer(OUTCOME_VALIDATION_FAILURE, sql=sql, error=verdict.reason)
        try:
            result = run_select(engine, sql, self._limits)
        except QueryTimeout:
            return Answer(
                OUTCOME_TIMEOUT,
                sql=sql,
                error="The query took too long and was stopped. Try narrowing the question.",
            )
        except SQLAlchemyError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, sql=sql, error=str(exc))
        return Answer(
            OUTCOME_SUCCESS,
            sql=sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            total_rows=result.total_rows,
        )
