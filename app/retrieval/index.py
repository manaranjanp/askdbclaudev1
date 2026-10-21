from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect

from app.config.loader import RetrievalSettings
from app.config.schema import TableSpec
from app.retrieval.embedder import Embedder, cosine


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    description: str
    vector: list[float] = field(repr=False)


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    vector: list[float] = field(repr=False)
    columns: list[ColumnInfo]
    # Tables linked by a foreign key in either direction. Added as join neighbours (SPEC 5.9).
    neighbours: frozenset[str]


@dataclass(frozen=True)
class Selection:
    tables: list[TableInfo]
    columns: dict[str, list[ColumnInfo]]

    @property
    def item_ids(self) -> list[str]:
        # Stored in the audit log so each answer shows exactly what the model was given (SPEC 7.4).
        ids = [f"table:{table.name}" for table in self.tables]
        for table in self.tables:
            ids += [f"column:{table.name}.{column.name}" for column in self.columns[table.name]]
        return ids

    def schema_text(self) -> str:
        lines: list[str] = []
        for table in self.tables:
            lines.append(f"{table.name}: {table.description}" if table.description else f"{table.name}:")
            for column in self.columns[table.name]:
                suffix = f": {column.description}" if column.description else ""
                lines.append(f"  - {column.name} {column.type}{suffix}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Retrieval:
    # Per-database indexes plus the app-wide sizes. Pipeline uses this when it is set.
    indexes: Mapping[str, "SchemaIndex"]
    settings: RetrievalSettings


class SchemaIndex:
    def __init__(self, tables: Mapping[str, TableInfo], embedder: Embedder) -> None:
        self._tables = dict(tables)
        self._embedder = embedder

    @classmethod
    def build(cls, engine: Engine, declared: Mapping[str, TableSpec], embedder: Embedder) -> "SchemaIndex":
        # Built once at startup; every table and column is embedded from its name, description and synonyms.
        inspector = inspect(engine)
        names = inspector.get_table_names()
        neighbours: dict[str, set[str]] = {name: set() for name in names}
        for name in names:
            for foreign_key in inspector.get_foreign_keys(name):
                target = foreign_key["referred_table"]
                if target in neighbours and target != name:
                    neighbours[name].add(target)
                    neighbours[target].add(name)

        drafts: list[tuple[str, str, list[tuple[str, str, str]]]] = []
        texts: list[str] = []
        for name in names:
            spec = declared.get(name)
            table_description = spec.description if spec else ""
            columns = []
            for column in inspector.get_columns(name):
                column_spec = spec.columns.get(column["name"]) if spec else None
                description = column_spec.description if column_spec else ""
                columns.append((column["name"], str(column["type"]), description))
                synonyms = " ".join(column_spec.synonyms) if column_spec else ""
                texts.append(f"{name} {column['name']} {description} {synonyms}")
            drafts.append((name, table_description, columns))
            synonyms = " ".join(spec.synonyms) if spec else ""
            texts.append(f"{name} {table_description} {synonyms}")

        vectors = embedder.embed(texts)
        tables: dict[str, TableInfo] = {}
        cursor = 0
        for name, table_description, columns in drafts:
            column_infos = []
            for column_name, column_type, description in columns:
                column_infos.append(ColumnInfo(column_name, column_type, description, vectors[cursor]))
                cursor += 1
            tables[name] = TableInfo(
                name=name,
                description=table_description,
                vector=vectors[cursor],
                columns=column_infos,
                neighbours=frozenset(neighbours[name]),
            )
            cursor += 1
        return cls(tables, embedder)

    def select(self, question: str, settings: RetrievalSettings) -> Selection:
        [query] = self._embedder.embed([question])
        ranked = sorted(self._tables.values(), key=lambda t: (-cosine(query, t.vector), t.name))
        chosen = [table.name for table in ranked[: settings.top_tables]]
        # Join neighbours are added once each, and the total stays within max_tables.
        for name in list(chosen):
            for neighbour in sorted(self._tables[name].neighbours):
                if len(chosen) >= settings.max_tables:
                    break
                if neighbour not in chosen:
                    chosen.append(neighbour)

        tables = [self._tables[name] for name in chosen]
        columns: dict[str, list[ColumnInfo]] = {}
        for table in tables:
            ranked_columns = sorted(table.columns, key=lambda c: (-cosine(query, c.vector), c.name))
            keep = {column.name for column in ranked_columns[: settings.columns_per_table]}
            # Keep the table's own column order so the schema reads naturally.
            columns[table.name] = [column for column in table.columns if column.name in keep]
        return Selection(tables=tables, columns=columns)
