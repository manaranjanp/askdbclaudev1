from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.config.catalog_check import DatabaseStatus
from app.db.store import AppStore
from app.pipeline.run import Pipeline

router = APIRouter(prefix="/api")

SESSION_COOKIE = "session"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    username: str


class AskRequest(BaseModel):
    database: str
    question: str


class AskResponse(BaseModel):
    outcome: str
    sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    error: str | None
    truncated: bool
    total_rows: int | None


class DatabaseInfo(BaseModel):
    name: str
    available: bool
    reason: str | None
    excluded: list[str]


def current_user(request: Request) -> str:
    store: AppStore = request.app.state.store
    token = request.cookies.get(SESSION_COOKIE)
    username = store.username_for_session(token) if token else None
    if username is None:
        raise HTTPException(status_code=401, detail="Log in first.")
    return username


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> UserInfo:
    store: AppStore = request.app.state.store
    if not store.verify_password(body.username, body.password):
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    response.set_cookie(SESSION_COOKIE, store.create_session(body.username), httponly=True, samesite="lax")
    return UserInfo(username=body.username)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    store: AppStore = request.app.state.store
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(username: str = Depends(current_user)) -> UserInfo:
    return UserInfo(username=username)


@router.get("/databases")
def databases(request: Request, _: str = Depends(current_user)) -> list[DatabaseInfo]:
    statuses: list[DatabaseStatus] = request.app.state.databases
    return [
        DatabaseInfo(name=s.name, available=s.available, reason=s.reason, excluded=list(s.excluded))
        for s in statuses
    ]


@router.post("/ask")
def ask(body: AskRequest, request: Request, username: str = Depends(current_user)) -> AskResponse:
    pipeline: Pipeline = request.app.state.pipeline
    answer = pipeline.ask(user=username, database=body.database, question=body.question)
    return AskResponse(
        outcome=answer.outcome,
        sql=answer.sql,
        columns=answer.columns,
        rows=[list(row) for row in answer.rows],
        error=answer.error,
        truncated=answer.truncated,
        total_rows=answer.total_rows,
    )
