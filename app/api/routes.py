from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.pipeline.run import Pipeline

router = APIRouter(prefix="/api")

# Fixed until username/password login arrives in slice 5.
DEV_USER = "dev"


class AskRequest(BaseModel):
    database: str
    question: str


class AskResponse(BaseModel):
    outcome: str
    sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    error: str | None


@router.post("/ask")
def ask(body: AskRequest, request: Request) -> AskResponse:
    pipeline: Pipeline = request.app.state.pipeline
    answer = pipeline.ask(user=DEV_USER, database=body.database, question=body.question)
    return AskResponse(
        outcome=answer.outcome,
        sql=answer.sql,
        columns=answer.columns,
        rows=[list(row) for row in answer.rows],
        error=answer.error,
    )
