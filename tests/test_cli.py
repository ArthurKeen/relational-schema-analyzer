from __future__ import annotations

import json
from pathlib import Path

import pytest

from relational_schema_analyzer.cli import main
from relational_schema_analyzer.types import PhysicalSchema

pytest.importorskip("polars")

_CSV_DIR = Path(__file__).resolve().parent / "fixtures" / "csv_demo"


@pytest.fixture
def snapshot_file(tmp_path) -> str:
    out = tmp_path / "physical.json"
    rc = main(["snapshot", "--source", "csv", "--url", str(_CSV_DIR), "-o", str(out)])
    assert rc == 0
    return str(out)


class TestSnapshot:
    def test_writes_valid_physical_schema(self, snapshot_file):
        schema = PhysicalSchema.load_from_file(snapshot_file)
        assert sorted(schema.tables) == ["authors", "books", "loans", "members"]

    def test_to_stdout(self, capsys):
        rc = main(["snapshot", "--source", "csv", "--url", str(_CSV_DIR)])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "tables" in data


class TestAnalyze:
    def test_emits_bundle_from_snapshot(self, snapshot_file, capsys):
        rc = main(["analyze", "--from-snapshot", snapshot_file])
        assert rc == 0
        bundle = json.loads(capsys.readouterr().out)
        assert set(bundle) == {"conceptualSchema", "physicalMapping", "metadata"}
        assert {e["name"] for e in bundle["conceptualSchema"]["entities"]} == {
            "Authors", "Books", "Loans", "Members",
        }

    def test_requires_source_or_snapshot(self):
        with pytest.raises(SystemExit):
            main(["analyze"])


class TestOwl:
    def test_turtle_to_file(self, snapshot_file, tmp_path):
        ttl = tmp_path / "out.ttl"
        rc = main(["owl", "--from-snapshot", snapshot_file, "--format", "turtle", "-o", str(ttl)])
        assert rc == 0
        text = ttl.read_text(encoding="utf-8")
        assert "a owl:Class" in text
        assert 'phys:tableName "authors"' in text

    def test_jsonld_to_stdout(self, snapshot_file, capsys):
        rc = main(["owl", "--from-snapshot", snapshot_file, "--format", "jsonld"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert "@graph" in doc and "@context" in doc

    def test_iri_base_override(self, snapshot_file, capsys):
        rc = main([
            "owl", "--from-snapshot", snapshot_file, "--format", "turtle",
            "--iri-base", "http://example.org/c#",
            "--phys-iri-base", "http://example.org/p#",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "@prefix : <http://example.org/c#> ." in out
