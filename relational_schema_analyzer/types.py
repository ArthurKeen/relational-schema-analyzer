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

from pydantic import BaseModel, model_serializer, model_validator


class ForeignKey(BaseModel):
    """A foreign key constraint, supporting both single- and multi-column FKs.

    Accepts legacy ``column``/``foreign_column`` (str) or composite
    ``columns``/``foreign_columns`` (list[str]).
    """

    columns: List[str]
    foreign_table: str
    foreign_columns: List[str]
    constraint_name: Optional[str] = None

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
        return d


class Column(BaseModel):
    name: str
    data_type: str
    is_nullable: bool = False
    is_primary_key: bool = False


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


class PhysicalSchema(BaseModel):
    tables: Dict[str, Table] = {}

    def save_to_file(self, path: str) -> None:
        with open(path, "w") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_file(cls, path: str) -> "PhysicalSchema":
        with open(path, "r") as f:
            return cls.model_validate_json(f.read())


# Back-compat alias: r2g and ported tests import ``Schema``.
Schema = PhysicalSchema
