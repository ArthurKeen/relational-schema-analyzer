"""Databricks connector — URL parsing + introspection assembly via a fake driver.

No in-process emulator exists for Databricks (the driver needs a live SQL
warehouse), so this validates the assembly against a scripted fake cursor at the
full conformance capability set. A real workspace is opt-in via RSA_DATABRICKS_DSN.
"""

from __future__ import annotations

import sys
import types

import pytest

from relational_schema_analyzer.connectors.databricks_source import (
    DatabricksConnector,
    _parse_databricks_url,
)
from tests import _conformance as conf


class TestParseUrl:
    def test_full_url(self):
        kw = _parse_databricks_url(
            "databricks://:dapiTOKEN@dbc-x.cloud.databricks.com"
            "/sql/1.0/warehouses/abc123?catalog=main&schema=sales"
        )
        assert kw["server_hostname"] == "dbc-x.cloud.databricks.com"
        assert kw["http_path"] == "/sql/1.0/warehouses/abc123"
        assert kw["access_token"] == "dapiTOKEN"
        assert kw["catalog"] == "main"
        assert kw["schema"] == "sales"

    def test_defaults_catalog_and_schema(self):
        kw = _parse_databricks_url("databricks://:tok@host/sql/1.0/warehouses/x")
        assert kw["catalog"] == "main"
        assert kw["schema"] == "default"

    def test_missing_token_rejected(self):
        with pytest.raises(ValueError, match="access token"):
            _parse_databricks_url("databricks://host/sql/1.0/warehouses/x")

    def test_non_databricks_scheme_rejected(self):
        with pytest.raises(ValueError, match="databricks://"):
            _parse_databricks_url("postgresql://u:p@h/db")


# ── Fake databricks.sql driver ───────────────────────────────────────────

def _resolve(sql: str):
    s = " ".join(sql.split())
    if "current_version()" in s:
        return [("16.1",)]
    if "information_schema.referential_constraints" in s:
        return [("orders_user_fk", "users_pk")]
    if "information_schema.key_column_usage" in s:
        return [
            ("users_pk", "id", 1),
            ("users_email_uq", "email", 1),
            ("orders_pk", "id", 1),
            ("orders_user_fk", "user_id", 1),
        ]
    if "information_schema.table_constraints" in s:
        return [
            ("users_pk", "PRIMARY KEY", "users"),
            ("users_email_uq", "UNIQUE", "users"),
            ("orders_pk", "PRIMARY KEY", "orders"),
            ("orders_user_fk", "FOREIGN KEY", "orders"),
        ]
    if "information_schema.columns" in s:
        return [
            ("users", "id", "int", "NO", None, 1, None),
            ("users", "email", "string", "NO", None, 2, "contact email"),
            ("users", "status", "string", "YES", "active", 3, None),
            ("users", "created_at", "timestamp", "YES", None, 4, None),
            ("orders", "id", "int", "NO", None, 1, None),
            ("orders", "user_id", "int", "NO", None, 2, None),
            ("orders", "total", "decimal(10,2)", "YES", None, 3, None),
            ("active_users", "id", "int", "YES", None, 1, None),
        ]
    if "information_schema.tables" in s:
        return [
            ("users", "BASE TABLE", "people"),
            ("orders", "BASE TABLE", None),
            ("active_users", "VIEW", None),
        ]
    return []


class _FakeCursor:
    def __init__(self):
        self._rows: list = []

    def execute(self, sql, params=None):
        self._rows = _resolve(sql)

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


def _install_fake_databricks(monkeypatch):
    sql_mod = types.ModuleType("databricks.sql")
    sql_mod.connect = lambda **k: _FakeConn()  # type: ignore[attr-defined]
    dbx = types.ModuleType("databricks")
    dbx.sql = sql_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks", dbx)
    monkeypatch.setitem(sys.modules, "databricks.sql", sql_mod)


@pytest.fixture
def databricks_shop(monkeypatch):
    _install_fake_databricks(monkeypatch)
    return DatabricksConnector(
        "databricks://:tok@host/sql/1.0/warehouses/x?catalog=main&schema=default"
    ).get_schema()


class TestIntrospection:
    def test_full_conformance(self, databricks_shop):
        caps = {
            conf.ORDINAL, conf.DEFAULTS, conf.COMMENTS, conf.UNIQUE,
            conf.FOREIGN_KEYS, conf.VIEWS, conf.PROVENANCE_VERSION,
        }
        conf.assert_shop_conformance(
            databricks_shop, dialect="databricks", capabilities=caps
        )

    def test_details(self, databricks_shop):
        assert databricks_shop.source.dialect == "databricks"
        assert databricks_shop.source.server_version == "16.1"
        assert databricks_shop.source.database == "main"
        users = conf._find_table(databricks_shop, "users")
        assert conf._find_col(users, "email").is_unique is True
        assert conf._find_col(users, "email").comment == "contact email"
        assert conf._find_table(databricks_shop, "active_users").is_view is True
        orders = conf._find_table(databricks_shop, "orders")
        assert orders.foreign_keys[0].foreign_table == "users"
        assert orders.foreign_keys[0].is_unique is False

    def test_missing_driver_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "databricks", None)
        monkeypatch.setitem(sys.modules, "databricks.sql", None)
        conn = DatabricksConnector("databricks://:tok@host/sql/1.0/warehouses/x")
        with pytest.raises(ImportError, match="relational-schema-analyzer\\[databricks\\]"):
            conn.get_schema()
