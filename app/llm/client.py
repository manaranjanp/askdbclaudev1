from typing import Protocol


class LLMClient(Protocol):
    def generate_sql(self, question: str) -> str: ...


class MockLLM:
    # Stands in for the Anthropic client until slice 6. Returns the same SQL for every question.
    def __init__(self, sql: str) -> None:
        self._sql = sql

    def generate_sql(self, question: str) -> str:
        return self._sql
