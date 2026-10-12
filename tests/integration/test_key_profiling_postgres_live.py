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
