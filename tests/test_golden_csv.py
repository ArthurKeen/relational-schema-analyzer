"""Offline golden corpus: the r2g CSV demo (authors / books / members / loans).

This is an end-to-end regression test that needs **no database and no Docker** —
the CSV connector reads the fixture directory directly (polars), so it exercises
the full ``connector -> baseline -> inferred-FK`` path on a realistic multi-table
corpus. The SQL-dump corpora (pagila / chinook / northwind) genuinely require a
live DB to introspect and are covered by Phase 5 Docker integration tests.

To regenerate the golden bundle after an intentional behavior change::

    python -c "import json; from relational_schema_analyzer import \
create_connector, RelationalSchemaAnalyzer; \
b=RelationalSchemaAnalyzer().analyze(create_connector('csv','tests/fixtures/csv_demo')\
.get_schema()).to_bundle(); b['metadata'].pop('timestamp',None); \
open('tests/fixtures/csv_demo_bundle.golden.json','w').write(json.dumps(b,indent=2,sort_keys=True)+'\n')"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

pytest.importorskip("polars")

from relational_schema_analyzer import (  # noqa: E402
    RelationalSchemaAnalyzer,
    create_connector,
)

_HERE = Path(__file__).resolve().parent
_CSV_DIR = _HERE / "fixtures" / "csv_demo"
_GOLDEN = _HERE / "fixtures" / "csv_demo_bundle.golden.json"
_CONTRACT = _HERE.parent / "docs" / "tool-contract" / "v1" / "response.schema.json"


def _bundle():
    physical = create_connector("csv", str(_CSV_DIR)).get_schema()
    return RelationalSchemaAnalyzer().analyze(physical).to_bundle()


class TestCsvDemoPhysical:
    def test_introspects_four_tables_with_id_pks(self):
        physical = create_connector("csv", str(_CSV_DIR)).get_schema()
        assert sorted(physical.tables) == ["authors", "books", "loans", "members"]
        for name in physical.tables:
            assert physical.tables[name].primary_key == ["id"]


class TestCsvDemoAnalysis:
    def test_entities_and_inferred_relationships(self):
        bundle = _bundle()
        names = {e["name"] for e in bundle["conceptualSchema"]["entities"]}
        assert names == {"Authors", "Books", "Loans", "Members"}

        rels = {
            r["type"]: r for r in bundle["conceptualSchema"]["relationships"]
        }
        # CSV has no declared FKs → all relationships are name-inferred.
        assert set(rels) == {"Books_Authors", "Loans_Books", "Loans_Members"}
        for r in rels.values():
            assert r["inferred"] is True
            assert r["cardinality"] == "1:N"
        assert rels["Books_Authors"]["toEntity"] == "Authors"
        assert rels["Loans_Books"]["toEntity"] == "Books"
        assert rels["Loans_Members"]["toEntity"] == "Members"

    def test_review_flagged_for_inference(self):
        meta = _bundle()["metadata"]
        assert meta["reviewRequired"] is True
        assert meta["detectedPatterns"] == ["inferred_foreign_keys"]

    def test_validates_against_contract(self):
        schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
        validator = Draft202012Validator(
            {"$ref": "#/$defs/AnalysisOutput", "$defs": schema["$defs"]}
        )
        errors = sorted(validator.iter_errors(_bundle()), key=lambda e: e.path)
        assert errors == [], "\n".join(f"{list(e.path)}: {e.message}" for e in errors)

    def test_matches_golden_bundle(self):
        golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
        bundle = _bundle()
        bundle["metadata"].pop("timestamp", None)
        assert bundle == golden
