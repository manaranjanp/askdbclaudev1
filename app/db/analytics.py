from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect


def open_readonly(path: Path) -> Engine:
    # mode=ro is the database-level read-only layer (SPEC 6.1). The validator is the second layer.
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def describe_schema(engine: Engine) -> str:
    # One line per table, for the SQL prompt. Retrieval (slice 9) will replace this with a subset.
    inspector = inspect(engine)
    lines = []
    for table in sorted(inspector.get_table_names()):
        columns = ", ".join(f"{c['name']} {c['type']!s}" for c in inspector.get_columns(table))
        lines.append(f"{table}({columns})")
    return "\n".join(lines)
