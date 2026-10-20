from typing import Any

from sqlalchemy import Engine


def run_select(engine: Engine, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    # exec_driver_sql so colons inside the SQL text are not read as bind parameters.
    with engine.connect() as conn:
        result = conn.exec_driver_sql(sql)
        columns = list(result.keys())
        rows = [tuple(row) for row in result.fetchall()]
    return columns, rows
