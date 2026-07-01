"""DuckDB connector conformance (embedded, always-on CI).

DuckDB is server-less and fully featured, so it exercises the *entire* conformance
capability set — views, comments, defaults, ordinals, unique constraints, and
foreign keys — against a real engine with no Docker. Its FK-resolution via
``information_schema`` is the same pattern the other RDBMS connectors use.
"""

from __future__ import annotations

import sys

import pytest

from tests import _conformance as conf

duckdb = pytest.importorskip("duckdb")

from relational_schema_analyzer import create_connector  # noqa: E402
from relational_schema_analyzer.connectors.duckdb_source import DuckDbConnector  # noqa: E402

_DDL = [
    "CREATE TABLE users ("
    " id INTEGER PRIMARY KEY,"
    " email VARCHAR NOT NULL UNIQUE,"
    " status VARCHAR DEFAULT 'active',"
    " created_at TIMESTAMP)",
    "CREATE TABLE orders ("
    " id INTEGER PRIMARY KEY,"
    " user_id INTEGER NOT NULL REFERENCES users(id),"
    " total DECIMAL(10,2))",
    "CREATE VIEW active_users AS SELECT id FROM users",
    "COMMENT ON TABLE users IS 'people'",
    "COMMENT ON COLUMN users.email IS 'contact email'",
]

_FULL_CAPABILITIES = {
    conf.ORDINAL,
    conf.DEFAULTS,
    conf.COMMENTS,
    conf.UNIQUE,
    conf.FOREIGN_KEYS,
    conf.VIEWS,
    conf.PROVENANCE_VERSION,
}


@pytest.fixture
def duckdb_shop(tmp_path):
    path = str(tmp_path / "shop.duckdb")
    con = duckdb.connect(path)
    for stmt in _DDL:
        con.execute(stmt)
    con.close()
    return create_connector("duckdb", path).get_schema()


def test_duckdb_full_conformance(duckdb_shop):
    conf.assert_shop_conformance(
        duckdb_shop, dialect="duckdb", capabilities=_FULL_CAPABILITIES
    )


def test_provenance(duckdb_shop):
    assert duckdb_shop.source.dialect == "duckdb"
    assert duckdb_shop.source.server_version
    assert duckdb_shop.source.namespace == "main"


def test_view_flagged(duckdb_shop):
    assert conf._find_table(duckdb_shop, "active_users").is_view is True


def test_unique_and_fk_cardinality(duckdb_shop):
    users = conf._find_table(duckdb_shop, "users")
    assert conf._find_col(users, "email").is_unique is True
    orders = conf._find_table(duckdb_shop, "orders")
    assert orders.foreign_keys[0].is_unique is False  # many:1


def test_missing_driver_surfaces_install_hint(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    conn = DuckDbConnector(str(tmp_path / "x.duckdb"))
    with pytest.raises(ImportError, match="relational-schema-analyzer\\[duckdb\\]"):
        conn.get_schema()


def test_public_schema_folds_to_main(tmp_path):
    conn = DuckDbConnector(str(tmp_path / "x.duckdb"), schema_name="public")
    assert conn.schema_name == "main"
