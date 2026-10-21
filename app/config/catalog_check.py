import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, inspect
from sqlalchemy.exc import SQLAlchemyError

from app.config.loader import DatabaseConfig, load_databases
from app.config.schema import JoinHintSpec, MetricSpec, NoteSpec, TableSpec
from app.db.analytics import open_readonly

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseStatus:
    name: str
    available: bool
    reason: str | None = None
    # Declared tables or columns that are not in the database. They are left out of the schema.
    excluded: tuple[str, ...] = ()


@dataclass
class Catalog:
    statuses: list[DatabaseStatus] = field(default_factory=list)
    engines: dict[str, Engine] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    # Declared tables per available database, for descriptions and synonyms in retrieval.
    tables: dict[str, dict[str, TableSpec]] = field(default_factory=dict)
    metrics: dict[str, dict[str, MetricSpec]] = field(default_factory=dict)
    notes: dict[str, dict[str, NoteSpec]] = field(default_factory=dict)
    joins: dict[str, list[JoinHintSpec]] = field(default_factory=dict)


def check_catalog(config_dir: Path) -> Catalog:
    # Startup never aborts on a bad database (SPEC 4.3): each one is marked and the rest keep working.
    catalog = Catalog()
    for config in load_databases(config_dir):
        status, engine = _check(config, taken={s.name for s in catalog.statuses})
        catalog.statuses.append(status)
        if engine is not None:
            catalog.engines[status.name] = engine
            catalog.tables[status.name] = config.tables
            catalog.metrics[status.name] = config.metrics
            catalog.notes[status.name] = config.notes
            catalog.joins[status.name] = config.joins
        elif status.name not in catalog.engines and status.reason is not None:
            # setdefault: a duplicate name must not replace the reason for the first definition.
            catalog.unavailable.setdefault(status.name, status.reason)
    return catalog


def _check(config: DatabaseConfig, taken: set[str]) -> tuple[DatabaseStatus, Engine | None]:
    if config.error is not None:
        return _unavailable(config.name, config.error)
    if config.name in taken:
        return _unavailable(config.name, f"{config.file.name}: name '{config.name}' is already used")
    if config.path is None or not config.path.exists():
        return _unavailable(config.name, f"{config.file.name}: database file not found: {config.path}")

    engine = open_readonly(config.path)
    try:
        excluded = _excluded_items(config, engine)
    except SQLAlchemyError as exc:
        engine.dispose()
        return _unavailable(config.name, f"{config.file.name}: {exc}")
    for item in excluded:
        logger.warning("%s: excluded %s, not found in %s", config.file.name, item, config.path)
    return DatabaseStatus(name=config.name, available=True, excluded=tuple(excluded)), engine


def _excluded_items(config: DatabaseConfig, engine: Engine) -> list[str]:
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    excluded: list[str] = []
    for table, spec in config.tables.items():
        if table not in actual_tables:
            excluded.append(table)
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table)}
        excluded.extend(f"{table}.{column}" for column in spec.columns if column not in actual_columns)
    return excluded


def _unavailable(name: str, reason: str) -> tuple[DatabaseStatus, None]:
    logger.warning("database %s unavailable: %s", name, reason)
    return DatabaseStatus(name=name, available=False, reason=reason), None
