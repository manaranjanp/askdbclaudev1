import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config.examples import load_examples
from app.factory import create_app
from app.llm.client import MockLLM
from app.retrieval.embedder import FakeEmbedder
from tests.fixtures.login import logged_in_client

KNOWN = {"sales", "regions"}
GOOD = {"question": "Total by region?", "sql": "SELECT region, SUM(amount) FROM sales GROUP BY region"}


def write_examples(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sample.yaml").write_text(body)
    return directory


def test_valid_examples_are_loaded(tmp_path: Path) -> None:
    body = "examples:\n  - question: Total by region?\n    sql: SELECT region, SUM(amount) FROM sales GROUP BY region\n"
    examples = load_examples(write_examples(tmp_path, body), "sample", KNOWN)

    assert [(e.question, e.sql) for e in examples] == [(GOOD["question"], GOOD["sql"])]


def test_example_with_drop_is_skipped_and_logged_with_file_index_and_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    body = (
        "examples:\n"
        "  - question: Total by region?\n    sql: SELECT region FROM sales\n"
        "  - question: Clear it\n    sql: DROP TABLE sales\n"
    )
    with caplog.at_level(logging.WARNING):
        examples = load_examples(write_examples(tmp_path, body), "sample", KNOWN)

    assert [e.question for e in examples] == ["Total by region?"]
    assert "sample.yaml" in caplog.text
    assert "index 1" in caplog.text
    assert "Only SELECT statements are allowed." in caplog.text


def test_example_with_unknown_table_is_skipped(tmp_path: Path) -> None:
    body = "examples:\n  - question: Secrets?\n    sql: SELECT * FROM salaries\n"

    assert load_examples(write_examples(tmp_path, body), "sample", KNOWN) == []


def test_example_missing_its_sql_is_skipped_without_losing_the_others(tmp_path: Path) -> None:
    body = "examples:\n  - question: No sql here\n  - question: Total by region?\n    sql: SELECT region FROM sales\n"
    examples = load_examples(write_examples(tmp_path, body), "sample", KNOWN)

    assert [e.question for e in examples] == ["Total by region?"]


def test_missing_examples_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_examples(tmp_path / "does-not-exist", "sample", KNOWN) == []


def test_malformed_examples_yaml_is_logged_and_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        examples = load_examples(write_examples(tmp_path, "examples: [unclosed\n"), "sample", KNOWN)

    assert examples == []
    assert "malformed YAML" in caplog.text


def test_app_starts_with_a_bad_example_and_still_answers(tmp_path: Path) -> None:
    db = tmp_path / "sample.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, amount REAL)")
    conn.execute("INSERT INTO sales (region, amount) VALUES ('North', 120.0)")
    conn.commit()
    conn.close()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sample.yaml").write_text(f"name: sample\npath: {db}\n")
    write_examples(
        config_dir / "examples",
        "examples:\n  - question: Clear it\n    sql: DROP TABLE sales\n",
    )
    store_path = tmp_path / "app_store.sqlite"
    app = create_app(config_dir, store_path, MockLLM("SELECT region FROM sales"), embedder=FakeEmbedder())
    client: TestClient = logged_in_client(app, store_path)

    body = client.post("/api/ask", json={"database": "sample", "question": "regions?"}).json()

    assert body["outcome"] == "success"


def test_database_with_no_examples_has_no_examples_text(tmp_path: Path) -> None:
    db = tmp_path / "sample.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT)")
    conn.commit()
    conn.close()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sample.yaml").write_text(f"name: sample\npath: {db}\n")
    seen: dict[str, str] = {}

    class ExamplesRecorder(MockLLM):
        def generate_sql(self, question: str, schema: str, examples: str) -> str:
            seen["examples"] = examples
            return super().generate_sql(question, schema, examples)

    store_path = tmp_path / "app_store.sqlite"
    app = create_app(config_dir, store_path, ExamplesRecorder("SELECT region FROM sales"), embedder=FakeEmbedder())
    client = logged_in_client(app, store_path)

    body = client.post("/api/ask", json={"database": "sample", "question": "regions?"}).json()

    assert body["outcome"] == "success"
    assert seen["examples"] == ""
