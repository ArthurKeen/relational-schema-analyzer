"""Databricks connector — URL parsing + introspection assembly via a fake driver.

No in-process emulator exists for Databricks (the driver needs a live SQL
warehouse), so this validates the assembly against a scripted fake cursor at the
full conformance capability set. A real workspace is opt-in via RSA_DATABRICKS_DSN.
"""

from __future__ import annotations

import re
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
        # Real Unity Catalog table_type values. UC never emits ANSI's
        # "BASE TABLE", so a fixture using it would exercise a code path
        # production can't reach.
        return [
            ("users", "MANAGED", "people"),
            ("orders", "EXTERNAL", None),
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


class TestUnityCatalogVocabulary:
    """Pins the connector to the vocabulary Unity Catalog actually emits.

    A fake driver can assert any behaviour you script it for, so these check
    against the *documented* UC surface rather than against our own fixture:
    the ``table_type`` enum (which has no ANSI ``BASE TABLE``) and
    ``full_data_type`` vs ``data_type``.
    """

    DSN = "databricks://:tok@host/sql/1.0/warehouses/x?catalog=main&schema=default"

    @staticmethod
    def _install(monkeypatch, resolve, *, unsupported: str = "", captured=None):
        """Install a fake driver; ``unsupported`` makes matching SQL raise."""

        class _Cur:
            def execute(self, sql, params=None):
                if captured is not None:
                    captured.append(" ".join(sql.split()))
                if unsupported and unsupported in sql:
                    raise RuntimeError(f"UNRESOLVED_COLUMN: {unsupported}")
                self._rows = resolve(sql)

            def fetchall(self):
                return list(getattr(self, "_rows", []))

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        sql_mod = types.ModuleType("databricks.sql")
        sql_mod.connect = lambda **k: _Conn()  # type: ignore[attr-defined]
        dbx = types.ModuleType("databricks")
        dbx.sql = sql_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "databricks", dbx)
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_mod)

    @staticmethod
    def _single_table(table_type: str, *, col_type: str = "int"):
        """Fake one table, answering the *requested* type column faithfully.

        UC returns the simple name from ``data_type`` and the declared type from
        ``full_data_type``. A fake that ignores which was asked for would pass
        whichever column the connector selects, hiding exactly the bug these
        tests exist to catch.
        """
        simple = re.split(r"[(<]", col_type)[0]

        def resolve(sql: str):
            s = " ".join(sql.split())
            if "information_schema.columns" in s:
                declared = col_type if "full_data_type" in s else simple
                return [("t", "id", declared, "NO", None, 1, None)]
            if "information_schema.tables" in s:
                return [("t", table_type, None)]
            return []

        return resolve

    # Every value documented for information_schema.tables.table_type. Only the
    # two query-defined kinds are views; FOREIGN (federated) and the shallow
    # clones are ordinary tables.
    @pytest.mark.parametrize(
        ("table_type", "is_view"),
        [
            ("VIEW", True),
            ("MATERIALIZED_VIEW", True),
            ("MANAGED", False),
            ("EXTERNAL", False),
            ("FOREIGN", False),
            ("STREAMING_TABLE", False),
            ("MANAGED_SHALLOW_CLONE", False),
            ("EXTERNAL_SHALLOW_CLONE", False),
        ],
    )
    def test_table_type_classification(self, monkeypatch, table_type, is_view):
        self._install(monkeypatch, self._single_table(table_type))
        schema = DatabricksConnector(self.DSN).get_schema()
        assert schema.tables["t"].is_view is is_view

    def test_columns_query_asks_for_full_data_type(self, monkeypatch):
        captured: list[str] = []
        self._install(monkeypatch, self._single_table("MANAGED"), captured=captured)
        DatabricksConnector(self.DSN).get_schema()
        columns_sql = [s for s in captured if "information_schema.columns" in s]
        assert columns_sql and "full_data_type" in columns_sql[0]

    def test_precision_and_element_types_survive(self, monkeypatch):
        # data_type would report bare "decimal" / "array", losing the parameters
        # that make the type meaningful downstream (sqlType, OWL, R2RML).
        for declared, expected in (("decimal(10,2)", "decimal(10,2)"),
                                   ("array<string>", "array<string>")):
            self._install(monkeypatch, self._single_table("MANAGED", col_type=declared))
            col = DatabricksConnector(self.DSN).get_schema().tables["t"].columns[0]
            assert col.data_type == expected

    def test_falls_back_when_full_data_type_is_unavailable(self, monkeypatch):
        # An older catalog without the column must still introspect, not crash.
        captured: list[str] = []
        self._install(
            monkeypatch,
            self._single_table("MANAGED", col_type="decimal"),
            unsupported="full_data_type",
            captured=captured,
        )
        schema = DatabricksConnector(self.DSN).get_schema()
        assert schema.tables["t"].columns[0].data_type == "decimal"
        assert any("full_data_type" in s for s in captured), "should try full form first"
        assert any(
            "information_schema.columns" in s and "full_data_type" not in s
            for s in captured
        ), "should retry with data_type"


class TestUnenforcedConstraints:
    """Unity Catalog never enforces PK/FK — the FK is intent, not proof."""

    def test_foreign_keys_are_marked_unenforced(self, databricks_shop):
        orders = conf._find_table(databricks_shop, "orders")
        assert orders.foreign_keys[0].enforced is False

    def test_unenforced_flag_survives_snapshot_round_trip(self, databricks_shop):
        from relational_schema_analyzer.types import PhysicalSchema

        restored = PhysicalSchema.model_validate_json(databricks_shop.model_dump_json())
        assert conf._find_table(restored, "orders").foreign_keys[0].enforced is False

    def test_baseline_flags_the_schema_as_unenforced(self, databricks_shop):
        from relational_schema_analyzer.baseline import infer_baseline

        result = infer_baseline(databricks_shop)
        assert "unenforced_foreign_keys" in result["detectedPatterns"]
