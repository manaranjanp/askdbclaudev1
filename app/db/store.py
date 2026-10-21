import hashlib
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import (
    Boolean,
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
    inspect,
    select,
    text,
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
    Column("prompt_version", String),
    Column("repair_used", Boolean, nullable=False, server_default="0"),
)

users = Table(
    "users",
    metadata,
    Column("username", String, primary_key=True),
    Column("password_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Only a hash of the session token is stored, so a copy of the store cannot be replayed as a login.
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String, primary_key=True),
    Column("username", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
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
    prompt_version: str | None = None
    repair_used: bool = False


class AppStore:
    def __init__(self, path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{path}")
        metadata.create_all(self._engine)
        self._add_missing_audit_columns()
        self._hasher = PasswordHasher()

    def _add_missing_audit_columns(self) -> None:
        # create_all does not alter existing tables, so a store made before a column existed gets it here.
        existing = {column["name"] for column in inspect(self._engine).get_columns("audit_log")}
        with self._engine.begin() as conn:
            for column in audit_log.columns:
                if column.name not in existing:
                    column_type = column.type.compile(dialect=self._engine.dialect)
                    conn.execute(text(f"ALTER TABLE audit_log ADD COLUMN {column.name} {column_type}"))

    @property
    def engine(self) -> Engine:
        return self._engine

    def log(self, entry: AuditEntry) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(audit_log).values(created_at=datetime.now(UTC), **asdict(entry)))

    def create_user(self, username: str, password: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(users).values(
                    username=username,
                    password_hash=self._hasher.hash(password),
                    created_at=datetime.now(UTC),
                )
            )

    def verify_password(self, username: str, password: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(select(users.c.password_hash).where(users.c.username == username)).first()
        if row is None:
            return False
        try:
            return self._hasher.verify(row.password_hash, password)
        except VerifyMismatchError:
            return False

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._engine.begin() as conn:
            conn.execute(
                insert(sessions).values(
                    token_hash=_hash_token(token),
                    username=username,
                    created_at=datetime.now(UTC),
                )
            )
        return token

    def username_for_session(self, token: str) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sessions.c.username).where(sessions.c.token_hash == _hash_token(token))
            ).first()
        return None if row is None else str(row.username)

    def delete_session(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(sessions.delete().where(sessions.c.token_hash == _hash_token(token)))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
