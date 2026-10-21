from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.store import AppStore, audit_log, users
from app.factory import create_app
from app.llm.client import MockLLM
from tests.fixtures.login import TEST_PASSWORD, TEST_USER, logged_in_client
from tests.fixtures.make_sample_db import make_sample_db

GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"


def build_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db = tmp_path / "sample.sqlite"
    make_sample_db(db)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sample.yaml").write_text(f"name: sample\npath: {db}\n")
    store_path = tmp_path / "app_store.sqlite"
    return create_app(config_dir, store_path, MockLLM(GOOD_SQL)), store_path


def test_ask_without_login_returns_401(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    response = TestClient(app).post("/api/ask", json={"database": "sample", "question": "Totals?"})

    assert response.status_code == 401


def test_databases_without_login_returns_401(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)

    assert TestClient(app).get("/api/databases").status_code == 401


def test_wrong_password_is_refused(tmp_path: Path) -> None:
    app, store_path = build_app(tmp_path)
    AppStore(store_path).create_user(TEST_USER, TEST_PASSWORD)
    client = TestClient(app)

    response = client.post("/api/login", json={"username": TEST_USER, "password": "wrong"})

    assert response.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_logout_ends_the_session(tmp_path: Path) -> None:
    app, store_path = build_app(tmp_path)
    client = logged_in_client(app, store_path)
    assert client.get("/api/me").json() == {"username": TEST_USER}

    client.post("/api/logout")

    assert client.get("/api/me").status_code == 401


def test_password_is_stored_as_argon2_hash(tmp_path: Path) -> None:
    _, store_path = build_app(tmp_path)
    store = AppStore(store_path)
    store.create_user(TEST_USER, TEST_PASSWORD)

    with store.engine.connect() as conn:
        stored = conn.execute(select(users.c.password_hash)).scalar_one()
    assert stored.startswith("$argon2")
    assert TEST_PASSWORD not in stored


def test_audit_row_carries_the_logged_in_username(tmp_path: Path) -> None:
    app, store_path = build_app(tmp_path)
    client = logged_in_client(app, store_path)
    client.post("/api/ask", json={"database": "sample", "question": "Totals?"})

    with AppStore(store_path).engine.connect() as conn:
        users_seen = [row.user for row in conn.execute(select(audit_log.c.user))]
    assert users_seen == [TEST_USER]
