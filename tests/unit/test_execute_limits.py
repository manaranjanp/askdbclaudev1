from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config.loader import Limits, load_limits
from app.db.analytics import open_readonly
from app.db.store import AppStore, audit_log
from app.factory import create_app
from app.llm.client import MockLLM
from app.pipeline.execute import QueryTimeout, run_select
from tests.fixtures.login import logged_in_client
from tests.fixtures.make_sample_db import make_events_db, make_sample_db

EVENTS_SQL = "SELECT id FROM events ORDER BY id"
# Busy but returns no rows, so the row cap never stops it and only the timeout can.
RUNAWAY_SQL = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
    "SELECT x FROM c WHERE x < 0"
)


@pytest.fixture
def events_db(tmp_path: Path) -> Path:
    path = tmp_path / "events.sqlite"
    make_sample_db(path)
    make_events_db(path, rows=1500)
    return path


def make_config(tmp_path: Path, db_path: Path, app_yaml: str | None = None) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "events.yaml").write_text(f"name: sample\npath: {db_path}\n")
    if app_yaml is not None:
        (directory / "app.yaml").write_text(app_yaml)
    return directory


def ask(config_dir: Path, store_path: Path, sql: str) -> Any:
    client = logged_in_client(create_app(config_dir, store_path, MockLLM(sql)), store_path)
    response = client.post("/api/ask", json={"database": "sample", "question": "q"})
    return response.json()


def audit_outcomes(store_path: Path) -> list[tuple[str, int | None]]:
    store = AppStore(store_path)
    with store.engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.outcome, audit_log.c.row_count))
        return [(r.outcome, r.row_count) for r in rows]


def test_large_result_is_capped_with_total(events_db: Path, tmp_path: Path) -> None:
    store_path = tmp_path / "app_store.sqlite"
    body = ask(make_config(tmp_path, events_db), store_path, EVENTS_SQL)

    assert body["outcome"] == "success"
    assert len(body["rows"]) == 1000
    assert body["truncated"] is True
    assert body["total_rows"] == 1500
    assert audit_outcomes(store_path) == [("success", 1000)]


def test_result_exactly_at_cap_is_not_truncated(events_db: Path, tmp_path: Path) -> None:
    body = ask(
        make_config(tmp_path, events_db),
        tmp_path / "app_store.sqlite",
        "SELECT id FROM events WHERE id <= 1000 ORDER BY id",
    )

    assert len(body["rows"]) == 1000
    assert body["truncated"] is False
    assert body["total_rows"] is None


def test_row_cap_is_read_from_config(events_db: Path, tmp_path: Path) -> None:
    config_dir = make_config(tmp_path, events_db, app_yaml="row_cap: 25\n")
    body = ask(config_dir, tmp_path / "app_store.sqlite", EVENTS_SQL)

    assert len(body["rows"]) == 25
    assert body["truncated"] is True
    assert body["total_rows"] == 1500


def test_runaway_query_times_out_and_is_audited(events_db: Path, tmp_path: Path) -> None:
    config_dir = make_config(tmp_path, events_db, app_yaml="query_timeout_seconds: 0.2\n")
    store_path = tmp_path / "app_store.sqlite"
    body = ask(config_dir, store_path, RUNAWAY_SQL)

    assert body["outcome"] == "timeout"
    assert "narrowing" in str(body["error"])
    assert audit_outcomes(store_path) == [("timeout", None)]


def test_timeout_is_read_from_config(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "app.yaml").write_text("row_cap: 500\nquery_timeout_seconds: 4.5\n")
    assert load_limits(directory) == Limits(row_cap=500, query_timeout_seconds=4.5)


def test_missing_app_yaml_uses_spec_defaults(tmp_path: Path) -> None:
    assert load_limits(tmp_path) == Limits(row_cap=1000, query_timeout_seconds=30.0)


def test_run_select_raises_timeout_directly(events_db: Path) -> None:
    with pytest.raises(QueryTimeout):
        run_select(open_readonly(events_db), RUNAWAY_SQL, Limits(query_timeout_seconds=0.2))
