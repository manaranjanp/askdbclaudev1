from pathlib import Path

from sqlalchemy import Engine, create_engine


def open_readonly(path: Path) -> Engine:
    # mode=ro is the database-level read-only layer (SPEC 6.1). The validator is the second layer.
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
