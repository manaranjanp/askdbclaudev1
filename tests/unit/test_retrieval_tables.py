import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config.loader import RetrievalSettings
from app.config.schema import ColumnSpec, TableSpec
from app.db.analytics import open_readonly
from app.db.store import AppStore, audit_log
from app.factory import create_app
from app.retrieval.embedder import FakeEmbedder, cosine
from app.retrieval.index import DeclaredContext, SchemaIndex
from tests.fixtures.login import logged_in_client

WIDE_COLUMNS = [f"metric_{i}" for i in range(30)]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "retrieval.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE regions (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            region_id INTEGER REFERENCES regions(id),
            region TEXT,
            amount REAL
        );
        CREATE TABLE archive_notes (id INTEGER PRIMARY KEY, note TEXT);
        """
    )
    conn.execute(f"CREATE TABLE wide (id INTEGER PRIMARY KEY, {', '.join(c + ' REAL' for c in WIDE_COLUMNS)})")
    conn.commit()
    conn.close()
    return path


def declared() -> dict[str, TableSpec]:
    return {
        "sales": TableSpec(
            description="Sales rows: one per sale, with the amount in the reporting currency.",
            columns={
                "amount": ColumnSpec(description="Sale value in the reporting currency.", synonyms=["revenue"]),
                "region": ColumnSpec(description="Region name where the sale was made."),
            },
        ),
        "regions": TableSpec(description="Reference list of sales regions.", columns={}),
        "archive_notes": TableSpec(description="Free-text notes from an old project.", columns={}),
    }


def index_for(db: Path) -> SchemaIndex:
    with_engine = open_readonly(db)
    return SchemaIndex.build(with_engine, DeclaredContext(tables=declared()), [], FakeEmbedder())


def settings(top_tables: int = 1, columns_per_table: int = 10, max_tables: int = 8) -> RetrievalSettings:
    return RetrievalSettings(top_tables=top_tables, columns_per_table=columns_per_table, max_tables=max_tables)


def test_most_relevant_table_and_columns_are_selected(db: Path) -> None:
    selection = index_for(db).select("total sales amount by region", settings(top_tables=1))

    assert selection.tables[0].name == "sales"
    assert {c.name for c in selection.columns["sales"]} >= {"amount", "region"}


def test_join_neighbour_is_added_once(db: Path) -> None:
    selection = index_for(db).select("total sales amount by region", settings(top_tables=1))

    names = [t.name for t in selection.tables]
    assert names == ["sales", "regions"]
    assert len(names) == len(set(names))


def test_neighbour_already_selected_is_not_added_twice(db: Path) -> None:
    selection = index_for(db).select("regions and sales amount", settings(top_tables=2))

    names = [t.name for t in selection.tables]
    assert sorted(names) == ["regions", "sales"]
    assert len(names) == len(set(names))


def test_total_tables_are_capped_by_max_tables(db: Path) -> None:
    selection = index_for(db).select("total sales amount by region", settings(top_tables=1, max_tables=1))

    assert [t.name for t in selection.tables] == ["sales"]


def test_wide_table_is_bounded_to_columns_per_table(db: Path) -> None:
    selection = index_for(db).select("metric 7 wide values", settings(top_tables=1, columns_per_table=10))

    assert [t.name for t in selection.tables] == ["wide"]
    assert len(selection.columns["wide"]) == 10


def test_unrelated_table_is_left_out_of_the_schema_text(db: Path) -> None:
    selection = index_for(db).select("total sales amount by region", settings(top_tables=1))

    assert "archive_notes" not in selection.schema_text()
    assert "Sale value in the reporting currency." in selection.schema_text()


def test_item_ids_name_the_tables_and_columns_sent(db: Path) -> None:
    selection = index_for(db).select("total sales amount by region", settings(top_tables=1))

    assert "table:sales" in selection.item_ids
    assert "column:sales.amount" in selection.item_ids
    assert "table:regions" in selection.item_ids


def test_fake_embedder_is_deterministic_and_cosine_ranks_overlap() -> None:
    first = FakeEmbedder().embed(["sales by region"])[0]
    second = FakeEmbedder().embed(["sales by region"])[0]
    other = FakeEmbedder().embed(["archive notes"])[0]

    assert first == second
    assert cosine(first, second) == pytest.approx(1.0)
    assert cosine(first, other) < cosine(first, second)


def test_app_sends_only_retrieved_schema_and_audits_item_ids(db: Path, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "retrieval.yaml").write_text(
        f"name: retrieval\npath: {db}\n"
        "tables:\n"
        "  sales:\n"
        "    description: Sales rows.\n"
        "    columns:\n"
        "      amount:\n"
        "        description: Sale value.\n"
    )
    (config_dir / "app.yaml").write_text("retrieval:\n  top_tables: 1\n")
    seen: dict[str, Any] = {}

    class SchemaRecorder:
        prompt_version = "test"

        def classify_intent(self, question: str) -> str:
            return "data"

        def generate_sql(self, question: str, schema: str, examples: str) -> str:
            seen["schema"] = schema
            return "SELECT region, SUM(amount) FROM sales GROUP BY region"

        def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
            return failed_sql

    store_path = tmp_path / "app_store.sqlite"
    client = logged_in_client(
        create_app(config_dir, store_path, SchemaRecorder(), embedder=FakeEmbedder()),
        store_path,
    )
    client.post("/api/ask", json={"database": "retrieval", "question": "total sales amount by region"})

    assert "sales" in seen["schema"]
    assert "archive_notes" not in seen["schema"]
    with AppStore(store_path).engine.connect() as conn:
        items = conn.execute(select(audit_log.c.retrieved_items)).scalar_one()
    assert "table:sales" in items
    assert "column:sales.amount" in items
