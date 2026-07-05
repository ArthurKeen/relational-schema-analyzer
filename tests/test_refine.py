from __future__ import annotations

import json

import pytest

from relational_schema_analyzer import RelationalSchemaAnalyzer
from relational_schema_analyzer.baseline import infer_baseline
from relational_schema_analyzer.providers.base import LLMError, LLMResponse
from relational_schema_analyzer.refine import refine
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table


class FakeProvider:
    """Returns scripted responses in order; an Exception item is raised."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate(self, *, model, system, prompt, timeout_ms):
        item = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item)


def _schema() -> PhysicalSchema:
    users = Table(name="users", columns=[Column(name="id", data_type="integer",
                  is_primary_key=True), Column(name="email", data_type="text")],
                  primary_key=["id"])
    orders = Table(
        name="orders",
        columns=[Column(name="id", data_type="integer", is_primary_key=True),
                 Column(name="user_id", data_type="integer")],
        primary_key=["id"],
        foreign_keys=[ForeignKey(column="user_id", foreign_table="users", foreign_column="id")],
    )
    return PhysicalSchema(tables={"users": users, "orders": orders})


def _baseline():
    r = infer_baseline(_schema())
    return r["conceptualSchema"], r["physicalMapping"]


class TestRefineApply:
    def test_renames_and_hints_applied(self):
        conceptual, pm = _baseline()
        out = json.dumps({
            "entities": {"Users": {"name": "Customer", "description": "a customer"}},
            "relationships": {"Orders_Users": {"type": "placedBy", "embed": False}},
        })
        c, new_pm, info = refine(
            conceptual, pm, provider=FakeProvider([out]), model="m",
            timeout_ms=1000, max_repair_attempts=2,
        )
        names = {e["name"] for e in c["entities"]}
        assert names == {"Customer", "Orders"}
        customer = next(e for e in c["entities"] if e["name"] == "Customer")
        assert customer["source"] == "llm"
        assert customer["description"] == "a customer"
        assert customer["labels"] == ["Customer"]
        rel = next(r for r in c["relationships"] if r["type"] == "placedBy")
        assert rel["toEntity"] == "Customer"
        assert rel["source"] == "llm"
        # physical mapping keys are re-keyed consistently
        assert "Customer" in new_pm["entities"] and "Users" not in new_pm["entities"]
        assert new_pm["entities"]["Customer"]["tableName"] == "users"
        assert "placedBy" in new_pm["relationships"]
        assert info["entitiesRefined"] == 1
        # originals untouched (refine works on copies)
        assert {e["name"] for e in conceptual["entities"]} == {"Users", "Orders"}

    def test_collision_triggers_repair_then_succeeds(self):
        conceptual, pm = _baseline()
        bad = json.dumps({"entities": {"Users": {"name": "X"}, "Orders": {"name": "X"}}})
        good = json.dumps({"entities": {"Users": {"name": "Customer"}}})
        provider = FakeProvider([bad, good])
        c, _pm, info = refine(
            conceptual, pm, provider=provider, model="m",
            timeout_ms=1000, max_repair_attempts=2,
        )
        assert provider.calls == 2
        assert info["repairAttempts"] == 1
        assert {e["name"] for e in c["entities"]} == {"Customer", "Orders"}

    def test_invalid_json_exhausts_repairs_and_raises(self):
        conceptual, pm = _baseline()
        provider = FakeProvider(["not json", "still not json", "nope"])
        with pytest.raises(LLMError):
            refine(conceptual, pm, provider=provider, model="m",
                   timeout_ms=1000, max_repair_attempts=2)


class TestAnalyzerLLMPath:
    def test_refinement_applied_end_to_end(self):
        out = json.dumps({"entities": {"Users": {"name": "Customer"}}})
        analysis = RelationalSchemaAnalyzer(llm_provider=FakeProvider([out])).analyze(_schema())
        bundle = analysis.to_bundle()
        assert {e["name"] for e in bundle["conceptualSchema"]["entities"]} == {
            "Customer", "Orders",
        }
        assert bundle["metadata"]["llm"]["applied"] is True
        assert bundle["metadata"]["llm"]["entitiesRefined"] == 1

    def test_provider_failure_falls_back_to_baseline(self):
        analysis = RelationalSchemaAnalyzer(
            llm_provider=FakeProvider([RuntimeError("boom")])
        ).analyze(_schema())
        bundle = analysis.to_bundle()
        # baseline preserved
        assert {e["name"] for e in bundle["conceptualSchema"]["entities"]} == {
            "Users", "Orders",
        }
        assert bundle["metadata"]["llm"]["applied"] is False
        assert "boom" in bundle["metadata"]["llm"]["error"]

    def test_no_provider_has_no_llm_metadata(self):
        analysis = RelationalSchemaAnalyzer().analyze(_schema())
        assert "llm" not in analysis.to_bundle()["metadata"]
