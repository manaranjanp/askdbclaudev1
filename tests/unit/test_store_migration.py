import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from app.db.store import AppStore, AuditEntry


def test_store_created_before_prompt_version_gets_the_column(tmp_path: Path) -> None:
    path = tmp_path / "app_store.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, "
        '"user" VARCHAR NOT NULL, "database" VARCHAR NOT NULL, question TEXT NOT NULL, sql TEXT, '
        "row_count INTEGER, outcome VARCHAR NOT NULL, error TEXT)"
    )
    conn.execute(
        "INSERT INTO audit_log (created_at, \"user\", \"database\", question, outcome) "
        "VALUES ('2026-01-01', 'old', 'sample', 'before', 'success')"
    )
    conn.commit()
    conn.close()

    store = AppStore(path)
    columns = {column["name"] for column in inspect(store.engine).get_columns("audit_log")}
    assert "prompt_version" in columns

    store.log(AuditEntry("new", "sample", "after", "success", None, None, None, prompt_version="sql_v1"))
    with store.engine.connect() as conn:
        versions = [row[0] for row in conn.exec_driver_sql("SELECT prompt_version FROM audit_log ORDER BY id")]
    assert versions == [None, "sql_v1"]
