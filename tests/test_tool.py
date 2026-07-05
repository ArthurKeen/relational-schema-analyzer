from __future__ import annotations

from pathlib import Path

import pytest

from relational_schema_analyzer import run_tool

pytest.importorskip("polars")

_CSV_DIR = str(Path(__file__).resolve().parent / "fixtures" / "csv_demo")


def _csv_source():
    return {"type": "csv", "url": _CSV_DIR}


class TestSnapshot:
    def test_ok(self):
        resp = run_tool({"operation": "snapshot", "source": _csv_source()})
        assert resp["ok"] is True
        assert resp["operation"] == "snapshot"
        assert sorted(resp["result"]["physical"]["tables"]) == [
            "authors", "books", "loans", "members",
        ]

    def test_request_id_echoed(self):
        resp = run_tool({"operation": "snapshot", "requestId": "abc", "source": _csv_source()})
        assert resp["requestId"] == "abc"


class TestAnalyze:
    def test_ok(self):
        resp = run_tool({"operation": "analyze", "source": _csv_source()})
        assert resp["ok"] is True
        bundle = resp["result"]["analysis"]
        assert set(bundle) == {"conceptualSchema", "physicalMapping", "metadata"}
        assert {e["name"] for e in bundle["conceptualSchema"]["entities"]} == {
            "Authors", "Books", "Loans", "Members",
        }

    def test_analyze_from_captured_physical(self):
        snap = run_tool({"operation": "snapshot", "source": _csv_source()})
        physical = snap["result"]["physical"]
        resp = run_tool({"operation": "analyze", "input": {"physical": physical}})
        assert resp["ok"] is True
        assert resp["result"]["analysis"]["conceptualSchema"]["entities"]


class TestOwl:
    def test_turtle(self):
        resp = run_tool({"operation": "owl", "source": _csv_source()})
        assert resp["ok"] is True
        assert resp["result"]["owl"]["format"] == "turtle"
        assert "a owl:Class" in resp["result"]["owl"]["content"]

    def test_jsonld(self):
        resp = run_tool({"operation": "owl", "source": _csv_source(),
                         "owl": {"format": "jsonld"}})
        assert resp["ok"] is True
        assert "@graph" in resp["result"]["owl"]["content"]

    def test_iri_override(self):
        resp = run_tool({"operation": "owl", "source": _csv_source(),
                         "owl": {"format": "turtle", "iriBase": "http://ex.org/c#"}})
        assert "@prefix : <http://ex.org/c#> ." in resp["result"]["owl"]["content"]


class TestErrors:
    def test_not_a_dict(self):
        resp = run_tool("nope")  # type: ignore[arg-type]
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_bad_operation(self):
        resp = run_tool({"operation": "frobnicate", "source": _csv_source()})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_missing_source(self):
        resp = run_tool({"operation": "analyze"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "INVALID_REQUEST"

    def test_bad_source_surfaces_source_error(self):
        resp = run_tool({"operation": "snapshot",
                         "source": {"type": "csv", "url": "/no/such/dir"}})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "SOURCE_ERROR"
