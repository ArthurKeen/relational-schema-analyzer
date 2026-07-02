"""Postgres connector introspection — assembly logic via a scripted fake cursor.

Validates that the enriched catalog queries are assembled into the model correctly
(columns/comments/defaults/ordinal, PK/UNIQUE/FK, FK cardinality hint, views,
provenance). The *live* SQL itself is exercised by the Docker integration workflow
(`integration.yml`); here we drive a fake psycopg so it runs with no database.
"""

from __future__ import annotations

import pytest

from relational_schema_analyzer.connectors import postgres as pg
from tests import _conformance as conf

# Per-table scripted result sets (dict rows, matching row_factory=dict_row).
_COLUMNS = {
    "users": [
        {"column_name": "id", "data_type": "integer", "is_nullable": "NO",
         "column_default": None, "ordinal_position": 1},
        {"column_name": "email", "data_type": "character varying", "is_nullable": "NO",
         "column_default": None, "ordinal_position": 2},
        {"column_name": "status", "data_type": "character varying", "is_nullable": "YES",
         "column_default": "'active'::text", "ordinal_position": 3},
        {"column_name": "created_at", "data_type": "timestamp without time zone",
         "is_nullable": "YES", "column_default": None, "ordinal_position": 4},
    ],
    "orders": [
        {"column_name": "id", "data_type": "integer", "is_nullable": "NO",
         "column_default": None, "ordinal_position": 1},
        {"column_name": "user_id", "data_type": "integer", "is_nullable": "NO",
         "column_default": None, "ordinal_position": 2},
        {"column_name": "total", "data_type": "numeric", "is_nullable": "YES",
         "column_default": None, "ordinal_position": 3},
    ],
    "active_users": [
        {"column_name": "id", "data_type": "integer", "is_nullable": "YES",
         "column_default": None, "ordinal_position": 1},
    ],
}
_COMMENTS = {"users": [{"column_name": "email", "comment": "contact email"}]}
_PKS = {"users": [{"column_name": "id"}], "orders": [{"column_name": "id"}]}
_UNIQUES = {"users": [{"constraint_name": "users_email_key", "column_name": "email",
                       "ordinal_position": 1}]}
_FKS = {
    "orders": [{"column_name": "user_id", "foreign_table_name": "users",
                "foreign_column_name": "id", "constraint_name": "orders_user_id_fkey"}],
}


def _resolve(sql: str, params: tuple):
    s = " ".join(sql.split())
    table = params[1] if len(params) >= 2 else None
    if "current_database()" in s:
        return [{"db": "shop", "ver": "16.1"}]
    if "pg_inherits" in s:
        return []
    if "relkind IN" in s:
        return [
            {"table_name": "users", "relkind": "r", "comment": "people"},
            {"table_name": "orders", "relkind": "r", "comment": None},
            {"table_name": "active_users", "relkind": "v", "comment": None},
        ]
    if "information_schema.columns" in s:
        return list(_COLUMNS.get(table, []))
    if "col_description" in s:
        return list(_COMMENTS.get(table, []))
    if "'PRIMARY KEY'" in s:
        return list(_PKS.get(table, []))
    if "'UNIQUE'" in s:
        return list(_UNIQUES.get(table, []))
    if "c.contype = 'f'" in s:
        return list(_FKS.get(table, []))
    if "c.contype = 'c'" in s:
        return []
    return []


class _FakeCursor:
    def __init__(self):
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._rows = _resolve(sql, params or ())

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


class _FakePsycopg:
    def connect(self, *a, **k):
        return _FakeConn()


@pytest.fixture
def pg_shop(monkeypatch):
    monkeypatch.setattr(pg, "psycopg", _FakePsycopg())
    return pg.PostgresConnector("postgresql://u:p@h/shop").get_schema()


def test_postgres_full_conformance(pg_shop):
    caps = {
        conf.ORDINAL, conf.DEFAULTS, conf.COMMENTS, conf.UNIQUE,
        conf.FOREIGN_KEYS, conf.VIEWS, conf.PROVENANCE_VERSION,
    }
    conf.assert_shop_conformance(pg_shop, dialect="postgresql", capabilities=caps)


def test_provenance(pg_shop):
    assert pg_shop.source.dialect == "postgresql"
    assert pg_shop.source.server_version == "16.1"
    assert pg_shop.source.database == "shop"


def test_enrichment_details(pg_shop):
    users = conf._find_table(pg_shop, "users")
    assert users.comment == "people"
    assert conf._find_col(users, "email").comment == "contact email"
    assert conf._find_col(users, "status").default == "'active'::text"
    assert conf._find_col(users, "email").is_unique is True
    assert conf._find_table(pg_shop, "active_users").is_view is True
    orders = conf._find_table(pg_shop, "orders")
    assert orders.foreign_keys[0].is_unique is False
