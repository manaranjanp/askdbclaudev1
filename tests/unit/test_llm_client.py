from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.db.store import AppStore, audit_log
from app.factory import create_app
from app.llm.anthropic_client import AnthropicLLM
from app.llm.client import GenerationError, LLMUnavailable
from app.pipeline.run import AI_UNAVAILABLE_MESSAGE
from tests.fixtures.login import logged_in_client
from tests.fixtures.make_sample_db import make_sample_db

MODEL = "claude-test-model"
SCHEMA = "sales(id INTEGER, region TEXT, amount REAL)"
GOOD_SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"


class StubMessages:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubClient:
    def __init__(self, result: Any) -> None:
        self.messages = StubMessages(result)


def text_reply(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def llm_with(result: Any) -> tuple[AnthropicLLM, StubClient]:
    stub = StubClient(result)
    return AnthropicLLM(model=MODEL, client=stub), stub


def test_returns_the_model_text_and_sends_schema_and_question_as_data() -> None:
    llm, stub = llm_with(text_reply(GOOD_SQL))

    assert llm.generate_sql("Total by region?", SCHEMA) == GOOD_SQL

    call = stub.messages.calls[0]
    assert call["model"] == MODEL
    assert SCHEMA in call["system"]
    assert "Total by region?" not in call["system"]
    assert call["messages"] == [{"role": "user", "content": "<question>\nTotal by region?\n</question>"}]


def test_prompt_version_is_the_template_name() -> None:
    llm, _ = llm_with(text_reply(GOOD_SQL))

    assert llm.prompt_version == "sql_v1"


def test_empty_reply_is_a_generation_error() -> None:
    llm, _ = llm_with(text_reply("   "))

    with pytest.raises(GenerationError):
        llm.generate_sql("q", SCHEMA)


def test_reply_without_a_text_block_is_a_generation_error() -> None:
    llm, _ = llm_with(SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="x")]))

    with pytest.raises(GenerationError):
        llm.generate_sql("q", SCHEMA)


def test_api_error_is_llm_unavailable() -> None:
    llm, _ = llm_with(connection_error())

    with pytest.raises(LLMUnavailable):
        llm.generate_sql("q", SCHEMA)


@pytest.fixture
def app_factory(tmp_path: Path) -> Any:
    db = tmp_path / "sample.sqlite"
    make_sample_db(db)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sample.yaml").write_text(f"name: sample\npath: {db}\n")

    def build(result: Any) -> tuple[Any, Path]:
        store_path = tmp_path / "app_store.sqlite"
        llm, _ = llm_with(result)
        return logged_in_client(create_app(config_dir, store_path, llm), store_path), store_path

    return build


def audit_rows(store_path: Path) -> list[tuple[str, str | None, str | None]]:
    with AppStore(store_path).engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.outcome, audit_log.c.sql, audit_log.c.prompt_version))
        return [(r.outcome, r.sql, r.prompt_version) for r in rows]


def test_api_outage_is_reported_and_no_query_is_logged(app_factory: Any) -> None:
    client, store_path = app_factory(connection_error())

    body = client.post("/api/ask", json={"database": "sample", "question": "Totals?"}).json()

    assert body["outcome"] == "execution error"
    assert body["error"] == AI_UNAVAILABLE_MESSAGE
    assert body["sql"] is None
    assert audit_rows(store_path) == [("execution error", None, "sql_v1")]


def test_malformed_reply_is_a_generation_failure_with_no_query(app_factory: Any) -> None:
    client, store_path = app_factory(text_reply("   "))

    body = client.post("/api/ask", json={"database": "sample", "question": "Totals?"}).json()

    assert body["outcome"] == "execution error"
    assert body["error"] == "The model did not return a SQL statement."
    assert audit_rows(store_path) == [("execution error", None, "sql_v1")]


def test_successful_answer_is_audited_with_prompt_version(app_factory: Any) -> None:
    client, store_path = app_factory(text_reply(GOOD_SQL))

    body = client.post("/api/ask", json={"database": "sample", "question": "Totals?"}).json()

    assert body["outcome"] == "success"
    assert body["rows"] == [["North", 200.0], ["South", 200.0], ["West", 50.0]]
    assert audit_rows(store_path) == [("success", GOOD_SQL, "sql_v1")]
