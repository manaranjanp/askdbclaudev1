from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.store import AppStore

TEST_USER = "analyst"
TEST_PASSWORD = "correct horse battery"


def logged_in_client(app: FastAPI, store_path: Path) -> TestClient:
    AppStore(store_path).create_user(TEST_USER, TEST_PASSWORD)
    client = TestClient(app)
    client.post("/api/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    return client
