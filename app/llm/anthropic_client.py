import json
from pathlib import Path
from typing import Any

import anthropic

from app.llm.client import GenerationError, LLMUnavailable

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SQL_PROMPT = "sql_v3.md"
INTENT_PROMPT = "intent_v1.md"
REPAIR_PROMPT = "repair_v2.md"
EXPLAIN_PROMPT = "explain_v1.md"
MAX_TOKENS = 1024


def _fill(template: str, schema: str, examples: str) -> str:
    # Schema and examples come from the database config, so they are filled in once, not re-parsed.
    return template.replace("{{schema}}", schema).replace("{{examples}}", examples or "(none)")


class AnthropicLLM:
    def __init__(self, model: str, prompts_dir: Path = PROMPTS_DIR, client: Any = None) -> None:
        self._model = model
        self._sql_template = (prompts_dir / SQL_PROMPT).read_text()
        self._intent_template = (prompts_dir / INTENT_PROMPT).read_text()
        self._repair_template = (prompts_dir / REPAIR_PROMPT).read_text()
        self._explain_template = (prompts_dir / EXPLAIN_PROMPT).read_text()
        # All versions go in the audit log, so an answer can be traced to the prompts that produced it.
        self.prompt_version = ",".join(Path(p).stem for p in (SQL_PROMPT, INTENT_PROMPT, REPAIR_PROMPT, EXPLAIN_PROMPT))
        # The SDK reads ANTHROPIC_API_KEY from the environment; tests pass a stub instead.
        self._client = client if client is not None else anthropic.Anthropic()

    def classify_intent(self, question: str) -> str:
        # Returns the raw reply; the pipeline parses it strictly.
        return self._send(self._intent_template, f"<message>\n{question}\n</message>")

    def generate_sql(self, question: str, schema: str, examples: str) -> str:
        system = _fill(self._sql_template, schema, examples)
        # The question goes in as data, wrapped so the prompt can refer to it as such.
        return self._sql_reply(self._send(system, f"<question>\n{question}\n</question>"))

    def repair_sql(self, question: str, schema: str, examples: str, failed_sql: str, error: str) -> str:
        system = _fill(self._repair_template, schema, examples)
        user_text = (
            f"<question>\n{question}\n</question>\n"
            f"<failed_sql>\n{failed_sql}\n</failed_sql>\n"
            f"<error>\n{error}\n</error>"
        )
        return self._sql_reply(self._send(system, user_text))

    def explain(self, question: str, sql: str, columns: list[str], rows: list[tuple[Any, ...]]) -> str:
        # Rows are sent as JSON inside a tag, so they are plainly data to the model.
        payload = json.dumps({"columns": columns, "rows": rows}, default=str)
        user_text = f"<question>\n{question}\n</question>\n<sql>\n{sql}\n</sql>\n<rows>\n{payload}\n</rows>"
        return self._send(self._explain_template, user_text)

    def _sql_reply(self, text: str) -> str:
        if not text:
            raise GenerationError("The model did not return a SQL statement.")
        return text

    def _send(self, system: str, user_text: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.APIError as exc:
            raise LLMUnavailable(str(exc)) from exc
        return "".join(
            getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
