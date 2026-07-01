"""DuckDB source connector (introspection only).

DuckDB is an embeddable, server-less analytical database with a standard
``information_schema``. That makes it a lightweight, always-on engine for testing
the generic catalog-introspection path, and a faithful reference for the same
``table_constraints`` / ``key_column_usage`` / ``referential_constraints`` pattern
used by the other RDBMS connectors.

``connection_string`` is a path to a ``.duckdb`` file. Introspection opens the file
read-only. Comments live in DuckDB's ``duckdb_tables()`` / ``duckdb_columns()``
catalog functions rather than ``information_schema``, so both are consulted.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from ..log import get_logger
from ..types import Column, ForeignKey, Schema, SourceProvenance, Table

logger = get_logger(__name__)

# DuckDB's default schema is ``main``; fold the cross-dialect ``public`` default to it.
_DEFAULT_SCHEMA_SENTINELS = frozenset({None, "", "public", "PUBLIC"})


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as err:
        raise ImportError(
            "DuckDB support requires duckdb. "
            "Install with: pip install 'relational-schema-analyzer[duckdb]'"
        ) from err
    return duckdb


class DuckDbConnector:
    """Introspect a DuckDB database file into a :class:`Schema`."""

    def __init__(self, connection_string: str, schema_name: str = "main") -> None:
        self.connection_string = connection_string
        self.schema_name = (
            "main" if schema_name in _DEFAULT_SCHEMA_SENTINELS else schema_name
        )
        self.database = Path(connection_string).stem or connection_string

    def get_schema(self) -> Schema:
        duckdb = _load_duckdb()
        try:
            conn = duckdb.connect(self.connection_string, read_only=True)
        except Exception as err:
            raise RuntimeError(f"Failed to open DuckDB database: {err}") from err
        try:
            return self._introspect(conn)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _query(self, conn: Any, sql: str, params: tuple = ()) -> list[tuple]:
        rel = conn.execute(sql, params) if params else conn.execute(sql)
        return rel.fetchall()

    def _introspect(self, conn: Any) -> Schema:
        schema = Schema(source=self._provenance(conn))

        columns_by_table = self._columns_by_table(conn)
        col_comments = self._column_comments(conn)
        table_comments = self._table_comments(conn)
        pks, uniques, fks = self._constraints(conn)

        table_rows = self._query(
            conn,
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = ? ORDER BY table_name",
            (self.schema_name,),
        )
        for table_name, table_type in table_rows:
            schema.tables[table_name] = self._build_table(
                table_name=table_name,
                is_view=(str(table_type).upper() == "VIEW"),
                comment=table_comments.get(table_name),
                columns=columns_by_table.get(table_name, []),
                col_comments=col_comments,
                pk=pks.get(table_name, []),
                unique_sets=uniques.get(table_name, []),
                fks=fks.get(table_name, []),
            )
        return schema

    def _provenance(self, conn: Any) -> SourceProvenance:
        version: Optional[str] = None
        try:
            row = self._query(conn, "SELECT version()")
            if row and row[0] and row[0][0]:
                version = str(row[0][0])
        except Exception:  # noqa: BLE001 - provenance is best-effort
            version = None
        return SourceProvenance(
            dialect="duckdb",
            server_version=version,
            database=self.database,
            namespace=self.schema_name,
        )

    def _columns_by_table(self, conn: Any) -> dict[str, list[dict[str, Any]]]:
        rows = self._query(
            conn,
            "SELECT table_name, column_name, data_type, is_nullable, "
            "column_default, ordinal_position "
            "FROM information_schema.columns WHERE table_schema = ? "
            "ORDER BY table_name, ordinal_position",
            (self.schema_name,),
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for table_name, name, data_type, is_nullable, default, ordinal in rows:
            out.setdefault(table_name, []).append(
                {
                    "name": name,
                    "data_type": str(data_type or "").lower(),
                    "is_nullable": (str(is_nullable).upper() == "YES"),
                    "default": (str(default) if default is not None else None),
                    "ordinal": (int(ordinal) - 1 if ordinal is not None else None),
                }
            )
        return out

    def _column_comments(self, conn: Any) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        try:
            rows = self._query(
                conn,
                "SELECT table_name, column_name, comment FROM duckdb_columns() "
                "WHERE schema_name = ?",
                (self.schema_name,),
            )
        except Exception:  # noqa: BLE001 - comments are best-effort
            return out
        for table_name, column_name, comment in rows:
            if comment:
                out[(table_name, column_name)] = str(comment)
        return out

    def _table_comments(self, conn: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for fn in ("duckdb_tables()", "duckdb_views()"):
            try:
                rows = self._query(
                    conn,
                    f"SELECT table_name, comment FROM {fn} WHERE schema_name = ?",
                    (self.schema_name,),
                )
            except Exception:  # noqa: BLE001
                continue
            for table_name, comment in rows:
                if comment:
                    out[table_name] = str(comment)
        return out

    def _constraints(
        self, conn: Any
    ) -> tuple[dict[str, list[str]], dict[str, list[list[str]]], dict[str, list[ForeignKey]]]:
        """Resolve PK / UNIQUE / FK per table via standard information_schema views."""
        tc = self._query(
            conn,
            "SELECT constraint_name, constraint_type, table_name "
            "FROM information_schema.table_constraints WHERE table_schema = ?",
            (self.schema_name,),
        )
        kcu = self._query(
            conn,
            "SELECT constraint_name, column_name, ordinal_position "
            "FROM information_schema.key_column_usage WHERE table_schema = ? "
            "ORDER BY constraint_name, ordinal_position",
            (self.schema_name,),
        )
        try:
            rc = self._query(
                conn,
                "SELECT constraint_name, unique_constraint_name "
                "FROM information_schema.referential_constraints WHERE constraint_schema = ?",
                (self.schema_name,),
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
                if not uc:
                    continue
                ref_table = table_by_constraint.get(uc)
                ref_cols = cols_by_constraint.get(uc, [])
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
        comment: Optional[str],
        columns: list[dict[str, Any]],
        col_comments: dict[tuple[str, str], str],
        pk: list[str],
        unique_sets: list[list[str]],
        fks: list[ForeignKey],
    ) -> Table:
        pk_set = set(pk)
        single_unique = {u[0] for u in unique_sets if len(u) == 1}
        if len(pk) == 1:
            single_unique.add(pk[0])

        built: list[Column] = []
        for c in columns:
            built.append(
                Column(
                    name=c["name"],
                    data_type=c["data_type"],
                    is_nullable=c["is_nullable"],
                    is_primary_key=c["name"] in pk_set,
                    is_unique=c["name"] in single_unique,
                    default=c["default"],
                    comment=col_comments.get((table_name, c["name"])),
                    ordinal=c["ordinal"],
                )
            )

        unique_sets_as_sets = [set(u) for u in unique_sets]
        if pk:
            unique_sets_as_sets.append(set(pk))
        for fk in fks:
            fk.is_unique = set(fk.columns) in unique_sets_as_sets

        return Table(
            name=table_name,
            columns=built,
            primary_key=pk,
            foreign_keys=fks,
            is_view=is_view,
            comment=comment,
            schema_name=self.schema_name,
            unique_constraints=[list(u) for u in unique_sets],
        )
