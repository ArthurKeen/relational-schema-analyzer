from __future__ import annotations

import sys
import types

import pytest

from relational_schema_analyzer.connectors.mysql import MySQLConnector, _parse_mysql_url

# ── URL parsing ──────────────────────────────────────────────────────────


class TestParseMysqlUrl:
    def test_full_url_parses_every_field(self):
        kw = _parse_mysql_url("mysql://svc:hunter2@db.host:3307/shop")
        assert kw["user"] == "svc"
        assert kw["password"] == "hunter2"
        assert kw["host"] == "db.host"
        assert kw["port"] == 3307
        assert kw["database"] == "shop"
        assert kw["charset"] == "utf8mb4"

    def test_default_port_is_3306(self):
        assert _parse_mysql_url("mysql://u:p@h/db")["port"] == 3306

    def test_mariadb_scheme_accepted(self):
        kw = _parse_mysql_url("mariadb://u:p@h/db")
        assert kw["database"] == "db"

    def test_charset_query_param_honored(self):
        kw = _parse_mysql_url("mysql://u:p@h/db?charset=latin1")
        assert kw["charset"] == "latin1"

    def test_percent_encoded_password_is_decoded(self):
        kw = _parse_mysql_url("mysql://u:a%40b%2Fc@h/db")
        assert kw["password"] == "a@b/c"

    def test_wrong_scheme_rejected(self):
        with pytest.raises(ValueError, match="mysql:// or mariadb://"):
            _parse_mysql_url("postgresql://u:p@h/db")

    def test_missing_database_rejected(self):
        with pytest.raises(ValueError, match="database"):
            _parse_mysql_url("mysql://u:p@h")

    def test_missing_user_rejected(self):
        with pytest.raises(ValueError, match="user and host"):
            _parse_mysql_url("mysql://:p@h/db")

    def test_blank_rejected(self):
        with pytest.raises(ValueError):
            _parse_mysql_url("")


class TestMysqlConnectorInit:
    def test_schema_name_defaults_to_url_database(self):
        conn = MySQLConnector("mysql://u:p@h/shop")
        assert conn.schema_name == "shop"

    def test_explicit_schema_overrides_url_database(self):
        conn = MySQLConnector("mysql://u:p@h/shop", schema_name="analytics")
        assert conn.schema_name == "analytics"
        assert conn._connect_params["database"] == "analytics"

    def test_public_sentinel_falls_back_to_url_database(self):
        # `source snapshot` passes the PG default "public"; MySQL ignores it.
        conn = MySQLConnector("mysql://u:p@h/shop", schema_name="public")
        assert conn.schema_name == "shop"


# ── Fake pymysql driver ──────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, db: dict, executed: list) -> None:
        self._db = db
        self._executed = executed
        self._rows: list = []
        self.description = None

    def execute(self, sql, params=None):
        self._executed.append(" ".join(sql.upper().split()))
        s = " ".join(sql.upper().split())
        if "INFORMATION_SCHEMA.TABLES" in s:
            self._rows = [{"TABLE_NAME": t} for t in self._db["tables"]]
        elif "INFORMATION_SCHEMA.COLUMNS" in s:
            self._rows = self._db["columns"][params[1]]
        elif "CONSTRAINT_NAME = 'PRIMARY'" in s:
            self._rows = [{"COLUMN_NAME": c} for c in self._db["pks"].get(params[1], [])]
        elif "REFERENCED_TABLE_NAME IS NOT NULL" in s:
            self._rows = self._db["fks"].get(params[1], [])
        elif "COUNT(*)" in s:
            self._rows = [(self._db.get("count", 0),)]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, db: dict) -> None:
        self._db = db
        self.executed: list = []
        self.closed = False
        self.committed = False

    def cursor(self, cursorclass=None):
        return _FakeCursor(self._db, self.executed)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _install_fake_pymysql(monkeypatch, connect_fn):
    mod = types.ModuleType("pymysql")
    cursors = types.ModuleType("pymysql.cursors")
    cursors.DictCursor = type("DictCursor", (), {})
    cursors.SSDictCursor = type("SSDictCursor", (), {})
    cursors.SSCursor = type("SSCursor", (), {})
    mod.cursors = cursors
    mod.connect = connect_fn
    monkeypatch.setitem(sys.modules, "pymysql", mod)
    monkeypatch.setitem(sys.modules, "pymysql.cursors", cursors)


def _sample_db() -> dict:
    return {
        "tables": ["users", "orders"],
        "columns": {
            "users": [
                {"COLUMN_NAME": "id", "DATA_TYPE": "INT", "IS_NULLABLE": "NO"},
                {"COLUMN_NAME": "name", "DATA_TYPE": "VARCHAR", "IS_NULLABLE": "YES"},
            ],
            "orders": [
                {"COLUMN_NAME": "order_id", "DATA_TYPE": "bigint", "IS_NULLABLE": "NO"},
                {"COLUMN_NAME": "product_id", "DATA_TYPE": "bigint", "IS_NULLABLE": "NO"},
                {"COLUMN_NAME": "user_id", "DATA_TYPE": "int", "IS_NULLABLE": "YES"},
            ],
        },
        "pks": {"users": ["id"], "orders": ["order_id", "product_id"]},
        "fks": {
            "orders": [
                {
                    "COLUMN_NAME": "user_id",
                    "REFERENCED_TABLE_NAME": "users",
                    "REFERENCED_COLUMN_NAME": "id",
                    "CONSTRAINT_NAME": "fk_orders_user",
                },
            ],
        },
    }


class TestIntrospection:
    def test_get_schema_populates_tables_columns_pks_fks(self, monkeypatch):
        captured = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            return _FakeConnection(_sample_db())

        _install_fake_pymysql(monkeypatch, fake_connect)

        schema = MySQLConnector("mysql://svc:xxx@h:3306/shop").get_schema()

        assert set(schema.tables) == {"users", "orders"}
        users = schema.tables["users"]
        assert [c.name for c in users.columns] == ["id", "name"]
        assert users.primary_key == ["id"]
        assert next(c for c in users.columns if c.name == "id").is_primary_key is True
        assert next(c for c in users.columns if c.name == "name").is_nullable is True
        # DATA_TYPE is normalized to lowercase
        assert next(c for c in users.columns if c.name == "id").data_type == "int"
        # connection used the parsed params + DictCursor
        assert captured["database"] == "shop"
        assert captured["host"] == "h"

    def test_composite_pk_preserved_in_order(self, monkeypatch):
        _install_fake_pymysql(monkeypatch, lambda **kw: _FakeConnection(_sample_db()))
        schema = MySQLConnector("mysql://u:p@h/shop").get_schema()
        assert schema.tables["orders"].primary_key == ["order_id", "product_id"]

    def test_foreign_key_grouped(self, monkeypatch):
        _install_fake_pymysql(monkeypatch, lambda **kw: _FakeConnection(_sample_db()))
        schema = MySQLConnector("mysql://u:p@h/shop").get_schema()
        fks = schema.tables["orders"].foreign_keys
        assert len(fks) == 1
        assert fks[0].columns == ["user_id"]
        assert fks[0].foreign_table == "users"
        assert fks[0].foreign_columns == ["id"]


class TestSession:
    def test_consistent_snapshot_transaction_started(self, monkeypatch):
        conn_box = {}

        def fake_connect(**kwargs):
            c = _FakeConnection(_sample_db())
            conn_box["conn"] = c
            return c

        _install_fake_pymysql(monkeypatch, fake_connect)

        session = MySQLConnector("mysql://u:p@h/shop").open_session()
        _ = session.connection  # triggers connect + snapshot setup
        executed = conn_box["conn"].executed
        assert any("REPEATABLE READ" in s for s in executed)
        assert any("START TRANSACTION WITH CONSISTENT SNAPSHOT" in s for s in executed)
        session.close()
        assert conn_box["conn"].committed is True
        assert conn_box["conn"].closed is True

    def test_count_rows(self, monkeypatch):
        db = _sample_db()
        db["count"] = 42
        _install_fake_pymysql(monkeypatch, lambda **kw: _FakeConnection(db))
        session = MySQLConnector("mysql://u:p@h/shop").open_session()
        assert session.count_rows("users") == 42
        session.close()

    def test_qualified_uses_backticks(self, monkeypatch):
        _install_fake_pymysql(monkeypatch, lambda **kw: _FakeConnection(_sample_db()))
        session = MySQLConnector("mysql://u:p@h/shop").open_session()
        assert session._qualified("users") == "`shop`.`users`"


# ── Enrichment conformance (scripted fake cursor, full capability set) ────

from tests import _conformance as conf  # noqa: E402


def _mysql_resolve(sql: str, params: tuple):
    s = " ".join(sql.upper().split())
    table = params[1] if len(params) >= 2 else None
    if "VERSION()" in s:
        return [{"v": "8.0.36"}]
    if "INFORMATION_SCHEMA.TABLES" in s:
        return [
            {"TABLE_NAME": "users", "TABLE_TYPE": "BASE TABLE", "TABLE_COMMENT": "people"},
            {"TABLE_NAME": "orders", "TABLE_TYPE": "BASE TABLE", "TABLE_COMMENT": ""},
            {"TABLE_NAME": "active_users", "TABLE_TYPE": "VIEW", "TABLE_COMMENT": ""},
        ]
    if "INFORMATION_SCHEMA.COLUMNS" in s:
        return {
            "users": [
                _mcol("id", "int", "NO", None, 1, ""),
                _mcol("email", "varchar", "NO", None, 2, "contact email"),
                _mcol("status", "varchar", "YES", "active", 3, ""),
                _mcol("created_at", "datetime", "YES", None, 4, ""),
            ],
            "orders": [
                _mcol("id", "int", "NO", None, 1, ""),
                _mcol("user_id", "int", "NO", None, 2, ""),
                _mcol("total", "decimal", "YES", None, 3, ""),
            ],
            "active_users": [_mcol("id", "int", "YES", None, 1, "")],
        }.get(table, [])
    if "CONSTRAINT_NAME = 'PRIMARY'" in s:
        return [{"COLUMN_NAME": "id"}] if table in ("users", "orders") else []
    if "CONSTRAINT_TYPE = 'UNIQUE'" in s:
        if table == "users":
            return [{"CONSTRAINT_NAME": "email_uq", "COLUMN_NAME": "email",
                     "ORDINAL_POSITION": 1}]
        return []
    if "REFERENCED_TABLE_NAME IS NOT NULL" in s:
        if table == "orders":
            return [{"COLUMN_NAME": "user_id", "REFERENCED_TABLE_NAME": "users",
                     "REFERENCED_COLUMN_NAME": "id", "CONSTRAINT_NAME": "fk_orders_user"}]
        return []
    if "CHECK_CONSTRAINTS" in s:
        return []
    return []


def _mcol(name, dtype, nullable, default, ordinal, comment):
    return {
        "COLUMN_NAME": name, "DATA_TYPE": dtype, "IS_NULLABLE": nullable,
        "COLUMN_DEFAULT": default, "ORDINAL_POSITION": ordinal, "COLUMN_COMMENT": comment,
    }


class _EnrichedCursor:
    def __init__(self):
        self._rows: list = []

    def execute(self, sql, params=None):
        self._rows = _mysql_resolve(sql, params or ())

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class _EnrichedConn:
    def cursor(self, cursorclass=None):
        return _EnrichedCursor()

    def close(self):
        pass


class TestEnrichmentConformance:
    def test_full_conformance(self, monkeypatch):
        _install_fake_pymysql(monkeypatch, lambda **kw: _EnrichedConn())
        schema = MySQLConnector("mysql://u:p@h/shop").get_schema()
        caps = {
            conf.ORDINAL, conf.DEFAULTS, conf.COMMENTS, conf.UNIQUE,
            conf.FOREIGN_KEYS, conf.VIEWS, conf.PROVENANCE_VERSION,
        }
        conf.assert_shop_conformance(schema, dialect="mysql", capabilities=caps)

    def test_details(self, monkeypatch):
        _install_fake_pymysql(monkeypatch, lambda **kw: _EnrichedConn())
        schema = MySQLConnector("mysql://u:p@h/shop").get_schema()
        assert schema.source.server_version == "8.0.36"
        users = conf._find_table(schema, "users")
        assert conf._find_col(users, "status").default == "active"
        assert conf._find_col(users, "email").is_unique is True
        assert conf._find_table(schema, "active_users").is_view is True
