"""Sampler SQL executed against a live Postgres (opt-in).

Closes the gap the DuckDB tests could only narrow. `DuckDbValueSampler` proves the
*shape* of these queries works, but each dialect writes its own SQL — DuckDB uses
`?` and `strpos`, Postgres binds `%s` and casts with `::float`, MySQL and SQL Server
differ again — so a passing DuckDB test says nothing about whether the Postgres
string is valid.

Until this file existed, the Postgres, MySQL, SQL Server and Databricks probes were
covered only by mock cursors primed with a canned number. That verifies the plumbing
and not one character of the SQL, which is exactly how two CSV probes shipped a
TypeError that fired on first contact with data.

Run with::

    RUN_INTEGRATION=1 RSA_PG_DSN=postgresql://user:pw@localhost:5432/db \\
        pytest tests/integration/test_sampler_probes.py

The fixture builds its own schema (`rsa_probe_it`) and drops it afterwards, so it
never touches anything else in the database.
"""

from __future__ import annotations

import os

import pytest

from relational_schema_analyzer.fk_inference import PostgresValueSampler

_RUN = os.environ.get("RUN_INTEGRATION") == "1"
_DSN = os.environ.get("RSA_PG_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _RUN, reason="integration tests are opt-in (set RUN_INTEGRATION=1)"),
    pytest.mark.skipif(not _DSN, reason="RSA_PG_DSN not set"),
]

SCHEMA = "rsa_probe_it"

# Each denormalization pattern present by construction: `zip` determines
# `city`/`state`, `plan_code` is low-cardinality, `tags` is comma-delimited in 3 of
# 6 rows, and `region_code` references nothing that exists.
_DDL = [
    f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.plans (code TEXT PRIMARY KEY)",
    f"INSERT INTO {SCHEMA}.plans VALUES ('gold'),('silver'),('bronze'),('platinum')",
    f"CREATE TABLE {SCHEMA}.regions (code TEXT PRIMARY KEY)",
    f"INSERT INTO {SCHEMA}.regions VALUES ('EU'),('US')",
    f"""CREATE TABLE {SCHEMA}.customers (
            id INT PRIMARY KEY, name TEXT, zip TEXT, city TEXT, state TEXT,
            tags TEXT, plan_code TEXT, region_code TEXT)""",
    f"""INSERT INTO {SCHEMA}.customers VALUES
        (1,'Ann','10001','New York','NY','vip,beta','gold','ZZ'),
        (2,'Bob','10001','New York','NY','beta','gold','ZZ'),
        (3,'Cal','94107','San Francisco','CA','vip','silver','YY'),
        (4,'Dee','94107','San Francisco','CA','beta,trial','silver','YY'),
        (5,'Eve','60601','Chicago','IL','vip,beta,trial','gold','ZZ'),
        (6,'Fay','60601','Chicago','IL','trial','bronze','YY')""",
]


@pytest.fixture(scope="module")
def pg_sampler():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN, autocommit=True) as conn:
        for stmt in _DDL:
            conn.execute(stmt)
    sampler = PostgresValueSampler(_DSN, schema_name=SCHEMA)
    yield sampler
    sampler.close()
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


class TestPostgresValueOverlapLive:
    def test_full_containment(self, pg_sampler):
        assert pg_sampler("customers", "plan_code", "plans", "code") == 1.0

    def test_empty_intersection(self, pg_sampler):
        assert pg_sampler("customers", "region_code", "regions", "code") == 0.0

    def test_missing_table_degrades_to_none(self, pg_sampler):
        """A failed probe must return None, not raise and not guess."""
        assert pg_sampler("customers", "plan_code", "nosuchtable", "code") is None


class TestPostgresDenormalizationProbesLive:
    def test_functional_dependency(self, pg_sampler):
        assert pg_sampler.group_single_valued("customers", ["zip"], "city") == 1.0
        assert pg_sampler.group_single_valued("customers", ["zip"], "state") == 1.0

    def test_non_dependency(self, pg_sampler):
        assert pg_sampler.group_single_valued("customers", ["zip"], "name") == 0.0

    def test_distinct_ratio(self, pg_sampler):
        assert pg_sampler.distinct_ratio("customers", "plan_code") == 0.5
        assert pg_sampler.distinct_ratio("customers", "id") == 1.0

    def test_delimiter_rate(self, pg_sampler):
        assert pg_sampler.delimiter_rate("customers", "tags", ",") == 0.5
        assert pg_sampler.delimiter_rate("customers", "city", ",") == 0.0

    def test_probes_degrade_to_none_on_missing_table(self, pg_sampler):
        assert pg_sampler.distinct_ratio("nosuchtable", "plan_code") is None
        assert pg_sampler.delimiter_rate("nosuchtable", "tags", ",") is None
        assert pg_sampler.group_single_valued("nosuchtable", ["zip"], "city") is None
