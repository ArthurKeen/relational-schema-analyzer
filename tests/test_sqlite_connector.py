"""SQLite connector conformance, plus the SQLite behaviour other engines lack.

SQLite ships with Python, so these tests always run: no Docker, no optional extra.
It has no comments, so the shared conformance runs without that capability.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests import _conformance as conf

from relational_schema_analyzer import create_connector
from relational_schema_analyzer.connectors.base import (
    SUPPORTED_SOURCE_TYPES,
    normalize_source_type,
)
from relational_schema_analyzer.connectors.sqlite_source import (
    SqliteConnector,
    connect_read_only,
)

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
]

_CAPABILITIES = {
    conf.ORDINAL,
    conf.DEFAULTS,
    conf.UNIQUE,
    conf.FOREIGN_KEYS,
    conf.VIEWS,
    conf.PROVENANCE_VERSION,
}


def _make(tmp_path, statements, name="shop.sqlite") -> str:
    path = tmp_path / name
    conn = sqlite3.connect(path)
    for stmt in statements:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def shop(tmp_path):
    return create_connector("sqlite", _make(tmp_path, _DDL)).get_schema()


def test_sqlite_conformance(shop):
    conf.assert_shop_conformance(shop, dialect="sqlite", capabilities=_CAPABILITIES)


def test_provenance(shop):
    assert shop.source.dialect == "sqlite"
    assert shop.source.server_version == sqlite3.sqlite_version
    assert shop.source.namespace == "main"
    assert shop.source.database == "shop"


def test_registered_with_an_alias():
    assert "sqlite" in SUPPORTED_SOURCE_TYPES
    assert normalize_source_type("SQLite3") == "sqlite"


def test_url_prefix_and_no_comments(tmp_path):
    path = _make(tmp_path, _DDL)
    schema = create_connector("sqlite", f"sqlite:///{path}").get_schema()
    users = conf._find_table(schema, "users")
    assert users.comment is None
    assert all(c.comment is None for c in users.columns)


def test_opens_read_only_and_never_creates_a_file(tmp_path):
    path = _make(tmp_path, _DDL)
    before = (tmp_path / "shop.sqlite").read_bytes()
    create_connector("sqlite", path).get_schema()
    assert (tmp_path / "shop.sqlite").read_bytes() == before
    with pytest.raises(RuntimeError, match="No SQLite database file"):
        create_connector("sqlite", str(tmp_path / "missing.sqlite")).get_schema()
    assert not (tmp_path / "missing.sqlite").exists()


def test_the_connection_refuses_writes(tmp_path):
    conn = connect_read_only(_make(tmp_path, _DDL))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE x (a)")
    finally:
        conn.close()


def test_only_main_schema(tmp_path):
    with pytest.raises(ValueError, match="one schema"):
        SqliteConnector(str(tmp_path / "x.sqlite"), schema_name="sales")
    assert SqliteConnector(str(tmp_path / "x.sqlite"), schema_name="public").schema_name == "main"


def test_composite_keys_case_insensitive_references_and_implicit_targets(tmp_path):
    """The shapes real files carry, BIRD's among them."""
    path = _make(
        tmp_path,
        [
            "CREATE TABLE Districts (district_id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE lines (order_id INTEGER, line_no INTEGER, sku TEXT,"
            " PRIMARY KEY (order_id, line_no))",
            # Referenced with different case, and with no column list (-> the PK).
            "CREATE TABLE account (account_id INTEGER PRIMARY KEY, District_Id INTEGER"
            " REFERENCES districts, opened TEXT)",
            # Composite reference.
            "CREATE TABLE shipments (id INTEGER PRIMARY KEY, order_id INTEGER, line_no INTEGER,"
            " FOREIGN KEY (order_id, line_no) REFERENCES LINES (ORDER_ID, LINE_NO))",
            # A reference to a table that does not exist: SQLite accepts it.
            "CREATE TABLE orphan (id INTEGER PRIMARY KEY, ghost_id INTEGER REFERENCES ghosts(id))",
        ],
    )
    schema = create_connector("sqlite", path).get_schema()
    lines = conf._find_table(schema, "lines")
    assert lines.primary_key == ["order_id", "line_no"]

    (fk,) = conf._find_table(schema, "account").foreign_keys
    assert (fk.columns, fk.foreign_table, fk.foreign_columns) == (
        ["District_Id"],
        "Districts",
        ["district_id"],
    )
    assert fk.constraint_name == "fk_account_0"

    (composite,) = conf._find_table(schema, "shipments").foreign_keys
    assert composite.foreign_table == "lines"
    assert composite.foreign_columns == ["order_id", "line_no"]
    assert composite.is_unique is False

    assert conf._find_table(schema, "orphan").foreign_keys == []


def test_unique_indexes_count_unless_partial_or_on_expressions(tmp_path):
    path = _make(
        tmp_path,
        [
            "CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, b TEXT, c TEXT, d TEXT)",
            "CREATE UNIQUE INDEX t_a ON t (a)",
            "CREATE UNIQUE INDEX t_bc ON t (b, c)",
            "CREATE UNIQUE INDEX t_d_partial ON t (d) WHERE d IS NOT NULL",
            "CREATE UNIQUE INDEX t_lower ON t (lower(a))",
            "CREATE INDEX t_plain ON t (c)",
        ],
    )
    t = conf._find_table(create_connector("sqlite", path).get_schema(), "t")
    assert sorted(t.unique_constraints) == [["a"], ["b", "c"]]
    assert conf._find_col(t, "a").is_unique is True
    assert conf._find_col(t, "d").is_unique is False


def test_a_one_to_one_reference_is_marked_unique(tmp_path):
    path = _make(
        tmp_path,
        [
            "CREATE TABLE person (id INTEGER PRIMARY KEY)",
            "CREATE TABLE passport (id INTEGER PRIMARY KEY,"
            " person_id INTEGER UNIQUE REFERENCES person(id))",
        ],
    )
    (fk,) = conf._find_table(create_connector("sqlite", path).get_schema(), "passport").foreign_keys
    assert fk.is_unique is True


def test_untyped_columns_and_names_with_spaces(tmp_path):
    path = _make(
        tmp_path,
        ['CREATE TABLE "school list" (id INTEGER PRIMARY KEY, "Charter Number", note)'],
    )
    t = conf._find_table(create_connector("sqlite", path).get_schema(), "school list")
    assert [c.name for c in t.columns] == ["id", "Charter Number", "note"]
    assert conf._find_col(t, "note").data_type == ""


def test_generated_columns_are_reported(tmp_path):
    """``pragma_table_info`` hides generated columns; they are still columns."""
    path = _make(
        tmp_path,
        [
            "CREATE TABLE t (id INTEGER PRIMARY KEY, a INT,"
            " b INT GENERATED ALWAYS AS (a * 2) STORED, c INT AS (a + 1))",
        ],
    )
    t = conf._find_table(create_connector("sqlite", path).get_schema(), "t")
    assert [c.name for c in t.columns] == ["id", "a", "b", "c"]


def test_only_sqlites_own_tables_are_hidden(tmp_path):
    """``_`` is a LIKE wildcard, so ``sqlite_%`` unescaped also matched ``sqlitedata``."""
    path = _make(
        tmp_path,
        [
            "CREATE TABLE sqlitedata (id INTEGER PRIMARY KEY)",
            "CREATE TABLE sqlite1 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE plain (id INTEGER PRIMARY KEY AUTOINCREMENT)",  # makes sqlite_sequence
        ],
    )
    schema = create_connector("sqlite", path).get_schema()
    assert sorted(schema.tables) == ["plain", "sqlite1", "sqlitedata"]


def test_an_unreadable_virtual_table_is_skipped_not_fatal(tmp_path):
    """One table whose module is not loaded (SpatiaLite, say) must not fail the file."""
    path = _make(tmp_path, ["CREATE TABLE a (id INTEGER PRIMARY KEY)"])
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute(
        "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) VALUES"
        " ('table', 'geo', 'geo', 0, 'CREATE VIRTUAL TABLE geo USING no_such_module(x)')"
    )
    conn.commit()
    conn.close()
    assert sorted(create_connector("sqlite", path).get_schema().tables) == ["a"]


def test_a_file_that_is_not_a_database_fails_as_the_connector_does(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("id,name\n1,a\n")
    with pytest.raises(RuntimeError, match="Failed to read SQLite database"):
        create_connector("sqlite", str(path)).get_schema()
