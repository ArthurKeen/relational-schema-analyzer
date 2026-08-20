"""Declared-key overlay — merging, provenance, and the strictness that makes it usable.

The module exists because a constraint-poor source (BigQuery, Glue, Hive) yields no keys, and
``fk_inference`` anchors on declared primary keys. Two properties carry most of the weight and
so get most of the tests: the catalog always beats the overlay, and a typo is an error rather
than a silent no-op.
"""

from __future__ import annotations

import json

import pytest

from relational_schema_analyzer.baseline import infer_baseline
from relational_schema_analyzer.overlay import (
    OVERLAY_CONSTRAINT_PREFIX,
    OVERLAY_EXTRA_KEY,
    OverlayError,
    apply_key_overlay,
    load_key_overlay,
    overlay_applied,
    overlay_summary,
)
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table


def _schema() -> PhysicalSchema:
    """A keyless two-table schema, the shape a lakehouse catalog hands us."""
    return PhysicalSchema(
        tables={
            "events": Table(
                name="events",
                columns=[
                    Column(name="GLOBALEVENTID", data_type="int64"),
                    Column(name="SQLDATE", data_type="int64"),
                    Column(name="SOURCEURL", data_type="string", is_nullable=True),
                ],
            ),
            "eventmentions": Table(
                name="eventmentions",
                columns=[
                    Column(name="GLOBALEVENTID", data_type="int64"),
                    Column(name="MentionIdentifier", data_type="string"),
                ],
            ),
        }
    )


_GDELT_OVERLAY = {
    "version": 1,
    "tables": {
        "events": {"primaryKey": ["GLOBALEVENTID"]},
        "eventmentions": {
            "foreignKeys": [
                {
                    "columns": ["GLOBALEVENTID"],
                    "references": {"table": "events", "columns": ["GLOBALEVENTID"]},
                    "comment": "GDELT codebook: mentions reference their event",
                }
            ]
        },
    },
}


class TestApply:
    def test_primary_key_applied_and_marked_on_columns(self):
        out = apply_key_overlay(_schema(), _GDELT_OVERLAY)
        events = out.tables["events"]
        assert events.primary_key == ["GLOBALEVENTID"]
        pk_col = next(c for c in events.columns if c.name == "GLOBALEVENTID")
        assert pk_col.is_primary_key
        # A lone PK column is unique by definition — the same rule every connector applies.
        assert pk_col.is_unique

    def test_composite_pk_members_are_not_marked_unique(self):
        overlay = {"tables": {"events": {"primaryKey": ["GLOBALEVENTID", "SQLDATE"]}}}
        out = apply_key_overlay(_schema(), overlay)
        assert out.tables["events"].primary_key == ["GLOBALEVENTID", "SQLDATE"]
        assert all(c.is_primary_key for c in out.tables["events"].columns[:2])
        assert not any(c.is_unique for c in out.tables["events"].columns)

    def test_foreign_key_is_labelled_as_asserted_not_verified(self):
        out = apply_key_overlay(_schema(), _GDELT_OVERLAY)
        (fk,) = out.tables["eventmentions"].foreign_keys
        assert fk.columns == ["GLOBALEVENTID"]
        assert fk.foreign_table == "events"
        assert fk.foreign_columns == ["GLOBALEVENTID"]
        # Nothing checked that the referenced rows exist.
        assert fk.enforced is False
        assert fk.constraint_name.startswith(OVERLAY_CONSTRAINT_PREFIX)

    def test_fk_cardinality_hint_from_overlay_supplied_uniqueness(self):
        """An FK unique on its own table is 1:1 — even when the overlay supplied the PK."""
        overlay = {
            "tables": {
                "events": {"primaryKey": ["GLOBALEVENTID"]},
                "eventmentions": {
                    "primaryKey": ["GLOBALEVENTID"],
                    "foreignKeys": [
                        {
                            "columns": ["GLOBALEVENTID"],
                            "references": {"table": "events", "columns": ["GLOBALEVENTID"]},
                        }
                    ],
                },
            }
        }
        out = apply_key_overlay(_schema(), overlay)
        assert out.tables["eventmentions"].foreign_keys[0].is_unique is True

    def test_unique_constraints_applied_and_deduplicated(self):
        overlay = {
            "tables": {"events": {"uniqueConstraints": [["SOURCEURL"], ["SOURCEURL"]]}}
        }
        out = apply_key_overlay(_schema(), overlay)
        assert out.tables["events"].unique_constraints == [["SOURCEURL"]]
        assert next(c for c in out.tables["events"].columns if c.name == "SOURCEURL").is_unique

    def test_input_schema_is_not_mutated(self):
        original = _schema()
        apply_key_overlay(original, _GDELT_OVERLAY)
        # Callers keep the raw snapshot to show what the source actually said.
        assert original.tables["events"].primary_key == []
        assert original.tables["eventmentions"].foreign_keys == []

    def test_case_insensitive_resolution_to_canonical_names(self):
        overlay = {"tables": {"EVENTS": {"primaryKey": ["globaleventid"]}}}
        out = apply_key_overlay(_schema(), overlay)
        assert out.tables["events"].primary_key == ["GLOBALEVENTID"]

    def test_table_without_overlay_entry_is_untouched(self):
        out = apply_key_overlay(_schema(), {"tables": {"events": {"primaryKey": ["SQLDATE"]}}})
        assert OVERLAY_EXTRA_KEY not in out.tables["eventmentions"].extra


class TestCatalogWins:
    """Rule 1: an overlay fills gaps; it never overrides what the source declared."""

    def test_declared_primary_key_is_not_replaced(self):
        schema = _schema()
        schema.tables["events"].primary_key = ["SQLDATE"]
        out = apply_key_overlay(schema, _GDELT_OVERLAY)
        assert out.tables["events"].primary_key == ["SQLDATE"]
        assert OVERLAY_EXTRA_KEY not in out.tables["events"].extra

    def test_declared_foreign_key_on_same_columns_is_not_duplicated(self):
        schema = _schema()
        schema.tables["eventmentions"].foreign_keys = [
            ForeignKey(
                columns=["GLOBALEVENTID"],
                foreign_table="events",
                foreign_columns=["GLOBALEVENTID"],
                constraint_name="declared_fk",
            )
        ]
        out = apply_key_overlay(schema, _GDELT_OVERLAY)
        (fk,) = out.tables["eventmentions"].foreign_keys
        assert fk.constraint_name == "declared_fk"
        assert fk.enforced is True


class TestStrictness:
    """A typo'd overlay that quietly does nothing is worse than one that fails."""

    def test_unknown_table_names_itself(self):
        with pytest.raises(OverlayError, match="unknown table 'evnets'"):
            apply_key_overlay(_schema(), {"tables": {"evnets": {"primaryKey": ["SQLDATE"]}}})

    def test_unknown_column_names_itself(self):
        with pytest.raises(OverlayError, match="unknown column 'GLOBALEVENTI'"):
            apply_key_overlay(
                _schema(), {"tables": {"events": {"primaryKey": ["GLOBALEVENTI"]}}}
            )

    def test_misspelled_key_is_rejected_rather_than_ignored(self):
        # The motivating failure: this would otherwise apply nothing and be discovered
        # later as an ontology with no relationships.
        with pytest.raises(OverlayError, match="primarykey"):
            apply_key_overlay(_schema(), {"tables": {"events": {"primarykey": ["SQLDATE"]}}})

    def test_mismatched_reference_column_counts(self):
        overlay = {
            "tables": {
                "eventmentions": {
                    "foreignKeys": [
                        {
                            "columns": ["GLOBALEVENTID"],
                            "references": {
                                "table": "events",
                                "columns": ["GLOBALEVENTID", "SQLDATE"],
                            },
                        }
                    ]
                }
            }
        }
        with pytest.raises(OverlayError, match="mismatched column counts"):
            apply_key_overlay(_schema(), overlay)

    def test_unknown_referenced_table(self):
        overlay = {
            "tables": {
                "eventmentions": {
                    "foreignKeys": [
                        {
                            "columns": ["GLOBALEVENTID"],
                            "references": {"table": "gkg", "columns": ["x"]},
                        }
                    ]
                }
            }
        }
        with pytest.raises(OverlayError, match="unknown table 'gkg'"):
            apply_key_overlay(_schema(), overlay)

    def test_unsupported_version(self):
        with pytest.raises(OverlayError, match="Unsupported overlay version"):
            apply_key_overlay(_schema(), {"version": 99, "tables": {}})

    def test_missing_tables_key(self):
        with pytest.raises(OverlayError, match="'tables'"):
            apply_key_overlay(_schema(), {"version": 1})

    def test_foreign_key_without_references(self):
        overlay = {
            "tables": {"eventmentions": {"foreignKeys": [{"columns": ["GLOBALEVENTID"]}]}}
        }
        with pytest.raises(OverlayError, match="references"):
            apply_key_overlay(_schema(), overlay)


class TestProvenance:
    def test_marker_records_what_was_supplied(self):
        out = apply_key_overlay(_schema(), _GDELT_OVERLAY)
        assert out.tables["events"].extra[OVERLAY_EXTRA_KEY] == {
            "primaryKey": ["GLOBALEVENTID"]
        }
        assert out.tables["eventmentions"].extra[OVERLAY_EXTRA_KEY] == {"foreignKeys": 1}
        assert overlay_applied(out)
        assert overlay_summary(out) == {
            "tables": 2,
            "primaryKeys": 1,
            "foreignKeys": 1,
            "uniqueConstraints": 0,
        }

    def test_no_overlay_leaves_serialization_byte_identical(self):
        """``extra`` is omitted when empty, so fingerprints are unaffected by this feature."""
        schema = _schema()
        before = schema.model_dump_json()
        after = apply_key_overlay(schema, {"tables": {}}).model_dump_json()
        assert before == after

    def test_marker_survives_a_snapshot_round_trip(self, tmp_path):
        out = apply_key_overlay(_schema(), _GDELT_OVERLAY)
        path = tmp_path / "physical.json"
        out.save_to_file(str(path))
        reloaded = PhysicalSchema.load_from_file(str(path))
        assert overlay_applied(reloaded)
        assert reloaded.tables["eventmentions"].foreign_keys[0].enforced is False

    def test_baseline_reports_the_pattern_and_an_assumption(self):
        bundle = infer_baseline(apply_key_overlay(_schema(), _GDELT_OVERLAY))
        assert "overlay_declared_keys" in bundle["detectedPatterns"]
        assert any("declared-key overlay" in a for a in bundle["assumptions"])

    def test_overlay_keys_produce_the_relationship_that_was_missing(self):
        """The whole point: keyless in, related out."""
        without = infer_baseline(_schema())
        assert without["conceptualSchema"]["relationships"] == []
        with_overlay = infer_baseline(apply_key_overlay(_schema(), _GDELT_OVERLAY))
        (rel,) = with_overlay["conceptualSchema"]["relationships"]
        assert rel["fromEntity"] == "Eventmentions"
        assert rel["toEntity"] == "Events"

    def test_overlay_is_not_review_flagged(self):
        """A human wrote these down; that is more review than an inferred FK ever gets."""
        schema = apply_key_overlay(_schema(), _GDELT_OVERLAY)
        # Give the second table a PK too, so `missing_primary_key` isn't what sets the flag.
        schema.tables["eventmentions"].primary_key = ["MentionIdentifier"]
        bundle = infer_baseline(schema)
        assert bundle["reviewRequired"] is False


class TestLoad:
    def test_loads_json(self, tmp_path):
        path = tmp_path / "keys.overlay.json"
        path.write_text(json.dumps(_GDELT_OVERLAY), encoding="utf-8")
        assert load_key_overlay(str(path)) == _GDELT_OVERLAY

    def test_loads_yaml_when_pyyaml_available(self, tmp_path):
        pytest.importorskip("yaml")
        import yaml

        path = tmp_path / "keys.overlay.yaml"
        path.write_text(yaml.safe_dump(_GDELT_OVERLAY), encoding="utf-8")
        assert load_key_overlay(str(path)) == _GDELT_OVERLAY

    def test_missing_file(self, tmp_path):
        with pytest.raises(OverlayError, match="not found"):
            load_key_overlay(str(tmp_path / "nope.json"))

    def test_malformed_json_reports_the_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(OverlayError, match="Failed to parse overlay"):
            load_key_overlay(str(path))

    def test_non_object_top_level(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(OverlayError, match="object at the top level"):
            load_key_overlay(str(path))
