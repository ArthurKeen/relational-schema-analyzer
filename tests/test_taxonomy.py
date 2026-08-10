"""Abstraction-discovery wiring (docs/DESIGN-ADDENDUM-taxonomy.md §2).

The mechanisms are tested in `conceptual-taxonomy` against eight physical encodings. These
cover the RSA side: assembling the inputs, the declared-containment asymmetry, and the merge.
"""

import pytest

from relational_schema_analyzer.taxonomy import (
    TAXONOMY_AVAILABLE,
    build_inputs,
    discover,
    merge_into_bundle,
    shared_pk_children,
)
from tests.test_discriminator import accounts_table, col, schema_of, subtype_table

requires_taxonomy = pytest.mark.skipif(
    not TAXONOMY_AVAILABLE, reason="conceptual-taxonomy not installed"
)

SUBTYPES = {
    "mortgage_account": [
        col("routing_number"),
        col("principal", "numeric"),
        col("monthly_payment", "numeric"),
    ],
    "checking_account": [col("routing_number"), col("overdraft_limit", "numeric")],
    "savings_account": [col("routing_number"), col("apy", "numeric")],
    "insurance_account": [col("premium", "numeric"), col("policy_number")],
}

NAMES = {
    "account": "Account",
    "mortgage_account": "MortgageAccount",
    "checking_account": "CheckingAccount",
    "savings_account": "SavingsAccount",
    "insurance_account": "InsuranceAccount",
}


def specialization_schema(*, check=True):
    """The ER pattern: supertype with a discriminator, subtypes on shared PK."""
    return schema_of(
        accounts_table(check=check),
        *[subtype_table(name, cols) for name, cols in SUBTYPES.items()],
    )


def bundle_for(schema):
    """A minimal bundle in RSA's dialect — style TABLE, tableName, not COLLECTION."""
    entities = []
    for table_name, table in schema.tables.items():
        entities.append(
            {
                "name": NAMES[table_name],
                "properties": [c.name for c in table.columns],
            }
        )
    return {
        "conceptualSchema": {"entities": entities, "relationships": []},
        "physicalMapping": {
            "entities": {NAMES[t]: {"style": "TABLE", "tableName": t} for t in schema.tables}
        },
    }


# ── shared-PK detection ──────────────────────────────────────────────────────


def test_finds_shared_pk_children():
    assert shared_pk_children(specialization_schema()) == {
        "account": sorted(SUBTYPES),
    }


def test_ordinary_reference_is_not_a_child():
    from relational_schema_analyzer.types import ForeignKey, Table

    schema = schema_of(
        accounts_table(),
        Table(
            name="statement",
            columns=[
                col("statement_id", "integer", is_primary_key=True),
                col("account_id", "integer"),
            ],
            primary_key=["statement_id"],
            foreign_keys=[
                ForeignKey(
                    columns=["account_id"], foreign_table="account", foreign_columns=["account_id"]
                )
            ],
        ),
    )
    assert shared_pk_children(schema) == {}


# ── input assembly ───────────────────────────────────────────────────────────


@requires_taxonomy
def test_containment_is_declared_not_measured():
    """The asymmetry with ArangoDB: an FK constraint *is* the containment guarantee."""
    _, containment, _ = build_inputs(specialization_schema(), NAMES)
    assert {c.child for c in containment} == {NAMES[t] for t in SUBTYPES}
    assert all(c.parent == "Account" and c.ratio == 1.0 for c in containment)


@requires_taxonomy
def test_discriminator_on_a_shared_pk_parent_names_the_parent_entity():
    discs, _, _ = build_inputs(specialization_schema(), NAMES)
    assert [d.parent_entity for d in discs] == ["Account"]


@requires_taxonomy
def test_plain_single_table_inheritance_has_no_parent_entity():
    """No subtype tables — the parent must be synthesized, not claimed."""
    discs, containment, _ = build_inputs(schema_of(accounts_table()), {"account": "Account"})
    assert [d.parent_entity for d in discs] == [None]
    assert containment == []


@requires_taxonomy
def test_measurements_need_a_counter():
    _, _, measurements = build_inputs(specialization_schema(), NAMES)
    assert measurements == []


@requires_taxonomy
def test_counter_supplies_disjointness_and_completeness():
    def counter(parent, pk, children):
        return {"total": 100, "inMultiple": 0, "inNone": 3}

    _, _, measurements = build_inputs(specialization_schema(), NAMES, counter=counter)
    assert len(measurements) == 1
    assert measurements[0].parent_keys_in_multiple_children == 0
    assert measurements[0].parent_keys_in_no_child == 3


@requires_taxonomy
def test_counter_failure_yields_no_measurement_rather_than_a_guess():
    def boom(parent, pk, children):
        raise RuntimeError("connection lost")

    _, _, measurements = build_inputs(specialization_schema(), NAMES, counter=boom)
    assert measurements == []


# ── discovery + merge ────────────────────────────────────────────────────────


@requires_taxonomy
def test_specialization_yields_one_parent_and_no_rival():
    schema = specialization_schema()
    proposals = discover(bundle_for(schema), schema)
    tops = [c for c in proposals["abstractClasses"] if len(c["members"]) == 4]
    assert len(tops) == 1
    assert tops[0]["conceptualClass"] == "Account"
    assert tops[0]["synthesized"] is False


@requires_taxonomy
def test_middle_layer_survives_an_explicit_parent():
    """The bug the fixtures caught: claiming a parent's children loses the layer below."""
    schema = specialization_schema()
    proposals = discover(bundle_for(schema), schema)
    extents = {frozenset(c["members"]) for c in proposals["abstractClasses"]}
    assert frozenset({"MortgageAccount", "CheckingAccount", "SavingsAccount"}) in extents


@requires_taxonomy
def test_aggregate_safety():
    schema = specialization_schema()
    proposals = discover(bundle_for(schema), schema)
    top = next(c for c in proposals["abstractClasses"] if len(c["members"]) == 4)
    partial = {p["name"]: p for p in top["partialProperties"]}
    assert partial["monthly_payment"]["presentOn"] == ["MortgageAccount"]


@requires_taxonomy
def test_merge_is_additive_and_leaves_baseline_subclassof_alone():
    schema = specialization_schema()
    bundle = bundle_for(schema)
    bundle["conceptualSchema"]["entities"][0]["subClassOf"] = "PreExisting"
    before = len(bundle["conceptualSchema"]["entities"])

    merged = merge_into_bundle(bundle, discover(bundle_for(schema), schema))
    assert merged["conceptualSchema"]["entities"][0]["subClassOf"] == "PreExisting"
    assert len(merged["conceptualSchema"]["entities"]) >= before
    assert merged["conceptualSchema"]["subClassOfProposals"]


def test_merge_with_no_proposals_is_a_no_op():
    bundle = {"conceptualSchema": {"entities": []}}
    assert merge_into_bundle(bundle, None) is bundle


def test_degrades_without_the_dependency(monkeypatch):
    monkeypatch.setattr("relational_schema_analyzer.taxonomy.TAXONOMY_AVAILABLE", False)
    schema = specialization_schema()
    assert discover(bundle_for(schema), schema) is None
    assert build_inputs(schema, NAMES) == ([], [], [])
