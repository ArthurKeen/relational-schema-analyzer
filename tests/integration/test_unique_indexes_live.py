"""Unique indexes as candidate keys, verified against live databases (opt-in).

FK inference targets only declared candidate keys -- a primary key or a UNIQUE key --
so a uniqueness guarantee the connector does not report is a relationship inference
can never find. ``CREATE UNIQUE INDEX`` is such a guarantee, and Postgres and SQL
Server do not list it among a table's constraints.

Each dialect builds the same table and asserts exactly which column sets become keys:

    code             UNIQUE constraint                       -> key (once)
    code             unique index duplicating the constraint -> not counted twice
    handle           unique index, no constraint             -> key
    region, ext      unique index (Postgres/SQL Server: INCLUDE note) -> key, key columns only
    email            partial / filtered unique index         -> NOT a key (Postgres, SQL Server)
    lower(email)     expression / functional unique index    -> NOT a key (Postgres, MySQL)
    ext, lower(note) mixed column + expression unique index  -> NOT a key on ext alone
                     (MySQL lists only the plain column, so this was misreported
                     before 0.8.1; Postgres needs its ``indexprs`` filter for it)
    handle           ...and a FOREIGN KEY elsewhere references it -> still a key
                     (Postgres records the referenced index on the FK constraint)
    id               unique index repeating the primary key  -> NOT a second key
    email_lower      SQL Server computed column, unique index -> NOT a key

MySQL already reports unique indexes as UNIQUE constraints and has no partial
indexes; it is here to pin that behaviour, not because it changed.

Run:  RUN_INTEGRATION=1 RSA_PG_DSN=... RSA_MYSQL_DSN=... RSA_MSSQL_DSN=... pytest tests/integration
"""

from __future__ import annotations

import os

import pytest

from relational_schema_analyzer import create_connector

from .conftest import _mssql_exec, _mysql_exec, _pg_exec

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1", reason="RUN_INTEGRATION=1 not set"
)

EXPECTED_KEYS = [["code"], ["handle"], ["region", "ext"]]
EXPECTED_SINGLE_COLUMN_KEYS = {"id", "code", "handle"}

# Each dialect's first two statements are its teardown.
_PG = [
    "DROP TABLE IF EXISTS rsa_uq_ref",
    "DROP TABLE IF EXISTS rsa_uq_t",
    "CREATE TABLE rsa_uq_t (id INT PRIMARY KEY, code TEXT, handle TEXT, email TEXT,"
    " region TEXT, ext TEXT, note TEXT, active BOOLEAN,"
    " CONSTRAINT rsa_uq_t_code_key UNIQUE (code))",
    "CREATE UNIQUE INDEX rsa_uq_t_handle ON rsa_uq_t (handle)",
    "CREATE UNIQUE INDEX rsa_uq_t_region_ext ON rsa_uq_t (region, ext) INCLUDE (note)",
    "CREATE UNIQUE INDEX rsa_uq_t_email_active ON rsa_uq_t (email) WHERE active",
    "CREATE UNIQUE INDEX rsa_uq_t_lower_email ON rsa_uq_t (lower(email))",
    "CREATE UNIQUE INDEX rsa_uq_t_code_dup ON rsa_uq_t (code)",
    "CREATE UNIQUE INDEX rsa_uq_t_mixed ON rsa_uq_t (ext, lower(note))",
    "CREATE UNIQUE INDEX rsa_uq_t_id_dup ON rsa_uq_t (id)",
    "CREATE TABLE rsa_uq_ref (id INT PRIMARY KEY, h TEXT REFERENCES rsa_uq_t (handle))",
]
_MSSQL = [
    "IF OBJECT_ID('rsa_uq_ref','U') IS NOT NULL DROP TABLE rsa_uq_ref",
    "IF OBJECT_ID('rsa_uq_t','U') IS NOT NULL DROP TABLE rsa_uq_t",
    "CREATE TABLE rsa_uq_t (id INT PRIMARY KEY, code VARCHAR(20), handle VARCHAR(20),"
    " email VARCHAR(50), region VARCHAR(5), ext VARCHAR(5), note VARCHAR(20), active BIT,"
    " email_lower AS LOWER(email),"
    " CONSTRAINT rsa_uq_t_code_key UNIQUE (code))",
    "CREATE UNIQUE INDEX rsa_uq_t_handle ON rsa_uq_t (handle)",
    "CREATE UNIQUE INDEX rsa_uq_t_region_ext ON rsa_uq_t (region, ext) INCLUDE (note)",
    # Filtered indexes require QUOTED_IDENTIFIER ON, which pymssql sessions have.
    "CREATE UNIQUE INDEX rsa_uq_t_email_active ON rsa_uq_t (email) WHERE active = 1",
    "CREATE UNIQUE INDEX rsa_uq_t_code_dup ON rsa_uq_t (code)",
    "CREATE UNIQUE INDEX rsa_uq_t_email_lower ON rsa_uq_t (email_lower)",
    "CREATE UNIQUE INDEX rsa_uq_t_id_dup ON rsa_uq_t (id)",
    "CREATE TABLE rsa_uq_ref (id INT PRIMARY KEY, h VARCHAR(20) REFERENCES rsa_uq_t (handle))",
]
_MYSQL = [
    "DROP TABLE IF EXISTS rsa_uq_ref",
    "DROP TABLE IF EXISTS rsa_uq_t",
    "CREATE TABLE rsa_uq_t (id INT PRIMARY KEY, code VARCHAR(20), handle VARCHAR(20),"
    " email VARCHAR(50), region VARCHAR(5), ext VARCHAR(5),"
    " CONSTRAINT rsa_uq_t_code_key UNIQUE (code))",
    "CREATE UNIQUE INDEX rsa_uq_t_handle ON rsa_uq_t (handle)",
    "CREATE UNIQUE INDEX rsa_uq_t_region_ext ON rsa_uq_t (region, ext)",
    "CREATE UNIQUE INDEX rsa_uq_t_lower_email ON rsa_uq_t ((lower(email)))",
    "CREATE UNIQUE INDEX rsa_uq_t_mixed ON rsa_uq_t (ext, (lower(handle)))",
    "CREATE TABLE rsa_uq_ref (id INT PRIMARY KEY, h VARCHAR(20),"
    " FOREIGN KEY (h) REFERENCES rsa_uq_t (handle))",
]


_CASES = [
    ("postgresql", "RSA_PG_DSN", _PG, _pg_exec, "public"),
    ("mysql", "RSA_MYSQL_DSN", _MYSQL, _mysql_exec, None),
    ("sqlserver", "RSA_MSSQL_DSN", _MSSQL, _mssql_exec, "dbo"),
]


@pytest.mark.parametrize(
    ("dialect", "env", "ddl", "run", "schema"), _CASES, ids=[c[0] for c in _CASES]
)
def test_unique_indexes_are_candidate_keys(dialect, env, ddl, run, schema):
    dsn = os.environ.get(env)
    if not dsn:
        pytest.skip(f"{env} not set")
    run(dsn, ddl[:2])
    run(dsn, ddl[2:])
    try:
        args = (dialect, dsn) if schema is None else (dialect, dsn, schema)
        table = create_connector(*args).get_schema().tables["rsa_uq_t"]
        assert [list(u) for u in table.unique_constraints] == EXPECTED_KEYS
        assert {c.name for c in table.columns if c.is_unique} == EXPECTED_SINGLE_COLUMN_KEYS
    finally:
        run(dsn, ddl[:2])
