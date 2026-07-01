"""Live-DB conformance: run the shared harness against real engines (opt-in).

Enabled only with ``RUN_INTEGRATION=1`` + a per-dialect DSN (see conftest). In CI
these run against Docker service containers (Postgres/MySQL/SQL Server); locally
they're skipped unless you point them at a database.
"""

from __future__ import annotations

import pytest

from tests import _conformance as conf

pytestmark = pytest.mark.integration


def test_live_conformance(live_shop):
    dialect, schema, capabilities = live_shop
    conf.assert_shop_conformance(schema, dialect=dialect, capabilities=capabilities)
