from __future__ import annotations

import pytest

from relational_schema_analyzer import RelationalSchemaAnalyzer, export_bundle
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table


def _schema() -> PhysicalSchema:
    users = Table(name="users", columns=[Column(name="id", data_type="integer",
                  is_primary_key=True)], primary_key=["id"])
    orders = Table(
        name="orders",
        columns=[Column(name="id", data_type="integer", is_primary_key=True),
                 Column(name="user_id", data_type="integer")],
        primary_key=["id"],
        foreign_keys=[ForeignKey(column="user_id", foreign_table="users", foreign_column="id")],
    )
    return PhysicalSchema(tables={"users": users, "orders": orders})


def test_export_bundle_from_analysis():
    analysis = RelationalSchemaAnalyzer().analyze(_schema())
    assert export_bundle(analysis) == analysis.to_bundle()


def test_export_bundle_passthrough_dict():
    bundle = {"conceptualSchema": {}, "physicalMapping": {}, "metadata": {}}
    assert export_bundle(bundle) is bundle


def test_export_bundle_rejects_other():
    with pytest.raises(TypeError):
        export_bundle(42)
