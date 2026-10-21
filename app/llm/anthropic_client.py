from pathlib import Path
from typing import Any

import anthropic

from app.llm.client import GenerationError, LLMUnavailable

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SQL_PROMPT = "sql_v1.md"
MAX_TOKENS = 1024


class AnthropicLLM:
    def __init__(self, model: str, prompts_dir: Path = PROMPTS_DIR, client: Any = None) -> None:
        self._model = model
        self._template = (prompts_dir / SQL_PROMPT).read_text()
        self.prompt_version = Path(SQL_PROMPT).stem
        # The SDK reads ANTHROPIC_API_KEY from the environment; tests pass a stub instead.
        self._client = client if client is not None else anthropic.Anthropic()

    def generate_sql(self, question: str, schema: str) -> str:
        system = self._template.replace("{{schema}}", schema)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                # The question goes in as data, wrapped so the prompt can refer to it as such.
                messages=[{"role": "user", "content": f"<question>\n{question}\n</question>"}],
            )
        except anthropic.APIError as exc:
            raise LLMUnavailable(str(exc)) from exc
        text = "".join(
            getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise GenerationError("The model did not return a SQL statement.")
        return text
