"""Enumeration CHECK constraints read back from live databases (opt-in).

Each database rewrites a CHECK before storing it -- Postgres into ``= ANY (ARRAY[...])``
with casts, MySQL with character-set introducers (and, on 8.4, a second layer of
escaping), SQL Server into a reversed ``OR`` chain -- so the only proof that
``enum_values`` is filled is to declare the constraint and read it back through the
connector. Each dialect builds the same table:

    account_type  IN ('personal','business')    -> values, and a declared discriminator
    note          IN ('it''s','x')              -> values, doubled quote resolved
    level         IN (1,2,3)                    -> values
    score         BETWEEN 1 AND 5               -> no values (range)
    code          NOT IN ('z')                  -> no values
    a, b          a IN ('x') OR b IN ('y')      -> no values (two columns)
    tier          MySQL ENUM('gold','silver')   -> values, from the column type
    a             MySQL ... NOT ENFORCED        -> no values

Values are compared as sets: SQL Server stores them in reverse order.

Run:  RUN_INTEGRATION=1 RSA_PG_DSN=... RSA_MYSQL_DSN=... RSA_MSSQL_DSN=... pytest tests/integration
"""

from __future__ import annotations

import os

import pytest

from relational_schema_analyzer import create_connector
from relational_schema_analyzer.discriminator import detect_discriminators
from relational_schema_analyzer.types import Schema

from .conftest import _mssql_exec, _mysql_exec, _pg_exec

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="RUN_INTEGRATION=1 not set"),
]

_COMMON_COLUMNS = (
    "id INT PRIMARY KEY,"
    " account_type VARCHAR(20) CHECK (account_type IN ('personal','business')),"
    " note VARCHAR(20) CHECK (note IN ('it''s','x')),"
    " level INT CHECK (level IN (1,2,3)),"
    " score INT CHECK (score BETWEEN 1 AND 5),"
    " code VARCHAR(5) CHECK (code NOT IN ('z')),"
    " a VARCHAR(5), b VARCHAR(5),"
    " CONSTRAINT rsa_enum_two_cols CHECK (a IN ('x') OR b IN ('y'))"
)

# Each dialect's first statement is its teardown.
_PG = [
    "DROP TABLE IF EXISTS rsa_enum_t",
    f"CREATE TABLE rsa_enum_t ({_COMMON_COLUMNS})",
]
_MYSQL = [
    "DROP TABLE IF EXISTS rsa_enum_t",
    f"CREATE TABLE rsa_enum_t ({_COMMON_COLUMNS},"
    " tier ENUM('gold','silver'),"
    " CONSTRAINT rsa_enum_not_enforced CHECK (a IN ('p','q')) NOT ENFORCED)",
]
_MSSQL = [
    "IF OBJECT_ID('rsa_enum_t','U') IS NOT NULL DROP TABLE rsa_enum_t",
    f"CREATE TABLE rsa_enum_t ({_COMMON_COLUMNS})",
]

EXPECTED = {
    "account_type": ["business", "personal"],
    "note": ["it's", "x"],
    "level": ["1", "2", "3"],
}
MYSQL_EXTRA = {"tier": ["gold", "silver"]}

_CASES = [
    ("postgresql", "RSA_PG_DSN", _PG, _pg_exec, "public", {}),
    ("mysql", "RSA_MYSQL_DSN", _MYSQL, _mysql_exec, None, MYSQL_EXTRA),
    ("sqlserver", "RSA_MSSQL_DSN", _MSSQL, _mssql_exec, "dbo", {}),
]


@pytest.mark.parametrize(
    ("dialect", "env", "ddl", "run", "schema", "extra"), _CASES, ids=[c[0] for c in _CASES]
)
def test_enumeration_checks_carry_their_values(dialect, env, ddl, run, schema, extra):
    dsn = os.environ.get(env)
    if not dsn:
        pytest.skip(f"{env} not set")
    run(dsn, ddl[:1])
    run(dsn, ddl[1:])
    try:
        args = (dialect, dsn) if schema is None else (dialect, dsn, schema)
        table = create_connector(*args).get_schema().tables["rsa_enum_t"]

        enumerated = {
            c.columns[0]: sorted(c.enum_values)
            for c in table.check_constraints
            if c.enum_values
        }
        assert enumerated == {**EXPECTED, **extra}
        # Every constraint is still reported, with its raw expression, values or not.
        assert all(c.expression for c in table.check_constraints)
        assert len(table.check_constraints) >= 6

        # And declared discriminator detection, which only reads enum_values, now fires.
        found = detect_discriminators(Schema(tables={"rsa_enum_t": table}))
        account = [c for c in found if c.column == "account_type"]
        assert account and account[0].source == "check_constraint"
        assert account[0].values == ["business", "personal"]
    finally:
        run(dsn, ddl[:1])
