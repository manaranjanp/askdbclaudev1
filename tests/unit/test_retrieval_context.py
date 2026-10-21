import logging
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config.examples import ValidExample
from app.config.loader import RetrievalSettings
from app.config.schema import ColumnSpec, JoinHintSpec, MetricSpec, NoteSpec, TableSpec
from app.db.analytics import open_readonly
from app.db.store import AppStore, audit_log
from app.factory import create_app
from app.retrieval.embedder import FakeEmbedder
from app.retrieval.index import DeclaredContext, SchemaIndex
from tests.fixtures.login import logged_in_client

REVENUE = MetricSpec(description="Total sales amount.", synonyms=["sales total"], table="sales", sql="SUM(sales.amount)")
GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"


@pytest.fixture
def engine(tmp_path: Path) -> Any:
    import sqlite3

    path = tmp_path / "retrieval.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE regions (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE sales (id INTEGER PRIMARY KEY, region_id INTEGER REFERENCES regions(id), region TEXT, amount REAL);
        CREATE TABLE archive_notes (id INTEGER PRIMARY KEY, note TEXT);
        CREATE TABLE returns (id INTEGER PRIMARY KEY, reason TEXT);
        """
    )
    conn.commit()
    conn.close()
    return open_readonly(path)


def declared() -> dict[str, TableSpec]:
    return {
        "sales": TableSpec(
            description="Sales rows: one per sale, with the amount in the reporting currency.",
            columns={"amount": ColumnSpec(description="Sale value in the reporting currency.")},
        ),
        "regions": TableSpec(description="Reference list of sales regions.", columns={}),
        "archive_notes": TableSpec(description="Free-text notes from an old project.", columns={}),
    }


def index(engine: Any, metrics: dict[str, MetricSpec] | None = None, examples: list[ValidExample] | None = None) -> SchemaIndex:
    context = DeclaredContext(tables=declared(), metrics=metrics or {"revenue": REVENUE})
    return SchemaIndex.build(engine, context, examples or [], FakeEmbedder())


def settings(top_tables: int = 1) -> RetrievalSettings:
    return RetrievalSettings(top_tables=top_tables)


def test_exact_synonym_forces_metric_and_its_table_into_context(engine: Any) -> None:
    selection = index(engine).select("What is the sales total?", settings(top_tables=1))

    assert [m.name for m in selection.metrics] == ["revenue"]
    assert "metric revenue" in selection.schema_text()
    assert "Definition: SUM(sales.amount)" in selection.schema_text()
    assert "sales" in [t.name for t in selection.tables]


def test_partial_synonym_does_not_force_the_metric(engine: Any) -> None:
    # "sales totals" is not the synonym "sales total" as a whole phrase.
    selection = index(engine).select("sales totals by year", settings(top_tables=1))

    assert selection.metrics == []


def test_no_match_above_threshold_flags_low_confidence_and_keeps_top_tables(engine: Any) -> None:
    selection = index(engine).select("Weather forecast for Paris next week?", settings(top_tables=2))

    assert selection.low_confidence is True
    assert len(selection.tables) >= 2


def test_strong_match_is_not_flagged(engine: Any) -> None:
    selection = index(engine).select("total sales amount by region", settings(top_tables=1))

    assert selection.low_confidence is False


def test_examples_are_ranked_by_question_and_capped(engine: Any) -> None:
    examples = [
        ValidExample(question="Total sales amount by region", sql=GOOD_SQL),
        ValidExample(question="Count of returns by reason", sql="SELECT reason, COUNT(*) FROM returns GROUP BY reason"),
        ValidExample(question="Notes about the old project", sql="SELECT note FROM archive_notes"),
    ]
    selection = index(engine, examples=examples).select("total sales amount by region", RetrievalSettings(max_examples=1))

    assert [e.question for e in selection.examples] == ["Total sales amount by region"]
    assert "Question: Total sales amount by region" in selection.examples_text()


def test_no_examples_gives_empty_text_and_no_error(engine: Any) -> None:
    selection = index(engine, examples=[]).select("total sales amount by region", settings())

    assert selection.examples_text() == ""


def build_app(tmp_path: Path, generate: Any, repair: Any, examples_yaml: str | None = None) -> tuple[Any, Path]:
    db = tmp_path / "retrieval.sqlite"
    import sqlite3

    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, amount REAL);
        CREATE TABLE archive_notes (id INTEGER PRIMARY KEY, note TEXT);
        INSERT INTO sales (region, amount) VALUES ('North', 120.0);
        """
    )
    conn.commit()
    conn.close()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "retrieval.yaml").write_text(
        f"name: retrieval\npath: {db}\n"
        "tables:\n  sales:\n    description: Sales rows.\n    columns:\n      amount:\n        description: Sale value.\n"
        "metrics:\n  revenue:\n    description: Total sales amount.\n    synonyms: [sales total]\n"
        "    table: sales\n    sql: SUM(sales.amount)\n"
    )
    (config_dir / "app.yaml").write_text("retrieval:\n  top_tables: 1\n")
    if examples_yaml is not None:
        (config_dir / "examples").mkdir()
        (config_dir / "examples" / "retrieval.yaml").write_text(examples_yaml)

    class Recorder:
        prompt_version = "test"

        def classify_intent(self, question: str) -> str:
            return "data"

        def explain(self, question: str, sql: str, columns: list, rows: list) -> str:
            return "Explained."

        def generate_sql(self, question: str, schema: str, examples: str) -> str:
            return generate(question, schema, examples)

        def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
            return repair(question, schema, examples, failed_sql, error)

    store_path = tmp_path / "app_store.sqlite"
    client = logged_in_client(create_app(config_dir, store_path, Recorder(), embedder=FakeEmbedder()), store_path)
    return client, store_path


def test_out_of_context_table_triggers_one_full_schema_repair(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def generate(question: str, schema: str, examples: str) -> str:
        seen["generate_schema"] = schema
        return "SELECT note FROM archive_notes"

    def repair(question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        seen["repair_schema"] = schema
        seen["repair_error"] = error
        return "SELECT COUNT(*) AS notes FROM archive_notes"

    client, store_path = build_app(tmp_path, generate, repair)
    body = client.post("/api/ask", json={"database": "retrieval", "question": "total sales"}).json()

    assert "archive_notes" not in seen["generate_schema"]
    assert "archive_notes" in seen["repair_schema"]
    assert "archive_notes" in seen["repair_error"]
    assert body["outcome"] == "success"
    assert body["repair_used"] is True
    assert body["full_schema_repair"] is True
    with AppStore(store_path).engine.connect() as conn:
        row = conn.execute(select(audit_log.c.full_schema_repair, audit_log.c.repair_used)).one()
    assert (bool(row.full_schema_repair), bool(row.repair_used)) == (True, True)


def test_repair_is_given_the_same_examples_as_generation(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    examples_yaml = "examples:\n  - question: Total sales by region\n    sql: SELECT region, SUM(amount) FROM sales GROUP BY region\n"

    def generate(question: str, schema: str, examples: str) -> str:
        seen["generate_examples"] = examples
        return "SELECT nonsense FROM sales"

    def repair(question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        seen["repair_examples"] = examples
        return "SELECT region FROM sales"

    client, _ = build_app(tmp_path, generate, repair, examples_yaml=examples_yaml)
    client.post("/api/ask", json={"database": "retrieval", "question": "total sales by region"})

    assert "Total sales by region" in seen["generate_examples"]
    assert seen["repair_examples"] == seen["generate_examples"]


def test_full_schema_is_not_used_when_retrieval_is_on_and_in_context(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def generate(question: str, schema: str, examples: str) -> str:
        seen["schema"] = schema
        return GOOD_SQL

    client, _ = build_app(tmp_path, generate, lambda *args: GOOD_SQL)
    body = client.post("/api/ask", json={"database": "retrieval", "question": "total sales by region"}).json()

    assert body["outcome"] == "success"
    assert body["full_schema_repair"] is False
    assert "archive_notes" not in seen["schema"]


def test_join_hint_is_included_with_the_tables_it_connects(engine: Any) -> None:
    joins = [JoinHintSpec(from_table="sales", from_column="region_id", to_table="regions", to_column="id", cardinality="one-to-many")]
    context = DeclaredContext(tables=declared(), metrics={}, joins=joins)
    selection = SchemaIndex.build(engine, context, [], FakeEmbedder()).select("total sales amount", settings(top_tables=2))

    assert "join sales.region_id = regions.id (one-to-many)" in selection.schema_text()
    assert "join:sales.region_id=regions.id" in selection.item_ids


def test_join_hint_with_a_missing_table_is_skipped_and_logged(engine: Any, caplog: pytest.LogCaptureFixture) -> None:
    joins = [JoinHintSpec(from_table="sales", from_column="x", to_table="ghost", to_column="id", cardinality="one-to-many")]
    with caplog.at_level(logging.WARNING):
        selection = SchemaIndex.build(engine, DeclaredContext(tables=declared(), joins=joins), [], FakeEmbedder()).select(
            "total sales amount", settings(top_tables=2)
        )

    assert selection.joins == []
    assert "ghost" in caplog.text


NOTE = NoteSpec(description="Revenue is net of returns.", synonyms=[], text="Revenue in this database is net of returns.")


def test_note_is_retrieved_when_the_question_matches_it(engine: Any) -> None:
    context = DeclaredContext(tables=declared(), notes={"net_revenue": NOTE})
    index_ = SchemaIndex.build(engine, context, [], FakeEmbedder())

    matched = index_.select("is revenue net of returns?", settings(top_tables=1))
    unrelated = index_.select("weather in paris", settings(top_tables=1))

    assert [n.name for n in matched.notes] == ["net_revenue"]
    assert "Revenue in this database is net of returns." in matched.schema_text()
    assert unrelated.notes == []
