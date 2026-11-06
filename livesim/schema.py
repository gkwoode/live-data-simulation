"""Field and table definitions plus type mappings for every integration target."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FIELD_TYPES = ("string", "int", "float", "bool", "timestamp", "date")

# Roles drive behaviour, not just documentation:
#   id / time / system - never touched by data-quality chaos
#   fk                 - foreign key into a reference (dim_*) table
#   dimension          - categorical attribute you group or filter by
#   measure            - numeric value you aggregate (eligible for outliers / sentinels)
#   label              - ground-truth target for machine learning (never corrupted)
#   text               - free text (NLP)
#   attribute          - any other descriptive value
ROLES = ("id", "time", "fk", "dimension", "measure", "label", "text", "attribute", "system")


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    role: str = "attribute"
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"{self.name}: unknown type {self.type!r}")
        if self.role not in ROLES:
            raise ValueError(f"{self.name}: unknown role {self.role!r}")

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type, "role": self.role, "description": self.description}


def F(name: str, type_: str, role: str = "attribute", description: str = "") -> Field:
    return Field(name, type_, role, description)


SYSTEM_FIELDS: tuple[Field, ...] = (
    Field(
        "_seq",
        "int",
        "system",
        "Ingestion sequence number: unique and increasing within a stream. Use it as an incremental-load cursor.",
    ),
    Field("_ingested_at", "timestamp", "system", "UTC time the simulator emitted the record"),
)


@dataclass
class TableDef:
    """A reference (dimension) table: small, static, generated from the seed."""

    name: str
    title: str
    description: str
    fields: list[Field]
    key: str
    rows: list[dict[str, Any]] = field(default_factory=list)


SQLITE_TYPES = {"string": "TEXT", "int": "INTEGER", "float": "REAL", "bool": "INTEGER", "timestamp": "TEXT", "date": "TEXT"}
EDM_TYPES = {
    "string": "Edm.String",
    "int": "Edm.Int64",
    "float": "Edm.Double",
    "bool": "Edm.Boolean",
    "timestamp": "Edm.DateTimeOffset",
    "date": "Edm.Date",
}
SPARK_TYPES = {
    "string": "STRING",
    "int": "BIGINT",
    "float": "DOUBLE",
    "bool": "BOOLEAN",
    "timestamp": "TIMESTAMP",
    "date": "DATE",
}
POWERBI_TYPES = {
    "string": "Text",
    "int": "Number",
    "float": "Number",
    "bool": "Boolean",
    "timestamp": "DateTime",
    "date": "DateTime",
}
POSTGRES_TYPES = {
    "string": "TEXT",
    "int": "BIGINT",
    "float": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "timestamp": "TIMESTAMPTZ",
    "date": "DATE",
}
SQLSERVER_TYPES = {
    "string": "NVARCHAR(400)",
    "int": "BIGINT",
    "float": "FLOAT",
    "bool": "BIT",
    "timestamp": "DATETIME2(3)",
    "date": "DATE",
}
PANDAS_TYPES = {
    "string": "string",
    "int": "Int64",
    "float": "float64",
    "bool": "boolean",
    "timestamp": "datetime64[ns, UTC]",
    "date": "object",
}
JSON_SCHEMA_TYPES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "timestamp": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
}


def sql_ddl(table: str, fields: list[Field], dialect: str = "postgres") -> str:
    types = SQLSERVER_TYPES if dialect == "sqlserver" else POSTGRES_TYPES
    quote = (lambda n: f"[{n}]") if dialect == "sqlserver" else (lambda n: f'"{n}"')
    cols = ",\n".join(f"  {quote(f.name)} {types[f.type]}" for f in fields)
    return f"CREATE TABLE {quote(table)} (\n{cols}\n);"


def spark_ddl(fields: list[Field]) -> str:
    return ", ".join(f"{f.name} {SPARK_TYPES[f.type]}" for f in fields)


def json_schema(name: str, title: str, fields: list[Field]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for f in fields:
        spec = dict(JSON_SCHEMA_TYPES[f.type])
        if f.role not in ("id", "time", "system"):
            spec["type"] = [spec["type"], "null"]
        if f.description:
            spec["description"] = f.description
        props[f.name] = spec
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"livesim:{name}",
        "title": title,
        "type": "object",
        "properties": props,
        "required": [f.name for f in fields if f.role in ("id", "time", "system")],
    }
