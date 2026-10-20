from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
)

metadata = MetaData()

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("user", String, nullable=False),
    Column("database", String, nullable=False),
    Column("question", Text, nullable=False),
    Column("sql", Text),
    Column("row_count", Integer),
    Column("outcome", String, nullable=False),
    Column("error", Text),
)


@dataclass(frozen=True)
class AuditEntry:
    user: str
    database: str
    question: str
    outcome: str
    sql: str | None
    row_count: int | None
    error: str | None


class AppStore:
    def __init__(self, path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{path}")
        metadata.create_all(self._engine)

    @property
    def engine(self) -> Engine:
        return self._engine

    def log(self, entry: AuditEntry) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(audit_log).values(created_at=datetime.now(UTC), **asdict(entry)))
