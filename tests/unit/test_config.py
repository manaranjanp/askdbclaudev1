import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.catalog_check import check_catalog
from app.factory import create_app
from app.llm.client import MockLLM

GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"

SALES_CONFIG = """\
name: {name}
path: {path}
tables:
  sales:
    description: Sales rows.
    columns:
      id:
        description: Sale identifier.
      region:
        description: Region name.
      amount:
        description: Sale value.
"""


def make_sales_db(path: Path, columns: str = "id INTEGER PRIMARY KEY, region TEXT, amount REAL") -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE sales ({columns})")
        if "amount" in columns:
            conn.execute("INSERT INTO sales (region, amount) VALUES ('North', 120.0), ('South', 200.0)")
        conn.commit()
    finally:
        conn.close()
    return path


def make_config_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    for file_name, body in files.items():
        (directory / file_name).write_text(body)
    return directory


def client_for(config_dir: Path, tmp_path: Path, sql: str = GOOD_SQL) -> TestClient:
    return TestClient(create_app(config_dir, tmp_path / "app_store.sqlite", MockLLM(sql)))


def database_rows(client: TestClient) -> dict[str, Any]:
    return {row["name"]: row for row in client.get("/api/databases").json()}


def test_malformed_yaml_reports_file_and_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config_dir = make_config_dir(tmp_path, {"broken.yaml": "name: broken\npath: x\n  tables: oops\n"})

    with caplog.at_level(logging.WARNING):
        catalog = check_catalog(config_dir)

    status = catalog.statuses[0]
    assert status.name == "broken"
    assert not status.available
    assert status.reason is not None
    assert "broken.yaml" in status.reason
    assert "line 3" in status.reason
    assert "broken.yaml" in caplog.text


def test_missing_required_key_is_reported_with_its_path(tmp_path: Path) -> None:
    body = "name: sales\npath: /nowhere.sqlite\ntables:\n  sales:\n    description: Sales.\n    columns:\n      id: {}\n"
    catalog = check_catalog(make_config_dir(tmp_path, {"sales.yaml": body}))

    reason = catalog.statuses[0].reason
    assert reason is not None
    assert "sales.yaml" in reason
    assert "tables.sales.columns.id.description" in reason


def test_missing_column_is_excluded_and_logged_while_app_starts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db = make_sales_db(tmp_path / "sales.sqlite", columns="id INTEGER PRIMARY KEY, region TEXT")
    config_dir = make_config_dir(tmp_path, {"sales.yaml": SALES_CONFIG.format(name="sales", path=db)})

    with caplog.at_level(logging.WARNING):
        client = client_for(config_dir, tmp_path)

    row = database_rows(client)["sales"]
    assert row["available"] is True
    assert row["excluded"] == ["sales.amount"]
    assert "sales.amount" in caplog.text


def test_missing_table_is_excluded(tmp_path: Path) -> None:
    db = make_sales_db(tmp_path / "sales.sqlite")
    body = SALES_CONFIG.format(name="sales", path=db) + "  returns:\n    description: Returns.\n    columns: {}\n"
    client = client_for(make_config_dir(tmp_path, {"sales.yaml": body}), tmp_path)

    assert database_rows(client)["sales"]["excluded"] == ["returns"]


def test_missing_database_file_is_unavailable_with_reason(tmp_path: Path) -> None:
    missing = tmp_path / "gone.sqlite"
    config_dir = make_config_dir(tmp_path, {"sales.yaml": SALES_CONFIG.format(name="sales", path=missing)})
    client = client_for(config_dir, tmp_path)

    row = database_rows(client)["sales"]
    assert row["available"] is False
    assert "database file not found" in row["reason"]
    assert str(missing) in row["reason"]


def test_question_to_unavailable_database_is_refused_and_audited(tmp_path: Path) -> None:
    config_dir = make_config_dir(
        tmp_path, {"sales.yaml": SALES_CONFIG.format(name="sales", path=tmp_path / "gone.sqlite")}
    )
    store_path = tmp_path / "app_store.sqlite"
    with TestClient(create_app(config_dir, store_path, MockLLM(GOOD_SQL))) as client:
        body = client.post("/api/ask", json={"database": "sales", "question": "Totals?"}).json()

    assert body["outcome"] == "execution error"
    assert body["error"].startswith("Database unavailable: sales.yaml")
    assert body["rows"] == []


def test_other_databases_keep_working(tmp_path: Path) -> None:
    db = make_sales_db(tmp_path / "sales.sqlite")
    config_dir = make_config_dir(
        tmp_path,
        {
            "good.yaml": SALES_CONFIG.format(name="sales", path=db),
            "bad.yaml": SALES_CONFIG.format(name="archive", path=tmp_path / "missing.sqlite"),
        },
    )
    client = client_for(config_dir, tmp_path)

    rows = database_rows(client)
    assert rows["sales"]["available"] is True
    assert rows["archive"]["available"] is False
    ask = client.post("/api/ask", json={"database": "sales", "question": "Totals?"}).json()
    assert ask["outcome"] == "success"
    assert ask["rows"] == [["North", 120.0], ["South", 200.0]]


def test_duplicate_name_keeps_the_first_definition(tmp_path: Path) -> None:
    db = make_sales_db(tmp_path / "sales.sqlite")
    config_dir = make_config_dir(
        tmp_path,
        {
            "a.yaml": SALES_CONFIG.format(name="sales", path=db),
            "b.yaml": SALES_CONFIG.format(name="sales", path=tmp_path / "missing.sqlite"),
        },
    )
    client = client_for(config_dir, tmp_path)

    ask = client.post("/api/ask", json={"database": "sales", "question": "Totals?"}).json()
    assert ask["outcome"] == "success"
    reasons = [row["reason"] for row in client.get("/api/databases").json() if row["name"] == "sales"]
    assert reasons[0] is None
    assert len(reasons) == 2
    assert "already used" in reasons[1]
