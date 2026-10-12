"""What the source-connector interface promises, and to whom.

``create_source_connector`` is public and hands back a :class:`SourceConnector`.
Before the split, that type also promised ``open_session`` — bulk row reads — which
four of the nine connectors do not have. A caller reading the declaration would call
it, crash, and have had their own type checker approve the call first.

Two of those four never will have it. A dbt ``manifest.json`` and an OSI
``*.osi.yaml`` are data catalogs (DESIGN §9.3.1): they say which tables exist, and
hold no rows. The other two, DuckDB and Databricks, are real databases that could
stream and do not yet — a gap, asserted here so it stays visible rather than being
rediscovered by a consumer.
"""

from __future__ import annotations

import pytest

from relational_schema_analyzer.connectors.base import (
    BulkReadableSource,
    SourceConnector,
    supports_bulk_read,
)

#: Every connector, and whether its rows can be read at all.
#: ``False`` for a data catalog is correct and permanent; ``False`` for a real
#: database is the open gap.
CONNECTORS: dict[str, tuple[str, bool]] = {
    "postgres": ("relational_schema_analyzer.connectors.postgres:PostgresConnector", True),
    "mysql": ("relational_schema_analyzer.connectors.mysql:MySQLConnector", True),
    "mssql": ("relational_schema_analyzer.connectors.mssql:SQLServerConnector", True),
    "snowflake": ("relational_schema_analyzer.connectors.snowflake:SnowflakeConnector", True),
    "csv": ("relational_schema_analyzer.connectors.csv_source:CsvConnector", True),
    # Real databases, no session yet.
    "duckdb": ("relational_schema_analyzer.connectors.duckdb_source:DuckDbConnector", False),
    "databricks": (
        "relational_schema_analyzer.connectors.databricks_source:DatabricksConnector",
        False,
    ),
    # Data catalogs: no rows exist to stream.
    "dbt": ("relational_schema_analyzer.connectors.dbt_manifest:DbtManifestConnector", False),
    "osi": ("relational_schema_analyzer.connectors.osi:OsiConnector", False),
}

CATALOG_ONLY = {"dbt", "osi"}


def _load(path: str) -> type:
    module, _, name = path.partition(":")
    import importlib

    return getattr(importlib.import_module(module), name)


class TestTheInterfaceTellsTheTruth:
    @pytest.mark.parametrize("key", sorted(CONNECTORS))
    def test_every_connector_can_describe_itself(self, key):
        """``get_schema`` is the one thing every source does, catalog or database."""
        cls = _load(CONNECTORS[key][0])
        assert callable(getattr(cls, "get_schema", None)), (
            f"{cls.__name__} cannot describe a schema, so it is not a source at all"
        )

    @pytest.mark.parametrize("key", sorted(CONNECTORS))
    def test_bulk_read_support_matches_the_registry(self, key):
        path, expected = CONNECTORS[key]
        cls = _load(path)
        instance = cls.__new__(cls)  # no connection attempted; the question is structural
        assert supports_bulk_read(instance) is expected, (
            f"{cls.__name__} reports bulk-read support = {not expected}. Update this "
            "registry deliberately: adding a session to DuckDB or Databricks closes a "
            "known gap, while a dbt manifest or an OSI file has no rows to stream."
        )

    @pytest.mark.parametrize("key", sorted(CATALOG_ONLY))
    def test_a_data_catalog_never_claims_to_hold_rows(self, key):
        """Permanent, not a gap: these describe tables and contain none of their data."""
        cls = _load(CONNECTORS[key][0])
        assert not supports_bulk_read(cls.__new__(cls))


class TestTheGuardIsUsableByCallers:
    def test_the_narrow_protocol_still_requires_describing(self):
        """``BulkReadableSource`` extends the base promise; it does not replace it.

        Asserted through the MRO because ``issubclass`` raises on a protocol with
        data members ("Protocols with non-method members don't support issubclass()"),
        which is the same limitation that makes :func:`supports_bulk_read` ask about
        ``open_session`` directly.
        """
        assert SourceConnector in BulkReadableSource.__mro__
        assert "open_session" in BulkReadableSource.__dict__
        assert "get_schema" in SourceConnector.__dict__

    def test_a_streaming_source_satisfies_both(self):
        from relational_schema_analyzer.connectors.postgres import PostgresConnector

        c = PostgresConnector("postgresql://user@host/db")
        assert isinstance(c, SourceConnector)
        assert supports_bulk_read(c)

    def test_the_guard_does_not_need_a_fully_built_instance(self):
        """A half-constructed connector must still answer the structural question.

        The protocol carries data members, so ``isinstance`` against it also demands
        ``connection_string`` and ``schema_name`` on the instance — which would make
        this answer "cannot stream" for a connector that plainly can.
        """
        from relational_schema_analyzer.connectors.postgres import PostgresConnector

        assert supports_bulk_read(PostgresConnector.__new__(PostgresConnector))

    def test_something_that_is_not_a_connector_is_not_bulk_readable(self):
        assert not supports_bulk_read(object())  # type: ignore[arg-type]
