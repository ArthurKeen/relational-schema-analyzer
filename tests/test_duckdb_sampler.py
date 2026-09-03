"""DuckDB value sampler — the sampler SQL, executed against a real database.

Every other sampler in this project is covered only by mock cursors primed to return
a canned number. That verifies the plumbing and not one character of the SQL, and it
is exactly why two of the CSV denormalization probes shipped a ``TypeError`` that
fired on their first contact with real rows.

DuckDB is the project's always-on engine (``docs/IMPLEMENTATION-PLAN.md`` testing
matrix): embedded, server-less, Postgres-shaped. So these tests need no Docker, no
DSN and no cloud account, and they run in ordinary CI — which is the whole point,
because a test that only runs when someone remembers to start a container is a test
that does not run.
"""

from __future__ import annotations

import pytest

from relational_schema_analyzer import create_connector, create_value_sampler
from relational_schema_analyzer.fk_inference import (
    DuckDbValueSampler,
    InferenceOptions,
    infer_foreign_keys,
)

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def db(tmp_path) -> str:
    """A small schema carrying each denormalization pattern by construction.

    ``zip`` determines ``city`` and ``state`` (an embedded lookup), ``plan_code`` is
    low-cardinality, ``tags`` is comma-delimited in 3 of 6 rows, and ``region_code``
    references nothing that exists — the case a value probe must veto.
    """
    path = str(tmp_path / "denorm.duckdb")
    conn = duckdb.connect(path)
    conn.execute("CREATE TABLE plans (code TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO plans VALUES ('gold'),('silver'),('bronze'),('platinum')")
    conn.execute("CREATE TABLE regions (code TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO regions VALUES ('EU'),('US')")
    conn.execute(
        """CREATE TABLE customers (
               id INTEGER PRIMARY KEY, name TEXT, zip TEXT, city TEXT, state TEXT,
               tags TEXT, plan_code TEXT, region_code TEXT)"""
    )
    conn.execute(
        """INSERT INTO customers VALUES
           (1,'Ann','10001','New York','NY','vip,beta','gold','ZZ'),
           (2,'Bob','10001','New York','NY','beta','gold','ZZ'),
           (3,'Cal','94107','San Francisco','CA','vip','silver','YY'),
           (4,'Dee','94107','San Francisco','CA','beta,trial','silver','YY'),
           (5,'Eve','60601','Chicago','IL','vip,beta,trial','gold','ZZ'),
           (6,'Fay','60601','Chicago','IL','trial','bronze','YY')"""
    )
    conn.close()
    return path


@pytest.fixture
def sampler(db) -> DuckDbValueSampler:
    s = DuckDbValueSampler(db)
    yield s
    s.close()


class TestValueOverlap:
    def test_full_containment_scores_one(self, sampler):
        assert sampler("customers", "plan_code", "plans", "code") == 1.0

    def test_no_containment_scores_zero(self, sampler):
        """`region_code` holds ZZ/YY; `regions` holds EU/US."""
        assert sampler("customers", "region_code", "regions", "code") == 0.0

    def test_partial_containment_is_proportional(self, sampler):
        """Distinct plan codes present in `regions.code`: none of 3."""
        assert sampler("customers", "plan_code", "regions", "code") == 0.0

    def test_unknown_table_degrades_to_none(self, sampler):
        assert sampler("customers", "plan_code", "nosuch", "code") is None


class TestDenormalizationProbes:
    def test_functional_dependency_detected(self, sampler):
        assert sampler.group_single_valued("customers", ["zip"], "city") == 1.0
        assert sampler.group_single_valued("customers", ["zip"], "state") == 1.0

    def test_non_dependency_scores_zero(self, sampler):
        assert sampler.group_single_valued("customers", ["zip"], "name") == 0.0

    def test_distinct_ratio(self, sampler):
        assert sampler.distinct_ratio("customers", "plan_code") == 0.5  # 3 of 6
        assert sampler.distinct_ratio("customers", "id") == 1.0

    def test_delimiter_rate(self, sampler):
        assert sampler.delimiter_rate("customers", "tags", ",") == 0.5  # 3 of 6
        assert sampler.delimiter_rate("customers", "city", ",") == 0.0

    def test_probes_degrade_to_none_on_a_missing_table(self, sampler):
        assert sampler.distinct_ratio("nosuch", "plan_code") is None
        assert sampler.delimiter_rate("nosuch", "tags", ",") is None
        assert sampler.group_single_valued("nosuch", ["zip"], "city") is None


class TestFactoryDispatch:
    def test_duckdb_source_type_now_yields_a_sampler(self, db):
        """Before this sampler existed the factory returned None for duckdb, so
        inference on a DuckDB source could never consult values at all."""
        s = create_value_sampler("duckdb", db)
        assert isinstance(s, DuckDbValueSampler)
        s.close()


class TestEndToEnd:
    """Connector → inference → sampler, every layer real, no mocks anywhere."""

    def test_value_overlap_vetoes_a_name_match_that_the_data_refutes(self, db):
        schema = create_connector("duckdb", db).get_schema()
        # Both name patterns look equally good: `plan_code` -> `plans.code` and
        # `region_code` -> `regions.code`. Only the data tells them apart.
        names_only = {
            (c.table, c.columns[0], c.foreign_table)
            for c in infer_foreign_keys(schema)
        }
        assert ("customers", "plan_code", "plans") in names_only
        assert ("customers", "region_code", "regions") in names_only

        with DuckDbValueSampler(db) as s:
            sampled = infer_foreign_keys(
                schema,
                options=InferenceOptions(sample_overlap=True),
                sampler=s,
            )
        with_values = {
            (c.table, c.columns[0], c.foreign_table) for c in sampled
        }
        # Real containment survives and is boosted; the empty one is vetoed.
        assert ("customers", "plan_code", "plans") in with_values
        assert ("customers", "region_code", "regions") not in with_values

    def test_overlap_boosts_confidence_above_the_name_only_score(self, db):
        schema = create_connector("duckdb", db).get_schema()

        def conf(candidates):
            return next(
                c.confidence for c in candidates
                if c.table == "customers" and c.columns == ["plan_code"]
            )

        baseline = conf(infer_foreign_keys(schema))
        with DuckDbValueSampler(db) as s:
            boosted = conf(
                infer_foreign_keys(
                    schema,
                    options=InferenceOptions(sample_overlap=True),
                    sampler=s,
                )
            )
        assert boosted > baseline
