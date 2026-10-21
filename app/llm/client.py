from typing import Protocol


class LLMUnavailable(Exception):
    # API errors, rate limits and timeouts (SPEC 10.3). No SQL was produced.
    pass


class GenerationError(Exception):
    # The model answered, but not with a usable SQL statement (SPEC 10.3).
    pass


class LLMClient(Protocol):
    # Recorded in the audit log so every answer can be traced to the prompts that produced it (SPEC 10.2).
    prompt_version: str

    # The intent classifier sees the question text only (CLAUDE.md guardrail 3).
    def classify_intent(self, question: str) -> str: ...

    def generate_sql(self, question: str, schema: str, examples: str) -> str: ...

    # One repair attempt per question (SPEC 5.7 and slice 8). The model sees the failed SQL and its error.
    def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str: ...


class MockLLM:
    # Stands in for the Anthropic client in tests. Returns the same SQL for every question.
    prompt_version = "mock"

    def __init__(self, sql: str) -> None:
        self._sql = sql

    def classify_intent(self, question: str) -> str:
        return "data"

    def generate_sql(self, question: str, schema: str, examples: str) -> str:
        return self._sql

    def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        return self._sql
