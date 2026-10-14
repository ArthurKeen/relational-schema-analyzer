"""Key discovery from data on Postgres (opt-in, live database).

Until this, ``SnowflakeValueSampler`` was the only sampler implementing ``KeyProbe``
(``column_stats`` / ``combo_stats``), so ``profile_primary_keys`` and
``draft_key_overlay`` — the feature built for sources whose catalog declares no keys
— could not run against a Postgres source at all. Issue #18.

The fixture is a keyless warehouse landing: three tables, no primary keys, no unique
constraints, no foreign keys. Inference alone finds nothing there, which is the point;
the whole value is in what profiling adds.

Run: RUN_INTEGRATION=1 RSA_PG_DSN='postgresql://...' pytest tests/integration/test_key_profiling_postgres_live.py
"""

from __future__ import annotations

import os

import pytest

from relational_schema_analyzer.connectors.postgres import PostgresConnector
from relational_schema_analyzer.fk_inference import (
    InferenceOptions,
    PostgresValueSampler,
    infer_foreign_keys,
)
from relational_schema_analyzer.key_profiling import draft_key_overlay, profile_primary_keys

_DSN = os.environ.get("RSA_PG_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="opt-in"),
    pytest.mark.skipif(not _DSN, reason="RSA_PG_DSN not set"),
]

SCHEMA = "rsa_keyprobe_it"


@pytest.fixture()
def keyless_schema():
    """Three tables a warehouse landing would produce: data, and nothing declared."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        c.execute(f"CREATE TABLE {SCHEMA}.accounts (account_id text, name text, region text)")
        c.execute(
            f"INSERT INTO {SCHEMA}.accounts "
            "SELECT 'A'||g, 'n'||g, 'r'||(g%3) FROM generate_series(1,500) g"
        )
        c.execute(
            f"CREATE TABLE {SCHEMA}.contracts (contract_id text, account_id text, amount bigint)"
        )
        c.execute(
            f"INSERT INTO {SCHEMA}.contracts "
            "SELECT 'C'||g, 'A'||((g%500)+1), g FROM generate_series(1,2000) g"
        )
        c.execute(f"CREATE TABLE {SCHEMA}.contacts (email text, account_id text)")
        c.execute(
            f"INSERT INTO {SCHEMA}.contacts "
            "SELECT 'e'||g||'@x.example', 'A'||((g%500)+1) FROM generate_series(1,1500) g"
        )
    yield
    with psycopg.connect(_DSN, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture()
def parts(keyless_schema):
    schema = PostgresConnector(_DSN, schema_name=SCHEMA).get_schema()
    probe = PostgresValueSampler(_DSN, schema_name=SCHEMA, limit=10_000)
    yield schema, probe
    probe.close()


def test_the_schema_really_declares_nothing(parts):
    """Guards the premise: if the fixture gained a key, the rest proves less."""
    schema, _ = parts
    assert all(not t.primary_key for t in schema.tables.values())
    assert all(not t.unique_constraints for t in schema.tables.values())
    assert sum(len(t.foreign_keys) for t in schema.tables.values()) == 0
    assert infer_foreign_keys(schema, options=InferenceOptions()) == [], (
        "inference must find nothing here — the value of profiling is what it adds"
    )


def test_keys_are_discovered_from_the_data(parts):
    schema, probe = parts
    profile = profile_primary_keys(schema, probe)
    assert not profile.not_evaluated, (
        f"every table should be decidable here; not evaluated: {profile.not_evaluated}"
    )
    best = {t: profile.best(t) for t in profile.candidates}
    assert best["accounts"].columns == ("account_id",)
    assert best["contracts"].columns == ("contract_id",)
    # 500 distinct over 500 rows is what makes it a key, not the name.
    assert best["accounts"].rows == 500 and best["accounts"].distinct == 500


def test_the_draft_proposes_the_references_those_keys_unlock(parts):
    schema, probe = parts
    draft = draft_key_overlay(schema, probe, sampler=probe)
    tables = draft.overlay.get("tables", {})
    found = {
        (t, tuple(fk["columns"]), fk["references"]["table"], tuple(fk["references"]["columns"]))
        for t, body in tables.items()
        for fk in (body.get("foreignKeys") or [])
    }
    assert ("contracts", ("account_id",), "accounts", ("account_id",)) in found
    assert ("contacts", ("account_id",), "accounts", ("account_id",)) in found


def test_a_probe_that_cannot_run_declines_rather_than_guessing(parts):
    """"Could not evaluate" must stay distinguishable from "no key here"."""
    schema, _ = parts
    broken = PostgresValueSampler(
        "postgresql://nobody:nobody@127.0.0.1:1/nothing", schema_name=SCHEMA, limit=100
    )
    try:
        assert broken.column_stats("accounts", ["account_id"], sample=True) is None
        assert broken.combo_stats("accounts", ["account_id"], sample=False) is None
        profile = profile_primary_keys(schema, broken)
        assert profile.not_evaluated, "a dead probe must leave tables not_evaluated"
        assert not profile.candidates, "and must not propose anything"
    finally:
        broken.close()


def test_sampling_screens_and_the_full_count_confirms(parts):
    """``sample=True`` bounds the read; ``sample=False`` counts the table."""
    _, probe = parts
    sampled = probe.column_stats("contracts", ["contract_id"], sample=True)
    full = probe.column_stats("contracts", ["contract_id"], sample=False)
    assert sampled is not None and full is not None
    assert full["contract_id"][0] == 2000
    assert sampled["contract_id"][0] <= 2000
    combo = probe.combo_stats("contracts", ["contract_id"], sample=False)
    assert combo == (2000, 2000, 2000)


def test_identifiers_that_need_escaping_are_handled(parts):
    """A column named ``a"b`` is legal; before the quoting fix it produced a syntax
    error, the probe returned ``None``, and that table's key could never be found —
    silently, since "could not evaluate" looks the same as a dead connection."""
    _, probe = parts
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN, autocommit=True) as c:
        c.execute(f'CREATE TABLE {SCHEMA}.odd ("Weird Col" text, "a""b" text, "order" text)')
        c.execute(f"INSERT INTO {SCHEMA}.odd VALUES ('x1','y1','z1'), ('x2','y2','z2')")
    for col in ("Weird Col", 'a"b', "order"):
        assert probe.column_stats("odd", [col], sample=False) == {col: (2, 2, 2)}, (
            f"column {col!r} could not be profiled"
        )
    assert probe.combo_stats("odd", ['a"b', "order"], sample=False) == (2, 2, 2)


# ── Additional candidate keys on already-keyed tables (#20) ──────────────────


@pytest.fixture()
def surrogate_pk_schema():
    """The shape that yielded nothing: a surrogate PK, and the real key undeclared.

    Children reference ``account_id``; nothing declares it unique; ``accounts`` has a
    ``bigserial`` primary key that no child points at. Before #20 every table here was
    skipped for having a primary key, so the one fact that unlocks the references was
    never looked for.
    """
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        c.execute(
            f"CREATE TABLE {SCHEMA}.accounts (id bigserial PRIMARY KEY, account_id text, "
            "account_name text, primary_champion text)"
        )
        c.execute(
            f"INSERT INTO {SCHEMA}.accounts (account_id, account_name, primary_champion) "
            "SELECT 'A'||g, 'name '||g, 'champ '||g FROM generate_series(1,500) g"
        )
        c.execute(f"CREATE TABLE {SCHEMA}.contracts (id bigserial PRIMARY KEY, account_id text)")
        c.execute(
            f"INSERT INTO {SCHEMA}.contracts (account_id) "
            "SELECT 'A'||((g%500)+1) FROM generate_series(1,2000) g"
        )
    yield
    with psycopg.connect(_DSN, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture()
def keyed_parts(surrogate_pk_schema):
    schema = PostgresConnector(_DSN, schema_name=SCHEMA).get_schema()
    probe = PostgresValueSampler(_DSN, schema_name=SCHEMA, limit=10_000)
    yield schema, probe
    probe.close()


def test_a_natural_key_beside_a_surrogate_one_is_found(keyed_parts):
    schema, probe = keyed_parts
    profile = profile_primary_keys(schema, probe)
    assert "accounts" in profile.declared, "the surrogate PK is still the declared key"
    found = {c.columns for c in profile.additional.get("accounts", [])}
    assert ("account_id",) in found, (
        "account_id is unique, non-null and named after its table; without it there is "
        "no target for the references children actually carry"
    )


def test_uniqueness_alone_is_not_enough(keyed_parts):
    """`account_name` and `primary_champion` are distinct in every row here and are
    not keys. Proposing them would be worse than proposing nothing, because an overlay
    reads as a curator's assertion."""
    schema, probe = keyed_parts
    profile = profile_primary_keys(schema, probe)
    proposed = {c.columns[0] for c in profile.additional.get("accounts", [])}
    assert "account_name" not in proposed
    assert "primary_champion" not in proposed


def test_the_declared_key_is_never_re_proposed(keyed_parts):
    schema, probe = keyed_parts
    profile = profile_primary_keys(schema, probe)
    for cands in profile.additional.values():
        assert all(c.columns != ("id",) for c in cands), (
            "`id` is the declared primary key; re-proposing it says nothing"
        )


def test_additional_keys_are_unique_constraints_not_primary_keys(keyed_parts):
    schema, probe = keyed_parts
    overlay = draft_key_overlay(schema, probe, sampler=probe).overlay
    accounts = overlay["tables"]["accounts"]
    assert accounts.get("uniqueConstraints") == [["account_id"]]
    assert "primaryKey" not in accounts, (
        "the table already declares one; proposing another would overwrite a fact the "
        "source asserted with one this engine guessed"
    )


def test_the_generated_draft_unlocks_the_references(keyed_parts):
    """End to end: from a schema where inference finds nothing, to the references."""
    schema, probe = keyed_parts
    assert infer_foreign_keys(schema, options=InferenceOptions()) == []
    overlay = draft_key_overlay(schema, probe, sampler=probe).overlay
    drafted = {
        (t, fk["columns"][0], fk["references"]["table"], fk["references"]["columns"][0])
        for t, body in overlay["tables"].items()
        for fk in (body.get("foreignKeys") or [])
    }
    assert ("contracts", "account_id", "accounts", "account_id") in drafted


def test_the_second_pass_can_be_turned_off(keyed_parts):
    schema, probe = keyed_parts
    profile = profile_primary_keys(schema, probe, find_additional_keys=False)
    assert profile.additional == {}
    assert "accounts" in profile.declared


# ── Cost governance (opt-in) ─────────────────────────────────────────────────


def test_the_server_cancels_a_query_past_the_timeout_and_the_limit_survives(keyless_schema):
    """``statement_timeout_s`` is enforced by Postgres, and keeps being enforced.

    The second half is the subtle one: a session ``SET`` is undone when its
    transaction rolls back, and the first cancelled probe *does* roll back. Unless
    the setting was committed, the timeout protects exactly one query.
    """
    probe = PostgresValueSampler(_DSN, schema_name=SCHEMA, statement_timeout_s=1, query_tag="rsa-it")
    try:
        assert probe._execute("SELECT pg_sleep(3)", (), what="it") is None  # cancelled at 1s
        assert probe._execute("SELECT current_setting('statement_timeout')", (), what="it") == ("1s",)
        assert probe._execute("SELECT current_setting('application_name')", (), what="it") == ("rsa-it",)
        assert probe._execute("SELECT pg_sleep(3)", (), what="it") is None  # still enforced
        # And ordinary probes still work after two cancellations.
        assert probe.column_stats("accounts", ["account_id"], sample=False) == {"account_id": (500, 500, 500)}
    finally:
        probe.close()


def test_a_spent_budget_leaves_tables_undecided(keyless_schema):
    schema = PostgresConnector(_DSN, schema_name=SCHEMA).get_schema()
    probe = PostgresValueSampler(_DSN, schema_name=SCHEMA, max_queries=1)
    try:
        profile = profile_primary_keys(schema, probe)
        assert probe.stats["queries_run"] == 1 and probe.stats["budget_exhausted"] is True
        assert profile.not_evaluated, "tables past the budget are undecided, not keyless"
    finally:
        probe.close()


def test_a_wrong_password_is_tried_once(keyless_schema):
    psycopg = pytest.importorskip("psycopg")
    info = psycopg.conninfo.conninfo_to_dict(_DSN)
    info["password"] = "definitely-wrong"
    bad = PostgresValueSampler(psycopg.conninfo.make_conninfo(**info), schema_name=SCHEMA, max_queries=50)
    try:
        schema = PostgresConnector(_DSN, schema_name=SCHEMA).get_schema()
        profile_primary_keys(schema, bad)
        assert bad.stats["connect_failed"] is True
        assert bad.stats["queries_run"] == 0
    finally:
        bad.close()
