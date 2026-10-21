from typing import Protocol


class LLMUnavailable(Exception):
    # API errors, rate limits and timeouts (SPEC 10.3). No SQL was produced.
    pass


class GenerationError(Exception):
    # The model answered, but not with a usable SQL statement (SPEC 10.3).
    pass


class LLMClient(Protocol):
    # Recorded in the audit log so every answer can be traced to the prompt that produced it (SPEC 10.2).
    prompt_version: str

    def generate_sql(self, question: str, schema: str) -> str: ...


class MockLLM:
    # Stands in for the Anthropic client in tests. Returns the same SQL for every question.
    prompt_version = "mock"

    def __init__(self, sql: str) -> None:
        self._sql = sql

    def generate_sql(self, question: str, schema: str) -> str:
        return self._sql
