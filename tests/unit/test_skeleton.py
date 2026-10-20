import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db.analytics import open_readonly
from app.db.store import AppStore, audit_log
from app.factory import create_app
from app.llm.client import MockLLM
from tests.fixtures.make_sample_db import make_sample_db

GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"


@pytest.fixture
def sample_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sample.sqlite"
    make_sample_db(path)
    monkeypatch.setenv("SAMPLE_DB_PATH", str(path))
    return path


@pytest.fixture
def config_dir(tmp_path: Path, sample_db: Path) -> Path:
    # Points at the env var so the test also covers ${VAR} expansion in the loader.
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "sample.yaml").write_text("name: sample\npath: ${SAMPLE_DB_PATH}\n")
    return directory


def build_client(config_dir: Path, store_path: Path, sql: str) -> TestClient:
    return TestClient(create_app(config_dir, store_path, MockLLM(sql)))


def audit_rows(store_path: Path) -> list[tuple[str, str, str | None, int | None]]:
    store = AppStore(store_path)
    with store.engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.user, audit_log.c.outcome, audit_log.c.sql, audit_log.c.row_count))
        return [(r.user, r.outcome, r.sql, r.row_count) for r in rows]


def sales_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0])
    finally:
        conn.close()


def test_select_runs_returns_rows_and_is_audited(config_dir: Path, tmp_path: Path) -> None:
    store_path = tmp_path / "app_store.sqlite"
    with build_client(config_dir, store_path, GOOD_SQL) as client:
        response = client.post("/api/ask", json={"database": "sample", "question": "Total by region?"})

    body = response.json()
    assert response.status_code == 200
    assert body["outcome"] == "success"
    assert body["columns"] == ["region", "total"]
    assert body["rows"] == [["North", 200.0], ["South", 200.0], ["West", 50.0]]
    assert audit_rows(store_path) == [("dev", "success", GOOD_SQL, 3)]


def test_drop_is_rejected_not_executed_and_audited(config_dir: Path, sample_db: Path, tmp_path: Path) -> None:
    store_path = tmp_path / "app_store.sqlite"
    with build_client(config_dir, store_path, "DROP TABLE sales") as client:
        response = client.post("/api/ask", json={"database": "sample", "question": "Drop it"})

    assert response.json()["outcome"] == "validation failure"
    assert sales_count(sample_db) == 4
    assert audit_rows(store_path) == [("dev", "validation failure", "DROP TABLE sales", None)]


def test_readonly_connection_refuses_writes(sample_db: Path) -> None:
    engine = open_readonly(sample_db)
    with pytest.raises(OperationalError), engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM sales")
    assert sales_count(sample_db) == 4
