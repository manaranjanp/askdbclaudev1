from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.config.loader import Limits, RetrievalSettings, load_llm_model
from app.config.schema import MetricSpec
from app.db.analytics import open_readonly
from app.db.store import AppStore
from app.llm.anthropic_client import AnthropicLLM
from app.llm.client import LLMClient
from app.pipeline.run import OUTCOME_SUCCESS, Answer, Pipeline
from app.retrieval.embedder import FakeEmbedder
from app.retrieval.index import DeclaredContext, Retrieval, SchemaIndex

CASES_PATH = Path(__file__).parent / "cases.yaml"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
GOLDEN_RESULTS = pytest.StashKey[tuple[int, int]]()
GATE = 0.95
# The 95% gate only applies once the set is big enough for a rate to mean something (PLAN slice 2).
GATE_MIN_CASES = 10

# The metric the retrieval cases use. Its synonym is what the synonym case must match exactly.
GOLDEN_METRICS = {
    "revenue": MetricSpec(
        description="Total sales amount.",
        synonyms=["sales total"],
        table="sales",
        sql="SUM(sales.amount)",
    ),
}


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    sql: list[str]
    expect: dict[str, Any]
    intent: str = "data"
    retrieval: bool = False


class RecordedLLM:
    # Replay mode: the SQL and intent are recorded in cases.yaml, so the suite needs no API key.
    # Replies come in order (generation, then repair). Once they run out, the last one repeats.
    prompt_version = "recorded"

    def __init__(self, sql: list[str], intent: str) -> None:
        self._sql = list(sql)
        self._intent = intent
        # The schema the model was last given, so cases can check what was sent.
        self.seen_schema = ""

    def classify_intent(self, question: str) -> str:
        return self._intent

    def generate_sql(self, question: str, schema: str, examples: str) -> str:
        self.seen_schema = schema
        return self._next_sql()

    def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        self.seen_schema = schema
        return self._next_sql()

    def _next_sql(self) -> str:
        return self._sql.pop(0) if len(self._sql) > 1 else self._sql[0]


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    return [
        Case(
            id=item["id"],
            question=item["question"],
            sql=_as_list(item.get("sql", "")),
            expect=item["expect"],
            intent=item.get("intent", "data"),
            retrieval=bool(item.get("retrieval", False)),
        )
        for item in raw["cases"]
    ]


def _as_list(sql: str | list[str]) -> list[str]:
    return [sql] if isinstance(sql, str) else list(sql)


def _retrieval(engine: Engine) -> Retrieval:
    # Fake embedder and fixed metrics, so the retrieval cases replay the same way every run.
    index = SchemaIndex.build(engine, DeclaredContext(metrics=GOLDEN_METRICS), [], FakeEmbedder())
    return Retrieval(indexes={"sample": index}, settings=RetrievalSettings())


def run_case(case: Case, db_path: Path, store_path: Path, live: bool = False) -> tuple[Answer, LLMClient]:
    # Live mode asks the real model and ignores the recorded SQL; it needs ANTHROPIC_API_KEY.
    llm: LLMClient = AnthropicLLM(model=load_llm_model(CONFIG_DIR)) if live else RecordedLLM(case.sql, case.intent)
    engine = open_readonly(db_path)
    pipeline = Pipeline(
        {"sample": engine},
        llm,
        AppStore(store_path),
        Limits(),
        unavailable={},
        retrieval=_retrieval(engine) if case.retrieval else None,
    )
    answer = pipeline.ask(user="golden", database="sample", question=case.question)
    return answer, llm


def mismatches(case: Case, answer: Answer, llm: LLMClient) -> list[str]:
    problems: list[str] = []
    for key, expected in case.expect.items():
        if key == "schema_contains":
            # Every listed text must appear in the schema the model was given.
            seen = getattr(llm, "seen_schema", "")
            missing = [text for text in expected if text not in seen]
            if missing:
                problems.append(f"schema_contains: missing {missing!r}")
            continue
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
    if key == "message":
        return answer.message
    if key == "repair_used":
        return answer.repair_used
    if key == "full_schema_repair":
        return answer.full_schema_repair
    if key == "low_retrieval_confidence":
        return answer.low_retrieval_confidence
    if key == "row_count":
        return len(answer.rows) if answer.outcome == OUTCOME_SUCCESS else None
    if key == "truncated":
        return answer.truncated
    if key == "total_rows":
        return answer.total_rows
    raise KeyError(f"Unknown golden expectation: {key}")
