"""Databricks (Unity Catalog) source connector — introspection only.

Databricks Unity Catalog exposes the standard ANSI ``information_schema``
(``tables`` / ``columns`` with a ``comment`` column, ``table_constraints`` /
``key_column_usage`` / ``referential_constraints`` for GA primary/foreign/unique
keys), so this is the same catalog-introspection pattern as the DuckDB / Postgres
connectors — with Databricks' three-level ``catalog.schema.table`` namespace.

Connection string (SQLAlchemy-ish; token as the password, http_path as the path)::

    databricks://:<access_token>@<server_hostname>/sql/1.0/warehouses/<id>?catalog=main&schema=default

There is no in-process emulator for Databricks (the driver speaks to a live SQL
warehouse), so the assembly is covered by mock-cursor tests; a live workspace is
opt-in via ``RSA_DATABRICKS_DSN``.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from ..log import get_logger
from ..types import Column, ForeignKey, Schema, SourceProvenance, Table

logger = get_logger(__name__)

_DEFAULT_SCHEMA_SENTINELS = frozenset({None, "", "public", "PUBLIC", "default"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_databricks() -> Any:
    try:
        from databricks import sql as dbsql
    except ImportError as err:
        raise ImportError(
            "Databricks support requires databricks-sql-connector. "
            "Install with: pip install 'relational-schema-analyzer[databricks]'"
        ) from err
    return dbsql


def _safe_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise ValueError(f"Unsafe Databricks {kind} name: {name!r}")
    return name


def _parse_databricks_url(url: str) -> dict[str, Any]:
    """Parse ``databricks://:<token>@<host>/<http_path>?catalog=&schema=`` into parts."""
    if not url or not url.startswith("databricks://"):
        raise ValueError(
            "Databricks connection string must look like "
            "databricks://:<token>@<host>/sql/1.0/warehouses/<id>?catalog=..&schema=.."
        )
    parsed = urlparse(url)
    host = parsed.hostname
    http_path = parsed.path or ""
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    token = unquote(parsed.password or "") or query.get("access_token", "")
    http_path = query.get("http_path", http_path)
    if not host or not http_path or not token:
        raise ValueError(
            "Databricks connection string needs host, http_path, and an access token"
        )
    return {
        "server_hostname": host,
        "http_path": http_path,
        "access_token": token,
        "catalog": query.get("catalog", "main"),
        "schema": query.get("schema", "default"),
    }


class DatabricksConnector:
    """Introspect a Databricks Unity Catalog schema into a :class:`Schema`."""

    def __init__(self, connection_string: str, schema_name: str = "default") -> None:
        self.connection_string = connection_string
        parts = _parse_databricks_url(connection_string)
        self._connect_params = {
            "server_hostname": parts["server_hostname"],
            "http_path": parts["http_path"],
            "access_token": parts["access_token"],
        }
        self.catalog = parts["catalog"]
        self.schema_name = (
            parts["schema"] if schema_name in _DEFAULT_SCHEMA_SENTINELS else schema_name
        )

    def get_schema(self) -> Schema:
        dbsql = _load_databricks()
        try:
            conn = dbsql.connect(
                catalog=self.catalog, schema=self.schema_name, **self._connect_params
            )
        except Exception as err:
            raise RuntimeError(f"Failed to connect to Databricks: {err}") from err
        try:
            cur = conn.cursor()
            try:
                return self._introspect(cur)
            finally:
                cur.close()
        finally:
            conn.close()

    def _ident(self) -> str:
        return _safe_identifier(self.catalog, "catalog")

    def _rows(self, cur: Any, sql: str) -> list[tuple]:
        cur.execute(sql)
        return cur.fetchall()

    def _introspect(self, cur: Any) -> Schema:
        catalog = self._ident()
        schema = _safe_identifier(self.schema_name, "schema")
        info = f"{catalog}.information_schema"

        result = Schema(source=self._provenance(cur))

        columns_by_table = self._columns_by_table(cur, info, schema)
        pks, uniques, fks = self._constraints(cur, info, schema)

        for name, table_type, comment in self._rows(
            cur,
            f"SELECT table_name, table_type, comment FROM {info}.tables "
            f"WHERE table_schema = '{schema}' ORDER BY table_name",
        ):
            result.tables[name] = self._build_table(
                table_name=name,
                is_view=(str(table_type).upper() in ("VIEW", "MATERIALIZED_VIEW")),
                comment=comment,
                columns=columns_by_table.get(name, []),
                pk=pks.get(name, []),
                unique_sets=uniques.get(name, []),
                fks=fks.get(name, []),
            )
        return result

    def _provenance(self, cur: Any) -> SourceProvenance:
        version: Optional[str] = None
        try:
            rows = self._rows(cur, "SELECT current_version() AS v")
            if rows and rows[0] and rows[0][0]:
                version = str(rows[0][0])
        except Exception:  # noqa: BLE001 - provenance is best-effort
            version = None
        return SourceProvenance(
            dialect="databricks",
            server_version=version,
            database=self.catalog,
            namespace=self.schema_name,
        )

    def _columns_by_table(
        self, cur: Any, info: str, schema: str
    ) -> dict[str, list[dict[str, Any]]]:
        rows = self._rows(
            cur,
            "SELECT table_name, column_name, data_type, is_nullable, column_default, "
            f"ordinal_position, comment FROM {info}.columns "
            f"WHERE table_schema = '{schema}' ORDER BY table_name, ordinal_position",
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for table_name, name, data_type, is_nullable, default, ordinal, comment in rows:
            out.setdefault(table_name, []).append(
                {
                    "name": name,
                    "data_type": str(data_type or "").lower(),
                    "is_nullable": (str(is_nullable).upper() == "YES"),
                    "default": (str(default) if default is not None else None),
                    "ordinal": (int(ordinal) - 1 if ordinal is not None else None),
                    "comment": (str(comment) if comment else None),
                }
            )
        return out

    def _constraints(
        self, cur: Any, info: str, schema: str
    ) -> tuple[dict[str, list[str]], dict[str, list[list[str]]], dict[str, list[ForeignKey]]]:
        tc = self._rows(
            cur,
            "SELECT constraint_name, constraint_type, table_name "
            f"FROM {info}.table_constraints WHERE table_schema = '{schema}'",
        )
        kcu = self._rows(
            cur,
            "SELECT constraint_name, column_name, ordinal_position "
            f"FROM {info}.key_column_usage WHERE table_schema = '{schema}' "
            "ORDER BY constraint_name, ordinal_position",
        )
        try:
            rc = self._rows(
                cur,
                "SELECT constraint_name, unique_constraint_name "
                f"FROM {info}.referential_constraints WHERE constraint_schema = '{schema}'",
            )
        except Exception:  # noqa: BLE001
            rc = []

        cols_by_constraint: dict[str, list[str]] = OrderedDict()
        for cname, col, _pos in kcu:
            cols_by_constraint.setdefault(cname, []).append(col)

        type_by_constraint: dict[str, str] = {}
        table_by_constraint: dict[str, str] = {}
        for cname, ctype, table_name in tc:
            type_by_constraint[cname] = ctype
            table_by_constraint[cname] = table_name

        referenced_uc = {cname: uc for cname, uc in rc}

        pks: dict[str, list[str]] = {}
        uniques: dict[str, list[list[str]]] = {}
        fks: dict[str, list[ForeignKey]] = {}
        for cname, ctype in type_by_constraint.items():
            table_name = table_by_constraint[cname]
            cols = cols_by_constraint.get(cname, [])
            if ctype == "PRIMARY KEY":
                pks[table_name] = cols
            elif ctype == "UNIQUE":
                uniques.setdefault(table_name, []).append(cols)
            elif ctype == "FOREIGN KEY":
                uc = referenced_uc.get(cname)
                ref_table = table_by_constraint.get(uc) if uc else None
                ref_cols = cols_by_constraint.get(uc, []) if uc else []
                if not ref_table or not ref_cols:
                    continue
                fks.setdefault(table_name, []).append(
                    ForeignKey(
                        columns=cols,
                        foreign_table=ref_table,
                        foreign_columns=ref_cols,
                        constraint_name=cname,
                    )
                )
        return pks, uniques, fks

    def _build_table(
        self,
        *,
        table_name: str,
        is_view: bool,
        comment: Any,
        columns: list[dict[str, Any]],
        pk: list[str],
        unique_sets: list[list[str]],
        fks: list[ForeignKey],
    ) -> Table:
        pk_set = set(pk)
        single_unique = {u[0] for u in unique_sets if len(u) == 1}
        if len(pk) == 1:
            single_unique.add(pk[0])

        built = [
            Column(
                name=c["name"],
                data_type=c["data_type"],
                is_nullable=c["is_nullable"],
                is_primary_key=c["name"] in pk_set,
                is_unique=c["name"] in single_unique,
                default=c["default"],
                comment=c["comment"],
                ordinal=c["ordinal"],
            )
            for c in columns
        ]

        unique_col_sets = [set(u) for u in unique_sets]
        if pk:
            unique_col_sets.append(set(pk))
        for fk in fks:
            fk.is_unique = set(fk.columns) in unique_col_sets

        return Table(
            name=table_name,
            columns=built,
            primary_key=pk,
            foreign_keys=fks,
            is_view=is_view,
            comment=(str(comment) if comment else None),
            schema_name=self.schema_name,
            unique_constraints=[list(u) for u in unique_sets],
        )
