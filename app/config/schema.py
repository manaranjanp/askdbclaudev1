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


class DatabaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    tables: dict[str, TableSpec] = Field(default_factory=dict)
