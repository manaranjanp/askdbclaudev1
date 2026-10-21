from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# extra="forbid" so a typo such as "tabels:" is reported instead of silently ignored.
class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    synonyms: list[str] = Field(default_factory=list)


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    synonyms: list[str] = Field(default_factory=list)
    columns: dict[str, ColumnSpec]


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    synonyms: list[str] = Field(default_factory=list)
    # The table the metric is computed from. It is always put into the context when the metric is.
    table: str
    # SQL expression for the metric, shown to the model as a definition (SPEC 5.5).
    sql: str


class NoteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The description is what retrieval matches on. The text is the background the model is given.
    description: str
    synonyms: list[str] = Field(default_factory=list)
    text: str


class JoinHintSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: Literal["one-to-one", "one-to-many", "many-to-many"]


class DatabaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    tables: dict[str, TableSpec] = Field(default_factory=dict)
    metrics: dict[str, MetricSpec] = Field(default_factory=dict)
    notes: dict[str, NoteSpec] = Field(default_factory=dict)
    joins: list[JoinHintSpec] = Field(default_factory=list)
