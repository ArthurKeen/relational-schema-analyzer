"""Paradigm-neutral physical schema model.

Lifted from ``r2g/src/r2g/types.py`` (the ``Schema`` / ``Table`` / ``Column`` /
``ForeignKey`` core). ArangoDB-specific models (``MappingConfig``,
``CollectionMapping``, ``EdgeDefinition``, ``FieldExpression``,
``NamingConvention``) stay in ``r2g``; they are *not* extracted here.

``Schema`` is renamed to :class:`PhysicalSchema` to make the relational, physical
nature explicit. A ``Schema`` alias is kept so ``r2g`` (and ported tests) can import
the type unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, computed_field, model_serializer, model_validator

from .typemap import normalized_type_category


class ForeignKey(BaseModel):
    """A foreign key constraint, supporting both single- and multi-column FKs.

    Accepts legacy ``column``/``foreign_column`` (str) or composite
    ``columns``/``foreign_columns`` (list[str]).
    """

    columns: List[str]
    foreign_table: str
    foreign_columns: List[str]
    constraint_name: Optional[str] = None
    # Cardinality hint: when the FK columns are themselves unique on the source
    # table, the relationship is 1:1 (vs. many:1). Lets consumers decide
    # functional vs. non-functional object properties (AOE contract).
    is_unique: bool = False

    @model_validator(mode="before")
    @classmethod
    def _accept_singular(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "column" in data and "columns" not in data:
                data["columns"] = [data.pop("column")]
            if "foreign_column" in data and "foreign_columns" not in data:
                data["foreign_columns"] = [data.pop("foreign_column")]
        return data

    @property
    def column(self) -> str:
        return self.columns[0]

    @property
    def foreign_column(self) -> str:
        return self.foreign_columns[0]

    @property
    def is_composite(self) -> bool:
        return len(self.columns) > 1

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        d: dict[str, Any] = {"foreign_table": self.foreign_table}
        if len(self.columns) == 1:
            d["column"] = self.columns[0]
            d["foreign_column"] = self.foreign_columns[0]
        else:
            d["columns"] = self.columns
            d["foreign_columns"] = self.foreign_columns
        if self.constraint_name is not None:
            d["constraint_name"] = self.constraint_name
        if self.is_unique:
            d["is_unique"] = True
        return d


class Column(BaseModel):
    name: str
    data_type: str
    is_nullable: bool = False
    is_primary_key: bool = False
    # Enrichment for downstream OWL/SHACL mapping (AOE contract). All optional /
    # back-compatible; older snapshots and the r2g re-import path are unaffected.
    is_unique: bool = False
    default: Optional[str] = None
    comment: Optional[str] = None
    ordinal: Optional[int] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def type_category(self) -> str:
        """Normalized type category derived from ``data_type`` (see typemap)."""
        return normalized_type_category(self.data_type)


class CheckConstraint(BaseModel):
    name: Optional[str] = None
    expression: str
    columns: List[str] = []
    # Best-effort recognition of ``col IN (...)`` → candidate enum values.
    enum_values: Optional[List[str]] = None


class Index(BaseModel):
    name: str
    columns: List[str]
    is_unique: bool = False
    is_primary: bool = False


class Table(BaseModel):
    name: str
    columns: List[Column]
    primary_key: List[str] = []
    foreign_keys: List[ForeignKey] = []
    # Partition metadata (PostgreSQL declarative partitioning). ``is_partitioned``
    # marks a partitioned *parent*; ``partition_of`` names the root parent of a
    # partition *child*. Non-partitioned tables (and non-Postgres sources) leave
    # both at their defaults. The default mapping collapses partition children
    # into their parent, so child FK constraints are rolled up onto the parent
    # during introspection.
    is_partitioned: bool = False
    partition_of: Optional[str] = None
    # Enrichment (AOE contract). All optional / back-compatible.
    schema_name: Optional[str] = None
    comment: Optional[str] = None
    is_view: bool = False
    unique_constraints: List[List[str]] = []
    check_constraints: List[CheckConstraint] = []
    indexes: List[Index] = []


class SourceProvenance(BaseModel):
    """Where a physical schema was introspected from (AOE stamps this per class)."""

    dialect: Optional[str] = None
    server_version: Optional[str] = None
    database: Optional[str] = None
    namespace: Optional[str] = None


class PhysicalSchema(BaseModel):
    tables: Dict[str, Table] = {}
    source: Optional[SourceProvenance] = None

    def save_to_file(self, path: str) -> None:
        with open(path, "w") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_file(cls, path: str) -> "PhysicalSchema":
        with open(path, "r") as f:
            return cls.model_validate_json(f.read())


# Back-compat alias: r2g and ported tests import ``Schema``.
Schema = PhysicalSchema
