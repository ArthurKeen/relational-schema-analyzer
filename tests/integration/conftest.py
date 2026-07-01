"""Live-database integration fixtures (opt-in).

Skipped entirely unless ``RUN_INTEGRATION=1``. Each dialect is enabled by its own
DSN env var; missing ones are skipped so the suite runs against whatever is
available (Docker service containers in CI, or a local/cloud instance).

Each fixture connects with the raw driver, materializes the canonical shop schema
(``tests/_conformance.py``), introspects it through this library's connector, yields
the resulting ``PhysicalSchema`` + capabilities, then tears the schema down.

Env vars:
    RUN_INTEGRATION=1
    RSA_PG_DSN        e.g. postgresql://user:pass@localhost:5432/rsa_it
    RSA_MYSQL_DSN     e.g. mysql://user:pass@localhost:3306/rsa_it
    RSA_MSSQL_DSN     e.g. mssql://sa:Passw0rd!@localhost:1433/rsa_it
"""

from __future__ import annotations

import os

import pytest

from relational_schema_analyzer import create_connector
from tests import _conformance as conf

_RUN = os.environ.get("RUN_INTEGRATION") == "1"

# Current RDBMS connectors introspect columns + PK + FK (not yet the enrichment
# fields), so live conformance checks the core + foreign keys. Capabilities widen
# as per-dialect enrichment lands.
_RDBMS_CAPABILITIES = {conf.FOREIGN_KEYS}

_PG_DDL = [
    "DROP VIEW IF EXISTS active_users",
    "DROP TABLE IF EXISTS orders",
    "DROP TABLE IF EXISTS users",
    "CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(100) NOT NULL UNIQUE,"
    " status VARCHAR(10) DEFAULT 'active', created_at TIMESTAMP)",
    "COMMENT ON TABLE users IS 'people'",
    "COMMENT ON COLUMN users.email IS 'contact email'",
    "CREATE TABLE orders (id INT PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id),"
    " total NUMERIC(10,2))",
    "CREATE VIEW active_users AS SELECT id FROM users",
]

_MYSQL_DDL = [
    "DROP VIEW IF EXISTS active_users",
    "DROP TABLE IF EXISTS orders",
    "DROP TABLE IF EXISTS users",
    "CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(100) NOT NULL UNIQUE,"
    " status VARCHAR(10) DEFAULT 'active', created_at DATETIME) COMMENT='people'",
    "CREATE TABLE orders (id INT PRIMARY KEY, user_id INT NOT NULL,"
    " total DECIMAL(10,2), FOREIGN KEY (user_id) REFERENCES users(id))",
    "CREATE VIEW active_users AS SELECT id FROM users",
]

_MSSQL_DDL = [
    "IF OBJECT_ID('active_users','V') IS NOT NULL DROP VIEW active_users",
    "IF OBJECT_ID('orders','U') IS NOT NULL DROP TABLE orders",
    "IF OBJECT_ID('users','U') IS NOT NULL DROP TABLE users",
    "CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(100) NOT NULL UNIQUE,"
    " status VARCHAR(10) DEFAULT 'active', created_at DATETIME2)",
    "CREATE TABLE orders (id INT PRIMARY KEY,"
    " user_id INT NOT NULL FOREIGN KEY REFERENCES users(id), total DECIMAL(10,2))",
    "CREATE VIEW active_users AS SELECT id FROM users",
]


def _pg_exec(dsn: str, statements: list[str]) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        for stmt in statements:
            conn.execute(stmt)


def _mysql_exec(dsn: str, statements: list[str]) -> None:
    from relational_schema_analyzer.connectors.mysql import _load_pymysql, _parse_mysql_url

    pymysql = _load_pymysql()
    conn = pymysql.connect(**_parse_mysql_url(dsn))
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _mssql_exec(dsn: str, statements: list[str]) -> None:
    from relational_schema_analyzer.connectors.mssql import _load_pymssql, _parse_mssql_url

    pymssql = _load_pymssql()
    conn = pymssql.connect(**_parse_mssql_url(dsn))
    try:
        cur = conn.cursor()
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


_DIALECTS = {
    "postgresql": ("RSA_PG_DSN", _PG_DDL, _pg_exec, "public"),
    "mysql": ("RSA_MYSQL_DSN", _MYSQL_DDL, _mysql_exec, None),
    "sqlserver": ("RSA_MSSQL_DSN", _MSSQL_DDL, _mssql_exec, "dbo"),
}


@pytest.fixture(params=list(_DIALECTS))
def live_shop(request):
    if not _RUN:
        pytest.skip("integration tests are opt-in (set RUN_INTEGRATION=1)")
    dialect = request.param
    dsn_env, ddl, exec_fn, schema_name = _DIALECTS[dialect]
    dsn = os.environ.get(dsn_env)
    if not dsn:
        pytest.skip(f"{dsn_env} not set")

    exec_fn(dsn, ddl)
    connector = create_connector(
        dialect, dsn, schema_name=schema_name or "public"
    )
    schema = connector.get_schema()
    return dialect, schema, _RDBMS_CAPABILITIES
