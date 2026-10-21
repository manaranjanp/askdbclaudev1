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
    # True when the SQL reads a table that was not in the retrieved context (SPEC 6.2).
    out_of_context: bool = False
    retrieved: list[str] = field(default_factory=list)
    low_retrieval_confidence: bool = False
    full_schema_repair: bool = False
    # None when there are no rows to explain, or the model was unavailable. The rows are still returned.
    explanation: str | None = None


@dataclass
class _Trace:
    # Filled in while the question is answered, then copied onto the answer and the audit row.
    retrieved: list[str] = field(default_factory=list)
    low_confidence: bool = False
    full_schema_repair: bool = False


@dataclass(frozen=True)
class _Context:
    schema: str
    examples: str
    item_ids: list[str]
    # Lower-cased tables in context. None means the whole schema is in context.
    context_tables: frozenset[str] | None
    low_confidence: bool


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
        trace = _Trace()
        answer = self._answer(database, question, trace)
        answer = replace(
            answer,
            retrieved=trace.retrieved,
            low_retrieval_confidence=trace.low_confidence,
            full_schema_repair=trace.full_schema_repair,
        )
        answer = self._explain(question, answer)
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
                retrieved_items=",".join(answer.retrieved) or None,
                low_retrieval_confidence=answer.low_retrieval_confidence,
                full_schema_repair=answer.full_schema_repair,
            )
        )
        return answer

    def _answer(self, database: str, question: str, trace: _Trace) -> Answer:
        # Stage order is fixed (CLAUDE.md guardrail 2). The explanation stage waits on SPEC Q2.
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
            context = self._context(database, engine, question)
            known_tables = table_names(engine)
        except SQLAlchemyError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, error=str(exc))
        trace.retrieved = context.item_ids
        trace.low_confidence = context.low_confidence
        try:
            sql = self._llm.generate_sql(question, context.schema, context.examples)
        except LLMUnavailable:
            # Nothing was generated, so nothing is recorded as an executed query (SPEC 10.3).
            return Answer(OUTCOME_EXECUTION_ERROR, error=AI_UNAVAILABLE_MESSAGE)
        except GenerationError as exc:
            return Answer(OUTCOME_EXECUTION_ERROR, error=str(exc))

        answer = self._attempt(engine, sql, known_tables, context.context_tables)
        if answer.outcome not in REPAIRABLE_OUTCOMES or answer.sql is None:
            return answer
        return self._repair(engine, question, context, known_tables, answer, trace)

    def _explain(self, question: str, answer: Answer) -> Answer:
        # Runs after the answer is final and before it is logged (CLAUDE.md guardrail 2).
        # Zero rows get the "No matching records" message instead, so nothing is sent to the model.
        if answer.outcome != OUTCOME_SUCCESS or not answer.rows or answer.sql is None:
            return answer
        try:
            text = self._llm.explain(question, answer.sql, answer.columns, answer.rows)
        except LLMUnavailable:
            return answer
        return replace(answer, explanation=text or None)

    def _context(self, database: str, engine: Engine, question: str) -> _Context:
        # With retrieval, only the question's tables, columns, metrics and examples are sent (SPEC 10.1).
        if self._retrieval is not None and database in self._retrieval.indexes:
            selection = self._retrieval.indexes[database].select(question, self._retrieval.settings)
            return _Context(
                schema=selection.schema_text(),
                examples=selection.examples_text(),
                item_ids=selection.item_ids,
                context_tables=frozenset(table.name.lower() for table in selection.tables),
                low_confidence=selection.low_confidence,
            )
        return _Context(
            schema=describe_schema(engine),
            examples="",
            item_ids=[],
            context_tables=None,
            low_confidence=False,
        )

    def _attempt(
        self, engine: Engine, sql: str, known_tables: set[str], context_tables: frozenset[str] | None
    ) -> Answer:
        verdict = validate(sql, known_tables)
        if not verdict.ok:
            return Answer(OUTCOME_VALIDATION_FAILURE, sql=sql, error=verdict.reason)
        if context_tables is not None:
            missing = verdict.tables - context_tables
            if missing:
                return Answer(
                    OUTCOME_VALIDATION_FAILURE,
                    sql=sql,
                    error=f"Table not in the schema provided: {', '.join(sorted(missing))}",
                    out_of_context=True,
                )
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

    def _repair(
        self,
        engine: Engine,
        question: str,
        context: _Context,
        known_tables: set[str],
        failed: Answer,
        trace: _Trace,
    ) -> Answer:
        # Exactly one repair call. Its result is final, whether it runs or not (SPEC 5.7, slice 8).
        assert failed.sql is not None
        schema, context_tables = context.schema, context.context_tables
        if failed.out_of_context:
            # A table outside the retrieved context: the repair sees the whole schema (SPEC 5.9).
            schema, context_tables = describe_schema(engine), None
            trace.full_schema_repair = True
        try:
            repaired_sql = self._llm.repair_sql(question, schema, context.examples, failed.sql, failed.error or "")
        except LLMUnavailable:
            return Answer(OUTCOME_EXECUTION_ERROR, sql=failed.sql, error=AI_UNAVAILABLE_MESSAGE, repair_used=True)
        except GenerationError:
            return replace(failed, repair_used=True)
        repaired = self._attempt(engine, repaired_sql, known_tables, context_tables)
        return replace(repaired, repair_used=True)
