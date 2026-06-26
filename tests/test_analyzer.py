from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from relational_schema_analyzer import (
    Analysis,
    RelationalSchemaAnalyzer,
    fingerprint_physical_schema,
)
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table

_CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "tool-contract"
    / "v1"
    / "response.schema.json"
)


def _col(name, data_type="integer", nullable=False, pk=False):
    return Column(name=name, data_type=data_type, is_nullable=nullable, is_primary_key=pk)


def _sample_schema() -> PhysicalSchema:
    users = Table(
        name="users",
        columns=[_col("id", pk=True), _col("name", "text"), _col("email", "varchar", nullable=True)],
        primary_key=["id"],
    )
    orders = Table(
        name="orders",
        columns=[_col("id", pk=True), _col("user_id"), _col("total", "numeric", nullable=True)],
        primary_key=["id"],
        foreign_keys=[ForeignKey(column="user_id", foreign_table="users", foreign_column="id")],
    )
    return PhysicalSchema(tables={"users": users, "orders": orders})


class TestAnalyze:
    def test_returns_analysis_with_bundle_shape(self):
        analysis = RelationalSchemaAnalyzer().analyze(_sample_schema())
        assert isinstance(analysis, Analysis)
        bundle = analysis.to_bundle()
        assert set(bundle.keys()) == {"conceptualSchema", "physicalMapping", "metadata"}
        assert {e["name"] for e in bundle["conceptualSchema"]["entities"]} == {"Users", "Orders"}

    def test_metadata_fields_present(self):
        analysis = RelationalSchemaAnalyzer().analyze(_sample_schema())
        meta = analysis.metadata
        assert 0.0 <= meta["confidence"] <= 1.0
        assert meta["analyzedCollectionCounts"] == {
            "documentCollections": 2,
            "edgeCollections": 1,
        }
        assert meta["reviewRequired"] is False
        assert meta["physicalSchemaFingerprint"].startswith("sha256-")
        assert "timestamp" in meta
        assert meta["detectedPatterns"] == []

    def test_baseline_runs_without_llm(self):
        analysis = RelationalSchemaAnalyzer(llm_provider=None).analyze(_sample_schema())
        assert analysis.metadata["generator"] == "relational-schema-analyzer"


class TestFingerprint:
    def test_stable_across_calls(self):
        schema = _sample_schema()
        assert fingerprint_physical_schema(schema) == fingerprint_physical_schema(schema)

    def test_changes_when_schema_changes(self):
        a = fingerprint_physical_schema(_sample_schema())
        schema = _sample_schema()
        schema.tables["users"].columns.append(_col("age"))
        assert fingerprint_physical_schema(schema) != a


class TestContractConformance:
    def test_bundle_validates_against_analysis_output_schema(self):
        schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
        sub = {"$ref": "#/$defs/AnalysisOutput", "$defs": schema["$defs"]}
        validator = Draft202012Validator(sub)

        bundle = RelationalSchemaAnalyzer().analyze(_sample_schema()).to_bundle()
        errors = sorted(validator.iter_errors(bundle), key=lambda e: e.path)
        assert errors == [], "\n".join(f"{list(e.path)}: {e.message}" for e in errors)

    def test_join_table_bundle_validates(self):
        students = Table(name="students", columns=[_col("id", pk=True)], primary_key=["id"])
        courses = Table(name="courses", columns=[_col("id", pk=True)], primary_key=["id"])
        enrollments = Table(
            name="enrollments",
            columns=[_col("student_id", pk=True), _col("course_id", pk=True),
                     _col("grade", "text", nullable=True)],
            primary_key=["student_id", "course_id"],
            foreign_keys=[
                ForeignKey(column="student_id", foreign_table="students", foreign_column="id"),
                ForeignKey(column="course_id", foreign_table="courses", foreign_column="id"),
            ],
        )
        schema_dict = json.loads(_CONTRACT.read_text(encoding="utf-8"))
        sub = {"$ref": "#/$defs/AnalysisOutput", "$defs": schema_dict["$defs"]}
        validator = Draft202012Validator(sub)

        bundle = RelationalSchemaAnalyzer().analyze(
            PhysicalSchema(tables={t.name: t for t in (students, courses, enrollments)})
        ).to_bundle()
        errors = sorted(validator.iter_errors(bundle), key=lambda e: e.path)
        assert errors == [], "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize("n_tables", [1, 3])
def test_analyze_is_deterministic(n_tables):
    schema = _sample_schema()
    b1 = RelationalSchemaAnalyzer().analyze(schema).to_bundle()
    b2 = RelationalSchemaAnalyzer().analyze(schema).to_bundle()
    # Drop timestamp (wall-clock) before comparing.
    b1["metadata"].pop("timestamp")
    b2["metadata"].pop("timestamp")
    assert b1 == b2
