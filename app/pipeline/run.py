from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config.loader import Limits
from app.db.analytics import describe_schema, table_names
from app.db.store import AppStore, AuditEntry
from app.llm.client import GenerationError, LLMClient, LLMUnavailable
from app.pipeline.execute import QueryTimeout, run_select
from app.pipeline.intent import DECLINE_MESSAGE, NO_MATCHING_RECORDS, parse_intent
from app.pipeline.validate import validate
from app.retrieval.index import Retrieval

OUTCOME_SUCCESS = "success"
OUTCOME_DECLINED = "declined: non-data"
OUTCOME_VALIDATION_FAILURE = "validation failure"
OUTCOME_EXECUTION_ERROR = "execution error"
OUTCOME_TIMEOUT = "timeout"

# Only these failures get the one repair attempt. A timeout is not repaired; it asks the user to narrow the question.
REPAIRABLE_OUTCOMES = (OUTCOME_VALIDATION_FAILURE, OUTCOME_EXECUTION_ERROR)

AI_UNAVAILABLE_MESSAGE = "The AI service is unavailable. Try again shortly."


@dataclass(frozen=True)
class Answer:
    outcome: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    error: str | None = None
    message: str | None = None
    truncated: bool = False
    total_rows: int | None = None
    repair_used: bool = False


class Pipeline:
    def __init__(
        self,
        engines: Mapping[str, Engine],
        llm: LLMClient,
        store: AppStore,
        limits: Limits,
        unavailable: Mapping[str, str],
        retrieval: Retrieval | None = None,
    ) -> None:
        self._engines = engines
        self._llm = llm
        self._store = store
        self._limits = limits
        self._unavailable = unavailable
        self._retrieval = retrieval

    def ask(self, user: str, database: str, question: str) -> Answer:
        retrieved: list[str] = []
        answer = self._answer(database, question, retrieved)
        self._store.log(
            AuditEntry(
                user=user,
                database=database,
                question=question,
                outcome=answer.outcome,
                sql=answer.sql,
                row_count=len(answer.rows) if answer.outcome == OUTCOME_SUCCESS else None,
                error=answer.error,
                prompt_version=self._llm.prompt_version,
                repair_used=answer.repair_used,
                retrieved_items=",".join(retrieved) or None,
            )
        )
        return answer

    def _answer(self, database: str, question: str, retrieved: list[str]) -> Answer:
        # Stage order is fixed (CLAUDE.md guardrail 2). Retrieval (slice 9) is not built yet and passes
        # straight through. The explanation stage waits on SPEC Q2.
        if database in self._unavailable:
            return Answer(OUTCOME_EXECUTION_ERROR, error=f"Database unavailable: {self._unavailable[database]}")
        engine = self._engines.get(database)
        if engine is None:
            return Answer(OUTCOME_EXECUTION_ERROR, error=f"Unknown database: {database}")
        try:
            intent = parse_intent(self._llm.classify_intent(question))
        except LLMUnavailable:
            return Answer(OUTCOME_EXECUTION_ERROR, error=AI_UNAVAILABLE_MESSAGE)
        if intent != "data":
            # The decline text never repeats the question, so injected text is not echoed back.
            return Answer(OUTCOME_DECLINED, message=DECLINE_MESSAGE)
        try:
            schema, item_ids = self._context(database, engine, question)
            known_tables = table_names(engine)
        except SQLAlchemyError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, error=str(exc))
        retrieved.extend(item_ids)
        try:
            sql = self._llm.generate_sql(question, schema)
        except LLMUnavailable:
            # Nothing was generated, so nothing is recorded as an executed query (SPEC 10.3).
            return Answer(OUTCOME_EXECUTION_ERROR, error=AI_UNAVAILABLE_MESSAGE)
        except GenerationError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, error=str(exc))

        answer = self._attempt(engine, sql, known_tables)
        if answer.outcome not in REPAIRABLE_OUTCOMES or answer.sql is None:
            return answer
        return self._repair(engine, question, schema, known_tables, answer)

    def _context(self, database: str, engine: Engine, question: str) -> tuple[str, list[str]]:
        # With retrieval, only the question's tables and columns are sent (SPEC 10.1). Without it, the whole schema.
        if self._retrieval is not None and database in self._retrieval.indexes:
            selection = self._retrieval.indexes[database].select(question, self._retrieval.settings)
            return selection.schema_text(), selection.item_ids
        return describe_schema(engine), []

    def _repair(
        self, engine: Engine, question: str, schema: str, known_tables: set[str], failed: Answer
    ) -> Answer:
        # Exactly one repair call. Its result is final, whether it runs or not (SPEC 5.7, slice 8).
        assert failed.sql is not None
        try:
            repaired_sql = self._llm.repair_sql(question, schema, failed.sql, failed.error or "")
        except LLMUnavailable:
            return Answer(OUTCOME_EXECUTION_ERROR, sql=failed.sql, error=AI_UNAVAILABLE_MESSAGE, repair_used=True)
        except GenerationError:
            return replace(failed, repair_used=True)
        return replace(self._attempt(engine, repaired_sql, known_tables), repair_used=True)

    def _attempt(self, engine: Engine, sql: str, known_tables: set[str]) -> Answer:
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
            message=None if result.rows else NO_MATCHING_RECORDS,
            truncated=result.truncated,
            total_rows=result.total_rows,
        )

