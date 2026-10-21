import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect
from sqlalchemy.engine import Inspector

from app.config.examples import ValidExample
from app.config.loader import RetrievalSettings
from app.config.schema import JoinHintSpec, MetricSpec, NoteSpec, TableSpec
from app.retrieval.embedder import Embedder, cosine

logger = logging.getLogger(__name__)


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
class MetricInfo:
    name: str
    description: str
    synonyms: tuple[str, ...]
    table: str
    sql: str
    vector: list[float] = field(repr=False)


@dataclass(frozen=True)
class NoteInfo:
    name: str
    text: str
    vector: list[float] = field(repr=False)


@dataclass(frozen=True)
class ExampleInfo:
    question: str
    sql: str
    vector: list[float] = field(repr=False)


@dataclass(frozen=True)
class DeclaredContext:
    # What support wrote in the database's config file (SPEC 4.1 and 5.5).
    tables: Mapping[str, TableSpec] = field(default_factory=dict)
    metrics: Mapping[str, MetricSpec] = field(default_factory=dict)
    notes: Mapping[str, NoteSpec] = field(default_factory=dict)
    joins: Sequence[JoinHintSpec] = ()


@dataclass(frozen=True)
class Selection:
    tables: list[TableInfo]
    columns: dict[str, list[ColumnInfo]]
    metrics: list[MetricInfo]
    notes: list[NoteInfo]
    joins: list[JoinHintSpec]
    examples: list[ExampleInfo]
    low_confidence: bool

    @property
    def item_ids(self) -> list[str]:
        # Stored in the audit log so each answer shows exactly what the model was given (SPEC 7.4).
        ids = [f"table:{table.name}" for table in self.tables]
        for table in self.tables:
            ids += [f"column:{table.name}.{column.name}" for column in self.columns[table.name]]
        ids += [f"metric:{metric.name}" for metric in self.metrics]
        ids += [f"note:{note.name}" for note in self.notes]
        ids += [f"join:{join.from_table}.{join.from_column}={join.to_table}.{join.to_column}" for join in self.joins]
        return ids

    def schema_text(self) -> str:
        lines: list[str] = []
        for table in self.tables:
            lines.append(f"{table.name}: {table.description}" if table.description else f"{table.name}:")
            for column in self.columns[table.name]:
                suffix = f": {column.description}" if column.description else ""
                lines.append(f"  - {column.name} {column.type}{suffix}")
        for join in self.joins:
            lines.append(
                f"join {join.from_table}.{join.from_column} = {join.to_table}.{join.to_column} "
                f"({join.cardinality})"
            )
        for metric in self.metrics:
            lines.append(f"metric {metric.name}: {metric.description} Definition: {metric.sql}")
        for note in self.notes:
            lines.append(f"note {note.name}: {note.text}")
        return "\n".join(lines)

    def examples_text(self) -> str:
        return "\n\n".join(f"Question: {example.question}\nSQL: {example.sql}" for example in self.examples)


@dataclass(frozen=True)
class Retrieval:
    # Per-database indexes plus the app-wide sizes. Pipeline uses this when it is set.
    indexes: Mapping[str, "SchemaIndex"]
    settings: RetrievalSettings


class SchemaIndex:
    def __init__(
        self,
        tables: Mapping[str, TableInfo],
        metrics: Sequence[MetricInfo],
        notes: Sequence[NoteInfo],
        joins: Sequence[JoinHintSpec],
        examples: Sequence[ExampleInfo],
        embedder: Embedder,
    ) -> None:
        self._tables = dict(tables)
        self._metrics = list(metrics)
        self._notes = list(notes)
        self._joins = list(joins)
        self._examples = list(examples)
        self._embedder = embedder

    @classmethod
    def build(
        cls,
        engine: Engine,
        declared: DeclaredContext,
        examples: Sequence[ValidExample],
        embedder: Embedder,
    ) -> "SchemaIndex":
        # Built once at startup. Every table, column, metric, note and example is embedded from its text.
        inspector = inspect(engine)
        names = inspector.get_table_names()
        neighbours = _neighbours(inspector, names)

        drafts: list[tuple[str, str, list[tuple[str, str, str]]]] = []
        texts: list[str] = []
        for name in names:
            spec = declared.tables.get(name)
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
        table_vectors = embedder.embed(texts)

        tables: dict[str, TableInfo] = {}
        cursor = 0
        for name, table_description, columns in drafts:
            column_infos = []
            for column_name, column_type, description in columns:
                column_infos.append(ColumnInfo(column_name, column_type, description, table_vectors[cursor]))
                cursor += 1
            tables[name] = TableInfo(
                name=name,
                description=table_description,
                vector=table_vectors[cursor],
                columns=column_infos,
                neighbours=frozenset(neighbours[name]),
            )
            cursor += 1

        metrics = _metrics(declared.metrics, set(names), embedder)
        notes = _notes(declared.notes, embedder)
        joins = [join for join in declared.joins if _join_tables_exist(join, set(names))]
        example_vectors = embedder.embed([example.question for example in examples])
        example_infos = [
            ExampleInfo(example.question, example.sql, vector)
            for example, vector in zip(examples, example_vectors, strict=True)
        ]
        return cls(tables, metrics, notes, joins, example_infos, embedder)

    def select(self, question: str, settings: RetrievalSettings) -> Selection:
        query = self._embedder.embed_query(question)
        ranked = sorted(self._tables.values(), key=lambda t: (-cosine(query, t.vector), t.name))
        best = cosine(query, ranked[0].vector) if ranked else 0.0
        chosen = [table.name for table in ranked[: settings.top_tables]]

        # A metric named in the question is always in context, with the table it is computed from (SPEC 5.5).
        metrics = [metric for metric in self._metrics if _mentions(question, metric)]
        for metric in metrics:
            if metric.table not in chosen:
                chosen.append(metric.table)

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

        # Join hints travel with the tables they connect (SPEC 5.4).
        joins = [join for join in self._joins if join.from_table in chosen and join.to_table in chosen]
        notes = sorted(self._notes, key=lambda n: -cosine(query, n.vector))
        notes = [note for note in notes if cosine(query, note.vector) >= settings.min_similarity][: settings.max_notes]
        examples = sorted(self._examples, key=lambda e: -cosine(query, e.vector))[: settings.max_examples]
        return Selection(
            tables=tables,
            columns=columns,
            metrics=metrics,
            notes=notes,
            joins=joins,
            examples=examples,
            low_confidence=best < settings.min_similarity,
        )


def _neighbours(inspector: Inspector, names: list[str]) -> dict[str, set[str]]:
    neighbours: dict[str, set[str]] = {name: set() for name in names}
    for name in names:
        for foreign_key in inspector.get_foreign_keys(name):
            target = foreign_key["referred_table"]
            if target in neighbours and target != name:
                neighbours[name].add(target)
                neighbours[target].add(name)
    return neighbours


def _metrics(declared: Mapping[str, MetricSpec], table_names: set[str], embedder: Embedder) -> list[MetricInfo]:
    kept: list[tuple[str, MetricSpec]] = []
    for name, spec in declared.items():
        if spec.table not in table_names:
            logger.warning("metric %s skipped: table %s is not in the database", name, spec.table)
            continue
        kept.append((name, spec))
    vectors = embedder.embed([f"{name} {spec.description} {' '.join(spec.synonyms)}" for name, spec in kept])
    return [
        MetricInfo(
            name=name,
            description=spec.description,
            synonyms=tuple(spec.synonyms),
            table=spec.table,
            sql=spec.sql,
            vector=vector,
        )
        for (name, spec), vector in zip(kept, vectors, strict=True)
    ]


def _notes(declared: Mapping[str, NoteSpec], embedder: Embedder) -> list[NoteInfo]:
    names = list(declared)
    vectors = embedder.embed([f"{name} {declared[name].description} {' '.join(declared[name].synonyms)}" for name in names])
    return [NoteInfo(name, declared[name].text, vector) for name, vector in zip(names, vectors, strict=True)]


def _join_tables_exist(join: JoinHintSpec, table_names: set[str]) -> bool:
    if join.from_table in table_names and join.to_table in table_names:
        return True
    logger.warning("join hint %s.%s = %s.%s skipped: a table is not in the database", join.from_table, join.from_column, join.to_table, join.to_column)
    return False


def _mentions(question: str, metric: MetricInfo) -> bool:
    # Exact, whole-word match on the metric name or a synonym. Retrieval scoring is not used for this.
    lowered = question.lower()
    for term in (metric.name.replace("_", " "), *metric.synonyms):
        if re.search(rf"\b{re.escape(term.lower())}\b", lowered):
            return True
    return False
