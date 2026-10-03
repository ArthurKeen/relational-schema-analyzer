"""Draft key overlays -- proposals for review, never applied. Via fakesnow."""

from __future__ import annotations

import json

import pytest

fakesnow = pytest.importorskip("fakesnow")
pytest.importorskip("snowflake.connector")

from relational_schema_analyzer.connectors.snowflake import SnowflakeConnector  # noqa: E402
from relational_schema_analyzer.fk_inference import SnowflakeValueSampler  # noqa: E402
from relational_schema_analyzer.key_profiling import draft_key_overlay  # noqa: E402
from relational_schema_analyzer.overlay import (  # noqa: E402
    apply_key_overlay,
    load_key_overlay,
)

_GEN = "FROM TABLE(GENERATOR(ROWCOUNT => {n}))"
_DDL = [
    "CREATE TABLE CUSTOMERS (CUSTOMER_ID INT, EMAIL VARCHAR)",
    "INSERT INTO CUSTOMERS SELECT seq4() + 1, 'u' || seq4() || '@x.io' " + _GEN.format(n=40),
    # A real reference: every CUSTOMER_ID exists in CUSTOMERS.
    "CREATE TABLE ORDERS (ORDER_ID INT, CUSTOMER_ID INT, TOTAL INT)",
    "INSERT INTO ORDERS SELECT seq4() + 1, MOD(seq4(), 40) + 1, 5 " + _GEN.format(n=60),
    # Named like a reference, but no value matches: inference proposes it by name,
    # the sampler's zero overlap must veto it.
    "CREATE TABLE STRAYS (STRAY_ID INT, CUSTOMER_ID INT)",
    "INSERT INTO STRAYS SELECT seq4() + 1, seq4() + 9000 " + _GEN.format(n=20),
    # Nothing here can be a key.
    "CREATE TABLE NOTES (BODY VARCHAR)",
    "INSERT INTO NOTES VALUES ('a'), ('a'), ('b')",
]


@pytest.fixture
def env():
    import snowflake.connector as sc

    with fakesnow.patch():
        conn = sc.connect(database="DB1", schema="PUBLIC")
        cur = conn.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        cur.close()
        schema = SnowflakeConnector("snowflake://u:p@acct/DB1/PUBLIC").get_schema()
        probe = SnowflakeValueSampler(connection=conn, schema_name="PUBLIC", limit=100)
        yield schema, probe
        conn.close()


def _fks(draft):
    return {
        (t, tuple(f["columns"]), f["references"]["table"])
        for t, spec in draft.overlay["tables"].items()
        for f in spec.get("foreignKeys", [])
    }


def test_proposes_primary_keys_with_their_reasons(env):
    schema, probe = env
    draft = draft_key_overlay(schema, probe, sampler=probe)
    tables = draft.overlay["tables"]

    assert tables["CUSTOMERS"]["primaryKey"] == ["CUSTOMER_ID"]
    assert tables["ORDERS"]["primaryKey"] == ["ORDER_ID"]
    assert "named after its table" in tables["CUSTOMERS"]["description"]
    # Alternatives are shown to the reviewer, not chosen for them.
    assert "Also unique: ['EMAIL']" in tables["CUSTOMERS"]["description"]


def test_a_real_reference_is_proposed_with_its_overlap(env):
    schema, probe = env
    draft = draft_key_overlay(schema, probe, sampler=probe)
    assert ("ORDERS", ("CUSTOMER_ID",), "CUSTOMERS") in _fks(draft)
    (orders_fk,) = draft.overlay["tables"]["ORDERS"]["foreignKeys"]
    assert "value overlap avg=1.00" in orders_fk["comment"]


def test_a_name_match_with_no_matching_values_is_vetoed(env):
    schema, probe = env
    assert ("STRAYS", ("CUSTOMER_ID",), "CUSTOMERS") not in _fks(
        draft_key_overlay(schema, probe, sampler=probe)
    )


def test_without_a_sampler_name_matches_are_kept(env):
    # Shows what the veto above is worth: by name alone, the stray looks real.
    schema, probe = env
    assert ("STRAYS", ("CUSTOMER_ID",), "CUSTOMERS") in _fks(draft_key_overlay(schema, probe))


def test_tables_without_a_proposal_say_why(env):
    schema, probe = env
    draft = draft_key_overlay(schema, probe, sampler=probe)
    assert "NOTES" not in draft.overlay["tables"]
    assert "non-null and unique" in draft.no_key_proposed["NOTES"]


def test_a_declared_primary_key_is_left_alone(env):
    schema, probe = env
    schema.tables["CUSTOMERS"].primary_key = ["CUSTOMER_ID"]
    draft = draft_key_overlay(schema, probe, sampler=probe)
    assert "primaryKey" not in draft.overlay["tables"].get("CUSTOMERS", {})
    assert draft.no_key_proposed["CUSTOMERS"] == "the source already declares a primary key"
    # The declared key is still a target for references.
    assert ("ORDERS", ("CUSTOMER_ID",), "CUSTOMERS") in _fks(draft)


def test_low_scoring_keys_are_withheld(env):
    schema, probe = env
    draft = draft_key_overlay(schema, probe, sampler=probe, min_pk_score=0.99)
    assert draft.overlay["tables"] == {}
    assert "below 0.99" in draft.no_key_proposed["CUSTOMERS"]


def test_the_draft_round_trips_through_a_file_and_the_loader(env, tmp_path):
    schema, probe = env
    draft = draft_key_overlay(schema, probe, sampler=probe)
    path = tmp_path / "keys.draft.json"
    path.write_text(json.dumps(draft.overlay, indent=2))

    applied = apply_key_overlay(schema, load_key_overlay(path))
    assert applied.tables["CUSTOMERS"].primary_key == ["CUSTOMER_ID"]
    (fk,) = applied.tables["ORDERS"].foreign_keys
    assert fk.enforced is False and fk.constraint_name.startswith("overlay:")
    assert draft.overlay["description"].startswith("DRAFT")


def test_drafting_never_mutates_the_schema(env):
    schema, probe = env
    before = schema.model_dump()
    draft_key_overlay(schema, probe, sampler=probe)
    assert schema.model_dump() == before
