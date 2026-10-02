"""SnowflakeValueSampler against a live warehouse (opt-in; spends credits).

Targets r2g's constraint-free Customer 360 rehearsal schema, whose reviewed key
overlay is ground truth: six foreign keys, none declared in Snowflake. Asserts
that the real ones score as overlaps and plausible wrong pairings do not, and
that the cost governor is real -- the budget caps what Snowflake actually ran,
the statement timeout and query tag are applied to the session.

Measured on 2026-10-02 (X-Small): 9 pairs in 8 queries (10 cache hits), ~13 KB
scanned, well under a second of query time.

Run:
    RUN_INTEGRATION=1 \\
    RSA_SNOWFLAKE_DSN='snowflake://USER:@ACCOUNT/CDF_FORGE/R2G_CUSTOMER_360?warehouse=...&role=...&private_key_file=...' \\
    pytest tests/integration/test_snowflake_sampler_live.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from relational_schema_analyzer.fk_inference import SnowflakeValueSampler

_DSN = os.environ.get("RSA_SNOWFLAKE_DSN")
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1", reason="RUN_INTEGRATION=1 not set"
    ),
    pytest.mark.skipif(not _DSN, reason="RSA_SNOWFLAKE_DSN not set"),
]

REAL = [
    ("CONTACTS", "ACCOUNT_ID", "ACCOUNTS", "ACCOUNT_ID"),
    ("EMAIL_EVENTS", "CONTACT_ID", "CONTACTS", "CONTACT_ID"),
    ("EMAIL_EVENTS", "ACCOUNT_ID", "ACCOUNTS", "ACCOUNT_ID"),
    ("ZOOM_TELEMETRY", "CONTACT_ID", "CONTACTS", "CONTACT_ID"),
    ("ZOOM_TELEMETRY", "ACCOUNT_ID", "ACCOUNTS", "ACCOUNT_ID"),
    ("HEALTH_SIGNALS", "ACCOUNT_ID", "ACCOUNTS", "ACCOUNT_ID"),
]
DECOYS = [
    ("CONTACTS", "CONTACT_ID", "ACCOUNTS", "ACCOUNT_ID"),
    ("EMAIL_EVENTS", "CONTACT_ID", "ACCOUNTS", "ACCOUNT_ID"),
    ("HEALTH_SIGNALS", "ACCOUNT_ID", "CONTACTS", "CONTACT_ID"),
]


def _history(sampler: SnowflakeValueSampler, tag: str) -> int:
    cur = sampler._conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION()) "
            "WHERE QUERY_TAG = %s AND EXECUTION_STATUS = 'SUCCESS' AND QUERY_TYPE = 'SELECT'",
            (tag,),
        )
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def test_reviewed_foreign_keys_overlap_and_decoys_do_not():
    tag = f"rsa-live-{uuid.uuid4().hex[:8]}"
    with SnowflakeValueSampler(_DSN, max_queries=40, statement_timeout_s=30, query_tag=tag) as s:
        for pair in REAL:
            assert s(*pair) == pytest.approx(1.0, abs=0.01), pair
        for pair in DECOYS:
            score = s(*pair)
            assert score is not None and score < 0.1, (pair, score)
        # O(columns), not O(pairs): 9 pairs, far fewer queries.
        assert s.stats["queries_run"] < len(REAL) + len(DECOYS)
        assert s.stats["cache_hits"] > 0
        # The tag reached Snowflake and every probe carried it.
        assert _history(s, tag) == s.stats["queries_run"]


def test_budget_caps_what_snowflake_actually_runs():
    tag = f"rsa-live-{uuid.uuid4().hex[:8]}"
    with SnowflakeValueSampler(_DSN, max_queries=3, statement_timeout_s=30, query_tag=tag) as s:
        results = [s(*pair) for pair in REAL[:4]]
        assert results[0] == pytest.approx(1.0, abs=0.01)
        assert results[1:] == [None, None, None]
        assert s.stats["budget_exhausted"] is True
        assert _history(s, tag) == 3

        cur = s._conn.cursor()
        try:
            cur.execute("SHOW PARAMETERS LIKE 'STATEMENT_TIMEOUT_IN_SECONDS' IN SESSION")
            assert int(cur.fetchone()[1]) == 30
        finally:
            cur.close()
