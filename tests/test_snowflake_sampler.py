"""SnowflakeValueSampler -- cost governor and overlap correctness, via fakesnow.

fakesnow (DuckDB-backed, in-process) lets these run in CI without an account.
What it cannot show -- that STATEMENT_TIMEOUT and QUERY_TAG really apply, and
real warehouse cost -- is covered by the opt-in live test.
"""

from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
pytest.importorskip("snowflake.connector")

from relational_schema_analyzer.fk_inference import (  # noqa: E402
    SnowflakeValueSampler,
    create_value_sampler,
)

# PARENT has 300 keys (more than the sampler's minimum bound of 100), so a
# LIMIT-ed slice of it would miss most of them. CHILD references 50 of the
# high ones plus 10 that do not exist; SMALL_PARENT fits the bound.
_DDL = [
    "CREATE TABLE PARENT (ID INT PRIMARY KEY, NAME VARCHAR)",
    "INSERT INTO PARENT SELECT seq4() + 1, 'p' || (seq4() + 1) FROM TABLE(GENERATOR(ROWCOUNT => 300))",
    "CREATE TABLE CHILD (ID INT, PARENT_ID INT, TAGS VARCHAR, REGION VARCHAR, CODE VARCHAR)",
    "INSERT INTO CHILD SELECT seq4() + 1, 251 + MOD(seq4(), 50), 'a,b', 'R' || MOD(seq4(), 3), 'C' || MOD(seq4(), 3) "
    "FROM TABLE(GENERATOR(ROWCOUNT => 200))",
    "INSERT INTO CHILD SELECT 1000 + seq4(), 9000 + seq4(), 'x', 'R0', 'C0' FROM TABLE(GENERATOR(ROWCOUNT => 10))",
    "CREATE TABLE SMALL_PARENT (ID INT PRIMARY KEY)",
    "INSERT INTO SMALL_PARENT VALUES (251), (252), (253), (254)",
    "CREATE TABLE EMPTY_T (V INT)",
]


@pytest.fixture
def conn():
    import snowflake.connector as sc

    with fakesnow.patch():
        c = sc.connect(database="DB1", schema="PUBLIC")
        cur = c.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        cur.close()
        yield c
        c.close()


def _sampler(conn, **kw) -> SnowflakeValueSampler:
    return SnowflakeValueSampler(connection=conn, schema_name="PUBLIC", limit=100, **kw)


def test_overlap_is_measured_against_the_whole_foreign_column(conn):
    """60 distinct child values: 50 exist among PARENT's 300 keys, 10 do not."""
    s = _sampler(conn)
    assert s("CHILD", "PARENT_ID", "PARENT", "ID") == pytest.approx(50 / 60)


def test_small_foreign_column_is_compared_client_side(conn):
    s = _sampler(conn)
    # 4 of CHILD's 60 distinct PARENT_ID values exist in SMALL_PARENT.
    assert s("CHILD", "PARENT_ID", "SMALL_PARENT", "ID") == pytest.approx(4 / 60)
    assert s.stats["queries_run"] == 2  # one distinct fetch per column, no server join


def test_each_column_is_fetched_once(conn):
    s = _sampler(conn)
    s("CHILD", "PARENT_ID", "SMALL_PARENT", "ID")
    before = s.stats["queries_run"]
    s("CHILD", "PARENT_ID", "SMALL_PARENT", "ID")
    assert s.stats["queries_run"] == before
    assert s.stats["cache_hits"] == 2


def test_budget_exhaustion_returns_none_and_stops_querying(conn):
    s = _sampler(conn, max_queries=1)
    assert s("CHILD", "PARENT_ID", "SMALL_PARENT", "ID") is None
    assert s.stats == {
        "queries_run": 1,
        "max_queries": 1,
        "cache_hits": 0,
        "budget_exhausted": True,
    }
    assert s.distinct_ratio("PARENT", "ID") is None
    assert s.stats["queries_run"] == 1


def test_empty_local_column_is_not_evaluated(conn):
    assert _sampler(conn)("EMPTY_T", "V", "PARENT", "ID") is None


def test_a_failing_probe_returns_none(conn):
    assert _sampler(conn)("NO_SUCH_TABLE", "X", "PARENT", "ID") is None


def test_denormalization_probes(conn):
    s = _sampler(conn)
    assert s.distinct_ratio("PARENT", "ID") == pytest.approx(1.0)
    assert s.delimiter_rate("CHILD", "TAGS", ",") == pytest.approx(1.0, abs=0.06)
    # REGION and CODE move together ('R'||n with 'C'||n): each REGION has one CODE.
    assert s.group_single_valued("CHILD", ["REGION"], "CODE") == pytest.approx(1.0)


def test_a_supplied_connection_is_not_closed(conn):
    s = _sampler(conn)
    s.close()
    cur = conn.cursor()
    cur.execute("SELECT 1")
    assert cur.fetchone()[0] == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "connection_string or a connection"),
        ({"connection": object(), "max_queries": 0}, "no unbounded mode"),
        ({"connection": object(), "statement_timeout_s": 0}, "at least 1 second"),
    ],
)
def test_construction_refuses_unbounded_or_unusable_settings(kwargs, match):
    with pytest.raises(ValueError, match=match):
        SnowflakeValueSampler(**kwargs)


def test_factory_returns_a_governed_sampler():
    s = create_value_sampler("snowflake", "snowflake://u:p@acct/DB1/CORE", pg_schema="public")
    assert isinstance(s, SnowflakeValueSampler)
    assert s.schema_name == "CORE"  # URL schema wins over the "public" default
    assert s.max_queries >= 1 and s.statement_timeout_s >= 1


def test_values_are_normalised_the_way_to_varchar_renders_them():
    from decimal import Decimal

    norm = SnowflakeValueSampler._norm
    assert norm(42) == "42"
    assert norm(Decimal("42")) == "42"
    # A scaled NUMBER keeps its scale under TO_VARCHAR; collapsing it to "42"
    # would make the server-side membership test miss every such key.
    assert norm(Decimal("42.00")) == "42.00"
    # Integral floats are the one case Python and Snowflake render differently.
    assert norm(42.0) == "42"
    assert norm(1.5) == "1.5"
    assert norm("ABC") == "ABC"
