import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config.loader import Limits

# SQLite checks the deadline every this many VM instructions, so a runaway query stops quickly.
_PROGRESS_INTERVAL = 10_000


class QueryTimeout(Exception):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    # None when truncated but the count query was not feasible within the timeout (SPEC 7.1).
    total_rows: int | None


def run_select(engine: Engine, sql: str, limits: Limits) -> QueryResult:
    deadline = time.monotonic() + limits.query_timeout_seconds
    try:
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            if raw is None:
                raise SQLAlchemyError("No DBAPI connection available")
            # A non-zero return from the progress handler aborts the statement.
            raw.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_INTERVAL)
            try:
                return _fetch(conn, sql, limits, deadline)
            finally:
                raw.set_progress_handler(None, 0)
    except SQLAlchemyError as exc:
        if time.monotonic() >= deadline:
            raise QueryTimeout(str(exc)) from exc
        raise


def _fetch(conn: Connection, sql: str, limits: Limits, deadline: float) -> QueryResult:
    # exec_driver_sql so colons inside the SQL text are not read as bind parameters.
    result = conn.exec_driver_sql(sql)
    columns = list(result.keys())
    # One row past the cap tells us whether the result was truncated without fetching everything.
    rows = [tuple(row) for row in result.fetchmany(limits.row_cap + 1)]
    result.close()
    truncated = len(rows) > limits.row_cap
    rows = rows[: limits.row_cap]
    total = _count(conn, sql) if truncated and time.monotonic() < deadline else None
    return QueryResult(columns=columns, rows=rows, truncated=truncated, total_rows=total)


def _count(conn: Connection, sql: str) -> int | None:
    # The newline before the closing paren keeps a trailing -- comment in the SQL from swallowing it.
    wrapped = f"SELECT COUNT(*) FROM (\n{sql.rstrip().rstrip(';')}\n)"
    try:
        return int(conn.exec_driver_sql(wrapped).scalar_one())
    except SQLAlchemyError:
        return None
